"""Kommandozeile: `python3 -m tonmischung …` – Holen, Zurückliefern, Helmut, Einstellungen, Diagnose, Bau-Hilfen."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from tonmischung import __version__, factory
from tonmischung.config.settings import SettingLockedError, Settings
from tonmischung.core.mixstate import DELIVERED, KEEP_LAST, KEEP_NEW, RELEASE_REASONS, ConflictError
from tonmischung.core.progress import Progress
from tonmischung.core.session_rules import same_session
from tonmischung.core.track_match import FROM_A11, MAP, SKIP, TrackDecision, parse_choice
from tonmischung.helmut.client import HelmutError
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.errors import PtslError
from tonmischung.workflow.deliver import DELIVER_STEPS, deliver
from tonmischung.workflow.fetch import FETCH_STEPS, FetchError, NeedsConfirmation, fetch


def _cmd_settings(args: argparse.Namespace) -> int:
    s = factory.load_settings()
    if args.wert is not None:
        try:
            value = json.loads(args.wert)
        except json.JSONDecodeError:
            value = args.wert
        try:
            s.set_local(args.pfad, value)
        except (KeyError, SettingLockedError) as e:
            print(f"Fehler: {e.args[0]}", file=sys.stderr)
            return 1
        print(f"{args.pfad} = {json.dumps(value, ensure_ascii=False)} (lokal gespeichert)")
        return 0
    for path, (value, source) in s.snapshot().items():
        if args.pfad and not path.startswith(args.pfad):
            continue
        print(f"{path} = {json.dumps(value, ensure_ascii=False)}  [{source}]")
    return 0


PUNCHBUDDY_SETTINGS = Path.home() / ".punchbuddy" / "settings.json"


def _cmd_import_punchbuddy(args: argparse.Namespace) -> int:
    """Einstellungen aus PunchBuddy einmalig übernehmen (die PunchBuddy-Datei bleibt unverändert)."""
    from tonmischung.config.settings import load_schema
    from tonmischung.punch.settings_bridge import import_punchbuddy
    path = Path(args.datei).expanduser() if args.datei else PUNCHBUDDY_SETTINGS
    try:
        pb = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"PunchBuddy-Einstellungen nicht lesbar ({path}): {e}", file=sys.stderr)
        return 1
    rows = import_punchbuddy(factory.load_settings(), load_schema(), pb, overwrite=args.ueberschreiben,
                             dry_run=args.probe)
    width = max((len(r.key) for r in rows), default=10)
    for r in rows:
        value = json.dumps(r.value, ensure_ascii=False)
        value = value if len(value) <= 60 else value[:57] + "…"
        print(f"{r.status:17} {r.key:{width}}  {value}{'  – ' + r.note if r.note else ''}")
    done = sum(r.status == "übernommen" for r in rows)
    print(f"\n{done} Werte {'würden übernommen' if args.probe else 'übernommen'} aus {path}"
          + (" (Probelauf, nichts gespeichert)" if args.probe else ""))
    return 0


def _cmd_diagnose(args: argparse.Namespace) -> int:
    from tonmischung.diagnose import write_report
    s = factory.load_settings()
    path = write_report(s, __version__, None if args.ohne_pro_tools else factory.ptsl_client(s),
                        Path(args.ordner).expanduser() if args.ordner else None)
    print(f"Diagnosebericht: {path}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    client = factory.ptsl_client(factory.load_settings())
    try:
        print(f"PTSL-Version: {client.version()}")
        session = client.open_session()
        print(f"Session: {session.name} ({session.path})" if session else "Session: keine")
        print(f"Transport: {client.transport_state()}")
    except PtslError as e:
        print(f"Pro Tools: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_save(args: argparse.Namespace) -> int:
    client = factory.ptsl_client(factory.load_settings())
    try:
        session = client.open_session()
        if session is None:
            print("Keine Session offen.", file=sys.stderr)
            return 1
        client.save()
    except PtslError as e:
        print(f"Pro Tools: {e}", file=sys.stderr)
        return 1
    print(f"Gespeichert: {session.name}")
    return 0


def _cmd_helmut(args: argparse.Namespace) -> int:
    s = factory.load_settings()
    client = factory.helmut_client(s)
    try:
        if args.aktion == "liste":
            for b in client.list_for_mixing():
                wer = f" · {b.mix.mixer} ({b.mix.room})" if b.mix.mixer else ""
                print(f"{b.id}  {b.title}  [{b.show}]  {b.mix.state}{wer}")
            return 0
        mixer, room = factory.identity(s)
        service = MixService(client, mixer, room)
        if args.aktion == "uebernehmen":
            b = service.take(args.beitrag)
            print(f"Übernommen: {b.title} · {mixer} ({room})")
        elif args.aktion == "freigeben":
            b = service.release(args.beitrag, args.grund, args.naechstes)
            print(f"Freigegeben: {b.title} · Grund: {args.grund} · nächstes Öffnen: {args.naechstes}")
        return 0
    except (HelmutError, ConflictError) as e:
        print(f"Helmut: {e}", file=sys.stderr)
        return 1


def _with_progress(title: str, steps: list, fn, window: bool):
    """fn(progress) mit Terminalzeile und – wenn gewünscht – PunchBuddys Fortschrittsfenster."""
    from tonmischung.ui.progress_sinks import PunchBuddyWindow, TerminalSink, run_with_event_loop

    def job():
        sinks = [TerminalSink()]
        if window:
            sinks.append(PunchBuddyWindow(title))
        progress = Progress(steps, sinks)
        try:
            result = fn(progress)
        except BaseException as e:
            progress.close(f"Abgebrochen: {e}")
            raise
        progress.close("Fertig")
        return result
    return run_with_event_loop(job) if window else job()


def _decide_missing(option: str | None):
    """Fragt, was mit AAF-Spuren geschehen soll, die im Template fehlen."""
    def decide(fehlen: list[str], verfuegbar: list[str]) -> dict[str, TrackDecision]:
        if option:
            try:
                parsed = parse_choice(option)
            except ValueError as e:
                raise FetchError(f"--fehlende-spuren: {e}") from e
            return {t: parsed for t in fehlen} if isinstance(parsed, TrackDecision) else parsed
        if not sys.stdin.isatty():
            raise FetchError(f"Im Template fehlen Spuren: {', '.join(fehlen)} – mit --fehlende-spuren "
                             "\"ab A11\" | weglassen | \"Name=Spur,…\" entscheiden")
        print("\n\nIm Template fehlen Spuren für Match Tracks:", ", ".join(fehlen), file=sys.stderr)
        print("Vorhandene Spuren:", ", ".join(verfuegbar), file=sys.stderr)
        decisions = {}
        for t in fehlen:
            while True:
                try:
                    a = input(f"  {t}: [r] ab A11 (nächste freie Spur), [w]eglassen "
                              "oder Name einer vorhandenen Spur: ").strip()
                except EOFError as e:
                    raise FetchError("Keine Antwort zu den fehlenden Spuren – nichts importiert") from e
                if a.lower() in ("r", "a11", "ab a11"):
                    decisions[t] = TrackDecision(FROM_A11)
                elif a.lower() in ("w", SKIP):
                    decisions[t] = TrackDecision(SKIP)
                elif a in verfuegbar:
                    decisions[t] = TrackDecision(MAP, a)
                else:
                    print("    bitte r, w oder einen vorhandenen Spurnamen", file=sys.stderr)
                    continue
                break
        return decisions
    return decide


def _template(s: Settings, given: str | None) -> tuple[str, str] | None:
    """„Gruppe/Name“ aus der Kommandozeile, sonst der persönliche Standard."""
    if given:
        group, _, name = given.partition("/")
        return (group, name) if name else None
    default = s.get("persoenlich.standard_template_id")
    for t in s.get("persoenlich.templates") or []:
        if t.get("id") == default:
            return t.get("gruppe", ""), t.get("pt_name", "")
    return None


def _app_running(pids: Callable[[], list[int]] | None = None) -> bool:
    """Läuft die App für diesen Benutzer? Dann steuert sie Pro Tools – die Kommandozeile nicht dazwischen (Review B29:
    ein Holen über die Kommandozeile lief sonst an der Auftragssperre der App vorbei)."""
    from tonmischung.watchdog import app_pids
    try:
        return bool((pids or app_pids)())
    except OSError:
        return False


APP_RUNNING = ("Ottilie läuft – bitte im Fenster holen bzw. zurückliefern oder über die Schnittstelle der App "
               "(python3 -m tonmischung mcp, Socket). Die Kommandozeile steuert Pro Tools nur, wenn Ottilie nicht "
               "läuft.")


def _cmd_fetch(args: argparse.Namespace) -> int:
    if _app_running():
        print(APP_RUNNING, file=sys.stderr)
        return 1
    s = factory.load_settings()
    template = _template(s, args.template)
    if template is None:
        print("Kein Template: --template \"Gruppe/Name\" angeben oder Standard festlegen",
              file=sys.stderr)
        return 1
    client = factory.helmut_client(s)
    mixer, room = factory.identity(s)

    def delivered(path: str) -> bool:
        return any(same_session(b.mix.session_path, path) and b.mix.state == DELIVERED
                   for b in client.list_for_mixing())

    confirmed = True if args.ja else None
    triage = None if args.ohne_triage or not s.get("import.zuordnungsregeln.aktiv") else factory.triage_options(s)
    while True:
        try:
            result = _with_progress(
                f"Holen: {args.beitrag}", FETCH_STEPS, lambda progress: fetch(
                    args.beitrag, service=MixService(client, mixer, room, queue=_queue()), ptsl=factory.ptsl_client(s),
                    template_group=template[0], template_name=template[1],
                    session_parent=s.get("import.session_ablage.lokaler_ordner"),
                    confirmed=confirmed, is_delivered=delivered,
                    with_video=not args.ohne_video, progress=progress,
                    decide_missing=_decide_missing(args.fehlende_spuren),
                    triage=triage, confirm_triage=_confirm_triage(args.ja),
                    program_start=factory.program_start(s)),
                window=not args.kein_fenster)
            break
        except NeedsConfirmation as e:
            if confirmed is not None or not sys.stdin.isatty():
                print(f"{e} – mit --ja speichern, schließen und importieren", file=sys.stderr)
                return 1
            answer = input(f"{e}. Speichern, schließen und importieren? [j/N] ").strip().lower()
            confirmed = answer in ("j", "ja", "y")
        except (FetchError, ConflictError, HelmutError, PtslError) as e:
            print(f"Holen nicht möglich: {e}", file=sys.stderr)
            return 1
    for w in result.warnings:
        print(f"Hinweis: {w}")
    if result.placement is not None:
        n = sum(v[0] for v in result.clip_report.values())
        ok = sum(v[1] for v in result.clip_report.values())
        print(f"AAF-Triage: {ok} von {n} Clips exakt in ihrer Endlage, "
              f"{len(result.placement.clip_moves)} Bereiche verschoben")
    else:
        print(f"Neue Spuren: {', '.join(result.new_tracks) or 'keine'}")
    return 0


def _cmd_deliver(args: argparse.Namespace) -> int:
    if _app_running():
        print(APP_RUNNING, file=sys.stderr)
        return 1
    s = factory.load_settings()
    client = factory.helmut_client(s)
    mixer, room = factory.identity(s)
    export, loud = factory.export_tracks(s, args.spuren)
    target = args.ziel or s.get("export.verarbeitung_helmut.uebergabeordner")
    lautheit = "export.spuren_lautheit.lautheit."
    confirmed = True if args.ja else None
    while True:
        try:
            result = _with_progress(
                f"Zurückliefern: {args.beitrag}", DELIVER_STEPS, lambda progress: deliver(
                    args.beitrag, service=MixService(client, mixer, room, queue=_queue()), ptsl=factory.ptsl_client(s),
                    export_tracks=export, loudness_tracks=loud, handover_root=target,
                    target_lufs=float(s.get(lautheit + "ziel_lufs")),
                    max_truepeak=float(s.get(lautheit + "true_peak_max_dbtp")),
                    confirmed=confirmed, progress=progress, export_names=factory.export_names(s),
                    program_start=factory.program_start(s)),
                window=not args.kein_fenster)
            break
        except NeedsConfirmation as e:
            if confirmed is not None or not sys.stdin.isatty():
                print(f"{e} – mit --ja speichern, schließen und die Session des Beitrags öffnen",
                      file=sys.stderr)
                return 1
            answer = input(f"{e}. Speichern, schließen und Session des Beitrags öffnen? [j/N] ")
            confirmed = answer.strip().lower() in ("j", "ja", "y")
        except (FetchError, ConflictError, HelmutError, PtslError, OSError) as e:
            print(f"Zurückliefern nicht möglich: {e}", file=sys.stderr)
            return 1
    for w in result.warnings:
        print(f"Hinweis: {w}")
    return 0


def _confirm_triage(yes: bool):
    """Vorschau im Terminal: Bericht und Verteilung zeigen, dann fragen (ohne Terminal: --ja)."""
    def confirm(result, placement, replan=None) -> bool:
        from tonmischung.triage.report import format_placement, format_report
        print("\n\n" + format_report(result) + "\n\n" + format_placement(placement, result.analysis.fps or 25),
              file=sys.stderr)
        if yes:
            return True
        if not sys.stdin.isatty():
            raise FetchError("Die Triage-Vorschau braucht eine Bestätigung – mit --ja ohne Rückfrage importieren")
        try:
            return input("So importieren? [J/n] ").strip().lower() in ("", "j", "ja", "y")
        except EOFError:
            return False
    return confirm


def _queue():
    """Nachtrag-Warteschlange dieses Macs (dieselbe Datei wie die App)."""
    from tonmischung.core.catchup import CatchUpQueue
    return CatchUpQueue()


def _cmd_catch_up(args: argparse.Namespace) -> int:
    from tonmischung.helmut.catchup import replay
    q = _queue()
    if args.verwerfen:
        try:
            e = q.dismiss(args.verwerfen)
        except KeyError as err:
            print(err, file=sys.stderr)
            return 1
        print(f"Verworfen: {e.describe()}")
        return 0
    if not args.nur_zeigen:
        print(replay(q, factory.helmut_client(factory.load_settings())).text)
    for e in q.pending():
        print(f"offen     {e.id}  {e.describe()}  ({e.attempts} Versuche)")
    for e in q.conflicts():
        print(f"Konflikt  {e.id}  {e.describe()} – {e.message}")
    return 1 if q.conflicts() else 0


def _cmd_triage(args: argparse.Namespace) -> int:
    """AAF beurteilen: Kategorie je Spur und je Clip (nur lesen, ohne Pro Tools)."""
    from tonmischung.triage.analyse import triage_aaf
    from tonmischung.triage.report import as_dict, format_report
    from tonmischung.ui.progress_sinks import TerminalSink
    o = factory.triage_options(factory.load_settings(), args.schwelle)
    progress = Progress([("AAF lesen und beurteilen", 1)], [TerminalSink(sys.stderr)])
    progress.step("AAF lesen und beurteilen", args.aaf.rsplit("/", 1)[-1])
    try:
        result = triage_aaf(args.aaf, threshold=o.threshold, keywords=o.keywords,
                            music_paths=o.music_paths, use_track_names=o.use_track_names,
                            use_source_names=o.use_source_names, use_signal=o.use_signal,
                            progress=lambda f, t: progress.update(f, t))
    except (OSError, ValueError) as e:
        progress.close("Abgebrochen")
        print(f"\nTriage nicht möglich: {e}", file=sys.stderr)
        return 1
    progress.close("Fertig")
    print(file=sys.stderr)
    print(format_report(result, all_clips=args.alle))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(as_dict(result), f, ensure_ascii=False, indent=1)
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    from tonmischung.mcp_server import run
    return run(factory.load_settings())


def _cmd_tools(args: argparse.Namespace) -> int:
    """Welche Werkzeuge (ffmpeg, bmx …) die Auslieferung findet und woher – mitgeliefert, eigener Pfad, Homebrew."""
    from tonmischung.delivery import tools as W
    s = factory.load_settings()
    print(W.report(W.configured({p: s.get(p) for p in s.fields if p.startswith(W.SETTINGS_PREFIX)})))
    return 0


def _cmd_selftest(args: argparse.Namespace) -> int:
    """Passt das Paket zu diesem Mac? Architektur, Python-Module, Werkzeuge (Apple-Silicon-Umstellung)."""
    from tonmischung import selftest
    head, checks = selftest.run()
    print(selftest.summary(head, checks))
    return 0 if all(c.ok for c in checks) else 1


def _cmd_launchagent(args: argparse.Namespace) -> int:
    """plist für /Library/LaunchAgents ausgeben (verteilt über die Geräteverwaltung) – installiert nichts."""
    from tonmischung.core import launchagent
    sys.stdout.buffer.write(launchagent.plist(args.app))
    return 0


def _cmd_mdm_doc(args: argparse.Namespace) -> int:
    from tonmischung.config import mdm_doc
    if args.plist:
        sys.stdout.buffer.write(mdm_doc.plist())
    else:
        sys.stdout.write(mdm_doc.markdown())
    return 0


def _cmd_licenses(args: argparse.Namespace) -> int:
    from tonmischung.about import markdown
    text = markdown(__version__)
    if args.datei:
        with open(args.datei, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"Geschrieben: {args.datei}")
    else:
        print(text)
    return 0


def _cmd_api_doc(args: argparse.Namespace) -> int:
    from tonmischung.app.api import markdown
    sys.stdout.write(markdown())
    return 0


def _cmd_app(args: argparse.Namespace) -> int:
    from tonmischung.app.main import run
    if args.autopilot and not (args.selbsttest and args.mock):
        print("--autopilot nur zusammen mit --selbsttest und --mock (Testordner)", file=sys.stderr)
        return 1
    return run(mock=args.mock, selftest=args.selbsttest, auto=args.autopilot)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ottilie", description="Ottilie – Tonmischung mit Helmut und Pro Tools")
    sub = p.add_subparsers(dest="befehl", required=True)
    sub.add_parser("version", help="Version anzeigen")
    e = sub.add_parser("einstellungen", help="Einstellungen anzeigen oder lokal setzen")
    e.add_argument("pfad", nargs="?", default="", help="Punktpfad oder Anfang davon")
    e.add_argument("wert", nargs="?", help="neuer Wert (JSON), nur mit vollständigem Pfad")
    u = sub.add_parser("einstellungen-uebernehmen",
                       help="Einstellungen aus PunchBuddy (~/.punchbuddy/settings.json) einmalig übernehmen")
    u.add_argument("--datei", help="andere PunchBuddy-Einstellungsdatei")
    u.add_argument("--probe", action="store_true", help="nur anzeigen, nichts speichern")
    u.add_argument("--ueberschreiben", action="store_true", help="auch lokal schon gesetzte Werte ersetzen")
    sub.add_parser("status", help="Pro Tools: Verbindung, offene Session, Transport (nur lesen)")
    sub.add_parser("speichern", help="offene Session in Pro Tools speichern")
    h = sub.add_parser("helmut", help="Beiträge in Helmut")
    hs = h.add_subparsers(dest="aktion", required=True)
    hs.add_parser("liste", help="Beiträge mit Zustand der Mischung")
    hu = hs.add_parser("uebernehmen", help="Beitrag für diesen Mischer und Raum übernehmen")
    hu.add_argument("beitrag", help="Projekt-ID")
    hf = hs.add_parser("freigeben", help="Übernahme aufheben (Zurückgeben)")
    hf.add_argument("beitrag", help="Projekt-ID")
    hf.add_argument("--grund", required=True,
                    choices=list(RELEASE_REASONS))
    hf.add_argument("--naechstes", choices=(KEEP_LAST, KEEP_NEW), default=KEEP_NEW,
                    help="beim nächsten Übernehmen: letzten Stand als Kopie öffnen oder neu importieren")
    g = sub.add_parser("holen", help="Beitrag aus Helmut holen: übernehmen, Session aus Template, AAF")
    g.add_argument("beitrag", help="Projekt-ID")
    g.add_argument("--template", help="„Templategruppe/Name“, sonst Standard aus Meine Templates")
    g.add_argument("--ja", action="store_true",
                   help="ohne Rückfrage: offene, nicht ausgelieferte Session speichern und schließen, "
                        "Triage-Vorschau bestätigen")
    g.add_argument("--ohne-video", action="store_true", help="Referenzvideo nicht importieren")
    g.add_argument("--kein-fenster", action="store_true", help="nur Terminalanzeige, kein Fortschrittsfenster")
    g.add_argument("--ohne-triage", action="store_true",
                   help="ohne AAF-Triage: Match Tracks auf gleichnamige Spuren (sonst laut Einstellung)")
    g.add_argument("--fehlende-spuren", metavar="ANTWORT",
                   help="AAF-Spuren, die im Template fehlen: \"ab A11\" | weglassen | \"Audio 1=A5,Audio 2=ab A11\" "
                        "(sonst Frage im Terminal)")
    z = sub.add_parser("zurueckliefern",
                       help="Mischung an Helmut zurückliefern: konsolidieren, R128, nur WAVs an Helmut")
    z.add_argument("beitrag", help="Projekt-ID")
    z.add_argument("--spuren", help="Exportspuren kommagetrennt, sonst aus der Spurtabelle")
    z.add_argument("--ziel", help="Übergabeordner ({projektId} wird ersetzt), sonst aus den Einstellungen")
    z.add_argument("--ja", action="store_true",
                   help="andere offene, nicht ausgelieferte Session ohne Rückfrage speichern und schließen")
    z.add_argument("--kein-fenster", action="store_true", help="nur Terminalanzeige, kein Fortschrittsfenster")
    a = sub.add_parser("app", help="Fenster und Menüleisten-Symbol öffnen (M6a)")
    a.add_argument("--mock", action="store_true", help="Helmut-Mock mit den Test-AAFs, Sessions im Testordner")
    a.add_argument("--selbsttest", action="store_true", help="Fenster öffnen, Verbindung prüfen, beenden")
    a.add_argument("--autopilot", default="", metavar="ABLAUF",
                   help="nur mit --selbsttest und --mock: z. B. holen:tw – klickt das Holen im Fenster durch")
    w = sub.add_parser("watchdog", help="Watchdog (Menüleiste 🐕): Ottilie neu starten oder beenden")
    w.add_argument("app_args", nargs=argparse.REMAINDER, help="Argumente für den Neustart der App")
    d = sub.add_parser("diagnose", help="Diagnosebericht als Textdatei (Standard: Schreibtisch)")
    d.add_argument("--ordner", help="Zielordner")
    d.add_argument("--ohne-pro-tools", action="store_true", help="Pro Tools nicht abfragen")
    n = sub.add_parser("nachtragen", help="Helmut-Änderungen nachtragen, die bei einem Ausfall liegen blieben")
    n.add_argument("--nur-zeigen", action="store_true", help="nur die Warteschlange zeigen")
    n.add_argument("--verwerfen", metavar="KENNUNG", help="Konflikt bewusst abschließen (Helmut bleibt, wie es ist)")
    sub.add_parser("mcp", help="MCP-Server (stdio) für KI-Assistenten – Werkzeuge über die laufende App")
    sub.add_parser("api-doku", help="Beschreibung der Schnittstelle (Markdown) ausgeben")
    lz = sub.add_parser("lizenzen", help="Mitgelieferte Software mit Lizenz, Urheber und Projekt (Markdown)")
    lz.add_argument("--datei", help="statt auszugeben in diese Datei schreiben (z. B. docs/LIZENZEN.md)")
    la = sub.add_parser("launchagent",
                        help="LaunchAgent-plist für den Start bei der Anmeldung ausgeben (für die Geräteverwaltung)")
    la.add_argument("--app", default="/Applications/Ottilie.app", help="Ort von Ottilie.app")
    sub.add_parser("werkzeuge", help="Werkzeuge der Auslieferung (ffmpeg, bmx …) und ihre Herkunft anzeigen")
    sub.add_parser("selbsttest", help="Passt das Paket zu diesem Mac? (Architektur, Python-Module, Werkzeuge)")
    jd = sub.add_parser("mdm-doku", help="Schlüssel für das Konfigurationsprofil (Markdown) bzw. --plist als Vorlage")
    jd.add_argument("--plist", action="store_true", help="Vorlage (plist) mit den Hausstandard-Werten ausgeben")
    t = sub.add_parser("triage", help="AAF beurteilen: O-Ton, Atmo, Musik je Spur und Clip (nur lesen)")
    t.add_argument("aaf", help="Pfad zur AAF")
    t.add_argument("--schwelle", type=int, metavar="PROZENT",
                   help="Mindest-Sicherheit in Prozent, sonst aus den Einstellungen (80)")
    t.add_argument("--alle", action="store_true", help="alle Clips zeigen, nicht nur abweichende")
    t.add_argument("--json", metavar="DATEI", help="Ergebnis zusätzlich als JSON schreiben")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    from tonmischung.paths import migrate
    moved = migrate()                               # einmalig: Daten von „Tonmischung“ nach „Ottilie“ kopieren
    if moved:
        print(moved, file=sys.stderr)
    if argv[:1] == ["watchdog"]:                    # Rest unverändert für den Neustart der App (z. B. --mock)
        from tonmischung import watchdog
        return watchdog.main(argv[1:])
    args = build_parser().parse_args(argv)
    if args.befehl == "version":
        print(f"Ottilie {__version__}")
        return 0
    commands = {"einstellungen": _cmd_settings, "einstellungen-uebernehmen": _cmd_import_punchbuddy,
                "status": _cmd_status, "speichern": _cmd_save,
                "helmut": _cmd_helmut, "holen": _cmd_fetch, "zurueckliefern": _cmd_deliver,
                "triage": _cmd_triage, "app": _cmd_app, "mcp": _cmd_mcp, "api-doku": _cmd_api_doc,
                "lizenzen": _cmd_licenses, "mdm-doku": _cmd_mdm_doc, "werkzeuge": _cmd_tools,
                "selbsttest": _cmd_selftest, "launchagent": _cmd_launchagent,
                "diagnose": _cmd_diagnose, "nachtragen": _cmd_catch_up}
    return commands[args.befehl](args)
