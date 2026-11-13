"""Raumstatus – was eine Ottilie über sich bekannt gibt (reine Logik). Verteilt über die Zentrale
(`zentrale/link.py`); Bonjour ist seit 13.11.2026 entfallen (Räume nur noch in der Zentrale).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from tonmischung.core import mixstate

FREE, MIXING, IMPORTING, EXPORTING = "frei", "mischt", "importiert", "exportiert"
STATES = (FREE, MIXING, IMPORTING, EXPORTING)


@dataclass(frozen=True)
class PeerStatus:
    room: str
    mixer: str = ""
    state: str = FREE
    title: str = ""
    contribution: str = ""
    pct: int = 0
    mode: str = ""                              # Betriebsart: „helmut_api“ | „uebergabeordner“ (leer: ältere App)


class PeerSource(Protocol):
    """Quelle für den Raumstatus der anderen Räume (die Zentrale)."""

    available: bool

    def peers(self) -> dict[str, PeerStatus]: ...          # Raum → Status der erreichbaren anderen Räume

    def publish(self, status: PeerStatus | None) -> None: ...   # None = nicht (mehr) teilen


def own_status(room: str, mixer: str, owned: Any, job: Mapping | None, mode: str = "") -> PeerStatus:
    """Was dieser Mac über sich bekannt gibt: laufender Auftrag vor übernommenem Beitrag vor „frei“.
    `mode`: Betriebsart (Jens 11.11.2026:
    in der Raumübersicht kenntlich machen, wenn Räume in verschiedenen Betriebsarten laufen)."""
    activity = {"holen": IMPORTING, "wiederoeffnen": IMPORTING, "havarie": IMPORTING, "zurueckliefern": EXPORTING}
    if job and job.get("kind") in activity:
        title = owned.title if owned is not None and owned.id == job.get("contribution") else job.get("title", "")
        return PeerStatus(room, mixer, activity[job["kind"]], title, str(job.get("contribution") or ""),
                          int(max(0.0, min(1.0, float(job.get("fraction") or 0))) * 100), mode=mode)
    if owned is not None and owned.mix.state in mixstate.IN_ROOM:
        return PeerStatus(room, mixer, MIXING, owned.title, owned.id, mode=mode)
    return PeerStatus(room, mixer, mode=mode)
