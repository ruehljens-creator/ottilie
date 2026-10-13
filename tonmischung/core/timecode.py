"""Timecode rechnen – ein Ort statt dreier Varianten (Review C: `core/session_text`, `delivery/mux`,
`sessions/pt_export` hatten je eigene, leicht verschiedene Fassungen).

Formate: „HH:MM:SS:FF“, mit Unterframes „HH:MM:SS:FF.ss“ (PTSL) und mit „;“ (Drop-Frame-Schreibweise, gerechnet wie
„:“ – das Haus arbeitet mit 25 fps, ohne Drop-Frame).
"""
from __future__ import annotations

PROGRAM_START = "10:00:00:00"          # Haus-Standard (Einstellung import.timecode_startband.programmbeginn)


def tc_parts(tc: str) -> tuple[int, int, int, int]:
    """„10:00:05:12.00“ → (10, 0, 5, 12); Unterframes werden ignoriert."""
    h, m, s, f = (int(x) for x in tc.strip().split(".")[0].replace(";", ":").split(":")[:4])
    return h, m, s, f


def tc_to_frames(tc: str, fps: int = 25) -> int:
    h, m, s, f = tc_parts(tc)
    return ((h * 60 + m) * 60 + s) * fps + f


def frames_to_tc(frames: int, fps: int = 25) -> str:
    """Frames → „HH:MM:SS:FF“; über 24 Stunden beginnt der Timecode wieder bei 00."""
    frames %= 24 * 3600 * fps
    s, f = divmod(frames, fps)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def parse_rate(text: object, default: float = 0.0) -> float:
    """Bildrate aus „25/1“, „30000/1001“ oder „25“ (ffprobe); Unlesbares → `default`."""
    try:
        num, _, den = str(text or "").partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return default


def tc_seconds(tc: str, fps: int = 25) -> float:
    """„10:00:05:12.00“ oder „10:00:05:12“ → Sekunden."""
    return tc_to_frames(tc, fps) / fps
