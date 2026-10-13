"""Raumstatus direkt (Bonjour): Anmeldungsinhalt, Zusammenführen mit Helmut, echter Bonjour-Durchlauf."""
import time
import types

import pytest

from tonmischung.app.roomstatus import RoomPublisher, rooms
from tonmischung.config.settings import Settings
from tonmischung.core.mixstate import MixState
from tonmischung.core.peers import PeerStatus, from_txt, own_status, to_txt
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mapping import Contribution
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService


def test_anmeldung_hin_und_zurueck():
    s = PeerStatus("Ton 2", "A. Wolf", "importiert", "Wahl: Ergebnis", "p7", 42)
    assert from_txt(to_txt(s)) == s
    long = PeerStatus("Ton 2", title="Ä" * 300)
    assert len(to_txt(long)["beitrag"]) <= 200 and from_txt(to_txt(long)).title == "Ä" * 100
    assert from_txt({"raum": b"Ton 2"}) is None                              # fremdes Format
    assert from_txt({**to_txt(s), "zustand": b"quatsch", "pct": b"x"}).state == "frei"


def test_eigener_stand():
    c = Contribution("p1", "Wahl", "hs", True, MixState("in_mischung", "Jens", "Ton 3"))
    assert own_status("Ton 3", "Jens", None, None) == PeerStatus("Ton 3", "Jens")
    assert own_status("Ton 3", "Jens", c, None).state == "mischt"
    job = {"kind": "zurueckliefern", "fraction": 0.456, "contribution": "p1", "title": "Zurückliefern: Wahl"}
    assert own_status("Ton 3", "Jens", c, job) == PeerStatus("Ton 3", "Jens", "exportiert", "Wahl", "p1", 45)
    assert own_status("Ton 3", "Jens", c, {"kind": "archiv"}).state == "mischt"


class FakePeers:
    available = True

    def __init__(self, live=None):
        self.live, self.published = dict(live or {}), []

    def peers(self):
        return dict(self.live)

    def publish(self, status):
        self.published.append(status)


@pytest.fixture
def ctx(tmp_path):
    m = MockHelmut()
    m.add_project("a", "Wahl", "hs", state="in_mischung", mischer="Jens", raum="Ton 3")
    m.add_project("b", "Kita", "hs", state="in_mischung", mischer="A. Wolf", raum="Ton 2")
    url = m.start()
    client = HelmutClient(url, lambda: ("test", "test"), sleep=lambda s: None, retries=0)
    c = types.SimpleNamespace(settings=Settings(local_path=tmp_path / "e.json"), helmut=client, room="Ton 3",
                              mixer="Jens", service=lambda: MixService(client, "Jens", "Ton 3"), mock=m)
    c.settings.set_local("system.raeume_nutzer.raeume", [{"id": n, "name": n, "rechnername": n.lower()}
                                                         for n in ("Ton 1", "Ton 2", "Ton 3", "Ton 4")])
    yield c
    m.stop()


def test_helmut_ergaenzt_um_direkt(ctx):
    peers = FakePeers({"Ton 1": PeerStatus("Ton 1", "K. Braun"),
                       "Ton 2": PeerStatus("Ton 2", "A. Wolf", "exportiert", "Kita", "b", 63)})
    by = {c["name"]: c for c in rooms(ctx, peers)["cards"]}
    assert (by["Ton 1"]["st"], by["Ton 1"]["mixer"], by["Ton 1"]["online"]) == ("frei", "K. Braun", True)
    assert (by["Ton 2"]["label"], by["Ton 2"]["source"]) == ("exportiert · 63 %", "direkt · Bonjour")
    assert by["Ton 3"]["online"] is None and by["Ton 3"]["here"]
    assert (by["Ton 4"]["label"], by["Ton 4"]["online"]) == ("frei · nicht erreichbar", False)


def test_ohne_helmut_nur_direkt(ctx):
    ctx.mock.unavailable = True
    peers = FakePeers({"Ton 2": PeerStatus("Ton 2", "A. Wolf", "mischt", "Kita", "b")})
    r = rooms(ctx, peers)
    by = {c["name"]: c for c in r["cards"]}
    assert "Helmut nicht erreichbar" in r["source"]
    assert (by["Ton 2"]["st"], by["Ton 2"]["title"], by["Ton 2"]["can_release"]) == ("mischt", "Kita", True)
    assert (by["Ton 1"]["st"], by["Ton 1"]["label"]) == ("offline", "nicht erreichbar")
    with pytest.raises(Exception):
        rooms(ctx, None)                                                    # ohne beides: Fehler melden


def test_eigener_stand_wird_angemeldet(ctx):
    peers = FakePeers()
    pub = RoomPublisher(ctx, lambda: None, peers)
    pub.tick()
    assert peers.published[-1] == PeerStatus("Ton 3", "Jens", "mischt", "Wahl", "a")
    ctx.settings.set_local("system.raeume_nutzer.raumstatus.teilen", False)
    pub.tick()
    assert peers.published[-1] is None


def test_echtes_bonjour():
    pytest.importorskip("Foundation")
    from Foundation import NSDate, NSRunLoop

    from tonmischung.app.bonjour import BonjourPeers
    room_a, room_b = f"Test A {time.time_ns()}", f"Test B {time.time_ns()}"
    a = BonjourPeers(room_a, 8899, lambda fn, *args: fn(*args), browse=False)
    b = BonjourPeers(room_b, 8899, lambda fn, *args: fn(*args))
    a.start()
    b.start()
    a.publish(PeerStatus(room_a, "Jens", "importiert", "Wahl", "p1", 30))
    try:
        end = time.time() + 8
        while time.time() < end and room_a not in b.peers():
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
        got = b.peers().get(room_a)
        assert got is not None and (got.state, got.pct, got.title) == ("importiert", 30, "Wahl")
        a.publish(PeerStatus(room_a, "Jens", "mischt", "Wahl", "p1"))
        end = time.time() + 8
        while time.time() < end and b.peers()[room_a].state != "mischt":
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
        assert b.peers()[room_a].state == "mischt"                          # Änderung kommt an
        a.stop()
        end = time.time() + 8
        while time.time() < end and room_a in b.peers():
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
        assert room_a not in b.peers()                                       # App weg → nicht erreichbar
    finally:
        a.stop()
        b.stop()


def test_nach_dem_export_ist_der_raum_frei(ctx):
    """Jens 04.10.2026: „Nach dem Export gilt der Beitrag als abgeschlossen und der Raum wird einfach frei angezeigt.“"""
    ctx.mock.add_project("c", "Apfelweinfest", "hs", state="exportiert", mischer="T. Nguyen", raum="Ton 4")
    ctx.mock.add_project("d", "Wetter", "hs", state="exportiert", mischer="Jens", raum="Ton 1")
    by = {c["name"]: c for c in rooms(ctx, FakePeers())["cards"]}
    assert (by["Ton 4"]["st"], by["Ton 4"]["title"], by["Ton 4"]["can_release"]) == ("frei", "", False)
    assert (by["Ton 2"]["st"], by["Ton 2"]["title"]) == ("mischt", "Kita")          # in Mischung belegt weiter
    c = Contribution("p1", "Wahl", "hs", True, MixState("exportiert", "Jens", "Ton 3"))
    assert own_status("Ton 3", "Jens", c, None) == PeerStatus("Ton 3", "Jens")
    job = {"kind": "zurueckliefern", "fraction": 0.5, "contribution": "p1"}
    assert own_status("Ton 3", "Jens", c, job).state == "exportiert"                # während des Exports
