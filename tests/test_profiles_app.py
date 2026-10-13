"""Spurprofile im Einstellungsfenster: Spuren aus Pro Tools lesen, Häkchen setzen, an das Template knüpfen."""
import pytest

from fakes import FakeEngine, _Track
from tonmischung.app import profiles_app as PA
from tonmischung.app.bridge import CommandError
from tonmischung.app.context import AppContext
from tonmischung.config.settings import Settings
from tonmischung.helmut.client import HelmutClient
from tonmischung.ptsl.client import PtslClient

TEMPLATE = ["ST", "A  IT", "Spr", "A1", "A2", "A5", "Musik1", "A11"]


def _ctx(tmp_path, engine):
    return AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient("http://127.0.0.1:9", lambda: ("t", "t")),
                      PtslClient(engine_factory=lambda: engine), "Jens", "Ton 3", str(tmp_path), None, "x")


@pytest.fixture
def ctx(tmp_path):
    engine = FakeEngine(session=("helmut test", "/S/helmut test.ptx"))
    engine.track_objs = [_Track(t, "AudioTrack", False) for t in TEMPLATE] + [_Track("Video", "VideoTrack", False)]
    return _ctx(tmp_path, engine), engine


def test_lesen_legt_profil_an_und_belegt_vor(ctx):
    c, _ = ctx
    v = PA.read(c, "test", "helmut test", now=lambda: "03.10.2026 09:00")
    assert v["message"] == "9 Spuren aus „helmut test“ gelesen und nach den bisherigen Einstellungen vorbelegt"
    rows = {r["name"]: r for r in v["selected"]["rows"]}
    assert rows["A1"]["o_ton"] and rows["A5"]["atmo"] and rows["Musik1"]["musik"] and rows["A11"]["rest"]
    assert rows["ST"]["export"] and rows["ST"]["r128"] and rows["ST"]["channel"] == 1
    assert rows["Video"]["kind"] == "Video" and not rows["Video"]["audio"]
    assert v["selected"]["read_from"] == "helmut test" and v["session"] == "helmut test"
    saved = c.settings.get("import.spurprofile")
    assert saved[0]["template_gruppe"] == "test" and saved[0]["spuren"][0]["name"] == "ST"
    assert {t["group"] + "/" + t["name"] for t in v["templates"] if t["has_profile"]} == {"test/helmut test"}


def test_haekchen_setzen(ctx):
    c, _ = ctx
    PA.read(c, "test", "helmut test")
    v = PA.set_cell(c, "test", "helmut test", "A11", "atmo", True)                  # Rest → Atmo (nur eins)
    r = {x["name"]: x for x in v["selected"]["rows"]}["A11"]
    assert r["atmo"] and not r["rest"]
    v = PA.set_cell(c, "test", "helmut test", "A11", "atmo", False)
    assert not any({x["name"]: x for x in v["selected"]["rows"]}["A11"][k] for k in ("o_ton", "atmo", "musik", "rest"))
    v = PA.set_cell(c, "test", "helmut test", "Spr", "export", True)
    v = PA.set_cell(c, "test", "helmut test", "Spr", "channel", "13")
    v = PA.set_cell(c, "test", "helmut test", "Spr", "content", " Kommentar ")
    spr = {x["name"]: x for x in v["selected"]["rows"]}["Spr"]
    assert (spr["export"], spr["channel"], spr["content"]) == (True, 13, "Kommentar")
    v = PA.set_cell(c, "test", "helmut test", "ST", "export", False)                # R128 fällt mit weg
    st = {x["name"]: x for x in v["selected"]["rows"]}["ST"]
    assert not st["export"] and not st["r128"]
    with pytest.raises(CommandError, match="keine Audiospur"):
        PA.set_cell(c, "test", "helmut test", "Video", "export", True)
    with pytest.raises(CommandError, match="Kanal"):
        PA.set_cell(c, "test", "helmut test", "Spr", "channel", "x")
    with pytest.raises(CommandError, match="Kein Spurprofil"):
        PA.set_cell(c, "andere", "helmut test", "Spr", "export", True)


def test_schalter_erneut_lesen_und_entfernen(ctx):
    c, engine = ctx
    PA.read(c, "test", "helmut test")
    v = PA.set_switch(c, "test", "helmut test", "when_full", "nachfragen")
    v = PA.set_switch(c, "test", "helmut test", "hide_empty", False)
    assert v["selected"]["switches"]["when_full"] == "nachfragen" and not v["selected"]["switches"]["hide_empty"]
    engine.track_objs = [t for t in engine.track_objs if t.name != "A5"]          # Spur in der Session gelöscht
    v = PA.read(c, "test", "helmut test")
    assert "1 fehlen in der Session" in v["message"]
    assert any("„A5“ fehlt in der Session" in x for x in v["selected"]["problems"])
    with pytest.raises(CommandError, match="Nur Spuren, die in der Session fehlen"):
        PA.remove_track(c, "test", "helmut test", "A1")
    v = PA.remove_track(c, "test", "helmut test", "A5")
    assert "A5" not in [x["name"] for x in v["selected"]["rows"]]
    v = PA.remove_profile(c, "test", "helmut test")
    assert v["selected"] is None and c.settings.get("import.spurprofile") == []


def test_ohne_session_oder_template(tmp_path):
    c = _ctx(tmp_path, FakeEngine(session=None))
    with pytest.raises(CommandError, match="Keine Session offen"):
        PA.read(c, "test", "helmut test")
    with pytest.raises(CommandError, match="Template wählen"):
        PA.read(c, "test", "")
    assert PA.view(c)["selected"] is None and PA.view(c)["session"] is None


def test_eigener_abschnitt_in_den_einstellungen(tmp_path):
    from tonmischung.app import settings_view as sv
    from tonmischung.config.settings import load_schema
    schema = load_schema()
    imp = next(a for a in sv.areas(schema) if a["id"] == "import")
    assert {"id": "import.spurprofile", "title": "Spurprofile je Template"} in imp["sections"]
    s = Settings(local_path=tmp_path / "e.json")
    assert "import.spurprofile" not in [f["path"] for g in sv.section(s, schema, "import")["groups"] for f in g["fields"]]


def test_exportname_setzt_export_kanaele_und_inhalt(ctx):
    c, _ = ctx
    PA.read(c, "test", "helmut test")
    v = PA.view(c, "test", "helmut test")
    assert v["export_names"] == ["ST", "IT", "DoE", "AD", "OGA", "Musik", "Spr"]           # Reihenfolge wie Jens
    assert [x["key"] for x in v["columns"] if x["group"] == "Export"][0] == "export_name"
    v = PA.set_cell(c, "test", "helmut test", "A  IT", "export_name", "IT")
    r = {x["name"]: x for x in v["selected"]["rows"]}["A  IT"]
    assert (r["export_name"], r["export"], r["channel"], r["content"]) == ("IT", True, 3, "IT")
    v = PA.set_cell(c, "test", "helmut test", "Spr", "export_name", "DoE")
    assert {x["name"]: x for x in v["selected"]["rows"]}["Spr"]["channel"] == 7
    v = PA.set_cell(c, "test", "helmut test", "Spr", "export_name", "")                       # zurück: Spurname
    assert {x["name"]: x for x in v["selected"]["rows"]}["Spr"]["export_name"] == ""
    with pytest.raises(CommandError, match="nicht in der Auswahl"):
        PA.set_cell(c, "test", "helmut test", "Spr", "export_name", "XYZ")


def test_exportnamen_auf_der_profilseite_aendern(ctx, tmp_path):
    from tonmischung.app import commands, settings_view as sv
    from tonmischung.app.bridge import Bridge
    from tonmischung.config.settings import load_schema
    c, _ = ctx
    sec = sv.section(c.settings, load_schema(), "import.spurprofile")
    g = next(x for x in sec["groups"] if x["title"] == "Auswahl für die Spalte „Exportname“")
    f = g["fields"][0]
    assert f["path"] == "export.spuren_lautheit.exportnamen" and f["kind"] == "table"
    assert [col["key"] for col in f["columns"]] == ["name", "kanal", "inhalt"]
    PA.read(c, "test", "helmut test")
    PA.set_cell(c, "test", "helmut test", "Spr", "export_name", "Musik")
    bridge = Bridge(emit=lambda p: None, inline=True)
    commands._register_settings(bridge, c)
    names = [dict(e) for e in c.settings.get("export.spuren_lautheit.exportnamen")]
    names[5]["name"] = "Mus"                                                    # „Musik“ → „Mus“
    view = bridge.call("einstellung_setzen", {"path": "export.spuren_lautheit.exportnamen", "value": names})
    assert not view["errors"] and "In den Spurprofilen umbenannt: Musik → Mus" in view["hints"]
    v = PA.view(c, "test", "helmut test")
    assert {x["name"]: x for x in v["selected"]["rows"]}["Spr"]["export_name"] == "Mus"
    assert "Mus" in v["export_names"] and "Musik" not in v["export_names"]
    names = [e for e in names if e["name"] != "Mus"]                             # Name entfernt → Hinweis
    bridge.call("einstellung_setzen", {"path": "export.spuren_lautheit.exportnamen", "value": names})
    assert any("„Mus“ steht nicht mehr in der Auswahl" in x for x in PA.view(c, "test", "helmut test")["selected"]["problems"])


def test_profile_exportieren_und_importieren(ctx, tmp_path):
    import json
    c, _ = ctx
    PA.read(c, "test", "helmut test")
    target = tmp_path / "out" / "Spurprofil helmut test.json"
    target.parent.mkdir()
    v = PA.export_profiles(c, "test", "helmut test", choose=lambda name: str(target), now=lambda: "heute")
    assert f"1 Spurprofil(e) exportiert: {target}" == v["message"]
    doc = json.loads(target.read_text())
    assert doc["format"] == "tonmischung-spurprofile" and doc["profile"][0]["template_name"] == "helmut test"
    again = PA.export_profiles(c, everything=True, choose=lambda name: str(target))          # nie überschreiben
    assert again["message"].endswith("Spurprofil helmut test 2.json") and json.loads(target.read_text()) == doc
    assert sorted(p.name for p in target.parent.iterdir()) == ["Spurprofil helmut test 2.json", "Spurprofil helmut test.json"]
    assert PA.export_profiles(c, "test", "helmut test", choose=lambda name: "")["message"] == "Export abgebrochen"

    # anderer Mac: leere Einstellungen, Exportname „Atmos“ fehlt dort
    doc["exportnamen"].append({"name": "Atmos", "kanal": 15, "inhalt": "Atmos"})
    doc["profile"].append({**doc["profile"][0], "template_name": "zweites"})
    target.write_text(json.dumps(doc))
    other = _ctx(tmp_path / "anderer", FakeEngine(session=None))
    chk = PA.check_import(other, choose=lambda: str(target))
    assert [(i["name"], i["status"]) for i in chk["items"]] == [("helmut test", "neu"), ("zweites", "neu")]
    assert chk["new_names"] == ["Atmos"]
    v = PA.do_import(other, chk["path"], [chk["items"][0]["key"]])
    assert v["message"] == "1 Spurprofil(e) importiert, Exportnamen ergänzt: Atmos"
    assert [p["template_name"] for p in other.settings.get("import.spurprofile")] == ["helmut test"]
    assert PA.check_import(other, str(target))["items"][0]["status"] == "gleich"
    with pytest.raises(CommandError, match="Kein Profil gewählt"):
        PA.do_import(other, str(target), [])
    (tmp_path / "kaputt.json").write_text("{}")
    with pytest.raises(CommandError, match="Keine Spurprofil-Datei"):
        PA.check_import(other, str(tmp_path / "kaputt.json"))
    assert PA.check_import(other, choose=lambda: "")["message"] == "Import abgebrochen"
