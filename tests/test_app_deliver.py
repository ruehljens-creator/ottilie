"""Zurückliefern über die Oberfläche – gegen Fake (Pro Tools) und Helmut-Mock."""
import itertools
import os
import time

import pytest

from test_deliver import WAIT, _engine
from tonmischung.sessions import pt_export
from tonmischung.app import deliver_app, mixes_app
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import JobRunner
from tonmischung.config.settings import Settings
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.ptsl.client import PtslClient


@pytest.fixture(autouse=True)
def _schnell(monkeypatch):
    """Wie in test_deliver: keine echten Wartezeiten, Testuhr für das Konsolidieren."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    real = pt_export.wait_for_consolidated
    monkeypatch.setattr(pt_export, "wait_for_consolidated", lambda *a, **k: real(
        *a, **{**k, **WAIT, "clock": itertools.count(0, 1).__next__}))


@pytest.fixture
def setup(tmp_path):
    e = _engine(tmp_path)
    m = MockHelmut()
    m.add_project("p1", "Bahnstreik", "Heimatjournal", state="importiert", mischer="Jens", raum="Ton 3",
                  assets=["/H/p1/Bahnstreik.aaf"])
    url = m.start()
    ctx = AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient(url, lambda: ("test", "test")),
                     PtslClient(engine_factory=lambda: e), "Jens", "Ton 3", str(tmp_path), m, "x",
                     handover_root=str(tmp_path / "Uebergabe" / "{projektId}"))
    yield ctx, JobRunner(Bridge(emit=lambda p: None, inline=True)), e, m
    m.stop()


def _wait(runner, states, timeout=20.0):
    import threading
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        cur = runner.current()
        if cur and cur["state"] in states:
            return cur
        threading.Event().wait(0.02)                 # time.sleep ist hier stillgelegt
    raise AssertionError(runner.current())


def _session(ctx, e):
    from tonmischung.core import mixstate
    c = ctx.helmut.get("p1")
    ctx.helmut.write_mix(c, mixstate.with_state(c.mix, mixstate.MIXING, session_path=e.session[1]))


def test_uebersicht_meldet_fehlende_spuren(setup):
    ctx, runner, e, m = setup
    _session(ctx, e)
    info = deliver_app.deliver_info(ctx, {"id": "p1"})
    assert info["session_open"] is True
    assert info["tracks"] == ["ST", "A IT", "OGA", "Mus", "Spr"]            # Spurtabelle der Einstellungen
    assert info["missing"] == ["A IT"]                                     # heißt im Template „IT“
    assert sorted(info["session_tracks"]) == ["IT", "Mus", "OGA", "ST", "Spr"]          # Reihenfolge wie in Pro Tools
    assert info["target"].endswith("Uebergabe/p1") and (info["lufs"], info["truepeak"]) == (-23, -3)


def test_zurueckliefern_als_auftrag(setup):
    ctx, runner, e, m = setup
    _session(ctx, e)
    job = deliver_app.start_deliver(ctx, runner, {"id": "p1", "tracks": "ST, IT, OGA, Mus, Spr"})
    assert job["title"] == "Zurückliefern: Bahnstreik"
    done = _wait(runner, {"fertig", "fehler"})
    assert done["state"] == "fertig", done["error"]
    assert sorted(done["result"]["wavs"]) == sorted(f"{t}.wav" for t in ["ST", "IT", "OGA", "Mus", "Spr"])
    assert os.path.isdir(done["result"]["folder"]) and "Uebergabe/p1" in done["result"]["folder"]
    assert m.mix_value("p1", "zustand") == "ausgeliefert"                  # Jens 09.10.2026
    (mix,) = mixes_app.list_mixes(ctx, {})["eintraege"]                     # Verzeichnis der Mischungen
    assert (mix["art"], mix["titel"], mix["beitrag"], mix["ordner"]) == (
        "zurückgeliefert", "Bahnstreik", "p1", done["result"]["folder"]) and len(mix["dateien"]) == 5


def test_ohne_spuren(setup):
    ctx, runner, e, m = setup
    ctx.settings.set_local("export.spuren_lautheit.spurtabelle", [])
    with pytest.raises(CommandError, match="Keine Exportspuren"):
        deliver_app.start_deliver(ctx, runner, {"id": "p1"})
