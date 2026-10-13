"""PunchBuddy-Funktionen in der App: Kern einbinden, Befehle registrieren, Menüpunkte.

Plan PunchBuddy-Übernahme, Schritt 2. Die Menüleiste bekommt die Punkte aus PunchBuddy
(Texte wie dort); Fenster, JSON-Socket und MCP erreichen dieselben Befehle über die Bridge
(`punch_<befehl>`), alles über `PunchCommands.dispatch`.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.config.settings import SettingLockedError, load_schema
from tonmischung.punch.commands import PRO_TOOLS, TITLES, PunchCommands, keepalive
from tonmischung.punch.core import PunchCore

log = logging.getLogger("tonmischung.app")

# Reihenfolge der Menüpunkte wie in PunchBuddy
MENU = ("record_a", "record_b", "play", "play_custom", "goto_start", "move_audio")
BRIDGE_COMMANDS = PRO_TOOLS | {"preset", "export_wav", "export_aaf", "export_aaf_reference"}


@dataclass
class Punch:
    core: PunchCore
    commands: PunchCommands
    restore: Callable[[], None]
    tracks: list[str] | None = None          # zuletzt gelesene Audiospuren der Session (Spurabfrage)


def start(ctx: Any, busy: Callable[[], str | None], *, on_error: Callable[[str, str], None],
          on_busy: Callable[[bool], None]) -> Punch:
    """Kern einbinden (Verbindung und Einstellungen von Ottilie) und Befehle bereitstellen."""
    core = PunchCore(ctx.ptsl, ctx.settings, load_schema(), on_error=on_error, on_busy=on_busy)
    restore = core.install()
    commands = PunchCommands(core, ctx.settings, busy=busy)
    return Punch(core, commands, restore)


def _args(name: str, args: dict) -> list[str]:
    if name == "preset":
        nr = args.get("nr", args.get("n"))
        return [str(nr)] if nr is not None else []
    return [str(a) for a in args.get("args") or []]


def register(bridge: Bridge, punch: Punch) -> None:
    """`punch_<befehl>` für Fenster, JSON-Socket und MCP; Fehler als CommandError."""
    def handler(name: str) -> Callable[[dict], dict]:
        def run(args: dict) -> dict:
            ok, msg = punch.commands.dispatch(name, _args(name, args))
            if not ok:
                raise CommandError(msg)
            return {"ok": True, "meldung": msg}
        return run
    for name in sorted(BRIDGE_COMMANDS):
        bridge.register(f"punch_{name}", handler(name))
    register_settings(bridge, punch)


def read_tracks(punch: Punch) -> str:
    """Spurabfrage: Audiospuren der offenen Session aus Pro Tools (nur lesen). Rückgabe: Meldung."""
    from tonmischung.ptsl.errors import PtslError
    try:
        session = punch.core.ptsl.open_session()
        if session is None:
            punch.tracks = None
            return "Keine Session offen – Spuren aus den Einstellungen"
        punch.tracks = [t.name for t in punch.core.ptsl.tracks() if t.type == "AudioTrack"]
        return f"{len(punch.tracks)} Spuren aus „{session.name}“ gelesen"
    except PtslError as e:
        punch.tracks = None
        return f"Pro Tools nicht erreichbar – Spuren aus den Einstellungen ({e})"


def register_settings(bridge: Bridge, punch: Punch) -> None:
    """Spurraster und Presets für das Einstellungsfenster."""
    from tonmischung.punch import grid as g
    settings = punch.core.settings

    def view(meldung: str = "") -> dict:
        return {**g.grid(settings, punch.tracks), "meldung": meldung}

    def raster(args: dict) -> dict:
        meldung = read_tracks(punch) if args.get("lesen") or punch.tracks is None else ""
        return view(meldung)

    def setzen(args: dict) -> dict:
        try:
            g.toggle(settings, args["spalte"], args["spur"], bool(args.get("an")))
        except g.GridError as e:
            raise CommandError(str(e)) from e
        return view()

    def presets(args: dict | None = None) -> dict:
        return {"presets": g.presets_view(settings)}

    def guarded(fn: Callable[[dict], Any]) -> Callable[[dict], dict]:
        def run(args: dict) -> dict:
            try:
                fn(args)
            except g.GridError as e:
                raise CommandError(str(e)) from e
            return presets()
        return run

    def verschieben(args: dict) -> dict:
        meldung = read_tracks(punch) if args.get("lesen") or punch.tracks is None else ""
        return {**g.move_view(settings, punch.tracks), "meldung": meldung}

    def verschieben_setzen(args: dict) -> dict:
        try:
            n = g.set_move_pairs(settings, list(args.get("paare") or []))
        except (g.GridError, SettingLockedError) as e:
            raise CommandError(str(e)) from e
        return {**g.move_view(settings, punch.tracks), "meldung": f"{n} Spurpaar{'e' if n != 1 else ''} gespeichert"}
    bridge.register("spurraster", raster)
    bridge.register("audio_verschieben", verschieben)
    bridge.register("audio_verschieben_setzen", verschieben_setzen)
    bridge.register("spurraster_setzen", setzen)
    bridge.register("presets", presets)
    bridge.register("preset_speichern", guarded(lambda a: g.save_preset(settings, a.get("name", ""),
                                                                        int(a["nr"]) if a.get("nr") else None)))
    bridge.register("preset_umbenennen", guarded(lambda a: g.rename_preset(settings, int(a["nr"]), a.get("name", ""))))
    bridge.register("preset_loeschen", guarded(lambda a: g.delete_preset(settings, int(a["nr"]))))


def menu_items(punch: Punch, rumps: Any) -> list:
    """Menüpunkte für rumps; Antworten (z. B. „belegt“) als Mitteilung."""
    def clicked(name: str) -> Callable[[Any], None]:
        def run(_sender: Any) -> None:
            log.info(">>> MENÜ „%s“ <<<", TITLES[name])
            ok, msg = punch.commands.dispatch(name)
            if not ok:
                rumps.notification("Ottilie", TITLES[name], msg)
        return run
    return [rumps.MenuItem(TITLES[name], callback=clicked(name)) for name in MENU]


def preset_menu(punch: Punch, rumps: Any) -> Any:
    """Untermenü „Presets“ mit den Namen aus den Einstellungen (Nummer = Position)."""
    sub = rumps.MenuItem("Presets")
    fill_presets(sub, punch, rumps)
    return sub


def fill_presets(sub: Any, punch: Punch, rumps: Any) -> tuple:
    names = tuple(p.get("name", f"Preset {i + 1}")
                  for i, p in enumerate(punch.core.settings.get("aufnahme.presets") or []))
    if getattr(sub, "_tm_names", None) == names:
        return names
    if getattr(sub, "_menu", None) is not None and len(sub):
        sub.clear()

    def load(nr: int) -> Callable[[Any], None]:
        def run(_sender: Any) -> None:
            ok, msg = punch.commands.dispatch("preset", [str(nr)])
            rumps.notification("Ottilie", f"Preset {nr}", msg if not ok else f"„{names[nr - 1]}“ geladen")
        return run
    for i, name in enumerate(names, start=1):
        sub.add(rumps.MenuItem(f"{i}  {name}", callback=load(i)))
    sub._tm_names = names
    return names


def start_keepalive(punch: Punch, busy: Callable[[], str | None]) -> None:
    keepalive(punch.core, skip=lambda: busy() is not None)
