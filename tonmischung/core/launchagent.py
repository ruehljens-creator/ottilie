"""LaunchAgent für Ottilie (Auftrag M9) – reine Logik: die plist, die die Geräteverwaltung nach
/Library/LaunchAgents legt.

Startet die App bei der Anmeldung im Fenster-Kontext (Aqua). Nach einem Absturz startet launchd sie neu
(`KeepAlive: SuccessfulExit = false`); wer die App bewusst beendet (Exit 0), bleibt ohne Neustart. Der eigene Watchdog
der App bleibt daneben für „hängt, reagiert nicht“.
"""
from __future__ import annotations

import plistlib

from tonmischung.paths import BUNDLE_ID

LABEL = BUNDLE_ID
DEFAULT_APP = "/Applications/Ottilie.app"


def plist(app_path: str = DEFAULT_APP, extra_args: tuple[str, ...] = ()) -> bytes:
    exe = app_path.rstrip("/") + "/Contents/MacOS/Ottilie"
    data = {
        "Label": LABEL,
        "ProgramArguments": [exe, "app", *extra_args],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "LimitLoadToSessionType": ["Aqua"],
        "ProcessType": "Interactive",
        "ThrottleInterval": 30,
    }
    return plistlib.dumps(data, sort_keys=False)
