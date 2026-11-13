"""Mischung an Helmut zurückliefern (R1).

Nur WAVs gehen an Helmut (Vorgabe Jens). Verpacken, Prüfen und Verteilen macht
Helmut („Verarbeitung durch Helmut“); ein eigenes Sendefile baut Ottilie nur im Havariemodus (`havarie_delivery`).
Danach steht der Beitrag auf „ausgeliefert“ und ist entsperrt (Jens 09.10.2026); erneut zurückliefern
kann der Mischer, solange ihn niemand anders übernommen hat.
"""
from __future__ import annotations

import errno
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping, Sequence

from tonmischung.core.timecode import PROGRAM_START
from tonmischung.core.progress import NullProgress, Progress
from tonmischung.core.catchup import HINT, HINT_AUTH
from tonmischung.core.mixstate import ConflictError
from tonmischung.core.fileio import write_new
from tonmischung.core.session_rules import same_session
from tonmischung.delivery import manifest
from tonmischung.helmut.client import HelmutAuthError, HelmutError
from tonmischung.helmut.mapping import Asset, Contribution
from tonmischung.helmut.service import HINT_WAITING, QUEUED, MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import PtslError
from tonmischung.sessions.pt_export import run_pt_export
from tonmischung.sessions.runner import run_import_plan
from tonmischung.workflow.fetch import FetchError, plan_for_open_session
from tonmischung.workflow.reopen import open_and_wait


VIDEO_MISSING = ("In Helmut ist kein Video zum Beitrag zu finden – in der Metadaten-Datei fehlt nur der Videopfad. "
                 "Die WAVs sind vollständig.")
VIDEO_UNKNOWN = ("Helmut hat den Videopfad nicht geliefert – in der Metadaten-Datei fehlt nur der Videopfad. "
                 "Die WAVs sind vollständig.")
HINT_REJECTED = ("Helmut hat einen Teil der Lieferung abgelehnt. Die WAVs liegen vollständig im Übergabeordner; "
                 "Ottilie versucht es noch einige Minuten weiter – den Stand zeigt „Nachtrag“ oben in der "
                 "Beitragsliste. Nicht erneut zurückliefern. Erscheint dort ein Konflikt, die Helmut-Betreuung "
                 "anrufen.")
QUEUED_HINTS = (HINT, HINT_AUTH, HINT_REJECTED, HINT_WAITING)

# Schritte mit Gewicht (grob nach Dauer) für die Fortschrittsanzeige
DELIVER_STEPS = [("Session vorbereiten", 3), ("Kürzen und einblenden", 3), ("Konsolidieren", 30),
                 ("Lautheit", 30), ("WAVs kopieren", 12), ("Übergeben", 22)]


@dataclass
class DeliverResult:
    contribution: Contribution
    delivered: list[str]
    warnings: list[str] = field(default_factory=list)
    metadata: str = ""                       # Metadaten-Datei im Übergabeordner
    queued: str = ""                         # etwas wartet noch auf Helmut: „abgelehnt“ | „ausfall“ | leer


@dataclass(frozen=True)
class DeliveryTarget:
    """Wohin kopiert wird (`copy_dir`) und wo die Dateien am Ende liegen (`final_dir`). `finish(kopierte)` legt sie an
    ihren Platz und gibt die neuen Pfade zurück (Betriebsart Übergabeordner: Hilfsordner → „Audio Export“)."""
    copy_dir: str
    final_dir: str
    finish: Callable[[list[str]], list[str]] = list
    check: Callable[[], None] = lambda: None     # vor jeder Datei: Ziel noch sicher? (OSError, wenn nicht)

    def final(self, staged: str) -> str:
        return os.path.join(self.final_dir, os.path.basename(staged))


def copy_atomic(src: str, dst_dir: str,
                on_bytes: Callable[[int], None] | None = None, chunk: int = 8 << 20) -> str:
    """Kopie unter Hilfsnamen, dann umbenennen – Helmut sieht nie halbe Dateien.

    `on_bytes` bekommt die jeweils kopierte Menge (für die Fortschrittsanzeige).
    """
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst):
        raise FileExistsError(f"{dst} gibt es schon – nichts überschrieben")
    tmp = dst + ".part"
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                data = fi.read(chunk)
                if not data:
                    break
                fo.write(data)
                if on_bytes:
                    on_bytes(len(data))
        if os.path.getsize(tmp) != os.path.getsize(src):      # Ziel voll o. Ä. ohne Fehlermeldung
            raise OSError(errno.EIO, f"Kopie von {os.path.basename(src)} unvollständig")
        shutil.copystat(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)                   # nur die eigene Hilfsdatei – das Original bleibt
        raise
    return dst


def deliver(project_id: str, *, service: MixService, ptsl: PtslClient,
            export_tracks: list[str], loudness_tracks: list[str], handover_root: str,
            target_lufs: float = -23.0, max_truepeak: float = -3.0,
            confirmed: bool | None = None, is_delivered: Callable[[str], bool] = lambda p: False,
            now: Callable[[], datetime] = datetime.now,
            log: Callable[[str], None] = lambda m: None,
            progress: Progress | None = None,
            export_names: dict[str, str] | None = None, program_start: str = PROGRAM_START,
            video_id: str | None = None, track_table: Sequence[Mapping] = (),
            video_id_required: bool = False,
            target: Callable[[str, str], DeliveryTarget] | None = None) -> DeliverResult:
    """`video_id`: in die Metadaten-Datei und ins Set „Mischung“ (None = wie in Helmut); `track_table`: Spurtabelle
    für die Kanalbelegung in der Metadaten-Datei. `target(projekt_id, zeitstempel)`: wohin geliefert wird (Betriebsart
    Übergabeordner: „<Filmordner>/Audio Export“); ohne Angabe `handover_root`/<Zeitstempel>."""
    progress = progress or NullProgress()
    if video_id is not None:                 # leer = Wert in Helmut bleibt (nie still löschen)
        video_id = manifest.clean_video_id(video_id) or None
    helmut = service.client
    service.settle(project_id)               # ältere Nachträge zuerst, sonst überholten sie diese Lieferung
    contribution = helmut.get(project_id)
    service.check_deliverer(contribution)
    if video_id_required and not (video_id or contribution.mix.video_id):
        raise FetchError("VideoID fehlt – ohne VideoID wird nicht zurückgeliefert")
    session_path = contribution.mix.session_path
    if not session_path:
        raise FetchError("Am Beitrag ist keine Session gespeichert – erst holen")

    # Richtige Session offen? Sonst nach den Session-Regeln wechseln.
    opened_hints: list[str] = []
    open_session = ptsl.open_session()
    if open_session is None or not same_session(open_session.path, session_path):
        _, plan = plan_for_open_session(ptsl, is_delivered, confirmed, target_session_path=session_path,
                                        opened=open_session)
        run_import_plan(ptsl, plan)
        log(f"Session öffnen: {session_path}")
        # warten und prüfen, dass genau sie offen ist – sonst ginge der Export an die falsche Session (Review B20)
        opened_hints = open_and_wait(ptsl, session_path, progress)

    stamp = now().strftime("%Y%m%d-%H%M%S")
    try:                                     # WAVs und Lautheitsbericht nach <Session>/export (wie PunchBuddy)
        result = run_pt_export(ptsl, export_tracks, None, loudness_tracks=loudness_tracks,
                               target_lufs=target_lufs, max_truepeak=max_truepeak, log=log,
                               progress=progress, export_names=export_names, program_start=program_start)
    except PtslError as e:
        raise FetchError(f"Pro-Tools-Teil gescheitert: {e} – nichts an Helmut übergeben") from e

    if target is None:
        plain = os.path.join(handover_root.format(projektId=project_id), stamp)
        dest = DeliveryTarget(plain, plain)
    else:
        try:
            dest = target(project_id, stamp)
        except OSError as e:                 # Exportordner unsicher (Symlink) oder nicht anlegbar
            raise FetchError(f"Ziel nicht nutzbar: {e.strerror or e} – nichts geliefert") from e
    dest_dir = dest.copy_dir
    log(f"WAVs an Helmut: {dest_dir}")
    delivered = []
    total = sum(os.path.getsize(w) for w in result.wav_files.values()) or 1
    copied = [0]
    progress.step("Übergeben", f"{len(result.wav_files)} WAVs")

    def counted(n: int, track: str = "") -> None:
        copied[0] += n
        progress.update(copied[0] / total, f"{os.path.basename(result.wav_files.get(track, track))} "
                                           f"({copied[0] / total * 100:.0f} %)")
    try:                                     # erst alle Dateien, dann Helmut (Ausfall)
        for track, wav in result.wav_files.items():
            dest.check()                     # Ordner zwischendurch gegen einen Verweis getauscht? (Sicherheit A)
            delivered.append(copy_atomic(wav, dest_dir, on_bytes=lambda n, t=track: counted(n, t)))
    except OSError as e:
        raise FetchError(f"Kopieren in den Übergabeordner gescheitert ({e.strerror or e}) – nichts an Helmut "
                         f"übergeben; Teilkopie in {dest_dir}") from e
    # Metadaten-Datei als letzte Datei (Jens 10.11.2026): ein Helmut-Ablauf kann sie als „alles da“ nehmen.
    # Sie entsteht, bevor Helmut etwas sieht – scheitert sie (Platte voll), ist in Helmut nichts halb angemeldet.
    video, video_hint = _video(helmut, project_id, log)
    finals = [dest.final(p) for p in delivered]          # wo die WAVs am Ende liegen (Pfade in der Datei)
    data = manifest.build(contribution, table=track_table, wavs=dict(zip(result.wav_files, finals)), video=video,
                          video_id=contribution.mix.video_id if video_id is None else video_id,
                          created=now().astimezone(), program_start=program_start,
                          program_end=result.out_time.split(".")[0], mixer=service.mixer, room=service.room,
                          target_lufs=target_lufs, max_truepeak=max_truepeak, loudness_tracks=loudness_tracks)
    meta_path = os.path.join(dest_dir, manifest.file_name(manifest.project_id(contribution)))
    try:
        dest.check()
        write_new(meta_path, lambda tmp: _dump(tmp, data))
    except OSError as e:
        raise FetchError(f"Metadaten-Datei nicht geschrieben ({e.strerror or e}) – nichts an Helmut übergeben; "
                         f"die WAVs liegen in {dest_dir}") from e
    log(f"Metadaten: {meta_path}")

    hints = [video_hint]
    try:                                     # der Export dauert Minuten: gehört der Beitrag noch? (Ausfall R7)
        service.check_deliverer(helmut.get(project_id))
    except QUEUED as e:                      # Helmut weg: alles einreihen, der Nachtrag prüft den Besitz selbst
        hints.append(HINT_AUTH if isinstance(e, HelmutAuthError) else HINT)
    except ConflictError as e:
        raise FetchError(f"{e} – nichts in Helmut angemeldet; die WAVs liegen in {dest_dir}") from e
    except HelmutError as e:                 # Serverfehler: noch nichts angemeldet – Ordner nennen, kein Rohtext
        raise FetchError(f"Helmut meldet einen Fehler ({e.status or 'unbekannt'}) – nichts in Helmut angemeldet; "
                         f"die WAVs liegen in {dest_dir}. Später erneut zurückliefern.") from e
    try:                                     # an den endgültigen Platz (Übergabeordner: „Audio Export“), Datei zuletzt
        placed = dest.finish(delivered + [meta_path])
    except OSError as e:
        raise FetchError(f"Lieferung nicht an ihren Platz gelegt ({e.strerror or e}) – nichts gemeldet; die Dateien "
                         f"liegen in {dest_dir} bzw. schon in {dest.final_dir}") from e
    delivered, meta_path = placed[:-1], placed[-1]
    dest_dir = dest.final_dir                # der Hilfsordner ist jetzt leer und entfernt

    def register(path: str, kind: str) -> str:
        """Anmelden; ist in diesem Lauf schon etwas eingereiht, dahinter. Lehnt Helmut ab (4xx/500), einreihen statt
        abbrechen – sonst lieferte der Mischer neu und die schon angemeldeten WAVs stünden doppelt in Helmut."""
        try:
            return service.add_asset_or_queue(contribution, path, kind, queue_only=any(hints[1:]))
        except HelmutError as e:
            if service.queue is None:
                raise
            log(f"Helmut lehnt {os.path.basename(path)} ab: {e} – eingereiht")
            service.queue.add_asset(contribution.id, contribution.title, path, kind, service.mixer, service.room)
            return HINT_REJECTED

    for dst in delivered:                    # ist eins eingereiht, folgen alle dahinter (Reihenfolge im Nachtrag)
        hints.append(register(dst, "tonspur"))
    # „ausgeliefert“ vor der Metadaten-Datei: eingereiht hält ein Konflikt beim Zustand sie im Nachtrag auf
    try:
        contribution, hint = service.deliver_or_queue(contribution, video_id, queue_only=any(hints[1:]))
    except HelmutError as e:                 # abgelehnt (kein Ausfall): einreihen, die WAVs stehen schon in Helmut
        if service.queue is None:
            raise
        log(f"Helmut lehnt „ausgeliefert“ ab: {e} – eingereiht")
        contribution, _ = service.deliver_or_queue(contribution, video_id, queue_only=True)
        hint = HINT_REJECTED
    except ConflictError as e:               # in den Sekunden seit der Besitzprüfung übernommen (Ausfall R8)
        raise FetchError(f"{e} – die WAVs sind bereits an diesem Beitrag in Helmut angemeldet und liegen in "
                         f"{dest_dir}; bitte mit dem neuen Mischer klären, nicht erneut zurückliefern") from e
    hints.append(hint)
    try:
        hints.append(register(meta_path, "metadaten"))
    except HelmutError as e:                 # ohne Warteschlange: WAVs und Zustand stehen, die Datei im Ordner
        log(f"Metadaten-Datei nicht angemeldet: {e}")
        hints.append("Helmut hat die Metadaten-Datei nicht angenommen – sie liegt im Übergabeordner, "
                     "die WAVs sind übergeben")
    log(f"Fertig: {len(delivered)} WAVs an Helmut übergeben, Zustand „ausgeliefert“")
    warnings = opened_hints + list(result.warnings) + sorted({h for h in hints if h})   # Ausfall: ein Hinweis genügt
    return DeliverResult(contribution, delivered, warnings, meta_path,
                         queued="abgelehnt" if HINT_REJECTED in hints else
                         "ausfall" if any(h in QUEUED_HINTS for h in hints) else "")


def _video(helmut, project_id: str, log: Callable[[str], None]) -> tuple[Asset | None, str]:
    """Referenzvideo des Beitrags laut Helmut; fehlt es oder antwortet Helmut nicht, steht es leer in der Datei."""
    try:
        video = helmut.handover(project_id).video
    except HelmutError as e:
        log(f"Videopfad nicht ermittelt: {e}")
        return None, VIDEO_UNKNOWN
    return video, "" if video else VIDEO_MISSING


def _dump(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
