"""PunchBuddy-Kern über die Verbindung und Einstellungen von Tonmischung (Plan PunchBuddy-Übernahme, Schritt 1)."""
import threading

import grpc
import pytest

from fakes import FakeEngine, FakeRpcError
from tonmischung.config.settings import Settings, load_schema
from tonmischung.ptsl.client import PtslClient
from tonmischung.punch.core import PunchCore

SCHEMA = load_schema()


@pytest.fixture
def core(tmp_path):
    engine = FakeEngine()
    client = PtslClient(engine_factory=lambda: engine)
    settings = Settings(local_path=tmp_path / "e.json")
    meldungen, belegt = [], []
    c = PunchCore(client, settings, SCHEMA, on_error=lambda t, m: meldungen.append((t, m)),
                  on_busy=belegt.append)
    restore = c.install()
    c.engine, c.meldungen, c.belegt = engine, meldungen, belegt
    yield c
    restore()


def test_kern_nutzt_unsere_verbindung(core):
    transport = core.module("transport")
    assert transport._get_engine() is core.engine
    assert core.module("export")._get_engine() is core.engine


def test_aufrufe_laufen_ueber_unsere_sperre(core):
    transport = core.module("transport")
    gesperrt = []

    def fremder_thread_versucht():
        ok = core.ptsl._lock.acquire(blocking=False)
        gesperrt.append(not ok)
        if ok:
            core.ptsl._lock.release()

    def befehl():
        t = threading.Thread(target=fremder_thread_versucht)
        t.start()
        t.join()
        return "TS_TransportStopped"
    assert transport._ptsl_call(befehl, label="Probe") == (True, "TS_TransportStopped")
    assert gesperrt == [True]


def test_fehler_wie_punchbuddy_als_ergebnis(core):
    transport = core.module("transport")

    def kaputt():
        raise ValueError("Unsinn")
    ok, fehler = transport._ptsl_call(kaputt, label="Probe")
    assert not ok and isinstance(fehler, ValueError)

    def zeitlimit():
        raise FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED)
    assert transport._ptsl_call(zeitlimit, label="Probe") == (False, None)
    assert core.ptsl.current_engine is None                    # tote Verbindung verworfen


def test_kern_liest_unsere_einstellungen(core):
    core.settings.set_local("aufnahme.spuren.rec_b", ["A1", "A2"])
    core.settings.set_local("import.timecode_startband.programmbeginn", "09:59:00:00")
    transport = core.module("transport")
    assert transport.load_settings()["tracks_b"] == ["A1", "A2"]
    import punchbuddy.state as state
    assert state.app_ref.settings["export_start_tc"] == "09:59:00:00"


def test_zum_programmbeginn_ende_zu_ende(core):
    """Echte PunchBuddy-Funktion run_goto_start gegen die Pro-Tools-Attrappe."""
    core.settings.set_local("import.timecode_startband.programmbeginn", "10:00:00:00")
    core.module("transport").run_goto_start()
    assert "selection 10:00:00:00.00–10:00:00:00.00" in core.engine.calls


def test_meldung_und_belegt_gehen_an_die_app(core):
    core.module("transport")._show_error("Export abgebrochen", "Video-Ende fehlt")
    assert core.meldungen == [("Export abgebrochen", "Video-Ende fehlt")]
    import punchbuddy.state as state
    state.app_ref.title = state.ICON_BUSY
    state.app_ref.title = state.ICON_IDLE
    assert core.belegt == [True, False]


def test_rueckbau_stellt_punchbuddy_wieder_her(tmp_path):
    from tonmischung.vendor import import_isolated
    engine_mod = import_isolated("punchbuddy.engine")
    transport = import_isolated("punchbuddy.transport")
    original = transport._get_engine
    c = PunchCore(PtslClient(engine_factory=lambda: FakeEngine()), Settings(local_path=tmp_path / "e.json"), SCHEMA)
    restore = c.install()
    assert transport._get_engine != original
    restore()
    assert transport._get_engine is original is engine_mod._get_engine


def test_sprache_deutsch(core):
    import punchbuddy.i18n as i18n
    assert i18n.get_language() == "de"
