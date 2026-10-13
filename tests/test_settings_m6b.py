import pytest

from tonmischung.config.settings import SettingLockedError, Settings, validate
from tonmischung.triage.placement import TargetRanges, _rest_tracks, expand_range, ranges_from_settings


def test_standard_am_objekt_gilt_fuer_die_felder(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    p = "import.zielspuren.bereiche."
    assert (s.get(p + "o_ton.von"), s.get(p + "o_ton.bis")) == ("A1", "A4")
    assert (s.get(p + "musik.von"), s.get(p + "musik.bis")) == ("Musik1", "Musik8")      # Jens
    assert (s.get(p + "rest.von"), s.get(p + "rest.bis")) == ("A11", "")                 # ohne Ende
    assert s.fields[p + "o_ton.von"].node.get("type") == "string"


def test_pruefen():
    assert validate({"type": "integer", "minimum": 50, "maximum": 100, "multipleOf": 5}, 80) == []
    assert validate({"type": "integer", "minimum": 50}, 40) == ["Wert: mindestens 50"]
    assert validate({"type": "integer", "multipleOf": 5}, 82) == ["Wert: in Schritten von 5"]
    assert validate({"type": "integer"}, "80") == ["Wert: ganze Zahl erwartet"]
    assert validate({"type": "integer"}, True) == ["Wert: ganze Zahl erwartet"]
    assert validate({"enum": ["immer", "nie"]}, "oft") == ["Wert: nur immer, nie"]
    assert validate({"type": "boolean"}, 1) == ["Wert: ja oder nein"]
    assert validate({"type": "array", "items": {"type": "string"}}, ["a", 3]) == ["Wert, Eintrag 2: Text erwartet"]
    table = {"type": "array", "items": {"type": "object", "required": ["id"],
                                        "properties": {"id": {"type": "string"}, "n": {"type": "integer"}}}}
    assert validate(table, [{"id": "x", "n": 1}]) == []
    assert validate(table, [{"n": "z"}], "Spuren") == ["Spuren, Zeile 1, n: ganze Zahl erwartet", "Spuren, Zeile 1: id fehlt"]


def test_zuruecksetzen_und_gesperrt(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local("import.zuordnungsregeln.mindest_sicherheit", 90)
    assert s.get("import.zuordnungsregeln.mindest_sicherheit") == 90
    s.reset_local("import.zuordnungsregeln.mindest_sicherheit")
    assert s.get("import.zuordnungsregeln.mindest_sicherheit") == 80
    locked = Settings(local_path=tmp_path / "f.json", managed=lambda k: (k.endswith("mcp_aktiv"), False))
    with pytest.raises(SettingLockedError):
        locked.reset_local("system.schnittstellen.mcp_aktiv")


def test_zielspuren_aus_den_einstellungen(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    r = ranges_from_settings(s)
    assert r.oton == ("A1", "A2", "A3", "A4") and r.music[-1] == "Musik8"
    assert (r.rest_prefix, r.rest_from, r.rest_to) == ("A", 11, None)
    s.set_local("import.zielspuren.bereiche.rest.bis", "A14")
    assert ranges_from_settings(s).rest_to == 14
    assert expand_range("Musik 3", "Musik 5") == ("Musik3", "Musik4", "Musik5")
    with pytest.raises(ValueError):
        expand_range("A1", "M4")
    tpl = ["A1", "A11", "A12", "A13", "A14", "A15", "A20", "Musik1"]
    assert _rest_tracks(tpl, 11, "A", 14) == ["A11", "A12", "A13", "A14"]
    assert _rest_tracks(tpl, 11) == ["A11", "A12", "A13", "A14", "A15", "A20"]
    assert TargetRanges().rest_to is None
