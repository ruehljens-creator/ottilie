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
from tonmischung.core.mixstate import ConflictError
from tonmischung.helmut.client import HelmutError, HelmutUnavailableError
from tonmischung.ptsl.errors import PtslError


def status(ctx: AppContext) -> dict:
    """Titelleiste: Pro Tools (offene Session), Helmut, Mischer · Raum."""
    try:
        s = ctx.ptsl.open_session()
        pro_tools = {"ok": True, "session": s.name if s else None}
    except PtslError as e:
        pro_tools = {"ok": False, "error": str(e), "halted": getattr(ctx.ptsl, "halted", "")}
    try:
        ctx.helmut.reachable()
        helmut = {"ok": True, "mock": ctx.mock is not None}
    except HelmutError as e:
        helmut = {"ok": False, "error": str(e), "mock": ctx.mock is not None}
    return {"version": ctx.version, "pro_tools": pro_tools, "helmut": helmut,
            "mixer": ctx.mixer, "room": ctx.room,
            "user": {"id": ctx.user.id, "name": ctx.user.name} if ctx.user is not None else None,
            "theme": str(ctx.settings.get("persoenlich.farbschema") or "ottilie"),       # Farbschema des Nutzers
            "nachtrag": ctx.queue.summary() if ctx.queue is not None else {"offen": 0, "konflikte": []},
            "fremd": ctx.extra["fremd"].state if "fremd" in ctx.extra else None,
            "spiegel": ctx.extra["spiegel"].state if "spiegel" in ctx.extra else None}


def list_contributions(ctx: AppContext, args: dict) -> dict:
    try:
        items = ctx.helmut.list_for_mixing()
    except HelmutError as e:
        raise CommandError(f"Helmut: {e}") from e
    return viewmodel.rows(items, ctx.mixer, ctx.room, args.get("query", ""), args.get("order", "zeit"))


def contribution_detail(ctx: AppContext, args: dict) -> dict:
    pid = str(args["id"])
    c = contribution(ctx, pid)
    try:
        handover = ctx.helmut.handover(pid)
    except HelmutError:
        handover = None
    return viewmodel.detail(c, handover, ctx.mixer, ctx.room)


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
    hints = notify_release(ctx, before, reason, keep) if args.get("notify", True) else []
    out = contribution_detail(ctx, args)
    out["hinweise"] = hints
    return out


def duplicate(ctx: AppContext, args: dict) -> dict:
    """Duplikat anlegen (Zweck → Titelvorschlag, Mischstand übernehmen, sofort übernehmen)."""
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
        except HelmutUnavailableError as e:
            if ctx.queue is None:
                hints.append(f"Benachrichtigung an {user} nicht gesendet: {e}")
                continue
            ctx.queue.add_notify(before.id, before.title, user, title, text)       # Nachtrag kennt Nachrichten
            hints.append(f"Benachrichtigung an {user} wird nachgetragen, sobald Helmut wieder da ist")
        except HelmutError as e:
            hints.append(f"Benachrichtigung an {user} nicht gesendet: {e}")
    return hints


# Nur mit angemeldetem Nutzer (Plan Nutzer & Räume): Aufträge, die einem Mischer zugeordnet werden
USER_REQUIRED = ("holen", "holen_offen", "wiederoeffnen", "sicherung_oeffnen", "archiv_import", "havarie_holen",
                 "havarie_ausliefern", "zurueckliefern")


def register_all(bridge: Bridge, ctx: AppContext, runner: JobRunner | None = None) -> None:
    """Alle Befehle des Fensters anmelden – je Thema im eigenen Modul."""
    from tonmischung.app import (archive_app, backups_app, catchup_app, deliver_app, fetch_app, havarie_app,
                                 mixes_app, profiles_app, rooms_app, settings_transfer_app, templates_app, users_app)
    bridge.register("ping", lambda args: {"pong": True, "version": ctx.version})
    bridge.register("status", lambda args: status(ctx))
    bridge.register("list", lambda args: list_contributions(ctx, args))
    bridge.register("detail", lambda args: contribution_detail(ctx, args))
    bridge.register("release", lambda args: release(ctx, args))
    bridge.register("duplizieren", lambda args: duplicate(ctx, args))
    bridge.register("ordner_waehlen", lambda args: choose_folder(args))
    bridge.register("ueber", lambda args: about(ctx))
    bridge.register("link_oeffnen", lambda args: open_link(args))
    _register_settings(bridge, ctx)
    for module in (catchup_app, mixes_app, profiles_app, settings_transfer_app, templates_app, rooms_app):
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
NOT_DURING_JOB = ("raumliste_speichern", "einstellungen_importieren", "vorlage_loeschen", "nutzer_umbenennen",
                  "nutzer_loeschen")


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
def _register_settings(bridge: Bridge, ctx: AppContext) -> None:
    from tonmischung.app import settings_view as sv
    from tonmischung.config.settings import load_schema
    schema = load_schema()
    bridge.register("einstellungen", lambda args: {"areas": sv.areas(schema)})
    bridge.register("einstellungen_abschnitt", lambda args: sv.section(ctx.settings, schema, args["id"]))

    def set_value(args: dict) -> dict:
        path = args["path"]
        before = ctx.settings.get(path) if path in ctx.settings.fields else None
        view = sv.set_value(ctx.settings, schema, path, args.get("value"))
        if path == "export.spuren_lautheit.exportnamen" and not view["errors"]:
            from tonmischung.app import profiles_app
            renamed = profiles_app.rename_export_names(ctx, before or [], ctx.settings.get(path) or [])
            if renamed:
                view = {**view, "hints": view["hints"] + [f"In den Spurprofilen umbenannt: {renamed}"]}
        return view
    bridge.register("einstellung_setzen", set_value)
    bridge.register("einstellung_zuruecksetzen", lambda args: sv.reset_value(ctx.settings, schema, args["path"]))
