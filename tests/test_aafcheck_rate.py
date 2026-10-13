"""Bildraten im AAF: DaVinci Resolve schreibt 50 fps und Ton in Samples (live 04.10.2026)."""

import pytest

from tonmischung import devlocal
from tonmischung.triage import aafcheck as A

DAVINCI = devlocal.real_file("davinci_aaf")                       # echte Datei, siehe testfiles/entwicklung.json


def test_auf_andere_bildrate_umrechnen():
    info = A.AafInfo(True, "AAF lesbar", 180000, 50, ("A1",), {"A1": ((180000, 181010), (180003, 180005))})
    b = A.at_rate(info, 25)
    assert (b.fps, b.start_frames, b.start_timecode) == (25, 90000, "01:00:00:00")
    assert b.clips == {"A1": ((90000, 90505), (90001, 90002))}                         # abgerundet
    assert A.at_rate(b, 25) is b and A.at_rate(A.AafInfo(False, "kaputt"), 25).ok is False


@pytest.mark.skipif(DAVINCI is None, reason="Testdatei „davinci_aaf“ nicht da (testfiles/entwicklung.json)")
def test_davinci_ton_in_samples():
    a = A.check_aaf(DAVINCI)
    assert (a.fps, a.start_timecode) == (50, "01:00:00:00")
    assert a.clips["Audio 1"] == ((180000, 181010),)                                  # 20,20 s, nicht 5,4 Stunden
    assert A.at_rate(a, 25).clips["Audio 3"] == ((90000, 90388),)                    # 15,52 s
