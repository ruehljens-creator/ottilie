"""SQLite-Datenbank der Zentrale (eine Datei unter /data). Ein Verbindungsobjekt mit Sperre – die Zugriffe sind kurz.

Gespeichert werden Räume, Geräte (Schlüssel nur als Hash), Kopplungscodes (nur als Hash), Administratoren
(Kennwort als scrypt), Einstellungen und das Protokoll. Nie: Personen, Benutzernamen der Macs, Klartext-Geheimnisse.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins(name TEXT PRIMARY KEY, hash TEXT NOT NULL, must_change INTEGER NOT NULL,
    failed INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, admin TEXT NOT NULL, csrf TEXT NOT NULL,
    valid_until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rooms(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
    created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY, room_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pair_codes(code_hash TEXT PRIMARY KEY, room_id INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS devices(id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL, name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE, created REAL NOT NULL, last_seen REAL NOT NULL DEFAULT 0,
    revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pending_devices(id INTEGER PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL, host TEXT NOT NULL DEFAULT '', created REAL NOT NULL, last_seen REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY, t REAL NOT NULL, level TEXT NOT NULL, area TEXT NOT NULL,
    room TEXT NOT NULL DEFAULT '', device TEXT NOT NULL DEFAULT '', text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS log_t ON log(t);
"""

ROOM_KINDS = ("mischraum", "schnittraum", "sonstiger")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("PRAGMA foreign_keys=ON")
            cols = [r[1] for r in self._con.execute("PRAGMA table_info(sessions)")]
            if cols and "valid_until" not in cols:            # Stand 0.1 vom 11.11.: Sitzungen sind flüchtig
                self._con.execute("DROP TABLE sessions")
            self._con.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def execute(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._con.execute(sql, tuple(args))

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._con.execute(sql, tuple(args)).fetchone()

    def all(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._con.execute(sql, tuple(args)).fetchall()

    def transaction(self):
        """`with db.transaction():` – BEGIN IMMEDIATE … COMMIT (atomar, z. B. Kopplungscode einlösen)."""
        return _Tx(self)

    # ── Einstellungen (JSON-Werte) ────────────────────────────────────────────
    def setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT v FROM settings WHERE k=?", (key,))
        return json.loads(row["v"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO settings(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                     (key, json.dumps(value, ensure_ascii=False)))

    def backup_to(self, target: str) -> None:
        dst = sqlite3.connect(target)
        try:
            with self._lock:
                self._con.backup(dst)
        finally:
            dst.close()


class _Tx:
    def __init__(self, db: Database) -> None:
        self.db = db

    def __enter__(self) -> Database:
        self.db._lock.acquire()
        self.db._con.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.db._con.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db._lock.release()


def now() -> float:
    return time.time()
