"""Was dieser Mac zuletzt von der Zentrale wusste: Raum und verteilte Einstellungen.

Rechnerweit im gemeinsamen Datenordner (Prüfrunde 13.11.2026: vorher je macOS-Benutzer – ein zweiter Benutzer, der
Ottilie bei ausgefallener Zentrale startete, arbeitete unter dem Rechnernamen und ohne die zentralen Vorgaben). Jede
Datei trägt die Gerätekennung der Kopplung: nach neuem Anmelden oder Koppeln gilt der alte Stand nicht mehr (sonst
arbeitete ein neu angemeldeter Mac unter dem Raum, den er früher hatte).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tonmischung.core.fileio import SHARED_FILE_MODE, CorruptFileError, read_json, write_json

log = logging.getLogger("tonmischung.zentrale")

ROOM, SETTINGS = "zentrale-raum.json", "zentrale-einstellungen.json"


def read(folder: Path, name: str, kennung: str, legacy: Path | None = None) -> dict:
    """Gespeicherter Stand – {} ohne, bei anderer Kopplung oder beschädigter Datei. `legacy`: Ottilie-Ordner des
    Benutzers (Vorabstand 0.3, ohne Kennung) – nur, solange es rechnerweit noch nichts gibt."""
    if not kennung:
        return {}
    try:
        data = read_json(Path(folder) / name, expect=dict, shared=True)
    except (CorruptFileError, OSError) as e:
        log.info("Zentrale-Stand %s nicht lesbar: %s", name, e)
        return {}
    if data is None and legacy is not None and Path(legacy) != Path(folder):
        try:
            return read_json(Path(legacy) / name, expect=dict) or {}
        except (CorruptFileError, OSError):
            return {}
    if not data or str(data.get("kennung") or "") != kennung:
        return {}
    return data


def write(folder: Path, name: str, kennung: str, data: dict[str, Any]) -> None:
    try:
        write_json(Path(folder) / name, {**data, "kennung": kennung}, mode=SHARED_FILE_MODE)
    except OSError as e:
        log.warning("Zentrale-Stand %s nicht gespeichert: %s", name, e)
