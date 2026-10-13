"""Stände einer Session im Fenster: „Sicherungen …“ ansehen, im Finder zeigen, einen Stand als Kopie öffnen – und
nach dem Freigeben den zuletzt gespeicherten Stand öffnen (Plan M7)."""
from __future__ import annotations

import os
import subprocess

from tonmischung.app import viewmodel
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import JobContext, JobRunner
from tonmischung.app.shared import confirming, contribution, is_delivered, placement
from tonmischung.core import mixstate
from tonmischung.core.storage import LOCAL, MIRROR

OPEN_TEXTS = ("wird der Stand geöffnet", "Speichern, schließen und öffnen",
              "Nicht geöffnet – die offene Session bleibt, wie sie ist")


def backups(ctx: AppContext, args: dict) -> dict:
    """„Sicherungen …“: Stände der Session; öffnen nur, wenn der Beitrag nicht anderswo übernommen ist."""
    from tonmischung.core.backups import list_states
    c = contribution(ctx, str(args["id"]))
    m = c.mix
    if not m.session_path:
        raise CommandError("Am Beitrag ist keine Session gespeichert")
    states = list_states(m.session_path)
    busy_else = m.state in mixstate.OWNED and not viewmodel.is_mine(c, ctx.mixer, ctx.room)
    mirrored = False
    if not states and m.session_storage == MIRROR and m.session_mirror:
        states = list_states(os.path.join(m.session_mirror, os.path.basename(m.session_path)))
        mirrored = bool(states)
    if mirrored:
        avail = ("Angezeigt werden die gespiegelten Stände; neuere Sicherungen seit dem letzten Spiegeln fehlen. "
                 "Weiterarbeiten: freigeben und beim Übernehmen „Letzten gespeicherten Stand öffnen“.")
    elif not states:
        avail = (f"Die Session ist von diesem Mac aus nicht erreichbar ({m.session_path})"
                 + (f" – sie liegt wohl lokal in {m.room}." if m.session_storage == LOCAL and m.room else "."))
    elif busy_else:
        avail = (f"Der Beitrag ist in {m.room} bei {m.mixer} übernommen. Zum Öffnen hier erst freigeben; "
                 "ansehen und im Finder zeigen geht.")
    else:
        avail = "Session und Sicherungen sind von diesem Mac aus erreichbar."
    folder = os.path.dirname(states[0].path) if states else os.path.dirname(m.session_path)
    return {"id": c.id, "title": c.title, "folder": folder, "reachable": bool(states),
            "can_open": bool(states) and not busy_else and not mirrored, "availability": avail,
            "rows": [s.row() for s in states],
            "hint": "Öffnen einer Sicherung legt eine Kopie „…_wiederhergestellt_hhmm.ptx“ an und öffnet sie; das "
                    "Original und die Sicherungen bleiben unverändert. Eine andere offene Session wird vorher "
                    "gespeichert und geschlossen."}


def reveal(ctx: AppContext, args: dict) -> dict:
    """Im Finder zeigen – nur die Session eines Beitrags, ihre Stände oder ihren Ordner."""
    from tonmischung.core.backups import belongs_to
    c = contribution(ctx, str(args["id"]))
    session = c.mix.session_path
    mirrored = os.path.join(c.mix.session_mirror, os.path.basename(session)) if c.mix.session_mirror and session else ""
    path = str(args.get("path") or (session if session and os.path.exists(session) else mirrored) or "")
    ok = any(base and (belongs_to(path, base) or os.path.normpath(path) == os.path.normpath(base))
             for base in (session, mirrored))
    if not session or not ok:
        raise CommandError("Nur die Session dieses Beitrags und ihre Stände lassen sich zeigen")
    if not os.path.exists(path):
        raise CommandError(f"Nicht erreichbar: {path}")
    subprocess.run(["/usr/bin/open", "-R", path], check=False)
    return {"ok": True, "path": path}


def start_restore(ctx: AppContext, runner: JobRunner, args: dict) -> dict:
    """Eine Sicherung als Kopie öffnen (Auftrag)."""
    from tonmischung.workflow.reopen import RESTORE_STEPS, restore_state
    pid, path = str(args["id"]), str(args.get("path") or "")
    title = contribution(ctx, pid).title

    def work(jc: JobContext) -> dict:
        r = confirming(jc, lambda confirmed: restore_state(
            pid, path, service=ctx.service(), ptsl=ctx.ptsl, confirmed=confirmed,
            is_delivered=lambda p: is_delivered(ctx, p), progress=jc.progress), *OPEN_TEXTS)
        return {"session": r.session_path, "kind": r.kind, "tracks": 0, "warnings": list(r.warnings)}
    return runner.start("wiederoeffnen", f"Stand öffnen: {title}", RESTORE_STEPS, work, contribution=pid)


def start_reopen(ctx: AppContext, runner: JobRunner, args: dict) -> dict:
    """Gespeicherten Stand als Kopie öffnen (freigegeben mit „Letzter Stand“ bzw. Duplikat mit Mischstand)."""
    from tonmischung.workflow.reopen import REOPEN_STEPS, reopen
    pid = str(args["id"])
    c = contribution(ctx, pid)
    title, pl = c.title, placement(ctx, c)

    def work(jc: JobContext) -> dict:
        r = confirming(jc, lambda confirmed: reopen(
            pid, service=ctx.service(), ptsl=ctx.ptsl, session_parent=pl.parent, session_storage=pl.mode,
            mirror_parent=pl.mirror_parent, confirmed=confirmed, is_delivered=lambda p: is_delivered(ctx, p),
            progress=jc.progress), *OPEN_TEXTS)
        return {"session": r.session_path, "kind": r.kind, "tracks": r.tracks, "warnings": list(r.warnings)}
    return runner.start("wiederoeffnen", f"Stand öffnen: {title}", REOPEN_STEPS, work, contribution=pid)


def register(bridge: Bridge, ctx: AppContext, runner: JobRunner | None) -> None:
    bridge.register("sicherungen", lambda args: backups(ctx, args))
    bridge.register("im_finder", lambda args: reveal(ctx, args))
    if runner is not None:
        bridge.register("wiederoeffnen", lambda args: start_reopen(ctx, runner, args))
        bridge.register("sicherung_oeffnen", lambda args: start_restore(ctx, runner, args))
