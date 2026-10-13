"""Befehle der PunchBuddy-Funktionen – Namen, Aliase und Antworten wie PunchBuddys `command_dispatch`.

Ein Eintrittspunkt für Menüleiste, Fenster, JSON-Socket, MCP und (Schritt 3) Zeilen-Socket und
Webtrigger: `dispatch(befehl, argumente) → (ok, meldung)`. Wie in PunchBuddy:
- Pro-Tools-Befehle laufen im Hintergrund, die Antwort kommt sofort („record_a queued“);
- Entprellen je Befehl gegen Stream-Deck-Salven (0,4 s; Presets 0,6 s; Exporte 1,0 s);
- Schutzfenster nach Start/Stopp sitzt im Kern (`transport._mark_transport_start` …).
Neu in Ottilie: Während ein Auftrag (Holen, Zurückliefern) Pro Tools belegt, werden Befehle, die
Pro Tools anfassen, abgelehnt (Einstellung `system.streamdeck_webtrigger.ablehnen_wenn_belegt`).
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from tonmischung.config.settings import Settings
from tonmischung.punch.core import PunchCore
from tonmischung.punch.settings_bridge import apply_preset

log = logging.getLogger("tonmischung.punch")

ALIASES = {"trigger": "record_a", "trigger2": "record_b", "a": "record_a", "b": "record_b",
           "rec_a": "record_a", "rec_b": "record_b", "start": "goto_start", "goto": "goto_start",
           "move": "move_audio", "export_aaf_embedded": "export_aaf"}
DEBOUNCE_S = 0.4
DEBOUNCE_PRESET_S = 0.6
BUSY_SETTING = "system.streamdeck_webtrigger.ablehnen_wenn_belegt"
# Befehle, die Pro Tools anfassen (bei Belegung abgelehnt)
PRO_TOOLS = {"record_a", "record_b", "play", "play_custom", "goto_start", "move_audio"}
TITLES = {"record_a": "Record A starten", "record_b": "Record B starten", "play": "Play Input",
          "play_custom": "Play Custom", "goto_start": "Zum Programmbeginn", "move_audio": "Audio verschieben"}


FORMAT_LABEL = {"TF_Mono": "Mono", "TF_Stereo": "Stereo"}


def move_check(src: list[str], dst: list[str], states: dict[str, Any], formats: dict[str, str],
               clips: dict[str, list[tuple[int, int, str]]]) -> tuple[list[str], list[str]]:
    """Vor dem Verschieben (rein): (Probleme, einzublendende Spuren). Ein Problem verhindert das Ausschneiden – nichts
    geht verloren: Spur fehlt, Kanalformate verschieden, auf dem Ziel liegt im selben Bereich schon Material (Einfügen
    würde es ersetzen)."""
    problems = [f"Spur „{n}“ gibt es in der Session nicht" for n in dict.fromkeys(src + dst) if n not in states]
    for s, t in zip(src, dst):
        if s in formats and t in formats and formats[s] != formats[t]:
            problems.append(f"„{s}“ ({FORMAT_LABEL.get(formats[s], formats[s])}) und „{t}“ "
                            f"({FORMAT_LABEL.get(formats[t], formats[t])}) haben verschiedene Kanalformate")
        busy = [c for c in clips.get(t, []) for a in clips.get(s, []) if c[0] < a[1] and a[0] < c[1]]
        if busy:
            problems.append(f"auf „{t}“ liegt im selben Bereich schon Material ({busy[0][2]})")
    hidden = [n for n in dict.fromkeys(src + dst) if n in states and getattr(states[n], "hidden", False)]
    return problems, hidden


class Debouncer:
    """Gleicher Befehl innerhalb des Intervalls → verworfen (thread-sicher)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def ok(self, key: str, interval: float) -> bool:
        now = self._clock()
        with self._lock:
            if now - self._last.get(key, -1e9) < interval:
                log.info("Befehl „%s“ entprellt (%.2f s < %.1f s)", key, now - self._last[key], interval)
                return False
            self._last[key] = now
            return True


def _spawn(fn: Callable[..., Any], *args: Any) -> None:
    threading.Thread(target=fn, args=args, daemon=True, name="tm-punch").start()


class PunchCommands:
    """`busy()` nennt den laufenden Pro-Tools-Auftrag von Ottilie oder None."""

    def __init__(self, core: PunchCore, settings: Settings, *,
                 busy: Callable[[], str | None] = lambda: None,
                 spawn: Callable[..., None] = _spawn, clock: Callable[[], float] = time.monotonic,
                 info: Callable[[], dict] = lambda: {}) -> None:
        self.core = core
        self.settings = settings
        self.busy = busy
        self.spawn = spawn
        self.info = info
        self.debounce = Debouncer(clock)
        self.pro_tools = set(PRO_TOOLS)
        self.table: dict[str, Callable[[list[str]], tuple[bool, str]]] = {
            "record_a": lambda a: self._record("record_a", "aufnahme.spuren.rec_a", "aufnahme.spuren.monitor_a"),
            "record_b": lambda a: self._record("record_b", "aufnahme.spuren.rec_b", "aufnahme.spuren.monitor_b"),
            "play": lambda a: self._run("play", "run_play"),
            "play_custom": lambda a: self._run("play_custom", "run_play_custom"),
            "goto_start": lambda a: self._run("goto_start", "run_goto_start"),
            "move_audio": lambda a: self._move_audio(),
            "preset": self._preset,
            "ping": lambda a: (True, "pong"),
            "status": self._status,
            "list": lambda a: (True, ",".join(sorted(self.table))),
        }

    def add(self, name: str, handler: Callable[[list[str]], tuple[bool, str]], pro_tools: bool = True) -> None:
        """Weiteren Befehl aufnehmen (z. B. „holen“ aus der App, Exporte, Vocaster)."""
        self.table[name] = handler
        if pro_tools:
            self.pro_tools.add(name)

    # ── Eintrittspunkt ──────────────────────────────────────────────────────
    def dispatch(self, command: str, args: list[str] | None = None) -> tuple[bool, str]:
        raw = (command or "").strip()
        cmd = ALIASES.get(raw.lower(), raw.lower())
        handler = self.table.get(cmd)
        if handler is None:
            return False, f"unknown command: {raw}"
        if cmd in self.pro_tools and self.settings.get(BUSY_SETTING):
            job = self.busy()
            if job:
                return False, f"Pro Tools ist belegt ({job}) – {cmd} abgelehnt"
        try:
            ok, msg = handler(list(args or []))
            return bool(ok), str(msg)
        except Exception as e:  # noqa: BLE001 – wie PunchBuddy: Fehler als Antwort
            log.exception("Befehl %s fehlgeschlagen", cmd)
            return False, f"error: {e}"

    # ── Pro-Tools-Befehle ───────────────────────────────────────────────────
    def _queue(self, name: str, fn: Callable[..., Any], *args: Any) -> tuple[bool, str]:
        if not self.debounce.ok(name, DEBOUNCE_S):
            return True, f"{name} debounced"
        log.info(">>> %s <<<", TITLES.get(name, name))
        self.spawn(fn, *args)
        return True, f"{name} queued"

    def _run(self, name: str, function: str) -> tuple[bool, str]:
        return self._queue(name, getattr(self.core.module("transport"), function))

    def _record(self, name: str, rec: str, monitor: str) -> tuple[bool, str]:
        tracks = list(self.settings.get(rec) or [])
        if not tracks:
            return False, f"{name}: keine Aufnahmespuren eingestellt"
        transport = self.core.module("transport")
        return self._queue(name, transport.run_punch_in, tracks, list(self.settings.get(monitor) or []))

    def _move_audio(self) -> tuple[bool, str]:
        src = list(self.settings.get("aufnahme.audio_verschieben.quellspuren") or [])
        dst = list(self.settings.get("aufnahme.audio_verschieben.zielspuren") or [])
        if not src or len(src) != len(dst):
            return False, "move_audio: Quell- und Zielspuren paarweise einstellen"
        return self._queue("move_audio", self._guarded_move, src, dst)

    def _guarded_move(self, src: list[str], dst: list[str]) -> None:
        """PunchBuddys Verschieben mit Vor- und Nachprüfung. Live 09.10.2026: Die Zielspur war ausgeblendet –
        Pro Tools schnitt das Material aus, das Einfügen ging ins Leere, der Clip lag nur noch in der Clip-Liste."""
        from tonmischung.core.session_text import track_clips
        from tonmischung.ptsl.errors import PtslError
        ptsl, title = self.core.ptsl, TITLES["move_audio"]
        try:
            states = {t.name: t for t in ptsl.track_states()}
            formats = {t.name: t.format for t in ptsl.tracks()}
            clips = track_clips(ptsl.session_text())
        except PtslError as e:
            self.core.on_error(title, f"Nicht verschoben – Spuren nicht lesbar: {e}")
            return
        problems, hidden = move_check(src, dst, states, formats, clips)
        if problems:
            self.core.on_error(title, "Nicht verschoben: " + "; ".join(problems))
            return
        try:
            if hidden:                              # ausgeblendet: das Einfügen ginge ins Leere
                log.info("Audio verschieben: blende ein: %s", ", ".join(hidden))
                ptsl.set_hidden(hidden, False)
            self.core.module("transport").run_move_audio(src, dst)
            after = track_clips(ptsl.session_text())
        except PtslError as e:
            self.core.on_error(title, f"Verschieben nicht nachweisbar: {e} – bitte in Pro Tools prüfen")
            return
        lost = [t for s, t in zip(src, dst) if clips.get(s) and not after.get(t)]
        if lost:
            self.core.on_error(title, f"Auf {', '.join(lost)} ist nichts angekommen – in Pro Tools mit ⌘Z rückgängig "
                                      "machen")

    # ── Einstellungen und Auskunft ──────────────────────────────────────────
    def _preset(self, args: list[str]) -> tuple[bool, str]:
        if not args:
            return False, "preset needs a number, e.g. 'preset 3'"
        try:
            n = int(str(args[0]).strip())
        except (ValueError, TypeError):
            return False, f"invalid preset number: {args[0]}"
        if not self.debounce.ok(f"preset:{n}", DEBOUNCE_PRESET_S):
            return True, f"preset {n} debounced"
        presets = self.settings.get("aufnahme.presets") or []
        if not 1 <= n <= len(presets):
            return False, f"invalid preset index: {n}"
        changed = apply_preset(self.settings, presets[n - 1])
        log.info("Preset %s („%s“) geladen: %s", n, presets[n - 1].get("name", ""),
                 ", ".join(changed) or "keine Änderung")
        return True, f"preset {n} loaded"

    def _status(self, args: list[str]) -> tuple[bool, str]:
        i = {"export_running": False, "import_running": bool(self.busy()),
             "http_port": self.settings.get("system.streamdeck_webtrigger.port"), **self.info()}
        return True, (f"ok export_running={i['export_running']} import_running={i['import_running']} "
                      f"http_port={i['http_port']}")


def keepalive(core: PunchCore, skip: Callable[[], bool], interval_s: float = 120.0,
              stop: threading.Event | None = None) -> threading.Thread:
    """Wie PunchBuddy: alle 120 s den Transportzustand lesen, damit die PTSL-Verbindung warm bleibt
    (NEXIS-Kaltstart). Übersprungen, solange aufgenommen wird oder ein Auftrag läuft."""
    stop = stop or threading.Event()
    state = core.module("state")

    def loop() -> None:
        while not stop.wait(interval_s):
            if skip() or state.running:
                continue
            if core.ptsl.current_engine is None:
                continue
            try:
                core.ptsl.call("KeepAlive", lambda e: e.transport_state(), timeout_s=3.0)
            except Exception as e:  # noqa: BLE001 – beim nächsten Befehl wird neu verbunden
                log.debug("KeepAlive: %s", e)
    t = threading.Thread(target=loop, daemon=True, name="tm-keepalive")
    t.start()
    return t
