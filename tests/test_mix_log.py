"""Verzeichnis der auf diesem Mac gemachten Mischungen (Jens 09.10.2026)."""
import types
from datetime import datetime

from tonmischung.core import mix_log as ML


def test_eintragen_neueste_zuerst_und_leeren(tmp_path):
    log = ML.MixLog(tmp_path / "m.json", now=lambda: datetime(2026, 10, 9, 14, 30))
    log.add(ML.MixRecord(ML.DELIVERED, "Bahnstreik", "p1", "Heimatjournal", "Jens", "Ton 3", "/S/B.ptx", ["ST.wav"], "/H/p1"))
    log.add(ML.MixRecord(ML.HAVARIE, "Wahl", sendung="hs", dateien=["Wahl.mxf"]))
    e = log.entries()
    assert [x["titel"] for x in e] == ["Wahl", "Bahnstreik"] and e[1]["zeit"] == "2026-10-09 14:30"
    assert e[1]["dateien"] == ["ST.wav"] and e[0]["art"] == "Havarie-Sendefile"
    assert log.clear() == 2 and log.entries() == [] and ML.MixLog(tmp_path / "m.json").entries() == []


def test_beschaedigte_datei_wird_beiseitegelegt(tmp_path):
    (tmp_path / "m.json").write_text("{kaputt")
    log = ML.MixLog(tmp_path / "m.json")
    assert log.entries() == [] and any(p.name.startswith("m.defekt-") for p in tmp_path.iterdir())


def test_befehle(tmp_path, monkeypatch):
    from tonmischung.app import mixes_app
    monkeypatch.setattr(ML, "default_path", lambda mock=False: tmp_path / ("mock.json" if mock else "echt.json"))
    ctx = types.SimpleNamespace(extra={}, mock=object())
    mixes_app.record_mix(ctx, ML.MixRecord(ML.DELIVERED, "Bahnstreik", "p1"))
    assert mixes_app.list_mixes(ctx, {})["anzahl"] == 1 and (tmp_path / "mock.json").exists()
    r = mixes_app.clear_mixes(ctx, {})
    assert r["anzahl"] == 0 and "Helmut, Sessions und Dateien bleiben" in r["text"]
