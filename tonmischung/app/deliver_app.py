"""Zurückliefern im Fenster: Übersicht vorher (Exportspuren, Lautheit, Ziel) und der Auftrag selbst."""
from __future__ import annotations

import os

from tonmischung import factory
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import JobContext, JobRunner
from tonmischung.app.mixes_app import record_mix
from tonmischung.app.shared import confirming, contribution, is_delivered
from tonmischung.core import mix_log as ML
from tonmischung.core.session_rules import same_session
from tonmischung.ptsl.errors import PtslError

LOUDNESS = "export.spuren_lautheit.lautheit."


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
            "target": ctx.handover_root.replace("{projektId}", pid),
            "lufs": ctx.settings.get(LOUDNESS + "ziel_lufs"),
            "truepeak": ctx.settings.get(LOUDNESS + "true_peak_max_dbtp"),
            "loudness_on": bool(ctx.settings.get(LOUDNESS + "aktiv"))}


def start_deliver(ctx: AppContext, runner: JobRunner, args: dict) -> dict:
    """Zurückliefern im Auftrags-Thread; Frage bei anderer offener Session."""
    from tonmischung.workflow.deliver import DELIVER_STEPS, deliver
    pid = str(args["id"])
    export, loud = factory.export_tracks(ctx.settings, args.get("tracks"))
    if not export:
        raise CommandError("Keine Exportspuren angegeben")
    title = contribution(ctx, pid).title
    s = ctx.settings

    def work(jc: JobContext) -> dict:
        r = confirming(jc, lambda confirmed: deliver(
            pid, service=ctx.service(), ptsl=ctx.ptsl, export_tracks=export, loudness_tracks=loud,
            handover_root=ctx.handover_root, target_lufs=float(s.get(LOUDNESS + "ziel_lufs")),
            max_truepeak=float(s.get(LOUDNESS + "true_peak_max_dbtp")), confirmed=confirmed,
            is_delivered=lambda p: is_delivered(ctx, p), progress=jc.progress,
            export_names=factory.export_names(s), program_start=factory.program_start(s)),
            "wird die Session des Beitrags geöffnet", "Speichern, schließen und weiter",
            "Nicht zurückgeliefert – die offene Session bleibt, wie sie ist", title="Andere Session offen")
        if "spiegel" in ctx.extra:              # „beim Export“: den Stand danach spiegeln
            ctx.extra["spiegel"].run(reason="nach dem Zurückliefern")
        c = r.contribution
        folder = os.path.dirname(r.delivered[0]) if r.delivered else ""
        wavs = [os.path.basename(p) for p in r.delivered]
        record_mix(ctx, ML.MixRecord(ML.DELIVERED, c.title, c.id, c.show, ctx.mixer, ctx.room, c.mix.session_path,
                                     wavs, folder))
        return {"wavs": wavs, "folder": folder, "warnings": list(r.warnings)}
    return runner.start("zurueckliefern", f"Zurückliefern: {title}", DELIVER_STEPS, work, contribution=pid)


def register(bridge: Bridge, ctx: AppContext, runner: JobRunner | None) -> None:
    if runner is not None:
        bridge.register("zurueckliefern_info", lambda args: deliver_info(ctx, args))
        bridge.register("zurueckliefern", lambda args: start_deliver(ctx, runner, args))
