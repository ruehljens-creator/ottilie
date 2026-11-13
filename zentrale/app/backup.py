"""Sicherungen der Datenbank: täglich eine, 7 bleiben; Download; Wiederherstellen beim nächsten Start.

Wiederherstellen tauscht nie die laufende Datei aus: Eine geprüfte Sicherung wird als `wiederherstellen.db`
vorgemerkt und beim nächsten Start (`prepare_restore`) eingesetzt – die bisherige Datenbank wird dabei selbst als
Sicherung beiseitegelegt, nicht gelöscht.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from collections.abc import Callable

from app.db import Database
from app.log import Log

KEEP = 7
NAME = re.compile(r"^zentrale-\d{8}(-\d{6})?\.db$")
PENDING = "wiederherstellen.db"
TABLES = {"admins", "rooms", "devices", "settings", "log"}


class Backups:
    def __init__(self, db: Database, folder: str, log: Log, clock: Callable[[], float] = time.time) -> None:
        self.db, self.folder, self.log, self.clock = db, folder, log, clock
        os.makedirs(folder, exist_ok=True)

    def list(self) -> list[dict]:
        out = []
        for n in sorted(os.listdir(self.folder), reverse=True):
            if NAME.match(n):
                p = os.path.join(self.folder, n)
                out.append({"name": n, "groesse": os.path.getsize(p), "zeit": os.path.getmtime(p)})
        return out

    def path(self, name: str) -> str | None:
        """Pfad einer Sicherung – nur Namen nach Muster, nie ein Pfad von außen."""
        if not NAME.match(name or ""):
            return None
        p = os.path.join(self.folder, name)
        return p if os.path.isfile(p) else None

    def create(self, stamp: str | None = None) -> str:
        stamp = stamp or time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        target = os.path.join(self.folder, f"zentrale-{stamp}.db")
        tmp = target + ".part"
        self.db.backup_to(tmp)
        os.replace(tmp, target)
        self.log.info("betrieb", f"Sicherung {os.path.basename(target)} angelegt")
        return target

    def daily(self) -> None:
        day = time.strftime("%Y%m%d", time.localtime(self.clock()))
        if not os.path.exists(os.path.join(self.folder, f"zentrale-{day}.db")):
            self.create(day)
        daily = [b for b in self.list() if re.match(r"^zentrale-\d{8}\.db$", b["name"])]
        for old in daily[KEEP:]:
            os.remove(os.path.join(self.folder, old["name"]))      # nur eigene Tagessicherungen über 7 hinaus

    def stage_restore(self, data: bytes) -> None:
        """Hochgeladene Sicherung prüfen und für den nächsten Start vormerken."""
        tmp = os.path.join(self.folder, PENDING + ".part")
        with open(tmp, "wb") as f:
            f.write(data)
        try:
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            try:
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Datei ist beschädigt")
                names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                con.close()
        except (sqlite3.DatabaseError, ValueError) as e:
            os.remove(tmp)
            raise ValueError(f"Keine gültige Sicherung der Zentrale ({e})") from None
        if not TABLES <= names:
            os.remove(tmp)
            raise ValueError("Keine Sicherung der Zentrale (Tabellen fehlen)")
        os.replace(tmp, os.path.join(self.folder, PENDING))
        self.log.info("betrieb", "Wiederherstellen vorgemerkt – wirkt beim nächsten Start der Zentrale")


def prepare_restore(data_dir: str) -> bool:
    """Beim Start, bevor die Datenbank geöffnet wird: vorgemerkte Sicherung einsetzen (alte beiseitelegen)."""
    pending = os.path.join(data_dir, "sicherungen", PENDING)
    if not os.path.isfile(pending):
        return False
    db = os.path.join(data_dir, "zentrale.db")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if os.path.exists(db):
        os.replace(db, os.path.join(data_dir, "sicherungen", f"zentrale-{stamp}.db"))
        for ext in ("-wal", "-shm"):
            if os.path.exists(db + ext):
                os.replace(db + ext, os.path.join(data_dir, "sicherungen", f"zentrale-{stamp}.db{ext}"))
    os.replace(pending, db)
    return True
