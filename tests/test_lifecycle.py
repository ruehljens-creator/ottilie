"""Beenden: Aufräumen genau einmal, auch bei SIGTERM; App Nap aus (produktive PunchBuddy-Linie)."""
import os
import signal
import subprocess
import sys
import textwrap

import pytest

from fakes import FakeEngine
from tonmischung.app.lifecycle import QUIT_ANYWAY, QUIT_KEEP, install_shutdown, prevent_app_nap, quit_question
from tonmischung.ptsl.client import PtslClient


def test_aufraeumen_nur_einmal():
    aufgeraeumt = []
    once = install_shutdown(lambda: aufgeraeumt.append(1), signals=())
    once()
    once()
    assert aufgeraeumt == [1]


def test_fehler_beim_aufraeumen_verhindert_das_ende_nicht():
    def kaputt():
        raise OSError("Socket schon weg")
    install_shutdown(kaputt, signals=())()          # keine Ausnahme


def test_sigterm_raeumt_auf(tmp_path):
    """Eigener Prozess: SIGTERM führt das Aufräumen aus und beendet mit 0."""
    marker = tmp_path / "aufgeraeumt"
    code = textwrap.dedent(f"""
        import os, signal, sys, time
        sys.path.insert(0, {os.getcwd()!r})
        from tonmischung.app.lifecycle import install_shutdown
        install_shutdown(lambda: open({str(marker)!r}, "w").write("ok"))
        print("bereit", flush=True)
        time.sleep(30)
    """)
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "bereit"
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=10) == 0
    assert marker.read_text() == "ok"


def test_kontext_schliesst_die_ptsl_verbindung():
    from tonmischung.app.context import AppContext
    e = FakeEngine()
    client = PtslClient(engine_factory=lambda: e)
    client.version()
    ctx = AppContext.__new__(AppContext)
    ctx.ptsl, ctx.mock = client, None
    ctx.close()
    assert e.closed and client._engine is None


@pytest.mark.skipif(sys.platform != "darwin", reason="nur macOS")
def test_app_nap_activity():
    assert prevent_app_nap("Test") is not None


def test_unit_tests_erreichen_pro_tools_nicht():
    if os.environ.get("TONMISCHUNG_PTSL_TESTS") == "1":
        pytest.skip("Integrationslauf")
    with pytest.raises(Exception, match="echte Pro Tools"):
        PtslClient().version()


def test_app_nap_fuer_stream_deck_und_pro_tools_nur_wenn_noetig():
    from tonmischung.app.lifecycle import partner_app_nap_off
    gesetzt = {"com.elgato.StreamDeck": "1"}
    befehle = []

    class R:
        def __init__(self, out):
            self.stdout = out

    def run(cmd, **kw):
        befehle.append(cmd)
        if cmd[1] == "read":
            return R(gesetzt.get(cmd[2], ""))
        gesetzt[cmd[2]] = "1"
        return R("")
    assert partner_app_nap_off(run) == ["com.avid.ProTools"]
    assert ["/usr/bin/defaults", "write", "com.avid.ProTools", "NSAppSleepDisabled", "-bool", "YES"] in befehle
    assert partner_app_nap_off(run) == []


def test_beenden_fragt_nur_waehrend_eines_auftrags():
    """Review A16: läuft ein Auftrag, fragt Beenden nach; vorgewählt ist „Weiterlaufen lassen“."""
    from pathlib import Path
    assert quit_question(None) is None and quit_question("") is None
    title, text = quit_question("Lautheitskorrektur Bahnstreik")
    assert title == "Ein Auftrag läuft noch" and "„Lautheitskorrektur Bahnstreik“" in text
    app = Path(__file__).resolve().parent.parent / "tonmischung" / "app"
    main = (app / "main.py").read_text(encoding="utf-8")
    assert 'quit_button="Beenden"' not in main and "ok=QUIT_KEEP, cancel=QUIT_ANYWAY) == 1" in main
    assert '"quitApp:" if quit else "terminate:"' in (app / "window.py").read_text(encoding="utf-8")
    assert QUIT_KEEP == "Weiterlaufen lassen" and QUIT_ANYWAY == "Trotzdem beenden"
