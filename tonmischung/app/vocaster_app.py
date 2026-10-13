"""Focusrite Vocaster in der App (Plan PunchBuddy-Übernahme, Schritt 4).

Kern aus PunchBuddy (`vocaster_control.py`, `vocaster_integration.py`, Submodul, nur lesen): Erkennung
One/Two über USB (libusb), 48 V, Autogain mit Fortschrittsfenster, Routing sichern und beim Start
anwenden. Wie in PunchBuddy wird das Gerät beim Start erkannt; der USB-Zugriff beendet „Vocaster Hub“
und kann den Audio-Ausgang kurz unterbrechen – er geschieht erst beim ersten Vocaster-Befehl bzw. mit
den Start-Optionen. Der Vocaster hängt nicht an Pro Tools.
Befehle/Webtrigger: vocaster_autogain_host|guest, vocaster_phantom_on|off – Antworten wie PunchBuddy.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tonmischung.punch.commands import DEBOUNCE_PRESET_S, PunchCommands
from tonmischung.vendor import import_isolated

log = logging.getLogger("tonmischung.vocaster")
NO_DEVICE = "no vocaster connected"
ACTIONS = {"vocaster_autogain_host": "autogain_host", "vocaster_autogain_guest": "autogain_guest",
           "vocaster_phantom_on": "phantom_on", "vocaster_phantom_off": "phantom_off"}
MENU = [("Autogain Host", "vocaster_autogain_host"), ("Autogain Gast", "vocaster_autogain_guest"),
        ("48 V an", "vocaster_phantom_on"), ("48 V aus", "vocaster_phantom_off")]


@dataclass
class Vocaster:
    controller: Any = None              # VocasterController oder None (kein Gerät)
    info: dict | None = None            # {name, channels, …} aus der Erkennung

    @property
    def present(self) -> bool:
        return self.controller is not None


def notify_wrapper(notify: Callable[[str, str, str], None]) -> Callable[..., None]:
    """PunchBuddys Mitteilungen tragen „PunchBuddy – Vocaster“ im Titel – in Ottilie nicht."""
    def wrapped(title: str, subtitle: str = "", message: str = "", **_: Any) -> None:
        log.info("Vocaster: %s – %s", subtitle, message)      # Ergebnisse (z. B. Autogain) auch im Protokoll
        notify(title.replace("PunchBuddy", "Ottilie"), subtitle, message)
    return wrapped


def detect(notify: Callable[[str, str, str], None], module: Any = None) -> Vocaster:
    vi = module or import_isolated("vocaster_integration")
    try:
        info = vi.VocasterController.detect()
    except Exception as e:  # noqa: BLE001 – ohne Vocaster läuft alles andere
        log.info("Vocaster-Erkennung übersprungen: %s", e)
        info = None
    if not info:
        log.info("Kein Vocaster erkannt – Vocaster-Befehle antworten „%s“", NO_DEVICE)
        return Vocaster()
    log.info("Vocaster erkannt: %s (%s Eingang/Eingänge)", info.get("name"), info.get("channels"))
    return Vocaster(vi.VocasterController(notify=notify_wrapper(notify)), info)


def run_action(voc: Vocaster, action: str) -> None:
    c = voc.controller
    if action == "autogain_host":
        c.request_autogain(0, "Host")
    elif action == "autogain_guest":
        c.request_autogain(1, "Guest")
    elif action == "phantom_on":
        c.set_phantom(True)
    elif action == "phantom_off":
        c.set_phantom(False)
    else:
        raise ValueError(action)


def add_commands(commands: PunchCommands, voc: Vocaster) -> None:
    """Befehle wie PunchBuddys `_cmd_vocaster` (Entprellen 0,6 s)."""
    def handler(name: str) -> Callable[[list[str]], tuple[bool, str]]:
        action = ACTIONS[name]

        def run(args: list[str]) -> tuple[bool, str]:
            if not voc.present:
                return False, NO_DEVICE
            if not commands.debounce.ok(f"voc:{action}", DEBOUNCE_PRESET_S):
                return True, f"vocaster {action} debounced"
            try:
                run_action(voc, action)
            except Exception as e:  # noqa: BLE001 – Antwort an Stream Deck/Webtrigger
                return False, f"vocaster error: {e}"
            return True, f"vocaster {action} queued"
        return run
    for name in ACTIONS:
        commands.add(name, handler(name), pro_tools=False)


def apply_start_options(voc: Vocaster, settings: Any, notify: Callable[[str, str, str], None]) -> None:
    """48 V und gesichertes Routing beim Start, wenn eingestellt (System → Focusrite Vocaster)."""
    if not voc.present:
        return
    if settings.get("system.vocaster.phantom_beim_start"):
        log.info("Vocaster: 48 V beim Start")
        voc.controller.set_phantom(True, announce=True)
    if settings.get("system.vocaster.routing_beim_start") and voc.controller.has_saved_routing():
        def work() -> None:
            ok, msg = voc.controller.apply_saved_routing()
            notify("Ottilie – Vocaster", "Routing angewendet" if ok else "Routing-Fehler", msg)
        threading.Thread(target=work, daemon=True, name="tm-vocaster-routing").start()


def info_view(voc: Vocaster, module: Any = None) -> dict[str, Any]:
    vi = module or import_isolated("vocaster_integration")
    routing = voc.controller.saved_routing_info() if voc.present else None
    if routing is None and not voc.present:
        try:
            with open(vi.ROUTING_PATH, encoding="utf-8") as f:
                data = json.load(f)
            routing = {"saved_at": data.get("saved_at", "?"), "model": data.get("model", "?")}
        except (OSError, ValueError):
            routing = None
    return {"geraet": (voc.info or {}).get("name") if voc.present else None,
            "kanaele": (voc.info or {}).get("channels") if voc.present else None,
            "routing": routing, "routing_datei": vi.ROUTING_PATH}


def register(bridge: Any, voc: Vocaster) -> None:
    from tonmischung.app.bridge import CommandError

    def sichern(args: dict) -> dict:
        if not voc.present:
            raise CommandError("Kein Vocaster angeschlossen")
        ok, msg = voc.controller.capture_routing_now()
        if not ok:
            raise CommandError(msg)
        return {**info_view(voc), "meldung": msg}
    bridge.register("vocaster_info", lambda args: info_view(voc))
    bridge.register("vocaster_routing_sichern", sichern)


def menu(voc: Vocaster, commands: PunchCommands, rumps: Any) -> Any | None:
    """Untermenü „Vocaster“ (nur mit Gerät); Gast nur beim Vocaster Two."""
    if not voc.present:
        return None
    sub = rumps.MenuItem("Vocaster")
    for label, name in MENU:
        if name == "vocaster_autogain_guest" and int((voc.info or {}).get("channels", 1)) < 2:
            continue

        def run(_sender: Any, _n: str = name, _l: str = label) -> None:
            ok, msg = commands.dispatch(_n)
            if not ok:
                rumps.notification("Ottilie – Vocaster", _l, msg)
        sub.add(rumps.MenuItem(label, callback=run))

    def save(_sender: Any) -> None:
        def work() -> None:
            ok, msg = voc.controller.capture_routing_now()
            rumps.notification("Ottilie – Vocaster", "Routing gesichert" if ok else "Fehler", msg)
        threading.Thread(target=work, daemon=True, name="tm-vocaster-sichern").start()
    sub.add(rumps.separator)
    sub.add(rumps.MenuItem("Aktuelles Routing sichern", callback=save))
    return sub
