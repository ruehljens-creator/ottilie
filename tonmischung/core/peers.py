"""Raumstatus direkt zwischen den Macs – was eine App über sich bekannt gibt (reine Logik).

Heute über Bonjour (Jens, 03.10.2026: „ja, bau das so“), später vielleicht über einen eigenen Server: beide
tragen denselben `PeerStatus`. Bonjour-Anmeldung: Dienst `_ottilie._tcp`, Name „Ottilie · <Raum>“,
Inhalt als TXT-Einträge (je höchstens 255 Bytes).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from tonmischung.core import mixstate

SERVICE_TYPE = "_ottilie._tcp."
FORMAT = "1"
FREE, MIXING, IMPORTING, EXPORTING = "frei", "mischt", "importiert", "exportiert"
STATES = (FREE, MIXING, IMPORTING, EXPORTING)
_MAX = 200                                      # Bytes je Wert (TXT erlaubt 255 inkl. Schlüssel)


@dataclass(frozen=True)
class PeerStatus:
    room: str
    mixer: str = ""
    state: str = FREE
    title: str = ""
    contribution: str = ""
    pct: int = 0
    host: str = ""                              # Rechnername (nur beim Lesen)


class PeerSource(Protocol):
    """Quelle für den direkten Raumstatus (Bonjour heute, Server vielleicht später)."""

    available: bool

    def peers(self) -> dict[str, PeerStatus]: ...          # Raum → Status der erreichbaren anderen Räume

    def publish(self, status: PeerStatus | None) -> None: ...   # None = nicht (mehr) teilen


def _cut(text: str) -> bytes:
    data = text.encode("utf-8")
    if len(data) <= _MAX:
        return data
    return data[:_MAX].decode("utf-8", "ignore").encode("utf-8")      # nie mitten im Zeichen abschneiden


def to_txt(s: PeerStatus) -> dict[str, bytes]:
    return {"v": FORMAT.encode(), "raum": _cut(s.room), "mischer": _cut(s.mixer), "zustand": s.state.encode(),
            "beitrag": _cut(s.title), "id": _cut(s.contribution), "pct": str(int(s.pct)).encode()}


def from_txt(txt: Mapping[str, Any], host: str = "") -> PeerStatus | None:
    def get(key: str) -> str:
        v = txt.get(key)
        if v is None:
            return ""
        return bytes(v).decode("utf-8", "replace") if not isinstance(v, str) else v
    if get("v") != FORMAT or not get("raum"):
        return None
    state = get("zustand") if get("zustand") in STATES else FREE
    try:
        pct = max(0, min(100, int(get("pct") or 0)))
    except ValueError:
        pct = 0
    return PeerStatus(get("raum"), get("mischer"), state, get("beitrag"), get("id"), pct, host)


def own_status(room: str, mixer: str, owned: Any, job: Mapping | None) -> PeerStatus:
    """Was dieser Mac über sich bekannt gibt: laufender Auftrag vor übernommenem Beitrag vor „frei“.
    Direkt wird der genaue Fortschritt gezeigt (über Helmut nur in Stufen)."""
    activity = {"holen": IMPORTING, "wiederoeffnen": IMPORTING, "havarie": IMPORTING, "zurueckliefern": EXPORTING}
    if job and job.get("kind") in activity:
        title = owned.title if owned is not None and owned.id == job.get("contribution") else job.get("title", "")
        return PeerStatus(room, mixer, activity[job["kind"]], title, str(job.get("contribution") or ""),
                          int(max(0.0, min(1.0, float(job.get("fraction") or 0))) * 100))
    if owned is not None and owned.mix.state in mixstate.IN_ROOM:
        return PeerStatus(room, mixer, MIXING, owned.title, owned.id)
    return PeerStatus(room, mixer)
