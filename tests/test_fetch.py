import os

import aaf2
import pytest

from fakes import FakeEngine, command_error
from tonmischung.core.naming import session_name_for, unique_session_dir
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.core.track_match import FROM_A11, MAP, SKIP, TrackDecision
from tonmischung.triage import aafcheck
from tonmischung.workflow import fetch as fetch_mod
from tonmischung.workflow.fetch import FetchError, NeedsConfirmation, fetch

CLIPS = {"A1": ((900000, 900250), (900400, 900600)), "A2": ((900000, 900600),)}
AAF_OK = aafcheck.AafInfo(True, "AAF lesbar", 900000, 25, ("A1", "A2"), CLIPS)


def _engine(**kw):
    """Template mit A1/A2 und Videospur; die AAF bringt CLIPS auf A1/A2."""
    kw.setdefault("session", None)
    return FakeEngine(aaf_tracks=("A1", "A2"), aaf_clips=CLIPS, video_track="Video", **kw)


@pytest.fixture
def mock():
    m = MockHelmut()
    m.add_project("p1", "Bahnstreik: Pendler warten", "Heimatjournal",
                  assets=["/H/p1/Bahnstreik.aaf", "/H/p1/Bahnstreik_ref.mxf"])
    m.add_project("p3", "Kita-Streik", "Heimatjournal", state="in_mischung", mischer="A. Wolf", raum="Ton 2")
    m.url = m.start()
    yield m
    m.stop()


@pytest.fixture(autouse=True)
def aaf_ok(monkeypatch):
    monkeypatch.setattr(fetch_mod, "check_aaf", lambda path: AAF_OK)


def _run(mock, engine, tmp_path, **kwargs):
    service = MixService(HelmutClient(mock.url, lambda: ("test", "test"), sleep=lambda s: None),
                         "Jens", "Ton 3", now=lambda: "jetzt")
    return fetch(kwargs.pop("pid", "p1"), service=service, ptsl=PtslClient(engine_factory=lambda: engine),
                 template_group="test", template_name="SM Session", session_parent=str(tmp_path),
                 **kwargs)


def test_holen_ende_zu_ende(mock, tmp_path):
    engine = _engine()
    r = _run(mock, engine, tmp_path)
    assert r.session_path == f"{tmp_path}/Bahnstreik/Bahnstreik.ptx"
    (args, opts), = engine.created
    assert args == ("test", "SM Session", "Bahnstreik", str(tmp_path))
    assert opts == {"sample_rate": 48000, "bit_depth": 24, "wave": True, "interleaved": True}
    cmds = [n for n, _ in engine.requests]
    assert cmds == ["Import", "ImportVideo"]
    video = engine.requests[1][1]
    assert video["location"] == "ML_Spot"
    assert video["spot_location_data"]["location"]["location"] == "10:00:00:00"
    imp = engine.requests[0][1]
    assert imp["session_path"] == "/H/p1/Bahnstreik.aaf"
    assert imp["session_data"]["match_options"] == "MT_MatchTracks"          # Vorgabe Jens
    assert imp["session_data"]["playlist_options"] == "ImportOverlayNewOnExistingPlaylists"
    assert imp["session_data"]["audio_options"] == "CopyFromSourceAudio"
    assert imp["session_data"]["timecode_mapping_units"] == "MaintainAbsoluteTimeCodeValues"
    assert imp["session_data"]["timecode_mapping_start_time"] == "10:00:00:00"   # nie leer
    assert r.new_tracks == [] and r.warnings == []
    assert r.clip_report == {"A1": (2, 2), "A2": (1, 1)}
    assert engine.calls[-1] == "save_session"
    assert mock.mix_value("p1", "zustand") == "importiert"
    assert mock.mix_value("p1", "session_pfad") == r.session_path
    assert mock.status("p1") == "LOCKED"


def test_offene_session_wird_erst_gefragt_dann_gespeichert(mock, tmp_path):
    engine = _engine(session=("Alt", "/S/Alt/Alt.ptx"))
    with pytest.raises(NeedsConfirmation):
        _run(mock, engine, tmp_path)
    assert mock.mix_value("p1", "zustand") == "bereit"          # nichts übernommen
    assert engine.calls == ["session_name", "session_path"]       # nichts an Pro Tools geändert
    _run(mock, engine, tmp_path, confirmed=True)
    i_save, i_close = engine.calls.index("save_session"), engine.calls.index("close_session(save_on_close=True)")
    assert i_save < i_close


def test_ausgelieferte_session_ohne_rueckfrage(mock, tmp_path):
    engine = _engine(session=("Alt", "/S/Alt/Alt.ptx"))
    _run(mock, engine, tmp_path, is_delivered=lambda p: p == "/S/Alt/Alt.ptx")
    assert "close_session(save_on_close=True)" in engine.calls


def test_fremd_uebernommen_aendert_nichts(mock, tmp_path):
    engine = _engine()
    with pytest.raises(Exception, match="A. Wolf"):
        _run(mock, engine, tmp_path, pid="p3")
    assert engine.calls == [] and engine.created == []


def test_kaputtes_aaf_aendert_nichts(mock, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mod, "check_aaf",
                        lambda p: aafcheck.AafInfo(False, "AAF ist an der 2-GiB-Grenze abgeschnitten"))
    engine = _engine()
    with pytest.raises(FetchError, match="2-GiB"):
        _run(mock, engine, tmp_path)
    assert mock.mix_value("p1", "zustand") == "bereit" and engine.calls == []


def test_import_scheitert_beitrag_bleibt_uebernommen(mock, tmp_path):
    engine = _engine(fail={"Import": command_error("PT_InvalidParameter")})
    with pytest.raises(FetchError, match="bleibt übernommen"):
        _run(mock, engine, tmp_path)
    assert mock.mix_value("p1", "zustand") == "uebernommen"


def test_video_fehler_ist_hinweis(mock, tmp_path):
    r = _run(mock, _engine(video_failure="Codec"), tmp_path)
    assert any("Referenzvideo" in w for w in r.warnings)


def _fehlt_a2():
    return FakeEngine(session=None, template_tracks=("ST", "A1", "A5"), aaf_tracks=("A1", "A2"),
                      aaf_clips=CLIPS, video_track="Video")


def test_fehlende_spur_ohne_frage_kein_import(mock, tmp_path):
    engine = _fehlt_a2()
    with pytest.raises(FetchError, match="keine Möglichkeit zu fragen"):
        _run(mock, engine, tmp_path)
    assert [n for n, _ in engine.requests] == []                       # nichts importiert


def test_fehlende_spur_ab_a11_ohne_neue_spur(mock, tmp_path):
    engine = FakeEngine(session=None, template_tracks=("ST", "A1", "A5", "A11", "A12"), aaf_tracks=("A1", "A2"),
                        aaf_clips=CLIPS, video_track="Video", template_clips={"A11": [(899000, 899500)]})
    gefragt = []

    def frage(fehlen, verfuegbar):
        gefragt.append((fehlen, verfuegbar))
        return {"A2": TrackDecision(FROM_A11)}
    r = _run(mock, engine, tmp_path, decide_missing=frage)
    assert gefragt == [(["A2"], ["A1", "A11", "A12", "A5", "ST"])]
    cmds = [n for n, _ in engine.requests]
    assert "CreateNewTracks" not in cmds                               # keine neue Spur (Jens)
    renames = [(q["current_name"], q["new_name"]) for n, q in engine.requests if n == "RenameTargetTrack"]
    assert renames == [("A12", "A2"), ("A2", "A12")]                   # A11 hat Clips → A12
    assert engine.clips["A12"] == [(900000, 900600, "neu")]
    assert r.clip_report == {"A1": (2, 2), "A2": (1, 1)} and r.warnings == [] and r.new_tracks == []


def test_ab_a11_ohne_freie_spur(mock, tmp_path):
    with pytest.raises(FetchError, match="Nicht genug freie Spuren ab A11"):
        _run(mock, _fehlt_a2(), tmp_path, decide_missing=lambda f, v: {"A2": TrackDecision(FROM_A11)})


def test_fehlende_spur_zuordnen_und_zurueckbenennen(mock, tmp_path):
    engine = _fehlt_a2()
    r = _run(mock, engine, tmp_path, decide_missing=lambda f, v: {"A2": TrackDecision(MAP, "A5")})
    renames = [(q["current_name"], q["new_name"]) for n, q in engine.requests if n == "RenameTargetTrack"]
    assert renames == [("A5", "A2"), ("A2", "A5")]                     # hin und zurück
    assert {t.name for t in engine.track_objs} >= {"A1", "A5"} and "A2" not in {t.name for t in engine.track_objs}
    assert engine.clips["A5"] == [(900000, 900600, "neu")]
    assert r.clip_report["A2"] == (1, 1) and r.warnings == []


def test_zuordnen_wird_auch_bei_fehler_zurueckgenommen(mock, tmp_path):
    engine = _fehlt_a2()
    engine.fail["Import"] = command_error("PT_InvalidParameter")
    with pytest.raises(FetchError):
        _run(mock, engine, tmp_path, decide_missing=lambda f, v: {"A2": TrackDecision(MAP, "A5")})
    assert "A5" in {t.name for t in engine.track_objs}


def test_fehlende_spur_weglassen_steht_im_bericht(mock, tmp_path):
    r = _run(mock, _fehlt_a2(), tmp_path, decide_missing=lambda f, v: {"A2": TrackDecision(SKIP)})
    assert r.clip_report["A2"] == (1, 0)
    assert r.warnings == ["A2: bewusst weggelassen (1 Clips nicht importiert)"]


def test_ungueltige_entscheidung(mock, tmp_path):
    with pytest.raises(FetchError, match="gibt es nicht"):
        _run(mock, _fehlt_a2(), tmp_path, decide_missing=lambda f, v: {"A2": TrackDecision(MAP, "X9")})


def test_verlorene_clips_werden_gemeldet(mock, tmp_path):
    engine = _engine()
    engine.aaf_clips = {"A1": CLIPS["A1"][:1], "A2": CLIPS["A2"]}       # Pro Tools verliert einen Clip
    r = _run(mock, engine, tmp_path)
    assert r.clip_report["A1"] == (2, 1)
    assert any("A1: 1 von 2 Clips nicht exakt angekommen, zuerst 10:00:16:00" in w for w in r.warnings)


def test_vorhandene_clips_bleiben_und_werden_geprueft(mock, tmp_path):
    engine = _engine(template_clips={"A1": [(899000, 899500)]})          # z. B. Startband im Template
    r = _run(mock, engine, tmp_path)
    assert r.warnings == []
    assert (899000, 899500, "vorhanden") in engine.clips["A1"]


def test_video_auf_vorhandene_spur_startband_bleibt(mock, tmp_path):
    engine = _engine(video_clips=("09:59:50:00.00", "10:00:00:00.00"))     # Pro Tools Ultimate
    r = _run(mock, engine, tmp_path)
    assert r.warnings == []
    assert engine.extents["Video"] == ("09:59:50:00.00", "10:00:30:00.00")


def test_pro_tools_studio_ersetzt_startband_und_meldet_es(mock, tmp_path):
    engine = _engine(video_clips=("09:59:50:00.00", "10:00:00:00.00"), video_single_clip=True)
    r = _run(mock, engine, tmp_path)
    renames = [(q["current_name"], q["new_name"]) for n, q in engine.requests if n == "RenameTargetTrack"]
    assert renames == [("Bahnstreik_ref", "Video")]                         # Name aus dem Template
    assert [t.name for t in engine.track_objs if t.name.startswith(("Video", "Bahn"))] == ["Video"]
    assert r.warnings == ["Vorhandener Videoclip 09:59:50:00–10:00:00:00 wurde ersetzt "
                          "(diese Pro-Tools-Version erlaubt nur einen Videoclip)"]


def test_video_auf_neuer_spur_wird_gemeldet(mock, tmp_path):
    r = _run(mock, _engine(video_new_track=True), tmp_path)
    assert any("Video auf neuer Spur" in w for w in r.warnings)


def test_vorhandene_session_wird_nicht_ueberschrieben(mock, tmp_path):
    os.makedirs(tmp_path / "Bahnstreik")
    r = _run(mock, _engine(), tmp_path)
    assert r.session_path.endswith("/Bahnstreik_2/Bahnstreik_2.ptx")


# ── reine Hilfen ────────────────────────────────────────────────────────────
def test_session_name():
    assert session_name_for("Bahnstreik: Pendler warten", "p1") == "Bahnstreik"
    assert session_name_for("A/B: x", "p1") == "A_B"
    assert session_name_for("", "p9") == "p9"
    assert unique_session_dir("/S", "X", exists=lambda p: p in ("/S/X", "/S/X_2")) == ("/S", "X_3")


def test_aaf_pruefung_lesbar(tmp_path):
    p = str(tmp_path / "t.aaf")
    with aaf2.open(p, "w") as f:
        comp = f.create.CompositionMob("Sequenz")
        comp.usage = "Usage_TopLevel"
        f.content.mobs.append(comp)
        tc = comp.create_timeline_slot(25)
        tc.segment = f.create.Timecode(25, False)
        tc.segment.start = 900000
        for name in ("Audio 1", "Audio 2"):
            s = comp.create_sound_slot(edit_rate=25)
            s.name = name
            s.segment = f.create.Sequence(media_kind="sound")
    info = aafcheck.check_aaf(p)
    assert info.ok and info.track_names == ("Audio 1", "Audio 2")
    assert info.start_timecode == "10:00:00:00"
    assert info.clips == {"Audio 1": (), "Audio 2": ()}


def test_aaf_an_der_2gib_grenze_abgeschnitten(tmp_path):
    p = tmp_path / "kaputt.aaf"
    with open(p, "wb") as f:                 # Sparse-Datei, belegt kaum Platz
        f.truncate(aafcheck.CFB_LIMIT - 65536)
    info = aafcheck.check_aaf(str(p))
    assert not info.ok and "2-GiB-Grenze" in info.message


def test_zum_schluss_auf_programmbeginn(mock, tmp_path):
    engine = _engine()
    _run(mock, engine, tmp_path)
    sel = [i for i, c in enumerate(engine.calls) if c.startswith("selection ")]
    last = engine.calls[sel[-1]]
    assert last == "selection 10:00:00:00.00–10:00:00:00.00"
    assert sel[-1] < max(i for i, c in enumerate(engine.calls) if c == "save_session")   # vor dem Speichern


def test_sprung_scheitert_ist_nur_hinweis(mock, tmp_path):
    from fakes import command_error
    engine = _engine()
    original = engine.set_timeline_selection

    def set_sel(in_time, out_time):
        if in_time == "10:00:00:00.00" and engine.requests:                       # nach dem Import
            raise command_error("PT_UnknownError", "kaputt")
        return original(in_time, out_time)
    engine.set_timeline_selection = set_sel
    r = _run(mock, engine, tmp_path)
    assert any("Nicht auf 10:00:00:00 gesprungen" in w for w in r.warnings)
    assert mock.mix_value("p1", "zustand") == "importiert"


def test_missing_files_nach_dem_import_wird_nach_einstellung_beantwortet(mock, tmp_path):
    """Jens: Dem AAF fehlt die Video-Verknüpfung – „Missing Files“ automatisch suchen lassen."""
    engine = _engine()
    client = PtslClient(engine_factory=lambda: engine)
    answered = []
    client.dialogs = lambda: (["Missing Files"] if any(n == "Import" for n, _ in engine.requests)
                              and not answered else [])
    client.dialog_handler = lambda names: answered.append(names) or "automatisch gesucht und verknüpft"
    client._sleep = lambda s: None
    service = MixService(HelmutClient(mock.url, lambda: ("test", "test"), sleep=lambda s: None),
                         "Jens", "Ton 3", now=lambda: "jetzt")
    r = fetch("p1", service=service, ptsl=client, template_group="test", template_name="SM Session",
              session_parent=str(tmp_path))
    assert answered == [["Missing Files"]]
    assert "automatisch gesucht und verknüpft" in r.warnings


def test_premiere_sequenz_ab_null_wird_auf_programmbeginn_gelegt():
    """Live 03.10.2026: Premiere-Sequenz beginnt bei 00:00:00:00, das Bild liegt bei 10:00:00:00."""
    from tonmischung.workflow.fetch import sequence_offset
    assert sequence_offset(0, 25) == 900000
    assert sequence_offset(900000, 25) == 0 and sequence_offset(900000 - 250, 25) == 0          # Startband 10 s
    assert sequence_offset(900000 - 31 * 25, 25) == 31 * 25 and sequence_offset(None, 25) == 0
    assert sequence_offset(90000, 25) == 810000                                                  # 01:00:00:00


def test_import_vor_dem_sessionbeginn_wird_nicht_versucht(mock, tmp_path, monkeypatch):
    """Live 03.10.2026: AAF vor dem Sessionbeginn → Assertion „Cmn_Interval.h, line 143“, danach Absturz."""
    from tonmischung.workflow.fetch import before_session_start
    assert before_session_start(900000, "09:58:00:00", 25) is None
    assert before_session_start(900000, "10:00:00:00", 25) is None
    assert "beginnt bei 09:59:50:00, die Session erst bei 10:00:00:00" in before_session_start(899750, "10:00:00:00", 25)
    assert before_session_start(899750, "10;00;00;00", 25)                                     # Drop-Frame-Schreibweise
    assert before_session_start(None, "10:00:00:00", 25) is None and before_session_start(0, "", 25) is None
    leader = {t: tuple((a - 250, b - 250) for a, b in v) for t, v in CLIPS.items()}       # 10 s Startband: bleibt
    monkeypatch.setattr(fetch_mod, "check_aaf",
                        lambda p: aafcheck.AafInfo(True, "AAF lesbar", 899750, 25, tuple(leader), leader))
    engine = _engine()
    engine.start_time = "10:00:00:00"
    with pytest.raises(FetchError, match="Session erst bei 10:00:00:00.*Nichts importiert"):
        _run(mock, engine, tmp_path)
    assert "Import" not in [n for n, _ in engine.requests]
    engine = _engine()
    engine.fail["session_start_time"] = command_error("PT_UnknownError")     # nicht lesbar: nur protokollieren
    _run(mock, engine, tmp_path, pid="p1")
    assert "Import" in [n for n, _ in engine.requests]


def test_holen_verschiebt_sequenz_und_clips(mock, tmp_path, monkeypatch):
    from tonmischung.triage import aafcheck
    from tonmischung.workflow import fetch as F
    clips = {t: tuple((a - 900000, b - 900000) for a, b in v) for t, v in CLIPS.items()}
    monkeypatch.setattr(F, "check_aaf", lambda p: aafcheck.AafInfo(True, "AAF lesbar", 0, 25, tuple(clips), clips))
    engine = _engine()
    r = _run(mock, engine, tmp_path)
    imp = engine.requests[0][1]["session_data"]
    assert imp["timecode_mapping_units"] == "MapStartTimeCodeTo" and imp["timecode_mapping_start_time"] == "10:00:00:00"
    assert any("Sequenz beginnt bei 00:00:00:00 – beim Import auf 10:00:00:00 gelegt" in w for w in r.warnings)
    assert not any("nicht exakt angekommen" in w for w in r.warnings)                           # Kontrolle in neuer Lage


def test_fehlende_audiodateien_werden_vorher_gefragt(mock, tmp_path, monkeypatch):
    """Verknüpftes AAF, Dateien nicht erreichbar: vorher sagen; „Abbrechen“ ändert nichts."""
    from tonmischung.triage import media as MD
    from tonmischung.workflow import fetch as F
    rep = MD.MediaReport(0, (MD.MediaRef("Musik.wav", "file:///C%3a/Musik.wav", "audio", ""),))
    monkeypatch.setattr(F, "media_check", lambda path, log=None: rep)
    engine = _engine()
    asked = []
    with pytest.raises(F.FetchError, match="Audiodateien des AAF fehlen"):
        _run(mock, engine, tmp_path, confirm_media=lambda r: asked.append(r) or False)
    assert asked == [rep] and engine.created == [] and mock.mix_value("p1", "zustand") == "bereit"
    r = _run(mock, _engine(), tmp_path, confirm_media=lambda r: True)
    assert any("Musik.wav" in w and "offline" in w for w in r.warnings)


def test_assertion_beim_import_bricht_ohne_weitere_befehle_ab(mock, tmp_path):
    """Live 03.10.2026: Nach „Assertion in Cmn_Interval.h“ wartete die App aufs Bestätigen und machte dann weiter
    (Referenzvideo) – Pro Tools stürzte ab. Jetzt: sofort aufhören, klare Meldung, nichts mehr an Pro Tools."""
    engine = _engine()
    client = PtslClient(engine_factory=lambda: engine)

    class Watch:
        def poll(self):
            hit = any(n == "Import" for n, _ in engine.requests)
            return ("Assertion in Cmn_Interval.h, Zeile 143" if hit else ""), False
    client.assert_watch = Watch()
    service = MixService(HelmutClient(mock.url, lambda: ("test", "test"), sleep=lambda s: None),
                         "Jens", "Ton 3", now=lambda: "jetzt")
    with pytest.raises(FetchError, match="internen Fehler gemeldet.*Beitrag bleibt übernommen"):
        fetch("p1", service=service, ptsl=client, template_group="test", template_name="SM Session",
              session_parent=str(tmp_path))
    assert [n for n, _ in engine.requests] == ["Import"] and "save_session" not in engine.calls


def test_davinci_50fps_wird_auf_die_session_umgerechnet(mock, tmp_path, monkeypatch):
    """Jens 04.10.2026 (DaVinci Resolve, „das ist auch etwas, was wir können müssen“): Sequenz mit 50 fps in einer
    25-fps-Session – Lagen umrechnen, sonst schlägt die Kontrolle falsch an."""
    clips50 = {t: tuple((2 * a, 2 * b) for a, b in v) for t, v in CLIPS.items()}
    monkeypatch.setattr(fetch_mod, "check_aaf",
                        lambda p: aafcheck.AafInfo(True, "AAF lesbar", 2 * 900000, 50, ("A1", "A2"), clips50))
    r = _run(mock, _engine(), tmp_path)
    assert any("Sequenz mit 50 fps – Lagen auf 25 fps der Session umgerechnet" in w for w in r.warnings)
    assert not any("nicht exakt angekommen" in w for w in r.warnings) and r.clip_report == {"A1": (2, 2), "A2": (1, 1)}


def test_neue_videospur_heisst_video_1(mock, tmp_path):
    """Jens 04.10.2026: neu angelegte Videospuren wieder in „Video 1“ umbenennen (Template ohne Videospur)."""
    engine = FakeEngine(aaf_tracks=("A1", "A2"), aaf_clips=CLIPS, video_track=None, session=None)
    r = _run(mock, engine, tmp_path)
    import ptsl.PTSL_pb2 as pt
    assert [t.name for t in engine.track_objs if pt.TrackType.Name(t.type) == "VideoTrack"] == ["Video 1"]
    assert not any("Videospur heißt jetzt" in w for w in r.warnings)


def test_videospur_pruefung_bricht_den_import_nicht_ab():
    """Review B22: scheiterte die Videospur-Prüfung (Zeitlimit), brach der schon gelungene Import ab."""
    from tonmischung.ptsl.errors import PtslTimeoutError
    from tonmischung.workflow.fetch import restore_video_track as _restore_video_track

    class Slow:
        def tracks(self):
            raise PtslTimeoutError("GetTrackList: keine Antwort von Pro Tools nach 30 s")
    assert _restore_video_track(Slow(), None, None) == [
        "Videospur nicht geprüft: GetTrackList: keine Antwort von Pro Tools nach 30 s"]
