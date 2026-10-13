"""Webtrigger-Adressen je Platz zum Kopieren (Plan PunchBuddy-Übernahme, Schritt 3b)."""
import random

import pytest

from tonmischung.config.settings import Settings
from tonmischung.punch.urls import addresses, new_id_code, set_id_code

RAEUME = [{"id": "r2", "name": "Ton 2", "rechnername": "ton2-mac"},
          {"id": "r3", "name": "Ton 3", "rechnername": "ton3-mac", "id_code": "T3-7F2A"}]


@pytest.fixture
def s(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local("system.raeume_nutzer.raeume", RAEUME)
    s.set_local("aufnahme.presets", [{"name": "Magazin"}, {"name": "News"}])
    return s


def test_adressen_mit_rechnername_und_id_code(s):
    a = addresses(s, "Ton 3")
    urls = {r["label"]: r["url"] for r in a["rows"]}
    assert urls["Record A"] == "http://ton3-mac:8899/trigger?id=T3-7F2A"
    assert urls["Holen (gewählter Beitrag)"] == "http://ton3-mac:8899/import?id=T3-7F2A"
    assert urls["Preset 2 · News"] == "http://ton3-mac:8899/preset/2?id=T3-7F2A"
    assert urls["Vocaster 48 V an"] == "http://ton3-mac:8899/vocaster/phantom/on?id=T3-7F2A"
    assert not any("interplay" in u for u in urls.values())
    assert a["count_text"].endswith("mit ID-Code") and a["rooms"] == ["Ton 2", "Ton 3"]


def test_ohne_code_mit_token_und_lokal(s):
    a = addresses(s, "Ton 2", token="geheim")
    assert a["rows"][0]["url"] == "http://ton2-mac:8899/trigger?token=geheim"
    s.set_local("system.streamdeck_webtrigger.adresse_in_url", "127.0.0.1")
    s.set_local("system.streamdeck_webtrigger.nur_mit_id_code", True)
    a = addresses(s, "Ton 2")
    assert a["rows"][0]["url"] == "http://127.0.0.1:8899/trigger" and a["count_text"].endswith("ohne Code nur von diesem Mac auslösbar")


def test_id_code_erzeugen_und_entfernen(s):
    code = new_id_code("Ton 2", random.Random(1))
    assert code.startswith("TON2-") and len(code) == 9
    set_id_code(s, "Ton 2", code)
    assert addresses(s, "Ton 2")["id_code"] == code
    set_id_code(s, "Ton 2", "")
    assert addresses(s, "Ton 2")["id_code"] == ""
    with pytest.raises(ValueError, match="Raumliste"):
        set_id_code(s, "Ton 9", "X")


def test_fensterbefehle(s):
    from tonmischung.app.bridge import Bridge, CommandError
    from tonmischung.app.triggers_app import Triggers, register

    class Ctx:
        settings, room = s, "Ton 3"
    kopiert = []
    bridge = Bridge(emit=lambda p: None, inline=True)
    register(bridge, Ctx(), Triggers(token=""), copy=kopiert.append)
    assert bridge.call("webtrigger_adressen", {})["room"] == "Ton 3"
    assert bridge.call("webtrigger_adressen", {"raum": "Ton 2"})["rows"][0]["url"].startswith("http://ton2-mac:8899/")
    assert bridge.call("id_code_erzeugen", {"raum": "Ton 2"})["id_code"].startswith("TON2-")
    assert bridge.call("id_code_entfernen", {"raum": "Ton 2"})["id_code"] == ""
    with pytest.raises(CommandError, match="Raumliste"):
        bridge.call("id_code_erzeugen", {"raum": "Ton 9"})
    bridge.call("kopieren", {"text": "http://ton3-mac:8899/trigger"})
    assert kopiert == ["http://ton3-mac:8899/trigger"]
