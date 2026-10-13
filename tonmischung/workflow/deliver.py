"""Mischung an Helmut zurückliefern (R1).

Nur WAVs gehen an Helmut (Vorgabe Jens). Verpacken, Prüfen und Verteilen macht
Helmut („Verarbeitung durch Helmut“); ein eigenes Sendefile baut Ottilie nur im Havariemodus (`havarie_delivery`).
Danach steht der Beitrag auf „ausgeliefert“ und ist entsperrt (Jens 09.10.2026); erneut zurückliefern
kann der Mischer, solange ihn niemand anders übernommen hat.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from tonmischung.core.timecode import PROGRAM_START
from tonmischung.core.progress import NullProgress, Progress
from tonmischung.core.session_rules import same_session
from tonmischung.helmut.mapping import Contribution
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import PtslError
from tonmischung.sessions.pt_export import run_pt_export
from tonmischung.sessions.runner import run_import_plan
from tonmischung.workflow.fetch import FetchError, plan_for_open_session
from tonmischung.workflow.reopen import open_and_wait


# Schritte mit Gewicht (grob nach Dauer) für die Fortschrittsanzeige
DELIVER_STEPS = [("Session vorbereiten", 3), ("Kürzen und einblenden", 3), ("Konsolidieren", 30),
                 ("Lautheit", 30), ("WAVs kopieren", 12), ("An Helmut übergeben", 22)]


@dataclass
class DeliverResult:
    contribution: Contribution
    delivered: list[str]
    warnings: list[str] = field(default_factory=list)


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
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        while True:
            data = fi.read(chunk)
            if not data:
                break
            fo.write(data)
            if on_bytes:
                on_bytes(len(data))
    shutil.copystat(src, tmp)
    os.replace(tmp, dst)
    return dst


def deliver(project_id: str, *, service: MixService, ptsl: PtslClient,
            export_tracks: list[str], loudness_tracks: list[str], handover_root: str,
            target_lufs: float = -23.0, max_truepeak: float = -3.0,
            confirmed: bool | None = None, is_delivered: Callable[[str], bool] = lambda p: False,
            now: Callable[[], datetime] = datetime.now,
            log: Callable[[str], None] = lambda m: None,
            progress: Progress | None = None,
            export_names: dict[str, str] | None = None, program_start: str = PROGRAM_START) -> DeliverResult:
    progress = progress or NullProgress()
    helmut = service.client
    contribution = helmut.get(project_id)
    service.check_deliverer(contribution)
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

    dest_dir = os.path.join(handover_root.format(projektId=project_id), stamp)
    log(f"WAVs an Helmut: {dest_dir}")
    delivered = []
    total = sum(os.path.getsize(w) for w in result.wav_files.values()) or 1
    copied = [0]
    progress.step("An Helmut übergeben", f"{len(result.wav_files)} WAVs")

    def counted(n: int, track: str = "") -> None:
        copied[0] += n
        progress.update(copied[0] / total, f"{os.path.basename(result.wav_files.get(track, track))} "
                                           f"({copied[0] / total * 100:.0f} %)")
    hints: list[str] = []
    for track, wav in result.wav_files.items():
        dst = copy_atomic(wav, dest_dir, on_bytes=lambda n, t=track: counted(n, t))
        hints.append(service.add_asset_or_queue(contribution, dst, "tonspur"))
        delivered.append(dst)
    contribution, hint = service.deliver_or_queue(contribution)
    hints.append(hint)
    log(f"Fertig: {len(delivered)} WAVs an Helmut übergeben, Zustand „ausgeliefert“")
    warnings = opened_hints + list(result.warnings) + sorted({h for h in hints if h})   # Ausfall: ein Hinweis genügt
    return DeliverResult(contribution, delivered, warnings)
