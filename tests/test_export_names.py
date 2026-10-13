"""Exportierte WAVs nach dem Exportnamen benennen (Spurprofil: ST, IT, DoE …) – nie überschreiben."""
import os

import pytest

from tonmischung.sessions.pt_export import ExportError, apply_export_names


def _wavs(tmp_path, *names):
    out = {}
    for n in names:
        p = tmp_path / f"{n}.wav"
        p.write_bytes(n.encode())
        out[n] = str(p)
    return out


def test_umbenennen(tmp_path):
    wavs = _wavs(tmp_path, "Sendeton Mix", "A  IT", "OGA")
    out = apply_export_names(wavs, {"Sendeton Mix": "ST", "A  IT": "IT", "OGA": "OGA"})
    assert out == {"Sendeton Mix": str(tmp_path / "ST.wav"), "A  IT": str(tmp_path / "IT.wav"),
                   "OGA": str(tmp_path / "OGA.wav")}
    assert sorted(os.listdir(tmp_path)) == ["IT.wav", "OGA.wav", "ST.wav"]
    assert (tmp_path / "ST.wav").read_bytes() == b"Sendeton Mix"


def test_doppelt_oder_vorhanden_wird_nicht_ueberschrieben(tmp_path):
    wavs = _wavs(tmp_path, "Mix 1", "Mix 2")
    with pytest.raises(ExportError, match="doppelt"):
        apply_export_names(wavs, {"Mix 1": "ST", "Mix 2": "ST"})
    (tmp_path / "ST.wav").write_bytes(b"alt")
    with pytest.raises(ExportError, match="nichts überschrieben"):
        apply_export_names(wavs, {"Mix 1": "ST"})
    assert (tmp_path / "ST.wav").read_bytes() == b"alt" and (tmp_path / "Mix 1.wav").exists()
    with pytest.raises(ExportError, match="Dateiname"):
        apply_export_names(wavs, {"Mix 2": "a/b"})


def test_aus_der_spurtabelle(tmp_path):
    from tonmischung.factory import export_names as _export_names
    from tonmischung.config.settings import Settings
    s = Settings(local_path=tmp_path / "e.json")
    assert _export_names(s) == {}
    s.set_local("export.spuren_lautheit.spurtabelle", [
        {"pt_spur": "Sendeton Mix", "inhalt": "Sendeton", "export": True, "r128": True, "exportname": "ST"},
        {"pt_spur": "AD", "inhalt": "AD", "export": False, "r128": False, "exportname": "AD"}])
    assert _export_names(s) == {"Sendeton Mix": "ST"}
