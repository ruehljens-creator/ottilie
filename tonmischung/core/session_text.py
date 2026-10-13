"""Clips je Spur aus Pro Tools' „Session Info as Text“ lesen – reine Logik.

Erwartet den Text mit Timecode (track_offset TimeCode, ohne Subframes). Pro Tools
gibt Clip-Grenzen ohne Fade-Bereich aus (fade_handling DontShowCrossfades).
"""
from __future__ import annotations

import re

from tonmischung.core.timecode import tc_to_frames

_CLIP = re.compile(r"^\s*\d+\s+\d+\s+(.+?)\s+(\d\d:\d\d:\d\d:\d\d)\s+(\d\d:\d\d:\d\d:\d\d)", re.M)

Clip = tuple[int, int, str]          # (Anfang, Ende) in Frames, Name


# Pro Tools hängt im Text das Format mehrkanaliger Spuren an: „ST (Stereo)“ (live 04.10.2026)
_FORMAT = re.compile(r" \((?:Mono|Stereo|LCR|LCRS|Quad|Ambisonics[^)]*|\d+(?:\.\d+)+(?: [A-Za-z]+)?)\)$")


def track_name(raw: str) -> str:
    """Spurname ohne angehängtes Kanalformat."""
    return _FORMAT.sub("", raw.strip())


def track_clips(text: str, fps: int = 25) -> dict[str, list[Clip]]:
    """{Spurname: [(Anfang, Ende, Clipname), …]} aus den TRACK-Blöcken."""
    result: dict[str, list[Clip]] = {}
    for block in text.split("\nTRACK NAME:\t")[1:]:
        name = track_name(block.split("\n", 1)[0])
        result[name] = [(tc_to_frames(a, fps), tc_to_frames(b, fps), n.strip())
                        for n, a, b in _CLIP.findall(block)]
    return result


def missing(expected: list[tuple[int, int]], present: list[Clip]) -> list[tuple[int, int]]:
    """Erwartete (Anfang, Ende), die nicht exakt als Clip vorhanden sind."""
    have = {(a, b) for a, b, _ in present}
    return [c for c in expected if c not in have]
