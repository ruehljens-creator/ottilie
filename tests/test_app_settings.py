from tonmischung.app import settings_view as sv
from tonmischung.config.settings import Settings, load_schema

SCHEMA = load_schema()


def _s(tmp_path, managed=None):
    return Settings(local_path=tmp_path / "e.json", managed=managed)


def test_bereiche_und_abschnitte():
    a = {x["id"]: x for x in sv.areas(SCHEMA)}
    assert list(a) == ["persoenlich", "aufnahme", "import", "export", "system"]
    # „Allgemein“ entfällt: dort stand nur „Abweichende Import-Einstellungen je Template“ (ohne Funktion, ausgeblendet)
    assert [s["title"] for s in a["import"]["sections"]][:4] == ["Zuordnungsregeln", "Zielspuren", "Spurprofile je Template", "Timecode & Startband"]
    assert "Spuren & Lautheit" in [s["title"] for s in a["export"]["sections"]]


def test_felder_mit_art_herkunft_und_beschriftung(tmp_path):
    sec = sv.section(_s(tmp_path), SCHEMA, "import.zuordnungsregeln")
    fields = {f["path"]: f for g in sec["groups"] for f in g["fields"]}
    ms = fields["import.zuordnungsregeln.mindest_sicherheit"]
    assert (ms["kind"], ms["value"], ms["min"], ms["max"], ms["step"]) == ("number", 80, 50, 100, 5)
    assert ms["layer_text"] == "Hausstandard" and not ms["resettable"]
    assert any("hängt am Nutzer" in h for h in ms["hints"])              # Plan Nutzer & Räume, Schritt 2
    assert fields["import.zuordnungsregeln.aktiv"]["kind"] == "bool"
    assert fields["import.zuordnungsregeln.musik_pfade"]["kind"] == "list"
    kaskade = fields["import.zuordnungsregeln.kaskade"]
    assert kaskade["kind"] == "table" and [c["key"] for c in kaskade["columns"]] == ["id", "aktiv"]
    assert [g["title"] for g in sec["groups"]] == ["", "Schlüsselwörter"]
    assert fields["import.zuordnungsregeln.schluesselwoerter.o_ton"]["label"] == "O-Ton"


def test_setzen_pruefen_zuruecksetzen(tmp_path):
    s = _s(tmp_path)
    bad = sv.set_value(s, SCHEMA, "import.zuordnungsregeln.mindest_sicherheit", 83)
    assert bad["errors"] == ["Mindest-Sicherheit (%): in Schritten von 5"] and bad["value"] == 83
    assert s.get("import.zuordnungsregeln.mindest_sicherheit") == 80                 # nicht gespeichert
    ok = sv.set_value(s, SCHEMA, "import.zuordnungsregeln.mindest_sicherheit", 90)
    assert ok["errors"] == [] and ok["layer"] == "lokal" and ok["resettable"]
    table = s.get("export.spuren_lautheit.spurtabelle")
    table[1]["pt_spur"] = "IT"
    assert sv.set_value(s, SCHEMA, "export.spuren_lautheit.spurtabelle", table)["errors"] == []
    assert Settings(local_path=tmp_path / "e.json").get("export.spuren_lautheit.spurtabelle")[1]["pt_spur"] == "IT"
    back = sv.reset_value(s, SCHEMA, "import.zuordnungsregeln.mindest_sicherheit")
    assert back["value"] == 80 and back["layer"] == "standard"


def test_hausstandards_bestehen_die_eigene_pruefung(tmp_path):
    s = _s(tmp_path)
    from tonmischung.config.settings import validate
    bad = {p: validate(sv._resolve(SCHEMA, f.node), s.get(p)) for p, f in s.fields.items() if f.has_default and s.get(p) is not None}
    assert {p: e for p, e in bad.items() if e} == {}


def test_mdm_gesperrt(tmp_path):
    s = _s(tmp_path, managed=lambda k: (k == "system.schnittstellen.mcp_nur_lesen", False))
    f = sv.field_view(s, SCHEMA, "system.schnittstellen.mcp_nur_lesen")
    assert f["locked"] and f["layer_text"] == "Geräteverwaltung – gesperrt" and f["value"] is False
    r = sv.set_value(s, SCHEMA, "system.schnittstellen.mcp_nur_lesen", True)
    assert r["errors"] and "Geräteverwaltung" in r["errors"][0]


def test_standard_template_aus_meinen_templates(tmp_path):
    s = _s(tmp_path)
    s.set_local("persoenlich.templates", [{"id": "hs", "gruppe": "test", "pt_name": "helmut test", "anzeigename": "Heimatjournal"}])
    f = sv.field_view(s, SCHEMA, "persoenlich.standard_template_id")
    assert f["kind"] == "enum" and f["options"] == ["", "hs"] and f["option_labels"] == ["– keiner –", "Heimatjournal"]


def test_spurdaten_preset_auswahl(tmp_path, monkeypatch):
    from tonmischung.app import settings_view as sv
    from tonmischung.config.settings import Settings, load_schema
    d = tmp_path / "Track Data to Import"
    d.mkdir()
    (d / "Ottilie Clips und Automation.ptpreset").write_bytes(b"x")
    (d / "Quick Preset 1.ptpreset").write_bytes(b"x")
    monkeypatch.setattr(sv, "TRACK_DATA_DIR", str(d))
    monkeypatch.setattr(sv.track_data_presets, "__defaults__", (str(d),))
    monkeypatch.setattr(sv.track_data_preset_path, "__defaults__", (str(d),))
    s = Settings(local_path=tmp_path / "e.json")
    v = sv.field_view(s, load_schema(), "import.import_vorschau.trackdaten_preset")
    assert v["kind"] == "enum" and v["options"] == ["", "Ottilie Clips und Automation", "Quick Preset 1"]
    assert v["value"] == "Ottilie Clips und Automation"                       # Vorgabe der App
    assert v["option_labels"][0].startswith("– kein Preset")
    assert sv.track_data_preset_path("Ottilie Clips und Automation").endswith("Ottilie Clips und Automation.ptpreset")
    assert sv.track_data_preset_path("gibts nicht") == "" and sv.track_data_preset_path("") == ""


def test_standard_preset_wird_angelegt_nie_ueberschrieben(tmp_path):
    from tonmischung.app import settings_view as sv
    d = tmp_path / "Track Data to Import"
    path = sv.track_data_preset_path("Ottilie Clips und Automation", str(d))      # fehlt → wird angelegt
    assert path and (d / "Ottilie Clips und Automation.ptpreset").read_bytes()[:16] == b"\x03001011110010101"
    flags = (d / "Ottilie Clips und Automation.ptpreset").read_bytes()[0x3a:0x3a + 30]
    assert flags[:7] == b"\x01" * 7 and flags[7:27] == b"\x00" * 20     # Preset 1 + Feld 6 (Volume-Automation)
    (d / "Ottilie Clips und Automation.ptpreset").write_bytes(b"eigenes")          # vom Mischer geändert
    assert sv.install_default_preset(str(d)).endswith(".ptpreset")
    assert (d / "Ottilie Clips und Automation.ptpreset").read_bytes() == b"eigenes"
    assert sorted(p.name for p in d.iterdir()) == ["Ottilie Clips und Automation.ptpreset"]


def test_vorgabe_fuer_missing_files_mit_lesbaren_optionen(tmp_path):
    from tonmischung.app import settings_view as sv
    from tonmischung.config.settings import Settings, load_schema
    s = Settings(local_path=tmp_path / "e.json")
    v = sv.field_view(s, load_schema(), "import.import_vorschau.fehlende_dateien")
    assert v["kind"] == "enum" and v["value"] == "automatisch_suchen"
    assert v["options"] == ["automatisch_suchen", "alle_ueberspringen", "selbst_bestaetigen"]
    assert v["option_labels"][0].startswith("Automatisch suchen und verknüpfen") and v["label"].startswith("Dialog")


def test_ausgeblendet_ist_was_noch_nicht_wirkt(tmp_path):
    """Jens 03.10.2026: ausblenden, was noch nicht wirkt (`x-ab`). Wächter: Liest Code eine ausgeblendete Einstellung,
    muss `x-ab` dort weg – sonst sähe der Mischer eine wirksame Einstellung nicht."""
    import pathlib
    from tonmischung.app import settings_view as sv
    from tonmischung.config.settings import Settings, load_schema, schema_fields
    schema = load_schema()
    fields = schema_fields(schema)
    hidden = [p for p, f in fields.items() if sv.hidden(f.node)]
    assert len(hidden) >= 100
    root = pathlib.Path(sv.__file__).parents[1]
    code = "\n".join(p.read_text(encoding="utf-8") for p in root.rglob("*.py") if p.name != "settings_view.py")
    assert [p for p in hidden if p in code] == []
    s = Settings(local_path=tmp_path / "e.json")
    shown = {f["path"] for a in sv.areas(schema) for sec in a["sections"]
             for g in sv.section(s, schema, sec["id"])["groups"] for f in g["fields"]}
    assert not shown & set(hidden) and "export.pse.pruefen" not in shown
    assert "Prüfung" not in [sec["title"] for a in sv.areas(schema) for sec in a["sections"]]   # ganz ausgeblendet
