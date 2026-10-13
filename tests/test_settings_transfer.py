"""Alle Einstellungen exportieren und importieren – mit Auswahl je Abschnitt und je Spurprofil."""
import json

import pytest

from tonmischung.app import settings_transfer_app as STA
from tonmischung.app.bridge import CommandError
from tonmischung.app.context import AppContext
from tonmischung.config.settings import Settings
from tonmischung.core import settings_transfer as ST
from tonmischung.core import track_profile as TP
from tonmischung.helmut.client import HelmutClient
from tonmischung.ptsl.client import PtslClient
from fakes import FakeEngine


def _ctx(folder, managed=None):
    s = Settings(local_path=folder / "e.json", managed=managed)
    return AppContext(s, HelmutClient("http://127.0.0.1:9", lambda: ("t", "t")),
                      PtslClient(engine_factory=lambda: FakeEngine(session=None)), "Jens", "Ton 3", str(folder), None, "x")


def _profile(name, *tracks):
    return TP.profile_to_dict(TP.TrackProfile("test", name, tuple(TP.ProfileTrack(t, triage="o_ton") for t in tracks)))


@pytest.fixture
def source(tmp_path):
    ctx = _ctx(tmp_path / "quelle")
    s = ctx.settings
    s.set_local("import.zuordnungsregeln.mindest_sicherheit", 70)
    s.set_local("export.spuren_lautheit.lautheit.ziel_lufs", -24)
    s.set_local("aufnahme.spuren.rec_a", ["ST", "Spr"])                         # nur für einen Mac
    s.set_local("system.raeume_nutzer.raum_fest", "Ton 3")
    s.set_local("import.spurprofile", [_profile("helmut test", "A1", "A2"), _profile("Doku_lang", "Dialog")])
    return ctx


def test_export_nur_lokale_werte(source, tmp_path):
    target = tmp_path / "Einstellungen.json"
    r = STA.export_all(source, choose=lambda n: str(target), now=lambda: "heute")
    doc = json.loads(target.read_text())
    assert doc["format"] == "tonmischung-einstellungen" and doc["von"] == "Jens · Ton 3"
    assert set(doc["werte"]) == {"import.zuordnungsregeln.mindest_sicherheit", "export.spuren_lautheit.lautheit.ziel_lufs",
                                 "aufnahme.spuren.rec_a", "system.raeume_nutzer.raum_fest", "import.spurprofile"}
    assert r["message"].startswith("5 Einstellungen exportiert")
    again = STA.export_all(source, choose=lambda n: str(target))                  # nie überschreiben
    assert again["path"].endswith("Einstellungen 2.json")
    assert STA.export_all(_ctx(tmp_path / "leer"))["message"].startswith("Auf diesem Mac ist nichts geändert")


def test_import_mit_auswahl(source, tmp_path):
    target = tmp_path / "Einstellungen.json"
    STA.export_all(source, choose=lambda n: str(target))
    other = _ctx(tmp_path / "ziel")
    other.settings.set_local("import.spurprofile", [_profile("helmut test", "A3")])   # vorhandenes, anderes Profil
    chk = STA.check(other, choose=lambda: str(target))
    by = {c["id"]: c for c in chk["choices"]}
    assert by["import.zuordnungsregeln"]["default"] and by["import.zuordnungsregeln"]["changed"] == 1
    assert by["export.spuren_lautheit"]["area"] == "Export und Auslieferung"
    assert not by["dieser_mac"]["default"] and by["dieser_mac"]["count"] == 2          # Aufnahmespuren, Raum
    assert by["spurprofil:test/helmut test"]["status"] == "ersetzt"
    assert by["spurprofil:test/doku_lang"]["status"] == "neu"
    r = STA.do_import(other, chk["path"], ["import.zuordnungsregeln", "spurprofil:test/doku_lang"])
    assert r["message"] == "1 Einstellungen übernommen, 1 Spurprofil(e)"
    s = other.settings
    assert s.get("import.zuordnungsregeln.mindest_sicherheit") == 70
    assert s.get("export.spuren_lautheit.lautheit.ziel_lufs") == -23                 # nicht gewählt
    assert s.get("system.raeume_nutzer.raum_fest") != "Ton 3"
    assert [p["template_name"] for p in s.get("import.spurprofile")] == ["helmut test", "Doku_lang"]
    assert s.get("import.spurprofile")[0]["spuren"][0]["name"] == "A3"                 # vorhandenes blieb
    with pytest.raises(CommandError, match="Nichts gewählt"):
        STA.do_import(other, chk["path"], [])


def test_mdm_gesperrte_werte_bleiben(source, tmp_path):
    target = tmp_path / "E.json"
    STA.export_all(source, choose=lambda n: str(target))
    managed = lambda key: (True, 80) if key == "import.zuordnungsregeln.mindest_sicherheit" else (False, None)
    other = _ctx(tmp_path / "mdm", managed)
    chk = STA.check(other, str(target))
    assert {c["id"]: c for c in chk["choices"]}["import.zuordnungsregeln"]["locked"] == 1
    r = STA.do_import(other, str(target), [c["id"] for c in chk["choices"]])
    assert "1 von der Geräteverwaltung gesperrt" in r["message"]
    assert other.settings.get("import.zuordnungsregeln.mindest_sicherheit") == 80


def test_fremde_dateien_und_unbekannte_werte(tmp_path):
    other = _ctx(tmp_path / "x")
    (tmp_path / "f.json").write_text('{"format": "etwas anderes"}')
    with pytest.raises(CommandError, match="Keine Einstellungsdatei"):
        STA.check(other, str(tmp_path / "f.json"))
    doc = ST.export_document({"gibt.es.nicht": 1, "import.zuordnungsregeln.mindest_sicherheit": 75})
    (tmp_path / "g.json").write_text(json.dumps(doc))
    chk = STA.check(other, str(tmp_path / "g.json"))
    assert chk["unknown"] == ["gibt.es.nicht"] and [c["id"] for c in chk["choices"]] == ["import.zuordnungsregeln"]
    assert STA.check(other, choose=lambda: "")["message"] == "Import abgebrochen"
    with pytest.raises(ST.TransferFormatError, match="neueren Version"):
        ST.read_document({"format": ST.FORMAT, "version": 9, "werte": {"a": 1}})


def test_abschnitt_zuordnen():
    secs = ["import", "import.zielspuren", "import.import_vorschau"]
    assert ST.section_of("import.zielspuren.bereiche.o_ton.von", secs) == "import.zielspuren"
    assert ST.section_of("import.profile_je_template", secs) == "import"
    assert ST.section_of("export.x", secs) == "export"


def test_abschnitt_in_den_einstellungen():
    from tonmischung.app import settings_view as sv
    from tonmischung.config.settings import load_schema
    system = next(a for a in sv.areas(load_schema()) if a["id"] == "system")
    assert system["sections"][-2:] == [{"id": "system.einstellungen_uebertragen", "title": "Einstellungen übertragen"},
                                       {"id": "system.ueber", "title": "Über Ottilie"}]
