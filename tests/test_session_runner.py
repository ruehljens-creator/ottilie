import pytest

from fakes import FakeEngine, command_error
from tonmischung.core.session_rules import ImportPlan, Outcome, Step, decide_before_import, \
    OpenSessionState
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import SaveFailedError, TransportBusyError
from tonmischung.sessions.runner import run_import_plan

PLAN = decide_before_import(OpenSessionState("/S/Alt.ptx", delivered=True))


class _Uhr:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _client(engine):
    return PtslClient(engine_factory=lambda: engine)


def test_speichern_dann_schliessen():
    engine = FakeEngine()
    run_import_plan(_client(engine), PLAN)
    assert engine.calls == ["transport_state", "save_session", "close_session(save_on_close=True)"]


def test_speichern_scheitert_dann_wird_nicht_geschlossen():
    engine = FakeEngine(fail={"save_session": command_error("OS_DiskSpace", "Platte voll")})
    with pytest.raises(SaveFailedError, match="bleibt offen"):
        run_import_plan(_client(engine), PLAN)
    assert not any(c.startswith("close_session") for c in engine.calls)


def test_wartet_bis_transport_steht():
    engine = FakeEngine(transport=["TS_TransportPlaying", "TS_TransportPlaying", "TS_TransportStopped"])
    uhr = _Uhr()
    run_import_plan(_client(engine), PLAN, sleep=uhr.sleep, clock=uhr)
    assert uhr.t == 2.0
    assert "save_session" in engine.calls


def test_transport_laeuft_zu_lange_nichts_wird_gespeichert():
    engine = FakeEngine(transport=["TS_TransportRecording"])
    uhr = _Uhr()
    with pytest.raises(TransportBusyError):
        run_import_plan(_client(engine), PLAN, max_wait_s=5, sleep=uhr.sleep, clock=uhr)
    assert "save_session" not in engine.calls


@pytest.mark.parametrize("outcome", [Outcome.ASK, Outcome.ABORT])
def test_nicht_ausfuehrbare_plaene(outcome):
    engine = FakeEngine()
    with pytest.raises(ValueError):
        run_import_plan(_client(engine), ImportPlan(outcome, (Step.SAVE,)))
    assert engine.calls == []
