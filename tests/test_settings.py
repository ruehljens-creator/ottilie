import json
from collections import Counter

import pytest

from tonmischung.config.settings import (
    LAYER_CENTRAL, LAYER_DEFAULT, LAYER_MDM, LAYER_LOCAL, SettingLockedError, Settings,
    load_schema, read_managed, schema_fields)

LUFS = "export.spuren_lautheit.lautheit.ziel_lufs"
PTSL_TIMEOUT = "system.schnittstellen.ptsl.zeitlimit_s"
HELMUT_URL = "system.schnittstellen.helmut.adresse"


def _mdm(values):
    return lambda key: (key in values, values.get(key))


def test_schema_felder_und_quellen():
    fields = schema_fields(load_schema())
    assert fields[LUFS].default == -23
    assert fields[PTSL_TIMEOUT].source == "mdm"
    assert fields["persoenlich.templates"].source == "helmut-nutzer"
    quellen = Counter(f.source for f in fields.values())
    assert quellen == {"helmut": 147, "mdm": 42, "lokal": 39, "helmut-nutzer": 6}    # − Kanäle 7/8, Stille (09.10.2026)
    assert fields["import.zuordnungsregeln.aktiv"].default is True          # Triage abschaltbar


def test_hausstandard_ohne_weitere_quellen(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    assert s.get(LUFS) == -23
    assert s.source(LUFS) == LAYER_DEFAULT


def test_rangfolge_mdm_zentral_lokal_standard(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local(LUFS, -24)
    assert (s.get(LUFS), s.source(LUFS)) == (-24, LAYER_LOCAL)
    s.update_central({LUFS: -22})
    assert (s.get(LUFS), s.source(LUFS)) == (-22, LAYER_CENTRAL)
    s = Settings(local_path=tmp_path / "e.json", managed=_mdm({LUFS: -21}), central={LUFS: -22})
    assert (s.get(LUFS), s.source(LUFS)) == (-21, LAYER_MDM)
    assert s.locked(LUFS)


def test_mdm_wert_laesst_sich_lokal_nicht_aendern(tmp_path):
    s = Settings(local_path=tmp_path / "e.json", managed=_mdm({HELMUT_URL: "https://helmut.test/api"}))
    with pytest.raises(SettingLockedError):
        s.set_local(HELMUT_URL, "https://anders")
    assert not (tmp_path / "e.json").exists()


def test_unbekannte_einstellung(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    with pytest.raises(KeyError):
        s.get("gibt.es.nicht")
    with pytest.raises(KeyError):
        s.set_local("gibt.es.nicht", 1)


def test_lokal_atomar_und_wieder_gelesen(tmp_path):
    pfad = tmp_path / "unter" / "e.json"
    Settings(local_path=pfad).set_local(PTSL_TIMEOUT, 45)
    assert json.loads(pfad.read_text()) == {PTSL_TIMEOUT: 45}
    assert list(pfad.parent.iterdir()) == [pfad]          # keine Hilfsdatei übrig
    assert Settings(local_path=pfad).get(PTSL_TIMEOUT) == 45


def test_kaputte_lokale_datei_faellt_auf_standard_zurueck(tmp_path):
    pfad = tmp_path / "e.json"
    pfad.write_text("{kein json")
    assert Settings(local_path=pfad).get(LUFS) == -23


def test_zentral_ignoriert_unbekannte(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.update_central({"gibt.es.nicht": 1, LUFS: -22})
    assert s.get(LUFS) == -22


def test_mdm_leser_ohne_profil_erzwingt_nichts():
    reader = read_managed("de.ottilie.test-ohne-profil")
    assert reader(LUFS) == (False, None)


def test_mdm_doku_aktuell_und_plist_lesbar():
    """M9: Schlüssel für das Konfigurationsprofil dokumentiert – aus dem Schema erzeugt, nie von Hand."""
    import plistlib
    from pathlib import Path
    from tonmischung.config import mdm_doc
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs" / "MDM.md").read_text(encoding="utf-8") == mdm_doc.markdown(), \
        "docs/MDM.md neu erzeugen: python3 -m tonmischung mdm-doku > docs/MDM.md"
    keys = {p for p, _ in mdm_doc.mdm_fields()}
    assert "system.schnittstellen.helmut.adresse" in keys and not any(k.startswith("export.baton") for k in keys)
    data = plistlib.loads(mdm_doc.plist())
    assert data["system.schnittstellen.ptsl.adresse"] == "localhost:31416" and set(data) <= keys
