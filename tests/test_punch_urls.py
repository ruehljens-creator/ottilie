"""Webtrigger-Adressen dieses Macs zum Kopieren (Plan PunchBuddy-Übernahme, Schritt 3b); ID-Code je Mac
(13.11.2026, vorher an der Raumliste)."""
import random

import pytest

from tonmischung.config.settings import Settings
from tonmischung.punch.urls import addresses, new_id_code, set_id_code

CODE = "system.streamdeck_webtrigger.id_code"


@pytest.fixture
def s(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local(CODE, "T3-7F2A")
    s.set_local("aufnahme.presets", [{"name": "Magazin"}, {"name": "News"}])
    return s


def test_adressen_mit_rechnername_und_id_code(s):
    a = addresses(s, "Ton 3", this_host="ton3-mac")
    urls = {r["label"]: r["url"] for r in a["rows"]}
    assert urls["Record A"] == "http://ton3-mac:8899/trigger?id=T3-7F2A"
    assert urls["Holen (gewählter Beitrag)"] == "http://ton3-mac:8899/import?id=T3-7F2A"
    assert urls["Preset 2 · News"] == "http://ton3-mac:8899/preset/2?id=T3-7F2A"
    assert urls["Vocaster 48 V an"] == "http://ton3-mac:8899/vocaster/phantom/on?id=T3-7F2A"
    assert not any("interplay" in u for u in urls.values())
    assert a["count_text"].endswith("mit ID-Code") and a["room"] == "Ton 3"


def test_ohne_code_mit_token_und_lokal(s):
    set_id_code(s, "")
    a = addresses(s, "Ton 2", token="geheim", this_host="ton2-mac")
    assert a["rows"][0]["url"] == "http://ton2-mac:8899/trigger?token=geheim"
    s.set_local("system.streamdeck_webtrigger.adresse_in_url", "127.0.0.1")
    s.set_local("system.streamdeck_webtrigger.nur_mit_id_code", True)
    a = addresses(s, "Ton 2")
    assert a["rows"][0]["url"] == "http://127.0.0.1:8899/trigger" and a["count_text"].endswith("ohne Code nur von diesem Mac auslösbar")


def test_id_code_erzeugen_und_entfernen(s):
    code = new_id_code("Ton 2", random.Random(1))
    assert code.startswith("TON2-") and len(code) == 9
    set_id_code(s, code)
    assert addresses(s, "Ton 2")["id_code"] == code
    set_id_code(s, "")
    assert addresses(s, "Ton 2")["id_code"] == ""


def test_fensterbefehle(s):
    from tonmischung.app.bridge import Bridge
    from tonmischung.app.triggers_app import Triggers, register

    class Ctx:
        settings, room = s, "Ton 3"
    kopiert = []
    bridge = Bridge(emit=lambda p: None, inline=True)
    register(bridge, Ctx(), Triggers(token=""), copy=kopiert.append)
    assert bridge.call("webtrigger_adressen", {})["room"] == "Ton 3"
    assert bridge.call("id_code_erzeugen", {})["id_code"].startswith("TON3-")
    assert bridge.call("id_code_entfernen", {})["id_code"] == ""
    bridge.call("kopieren", {"text": "http://ton3-mac:8899/trigger"})
    assert kopiert == ["http://ton3-mac:8899/trigger"]
