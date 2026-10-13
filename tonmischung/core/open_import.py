"""Import in die offene Session – Optionen, Lage und Überschneidungen (reine Logik).

Funktionsbeschreibung S. 10–11: Statt einer neuen Session wird das AAF in die geöffnete Session
geladen. Einstellungen unter „Session-Ablage → Offene Session“: Umzug (kopieren / Speichern unter /
bleibt), Clips (neue Playlist / einfügen / neue Spuren), Position (Programmbeginn / nach dem letzten
Clip / Einfügemarke), bisheriger Beitrag (bleibt in Mischung / zurückgeben / weitergeführt).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tonmischung.core.track_match import MAP, SKIP, TrackDecision

RELOCATE = ("kopieren", "speichern_unter", "bleibt")
CLIPS = ("neue_playlist", "einfuegen", "neue_spuren")
POSITION = ("programmbeginn", "nach_letztem_clip", "einfuegemarke")
PREVIOUS = ("bleibt_in_mischung", "zurueckgeben", "weitergefuehrt_markieren")

LABELS = {
    "kopieren": "Session mit allen Dateien ins neue Projekt kopieren",
    "speichern_unter": "Session ins neue Projekt verlegen (Speichern unter)",
    "bleibt": "Session bleibt im bisherigen Projekt",
    "neue_playlist": "passende Spuren auf neue Playlist",
    "einfuegen": "in passende Spuren einfügen",
    "neue_spuren": "als neue Spuren",
    "programmbeginn": "Programmbeginn (Lage aus der AAF)",
    "nach_letztem_clip": "nach dem letzten Clip (nächste volle Minute)",
    "einfuegemarke": "an der Einfügemarke",
    "bleibt_in_mischung": "bleibt in Mischung",
    "zurueckgeben": "zurückgeben (freigeben)",
    "weitergefuehrt_markieren": "als „weitergeführt in …“ markieren",
}

# ANNAHME A5 (Plan M4-Rest): Abbildung auf PTSL – „neue Playlist“ = bestehende Playlists ersetzen.
# Ob Pro Tools die bisherige Playlist als Alternative behält, ist nicht belegt; daher nur mit Umzug.
PLAYLIST = {"neue_playlist": "ImportReplaceExistingPlaylists",
            "einfuegen": "ImportOverlayNewOnExistingPlaylists",
            "neue_spuren": "ImportOverlayNewOnExistingPlaylists"}

Interval = tuple[int, int]
MIN_GAP_S = 30            # Mindestabstand zum letzten Clip (Jens, 03.10.2026)


@dataclass(frozen=True)
class OpenImportOptions:
    relocate: str = "kopieren"
    clips: str = "neue_playlist"
    position: str = "programmbeginn"
    previous: str = "bleibt_in_mischung"

    def check(self) -> list[str]:
        """Fehler der Kombination; leer = in Ordnung."""
        errors = []
        for value, allowed in ((self.relocate, RELOCATE), (self.clips, CLIPS), (self.position, POSITION),
                               (self.previous, PREVIOUS)):
            if value not in allowed:
                errors.append(f"Unbekannte Wahl: {value}")
        if self.relocate == "bleibt" and self.clips == "neue_playlist":
            errors.append("„Neue Playlist“ ersetzt die bisherige Playlist; ob Pro Tools sie als Alternative "
                          "behält, ist noch nicht geprüft. Mit „Session bleibt“ bitte „einfügen“ oder „neue "
                          "Spuren“ wählen, oder die Session kopieren bzw. unter neuem Namen speichern.")
        return errors


def start_frame(position: str, clips_before: Mapping[str, Sequence[tuple]], insertion: int | None,
                fps: int) -> int | None:
    """Wohin der Anfang der AAF-Sequenz kommt (Frames) – None = Lage aus der AAF beibehalten.

    „nach dem letzten Clip“: nächste volle Minute nach dem Ende des letzten Clips mit mindestens 30 s
    Abstand, sonst eine volle Minute später (Jens, 03.10.2026); ohne Clips wie Programmbeginn.
    """
    if position == "einfuegemarke":
        return insertion
    if position == "nach_letztem_clip":
        ends = [c[1] for v in clips_before.values() for c in v]
        if not ends:
            return None
        minute = 60 * fps
        return -(-(max(ends) + MIN_GAP_S * fps) // minute) * minute
    return None


def expected_clips(aaf_clips: Mapping[str, Sequence[Interval]], aaf_start: int | None, start: int | None,
                   decisions: Mapping[str, TrackDecision]) -> dict[str, list[Interval]]:
    """Wo die AAF-Clips nach dem Import liegen sollen: Zielspur → [(Anfang, Ende)]."""
    delta = 0 if start is None or aaf_start is None else start - aaf_start
    out: dict[str, list[Interval]] = {}
    for track, clips in aaf_clips.items():
        d = decisions.get(track)
        if d is not None and d.action == SKIP:
            continue
        target = d.target if d is not None and d.action == MAP else track
        out.setdefault(target, []).extend((a + delta, b + delta) for a, b in clips)
    return out


def overlaps(expected: Mapping[str, Sequence[Interval]],
             clips_before: Mapping[str, Sequence[tuple]]) -> dict[str, int]:
    """Spuren, auf denen neue Clips vorhandene überdecken würden: Spur → Anzahl betroffener Clips."""
    hits: dict[str, int] = {}
    for track, new in expected.items():
        old = [(c[0], c[1]) for c in clips_before.get(track, ())]
        n = sum(1 for a, b in old if any(a < nb and na < b for na, nb in new))
        if n:
            hits[track] = n
    return hits
