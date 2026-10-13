"""Transport-Sperre: vor Export und Lautheitskorrektur muss Pro Tools nachweislich stehen (Jens)."""
import grpc
import pytest

from fakes import FakeEngine, FakeRpcError, command_error
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import TransportBusyError
from tonmischung.sessions.transport_guard import ensure_stopped


def _guard(*states, fail=None, **kw):
    e = FakeEngine(transport=states, fail=fail)
    pausen = []
    stopped = ensure_stopped(PtslClient(engine_factory=lambda: e), "Export", sleep=pausen.append, **kw)
    return stopped, e, pausen


def test_steht_schon():
    stopped, e, pausen = _guard("TS_TransportStopped")
    assert stopped is False and "toggle_play_state" not in e.calls and pausen == []


@pytest.mark.parametrize("state", ["TS_TransportPlaying", "TS_TransportRecording", "TS_TransportIsCued"])
def test_laeuft_wird_angehalten(state):
    stopped, e, pausen = _guard(state, state, "TS_TransportStopped")
    assert stopped is True
    assert e.calls.count("toggle_play_state") == 1
    assert pausen == [0.5, 1.0, 2.0]                 # zwei Kontrollen, dann Zeit zum Finalisieren


def test_haelt_gerade_an_ohne_umschalten():
    """„IsStopping“: Pro Tools hält selbst an – Umschalten würde wieder starten."""
    stopped, e, _ = _guard("TS_TransportIsStopping", "TS_TransportStopped")
    assert stopped is True and "toggle_play_state" not in e.calls


def test_stillstand_nie_bestaetigt():
    with pytest.raises(TransportBusyError, match="Export abgebrochen: Pro Tools steht nicht \\(Wiedergabe läuft\\)"):
        _guard("TS_TransportPlaying")


def test_zustand_nicht_lesbar_bricht_ab():
    """Anders als PunchBuddy („fahre vorsichtshalber fort“) wird hier abgebrochen."""
    with pytest.raises(TransportBusyError, match="nicht lesbar"):
        _guard("TS_TransportStopped", fail={"transport_state": FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED)})


def test_ohne_session():
    with pytest.raises(TransportBusyError, match="keine Session"):
        _guard("TS_TransportStopped", fail={"transport_state": command_error("PT_NoOpenedSession")})


def test_meldung_nennt_den_schritt():
    e = FakeEngine(transport=("TS_TransportRecording",))
    meldungen = []
    with pytest.raises(TransportBusyError, match="^Lautheit ST abgebrochen"):
        ensure_stopped(PtslClient(engine_factory=lambda: e), "Lautheit ST", sleep=lambda s: None,
                       log=meldungen.append)
    assert meldungen == ["Lautheit ST: Aufnahme läuft – Pro Tools wird angehalten"]
