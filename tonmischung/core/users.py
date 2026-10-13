"""Nutzer der App (Plan Nutzer & Räume, Schritt 1) – ohne Passwort.

Jens (04.10.2026): Nutzer anlegen, duplizieren, löschen; Anmeldung aus einem Dropdown, beim App-Start fragen oder einen
Standard-Nutzer je Mac automatisch anmelden. Ablage lokal je Mac, wahlweise ein gemeinsamer Ordner – keine Datenbank:

    <Ablage>/<id>/nutzer.json          Name, angelegt, Stand
    <Ablage>/<id>/einstellungen.json   Einstellungen des Nutzers (Schritt 2)
    <Ablage>/<id>/Templates/           hochgeladene Pro-Tools-Templates (Schritt 3)
    <Ablage>/Archiv/<id>-<Zeit>/       gelöschte Nutzer (verschoben, nicht gelöscht)

Geschrieben wird atomar (Hilfsdatei, dann umbenennen); vorhandene Ordner werden nie überschrieben.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tonmischung.core.fileio import write_json
from tonmischung.core.naming import ascii_umlauts

META = "nutzer.json"
SETTINGS = "einstellungen.json"
ARCHIVE = "Archiv"


class UserError(ValueError):
    """Nutzer so nicht anlegbar/änderbar – Meldung für das Fenster."""


@dataclass(frozen=True)
class User:
    id: str
    name: str
    created: str = ""


def default_root(mock: bool = False) -> Path:
    name = "Nutzer-Mock" if mock else "Nutzer"
    from tonmischung.paths import support_dir
    return support_dir() / name


def slug(name: str) -> str:
    """„Jens Rühl“ → „jens-ruehl“ (Ordnername)."""
    s = ascii_umlauts(name.strip())
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "nutzer"


def _write_json(path: str, data: dict) -> None:
    write_json(path, data)                      # eigene Verwaltungsdatei (atomar, fsync)


class UserStore:
    def __init__(self, root: str | Path, now: Callable[[], datetime] = datetime.now) -> None:
        self.root = str(root)
        self._now = now

    # ── lesen ────────────────────────────────────────────────────────────────
    def users(self) -> list[User]:
        out = []
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        for d in names:
            if d.startswith(".") or d == ARCHIVE:
                continue
            u = self._read(d)
            if u is not None:
                out.append(u)
        return sorted(out, key=lambda u: u.name.lower())

    def get(self, user_id: str) -> User | None:
        return self._read(user_id) if user_id and "/" not in user_id else None

    def folder(self, user_id: str) -> str:
        return os.path.join(self.root, user_id)

    def _read(self, user_id: str) -> User | None:
        try:
            with open(os.path.join(self.root, user_id, META), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        return User(user_id, str(d.get("name") or user_id), str(d.get("angelegt") or ""))

    # ── ändern ───────────────────────────────────────────────────────────────
    def _check_name(self, name: str, except_id: str = "") -> str:
        name = " ".join(str(name or "").split())
        if not name:
            raise UserError("Bitte einen Namen eingeben")
        if any(u.name.lower() == name.lower() and u.id != except_id for u in self.users()):
            raise UserError(f"„{name}“ gibt es schon")
        return name

    def _free_id(self, name: str) -> str:
        base, n = slug(name), 1
        uid = base
        while os.path.exists(os.path.join(self.root, uid)) or uid == ARCHIVE.lower():
            n += 1
            uid = f"{base}-{n}"
        return uid

    def create(self, name: str) -> User:
        name = self._check_name(name)
        uid = self._free_id(name)
        os.makedirs(os.path.join(self.root, uid))       # exist_ok=False: nie einen vorhandenen Ordner benutzen
        stamp = self._now().isoformat(timespec="seconds")
        _write_json(os.path.join(self.root, uid, META), {"name": name, "angelegt": stamp})
        self.save_settings(uid, {})                      # neuer Nutzer: Hausstandard (übernimmt nichts vom Mac)
        return User(uid, name, stamp)

    def rename(self, user_id: str, name: str) -> User:
        u = self.get(user_id)
        if u is None:
            raise UserError("Nutzer nicht gefunden")
        name = self._check_name(name, except_id=user_id)
        _write_json(os.path.join(self.root, user_id, META), {"name": name, "angelegt": u.created})
        return User(user_id, name, u.created)

    def duplicate(self, user_id: str, name: str) -> User:
        """Kopie mit allen Einstellungen und Templates unter neuem Namen."""
        src = self.get(user_id)
        if src is None:
            raise UserError("Nutzer nicht gefunden")
        name = self._check_name(name)
        uid = self._free_id(name)
        shutil.copytree(os.path.join(self.root, user_id), os.path.join(self.root, uid))   # Ziel darf nicht existieren
        stamp = self._now().isoformat(timespec="seconds")
        _write_json(os.path.join(self.root, uid, META), {"name": name, "angelegt": stamp, "kopie_von": src.name})
        return User(uid, name, stamp)

    # ── Einstellungen (Schritt 2) ─────────────────────────────────────────────
    def has_settings(self, user_id: str) -> bool:
        return os.path.isfile(os.path.join(self.root, user_id, SETTINGS))

    def load_settings(self, user_id: str) -> dict:
        try:
            with open(os.path.join(self.root, user_id, SETTINGS), encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            raise UserError(f"Einstellungen von „{user_id}“ nicht lesbar: {e}") from e
        return dict(data.get("werte") or {}) if isinstance(data, dict) else {}

    def save_settings(self, user_id: str, values: dict) -> None:
        if self.get(user_id) is None:
            raise UserError("Nutzer nicht gefunden")
        _write_json(os.path.join(self.root, user_id, SETTINGS),
                    {"format": "tonmischung-nutzer-einstellungen", "version": 1,
                     "geaendert": self._now().isoformat(timespec="seconds"), "werte": values})

    def archive(self, user_id: str) -> str:
        """Löschen = ins Archiv verschieben (Jens 04.10.2026: „Ins Archiv“). Rückgabe: Archivordner."""
        if self.get(user_id) is None:
            raise UserError("Nutzer nicht gefunden")
        dest = os.path.join(self.root, ARCHIVE, f"{user_id}-{self._now().strftime('%Y%m%d-%H%M%S')}")
        n = 2
        while os.path.exists(dest):
            dest = f"{dest.rsplit('~', 1)[0]}~{n}"
            n += 1
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.rename(os.path.join(self.root, user_id), dest)
        return dest
