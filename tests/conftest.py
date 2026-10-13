import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Kein Test schreibt ins echte Home (Einstellungen, PunchBuddy-Log)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _kein_echtes_pro_tools(monkeypatch):
    """Unit-Tests erreichen Pro Tools nie (Sicherheitsregel): ohne `engine_factory` gibt es keine
    Verbindung. Nur die Integrationstests mit TONMISCHUNG_PTSL_TESTS=1 dürfen verbinden."""
    if os.environ.get("TONMISCHUNG_PTSL_TESTS") == "1":
        return
    from tonmischung.ptsl.client import PtslClient

    def gesperrt(self):
        raise RuntimeError("Test wollte das echte Pro Tools erreichen – engine_factory übergeben")
    monkeypatch.setattr(PtslClient, "_default_engine", gesperrt)
