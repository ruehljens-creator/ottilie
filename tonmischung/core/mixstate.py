"""Zustand der Mischung am Helmut-Projekt (Metadaten-Set „Mischung“) – reine Logik.

Helmut kennt am Projekt nur LOCKED/UNLOCKED; alles Weitere steht im Set „Mischung“
(Auftrag Abschnitt 4). Feldnamen im Set sind deutsch (Daten), hier englisch (Code).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

# Zustände laut Auftrag Abschnitt 4 (Werte im Metadaten-Set)
READY = "bereit"
TAKEN = "uebernommen"
IMPORTED = "importiert"
MIXING = "in_mischung"
EXPORTED = "exportiert"            # Altbestand: bis 09.10.2026 der Zustand nach dem Zurückliefern (jetzt DELIVERED)
DELIVERED = "ausgeliefert"
RETURNED = "zurueckgegeben"
STATES = (READY, TAKEN, IMPORTED, MIXING, EXPORTED, DELIVERED, RETURNED)

# Anzeige der Zustände (Oberfläche, Meldungen)
STATE_LABEL = {READY: "bereit für Mischung", RETURNED: "zurückgegeben", DELIVERED: "ausgeliefert",
               TAKEN: "übernommen", IMPORTED: "importiert", MIXING: "in Mischung", EXPORTED: "zurückgeliefert"}

# Zustände, in denen ein Beitrag einem Mischer gehört
OWNED = (TAKEN, IMPORTED, MIXING, EXPORTED)
# Zustände, in denen ein Beitrag seinen Raum belegt (Raumstatus). Jens 04.10.2026: „Nach dem Export gilt der Beitrag
# als abgeschlossen und der Raum wird einfach frei angezeigt.“
IN_ROOM = (TAKEN, IMPORTED, MIXING)
# Zustände, aus denen ein Beitrag übernommen werden darf
TAKEABLE = (READY, RETURNED, DELIVERED)

# Freigeben: was beim nächsten Übernehmen geschieht (Funktionsbeschreibung S. 27)
KEEP_LAST = "letzter_stand"
KEEP_NEW = "neu_importieren"
KEEP_LABEL = {KEEP_LAST: "Letzten gespeicherten Stand öffnen", KEEP_NEW: "Neu importieren"}
RELEASE_REASONS = ("Workstation abgestürzt", "Raumwechsel", "Versehentlich übernommen")

# Ablage der Session (Plan M7-Ablage); `core.storage` plant damit den Ort
HELMUT_STORAGE, LOCAL, MIRROR = "helmut_storage", "lokal", "lokal_spiegelung"


class ConflictError(Exception):
    """Beitrag ist inzwischen anders zugewiesen – melden, nicht überschreiben."""


@dataclass(frozen=True)
class MixState:
    state: str = READY
    mixer: str = ""
    room: str = ""
    progress: int = 0
    session_path: str = ""
    session_storage: str = ""
    taken_at: str = ""
    return_reason: str = ""
    returned_at: str = ""
    returned_from_room: str = ""
    continued_in: str = ""
    return_keep: str = ""           # beim nächsten Öffnen: KEEP_LAST | KEEP_NEW
    duplicate_of: str = ""          # Duplikat: Titel (Projekt-ID) des Originals
    duplicate_session: str = ""     # Duplikat mit Mischstand: Session des Originals
    session_mirror: str = ""        # „lokal mit Spiegelung“: Spiegelordner im Projekt (Plan M7-Ablage)
    activity: str = ""              # Raumstatus: „import“ | „export“ während eines Auftrags, sonst leer
    template: str = ""              # „Gruppe/Name“ des Templates, aus dem die Session angelegt wurde (Spurprofil)


def owned_by(current: MixState, mixer: str, room: str) -> bool:
    """Gehört der Beitrag `mixer` in `room` (übernommen, importiert, in Mischung oder – Altbestand – exportiert)?"""
    return current.state in OWNED and current.mixer == mixer and current.room == room


def delivered_by(current: MixState, mixer: str, room: str) -> bool:
    """Hat `mixer` in `room` den Beitrag ausgeliefert, und hat ihn seitdem niemand übernommen?"""
    return current.state == DELIVERED and current.mixer == mixer and current.room == room


def may_deliver(current: MixState, mixer: str, room: str) -> bool:
    """Zurückliefern darf, wem der Beitrag gehört – und erneut, wer ihn ausgeliefert hat (Jens 09.10.2026:
    Zurückliefern setzt „ausgeliefert“ und gibt den Beitrag frei; bis ihn jemand übernimmt, bleibt Korrigieren und
    erneutes Zurückliefern dem Mischer)."""
    return owned_by(current, mixer, room) or delivered_by(current, mixer, room)


def after_delivery(current: MixState) -> MixState:
    """Zurückgeliefert: „ausgeliefert“; Mischer, Raum und Session bleiben stehen (wer, wo), Tätigkeit des
    Raumstatus endet – sonst hinge „exportiert“ am Beitrag, bis ihn jemand neu übernimmt."""
    return replace(current, state=DELIVERED, progress=100, activity="")


def check_take(current: MixState, locked: bool, mixer: str, room: str) -> None:
    """Darf `mixer` in `room` den Beitrag übernehmen? Wirft ConflictError, sonst nichts.

    Gehört er schon genau diesem Mischer in diesem Raum, ist das kein Konflikt
    (erneutes Übernehmen, z. B. nach Neustart der App).
    """
    if owned_by(current, mixer, room):
        return
    if current.state in OWNED or locked:
        who = f"{current.mixer or 'jemand'} ({current.room or 'Raum unbekannt'})"
        raise ConflictError(f"Beitrag ist bereits übernommen von {who}, Zustand „{current.state}“")
    if current.state not in TAKEABLE:
        raise ConflictError(f"Beitrag kann im Zustand „{current.state}“ nicht übernommen werden")


def after_take(current: MixState, mixer: str, room: str, now: str) -> MixState:
    if owned_by(current, mixer, room):
        return current
    # Die Rückgabe-Angaben bleiben bis zum Ende des Holens stehen (Kennzeichen „zurückgegeben“
    # bis zum nächsten Öffnen); `with_state` räumt sie beim Zustand „importiert“ ab.
    return replace(current, state=TAKEN, mixer=mixer, room=room, progress=0, taken_at=now)


def keep_options(current: MixState, here: bool) -> list[str]:
    """Was „Beim nächsten Öffnen“ angeboten wird.

    Den letzten gespeicherten Stand gibt es nur mit Session-Pfad; liegt die Session nur lokal
    auf einem anderen Mac, ist sie von hier nicht erreichbar → nur „Neu importieren“.
    """
    if not current.session_path:
        return [KEEP_NEW]
    if current.session_storage == LOCAL and not here:
        return [KEEP_NEW]
    if current.session_storage == MIRROR and not here and not current.session_mirror:
        return [KEEP_NEW]                         # anderswo nur über den Spiegel erreichbar
    return [KEEP_LAST, KEEP_NEW]


def after_release(current: MixState, reason: str, now: str, keep: str = KEEP_NEW) -> MixState:
    """Übernahme aufheben: Mischer, Raum, Zustand zurücksetzen, Grund, Herkunft und Wunsch fürs
    nächste Öffnen festhalten. Der Session-Pfad bleibt („letzten gespeicherten Stand öffnen“)."""
    if current.state not in OWNED:
        raise ConflictError(f"Beitrag ist nicht übernommen (Zustand „{current.state}“)")
    if keep not in (KEEP_LAST, KEEP_NEW):
        raise ValueError(f"Unbekannte Wahl fürs nächste Öffnen: {keep}")
    return replace(current, state=RETURNED, mixer="", room="", progress=0, return_reason=reason,
                   returned_at=now, returned_from_room=current.room, return_keep=keep)


DUPLICATE_PURPOSES = ("Zweitfassung", "Kurzfassung", "Web / Social", "Ersatz")


def duplicate_title(title: str, purpose: str) -> str:
    """Titelvorschlag fürs Duplikat (frei änderbar): „Titel – Zweck“."""
    return f"{title} – {purpose}" if purpose else title


def for_duplicate(original_title: str, original_id: str, original: MixState, keep_mix: bool) -> MixState:
    """Set „Mischung“ des Duplikats: frei zur Mischung, mit Herkunft; mit Mischstand die Session des
    Originals als Vorlage (kopiert wird beim Übernehmen)."""
    if keep_mix and not original.session_path:
        raise ConflictError("Das Original hat keine gespeicherte Session – Mischstand nicht übernehmbar")
    return MixState(state=READY, duplicate_of=f"{original_title} ({original_id})",
                    duplicate_session=original.session_path if keep_mix else "")


def opened(current: MixState) -> MixState:
    """Nach dem Öffnen (Import oder Stand als Kopie): Rückgabe abgeschlossen, Kennzeichen weg."""
    return replace(current, return_reason="", returned_at="", returned_from_room="", return_keep="")


def with_state(current: MixState, state: str, **changes) -> MixState:
    if state not in STATES:
        raise ValueError(f"Unbekannter Zustand: {state}")
    if state == IMPORTED:                    # geöffnet → Kennzeichen „zurückgegeben“ entfällt
        current = opened(current)
    return replace(current, state=state, **changes)
