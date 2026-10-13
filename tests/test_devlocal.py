"""Entwicklerdaten aus testfiles/entwicklung.json – stehen nicht im Code (13.10.2026)."""
import json
import os

from tonmischung import devlocal


def test_ohne_datei_neutrale_vorgaben(tmp_path):
    assert devlocal.load(tmp_path / "fehlt.json") == {}
    (tmp_path / "kaputt.json").write_text("{")
    assert devlocal.load(tmp_path / "kaputt.json") == {}
    assert devlocal.test_folder({}) == os.path.expanduser(devlocal.DEFAULT_TEST_FOLDER)
    assert devlocal.dev_projects({}) == [] and devlocal.dev_templates({}) == () and devlocal.real_file("x", {}) is None


def test_werte_und_relative_pfade(tmp_path):
    aaf = tmp_path / "a.aaf"
    aaf.write_bytes(b"x")
    data = {"testordner": str(tmp_path / "Test"), "templates": ["test/neu", "test/alt"],
            "beitraege": [{"id": "b1", "titel": "Wahl", "dateien": ["testfiles/b.aaf", str(aaf)]}],
            "testdateien": {"da": str(aaf), "fehlt": str(tmp_path / "nein.aaf")}}
    (tmp_path / "e.json").write_text(json.dumps(data))
    assert devlocal.load(tmp_path / "e.json") == data
    assert devlocal.test_folder(data) == str(tmp_path / "Test")
    assert devlocal.dev_templates(data) == ("test/neu", "test/alt")
    (pid, title, show, files), = devlocal.dev_projects(data)
    assert (pid, title, show) == ("b1", "Wahl", "")
    assert files == [str(devlocal.PROJECT_DIR / "testfiles/b.aaf"), str(aaf)]           # relativ ab Projektordner
    assert devlocal.real_file("da", data) == str(aaf) and devlocal.real_file("fehlt", data) is None
