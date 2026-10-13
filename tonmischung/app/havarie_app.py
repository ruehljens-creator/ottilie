"""Havarie im Fenster: „Paket manuell öffnen“ (M9) und „Sendefile erstellen und ausliefern“ (Plan M5, Schritt 6).

Wenn Helmut ausfällt: Das Übergabepaket wird ohne Helmut geholt (derselbe Ablauf wie „Holen“; Übernahme und Zustand
gehen in die Nachtrag-Warteschlange). Danach baut Ottilie das Sendefile selbst – nur im Havariemodus (Jens
03.10.2026). Der Mischer wählt Paketordner (Bild neben dem AAF), Preset und Ziele; Spuren und Kanäle kommen aus
Spurtabelle bzw. Spurprofil – nichts wird getippt. Ziele vorerst (Jens 03.10.2026, Havarie-Ziele noch unbekannt):
Schreibtisch (vorgewählt) und USB-Sticks, dazu bei Bedarf gewählte Ordner.
"""
from __future__ import annotations

import os
import shutil
import time
from typing import Any

from tonmischung import factory
from tonmischung.app import fetch_app, templates_app
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import JobContext, JobRunner
from tonmischung.app.mixes_app import record_mix
from tonmischung.app.shared import confirming
from tonmischung.core import mix_log as ML
from tonmischung.delivery import mux as M
from tonmischung.delivery import tools as W
from tonmischung.delivery import transfer as T
from tonmischung.delivery.presets import DEFAULT_PRESETS
from tonmischung.workflow import havarie_delivery as H

DEFAULT_PRESET = "xdcam16"


def _tools(ctx: AppContext) -> H.Tools:
    custom = W.configured({p: ctx.settings.get(p) for p in ctx.settings.fields if p.startswith(W.SETTINGS_PREFIX)})
    return H.Tools(W.find("ffmpeg", custom), W.find("ffprobe", custom), W.find("bmxtranswrap", custom),
                   W.find("mediainfo", custom))


def _package(path: str):
    """Übergabepaket im Ordner (AAF darin oder eine Ebene tiefer) – sonst eine klare Meldung."""
    from tonmischung.core.havarie import find_package
    path = str(path or "").strip()
    if not path:
        raise CommandError("Bitte einen Paketordner wählen")
    if not os.path.isdir(os.path.expanduser(path)):
        raise CommandError(f"Ordner nicht gefunden: {path}")
    pkg = find_package(path)
    if pkg is None:
        raise CommandError("Im Ordner (und eine Ebene darunter) liegt kein AAF")
    return pkg


# ── Paket manuell öffnen (M9) ──────────────────────────────────────────────────────
def package_info(ctx: AppContext, args: dict) -> dict:
    """Paket im Ordner erkennen: AAF (Pflicht), Video, Titel- und ID-Vorschlag."""
    pkg = _package(args.get("path"))
    return {"folder": pkg.folder, "aaf": pkg.aaf, "video": pkg.video, "title": pkg.title,
            "project_id": pkg.project_id, "complete": pkg.complete,
            "hint": "" if pkg.video else "Kein Referenzvideo im Paket – Import ohne Video."}


def start_open_package(ctx: AppContext, runner: JobRunner, args: dict) -> dict:
    """Paket ohne Helmut holen – derselbe Ablauf wie „Holen“; Übernahme und Zustand werden nachgetragen."""
    from tonmischung.core.storage import LOCAL, Placement, StorageError
    from tonmischung.helmut.offline import OfflineService
    from tonmischung.workflow.fetch import FETCH_STEPS, fetch
    if ctx.queue is None:
        raise CommandError("Ohne Nachtrag-Warteschlange keine Havarie (Übernahme könnte nicht nachgetragen werden)")
    pkg = _package(args.get("path"))
    group, name = args.get("group", ""), args.get("name", "")
    if not group or not name:
        raise CommandError("Bitte ein Template wählen")
    templates_app.ensure_installed(ctx, group, name)
    title = str(args.get("title") or pkg.title).strip() or pkg.title
    project_id = str(args.get("project_id") or "").strip()
    havarie_id = f"havarie-{time.strftime('%Y%m%d-%H%M%S')}"
    service = OfflineService(pkg, title, project_id, havarie_id, ctx.mixer, ctx.room, ctx.queue)
    hints = []
    try:
        pl = ctx.placement(service.contribution)
    except StorageError as e:                     # Projektordner ohne Helmut unbekannt → lokal
        pl = Placement(LOCAL, ctx.session_parent, hint=f"{e} – Havarie-Session liegt lokal")
    if pl.hint:
        hints.append(pl.hint)
    use_triage = bool(args.get("triage", ctx.settings.get("import.zuordnungsregeln.aktiv")))
    options = factory.triage_options(ctx.settings) if use_triage else None
    import_opts = fetch_app.import_options(ctx)
    start = factory.program_start(ctx.settings)

    def work(jc: JobContext) -> dict:
        r = confirming(jc, lambda confirmed: fetch(
            havarie_id, service=service, ptsl=ctx.ptsl, template_group=group, template_name=name,
            session_parent=pl.parent, session_storage=pl.mode, mirror_parent=pl.mirror_parent,
            confirmed=confirmed, with_video=bool(args.get("video", True)) and bool(pkg.video),
            progress=jc.progress, decide_missing=fetch_app.ask_missing(jc), triage=options,
            confirm_triage=fetch_app.confirm_triage(jc, ctx, f"{group}/{name}"), goto=start, program_start=start,
            import_options=import_opts, track_view=fetch_app.track_view(ctx),
            confirm_media=fetch_app.ask_media(jc)), *fetch_app.IMPORT_TEXTS)
        r.warnings[:0] = hints
        r.warnings += fetch_app.preset_hint(import_opts)
        try:                                   # für „Sendefile ausliefern“: Paket dieser Session merken
            package_index(ctx).remember(r.session_path, pkg.folder)
        except OSError as e:
            r.warnings.append(f"Paketordner nicht gemerkt: {e}")
        return {**fetch_app.fetch_summary(r), "havarie": havarie_id, "project_id": project_id}
    return runner.start("havarie", f"Havarie: {title}", FETCH_STEPS, work, contribution=project_id)


# ── Sendefile erstellen und ausliefern (M5) ────────────────────────────────────────

def _preset(pid: str) -> dict:
    return next((p for p in DEFAULT_PRESETS if p["id"] == pid), DEFAULT_PRESETS[0])


def package_index(ctx: AppContext):
    from tonmischung.core.havarie import PackageIndex, index_path
    return ctx.extra.setdefault("havarie_pakete", PackageIndex(index_path(mock=ctx.mock is not None)))


def info(ctx: AppContext, args: dict, volumes: Any = None) -> dict[str, Any]:
    """Alles für den Dialog: Session, Bilder neben dem AAF, Presets, Kanäle, USB-Datenträger, Werkzeuge.

    Ohne Pfad: Paketordner der offenen Session vorschlagen (gemerkt beim Havarie-Import); `auto` = still, wenn
    keiner bekannt ist."""
    from tonmischung.delivery.picture import PictureError, candidates
    path = str(args.get("path") or "")
    if not path:
        try:
            s = ctx.ptsl.open_session()
        except Exception:  # noqa: BLE001 – Pro Tools nicht erreichbar
            s = None
        path = package_index(ctx).lookup(s.path) if s else ""
        if not path and args.get("auto"):
            return {"path": ""}
    pkg = _package(path)
    try:
        pictures = list(candidates(pkg.aaf))
    except PictureError:
        pictures = []
    session = None
    try:
        s = ctx.ptsl.open_session()
        session = {"name": s.name, "path": s.path} if s else None
    except Exception:  # noqa: BLE001 – Pro Tools nicht erreichbar: im Dialog melden
        session = None
    table = ctx.settings.get("export.spuren_lautheit.spurtabelle") or []
    try:
        names = H.channel_tracks(table, 16)
    except H.HavarieDeliveryError as e:
        raise CommandError(str(e)) from e
    rows = {str(r.get("pt_spur") or ""): r for r in table}
    channels = [{"kanaele": f"{i + 1}/{i + 2}", "spur": names[i] if names[i] != H.SILENCE else "",
                 "exportname": str((rows.get(names[i]) or {}).get("exportname") or ""),
                 "inhalt": str((rows.get(names[i]) or {}).get("inhalt") or "")} for i in range(0, 16, 2)]
    tools = _tools(ctx)
    missing = [n for n, p in (("ffmpeg", tools.ffmpeg), ("ffprobe", tools.ffprobe)) if not p]
    return {"path": path, "aaf": pkg.aaf, "title": pkg.title, "project_id": pkg.project_id, "pictures": pictures,
            "session": session, "presets": [{"id": p["id"], "name": p["name"], "profil": p["mxf_profil"]}
                                            for p in DEFAULT_PRESETS],
            "preset": DEFAULT_PRESET, "channels": channels, "profile": ctx.settings.profile_label,
            "usb": [_with_auto(v) for v in (volumes or T.removable_volumes)()], "missing_tools": missing,
            "bmx": bool(tools.bmx), "havarie_ordner": str(ctx.settings.get("export.havarie_zielordner") or ""),
            "schreibtisch": os.path.join(os.path.expanduser("~"), "Desktop", T.HAVARIE_FOLDER),
            "pruefung": bool(ctx.settings.get("export.pruefung.formatpruefung"))}


AUTO_USB_MIN_FREE = 1_000_000_000                 # kleinere Partitionen (z. B. RUFUS_BOOT, 1 MB) nicht vorwählen


def _with_auto(volume: dict, free: Any = None) -> dict:
    """USB-Stick automatisch vorwählen, wenn beschreibbar und genug Platz (Jens 04.10.2026) – nur Sticks, keine
    externen Platten (Review B4)."""
    try:
        space = (free or (lambda p: shutil.disk_usage(p).free))(volume["mount"])
    except OSError:
        space = 0
    stick = volume.get("kind", T.STICK) == T.STICK
    return {**volume, "auto": stick and bool(volume.get("writable")) and space >= AUTO_USB_MIN_FREE}


def _targets(args: dict, havarie_folder: str = "") -> list[T.Target]:
    out = []
    for z in args.get("ziele") or []:
        if z.get("art") == "schreibtisch":
            out.append(T.desktop_target())
        elif z.get("art") == "havarieordner" and havarie_folder:
            p = havarie_folder.rstrip("/")
            out.append(T.Target(f"havarie:{p}", f"Havarie-Ordner {os.path.basename(p) or p}", T.FOLDER, path=p,
                                retries=1))
        elif z.get("art") == "usb":
            out.append(T.usb_target({"mount": z["mount"], "name": z.get("name") or os.path.basename(z["mount"])}))
        elif z.get("art") == "ordner" and z.get("pfad"):
            p = str(z["pfad"]).rstrip("/")
            out.append(T.Target(f"ordner:{p}", os.path.basename(p) or p, T.FOLDER, path=p, retries=1))
    return out


def _queue_delivered(ctx: AppContext, session_path: str) -> str:
    """Zustand „ausgeliefert“ und Entsperren für Helmut nachtragen – über den Havarie-Eintrag mit dieser Session
    (wie beim Zurückliefern, Jens 09.10.2026)."""
    from tonmischung.core import mixstate
    if ctx.queue is None:
        return "Ohne Nachtrag-Warteschlange: Zustand „ausgeliefert“ nicht für Helmut vorgemerkt"
    entry = next((e for e in reversed(ctx.queue.entries())
                  if e.havarie and e.changes.get("session_path") == session_path), None)
    if entry is None:
        return "Zu dieser Session gibt es keinen Havarie-Eintrag – Zustand „ausgeliefert“ nicht vorgemerkt"
    before = mixstate.MixState(state=mixstate.IMPORTED, mixer=ctx.mixer, room=ctx.room, session_path=session_path)
    after = mixstate.after_delivery(before)
    # Das Sendefile liegt lokal neben der Session; Helmut erreicht es nicht – kein Asset, nur der Zustand
    ctx.queue.add_mix(entry.project_id, entry.title, before, after, ctx.mixer, ctx.room, havarie=entry.havarie)
    ctx.queue.add_lock(entry.project_id, entry.title, False, havarie=entry.havarie)
    return ""


def start(ctx: AppContext, runner: JobRunner, args: dict) -> dict:
    pkg = _package(str(args.get("path") or ""))
    targets = _targets(args, str(ctx.settings.get("export.havarie_zielordner") or ""))
    preset = _preset(str(args.get("preset") or DEFAULT_PRESET))
    tools = _tools(ctx)
    if not tools.ffmpeg or not tools.ffprobe:
        raise CommandError("ffmpeg/ffprobe fehlen – Einstellungen › Verbindungen › Werkzeuge")
    if str(preset.get("werkzeug") or "bmx") == "bmx" and not tools.bmx:
        raise CommandError(M.BMX_MISSING)               # vor Pro Tools und Export, nicht erst in der Prüfung (B36)
    try:
        s = ctx.ptsl.open_session()
    except Exception as e:  # noqa: BLE001
        raise CommandError(f"Pro Tools: {e}") from e
    if s is None:
        raise CommandError("Keine Session offen – bitte die Havarie-Session in Pro Tools öffnen")
    session_path = s.path
    req = H.Request(pkg.aaf, preset, targets, str(args.get("title") or pkg.title), show=str(args.get("sendung") or ""),
                    project_id=pkg.project_id, picture=str(args.get("bild") or ""))

    def work(jc: JobContext) -> dict:
        r = H.deliver_havarie(req, ptsl=ctx.ptsl, get=ctx.settings.get, tools=tools, session_path=session_path,
                              progress=jc.progress, ask=jc.ask)
        hint = _queue_delivered(ctx, session_path)
        if r.sendefile:                                  # Verzeichnis der Mischungen dieses Macs (Jens 09.10.2026)
            record_mix(ctx, ML.MixRecord(ML.HAVARIE, req.title, req.project_id, req.show, ctx.mixer, ctx.room,
                                         session_path, [os.path.basename(r.sendefile)], os.path.dirname(r.sendefile)))
        return {"sendefile": r.sendefile, "bericht": r.report[0], "pruefung": r.qc_overall or "nicht geprüft",
                "gestoppt": r.blocked, "uebertragen": [{"ziel": t.target.name, "ok": t.ok, "fehler": t.error,
                                                        "dateien": t.sent} for t in r.transfers],
                "warnings": r.warnings + ([hint] if hint else [])}
    return runner.start("havarie_ausliefern", f"Havarie-Sendefile: {req.title}", H.STEPS, work)


def register(bridge: Bridge, ctx: AppContext, runner: JobRunner | None) -> None:
    bridge.register("havarie_info", lambda args: package_info(ctx, args))
    bridge.register("havarie_ausliefern_info", lambda args: info(ctx, args))
    if runner is not None:
        bridge.register("havarie_holen", lambda args: start_open_package(ctx, runner, args))
        bridge.register("havarie_ausliefern", lambda args: start(ctx, runner, args))
