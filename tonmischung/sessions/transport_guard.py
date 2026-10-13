"""Transport-Sperre vor Export und Lautheitskorrektur (Vorgabe Jens, 02.10.2026).

Läuft beim Export noch Wiedergabe oder Aufnahme, kann die Lautheitskorrektur – sie
überschreibt die konsolidierte WAV in der Session – Pro Tools zum Absturz bringen.
Deshalb muss der Transport vor dem Export, vor dem Konsolidieren und unmittelbar vor
jeder Lautheitskorrektur nachweislich stehen.

Wie PunchBuddy `_ensure_transport_stopped`: läuft der Transport, einmal Stopp senden,
gestaffelt auf den bestätigten Stillstand warten, dann kurz Zeit zum Finalisieren der
Dateien geben. Anders als dort wird **abgebrochen**, wenn der Zustand nicht lesbar ist
oder der Stillstand nicht bestätigt wird (PunchBuddy fährt dann „trotzdem fort“).
"""
from __future__ import annotations

import time
from typing import Callable

from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import NoSessionError, PtslError, TransportBusyError

STOPPED = ("TS_TransportStopped", "TState_TransportStopped")
# Zustände, in denen ein Stopp gesendet wird. „IsStopping“ fehlt bewusst: Pro Tools hält
# dort schon selbst an, ein Umschalten würde wieder starten (wie PunchBuddy).
RUNNING = ("TS_TransportRecording", "TS_TransportPlaying", "TS_TransportIsCued",
           "TS_TransportIsCuedForPreview")
STATE_TEXT = {"TS_TransportRecording": "Aufnahme läuft", "TS_TransportPlaying": "Wiedergabe läuft",
              "TS_TransportIsCued": "Wiedergabe vorbereitet", "TS_TransportIsCuedForPreview": "Vorhören vorbereitet",
              "TS_TransportIsStopping": "hält gerade an"}
# Gestaffelte Kontrollen wie PunchBuddy (wenige PTSL-Aufrufe); Pro Tools bestätigt im Studio
# nach ~2,6 s, mit NEXIS auch später – insgesamt 8 s.
CHECKS_S = (0.5, 1.0, 1.5, 2.0, 3.0)
SETTLE_S = 2.0


def state_text(state: str) -> str:
    return STATE_TEXT.get(state, state)


def ensure_stopped(ptsl: PtslClient, before: str, *, checks_s: tuple[float, ...] = CHECKS_S,
                   settle_s: float = SETTLE_S, sleep: Callable[[float], None] | None = None,
                   log: Callable[[str], None] = lambda m: None) -> bool:
    """Stellt sicher, dass Pro Tools weder abspielt noch aufnimmt; sonst TransportBusyError.

    `before` nennt den folgenden Schritt („Export“, „Lautheit ST“) für die Meldung.
    Ergebnis: True, wenn erst angehalten werden musste.
    """
    sleep = sleep or time.sleep
    try:
        state = ptsl.transport_state()
    except NoSessionError as e:
        raise TransportBusyError(f"{before}: keine Session offen") from e
    except PtslError as e:
        raise TransportBusyError(f"{before} abgebrochen: Transportzustand von Pro Tools nicht lesbar ({e})") from e
    if state in STOPPED:
        return False
    if state in RUNNING:
        log(f"{before}: {state_text(state)} – Pro Tools wird angehalten")
        ptsl.call("Transport anhalten", lambda e: e.toggle_play_state(), timeout_s=10.0)
    for pause in checks_s:
        sleep(pause)
        try:
            state = ptsl.transport_state()
        except PtslError:
            continue                         # unter Last kurz keine Antwort – weiter prüfen
        if state in STOPPED:
            log(f"{before}: Pro Tools steht – {settle_s:.0f} s zum Finalisieren der Dateien")
            sleep(settle_s)
            return True
    raise TransportBusyError(
        f"{before} abgebrochen: Pro Tools steht nicht ({state_text(state)}). Wiedergabe bzw. "
        "Aufnahme anhalten und erneut starten – mit laufendem Transport würde die "
        "Lautheitskorrektur Pro Tools gefährden.")
