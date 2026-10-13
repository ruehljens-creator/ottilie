"""Start und Ende der App: App Nap aus, sauberes Beenden auch bei SIGTERM/SIGINT.

Beides aus der produktiven PunchBuddy-Linie (Erkenntnisse §10 und
Fehlerbehebung D):
- App Nap drosselt Hintergrund-Apps. Im Betrieb liegt Pro Tools vorn, die App
  hinten – Timer, Socket und Auftrags-Thread wurden dadurch ausgebremst. Die
  NSProcessInfo-Activity nimmt die App selbst davon aus; der Mac darf weiter in
  den Ruhezustand.
- Ein Beenden per Signal (Watchdog, `kill`) ließ die PTSL-Verbindung offen; auf
  Pro-Tools-Seite sammelten sich tote Verbindungen (CLOSE_WAIT).
"""
from __future__ import annotations

import atexit
import logging
import signal
import subprocess
import sys
from typing import Any, Callable

log = logging.getLogger("tonmischung.app")

# Apple-Konstanten, falls PyObjC sie nicht kennt: UserInitiatedAllowingIdleSystemSleep | LatencyCritical
_FALLBACK_OPTIONS = (0x00FFFFFF & ~(1 << 20)) | 0xFF00000000


def prevent_app_nap(reason: str = "Ottilie steuert Pro Tools (Socket, Webtrigger, PTSL)") -> Any:
    """Activity beginnen; das Ergebnis muss gehalten werden, solange die App läuft."""
    try:
        from Foundation import NSProcessInfo
        try:
            from Foundation import NSActivityLatencyCritical, NSActivityUserInitiatedAllowingIdleSystemSleep
            options = NSActivityUserInitiatedAllowingIdleSystemSleep | NSActivityLatencyCritical
        except ImportError:
            options = _FALLBACK_OPTIONS
        token = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(options, reason)
        log.info("App Nap aus (NSProcessInfo-Activity)")
        return token
    except Exception as e:  # noqa: BLE001 – ohne Activity läuft die App trotzdem
        log.warning("App Nap ließ sich nicht abschalten: %s", e)
        return None


QUIT_KEEP, QUIT_ANYWAY = "Weiterlaufen lassen", "Trotzdem beenden"


def quit_question(running: str | None) -> tuple[str, str] | None:
    """Rückfrage beim Beenden (Menüleiste, ⌘Q), solange ein Auftrag läuft – vorher endete z. B. eine
    Lautheitskorrektur mittendrin ohne Nachfrage (Review A16). `None`: ohne Rückfrage beenden.
    Vorgewählt (Return) ist „Weiterlaufen lassen“."""
    if not running:
        return None
    return ("Ein Auftrag läuft noch",
            f"„{running}“ ist noch nicht fertig. Beenden bricht ihn mittendrin ab – Pro Tools und die Dateien "
            "bleiben dann womöglich in einem Zwischenstand.")


def install_shutdown(cleanup: Callable[[], None],
                     signals: tuple[int, ...] = (signal.SIGTERM, signal.SIGINT),
                     exit: Callable[[], None] = lambda: sys.exit(0),
                     on_signal_first: Callable[[], None] = lambda: None) -> Callable[[], None]:
    """`cleanup` läuft genau einmal: beim normalen Ende (atexit) und bei SIGTERM/SIGINT.

    `exit` beendet nach dem Signal. In der App ist das `rumps.quit_application`: ein
    `sys.exit` aus einem AppKit-Rückruf verschluckt PyObjC, der Prozess liefe weiter
    (live 02.10.2026). Gibt die einmalige Aufräumfunktion zurück (auch für `finally`).
    """
    done = {"ran": False}

    def once() -> None:
        if done["ran"]:
            return
        done["ran"] = True
        try:
            cleanup()
        except Exception as e:  # noqa: BLE001 – Aufräumen darf das Beenden nicht verhindern
            log.warning("Aufräumen beim Beenden: %s", e)

    def on_signal(signum: int, _frame: Any) -> None:
        log.info("Signal %s – Verbindungen schließen, Ende", signum)
        on_signal_first()
        once()
        exit()

    atexit.register(once)
    for s in signals:
        signal.signal(s, on_signal)
    return once


PARTNER_APPS = ("com.elgato.StreamDeck", "com.avid.ProTools")


def partner_app_nap_off(run: Callable[..., Any] | None = None) -> list[str]:
    """Stream Deck und Pro Tools vom App Nap ausnehmen (wirkt ab deren nächstem Start), wie PunchBuddy:
    liegt Pro Tools vorn, drosselte macOS die Stream-Deck-App – Tasten kamen Sekunden zu spät (PunchBuddy §10).
    Nur setzen, wenn noch nicht gesetzt. Rückgabe: Apps, für die es neu gesetzt wurde."""
    run = run or subprocess.run
    changed = []
    for bid in PARTNER_APPS:
        try:
            cur = run(["/usr/bin/defaults", "read", bid, "NSAppSleepDisabled"], capture_output=True, text=True,
                      timeout=5).stdout.strip()
            if cur != "1":
                run(["/usr/bin/defaults", "write", bid, "NSAppSleepDisabled", "-bool", "YES"],
                    capture_output=True, timeout=5)
                changed.append(bid)
                log.info("App-Nap-Ausnahme gesetzt für %s (wirkt ab dessen Neustart)", bid)
        except Exception as e:  # noqa: BLE001 – kein Grund, den Start abzubrechen
            log.warning("App-Nap-Ausnahme für %s: %s", bid, e)
    return changed
