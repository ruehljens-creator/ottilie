import pytest

from tonmischung.core.track_match import (
    FROM_A11, MAP, SKIP, TrackDecision, assign_from_a11, check_decisions, missing_tracks, parse_choice)

SESSION = {"ST", "A1", "A2", "A5", "Musik1"}
AAF = ("A1", "Audio 1", "Audio 2")


def test_fehlende_spuren():
    assert missing_tracks(AAF, SESSION) == ["Audio 1", "Audio 2"]


def test_entscheidungen_pruefen():
    ok = {"Audio 1": TrackDecision(FROM_A11), "Audio 2": TrackDecision(MAP, "A5")}
    assert check_decisions(["Audio 1", "Audio 2"], ok, SESSION, AAF) == []
    assert "Keine Entscheidung für Audio 2" in check_decisions(["Audio 1", "Audio 2"],
                                                               {"Audio 1": TrackDecision(SKIP)}, SESSION, AAF)
    assert any("bekommt schon Clips" in e for e in check_decisions(
        ["Audio 1"], {"Audio 1": TrackDecision(MAP, "A1")}, SESSION, AAF))
    assert any("mehreren" in e for e in check_decisions(
        ["Audio 1", "Audio 2"], {"Audio 1": TrackDecision(MAP, "A5"), "Audio 2": TrackDecision(MAP, "A5")},
        SESSION, AAF))


def test_kommandozeile():
    assert parse_choice("ab A11") == TrackDecision(FROM_A11)
    assert parse_choice("Audio 1=ab A11") == {"Audio 1": TrackDecision(FROM_A11)}
    assert parse_choice("Audio 1=A5, Audio 2=weglassen") == {
        "Audio 1": TrackDecision(MAP, "A5"), "Audio 2": TrackDecision(SKIP)}
    with pytest.raises(ValueError):
        parse_choice("irgendwas")


def test_ab_a11_naechste_freie_spur():
    session = ["A1", "A11", "A12", "A13", "A14", "A2", "A9"]
    d = assign_from_a11({"Audio 3": TrackDecision(FROM_A11), "Audio 4": TrackDecision(FROM_A11),
                         "Audio 5": TrackDecision(MAP, "A12")}, session, occupied={"A11"})
    assert d == {"Audio 3": TrackDecision(MAP, "A13"), "Audio 4": TrackDecision(MAP, "A14"),
                 "Audio 5": TrackDecision(MAP, "A12")}           # A11 belegt, A12 schon Ziel, A9 < 11
    with pytest.raises(ValueError, match="Nicht genug freie Spuren ab A11"):
        assign_from_a11({"x": TrackDecision(FROM_A11), "y": TrackDecision(FROM_A11)}, ["A11", "A12"],
                        occupied={"A12"})
