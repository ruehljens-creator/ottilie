"""Fremde Audiodateien (Plan M7): erkennen, ins Projekt kopieren, in Helmut melden – ohne Pro Tools."""
import os
import types

import pytest

from fakes import FakeEngine
from tonmischung.app.foreign_app import ForeignWatcher
from tonmischung.config.settings import Settings
from tonmischung.core.foreign import DONE, MISSING, NEW, archive_candidates, plan
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.workflow.foreign import PREF_HINT, adopt, copy_new


def _files(tmp_path):
    session = tmp_path / "Wahl" / "Wahl.ptx"
    (tmp_path / "Wahl" / "Audio Files").mkdir(parents=True)
    session.write_bytes(b"PTX")
    inner = tmp_path / "Wahl" / "Audio Files" / "A1_01.wav"
    inner.write_bytes(b"innen")
    dl = tmp_path / "Downloads"
    dl.mkdir()
    (dl / "Hund.wav").write_bytes(b"wuff")
    (dl / "Regen.wav").write_bytes(b"pladder")
    return str(session), [(str(inner), True), (str(dl / "Hund.wav"), True), (str(dl / "Regen.wav"), True),
                          (str(tmp_path / "weg.wav"), False)]


def test_plan(tmp_path):
    session, files = _files(tmp_path)
    p = {os.path.basename(f.source): f for f in plan(files, session)}
    assert set(p) == {"Hund.wav", "Regen.wav", "weg.wav"}                       # innen zählt nicht
    assert p["Hund.wav"].status == NEW and p["Hund.wav"].target.endswith("Wahl/Audio Files/Extern/Hund.wav")
    assert p["weg.wav"].status == MISSING
    ext = tmp_path / "Wahl" / "Audio Files" / "Extern"
    ext.mkdir()
    (ext / "Hund.wav").write_bytes(b"wuff")                                    # schon übernommen
    (ext / "Regen.wav").write_bytes(b"anders")                                 # gleicher Name, anderer Inhalt
    p = {os.path.basename(f.source): f for f in plan(files, session)}
    assert p["Hund.wav"].status == DONE
    assert p["Regen.wav"].status == NEW and p["Regen.wav"].target.endswith("Extern/Regen_2.wav")


def test_gleiche_groesse_ist_nicht_gleicher_inhalt(tmp_path):
    """Review B12: „Regen.wav“ im Download-Ordner und eine andere „Regen.wav“ gleicher Größe im Projekt."""
    session, files = _files(tmp_path)
    ext = tmp_path / "Wahl" / "Audio Files" / "Extern"
    ext.mkdir(parents=True)
    src = next(p for p, _ in files if p.endswith("Regen.wav"))
    (ext / "Regen.wav").write_bytes(bytes(len(open(src, "rb").read())))   # gleiche Länge, andere Bytes
    p = {os.path.basename(f.source): f for f in plan(files, session)}
    assert p["Regen.wav"].status == NEW and p["Regen.wav"].target.endswith("Extern/Regen_2.wav")


def test_kopie_ueberschreibt_nie(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"neu")
    (tmp_path / "b.wav").write_bytes(b"alt")
    with pytest.raises(FileExistsError):
        copy_new(str(tmp_path / "a.wav"), str(tmp_path / "b.wav"))
    assert (tmp_path / "b.wav").read_bytes() == b"alt" and not (tmp_path / "b.wav.part").exists()


def test_uebernehmen_mit_uebergabe_und_asset(tmp_path):
    session, files = _files(tmp_path)
    pt = types.SimpleNamespace(file_locations=lambda: files)
    registered = []
    r = adopt(pt, session, upload_dir=str(tmp_path / "Uebergabe" / "Extern"),
              register=lambda p: registered.append(p) or "")
    assert sorted(os.listdir(tmp_path / "Wahl" / "Audio Files" / "Extern")) == ["Hund.wav", "Regen.wav"]
    assert sorted(os.path.basename(p) for p in registered) == ["Hund.wav", "Regen.wav"]
    assert (tmp_path / "Downloads" / "Hund.wav").exists()                     # Original bleibt
    assert r.missing == [str(tmp_path / "weg.wav")] and PREF_HINT in r.hints
    assert r.text == "2 Dateien ins Projekt übernommen, 1 Datei fehlt (offline)"
    again = adopt(pt, session, upload_dir=str(tmp_path / "Uebergabe" / "Extern"), register=registered.append)
    assert again.copied == [] and len(registered) == 2                         # nichts doppelt


def test_archiv_ordner(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"x")
    (tmp_path / "b.txt").write_bytes(b"x")
    (tmp_path / ".c.wav").write_bytes(b"x")
    old = tmp_path / "alt.wav"
    old.write_bytes(b"x")
    os.utime(old, (1000, 1000))
    got = archive_candidates([str(tmp_path), str(tmp_path / "gibtsnicht")], since=2000)
    assert [os.path.basename(p) for p, _ in got] == ["a.wav"]


@pytest.fixture
def watcher_setup(tmp_path):
    session, files = _files(tmp_path)
    m = MockHelmut()
    m.add_project("w", "Wahl", "hs", state="in_mischung", mischer="Jens", raum="Ton 3", session_pfad=session)
    url = m.start()
    client = HelmutClient(url, lambda: ("test", "test"), sleep=lambda s: None)
    pt = types.SimpleNamespace(open_session=lambda: types.SimpleNamespace(name="Wahl", path=session),
                               file_locations=lambda: files)
    settings = Settings(local_path=tmp_path / "e.json")
    ctx = types.SimpleNamespace(settings=settings, helmut=client, ptsl=pt, mixer="Jens", room="Ton 3",
                                handover_root=str(tmp_path / "H" / "{projektId}"),
                                service=lambda: MixService(client, "Jens", "Ton 3"))
    notes = []
    yield ForeignWatcher(ctx, notify=lambda t, x: notes.append((t, x))), ctx, m, tmp_path, notes
    m.stop()


def test_waechter_sofort(watcher_setup):
    w, ctx, m, tmp_path, notes = watcher_setup
    state = w.check()
    assert (state["neu"], state["kopiert"], state["fehlt"], state["eigen"]) == (0, 2, 1, True)
    assert sorted(os.listdir(tmp_path / "H" / "w" / "Extern")) == ["Hund.wav", "Regen.wav"]
    assert sorted(a["filepath"].rsplit("/", 1)[-1] for a in m.assets["w"]) == ["Hund.wav", "Regen.wav"]


def test_waechter_erst_beim_schliessen(watcher_setup):
    w, ctx, m, tmp_path, notes = watcher_setup
    ctx.settings.set_local("import.session_ablage.fremde_dateien.uebernehmen", "beim_schliessen_und_export")
    assert w.check()["neu"] == 2 and not (tmp_path / "Wahl" / "Audio Files" / "Extern").exists()
    assert w.check("schliessen")["kopiert"] == 2


def test_waechter_fremde_session_nur_warnen(watcher_setup):
    w, ctx, m, tmp_path, notes = watcher_setup
    ctx.mixer = "A. Wolf"
    assert w.check()["eigen"] is False and w.state["neu"] == 2
    w.check("schliessen")
    assert not (tmp_path / "Wahl" / "Audio Files" / "Extern").exists()
    assert notes and notes[0][0] == "Dateien nur lokal" and "2 Audiodateien" in notes[0][1]


def test_haken_vor_dem_schliessen():
    engine = FakeEngine(session=("S", "/x/S.ptx"))
    c = PtslClient(engine_factory=lambda: engine)
    seen = []

    def hook():
        seen.append([x for x in engine.calls if x.startswith("close")])
        raise RuntimeError("kaputt")                                       # darf das Schließen nicht verhindern
    c.before_close = hook
    c.close()
    assert seen == [[]] and engine.session is None


def test_archiv_dialog_und_import(tmp_path):
    import time as _t

    from tonmischung.app import archive_app
    from tonmischung.app.bridge import Bridge, CommandError
    from tonmischung.app.jobs import JobRunner
    session, files = _files(tmp_path)
    imported = []
    pt = types.SimpleNamespace(open_session=lambda: types.SimpleNamespace(name="Wahl", path=session),
                               file_locations=lambda: files,
                               import_audio=lambda paths, to_clip_list, copy_to: imported.append((paths, to_clip_list, copy_to)))
    settings = Settings(local_path=tmp_path / "e.json")
    settings.set_local("import.session_ablage.archiv_ordner", [str(tmp_path / "Downloads")])
    ctx = types.SimpleNamespace(settings=settings, ptsl=pt)
    info = archive_app.archive_info(ctx, {})
    assert {f["name"]: f["state"] for f in info["foreign"]} == {"Hund.wav": "nur lokal", "Regen.wav": "nur lokal",
                                                               "weg.wav": "fehlt"}
    assert sorted(a["name"] for a in info["archive"]) == ["Hund.wav", "Regen.wav"]
    runner = JobRunner(Bridge(emit=lambda p: None, inline=True))
    with pytest.raises(CommandError, match="Archiv-Ordnern"):
        archive_app.start_archive_import(ctx, runner, {"paths": ["/etc/hosts"]})
    archive_app.start_archive_import(ctx, runner, {"paths": [str(tmp_path / "Downloads" / "Hund.wav")], "ziel": "clipliste"})
    t0 = _t.monotonic()
    while runner.current()["state"] not in ("fertig", "fehler") and _t.monotonic() - t0 < 5:
        _t.sleep(0.01)
    assert runner.current()["state"] == "fertig", runner.current()["error"]
    assert imported == [([str(tmp_path / "Downloads" / "Hund.wav")], True, str(tmp_path / "Wahl" / "Audio Files"))]
