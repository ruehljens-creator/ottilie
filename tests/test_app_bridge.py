import os
import subprocess
import sys

import pytest

from fakes import FakeEngine
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.commands import status
from tonmischung.app.context import AppContext, dev_mock
from tonmischung.config.settings import Settings
from tonmischung.helmut.client import HelmutClient
from tonmischung.ptsl.client import PtslClient


def _bridge():
    out = []
    return Bridge(emit=out.append, inline=True), out


def test_befehl_antwort_und_fehler():
    b, out = _bridge()
    b.register("plus", lambda a: a["x"] + 1)
    b.register("nein", lambda a: (_ for _ in ()).throw(CommandError("Geht nicht")))
    b.register("kaputt", lambda a: 1 / 0)
    b.handle('{"id": 1, "cmd": "plus", "args": {"x": 1}}')
    b.handle({"id": 2, "cmd": "nein"})
    b.handle({"id": 3, "cmd": "kaputt"})
    b.handle({"id": 4, "cmd": "gibtsnicht"})
    b.handle("kein json")
    assert out == [
        {"type": "reply", "id": 1, "ok": True, "result": 2},
        {"type": "reply", "id": 2, "ok": False, "error": "Geht nicht"},
        {"type": "reply", "id": 3, "ok": False, "error": "division by zero"},
        {"type": "reply", "id": 4, "ok": False, "error": "Unbekannter Befehl: gibtsnicht"},
    ]


def test_ereignis_und_kaputtes_fenster():
    b, out = _bridge()
    b.event("fortschritt", {"p": 0.5})
    assert out == [{"type": "event", "name": "fortschritt", "data": {"p": 0.5}}]
    bad = Bridge(emit=lambda p: 1 / 0, inline=True)
    bad.register("x", lambda a: 1)
    bad.handle({"id": 1, "cmd": "x"})                 # Fehler beim Senden wird protokolliert, nicht geworfen


def test_status_mit_mock_und_fake(tmp_path):
    m = dev_mock()
    url = m.start()
    try:
        ctx = AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient(url, lambda: ("test", "test")),
                         PtslClient(engine_factory=lambda: FakeEngine()), "Jens", "Ton 3", str(tmp_path), m, "x")
        st = status(ctx)
    finally:
        m.stop()
    assert st["pro_tools"] == {"ok": True, "session": "Beitrag"}
    assert st["helmut"] == {"ok": True, "mock": True}
    assert (st["mixer"], st["room"]) == ("Jens", "Ton 3")


@pytest.mark.skipif(os.environ.get("TONMISCHUNG_UI_TESTS") != "1", reason="Fenster nur mit TONMISCHUNG_UI_TESTS=1")
def test_fenster_selbsttest():
    os.makedirs(os.environ["HOME"], exist_ok=True)   # WebKit startet nicht mit fehlendem HOME (live)
    r = subprocess.run([sys.executable, "-m", "tonmischung", "app", "--mock", "--selbsttest"],
                       capture_output=True, text=True, timeout=90)
    assert "SELBSTTEST OK" in r.stdout, r.stdout + r.stderr


@pytest.mark.skipif(os.environ.get("TONMISCHUNG_UI_TESTS") != "1" or os.environ.get("TONMISCHUNG_PTSL_TESTS") != "1",
                    reason="Autopilot gegen Pro Tools nur mit TONMISCHUNG_UI_TESTS=1 und TONMISCHUNG_PTSL_TESTS=1")
def test_fenster_autopilot_holen():
    """Ganzer Ablauf im echten Fenster gegen Pro Tools (Testordner) und Helmut-Mock."""
    import pwd
    os.makedirs(os.environ["HOME"], exist_ok=True)
    # Das Home ist umgeleitet; die Templates von Pro Tools liegen im echten Home – nur lesen, daher ein Link
    real = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, "Documents", "Pro Tools", "Session Templates")
    link = os.path.join(os.environ["HOME"], "Documents", "Pro Tools", "Session Templates")
    os.makedirs(os.path.dirname(link), exist_ok=True)
    os.symlink(real, link)
    r = subprocess.run([sys.executable, "-m", "tonmischung", "app", "--mock", "--selbsttest", "--autopilot", "holen:tw"],
                       capture_output=True, text=True, timeout=900)
    assert "SELBSTTEST OK" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]

