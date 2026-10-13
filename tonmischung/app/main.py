"""Ottilie als Mac-App: Menüleisten-Symbol und Hauptfenster (M6a).

Start: `python3 -m tonmischung app [--mock]`. Mit `--selbsttest` öffnet sich das Fenster, die
Oberfläche meldet sich über die Bridge zurück, danach endet die App (Exit-Code 0) – zum
Prüfen ohne Bildschirmfoto.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable

from tonmischung.app.bridge import Bridge
from tonmischung.app.catchup_app import catch_up
from tonmischung.app.commands import register_all
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import JobRunner
from tonmischung.app.lifecycle import (QUIT_ANYWAY, QUIT_KEEP, install_shutdown, partner_app_nap_off,
                                       prevent_app_nap, quit_question)

log = logging.getLogger("tonmischung.app")

SELFTEST_TIMEOUT_S = 30.0


def resource(name: str) -> str:
    """Datei aus `tonmischung/resources` (auch im Paket), sonst ""."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", name)
    return path if os.path.isfile(path) else ""


def _set_dock_icon(ns_app) -> None:
    """Symbol im Dock – auch beim Start über python3 (sonst die Python-Rakete)."""
    path = resource("Ottilie.png")
    if not path:
        return
    try:
        from AppKit import NSImage
        img = NSImage.alloc().initWithContentsOfFile_(path)
        if img is not None:
            ns_app.setApplicationIconImage_(img)
    except Exception as e:  # noqa: BLE001 – nur Darstellung
        log.warning("Dock-Symbol: %s", e)


def every(seconds: float, work: Callable[[], None], name: str) -> None:
    """Takt: `work` im eigenen Thread. Läuft der vorige noch (z. B. Helmut hängt), fällt dieser Takt aus – sonst
    stapeln sich Threads (Review B9)."""
    import rumps
    busy = threading.Lock()

    def run_once() -> None:
        try:
            work()
        except Exception:
            log.exception("Takt %s", name)
        finally:
            busy.release()

    def tick(_timer) -> None:
        if busy.acquire(blocking=False):
            threading.Thread(target=run_once, daemon=True, name=name).start()
    rumps.Timer(tick, seconds).start()


def _selftest_ready(selftest: bool) -> Callable[[dict], dict]:
    """Befehl „ready“: Die Oberfläche hat geladen. Im Selbsttest Menü und Autopilot melden, dann beenden."""
    def ready(args: dict) -> dict:
        if not selftest:
            return {"ok": True}

        import rumps
        from AppKit import NSApp
        from PyObjCTools import AppHelper
        menu = NSApp.mainMenu()
        titles = [menu.itemAtIndex_(i).submenu().title() for i in range(menu.numberOfItems())] if menu else []
        print("Menü:", ", ".join(titles), flush=True)
        job = args.get("auto")
        if job is not None:
            print("Autopilot:", json.dumps({k: job.get(k) for k in ("title", "state", "error", "result")},
                                           ensure_ascii=False), flush=True)
            print("SELBSTTEST OK" if job.get("state") == "fertig" else "SELBSTTEST FEHLGESCHLAGEN", flush=True)
        else:
            print("SELBSTTEST OK", flush=True)
        AppHelper.callAfter(rumps.quit_application)
        return {"ok": True}
    return ready


def _report_problems(ctx: AppContext, notify: Callable[[str, str, str], None], mock: bool) -> None:
    """Beschädigte eigene Dateien wurden beiseitegelegt (Review 09.10.2026), die Datenübernahme scheiterte (B10) oder
    das Recht „Bildschirmaufnahme“ fehlt (B24) – dem Mischer sagen, nicht nur ins Protokoll."""
    from tonmischung import paths
    from tonmischung.ptsl.dialogs import SCREEN_CAPTURE_HINT, screen_capture_allowed
    moved = paths.LAST if "nicht übernommen" in paths.LAST else ""
    capture = SCREEN_CAPTURE_HINT if screen_capture_allowed() is False and not mock else ""
    for problem in (ctx.settings.local_problem, getattr(ctx.queue, "problem", ""), moved, capture):
        if problem:
            notify("Ottilie", "Bitte prüfen", problem)


def _start_watchers(ctx: AppContext, runner: JobRunner, notify: Callable[[str, str, str], None]):
    """Fremde Audiodateien (M7: prüfen, nach Einstellung übernehmen) und Spiegelung der Session-Ablage; vor jedem
    Schließen erst fremde Dateien ins Projekt, dann spiegeln."""
    from tonmischung.app.foreign_app import ForeignWatcher
    from tonmischung.app.mirror_app import MirrorWatcher
    foreign = ForeignWatcher(ctx, busy=runner.busy, notify=lambda title, text: notify("Ottilie", title, text))
    mirror_w = MirrorWatcher(ctx, owner=foreign.owner, notify=lambda title, text: notify("Ottilie", title, text))
    ctx.extra["fremd"], ctx.extra["spiegel"] = foreign, mirror_w

    def before_close() -> None:
        foreign.before_close()
        mirror_w.before_close()
    ctx.ptsl.before_close = before_close
    return foreign, mirror_w


def _start_triggers_and_api(ctx: AppContext, bridge: Bridge, punch):
    """Webtrigger (Port 8899) und Stream-Deck-Socket wie PunchBuddy – /import holt den gewählten Beitrag über den
    Befehl des Fensters, mit denselben Prüfungen (angemeldeter Nutzer, Review A17); dazu der API-Socket."""
    import rumps
    from PyObjCTools import AppHelper

    from tonmischung.app import templates_app, triggers_app
    bridge.register("auswahl", lambda args: ctx.extra.__setitem__("auswahl", args.get("id")) or {"ok": True})
    triggers_app.add_fetch_command(punch.commands, ctx, lambda a: bridge.call("holen", a),
                                   lambda: templates_app.library(ctx)["default"])
    triggers = triggers_app.start(ctx, punch.commands, on_warning=lambda w: AppHelper.callAfter(
        rumps.notification, "Ottilie", "Hinweis", w))
    triggers_app.register(bridge, ctx, triggers)
    api = None
    if ctx.settings.get("system.streamdeck_webtrigger.unix_socket_aktiv"):
        from tonmischung.app.api import ApiDispatcher
        from tonmischung.app.socket_api import SocketApi
        try:
            api = SocketApi(ApiDispatcher(bridge.call, ctx.settings))
            api.start()
        except (OSError, RuntimeError) as e:
            log.warning("API-Socket nicht gestartet: %s", e)
            api = None
    return triggers, api


def _menubar(ctx: AppContext, window, punch, voc, runner: JobRunner, notify: Callable[[str, str, str], None]):
    """Menüleiste: Logo (Jens 08.10.2026: einfarbiges Vorlagenbild, macOS färbt es passend zur Menüleiste; ohne
    Datei „Ottilie“ als Text), Fenster, Punch-In, Presets, Exporte, Vocaster, Diagnose, Beenden."""
    import rumps
    from PyObjCTools import AppHelper

    from tonmischung.app import export_app, punch_app, vocaster_app
    logo = resource("Ottilie-Menue.png")
    app = (rumps.App("Ottilie", title=None, icon=logo, template=True, quit_button=None) if logo
           else rumps.App("Ottilie", title="Ottilie", quit_button=None))

    def request_quit(_sender=None) -> None:
        """Beenden aus der Menüleiste oder mit ⌘Q: läuft ein Auftrag, erst fragen (Review A16)."""
        question = quit_question(runner.running_title())
        if question and rumps.alert(*question, ok=QUIT_KEEP, cancel=QUIT_ANYWAY) == 1:
            return
        rumps.quit_application()

    def diagnose(_sender) -> None:
        def work() -> None:

            from tonmischung.diagnose import write_report
            path = write_report(ctx.settings, ctx.version, ctx.ptsl)
            notify("Ottilie", "Diagnosebericht erstellt", str(path))
            subprocess.run(["/usr/bin/open", "-R", str(path)], check=False)
        threading.Thread(target=work, daemon=True, name="tm-diagnose").start()

    presets = punch_app.preset_menu(punch, rumps)
    voc_menu = vocaster_app.menu(voc, punch.commands, rumps)
    app.menu = [rumps.MenuItem("Fenster öffnen", callback=lambda _: window.show()), None,
                *punch_app.menu_items(punch, rumps), presets, export_app.menu(punch.commands, rumps),
                *([voc_menu] if voc_menu else []), None]
    if voc.present:
        rumps.Timer(lambda _: voc.controller.drive(), 0.4).start()      # Autogain-Fenster im Main-Thread
        AppHelper.callAfter(vocaster_app.apply_start_options, voc, ctx.settings, notify)
    app.menu.add(rumps.MenuItem("Diagnosebericht erstellen", callback=diagnose))
    app.menu.add(None)
    app.menu.add(rumps.MenuItem("Beenden", callback=request_quit))
    rumps.Timer(lambda _: punch_app.fill_presets(presets, punch, rumps), 2.0).start()   # Namen aktuell halten
    return app, bool(logo), request_quit


def _start_timers(ctx: AppContext, runner: JobRunner, foreign, mirror_w, peers) -> None:
    """Takte: Nachtrag (60 s, solange etwas offen ist), fremde Dateien, Spiegeln, Spurprofil der offenen Session,
    Raumstatus (Helmut und Bonjour)."""
    from tonmischung.app.profile_binder import ProfileBinder
    from tonmischung.app.roomstatus import RoomPublisher

    def catch_up_tick() -> None:
        if ctx.queue is not None and ctx.queue.pending():
            log.info("Nachtrag: %s", catch_up(ctx)["text"])
    every(60.0, catch_up_tick, "tm-nachtrag")
    every(max(float(ctx.settings.get("import.session_ablage.fremde_dateien.abfrage_intervall_s") or 30), 10.0),
          foreign.check, "tm-fremd")
    every(5.0, mirror_w.tick, "tm-spiegel")
    binder = ProfileBinder(ctx, busy=runner.busy)
    ctx.extra["profile_binder"] = binder
    every(5.0, binder.tick, "tm-spurprofil")
    publisher = RoomPublisher(ctx, runner.running, peers)
    every(max(float(ctx.settings.get("system.raeume_nutzer.raumstatus.intervall_s") or 5), 2.0),
          publisher.tick, "tm-raumstatus")


def run(mock: bool = False, selftest: bool = False, auto: str = "") -> int:
    """App starten. `auto` (nur mit selftest): Autopilot, z. B. „holen:tw“ – klickt das Holen im Fenster durch."""
    from tonmischung.app import logfile
    logpath = logfile.setup(debug=bool(os.environ.get("TM_DEBUG")), console=bool(os.environ.get("TM_DEBUG")))
    log.info("Ottilie startet (Protokoll: %s)", logpath)
    from tonmischung import paths
    if paths.LAST:
        log.info(paths.LAST)                         # einmalige Übernahme von „Tonmischung“ (cli.main)
    import rumps
    from AppKit import NSApplication, NSApplicationActivationPolicyRegular
    from PyObjCTools import AppHelper

    from tonmischung import watchdog
    from tonmischung.app import pt_queries, punch_app, rooms_app, users_app, vocaster_app
    from tonmischung.app.bonjour import BonjourPeers
    from tonmischung.app.window import MainWindow, install_main_menu

    # 1. Kontext: Einstellungen, Helmut, Pro Tools, Nutzer und Raum
    ctx = AppContext.create(mock=mock)
    users_app.startup(ctx, selftest=selftest)      # Standard-Nutzer anmelden, sonst fragt das Fenster
    rooms_app.startup(ctx)                         # Raumliste aus der Ablage, Raum dieses Macs
    no_nap = prevent_app_nap()                     # Referenz halten, solange die App läuft
    if not selftest:
        partner_app_nap_off()
    ns_app = NSApplication.sharedApplication()
    ns_app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    _set_dock_icon(ns_app)

    def notify(title: str, subtitle: str, message: str) -> None:
        AppHelper.callAfter(rumps.notification, title, subtitle, message)

    # 2. Fenster und Befehle
    holder: dict = {}
    bridge = Bridge(emit=lambda payload: holder["window"].send(payload))
    window = MainWindow(on_message=bridge.handle, fragment=f"auto={auto}" if (selftest and auto) else "")
    holder["window"] = window
    runner = JobRunner(bridge)
    runner.ptsl = ctx.ptsl
    register_all(bridge, ctx, runner)
    pt_queries.register(bridge, ctx.ptsl)

    # 3. PunchBuddy-Funktionen über unsere Verbindung und Einstellungen; Menüleiste zeigt belegtes Pro Tools
    menubar: dict = {}

    def on_busy(busy: bool) -> None:
        if "app" in menubar:
            name = "" if menubar.get("logo") else "Ottilie"
            title = f"{name} ●".strip() if busy else (name or None)
            AppHelper.callAfter(setattr, menubar["app"], "title", title)

    def on_error(title: str, msg: str) -> None:
        log.warning("%s: %s", title, msg)
        AppHelper.callAfter(rumps.alert, title, msg)
    punch = punch_app.start(ctx, runner.running_title, on_error=on_error, on_busy=on_busy)
    punch_app.register(bridge, punch)
    punch_app.start_keepalive(punch, runner.running_title)
    _report_problems(ctx, notify, mock)
    voc = vocaster_app.detect(notify)               # Focusrite Vocaster (Erkennung beim Start wie PunchBuddy)
    vocaster_app.add_commands(punch.commands, voc)
    vocaster_app.register(bridge, voc)

    # 4. Raumstatus direkt per Bonjour (Jens 03.10.2026), Beobachter, Exporte, Webtrigger und API
    peers = BonjourPeers(ctx.room, ctx.settings.get("system.streamdeck_webtrigger.port") or 8899, AppHelper.callAfter,
                         browse=ctx.settings.get("system.raeume_nutzer.raumstatus.quelle") != "nur_helmut")
    peers.start()
    ctx.extra["peers"] = peers
    foreign, mirror_w = _start_watchers(ctx, runner, notify)
    from tonmischung.app import export_app
    export_app.add_export_commands(punch.commands, punch.core, runner)
    triggers, api = _start_triggers_and_api(ctx, bridge, punch)
    bridge.register("ready", _selftest_ready(selftest))

    # 5. Watchdog (eigener Prozess: Neustart/Beenden, wenn das Menü hängt – nicht im Selbsttest), Aufräumen
    by_signal = {"ja": False}
    watchdog_proc = None if selftest else watchdog.start_for_app(["--mock"] if mock else [])

    def cleanup() -> None:
        if watchdog_proc is not None and not by_signal["ja"]:
            watchdog_proc.terminate()               # nur beim normalen Beenden; beim Neustart durch den
        triggers.stop()                              # Watchdog (SIGTERM) bleibt er laufen
        if voc.present:
            voc.controller.shutdown()                # trennt sicher seit punchbuddy-protools 1124b7c
        if api is not None:
            api.stop()
        peers.stop()                                 # Bonjour-Anmeldung zurücknehmen
        bridge.close()
        ctx.close()
    shutdown = install_shutdown(cleanup, exit=lambda: rumps.quit_application(),
                                on_signal_first=lambda: by_signal.__setitem__("ja", True))
    if selftest:
        def fail() -> None:
            print("SELBSTTEST FEHLGESCHLAGEN: Oberfläche hat sich nicht gemeldet", flush=True)
            os._exit(1)
        t = threading.Timer(SELFTEST_TIMEOUT_S if not auto else 900.0, fail)
        t.daemon = True
        t.start()

    # 6. Menüleiste und Takte, dann los
    app, has_logo, request_quit = _menubar(ctx, window, punch, voc, runner, notify)
    menubar["app"], menubar["logo"] = app, has_logo
    _start_timers(ctx, runner, foreign, mirror_w, peers)
    # Python führt Signal-Handler nur aus, wenn im Haupt-Thread Python-Code läuft – im
    # AppKit-Lauf also erst beim nächsten Rückruf. Der Takt sorgt dafür, dass SIGTERM sofort wirkt.
    rumps.Timer(lambda _: None, 1.0).start()
    logfile.restore_crash_log()
    AppHelper.callAfter(install_main_menu, window, request_quit)   # nach dem Start von rumps, sonst überschrieben
    window.show()
    try:
        app.run()
    finally:
        shutdown()
        del no_nap
    return 0


if __name__ == "__main__":
    sys.exit(run(mock="--mock" in sys.argv, selftest="--selbsttest" in sys.argv))
