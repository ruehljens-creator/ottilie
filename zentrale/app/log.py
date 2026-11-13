"""Protokoll der Zentrale: in SQLite (Fehler/Warnung/Info, Debug nur zugeschaltet) und auf stdout (ohne Debug).

Geschwärzt wird zentral vor dem Schreiben: Geräteschlüssel, Kopplungscodes, Kennwörter und andere lange Zeichenketten,
die wie Geheimnisse aussehen. Chattext kommt nie hierher – Aufrufer übergeben bei Chat nur Räume, Zeit, Länge, ID.
Debug lässt sich für eine Frist zuschalten (1 h – 3 Tage), optional nur für bestimmte Räume, Geräte oder Bereiche,
und schaltet sich selbst ab; Ein und Aus werden protokolliert.
"""
from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable

from app.db import Database

LEVELS = ("fehler", "warnung", "info", "debug")
DEBUG_SPANS_H = (1, 8, 24, 72)
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")
_SECRET = re.compile(r"[A-Za-z0-9_\-]{32,}")          # Schlüssel, Token, Hashes (Geräteschlüssel: 43)
_FIELDS = re.compile(r"(?i)\b(kennwort|passwort|password|schluessel|schlüssel|key|token|code|secret)\b(\s*[=:]\s*)"
                     r"(\"[^\"]*\"|'[^']*'|\S+)")


_AUTH = re.compile(r"(?i)\b(basic|bearer)\s+\S+")
_URL_CRED = re.compile(r"://[^/@\s]+:[^/@\s]+@")
_QUERY = re.compile(r"(?i)([?&](?:api[_-]?key|apikey|key|token|secret|password|passwort|kennwort)=)[^&\s]+")
_NAMED = re.compile(r"(?i)\b(api[_-]?key|apikey|kopplungscode|kennwort|passwort|password)\b(\s*[=:]?\s*)"
                    r"(„[^“]*“|\"[^\"]*\"|'[^']*'|\S+)")


def redact(text: str) -> str:
    """Alles, was nach Geheimnis aussieht, durch •••• ersetzen (vor jedem Schreiben)."""
    text = str(text)
    text = _AUTH.sub(lambda m: f"{m.group(1)} ••••", text)
    text = _URL_CRED.sub("://••••@", text)
    text = _QUERY.sub(lambda m: f"{m.group(1)}••••", text)
    text = _NAMED.sub(lambda m: f"{m.group(1)}{m.group(2)}••••", text)
    text = _FIELDS.sub(lambda m: f"{m.group(1)}{m.group(2)}••••", text)
    return _SECRET.sub("••••", text)


class Log:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time, out=None) -> None:
        self.db, self.clock = db, clock
        self.out = out                                # None: das jeweils aktuelle sys.stdout
        self._limited: dict[str, list] = {}

    # ── Debug zuschalten ──────────────────────────────────────────────────────
    def debug_state(self) -> dict:
        until = float(self.db.setting("debug.bis", 0) or 0)
        if until and until <= self.clock():
            self.db.set_setting("debug.bis", 0)
            self.write("info", "protokoll", "Debug-Protokoll selbst abgeschaltet (Frist abgelaufen)")
            until = 0
        return {"aktiv": bool(until), "bis": until, "rest_s": max(0, int(until - self.clock())) if until else 0,
                "raeume": self.db.setting("debug.raeume", []), "geraete": self.db.setting("debug.geraete", []),
                "bereiche": self.db.setting("debug.bereiche", [])}

    def debug_on(self, hours: float, rooms=(), devices=(), areas=(), by: str = "") -> None:
        hours = float(hours)
        if hours not in DEBUG_SPANS_H:
            raise ValueError("Dauer nur 1 h, 8 h, 24 h oder 3 Tage")
        self.db.set_setting("debug.bis", self.clock() + hours * 3600)
        self.db.set_setting("debug.raeume", list(rooms))
        self.db.set_setting("debug.geraete", list(devices))
        self.db.set_setting("debug.bereiche", list(areas))
        self.write("info", "protokoll", f"Debug-Protokoll an für {hours:g} h ({by or 'unbekannt'})")

    def debug_off(self, by: str = "") -> None:
        self.db.set_setting("debug.bis", 0)
        self.write("info", "protokoll", f"Debug-Protokoll aus ({by or 'unbekannt'})")

    def _debug_wanted(self, area: str, room: str, device: str) -> bool:
        st = self.debug_state()
        if not st["aktiv"]:
            return False
        return ((not st["raeume"] or room in st["raeume"]) and (not st["geraete"] or device in st["geraete"])
                and (not st["bereiche"] or area in st["bereiche"]))

    # ── Schreiben ─────────────────────────────────────────────────────────────
    def write(self, level: str, area: str, text: str, room: str = "", device: str = "") -> None:
        if level not in LEVELS:
            level = "info"
        if level == "debug" and not self._debug_wanted(area, room, device):
            return
        text = _CTRL.sub(" ", redact(text))           # eine Zeile: nie gefälschte Zeilen auf stdout (Sicherheit)
        room, device = _CTRL.sub(" ", room)[:100], _CTRL.sub(" ", device)[:100]
        t = self.clock()
        self.db.execute("INSERT INTO log(t, level, area, room, device, text) VALUES(?,?,?,?,?,?)",
                        (t, level, area, room, device, text))
        if level != "debug":
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
            where = " ".join(x for x in (room, device) if x)
            suffix = f" [{where}]" if where else ""
            print(f"{stamp} {level.upper():7} {area}: {text}{suffix}", file=self.out or sys.stdout, flush=True)

    def error(self, area: str, text: str, **kw) -> None:
        self.write("fehler", area, text, **kw)

    def warn(self, area: str, text: str, **kw) -> None:
        self.write("warnung", area, text, **kw)

    def info(self, area: str, text: str, **kw) -> None:
        self.write("info", area, text, **kw)

    def debug(self, area: str, text: str, **kw) -> None:
        self.write("debug", area, text, **kw)

    def info_limited(self, key: str, area: str, text: str, **kw) -> None:
        """Wie `warn_limited`, aber als Info (Verbinden/Trennen: ein wackeliges Gerät flutet sonst das Protokoll)."""
        t = self.clock()
        seen = self._limited.setdefault(key, [0.0, 0])
        if t - seen[0] >= 60:
            more = f" ({seen[1]} weitere in der letzten Minute)" if seen[1] else ""
            seen[0], seen[1] = t, 0
            self.info(area, text + more, **kw)
        else:
            seen[1] += 1

    def warn_limited(self, key: str, area: str, text: str) -> None:
        """Abgelehnte Anfragen: je Quelle höchstens eine Zeile pro Minute, danach „n weitere“ (Sicherheit: Fluten)."""
        t = self.clock()
        seen = self._limited.setdefault(key, [0.0, 0])
        if t - seen[0] >= 60:
            more = f" ({seen[1]} weitere in der letzten Minute)" if seen[1] else ""
            seen[0], seen[1] = t, 0
            self.warn(area, text + more)
        else:
            seen[1] += 1
        if len(self._limited) > 5000:
            self._limited.clear()

    def changed(self, area: str, key: str, old, new, by: str, secret: bool = False) -> None:
        """Einstellungsänderung alt → neu (Geheimnisse ••••)."""
        if secret:
            old, new = ("••••" if old else "–"), ("••••" if new else "–")
        self.info(area, f"{key}: {old!r} → {new!r} ({by})")

    # ── Lesen und Aufräumen ───────────────────────────────────────────────────
    def query(self, since: float = 0, until: float = 0, levels=(), room: str = "", device: str = "", area: str = "",
              search: str = "", limit: int = 500) -> list[dict]:
        sql, args = "SELECT * FROM log WHERE t>=?", [since]
        if until:
            sql += " AND t<=?"
            args.append(until)
        if levels:
            sql += f" AND level IN ({','.join('?' * len(levels))})"
            args += list(levels)
        for col, val in (("room", room), ("device", device), ("area", area)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        if search:
            sql += " AND text LIKE ?"
            args.append(f"%{search}%")
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in self.db.all(sql, args)]

    def cleanup(self) -> int:
        """Alte Einträge löschen: Aufbewahrung 1–365 Tage (Standard 30), Debug eigene Frist (Standard 3 Tage)."""
        keep = min(365, max(1, int(self.db.setting("protokoll.aufbewahrung_tage", 30))))
        keep_debug = min(365, max(1, int(self.db.setting("protokoll.debug_aufbewahrung_tage", 3))))
        t = self.clock()
        a = self.db.execute("DELETE FROM log WHERE level!='debug' AND t<?", (t - keep * 86400,)).rowcount
        b = self.db.execute("DELETE FROM log WHERE level='debug' AND t<?", (t - keep_debug * 86400,)).rowcount
        return a + b
