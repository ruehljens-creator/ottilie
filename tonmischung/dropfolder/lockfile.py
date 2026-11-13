"""Sperrdatei im Filmordner – Ersatz für LOCKED und das Set „Mischung“ in der Betriebsart Übergabeordner.

Format „ottilie-sperre/1“:

    {"format": "ottilie-sperre/1", "gesperrt": true, "raum": "<Raum>", "mischer": "<Mischer>", "zeit": "<ISO UTC>",
     "zustand": "uebernommen", "uebergabe_nr": 1, "mischung": {"zustand": …, "mischer": …, … wie im Set „Mischung“}}

Sperren geschieht unter einer exklusiv angelegten Hilfsdatei (`O_EXCL`), nie „prüfen, dann schreiben“ ohne Schutz: Von
zwei Räumen gewinnt genau einer – auch wenn schon eine freie Sperrdatei (ausgeliefert, zurückgegeben) daliegt.
Freigeben schreibt „gesperrt: false“ hinein (Jens 11.11.2026: umschreiben, nicht löschen – so bleibt sichtbar, wer den
Film zuletzt hatte).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, fields
from typing import Any

from tonmischung.core.fileio import CorruptFileError, drop_appledouble, write_json
from tonmischung.core.mixstate import MixState
from tonmischung.helmut.mapping import MIX_KEYS, parse_progress

FORMAT = "ottilie-sperre/1"
_BY_NAME = {v: k for k, v in MIX_KEYS.items()}


class LockTaken(Exception):
    """Der Film ist schon belegt (Sperrdatei mit „gesperrt“) – nichts geschrieben."""


def read(path: str) -> dict | None:
    """Inhalt der Sperrdatei; None, wenn es keine gibt. Unlesbar (halb geschrieben, beschädigt, Symlink, FIFO, zu
    groß) → `CorruptFileError` – gelesen ohne Verweisen zu folgen und ohne zu blockieren (Sicherheit C)."""
    from tonmischung.dropfolder.handover import NotRegular, read_regular
    try:
        raw = read_regular(path)
    except NotRegular as e:
        raise CorruptFileError(path, e.strerror or "keine normale Datei") from None
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as e:
        raise CorruptFileError(path, f"kein lesbares JSON ({type(e).__name__})") from None
    if not isinstance(data, dict):
        raise CorruptFileError(path, "kein JSON-Objekt")
    if data.get("format") not in (None, FORMAT):
        raise CorruptFileError(path, f"unbekanntes Format „{data.get('format')}“")
    return data


def mix_of(data: dict | None) -> MixState:
    """Stand der Mischung aus der Sperrdatei (wie aus dem Set „Mischung“)."""
    values = (data or {}).get("mischung") or {}
    kwargs: dict[str, Any] = {}
    names = {f.name for f in fields(MixState)}
    for key, value in values.items() if isinstance(values, dict) else ():
        name = _BY_NAME.get(key)
        if name in names and value not in (None, ""):
            kwargs[name] = parse_progress(value) if name == "progress" else str(value)
    return MixState(**kwargs)


def locked(data: dict | None) -> bool:
    return bool(data and data.get("gesperrt"))


def content(mix: MixState, locked_now: bool, room: str, mixer: str, when: str, number: int) -> dict:
    """Inhalt der Sperrdatei: oben das Wichtigste zum Lesen, unter „mischung“ alles wie im Set „Mischung“."""
    values = {MIX_KEYS[k]: ("" if v is None else str(v)) for k, v in asdict(mix).items()}
    return {"format": FORMAT, "gesperrt": locked_now, "raum": room, "mischer": mixer, "zeit": when,
            "zustand": mix.state, "uebergabe_nr": number, "mischung": values}


STALE_CLAIM_S = 60.0        # Hilfsdatei eines abgestürzten Schreibers: danach darf ein anderer Raum weiter
WAIT_S = 3.0                # so lange auf die Hilfsdatei eines anderen Raums warten (er schreibt nur kurz)


def _server_now(folder: str) -> float:
    """Uhrzeit des Speichers: eine eigene Probedatei anlegen und ihre Änderungszeit lesen. Mac und NAS gehen oft
    verschieden – das Alter einer fremden Hilfsdatei nach der Mac-Uhr zu messen, hob den Schutz auf (Ausfall F5)."""
    probe = os.path.join(folder, f".ottilie-zeit-{uuid.uuid4().hex[:8]}")
    fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o664)
    try:
        os.close(fd)
        return os.stat(probe).st_mtime
    finally:
        os.unlink(probe)                             # nur die eigene Probedatei


def _token(path: str) -> str | None:
    """Kennung in einer Hilfsdatei – ohne Verweisen zu folgen; Symlink, FIFO o. Ä. → None."""
    from tonmischung.dropfolder.handover import NotRegular, read_regular
    try:
        raw = read_regular(path, limit=256)
    except (NotRegular, OSError):
        return None
    return raw.decode("utf-8", "replace").strip() if raw is not None else None


def _take_mutex(mutex: str, wait_s: float, sleep: Callable[[float], None]) -> str:
    """Hilfsdatei „<Sperrdatei>.claim“ exklusiv anlegen (`O_EXCL`), mit eigener Kennung darin – immer nur ein Raum
    liest und schreibt die Sperrdatei. Hält ein anderer sie, bis `wait_s` warten. Liegt sie nach der Uhr des Speichers
    länger als `STALE_CLAIM_S` (Absturz mitten im Schreiben) – oder weit in der Zukunft –, wird sie beiseite benannt;
    war es inzwischen eine frische eines anderen Raums (andere Kennung), wird sie zurückgelegt (Sicherheit E–G).
    Rückgabe: die eigene Kennung."""
    token = uuid.uuid4().hex
    waited = 0.0
    while True:
        try:
            fd = os.open(mutex, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o664)
        except FileExistsError:
            try:
                seen = _token(mutex)
                age = _server_now(os.path.dirname(mutex)) - os.lstat(mutex).st_mtime
            except FileNotFoundError:
                age = 0.0                             # eben verschwunden: kurz warten, dann neu versuchen
            else:
                if age >= STALE_CLAIM_S or age < -STALE_CLAIM_S:
                    aside = f"{mutex}.{uuid.uuid4().hex[:8]}.alt"
                    try:
                        os.rename(mutex, aside)
                    except FileNotFoundError:
                        continue
                    if _token(aside) != seen:         # inzwischen die frische eines anderen: zurücklegen
                        try:
                            os.link(aside, mutex)
                        except OSError:
                            pass
                    os.unlink(aside)
                    continue
            if waited >= wait_s:
                raise LockTaken("gerade schreibt ein anderer Raum – gleich noch einmal versuchen") from None
            sleep(0.1)
            waited += 0.1
            continue
        with os.fdopen(fd, "w") as f:
            f.write(token)
        return token


def _release_mutex(mutex: str, token: str) -> None:
    """Nur die eigene Hilfsdatei entfernen – eine inzwischen fremde (nach Beiseitelegen) bleibt."""
    if _token(mutex) == token:
        try:
            os.unlink(mutex)
        except OSError:
            pass


@contextmanager
def guarded(path: str, wait_s: float = WAIT_S, sleep: Callable[[float], None] = time.sleep):
    """Sperrdatei unter der Hilfsdatei lesen und schreiben."""
    mutex = path + ".claim"
    token = _take_mutex(mutex, wait_s, sleep)
    try:
        yield
    finally:
        _release_mutex(mutex, token)


def claim(path: str, data: dict | Callable[[dict | None], dict],
          sleep: Callable[[float], None] = time.sleep) -> None:
    """Film belegen. Ist er belegt (oder die Sperrdatei unlesbar) → `LockTaken`, nichts geschrieben.

    Lesen und Schreiben geschehen unter der Hilfsdatei (`guarded`), so nie gleichzeitig in zwei Räumen. (Vorher:
    freie Sperrdatei beiseite benennen und neu anlegen – im Test mit acht Räumen gewannen zwei.)"""
    with guarded(path, sleep=sleep):
        try:
            current = read(path)
        except CorruptFileError:
            raise LockTaken("Sperrdatei unlesbar") from None
        if locked(current):
            raise LockTaken(f"belegt von {current.get('mischer') or 'jemand'} ({current.get('raum') or '?'})")
        # aus dem eben unter der Hilfsdatei gelesenen Stand bilden, nicht aus einem älteren (Review 7)
        _write(path, data(current) if callable(data) else data)


def _write(path: str, data: dict) -> None:
    """Atomar schreiben, dann für alle Konten am Speicher lesbar (0664): `write_json` legt 0600 an – andere Macs mit
    eigenem Konto sähen die Sperrdatei sonst nicht (Review 3)."""
    write_json(path, data)
    try:
        os.chmod(path, 0o664)
    except OSError:
        pass                                         # Speicher ohne POSIX-Rechte
    drop_appledouble(path)                           # erst nach chmod – das legt sie auf SMB neu an


def update(path: str, change: Callable[[dict | None], dict | None],
           sleep: Callable[[float], None] = time.sleep) -> None:
    """Sperrdatei unter der Hilfsdatei lesen, `change(aktuell)` bilden und schreiben (None = nichts schreiben).
    `change` prüft dabei, ob der Film noch dem Schreiber gehört (Ausfall F9: ein verspätetes Schreiben aus Raum A
    setzte sonst den Besitz von Raum B zurück). Unlesbar → `CorruptFileError` an den Aufrufer."""
    with guarded(path, sleep=sleep):
        new = change(read(path))
        if new is not None:
            _write(path, new)
