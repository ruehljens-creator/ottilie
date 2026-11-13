"""Zurückliefern im Fenster: Übersicht vorher (Exportspuren, Lautheit, Ziel) und der Auftrag selbst."""
from __future__ import annotations

import os
from typing import Callable

from tonmischung import factory
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import JobContext, JobRunner
from tonmischung.app.mixes_app import record_mix
from tonmischung.app.shared import confirming, contribution, is_delivered
from tonmischung.app.triggers_app import read_clipboard
from tonmischung.core import mix_log as ML
from tonmischung.core.session_rules import same_session
from tonmischung.delivery.manifest import VideoIdError, clean_video_id
from tonmischung.ptsl.errors import PtslError

LOUDNESS = "export.spuren_lautheit.lautheit."
VIDEOID = "export.notweg_bericht.videoid_"


def deliver_info(ctx: AppContext, args: dict) -> dict:
    """Übersicht vor dem Zurückliefern: Exportspuren, Lautheit, Ziel, fehlende Spuren."""
    pid = str(args["id"])
    c = contribution(ctx, pid)
    export, loud = factory.export_tracks(ctx.settings)
    missing, session_open, names = [], False, []
    try:
        open_s = ctx.ptsl.open_session()
        if open_s is not None and same_session(open_s.path, c.mix.session_path):
            session_open = True
            names = [t.name for t in ctx.ptsl.tracks() if t.type == "AudioTrack"]   # Reihenfolge wie in Pro Tools
            missing = [t for t in export if t not in names]
    except PtslError:
        pass
    return {"id": pid, "title": c.title, "session": c.mix.session_path, "session_open": session_open,
            "tracks": export, "loudness": loud, "missing": missing, "session_tracks": names,
            "target": ctx.target_text(pid),
            "lufs": ctx.settings.get(LOUDNESS + "ziel_lufs"),
            "truepeak": ctx.settings.get(LOUDNESS + "true_peak_max_dbtp"),
            "loudness_on": bool(ctx.settings.get(LOUDNESS + "aktiv")),
            "videoid": c.mix.video_id, "videoid_ask": _ask(ctx.settings),
            "videoid_clipboard": bool(ctx.settings.get(VIDEOID + "zwischenablage")),
            "videoid_required": bool(ctx.settings.get(VIDEOID + "pflicht")),
            "betriebsart": ctx.mode, "neue_fassung": _new_version(c)}


def _new_version(c) -> str:
    """Betriebsart Übergabeordner: Warnung, wenn inzwischen eine neuere Fassung übergeben wurde (Mischer-Prüfung:
    sonst ginge die alte Fassung unbemerkt raus)."""
    from tonmischung.app.viewmodel import new_handover_text
    return new_handover_text(c) if isinstance(c.raw, dict) and c.raw.get("neue_uebergabe") else ""


def start_deliver(ctx: AppContext, runner: JobRunner, args: dict) -> dict:
    """Zurückliefern im Auftrags-Thread; Frage bei anderer offener Session."""
    from tonmischung.workflow.deliver import DELIVER_STEPS, QUEUED_HINTS, deliver
    pid = str(args["id"])
    export, loud = factory.export_tracks(ctx.settings, args.get("tracks"))
    if not export:
        raise CommandError("Keine Exportspuren angegeben")
    c = contribution(ctx, pid)
    title, s = c.title, ctx.settings
    video_id = _video_id(s, args, c.mix.video_id)
    if _new_version(c) and not args.get("neue_fassung_ok"):
        raise CommandError(f"{_new_version(c)} Erst bestätigen (neue_fassung_ok), dann zurückliefern.")
    try:
        target = ctx.delivery_target(pid)          # Betriebsart Übergabeordner: „<Filmordner>/Audio Export“
    except Exception as e:                         # Filmordner nicht erreichbar
        raise CommandError(str(e)) from None

    def work(jc: JobContext) -> dict:
        r = confirming(jc, lambda confirmed: deliver(
            pid, service=ctx.service(), ptsl=ctx.ptsl, export_tracks=export, loudness_tracks=loud,
            handover_root=ctx.handover_root, target_lufs=float(s.get(LOUDNESS + "ziel_lufs")),
            max_truepeak=float(s.get(LOUDNESS + "true_peak_max_dbtp")), confirmed=confirmed,
            is_delivered=lambda p: is_delivered(ctx, p), progress=jc.progress,
            export_names=factory.export_names(s), program_start=factory.program_start(s),
            video_id=video_id, track_table=factory.track_table(s),
            video_id_required=bool(s.get(VIDEOID + "pflicht")), target=target),
            "wird die Session des Beitrags geöffnet", "Speichern, schließen und weiter",
            "Nicht zurückgeliefert – die offene Session bleibt, wie sie ist", title="Andere Session offen")
        if "spiegel" in ctx.extra:              # „beim Export“: den Stand danach spiegeln
            ctx.extra["spiegel"].run(reason="nach dem Zurückliefern")
        c = r.contribution
        folder = os.path.dirname(r.delivered[0]) if r.delivered else ""
        wavs = [os.path.basename(p) for p in r.delivered]
        record_mix(ctx, ML.MixRecord(ML.DELIVERED, c.title, c.id, c.show, ctx.mixer, ctx.room, c.mix.session_path,
                                     wavs, folder))
        warnings = list(r.warnings)
        if ctx.folder_mode:                     # „Helmut nicht erreichbar …“: das Banner nennt die Fertigmeldung (live)
            warnings = [w for w in warnings if w not in QUEUED_HINTS]
        return {"wavs": wavs, "folder": folder, "warnings": warnings,
                "metadaten": os.path.basename(r.metadata), "videoid": r.contribution.mix.video_id,
                "videoid_ask": _ask(s), "nachtrag": r.queued, "betriebsart": ctx.mode,
                "fertigmeldung": _done_state(ctx, r.queued)}
    return runner.start("zurueckliefern", f"Zurückliefern: {title}", DELIVER_STEPS, work, contribution=pid)


def _done_state(ctx: AppContext, queued: str) -> str:
    """Betriebsart Übergabeordner: Stand der Fertigmeldung fürs Ergebnis – gesendet | aus | wartet | abgelehnt |
    nachtrag (Meldung aus, aber die Sperrdatei ist noch nicht geschrieben)."""
    if not ctx.folder_mode:
        return ""
    if not ctx.helmut.folder.config.notify_active:
        # eingereiht trotz abgeschalteter Meldung: die Sperrdatei hakt – sonst fehlte jeder Hinweis (Review)
        return "nachtrag" if queued else "aus"
    return {"ausfall": "wartet", "abgelehnt": "abgelehnt"}.get(queued, "gesendet")


def _ask(s) -> bool:
    """Feld zeigen: wenn abgefragt – und immer, wenn die VideoID Pflicht ist (sonst käme man nicht weiter)."""
    return bool(s.get(VIDEOID + "abfragen") or s.get(VIDEOID + "pflicht"))


def _video_id(s, args: dict, known: str) -> str | None:
    """VideoID aus dem Dialog oder der Schnittstelle; None = der Wert in Helmut bleibt (nicht mitgegeben oder Feld
    geleert – ein vorhandener Wert verschwindet nie still). Ist sie Pflicht, muss am Ende eine da sein."""
    given = None
    if "videoid" in args:
        try:
            given = clean_video_id(args["videoid"]) or None
        except VideoIdError as e:
            raise CommandError(str(e)) from None
    if s.get(VIDEOID + "pflicht") and not (given or known):
        raise CommandError("VideoID fehlt – bitte im Feld VideoID eintragen. Ohne VideoID wird nicht zurückgeliefert.")
    return given


def paste_video_id(read: Callable[[], str] = read_clipboard) -> dict:
    """VideoID aus der Zwischenablage, geprüft wie die Eingabe (Ränder und abschließender Umbruch fallen weg)."""
    try:
        return {"videoid": clean_video_id(read())}
    except VideoIdError:
        raise CommandError("In der Zwischenablage steht mehr als eine VideoID (Zeilenumbruch oder Tab) oder ein zu "
                           "langer Text – bitte nur die VideoID kopieren") from None


def register(bridge: Bridge, ctx: AppContext, runner: JobRunner | None) -> None:
    if runner is not None:
        bridge.register("zurueckliefern_info", lambda args: deliver_info(ctx, args))
        bridge.register("zurueckliefern", lambda args: start_deliver(ctx, runner, args))
        bridge.register("videoid_einfuegen", lambda args: paste_video_id())
