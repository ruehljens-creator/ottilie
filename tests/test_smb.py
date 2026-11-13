"""Betriebsart Übergabeordner auf einem echten Netzspeicher (SMB) – nur mit TONMISCHUNG_SMB_TEST_DIR.

Aufruf (Freigabe vorher im Finder bzw. mit `mount_smbfs` einbinden):

    TONMISCHUNG_SMB_TEST_DIR=/Volumes/<Freigabe> .venv/bin/python -m pytest -m smb tests/test_smb.py -v -s

Alles geschieht in einem eigenen, neu angelegten Ordner `<Freigabe>/_ottilie_smbtest-<Zeit>/`; außerhalb wird nichts
gelesen, geschrieben oder gelöscht. Am Ende wird nur dieser Ordner entfernt (die Dateien darin sind Attrappen dieses
Tests, keine Medien) – mit TONMISCHUNG_SMB_KEEP=1 bleibt er zum Ansehen liegen. Messwerte (Uhrabweichung Mac ↔
Speicher, Rechte, Symlinks) stehen am Ende in `testfiles/smb-test-<Zeit>.md` im Repository (nicht eingecheckt).
"""
import http.server
import json
import multiprocessing as mp
import os
import shutil
import socketserver
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

ENV = "TONMISCHUNG_SMB_TEST_DIR"
pytestmark = [pytest.mark.smb,
              pytest.mark.skipif(not os.environ.get(ENV), reason=f"SMB-Test nur mit {ENV}=<eingebundene Freigabe>")]
REPO = Path(__file__).resolve().parent.parent
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
REPORT: dict = {"zeit": STAMP, "ziel": os.environ.get(ENV, ""), "ergebnisse": {}}


# ── Testordner und Bericht ─────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def base():
    share = Path(os.environ[ENV]).expanduser()
    assert share.is_dir(), f"{share} ist kein Ordner – Freigabe eingebunden?"
    folder = share / f"_ottilie_smbtest-{STAMP}"
    os.mkdir(folder)                                       # neu, nie einen vorhandenen Ordner benutzen
    REPORT["testordner"] = str(folder)
    REPORT["mount"] = _mount_info(share)
    yield folder
    _write_report()
    if os.environ.get("TONMISCHUNG_SMB_KEEP") != "1":
        assert folder.name.startswith("_ottilie_smbtest-") and folder.parent == share
        shutil.rmtree(folder)                              # nur der eigene Testordner


def _mount_info(path: Path) -> str:
    import subprocess
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    best = ""
    for line in out.splitlines():
        parts = line.split(" on ", 1)
        if len(parts) == 2:
            point = parts[1].split(" (", 1)[0]
            if str(path).startswith(point) and len(point) > len(best.split(" on ", 1)[-1].split(" (", 1)[0]):
                best = line
    return best


def _note(key: str, value) -> None:
    REPORT["ergebnisse"][key] = value


def _write_report() -> None:
    out = REPO / "testfiles" / f"smb-test-{STAMP}.md"
    out.parent.mkdir(exist_ok=True)
    lines = [f"# SMB-Test Übergabeordner – {STAMP}", "", f"- Ziel: `{REPORT['ziel']}`",
             f"- Testordner: `{REPORT.get('testordner', '')}`", f"- Einbindung: `{REPORT.get('mount', '')}`", ""]
    for k, v in REPORT["ergebnisse"].items():
        lines.append(f"- **{k}:** {v}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nBericht: {out}")


# ── Hilfen ─────────────────────────────────────────────────────────────────────
def _film(root: Path, name: str, aaf=10, mxf=20, json_last=True, nr=1) -> Path:
    folder = root / name
    folder.mkdir()
    (folder / f"{name}.aaf").write_bytes(b"A" * aaf)
    (folder / f"{name}.mxf").write_bytes(b"M" * mxf)
    if json_last:
        _handover(folder, name, aaf, mxf, nr)
    return folder


def _handover(folder: Path, name: str, aaf: int, mxf: int, nr: int = 1) -> None:
    data = {"format": "uebergabe/1", "erstellt": datetime.now().astimezone().isoformat(), "uebergabe_nr": nr,
            "beitrag": {"id": name, "titel": f"Test {name}", "sendung": "SMB-Test"},
            "herkunft": {"schnittraum": "Schnitt Test"},
            "dateien": [{"typ": "aaf", "name": f"{name}.aaf", "groesse": aaf},
                        {"typ": "video", "name": f"{name}.mxf", "groesse": mxf}]}
    (folder / "uebergabe.json").write_text(json.dumps(data), encoding="utf-8")


def _no_appledouble(top: Path) -> None:
    """Keine Begleitdateien von macOS („._*“) unter `top` – Helmut soll nur die Lieferung sehen."""
    found = [os.path.join(r, n) for r, ds, fs in os.walk(top) for n in ds + fs if n.startswith("._")]
    assert not found, f"Begleitdateien von macOS: {found}"


def _client(root: Path, mixer="Jens", room="Ton 3", **cfg):
    from tonmischung.dropfolder.client import FolderClient, FolderConfig
    config = FolderConfig(root=str(root), **{"wait_s": 0, **cfg})
    return FolderClient(lambda: config, lambda: (mixer, room))


def _svc(client, mixer="Jens", room="Ton 3", queue=None):
    from tonmischung.helmut.service import MixService
    return MixService(client, mixer, room, queue=queue, now=lambda: "jetzt", sleep=time.sleep)


# ── 1. Belegen: N Prozesse gleichzeitig ────────────────────────────────────────
def _claim_child(path: str, i: int, start_at: float, delay: float, out) -> None:
    from tonmischung.dropfolder import lockfile as L
    from tonmischung.core.mixstate import MixState
    if delay:
        real = L._write

        def slow(p, d):
            time.sleep(delay)                              # absichtlich langsamer Schreiber
            real(p, d)
        L._write = slow
    while time.time() < start_at:
        time.sleep(0.001)
    try:
        L.claim(path, L.content(MixState(), True, f"Ton {i}", f"M{i}", "t", 1))
        out.put(("gewonnen", i))
    except L.LockTaken as e:
        out.put(("belegt", str(e)))
    except Exception as e:                                 # noqa: BLE001 – im Bericht sichtbar machen
        out.put(("fehler", f"{type(e).__name__}: {e}"))


@pytest.mark.parametrize("delay", [0.0, 0.5])
def test_belegen_genau_ein_gewinner(base, delay):
    from tonmischung.dropfolder import lockfile as L
    ctx = mp.get_context("spawn")
    rounds, n, results = 5, 8, []
    for r in range(rounds):
        path = str(base / f"sperre-{delay}-{r}.json")
        if r % 2:                                          # jede zweite Runde: freie Sperrdatei liegt schon da
            L.update(path, lambda cur: {"format": L.FORMAT, "gesperrt": False, "mischung": {}})
        q = ctx.Queue()
        start = time.time() + 2.0
        procs = [ctx.Process(target=_claim_child, args=(path, i, start, delay if i % 2 else 0.0, q))
                 for i in range(n)]
        for p in procs:
            p.start()
        got = [q.get(timeout=60) for _ in procs]
        for p in procs:
            p.join(30)
        winners = [g[1] for g in got if g[0] == "gewonnen"]
        errors = [g[1] for g in got if g[0] == "fehler"]
        results.append((len(winners), errors))
        assert not errors, errors
        assert len(winners) == 1, got
        assert L.read(path)["raum"] == f"Ton {winners[0]}"
        assert not os.path.exists(path + ".claim")         # Hilfsdatei aufgeräumt
    _note(f"Belegen ({n} Prozesse, Verzögerung {delay} s, {rounds} Runden)", "bestanden – je Runde genau ein Gewinner")


# ── 2. Umbenennen nach Audio Export und _alt ───────────────────────────────────
def test_lieferung_umbenennen(base):
    from tonmischung.dropfolder.client import export_target
    film = base / "lieferung"
    film.mkdir()

    def deliver(stamp: str, names: list[str], mode="ersetzen") -> list[str]:
        t = export_target(str(film), "Audio Export", mode, stamp, root=str(base))
        staged = []
        for n in names:
            p = Path(t.copy_dir) / n
            p.write_bytes(os.urandom(1 << 20) + n.encode())
            staged.append(str(p))
        return t.finish(staged)
    first = deliver("20261111-100000", ["ST.wav", "IT.wav", "lieferung_metadaten.json"])
    sizes = {os.path.basename(p): os.path.getsize(p) for p in first}
    second = deliver("20261111-110000", ["ST.wav", "lieferung_metadaten.json"])
    exp = film / "Audio Export"
    assert sorted(os.listdir(exp)) == ["ST.wav", "_alt", "lieferung_metadaten.json"]
    _no_appledouble(film)
    alt = os.listdir(exp / "_alt")
    assert len(alt) == 1
    moved = {n: os.path.getsize(exp / "_alt" / alt[0] / n) for n in os.listdir(exp / "_alt" / alt[0])}
    assert moved == sizes                                 # nichts verloren, nichts verändert
    assert second[-1].endswith("lieferung_metadaten.json")
    deliver("20261111-120000", ["ST.wav"], mode="zeitstempel")
    assert (exp / "20261111-120000" / "ST.wav").is_file()
    _no_appledouble(film)
    _note("Umbenennen nach Audio Export / _alt", f"bestanden – _alt/{alt[0]}, Größen gleich, nichts verloren")


# ── 3. Rechte und Zeitstempel ──────────────────────────────────────────────────
def test_rechte_und_uhr(base):
    from tonmischung.dropfolder import lockfile as L
    from tonmischung.core.mixstate import MixState
    path = str(base / "rechte.json")
    L.claim(path, L.content(MixState(), True, "Ton 3", "Jens", "t", 1))
    mode = os.stat(path).st_mode & 0o777
    offsets = []
    for _ in range(5):
        before = time.time()
        server = L._server_now(str(base))
        after = time.time()
        offsets.append(server - (before + after) / 2)
        time.sleep(0.2)
    offset = sorted(offsets)[len(offsets) // 2]
    _note("Dateirechte der Sperrdatei", f"{oct(mode)} (erwartet 0o664; auf SMB bestimmt oft der Server)")
    _note("Uhrabweichung Speicher − Mac (Median aus 5)", f"{offset:+.2f} s (Einzelwerte: "
                                                         f"{', '.join(f'{o:+.2f}' for o in offsets)})")
    # verwaiste Hilfsdatei nach Speicheruhr: Änderungszeit 10 min vor der Speicherzeit
    claim = base / "verwaist.json.claim"
    claim.write_text("abgestürzt")
    old = L._server_now(str(base)) - 600
    os.utime(claim, (old, old))
    utime_ok = abs(os.lstat(claim).st_mtime - old) < 5
    L.claim(str(base / "verwaist.json"), L.content(MixState(), True, "Ton 3", "Jens", "t", 1))
    assert not claim.exists()
    _note("Verwaiste .claim nach Speicheruhr", f"bestanden – beiseitegelegt (utime vom Server übernommen: {utime_ok})")
    assert mode & 0o044, "Sperrdatei für andere Konten nicht lesbar"


# ── 4. Symlinks ────────────────────────────────────────────────────────────────
def test_symlinks_abgelehnt(base):
    from tonmischung.dropfolder.client import UnsafeTarget, export_target
    outside = base / "draussen"
    outside.mkdir()
    film = base / "symfilm"
    film.mkdir()
    try:
        os.symlink(outside, film / "Audio Export")
    except OSError as e:
        _note("Symlinks", f"Server lässt keine Symlinks zu ({e.strerror}) – nichts zu prüfen")
        pytest.skip("keine Symlinks auf dieser Freigabe")
    with pytest.raises(UnsafeTarget):
        export_target(str(film), "Audio Export", "ersetzen", "x", root=str(base))
    root = base / "symroot"
    root.mkdir()
    _film(root, "echt")
    os.symlink(outside, root / "verweis")
    assert [c.id for c in _client(root).list_for_mixing()] == ["echt"]
    assert os.listdir(outside) == []
    _note("Symlinks", "Server erlaubt sie; „Audio Export“ als Verweis abgelehnt, Verweis-Filmordner nicht gelistet")


# ── 5. Erkennung beim langsamen Übertragen ─────────────────────────────────────
def test_erkennung_langsame_uebertragung(base):
    from tonmischung.dropfolder import handover as H
    root = base / "eingang"
    root.mkdir()
    size, chunk = 64 << 20, 4 << 20
    folder = root / "langsam"
    folder.mkdir()
    (folder / "langsam.aaf").write_bytes(b"A" * 10)
    c = _client(root, wait_s=3)
    states = []

    def writer():
        with open(folder / "langsam.mxf", "wb") as f:
            for _ in range(size // chunk):
                f.write(os.urandom(chunk))
                f.flush()
                time.sleep(0.3)
    t = threading.Thread(target=writer)
    t.start()
    while t.is_alive():
        states.append(c.get("langsam").raw["uebergabe"])
        time.sleep(0.5)
    t.join()
    assert set(states) == {H.TRANSFER}                     # ohne JSON nie „bereit“
    _handover(folder, "langsam", 10, size)
    first = c.get("langsam").raw["uebergabe"]
    assert first == H.TRANSFER                             # 3 s Wartezeit nach neuer JSON
    time.sleep(3.5)
    assert c.get("langsam").raw["uebergabe"] == H.READY
    # JSON vor fertigen Medien → unvollständig
    early = root / "frueh"
    early.mkdir()
    (early / "frueh.aaf").write_bytes(b"A" * 10)
    (early / "frueh.mxf").write_bytes(b"M" * 1000)
    _handover(early, "frueh", 10, 5000)
    time.sleep(3.5)
    got = c.get("frueh").raw
    assert got["uebergabe"] == H.INCOMPLETE and "noch nicht vollständig" in got["probleme"][0]
    # übernehmen, dann JSON weg (neue Fassung kommt) → wird übertragen + neue Übergabe
    svc = _svc(c)
    svc.take("langsam")
    os.remove(folder / "uebergabe.json")
    got = c.get("langsam").raw
    assert got["uebergabe"] == H.TRANSFER and got["neue_uebergabe"]
    _note("Erkennung", f"bestanden – während des Schreibens ({len(states)} Abfragen) „wird übertragen“, 3 s nach der "
                       "JSON „bereit“, JSON vor den Medien „unvollständig“, JSON weg „neue Übergabe“")


# ── 6. Ende zu Ende mit Pro-Tools-Attrappe und lokalem Empfänger ───────────────
class _Receiver(http.server.BaseHTTPRequestHandler):
    bodies: list = []
    fail_next = 0

    def do_POST(self):  # noqa: N802
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(data)
        if type(self).fail_next:
            type(self).fail_next -= 1
            self.send_response(503)
        else:
            self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


def test_ende_zu_ende(base, tmp_path, monkeypatch):
    from fakes import FakeEngine, _Track
    from tonmischung.core import mixstate
    from tonmischung.core.catchup import CatchUpQueue
    from tonmischung.dropfolder.client import FolderClient, FolderConfig
    from tonmischung.dropfolder.notify import NotifyConfig
    from tonmischung.helmut.catchup import replay
    from tonmischung.ptsl.client import PtslClient
    from tonmischung.sessions import pt_export
    from tonmischung.workflow.deliver import deliver
    import itertools
    monkeypatch.setattr(time, "sleep", lambda s: None)
    real = pt_export.wait_for_consolidated
    monkeypatch.setattr(pt_export, "wait_for_consolidated", lambda *a, **k: real(
        *a, **{**k, "stable_s": 0, "poll_s": 0, "sleep": lambda s: None, "clock": itertools.count(0, 1).__next__}))
    srv = socketserver.TCPServer(("127.0.0.1", 0), _Receiver)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Receiver.bodies, _Receiver.fail_next = [], 3            # erste Lieferung: Dienst antwortet nicht (3× 503)
    root = base / "e2e"
    root.mkdir()
    folder = _film(root, "film")
    cfg = FolderConfig(root=str(root), wait_s=0, notify_active=True,
                       notify=NotifyConfig(url=f"http://127.0.0.1:{srv.server_address[1]}/fertig", retries=2))
    c = FolderClient(lambda: cfg, lambda: ("Jens", "Ton 3"))
    q = CatchUpQueue(str(tmp_path / "nachtrag.json"))
    svc = _svc(c, queue=q)
    session = tmp_path / "Session" / "Session.ptx"
    (tmp_path / "Session" / "Audio Files").mkdir(parents=True)
    e = FakeEngine(session=("Session", str(session)))
    e.track_objs = [_Track(t, "AudioTrack", True) for t in ("ST", "IT")] + [_Track("Video 1", "VideoTrack", True)]
    svc.take("film")
    h = c.handover("film")
    svc.set_state("film", mixstate.IMPORTED, session_path=str(session), handover_nr=str(h.number))
    r = deliver("film", service=svc, ptsl=PtslClient(engine_factory=lambda: e), export_tracks=["ST", "IT"],
                loudness_tracks=["ST"], handover_root="", target=c.delivery_target("film"))
    exp = folder / "Audio Export"
    assert sorted(os.listdir(exp)) == ["IT.wav", "ST.wav", "film_metadaten.json"]
    _no_appledouble(exp)
    assert "._Audio Export" not in os.listdir(folder)
    assert not [n for n in os.listdir(folder) if n.startswith("._.ottilie")]     # Sperrdatei ohne Begleitdatei
    assert r.queued == "ausfall" and len(_Receiver.bodies) == 3      # 1 + 2 Wiederholungen, alle 503
    ids = {b["meldung_id"] for b in _Receiver.bodies}
    assert len(ids) == 1                                   # Wiederholung mit derselben meldung_id
    report = replay(q, c)                                  # Dienst wieder da: Nachtrag
    assert len(report.written) == 1 and len(_Receiver.bodies) == 4
    assert _Receiver.bodies[-1]["meldung_id"] in ids
    replay(q, c)
    assert len(_Receiver.bodies) == 4                      # nicht doppelt
    body = _Receiver.bodies[-1]
    assert body["export_ordner"] == "film/Audio Export" and body["herkunft"] == {"schnittraum": "Schnitt Test"}
    lock = json.loads((folder / ".ottilie-sperre.json").read_text())
    assert (lock["zustand"], lock["gesperrt"]) == ("ausgeliefert", False)
    assert lock["fertigmeldung"]["id"] == body["meldung_id"]
    srv.shutdown()
    _note("Ende zu Ende", "bestanden – Holen, Zurückliefern nach Audio Export, Fertigmeldung bei Ausfall eingereiht, "
                          "im Nachtrag mit derselben meldung_id zugestellt, keine Doppelmeldung")


# ── 8. Session-Spiegel unter <Filmordner>/Ton/ProTools/ ────────────────────────
def test_spiegel_ohne_begleitdateien(base, tmp_path):
    from tonmischung.workflow.mirror import VERSIONS, mirror
    src = tmp_path / "Session"
    (src / "Audio Files").mkdir(parents=True)
    (src / "Session.ptx").write_bytes(b"ptx1")
    (src / "Audio Files" / "A1.wav").write_bytes(os.urandom(1 << 16))
    dst = base / "spiegel" / "Ton" / "ProTools" / "Session"
    t = iter([datetime(2026, 11, 11, 10), datetime(2026, 11, 11, 11)])
    first = mirror(str(src), str(dst), now=lambda: next(t))
    assert len(first.copied) == 2 and not first.failed
    (src / "Session.ptx").write_bytes(b"ptx2-geaendert")
    second = mirror(str(src), str(dst), now=lambda: next(t))
    assert len(second.replaced) == 1 and not second.failed
    assert (dst / "Session.ptx").read_bytes() == b"ptx2-geaendert"
    assert (dst / VERSIONS / "20261111-110000" / "Session.ptx").read_bytes() == b"ptx1"   # nichts überschrieben
    files = [os.path.join(r, n) for r, ds, fs in os.walk(dst) for n in ds + fs if n.startswith("._")]
    files += [str(dst.parent / n) for n in os.listdir(dst.parent) if n == "._Session"]
    _note("Session-Spiegel", f"Vorversion aufgehoben, nichts überschrieben; Begleitdateien „._“ übrig: {len(files)}")
    assert not files, files
