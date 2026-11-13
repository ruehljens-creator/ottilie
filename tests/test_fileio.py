"""Eigene JSON-Dateien: beschädigt ≠ leer, nie überschreiben (Review 09.10.2026, A4/A5/B11)."""
import json
import os

import pytest

from tonmischung.config.settings import Settings, SettingLockedError
from tonmischung.core.fileio import CorruptFileError, read_json, set_aside, write_json


def test_lesen_unterscheidet_fehlt_und_kaputt(tmp_path):
    assert read_json(tmp_path / "fehlt.json") is None
    (tmp_path / "k.json").write_text("{kaputt")
    with pytest.raises(CorruptFileError):
        read_json(tmp_path / "k.json")
    (tmp_path / "l.json").write_text("[1, 2]")
    assert read_json(tmp_path / "l.json") == [1, 2]
    with pytest.raises(CorruptFileError, match="unerwartete Form"):
        read_json(tmp_path / "l.json", expect=dict)


def test_schreiben_atomar_ohne_reste(tmp_path):
    write_json(tmp_path / "a" / "x.json", {"ä": 1})
    assert json.loads((tmp_path / "a" / "x.json").read_text(encoding="utf-8")) == {"ä": 1}
    with pytest.raises(TypeError):
        write_json(tmp_path / "a" / "x.json", {"nicht": object()})
    assert sorted(os.listdir(tmp_path / "a")) == ["x.json"]                        # keine Hilfsdatei liegen geblieben
    assert json.loads((tmp_path / "a" / "x.json").read_text(encoding="utf-8")) == {"ä": 1}


def test_beiseitelegen_ueberschreibt_nichts(tmp_path):
    (tmp_path / "e.json").write_text("1")
    a = set_aside(tmp_path / "e.json")
    (tmp_path / "e.json").write_text("2")
    b = set_aside(tmp_path / "e.json")
    assert a != b and a.read_text() == "1" and b.read_text() == "2" and not (tmp_path / "e.json").exists()


def test_beschaedigte_einstellungen_gehen_nicht_verloren(tmp_path):
    """Vorher: als leer gelesen, beim nächsten Speichern überschrieben – alle Mac-Werte weg."""
    path = tmp_path / "einstellungen.json"
    path.write_text('{"system.schnittstellen.ptsl.zeitlimit_s": 45,')
    s = Settings(local_path=path)
    assert "beiseitegelegt" in s.local_problem
    s.set_local("persoenlich.farbschema", "kontrast")
    kept = list(tmp_path.glob("einstellungen.defekt-*.json"))
    assert len(kept) == 1 and "zeitlimit_s" in kept[0].read_text()
    path.write_text("[]")                                            # falsche Form zählt als beschädigt
    assert "beiseitegelegt" in Settings(local_path=path).local_problem


def test_unlesbare_einstellungen_werden_nicht_ueberschrieben(tmp_path):
    path = tmp_path / "einstellungen.json"
    path.write_text('{"persoenlich.farbschema": "bisher"}')
    os.chmod(path, 0)
    try:
        s = Settings(local_path=path)
        if not s.local_problem:                                      # als root lesbar – dann nichts zu prüfen
            pytest.skip("Datei trotz chmod 0 lesbar")
        with pytest.raises(SettingLockedError, match="nicht lesbar"):
            s.set_local("persoenlich.farbschema", "kontrast")
    finally:
        os.chmod(path, 0o600)
    assert json.loads(path.read_text()) == {"persoenlich.farbschema": "bisher"}


def test_neue_datei_nie_ueber_eine_vorhandene(tmp_path):
    from tonmischung.core.fileio import write_new, write_text
    target = tmp_path / "Ottilie Einstellungen.json"
    write_new(target, lambda t: write_text(t, "neu"))
    assert target.read_text() == "neu"
    with pytest.raises(FileExistsError):
        write_new(target, lambda t: write_text(t, "anders"))
    assert target.read_text() == "neu" and sorted(p.name for p in tmp_path.iterdir()) == [target.name]


def test_ohne_hardlinks_wird_umbenannt(tmp_path, monkeypatch):
    """exFAT/FAT32-Sticks: os.link → ENOTSUP. Vorher scheiterte z. B. der Einstellungsexport auf einen Stick."""
    import errno
    import os
    from tonmischung.core import fileio
    from tonmischung.core.fileio import write_new, write_text

    def no_links(src, dst):
        raise OSError(errno.ENOTSUP, "Operation not supported")
    monkeypatch.setattr(fileio.os, "link", no_links)
    target = tmp_path / "Profil.json"
    write_new(target, lambda t: write_text(t, "eins"))
    assert target.read_text() == "eins"
    with pytest.raises(FileExistsError):
        write_new(target, lambda t: write_text(t, "zwei"))
    assert target.read_text() == "eins" and os.listdir(tmp_path) == ["Profil.json"]


# ── Begleitdateien von macOS (SMB-Test 11.11.2026) ─────────────────────────────
AD = b"\x00\x05\x16\x07" + b"\x00" * 60


def test_begleitdatei_nur_echte_appledouble(tmp_path):
    from tonmischung.core.fileio import drop_appledouble, os_metadata, sweep_appledouble
    assert os_metadata("._film.aaf") and os_metadata(".DS_Store") and not os_metadata(".ottilie-sperre.json")
    (tmp_path / "ST.wav").write_bytes(b"RIFF")
    (tmp_path / "._ST.wav").write_bytes(AD)
    assert drop_appledouble(tmp_path / "ST.wav") and not (tmp_path / "._ST.wav").exists()
    assert (tmp_path / "ST.wav").read_bytes() == b"RIFF"
    (tmp_path / "._fremd.wav").write_bytes(b"RIFF....")                # heißt nur so – bleibt
    (tmp_path / "._gross").write_bytes(AD + b"\x00" * (1 << 20))       # zu groß – bleibt
    (tmp_path / "._verwaist").write_bytes(AD)
    (tmp_path / "ziel").write_bytes(AD)
    os.symlink(tmp_path / "ziel", tmp_path / "._verweis")              # Verweis – weder gelöscht noch gefolgt
    assert sweep_appledouble(tmp_path) == 1
    assert sorted(os.listdir(tmp_path)) == ["._fremd.wav", "._gross", "._verweis", "ST.wav", "ziel"]
    assert sweep_appledouble(tmp_path / "fehlt") == 0


def test_write_json_entfernt_eigene_begleitdatei(tmp_path, monkeypatch):
    real = os.replace

    def replace(a, b):                                  # wie smbfs: Begleitdatei wandert mit
        real(a, b)
        with open(os.path.join(os.path.dirname(b), "._" + os.path.basename(b)), "wb") as f:
            f.write(AD)
    monkeypatch.setattr(os, "replace", replace)
    write_json(tmp_path / "a.json", {"x": 1})
    assert os.listdir(tmp_path) == ["a.json"]


def test_waise_entscheidet_das_dateisystem(tmp_path):
    """Sicherheit 11.11.2026: „._film.wav“ neben „Film.wav“ ist auf APFS/SMB (ohne Groß/Klein) keine Waise."""
    from tonmischung.core.fileio import sweep_appledouble
    (tmp_path / "Film.wav").write_bytes(b"RIFF")
    if not (tmp_path / "film.wav").exists():
        pytest.skip("Dateisystem unterscheidet Groß/Klein")
    (tmp_path / "._film.wav").write_bytes(AD)
    (tmp_path / "._weg.wav").write_bytes(AD)
    assert sweep_appledouble(tmp_path, set()) == 1
    assert sorted(os.listdir(tmp_path)) == ["._film.wav", "Film.wav"]
