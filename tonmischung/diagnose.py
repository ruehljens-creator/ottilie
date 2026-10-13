"""Diagnosebericht – eine Textdatei für die Fehlersuche (wie PunchBuddys collect_diagnostics.py).

Lehren aus der produktiven PunchBuddy-Linie:
- vollständiges Protokoll der App, nicht nur die letzten Zeilen (§12: erst den Fehlerzeitraum haben);
- PTSL-Antwortzeiten mehrfach messen – sie sind zweigeteilt (5–15 ms oder 300–1400 ms, §7);
- worauf die Satellite-Ports 28282/28284 der Video Engine binden (Loopback gut, geroutet schlecht, §1);
- Netzwerk-Reihenfolge, Prozesse nach CPU (Defender, Time Machine), App Nap (§1b, §9, §10).
Jeder Abschnitt hat ein Zeitlimit und bricht den Bericht nie ab. Der Bericht enthält Pfade, Session-
und Spurnamen – nur an Jens bzw. die IT weitergeben.
"""
from __future__ import annotations

import glob
import os
import platform
import socket
import statistics
import subprocess
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

Run = Callable[[list[str]], str]


def run_cmd(cmd: list[str], timeout: float = 15.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (out.stdout + (out.stderr if out.returncode else "")).rstrip()
    except (OSError, subprocess.SubprocessError) as e:
        return f"(nicht ausführbar: {e})"


def _tail_text(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()                                   # angeschnittene Zeile verwerfen
        return f.read().decode("utf-8", "replace")


# ── Abschnitte ─────────────────────────────────────────────────────────────
def head(version: str, run: Run) -> str:
    return "\n".join([f"Erstellt: {datetime.now():%Y-%m-%d %H:%M:%S}", f"Ottilie: {version}",
                      f"Rechner: {socket.gethostname()}", f"macOS: {run(['/usr/bin/sw_vers', '-productVersion'])}",
                      f"Architektur: {platform.machine()}", f"Python: {platform.python_version()}"])


def settings_section(settings: Any) -> str:
    lines = []
    for path, (value, source) in settings.snapshot().items():
        lines.append(f"{path} = {value!r}  [{source}]")
    return "\n".join(lines) or "(keine)"


def pro_tools(ptsl: Any, run: Run, samples: int = 10) -> str:
    lines = [f"Prozess: {run(['/usr/bin/pgrep', '-lf', 'Pro Tools.app/Contents/MacOS/Pro Tools']) or 'läuft nicht'}",
             f"Version: {run(['/usr/bin/defaults', 'read', '/Applications/Pro Tools.app/Contents/Info', 'CFBundleShortVersionString'])}"]  # noqa: E501
    if ptsl is None:
        return "\n".join(lines)
    try:
        lines.append(f"PTSL-Version: {ptsl.version()}")
        s = ptsl.open_session()
        lines.append(f"Offene Session: {s.name if s else '–'} ({s.path if s else ''})")
        times = []
        for _ in range(samples):
            t0 = time.perf_counter()
            state = ptsl.transport_state()
            times.append((time.perf_counter() - t0) * 1000)
        lines.append(f"Transport: {state}")
        lines.append(f"PTSL-Antwortzeit transport_state ({samples}×): min {min(times):.0f} ms, Median "
                     f"{statistics.median(times):.0f} ms, max {max(times):.0f} ms; langsam (>300 ms): "
                     f"{sum(t > 300 for t in times)}")
    except Exception as e:  # noqa: BLE001 – Befund statt Abbruch
        lines.append(f"PTSL: {e}")
    return "\n".join(lines)


def triggers(settings: Any) -> str:
    port = settings.get("system.streamdeck_webtrigger.port") or 8899
    sock = settings.get("system.streamdeck_webtrigger.unix_socket_pfad") or "/tmp/punchbuddy.sock"
    lines = []
    try:
        t0 = time.perf_counter()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/command/ping", timeout=3) as r:
            body = r.read().decode()
        lines.append(f"Webtrigger http://127.0.0.1:{port}/command/ping → {r.status} {body} "
                     f"({(time.perf_counter() - t0) * 1000:.1f} ms)")
    except Exception as e:  # noqa: BLE001
        lines.append(f"Webtrigger Port {port}: {e}")
    try:
        t0 = time.perf_counter()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(sock)
            s.sendall(b"ping\n")
            answer = s.recv(200).decode().strip()
        lines.append(f"Stream-Deck-Socket {sock} → {answer} ({(time.perf_counter() - t0) * 1000:.1f} ms)")
    except OSError as e:
        lines.append(f"Stream-Deck-Socket {sock}: {e}")
    return "\n".join(lines)


def system(run: Run) -> list[tuple[str, Callable[[], str]]]:
    """Abschnitte erst beim Schreiben ausführen – ein Fehler trifft nur seinen Abschnitt."""
    return [
        ("Ports (PTSL 31416, Webtrigger 8899, Video-Satellite 28282/28284)",
         lambda: run(["/usr/sbin/lsof", "-nP", "-iTCP:31416", "-iTCP:8899", "-iTCP:28282",
                      "-iTCP:28284"]) or "(keine)"),
        ("Netzwerk – Reihenfolge der Dienste", lambda: run(["/usr/sbin/networksetup", "-listnetworkserviceorder"])),
        ("Netzwerk – Routen", lambda: "\n".join(run(["/usr/sbin/netstat", "-rn", "-f", "inet"]).splitlines()[:25])),
        ("Prozesse nach CPU", lambda: "\n".join(run(["/bin/ps", "-axo", "pcpu,pmem,comm", "-r"]).splitlines()[:25])),
        ("App Nap (NSAppSleepDisabled)", lambda: "\n".join(
            f"{bid}: {run(['/usr/bin/defaults', 'read', bid, 'NSAppSleepDisabled']) or '–'}"
            for bid in ("com.elgato.StreamDeck", "com.avid.ProTools"))),
        ("Stream Deck", lambda: run(["/usr/bin/pgrep", "-lf", "Stream Deck"]) or "läuft nicht"),
        ("PunchBuddy (soll auf dem Mischplatz nicht mehr laufen)",
         lambda: run(["/usr/bin/pgrep", "-lf", "PunchBuddy.app/Contents/MacOS/PunchBuddy"]) or "läuft nicht"),
    ]


def vocaster() -> str:
    try:
        from tonmischung.vendor import import_isolated
        info = import_isolated("vocaster_integration").VocasterController.detect()
        return str(info) if info else "kein Vocaster erkannt"
    except Exception as e:  # noqa: BLE001
        return f"Erkennung nicht möglich: {e}"


def crashes(days: int = 7) -> str:
    since = time.time() - days * 86400
    hits = []
    for folder in (Path.home() / "Library/Logs/DiagnosticReports", Path("/Library/Logs/DiagnosticReports")):
        for p in sorted(folder.glob("*")) if folder.exists() else []:
            name = p.name
            if any(k in name for k in ("Pro Tools", "Python", "Ottilie", "Tonmischung", "AvidVideoEngine")):
                try:
                    if p.stat().st_mtime >= since:
                        hits.append(f"{datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}  {p}")
                except OSError:
                    pass
    return "\n".join(hits) or f"keine in den letzten {days} Tagen"


def pro_tools_log(max_bytes: int = 2_000_000, tail_lines: int = 300) -> str:
    logs = sorted(glob.glob(os.path.expanduser("~/Library/Logs/Avid/Pro_Tools_*.txt")), key=os.path.getmtime)
    if not logs:
        return "kein Pro-Tools-Protokoll gefunden"
    path = Path(logs[-1])
    text = _tail_text(path, max_bytes)                      # Pro-Tools-Protokolle können GB groß werden
    markers = {m: text.count(m) for m in ("SLnk_Manager::HandleNetworkError", "IsAcquired - false",
                                          "waitingtrigger", "Access Violation", "PTSL")}
    lines = text.splitlines()[-tail_lines:]
    return (f"{path} ({path.stat().st_size / 1e6:.1f} MB; Zähler in den letzten {max_bytes / 1e6:.0f} MB: "
            + ", ".join(f"{k}: {v}" for k, v in markers.items()) + ")\n\n" + "\n".join(lines))


def app_log() -> str:
    from tonmischung.app import logfile
    parts = []
    for p in sorted(logfile.log_dir().glob("Ottilie.log*")) + [logfile.log_dir() / "Ottilie_crash.log"]:
        if p.exists() and p.stat().st_size:
            parts.append(f"── {p} ──\n" + p.read_text(encoding="utf-8", errors="replace"))
    return logfile.redact("\n\n".join(parts)) or "(leer)"          # auch ältere Protokolle ohne Schlüssel


# ── Bericht ────────────────────────────────────────────────────────────────
def report(settings: Any, version: str, ptsl: Any = None, run: Run = run_cmd) -> str:
    sections: list[tuple[str, Callable[[], str]]] = [
        ("Übersicht", lambda: head(version, run)),
        ("Pro Tools", lambda: pro_tools(ptsl, run)),
        ("Webtrigger und Stream-Deck-Socket (Selbsttest)", lambda: triggers(settings)),
        *system(run),
        ("Focusrite Vocaster", vocaster),
        ("Abstürze (Diagnoseberichte)", crashes),
        ("Einstellungen (Wert und Herkunft; Zugangsdaten liegen nur im Schlüsselbund)",
         lambda: settings_section(settings)),
        ("Pro-Tools-Protokoll (Ende)", pro_tools_log),
        ("Ottilie-Protokoll (vollständig)", app_log),
    ]
    out = ["OTTILIE – DIAGNOSEBERICHT",
           "Enthält Pfade, Session- und Spurnamen – nur an Jens bzw. die IT weitergeben.", ""]
    for i, (title, fn) in enumerate(sections, start=1):
        try:
            body = fn()
        except Exception as e:  # noqa: BLE001 – ein Abschnitt darf den Bericht nicht verhindern
            body = f"(Abschnitt fehlgeschlagen: {e})"
        out += [f"{'=' * 78}", f"{i}. {title}", "=" * 78, body, ""]
    return "\n".join(out)


def write_report(settings: Any, version: str, ptsl: Any = None, folder: Path | None = None) -> Path:
    folder = folder or (Path.home() / "Desktop")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"Ottilie_Diagnose_{datetime.now():%Y%m%d-%H%M%S}.txt"
    path.write_text(report(settings, version, ptsl), encoding="utf-8")
    return path
