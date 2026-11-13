"""Chat der Zentrale: Gespräche zwischen Räumen, Nachrichten nur bis zur Zustellung (Stufe 2).

- Eigene Datenbankdatei `chat.db` mit `secure_delete` – nicht in den Sicherungen (dort soll nie Chattext liegen).
- Eine Nachricht wird gelöscht, sobald jeder Zielraum sie empfangen hat (ein Gerät des Raums bestätigt), spätestens
  nach der Haltezeit (Standard 24 h, einstellbar 1 h – 7 Tage).
- Gesprächsarten: „alle“ (alle Räume), „raum“ (Raum an Raum), „beitrag“ (Herkunfts-Schnittraum + Mischräume, beim
  Belegen automatisch). Systemmeldungen stehen im Beitragsgespräch.
- Chattext nie ins Protokoll – dort nur Räume, Zeit, Länge, ID.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections.abc import Callable

KINDS = ("alle", "raum", "beitrag")
MAX_TEXT = 2000
DEFAULT_NOTICE = ("Dieser Chat dient nur der Abstimmung von Arbeitsabläufen. Bitte teilen Sie hier in keinem Fall "
                  "private oder sensible Daten (z. B. persönliche Angaben, Zugangsdaten, Kennwörter, vertrauliche "
                  "Inhalte).")
SYSTEM_KINDS = {"uebergeben": "Beitrag übergeben", "belegt": "belegt", "freigegeben": "freigegeben",
                "audio_export_fertig": "Audio Export fertig", "fertigmeldung": "Fertigmeldung gesendet"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS convs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
    beitrag TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS members(conv TEXT NOT NULL, room TEXT NOT NULL, PRIMARY KEY(conv, room));
CREATE TABLE IF NOT EXISTS msgs(id TEXT PRIMARY KEY, conv TEXT NOT NULL, sender TEXT NOT NULL, text TEXT NOT NULL,
    urgent INTEGER NOT NULL, system INTEGER NOT NULL, t REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pending(msg TEXT NOT NULL, room TEXT NOT NULL, PRIMARY KEY(msg, room));
CREATE TABLE IF NOT EXISTS lesestand(conv TEXT NOT NULL, room TEXT NOT NULL, last TEXT NOT NULL,
    PRIMARY KEY(conv, room));
"""


class ChatStore:
    def __init__(self, path: str, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._lock = threading.RLock()
        self._con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            self._con.execute("PRAGMA secure_delete=ON")
            # kein Journal auf Platte: gelöschter Text bleibt nie liegen (ein Absturz kostet höchstens die letzte
            # Nachricht; Sicherheit 11.11.2026)
            self._con.execute("PRAGMA journal_mode=MEMORY")
            self._con.executescript(SCHEMA)
            self._con.execute("INSERT OR IGNORE INTO convs(id, kind, title, created) VALUES('alle','alle',"
                              "'Alle Räume',?)", (self.clock(),))

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._con.execute(sql, tuple(args)).fetchall()

    def _x(self, sql: str, args=()) -> sqlite3.Cursor:
        with self._lock:
            return self._con.execute(sql, tuple(args))

    # ── Gespräche ─────────────────────────────────────────────────────────────
    def members(self, conv: str, all_rooms: list[str]) -> list[str]:
        row = self._q("SELECT kind FROM convs WHERE id=?", (conv,))
        if not row:
            return []
        if row[0]["kind"] == "alle":
            return list(all_rooms)
        return [r["room"] for r in self._q("SELECT room FROM members WHERE conv=? ORDER BY room", (conv,))]

    def convs_of_kind(self, kind: str) -> list[str]:
        return [r["id"] for r in self._q("SELECT id FROM convs WHERE kind=?", (kind,))]

    def conv(self, conv: str) -> dict | None:
        row = self._q("SELECT * FROM convs WHERE id=?", (conv,))
        return dict(row[0]) if row else None

    def convs_of(self, room: str, enabled: dict) -> list[dict]:
        out = []
        for c in self._q("SELECT * FROM convs ORDER BY created"):
            if not enabled.get(c["kind"], True):
                continue
            if c["kind"] == "alle" or self._q("SELECT 1 FROM members WHERE conv=? AND room=?", (c["id"], room)):
                out.append(dict(c))
        return out

    def room_conv(self, a: str, b: str) -> tuple[dict, bool]:
        """Gespräch Raum an Raum (gibt es schon → dasselbe)."""
        x, y = sorted((a, b))
        cid = f"raum:{x}|{y}"
        new = not self._q("SELECT 1 FROM convs WHERE id=?", (cid,))
        with self._lock:
            if new:
                self._x("INSERT INTO convs(id, kind, title, created) VALUES(?,?,?,?)",
                        (cid, "raum", f"{x} ↔ {y}", self.clock()))
            for r in (x, y):                              # auch bei einem vorhandenen (Altlast): beide sind Mitglied
                self._x("INSERT OR IGNORE INTO members(conv, room) VALUES(?,?)", (cid, r))
        return self.conv(cid), new

    def item_conv(self, beitrag: str, title: str, rooms: list[str]) -> tuple[dict, list[str]]:
        """Beitragsgespräch anlegen oder Räume ergänzen → (Gespräch, neu hinzugekommene Räume)."""
        cid = f"beitrag:{beitrag}"
        with self._lock:
            self._x("INSERT OR IGNORE INTO convs(id, kind, title, beitrag, created) VALUES(?,?,?,?,?)",
                    (cid, "beitrag", title or beitrag, beitrag, self.clock()))
            added = []
            for r in rooms:
                if r and self._x("INSERT OR IGNORE INTO members(conv, room) VALUES(?,?)", (cid, r)).rowcount:
                    added.append(r)
        return self.conv(cid), added

    # ── Nachrichten ───────────────────────────────────────────────────────────
    def add(self, conv: str, sender: str, text: str, targets: list[str], urgent: bool = False,
            system: bool = False) -> dict:
        text = str(text)[:MAX_TEXT]
        mid = uuid.uuid4().hex
        t = self.clock()
        with self._lock:
            self._x("INSERT INTO msgs(id, conv, sender, text, urgent, system, t) VALUES(?,?,?,?,?,?,?)",
                    (mid, conv, sender, text, int(urgent), int(system), t))
            for r in set(targets):
                self._x("INSERT OR IGNORE INTO pending(msg, room) VALUES(?,?)", (mid, r))
        self._gc(mid)
        return {"id": mid, "gespraech": conv, "von": sender, "text": text, "dringend": urgent, "system": system,
                "zeit": t}

    def pending_for(self, room: str) -> list[dict]:
        rows = self._q("SELECT m.* FROM msgs m JOIN pending p ON p.msg=m.id WHERE p.room=? ORDER BY m.t", (room,))
        return [{"id": r["id"], "gespraech": r["conv"], "von": r["sender"], "text": r["text"],
                 "dringend": bool(r["urgent"]), "system": bool(r["system"]), "zeit": r["t"]} for r in rows]

    def delivered(self, room: str, ids: list[str]) -> int:
        n = 0
        with self._lock:
            for mid in ids[:500]:
                n += self._x("DELETE FROM pending WHERE msg=? AND room=?", (str(mid), room)).rowcount
                self._gc(str(mid))
        return n

    def _gc(self, mid: str) -> None:
        if not self._q("SELECT 1 FROM pending WHERE msg=?", (mid,)):
            self._x("DELETE FROM msgs WHERE id=?", (mid,))     # allen zugestellt → weg

    def set_read(self, conv: str, room: str, last: str) -> None:
        self._x("INSERT INTO lesestand(conv, room, last) VALUES(?,?,?) ON CONFLICT(conv, room) DO UPDATE SET "
                "last=excluded.last", (conv, room, str(last)[:64]))

    def expire(self, hold_h: float) -> int:
        """Haltezeit abgelaufen: Nachricht weg, auch wenn nicht alle sie bekommen haben."""
        cut = self.clock() - max(1.0, min(168.0, float(hold_h))) * 3600
        with self._lock:
            ids = [r["id"] for r in self._q("SELECT id FROM msgs WHERE t<?", (cut,))]
            for mid in ids:
                self._x("DELETE FROM pending WHERE msg=?", (mid,))
                self._x("DELETE FROM msgs WHERE id=?", (mid,))
            # Beitragsgespräche ohne Nachrichten nach 7 Tagen weg (Raum an Raum bleibt)
            self._x("DELETE FROM members WHERE conv IN (SELECT id FROM convs WHERE kind='beitrag' AND created<? "
                    "AND id NOT IN (SELECT conv FROM msgs))", (self.clock() - 7 * 86400,))
            self._x("DELETE FROM convs WHERE kind='beitrag' AND created<? AND id NOT IN (SELECT conv FROM msgs)",
                    (self.clock() - 7 * 86400,))
        return len(ids)

    def waiting_for_any(self, rooms: list[str]) -> int:
        if not rooms:
            return 0
        q = ",".join("?" * len(rooms))
        return int(self._q(f"SELECT COUNT(*) AS n FROM pending WHERE room IN ({q})", rooms)[0]["n"])

    def waiting_from(self, room: str) -> int:
        return int(self._q("SELECT COUNT(*) AS n FROM msgs WHERE sender=?", (room,))[0]["n"])

    def waiting(self) -> int:
        return int(self._q("SELECT COUNT(*) AS n FROM msgs")[0]["n"])

    def rename_room(self, old: str, new: str) -> None:
        """Raum umbenannt: Mitgliedschaften, Wartendes, Lesestand, Absender und Gespräche „Raum an Raum“ nachziehen."""
        with self._lock:
            self._x("BEGIN IMMEDIATE")
            try:
                for table in ("members", "pending", "lesestand"):
                    # Altlasten eines früher gelöschten Raums gleichen Namens: nicht abbrechen (Programmierer
                    # 13.11.2026: sonst war der Raum halb umbenannt), übrig gebliebene Zeilen des alten Namens weg
                    self._x(f"UPDATE OR IGNORE {table} SET room=? WHERE room=?", (new, old))
                    self._x(f"DELETE FROM {table} WHERE room=?", (old,))
                self._x("UPDATE msgs SET sender=? WHERE sender=?", (new, old))
                for row in self._q("SELECT id FROM convs WHERE kind='raum'"):
                    a, _, b = row["id"][len("raum:"):].partition("|")
                    if old not in (a, b):
                        continue
                    x, y = sorted(new if r == old else r for r in (a, b))
                    cid = f"raum:{x}|{y}"
                    if self._q("SELECT 1 FROM convs WHERE id=?", (cid,)):
                        continue                                # gibt es schon (Altlast) – nichts überschreiben
                    self._x("UPDATE convs SET id=?, title=? WHERE id=?", (cid, f"{x} ↔ {y}", row["id"]))
                    for table in ("members", "msgs", "lesestand"):
                        self._x(f"UPDATE {table} SET conv=? WHERE conv=?", (cid, row["id"]))
                self._x("COMMIT")
            except Exception:
                self._x("ROLLBACK")
                raise

    def remove_room(self, room: str) -> None:
        """Raum gelöscht: seine Mitgliedschaften, Wartendes und Lesestände entfernen – sonst erbte ein späterer Raum
        gleichen Namens Gespräche und Nachrichten (Sicherheit 13.11.2026)."""
        with self._lock:
            for table in ("members", "pending", "lesestand"):
                self._x(f"DELETE FROM {table} WHERE room=?", (room,))
            for row in self._q("SELECT id FROM convs WHERE kind='raum'"):     # Gespräche „Raum an Raum“ mit ihm
                if room in row["id"][len("raum:"):].split("|"):
                    for table in ("members", "lesestand"):
                        self._x(f"DELETE FROM {table} WHERE conv=?", (row["id"],))
                    self._x("DELETE FROM pending WHERE msg IN (SELECT id FROM msgs WHERE conv=?)", (row["id"],))
                    self._x("DELETE FROM msgs WHERE conv=?", (row["id"],))
                    self._x("DELETE FROM convs WHERE id=?", (row["id"],))

    def wipe(self) -> int:
        """Chat aus oder „wartende löschen“: alle Nachrichten sofort weg."""
        with self._lock:
            n = self.waiting()
            self._x("DELETE FROM pending")
            self._x("DELETE FROM msgs")
            self._x("DELETE FROM lesestand")
            self._x("VACUUM")
        return n
