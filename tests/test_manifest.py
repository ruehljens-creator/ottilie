"""Metadaten-Datei zum Zurückliefern (delivery/manifest.py) – reine Logik."""
from datetime import datetime, timedelta, timezone

import pytest

from tonmischung.core.mixstate import MixState
from tonmischung.delivery import manifest as M
from tonmischung.helmut.mapping import Asset, Contribution

TABLE = [{"pt_spur": "ST", "inhalt": "Sendeton", "export": True, "r128": True},
         {"pt_spur": "IT", "inhalt": "IT", "export": True},
         {"pt_spur": "_AD", "inhalt": "Audiodeskription", "export": True},
         {"pt_spur": "DoE", "inhalt": "Dolby E", "export": True},
         {"pt_spur": "", "inhalt": "OGA"},
         {"pt_spur": "Mus", "inhalt": "Musik", "export": True}]
WAVS = {"ST": "/U/p1/1/ST.wav", "IT": "/U/p1/1/IT.wav", "DoE": "/U/p1/1/DoE.wav", "Mus": "/U/p1/1/Mus.wav",
        "Spr": "/U/p1/1/Spr.wav"}
CREATED = datetime(2026, 11, 10, 14, 32, 5, 500000, tzinfo=timezone(timedelta(hours=1)))


def _meta(name, value, set_name="Beitrag"):
    return {"name": name, "value": value, "parent": [{"name": set_name}]}


def _contribution(*metadata):
    return Contribution("p1", "Bahnstreik", "Heimatjournal", False,
                        MixState(session_path="/S/Bahnstreik.ptx"), raw={"id": "p1", "metadata": list(metadata)})


def _build(c=None, wavs=WAVS, video_id="VID-0142", table=TABLE,
           video=Asset("a1", "Bahnstreik.mxf", "/H/p1/Bahnstreik.mxf")):
    return M.build(c or _contribution(), table=table, wavs=wavs, video=video, video_id=video_id, created=CREATED,
                   program_start="10:00:00:00", program_end="10:12:34:05", mixer="Jens", room="Ton 3",
                   target_lufs=-23.0, max_truepeak=-3.0, loudness_tracks=["ST"])


def test_audio_in_kanalreihenfolge_mit_pfaden():
    """Reihenfolge wie im MXF (Zeile der Spurtabelle = Kanalpaar); Spuren ohne Zeile folgen ohne Kanal."""
    d = _build()
    assert [(a["position"], a["spur"], a["kanaele"], a["datei"]) for a in d["audio"]] == [
        (1, "ST", [1, 2], "ST.wav"), (2, "IT", [3, 4], "IT.wav"), (3, "DoE", [7, 8], "DoE.wav"),
        (4, "Mus", [11, 12], "Mus.wav"), (5, "Spr", [], "Spr.wav")]
    assert d["audio"][0]["pfad"] == "/U/p1/1/ST.wav"
    assert d["video"] == {"name": "Bahnstreik.mxf", "pfad": "/H/p1/Bahnstreik.mxf"}


def test_kanalbelegung_16_kanaele_stille_rueckt_nicht_auf():
    d = _build()
    assert [(p["kanaele"], p["spur"]) for p in d["kanalbelegung"]] == [
        ([1, 2], "ST"), ([3, 4], "IT"), ([5, 6], "Stille"), ([7, 8], "DoE"), ([9, 10], "Stille"),
        ([11, 12], "Mus"), ([13, 14], "Stille"), ([15, 16], "Stille")]       # bis 16 mit Stille aufgefüllt


def test_zeilen_jenseits_von_kanal_16_ohne_kanal():
    table = [{"pt_spur": f"T{i}", "inhalt": "x", "export": True} for i in range(9)]
    wavs = {f"T{i}": f"/U/T{i}.wav" for i in range(9)}
    d = _build(table=table, wavs=wavs)
    assert len(d["kanalbelegung"]) == 8 and d["audio"][-1] == {
        "position": 9, "spur": "T8", "inhalt": "", "kanaele": [], "datei": "T8.wav", "pfad": "/U/T8.wav"}


def test_eine_spur_auf_zwei_kanalpaaren():
    table = [{"pt_spur": "ST", "inhalt": "Sendeton", "export": True},
             {"pt_spur": "ST", "inhalt": "Sendeton", "export": True}]
    d = _build(table=table, wavs={"ST": "/U/ST.wav"})
    assert [(a["spur"], a["kanaele"]) for a in d["audio"]] == [("ST", [1, 2, 3, 4])]


def test_bmx_zeitstempel():
    assert M.bmx_timestamp(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)) == "2026-01-02T03:04:05:0"
    assert M.bmx_timestamp(datetime(2026, 1, 2, 3, 4, 5, 999999, tzinfo=timezone.utc)) == "2026-01-02T03:04:05:249"


def test_ohne_videoid_und_ohne_video():
    d = _build(video_id="", video=None)
    assert d["videoid"] == "" and d["video"] is None
    assert d["bmx"]["clip"] == "Bahnstreik"


def test_kopf():
    d = _build()
    assert (d["format"], d["projekt"], d["videoid"]) == (
        "ottilie-auslieferung/1", {"id": "p1", "titel": "Bahnstreik", "sendung": "Heimatjournal"}, "VID-0142")
    assert (d["mischung"]["programmbeginn"], d["mischung"]["programmende"]) == ("10:00:00:00", "10:12:34:05")
    assert d["mischung"]["lautheit"] == {"ziel_lufs": -23.0, "true_peak_max_dbtp": -3.0, "spuren": ["ST"]}
    assert M.file_name("p1") == "p1_metadaten.json"


@pytest.mark.parametrize("raw, clean", [("  VID-0142\n", "VID-0142"), (None, ""), ("", ""), ("A" * 64, "A" * 64)])
def test_videoid_bereinigt(raw, clean):
    assert M.clean_video_id(raw) == clean


@pytest.mark.parametrize("raw, why", [("VID\n0142", "Zeilenumbruch"), ("VID\t1", "Zeilenumbruch"),
                                      ("VID​1", "Steuerzeichen"), ("A" * 65, "länger als 64")])
def test_videoid_abgelehnt(raw, why):
    with pytest.raises(M.VideoIdError, match=why):
        M.clean_video_id(raw)


def test_bmx_nur_ard_zdf_konform():
    """Jens 11.11.2026: HDF-Profil ohne Descriptive Metadata – nur Clipname (mit VideoID) und Erstellungsdatum."""
    b = _build()["bmx"]
    assert b == {"profil": "ARD-ZDF HDF (ohne Descriptive Metadata)", "clip": "VID-0142 Bahnstreik",
                 "create_date": "2026-11-10T13:32:05:125"}


def test_clipname():
    assert M.clip_name(" VID-1 ", "Bahn  streik\n") == "VID-1 Bahn streik" and M.clip_name("", "Titel") == "Titel"
