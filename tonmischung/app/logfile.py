"""Protokoll der App in eine Datei – mit Millisekunden, sieben Tage aufbewahrt.

Lehre aus der produktiven PunchBuddy-Linie (§7, §12): Fehler im Studio lassen sich nur mit
dem vollständigen Protokoll des Fehlerzeitraums klären; die Zeitstempel brauchen Millisekunden
(PTSL-Latenz, Stream-Deck-Salven). Abstürze schreibt faulthandler in eine eigene Datei.
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import re
from pathlib import Path

FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"
HIDDEN = "••••"
# Webtrigger-Schlüssel in Adressen (`token=…`, auch JSON-kodiert), Helmut-Token im Kopf (`Bearer …`)
_SECRETS = (re.compile(r"(token=)[^&\s\"'\\<>]+"), re.compile(r"(\"token\"\s*:\s*\")[^\"]+"),
            re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+"))


def redact(text: str) -> str:
    """Geheimnisse aus einer Protokollzeile entfernen (Review A18: mit TM_DEBUG standen Webtrigger-Adressen samt
    Schlüssel im Protokoll und damit im Diagnosebericht)."""
    for pattern in _SECRETS:
        text = pattern.sub(lambda m: m.group(1) + HIDDEN, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Wie `logging.Formatter`, aber ohne Geheimnisse – auch in Ausnahmetexten."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def log_dir() -> Path:
    from tonmischung.paths import log_dir as d
    return d()


def log_path() -> Path:
    return log_dir() / "Ottilie.log"


_crash_file = None


def setup(debug: bool = False, console: bool = False) -> Path:
    """Datei-Protokoll am Root-Logger einrichten (einmal je Prozess). Rückgabe: Pfad der Datei."""
    global _crash_file
    folder = log_dir()
    folder.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if not any(getattr(h, "_tonmischung", False) for h in root.handlers):
        fh = logging.handlers.TimedRotatingFileHandler(log_path(), when="midnight", backupCount=7, encoding="utf-8")
        fh.setFormatter(RedactingFormatter(FORMAT, DATEFMT))
        fh._tonmischung = True  # type: ignore[attr-defined]
        root.addHandler(fh)
        if console:
            ch = logging.StreamHandler()
            ch.setFormatter(RedactingFormatter(FORMAT, DATEFMT))
            ch._tonmischung = True  # type: ignore[attr-defined]
            root.addHandler(ch)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    if _crash_file is None:
        _crash_file = open(folder / "Ottilie_crash.log", "a", encoding="utf-8")  # noqa: SIM115 – bleibt offen
        faulthandler.enable(_crash_file)
    return log_path()


def restore_crash_log() -> None:
    """Absturzprotokoll wieder auf die Datei lenken – `vendor.import_isolated` setzt faulthandler
    beim Laden von PunchBuddy-Modulen auf die Konsole zurück."""
    if _crash_file is not None:
        faulthandler.enable(_crash_file)
