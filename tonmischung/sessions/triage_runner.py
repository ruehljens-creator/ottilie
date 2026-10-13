"""Triage T3: Verteilung in Pro Tools ausführen (Weg C).

1. Template-Spuren für den Import so umbenennen, dass Match Tracks jede AAF-Spur auf ihre
   Zielspur legt – kollisionsfrei über Zwischennamen, danach alles zurück (auch bei Fehlern).
2. Abweichende Clips auf ihre Zielspuren: kopieren, einfügen, prüfen, dann Quelle leeren
   (Edit-Modus Slip; beteiligte Spuren werden eingeblendet).
3. Mono ohne Partner auf die Partnerspur kopieren (Doppelmono).
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from tonmischung.core.session_text import track_clips
from tonmischung.core.timecode import frames_to_tc
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import PtslError
from tonmischung.triage.placement import ClipMove, PlacementError, TrackCopy, TrackMove

TEMP_PREFIX = "tm~"


@contextmanager
def renamed_for_import(ptsl: PtslClient, moves: Sequence[TrackMove], session_tracks: Sequence[str],
                       warnings: list[str]) -> Iterator[None]:
    """Zielspuren tragen während des Imports die AAF-Namen; Spuren, deren Name mit einer
    AAF-Spur kollidiert, bekommen einen Zwischennamen. Danach heißt alles wieder wie vorher."""
    aaf_names = [m.aaf_track for m in moves]
    if len(set(aaf_names)) != len(aaf_names):
        raise PlacementError("Gleichnamige AAF-Spuren – Match Tracks kann sie nicht trennen")
    targets = {m.target for m in moves}
    involved = [t for t in session_tracks if t in targets or t in set(aaf_names)]
    taken = set(session_tracks) | set(aaf_names)
    temp, n = {}, 0
    for t in involved:
        while f"{TEMP_PREFIX}{n}" in taken:
            n += 1
        temp[t] = f"{TEMP_PREFIX}{n}"
        n += 1
    current = {t: t for t in involved}
    try:
        for t in involved:
            ptsl.rename_track(t, temp[t])
            current[t] = temp[t]
        for m in moves:
            ptsl.rename_track(temp[m.target], m.aaf_track)
            current[m.target] = m.aaf_track
        yield
    finally:
        # erst alles auf Zwischennamen (gibt die AAF-Namen frei), dann zurück auf die Originalnamen
        for t in involved:
            if current[t] not in (temp[t], t):
                try:
                    ptsl.rename_track(current[t], temp[t])
                    current[t] = temp[t]
                except PtslError as e:
                    warnings.append(f"Spur „{current[t]}“ nicht zurück in „{t}“ umbenannt: {e}")
        for t in involved:
            if current[t] != t:
                try:
                    ptsl.rename_track(current[t], t)
                    current[t] = t
                except PtslError as e:
                    warnings.append(f"Spur „{current[t]}“ nicht zurück in „{t}“ umbenannt: {e}")


def apply_clip_moves(ptsl: PtslClient, moves: Sequence[ClipMove], fps: int = 25,
                     on_move: Callable[[int, int], None] | None = None) -> list[str]:
    """Bereiche verschieben – sicher: kopieren, einfügen, prüfen, erst dann die Quelle leeren.

    Scheitert das Einfügen oder fehlt danach ein Clip auf der Zielspur, bleibt die Quelle
    unangetastet (nichts geht verloren). Beteiligte Spuren werden vorher eingeblendet.
    """
    warnings: list[str] = []
    if not moves:
        return warnings
    ptsl.show_tracks(sorted({t for m in moves for t in m.sources + m.targets}))
    pasted: list[ClipMove] = []
    with ptsl.edit_selection():
        for i, m in enumerate(moves, 1):
            a, b = frames_to_tc(m.start, fps), frames_to_tc(m.end, fps)
            try:
                ptsl.select_range(list(m.sources), a, b)
                ptsl.edit("Copy")
                if len(m.targets) == len(m.sources):
                    ptsl.select_range(list(m.targets), a, b)
                    ptsl.edit("Paste")
                else:                                   # Mono → beide Hälften eines Paares
                    for t in m.targets:
                        ptsl.select_range([t], a, b)
                        ptsl.edit("Paste")
                pasted.append(m)
            except PtslError as e:
                warnings.append(f"{'/'.join(m.sources)} {a}–{b} nicht nach {'/'.join(m.targets)} "
                                f"verschoben, bleibt auf der Spur: {e}")
            if on_move:
                on_move(i, len(moves))
        if not pasted:
            return warnings
        now = track_clips(ptsl.session_text(), fps)
        doubts: list[tuple[ClipMove, PtslError]] = []
        for m in pasted:
            a, b = frames_to_tc(m.start, fps), frames_to_tc(m.end, fps)
            have = {t: {(x, y) for x, y, _ in now.get(t, [])} for t in m.targets}
            missing = [c for t, part in zip(m.targets, m.parts) for c in part if (c.start, c.end) not in have[t]]
            if missing:
                warnings.append(f"{'/'.join(m.sources)} {a}–{b}: auf {'/'.join(m.targets)} fehlen "
                                f"{len(missing)} Clips – Quelle bleibt (Material jetzt doppelt)")
                continue
            try:
                ptsl.select_range(list(m.sources), a, b)
                ptsl.edit("Clear")
            except PtslError as e:
                doubts.append((m, e))
        # Pro Tools meldet beim Leeren manchmal einen Fehler der Video Engine („unable to create
        # thumbnails“), obwohl geleert wurde (live 02.10.2026) – nachsehen, einmal wiederholen.
        for attempt in (1, 2):
            if not doubts:
                break
            now = track_clips(ptsl.session_text(), fps)
            still = []
            for m, e in doubts:
                left = [c for t, part in zip(m.sources, _source_parts(m)) for c in part
                        if (c.start, c.end) in {(x, y) for x, y, _ in now.get(t, [])}]
                if not left:
                    continue
                if attempt == 1:
                    try:
                        ptsl.select_range(list(m.sources), frames_to_tc(m.start, fps), frames_to_tc(m.end, fps))
                        ptsl.edit("Clear")
                    except PtslError as e2:
                        e = e2
                    still.append((m, e))
                else:
                    warnings.append(f"{'/'.join(m.sources)} {frames_to_tc(m.start, fps)}–"
                                    f"{frames_to_tc(m.end, fps)} kopiert, Quelle nicht geleert "
                                    f"(Material doppelt): {e}")
            doubts = still
    return warnings


def _source_parts(m: ClipMove) -> tuple[tuple, ...]:
    """Auf welcher Quellspur welche Clips liegen (Paar: je Hälfte; Mono: alle auf der einen)."""
    return m.parts[:len(m.sources)] if len(m.parts) >= len(m.sources) else (m.clips,) * len(m.sources)


def apply_copies(ptsl: PtslClient, copies: Sequence[TrackCopy]) -> list[str]:
    """Alle Clips der Quellspur an gleicher Stelle auf die Partnerspur kopieren."""
    warnings: list[str] = []
    if not copies:
        return warnings
    ptsl.show_tracks(sorted({t for c in copies for t in (c.source, c.target)}))
    with ptsl.edit_selection():
        for c in copies:
            try:
                a, b = ptsl.track_selection(c.source)
                ptsl.edit("Copy")
                ptsl.select_range([c.target], a, b)
                ptsl.edit("Paste")
            except PtslError as e:
                warnings.append(f"{c.source} nicht auf {c.target} kopiert: {e}")
    return warnings
