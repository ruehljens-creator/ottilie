"""Holen mit AAF-Triage – gegen den Fake (Pro Tools) und den Helmut-Mock."""
import pytest

from fakes import FakeEngine
from test_fetch import _run
from tonmischung.helmut.mock import MockHelmut
from tonmischung.triage import aafcheck
from tonmischung.triage.analyse import AafTrack, Analysis, TriageResult
from tonmischung.triage.decide import ClipFacts, triage_tracks
from tonmischung.triage.names import H_MUSIC, NameHint
from tonmischung.workflow import fetch as fetch_mod
from tonmischung.workflow.fetch import FetchError, TriageOptions

TEMPLATE = ("Spr 2", "A1", "A2", "A3", "A4", "A5", "A6", "Musik1", "Musik2", "A11", "A12")
# AAF: O-Ton-Paar, Atmo-Paar mit einem Musik-Clip (per Name), Sprecher
AAF = {"Audio 1": [(900000, 900250), (900400, 900600)], "Audio 2": [(900000, 900250), (900400, 900600)],
       "Atmo L": [(900000, 900300), (900300, 900500), (900600, 900900)],
       "Atmo R": [(900000, 900300), (900300, 900500), (900600, 900900)],
       "Spr 2": [(900100, 900200)]}
MUSIC_AT = 900300


def _triage(names=tuple(AAF), music=True):
    facts = []
    for t in names:
        for i, (a, b) in enumerate(AAF[t]):
            sig = "sprache" if t.startswith(("Audio", "Spr")) else "atmo"
            hint = NameHint(H_MUSIC, "Musikdatei (.mp3)") if music and t.startswith("Atmo") and a == MUSIC_AT else None
            facts.append(ClipFacts(t, i, a, b, f"{t}-{i}", hint, sig, 0.95))
    pairs, verdicts, decisions = triage_tracks(list(names), facts, threshold=0.8,
                                               keywords={"atmo": ["Atmo"]})
    analysis = Analysis("/H/p1/Bahnstreik.aaf", "Premiere Pro", 25, 900000,
                        [AafTrack(t, False) for t in names], facts)
    return TriageResult(analysis, verdicts, decisions, pairs)


@pytest.fixture
def mock():
    m = MockHelmut()
    m.add_project("p1", "Bahnstreik: Pendler warten", "Heimatjournal",
                  assets=["/H/p1/Bahnstreik.aaf", "/H/p1/Bahnstreik_ref.mxf"])
    m.url = m.start()
    yield m
    m.stop()


@pytest.fixture
def engine(monkeypatch):
    names = tuple(AAF)
    monkeypatch.setattr(fetch_mod, "check_aaf", lambda p: aafcheck.AafInfo(
        True, "AAF lesbar", 900000, 25, names, {t: tuple(v) for t, v in AAF.items()}))
    monkeypatch.setattr(fetch_mod, "triage_aaf", lambda path, **kw: _triage())
    return FakeEngine(session=None, template_tracks=TEMPLATE, aaf_tracks=names, aaf_clips=AAF,
                      video_track="Video")


def test_holen_mit_triage(mock, engine, tmp_path):
    r = _run(mock, engine, tmp_path, triage=TriageOptions(preview="nie"))
    assert {m.aaf_track: m.target for m in r.placement.track_moves} == {
        "Audio 1": "A1", "Audio 2": "A2", "Atmo L": "A5", "Atmo R": "A6", "Spr 2": "Spr 2"}
    assert engine.clips["Musik1"] == engine.clips["Musik2"] == [(900300, 900500, "neu")]   # Musik verschoben
    assert (900300, 900500, "neu") not in engine.clips["A5"]
    assert r.clip_report == {"Audio 1": (2, 2), "Audio 2": (2, 2), "Atmo L": (3, 3), "Atmo R": (3, 3),
                             "Spr 2": (1, 1)}
    assert r.warnings == [] and r.new_tracks == []
    assert [t.name for t in engine.track_objs if t.name in TEMPLATE] == list(TEMPLATE)    # Namen zurück
    assert "CreateNewTracks" not in [n for n, _ in engine.requests]


def test_vorschau_abgelehnt_nichts_importiert(mock, engine, tmp_path):
    gefragt = []
    with pytest.raises(FetchError, match="nicht bestätigt"):
        _run(mock, engine, tmp_path, triage=TriageOptions(preview="immer"),
             confirm_triage=lambda tr, pl, rp: gefragt.append(pl) or False)
    assert len(gefragt) == 1
    assert "Import" not in [n for n, _ in engine.requests]


def test_vorschau_nur_bei_unsicherheit(mock, engine, tmp_path, monkeypatch):
    gefragt = []
    _run(mock, engine, tmp_path, triage=TriageOptions(), confirm_triage=lambda tr, pl, rp: gefragt.append(1) or True)
    assert gefragt == []                                     # alles sicher → keine Vorschau
    unsicher = _triage()
    unsicher.decisions[0] = type(unsicher.decisions[0])(unsicher.decisions[0].clip, "rest", "nicht zuzuordnen")
    monkeypatch.setattr(fetch_mod, "triage_aaf", lambda path, **kw: unsicher)
    engine2 = FakeEngine(session=None, template_tracks=TEMPLATE, aaf_tracks=tuple(AAF), aaf_clips=AAF,
                         video_track="Video")
    _run(mock, engine2, tmp_path, pid="p1", triage=TriageOptions(),
         confirm_triage=lambda tr, pl, rp: gefragt.append(1) or True)
    assert gefragt == [1]


def test_zu_wenig_zielspuren(mock, engine, tmp_path):
    engine.template_tracks = ["A1", "A2"]
    with pytest.raises(FetchError, match="keine Spur angelegt"):
        _run(mock, engine, tmp_path, triage=TriageOptions(preview="nie"))
    assert "Import" not in [n for n, _ in engine.requests]


def test_korrektur_in_der_vorschau(mock, engine, tmp_path):
    from tonmischung.triage.corrections import apply_corrections

    def confirm(tr, pl, replan):
        assert len(pl.clip_moves) == 1                                       # Musik wäre verschoben worden
        fixed = apply_corrections(tr, [("Atmo L", 1, "atmo")])               # Mischer: ist Atmo
        assert replan(fixed).clip_moves == []
        return fixed
    r = _run(mock, engine, tmp_path, triage=TriageOptions(preview="immer"), confirm_triage=confirm)
    assert r.placement.clip_moves == []
    assert (900300, 900500, "neu") in engine.clips["A5"] and (900300, 900500, "neu") in engine.clips["A6"]
    assert not engine.clips.get("Musik1")
    assert r.clip_report["Atmo L"] == (3, 3) and r.clip_report["Atmo R"] == (3, 3)
