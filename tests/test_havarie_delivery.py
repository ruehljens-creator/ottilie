"""Havarie: Sendefile erstellen und ausliefern – Pro-Tools-Teil als Attrappe, Dateiarbeit echt (ffmpeg/bmx)."""
import os
import shutil
import subprocess
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from tonmischung.config.settings import Settings
from tonmischung.delivery.presets import DEFAULT_PRESETS
from tonmischung.delivery import qc as Q
from tonmischung.delivery import transfer as T
from tonmischung.workflow import havarie_delivery as H

FF, FP, BMX, MI = (shutil.which(x) for x in ("ffmpeg", "ffprobe", "bmxtranswrap", "mediainfo"))
XDCAM16 = next(p for p in DEFAULT_PRESETS if p["id"] == "xdcam16")
NOW = lambda: datetime(2026, 10, 3, 14, 0, 0)


def test_kanaele_aus_der_spurtabelle():
    table = [{"pt_spur": "ST", "export": True}, {"pt_spur": "", "export": False}, {"pt_spur": "AD", "export": False},
             {"pt_spur": "OGA", "export": True}]
    ch = H.channel_tracks(table, 16)
    assert ch[:8] == ["ST", "ST", "Stille", "Stille", "Stille", "Stille", "OGA", "OGA"] and len(ch) == 16
    with pytest.raises(H.HavarieDeliveryError, match="mehr als 8 Kanäle"):
        H.channel_tracks([{"pt_spur": f"S{i}", "export": True} for i in range(5)], 8)


def test_dateiname(tmp_path):
    assert H.safe_name("Wahl in Hessen: Überblick / Teil 2") == "Wahl_in_Hessen_Ueberblick_Teil_2"
    v = {"sendung": "Heimatjournal", "datum": "20261003", "titel": "Bahn-Streik!"}
    assert H.sendefile_name("{sendung}_{datum}_{titel}_v{version}", {**v, "version": 1}) == "Heimatjournal_20261003_Bahn-Streik_v1"
    assert H.sendefile_name("{titel}_{unbekannt}", v) == "Bahn-Streik"
    (tmp_path / "Heimatjournal_20261003_Bahn-Streik_v1.mxf").write_bytes(b"x")
    assert H.free_version(str(tmp_path), "{sendung}_{datum}_{titel}_v{version}", v) == ("Heimatjournal_20261003_Bahn-Streik_v2", 2)
    (tmp_path / "Bahn-Streik.mxf").write_bytes(b"x")
    assert H.free_version(str(tmp_path), "{titel}", v)[0] == "Bahn-Streik_2"
    ziel = tmp_path / "Stick"                                       # zweiter Lauf aus anderer Session (live 04.10.2026)
    ziel.mkdir()
    (ziel / "Heimatjournal_20261003_Bahn-Streik_v2.mxf").write_bytes(b"x")
    assert H.free_version(str(tmp_path), "{sendung}_{datum}_{titel}_v{version}", v, also=[str(ziel), ""])[1] == 3


def _paket(tmp_path):
    pkg = tmp_path / "Paket"
    pkg.mkdir()
    (pkg / "Beitrag_forPT.aaf").write_bytes(b"aaf")
    # Bild mit 1 s Startband: Timecode 09:59:59:00, 3 s lang, XDCAM HD422 1080i25
    subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25", "-t", "3", "-c:v",
                    "mpeg2video", "-profile:v", "0", "-level:v", "2", "-b:v", "50M", "-minrate", "50M", "-maxrate",
                    "50M", "-bufsize", "17825792", "-pix_fmt", "yuv422p", "-flags", "+ildct+ilme", "-field_order",
                    "tt", "-g", "25", "-bf", "2", "-aspect", "16:9", "-timecode", "09:59:59:00", "-an", "-f", "mxf",
                    str(pkg / "Beitrag.mxf")], check=True, timeout=120)
    return pkg


def _fake_export(tmp_path):
    sf = pytest.importorskip("soundfile")

    def run(ptsl, export, export_dir, **kw):
        os.makedirs(export_dir, exist_ok=True)
        t = np.arange(96000) / 48000
        wavs = {}
        for i, name in enumerate(export):
            path = os.path.join(export_dir, f"{kw['export_names'].get(name, name)}.wav")
            sf.write(path, np.column_stack([0.2 * np.sin(2 * np.pi * (300 + 100 * i) * t)] * 2), 48000, subtype="PCM_24")
            wavs[name] = path
        return SimpleNamespace(wav_files=wavs, warnings=[])
    return run


def _settings(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local("export.spuren_lautheit.spurtabelle", [
        {"pt_spur": "Sendeton Mix", "inhalt": "Sendeton", "export": True, "r128": True, "exportname": "ST"},
        {"pt_spur": "IT", "inhalt": "IT", "export": True, "r128": False}])
    return s


class _Pt:
    def __init__(self, path):
        self.path = path

    def open_session(self):
        return SimpleNamespace(name="Havarie", path=self.path) if self.path else None


@pytest.mark.skipif(not (FF and FP), reason="ffmpeg fehlt")
def test_havarie_ende_zu_ende(tmp_path):
    pkg = _paket(tmp_path)
    s = _settings(tmp_path)
    session = str(tmp_path / "Sessions" / "Havarie" / "Havarie.ptx")
    target = tmp_path / "Playout"
    target.mkdir()
    req = H.Request(str(pkg / "Beitrag_forPT.aaf"), XDCAM16, [T.Target("p", "Playout", "ordner", path=str(target))],
                    "Bahnstreik", show="Heimatjournal")
    asked = []
    r = H.deliver_havarie(req, ptsl=_Pt(session), get=s.get, tools=H.Tools(FF, FP, BMX or "", MI or ""),
                          session_path=session, ask=asked.append, now=NOW, run_export=_fake_export(tmp_path))
    assert os.path.basename(r.sendefile) == "Heimatjournal_20261003_Bahnstreik_v1.mxf"
    assert os.path.dirname(r.sendefile) == str(tmp_path / "Sessions" / "Havarie" / "export")       # wie PunchBuddy
    assert r.qc_overall in (Q.PASS, Q.WARN) and not r.blocked and asked == []
    assert sorted(os.listdir(target)) == ["Heimatjournal_20261003_Bahnstreik_v1.mxf", "Heimatjournal_20261003_Bahnstreik_v1.mxf.sha256"]
    assert all(t.ok for t in r.transfers)
    txt = open(r.report[0], encoding="utf-8").read()
    assert "Startband" in txt and "Transfer Playout" in txt and "Gesamt:" in txt
    assert os.path.basename(r.wavs["Sendeton Mix"]) == "ST.wav"                            # Exportname


@pytest.mark.skipif(not (FF and FP), reason="ffmpeg fehlt")
def test_pruefung_mit_fehler_stoppt_den_transfer(tmp_path, monkeypatch):
    pkg = _paket(tmp_path)
    s = _settings(tmp_path)
    session = str(tmp_path / "S" / "H.ptx")
    target = tmp_path / "Playout"
    target.mkdir()
    monkeypatch.setattr(Q, "run_qc", lambda *a, **k: Q.QcResult([Q.Finding("Tonspuren", Q.FAIL, "16", "15")]))
    req = H.Request(str(pkg / "Beitrag_forPT.aaf"), XDCAM16, [T.Target("p", "Playout", "ordner", path=str(target))], "B")
    r = H.deliver_havarie(req, ptsl=_Pt(session), get=s.get, tools=H.Tools(FF, FP, BMX or ""), session_path=session,
                          now=NOW, run_export=_fake_export(tmp_path))
    assert r.blocked == "gestoppt" and r.transfers == [] and os.listdir(target) == []
    assert any("nicht übertragen" in w for w in r.warnings)
    s.set_local("export.pruefung.bei_fehler", "freigabe_anfordern")
    asked = []
    r = H.deliver_havarie(req, ptsl=_Pt(session), get=s.get, tools=H.Tools(FF, FP, BMX or ""), session_path=session,
                          ask=lambda q: asked.append(q) or True, now=lambda: datetime(2026, 10, 3, 15, 0),
                          run_export=_fake_export(tmp_path))
    assert asked and asked[0]["kind"] == "pruefung" and "Tonspuren" in asked[0]["text"]
    assert not r.blocked and r.transfers[0].ok


def test_abbruch_schreibt_bericht(tmp_path):
    s = _settings(tmp_path)
    session = str(tmp_path / "S" / "H.ptx")
    req = H.Request(str(tmp_path / "x.aaf"), XDCAM16, [], "B")
    with pytest.raises(H.HavarieDeliveryError, match="nicht offen"):
        H.deliver_havarie(req, ptsl=_Pt(""), get=s.get, tools=H.Tools("ffmpeg", "ffprobe"), session_path=session, now=NOW)
    folder = tmp_path / "S" / "export"
    assert "Abbruch" in (folder / "Havarie-Abbruch-20261003-140000.bericht.txt").read_text(encoding="utf-8")
