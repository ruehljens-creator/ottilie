"""Sendefile prüfen und Bericht (M5 Schritt 3) – Bewertung ohne Werkzeuge, dazu ein echter Lauf mit ffmpeg/bmx."""
import json
import os
import shutil
from datetime import datetime

import pytest

from tonmischung.delivery import qc as Q
from tonmischung.delivery import report as R
from tonmischung.delivery.profiles import profile

P16 = profile("ARD_ZDF_HDF01b")


def _probe(tracks=16, rate="48000", codec="pcm_s24le", bits="24", tc="10:00:00:00", field="tt", vdur="60.0",
           adur="60.0", extra_tags=None):
    audio = [{"codec_type": "audio", "codec_name": codec, "sample_rate": rate, "channels": 1,
              "bits_per_sample": bits, "duration": adur, "tags": {}} for _ in range(tracks)]
    return {"streams": [{"codec_type": "video", "codec_name": "mpeg2video", "width": 1920, "height": 1080,
                         "r_frame_rate": "25/1", "field_order": field, "duration": vdur,
                         "tags": {"timecode": tc} if tc else {}}] + audio,
            "format": {"format_name": "mxf", "duration": vdur, "tags": dict(extra_tags or {})}}


def _by(findings):
    return {f.check: f for f in findings}


def test_alles_wie_im_profil():
    f = Q.evaluate(Q.Expectation(P16, "10:00:00:00"), _probe(), content={"available": True, "black_total": 0.4,
                                                                            "black_count": 1, "freeze_count": 0})
    assert Q.overall(f) == Q.PASS
    b = _by(f)
    assert b["Tonspuren"].status == Q.PASS and b["Start-Timecode"].actual == "10:00:00:00"
    assert b["Schwarzbild"].status == Q.PASS and b["Lautheit"].status == Q.INFO


def test_abweichungen_werden_gemeldet():
    f = _by(Q.evaluate(Q.Expectation(P16, "09:59:59:24"),
                       _probe(tracks=15, rate="44100", bits="16", tc="10:00:00:00", field="tb", adur="59.5",
                              extra_tags={"com.adobe.premiere": "x"}),
                       content={"available": True, "black_total": 3.0, "black_count": 2, "freeze_count": 1}))
    assert f["Tonspuren"].status == Q.FAIL and "verschiebt" in f["Tonspuren"].message
    assert f["Abtastrate"].status == Q.FAIL and f["Bittiefe"].status == Q.FAIL
    assert f["Start-Timecode"].status == Q.FAIL and f["Start-Timecode"].expected == "09:59:59:24"
    assert f["Abtastung"].status == Q.FAIL                                          # „tb“ ist hier nicht „tt“
    assert f["Länge Bild/Ton"].status == Q.FAIL and f["Länge Bild/Ton"].actual == "Δ 500 ms"
    assert f["Fremd-Metadaten"].status == Q.WARN and "com.adobe.premiere" in f["Fremd-Metadaten"].actual
    assert f["Schwarzbild"].status == Q.WARN and f["Standbild"].status == Q.WARN
    assert Q.overall(list(f.values())) == Q.FAIL


def test_ohne_timecode_und_ffmpeg_nachlauf():
    mi = {"media": {"track": [{"@type": "General", "Format_Profile": "OP-1a", "Encoded_Application_Name": "Lavf (FFmpeg)"}]}}
    f = _by(Q.evaluate(Q.Expectation(P16, "10:00:00:00", check_lengths=False), _probe(tc=None), mi, content={"available": False}))
    assert f["Start-Timecode"].status == Q.FAIL and f["MXF-Nachlauf (bmx)"].status == Q.WARN
    assert "Länge Bild/Ton" not in f and f["Inhaltsprüfung"].status == Q.INFO


def test_bei_fehler_nach_einstellung():
    get = lambda mode: (lambda p: mode if p == "export.pruefung.bei_fehler" else None)
    assert Q.blocks_transfer(Q.PASS, get("stoppen")) == "" and Q.blocks_transfer(Q.WARN, get("stoppen")) == ""
    assert Q.blocks_transfer(Q.FAIL, get("stoppen")) == "gestoppt"
    assert Q.blocks_transfer(Q.FAIL, get("freigabe_anfordern")) == "freigabe"
    assert Q.blocks_transfer(Q.FAIL, get("nur_protokollieren")) == ""


def test_einstellungen_der_pruefung(tmp_path):
    from tonmischung.config.settings import Settings
    s = Settings(local_path=tmp_path / "e.json")
    e = Q.settings_expectation(s.get, P16, "10:00:00:00")
    assert (e.check_lengths, e.black_max_s, e.freeze_max_s) == (True, 1.0, 5.0) and Q.enabled(s.get)


def test_parser():
    text = ("[blackdetect @ 0x1] black_start:0 black_end:0.4 black_duration:0.4\n"
            "[blackdetect @ 0x1] black_start:10 black_end:12 black_duration:2\nfreeze_start: 3\nsilence_start: 1")
    assert Q.parse_blackdetect(text) == (2, 2.4) and Q.parse_count(text, "freeze_start") == 1


def test_pruefsumme_und_bericht(tmp_path):
    f = tmp_path / "Sendefile.mxf"
    f.write_bytes(b"x" * 1000)
    seen = []
    digest = R.sha256(str(f), seen.append, chunk=300)
    import hashlib
    assert digest == hashlib.sha256(b"x" * 1000).hexdigest()
    assert seen[-1] == 1.0
    sha = R.write_sha256(str(f), digest)
    assert open(sha).read() == f"{digest}  Sendefile.mxf\n"
    assert R.write_sha256(str(f), digest).endswith("Sendefile.mxf 2.sha256")              # nie überschreiben
    j = R.Journal("Bahnstreik", started=datetime(2026, 10, 3, 10, 0))
    j.meta.update({"Sendefile": "Sendefile.mxf", "Profil": "ARD_ZDF_HDF01b"})
    j.step("Umpacken", "Startband entfernt", now=lambda: datetime(2026, 10, 3, 10, 1))
    findings = [{"check": "Tonspuren", "status": "PASS", "expected": "16", "actual": "16", "message": ""}]
    txt, js = R.write_report(str(f), j, findings, "PASS", ended=datetime(2026, 10, 3, 10, 2))
    assert txt.endswith("Sendefile.bericht.txt") and "Gesamt: PASS" in open(txt).read()
    data = json.load(open(js))
    assert data["pruefung"]["gesamt"] == "PASS" and data["ablauf"][0]["schritt"] == "Umpacken"
    assert R.write_report(str(f), j, findings, "PASS")[0].endswith("Sendefile.bericht 2.txt")
    assert not any(n.endswith(".part") for n in os.listdir(tmp_path))


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg fehlt")
def test_echt_sendefile_bauen_und_pruefen(tmp_path):
    import subprocess

    import numpy as np
    sf = pytest.importorskip("soundfile")
    from tonmischung.delivery import mux as M
    ff, fp, bmx, mi = shutil.which("ffmpeg"), shutil.which("ffprobe"), shutil.which("bmxtranswrap"), shutil.which("mediainfo")
    src = tmp_path / "bild.mxf"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25", "-t", "2", "-c:v",
                    "mpeg2video", "-profile:v", "0", "-level:v", "2", "-b:v", "50M", "-minrate", "50M", "-maxrate",
                    "50M", "-bufsize", "17825792", "-pix_fmt", "yuv422p", "-flags", "+ildct+ilme", "-field_order",
                    "tt", "-g", "12", "-bf", "2", "-aspect", "16:9", "-timecode", "10:00:00:00", "-an", "-f", "mxf",
                    str(src)], check=True, timeout=120)
    t = np.arange(96000) / 48000
    sf.write(str(tmp_path / "ST.wav"), np.column_stack([0.2 * np.sin(2 * np.pi * 440 * t)] * 2), 48000, subtype="PCM_24")
    job = M.MuxJob(str(src), (M.AudioSource(str(tmp_path / "ST.wav")),) + (M.AudioSource(None),) * 7,
                   str(tmp_path / "out" / "S.mxf"), P16, "copy", use_bmx=bool(bmx))
    r = M.run(job, ffmpeg=ff, ffprobe=fp, bmx=bmx or "", low_priority=False)
    res = Q.run_qc(r.path, Q.Expectation(P16, r.leader.first_tc), ffprobe=fp, ffmpeg=ff, mediainfo=mi or "")
    by = _by(res.findings)
    assert by["Tonspuren"].status == Q.PASS and by["Start-Timecode"].status == Q.PASS
    assert by["Bittiefe"].status == Q.PASS and by["Länge Bild/Ton"].status == Q.PASS
    if bmx:
        assert by["MXF-Struktur"].status in (Q.PASS, Q.WARN) and by["Abtastung"].status == Q.PASS
    assert res.overall in (Q.PASS, Q.WARN), [f for f in res.findings if f.status == Q.FAIL]


def test_abgeschnittene_mxf_bricht_die_pruefung_nicht_ab(tmp_path):
    """Review A20: eine abgeschnittene MXF (Kopie abgebrochen) ließ die ganze Prüfung mit `struct.error` scheitern."""
    from tonmischung.delivery import mxf_structure as M
    header = M._PART_PREFIX + bytes([2, 4, 0]) + bytes([0x58]) + bytes(40)      # Partition verspricht 88 Byte
    cut = tmp_path / "S.mxf"
    cut.write_bytes(header)
    assert M.read_structure(str(cut))["abgeschnitten"] == 0
    (f,) = Q.structure_findings(str(cut), profile("ARD_ZDF_HDF01b"))
    assert (f.check, f.status) == ("MXF-Struktur", Q.FAIL) and "unvollständig" in f.message
    short_pack = M._PART_PREFIX + bytes([2, 4, 0]) + bytes([0x08]) + bytes(8)   # vollständig, aber viel zu kurz
    cut.write_bytes(short_pack)
    (f,) = Q.structure_findings(str(cut), profile("ARD_ZDF_HDF01b"))
    assert f.status == Q.WARN and f.actual == "nicht prüfbar"
