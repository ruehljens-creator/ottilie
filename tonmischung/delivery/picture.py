"""Bild für das Sendefile finden und den Programmbeginn darin bestimmen (reine Logik).

Jens (03.10.2026, Frage 1 aus dem Plan M5): „Das Bild wird neben dem AAF liegen.“ Gesucht wird deshalb im Ordner
des Übergabe-AAF. Liegen dort mehrere Videos, wählt der Mischer im Export-Dialog – die App entscheidet nicht
still. Wo 10:00:00:00 im Bild liegt, sagt der Timecode des Bildes; ohne Timecode gilt der Dateianfang.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from tonmischung.core.timecode import PROGRAM_START, tc_to_frames
from tonmischung.delivery.mux import offset_from_timecode
from tonmischung.helmut.mapping import VIDEO_EXT


class PictureError(ValueError):
    """Kein eindeutiges oder kein plausibles Bild – klare Meldung, kein Sendefile."""


@dataclass(frozen=True)
class Picture:
    path: str
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class ProgramStart:
    offset_s: float           # Sekunden vom Bildanfang bis zum Programmbeginn (Startband davor)
    note: str


def candidates(aaf_path: str, listdir: Callable[[str], list[str]] = os.listdir,
               isfile: Callable[[str], bool] = os.path.isfile) -> tuple[str, ...]:
    folder = os.path.dirname(os.path.abspath(aaf_path))
    try:
        names = sorted(listdir(folder))
    except OSError as e:
        raise PictureError(f"Übergabeordner nicht lesbar: {folder} ({e.strerror or e})") from e
    return tuple(os.path.join(folder, n) for n in names
                 if not n.startswith(".") and n.lower().endswith(VIDEO_EXT) and isfile(os.path.join(folder, n)))


def find_picture(aaf_path: str, chosen: str = "", listdir: Callable[[str], list[str]] = os.listdir,
                 isfile: Callable[[str], bool] = os.path.isfile) -> Picture:
    """Das Bild neben dem AAF; bei mehreren muss `chosen` eines davon sein."""
    if not aaf_path:
        raise PictureError("Kein AAF in der Übergabe – das Bild wird neben dem AAF gesucht")
    found = candidates(aaf_path, listdir, isfile)
    if not found:
        raise PictureError(f"Kein Bild neben dem AAF ({os.path.dirname(os.path.abspath(aaf_path))})")
    if chosen:
        if chosen not in found:
            raise PictureError(f"„{os.path.basename(chosen)}“ liegt nicht neben dem AAF")
        return Picture(chosen, found)
    if len(found) > 1:
        raise PictureError("Mehrere Bilder neben dem AAF – bitte wählen: "
                           + ", ".join(os.path.basename(f) for f in found))
    return Picture(found[0], found)


def program_start(picture_tc: str | None, program_tc: str = PROGRAM_START, max_leader_s: float = 30.0,
                  fps: int = 25, sequence_tc: str | None = None, sequence_fps: int | None = None) -> ProgramStart:
    """Wo der Programmbeginn im Bild liegt. Ein Bild, das erst nach dem Programmbeginn anfängt oder ein
    längeres Startband hätte als erlaubt (`import.timecode_startband.startband_max_s`), wird abgelehnt.

    `sequence_tc`: Start der Sequenz im AAF, wenn der Import sie auf den Programmbeginn verschoben hat (Premiere ab
    00:00:00:00, DaVinci Resolve ab 01:00:00:00 – live 04.10.2026). Bild und Sequenz kommen aus derselben Timeline;
    gemessen wird dann am Sequenzstart statt an `program_tc`. `fps` ist die Bildrate des Bildes."""
    off = offset_from_timecode(picture_tc, program_tc, fps)
    if off is None:
        # ANNAHME: ohne Timecode beginnt das Programm am Dateianfang (so liegt das Referenzvideo in Pro Tools)
        return ProgramStart(0.0, "Bild ohne Timecode – Programmbeginn am Dateianfang angenommen")
    if sequence_tc and not 0 <= off <= max_leader_s:
        seq = (tc_to_frames(sequence_tc, sequence_fps or fps) / (sequence_fps or fps)
               - tc_to_frames(str(picture_tc), fps) / fps)
        if 0 <= seq <= max_leader_s:
            lead = "Programmbeginn am Bildanfang" if seq == 0 else f"Startband im Bild: {seq:.2f} s"
            return ProgramStart(seq, f"Bild beginnt bei {picture_tc} wie die Sequenz (beim Import auf {program_tc} "
                                     f"gelegt) – {lead}")
    if off < 0:
        raise PictureError(f"Bild beginnt bei {picture_tc}, nach dem Programmbeginn {program_tc}")
    if off > max_leader_s:
        raise PictureError(f"Bild beginnt bei {picture_tc}: {off:.0f} s vor {program_tc}, mehr als "
                           f"{max_leader_s:.0f} s Startband – Timecode prüfen")
    if off == 0:
        return ProgramStart(0.0, f"Bild beginnt am Programmbeginn {program_tc}")
    return ProgramStart(off, f"Startband im Bild: {off:.2f} s ({picture_tc} bis {program_tc})")
