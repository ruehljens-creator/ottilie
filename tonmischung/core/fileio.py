"""Eigene JSON-Dateien der App sicher lesen und schreiben (Review 09.10.2026, A4/A5/B11/C).

Vorher gab es sechs Varianten: Hilfsdateien mit PID (nicht thread-sicher), fest „.part“, mkstemp; kein fsync; und
beim Lesen galt „kaputt“ wie „gibt es nicht“ – die nächste Änderung überschrieb dann die beschädigte Datei samt allen
Werten. Hier gilt:

- `read_json`: fehlt die Datei → `None`; ist sie kaputt → `CorruptFileError`; andere Lesefehler (Rechte, Netz) gehen
  als `OSError` nach oben – nie als „leer“.
- `write_json`: Hilfsdatei mit eindeutigem Namen im selben Ordner, `fsync`, dann umbenennen; die Hilfsdatei wird bei
  Fehlern wieder entfernt (nur die eigene).
- `set_aside`: beschädigte Datei unter neuem Namen daneben legen (nie löschen, nie überschreiben).
- `write_new` / `publish_new`: eine *neue* Datei anlegen, nie eine vorhandene überschreiben (vorher vier Varianten,
  ohne Ausweg auf Dateisystemen ohne Hardlinks – ein Export auf einen exFAT-Stick scheiterte).
- `sha256_file`, `same_content`, `inside`: Prüfsumme, gleicher Inhalt, „liegt im Ordner“ (vorher mehrfach).
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any


class CorruptFileError(ValueError):
    """Datei da, aber kein lesbares JSON (oder nicht die erwartete Form)."""

    def __init__(self, path: str | Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path, self.reason = str(path), reason


def read_json(path: str | Path, expect: type | None = None) -> Any:
    """Inhalt der Datei; `None`, wenn es sie nicht gibt. `expect` (z. B. dict): andere Form gilt als beschädigt."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CorruptFileError(path, f"kein lesbares JSON ({e})") from e
    if expect is not None and not isinstance(data, expect):
        raise CorruptFileError(path, f"unerwartete Form ({type(data).__name__} statt {expect.__name__})")
    return data


def write_json(path: str | Path, data: Any, indent: int = 1, sort_keys: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent, sort_keys=sort_keys)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)                       # nur die eigene Hilfsdatei
        except FileNotFoundError:
            pass
        raise


NO_HARDLINKS = (errno.ENOTSUP, errno.EOPNOTSUPP)


def own_temp_name(path: str | Path) -> str:
    """Hilfsname neben `path`, eindeutig je Prozess und Thread (versteckt, Endung „.part“)."""
    p = Path(path)
    return str(p.with_name(f".{p.name}.{os.getpid()}-{threading.get_ident()}.part"))


def publish_new(tmp: str, dst: str) -> None:
    """Fertige Hilfsdatei `tmp` unter `dst` sichtbar machen – nie überschreiben (`FileExistsError`, wenn es `dst` gibt).
    Per Hardlink (scheitert atomar, wenn `dst` inzwischen entstand); FAT32/exFAT und manche Freigaben kennen keine
    Hardlinks – dort unmittelbar vorher prüfen und umbenennen. `tmp` ist danach weg."""
    try:
        os.link(tmp, dst)
    except FileExistsError:
        raise
    except OSError as e:
        if e.errno not in NO_HARDLINKS:
            raise
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, "gibt es schon – nichts überschrieben", dst) from None
        os.rename(tmp, dst)
        return
    os.unlink(tmp)


def write_text(path: str | Path, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def write_new(path: str | Path, write: Callable[[str], None]) -> None:
    """Neue Datei anlegen: `write(hilfspfad)` füllt eine eigene Hilfsdatei, dann `publish_new`. Gibt es `path` schon →
    `FileExistsError`. Die Hilfsdatei wird in jedem Fall entfernt (nur die eigene)."""
    tmp = own_temp_name(path)
    try:
        write(tmp)
        publish_new(tmp, str(path))
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def sha256_file(path: str | Path, on_progress: Callable[[float], None] = lambda f: None, chunk: int = 8 << 20) -> str:
    """SHA-256 einer Datei in Blöcken; `on_progress` bekommt den gelesenen Anteil (0–1)."""
    total = os.path.getsize(path) or 1
    h, done = hashlib.sha256(), 0
    with open(path, "rb") as f:
        while data := f.read(chunk):
            h.update(data)
            done += len(data)
            on_progress(min(1.0, done / total))
    return h.hexdigest()


def same_content(a: str | Path, b: str | Path) -> bool:
    """Gleicher Inhalt (Größe und SHA-256)? Nur die Größe genügt nicht (Review B12)."""
    return os.path.getsize(a) == os.path.getsize(b) and sha256_file(a) == sha256_file(b)


def inside(path: str | Path, folder: str | Path) -> bool:
    """Liegt `path` in `folder` (oder ist es)?"""
    p, f = os.path.normpath(path), os.path.normpath(folder)
    return p == f or p.startswith(f + os.sep)


def set_aside(path: str | Path, now: datetime | None = None) -> Path:
    """Beschädigte Datei als `<Name>.defekt-<Zeit><Endung>` daneben legen; Rückgabe: neuer Pfad."""
    path = Path(path)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}.defekt-{stamp}{path.suffix}")
    n = 1
    while target.exists():
        n += 1
        target = path.with_name(f"{path.stem}.defekt-{stamp}-{n}{path.suffix}")
    os.rename(path, target)
    return target
