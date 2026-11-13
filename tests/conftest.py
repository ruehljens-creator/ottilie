import os
import platform

import pytest

# Selbst gebautes bmx (tools/build_bmx.sh) wie im Paket bevorzugen – sonst fehlen bmxtranswrap/mxf2raw für die
# Sendefile-Tests (Mac mini, 11.11.2026)
_BMX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build",
                    f"bmx-{platform.machine()}", "bin")
if os.path.isdir(_BMX) and _BMX not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = _BMX + os.pathsep + os.environ.get("PATH", "")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Kein Test schreibt ins echte Home (Einstellungen, PunchBuddy-Log) und keiner liest die echte Kopplung mit der
    Zentrale (System-Schlüsselbund) – stattdessen eine Datei im Testordner."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TONMISCHUNG_ZENTRALE_GERAET", str(tmp_path / "zentrale-geraet.json"))


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
