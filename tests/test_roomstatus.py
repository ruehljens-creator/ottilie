"""Raumstatus nur mit Zentrale (Jens 13.11.2026): eigener Stand an die Zentrale, Räume aus der Zentrale, Belegung aus
Helmut bzw. den Sperrdateien. Kein Bonjour, kein Fortschritt im Takt über Helmut."""
import types

import pytest

from tonmischung.app.roomstatus import RoomPublisher, rooms
from tonmischung.config.settings import Settings
from tonmischung.core.mixstate import MixState
from tonmischung.core.peers import PeerStatus, own_status
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mapping import Contribution
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService


class FakeZentrale:
    """Wie `ZentraleLink` als Quelle: alle Räume der Zentrale, die verbundenen anderen mit Stand."""
    available = True
    label = "Zentrale"

    def __init__(self, live=None, names=("Ton 1", "Ton 2", "Ton 3", "Ton 4")):
        self.live, self.names, self.published = dict(live or {}), list(names), []

    def peers(self):
        return dict(self.live)

    def room_names(self):
        return list(self.names)

    def publish(self, status):
        self.published.append(status)


@pytest.fixture
def ctx(tmp_path):
    m = MockHelmut()
    m.add_project("a", "Wahl", "hs", state="in_mischung", mischer="Jens", raum="Ton 3", uebernommen_um="2026-10-03T08:00")
    m.add_project("b", "Kita", "hs", state="in_mischung", mischer="A. Wolf", raum="Ton 2")
    m.add_project("c", "Frei", "hs")
    url = m.start()
    client = HelmutClient(url, lambda: ("test", "test"), sleep=lambda s: None, retries=0)
    c = types.SimpleNamespace(settings=Settings(local_path=tmp_path / "e.json"), helmut=client, room="Ton 3",
                              mixer="Jens", service=lambda: MixService(client, "Jens", "Ton 3"), mock=m)
    yield c
    m.stop()


def test_eigener_stand():
    c = Contribution("p1", "Wahl", "hs", True, MixState("in_mischung", "Jens", "Ton 3"))
    assert own_status("Ton 3", "Jens", None, None) == PeerStatus("Ton 3", "Jens")
    assert own_status("Ton 3", "Jens", c, None).state == "mischt"
    job = {"kind": "zurueckliefern", "fraction": 0.456, "contribution": "p1", "title": "Zurückliefern: Wahl"}
    assert own_status("Ton 3", "Jens", c, job) == PeerStatus("Ton 3", "Jens", "exportiert", "Wahl", "p1", 45)
    assert own_status("Ton 3", "Jens", c, {"kind": "archiv"}).state == "mischt"


def test_stand_geht_an_die_zentrale_nicht_im_takt_an_helmut(ctx):
    z = FakeZentrale()
    pub = RoomPublisher(ctx, lambda: {"kind": "holen", "fraction": 0.37, "contribution": "a"}, z)
    puts = len([r for r in ctx.mock.requests if r[0] == "PUT"])
    pub.tick()
    assert z.published[-1] == PeerStatus("Ton 3", "Jens", "importiert", "Wahl", "a", 37)
    assert len([r for r in ctx.mock.requests if r[0] == "PUT"]) == puts         # Helmut bekommt nichts im Takt
    pub.running = lambda: None
    pub.tick()
    assert z.published[-1] == PeerStatus("Ton 3", "Jens", "mischt", "Wahl", "a")
    RoomPublisher(ctx, lambda: None, None).tick()                                 # ohne Zentrale: nichts


def test_alle_raeume_der_zentrale_mit_belegung_aus_helmut(ctx):
    z = FakeZentrale({"Ton 1": PeerStatus("Ton 1"), "Ton 2": PeerStatus("Ton 2", "", "exportiert", "Kita", "b", 63)})
    r = rooms(ctx, z)
    by = {c["name"]: c for c in r["cards"]}
    assert list(by) == ["Ton 1", "Ton 2", "Ton 3", "Ton 4"] and "Helmut und Zentrale" in r["source"]
    assert (by["Ton 1"]["st"], by["Ton 1"]["online"]) == ("frei", True)
    assert (by["Ton 2"]["label"], by["Ton 2"]["mixer"], by["Ton 2"]["source"]) == ("exportiert · 63 %", "A. Wolf",
                                                                                   "Zentrale")
    assert by["Ton 3"]["here"] and by["Ton 3"]["online"] is None and by["Ton 3"]["st"] == "mischt"
    assert (by["Ton 4"]["label"], by["Ton 4"]["online"]) == ("frei · nicht erreichbar", False)


def test_personen_nur_im_eigenen_raum(ctx):
    from tonmischung.core.users import User
    ctx.user = User("jens", "Jens")
    by = {c["name"]: c for c in rooms(ctx, FakeZentrale({"Ton 1": PeerStatus("Ton 1")}))["cards"]}
    assert by["Ton 3"]["user"] == "Jens" and by["Ton 1"]["user"] == ""        # die Zentrale kennt keine Personen


def test_ohne_zentrale_nur_helmut_ohne_helmut_nur_zentrale(ctx):
    r = rooms(ctx, None)
    assert [c["name"] for c in r["cards"]] == ["Ton 2", "Ton 3"] and "Zentrale nicht verbunden" in r["source"]
    ctx.mock.unavailable = True
    r = rooms(ctx, FakeZentrale({"Ton 2": PeerStatus("Ton 2", "", "mischt", "Kita", "b")}))
    by = {c["name"]: c for c in r["cards"]}
    assert "Helmut nicht erreichbar" in r["source"]
    assert (by["Ton 2"]["st"], by["Ton 2"]["title"], by["Ton 2"]["can_release"]) == ("mischt", "Kita", True)
    assert (by["Ton 1"]["st"], by["Ton 1"]["label"]) == ("offline", "nicht erreichbar")
    with pytest.raises(Exception):
        rooms(ctx, None)                                                         # ohne beides: Fehler melden


def test_nach_dem_export_ist_der_raum_frei(ctx):
    """Jens 04.10.2026: „Nach dem Export gilt der Beitrag als abgeschlossen und der Raum wird einfach frei angezeigt.“"""
    ctx.mock.add_project("d", "Apfelweinfest", "hs", state="exportiert", mischer="T. Nguyen", raum="Ton 4")
    by = {c["name"]: c for c in rooms(ctx, FakeZentrale())["cards"]}
    assert (by["Ton 4"]["st"], by["Ton 4"]["title"], by["Ton 4"]["can_release"]) == ("frei", "", False)
    assert (by["Ton 2"]["st"], by["Ton 2"]["title"]) == ("mischt", "Kita")


def test_raum_bleibt_belegt_wenn_ein_uebergabeordner_fehlt():
    """Ausfall 13.11.2026: sonst meldete der Raum „frei“ und durfte mitten in der Mischung umbenannt werden."""
    import types

    from tonmischung.app.roomstatus import RoomPublisher
    from tonmischung.core.mixstate import MixState
    mix = MixState(state="importiert", mixer="Jens", room="Ton 3", taken_at="1")
    film = types.SimpleNamespace(id="nord/p1", title="Film", mix=mix)
    rows = [film]
    folder = types.SimpleNamespace(complete=True)
    folder.list_with_problems = lambda: (list(rows), [] if folder.complete else [{"name": "Nord", "meldung": "weg"}])
    ctx = types.SimpleNamespace(mixer="Jens", room="Ton 3", folder_mode=True,
                                helmut=types.SimpleNamespace(list_for_mixing=lambda: list(rows), folder=folder))
    t = [0.0]
    pub = RoomPublisher(ctx, lambda: None, None, clock=lambda: t[0])
    assert pub.owned() is film
    rows.clear()
    folder.complete = False                                   # Ordner „Nord“ weg
    t[0] += 1000
    assert pub.owned() is film
    folder.complete = True                                    # wieder vollständig: jetzt wirklich frei
    t[0] += 1000
    assert pub.owned() is None
