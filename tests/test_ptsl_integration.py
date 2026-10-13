"""Gegen das echte Pro Tools – nur mit TONMISCHUNG_PTSL_TESTS=1.

Speichern nur, wenn die offene Session im Testordner TONMISCHUNG_TESTORDNER liegt.
"""
import os

import pytest

from tonmischung.ptsl.client import PtslClient

pytestmark = pytest.mark.skipif(os.environ.get("TONMISCHUNG_PTSL_TESTS") != "1",
                                reason="PTSL-Integrationstests nur mit TONMISCHUNG_PTSL_TESTS=1")


def test_lesen():
    client = PtslClient()
    assert client.version() > 0
    client.open_session()
    assert isinstance(client.transport_running(), bool)


def test_speichern_nur_in_testordner():
    testordner = os.environ.get("TONMISCHUNG_TESTORDNER")
    if not testordner:
        pytest.skip("TONMISCHUNG_TESTORDNER nicht gesetzt")
    client = PtslClient()
    session = client.open_session()
    if session is None or not os.path.realpath(session.path).startswith(os.path.realpath(testordner)):
        pytest.skip("offene Session liegt nicht im Testordner")
    client.save()
