import io

import pytest

from tonmischung import cli, factory
from tonmischung.config.settings import Settings
from tonmischung.triage.placement import ClipMove, Placement, TrackCopy, TrackMove
from tonmischung.triage.report import format_placement
from tonmischung.workflow.fetch import FetchError


def test_triage_optionen_aus_den_einstellungen(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    o = factory.triage_options(s)
    assert o.threshold == 0.8 and o.preview == "nur_bei_unsicherheit"
    assert "Musik" in o.keywords["musik"] and o.use_signal
    assert factory.triage_options(s, 90).threshold == 0.9


def test_vorschau_ohne_terminal_braucht_ja(monkeypatch, capsys):
    from test_fetch_triage import _triage
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    p = Placement([TrackMove("Audio 1", "A1")])
    with pytest.raises(FetchError, match="--ja"):
        cli._confirm_triage(False)(_triage(), p)
    assert cli._confirm_triage(True)(_triage(), p) is True
    assert "Verteilung auf das Template" in capsys.readouterr().err


def test_verteilung_als_text():
    p = Placement([TrackMove("Audio 1", "A1")], [ClipMove(("A5", "A6"), ("Musik1", "Musik2"), 900300, 900500,
                                                          "musik", ())], [TrackCopy("A7", "A8")], {}, ["x"])
    text = format_placement(p)
    assert "Audio 1 → A1" in text and "A5/A6 10:00:12:00–10:00:20:00 → Musik1/Musik2 (Musik)" in text
    assert "Doppelmono: A7 → A8" in text and "Hinweis: x" in text
