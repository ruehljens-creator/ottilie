"""Übergabe-JSON eines Filmordners lesen und mit den Dateien abgleichen – reine Logik bis auf `stat`.

Format „uebergabe/1“ (Spezifikation für den Helmut-Entwickler, 11.11.2026):

    {"format": "uebergabe/1", "erstellt": "<ISO 8601>", "uebergabe_nr": 1,
     "beitrag": {"id", "titel", "sendung", "sendetermin" (ISO 8601), "laenge" (HH:MM:SS:FF), "cutter", "videoid",
                 "dringend" (bool)},
     "herkunft": {"schnittraum" (Pflicht), "schnittsystem", "rechner"},
     "dateien": [{"typ": "aaf" | "video", "name": "<Datei>", "groesse": <Bytes>}]}

Fertig heißt: Die JSON liegt im Ordner (Helmut schreibt sie zuletzt), sie lässt sich lesen, und jede genannte Datei
ist da und hat die genannte Größe. Keine Inhaltsprüfung, keine Prüfsummen.
"""
from __future__ import annotations

import errno
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from tonmischung.core.fileio import os_metadata
from tonmischung.helmut.mapping import AAF_EXT, VIDEO_EXT

FORMAT = "uebergabe/1"
READY, INCOMPLETE, TRANSFER = "bereit", "unvollstaendig", "wird_uebertragen"
STATE_TEXT = {READY: "bereit", INCOMPLETE: "unvollständig", TRANSFER: "wird übertragen"}
MAX_FILES = 64                   # mehr Einträge in „dateien“ ist kein Film, sondern ein Fehler (Sicherheit 2/3)
MAX_JSON_BYTES = 1 << 20
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class HandoverError(ValueError):
    """Übergabe-JSON nicht lesbar oder nicht im Format – beim nächsten Durchlauf erneut versuchen."""


@dataclass(frozen=True)
class FilmFolder:
    """Ein Filmordner, wie die Liste ihn sieht."""
    folder: str                      # absoluter Pfad
    name: str                        # Ordnername
    state: str                       # READY | INCOMPLETE | TRANSFER
    beitrag_id: str = ""             # aus der JSON (leer: Ordnername gilt)
    title: str = ""
    show: str = ""
    created: str = ""                # „erstellt“ der JSON (Übergabezeit)
    number: int = 1                  # uebergabe_nr
    origin: dict = field(default_factory=dict)           # herkunft
    info: dict = field(default_factory=dict)             # sendetermin, laenge, cutter, videoid, dringend
    aaf: str = ""                    # Pfade der Übergabe (leer: fehlt)
    video: str = ""
    problems: tuple[str, ...] = ()   # was fehlt oder nicht passt (Zustand INCOMPLETE)

    @property
    def display_title(self) -> str:
        return self.title or self.name

    @property
    def edit_room(self) -> str:
        return str(self.origin.get("schnittraum") or "")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse(data: Any) -> dict:
    """Übergabe-JSON prüfen und auf die Felder bringen, die Ottilie braucht. Fehler → `HandoverError`."""
    if not isinstance(data, Mapping):
        raise HandoverError("kein JSON-Objekt")
    if data.get("format") not in (None, FORMAT):
        raise HandoverError(f"unbekanntes Format „{data.get('format')}“ (erwartet {FORMAT})")
    beitrag = data.get("beitrag") or {}
    origin = data.get("herkunft") or {}
    files = data.get("dateien")
    if not isinstance(beitrag, Mapping) or not isinstance(origin, Mapping):
        raise HandoverError("„beitrag“ bzw. „herkunft“ ist kein Objekt")
    if not isinstance(files, list) or not files:
        raise HandoverError("„dateien“ fehlt oder ist leer")
    if len(files) > MAX_FILES:
        raise HandoverError(f"mehr als {MAX_FILES} Einträge in „dateien“")
    out_files = []
    for f in files:
        if not isinstance(f, Mapping) or not _text(f.get("name")):
            raise HandoverError("Eintrag in „dateien“ ohne Namen")
        name = _text(f["name"])
        if os.path.basename(name) != name or name in (".", "..") or any(ord(c) < 32 for c in name):
            raise HandoverError(f"Dateiname „{name!r}“ mit Ordnerangabe oder Steuerzeichen – nur Dateien im Filmordner")
        if os_metadata(name):                        # „._Film.aaf“ ist eine Begleitdatei von macOS, kein Medium
            continue
        size = f.get("groesse")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
            raise HandoverError(f"Größe von „{name}“ ist keine Bytezahl")
        out_files.append({"typ": _text(f.get("typ")).lower(), "name": name, "groesse": size})
    number = data.get("uebergabe_nr", 1)
    if number is None:
        number = 1
    if isinstance(number, bool) or not isinstance(number, int) or not 0 < number < 10_000:
        raise HandoverError("„uebergabe_nr“ ist keine Ganzzahl von 1 bis 9999")
    info = {k: beitrag.get(k) for k in ("sendetermin", "laenge", "cutter", "videoid", "dringend")
            if beitrag.get(k) not in (None, "")}
    return {"id": _text(beitrag.get("id")), "titel": _text(beitrag.get("titel")),
            "sendung": _text(beitrag.get("sendung")), "erstellt": _text(data.get("erstellt")),
            "nr": number, "herkunft": {k: _text(v) for k, v in origin.items() if _text(v)},
            "info": info, "dateien": out_files}


def _kind(entry: Mapping) -> str:
    typ, name = entry["typ"], entry["name"].lower()
    if typ in ("aaf", "video"):
        return typ
    return "aaf" if name.endswith(AAF_EXT) else ("video" if name.endswith(VIDEO_EXT) else "")


class NotRegular(OSError):
    """Keine normale Datei (Symlink, FIFO, Gerät) oder zu groß – fremde Daten, nicht lesen."""


def read_regular(path: str, limit: int = MAX_JSON_BYTES) -> bytes | None:
    """Inhalt einer echten Datei im Filmordner; None, wenn sie fehlt. Symlink, FIFO, Gerät oder größer als `limit` →
    `NotRegular` – ohne zu folgen und ohne zu blockieren (Sicherheit C: ein FIFO hielt die Liste an, ein Symlink las
    eine Datei von außerhalb)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise NotRegular(errno.ELOOP, "Verweis (Symlink)") from None
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise NotRegular(errno.EINVAL, "keine normale Datei")
        if st.st_size > limit:
            raise NotRegular(errno.EFBIG, "zu groß")
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise NotRegular(errno.EFBIG, "zu groß")
        return data
    finally:
        os.close(fd)


def regular_size(path: str) -> int | None:
    """Größe einer echten Datei; None, wenn sie fehlt, ein Symlink oder keine normale Datei ist (Sicherheit 6: ein
    Symlink führte aus dem Filmordner hinaus) – nur `lstat`, kein Inhalt."""
    try:
        st = os.lstat(path)
    except (OSError, ValueError):
        return None
    return st.st_size if stat.S_ISREG(st.st_mode) else None


def check(folder: str, parsed: Mapping, size_of: Callable[[str], int | None]) -> FilmFolder:
    """Genannte Dateien vorhanden und gleich groß (nur `stat`)? `size_of(pfad)` → Bytes oder None (fehlt)."""
    problems, paths = [], {"aaf": "", "video": ""}
    for f in parsed["dateien"]:
        path = os.path.join(folder, f["name"])
        size = size_of(path)
        if size is None:
            problems.append(f"{f['name']} fehlt")
            continue
        if f["groesse"] is not None and size != f["groesse"]:
            problems.append(f"{f['name']} noch nicht vollständig ({_gb(size)} von {_gb(f['groesse'])})")
            continue
        kind = _kind(f)
        if kind and not paths[kind]:
            paths[kind] = path
    if not paths["aaf"] and not problems:
        problems.append("keine AAF in der Übergabe")
    return FilmFolder(folder=folder, name=os.path.basename(folder.rstrip(os.sep)),
                      state=INCOMPLETE if problems else READY, beitrag_id=parsed["id"], title=parsed["titel"],
                      show=parsed["sendung"], created=parsed["erstellt"], number=parsed["nr"],
                      origin=dict(parsed["herkunft"]), info=dict(parsed["info"]), aaf=paths["aaf"],
                      video=paths["video"], problems=tuple(problems))


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB".replace(".", ",") if n >= 1e8 else f"{n / 1e6:.1f} MB".replace(".", ",")


def unreadable(folder: str, why: str) -> FilmFolder:
    """Filmordner, der sich nicht auswerten lässt (fremde Daten kaputt): unvollständig, nicht holbar – die übrigen
    Filme bleiben in der Liste (Sicherheit 2: vorher fiel die ganze Liste aus)."""
    return FilmFolder(folder=folder, name=os.path.basename(folder.rstrip(os.sep)), state=INCOMPLETE,
                      problems=(why,))


def in_transfer(folder: str) -> FilmFolder:
    """Ordner ohne (lesbare) Übergabe-JSON: Helmut überträgt noch – nicht importierbar."""
    return FilmFolder(folder=folder, name=os.path.basename(folder.rstrip(os.sep)), state=TRANSFER)


def ignored(name: str, prefixes: tuple[str, ...], export_folder: str) -> bool:
    """Ordner, die nicht gelistet werden: Präfixe („_“, „.“), der Exportordner selbst und – auch ohne Präfix „.“ in
    den Einstellungen – Begleitdateien von macOS („._*“, „.DS_Store“)."""
    return name == export_folder or os_metadata(name) or any(p and name.startswith(p) for p in prefixes)
