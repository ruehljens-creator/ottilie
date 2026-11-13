"""Tests der Zentrale: ohne Netz, ohne Docker (Starlette-TestClient, WebSocket im Prozess)."""
import os
import sys

import pytest

# Wie im Abbild: die Übergabe-Regeln aus Ottilie (tonmischung/dropfolder/handover.py) liegen neben der Zentrale
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(_REPO, "tonmischung")) and _REPO not in sys.path:
    sys.path.insert(0, _REPO)
from starlette.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(tmp_path, clock):
    return create_app(str(tmp_path / "daten"), clock=clock, background=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def ctx(app):
    return app.state.ctx
