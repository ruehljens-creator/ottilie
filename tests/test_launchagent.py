"""LaunchAgent (M9): Start bei der Anmeldung, Neustart nur nach Absturz."""
import plistlib

from tonmischung.core import launchagent


def test_plist():
    d = plistlib.loads(launchagent.plist("/Applications/Ottilie.app/"))
    assert d["Label"] == "de.ottilie.app" and d["RunAtLoad"] is True
    assert d["ProgramArguments"] == ["/Applications/Ottilie.app/Contents/MacOS/Ottilie", "app"]
    assert d["KeepAlive"] == {"SuccessfulExit": False} and d["LimitLoadToSessionType"] == ["Aqua"]
