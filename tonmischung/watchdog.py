"""Watchdog für Ottilie – eigener kleiner Prozess mit Menüleisten-Symbol (wie PunchBuddys watchdog.py).

Hängt das Menü der App, lässt sich Ottilie hier neu starten oder beenden. Anders als PunchBuddy
(sofort SIGKILL) zuerst SIGTERM, damit die App Socket und PTSL-Verbindung sauber schließt; erst wenn
sie nach 5 s noch läuft, SIGKILL. Beendet wird nur die App dieses Benutzers – kein MCP-Server, keine Kommandozeile.

Start: `python3 -m tonmischung watchdog [App-Argumente für den Neustart …]`; die App startet ihn selbst.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable

BUNDLE_EXES = ("Ottilie.app/Contents/MacOS/Ottilie", "Tonmischung.app/Contents/MacOS/Tonmischung")
SOURCE_MODULE = "-m tonmischung"
APP, WATCHDOG = "app", "watchdog"


def role(command: str) -> str | None:
    """Was für ein Ottilie-Prozess ist das? `app`, `watchdog`, ein anderer Befehl (`mcp`, `holen` …) oder None.

    Vorher genügte der Programmpfad des Pakets – „Ottilie neu starten“ beendete so auch den MCP-Server und
    laufende Kommandozeilen-Aufrufe (Review A19). Das Paket ohne Argumente ist die App (`tonmischung_start.py`)."""
    for marker, bundle in [(m, True) for m in BUNDLE_EXES] + [(SOURCE_MODULE, False)]:
        at = command.find(marker)
        if at < 0:
            continue
        rest = command[at + len(marker):]
        if rest and not rest.startswith(" "):
            continue                                 # z. B. „-m tonmischung.irgendwas“
        words = rest.split()
        if not words:
            return APP if bundle else None
        return words[0]
    return None


def own_processes() -> str:
    """`ps` nur für die Prozesse dieses Benutzers – nie die Ottilie eines anderen angemeldeten Benutzers."""
    return subprocess.run(["/bin/ps", "-x", "-U", str(os.getuid()), "-ww", "-o", "pid=,command="],
                          capture_output=True, text=True).stdout


def find_pids(ps_output: str, own_pid: int, wanted: str = APP) -> list[int]:
    pids = []
    for line in ps_output.splitlines():
        pid_text, _, command = line.strip().partition(" ")
        if pid_text.isdigit() and int(pid_text) != own_pid and role(command) == wanted:
            pids.append(int(pid_text))
    return pids


def self_command(*args: str, frozen: bool | None = None, executable: str | None = None) -> list[str]:
    """Aufruf von Ottilie selbst: aus dem Quelltext `python -m tonmischung …`, im Paket (PyInstaller) das
    eigene Programm mit denselben Argumenten (dort gibt es kein `-m`)."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    exe = executable or sys.executable
    return [exe, *args] if frozen else [exe, "-m", "tonmischung", *args]


def find_app_pids(ps_output: str, own_pid: int) -> list[int]:
    """PIDs der laufenden App aus `ps … -o pid=,command=` (weder Watchdog noch MCP-Server oder Kommandozeile)."""
    return find_pids(ps_output, own_pid, APP)


def app_pids() -> list[int]:
    return find_app_pids(own_processes(), os.getpid())


def stop_app(pids: list[int], kill: Callable[[int, int], None] = os.kill,
             alive: Callable[[int], bool] | None = None, wait_s: float = 5.0,
             sleep: Callable[[float], None] = time.sleep) -> list[int]:
    """SIGTERM, bis zu `wait_s` warten, Übrige mit SIGKILL. Rückgabe: per SIGKILL beendete PIDs."""
    def is_alive(pid: int) -> bool:
        try:
            kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
    alive = alive or is_alive
    for pid in pids:
        try:
            kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    waited = 0.0
    while waited < wait_s and any(alive(p) for p in pids):
        sleep(0.25)
        waited += 0.25
    hard = [p for p in pids if alive(p)]
    for pid in hard:
        try:
            kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return hard


def main(restart_args: list[str]) -> int:
    import rumps

    def note(text: str) -> None:
        rumps.notification("Ottilie-Watchdog", "", text)

    def restart(_sender: object) -> None:
        stop_app(app_pids())
        time.sleep(1)
        subprocess.Popen(self_command("app", *restart_args), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        note("Ottilie wird neu gestartet.")

    def beenden(_sender: object) -> None:
        pids = app_pids()
        if not pids:
            return note("Ottilie läuft nicht.")
        hart = stop_app(pids)
        note("Ottilie beendet." + (" (hart beendet – es reagierte nicht)" if hart else ""))

    def status(_sender: object) -> None:
        pids = app_pids()
        note(f"Ottilie läuft (PID {', '.join(map(str, pids))})." if pids else "Ottilie läuft NICHT.")

    app = rumps.App("Ottilie-Watchdog", title="🐕", quit_button="Watchdog beenden")   # Jens 04.10.2026: Hund statt 💀
    app.menu = [rumps.MenuItem("Ottilie neu starten", callback=restart),
                rumps.MenuItem("Ottilie beenden", callback=beenden), None,
                rumps.MenuItem("Status", callback=status)]
    app.run()
    return 0


def start_for_app(app_args: list[str]) -> subprocess.Popen | None:
    """Von der App aufgerufen: alten Watchdog beenden, neuen starten (Neustart mit denselben Argumenten)."""
    for pid in find_pids(own_processes(), os.getpid(), WATCHDOG):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    return subprocess.Popen(self_command("watchdog", *app_args),
                            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
