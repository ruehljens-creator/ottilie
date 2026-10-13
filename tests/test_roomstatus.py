"""Raumstatus über Helmut (M7): Fortschritt in Stufen melden, Räume aus den Beiträgen lesen."""
import types

import pytest

from tonmischung.app.roomstatus import RoomPublisher, rooms, step_of
from tonmischung.config.settings import Settings
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService


@pytest.fixture
def ctx(tmp_path):
    m = MockHelmut()
    m.add_project("a", "Wahl", "hs", state="uebernommen", mischer="Jens", raum="Ton 3", uebernommen_um="2026-10-03T08:00")
    m.add_project("b", "Kita", "hs", state="in_mischung", mischer="A. Wolf", raum="Ton 2")
    m.add_project("c", "Frei", "hs")
    url = m.start()
    client = HelmutClient(url, lambda: ("test", "test"), sleep=lambda s: None)
    c = types.SimpleNamespace(settings=Settings(local_path=tmp_path / "e.json"), helmut=client, room="Ton 3",
                              mixer="Jens", service=lambda: MixService(client, "Jens", "Ton 3"), mock=m)
    yield c
    m.stop()


def test_stufen():
    assert [step_of(f, 10) for f in (0, 0.09, 0.37, 1.2)] == [0, 0, 30, 100]
    assert step_of(0.74, 25) == 50


def test_fortschritt_wird_in_stufen_gemeldet(ctx):
    m = ctx.mock
    job = {"kind": "holen", "fraction": 0.37, "contribution": "a"}
    pub = RoomPublisher(ctx, lambda: job)
    pub.tick()
    assert (m.mix_value("a", "fortschritt"), m.mix_value("a", "taetigkeit")) == ("30", "import")
    assert m.mix_value("a", "zustand") == "uebernommen"                      # Zustand unverändert
    puts = len([r for r in m.requests if r[0] == "PUT"])
    job["fraction"] = 0.39
    pub.tick()
    assert len([r for r in m.requests if r[0] == "PUT"]) == puts            # gleiche Stufe: nichts schreiben
    job = None
    pub.running = lambda: None
    pub.tick()
    assert (m.mix_value("a", "fortschritt"), m.mix_value("a", "taetigkeit")) == ("0", "")


def test_raumstatus_wartet_nicht_auf_einen_auftrag(ctx):
    """Review B9: schreibt gerade ein Auftrag das Set „Mischung“, fällt die Meldung aus und kommt beim nächsten Takt."""
    import threading
    m = ctx.mock
    pub = RoomPublisher(ctx, lambda: {"kind": "holen", "fraction": 0.37, "contribution": "a"})
    pub.tick()
    pub.running = lambda: None
    held, release = threading.Event(), threading.Event()

    def job_writes():
        with MixService._write_lock:
            held.set()
            release.wait(5)
    t = threading.Thread(target=job_writes)
    t.start()
    held.wait(5)
    pub.tick()                                                              # kehrt sofort zurück
    assert m.mix_value("a", "taetigkeit") == "import"
    release.set()
    t.join()
    pub.tick()                                                              # Tätigkeit wird nachgeholt gelöscht
    assert (m.mix_value("a", "fortschritt"), m.mix_value("a", "taetigkeit")) == ("0", "")


def test_nur_eigene_und_nur_wenn_geteilt(ctx):
    m = ctx.mock
    RoomPublisher(ctx, lambda: {"kind": "holen", "fraction": 0.5, "contribution": "b"}).tick()
    assert m.mix_value("b", "taetigkeit") in (None, "")                     # gehört A. Wolf
    ctx.settings.set_local("system.raeume_nutzer.raumstatus.teilen", False)
    RoomPublisher(ctx, lambda: {"kind": "zurueckliefern", "fraction": 0.5, "contribution": "a"}).tick()
    assert m.mix_value("a", "taetigkeit") in (None, "")


def test_raumuebersicht(ctx):
    RoomPublisher(ctx, lambda: {"kind": "zurueckliefern", "fraction": 0.55, "contribution": "a"}).tick()
    r = rooms(ctx)
    by = {c["name"]: c for c in r["cards"]}
    assert set(by) == {"Ton 2", "Ton 3"} and r["busy"] == 2
    assert (by["Ton 3"]["st"], by["Ton 3"]["label"], by["Ton 3"]["here"], by["Ton 3"]["can_release"]) == \
        ("exportiert", "exportiert · 50 %", True, False)
    assert (by["Ton 2"]["st"], by["Ton 2"]["mixer"], by["Ton 2"]["id"], by["Ton 2"]["can_release"]) == \
        ("mischt", "A. Wolf", "b", True)
    ctx.settings.set_local("system.raeume_nutzer.raeume", [{"id": "r1", "name": "Ton 1", "rechnername": "ton1"},
                                                           {"id": "r2", "name": "Ton 2", "rechnername": "ton2"}])
    assert [(c["name"], c["st"]) for c in rooms(ctx)["cards"]] == [("Ton 1", "frei"), ("Ton 2", "mischt")]
