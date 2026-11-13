"""Belegungen, Beiträge und verteilte Einstellungen (Stufe 2).

Belegen: atomar in der Datenbank – die erste Anfrage gewinnt; derselbe Raum darf erneut belegen. Freigeben darf jeder
Raum (wie in Ottilie: jeder Mischer darf freigeben, festgehalten wird, wer es tat). Nach einem Verbindungsabbruch meldet
eine Ottilie ihre Belegungen vollständig: Fehlende des Raums gelten als freigegeben, fremd belegte als Konflikt – die
Zentrale entscheidet nicht selbst, sie zeigt den Konflikt (Administration → Betrieb).

Beiträge: was ein Schnittraum unter „Meine Beiträge“ sieht (übergeben → belegt → in Mischung → Audio Export fertig),
7 Tage lang. Keine Personen, nur Räume.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable

from app.db import Database

STEPS = ("uebergeben", "belegt", "in_mischung", "audio_export_fertig")
KEEP_ITEMS_S = 7 * 86400
SCHEMA = """
CREATE TABLE IF NOT EXISTS claims(beitrag TEXT PRIMARY KEY, room TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    t REAL NOT NULL);
CREATE TABLE IF NOT EXISTS conflicts(beitrag TEXT NOT NULL, room TEXT NOT NULL, holder TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '', t REAL NOT NULL, PRIMARY KEY(beitrag, room));
CREATE TABLE IF NOT EXISTS items(beitrag TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL
    DEFAULT '', origin_room TEXT NOT NULL DEFAULT '', step TEXT NOT NULL, mixroom TEXT NOT NULL DEFAULT '',
    t REAL NOT NULL);
"""
# Was die Administration unter „Übergabeordner“ an alle Ottilies verteilt (Punktpfade der Ottilie-Einstellungen).
# Nie Namen von Schlüsselbund-Einträgen oder die Fertigmeldung: eine gekaperte Zentrale könnte sonst ein Geheimnis
# eines Macs an eine Adresse ihrer Wahl lenken (Sicherheit 11.11.2026; Ottilie verwirft solche Werte ohnehin).
AUTH_KINDS = ("gast", "kerberos", "schluesselbund")
# Weitere Übergabeordner nur mit Gast oder Kerberos: den Schlüsselbund nimmt Ottilie nur für Netzadressen vom Mac
# selbst (IT-Administrator 13.11.2026: sonst stand der Ordner dauerhaft auf „nicht verbunden“)
FOLDER_AUTH_KINDS = ("gast", "kerberos")
FOLDER_KEYS = {
    "system.uebergabeordner.betriebsart": ("Betriebsart", "enum", ("helmut_api", "uebergabeordner")),
    "system.uebergabeordner.name": ("Name des Übergabeordners (Herkunft in der Liste)", "str", None),
    "system.uebergabeordner.weitere_ordner": ("Weitere Übergabeordner", "folders", FOLDER_AUTH_KINDS),
    "system.uebergabeordner.wurzelordner": ("Übergabeordner auf dem Mac (Pfad)", "str", None),
    "system.uebergabeordner.netzadresse": ("Netzadresse (smb://Server/Freigabe/Ordner)", "str", None),
    "system.uebergabeordner.netz_anmeldung": ("Anmeldung an der Freigabe", "enum",
                                              ("gast", "kerberos", "schluesselbund")),
    "system.uebergabeordner.uebergabe_json": ("Name der Übergabe-JSON", "str", None),
    "system.uebergabeordner.wartezeit_s": ("Wartezeit (s)", "int", (0, 120)),
    "system.uebergabeordner.abfrageintervall_s": ("Abfrageintervall (s)", "int", (5, 600)),
    "system.uebergabeordner.ordner_ignorieren": ("Ignorierte Präfixe (kommagetrennt)", "list", None),
    "system.uebergabeordner.exportordner": ("Exportordner", "str", None),
    "system.uebergabeordner.erneute_lieferung": ("Erneute Lieferung", "enum", ("ersetzen", "zeitstempel")),
    "system.uebergabeordner.sperre_verwaist_h": ("Sperre gilt als verwaist nach (h)", "int", (1, 168)),
}


def parse_value(kind: str, extra, raw: str):
    """Formularwert → Wert; leer = nicht zentral vorgegeben (None). ValueError bei Unzulässigem."""
    raw = (raw or "").strip()
    if raw == "":
        return None
    if kind == "enum":
        if raw not in extra:
            raise ValueError(f"„{raw}“ ist nicht erlaubt")
        return raw
    if kind == "int":
        v = int(raw)
        if not extra[0] <= v <= extra[1]:
            raise ValueError(f"{v} liegt nicht zwischen {extra[0]} und {extra[1]}")
        return v
    if kind == "bool":
        return raw in ("1", "ja", "true", "on")
    if kind == "list":
        return [x.strip() for x in raw.split(",") if x.strip()]
    if kind == "folders":
        return parse_folders(raw, extra)
    if len(raw) > 500 or any(ord(c) < 32 for c in raw):
        raise ValueError("Wert zu lang oder mit Steuerzeichen")
    return raw


def slug(text: str) -> str:
    """Kennung eines Übergabeordners aus seinem Namen – wie Ottilie (`tonmischung.core.naming.slug`)."""
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_folders(raw: str, auth_kinds) -> list[dict] | None:
    """Weitere Übergabeordner aus dem Formular (je Zeile Name | Netzadresse | Anmeldung | Pfad)."""
    out, names = [], set()
    for n, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        if len(line) > 500 or any(ord(c) < 32 for c in line):
            raise ValueError(f"Zeile {n}: zu lang oder mit Steuerzeichen")
        parts = [p.strip() for p in line.split("|")] + ["", "", "", ""]
        name, net, auth, path = parts[:4]
        if not name or len(name) > 60:
            raise ValueError(f"Zeile {n}: Name fehlt oder ist zu lang")
        if not slug(name) or slug(name) in names:
            raise ValueError(f"Zeile {n}: Name „{name}“ doppelt oder ohne Buchstaben und Ziffern")
        if net and not net.lower().startswith("smb://"):
            raise ValueError(f"Zeile {n}: Netzadresse nur smb://")
        if auth and auth not in auth_kinds:
            raise ValueError(f"Zeile {n}: Anmeldung „{auth}“ ist nicht erlaubt")
        if not net and not path:
            raise ValueError(f"Zeile {n}: Netzadresse oder Pfad angeben")
        names.add(slug(name))
        out.append({k: v for k, v in (("name", name), ("netzadresse", net), ("netz_anmeldung", auth or "gast"),
                                      ("wurzelordner", path)) if v})
    return out or None


def folder_keys(entries) -> set[str]:
    """Kennungen der weiteren Übergabeordner (aus dem Namen – wie in Ottilie)."""
    return {slug(str(e.get("name") or "")) for e in entries or [] if isinstance(e, dict)}


def folder_lines(entries) -> str:
    """Formular-Text der weiteren Übergabeordner (eine Zeile je Ordner)."""
    lines = [" | ".join([e.get("name", ""), e.get("netzadresse", ""), e.get("netz_anmeldung", ""),
                         e.get("wurzelordner", "")]).rstrip(" |") for e in entries or [] if isinstance(e, dict)]
    return "\n".join(lines)


class Work:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self.db, self.clock = db, clock
        with db._lock:
            db._con.executescript(SCHEMA)
            cols = [r[1] for r in db._con.execute("PRAGMA table_info(items)")]
            if "nr" not in cols:
                db._con.execute("ALTER TABLE items ADD COLUMN nr INTEGER NOT NULL DEFAULT 0")

    # ── Belegen ───────────────────────────────────────────────────────────────
    def claim(self, beitrag: str, room: str, title: str = "") -> tuple[bool, str]:
        """(gewonnen, Raum, der ihn hat). Atomar: BEGIN IMMEDIATE."""
        with self.db.transaction():
            row = self.db.one("SELECT room FROM claims WHERE beitrag=?", (beitrag,))
            if row is not None and row["room"] != room:
                return False, row["room"]
            self.db.execute("INSERT INTO claims(beitrag, room, title, t) VALUES(?,?,?,?) ON CONFLICT(beitrag) DO "
                            "UPDATE SET title=excluded.title, t=excluded.t", (beitrag, room, title[:200], self.clock()))
            self.db.execute("DELETE FROM conflicts WHERE beitrag=? AND room=?", (beitrag, room))
            return True, room

    def release(self, beitrag: str) -> str:
        """Freigeben; Rückgabe: wer ihn hatte ("" = niemand)."""
        with self.db.transaction():
            row = self.db.one("SELECT room FROM claims WHERE beitrag=?", (beitrag,))
            self.db.execute("DELETE FROM claims WHERE beitrag=?", (beitrag,))
            self.db.execute("DELETE FROM conflicts WHERE beitrag=?", (beitrag,))
            return row["room"] if row else ""

    def report(self, room: str, held: list[dict]) -> tuple[list[dict], list[str]]:
        """Belegungen eines Raums nach der Rückkehr → (Konflikte, als freigegeben gewertete Beiträge)."""
        mine = {str(h.get("beitrag_id") or "")[:200]: str(h.get("titel") or "")[:200] for h in held
                if h.get("beitrag_id")}
        conflicts, released = [], []
        with self.db.transaction():
            # Belegungen der letzten 2 Minuten nicht freigeben: Ottilie las ihre Liste vielleicht, bevor sie belegte
            for row in self.db.all("SELECT beitrag FROM claims WHERE room=? AND t<?", (room, self.clock() - 120)):
                if row["beitrag"] not in mine:
                    self.db.execute("DELETE FROM claims WHERE beitrag=?", (row["beitrag"],))
                    released.append(row["beitrag"])
            for beitrag, title in mine.items():
                row = self.db.one("SELECT room FROM claims WHERE beitrag=?", (beitrag,))
                if row is None:
                    self.db.execute("INSERT INTO claims(beitrag, room, title, t) VALUES(?,?,?,?)",
                                    (beitrag, room, title[:200], self.clock()))
                elif row["room"] != room:
                    self.db.execute("INSERT OR REPLACE INTO conflicts(beitrag, room, holder, title, t) "
                                    "VALUES(?,?,?,?,?)", (beitrag, room, row["room"], title[:200], self.clock()))
                    conflicts.append({"beitrag_id": beitrag, "titel": title, "belegt_von": row["room"]})
        return conflicts, released

    def claims(self) -> list[dict]:
        return [dict(r) for r in self.db.all("SELECT * FROM claims ORDER BY t")]

    def in_work(self, room: str) -> list[str]:
        """Was in einem Raum in Arbeit ist (Belegungen, Konflikte) – Titel bzw. Beitrags-IDs."""
        rows = self.db.all("SELECT beitrag, title FROM claims WHERE room=? UNION SELECT beitrag, title FROM conflicts "
                           "WHERE room=? OR holder=?", (room, room, room))
        return [r["title"] or r["beitrag"] for r in rows]

    def rename_room(self, old: str, new: str) -> None:
        """Raumnamen in Belegungen, Konflikten und „Meine Beiträge“ nachziehen."""
        with self.db.transaction():
            self.db.execute("UPDATE claims SET room=? WHERE room=?", (new, old))
            self.db.execute("UPDATE conflicts SET room=? WHERE room=?", (new, old))
            self.db.execute("UPDATE conflicts SET holder=? WHERE holder=?", (new, old))
            self.db.execute("UPDATE items SET origin_room=? WHERE origin_room=?", (new, old))
            self.db.execute("UPDATE items SET mixroom=? WHERE mixroom=?", (new, old))

    def conflicts(self) -> list[dict]:
        return [dict(r) for r in self.db.all("SELECT * FROM conflicts ORDER BY t")]

    # ── Beiträge („Meine Beiträge“) ──────────────────────────────────────────
    def item(self, beitrag: str, step: str, title: str = "", origin: str = "", origin_room: str = "",
             mixroom: str | None = None) -> dict:
        row = self.db.one("SELECT * FROM items WHERE beitrag=?", (beitrag,))
        cur = dict(row) if row else {"beitrag": beitrag, "title": "", "origin": "", "origin_room": "",
                                     "mixroom": "", "step": step}
        cur.update({k: v for k, v in (("title", title), ("origin", origin), ("origin_room", origin_room)) if v})
        if mixroom is not None:
            cur["mixroom"] = mixroom
        cur["step"], cur["t"] = step, self.clock()
        self.db.execute("INSERT OR REPLACE INTO items(beitrag, title, origin, origin_room, step, mixroom, t, nr) "
                        "VALUES(?,?,?,?,?,?,?,?)", (beitrag, cur["title"][:200], cur["origin"][:100],
                                                    cur["origin_room"], step, cur["mixroom"], cur["t"],
                                                    int(cur.get("nr") or 0)))
        return cur

    def set_number(self, beitrag: str, number: int) -> None:
        self.db.execute("UPDATE items SET nr=? WHERE beitrag=?", (int(number), beitrag))

    def get_item(self, beitrag: str) -> dict | None:
        row = self.db.one("SELECT * FROM items WHERE beitrag=?", (beitrag,))
        return dict(row) if row else None

    def items_of(self, room: str) -> list[dict]:
        cut = self.clock() - KEEP_ITEMS_S
        self.db.execute("DELETE FROM items WHERE t<?", (cut,))
        return [dict(r) for r in self.db.all("SELECT * FROM items WHERE origin_room=? OR mixroom=? ORDER BY t DESC",
                                             (room, room))]

    # ── Verteilte Einstellungen ───────────────────────────────────────────────
    def folder_values(self) -> dict:
        return {k: v for k, v in (self.db.setting("verteilt.uebergabeordner", {}) or {}).items() if k in FOLDER_KEYS}

    def set_folder_values(self, values: dict) -> None:
        kept = {k: v for k, v in values.items() if k in FOLDER_KEYS and v is not None}
        self.db.set_setting("verteilt.uebergabeordner", kept)


def item_message(item: dict) -> dict:
    return {"id": item["beitrag"], "titel": item.get("title") or item["beitrag"], "schritt": item["step"],
            "mischraum": item.get("mixroom", ""), "schnittraum": item.get("origin_room", ""), "zeit": item.get("t", 0)}


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)
