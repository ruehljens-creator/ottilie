"""Anzeigen für den Fortschritt: Terminalzeile und PunchBuddys Fortschrittsfenster."""
from __future__ import annotations

import sys
import threading
from typing import Callable, TextIO, TypeVar

T = TypeVar("T")


class TerminalSink:
    """Eine mitlaufende Zeile mit Balken; ohne Terminal eine Zeile je neuem Text."""

    def __init__(self, stream: TextIO | None = None, width: int = 24) -> None:
        self.stream = stream or sys.stderr
        self.width = width
        self._tty = hasattr(self.stream, "isatty") and self.stream.isatty()
        self._last = ""
        self._lock = threading.Lock()

    def update(self, fraction: float, text: str) -> None:
        with self._lock:
            filled = int(round(fraction * self.width))
            line = f"[{'#' * filled}{'.' * (self.width - filled)}] {fraction * 100:3.0f} %  {text}"
            if self._tty:
                self.stream.write("\r\x1b[2K" + line)
                self.stream.flush()
            elif text.split(" (")[0] != self._last:          # ohne Sekundenzähler vergleichen
                self.stream.write(line + "\n")
            self._last = text.split(" (")[0]

    def close(self) -> None:
        if self._tty:
            with self._lock:
                self.stream.write("\n")
                self.stream.flush()


class PunchBuddyWindow:
    """PunchBuddys schwebendes Fortschrittsfenster (punchbuddy.uikit._show_progress_win).

    Braucht eine laufende macOS-Ereignisschleife: in der PunchBuddy-App vorhanden,
    in der Kommandozeile über `run_with_event_loop`.
    """

    def __init__(self, title: str) -> None:
        from tonmischung.vendor import import_isolated
        uikit = import_isolated("punchbuddy.uikit")
        self._win = uikit._show_progress_win(title)

    def update(self, fraction: float, text: str) -> None:
        self._win["update"](fraction, text)

    def close(self) -> None:
        self._win["close"]()


def run_with_event_loop(fn: Callable[[], T]) -> T:
    """fn im Hintergrund ausführen, solange der Hauptthread die Ereignisschleife betreibt.

    So kann die Kommandozeile PunchBuddys AppKit-Fenster zeigen. Ohne Dock-Symbol;
    Strg+C bricht ab. Ausnahmen aus fn werden im Hauptthread weitergereicht.
    """
    import AppKit
    from PyObjCTools import AppHelper

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    result: dict = {}

    def worker() -> None:
        try:
            result["value"] = fn()
        except BaseException as e:  # noqa: BLE001 – an den Hauptthread weiterreichen
            result["error"] = e
        finally:
            AppHelper.callAfter(AppHelper.stopEventLoop)

    threading.Thread(target=worker, name="tonmischung-ablauf", daemon=True).start()
    AppHelper.runConsoleEventLoop(installInterrupt=True)
    if "error" in result:
        raise result["error"]
    return result.get("value")  # type: ignore[return-value]
