"""Verbindung Fenster ↔ Kern: Befehle als JSON, Antworten und Ereignisse zurück.

Ohne WebKit testbar. Das Fenster schickt `{"id", "cmd", "args"}`; jeder Befehl läuft im
Hintergrund (nie im Hauptthread – sonst stünde das Fenster), die Antwort geht als
`{"type": "reply", "id", "ok", "result" | "error"}` zurück. Ereignisse (Fortschritt, Fragen)
gehen als `{"type": "event", "name", "data"}` an das Fenster.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

Handler = Callable[[dict], Any]
Emit = Callable[[dict], None]

log = logging.getLogger("tonmischung.app")


class CommandError(Exception):
    """Fehler, dessen Text der Mischer sehen soll (ohne Traceback im Protokoll)."""


class Bridge:
    # 8 statt 2 (Review A13): hängt Helmut, blockieren Liste, Detail und Räume je einen Thread – Klicks im Fenster
    # sollen trotzdem sofort laufen. Lange Aufträge laufen ohnehin in eigenen Threads (JobRunner).
    def __init__(self, emit: Emit, workers: int = 8, inline: bool = False) -> None:
        self._emit = emit
        self._handlers: dict[str, Handler] = {}
        self._inline = inline
        self._pool = None if inline else ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tm-befehl")

    def register(self, name: str, handler: Handler) -> None:
        self._handlers[name] = handler

    def guard(self, names: tuple[str, ...], check: Callable[[], None]) -> None:
        """Vor diesen Befehlen erst `check()` aufrufen (wirft CommandError, wenn der Befehl jetzt nicht darf)."""
        for name in names:
            handler = self._handlers[name]
            self._handlers[name] = lambda args, h=handler: (check(), h(args))[1]

    def handle(self, raw: str | dict) -> None:
        """Nachricht aus dem Fenster annehmen und im Hintergrund ausführen."""
        log.debug("Fenster → Kern: %s", raw)
        try:
            msg = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError):
            log.warning("Unlesbare Nachricht aus dem Fenster: %r", raw)
            return
        if self._inline:
            self._run(msg)
        else:
            self._pool.submit(self._run, msg)

    def _run(self, msg: dict) -> None:
        name, rid = msg.get("cmd"), msg.get("id")
        handler = self._handlers.get(name)
        if handler is None:
            reply = {"type": "reply", "id": rid, "ok": False, "error": f"Unbekannter Befehl: {name}"}
        else:
            try:
                reply = {"type": "reply", "id": rid, "ok": True, "result": handler(msg.get("args") or {})}
            except CommandError as e:
                reply = {"type": "reply", "id": rid, "ok": False, "error": str(e)}
            except Exception as e:  # noqa: BLE001 – jeder Fehler geht als Meldung ans Fenster
                log.exception("Befehl %s gescheitert", name)
                reply = {"type": "reply", "id": rid, "ok": False, "error": str(e) or e.__class__.__name__}
        self._send(reply)

    def _send(self, payload: dict) -> None:
        try:
            self._emit(payload)
        except Exception:  # noqa: BLE001 – sonst verschluckt der Thread-Pool den Fehler
            log.exception("Antwort an das Fenster gescheitert")

    def call(self, name: str, args: dict | None = None) -> Any:
        """Befehl direkt ausführen und das Ergebnis zurückgeben (Socket, MCP, Tests)."""
        handler = self._handlers.get(name)
        if handler is None:
            raise CommandError(f"Unbekannter Befehl: {name}")
        return handler(dict(args or {}))

    def event(self, name: str, data: Any = None) -> None:
        self._send({"type": "event", "name": name, "data": data})

    def close(self) -> None:
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)
