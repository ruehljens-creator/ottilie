"""Auslieferung: Werkzeugsuche und Tonformat lesen (ohne Pro Tools)."""
import stat

import numpy as np
import pytest

from tonmischung.delivery import audio_format as AF
from tonmischung.delivery import tools as T

sf = pytest.importorskip("soundfile")


def _fake_tool(folder, name, output="ffmpeg version 9.9.9-test"):
    p = folder / name
    p.write_text(f"#!/bin/sh\necho '{output}'\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


@pytest.fixture
def nowhere(monkeypatch):
    monkeypatch.setattr(T, "bundle_dirs", lambda: [])
    monkeypatch.setattr(T.shutil, "which", lambda n: None)
    monkeypatch.setattr(T, "HOMEBREW_DIRS", [])


def test_eigener_pfad_aus_den_einstellungen(tmp_path, nowhere):
    own = _fake_tool(tmp_path, "ffmpeg")
    get = {"system.schnittstellen.werkzeuge.ffmpeg.modus": "eigener_pfad",
           "system.schnittstellen.werkzeuge.ffmpeg.pfad": own,
           "system.schnittstellen.werkzeuge.bmx.modus": "mitgeliefert",
           "system.schnittstellen.werkzeuge.bmx.pfad": "/egal"}
    custom = T.configured(get)
    assert custom == {"ffmpeg": own}
    assert T.locate("ffmpeg", custom) == (own, "Einstellung")


def test_ungueltiger_eigener_pfad_wird_gemeldet(tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    bundled = _fake_tool(b, "ffmpeg")
    monkeypatch.setattr(T, "bundle_dirs", lambda: [str(b)])
    custom = {"ffmpeg": "/gibt/es/nicht"}
    assert T.locate("ffmpeg", custom) == (bundled, "mitgeliefert")
    st = {s.name: s for s in T.check(custom)}
    assert st["ffmpeg"].ok and "nicht ausführbar" in st["ffmpeg"].line()


def test_mitgeliefert_vor_path(tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    bundled = _fake_tool(b, "mediainfo")
    monkeypatch.setattr(T, "bundle_dirs", lambda: [str(b)])
    monkeypatch.setattr(T.shutil, "which", lambda n: "/irgendwo/" + n)
    assert T.find("mediainfo") == bundled


def test_nichts_gefunden(nowhere):
    assert T.locate("bmxtranswrap") == ("", "")


def test_version_und_bericht(tmp_path, monkeypatch, nowhere):
    assert T.version("ffmpeg", _fake_tool(tmp_path, "ffmpeg", "ffmpeg version 9.0.1 Copyright")) == "9.0.1"
    assert T.version("bmxtranswrap", _fake_tool(tmp_path, "bmxtranswrap", "bmxtranswrap, bmx v1.6.0, Sep")) == "1.6.0"
    assert T.version("ffmpeg", "/gibt/es/nicht") == ""
    b = tmp_path / "bin"
    b.mkdir()
    for n in ("ffmpeg", "ffprobe"):
        _fake_tool(b, n, f"{n} version 1.2.3")
    monkeypatch.setattr(T, "bundle_dirs", lambda: [str(b)])
    text = T.report()
    assert "ffmpeg: 1.2.3 (mitgeliefert" in text and "mediainfo: fehlt" in text and "mxf2raw: fehlt (optional)" in text


def _wav(path, rate=44100, subtype="PCM_16", seconds=0.5, channels=2):
    t = np.arange(int(rate * seconds)) / rate
    x = 0.3 * np.sin(2 * np.pi * 440 * t)
    sf.write(str(path), np.column_stack([x] * channels), rate, subtype=subtype)
    return str(path)


def test_tonformat_lesen(tmp_path):
    info = AF.analyse(_wav(tmp_path / "ST.wav", 48000, "PCM_24"))
    assert (info.bits, info.rate, info.channels) == (24, 48000, 2)
    assert AF.analyse(str(tmp_path / "fehlt.wav")) is None


def test_bits_aus_ffprobe():
    assert AF.bits_from_probe({"bits_per_sample": "24"}) == 24
    assert AF.bits_from_probe({"bits_per_sample": 0, "bits_per_raw_sample": "16"}) == 16
    assert AF.bits_from_probe({"codec_name": "pcm_s24le"}) == 24
    assert AF.bits_from_probe({"codec_name": "aac"}) is None
