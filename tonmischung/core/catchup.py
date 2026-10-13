"""Nachtrag-Warteschlange: Helmut-Schreibvorgänge, die bei einem Ausfall liegen blieben (Plan M2-Nachtrag).

Funktionsbeschreibung S. 41: „V1 trägt nach, was während eines Helmut-Ausfalls lokal passiert ist … Hat in
der Zwischenzeit jemand denselben Beitrag in Helmut anders zugewiesen, meldet die App den Konflikt, statt
ihn zu überschreiben.“ Hier stehen Einträge, Entscheidung und die Datei; das Nachtragen gegen Helmut
steht in `helmut/catchup.py`.
"""
from __future__ import annotations

import fcntl
import logging
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path

from tonmischung.core import mixstate
from tonmischung.core.fileio import CorruptFileError, read_json, set_aside, write_json
from tonmischung.core.mixstate import MixState

log = logging.getLogger(__name__)

MIX, LOCK, ASSET, NOTIFY, TAKE = "mischung", "sperre", "asset", "nachricht", "uebernahme"
OPEN, DONE, CONFLICT = "offen", "erledigt", "konflikt"
APPLY = "schreiben"
HINT = "Helmut ist nicht erreichbar – wird nachgetragen, sobald Helmut wieder da ist"


def default_path(mock: bool = False) -> Path:
    name = "nachtrag-mock.json" if mock else "nachtrag.json"
    from tonmischung.paths import support_dir
    return support_dir() / name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Entry:
    kind: str
    project_id: str
    title: str = ""
    created: str = ""
    expect_mixer: str = ""          # MIX: der Beitrag muss noch diesem Mischer …
    expect_room: str = ""           # … in diesem Raum gehören
    changes: dict = field(default_factory=dict)     # MIX: MixState-Felder → neuer Wert
    locked: bool = False            # LOCK
    path: str = ""                  # ASSET
    asset_kind: str = ""
    user: str = ""                  # NOTIFY
    subject: str = ""
    text: str = ""
    havarie: str = ""               # Havarie-Kennung: Einträge eines manuell geöffneten Pakets
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = OPEN
    attempts: int = 0
    message: str = ""

    def describe(self) -> str:
        state = self.changes.get("state", "")
        what = {TAKE: "übernehmen",
                MIX: f"Zustand „{mixstate.STATE_LABEL.get(state, state)}“" if state else "Set „Mischung“",
                LOCK: "sperren" if self.locked else "entsperren",
                ASSET: f"Asset {os.path.basename(self.path)}", NOTIFY: f"Nachricht an {self.user}"}[self.kind]
        return f"{self.title or self.project_id}: {what}"


def mix_changes(before: MixState, after: MixState) -> dict:
    """Nur die Felder, die sich ändern – so bleibt fremdes Unverändertes beim Nachtragen unberührt."""
    return {f.name: getattr(after, f.name) for f in fields(MixState)
            if getattr(before, f.name) != getattr(after, f.name)}


def decide_mix(entry: Entry, current: MixState) -> tuple[str, str]:
    """(APPLY | DONE | CONFLICT, Meldung) für einen Eintrag „Set Mischung“ gegen den aktuellen Stand."""
    if all(getattr(current, k) == v for k, v in entry.changes.items()):
        return DONE, "stand schon so in Helmut"
    owner = mixstate.owned_by(current, entry.expect_mixer, entry.expect_room)
    if entry.changes.get("state", current.state) == mixstate.DELIVERED:     # erneut zurückgeliefert
        owner = owner or mixstate.delivered_by(current, entry.expect_mixer, entry.expect_room)
    if not owner:
        return CONFLICT, (f"Beitrag gehört inzwischen {current.mixer or 'niemandem'} ({current.room or '–'}), "
                          f"Zustand „{mixstate.STATE_LABEL.get(current.state, current.state)}“ – nicht überschrieben")
    return APPLY, ""


def apply_changes(current: MixState, entry: Entry) -> MixState:
    return replace(current, **entry.changes)


class CatchUpQueue:
    """Einträge in einer JSON-Datei; jede Änderung wird sofort atomar gespeichert.

    App (mehrere Threads) und Kommandozeile arbeiten mit derselben Datei (Review A8). Deshalb liest jede Änderung die
    Datei unter einer Dateisperre neu, ändert und speichert; beim Lesen werden Änderungen anderer Prozesse nachgeholt.
    Nachtragen läuft nur einmal zur Zeit (`replaying`)."""

    def __init__(self, path: Path | None = None, now: Callable[[], str] = _now) -> None:
        self.path = Path(path) if path else default_path()
        self._now = now
        self.problem = ""                         # Hinweis fürs Fenster (beschädigte Datei beiseitegelegt)
        self._lock = threading.RLock()
        self._depth = 0                           # verschachtelte Änderungen (dismiss → mark) sperren nur einmal
        self._stamp: tuple[int, int, int] | None = None
        self._entries: list[Entry] = []
        self._reload(strict=True)

    # ── Datei ─────────────────────────────────────────────────────────────
    def _file_stamp(self) -> tuple[int, int, int] | None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return st.st_ino, st.st_mtime_ns, st.st_size

    def _reload(self, strict: bool) -> None:
        """Stand der Datei holen, wenn sie sich geändert hat. Beschädigt: beiseitelegen und mit dem bekannten Stand
        weiterarbeiten (Review B11: vorher startete die App gar nicht). Nicht lesbar: Fehler, nichts überschreiben.
        `strict=False` (nur lesen): bei Problemen den bekannten Stand behalten, die nächste Änderung klärt es."""
        stamp = self._file_stamp()
        if stamp == self._stamp:                  # unverändert (oder weiterhin keine Datei)
            return
        try:
            raw = read_json(self.path, expect=dict)
        except CorruptFileError as e:
            if not strict:
                return
            moved = set_aside(self.path)
            self.problem = (f"Nachtrag-Datei war beschädigt ({e.reason}) – beiseitegelegt als „{moved.name}“; "
                            "darin offene Änderungen bitte von Hand prüfen")
            log.error("%s", self.problem)
            self._stamp = None
            return
        except OSError as e:
            if not strict:
                return
            raise RuntimeError(f"Nachtrag-Datei {self.path} nicht lesbar: {e} – nichts überschrieben") from e
        known = {f.name for f in fields(Entry)}
        self._entries = [Entry(**{k: v for k, v in e.items() if k in known})
                         for e in (raw or {}).get("eintraege", []) if isinstance(e, dict)]
        self._stamp = stamp

    def _save(self) -> None:
        write_json(self.path, {"version": 1, "eintraege": [asdict(e) for e in self._entries]})
        self._stamp = self._file_stamp()

    def _lock_path(self, kind: str) -> Path:
        return self.path.with_name(f"{self.path.stem}.{kind}.lock")

    @contextmanager
    def _changing(self) -> Iterator[None]:
        """Ändern: Thread- und Dateisperre, vorher den Stand der Datei holen, danach speichern (nicht bei Fehlern)."""
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._lock_path("aendern"), "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._depth = 1
                try:
                    self._reload(strict=True)
                    yield
                    self._save()
                finally:
                    self._depth = 0
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _view(self) -> list[Entry]:
        with self._lock:
            if not self._depth:
                self._reload(strict=False)
            return list(self._entries)

    @contextmanager
    def replaying(self) -> Iterator[bool]:
        """Nur ein Nachtragen zur Zeit – auch zwischen App und Kommandozeile. `False`: läuft schon woanders."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path("nachtragen"), "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    # ── Einträge ─────────────────────────────────────────────────────────
    def add(self, entry: Entry) -> Entry:
        entry.created = entry.created or self._now()
        with self._changing():
            self._entries.append(entry)
        return entry

    def add_mix(self, project_id: str, title: str, before: MixState, after: MixState, mixer: str, room: str,
                havarie: str = "") -> Entry:
        return self.add(Entry(MIX, project_id, title, expect_mixer=mixer, expect_room=room,
                              changes=mix_changes(before, after), havarie=havarie))

    def add_take(self, project_id: str, title: str, mixer: str, room: str, havarie: str = "") -> Entry:
        """Übernehmen nachtragen (Havarie): nur, wenn der Beitrag dann noch frei ist – sonst Konflikt."""
        return self.add(Entry(TAKE, project_id, title, expect_mixer=mixer, expect_room=room, havarie=havarie))

    def add_lock(self, project_id: str, title: str, locked: bool, havarie: str = "") -> Entry:
        return self.add(Entry(LOCK, project_id, title, locked=locked, havarie=havarie))

    def add_asset(self, project_id: str, title: str, path: str, kind: str) -> Entry:
        return self.add(Entry(ASSET, project_id, title, path=path, asset_kind=kind))

    def add_notify(self, project_id: str, title: str, user: str, subject: str, text: str) -> Entry:
        return self.add(Entry(NOTIFY, project_id, title, user=user, subject=subject, text=text))

    def entries(self) -> list[Entry]:
        return self._view()

    def pending(self) -> list[Entry]:
        """Offen und einem Beitrag zugeordnet (ohne Projekt-ID wartet ein Havarie-Eintrag auf Zuordnung)."""
        return [e for e in self._view() if e.status == OPEN and e.project_id]

    def waiting(self) -> list[Entry]:
        return [e for e in self._view() if e.status == OPEN and not e.project_id]

    def conflicts(self) -> list[Entry]:
        return [e for e in self._view() if e.status == CONFLICT]

    def assign(self, havarie: str, project_id: str) -> int:
        """Havarie-Einträge einem Helmut-Beitrag zuordnen; Rückgabe: Anzahl."""
        with self._changing():
            hits = [e for e in self._entries if e.havarie == havarie and e.status == OPEN and not e.project_id]
            for e in hits:
                e.project_id = project_id
        return len(hits)

    def mark(self, entry: Entry, status: str, message: str = "", attempts: int | None = None) -> None:
        """Stand eines Eintrags setzen – am Eintrag in der Datei (per Kennung) und am übergebenen Objekt."""
        with self._changing():
            targets = [entry] + [e for e in self._entries if e.id == entry.id and e is not entry]
            for e in targets:
                e.status, e.message = status, message
                if attempts is not None:
                    e.attempts = attempts

    def dismiss(self, entry_id: str) -> Entry:
        """Eintrag bewusst nicht nachtragen – Konflikt oder noch offene Änderung (Jens 09.10.2026: auch offene
        verwerfen können). Helmut bleibt, wie es ist."""
        with self._changing():
            e = next((x for x in self._entries if x.id == entry_id), None)
            if e is None or e.status not in (OPEN, CONFLICT):
                raise KeyError(f"Kein offener Eintrag mit der Kennung {entry_id}")
            self.mark(e, DONE, f"verworfen: {e.message or 'nicht nachgetragen'}")
        return e

    def dismiss_many(self, havarie: str | None = None) -> int:
        """Alle offenen Einträge und Konflikte verwerfen (oder nur die einer Havarie-Session); Rückgabe: Anzahl."""
        with self._changing():
            hits = [e for e in self._entries if e.status in (OPEN, CONFLICT)
                    and (havarie is None or e.havarie == havarie)]
            for e in hits:
                self.mark(e, DONE, f"verworfen: {e.message or 'nicht nachgetragen'}")
        return len(hits)

    def forget_done(self, keep: int = 50) -> None:
        """Erledigte Einträge kürzen (die letzten `keep` bleiben zur Nachvollziehbarkeit)."""
        with self._changing():
            done = [e for e in self._entries if e.status == DONE]
            if len(done) > keep:
                drop = {id(e) for e in done[:-keep]}
                self._entries = [e for e in self._entries if id(e) not in drop]

    def summary(self) -> dict:
        entries = self._view()
        groups: dict[str, str] = {}
        for e in entries:
            if e.status == OPEN and not e.project_id:
                groups.setdefault(e.havarie, e.title)
        return {"offen": sum(e.status == OPEN and bool(e.project_id) for e in entries),
                "eintraege": [{"id": e.id, "text": e.describe()} for e in entries
                              if e.status == OPEN and e.project_id],
                "konflikte": [{"id": e.id, "text": e.describe(), "grund": e.message}
                              for e in entries if e.status == CONFLICT],
                "ohne_beitrag": [{"id": h, "titel": t} for h, t in groups.items()]}
