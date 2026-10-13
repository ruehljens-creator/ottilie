"""Dialog „Archiv und fremde Dateien …“ (Plan M7): fremde Audiodateien der offenen Session ansehen, neue Dateien aus
den Archiv-Ordnern importieren. Das regelmäßige Prüfen und Übernehmen fremder Dateien steht in `foreign_app.py`."""
from __future__ import annotations

import os
import time
from datetime import datetime

from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import JobContext, JobRunner
from tonmischung.ptsl.errors import PtslError

ARCHIVE_DAYS = 7          # ANNAHME F5: „neu“ im Archiv-Ordner = in den letzten 7 Tagen geändert


def archive_info(ctx: AppContext, args: dict) -> dict:
    """Fremde Dateien der offenen Session und neue Archiv-Dateien."""
    from tonmischung.core.foreign import DONE, MISSING, NEW, archive_candidates, plan
    label = {NEW: "nur lokal", DONE: "übernommen", MISSING: "fehlt"}
    s = ctx.settings
    try:
        opened = ctx.ptsl.open_session()
        subfolder = s.get("import.session_ablage.fremde_dateien.unterordner") or "Audio Files/Extern/"
        files = plan(ctx.ptsl.file_locations(), opened.path, subfolder) if opened else []
    except PtslError as e:
        raise CommandError(f"Pro Tools: {e}") from e
    folders = s.get("import.session_ablage.archiv_ordner") or []
    archive = archive_candidates(folders, time.time() - ARCHIVE_DAYS * 86400)
    return {"session": opened.name if opened else "", "folders": [os.path.expanduser(f) for f in folders],
            "foreign": [{"name": os.path.basename(f.source), "path": f.source, "state": label[f.status],
                         "copy": f.target} for f in files],
            "archive": [{"name": os.path.basename(p), "path": p, "folder": os.path.dirname(p),
                         "time": datetime.fromtimestamp(t).strftime("%d.%m. %H:%M")} for p, t in archive],
            "days": ARCHIVE_DAYS}


def start_archive_import(ctx: AppContext, runner: JobRunner, args: dict) -> dict:
    """Archiv-Dateien importieren – an der Einfügemarke (neue Spur) oder in die Clip-Liste; Pro Tools
    kopiert sie dabei in den Session-Ordner, eine spätere Übernahme ist dann nicht nötig."""
    from tonmischung.core.foreign import AUDIO_EXT
    paths = [str(p) for p in (args.get("paths") or [])]
    allowed = [os.path.normpath(os.path.expanduser(f))
               for f in (ctx.settings.get("import.session_ablage.archiv_ordner") or [])]
    bad = [p for p in paths if not p.lower().endswith(AUDIO_EXT) or os.path.dirname(os.path.normpath(p)) not in allowed]
    if not paths or bad:
        raise CommandError("Nur Audiodateien aus den Archiv-Ordnern lassen sich importieren"
                           + (f": {', '.join(os.path.basename(b) for b in bad)}" if bad else ""))
    to_clips = args.get("ziel") == "clipliste"

    def work(jc: JobContext) -> dict:
        opened = ctx.ptsl.open_session()
        if opened is None:
            raise CommandError("In Pro Tools ist keine Session offen")
        jc.progress.step("Importieren", f"{len(paths)} Dateien")
        ctx.ptsl.import_audio(paths, to_clip_list=to_clips,
                              copy_to=os.path.join(os.path.dirname(opened.path), "Audio Files"))
        return {"imported": [os.path.basename(p) for p in paths], "session": opened.name,
                "where": "Clip-Liste" if to_clips else "neue Spur an der Einfügemarke", "warnings": []}
    return runner.start("archiv", f"Archiv-Import: {len(paths)} Dateien", [("Importieren", 1.0)], work)


def register(bridge: Bridge, ctx: AppContext, runner: JobRunner | None) -> None:
    bridge.register("archiv", lambda args: archive_info(ctx, args))
    if runner is not None:
        bridge.register("archiv_import", lambda args: start_archive_import(ctx, runner, args))
