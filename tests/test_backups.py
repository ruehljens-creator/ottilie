"""„Sicherungen …“ und „Im Finder zeigen“ (M7-Teil) – Stände listen, als Kopie öffnen."""
import os
import time
from datetime import datetime

import pytest

from fakes import FakeEngine, _Track
from tonmischung.app import backups_app, commands
from tonmischung.app.bridge import CommandError
from tonmischung.app.context import AppContext
from tonmischung.config.settings import Settings
from tonmischung.core.backups import BACKUP, CURRENT, OTHER, belongs_to, list_states
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.workflow.fetch import FetchError
from tonmischung.workflow.reopen import restore_state

NOW = datetime(2026, 10, 3, 0, 30)


def _session(tmp_path):
    folder = tmp_path / "Wahl"
    (folder / "Session File Backups").mkdir(parents=True)
    main = folder / "Wahl.ptx"
    main.write_bytes(b"AKTUELL")
    for i, name in enumerate(["Wahl.bak.000.ptx", "Wahl.bak.001.ptx"]):
        p = folder / "Session File Backups" / name
        p.write_bytes(f"BAK{i}".encode())
        os.utime(p, (time.time() - 100 + i * 10, time.time() - 100 + i * 10))
    (folder / "Wahl_wiederhergestellt_2200.ptx").write_bytes(b"ALT")
    (folder / "WaveCache.wfm").write_bytes(b"x")
    return str(main)


def test_staende_in_reihenfolge(tmp_path):
    main = _session(tmp_path)
    states = list_states(main)
    assert [(s.kind, os.path.basename(s.path)) for s in states] == [
        (CURRENT, "Wahl.ptx"), (OTHER, "Wahl_wiederhergestellt_2200.ptx"),
        (BACKUP, "Wahl.bak.001.ptx"), (BACKUP, "Wahl.bak.000.ptx")]
    assert states[2].row()["name"] == "Session File Backups/Wahl.bak.001.ptx"
    assert list_states(str(tmp_path / "weg" / "x.ptx")) == []
    assert belongs_to(states[3].path, main) and not belongs_to("/etc/passwd", main)
    assert not belongs_to(str(tmp_path / "Wahl" / "WaveCache.wfm"), main)


@pytest.fixture
def setup(tmp_path):
    main = _session(tmp_path)
    m = MockHelmut()
    m.add_project("w", "Wahl", "hs", state="in_mischung", mischer="Jens", raum="Ton 3", session_pfad=main)
    m.add_project("x", "Kita", "hs", state="in_mischung", mischer="A. Wolf", raum="Ton 2", session_pfad=main)
    m.url = m.start()
    engine = FakeEngine(session=None)
    engine.track_objs = [_Track("ST", "AudioTrack", True)]
    service = MixService(HelmutClient(m.url, lambda: ("test", "test"), sleep=lambda s: None), "Jens", "Ton 3",
                         now=lambda: "jetzt")
    yield main, m, engine, service
    m.stop()


def _restore(service, engine, pid, path):
    return restore_state(pid, path, service=service, ptsl=PtslClient(engine_factory=lambda: engine), now=lambda: NOW)


def test_sicherung_als_kopie_oeffnen(setup):
    main, m, engine, service = setup
    bak = os.path.join(os.path.dirname(main), "Session File Backups", "Wahl.bak.001.ptx")
    r = _restore(service, engine, "w", bak)
    target = os.path.join(os.path.dirname(main), "Wahl_wiederhergestellt_0030.ptx")
    assert r.session_path == target and open(target, "rb").read() == b"BAK1" and engine.session[1] == target
    assert open(bak, "rb").read() == b"BAK1" and open(main, "rb").read() == b"AKTUELL"       # unverändert
    assert m.mix_value("w", "session_pfad") == target and m.mix_value("w", "zustand") == "in_mischung"


def test_aktuelle_session_ohne_kopie(setup):
    main, m, engine, service = setup
    r = _restore(service, engine, "w", main)
    assert r.session_path == main and engine.session[1] == main and r.kind == "aktuell"
    assert sorted(os.listdir(os.path.dirname(main))) == ["Session File Backups", "Wahl.ptx",
                                                         "Wahl_wiederhergestellt_2200.ptx", "WaveCache.wfm"]


def test_fremder_raum_und_fremder_pfad(setup):
    main, m, engine, service = setup
    with pytest.raises(FetchError, match="erst freigeben"):
        _restore(service, engine, "x", main)
    with pytest.raises(FetchError, match="gehört nicht zu dieser Session"):
        _restore(service, engine, "w", "/etc/hosts")
    assert engine.session is None


def test_app_liste_und_finder_schutz(setup, tmp_path):
    main, m, engine, service = setup
    ctx = AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient(m.url, lambda: ("test", "test")),
                     PtslClient(engine_factory=lambda: engine), "Jens", "Ton 3", str(tmp_path), m, "x")
    b = backups_app.backups(ctx, {"id": "w"})
    assert b["can_open"] and b["reachable"] and len(b["rows"]) == 4 and b["rows"][0]["current"]
    other = backups_app.backups(ctx, {"id": "x"})
    assert not other["can_open"] and "bei A. Wolf übernommen" in other["availability"]
    with pytest.raises(CommandError, match="Nur die Session"):
        backups_app.reveal(ctx, {"id": "w", "path": "/etc/hosts"})
    d = commands.contribution_detail(ctx, {"id": "w"})
    assert d["actions"]["backups"] and d["actions"]["finder"]


def test_gespiegelte_staende_in_anderem_raum(tmp_path):
    main = _session(tmp_path / "Spiegel")                                   # nur der Spiegel ist erreichbar
    m = MockHelmut()
    m.add_project("w", "Wahl", "hs", state="zurueckgegeben", session_pfad="/Lokal/Ton2/Wahl/Wahl.ptx",
                  session_ablage="lokal_spiegelung", session_spiegel=os.path.dirname(main))
    url = m.start()
    try:
        ctx = AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient(url, lambda: ("test", "test")),
                         PtslClient(engine_factory=lambda: FakeEngine()), "Jens", "Ton 3", str(tmp_path), m, "x")
        b = backups_app.backups(ctx, {"id": "w"})
        assert b["reachable"] and not b["can_open"] and "gespiegelten Stände" in b["availability"]
        assert b["rows"][0]["path"] == main and b["folder"] == os.path.dirname(main)
        with pytest.raises(CommandError, match="Nur die Session"):
            backups_app.reveal(ctx, {"id": "w", "path": "/etc/hosts"})
    finally:
        m.stop()
