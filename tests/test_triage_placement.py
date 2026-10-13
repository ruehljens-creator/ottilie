import pytest

from tonmischung.triage.decide import ClipFacts, triage_tracks
from tonmischung.triage.names import H_MUSIC, NameHint
from tonmischung.triage.placement import ClipMove, PlacementError, TrackCopy, TrackMove, plan

TEMPLATE = ["Spr 1", "Spr 2", "Spr 3"] + [f"A{i}" for i in range(1, 15)] + [f"Musik{i}" for i in range(1, 9)]
KW = {"musik": ["Musik"], "atmo": ["Atmo"]}


def c(track, i, signal="atmo", conf=0.9, start=None, length=90, hint=None, fades=(0, 0)):
    start = i * 100 if start is None else start
    return ClipFacts(track, i, start, start + length, f"{track}-{i}", NameHint(hint, hint) if hint else None,
                     signal, conf, *fades)


def run(names, clips, template=TEMPLATE, occupied=frozenset()):
    pairs, verdicts, decisions = triage_tracks(names, clips, threshold=0.8, keywords=KW)
    return plan(names, pairs, verdicts, decisions, template, set(occupied))


def pair(left, right, signal, n=3, conf=0.9):
    return [c(left, i, signal, conf) for i in range(n)] + [c(right, i, signal, conf) for i in range(n)]


def test_spuren_nach_kategorie_auf_zielpaare():
    clips = (pair("Audio 1", "Audio 2", "sprache") + pair("Audio 5", "Audio 6", "atmo")
             + pair("Audio 9", "Audio 10", "musik") + [c("Spr 2", 0, "sprache")])
    p = run(["Spr 2", "Audio 1", "Audio 2", "Audio 5", "Audio 6", "Audio 9", "Audio 10"], clips)
    assert p.track_moves == [TrackMove("Spr 2", "Spr 2"), TrackMove("Audio 1", "A1"), TrackMove("Audio 2", "A2"),
                             TrackMove("Audio 5", "A5"), TrackMove("Audio 6", "A6"),
                             TrackMove("Audio 9", "Musik1"), TrackMove("Audio 10", "Musik2")]
    assert p.clip_moves == [] and p.copies == []
    assert p.final[("Audio 2", 1)] == ("A2",) and p.final[("Spr 2", 0)] == ("Spr 2",)


def test_zweite_fassung_und_unklares_ab_a11():
    clips = (pair("Audio 3_FX", "Audio 4_FX", "sprache") + pair("Audio 3_No FX", "Audio 4_No FX", "sprache"))
    p = run(["Audio 3_FX", "Audio 3_No FX", "Audio 4_FX", "Audio 4_No FX"], clips)
    assert {m.aaf_track: m.target for m in p.track_moves} == {
        "Audio 3_FX": "A1", "Audio 4_FX": "A2", "Audio 3_No FX": "A11", "Audio 4_No FX": "A12"}


def test_mono_ohne_partner_wird_verdoppelt():
    p = run(["Atmo L"], [c("Atmo L", 0, "atmo"), c("Atmo L", 1, "atmo", start=500)])
    assert p.track_moves == [TrackMove("Atmo L", "A5")]
    assert p.copies == [TrackCopy("A5", "A6")]
    assert p.final[("Atmo L", 1)] == ("A5", "A6")
    assert any("verdoppelt" in n for n in p.notes)


def test_belegte_templatespuren_und_ueberlauf_ab_a11():
    clips = pair("O1", "O2", "sprache") + pair("O3", "O4", "sprache") + pair("O5", "O6", "sprache")
    p = run(["O1", "O2", "O3", "O4", "O5", "O6"], clips, occupied={"A1"})       # A1 hat schon Clips
    assert [m.target for m in p.track_moves] == ["A3", "A4", "A11", "A12", "A13", "A14"]
    assert any("keine freie O-Ton-Spur" in n for n in p.notes)


def test_nicht_genug_spuren():
    clips = pair("O1", "O2", "sprache") + pair("O3", "O4", "sprache")
    with pytest.raises(PlacementError, match="keine Spur angelegt"):
        run(["O1", "O2", "O3", "O4"], clips, template=["A1", "A2"])


def test_abweichender_clip_auf_freies_paar_seiner_kategorie():
    # Atmo-Paar mit sicherer Musik (beide Hälften), zwei Clips über eine Blende verbunden
    left = [c("Atmo L", 0), c("Atmo L", 1, "musik", 0.9, fades=(0, 5)),
            c("Atmo L", 2, "musik", 0.9, start=195, fades=(5, 0)), c("Atmo L", 3, start=400)]
    right = [ClipFacts("Atmo R", x.index, x.start, x.end, x.source, x.hint, x.signal, x.confidence, x.fade_in,
                       x.fade_out) for x in left]
    p = run(["Atmo L", "Atmo R"], left + right)
    assert [m.target for m in p.track_moves] == ["A5", "A6"]
    (move,) = p.clip_moves
    assert (move.sources, move.targets, move.start, move.end) == (("A5", "A6"), ("Musik1", "Musik2"), 100, 285)
    assert len(move.clips) == 4                      # zwei Clips je Hälfte, ein Bereich
    assert p.final[("Atmo L", 1)] == ("Musik1",) and p.final[("Atmo R", 2)] == ("Musik2",)
    assert p.final[("Atmo L", 0)] == ("A5",)


def test_ziel_belegt_dann_naechstes_paar_dann_ab_a11():
    music = pair("M1", "M2", "musik", n=5)                   # belegt Musik1/2 durchgehend 0–490
    atmo = [c("L", i, hint=(H_MUSIC if i == 1 else None)) for i in range(3)]
    atmo += [ClipFacts("R", x.index, x.start, x.end, x.source, x.hint, x.signal, x.confidence) for x in atmo]
    p = run(["M1", "M2", "L", "R"], music + atmo,
            template=[f"A{i}" for i in range(5, 13)] + ["Musik1", "Musik2"])
    (move,) = p.clip_moves
    assert move.targets == ("A11", "A12")             # Musik1/2 zu der Zeit belegt, keine weiteren Musikspuren


def test_kein_platz_bleibt_und_wird_gemeldet():
    music = pair("M1", "M2", "musik", n=5)
    atmo = [c("L", i, hint=(H_MUSIC if i == 1 else None)) for i in range(3)]
    atmo += [ClipFacts("R", x.index, x.start, x.end, x.source, x.hint, x.signal, x.confidence) for x in atmo]
    p = run(["M1", "M2", "L", "R"], music + atmo, template=["A5", "A6", "Musik1", "Musik2"])
    assert p.clip_moves == [] and any("bleibt auf der Spur" in n for n in p.notes)
    assert p.final[("L", 1)] == ("A5",)


def test_clipmove_typ():
    assert ClipMove(("A5",), ("Musik1",), 0, 10, "musik", ()).targets == ("Musik1",)


def test_wenn_voll_auf_die_naechste_gruppe():
    """Jens: wenn voll, dann auf die folgenden Spuren der nächsten Gruppe – oder auf die Reservespuren."""
    from tonmischung.triage.placement import TargetRanges
    clips = pair("O1", "O2", "sprache") + pair("O3", "O4", "sprache") + pair("O5", "O6", "sprache")
    names = ["O1", "O2", "O3", "O4", "O5", "O6"]
    pairs, verdicts, decisions = triage_tracks(names, clips, threshold=0.8, keywords=KW)
    reserve = plan(names, pairs, verdicts, decisions, TEMPLATE, set(), TargetRanges())
    nxt = plan(names, pairs, verdicts, decisions, TEMPLATE, set(), TargetRanges(when_full="verwandte_kategorie"))
    assert [m.target for m in reserve.track_moves] == ["A1", "A2", "A3", "A4", "A11", "A12"]
    assert [m.target for m in nxt.track_moves] == ["A1", "A2", "A3", "A4", "A5", "A6"]          # Atmo-Spuren
    assert any("keine freie O-Ton-Spur mehr – auf A5/A6" in n for n in nxt.notes)


def test_reihenfolge_der_gruppen():
    from tonmischung.triage.names import ATMO, MUSIC, OTON, REST, SPEAKER
    from tonmischung.triage.placement import TargetRanges
    r = TargetRanges(when_full="verwandte_kategorie")
    assert r.chain(OTON) == [OTON, ATMO, MUSIC, REST] and r.chain(MUSIC) == [MUSIC, REST] and r.chain(REST) == [REST]
    assert TargetRanges().chain(ATMO) == [ATMO, REST] and r.chain(SPEAKER) == [SPEAKER, REST]
    assert TargetRanges(when_full="nachfragen").chain(OTON) == [OTON, REST]            # noch nicht umgesetzt
