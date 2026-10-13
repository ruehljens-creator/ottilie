"""Match Tracks: fehlende Template-Spuren erkennen und Entscheidungen prüfen – reine Logik.

Fehlt eine AAF-Spur im Template, würde Pro Tools deren Clips still verwerfen. Wir
brechen nicht ab und verwerfen nichts still, sondern fragen (Vorgabe Jens):
  ab A11     – auf die nächste freie Template-Spur ab A11 (keine neue Spur anlegen,
               Jens 02.10.2026); technisch wie „zuordnen“
  zuordnen   – eine vorhandene Template-Spur für den Import auf den AAF-Namen
               umbenennen und danach zurück
  weglassen  – bewusst nicht importieren (steht im Bericht)
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

FROM_A11, MAP, SKIP = "ab A11", "zuordnen", "weglassen"


@dataclass(frozen=True)
class TrackDecision:
    action: str
    target: str = ""          # nur bei „zuordnen“: vorhandene Template-Spur


def missing_tracks(aaf_tracks: list[str] | tuple[str, ...], session_tracks: set[str]) -> list[str]:
    return [t for t in aaf_tracks if t not in session_tracks]


def check_decisions(missing: list[str], decisions: dict[str, TrackDecision],
                    session_tracks: set[str], aaf_tracks: list[str] | tuple[str, ...]) -> list[str]:
    """Fehler in den Entscheidungen; leer = alles in Ordnung."""
    errors = []
    for t in missing:
        if t not in decisions:
            errors.append(f"Keine Entscheidung für {t}")
    targets = [d.target for d in decisions.values() if d.action == MAP]
    for t, d in decisions.items():
        if d.action not in (FROM_A11, MAP, SKIP):
            errors.append(f"{t}: unbekannte Entscheidung „{d.action}“")
        elif d.action == MAP:
            if d.target not in session_tracks:
                errors.append(f"{t}: Zielspur „{d.target}“ gibt es nicht")
            elif d.target in aaf_tracks:
                errors.append(f"{t}: „{d.target}“ bekommt schon Clips aus der AAF")
            elif targets.count(d.target) > 1:
                errors.append(f"„{d.target}“ ist mehreren AAF-Spuren zugeordnet")
    return errors


def parse_choice(text: str) -> dict[str, TrackDecision] | TrackDecision:
    """Kommandozeile: „ab A11“, „weglassen“ (für alle) oder „Audio 1=A5,Audio 2=ab A11“."""
    text = text.strip()
    if _keyword(text):
        return TrackDecision(_keyword(text))
    result = {}
    for part in text.split(","):
        if "=" not in part:
            raise ValueError(f"„{part}“: erwartet Name=Zielspur, „ab A11“ oder „weglassen“")
        name, target = (x.strip() for x in part.split("=", 1))
        result[name] = TrackDecision(_keyword(target)) if _keyword(target) else TrackDecision(MAP, target)
    return result


def _keyword(text: str) -> str | None:
    t = re.sub(r"\s+", " ", text.strip().lower())
    if t in ("ab a11", "a11", "ab11"):
        return FROM_A11
    if t == SKIP:
        return SKIP
    return None


def assign_from_a11(decisions: dict[str, TrackDecision], session_tracks: Sequence[str],
                    occupied: set[str]) -> dict[str, TrackDecision]:
    """„ab A11“ auflösen: je AAF-Spur die nächste freie Template-Spur A11, A12, … (zuordnen).

    Frei = im Template vorhanden, ohne Clips und nicht schon Ziel. Reicht es nicht,
    ValueError – es wird keine Spur angelegt (Jens 02.10.2026).
    """
    numbered = sorted((int(m.group(1)), t) for t in session_tracks
                      if (m := re.fullmatch(r"A(\d+)", t)) and int(m.group(1)) >= 11)
    taken = set(occupied) | {d.target for d in decisions.values() if d.action == MAP}
    free = [t for _, t in numbered if t not in taken]
    wanted = [t for t, d in decisions.items() if d.action == FROM_A11]
    if len(wanted) > len(free):
        raise ValueError(f"Nicht genug freie Spuren ab A11 im Template: {len(wanted)} gebraucht, "
                         f"{len(free)} frei ({', '.join(free) or 'keine'})")
    result = dict(decisions)
    for track, target in zip(wanted, free):
        result[track] = TrackDecision(MAP, target)
    return result
