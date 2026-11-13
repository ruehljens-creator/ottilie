"""Raum dieses Macs ohne Raumliste (Jens 13.11.2026): Raumname-Einstellung, sonst Rechnername; die frühere
Raumliste wird einmal umgestellt (Name und Webtrigger-ID-Code dieses Macs)."""
import json

from fakes import FakeEngine
from tonmischung.app import rooms_app as RA
from tonmischung.app.context import AppContext
from tonmischung.config.settings import Settings
from tonmischung.helmut.client import HelmutClient
from tonmischung.ptsl.client import PtslClient

ROOM, CODE = "system.raeume_nutzer.raumname", "system.streamdeck_webtrigger.id_code"


def _ctx(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local("system.raeume_nutzer.ablage", "gemeinsamer_ordner")
    s.set_local("system.raeume_nutzer.gemeinsamer_ordner", str(tmp_path / "Storage"))
    return AppContext(s, HelmutClient("http://127.0.0.1:9", lambda: ("t", "t")),
                      PtslClient(engine_factory=lambda: FakeEngine()), "Jens", "?", str(tmp_path))


def _legacy_local(tmp_path, **values):
    """Einstellungsdatei wie bis 0.2 (Schlüssel, die es heute nicht mehr gibt)."""
    data = json.loads((tmp_path / "e.json").read_text()) if (tmp_path / "e.json").exists() else {}
    (tmp_path / "e.json").write_text(json.dumps({**data, **values}))


def test_ohne_einstellung_der_rechnername(tmp_path):
    ctx = _ctx(tmp_path)
    RA.startup(ctx, hostname="MacBookPro.local")
    assert ctx.room == "macbookpro"
    ctx.settings.set_local(ROOM, " Ton 3 ")
    RA.startup(ctx, hostname="MacBookPro.local")
    assert ctx.room == "Ton 3"


def test_fruehere_raumliste_wird_einmal_umgestellt(tmp_path):
    _legacy_local(tmp_path, **{"system.raeume_nutzer.raeume": [
        {"id": "t1", "name": "Ton 1", "rechnername": "ton1"},
        {"id": "t3", "name": "Ton 3", "rechnername": "ton3-mac", "id_code": "TON3-7F2A"}]})
    ctx = _ctx(tmp_path)
    RA.startup(ctx, hostname="TON3-Mac.example.org")
    assert ctx.room == "Ton 3" and ctx.settings.get(CODE) == "TON3-7F2A"
    ctx.settings.set_local(ROOM, "Studio 3")                 # später geändert – die alte Liste setzt nichts zurück
    RA.startup(ctx, hostname="ton3-mac")
    assert ctx.room == "Studio 3"
    ctx.settings.set_local(ROOM, "")                         # geleert und ID-Code entfernt (Review 13.11.2026) …
    ctx.settings.set_local(CODE, "")
    RA.startup(ctx, hostname="ton3-mac")
    assert ctx.room == "ton3-mac" and not ctx.settings.get(CODE)    # … kommt nach dem Neustart nicht zurück


def test_gemeinsame_liste_in_der_ablage_und_fester_raum(tmp_path):
    shared = tmp_path / "Storage" / "Ottilie Nutzer"
    shared.mkdir(parents=True)
    (shared / "raeume.json").write_text(json.dumps({"raeume": [{"id": "t5", "name": "Ton 5", "rechnername": "ton5"}]}))
    ctx = _ctx(tmp_path)
    RA.startup(ctx, hostname="ton5.local")
    assert ctx.room == "Ton 5"
    other = tmp_path / "other"
    other.mkdir()
    _legacy_local(other, **{"system.raeume_nutzer.raum_erkennung": "fest", "system.raeume_nutzer.raum_fest": "t5"})
    ctx2 = _ctx(other)
    ctx2.settings.set_local("system.raeume_nutzer.gemeinsamer_ordner", str(tmp_path / "Storage"))
    RA.startup(ctx2, hostname="irgendwas")
    assert ctx2.room == "Ton 5"                              # fest eingestellt, Name aus der gemeinsamen Liste


def test_kaputte_alte_liste_haelt_den_start_nicht_auf(tmp_path):
    shared = tmp_path / "Storage" / "Ottilie Nutzer"
    shared.mkdir(parents=True)
    (shared / "raeume.json").write_text('{"raeume": [{"name": "Ton 1"')     # halb geschrieben
    ctx = _ctx(tmp_path)
    RA.startup(ctx, hostname="ton1.local")
    assert ctx.room == "ton1" and (shared / "raeume.json").read_text() == '{"raeume": [{"name": "Ton 1"'
