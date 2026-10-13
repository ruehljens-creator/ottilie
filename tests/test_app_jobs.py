import threading
import time

import pytest

from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.jobs import Cancelled, JobRunner

STEPS = [("Prüfen", 1), ("Import", 3)]


def _runner():
    events = []
    lock = threading.Lock()

    def emit(p):
        with lock:
            events.append(p)
    return JobRunner(Bridge(emit=emit, inline=True)), events


def _wait(runner, state, timeout=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        cur = runner.current()
        if cur and cur["state"] == state:
            return cur
        time.sleep(0.01)
    raise AssertionError(f"Auftrag nicht im Zustand {state}: {runner.current()}")


def test_auftrag_mit_fortschritt_und_frage():
    runner, events = _runner()

    def work(ctx):
        ctx.progress.step("Prüfen", "AAF")
        answer = ctx.ask({"kind": "open_session", "text": "Speichern und schließen?"})
        if answer != "ja":
            raise Cancelled("nein")
        ctx.progress.step("Import")
        ctx.progress.update(0.5, "Hälfte")
        return {"clips": 8}
    runner.start("holen", "Holen: Test", STEPS, work)
    q = _wait(runner, "frage")
    assert q["question"]["kind"] == "open_session"
    assert [s["state"] for s in q["steps"]] == ["run", "wait"]
    runner.answer(q["question"]["id"], "ja")
    done = _wait(runner, "fertig")
    assert done["result"] == {"clips": 8}
    assert [s["state"] for s in done["steps"]] == ["done", "done"]
    names = {e["name"] for e in events if e["type"] == "event"}
    assert names == {"job"}
    with pytest.raises(CommandError, match="nicht mehr offen"):
        runner.answer(q["question"]["id"], "ja")


def test_immer_nur_ein_auftrag_und_fehler():
    runner, _ = _runner()
    go = threading.Event()
    runner.start("holen", "A", STEPS, lambda ctx: go.wait(2))
    with pytest.raises(CommandError, match="läuft schon ein Auftrag: A"):
        runner.start("holen", "B", STEPS, lambda ctx: None)
    go.set()
    _wait(runner, "fertig")

    def boom(ctx):
        ctx.progress.step("Import")
        raise RuntimeError("Pro Tools weg")
    runner.start("holen", "C", STEPS, boom)
    err = _wait(runner, "fehler")
    assert err["error"] == "Pro Tools weg"
    assert [s["state"] for s in err["steps"]] == ["done", "error"]


def test_abbruch_in_der_frage():
    runner, _ = _runner()

    def work(ctx):
        if ctx.ask({"kind": "x"}) is False:
            raise Cancelled("Vom Mischer abgebrochen")
    runner.start("holen", "D", STEPS, work)
    q = _wait(runner, "frage")
    runner.answer(q["question"]["id"], False)
    c = _wait(runner, "abgebrochen")
    assert c["error"] == "Vom Mischer abgebrochen"


def test_dialoghinweise_in_jedem_auftrag():
    """Review B21: „Pro Tools zeigt … – bitte bestätigen“ erschien nur beim Holen."""
    from fakes import FakeEngine
    from tonmischung.ptsl.client import PtslClient
    runner, events = _runner()
    runner.ptsl = PtslClient(engine_factory=lambda: FakeEngine())
    seen = []

    def work(jc):
        jc.progress.step("Export")
        runner.ptsl.on_dialog("Pro Tools zeigt „Missing Files“ – bitte in Pro Tools bestätigen")
        seen.append(jc.progress.text())
        runner.ptsl.on_dialog("")                                    # bestätigt: Text bleibt bis zum nächsten Schritt
        return {}
    runner.start("zurueckliefern", "Zurückliefern: Test", [("Export", 1)], work)
    _wait(runner, "fertig")
    assert "Missing Files" in seen[0] and runner.ptsl.on_dialog is None


def test_antworten_werden_gegen_die_moeglichkeiten_geprueft():
    """Review B26: über MCP kam „false“ als Text – und ein nicht leerer Text galt als Ja."""
    from tonmischung.app.jobs import checked_answer
    q = {"options": [{"value": True, "label": "Speichern und weiter"}, {"value": False, "label": "Abbrechen"}]}
    assert checked_answer(q, "false") is False and checked_answer(q, "true") is True and checked_answer(q, False) is False
    for wrong in ("vielleicht", 1, 0, None):
        with pytest.raises(CommandError, match="passt nicht"):
            checked_answer(q, wrong)
    assert checked_answer({"options": []}, {"A1": "A3"}) == {"A1": "A3"}       # fehlende Spuren: freie Antwort
    runner, _ = _runner()
    box = []
    runner.start("holen", "Holen: Test", [("Prüfen", 1)], lambda jc: box.append(jc.ask({"title": "Frage", **q})))
    cur = _wait(runner, "frage")
    with pytest.raises(CommandError):
        runner.answer(cur["question"]["id"], "vielleicht")
    runner.answer(cur["question"]["id"], "false")
    _wait(runner, "fertig")
    assert box == [False]
