"""Räume im Fenster: Raumübersicht (wer mischt wo) und die einmalige Umstellung der früheren Raumliste.

Seit 13.11.2026 (Jens: „Raum mit Namen fest zuordnen – in der Zentrale auf der Adminseite“) gibt es keine Raumliste
in Ottilie mehr. Gekoppelt gilt der Raum der Zentrale, sonst „Raumname dieses Macs“ oder der Rechnername. Was die
alte Liste für diesen Mac enthielt (Name, Webtrigger-ID-Code), übernimmt `migrate_legacy` einmal in die neuen
Einstellungen.
"""
from __future__ import annotations

import logging
import os
import socket
from collections.abc import Mapping
from typing import Any

from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.core.fileio import CorruptFileError, read_json
from tonmischung.core.identity import short_host

log = logging.getLogger(__name__)

ROOM_NAME = "system.raeume_nutzer.raumname"
ID_CODE = "system.streamdeck_webtrigger.id_code"
LEGACY_ROOMS = "system.raeume_nutzer.raeume"        # bis 0.2: Raumliste (Raum ↔ Rechnername, ID-Code)
LEGACY_FILE = "raeume.json"                         # bis 0.2: Raumliste in der Ablage der Nutzer


def legacy_entry(local: Mapping[str, Any], shared: list | None, hostname: str) -> dict:
    """Eintrag dieses Macs aus der früheren Raumliste (lokal oder in der Ablage) bzw. dem festen Raum – {} ohne."""
    if local.get("system.raeume_nutzer.raum_erkennung") == "fest" and local.get("system.raeume_nutzer.raum_fest"):
        fixed = str(local["system.raeume_nutzer.raum_fest"])
        rows = list(shared or []) + list(local.get(LEGACY_ROOMS) or [])
        hit = next((r for r in rows if isinstance(r, Mapping) and fixed in (r.get("id"), r.get("name"))), {})
        return {"name": str(hit.get("name") or fixed), "id_code": str(hit.get("id_code") or "")}
    host = short_host(hostname)
    for rows in (shared or [], local.get(LEGACY_ROOMS) or []):
        for r in rows:
            if isinstance(r, Mapping) and short_host(str(r.get("rechnername") or "")) == host and r.get("name"):
                return {"name": str(r["name"]), "id_code": str(r.get("id_code") or "")}
    return {}


def _shared_rows(ctx: AppContext) -> list | None:
    """Frühere Raumliste in der Ablage der Nutzer – None ohne (oder unlesbar)."""
    from tonmischung.app.users_app import store_root
    try:
        data = read_json(os.path.join(store_root(ctx), LEGACY_FILE))
    except (CorruptFileError, OSError):
        return None
    rows = data.get("raeume") if isinstance(data, Mapping) else data
    return rows if isinstance(rows, list) else None


MIGRATED = "raumliste-umgestellt"                   # Merker neben den lokalen Einstellungen: nur einmal umstellen


def migrate_legacy(ctx: AppContext, hostname: str | None = None) -> None:
    """Einmal: Raumname und ID-Code dieses Macs aus der früheren Raumliste übernehmen (nur, wo noch nichts steht).
    Danach nie wieder (Review 13.11.2026: sonst kam ein entfernter ID-Code nach jedem Neustart zurück)."""
    s = ctx.settings
    marker = s.local_path.with_name(MIGRATED) if s.local_path else None
    if marker is not None and marker.exists():
        return
    local = {**s.unknown_local(), **s.local_values()}
    shared = _shared_rows(ctx)
    if shared or LEGACY_ROOMS in local or "system.raeume_nutzer.raum_fest" in local:
        entry = legacy_entry(local, shared, hostname or socket.gethostname())
        for path, value in ((ROOM_NAME, entry.get("name", "")), (ID_CODE, entry.get("id_code", ""))):
            if value and not local.get(path) and not s.locked(path):
                s.set_local(path, value)
                log.info("Aus der früheren Raumliste übernommen: %s", path)
    if marker is not None:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("Raumliste bis Ottilie 0.2 übernommen – nicht noch einmal\n", encoding="utf-8")
        except OSError as e:
            log.warning("Merker der Raumlisten-Umstellung nicht geschrieben: %s", e)


def startup(ctx: AppContext, hostname: str | None = None) -> None:
    """Beim Start: alte Raumliste umstellen, Raum dieses Macs (ohne Zentrale) bestimmen."""
    try:
        migrate_legacy(ctx, hostname)
    except Exception as e:  # noqa: BLE001 – die Umstellung darf den Start nie aufhalten
        log.warning("Frühere Raumliste nicht übernommen: %s", e)
    ctx.room = str(ctx.settings.get(ROOM_NAME) or "").strip() or short_host(hostname or socket.gethostname())


def overview(ctx: AppContext) -> dict:
    """Raumübersicht: Räume der Zentrale mit ihrem Stand, ergänzt um Helmut bzw. die Sperrdateien."""
    from tonmischung.app.roomstatus import rooms
    from tonmischung.helmut.client import HelmutError
    try:
        return rooms(ctx, ctx.extra.get("peers"))
    except HelmutError as e:
        raise CommandError(f"Raumstatus braucht Helmut: {e}") from e


def register(bridge: Bridge, ctx: AppContext) -> None:
    bridge.register("raeume", lambda args: overview(ctx))
