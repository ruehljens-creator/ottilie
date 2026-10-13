"""Audiodateien von außerhalb des Projekts erkennen und Kopierziele planen (Plan M7 fremde Dateien).

Funktionsbeschreibung S. 33: Geräusche kommen oft aus dem Download-Ordner und werden direkt in die
Timeline gezogen. Liegen sie danach weiter nur dort, fehlen sie an einem anderen Platz. Die App findet
sie (PTSL GetFileLocation) und kopiert sie nach `Audio Files/Extern/` im Session-Ordner.
ANNAHME F1: „im Projekt“ = im Session-Ordner (samt Unterordnern).
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from tonmischung.core.fileio import inside, sha256_file

NEW, DONE, MISSING = "neu", "schon_kopiert", "fehlt"
DEFAULT_SUBFOLDER = "Audio Files/Extern/"
AUDIO_EXT = (".wav", ".aif", ".aiff", ".mp3", ".m4a", ".flac", ".bwf", ".caf")


@dataclass(frozen=True)
class ForeignFile:
    source: str
    status: str                      # NEW | DONE | MISSING
    target: str = ""                 # Kopie im Projekt (bei NEW: wohin; bei DONE: wo sie liegt)


_DIGESTS: dict[tuple[str, int, int], str] = {}


def digest(path: str) -> str:
    """SHA-256 einer Datei, zwischengespeichert je (Pfad, Größe, Änderungszeit) – die Prüfung läuft alle 30 s."""
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _DIGESTS:
        if len(_DIGESTS) > 5000:
            _DIGESTS.clear()
        _DIGESTS[key] = sha256_file(path, chunk=1 << 20)
    return _DIGESTS[key]


def same_content(a: str, b: str) -> bool:
    """Gleicher Inhalt (Größe und SHA-256)? Vorher genügte die Größe – zwei verschiedene Geräusche gleicher Länge
    galten als dieselbe Datei, und die Session hätte auf die falsche gezeigt (Review B12)."""
    return os.path.getsize(a) == os.path.getsize(b) and digest(a) == digest(b)


def plan(files: Iterable[tuple[str, bool]], session_path: str, subfolder: str = DEFAULT_SUBFOLDER,
         exists: Callable[[str], bool] = os.path.exists,
         same: Callable[[str, str], bool] = same_content) -> list[ForeignFile]:
    """Je Datei außerhalb des Session-Ordners: neu zu kopieren, schon kopiert oder fehlt (offline)."""
    folder = os.path.dirname(session_path)
    target_dir = os.path.join(folder, subfolder.strip("/"))
    out: list[ForeignFile] = []
    seen: set[str] = set()
    reserved: set[str] = set()
    for path, online in files:
        if not path or inside(path, folder) or path in seen:
            continue
        seen.add(path)
        if not online or not exists(path):
            out.append(ForeignFile(path, MISSING))
            continue
        stem, ext = os.path.splitext(os.path.basename(path))
        n = 1
        while True:
            name = f"{stem}{ext}" if n == 1 else f"{stem}_{n}{ext}"
            target = os.path.join(target_dir, name)
            if target in reserved:
                n += 1
                continue
            if not exists(target):
                reserved.add(target)
                out.append(ForeignFile(path, NEW, target))
                break
            try:
                identical = same(target, path)
            except OSError:
                identical = False
            if identical:                                # gleiche Datei schon übernommen
                out.append(ForeignFile(path, DONE, target))
                break
            n += 1                                       # anderer Inhalt unter dem Namen: nie überschreiben
    return out


def summary(files: list[ForeignFile]) -> dict:
    """Zähler für Titelleiste und Dialog."""
    return {"neu": sum(f.status == NEW for f in files), "kopiert": sum(f.status == DONE for f in files),
            "fehlt": sum(f.status == MISSING for f in files)}


def archive_candidates(folders: Iterable[str], since: float, listdir: Callable[[str], list[str]] = os.listdir,
                       mtime: Callable[[str], float] = os.path.getmtime, limit: int = 200) -> list[tuple[str, float]]:
    """Neue Audiodateien in den Archiv-Ordnern (ohne Unterordner), neueste zuerst."""
    found: list[tuple[str, float]] = []
    for folder in folders:
        folder = os.path.expanduser(folder)
        try:
            names = listdir(folder)
        except OSError:
            continue
        for name in names:
            if name.startswith(".") or not name.lower().endswith(AUDIO_EXT):
                continue
            p = os.path.join(folder, name)
            try:
                t = mtime(p)
            except OSError:
                continue
            if t >= since:
                found.append((p, t))
    return sorted(found, key=lambda x: x[1], reverse=True)[:limit]
