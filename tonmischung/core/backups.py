"""Session-Stände eines Beitrags: aktuelle Session, weitere Session-Dateien, Pro-Tools-Sicherungen.

Funktionsbeschreibung S. 31–32 („Sicherungen …“): die Liste zeigt alle Stände, aus denen man
weiterarbeiten kann, ohne in den Finder zu gehen. Pro Tools legt Sicherungen als
`Session File Backups/<Name>.bak.NNN.ptx` an.
Nur lesen – Öffnen legt immer eine Kopie an (`workflow/reopen.py`).
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from tonmischung.core.session_rules import same_session

BACKUP_DIR = "Session File Backups"
CURRENT, OTHER, BACKUP = "Aktuelle Session", "Weitere Session-Datei", "Pro-Tools-Sicherung"


@dataclass(frozen=True)
class SessionState:
    kind: str
    path: str
    mtime: float
    size: int

    @property
    def current(self) -> bool:
        return self.kind == CURRENT

    def row(self) -> dict:
        folder = os.path.dirname(self.path)
        name = os.path.basename(self.path)
        if os.path.basename(folder) == BACKUP_DIR:
            name = f"{BACKUP_DIR}/{name}"
        return {"kind": self.kind, "name": name, "path": self.path, "current": self.current,
                "time": datetime.fromtimestamp(self.mtime).strftime("%d.%m. %H:%M"),
                "size": f"{self.size / 1e6:.1f} MB".replace(".", ",")}


def _stat(path: str) -> tuple[float, int]:
    st = os.stat(path)
    return st.st_mtime, st.st_size


def list_states(session_path: str, listdir: Callable[[str], list[str]] = os.listdir,
                stat: Callable[[str], tuple[float, int]] = _stat) -> list[SessionState]:
    """Aktuelle Session zuerst, dann weitere Session-Dateien und Sicherungen, jeweils neueste zuerst."""
    folder = os.path.dirname(session_path)
    out: list[SessionState] = []
    try:
        out.append(SessionState(CURRENT, session_path, *stat(session_path)))
    except OSError:
        return []                                           # Session nicht erreichbar
    others: list[SessionState] = []
    for name in listdir(folder):
        p = os.path.join(folder, name)
        if (name.lower().endswith(".ptx") and not name.startswith(".")
                and not same_session(p, session_path)):
            try:
                others.append(SessionState(OTHER, p, *stat(p)))
            except OSError:
                pass
    backups: list[SessionState] = []
    bdir = os.path.join(folder, BACKUP_DIR)
    try:
        names = listdir(bdir)
    except OSError:
        names = []
    for name in names:
        if name.lower().endswith(".ptx") and not name.startswith("."):
            p = os.path.join(bdir, name)
            try:
                backups.append(SessionState(BACKUP, p, *stat(p)))
            except OSError:
                pass
    by_time = lambda s: s.mtime  # noqa: E731
    return out + sorted(others, key=by_time, reverse=True) + sorted(backups, key=by_time, reverse=True)


def belongs_to(path: str, session_path: str) -> bool:
    """Liegt `path` im Session-Ordner bzw. dessen Sicherungsordner? (Schutz für „Im Finder zeigen“/Öffnen)"""
    folder = os.path.normpath(os.path.dirname(session_path))
    p = os.path.normpath(path)
    return os.path.dirname(p) in (folder, os.path.join(folder, BACKUP_DIR)) and p.lower().endswith(".ptx")
