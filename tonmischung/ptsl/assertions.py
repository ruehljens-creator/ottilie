"""Interne Fehler von Pro Tools („Assertion“) erkennen – über das Protokoll, das Pro Tools laufend schreibt.

Live 03.10.2026 (Premiere-AAF, drei Läufe): Pro Tools meldet beim Import „Could not complete your request because
Assertion in …Cmn_Interval.h, line 143“. Nach dem Bestätigen stürzt Pro Tools ab – auch dann, wenn die App keinen
Befehl mehr schickt (Probe 6: Access Violation 0,05 s nach dem OK). Den Absturz kann die App nicht verhindern; sie
hört aber sofort auf, statt „bitte bestätigen, danach geht es weiter“ zu melden und weiterzuarbeiten.

Quelle ist `~/Library/Logs/Avid/Pro_Tools_<Start>.txt` (Digitrace, wird laufend geschrieben; Zeile
`PtApp_Assert(Release): …`). So wird die Assertion auch erkannt, wenn die Meldung schon weggeklickt ist. Ein neues
Protokoll heißt: Pro Tools wurde neu gestartet.
"""
from __future__ import annotations

import glob
import os
import re
from collections.abc import Callable

LOG_DIR = os.path.expanduser("~/Library/Logs/Avid")
LOG_GLOB = "Pro_Tools_2*.txt"            # Pro_Tools_2026_10_03_16_57_04.txt (nicht Pro_Tools_Video_…)
MARKERS = ("PtApp_Assert", "because Assertion in")
MAX_READ = 8 << 20


def assertion_lines(text: str) -> list[str]:
    """Zeilen mit einer Assertion (reine Logik)."""
    return [line.strip() for line in text.splitlines() if any(m in line for m in MARKERS)]


def short(line: str) -> str:
    """„Assertion in Cmn_Interval.h, Zeile 143“ aus einer Protokollzeile."""
    m = re.search(r"([A-Za-z0-9_]+\.(?:h|cpp|mm?))\"?,? [Ll]ine (\d+)", line)
    return f"Assertion in {m.group(1)}, Zeile {m.group(2)}" if m else "Assertion"


class AssertionWatch:
    """Liest das jeweils neueste Pro-Tools-Protokoll fortlaufend weiter.

    `poll()` → (Meldung der ersten neuen Assertion oder "", Pro Tools neu gestartet?). Beim ersten Blick wird nur
    das Ende gemerkt (ältere Einträge zählen nicht); ein später neu entstandenes Protokoll wird von vorn gelesen.
    """

    def __init__(self, log_dir: str = LOG_DIR, newest: Callable[[str], str] | None = None) -> None:
        self.log_dir = log_dir
        self._newest = newest or _newest_log
        self._path = ""
        self._offset = 0
        self._rest = ""

    def poll(self) -> tuple[str, bool]:
        path = self._newest(self.log_dir)
        if not path:
            return "", False
        restarted = False
        if path != self._path:
            first = not self._path
            restarted = not first
            self._path, self._rest = path, ""
            self._offset = _size(path) if first else 0
        try:
            size = _size(path)
            if size < self._offset:                      # Datei neu begonnen
                self._offset = 0
            if size == self._offset:
                return "", restarted
            with open(path, "rb") as f:
                f.seek(self._offset)
                data = f.read(MAX_READ)
            self._offset += len(data)
        except OSError:
            return "", restarted
        text = self._rest + data.decode("utf-8", "replace")
        text, _, self._rest = text.rpartition("\n") if "\n" in text else ("", "", text)
        hits = assertion_lines(text)
        return (short(hits[0]) if hits else ""), restarted


def _size(path: str) -> int:
    return os.path.getsize(path)


def _newest_log(log_dir: str) -> str:
    try:
        files = glob.glob(os.path.join(log_dir, LOG_GLOB))
        return max(files, key=os.path.getmtime) if files else ""
    except OSError:
        return ""
