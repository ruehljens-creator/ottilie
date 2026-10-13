"""Gespeicherten Stand als Kopie öffnen – nach dem Freigeben und für Duplikate mit Mischstand.

Funktionsbeschreibung S. 27–29: Ein anderer Raum öffnet den gespeicherten Stand „als Kopie unter
neuem Namen; die alte Datei bleibt unangetastet“. Zwei Fälle:

- **Freigegeben mit „Letzten gespeicherten Stand öffnen“:** die Session-Datei wird im selben Ordner
  als `…_wiederhergestellt_hhmm.ptx` kopiert; Audiodateien und Sicherungen bleiben, wo sie sind.
- **Duplikat mit Mischstand:** der Session-Ordner des Originals wird samt Audiodateien in einen neuen
  Ordner für das Duplikat kopiert (Name aus dem Titel des Duplikats).

Reihenfolge wie beim Holen: erst prüfen, dann übernehmen, dann Pro Tools frei machen (vor dem
Schließen speichern), dann kopieren und öffnen. Kopiert wird nie über etwas Vorhandenes.
"""
from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from tonmischung.core import mixstate
from tonmischung.core.naming import session_name_for, unique_session_dir
from tonmischung.core.progress import NullProgress, Progress
from tonmischung.core.session_rules import same_session
from tonmischung.helmut.mapping import Contribution
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import PtslError
from tonmischung.sessions.runner import run_import_plan
from tonmischung.workflow.fetch import FetchError, plan_for_open_session

REOPEN_STEPS = [("Prüfen", 5), ("Übernehmen", 3), ("Session wechseln", 10), ("Kopie anlegen", 30),
                ("Session öffnen", 30), ("Spurliste prüfen", 10), ("Helmut aktualisieren", 5)]
FILE = "datei"          # nur die Session-Datei kopieren (gleicher Ordner)
FOLDER = "ordner"       # Session-Ordner samt Audiodateien kopieren (Duplikat)
SPACE_RESERVE = 1.1     # freier Platz: Größe + 10 %


@dataclass(frozen=True)
class CopyPlan:
    kind: str
    source: str                     # vorhandene .ptx (wird nie verändert)
    target: str                     # neue .ptx
    source_dir: str = ""
    target_dir: str = ""


def restore_target(source: str, now: datetime, exists: Callable[[str], bool] = os.path.exists) -> str:
    """`<Ordner>/<Name>_wiederhergestellt_hhmm.ptx`, bei Gleichstand mit _2, _3 …"""
    folder, base = os.path.split(source)
    stem = os.path.splitext(base)[0]
    name = f"{stem}_wiederhergestellt_{now:%H%M}"
    candidate, n = name, 1
    while exists(os.path.join(folder, f"{candidate}.ptx")):
        n += 1
        candidate = f"{name}_{n}"
    return os.path.join(folder, f"{candidate}.ptx")


def plan_copy(c: Contribution, session_parent: str, now: datetime,
              exists: Callable[[str], bool] = os.path.exists, room: str = "") -> CopyPlan | None:
    """Was zu kopieren ist – oder None, wenn der Beitrag neu importiert wird.

    „lokal mit Spiegelung“ und freigegeben aus einem anderen Raum: Die lokale Session liegt auf dem
    anderen Mac – geöffnet wird der gespiegelte Ordner als Kopie im lokalen Ordner dieses Raums.
    """
    m = c.mix
    from_mirror = (m.session_storage == mixstate.MIRROR and m.session_mirror and room
                   and m.returned_from_room and m.returned_from_room != room)
    if m.return_keep == mixstate.KEEP_LAST and m.session_path and from_mirror:
        source = os.path.join(m.session_mirror, os.path.basename(m.session_path))
        parent, name = unique_session_dir(session_parent, session_name_for(c.title, c.id), exists)
        target_dir = os.path.join(parent, name)
        return CopyPlan(FOLDER, source, os.path.join(target_dir, f"{name}.ptx"), m.session_mirror, target_dir)
    if m.return_keep == mixstate.KEEP_LAST and m.session_path:
        return CopyPlan(FILE, m.session_path, restore_target(m.session_path, now, exists))
    if m.duplicate_session and not m.session_path:
        parent, name = unique_session_dir(session_parent, session_name_for(c.title, c.id), exists)
        target_dir = os.path.join(parent, name)
        return CopyPlan(FOLDER, m.duplicate_session, os.path.join(target_dir, f"{name}.ptx"),
                        os.path.dirname(m.duplicate_session), target_dir)
    return None


def folder_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def check_copy(plan: CopyPlan, exists: Callable[[str], bool] = os.path.exists,
               free: Callable[[str], int] = lambda p: shutil.disk_usage(p).free,
               size: Callable[[str], int] = folder_size) -> None:
    """Vor dem Übernehmen: Quelle erreichbar, Ziel frei, genug Platz. Wirft FetchError."""
    if not exists(plan.source):
        raise FetchError(f"Gespeicherte Session nicht erreichbar: {plan.source} – „Neu importieren“ wählen "
                         "oder die Ablage prüfen; nichts verändert")
    if exists(plan.target):
        raise FetchError(f"{plan.target} gibt es schon – nichts überschrieben, nichts verändert")
    if plan.kind == FOLDER:
        parent = os.path.dirname(plan.target_dir)
        while parent and not exists(parent) and os.path.dirname(parent) != parent:
            parent = os.path.dirname(parent)                  # Ablageordner gibt es evtl. noch nicht
        need = int(size(plan.source_dir) * SPACE_RESERVE)
        if free(parent) < need:
            raise FetchError(f"Zu wenig Platz in {parent}: {need / 1e9:.1f} GB nötig, "
                             f"{free(parent) / 1e9:.1f} GB frei – nichts verändert")


def other_sessions(source: str) -> Callable[[str, list[str]], list[str]]:
    """Für copytree: andere Session-Dateien im Hauptordner (z. B. wiederhergestellte Stände) weglassen."""
    top, keep = os.path.dirname(source), os.path.basename(source)

    def ignore(folder: str, names: list[str]) -> list[str]:
        if os.path.normpath(folder) != os.path.normpath(top):
            return []
        return [n for n in names if (n.lower().endswith(".ptx") and n != keep) or n == ".Spiegel-Vorversionen"]
    return ignore


def copy_session(plan: CopyPlan, copy_file: Callable[[str, str], object] = shutil.copy2,
                 copy_tree: Callable[..., object] = shutil.copytree) -> None:
    """Kopie anlegen. Die Quelle wird nur gelesen; das Ziel darf nicht existieren."""
    if os.path.exists(plan.target):
        raise FetchError(f"{plan.target} gibt es schon – nichts überschrieben")
    if plan.kind == FILE:
        copy_file(plan.source, plan.target)
        return
    copy_tree(plan.source_dir, plan.target_dir, ignore=other_sessions(plan.source))   # wirft, wenn es ihn gibt
    copied = os.path.join(plan.target_dir, os.path.basename(plan.source))
    if copied != plan.target:
        os.rename(copied, plan.target)                          # nur die frische Kopie umbenennen


OPEN_WAIT_S = 300.0       # so lange auf die Bestätigung eines Pro-Tools-Dialogs warten


def open_and_wait(ptsl: PtslClient, path: str, progress: Progress | None = None,
                  wait_s: float = OPEN_WAIT_S, dialogs: Callable[[], list[str]] | None = None,
                  sleep: Callable[[float], None] | None = None,
                  clock: Callable[[], float] | None = None) -> list[str]:
    """Session öffnen und prüfen, dass genau sie offen ist. Rückgabe: Hinweise zu beantworteten Dialogen.

    „Missing AAX Plugins“ bestätigt der Client selbst (Jens 13.10.2026). Hängt OpenSession an einem anderen Dialog,
    meldet der Fortschritt ihn und wartet bis `wait_s`, dass jemand ihn in Pro Tools bestätigt.
    """

    from tonmischung.ptsl.dialogs import pro_tools_dialogs
    from tonmischung.ptsl.errors import PtslTimeoutError
    progress = progress or NullProgress()
    dialogs = dialogs or pro_tools_dialogs
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    want = os.path.normpath(path)
    before = len(ptsl.dialog_events)
    try:
        ptsl.open(path)
    except PtslTimeoutError:
        end = clock() + wait_s
        while True:
            names = dialogs()
            if names and ptsl.dialog_handler is not None:          # z. B. erst nach dem Zeitlimit erschienen
                done = ptsl.dialog_handler(names)
                if done:
                    ptsl.dialog_events.append(done)
                    continue
            what = f"„{'“, „'.join(names)}“" if names else "ein Dialog"
            progress.update(0.0, f"Pro Tools zeigt {what} – bitte in Pro Tools bestätigen")
            try:
                info = ptsl.open_session()
            except PtslTimeoutError:
                info = None
            if info is not None and same_session(info.path, want):
                return ptsl.dialog_events[before:]
            if clock() >= end:
                raise FetchError(f"Pro Tools hat „{os.path.basename(path)}“ nicht geöffnet – {what} wartet auf "
                                 "Bestätigung in Pro Tools")
            sleep(2.0)
    info = ptsl.open_session()
    if info is None or not same_session(info.path, want):
        raise FetchError(f"Pro Tools hat „{os.path.basename(path)}“ nicht geöffnet "
                         f"(offen: {info.name if info else 'keine Session'}) – Dialog in Pro Tools?")
    return ptsl.dialog_events[before:]


@dataclass
class ReopenResult:
    contribution: Contribution
    session_path: str
    kind: str
    tracks: int = 0
    warnings: list[str] = field(default_factory=list)


def reopen(project_id: str, *, service: MixService, ptsl: PtslClient, session_parent: str,
           session_storage: str = "lokal", mirror_parent: str = "",
           confirmed: bool | None = None, unattended: bool = False,
           is_delivered: Callable[[str], bool] = lambda path: False,
           now: Callable[[], datetime] = datetime.now, exists: Callable[[str], bool] = os.path.exists,
           check: Callable[[CopyPlan], None] | None = None, copy: Callable[[CopyPlan], None] = copy_session,
           log: Callable[[str], None] = lambda m: None, progress: Progress | None = None) -> ReopenResult:
    progress = progress or NullProgress()
    helmut = service.client

    # 1. Prüfen, ohne etwas zu ändern
    progress.step("Prüfen", "gespeicherten Stand")
    c = helmut.get(project_id)
    mixstate.check_take(c.mix, c.locked, service.mixer, service.room)
    plan = plan_copy(c, session_parent, now(), exists, room=service.room)
    if plan is None:
        raise FetchError("Für diesen Beitrag gibt es keinen gespeicherten Stand zum Öffnen – neu importieren")
    (check or (lambda p: check_copy(p, exists)))(plan)
    open_session, pt_plan = plan_for_open_session(ptsl, is_delivered, confirmed, unattended)

    # 2. In Helmut übernehmen
    progress.step("Übernehmen", c.title)
    log(f"Übernehmen: {c.title} · {service.mixer} ({service.room})")
    c = service.take(project_id)

    # 3. Pro Tools frei machen (vor dem Schließen speichern), kopieren, öffnen
    warnings: list[str] = []
    try:
        if pt_plan.steps:
            progress.step("Session wechseln", f"„{open_session.name}“ speichern und schließen")
        run_import_plan(ptsl, pt_plan)
        what = "Session-Datei" if plan.kind == FILE else "Session-Ordner mit Audiodateien"
        progress.step("Kopie anlegen", f"{what} → {os.path.basename(plan.target)}")
        log(f"Kopie anlegen ({what}): {plan.source} → {plan.target}")
        try:
            copy(plan)
        except OSError as e:
            raise FetchError(f"Kopie nicht angelegt: {e} – Beitrag bleibt übernommen; erneut versuchen "
                             "oder freigeben") from e
        progress.step("Session öffnen", os.path.basename(plan.target))
        warnings += open_and_wait(ptsl, plan.target, progress)
        progress.step("Spurliste prüfen", "Spuren und Video")
        try:
            tracks = ptsl.tracks()
        except PtslError as e:                       # Session ist offen – nur ein Hinweis (Review B22)
            tracks = None
            warnings.append(f"Spurliste nicht geprüft: {e}")
        if tracks is not None and not tracks:
            warnings.append("Die geöffnete Session hat keine Spuren")
        if tracks and not any(t.is_video for t in tracks):
            warnings.append("Keine Videospur in der Session – Referenzvideo prüfen")
    except PtslError as e:
        raise FetchError(f"{e} – Beitrag bleibt übernommen; erneut versuchen oder freigeben") from e

    # 4. Helmut aktualisieren – Rückgabe abgeschlossen, Session-Pfad der Kopie
    progress.step("Helmut aktualisieren", "Zustand „importiert“")
    if plan.kind == FOLDER and plan.source_dir == c.mix.session_mirror:   # aus dem Spiegel: weiter dorthin
        storage, mirror = c.mix.session_storage, c.mix.session_mirror
    elif plan.kind == FOLDER:                   # neuer Ordner: Ablage dieses Raums
        storage = session_storage
        mirror = os.path.join(mirror_parent, os.path.basename(plan.target_dir)) if mirror_parent else ""
    else:                                       # Kopie im selben Ordner: Ablage bleibt
        storage, mirror = c.mix.session_storage or "lokal", c.mix.session_mirror
    c, hint = service.set_state_or_queue(c, mixstate.IMPORTED, session_path=plan.target, session_storage=storage,
                                         session_mirror=mirror)
    if hint:
        warnings.append(hint)
    log(f"Fertig: {plan.target}")
    return ReopenResult(c, plan.target, plan.kind, len(tracks), warnings)


RESTORE_STEPS = [("Prüfen", 5), ("Übernehmen", 3), ("Session wechseln", 10), ("Kopie anlegen", 20),
                 ("Session öffnen", 40), ("Helmut aktualisieren", 5)]


def restore_state(project_id: str, state_path: str, *, service: MixService, ptsl: PtslClient,
                  confirmed: bool | None = None, unattended: bool = False,
                  is_delivered: Callable[[str], bool] = lambda path: False,
                  now: Callable[[], datetime] = datetime.now, exists: Callable[[str], bool] = os.path.exists,
                  copy: Callable[[CopyPlan], None] = copy_session,
                  log: Callable[[str], None] = lambda m: None, progress: Progress | None = None) -> ReopenResult:
    """Einen Stand aus „Sicherungen …“ öffnen (Funktionsbeschreibung S. 32).

    Die aktuelle Session wird direkt geöffnet; jeder andere Stand als Kopie
    `<Session>_wiederhergestellt_hhmm.ptx` im Session-Ordner (dort finden sich die Audiodateien).
    Gehört der Beitrag jemand anderem, wird nichts geöffnet – erst freigeben.
    """
    from tonmischung.core.backups import belongs_to
    progress = progress or NullProgress()
    progress.step("Prüfen", os.path.basename(state_path))
    c = service.client.get(project_id)
    m = c.mix
    if not m.session_path:
        raise FetchError("Am Beitrag ist keine Session gespeichert")
    if not belongs_to(state_path, m.session_path) or not exists(state_path):
        raise FetchError(f"Stand nicht erreichbar oder gehört nicht zu dieser Session: {state_path}")
    mine = mixstate.owned_by(m, service.mixer, service.room)
    if m.state in mixstate.OWNED and not mine:
        raise FetchError(f"Der Beitrag ist in {m.room or '?'} bei {m.mixer or 'jemand'} übernommen – zum Öffnen "
                         "erst freigeben; ansehen und im Finder zeigen geht")
    current = same_session(state_path, m.session_path)
    plan = None if current else CopyPlan(FILE, state_path, restore_target(m.session_path, now(), exists))
    opened = ptsl.open_session()
    if opened is not None and current and same_session(opened.path, state_path):
        pt_plan = None                                     # ist schon offen
    else:
        _, pt_plan = plan_for_open_session(ptsl, is_delivered, confirmed, unattended, opened=opened)
    if not mine:
        progress.step("Übernehmen", c.title)
        c = service.take(project_id)
    warnings: list[str] = []
    target = state_path if plan is None else plan.target
    try:
        if pt_plan is not None:
            if pt_plan.steps:
                progress.step("Session wechseln", f"„{opened.name}“ speichern und schließen")
            run_import_plan(ptsl, pt_plan)
            if plan is not None:
                progress.step("Kopie anlegen", os.path.basename(plan.target))
                log(f"Stand kopieren: {plan.source} → {plan.target}")
                try:
                    copy(plan)
                except OSError as e:
                    raise FetchError(f"Kopie nicht angelegt: {e}") from e
            progress.step("Session öffnen", os.path.basename(target))
            warnings += open_and_wait(ptsl, target, progress)
    except PtslError as e:
        raise FetchError(f"{e} – nichts in Helmut geändert") from e
    progress.step("Helmut aktualisieren", "Session-Pfad")
    state_now = (c.mix.state if c.mix.state in (mixstate.MIXING, mixstate.EXPORTED, mixstate.IMPORTED)
                 else mixstate.IMPORTED)
    if target != m.session_path or state_now != c.mix.state:
        c, hint = service.set_state_or_queue(c, state_now, session_path=target)
        if hint:
            warnings.append(hint)
    log(f"Geöffnet: {target}")
    return ReopenResult(c, target, FILE if plan else "aktuell", 0, warnings)
