"""Abbrechen-Knopf (Jens 04.10.2026): Importe am nächsten sicheren Punkt abbrechen – Helmut-Weg und Havarie."""
import threading
import time

import pytest

from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.jobs import JobRunner
from tonmischung.core.progress import Progress, Stopped

STEPS = [("Prüfen", 1), ("Session anlegen", 1), ("AAF importieren", 1)]


def _wait(runner, states, timeout=5):
    t0 = time.monotonic()
    while runner.current()["state"] not in states and time.monotonic() - t0 < timeout:
        time.sleep(0.01)
    return runner.current()


def test_schrittgrenze_stoppt():
    p = Progress(STEPS, stop_requested=lambda: True, tick_s=None)
    with pytest.raises(Stopped, match="Abgebrochen vor „Prüfen“"):
        p.step("Prüfen")


def test_abbrechen_beim_naechsten_schritt():
    runner = JobRunner(Bridge(emit=lambda p: None, inline=True))
    gate, done = threading.Event(), []

    def work(jc):
        jc.progress.step("Prüfen")
        jc.progress.step("Session anlegen")
        gate.wait(5)                                    # Session wird angelegt …
        jc.progress.step("AAF importieren")             # … hier greift der Abbruch
        done.append("importiert")
    job = runner.start("holen", "Holen: Test", STEPS, work)
    time.sleep(0.05)
    snap = runner.cancel(job["id"])
    assert snap["cancel_requested"] and snap["cancellable"] and "Wird abgebrochen" in snap["text"]
    gate.set()
    end = _wait(runner, ("fertig", "fehler", "abgebrochen"))
    assert end["state"] == "abgebrochen" and done == []
    assert end["error"].startswith("Abgebrochen vor „AAF importieren“ (zuletzt: „Session anlegen“)")
    assert "gespeichert wurde danach nichts mehr" in end["error"]
    with pytest.raises(CommandError, match="Kein laufender Auftrag"):
        runner.cancel()


def test_abbrechen_waehrend_einer_frage():
    runner = JobRunner(Bridge(emit=lambda p: None, inline=True))

    def work(jc):
        jc.progress.step("Prüfen")
        jc.ask({"kind": "triage_vorschau", "title": "Vorschau der AAF-Triage"})
        jc.progress.step("Session anlegen")
    runner.start("havarie", "Havarie: Test", STEPS, work)
    assert _wait(runner, ("frage",))["state"] == "frage"
    runner.cancel()
    end = _wait(runner, ("fertig", "fehler", "abgebrochen"))
    assert end["state"] == "abgebrochen" and end["error"].startswith("Abgebrochen bei der Frage „Vorschau der AAF-Triage“")


def test_nur_importe_sind_abbrechbar():
    runner = JobRunner(Bridge(emit=lambda p: None, inline=True))
    gate = threading.Event()
    runner.start("zurueckliefern", "Zurückliefern: Test", STEPS, lambda jc: gate.wait(5))
    assert runner.current()["cancellable"] is False
    with pytest.raises(CommandError, match="nicht abbrechen"):
        runner.cancel()
    gate.set()
    _wait(runner, ("fertig",))
