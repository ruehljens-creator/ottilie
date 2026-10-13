"""Einstellungen zwischen Tonmischung und PunchBuddy-Kern (Plan PunchBuddy-Übernahme, Schritt 1)."""

from tonmischung.config.settings import LAYER_LOCAL, Settings, load_schema
from tonmischung.punch.settings_bridge import import_punchbuddy, mapping, punch_settings

SCHEMA = load_schema()
# Auszug aus PunchBuddys DEFAULT_SETTINGS (punchbuddy/config.py)
PB_DEFAULTS = {
    "tracks": ["ST", "A  IT", "OGA", "Mus", "Spr", "Spr 2"], "video_track": "Video 1",
    "export_start_tc": "10:00:00:00", "http_port": 8899, "http_bind_host": "127.0.0.1", "webtrigger_token": "",
    "interplay_enabled": False, "interplay_workspace_steps": 17, "language": "de", "extend_count": 7,
}


def _s(tmp_path, **managed):
    return Settings(local_path=tmp_path / "e.json", managed=(lambda: managed) if managed else None)


def test_zuordnung_aus_dem_schema():
    m = mapping(SCHEMA)
    assert m["tracks"] == "aufnahme.spuren.rec_a"
    assert m["play_custom_ch2_mute_stop"] == "aufnahme.play_custom.kanal_2_stumm_bei_stopp"
    assert m["export_start_tc"] == "import.timecode_startband.programmbeginn"
    assert m["vocaster_apply_routing_on_start"] == "system.vocaster.routing_beim_start"
    assert m["unix_socket_path"] == "system.streamdeck_webtrigger.unix_socket_pfad"
    assert not any(k.startswith("interplay") for k in m)
    assert "export_tracks" not in m                     # kommt aus der Spurtabelle


def test_alle_punchbuddy_schluessel_haben_ein_zuhause():
    """Jeder Schlüssel aus PunchBuddys Vorgaben ist zugeordnet, Spurtabelle oder bewusst entfallen."""
    from tonmischung.vendor import import_isolated
    config = import_isolated("punchbuddy.config")
    from tonmischung.punch.settings_bridge import DROPPED
    m = mapping(SCHEMA)
    rest = [k for k in config.DEFAULT_SETTINGS if k not in m and k not in DROPPED
            and not k.startswith("interplay_") and k not in ("export_tracks", "loudness_tracks")]
    assert rest == []


def test_kern_bekommt_unsere_werte(tmp_path):
    s = _s(tmp_path)
    s.set_local("aufnahme.spuren.rec_b", ["A1", "A2"])
    s.set_local("system.streamdeck_webtrigger.bind_adresse", "netzwerk")
    d = punch_settings(s, SCHEMA, PB_DEFAULTS, token="geheim")
    assert d["tracks_b"] == ["A1", "A2"] and d["tracks"] == ["ST", "A  IT", "OGA", "Mus", "Spr", "Spr 2"]
    assert d["http_bind_host"] == "0.0.0.0" and d["webtrigger_token"] == "geheim"
    assert d["export_start_tc"] == "10:00:00:00" and d["language"] == "de"
    assert d["unix_socket_path"] == "/tmp/punchbuddy.sock" and d["http_port"] == 8899
    assert "interplay_enabled" not in d and d["extend_count"] == 7     # Vorgabe von PunchBuddy bleibt
    assert d["export_tracks"][0] == "ST" and d["loudness_tracks"] == ["ST"]   # aus der Spurtabelle


def test_uebernahme_aus_punchbuddy(tmp_path):
    s = _s(tmp_path)
    pb = {"tracks_b": ["A3", "A4"], "play_custom_ch1_track": "KH1", "http_bind_host": "0.0.0.0",
          "webtrigger_token": "abc", "export_tracks": ["ST", "A  IT"], "loudness_tracks": ["ST"],
          "interplay_enabled": True, "language": "en", "video_track": "Video 1"}
    rows = {r.key: r for r in import_punchbuddy(s, SCHEMA, pb)}
    assert rows["tracks_b"].status == "übernommen" and s.get("aufnahme.spuren.rec_b") == ["A3", "A4"]
    assert s.source("aufnahme.spuren.rec_b") == LAYER_LOCAL
    assert rows["http_bind_host"].status == "übernommen" and s.get("system.streamdeck_webtrigger.bind_adresse") == "netzwerk"
    assert rows["video_track"].status == "gleich"
    assert rows["webtrigger_token"].status == "nicht übernommen" and rows["webtrigger_token"].value == "••••"
    assert rows["export_tracks / loudness_tracks"].status == "nicht übernommen"
    assert rows["interplay_enabled"].status == "entfällt" and rows["language"].status == "entfällt"
    assert s.get("export.spuren_lautheit.spurtabelle")[0]["pt_spur"] == "ST"   # Spurtabelle unverändert


def test_lokal_gesetzte_werte_bleiben_ausser_mit_ueberschreiben(tmp_path):
    s = _s(tmp_path)
    s.set_local("aufnahme.play_custom.kanal_1_spur", "KH3")
    r = import_punchbuddy(s, SCHEMA, {"play_custom_ch1_track": "KH1"})[0]
    assert r.status == "übersprungen" and s.get("aufnahme.play_custom.kanal_1_spur") == "KH3"
    r = import_punchbuddy(s, SCHEMA, {"play_custom_ch1_track": "KH1"}, overwrite=True)[0]
    assert r.status == "übernommen" and s.get("aufnahme.play_custom.kanal_1_spur") == "KH1"


def test_probelauf_aendert_nichts(tmp_path):
    s = _s(tmp_path)
    r = import_punchbuddy(s, SCHEMA, {"tracks_b": ["A9"]}, dry_run=True)[0]
    assert r.status == "übernommen" and s.get("aufnahme.spuren.rec_b") == []
    assert not (tmp_path / "e.json").exists()


def test_ungueltige_werte_werden_gemeldet(tmp_path):
    s = _s(tmp_path)
    rows = {r.key: r for r in import_punchbuddy(s, SCHEMA, {"http_port": "achtzig", "aaf_reference_bit_depth": 20})}
    assert rows["http_port"].status in ("ungültig", "gesperrt")
    assert rows["aaf_reference_bit_depth"].status == "ungültig"
    assert s.get("export.dateiexporte.aaf_referenz_bittiefe") == 24


def test_presets_mit_zusatzfeldern(tmp_path):
    s = _s(tmp_path)
    presets = [{"name": "Abend", "rec_a": ["ST"], "mon_a": [], "rec_b": [], "mon_b": [], "export": ["ST"],
                "loudness_tracks": ["ST"], "play_monitor_tracks": []}]
    r = import_punchbuddy(s, SCHEMA, {"track_presets": presets})[0]
    assert r.status == "übernommen" and s.get("aufnahme.presets")[0]["loudness_tracks"] == ["ST"]


def test_befehl_einstellungen_uebernehmen(tmp_path, capsys, monkeypatch):
    import json
    from tonmischung import cli, factory
    datei = tmp_path / "settings.json"
    datei.write_text(json.dumps({"tracks_b": ["A5", "A6"], "interplay_enabled": False}))
    s = _s(tmp_path)
    monkeypatch.setattr(factory, "load_settings", lambda: s)
    assert cli.main(["einstellungen-uebernehmen", "--datei", str(datei), "--probe"]) == 0
    out = capsys.readouterr().out
    assert "übernommen" in out and "Probelauf" in out and s.get("aufnahme.spuren.rec_b") == []
    assert cli.main(["einstellungen-uebernehmen", "--datei", str(datei)]) == 0
    assert s.get("aufnahme.spuren.rec_b") == ["A5", "A6"]
    assert cli.main(["einstellungen-uebernehmen", "--datei", str(tmp_path / "fehlt.json")]) == 1
