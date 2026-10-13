"""Havarie „Paket manuell öffnen“ (M9): ohne Helmut holen, später nachtragen – mit Konfliktprüfung."""
import os
import time

import pytest

from fakes import FakeEngine
from test_fetch import AAF_OK, CLIPS
from tonmischung.core.catchup import CONFLICT, CatchUpQueue
from tonmischung.core.havarie import find_package, title_from
from tonmischung.helmut.catchup import replay
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.offline import OfflineService
from tonmischung.ptsl.client import PtslClient
from tonmischung.workflow import fetch as fetch_mod
from tonmischung.workflow.fetch import fetch


def _package(tmp_path, name="5f3a9c2b1d4e6f7a8b9c0d1e"):
    folder = tmp_path / name
    (folder / "Uebergabe").mkdir(parents=True)
    (folder / "Uebergabe" / "Wahl_Ergebnis_forPT_1.aaf").write_bytes(b"aaf")
    (folder / "Uebergabe" / "Wahl_ref.mxf").write_bytes(b"mxf")
    old = folder / "Uebergabe" / "alt.aaf"
    old.write_bytes(b"alt")
    os.utime(old, (1000, 1000))
    return folder


def test_paket_erkennen(tmp_path):
    folder = _package(tmp_path)
    p = find_package(str(folder))
    assert os.path.basename(p.aaf) == "Wahl_Ergebnis_forPT_1.aaf" and os.path.basename(p.video) == "Wahl_ref.mxf"
    assert p.project_id == "5f3a9c2b1d4e6f7a8b9c0d1e" and p.complete
    assert find_package(str(_package(tmp_path, "Bahnstreik"))).project_id == ""
    assert title_from("/x/Wahl_Ergebnis_forPT_1.aaf") == "Wahl Ergebnis 1"
    (tmp_path / "leer").mkdir()
    assert find_package(str(tmp_path / "leer")) is None


@pytest.fixture(autouse=True)
def aaf_ok(monkeypatch):
    monkeypatch.setattr(fetch_mod, "check_aaf", lambda path: AAF_OK)


def _offline_fetch(tmp_path, pid, queue):
    pkg = find_package(str(_package(tmp_path)))
    service = OfflineService(pkg, "Wahl", pid, "havarie-1", "Jens", "Ton 3", queue)
    engine = FakeEngine(session=None, aaf_tracks=("A1", "A2"), aaf_clips=CLIPS, video_track="Video")
    r = fetch("havarie-1", service=service, ptsl=PtslClient(engine_factory=lambda: engine), template_group="test",
              template_name="SM Session", session_parent=str(tmp_path / "Sessions"))
    return r, engine


def test_ohne_helmut_holen_und_nachtragen(tmp_path):
    q = CatchUpQueue(tmp_path / "n.json")
    r, engine = _offline_fetch(tmp_path, "w1", q)
    assert r.contribution.mix.state == "importiert" and any("Havarie" in w for w in r.warnings)
    assert [e.kind for e in q.pending()] == ["uebernahme", "mischung"]
    m = MockHelmut()
    m.add_project("w1", "Wahl", "hs")
    url = m.start()
    try:
        rep = replay(q, HelmutClient(url, lambda: ("test", "test"), sleep=lambda s: None))
        assert len(rep.written) == 2 and not q.pending()
        assert (m.mix_value("w1", "zustand"), m.mix_value("w1", "mischer"), m.status("w1")) == ("importiert", "Jens", "LOCKED")
        assert m.mix_value("w1", "session_pfad") == r.session_path
    finally:
        m.stop()


def test_inzwischen_anders_vergeben_ist_konflikt(tmp_path):
    q = CatchUpQueue(tmp_path / "n.json")
    _offline_fetch(tmp_path, "w1", q)
    m = MockHelmut()
    m.add_project("w1", "Wahl", "hs", state="in_mischung", mischer="A. Wolf", raum="Ton 2")
    url = m.start()
    try:
        rep = replay(q, HelmutClient(url, lambda: ("test", "test"), sleep=lambda s: None))
        assert len(rep.conflicts) == 1 and "A. Wolf" in rep.conflicts[0]
        assert [e.status for e in q.entries()] == [CONFLICT, "offen"]               # Zustand wartet dahinter
        assert m.mix_value("w1", "mischer") == "A. Wolf"                             # nichts überschrieben
    finally:
        m.stop()


def test_ohne_projekt_id_erst_zuordnen(tmp_path):
    q = CatchUpQueue(tmp_path / "n.json")
    _offline_fetch(tmp_path, "", q)
    assert q.pending() == [] and len(q.waiting()) == 2
    assert q.summary()["ohne_beitrag"] == [{"id": "havarie-1", "titel": "Wahl"}]
    assert q.assign("havarie-1", "w1") == 2 and len(q.pending()) == 2 and q.summary()["ohne_beitrag"] == []


def test_app_havarie_auftrag(tmp_path):
    from tonmischung.app import catchup_app, havarie_app
    from tonmischung.app.bridge import Bridge, CommandError
    from tonmischung.app.context import AppContext
    from tonmischung.app.jobs import JobRunner
    from tonmischung.config.settings import Settings
    folder = _package(tmp_path, "Bahnstreik")
    m = MockHelmut()
    m.add_project("p9", "Bahnstreik", "hs")
    url = m.start()
    try:
        engine = FakeEngine(session=None, aaf_tracks=("A1", "A2"), aaf_clips=CLIPS, video_track="Video")
        ctx = AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient(url, lambda: ("test", "test")),
                         PtslClient(engine_factory=lambda: engine), "Jens", "Ton 3", str(tmp_path / "S"), m, "x",
                         queue=CatchUpQueue(tmp_path / "n.json"))
        info = havarie_app.package_info(ctx, {"path": str(folder)})
        assert info["complete"] and info["project_id"] == "" and info["title"] == "Wahl Ergebnis 1"
        with pytest.raises(CommandError, match="kein AAF"):
            (tmp_path / "leer").mkdir()
            havarie_app.package_info(ctx, {"path": str(tmp_path / "leer")})
        runner = JobRunner(Bridge(emit=lambda p: None, inline=True))
        havarie_app.start_open_package(ctx, runner, {"path": str(folder), "title": "Bahnstreik", "group": "test",
                                             "name": "SM Session", "triage": False})
        t0 = time.monotonic()
        while runner.current()["state"] not in ("fertig", "fehler") and time.monotonic() - t0 < 5:
            time.sleep(0.01)
        done = runner.current()
        assert done["state"] == "fertig", done["error"]
        hv = done["result"]["havarie"]
        assert ctx.queue.summary()["ohne_beitrag"] == [{"id": hv, "titel": "Bahnstreik"}]
        r = catchup_app.assign_havarie(ctx, {"havarie": hv, "id": "p9"})
        assert r["offen"] == 0 and m.mix_value("p9", "zustand") == "importiert"
    finally:
        m.stop()
