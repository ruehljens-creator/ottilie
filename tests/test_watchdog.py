"""Watchdog: nur Tonmischung finden, erst SIGTERM, dann SIGKILL (Plan PunchBuddy-Übernahme, Schritt 6)."""
import signal

from tonmischung.watchdog import find_app_pids, stop_app

PS = """  101 /usr/bin/python3 -m tonmischung app --mock
  102 /usr/bin/python3 -m tonmischung watchdog --mock
  103 /Applications/Tonmischung.app/Contents/MacOS/Tonmischung
  104 /Applications/Pro Tools.app/Contents/MacOS/Pro Tools
  105 /Applications/PunchBuddy.app/Contents/MacOS/PunchBuddy
  106 /usr/bin/python3 -m tonmischung mcp
"""


def test_nur_tonmischung_app():
    assert find_app_pids(PS, own_pid=102) == [101, 103]
    assert find_app_pids(PS, own_pid=101) == [103]


def test_mcp_server_und_kommandozeile_bleiben_unberuehrt():
    """Review A19: der Pfad des Pakets allein reicht nicht – `Ottilie mcp` oder `Ottilie holen` sind nicht die App."""
    from tonmischung.watchdog import WATCHDOG, find_pids, role
    ps = """  201 /Applications/Ottilie.app/Contents/MacOS/Ottilie
  202 /Applications/Ottilie.app/Contents/MacOS/Ottilie app --mock
  203 /Applications/Ottilie.app/Contents/MacOS/Ottilie mcp
  204 /Applications/Ottilie.app/Contents/MacOS/Ottilie holen 4711
  205 /Applications/Ottilie.app/Contents/MacOS/Ottilie watchdog
  206 /usr/bin/python3 -m tonmischung nachtragen
  207 /usr/bin/python3 -m http.server 8731 --directory tonmischung/app/ui
  208 /usr/bin/python3 -m tonmischung.mcp_server
"""
    assert find_app_pids(ps, own_pid=1) == [201, 202]
    assert find_pids(ps, own_pid=205, wanted=WATCHDOG) == [] and find_pids(ps, own_pid=1, wanted=WATCHDOG) == [205]
    assert role("/x/Ottilie.app/Contents/MacOS/Ottilie mcp") == "mcp" and role("/bin/zsh") is None


def test_fremde_prozesse_brechen_den_watchdog_nicht_ab():
    def kill(pid, sig):
        raise PermissionError(1, "Operation not permitted")
    assert stop_app([301], kill=kill, sleep=lambda s: None) == []


def test_erst_sigterm_dann_sigkill():
    signale, lebt = [], {201: True, 202: True}

    def kill(pid, sig):
        signale.append((pid, sig))
        if sig == signal.SIGTERM and pid == 201:
            lebt[201] = False                         # beendet sich sauber
        if sig == signal.SIGKILL:
            lebt[pid] = False
    hart = stop_app([201, 202], kill=kill, alive=lambda p: lebt[p], wait_s=1.0, sleep=lambda s: None)
    assert hart == [202]
    assert (201, signal.SIGTERM) in signale and (202, signal.SIGKILL) in signale and (201, signal.SIGKILL) not in signale


def test_aufruf_aus_quelltext_und_paket():
    from tonmischung.watchdog import self_command
    assert self_command("app", "--mock", frozen=False, executable="/usr/bin/python3") == \
        ["/usr/bin/python3", "-m", "tonmischung", "app", "--mock"]
    exe = "/Applications/Tonmischung.app/Contents/MacOS/Tonmischung"
    assert self_command("watchdog", frozen=True, executable=exe) == [exe, "watchdog"]
