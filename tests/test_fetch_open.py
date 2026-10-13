"""Import in die offene Session (Plan M4-Rest, Schritt 5) – Mock-Helmut, Fake-Pro-Tools."""
import os

import pytest

from fakes import FakeEngine, _Track, command_error
from tonmischung.core.open_import import OpenImportOptions, expected_clips, overlaps, start_frame
from tonmischung.core.track_match import MAP, TrackDecision
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.triage import aafcheck
from tonmischung.workflow import fetch as fetch_mod
from tonmischung.workflow import fetch_open as fo
from tonmischung.workflow.fetch import FetchError

AAF_CLIPS = {"A1": ((900000, 900250),), "A2": ((900100, 900400),)}
AAF = aafcheck.AafInfo(True, "AAF lesbar", 900000, 25, ("A1", "A2"), AAF_CLIPS)
OLD = [(900000, 900500, "alt")]


def test_reine_logik():
    assert start_frame("programmbeginn", {"A1": OLD}, None, 25) is None
    assert start_frame("nach_letztem_clip", {"A1": OLD}, None, 25) == 901500         # 10:01:00:00
    assert start_frame("nach_letztem_clip", {"A1": [(900000, 900750, "x")]}, None, 25) == 901500   # genau 30 s
    assert start_frame("nach_letztem_clip", {"A1": [(900000, 900751, "x")]}, None, 25) == 903000   # < 30 s → +1 min
    assert start_frame("nach_letztem_clip", {"A1": [(900000, 901475, "x")]}, None, 25) == 903000   # 10:00:59 → 10:02
    assert start_frame("nach_letztem_clip", {}, None, 25) is None
    assert start_frame("einfuegemarke", {"A1": OLD}, 901500, 25) == 901500
    exp = expected_clips(AAF_CLIPS, 900000, 900525, {"A2": TrackDecision(MAP, "A5")})
    assert exp == {"A1": [(900525, 900775)], "A5": [(900625, 900925)]}
    assert overlaps(expected_clips(AAF_CLIPS, 900000, None, {}), {"A1": OLD}) == {"A1": 1}
    assert overlaps(exp, {"A1": OLD}) == {}
    assert OpenImportOptions("bleibt", "neue_playlist").check()
    assert OpenImportOptions("kopieren", "neue_playlist").check() == []


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mod, "check_aaf", lambda p: AAF)
    old_dir = tmp_path / "Alt"
    (old_dir / "Audio Files").mkdir(parents=True)
    (old_dir / "Audio Files" / "alt.wav").write_bytes(b"RIFF")
    old_ptx = old_dir / "Alt.ptx"
    old_ptx.write_bytes(b"ALT")
    m = MockHelmut()
    m.add_project("alt", "Alt", "hs", state="in_mischung", mischer="Jens", raum="Ton 3", session_pfad=str(old_ptx))
    m.add_project("neu", "Neu: zweiter Teil", "hs", assets=["/H/neu/Neu.aaf", "/H/neu/Neu.mxf"])
    url = m.url = m.start()
    engine = FakeEngine(session=("Alt", str(old_ptx)), aaf_tracks=("A1", "A2"), aaf_clips=AAF_CLIPS, video_track="Video")
    engine.track_objs = [_Track(n, "AudioTrack", n == "A1") for n in ("A1", "A2", "A5", "A11")]
    engine.track_objs.append(_Track("Video", "VideoTrack", True))
    engine.clips = {"A1": list(OLD)}
    service = MixService(HelmutClient(url, lambda: ("test", "test"), sleep=lambda s: None), "Jens", "Ton 3",
                         now=lambda: "jetzt")

    def run(options, **kw):
        return fo.fetch_into_open("neu", service=service, ptsl=PtslClient(engine_factory=lambda: engine),
                                  options=options, session_parent=str(tmp_path / "Sessions"), **kw)
    yield run, engine, m, tmp_path
    m.stop()


def test_kopieren_einfuegen_nach_letztem_clip(setup):
    run, engine, m, tmp_path = setup
    r = run(OpenImportOptions("kopieren", "einfuegen", "nach_letztem_clip"))
    target = str(tmp_path / "Sessions" / "Neu" / "Neu.ptx")
    assert r.session_path == target and engine.session[1] == target
    assert open(target, "rb").read() == b"ALT" and (tmp_path / "Sessions" / "Neu" / "Audio Files" / "alt.wav").exists()
    assert (tmp_path / "Alt" / "Alt.ptx").read_bytes() == b"ALT"                       # bisherige unverändert
    assert engine.calls.index("save_session") < engine.calls.index("close_session(save_on_close=True)")
    assert sorted(engine.clips["A1"]) == [(900000, 900500, "alt"), (901500, 901750, "neu")]
    assert [c for c in engine.calls if c.startswith("selection ")][-1] == "selection 10:01:00:00.00–10:01:00:00.00"
    assert r.clip_report == {"A1": (1, 1), "A2": (1, 1)} and r.warnings == []
    assert (m.mix_value("neu", "zustand"), m.mix_value("neu", "session_pfad")) == ("importiert", target)
    assert (r.previous, r.previous_action) == ("Alt", "bleibt in Mischung") and m.mix_value("alt", "zustand") == "in_mischung"


def test_speichern_unter_neue_playlist_und_weitergefuehrt(setup):
    run, engine, m, tmp_path = setup
    r = run(OpenImportOptions("speichern_unter", "neue_playlist", "programmbeginn", "weitergefuehrt_markieren"))
    target = str(tmp_path / "Sessions" / "Neu" / "Neu.ptx")
    assert r.session_path == target and os.path.isdir(tmp_path / "Sessions" / "Neu")
    assert "close_session(save_on_close=True)" not in engine.calls
    assert any("nicht mehr auf der Hauptplaylist" in w for w in r.warnings)
    assert m.mix_value("alt", "weitergefuehrt_in") == "Neu: zweiter Teil (neu)"
    assert r.previous_action == "als weitergeführt in „Neu: zweiter Teil“ markiert"


def test_ueberdecken_nur_mit_zustimmung(setup):
    run, engine, m, tmp_path = setup
    with pytest.raises(FetchError, match="überdecken.*A1: 1"):
        run(OpenImportOptions("bleibt", "einfuegen", "programmbeginn"))
    assert m.mix_value("neu", "zustand") == "bereit" and "save_session" not in engine.calls
    gefragt = []
    r = run(OpenImportOptions("bleibt", "einfuegen", "programmbeginn"),
            confirm_overlap=lambda hit: gefragt.append(hit) or True)
    assert gefragt == [{"A1": 1}] and r.session_path.endswith("Alt.ptx")
    assert m.mix_value("neu", "zustand") == "importiert"


def test_bleibt_mit_neuer_playlist_gesperrt(setup):
    run, *_ = setup
    with pytest.raises(FetchError, match="noch nicht geprüft"):
        run(OpenImportOptions("bleibt", "neue_playlist"))


def test_neue_spuren_an_der_einfuegemarke_und_zurueckgeben(setup):
    run, engine, m, tmp_path = setup
    engine._extent = ("10:01:00:00.00", "10:01:00:00.00")
    r = run(OpenImportOptions("bleibt", "neue_spuren", "einfuegemarke", "zurueckgeben"))
    assert engine.clips["A1.1"] == [(901500, 901750, "neu")] and engine.clips["A1"] == list(OLD)
    assert r.clip_report == {"A1": (1, 1), "A2": (1, 1)} and r.warnings == []
    assert m.mix_value("alt", "zustand") == "zurueckgegeben"
    assert m.mix_value("alt", "rueckgabe_grund") == "weitergeführt in „Neu: zweiter Teil“"
    assert m.mix_value("neu", "session_pfad") == str(tmp_path / "Alt" / "Alt.ptx")      # zeigt ins bisherige Projekt


def test_fehlende_spur_wird_erfragt(setup, monkeypatch):
    run, engine, m, tmp_path = setup
    engine.track_objs = [t for t in engine.track_objs if t.name != "A2"]
    with pytest.raises(FetchError, match="In der offenen Session fehlen Spuren für Match Tracks: A2"):
        run(OpenImportOptions("bleibt", "einfuegen", "nach_letztem_clip"))
    r = run(OpenImportOptions("bleibt", "einfuegen", "nach_letztem_clip"),
            decide_missing=lambda fehlen, verfuegbar: {"A2": TrackDecision(MAP, "A5")})
    assert engine.clips["A5"] == [(901600, 901900, "neu")] and r.clip_report["A2"] == (1, 1)
    assert {t.name for t in engine.track_objs} >= {"A5", "A1"} and "A2" not in {t.name for t in engine.track_objs}


def test_ohne_offene_session_und_speichern_scheitert(setup):
    run, engine, m, tmp_path = setup
    sess = engine.session
    engine.session = None
    with pytest.raises(FetchError, match="keine Session offen"):
        run(OpenImportOptions())
    engine.session = sess
    engine.fail["save_session"] = command_error("PT_UnknownError")
    with pytest.raises(FetchError, match="bleibt übernommen"):
        run(OpenImportOptions("kopieren", "einfuegen", "nach_letztem_clip"))
    assert not any("close_session" in c for c in engine.calls) and not (tmp_path / "Sessions").exists()
    assert not any(c == "Import" for c in engine.calls)


def test_app_angebot_und_auftrag(setup):
    import time

    from tonmischung.app import fetch_app
    from tonmischung.app.bridge import Bridge, CommandError
    from tonmischung.app.context import AppContext
    from tonmischung.app.jobs import JobRunner
    from tonmischung.config.settings import Settings
    run, engine, m, tmp_path = setup
    ctx = AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient(m.url, lambda: ("test", "test")),
                     PtslClient(engine_factory=lambda: engine), "Jens", "Ton 3", str(tmp_path / "Sessions"), m, "x")
    info = fetch_app.open_session_info(ctx, {"id": "neu"})
    assert info["offer"] and info["name"] == "Alt" and info["defaults"]["umzug"] == "kopieren"
    assert [c["value"] for c in info["choices"]["clips"]] == ["neue_playlist", "einfuegen", "neue_spuren"]
    assert fetch_app.open_session_info(ctx, {"id": "alt"})["offer"] is False         # eigene Session
    with pytest.raises(CommandError, match="noch nicht geprüft"):
        fetch_app.start_fetch_open(ctx, None, {"id": "neu", "umzug": "bleibt", "clips": "neue_playlist"})
    runner = JobRunner(Bridge(emit=lambda p: None, inline=True))
    fetch_app.start_fetch_open(ctx, runner, {"id": "neu", "umzug": "bleibt", "clips": "einfuegen",
                                            "position": "programmbeginn"})
    t0 = time.monotonic()
    while runner.current()["state"] != "frage" and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    q = runner.current()["question"]
    assert q["kind"] == "ueberdecken" and "A1 (1 Clips)" in q["text"]
    runner.answer(q["id"], False)
    while runner.current()["state"] not in ("fehler", "fertig", "abgebrochen") and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    assert runner.current()["state"] == "fehler" and "nichts verändert" in runner.current()["error"]
    fetch_app.start_fetch_open(ctx, runner, {"id": "neu", "umzug": "kopieren", "clips": "einfuegen",
                                            "position": "nach_letztem_clip", "bisheriger_beitrag": "zurueckgeben"})
    while runner.current()["state"] not in ("fehler", "fertig") and time.monotonic() - t0 < 10:
        time.sleep(0.01)
    done = runner.current()
    assert done["state"] == "fertig", done["error"]
    r = done["result"]
    assert r["open_import"] and (r["clips"], r["clips_ok"]) == (2, 2) and r["previous_action"] == "freigegeben"


def test_fehlende_audiodateien_vorher_gefragt(setup, monkeypatch):
    """Wie beim Holen: verknüpftes AAF ohne erreichbaren Ton – vorher fragen, „Abbrechen“ ändert nichts."""
    from tonmischung.triage import media as MD
    run, engine, m, tmp_path = setup
    rep = MD.MediaReport(0, (MD.MediaRef("Musik.wav", "file:///C%3a/Musik.wav", "audio", ""),))
    monkeypatch.setattr(fetch_mod, "media_check", lambda path, log=None: rep)
    with pytest.raises(FetchError, match="Audiodateien des AAF fehlen"):
        run(OpenImportOptions("kopieren", "einfuegen", "nach_letztem_clip"), confirm_media=lambda r: False)
    assert m.mix_value("neu", "zustand") == "bereit" and "save_session" not in engine.calls
    r = run(OpenImportOptions("kopieren", "einfuegen", "nach_letztem_clip"), confirm_media=lambda r: True)
    assert any("Musik.wav" in w and "offline" in w for w in r.warnings)


def test_premiere_sequenz_und_sessionbeginn(setup, monkeypatch):
    """„Programmbeginn“ mit einer Sequenz ab 00:00:00:00 → auf 10:00:00:00 legen; vor dem Sessionbeginn nie."""
    run, engine, m, tmp_path = setup
    clips = {t: tuple((a - 900000, b - 900000) for a, b in v) for t, v in AAF_CLIPS.items()}
    monkeypatch.setattr(fetch_mod, "check_aaf", lambda p: aafcheck.AafInfo(True, "AAF lesbar", 0, 25, ("A1", "A2"), clips))
    engine.clips = {}
    r = run(OpenImportOptions("bleibt", "einfuegen", "programmbeginn"))
    imp = [req for n, req in engine.requests if n == "Import"][-1]["session_data"]
    assert imp["timecode_mapping_units"] == "MapStartTimeCodeTo" and imp["timecode_mapping_start_time"] == "10:00:00:00"
    assert r.clip_report == {"A1": (1, 1), "A2": (1, 1)}
    assert any("auf 10:00:00:00 gelegt" in w for w in r.warnings)
    monkeypatch.setattr(fetch_mod, "check_aaf", lambda p: AAF)
    engine.start_time = "10:00:10:00"
    with pytest.raises(FetchError, match="Session erst bei 10:00:10:00 .*nichts verändert"):
        run(OpenImportOptions("bleibt", "neue_spuren", "programmbeginn"))
