"""Nach dem Import: Spuren mit Clips einblenden, leere ausblenden (Jens 03.10.2026)."""
from fakes import FakeEngine, _Track
from tonmischung.core.track_view import TrackState, is_target, plan_view, prefixes_from_ranges
from tonmischung.ptsl.client import PtslClient
from tonmischung.workflow.fetch import apply_track_view

TRACKS = [
    TrackState("ST", "AudioTrack", False, False),
    TrackState("A1", "AudioTrack", True, False, folder="O-Ton", is_open=True),
    TrackState("A2", "AudioTrack", True, True, folder="O-Ton"),
    TrackState("A7", "AudioTrack", False, False, folder="Atmo"),
    TrackState("Musik3", "AudioTrack", True, True, folder="Musik"),
    TrackState("O-Ton", "BasicFolder", False, False, is_open=False),
    TrackState("Musik", "BasicFolder", False, True, folder="Stems", is_open=False),
    TrackState("Stems", "RoutingFolder", False, False, is_open=False),
    TrackState("Atmo", "BasicFolder", False, False),
    TrackState("Hall", "Aux", False, False),
    TrackState("Video 1", "VideoTrack", True, False),
    TrackState("Fade", "AudioTrack", False, False, has_automation=True),
    TrackState("Spr", "AudioTrack", False, False),
]


def test_plan():
    p = plan_view(TRACKS, keep={"ST", "Spr"})
    assert p.show == ("A2", "Musik3", "Musik")                      # A-/Musik-Spuren mit Clips und ihr Ordner
    assert p.open_folders == ("O-Ton", "Musik", "Stems")
    assert p.hide == ("A7",)                                          # nur leere A-/Musik-Spuren
    assert plan_view(TRACKS, hide_empty=False).hide == ()
    assert plan_view(TRACKS, show_clips=False).show == () and plan_view(TRACKS, show_clips=False).open_folders == ()


def test_stems_spr_video_bleiben_immer():
    tracks = [TrackState("ST", "AudioTrack", False, False), TrackState("Spr", "AudioTrack", False, False),
              TrackState("Spr 2", "AudioTrack", True, True), TrackState("Video 1", "VideoTrack", True, True),
              TrackState("Mus", "AudioTrack", False, False), TrackState("A10", "AudioTrack", False, False),
              TrackState("Musik 8", "AudioTrack", False, False)]
    p = plan_view(tracks)                                             # auch ohne „keep“
    assert p.show == () and p.hide == ("A10", "Musik 8")
    assert prefixes_from_ranges({"o_ton": {"von": "A1", "bis": "A4"}, "musik": {"von": "Musik1", "bis": "Musik8"},
                                 "rest": {"von": "A11", "bis": ""}}) == ("A", "Musik")
    assert prefixes_from_ranges({}) == ("A", "Musik")
    assert is_target("A12") and is_target("Musik 3") and not is_target("ST") and not is_target("Atmo")


def test_anwenden_ueber_ptsl():
    engine = FakeEngine(session=("S", "/x/S.ptx"))
    engine.track_objs = [_Track("ST", "AudioTrack", False), _Track("A1", "AudioTrack", True, hidden=True, folder="O-Ton"),
                         _Track("O-Ton", "BasicFolder", False, is_open=False), _Track("A9", "AudioTrack", False)]
    warnings = []
    apply_track_view(PtslClient(engine_factory=lambda: engine), {"show_clips": True, "hide_empty": True, "keep": ["ST"]},
                     warnings)
    state = {t.name: (t.track_attributes.is_hidden, t.track_attributes.is_open) for t in engine.track_objs}
    assert state == {"ST": (False, True), "A1": (False, True), "O-Ton": (False, True), "A9": (True, True)}
    assert warnings == []


def test_aus_den_einstellungen(tmp_path):
    import types

    from tonmischung.app.fetch_app import track_view as _track_view
    from tonmischung.config.settings import Settings
    v = _track_view(types.SimpleNamespace(settings=Settings(local_path=tmp_path / "e.json")))
    assert v["show_clips"] and v["hide_empty"] and v["prefixes"] == ["A", "Musik"]
    assert {"ST", "Spr"} <= set(v["keep"])
