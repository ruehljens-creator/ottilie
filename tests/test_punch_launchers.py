"""Stream-Deck-Launcher (Plan PunchBuddy-Übernahme, Schritt 3c)."""
import os
import plistlib
import socket
import subprocess

import pytest

from tonmischung.punch.launchers import entries, make_launcher, make_launchers


def test_eintraege_mit_presets():
    e = dict((p, l) for l, p in entries([{"name": "Magazin"}, {"name": ""}]))
    assert e["record_a"] == "Record A" and e["holen"] == "Holen (gewählter Beitrag)"
    assert e["preset 1"] == "Preset 1 – Magazin" and e["preset 2"] == "Preset 2"
    assert not any("interplay" in p or p == "import" for p in e)


def test_launcher_schickt_befehl_an_den_socket(tmp_path):
    path = f"/tmp/tm-launch-{os.getpid()}.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    try:
        apps = make_launchers(path, [{"name": "Magazin"}], dest=tmp_path)
        app = next(a for a in apps if a.name == "Ottilie Record A.app")
        with open(app / "Contents" / "Info.plist", "rb") as f:
            info = plistlib.load(f)
        assert info["CFBundleIdentifier"] == "de.ottilie.app.launcher.record-a" and info["LSUIElement"]
        p = subprocess.Popen([str(app / "Contents" / "MacOS" / "launcher")])
        srv.settimeout(5)
        conn, _ = srv.accept()
        assert conn.recv(100) == b"record_a\n"
        conn.sendall(b"OK record_a queued\n")
        conn.close()
        assert p.wait(timeout=5) == 0
    finally:
        srv.close()
        os.unlink(path)


def test_fremde_apps_werden_nicht_ersetzt(tmp_path):
    fremd = tmp_path / "Ottilie Record A.app" / "Contents"
    fremd.mkdir(parents=True)
    with open(fremd / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "com.example.fremd"}, f)
    with pytest.raises(FileExistsError, match="gehört nicht zu Ottilie"):
        make_launcher(tmp_path, "Record A", "record_a", "/tmp/x.sock")
    make_launcher(tmp_path, "Play Input", "play", "/tmp/x.sock")
    make_launcher(tmp_path, "Play Input", "play", "/tmp/x.sock")           # eigene werden ersetzt


def test_nur_sichere_befehle(tmp_path):
    with pytest.raises(ValueError):
        make_launcher(tmp_path, "Böse", "play'; rm -rf ~; '", "/tmp/x.sock")


def test_alte_tm_starter_bleiben_und_werden_gemeldet(tmp_path):
    """Jens 09.10.2026: „Ottilie …“ statt „TM …“; vorhandene „TM …“-Starter funktionieren weiter."""
    from tonmischung.punch.launchers import legacy_launchers
    old = tmp_path / "TM Play Input.app" / "Contents"
    old.mkdir(parents=True)
    with open(old / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "de.haus.tonmischung.launcher.play"}, f)
    app = make_launcher(tmp_path, "Play Input", "play", "/tmp/x.sock")
    assert app.name == "Ottilie Play Input.app" and (tmp_path / "TM Play Input.app").exists()
    assert [p.name for p in legacy_launchers(tmp_path)] == ["TM Play Input.app"]
