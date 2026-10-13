import pytest

from fakes import FakeEngine, _Track, command_error
from tonmischung.ptsl.client import PtslClient
from tonmischung.sessions.triage_runner import apply_clip_moves, apply_copies, renamed_for_import
from tonmischung.triage.decide import ClipFacts
from tonmischung.triage.placement import ClipMove, PlacementError, TrackCopy, TrackMove

SESSION = ["Spr 2", "A1", "A2", "A3", "A4", "A5", "A6", "Musik1", "Musik2"]


def _client(**clips):
    e = FakeEngine()
    e.track_objs = [_Track(t, "AudioTrack", bool(clips.get(t))) for t in SESSION]
    e.clips = {t: list(v) for t, v in clips.items()}
    return PtslClient(engine_factory=lambda: e), e


def names(e):
    return [t.name for t in e.track_objs]


def test_umbenennen_kollisionsfrei_und_zurueck():
    ptsl, e = _client()
    moves = [TrackMove("Spr 2", "Spr 2"), TrackMove("A1.01", "A1"), TrackMove("A2.01", "A2"),
             TrackMove("A3", "A5"), TrackMove("A4", "A6")]
    warnings = []
    with renamed_for_import(ptsl, moves, SESSION, warnings):
        during = dict(zip(SESSION, names(e)))
    assert during["A5"] == "A3" and during["A6"] == "A4"            # AAF-Atmo landet auf A5/A6
    assert during["A1"] == "A1.01" and during["Spr 2"] == "Spr 2"
    assert during["A3"].startswith("tm~") and during["A4"].startswith("tm~")   # Kollision beiseite
    assert len(set(names(e))) == len(SESSION)
    assert names(e) == SESSION and warnings == []


def test_umbenennen_wird_auch_nach_fehler_zurueckgenommen():
    ptsl, e = _client()
    with pytest.raises(RuntimeError):
        with renamed_for_import(ptsl, [TrackMove("A3", "A5")], SESSION, []):
            raise RuntimeError("Import gescheitert")
    assert names(e) == SESSION


def test_gleichnamige_aaf_spuren():
    ptsl, _ = _client()
    with pytest.raises(PlacementError, match="Gleichnamige"):
        with renamed_for_import(ptsl, [TrackMove("ST", "A1"), TrackMove("ST", "A2")], SESSION, []):
            pass


def cf(a, b):
    return ClipFacts("x", 0, a, b, "x")


def test_clips_verschieben_paar_und_mono():
    ptsl, e = _client(A5=[(900000, 900100, "a"), (900200, 900300, "m")],
                      A6=[(900000, 900100, "a"), (900200, 900300, "m")], A3=[(900400, 900500, "x")])
    e.hidden = {"Musik1", "Musik2"}                                    # wie im Template ausgeblendet
    m = (cf(900200, 900300),)
    x = (cf(900400, 900500),)
    w = apply_clip_moves(ptsl, [
        ClipMove(("A5", "A6"), ("Musik1", "Musik2"), 900200, 900300, "musik", m * 2, (m, m)),
        ClipMove(("A3",), ("A1", "A2"), 900400, 900500, "o_ton", x, (x, x))])
    assert w == []
    assert e.clips["A5"] == [(900000, 900100, "a")] and e.clips["Musik2"] == [(900200, 900300, "m")]
    assert e.clips["A1"] == e.clips["A2"] == [(900400, 900500, "x")] and e.clips["A3"] == []
    assert e.edit_mode == "EMO_Shuffle"                               # Edit-Modus zurückgesetzt
    assert ("SetEditMode", {"edit_mode": "EMO_Slip"}) in e.requests
    assert not e.hidden                                               # Ziel eingeblendet


def test_einfuegen_scheitert_quelle_bleibt():
    ptsl, e = _client(A5=[(900000, 900100, "a")])
    e.fail["Paste"] = command_error("PT_Forbidden", "nicht möglich")
    m = (cf(900000, 900100),)
    w = apply_clip_moves(ptsl, [ClipMove(("A5",), ("Musik1",), 900000, 900100, "musik", m, (m,))])
    assert len(w) == 1 and "bleibt auf der Spur" in w[0]
    assert e.clips["A5"] == [(900000, 900100, "a")]                   # nichts verloren


def test_ziel_nicht_bestaetigt_quelle_bleibt():
    ptsl, e = _client(A5=[(900000, 900100, "a")])
    m = (cf(900000, 900120),)                                         # erwartet, aber so nicht da
    w = apply_clip_moves(ptsl, [ClipMove(("A5",), ("Musik1",), 900000, 900100, "musik", m, (m,))])
    assert "Quelle bleibt" in w[0]
    assert e.clips["A5"] == [(900000, 900100, "a")]


def test_kopieren_auf_partnerspur():
    ptsl, e = _client(A5=[(900000, 900100, "a"), (900500, 900600, "b")])
    assert apply_copies(ptsl, [TrackCopy("A5", "A6")]) == []
    assert e.clips["A6"] == e.clips["A5"]


def test_leeren_meldet_fehler_obwohl_geleert():
    """Pro Tools: Video-Engine-Fehler nach erfolgreichem Clear – kein Hinweis, nichts doppelt."""
    ptsl, e = _client(A3=[(900000, 900100, "a")])
    real = e.client.run_command

    def clear_with_error(cid, req):
        import ptsl.PTSL_pb2 as pt
        result = real(cid, req)
        if pt.CommandId.Name(cid) == "Clear":
            raise command_error("PT_UnknownError", "The Video Engine is unable to create thumbnails")
        return result
    e.client.run_command = clear_with_error
    m = (cf(900000, 900100),)
    w = apply_clip_moves(ptsl, [ClipMove(("A3",), ("A5",), 900000, 900100, "atmo", m, (m,))])
    assert w == [] and e.clips["A3"] == [] and e.clips["A5"] == [(900000, 900100, "a")]


def test_leeren_scheitert_wirklich():
    ptsl, e = _client(A3=[(900000, 900100, "a")])
    e.fail["Clear"] = command_error("PT_Forbidden", "nein")
    m = (cf(900000, 900100),)
    w = apply_clip_moves(ptsl, [ClipMove(("A3",), ("A5",), 900000, 900100, "atmo", m, (m,))])
    assert len(w) == 1 and "Material doppelt" in w[0]

