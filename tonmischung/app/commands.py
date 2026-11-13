"""Befehle des Fensters – dünn: Daten holen, an den Kern weiterreichen, Viewmodel zurück.

Hier stehen Titelleiste, Liste und Detail, Freigeben, Duplizieren, Auftragsstand und `register_all`; die Aufträge und
Dialoge haben je ein eigenes Modul (`fetch_app`, `deliver_app`, `backups_app`, `havarie_app`, `archive_app`,
`catchup_app`, `mixes_app` …, Durchsicht 13.10.2026).
"""
from __future__ import annotations

import subprocess
import time

from tonmischung.app import viewmodel
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import JobRunner
from tonmischung.app.shared import contribution
from tonmischung.core import mixstate
from tonmischung.core.mixstate import ConflictError
from tonmischung.helmut.client import HelmutAuthError, HelmutError, HelmutUnavailableError
from tonmischung.helmut.service import QUEUED
from tonmischung.ptsl.errors import PtslError


def status(ctx: AppContext) -> dict:
    """Titelleiste: Pro Tools (offene Session), Helmut, Mischer · Raum."""
    try:
        s = ctx.ptsl.open_session()
        pro_tools = {"ok": True, "session": s.name if s else None}
    except PtslError as e:
        pro_tools = {"ok": False, "error": str(e), "halted": getattr(ctx.ptsl, "halted", "")}
    from tonmischung.dropfolder.client import MODE_FOLDER
    from tonmischung.dropfolder.mode import MODE_TEXT
    folder = ctx.mode == MODE_FOLDER
    try:
        ctx.helmut.reachable()
        helmut = {"ok": True, "mock": ctx.mock is not None and not folder}
        if folder:
            helmut["nur_lesbar"] = not ctx.helmut.folder.writable()
    except HelmutError as e:
        helmut = {"ok": False, "error": str(e), "mock": ctx.mock is not None and not folder}
    interval = int(ctx.settings.get("system.uebergabeordner.abfrageintervall_s") or 15) if folder else 15
    link = ctx.extra.get("zentrale")
    if folder and link is not None and link.available and _all_watched(ctx):
        interval = max(interval, 120)               # Zentrale meldet neue Beiträge sofort – Einlesen nur als Rückfall
    return {"version": ctx.version, "pro_tools": pro_tools, "helmut": helmut,
            "betriebsart": ctx.mode, "betriebsart_text": MODE_TEXT[ctx.mode], "liste_intervall_s": interval,
            "mixer": ctx.mixer, "room": ctx.room,
            "user": {"id": ctx.user.id, "name": ctx.user.name} if ctx.user is not None else None,
            "theme": str(ctx.settings.get("persoenlich.farbschema") or "ottilie"),       # Farbschema des Nutzers
            "nachtrag": ctx.queue.summary() if ctx.queue is not None else {"offen": 0, "konflikte": []},
            "fremd": ctx.extra["fremd"].state if "fremd" in ctx.extra else None,
            "spiegel": ctx.extra["spiegel"].state if "spiegel" in ctx.extra else None,
            "zentrale": _zentrale_status(ctx)}


def _all_watched(ctx: AppContext) -> bool:
    """Beobachtet die Zentrale alle Übergabeordner dieses Macs? Sonst kämen neue Filme eines nicht beobachteten
    Ordners bis zu 2 Minuten zu spät (Tontechniker 13.11.2026)."""
    data = ctx.extra.get("zentrale_settings") or {}
    if not data.get("ordner_beobachtet"):
        return False
    keys = data.get("ordner_beobachtet_kennungen")
    if not isinstance(keys, list):                  # ältere Zentrale: nur ein Ordner
        return True
    folder = getattr(ctx.helmut, "folder", None)
    mine = [k for k, _n, _c in folder.folders()] if folder is not None and hasattr(folder, "folders") else [""]
    return set(mine) <= set(keys)


def _zentrale_status(ctx: AppContext) -> dict:
    link = ctx.extra.get("zentrale")
    if link is None:
        return {"zustand": "aus", "text": "ohne Zentrale"}
    chat = ctx.extra.get("chat")
    # Chat bleibt lesbar, auch wenn die Verbindung wackelt – nur Senden geht dann nicht (Mischer H5)
    on = bool(((ctx.extra.get("zentrale_settings") or {}).get("chat") or {}).get("aktiv")) and link.state in (
        "verbunden", "verbindet", "getrennt")
    return {"zustand": link.state, "text": link.text(), "chat_aktiv": on, "verbunden": link.available,
            "raum_ausstehend": str(ctx.extra.get("raum_ausstehend") or ""),
            "ungelesen": sum(chat.view()["ungelesen"].values()) if chat is not None and on else 0,
            "konflikte": list(ctx.extra.get("zentrale_konflikte") or [])}


def list_contributions(ctx: AppContext, args: dict) -> dict:
    from tonmischung.dropfolder.client import MODE_FOLDER, overlay_peers
    folder = ctx.mode == MODE_FOLDER
    try:
        if folder and args.get("sofort"):                    # ⌘R / Aktualisieren: Freigabe sofort neu verbinden
            ctx.helmut.folder.connect(now=True)
        items = ctx.helmut.list_for_mixing()
    except HelmutError as e:
        raise CommandError(str(e) if folder else f"Helmut: {e}") from e
    peers = ctx.extra.get("peers")
    if folder and peers is not None and getattr(peers, "available", False):
        items = overlay_peers(items, peers.peers(), ctx.mixer, ctx.room)    # Übernahmen anderer Räume sofort zeigen
    out = viewmodel.rows(items, ctx.mixer, ctx.room, args.get("query", ""), args.get("order", "zeit"))
    out["aktualisiert"] = time.strftime("%H:%M:%S")
    if folder:                                       # mehrere Übergabeordner: welche gerade fehlen (Jens 13.11.2026)
        out["ordner_probleme"] = list(ctx.helmut.folder.problems)
    return out


def contribution_detail(ctx: AppContext, args: dict) -> dict:
    pid = str(args["id"])
    c = contribution(ctx, pid)
    try:
        handover = ctx.helmut.handover(pid)
    except HelmutError:
        handover = None
    return viewmodel.detail(c, handover, ctx.mixer, ctx.room, folder_mode=ctx.folder_mode)


def release(ctx: AppContext, args: dict) -> dict:
    """Freigeben (Übernahme aufheben) mit Grund, Wahl fürs nächste Öffnen und Benachrichtigung."""
    from tonmischung.core import mixstate
    reason = args.get("reason", "")
    if reason not in viewmodel.RELEASE_REASONS:
        raise CommandError("Bitte einen Grund wählen")
    pid = str(args["id"])
    if "fremd" in ctx.extra:                       # vor dem Freigeben fremde Dateien übernehmen (M7)
        ctx.extra["fremd"].check("schliessen")
    try:
        before = ctx.helmut.get(pid)
        keep = args.get("keep") or mixstate.keep_options(before.mix, here=before.mix.room == ctx.room)[0]
        ctx.service().release(pid, reason, keep)
    except (HelmutError, ConflictError) as e:
        raise CommandError(str(e)) from e
    notify = args.get("notify", True) and not ctx.folder_mode         # Benachrichtigen nur mit der Helmut-API
    hints = notify_release(ctx, before, reason, keep) if notify else []
    out = contribution_detail(ctx, args)
    out["hinweise"] = hints
    return out


def duplicate(ctx: AppContext, args: dict) -> dict:
    """Duplikat anlegen (Zweck → Titelvorschlag, Mischstand übernehmen, sofort übernehmen)."""
    if ctx.folder_mode:
        raise CommandError(ONLY_API_DUPLICATE)
    try:
        dup = ctx.service().duplicate(
            str(args["id"]), str(args.get("title") or ""), keep_mix=bool(args.get("keep")),
            take=bool(args.get("take")))
    except (HelmutError, ConflictError, ValueError) as e:
        raise CommandError(str(e)) from e
    return contribution_detail(ctx, {"id": dup.id})


def job_key(snap: dict | None) -> tuple | None:
    """Was eine Änderung des Auftrags ausmacht: Auftrag, Zustand, Schritt, ganze Prozent, offene Frage."""
    if snap is None:
        return None
    question = snap.get("question") or {}
    return (snap.get("id"), snap.get("state"), snap.get("step"), int(float(snap.get("fraction") or 0) * 100),
            question.get("id") if isinstance(question, dict) else None)


def wait_for_job(runner: JobRunner, args: dict, clock=None, sleep=None) -> dict:
    """Für Automation (Jens 08.10.2026): warten, bis sich der Auftrag ändert – höchstens `sekunden` (1–55)."""
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    try:
        limit = min(max(float(args.get("sekunden") or 25), 1.0), 55.0)
    except (TypeError, ValueError):
        raise CommandError("sekunden: Zahl zwischen 1 und 55") from None
    before = job_key(runner.current())
    t0 = clock()
    while clock() - t0 < limit:
        sleep(0.25)
        now = runner.current()
        if job_key(now) != before:
            return {"geaendert": True, "auftrag": now}
    return {"geaendert": False, "auftrag": runner.current()}


def notify_release(ctx: AppContext, before, reason: str, keep: str) -> list[str]:
    """Bisherigen Mischer und Cutter über Helmut benachrichtigen; Rückgabe: Hinweise fürs Fenster."""
    from tonmischung.core.notify import helmut_users, release_message
    cutter = viewmodel._field(before, "cutter", "editor")
    users, missing = helmut_users([before.mix.mixer, cutter], ctx.settings.get("system.raeume_nutzer.nutzer") or [])
    title, text = release_message(before.title, reason, keep, before.mix.room, ctx.mixer)
    hints = ([f"Nicht benachrichtigt (kein Helmut-Benutzer in „Räume & Nutzer“): {', '.join(missing)}"]
             if missing else [])
    for user in users:
        try:
            ctx.helmut.notify(user, title, text)
        except QUEUED as e:
            if getattr(e, "sent_unknown", False):           # ging hinaus, Antwort blieb aus: nicht doppelt schicken
                hints.append(f"Nachricht an {user} ist wahrscheinlich angekommen (Helmut hat den Empfang nicht "
                             "bestätigt) – Ottilie schickt sie nicht noch einmal; im Zweifel kurz Bescheid geben")
                continue
            if ctx.queue is None:
                hints.append(f"Benachrichtigung an {user} nicht gesendet: {e}")
                continue
            ctx.queue.add_notify(before.id, before.title, user, title, text)       # Nachtrag kennt Nachrichten
            until = "die Anmeldung wieder gelingt" if isinstance(e, HelmutAuthError) else "Helmut wieder da ist"
            hints.append(f"Benachrichtigung an {user} wird nachgetragen, sobald {until}")
        except HelmutError as e:
            hints.append(f"Benachrichtigung an {user} nicht gesendet: {e}")
    return hints


# Nur mit angemeldetem Nutzer (Plan Nutzer & Räume): Aufträge, die einem Mischer zugeordnet werden
USER_REQUIRED = ("holen", "holen_offen", "wiederoeffnen", "sicherung_oeffnen", "archiv_import", "havarie_holen",
                 "havarie_ausliefern", "zurueckliefern")


def register_all(bridge: Bridge, ctx: AppContext, runner: JobRunner | None = None) -> None:
    """Alle Befehle des Fensters anmelden – je Thema im eigenen Modul."""
    from tonmischung.app import (archive_app, backups_app, catchup_app, deliver_app, fetch_app, folder_app,
                                 havarie_app, mixes_app, profiles_app, rooms_app, templates_app,
                                 users_app, zentrale_app)
    bridge.register("ping", lambda args: {"pong": True, "version": ctx.version})
    bridge.register("status", lambda args: status(ctx))
    bridge.register("list", lambda args: list_contributions(ctx, args))
    bridge.register("detail", lambda args: contribution_detail(ctx, args))
    bridge.register("release", lambda args: release(ctx, args))
    bridge.register("duplizieren", lambda args: duplicate(ctx, args))
    bridge.register("ordner_waehlen", lambda args: choose_folder(args))
    bridge.register("ueber", lambda args: about(ctx))
    bridge.register("link_oeffnen", lambda args: open_link(args))
    _register_settings(bridge, ctx, runner)
    for module in (catchup_app, mixes_app, profiles_app, templates_app, rooms_app, folder_app,
                   zentrale_app):
        module.register(bridge, ctx)
    for module in (fetch_app, deliver_app, backups_app, archive_app, havarie_app, users_app):
        module.register(bridge, ctx, runner)
    if runner is not None:
        bridge.register("auftrag", lambda args: runner.current())
        bridge.register("auftrag_warten", lambda args: wait_for_job(runner, args))
        bridge.register("antworten", lambda args: runner.answer(args["id"], args.get("value")) or {"ok": True})
        bridge.register("auftrag_abbrechen", lambda args: runner.cancel(args.get("id")))
        bridge.guard(NOT_DURING_JOB, lambda: _require_idle(runner))
        bridge.guard(USER_REQUIRED, lambda: users_app.require_user(ctx))


# Nicht während eines Auftrags (Review B25): Nutzer umbenennen/löschen änderte den Mischer mitten im Auftrag (Helmut
# meldete dann „gehört nicht mehr“), Template löschen das gerade benutzte, Raumliste und Einstellungsimport den Raum.
NOT_DURING_JOB = ("vorlage_loeschen", "nutzer_umbenennen", "nutzer_loeschen")


def _require_idle(runner: JobRunner) -> None:
    title = runner.running_title()
    if title:
        raise CommandError(f"Während eines Auftrags nicht möglich – bitte warten, bis „{title}“ fertig ist")


# ── Kleines fürs Fenster ─────────────────────────────────────────────────────────
def about(ctx: AppContext) -> dict:
    """„Über Ottilie“: Version und mitgelieferte Software mit Lizenz, Urheber, Projekt-Link."""
    from tonmischung.about import KINDS, listing
    return {"version": ctx.version, "groups": list(KINDS.values()), "components": listing()}


def open_link(args: dict) -> dict:
    """Projekt-Link im Standardbrowser öffnen – nur http(s)."""
    url = str(args.get("url") or "")
    if not url.startswith(("https://", "http://")):
        raise CommandError("Kein Web-Link")
    subprocess.run(["/usr/bin/open", url], timeout=10)
    return {"ok": True}


def choose_folder(args: dict) -> dict:
    """Ordnerauswahl von macOS (AppleScript „choose folder“); Abbrechen → leerer Pfad."""
    from tonmischung.app import choosers
    return {"path": choosers.choose_folder(str(args.get("prompt") or "Übergabepaket wählen"))}


# ── Einstellungen (M6b) ───────────────────────────────────────────────────────
MODE_SETTING = "system.uebergabeordner.betriebsart"


ONLY_API_DUPLICATE = "Duplizieren geht nur mit der Helmut-API."


def mode_switch_blocker(ctx: AppContext, runner: JobRunner | None) -> str:
    """Darf die Betriebsart (oder die Liste der Übergabeordner) jetzt wechseln? Grund dagegen oder ""."""
    return work_blocker(ctx, runner, strict=False)


def room_switch_blocker(ctx: AppContext, runner: JobRunner | None) -> str:
    """Darf dieser Mac jetzt den Raumnamen wechseln? Strenger als beim Umschalten (Ausfall 13.11.2026): ist Helmut
    bzw. ein Übergabeordner nicht lesbar, gilt das als „in Arbeit“ – sonst gehörte ein übernommener Film danach
    keinem Raum mehr."""
    return work_blocker(ctx, runner, strict=True)


def work_blocker(ctx: AppContext, runner: JobRunner | None, strict: bool = False) -> str:
    """Grund, warum hier gerade etwas in Arbeit ist: laufender Auftrag, Übernahmen dieses Raums (jedes Mischers und
    jedes Benutzers des Macs), offene Nachträge – oder "" ."""
    from tonmischung.dropfolder.mode import switch_blocker
    job = runner.running_title() if runner is not None else ""
    s = ctx.queue.summary() if ctx.queue is not None else {}
    open_entries = s.get("offen", 0) + len(s.get("konflikte", [])) + len(s.get("ohne_beitrag", []))
    where = "Übergabeordner" if ctx.folder_mode else "Helmut"
    unknown = ""
    try:
        folder = getattr(ctx.helmut, "folder", None) if ctx.folder_mode else None
        if folder is not None and hasattr(folder, "list_with_problems"):
            items, problems = folder.list_with_problems()
        else:
            items, problems = ctx.helmut.list_for_mixing(), []
        owned = [c.title for c in items if c.mix.state in mixstate.OWNED and c.mix.room == ctx.room]
        missing = [p["name"] for p in problems]
        if missing and strict:
            # Raumwechsel: ein fehlender Übergabeordner könnte einen übernommenen Film verbergen (Ausfall
            # 13.11.2026). Beim Ändern der Betriebsart oder der Ordnerliste nicht – sonst ließe sich ein kaputter
            # Ordner nie entfernen (Nachprüfung); bekannte Übernahmen stehen ohnehin mit dem letzten Stand in der Liste.
            unknown = (f"Übergabeordner {', '.join(missing)} nicht erreichbar – ob dort noch etwas übernommen ist, "
                       "lässt sich nicht prüfen. Später, sobald er wieder erreichbar ist")
    except Exception as e:                           # noqa: BLE001 – auch kaputte Daten dürfen nicht durchwinken
        owned = []
        # Nicht erreichbar: ob hier noch ein Film übernommen ist, ist unbekannt – dann nicht umschalten (Review 5,
        # Ausfall F8). Die Betriebsart Helmut-API bleibt beim Umschalten von Hand, wie sie war (Review 6,
        # 2. Runde); für den Raumwechsel gilt es in beiden Betriebsarten.
        if strict or (ctx.folder_mode and (isinstance(e, HelmutUnavailableError) or not isinstance(e, HelmutError))):
            unknown = (f"{where} ist nicht erreichbar – ob hier noch etwas übernommen ist, lässt sich nicht prüfen. "
                       "Später, sobald er wieder erreichbar ist")
    return switch_blocker(job, open_entries, owned) or unknown


def _register_settings(bridge: Bridge, ctx: AppContext, runner: JobRunner | None = None) -> None:
    from tonmischung.app import settings_view as sv
    from tonmischung.config.settings import load_schema
    schema = load_schema()
    bridge.register("einstellungen", lambda args: {"areas": sv.areas(schema)})

    # Betriebsart: gibt die Zentrale vor (Jens 13.11.2026) – dann steht sie in der Schicht „zentral“ und ist gesperrt.
    # Der Raumname gilt nur ohne Zentrale: an einem angemeldeten Mac gesperrt (Barrierefreiheit 13.11.2026: sonst
    # hieß es „gespeichert“, obwohl der Wert nichts bewirkte).
    def section(args: dict) -> dict:
        view = sv.section(ctx.settings, schema, args["id"])
        link = ctx.extra.get("zentrale")
        if args["id"] == "system.raeume_nutzer" and link is not None and link.state not in ("aus", "nicht_gekoppelt"):
            for group in view.get("groups", []):
                for f in group.get("fields", []):
                    if f.get("path") == "system.raeume_nutzer.raumname":
                        f["locked"] = True
                        f["hints"] = list(f.get("hints") or []) + [
                            "Gilt nur ohne Zentrale – den Raum legt die Administration der Zentrale fest"]
        return view

    def set_value(args: dict) -> dict:
        path = args["path"]
        if path == MODE_SETTING and args.get("value") != ctx.settings.get(path):
            why = mode_switch_blocker(ctx, runner)
            if why:
                raise CommandError(f"Betriebsart nicht umgeschaltet: {why}")
        before = ctx.settings.get(path) if path in ctx.settings.fields else None
        view = sv.set_value(ctx.settings, schema, path, args.get("value"))
        if path == "export.spuren_lautheit.exportnamen" and not view["errors"]:
            from tonmischung.app import profiles_app
            renamed = profiles_app.rename_export_names(ctx, before or [], ctx.settings.get(path) or [])
            if renamed:
                view = {**view, "hints": view["hints"] + [f"In den Spurprofilen umbenannt: {renamed}"]}
        return view
    bridge.register("einstellungen_abschnitt", section)
    bridge.register("einstellung_setzen", set_value)

    def reset_value(args: dict) -> dict:
        if args["path"] == MODE_SETTING:
            why = mode_switch_blocker(ctx, runner)
            if why:
                raise CommandError(f"Betriebsart nicht umgeschaltet: {why}")
        return sv.reset_value(ctx.settings, schema, args["path"])
    bridge.register("einstellung_zuruecksetzen", reset_value)
