"""Session-Ablage: wo die Session eines Beitrags liegt und wohin gespiegelt wird (Plan M7-Ablage, reine Logik).

Drei Arten (Einstellung `import.session_ablage.modus`, Jens 03.10.2026: alle einstellbar, Vorgabe
„lokal mit Spiegelung“):
- `lokal`: Session im lokalen Ordner des Macs,
- `lokal_spiegelung`: ebenso, dazu eine Spiegelung in den Projektordner auf dem Helmut-Storage,
- `helmut_storage`: Session direkt im Projektordner.
ANNAHME S1: Projektordner = Ordner von `path` des Helmut-Projekts (sonst `savePath`).
ANNAHME S3: `{version}` = Metadatenfeld „version“, sonst leer.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from tonmischung.core.mixstate import HELMUT_STORAGE as HELMUT
from tonmischung.core.mixstate import LOCAL, MIRROR
from tonmischung.core.naming import clean_name
from tonmischung.helmut.mapping import Contribution

MODES = (HELMUT, LOCAL, MIRROR)
_FILE_EXT = re.compile(r"^\.[A-Za-z0-9]{1,6}$")


class StorageError(Exception):
    """Ablageort nicht bestimmbar – klare Meldung statt einer Session an falscher Stelle."""


def looks_like_file(path: str, isdir: Callable[[str], bool] = os.path.isdir) -> bool:
    """Datei oder Ordner? Ein vorhandener Ordner ist ein Ordner; sonst zählt nur eine echte Endung (1–6 Zeichen,
    nicht nur Ziffern). Vorher galt „Heimatjournal 09.10.2026“ wegen „.2026“ als Datei (Review B13)."""
    if isdir(path):
        return False
    ext = os.path.splitext(path.rstrip("/"))[1]
    return bool(_FILE_EXT.match(ext)) and not ext[1:].isdigit()


def project_folder(raw: Mapping, isdir: Callable[[str], bool] = os.path.isdir) -> str | None:
    """Projektordner aus dem Helmut-Projekt (Datei → ihr Ordner)."""
    for key in ("path", "savePath"):
        p = str(raw.get(key) or "").strip()
        if p:
            return os.path.dirname(p) if looks_like_file(p, isdir) else p.rstrip("/")
    return None


def resolve(template: str, project: str | None, show: str, title: str, version: str = "") -> str:
    """Platzhalter {projekt}, {sendung}, {titel}, {version} ersetzen; {projekt} ist ein Pfad, der Rest Namen."""
    if "{projekt}" in template and not project:
        raise StorageError("Der Projektordner des Beitrags ist in Helmut nicht bekannt – Ablage auf dem "
                           "Helmut-Storage nicht möglich")
    out = (template.replace("{projekt}", (project or "").rstrip("/"))
           .replace("{sendung}", clean_name(show)).replace("{titel}", clean_name(title.split(":", 1)[0]))
           .replace("{version}", clean_name(version)))
    return os.path.normpath(os.path.expanduser(out))


@dataclass(frozen=True)
class Placement:
    mode: str
    parent: str                  # hier legt Pro Tools den Session-Ordner an
    mirror_parent: str = ""      # Spiegelung: hierhin kommt der Session-Ordner (leer = keine)
    hint: str = ""

    def mirror_dir(self, session_folder_name: str) -> str:
        return os.path.join(self.mirror_parent, session_folder_name) if self.mirror_parent else ""


def placement(mode: str, contribution: Contribution, local_folder: str, template: str) -> Placement:
    if mode not in MODES:
        raise StorageError(f"Unbekannte Session-Ablage: {mode}")
    raw = contribution.raw if isinstance(contribution.raw, dict) else {}
    version = next((str(m.get("value") or "") for m in raw.get("metadata") or []
                    if str(m.get("name", "")).lower() == "version"), "")
    project = project_folder(raw)
    if mode == HELMUT:
        return Placement(mode, resolve(template, project, contribution.show, contribution.title, version))
    local = os.path.normpath(os.path.expanduser(local_folder))
    if mode == LOCAL:
        return Placement(mode, local)
    try:
        mirror = resolve(template, project, contribution.show, contribution.title, version)
    except StorageError as e:
        return Placement(LOCAL, local, hint=f"{e} – Session liegt nur lokal, ohne Spiegelung")
    return Placement(mode, local, mirror)
