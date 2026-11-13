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
- `os_metadata`, `drop_appledouble`, `sweep_appledouble`: Begleitdateien von macOS („._*“, „.DS_Store“) erkennen und
  die eigenen wieder entfernen (SMB-Test 11.11.2026).
"""
from __future__ import annotations

import errno
import fcntl
import grp
import hashlib
import json
import logging
import os
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("tonmischung.fileio")


class CorruptFileError(ValueError):
    """Datei da, aber kein lesbares JSON (oder nicht die erwartete Form)."""

    def __init__(self, path: str | Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path, self.reason = str(path), reason


SHARED_MAX_BYTES = 32 * 1024 * 1024


def read_json(path: str | Path, expect: type | None = None, shared: bool = False) -> Any:
    """Inhalt der Datei; `None`, wenn es sie nicht gibt. `expect` (z. B. dict): andere Form gilt als beschädigt.
    `shared`: Datei im gemeinsamen Datenordner, die jeder Benutzer des Macs ersetzen kann (Sicherheit 13.11.2026) –
    nur eine gewöhnliche Datei, kein Verweis (sonst läse Ottilie eine fremde Datei des Benutzers und schriebe sie in
    den gemeinsamen Ordner zurück), keine Pipe (hinge), höchstens `SHARED_MAX_BYTES`."""
    try:
        if shared:
            data = json.loads(_read_plain(path))
        else:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
    except FileNotFoundError:
        return None
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CorruptFileError(path, f"kein lesbares JSON ({e})") from e
    if expect is not None and not isinstance(data, expect):
        raise CorruptFileError(path, f"unerwartete Form ({type(data).__name__} statt {expect.__name__})")
    return data


def _read_plain(path: str | Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise CorruptFileError(path, "ist ein Verweis, keine Datei") from None
        raise
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise CorruptFileError(path, "keine gewöhnliche Datei")
        if st.st_size > SHARED_MAX_BYTES:
            raise CorruptFileError(path, f"zu groß ({st.st_size} Bytes)")
        return f.read(SHARED_MAX_BYTES + 1).decode("utf-8")


def write_json(path: str | Path, data: Any, indent: int = 1, sort_keys: bool = False, mode: int | None = None) -> None:
    """Atomar schreiben (Hilfsdatei, dann umbenennen). `mode`: Rechte der Datei – z. B. 0o664 im gemeinsamen
    Datenordner, damit die anderen Benutzer des Macs sie lesen und ersetzen können (sonst 0600 wie mkstemp)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        if mode is not None:
            os.fchmod(fd, mode)
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
    drop_appledouble(path)


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
        drop_appledouble(path)
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


# ── Begleitdateien von macOS ──────────────────────────────────────────────────
# Auf Freigaben ohne erweiterte Attribute (SMB ohne „streams_xattr“) legt macOS zu jeder Datei mit Attribut eine
# AppleDouble-Datei „._<Name>“ an – und das Attribut „com.apple.provenance“ hängt der Kern an jede Datei, die ein aus
# einer App gestarteter Prozess anlegt; abschalten lässt es sich nicht, auch nicht beim Kopieren (SMB-Test 11.11.2026).
# Löscht man die „._“-Datei, ist das Attribut auf der Freigabe weg. Wird eine Datei ohne Begleitdatei umbenannt, legt
# smbfs am alten Platz eine verwaiste „._“-Datei an – die räumt `sweep_appledouble` mit weg.
APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"
APPLEDOUBLE_MAX = 1 << 20


def os_metadata(name: str) -> bool:
    """Begleitdatei von macOS („._*“, „.DS_Store“) – nie Inhalt, überall übergehen."""
    return name.startswith("._") or name == ".DS_Store"


def _appledouble(name: str, dir_fd: int | None = None) -> bool:
    """Wirklich eine AppleDouble-Datei (normale Datei, klein, Kennung am Anfang)? Folgt keinem Verweis."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError:
        return False
    try:
        st = os.fstat(fd)
        return stat.S_ISREG(st.st_mode) and st.st_size <= APPLEDOUBLE_MAX and os.read(fd, 4) == APPLEDOUBLE_MAGIC
    except OSError:
        return False
    finally:
        os.close(fd)


def _drop(name: str, dir_fd: int) -> bool:
    if not _appledouble(name, dir_fd):
        return False
    try:
        os.unlink(name, dir_fd=dir_fd)
        return True
    except OSError as e:
        log.debug("Begleitdatei %s nicht entfernt: %s", name, e)
        return False


def _missing(name: str, dir_fd: int) -> bool:
    """Gibt es `name` nicht? Das Dateisystem entscheidet – mit seinen Regeln zu Groß/Klein und Unicode-Form (ein
    Zeichenkettenvergleich hielt „._film.wav“ neben „Film.wav“ für verwaist; Sicherheit 11.11.2026)."""
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        return False
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _open_dir(folder: str) -> int | None:
    """Ordner öffnen, ohne einem Verweis zu folgen – geprüft und gelöscht wird dann relativ dazu, ein später
    untergeschobener Verweis lenkt nichts mehr um (Sicherheit 11.11.2026)."""
    try:
        return os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None


def drop_appledouble(path: str | Path) -> bool:
    """Die AppleDouble-Begleitdatei „._<Name>“ einer eigenen, eben geschriebenen Datei (oder eines Ordners) entfernen.
    Nur sie, und nur wenn sie wirklich eine ist; Fehler werden geschluckt (es geht um Ordnung, nicht um Inhalt)."""
    p = os.path.normpath(str(path))
    fd = _open_dir(os.path.dirname(p) or ".")
    if fd is None:
        return False
    try:
        return _drop("._" + os.path.basename(p), fd)
    finally:
        os.close(fd)


def sweep_appledouble(folder: str | Path, own: set[str] | None = None, orphans: bool = True) -> int:
    """AppleDouble-Dateien in einem Ordner entfernen – nur eigene (Regel 03.10.2026: nie eine fremde, vorher
    vorhandene): „._X“, wenn X zu `own` gehört (eben geschrieben, verschoben oder angelegt) oder X fehlt (verwaist –
    smbfs lässt sie beim Umbenennen zurück; nur mit `orphans`, also in Ordnern der App). `own=None`: alle – nur für
    Ordner, die in diesem Lauf entstanden sind. Ist der Ordner ein Verweis: nichts. Rückgabe: wie viele."""
    fd = _open_dir(str(folder))
    if fd is None:
        return 0
    try:
        try:
            names = os.listdir(fd)
        except OSError:
            return 0
        mine = [n for n in names if n.startswith("._")
                and (own is None or n[2:] in own or (orphans and _missing(n[2:], fd)))]
        return sum(_drop(n, fd) for n in mine)
    finally:
        os.close(fd)


# ── Gemeinsamer Datenordner aller Benutzer eines Macs (Auftrag „rechnerweit“, 13.11.2026) ─────────────────────
SHARED_FILE_MODE, SHARED_DIR_MODE = 0o664, 0o2775


def ensure_shared_dir(path: str | Path, group: str = "staff") -> Path:
    """Ordner für alle lokalen Benutzer: Gruppe `staff`, 2775 (neue Dateien erben die Gruppe), nicht weltweit
    beschreibbar. Gruppe und Rechte setzt nur, wer ihn hier neu anlegt – einen vorhandenen Ordner ändert Ottilie nie
    (Sicherheit 13.11.2026: sonst machte ein Verweis oder ein Pfad wie „~“ den Benutzerordner für alle beschreibbar).
    Kein Verweis, kein für alle beschreibbarer Ordner, für den Aufrufer beschreibbar – sonst OSError."""
    p = Path(path)
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(p, 0o700)                        # erst eng, dann Gruppe und Rechte – kein Fenster dazwischen
        except FileExistsError:
            pass                                      # gleichzeitig von einem anderen Benutzer angelegt
        else:
            try:
                os.chown(p, -1, grp.getgrnam(group).gr_gid)
            except (KeyError, OSError):
                pass                                  # z. B. Gruppe fehlt: dann gilt die Gruppe des Elternordners
            os.chmod(p, SHARED_DIR_MODE)
        st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise PermissionError(f"{p} ist kein Ordner (oder ein Verweis) – nicht benutzt")
    if st.st_mode & stat.S_IWOTH and not st.st_mode & stat.S_ISVTX:
        raise PermissionError(f"{p} ist für alle beschreibbar – nicht benutzt")
    if not os.access(p, os.W_OK | os.X_OK):
        raise PermissionError(f"{p} ist für diesen Benutzer nicht beschreibbar")
    return p


def _open_lock(p: Path) -> int:
    """Sperrdatei öffnen, ohne einem Verweis zu folgen (Sicherheit 13.11.2026: ein anderer Benutzer des Macs konnte sie
    durch einen Verweis auf eine Datei des Opfers ersetzen, die dann 0664 bekam). Rechte setzt nur, wer sie anlegt."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    for _ in range(3):
        try:
            fd, created = os.open(p, flags | os.O_CREAT | os.O_EXCL, SHARED_FILE_MODE), True
        except FileExistsError:
            try:
                fd, created = os.open(p, flags), False
            except FileNotFoundError:
                continue                              # eben entfernt – noch einmal anlegen
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise PermissionError(errno.EPERM, f"Sperrdatei {p} ist keine gewöhnliche Datei – nicht benutzt")
            if created and stat.S_IMODE(st.st_mode) != SHARED_FILE_MODE:
                os.fchmod(fd, SHARED_FILE_MODE)       # gegen die umask: alle Benutzer müssen sie öffnen können
        except BaseException:
            os.close(fd)
            raise
        return fd
    raise PermissionError(errno.EAGAIN, f"Sperrdatei {p} lässt sich nicht anlegen")


@contextmanager
def file_lock(path: str | Path, exclusive: bool = True, wait: bool = True) -> Iterator[bool]:
    """Dateisperre (flock) über eine Sperrdatei, die alle Benutzer öffnen können (0664, nur lesend geöffnet – flock
    braucht kein Schreibrecht). Liefert False, wenn `wait=False` und die Sperre gerade jemand anders hält."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = _open_lock(p)
    try:
        flags = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | (0 if wait else fcntl.LOCK_NB)
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
