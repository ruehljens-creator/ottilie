"""Fortschritt langer Abläufe: gewichtete Schritte, Lebenszeichen, beliebige Anzeigen.

Die Abläufe melden Schritt und Teilfortschritt; Anzeigen (PunchBuddy-Fenster,
Terminal, Hauptfenster) bekommen daraus Gesamtanteil und Text. Läuft ein
Schritt lange ohne neue Meldung (z. B. Lautheit messen), zeigt ein Lebenszeichen
jede Sekunde die vergangene Zeit – damit niemand denkt, es hängt.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Protocol, Sequence

log = logging.getLogger(__name__)


class Sink(Protocol):
    def update(self, fraction: float, text: str) -> None: ...

    def close(self) -> None: ...


class Stopped(Exception):
    """Der Mischer hat abgebrochen; ausgelöst beim nächsten Schrittwechsel (Jens 04.10.2026: Abbrechen-Knopf)."""


class Progress:
    def __init__(self, steps: Sequence[tuple[str, float]], sinks: Sequence[Sink] = (),
                 clock: Callable[[], float] = time.monotonic, tick_s: float | None = 1.0,
                 show_elapsed_after_s: float = 3.0, stop_requested: Callable[[], bool] | None = None) -> None:
        total = sum(w for _, w in steps) or 1.0
        self._spans: dict[str, tuple[float, float]] = {}
        acc = 0.0
        for name, weight in steps:
            self._spans[name] = (acc / total, weight / total)
            acc += weight
        self._sinks = list(sinks)
        self._stop_requested = stop_requested
        self._clock = clock
        self._show_after = show_elapsed_after_s
        self._step: str | None = None
        self._sub = 0.0
        self._detail = ""
        self._started = clock()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ticker = None
        if tick_s:
            self._ticker = threading.Thread(target=self._tick, args=(tick_s,), daemon=True)
            self._ticker.start()

    # ── Melden ───────────────────────────────────────────────────────────────
    def step(self, name: str, detail: str = "") -> None:
        if name not in self._spans:
            raise KeyError(f"Unbekannter Fortschrittsschritt: {name}")
        if self._stop_requested is not None and self._stop_requested():
            # Schrittgrenze = sicherer Punkt: der vorige Schritt ist fertig, der nächste hat nicht begonnen
            raise Stopped(f"Abgebrochen vor „{name}“" + (f" (zuletzt: „{self._step}“)" if self._step else ""))
        with self._lock:
            self._step, self._sub, self._detail, self._started = name, 0.0, detail, self._clock()
            self._emit()

    def update(self, sub_fraction: float, detail: str | None = None) -> None:
        with self._lock:
            self._sub = min(1.0, max(0.0, float(sub_fraction)))
            if detail is not None:
                self._detail = detail
            self._emit()

    def note(self, detail: str) -> None:
        """Nur den Text ändern (z. B. „Pro Tools zeigt … – bitte bestätigen“), der Fortschritt bleibt."""
        with self._lock:
            self._detail = detail
            self._emit()

    def close(self, text: str = "Fertig") -> None:
        self._stop.set()
        with self._lock:
            for s in self._sinks:
                try:
                    s.update(1.0, text)
                    s.close()
                except Exception:  # noqa: BLE001 – eine Anzeige darf den Ablauf nie stören
                    log.debug("Fortschrittsanzeige: Fehler beim Schließen", exc_info=True)

    # ── Werte ────────────────────────────────────────────────────────────────
    @property
    def current_step(self) -> str | None:
        return self._step

    def fraction(self) -> float:
        if self._step is None:
            return 0.0
        base, width = self._spans[self._step]
        return base + width * self._sub

    def text(self) -> str:
        if self._step is None:
            return ""
        label = f"{self._step}: {self._detail}" if self._detail else self._step
        elapsed = self._clock() - self._started
        return f"{label} ({elapsed:.0f} s)" if elapsed >= self._show_after else label

    # ── intern ───────────────────────────────────────────────────────────────
    def _emit(self) -> None:
        frac, text = self.fraction(), self.text()
        for s in self._sinks:
            try:
                s.update(frac, text)
            except Exception:  # noqa: BLE001
                log.debug("Fortschrittsanzeige: Fehler", exc_info=True)

    def _tick(self, every: float) -> None:
        while not self._stop.wait(every):
            with self._lock:
                if self._step is not None:
                    self._emit()


class NullProgress(Progress):
    """Ohne Anzeige – für Aufrufe ohne Fortschritt."""

    def __init__(self) -> None:
        super().__init__([], tick_s=None)

    def step(self, name: str, detail: str = "") -> None:
        pass

    def update(self, sub_fraction: float, detail: str | None = None) -> None:
        pass

    def note(self, detail: str) -> None:
        pass
