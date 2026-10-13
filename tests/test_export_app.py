"""Datei-Exporte aus PunchBuddy als Auftrag (Plan PunchBuddy-Übernahme, Schritt 5)."""
import itertools
import threading

import pytest

from fakes import FakeEngine
from tonmischung.app import export_app
from tonmischung.app.bridge import Bridge
from tonmischung.app.jobs import JobRunner
from tonmischung.config.settings import Settings, load_schema
from tonmischung.ptsl.client import PtslClient
from tonmischung.punch.commands import PunchCommands
from tonmischung.punch.core import PunchCore


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = Settings(local_path=tmp_path / "e.json")
    core = PunchCore(PtslClient(engine_factory=lambda: FakeEngine()), settings, load_schema())
    restore = core.install()
    runner = JobRunner(Bridge(emit=lambda p: None, inline=True))
    uhr = itertools.count(0, 10)
    cmds = PunchCommands(core, settings, busy=runner.running_title, spawn=lambda fn, *a: fn(*a), clock=lambda: next(uhr))
    export_app.add_export_commands(cmds, core, runner)
    gerufen, fertig = [], threading.Event()
    for fn in ("run_wav_export_standalone", "run_aaf_export_standalone", "run_aaf_reference_export_standalone"):
        def fake(tracks, pb, _fn=fn):
            gerufen.append((_fn, tracks, pb["loudness_tracks"], pb["language"]))
            fertig.set()
        monkeypatch.setattr(core.module("export"), fn, fake)
    yield cmds, runner, gerufen, fertig, settings
    restore()


def _warten(runner):
    for _ in range(200):
        if runner.running_title() is None:
            return
        threading.Event().wait(0.01)


def test_wav_export_als_auftrag_mit_spurtabelle(env):
    cmds, runner, gerufen, fertig, _ = env
    assert cmds.dispatch("export_wav") == (True, "export_wav queued")
    assert fertig.wait(2)
    _warten(runner)
    name, tracks, loud, lang = gerufen[0]
    assert name == "run_wav_export_standalone" and tracks[0] == "ST" and loud == ["ST"] and lang == "de"
    assert runner.current()["title"] == "WAV-Export" and runner.current()["state"] == "fertig"


def test_alias_aaf_eingebettet_und_belegung(env):
    cmds, runner, gerufen, fertig, _ = env
    blocker = threading.Event()
    runner.start("holen", "Holen: Test", [("x", 1.0)], lambda jc: blocker.wait(2))
    try:
        ok, msg = cmds.dispatch("export_aaf_embedded")
        assert not ok and "Pro Tools ist belegt (Holen: Test)" in msg and gerufen == []
    finally:
        blocker.set()
        _warten(runner)
    assert cmds.dispatch("export_aaf_embedded") == (True, "export_aaf queued")


def test_ohne_exportspuren(env):
    cmds, *_, settings = env
    settings.set_local("export.spuren_lautheit.spurtabelle", [{"pt_spur": "ST", "inhalt": "Sendeton", "export": False}])
    assert cmds.dispatch("export_wav") == (False, "export_wav: keine Exportspuren (Export → Spuren & Lautheit)")


def test_fenstertitel_ohne_alten_namen(env):
    import punchbuddy.i18n as i18n
    assert i18n.t("prog_title_wav") == "Ottilie – WAV Export"
    assert "PunchBuddy" not in i18n.t("prog_loudness_win_title")
