"""Ein Ort für Timecode-Rechnung (Review C)."""
from tonmischung.core.timecode import frames_to_tc, tc_parts, tc_seconds, tc_to_frames


def test_formate():
    assert tc_parts("10:00:05:12.00") == (10, 0, 5, 12) == tc_parts("10;00;05;12")
    assert tc_to_frames("10:00:00:00") == 900000 and tc_to_frames("00:00:01:00", 50) == 50
    assert frames_to_tc(900000 + 25 * 5 + 12) == "10:00:05:12"
    assert frames_to_tc(24 * 3600 * 25 + 1) == "00:00:00:01"                  # nach 24 h wieder ab 00
    assert tc_seconds("10:00:05:12.00") == 36005.48
    for f in (0, 1, 24, 25, 89999, 2159999):
        assert tc_to_frames(frames_to_tc(f)) == f


def test_bildrate_lesen():
    from tonmischung.core.timecode import parse_rate
    assert parse_rate("25/1") == 25.0 and round(parse_rate("30000/1001"), 3) == 29.97 and parse_rate("50") == 50.0
    assert parse_rate(None) == 0.0 and parse_rate("x/0", 25.0) == 25.0 and parse_rate("1/0") == 0.0
