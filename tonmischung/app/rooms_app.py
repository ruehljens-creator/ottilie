"""Räume im Fenster: Raumübersicht (wer mischt wo) und Räume über Mac-Namen (Plan Nutzer & Räume, Schritt 4:
Liste Raum ↔ Mac, Mac aus einer Auswahl)."""
from __future__ import annotations

import logging
import socket
from typing import Any

from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.core import rooms as R
from tonmischung.core.identity import short_host, this_room

log = logging.getLogger(__name__)

PATH = "system.raeume_nutzer.raeume"
P = "system.raeume_nutzer."


def _root(ctx: AppContext) -> str:
    from tonmischung.app.users_app import store_root
    return store_root(ctx)


def _apply(ctx: AppContext, rows: list[dict], hostname: str | None = None) -> None:
    """Raumliste gilt (Ebene „zentral“), Raum dieses Macs neu bestimmen."""
    ctx.settings.update_central({PATH: rows} if rows else {})
    s = ctx.settings
    room = this_room(rows, hostname or socket.gethostname(), s.get(P + "raum_erkennung"), s.get(P + "raum_fest") or "")
    ctx.room = room or short_host(hostname or socket.gethostname())


def startup(ctx: AppContext, hostname: str | None = None) -> None:
    """Raumliste aus der Ablage; gibt es dort keine, wird die bisherige Einstellung des Macs übernommen."""
    root = _root(ctx)
    ctx.extra["rooms_root"] = root
    ctx.extra.pop("rooms_problem", None)
    try:
        rows = R.load(root)
    except R.RoomsUnreadable as e:
        log.warning("%s – Raumliste bleibt, wie sie ist", e)
        ctx.extra["rooms_problem"] = str(e)
        _apply(ctx, [dict(r) for r in (ctx.settings.local_values().get(PATH) or [])], hostname)
        return
    if rows is None:
        rows = [dict(r) for r in (ctx.settings.local_values().get(PATH) or [])]
        if rows:
            try:
                R.save(root, rows)
                log.info("Raumliste in die Ablage übernommen: %d Räume", len(rows))
            except OSError as e:
                log.warning("Raumliste nicht gespeichert: %s", e)
    _apply(ctx, rows, hostname)


def view(ctx: AppContext, message: str = "", errors: list[str] | None = None) -> dict[str, Any]:
    # Ablage gewechselt bzw. erneut versuchen
    if ctx.extra.get("rooms_root") != _root(ctx) or ctx.extra.get("rooms_problem"):
        startup(ctx)
    rows = [dict(r) for r in (ctx.settings.get(PATH) or [])]
    peers = ctx.extra.get("peers")
    hosts = [p.host for p in peers.peers().values()] if peers is not None and getattr(peers, "available", False) else []
    problem = ctx.extra.get("rooms_problem")
    return {"rooms": rows, "macs": R.known_macs(socket.gethostname(), hosts, rows),
            "this_mac": short_host(socket.gethostname()), "this_room": ctx.room, "ablage": _root(ctx),
            "message": message, "errors": (errors or []) + ([f"{problem} – bitte prüfen; gespeichert wird erst nach "
                                                             "Behebung"] if problem else [])}


def save(ctx: AppContext, args: dict) -> dict[str, Any]:
    if ctx.extra.get("rooms_problem"):
        raise CommandError("Die Raumliste in der Ablage ist nicht lesbar – erst prüfen, sonst ginge sie verloren")
    rows, errors = R.normalize(args.get("rooms") or [])
    if errors:
        return view(ctx, "", errors)
    try:
        R.save(_root(ctx), rows)
    except OSError as e:
        raise CommandError(f"Raumliste nicht gespeichert: {e}") from e
    before = ctx.room
    _apply(ctx, rows)
    note = f"{len(rows)} Räume gespeichert" + (f" – dieser Mac ist jetzt „{ctx.room}“" if ctx.room != before else "")
    return view(ctx, note)


def overview(ctx: AppContext) -> dict:
    """Raumübersicht: Stand jedes Raums aus Helmut und direkt von den anderen Macs (Bonjour)."""
    from tonmischung.app.roomstatus import rooms
    from tonmischung.helmut.client import HelmutError
    try:
        return rooms(ctx, ctx.extra.get("peers"))
    except HelmutError as e:
        raise CommandError(f"Raumstatus braucht Helmut: {e}") from e


def register(bridge: Bridge, ctx: AppContext) -> None:
    bridge.register("raeume", lambda args: overview(ctx))
    bridge.register("raumliste", lambda args: view(ctx))
    bridge.register("raumliste_speichern", lambda args: save(ctx, args))
