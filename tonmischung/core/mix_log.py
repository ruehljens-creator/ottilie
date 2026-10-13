"""Verzeichnis der auf diesem Mac gemachten Mischungen (Jens 09.10.2026).

Je Eintrag: Zeit, Art (zurückgeliefert, Havarie-Sendefile), Beitrag, Titel, Sendung, Mischer, Raum, Session, gelieferte
Dateien und ihr Ordner. „Liste leeren“ leert nur dieses Verzeichnis – Helmut, Sessions und Dateien bleiben, wie sie
sind. Die Datei liegt neben den anderen Daten der App; eine beschädigte wird beiseitegelegt, nie überschrieben.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from tonmischung.core.fileio import CorruptFileError, read_json, set_aside, write_json

log = logging.getLogger(__name__)

DELIVERED, HAVARIE = "zurückgeliefert", "Havarie-Sendefile"
MAX_ENTRIES = 2000                      # älteste fallen dann heraus


def default_path(mock: bool = False) -> Path:
    from tonmischung.paths import support_dir
    return support_dir() / ("mischungen-mock.json" if mock else "mischungen.json")


@dataclass
class MixRecord:
    art: str
    titel: str
    beitrag: str = ""                   # Helmut-ID (Havarie ohne Beitrag: leer)
    sendung: str = ""
    mischer: str = ""
    raum: str = ""
    session: str = ""
    dateien: list[str] = field(default_factory=list)
    ordner: str = ""
    zeit: str = ""


class MixLog:
    def __init__(self, path: Path | None = None, now: Callable[[], datetime] = datetime.now) -> None:
        self.path = Path(path) if path else default_path()
        self._now = now
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            data = read_json(self.path, expect=dict)
        except CorruptFileError as e:
            moved = set_aside(self.path)
            log.error("Verzeichnis der Mischungen war beschädigt (%s) – beiseitegelegt als „%s“",
                      e.reason, moved.name)
            return []
        return [e for e in (data or {}).get("eintraege", []) if isinstance(e, dict)]

    def add(self, record: MixRecord) -> MixRecord:
        record.zeit = record.zeit or self._now().strftime("%Y-%m-%d %H:%M")
        with self._lock:
            entries = self._read()
            entries.append(asdict(record))
            write_json(self.path, {"version": 1, "eintraege": entries[-MAX_ENTRIES:]})
        return record

    def entries(self) -> list[dict]:
        """Neueste zuerst."""
        with self._lock:
            return list(reversed(self._read()))

    def clear(self) -> int:
        """Verzeichnis leeren; Rückgabe: Anzahl der entfernten Einträge."""
        with self._lock:
            n = len(self._read())
            write_json(self.path, {"version": 1, "eintraege": []})
        return n
