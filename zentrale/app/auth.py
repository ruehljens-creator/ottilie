"""Administratoren, Sitzungen, Räume, Kopplung und Geräte.

- Kennwörter mit scrypt (Standardbibliothek; Jens 11.11.2026), Vergleich in konstanter Zeit.
- Erste Anmeldung admin/admin, danach Kennwortzwang (mind. 10 Zeichen) vor jeder anderen Seite.
- 5 Fehlversuche → 5 min Sperre (je Konto). Sitzung 8 h (einstellbar), Sitzungs- und CSRF-Token zufällig, in der
  Datenbank nur der Hash des Sitzungstokens.
- Kopplungscode: 6 Zeichen ohne Verwechsler, 15 min gültig, einmalig, nur als Hash gespeichert. Einlösen ergibt
  einen Geräteschlüssel (32 Byte, nur als SHA-256 gespeichert). Ein Gerät gehört zu einem Raum – nie zu einer Person.
- Anmelden ohne Code (Jens 13.11.2026): Ein Mac bekommt einen Schlüssel, ist aber **wartend**, bis die Administration
  ihn einem Raum zuordnet. Die Gerätekennung (6 Zeichen, aus dem Schlüssel abgeleitet) steht auf der Admin-Seite und
  in Ottilie – so lässt sich vergleichen, welcher Mac es ist. Höchstens 20 wartende, nach 24 h verfallen sie.
- Umziehen und Umbenennen: nur, wenn im Raum nichts in Arbeit ist (prüft der Aufrufer, `Flows.room_busy`).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.db import ROOM_KINDS, Database

CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LEN = 6
CODE_TTL_S = 15 * 60
MAX_FAILED = 5
LOCK_S = 5 * 60
MIN_PASSWORD = 10
SESSION_H = 8
PENDING_TTL_S = 24 * 3600
MAX_PENDING = 20
MAX_PENDING_PER_HOST = 2
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


class AuthError(Exception):
    """Anmeldung oder Kopplung abgelehnt – Text für die Anzeige (ohne Hinweis, ob es das Konto gibt)."""


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=64 << 20, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 << 20, dklen=len(bytes.fromhex(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


_DUMMY = hash_password("unbekanntes-konto", b"0" * 16)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_code(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def fingerprint(key: str) -> str:
    """Gerätekennung aus dem Schlüssel – Ottilie rechnet sie gleich (`tonmischung.zentrale.device.fingerprint`)."""
    digest = hashlib.sha256(("ottilie-geraet:" + key).encode("utf-8")).digest()
    return "".join(CODE_ALPHABET[b % len(CODE_ALPHABET)] for b in digest[:CODE_LEN])


def clean_room_name(name: str) -> str:
    name = " ".join(str(name).split())
    if not name or len(name) > 60:
        raise AuthError("Raumname fehlt oder ist zu lang")
    return name


@dataclass(frozen=True)
class Session:
    admin: str
    csrf: str
    must_change: bool


@dataclass(frozen=True)
class Device:
    id: int
    room_id: int
    room: str
    name: str


@dataclass(frozen=True)
class PendingDevice:
    id: int
    name: str
    fingerprint: str
    host: str
    created: float


class Auth:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self.db, self.clock = db, clock
        self._pending_seen: dict[int, float] = {}
        if not self.db.one("SELECT 1 FROM admins"):
            self.db.execute("INSERT INTO admins(name, hash, must_change, created) VALUES(?,?,1,?)",
                            ("admin", hash_password("admin"), self.clock()))

    # ── Administratoren ───────────────────────────────────────────────────────
    def login(self, name: str, password: str) -> tuple[str, Session]:
        """(Sitzungstoken, Sitzung) – sonst AuthError. Gesperrte Konten prüfen kein Kennwort."""
        row = self.db.one("SELECT * FROM admins WHERE name=?", (name,))
        t = self.clock()
        if row is not None and row["locked_until"] > t:
            raise AuthError(f"Zu viele Fehlversuche – gesperrt für {int(row['locked_until'] - t) // 60 + 1} min")
        if row is None:
            check_password(password, _DUMMY)            # gleiche Dauer – verrät nicht, ob es das Konto gibt
        if row is None or not check_password(password, row["hash"]):
            if row is not None:
                failed = row["failed"] + 1
                locked = t + LOCK_S if failed >= MAX_FAILED else 0
                self.db.execute("UPDATE admins SET failed=?, locked_until=? WHERE name=?",
                                (0 if locked else failed, locked, name))
                if locked:
                    raise AuthError("Zu viele Fehlversuche – gesperrt für 5 min")
            raise AuthError("Anmeldung fehlgeschlagen")
        self.db.execute("UPDATE admins SET failed=0, locked_until=0 WHERE name=?", (name,))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        hours = float(self.db.setting("admin.sitzung_h", SESSION_H) or SESSION_H)
        self.db.execute("DELETE FROM sessions WHERE valid_until<?", (t,))
        self.db.execute("INSERT INTO sessions(token_hash, admin, csrf, valid_until) VALUES(?,?,?,?)",
                        (sha(token), name, csrf, t + hours * 3600))
        return token, Session(name, csrf, bool(row["must_change"]))

    def session(self, token: str | None) -> Session | None:
        if not token:
            return None
        row = self.db.one("SELECT s.admin, s.csrf, s.valid_until, a.must_change FROM sessions s JOIN admins a "
                          "ON a.name=s.admin WHERE s.token_hash=?", (sha(token),))
        if row is None or row["valid_until"] <= self.clock():
            return None
        return Session(row["admin"], row["csrf"], bool(row["must_change"]))

    def logout(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (sha(token),))

    def change_password(self, name: str, old: str, new: str, token: str | None = None) -> None:
        row = self.db.one("SELECT hash FROM admins WHERE name=?", (name,))
        if row is None or not check_password(old, row["hash"]):
            raise AuthError("Bisheriges Kennwort stimmt nicht")
        self._set_password(name, new)
        # andere Sitzungen des Kontos beenden, die eigene bleibt
        keep = sha(token) if token else ""
        self.db.execute("DELETE FROM sessions WHERE admin=? AND token_hash!=?", (name, keep))

    def _set_password(self, name: str, new: str, must_change: bool = False) -> None:
        if len(new) < MIN_PASSWORD:
            raise AuthError(f"Das Kennwort braucht mindestens {MIN_PASSWORD} Zeichen")
        if new == "admin" or new.lower() == name.lower():
            raise AuthError("Dieses Kennwort ist nicht zulässig")
        self.db.execute("UPDATE admins SET hash=?, must_change=?, failed=0, locked_until=0 WHERE name=?",
                        (hash_password(new), int(must_change), name))

    def add_admin(self, name: str, password: str) -> None:
        name = name.strip()
        if not name or len(name) > 40:
            raise AuthError("Name fehlt oder ist zu lang")
        if self.db.one("SELECT 1 FROM admins WHERE name=?", (name,)):
            raise AuthError("Den Namen gibt es schon")
        self.db.execute("INSERT INTO admins(name, hash, must_change, created) VALUES(?,?,1,?)",
                        (name, hash_password("x" * MIN_PASSWORD), self.clock()))
        self._set_password(name, password, must_change=True)

    def remove_admin(self, name: str) -> None:
        if len(self.db.all("SELECT 1 FROM admins")) <= 1:
            raise AuthError("Der letzte Administrator bleibt")
        self.db.execute("DELETE FROM sessions WHERE admin=?", (name,))
        self.db.execute("DELETE FROM admins WHERE name=?", (name,))

    def reset_password(self, name: str = "admin") -> str:
        """Nur per Befehl im Container: neues Einmal-Kennwort, beim nächsten Anmelden zu ändern."""
        temp = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
        if not self.db.one("SELECT 1 FROM admins WHERE name=?", (name,)):
            raise AuthError(f"Konto „{name}“ gibt es nicht")
        self._set_password(name, temp, must_change=True)
        self.db.execute("DELETE FROM sessions WHERE admin=?", (name,))
        return temp

    def admins(self) -> list[dict]:
        rows = self.db.all("SELECT name, must_change, locked_until, created FROM admins ORDER BY name")
        return [dict(r) for r in rows]

    # ── Räume ─────────────────────────────────────────────────────────────────
    def add_room(self, name: str, kind: str = "mischraum") -> int:
        name = clean_room_name(name)
        if kind not in ROOM_KINDS:
            raise AuthError("Unbekannte Raumart")
        if self.db.one("SELECT 1 FROM rooms WHERE name=?", (name,)):
            raise AuthError("Den Raum gibt es schon")
        return int(self.db.execute("INSERT INTO rooms(name, kind, created) VALUES(?,?,?)",
                                   (name, kind, self.clock())).lastrowid)

    def room_id(self, name: str) -> int | None:
        row = self.db.one("SELECT id FROM rooms WHERE name=?", (name,))
        return int(row["id"]) if row else None

    def rooms(self) -> list[dict]:
        out = []
        for r in self.db.all("SELECT * FROM rooms ORDER BY name"):
            aliases = [a["alias"] for a in self.db.all("SELECT alias FROM aliases WHERE room_id=? ORDER BY alias",
                                                       (r["id"],))]
            out.append({**dict(r), "aliases": aliases})
        return out

    def rename_room(self, room_id: int, new_name: str) -> str:
        """Raum umbenennen → bisheriger Name. Belegungen, Beiträge und Chat zieht der Aufrufer nach."""
        new_name = clean_room_name(new_name)
        row = self.db.one("SELECT name FROM rooms WHERE id=?", (room_id,))
        if row is None:
            raise AuthError("Raum nicht gefunden")
        if new_name == row["name"]:
            raise AuthError("Der Raum heißt schon so")
        if self.db.one("SELECT 1 FROM rooms WHERE name=? AND id!=?", (new_name, room_id)):
            raise AuthError("Den Raum gibt es schon")
        self.db.execute("UPDATE rooms SET name=? WHERE id=?", (new_name, room_id))
        return str(row["name"])

    def room_name(self, room_id: int) -> str | None:
        row = self.db.one("SELECT name FROM rooms WHERE id=?", (room_id,))
        return str(row["name"]) if row else None

    def remove_room(self, room_id: int) -> None:
        for d in self.db.all("SELECT id FROM devices WHERE room_id=?", (room_id,)):
            self.revoke(int(d["id"]))
        self.db.execute("DELETE FROM aliases WHERE room_id=?", (room_id,))
        self.db.execute("DELETE FROM pair_codes WHERE room_id=?", (room_id,))
        self.db.execute("DELETE FROM devices WHERE room_id=?", (room_id,))
        self.db.execute("DELETE FROM rooms WHERE id=?", (room_id,))

    def set_aliases(self, room_id: int, aliases: list[str]) -> None:
        """Schreibweisen aus `herkunft.schnittraum` (z. B. „SR4“) → dieser Raum. Vergleich ohne Groß/Klein."""
        clean = sorted({" ".join(a.split()).casefold() for a in aliases if a.strip()})
        with self.db.transaction():
            self.db.execute("DELETE FROM aliases WHERE room_id=?", (room_id,))
            for a in clean:
                self.db.execute("INSERT OR REPLACE INTO aliases(alias, room_id) VALUES(?,?)", (a, room_id))

    def room_for_alias(self, text: str) -> str | None:
        key = " ".join(str(text).split()).casefold()
        row = self.db.one("SELECT r.name FROM aliases a JOIN rooms r ON r.id=a.room_id WHERE a.alias=?", (key,))
        if row:
            return row["name"]
        row = self.db.one("SELECT name FROM rooms WHERE lower(name)=?", (key,))
        return row["name"] if row else None

    # ── Kopplung und Geräte ───────────────────────────────────────────────────
    def pairing_code(self, room_id: int) -> str:
        if not self.db.one("SELECT 1 FROM rooms WHERE id=?", (room_id,)):
            raise AuthError("Raum nicht gefunden")
        t = self.clock()
        self.db.execute("DELETE FROM pair_codes WHERE expires<?", (t,))
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
        self.db.execute("INSERT INTO pair_codes(code_hash, room_id, expires) VALUES(?,?,?)",
                        (sha(code), room_id, t + CODE_TTL_S))
        return code

    def pair(self, code: str, device_name: str) -> tuple[str, Device]:
        """Code einlösen (einmalig, atomar) → (Geräteschlüssel, Gerät)."""
        code = normalize_code(code)
        name = " ".join(str(device_name).split())[:60] or "Gerät"
        if len(code) != CODE_LEN:
            raise AuthError("Kopplungscode ungültig oder abgelaufen")
        t = self.clock()
        key = secrets.token_urlsafe(32)
        with self.db.transaction():
            row = self.db.one("SELECT room_id, expires FROM pair_codes WHERE code_hash=?", (sha(code),))
            if row is None or row["expires"] < t:
                raise AuthError("Kopplungscode ungültig oder abgelaufen")
            self.db.execute("DELETE FROM pair_codes WHERE code_hash=?", (sha(code),))
            dev_id = int(self.db.execute("INSERT INTO devices(room_id, name, key_hash, created, last_seen) "
                                         "VALUES(?,?,?,?,?)", (row["room_id"], name, sha(key), t, t)).lastrowid)
        return key, self.device_by_id(dev_id)

    def device(self, key: str | None) -> Device | None:
        if not key or not isinstance(key, str) or len(key) > 200:
            return None
        row = self.db.one("SELECT d.id, d.room_id, d.name, r.name AS room FROM devices d JOIN rooms r "
                          "ON r.id=d.room_id WHERE d.key_hash=? AND d.revoked=0", (sha(key),))
        return Device(int(row["id"]), int(row["room_id"]), row["room"], row["name"]) if row else None

    def device_by_id(self, dev_id: int) -> Device:
        row = self.db.one("SELECT d.id, d.room_id, d.name, r.name AS room FROM devices d JOIN rooms r "
                          "ON r.id=d.room_id WHERE d.id=?", (dev_id,))
        return Device(int(row["id"]), int(row["room_id"]), row["room"], row["name"])

    def seen(self, dev_id: int) -> None:
        self.db.execute("UPDATE devices SET last_seen=? WHERE id=?", (self.clock(), dev_id))

    def revoke(self, dev_id: int) -> None:
        self.db.execute("UPDATE devices SET revoked=1 WHERE id=?", (dev_id,))

    def move_device(self, dev_id: int, room_id: int) -> Device:
        """Gerät einem anderen Raum zuordnen (ohne neu zu koppeln) → Gerät mit neuem Raum."""
        if not self.db.one("SELECT 1 FROM rooms WHERE id=?", (room_id,)):
            raise AuthError("Raum nicht gefunden")
        if not self.db.one("SELECT 1 FROM devices WHERE id=? AND revoked=0", (dev_id,)):
            raise AuthError("Gerät nicht gefunden")
        self.db.execute("UPDATE devices SET room_id=? WHERE id=?", (room_id, dev_id))
        return self.device_by_id(dev_id)

    # ── Anmelden ohne Code: wartende Geräte ───────────────────────────────────
    def _expire_pending(self) -> None:
        # Verfall ab dem letzten Lebenszeichen (IT-Administrator 13.11.2026: anmelden am Freitag, zuordnen am Montag –
        # ein verbundener Mac verfällt nicht)
        self.db.execute("DELETE FROM pending_devices WHERE last_seen<?", (self.clock() - PENDING_TTL_S,))

    def register(self, device_name: str, host: str = "") -> tuple[str, PendingDevice]:
        """Gerät meldet sich ohne Code an → (Schlüssel, wartendes Gerät). Es sieht nichts, bis es zugeordnet ist."""
        name = " ".join(str(device_name).split())[:60] or "Gerät"
        t = self.clock()
        key = secrets.token_urlsafe(32)
        with self.db.transaction():
            self._expire_pending()
            n = self.db.one("SELECT count(*) AS n FROM pending_devices")["n"]
            if n >= MAX_PENDING:
                raise AuthError("Zu viele wartende Geräte – bitte später erneut oder mit Kopplungscode")
            # je Adresse höchstens zwei (Sicherheit 13.11.2026: sonst füllte eine Adresse die Liste für alle)
            if host and self.db.one("SELECT count(*) AS n FROM pending_devices WHERE host=?",
                                    (str(host)[:100],))["n"] >= MAX_PENDING_PER_HOST:
                raise AuthError("Von dieser Adresse warten schon Geräte – erst zuordnen oder ablehnen lassen")
            pid = int(self.db.execute(
                "INSERT INTO pending_devices(name, key_hash, fingerprint, host, created, last_seen) "
                "VALUES(?,?,?,?,?,?)", (name, sha(key), fingerprint(key), str(host)[:100], t, t)).lastrowid)
        return key, self.pending_by_id(pid)

    def pending(self, key: str | None) -> PendingDevice | None:
        if not key or not isinstance(key, str) or len(key) > 200:
            return None
        row = self.db.one("SELECT * FROM pending_devices WHERE key_hash=? AND last_seen>=?",
                          (sha(key), self.clock() - PENDING_TTL_S))
        return self._pending(row) if row else None

    def pending_by_id(self, pid: int) -> PendingDevice:
        row = self.db.one("SELECT * FROM pending_devices WHERE id=?", (pid,))
        if row is None:
            raise AuthError("Wartendes Gerät nicht gefunden (abgelaufen?)")
        return self._pending(row)

    @staticmethod
    def _pending(row) -> PendingDevice:
        return PendingDevice(int(row["id"]), row["name"], row["fingerprint"], row["host"], float(row["created"]))

    def pending_list(self) -> list[PendingDevice]:
        self._expire_pending()
        return [self._pending(r) for r in self.db.all("SELECT * FROM pending_devices ORDER BY created")]

    def seen_pending(self, pid: int) -> None:
        """Lebenszeichen – höchstens einmal je Minute in die Datenbank (Sicherheit 13.11.2026: vorher bei jeder
        Nachricht ein Schreibvorgang in der Ereignisschleife)."""
        t = self.clock()
        if t - self._pending_seen.get(pid, 0.0) < 60:
            return
        if len(self._pending_seen) > 1000:
            self._pending_seen.clear()
        self._pending_seen[pid] = t
        self.db.execute("UPDATE pending_devices SET last_seen=? WHERE id=?", (t, pid))

    def assign(self, pid: int, room_id: int, kennung: str = "") -> Device:
        """Wartendes Gerät einem Raum zuordnen – derselbe Schlüssel gilt danach als gekoppelt. `kennung`: was die
        Administration gesehen hat (Sicherheit 13.11.2026: Zeilen-IDs werden wiederverwendet – sonst koppelte ein Klick
        in einer veralteten Seite ein inzwischen anderes Gerät)."""
        t = self.clock()
        with self.db.transaction():
            row = self.db.one("SELECT * FROM pending_devices WHERE id=? AND last_seen>=?", (pid, t - PENDING_TTL_S))
            if row is None:
                raise AuthError("Wartendes Gerät nicht gefunden (abgelaufen?)")
            if kennung and row["fingerprint"] != kennung:
                raise AuthError("Das wartende Gerät hat sich inzwischen geändert – Seite neu laden und vergleichen")
            if not self.db.one("SELECT 1 FROM rooms WHERE id=?", (room_id,)):
                raise AuthError("Raum nicht gefunden")
            dev_id = int(self.db.execute("INSERT INTO devices(room_id, name, key_hash, created, last_seen) "
                                         "VALUES(?,?,?,?,?)", (room_id, row["name"], row["key_hash"], t,
                                                               float(row["last_seen"]))).lastrowid)
            self.db.execute("DELETE FROM pending_devices WHERE id=?", (pid,))
        return self.device_by_id(dev_id)

    def reject(self, pid: int) -> PendingDevice:
        dev = self.pending_by_id(pid)
        self.db.execute("DELETE FROM pending_devices WHERE id=?", (pid,))
        return dev

    def devices(self) -> list[dict]:
        return [dict(r) for r in self.db.all(
            "SELECT d.id, d.name, d.created, d.last_seen, d.revoked, r.name AS room FROM devices d "
            "JOIN rooms r ON r.id=d.room_id ORDER BY r.name, d.name")]
