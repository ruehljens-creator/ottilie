import pytest

from test_fetch_triage import _triage
from tonmischung.triage.corrections import REASON, apply_corrections, partners


def test_korrektur_gilt_fuer_beide_haelften():
    tr = _triage()
    assert partners(tr, "Atmo L", 1) == [("Atmo L", 1), ("Atmo R", 1)]
    assert partners(tr, "Spr 2", 0) == [("Spr 2", 0)]
    fixed = apply_corrections(tr, [("Atmo R", 1, "atmo")])
    d = {(x.clip.track, x.clip.index): x for x in fixed.decisions}
    assert d["Atmo L", 1].category == d["Atmo R", 1].category == "atmo"
    assert d["Atmo L", 1].reason == REASON and not d["Atmo L", 1].deviates
    assert d["Audio 1", 0].category == "o_ton"                     # Rest unverändert
    assert {(x.clip.track, x.clip.index): x.category for x in tr.decisions}[("Atmo L", 1)] == "musik"  # Original bleibt


def test_korrektur_weicht_von_spur_ab_und_unbekannte_kategorie():
    fixed = apply_corrections(_triage(), [("Audio 1", 0, "musik")])
    d = {(x.clip.track, x.clip.index): x for x in fixed.decisions}
    assert d["Audio 1", 0].deviates and d["Audio 2", 0].category == "musik"
    with pytest.raises(ValueError):
        apply_corrections(_triage(), [("Audio 1", 0, "gibtsnicht")])
