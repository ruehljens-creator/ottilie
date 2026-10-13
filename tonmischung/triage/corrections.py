"""Korrekturen aus der Vorschau: einen Clip in eine andere Kategorie verschieben – reine Logik.

Eine Korrektur gilt immer für beide Hälften eines Paares (Doppelmono/Stereo, Jens), damit
die Paare beim Verteilen zusammen bleiben.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from tonmischung.triage.analyse import TriageResult
from tonmischung.triage.decide import ClipDecision
from tonmischung.triage.names import ATMO, MUSIC, OTON, REST

CATEGORIES = (OTON, ATMO, MUSIC, REST)
REASON = "Korrektur in der Vorschau"


def partners(result: TriageResult, track: str, index: int) -> list[tuple[str, int]]:
    """Der Clip und – bei Paaren mit gleichem Schnitt – sein Gegenstück."""
    keys = [(track, index)]
    other = {a: b for a, b in result.pairs} | {b: a for a, b in result.pairs}
    if track in other:
        mine = next((d.clip for d in result.decisions if (d.clip.track, d.clip.index) == (track, index)), None)
        twin = next((d.clip for d in result.decisions
                     if d.clip.track == other[track] and d.clip.index == index), None)
        if mine and twin and abs(mine.start - twin.start) <= 1 and abs(mine.end - twin.end) <= 1:
            keys.append((twin.track, twin.index))
    return keys


def apply_corrections(result: TriageResult, corrections: Iterable[tuple[str, int, str]]) -> TriageResult:
    """Neue TriageResult mit geänderten Clip-Kategorien (Spurkategorien bleiben)."""
    wanted: dict[tuple[str, int], str] = {}
    for track, index, category in corrections:
        if category not in CATEGORIES:
            raise ValueError(f"Unbekannte Kategorie: {category}")
        for key in partners(result, track, int(index)):
            wanted[key] = category
    decisions = []
    for d in result.decisions:
        key = (d.clip.track, d.clip.index)
        if key in wanted:
            cat = wanted[key]
            decisions.append(ClipDecision(d.clip, cat, REASON, cat != result.verdicts[d.clip.track].category))
        else:
            decisions.append(d)
    return replace(result, decisions=decisions)
