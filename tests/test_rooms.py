"""Räume über Mac-Namen (Plan Nutzer & Räume, Schritt 4)."""
import pytest

from fakes import FakeEngine
from tonmischung.app import rooms_app as RA
from tonmischung.app.context import AppContext
from tonmischung.config.settings import Settings
from tonmischung.core import rooms as R
from tonmischung.helmut.client import HelmutClient
from tonmischung.ptsl.client import PtslClient


def test_liste_pruefen_und_macs_zur_auswahl(tmp_path):
    rows, errors = R.normalize([{"name": " Ton  1 ", "rechnername": "TON1-Mac.example.org"}, {"name": "", "rechnername": ""},
                                {"name": "Ton 2", "rechnername": "ton2", "id_code": " 42 "}])
    assert errors == [] and rows == [{"id": "ton-1", "name": "Ton 1", "rechnername": "ton1-mac"},
                                     {"id": "ton-2", "name": "Ton 2", "rechnername": "ton2", "id_code": "42"}]
    _, errors = R.normalize([{"name": "Ton 1", "rechnername": "a"}, {"name": "ton 1", "rechnername": "a"},
                             {"name": "Ton 3", "rechnername": ""}, {"name": "", "rechnername": "x"}])
    assert errors == ["Raum „ton 1“ steht doppelt in der Liste", "Mac „a“ ist schon „Ton 1“ zugeordnet",
                      "„Ton 3“: Mac wählen", "Mac „x“: Raumname fehlt"]
    assert R.known_macs("Ton3.local", ["ton1.local", "", "Ton3.local"], [{"rechnername": "ton7"}]) == ["ton3", "ton1", "ton7"]
    assert R.load(str(tmp_path)) is None
    R.save(str(tmp_path), rows)
    assert R.load(str(tmp_path)) == rows


class Peers:
    available = True

    def peers(self):
        from tonmischung.core.peers import PeerStatus
        return {"ton1": PeerStatus("ton1", host="ton1.local")}


def _ctx(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local("system.raeume_nutzer.ablage", "gemeinsamer_ordner")
    s.set_local("system.raeume_nutzer.gemeinsamer_ordner", str(tmp_path / "Storage"))
    return AppContext(s, HelmutClient("http://127.0.0.1:9", lambda: ("t", "t")),
                      PtslClient(engine_factory=lambda: FakeEngine()), "Jens", "?", str(tmp_path))


def test_raumliste_im_fenster(tmp_path, monkeypatch):
    monkeypatch.setattr(RA.socket, "gethostname", lambda: "ton3.local")
    ctx = _ctx(tmp_path)
    ctx.settings.set_local("system.raeume_nutzer.raeume", [{"id": "t3", "name": "Ton 3", "rechnername": "ton3"}])
    RA.startup(ctx)                                     # bisherige Mac-Liste geht in die Ablage
    assert ctx.room == "Ton 3" and R.load(str(tmp_path / "Storage" / "Ottilie Nutzer"))[0]["name"] == "Ton 3"
    ctx.extra["peers"] = Peers()
    v = RA.view(ctx)
    assert v["macs"] == ["ton3", "ton1"] and v["this_mac"] == "ton3" and v["this_room"] == "Ton 3"
    v = RA.save(ctx, {"rooms": [{"name": "Ton 1", "rechnername": "ton1"}, {"name": "Ton 3", "rechnername": "ton1"}]})
    assert v["errors"] == ["Mac „ton1“ ist schon „Ton 1“ zugeordnet"] and ctx.room == "Ton 3"   # nichts gespeichert
    v = RA.save(ctx, {"rooms": [{"name": "Ton 1", "rechnername": "ton1"}, {"name": "Studio 3", "rechnername": "ton3"}]})
    assert v["errors"] == [] and ctx.room == "Studio 3" and "jetzt „Studio 3“" in v["message"]
    assert ctx.settings.get("system.raeume_nutzer.raeume")[1]["name"] == "Studio 3"          # gilt (zentral)
    other = _ctx(tmp_path)                              # anderer Mac mit demselben gemeinsamen Ordner
    monkeypatch.setattr(RA.socket, "gethostname", lambda: "ton1.local")
    RA.startup(other)
    assert other.room == "Ton 1"


def test_raumkarten_zeigen_wer_angemeldet_ist(tmp_path):
    """Schritt 5: Übersicht im Hauptfenster – je Raum der angemeldete Nutzer (hier selbst, sonst per Bonjour)."""
    from tonmischung.app.roomstatus import rooms
    from tonmischung.core.peers import PeerStatus
    from tonmischung.core.users import User
    ctx = _ctx(tmp_path)
    ctx.room = "Ton 3"
    ctx.user = User("jens", "Jens")
    ctx.settings.set_local("system.raeume_nutzer.raumstatus.quelle", "nur_direkt")
    ctx.settings.update_central({"system.raeume_nutzer.raeume": [{"id": "1", "name": "Ton 1", "rechnername": "ton1"},
                                                                 {"id": "3", "name": "Ton 3", "rechnername": "ton3"},
                                                                 {"id": "5", "name": "Ton 5", "rechnername": "ton5"}]})

    class P:
        available = True

        def peers(self):
            return {"Ton 1": PeerStatus("Ton 1", "B. Sommer", "importiert", "Wahl", "w", 40, "ton1.local")}
    r = rooms(ctx, P())
    cards = {c["name"]: c for c in r["cards"]}
    assert (cards["Ton 1"]["user"], cards["Ton 1"]["label"], cards["Ton 1"]["pct"]) == ("B. Sommer", "importiert · 40 %", 40)
    assert cards["Ton 3"]["user"] == "Jens" and cards["Ton 3"]["here"]
    assert cards["Ton 5"]["user"] == "" and cards["Ton 5"]["online"] is False


def test_unlesbare_gemeinsame_raumliste_bleibt_unangetastet(tmp_path, monkeypatch):
    """Review A5: vorher überschrieb die lokale Liste eines Macs die kaputte gemeinsame Liste aller Räume."""
    from tonmischung.app.bridge import CommandError
    monkeypatch.setattr(RA.socket, "gethostname", lambda: "ton3.local")
    ctx = _ctx(tmp_path)
    ctx.settings.set_local("system.raeume_nutzer.raeume", [{"id": "t3", "name": "Ton 3", "rechnername": "ton3"}])
    shared = tmp_path / "Storage" / "Ottilie Nutzer"
    shared.mkdir(parents=True)
    (shared / R.FILE).write_text('{"raeume": [{"name": "Ton 1"')                 # halb geschrieben
    RA.startup(ctx)
    assert (shared / R.FILE).read_text() == '{"raeume": [{"name": "Ton 1"'        # nicht überschrieben
    assert ctx.room == "Ton 3"                                                      # Mac-Liste gilt vorerst
    view = RA.view(ctx)
    assert any("nicht lesbar" in e for e in view["errors"])
    with pytest.raises(CommandError, match="nicht lesbar"):
        RA.save(ctx, {"rooms": [{"name": "Ton 9", "rechnername": "ton9"}]})
    (shared / R.FILE).write_text('{"raeume": [{"id": "t1", "name": "Ton 1", "rechnername": "ton3"}]}')   # repariert
    assert RA.view(ctx)["errors"] == [] and ctx.room == "Ton 1"
