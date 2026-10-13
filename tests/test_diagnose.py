"""Diagnosebericht (Plan PunchBuddy-Übernahme, Schritt 6b) – ohne Pro Tools und ohne Netz."""

from fakes import FakeEngine
from tonmischung import diagnose
from tonmischung.config.settings import Settings
from tonmischung.ptsl.client import PtslClient


def test_bericht_vollstaendig_und_robust(tmp_path, monkeypatch):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local("system.streamdeck_webtrigger.port", 1)          # Webtrigger nicht erreichbar → Befund
    monkeypatch.setattr(diagnose, "vocaster", lambda: "kein Vocaster erkannt")
    from tonmischung.app import logfile
    logfile.log_dir().mkdir(parents=True, exist_ok=True)
    logfile.log_path().write_text("2026-10-02 22:00:00.123 INFO    tonmischung.app: Zeile eins\n")
    gerufen = []

    def run(cmd):
        gerufen.append(cmd[0])
        if "networksetup" in cmd[0]:
            raise RuntimeError("Befehl kaputt")              # ein Abschnitt scheitert – Bericht geht weiter
        return "Ausgabe"
    text = diagnose.report(s, "Test", PtslClient(engine_factory=lambda: FakeEngine()), run=run)
    for titel in ("Pro Tools", "Webtrigger und Stream-Deck-Socket", "Video-Satellite 28282/28284",
                  "App Nap", "Focusrite Vocaster", "Ottilie-Protokoll (vollständig)", "Einstellungen"):
        assert titel in text
    assert "Abschnitt fehlgeschlagen: Befehl kaputt" in text
    assert "PTSL-Antwortzeit transport_state (10×)" in text
    assert "Zeile eins" in text and "Webtrigger Port 1:" in text
    assert "nur an Jens bzw. die IT weitergeben" in text


def test_bericht_als_datei(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnose, "report", lambda *a, **k: "Inhalt")
    p = diagnose.write_report(Settings(local_path=tmp_path / "e.json"), "Test", folder=tmp_path / "ziel")
    assert p.name.startswith("Ottilie_Diagnose_") and p.read_text() == "Inhalt"
