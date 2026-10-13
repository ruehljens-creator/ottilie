import pytest

from tonmischung.core.session_rules import (
    CLEAR_PRO_TOOLS, OpenSessionState, Outcome, decide_before_import)

OFFEN = OpenSessionState(path="/Sessions/Alt/Alt.ptx", delivered=False)
AUSGELIEFERT = OpenSessionState(path="/Sessions/Alt/Alt.ptx", delivered=True)


@pytest.mark.parametrize("offen, kwargs, outcome, steps", [
    (None, {}, Outcome.PROCEED, ()),
    (AUSGELIEFERT, {}, Outcome.PROCEED, CLEAR_PRO_TOOLS),
    (OFFEN, {}, Outcome.ASK, ()),
    (OFFEN, {"confirmed": True}, Outcome.PROCEED, CLEAR_PRO_TOOLS),
    (OFFEN, {"confirmed": False}, Outcome.ABORT, ()),
    (OFFEN, {"unattended": True}, Outcome.PROCEED, CLEAR_PRO_TOOLS),
    (OFFEN, {"target_session_path": "/Sessions/Alt/Alt.ptx"}, Outcome.PROCEED, ()),
])
def test_entscheidung_vor_dem_import(offen, kwargs, outcome, steps):
    plan = decide_before_import(offen, **kwargs)
    assert plan.outcome is outcome
    assert plan.steps == steps
    assert plan.reason


def test_immer_erst_speichern_dann_schliessen():
    names = [s.name for s in CLEAR_PRO_TOOLS]
    assert names.index("SAVE") < names.index("CLOSE")
    assert names[0] == "WAIT_TRANSPORT"


def test_umlaute_in_nfc_und_nfd_sind_dieselbe_session():
    """Review B19: Pro Tools meldet „Gespräch“ zerlegt (NFD), Helmut zusammengesetzt (NFC)."""
    import unicodedata
    from tonmischung.core.session_rules import same_session
    nfc = "/S/Gespräch/Gespräch.ptx"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd and same_session(nfc, nfd) and same_session("/S/a/../Gespräch/Gespräch.ptx", nfd)
    assert not same_session(nfc, "/S/Gespraech/Gespraech.ptx") and not same_session(None, nfc)
