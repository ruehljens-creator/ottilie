"""Betriebsart Übergabeordner mit echtem Pro Tools – Testablauf ohne Fenster (Entwicklung, Mac mit Pro Tools).

    .venv/bin/python tools/pt_uebergabe_test.py vorbereiten <eingebundene Freigabe>
    .venv/bin/python tools/pt_uebergabe_test.py liste      <Arbeitsordner>
    .venv/bin/python tools/pt_uebergabe_test.py holen      <Arbeitsordner> [--template Gruppe/Name]
    .venv/bin/python tools/pt_uebergabe_test.py liefern    <Arbeitsordner> --spuren A1,A2 [--empfaenger-aus]
    .venv/bin/python tools/pt_uebergabe_test.py nachtragen <Arbeitsordner>
    .venv/bin/python tools/pt_uebergabe_test.py ansehen    <Arbeitsordner>

„vorbereiten“ erzeugt eigene Testmedien (Töne, AAF mit eingebetteten Tönen ab 10:00:00:00, MXF mit Testbild und
Timecode) und legt einen Filmordner in `<Freigabe>/_ottilie_pttest-<Zeit>/` an; die Übergabe-JSON kommt zuletzt.
Arbeitsordner: `testfiles/pttest-<Zeit>/` (Einstellungen, Nachtrag, Empfänger-Protokoll) – Jens' Einstellungen bleiben
unberührt. Sessions nur im Testordner `~/Documents/SM Session/Ottilie-Test/`. Fragen des Ablaufs werden vorsichtig
beantwortet: fehlende Spuren → „ab A11“, Triage-Vorschau → ja, andere offene Session → nur ja, wenn sie im
Testordner liegt, sonst nein. Die Fertigmeldung geht an einen Empfänger auf 127.0.0.1, der nur für die Dauer des
Befehls läuft.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

FILM = "Testfilm"
TEST_FOLDER = "~/Documents/SM Session/Ottilie-Test"      # Testordner (Jens 11.11.2026: SM Session für Tests)
SECONDS = 20


# ── Testmedien ────────────────────────────────────────────────────────────────
def _media(dst: Path) -> dict[str, Path]:
    import aaf2
    work = dst / "_medien"
    work.mkdir(parents=True)
    tones = []
    for i, freq in enumerate((440, 1000), 1):
        wav = work / f"ton{i}.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency={freq}:sample_rate=48000:duration={SECONDS}", "-af", "volume=-20dB",
                        "-c:a", "pcm_s24le", "-ac", "1", str(wav)], check=True)
        tones.append(wav)
    mxf = work / f"{FILM}.mxf"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=1920x1080:rate=25:duration={SECONDS}", "-c:v", "dnxhd", "-b:v", "36M",
                    "-pix_fmt", "yuv422p", "-timecode", "10:00:00:00", "-f", "mxf", str(mxf)], check=True)
    aaf = work / f"{FILM}.aaf"
    with aaf2.open(str(aaf), "w") as f:
        comp = f.create.CompositionMob(FILM)
        comp.usage = "Usage_TopLevel"
        f.content.mobs.append(comp)
        tc = comp.create_timeline_slot(25)
        tc.segment = f.create.Timecode(25, False)
        tc.segment.start = 900000                       # 10:00:00:00
        for i, wav in enumerate(tones, 1):
            master = f.create.MasterMob(f"Ton {i}")
            f.content.mobs.append(master)
            slot = master.import_audio_essence(str(wav), 25)
            s = comp.create_sound_slot(edit_rate=25)
            s.name = f"A{i}"
            seq = f.create.Sequence(media_kind="sound")
            seq.components.append(master.create_source_clip(slot.slot_id, length=slot.segment.length))
            s.segment = seq
    return {"aaf": aaf, "video": mxf}


def prepare(share: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    work = REPO / "testfiles" / f"pttest-{stamp}"
    work.mkdir(parents=True)
    root = share / f"_ottilie_pttest-{stamp}"
    os.mkdir(root)                                       # neu, nie ein vorhandener Ordner
    film = root / FILM
    film.mkdir()
    media = _media(work)
    files = []
    for kind, src in media.items():
        shutil.copyfile(src, film / src.name)
        files.append({"typ": kind, "name": src.name, "groesse": os.path.getsize(film / src.name)})
    handover = {"format": "uebergabe/1", "erstellt": datetime.now().astimezone().isoformat(timespec="seconds"),
                "uebergabe_nr": 1, "beitrag": {"id": "PTTEST-1", "titel": "Testfilm Pro Tools", "sendung": "Test",
                                               "cutter": "Test", "dringend": False},
                "herkunft": {"schnittraum": "Schnitt Test"}, "dateien": files}
    (film / "uebergabe.json").write_text(json.dumps(handover, ensure_ascii=False, indent=1))      # zuletzt
    sessions = Path(TEST_FOLDER).expanduser() / f"pttest-{stamp}"
    sessions.mkdir(parents=True)
    (work / "stand.json").write_text(json.dumps({"wurzel": str(root), "sessions": str(sessions)}, indent=1))
    print(f"Arbeitsordner: {work}\nÜbergabeordner: {root}\nSessions: {sessions}")
    return work


# ── Ottilie wie im Fenster, aber mit eigenen Einstellungen ───────────────────
class _Receiver(http.server.BaseHTTPRequestHandler):
    log_path: Path
    down = False

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.down:
            self.send_response(503)
            self.end_headers()
            return
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(body.decode("utf-8") + "\n")
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def _context(work: Path, receiver_port: int):
    from tonmischung import __version__, factory
    from tonmischung.app.context import AppContext
    from tonmischung.config.settings import Settings
    from tonmischung.core.catchup import CatchUpQueue
    from tonmischung.dropfolder.mode import ModeClient
    from tonmischung.core import mix_log as ML
    from tonmischung.core.fileio import inside
    state = json.loads((work / "stand.json").read_text())
    if not inside(os.path.realpath(state["sessions"]), os.path.realpath(os.path.expanduser(TEST_FOLDER))):
        raise SystemExit(f"Sessions nur im Testordner {TEST_FOLDER} – {state['sessions']} liegt außerhalb")
    if not os.path.basename(state["wurzel"]).startswith("_ottilie_pttest-"):
        raise SystemExit(f"Übergabeordner muss ein eigener Testordner sein: {state['wurzel']}")
    s = Settings(local_path=work / "einstellungen.json", managed=None)
    F = "system.uebergabeordner."
    for key, value in {F + "betriebsart": "uebergabeordner", F + "wurzelordner": state["wurzel"],
                       F + "wartezeit_s": 2, F + "fertigmeldung.aktiv": True,
                       F + "fertigmeldung.url": f"http://127.0.0.1:{receiver_port}/fertig",
                       F + "fertigmeldung.wiederholungen": 1, F + "fertigmeldung.zeitlimit_s": 5,
                       "import.session_ablage.modus": "lokal_spiegelung",
                       "import.session_ablage.lokaler_ordner": state["sessions"]}.items():
        if s.get(key) != value:
            s.set_local(key, value)
    mixer, room = factory.identity(s)
    ctx = AppContext(s, factory.helmut_client(s), factory.ptsl_client(s), mixer, room, state["sessions"],
                     None, __version__, queue=CatchUpQueue(str(work / "nachtrag.json")))

    def folder():
        return factory.folder_client(s, lambda: (ctx.mixer, ctx.room), safe_root=state["wurzel"])
    ctx.helmut = ModeClient(ctx.helmut, folder, lambda: factory.operating_mode(s))
    ctx.extra["mischungen"] = ML.MixLog(str(work / "mischungen.json"))     # nicht das Verzeichnis dieses Macs
    assert ctx.folder_mode
    return ctx


def _run_job(start, ctx, args: dict) -> dict:
    from tonmischung.app.bridge import Bridge
    from tonmischung.app.jobs import JobRunner
    from tonmischung.core.track_match import FROM_A11
    events: list[dict] = []
    runner = JobRunner(Bridge(lambda kind, data=None: events.append({"kind": kind, "data": data}), inline=True))
    runner.ptsl = ctx.ptsl
    job = start(ctx, runner, args)
    print(f"Auftrag: {job['title']}")
    answered: set[int] = set()
    last = ""
    while True:
        snap = runner.current() or {}
        q = snap.get("question")
        if q and q["id"] not in answered:
            answered.add(q["id"])
            kind = q.get("kind")
            print(f"  Frage „{q.get('title')}“ ({kind})")
            if kind == "fehlende_spuren":
                value = {t: FROM_A11 for t in q.get("missing", [])}
            elif kind == "triage_vorschau":
                value = True
            elif kind == "offene_session":                # speichern und schließen nur im Testordner
                value = _open_in_test_folder(ctx)
            else:                                        # offene Session, Medien, Überdecken, Unbekanntes: nein
                value = False
            print(f"    Antwort: {value}")
            runner.answer(q["id"], value)
        text = f"{snap.get('state')} {snap.get('text') or ''}".strip()
        if text != last:
            print(f"  {text}")
            last = text
        if not runner.busy() and snap.get("state") in ("fertig", "fehler", "abgebrochen"):
            break
        time.sleep(0.5)
    snap = runner.current() or {}
    if snap.get("state") != "fertig":
        print(f"FEHLER: {snap.get('state')}: {snap.get('error')}")
    return snap


def _open_in_test_folder(ctx) -> bool:
    from tonmischung.core.fileio import inside
    session = ctx.ptsl.open_session()
    folder = os.path.realpath(os.path.expanduser(TEST_FOLDER))
    return bool(session and inside(os.path.realpath(session.path), folder))


def _port(work: Path) -> int:
    """Fester Port je Arbeitsordner – die Adresse steht in den Einstellungen und im Nachtrag."""
    p = work / "port.txt"
    if not p.exists():
        with socketserver.TCPServer(("127.0.0.1", 0), _Receiver) as probe:
            p.write_text(str(probe.server_address[1]))
    return int(p.read_text())


def _serve(work: Path, down: bool):
    port = _port(work)
    _Receiver.log_path, _Receiver.down = work / "empfaenger.jsonl", down
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", port), _Receiver)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("befehl", choices=("vorbereiten", "liste", "holen", "liefern", "nachtragen", "ansehen"))
    p.add_argument("pfad")
    p.add_argument("--template", default="Jens/Default")
    p.add_argument("--spuren", default="")
    p.add_argument("--empfaenger-aus", action="store_true", help="Empfänger antwortet 503 (Ausfall)")
    p.add_argument("--neue-fassung-ok", action="store_true")
    a = p.parse_args()
    if a.befehl == "vorbereiten":
        prepare(Path(a.pfad).expanduser())
        return 0
    work = Path(a.pfad).resolve()
    srv, port = _serve(work, a.empfaenger_aus)
    ctx = _context(work, port)
    try:
        film_id = FILM
        if a.befehl == "liste":
            for c in ctx.helmut.list_for_mixing():
                print(f"{c.id}: {c.title} – {c.mix.state or 'frei'} {c.mix.mixer} {c.mix.room} "
                      f"{(c.raw or {}).get('zustand', '')}")
        elif a.befehl == "holen":
            from tonmischung.app import fetch_app
            ctx.service().take(film_id)
            group, name = a.template.split("/", 1)
            snap = _run_job(fetch_app.start_fetch, ctx, {"id": film_id, "group": group, "name": name})
            print(json.dumps(snap.get("result"), ensure_ascii=False, indent=1, default=str))
        elif a.befehl == "liefern":
            from tonmischung.app import deliver_app
            from tonmischung.app.mirror_app import MirrorWatcher

            def owner(path: str):
                from tonmischung.core.session_rules import same_session
                return next((c for c in ctx.helmut.list_for_mixing() if c.mix.session_path
                             and same_session(c.mix.session_path, path)), None)
            ctx.extra["spiegel"] = MirrorWatcher(ctx, owner=owner)
            snap = _run_job(deliver_app.start_deliver, ctx, {"id": film_id, "tracks": a.spuren,
                                                             "neue_fassung_ok": a.neue_fassung_ok})
            print(json.dumps(snap.get("result"), ensure_ascii=False, indent=1, default=str))
            print("Spiegel:", ctx.extra["spiegel"].state)
        elif a.befehl == "nachtragen":
            from tonmischung.helmut.catchup import replay
            r = replay(ctx.queue, ctx.helmut)
            print(f"nachgetragen: {len(r.written)}, offen: {ctx.queue.summary()['offen']}")
        elif a.befehl == "ansehen":
            root = json.loads((work / "stand.json").read_text())["wurzel"]
            for r, ds, fs in os.walk(root):
                for n in sorted(ds + fs):
                    path = os.path.join(r, n)
                    print(("  " * (r.count(os.sep) - root.count(os.sep))) + n
                          + ("" if os.path.isdir(path) else f"  ({os.path.getsize(path)} B)"))
            log = work / "empfaenger.jsonl"
            if log.exists():
                print("Empfänger:", log.read_text())
    finally:
        ctx.ptsl.reset()
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
