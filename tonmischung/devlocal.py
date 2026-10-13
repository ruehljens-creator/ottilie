"""Daten des Entwickler-Macs – stehen nicht im Code (13.10.2026, vor der Veröffentlichung des Codes).

Testordner, Beispielbeiträge des Helmut-Mocks (`app --mock`), Test-Templates und echte Testdateien für einzelne Tests
liegen je Mac in `testfiles/entwicklung.json` – `testfiles/` ist nicht eingecheckt. Vorlage mit allen Schlüsseln:
`tools/entwicklung.beispiel.json`. Relative Pfade gelten ab dem Projektordner.

Fehlt die Datei, gilt der Testordner `~/Documents/Ottilie-Test`, der Mock zeigt nur den belegten Beispielbeitrag, und
Tests mit echten Dateien werden übersprungen.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tonmischung.core.fileio import CorruptFileError, read_json

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEV_FILE = PROJECT_DIR / "testfiles" / "entwicklung.json"
DEFAULT_TEST_FOLDER = "~/Documents/Ottilie-Test"


def load(path: Path = DEV_FILE) -> dict[str, Any]:
    """Inhalt der lokalen Datei; leer, wenn es sie nicht gibt oder sie nicht lesbar ist (nur Entwicklung)."""
    try:
        return read_json(path, expect=dict) or {}
    except (CorruptFileError, OSError):
        return {}


def _abs(p: str) -> str:
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else str(PROJECT_DIR / p)


def test_folder(data: dict | None = None) -> str:
    """Ordner, in dem im Entwicklungsmodus alle Sessions landen (Sicherheitsregel: nur der Testordner)."""
    return _abs(str((load() if data is None else data).get("testordner") or DEFAULT_TEST_FOLDER))


def dev_projects(data: dict | None = None) -> list[tuple[str, str, str, list[str]]]:
    """Beispielbeiträge für den Mock: (ID, Titel, Sendung, Übergabe-Dateien)."""
    out = []
    for b in (load() if data is None else data).get("beitraege") or []:
        files = [_abs(f) for f in b.get("dateien") or []]
        out.append((str(b["id"]), str(b["titel"]), str(b.get("sendung") or ""), files))
    return out


def dev_templates(data: dict | None = None) -> tuple[str, ...]:
    """Test-Templates, die der Entwicklungsmodus in dieser Reihenfolge vorschlägt."""
    return tuple(str(t) for t in (load() if data is None else data).get("templates") or [])


def real_file(key: str, data: dict | None = None) -> str | None:
    """Pfad einer echten Testdatei aus `testdateien` – None, wenn nicht eingetragen oder nicht vorhanden."""
    p = ((load() if data is None else data).get("testdateien") or {}).get(key)
    if not p:
        return None
    p = _abs(str(p))
    return p if os.path.exists(p) else None
