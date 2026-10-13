"""Webtrigger und Stream-Deck-Socket in der App (Plan PunchBuddy-Übernahme, Schritt 3).

Einstellungen: `system.streamdeck_webtrigger` – Port, Erreichbarkeit (127.0.0.1 / Netzwerk),
Socket-Pfad, Token (im Schlüsselbund), „nur mit ID-Code“; der ID-Code steht am Raum
(`system.raeume_nutzer.raeume`). `/import` holt den im Fenster gewählten Beitrag.
"""
from __future__ import annotations

import logging
import secrets
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tonmischung.config.keychain import read_secret, write_secret
from tonmischung.punch.commands import PunchCommands
from tonmischung.punch.triggers import (DEFAULT_SOCKET, LineSocket, TriggerConfig, Webtrigger,
                                        punchbuddy_running)

log = logging.getLogger("tonmischung.trigger")
P = "system.streamdeck_webtrigger."
TOKEN_ACCOUNT = "webtrigger"


def room_id_code(settings: Any, room: str) -> str:
    for r in settings.get("system.raeume_nutzer.raeume") or []:
        if r.get("name") == room:
            return (r.get("id_code") or "").strip()
    return ""


def ensure_token(settings: Any, read: Callable[[str, str], str] = read_secret,
                 write: Callable[[str, str, str], None] = write_secret) -> str:
    """Token aus dem Schlüsselbund. Im Netzwerk-Modus ohne Token wird eins erzeugt und gespeichert –
    sonst stünde der Webtrigger ungeschützt im LAN (wie PunchBuddy)."""
    service = settings.get(P + "token_schluesselbund") or ""
    token = read(service, TOKEN_ACCOUNT) if service else ""
    if not token and settings.get(P + "bind_adresse") == "netzwerk":
        if not service:
            raise RuntimeError("Webtrigger im Netzwerk braucht einen Schlüsselbund-Eintrag für das Token")
        token = secrets.token_urlsafe(16)
        write(service, TOKEN_ACCOUNT, token)
        log.warning("Webtrigger im Netzwerk erreichbar – Token erzeugt und im Schlüsselbund „%s“ gespeichert", service)
    return token


def trigger_config(settings: Any, room: str, token: str) -> TriggerConfig:
    return TriggerConfig(port=int(settings.get(P + "port") or 8899),
                         bind="0.0.0.0" if settings.get(P + "bind_adresse") == "netzwerk" else "127.0.0.1",
                         token=token, id_code=room_id_code(settings, room),
                         require_id=bool(settings.get(P + "nur_mit_id_code")))


@dataclass
class Triggers:
    webtrigger: Webtrigger | None = None
    line_socket: LineSocket | None = None
    token: str = ""
    warnings: list[str] = field(default_factory=list)

    def stop(self) -> None:
        if self.webtrigger is not None:
            self.webtrigger.stop()
        if self.line_socket is not None:
            self.line_socket.stop()


def add_fetch_command(commands: PunchCommands, ctx: Any, start_fetch: Callable[[dict], Any],
                      default_template: Callable[[], dict | None]) -> None:
    """Befehl „holen“ (Webtrigger /import): den im Fenster gewählten Beitrag mit dem Standard-Template holen.
    Fragen (offene Session, Triage-Vorschau) erscheinen im Fenster."""
    def holen(args: list[str]) -> tuple[bool, str]:
        pid = args[0] if args else ctx.extra.get("auswahl")
        if not pid:
            return False, "Kein Beitrag im Fenster ausgewählt"
        tpl = default_template()
        if not tpl:
            return False, "Kein Standard-Template eingestellt (Persönlich → Meine Templates)"
        try:
            start_fetch({"id": pid, "group": tpl["group"], "name": tpl["name"]})
        except Exception as e:  # noqa: BLE001 – Antwort an Stream Deck/Webtrigger
            return False, str(e)
        return True, f"holen {pid} gestartet"
    commands.add("holen", holen)


def start(ctx: Any, commands: PunchCommands, on_warning: Callable[[str], None]) -> Triggers:
    """Socket und Webtrigger laut Einstellungen starten; Probleme als Warnung, nie als Absturz."""
    t = Triggers()
    s = ctx.settings
    if punchbuddy_running():
        t.warnings.append("PunchBuddy läuft noch. Ottilie ersetzt es auf diesem Platz – bitte PunchBuddy "
                          "beenden, sonst sind Webtrigger und Stream-Deck-Socket doppelt belegt.")
    if s.get(P + "unix_socket_aktiv"):
        ls = LineSocket(commands.dispatch, s.get(P + "unix_socket_pfad") or DEFAULT_SOCKET)
        try:
            ls.start()
            t.line_socket = ls
        except (OSError, RuntimeError) as e:
            t.warnings.append(f"Stream-Deck-Socket nicht gestartet: {e}")
    if s.get(P + "webtrigger_aktiv"):
        try:
            t.token = ensure_token(s)
            cfg = trigger_config(s, ctx.room, t.token)
            wt = Webtrigger(commands.dispatch, lambda: trigger_config(s, ctx.room, t.token))
            port = wt.start()
            t.webtrigger = wt
            if port != cfg.port:
                t.warnings.append(f"Webtrigger: Port {cfg.port} belegt – läuft vorübergehend auf {port}. "
                                  "Stream-Deck-Tasten und Companion erreichen ihn so nicht.")
        except (OSError, RuntimeError) as e:
            t.warnings.append(f"Webtrigger nicht gestartet: {e}")
    for w in t.warnings:
        log.warning(w)
        on_warning(w)
    return t


def copy_to_clipboard(text: str) -> None:
    from AppKit import NSPasteboard, NSPasteboardTypeString
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)


def register(bridge: Any, ctx: Any, triggers: Triggers,
             copy: Callable[[str], None] = copy_to_clipboard) -> None:
    """Adressen zum Kopieren, ID-Code je Platz und Zwischenablage für das Einstellungsfenster."""
    from tonmischung.app.bridge import CommandError
    from tonmischung.punch import urls

    def view(args: dict) -> dict:
        return urls.addresses(ctx.settings, args.get("raum") or ctx.room, token=triggers.token)

    def code(args: dict, value: Callable[[str], str]) -> dict:
        room = args.get("raum") or ctx.room
        try:
            urls.set_id_code(ctx.settings, room, value(room))
        except ValueError as e:
            raise CommandError(str(e)) from e
        return view({"raum": room})

    def kopieren(args: dict) -> dict:
        copy(str(args.get("text", "")))
        return {"ok": True}
    bridge.register("webtrigger_adressen", view)
    bridge.register("id_code_erzeugen", lambda a: code(a, urls.new_id_code))
    bridge.register("id_code_entfernen", lambda a: code(a, lambda room: ""))
    bridge.register("kopieren", kopieren)

    def launcher(args: dict) -> dict:
        from tonmischung.punch import launchers
        sock = ctx.settings.get(P + "unix_socket_pfad") or DEFAULT_SOCKET
        try:
            apps = launchers.make_launchers(sock, ctx.settings.get("aufnahme.presets") or [])
        except (OSError, ValueError) as e:
            raise CommandError(f"Launcher nicht erzeugt: {e}") from e
        subprocess.run(["/usr/bin/open", str(launchers.default_dir())], check=False)
        old = launchers.legacy_launchers()
        return {"ordner": str(launchers.default_dir()), "anzahl": len(apps), "alte": len(old)}
    bridge.register("launcher_erzeugen", launcher)
