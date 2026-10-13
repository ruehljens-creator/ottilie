"""Interne Fehler von Pro Tools („Assertion“): erkennen, danach keine Befehle mehr; Startfenster schließen."""
import os

import pytest

from fakes import FakeEngine
from tonmischung.ptsl.assertions import AssertionWatch, assertion_lines, short
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.dialogs import DASHBOARD_CLOSED, UNNAMED, close_dashboard, is_dashboard, make_handler
from tonmischung.ptsl.errors import ProToolsAssertionError

LINE = ('5595.269190,00103,0e0f: PtApp_Assert(Release): "inInterval.mEnd >= inInterval.mBegin && inInterval.mBegin '
        '>= 0", /Users/autobld/gitlabci/PT/release/2026/r1/ProTools/NewFileLibs/Cmn/Cmn_Interval.h Line 143, Thread: Main')
ALERT = ('4772.272323,00103,0f09: Alert: Could not complete your request because Assertion in "/Users/autobld/'
         'gitlabci/PT/release/2026/r1/ProTools/NewFileLibs/Cmn/Cmn_Interval.h", line 143.')


def test_protokollzeilen_erkennen():
    assert assertion_lines(f"a\n{LINE}\nb\n{ALERT}\n") == [LINE, ALERT]
    assert short(LINE) == "Assertion in Cmn_Interval.h, Zeile 143" == short(ALERT)
    assert short("PtApp_Assert(Release): irgendwas") == "Assertion"


def test_protokoll_fortlaufend_lesen(tmp_path):
    log = tmp_path / "Pro_Tools_2026_10_03_16_55_14.txt"
    log.write_text(f"alt\n{LINE}\n")                                   # vor dem Start der App: zählt nicht
    w = AssertionWatch(str(tmp_path))
    assert w.poll() == ("", False)
    with open(log, "a") as f:
        f.write("5590.1: Progress: Restoring tracks...\n" + LINE[:40])  # Zeile noch nicht fertig
    assert w.poll() == ("", False)
    with open(log, "a") as f:
        f.write(LINE[40:] + "\n")
    assert w.poll() == ("Assertion in Cmn_Interval.h, Zeile 143", False)
    assert w.poll() == ("", False)
    new = tmp_path / "Pro_Tools_2026_10_03_16_57_04.txt"                # Pro Tools neu gestartet
    new.write_text("Start\n")
    os.utime(new, (log.stat().st_mtime + 10,) * 2)
    assert w.poll() == ("", True)
    with open(new, "a") as f:
        f.write(ALERT + "\n")
    assert w.poll() == ("Assertion in Cmn_Interval.h, Zeile 143", False)
    assert AssertionWatch(str(tmp_path / "leer")).poll() == ("", False)


class Watch:
    def __init__(self):
        self.found, self.restarted = "", False

    def poll(self):
        r = (self.found, self.restarted)
        self.found, self.restarted = "", False
        return r


def test_nach_assertion_keine_befehle_bis_zum_neustart():
    engine = FakeEngine()
    client = PtslClient(engine_factory=lambda: engine)
    client.assert_watch = w = Watch()
    assert client.open_session().name == "Beitrag"
    n = len(engine.calls)
    w.found = "Assertion in Cmn_Interval.h, Zeile 143"
    with pytest.raises(ProToolsAssertionError, match="internen Fehler gemeldet .Assertion in Cmn_Interval.h, Zeile 143.*neu starten"):
        client.save()
    with pytest.raises(ProToolsAssertionError):                       # bleibt gesperrt, auch ohne neue Zeile
        client.engine()
    assert len(engine.calls) == n                                      # nichts ging an Pro Tools
    w.restarted = True
    client.save()
    assert engine.calls[-1] == "save_session" and client.halted == ""


def test_assertion_meldung_wird_nicht_abgewartet():
    """Früher: „bitte in Pro Tools bestätigen, danach geht es weiter“ – und danach stürzte Pro Tools ab."""
    engine = FakeEngine()
    client = PtslClient(engine_factory=lambda: engine)
    client.assert_watch = w = Watch()
    client.dialogs = lambda: [UNNAMED]
    client.dialog_wait_s, client._sleep = 600, (lambda s: setattr(w, "found", "Assertion in X.h, Zeile 1"))
    with pytest.raises(ProToolsAssertionError, match="Nach dem AAF-Import: Pro Tools hat einen internen Fehler"):
        client.guard_dialogs("Nach dem AAF-Import")


def test_startfenster_wird_geschlossen():
    for name in ("Pro Tools Studio 2026.4.1", "Pro Tools Ultimate 2026.4", "Pro Tools 2025.12", "Dashboard"):
        assert is_dashboard(name)
    for name in ("Missing Files", "Pro Tools", UNNAMED, "Pro Tools Studio", "Save Session"):
        assert not is_dashboard(name)
    scripts = []

    def run(script):
        scripts.append(script)
        return 0, "ok"
    assert close_dashboard("Pro Tools Studio 2026.4.1", run) == DASHBOARD_CLOSED
    assert 'button "Create" of w' in scripts[0] and 'click button "Cancel" of w' in scripts[0]
    assert close_dashboard("Pro Tools Studio 2026.4.1", lambda s: (0, "anders")) is None      # Knöpfe passen nicht
    handle = make_handler(lambda: "selbst_bestaetigen", run)
    assert handle(["Pro Tools Studio 2026.4.1"]) == DASHBOARD_CLOSED
    assert handle(["Save Session"]) is None                                    # Unbekanntes bleibt dem Mischer

    engine = FakeEngine()
    client = PtslClient(engine_factory=lambda: engine)
    state = {"open": True}
    client.dialogs = lambda: ["Pro Tools Studio 2026.4.1"] if state["open"] else []
    client._sleep = lambda s: None
    client.dialog_handler = lambda names: (state.update(open=False), DASHBOARD_CLOSED)[1]
    client.close()
    assert "close_session(save_on_close=True)" in engine.calls and client.dialog_events == []   # keine Warnung


def test_fortschrittsbalken_ist_keine_meldung():
    """Live 04.10.2026: Beim Anlegen aus dem Template zeigt Pro Tools kurz titellose Fortschrittsfenster – die App bat
    trotzdem „bitte in Pro Tools bestätigen“. Jetzt: ohne Knöpfe bzw. ohne Antwort = Pro Tools arbeitet."""
    from tonmischung.ptsl.dialogs import ALERT, PROGRESS, unnamed_kind
    assert unnamed_kind(lambda s: (0, "meldung")) == ALERT
    assert unnamed_kind(lambda s: (0, "fortschritt")) == PROGRESS
    assert unnamed_kind(lambda s: (124, "")) == PROGRESS                            # antwortet nicht: arbeitet
    for kind, want in ((PROGRESS, "Pro Tools arbeitet noch – es geht gleich weiter (CloseSession)"),
                       (ALERT, "Pro Tools zeigt „Meldung von Pro Tools“ – bitte in Pro Tools bestätigen, danach geht "
                               "es weiter (CloseSession)")):
        engine = FakeEngine()
        client = PtslClient(engine_factory=lambda: engine)
        seen = iter([[UNNAMED], [UNNAMED], []])
        client.dialogs = lambda: next(seen, [])
        client.unnamed_kind = lambda kind=kind: kind
        client.dialog_wait_s, client._sleep = 10, (lambda s: None)
        notes = []
        client.on_dialog = notes.append
        client.close()
        assert notes == [want, ""] and "close_session(save_on_close=True)" in engine.calls
