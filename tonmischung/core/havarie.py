"""Havarie „Paket manuell öffnen“ (M9): Übergabepaket ohne Helmut erkennen; dazu `PackageIndex`, der sich je
Session den Paketordner merkt (für „Sendefile ausliefern“).

Funktionsbeschreibung S. 13: „Paket manuell öffnen“ importiert ohne Helmut direkt aus einem Übergabepaket auf
dem Storage. Jens (03.10.2026): AAF und Video in jedem Fall, ein Manifest wird nicht gebraucht.
Gesucht wird im gewählten Ordner und eine Ebene darunter (z. B. `…/Uebergabe/`); je Art die neueste Datei.
Die Helmut-Projekt-ID ist optional: aus dem Ordnernamen, wenn er wie eine ID aussieht, sonst später zuordnen.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass

from tonmischung.helmut.mapping import AAF_EXT, VIDEO_EXT

_ID = re.compile(r"^(?:[0-9a-f]{24}|[0-9a-f]{32}|[0-9a-f-]{36}|\d{4,})$", re.IGNORECASE)   # Mongo-, UUID- oder Zahl-ID


@dataclass(frozen=True)
class Package:
    folder: str
    aaf: str
    video: str = ""
    title: str = ""
    project_id: str = ""                  # Vorschlag aus dem Ordnernamen, sonst leer

    @property
    def complete(self) -> bool:
        return bool(self.aaf and self.video)


def _files(folder: str, listdir: Callable[[str], list[str]], isdir: Callable[[str], bool]) -> list[str]:
    out = []
    try:
        names = listdir(folder)
    except OSError:
        return []
    for name in names:
        if name.startswith("."):
            continue
        p = os.path.join(folder, name)
        if isdir(p):
            try:
                out += [os.path.join(p, n) for n in listdir(p) if not n.startswith(".")]
            except OSError:
                pass
        else:
            out.append(p)
    return out


def title_from(aaf: str) -> str:
    stem = os.path.splitext(os.path.basename(aaf))[0]
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"(?i)\b(for ?pt|final|aaf)\b", " ", stem)
    return re.sub(r"\s+", " ", stem).strip(" -.") or stem


def find_package(folder: str, listdir: Callable[[str], list[str]] = os.listdir,
                 isdir: Callable[[str], bool] = os.path.isdir,
                 mtime: Callable[[str], float] = os.path.getmtime) -> Package | None:
    """AAF (Pflicht) und Video im Ordner bzw. eine Ebene tiefer – je die neueste Datei."""
    folder = os.path.normpath(os.path.expanduser(folder))
    files = _files(folder, listdir, isdir)

    def newest(ext: tuple[str, ...]) -> str:
        found = [f for f in files if f.lower().endswith(ext)]
        return max(found, key=lambda f: (mtime(f), f)) if found else ""
    aaf = newest(AAF_EXT)
    if not aaf:
        return None
    name = os.path.basename(folder)
    pid = name if _ID.match(name) else ""
    return Package(folder, aaf, newest(VIDEO_EXT), title_from(aaf), pid)


# ── Welche Session aus welchem Paket kommt (Jens 04.10.2026) ─────────────────────
# „Havarie: Sendefile ausliefern“ braucht den Paketordner (Bild neben dem AAF). Nach dem Neuladen der Oberfläche
# oder einem Neustart fragte der Dialog danach – jetzt schlägt die App ihn für die offene Session vor.
def index_path(mock: bool = False):
    from tonmischung.paths import support_dir
    name = "havarie-pakete-mock.json" if mock else "havarie-pakete.json"
    return support_dir() / name


class PackageIndex:
    """Session-Pfad → Paketordner (eigene kleine JSON-Datei der App)."""

    def __init__(self, path) -> None:
        self.path = str(path)

    def _load(self) -> dict[str, str]:
        from tonmischung.core.fileio import CorruptFileError, read_json, set_aside
        try:
            return read_json(self.path, expect=dict) or {}
        except CorruptFileError:
            set_aside(self.path)                       # nicht überschreiben – nur ein Merkzettel, neu beginnen
            return {}
        except OSError:
            return {}

    def remember(self, session_path: str, folder: str) -> None:
        from tonmischung.core.fileio import write_json
        data = self._load()
        data[os.path.normpath(session_path)] = folder
        write_json(self.path, data)                    # nur die eigene Verzeichnisdatei

    def lookup(self, session_path: str) -> str:
        return self._load().get(os.path.normpath(session_path or ""), "") if session_path else ""
