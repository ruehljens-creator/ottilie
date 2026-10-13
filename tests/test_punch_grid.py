"""Spurraster und Presets im Einstellungsfenster (Plan PunchBuddy-Übernahme, Schritt 2b)."""
import pytest

from tonmischung.config.settings import Settings
from tonmischung.punch.grid import (GridError, delete_preset, grid, presets_view, rename_preset, save_preset,
                                     toggle)

SESSION = ["ST", "IT", "OGA", "Mus", "Spr", "A1", "A2", "A3", "A4"]


@pytest.fixture
def s(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local("aufnahme.spuren.rec_a", ["A1", "A2"])
    s.set_local("export.spuren_lautheit.spurtabelle", [
        {"pt_spur": "ST", "inhalt": "Sendeton", "export": True, "r128": True},
        {"pt_spur": "IT", "inhalt": "IT", "export": True, "r128": False},
        {"pt_spur": "", "inhalt": "leer", "export": False, "r128": False}])
    return s


def test_raster_aus_session_und_einstellungen(s):
    g = grid(s, SESSION)
    assert [c["label"] for c in g["columns"]] == ["Rec A", "Rec B", "Export", "Lautheit", "Play Input"]
    rows = {r["spur"]: r for r in g["rows"]}
    assert [r["spur"] for r in g["rows"]][:len(SESSION)] == SESSION        # Reihenfolge wie in Pro Tools
    assert rows["A1"]["rec_a"] and not rows["A3"]["rec_a"]
    assert rows["ST"]["export"] and rows["ST"]["r128"] and rows["IT"]["export"] and not rows["IT"]["r128"]
    assert rows["A1"]["export"] is None                                   # nicht in der Spurtabelle
    assert g["verbunden"]


def test_spuren_aus_einstellungen_die_in_der_session_fehlen(s):
    s.set_local("aufnahme.spuren.rec_b", ["Spr 2"])
    rows = {r["spur"]: r for r in grid(s, SESSION)["rows"]}
    assert rows["Spr 2"]["rec_b"] and not rows["Spr 2"]["in_session"]


def test_ohne_pro_tools(s):
    g = grid(s, None)
    assert not g["verbunden"] and {r["spur"] for r in g["rows"]} == {"A1", "A2", "ST", "IT"}


def test_haekchen_setzen_und_entfernen(s):
    assert toggle(s, "rec_a", "A3", True) == "aufnahme.spuren.rec_a"
    assert s.get("aufnahme.spuren.rec_a") == ["A1", "A2", "A3"]
    toggle(s, "rec_a", "A1", False)
    assert s.get("aufnahme.spuren.rec_a") == ["A2", "A3"]
    toggle(s, "play_input", "Spr", True)
    assert s.get("aufnahme.spuren.play_input") == ["Spr"]


def test_export_und_lautheit_in_der_spurtabelle(s):
    toggle(s, "r128", "IT", True)                       # Lautheit setzt Export mit
    it = next(r for r in s.get("export.spuren_lautheit.spurtabelle") if r["pt_spur"] == "IT")
    assert it["export"] and it["r128"]
    toggle(s, "export", "ST", False)                    # ohne Export keine Lautheit
    st = next(r for r in s.get("export.spuren_lautheit.spurtabelle") if r["pt_spur"] == "ST")
    assert not st["export"] and not st["r128"]
    with pytest.raises(GridError, match="nicht in der Spurtabelle"):
        toggle(s, "export", "A1", True)


def test_presets_anlegen_ueberschreiben_umbenennen_loeschen(s):
    s.set_local("aufnahme.presets", [])
    assert save_preset(s, "Magazin") == 1
    p = s.get("aufnahme.presets")[0]
    assert p["rec_a"] == ["A1", "A2"] and p["export_tracks"] == ["ST", "IT"] and p["loudness_tracks"] == ["ST"]
    toggle(s, "rec_a", "A3", True)
    assert save_preset(s, "Magazin neu", nr=1) == 1
    assert s.get("aufnahme.presets")[0]["rec_a"] == ["A1", "A2", "A3"]
    save_preset(s, "News")
    rename_preset(s, 2, "Nachrichten")
    assert [v["name"] for v in presets_view(s)] == ["Magazin neu", "Nachrichten"]
    assert "Rec A: A1, A2, A3" in presets_view(s)[0]["inhalt"]
    delete_preset(s, 1)
    assert [v["nr"] for v in presets_view(s)] == [1] and presets_view(s)[0]["name"] == "Nachrichten"
    with pytest.raises(GridError, match="Namen"):
        save_preset(s, "  ")
    with pytest.raises(GridError, match="gibt es nicht"):
        delete_preset(s, 5)


def test_fensterbefehle_spurraster_und_presets(tmp_path):
    """Bridge-Befehle für das Einstellungsfenster, Spurabfrage gegen die Pro-Tools-Attrappe."""
    from fakes import FakeEngine, _Track
    from tonmischung.app import punch_app
    from tonmischung.app.bridge import Bridge, CommandError
    from tonmischung.ptsl.client import PtslClient

    class Ctx:
        pass
    ctx = Ctx()
    engine = FakeEngine(session=("Test", "/T/Test.ptx"))
    engine.track_objs = [_Track(n, "AudioTrack", False) for n in ("ST", "IT", "A1", "A2")] + [_Track("Video 1", "VideoTrack", True)]
    ctx.ptsl, ctx.settings = PtslClient(engine_factory=lambda: engine), Settings(local_path=tmp_path / "e.json")
    punch = punch_app.start(ctx, lambda: None, on_error=lambda t, m: None, on_busy=lambda b: None)
    try:
        bridge = Bridge(emit=lambda p: None, inline=True)
        punch_app.register(bridge, punch)
        g = bridge.call("spurraster", {})
        assert g["verbunden"] and "4 Spuren aus „Test“ gelesen" == g["meldung"]
        assert [r["spur"] for r in g["rows"]][:4] == ["ST", "IT", "A1", "A2"] and "Video 1" not in [r["spur"] for r in g["rows"]]
        g = bridge.call("spurraster_setzen", {"spalte": "rec_b", "spur": "A2", "an": True})
        assert ctx.settings.get("aufnahme.spuren.rec_b") == ["A2"]
        with pytest.raises(CommandError, match="nicht in der Spurtabelle"):
            bridge.call("spurraster_setzen", {"spalte": "export", "spur": "A1", "an": True})
        ctx.settings.set_local("aufnahme.presets", [])
        assert bridge.call("preset_speichern", {"name": "Abend"})["presets"][0]["name"] == "Abend"
        assert bridge.call("preset_umbenennen", {"nr": 1, "name": "Spät"})["presets"][0]["name"] == "Spät"
        assert bridge.call("punch_preset", {"nr": 1}) == {"ok": True, "meldung": "preset 1 loaded"}
        assert bridge.call("preset_loeschen", {"nr": 1})["presets"] == []
    finally:
        punch.restore()


def test_audio_verschieben_paarweise_wie_punchbuddy(s):
    """Jens 09.10.2026: Spurpaare Quelle → Ziel mit Spuren aus Pro Tools; nur vollständige Paare werden gespeichert."""
    from tonmischung.punch import grid as g
    v = g.move_view(s, ["ST", "Spr", "Spr 2"])
    assert v["paare"] == [["", ""]] and v["spuren"] == ["ST", "Spr", "Spr 2"] and v["verbunden"]
    assert g.set_move_pairs(s, [["Spr", "Spr 2"], ["ST", ""], ["", "OGA"]]) == 1
    assert s.get(g.MOVE_SRC) == ["Spr"] and s.get(g.MOVE_DST) == ["Spr 2"]
    v = g.move_view(s, None)                                      # Pro Tools nicht erreichbar
    assert v["paare"] == [["Spr", "Spr 2"]] and v["fehlen"] == ["Spr", "Spr 2"] and not v["verbunden"]
    with pytest.raises(g.GridError, match="dieselbe Spur"):
        g.set_move_pairs(s, [["ST", "ST"]])
    assert s.get(g.MOVE_SRC) == ["Spr"]                            # nichts geändert
    s.set_local(g.MOVE_SRC, ["A", "B"])                            # alte, ungleich lange Listen
    assert g.move_view(s, [])["paare"] == [["A", "Spr 2"], ["B", ""]]
