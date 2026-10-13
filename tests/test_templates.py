"""Persönliche Templates: Vorauswahl Standard / zuletzt je Sendung (Plan M4-Rest, Schritt 1)."""
from tonmischung.core.templates import Template, from_settings, preselect, remember

MINE = from_settings([
    {"id": "t1", "gruppe": "Jens", "pt_name": "hs 16", "anzeigename": "Heimatjournal 16 Spuren", "sendung": "Heimatjournal"},
    {"id": "t2", "gruppe": "Jens", "pt_name": "Doku", "sendung": "Doku"},
    {"id": "t3", "gruppe": "test", "pt_name": "helmut test"},
])
FOUND = [Template("test", "helmut test", "helmut test"), Template("test", "SM Session", "SM Session")]


def _pick(show, last=None, suggest=True, default="t3"):
    p = preselect(MINE, FOUND, default, last or {}, show, suggest)
    return (p.template.key, p.hint) if p else None


def test_standard_ohne_sendungsbezug():
    assert _pick("Maintower") == ("test/helmut test", "Ihr Standard")
    assert _pick("Heimatjournal", suggest=False) == ("test/helmut test", "Ihr Standard")


def test_template_mit_dieser_sendung_wie_im_prototyp():
    assert _pick("Heimatjournal") == ("Jens/hs 16", "für Heimatjournal")


def test_zuletzt_benutzt_geht_vor():
    last = {"Heimatjournal": "test/SM Session"}
    assert _pick("Heimatjournal", last) == ("test/SM Session", "zuletzt für Heimatjournal")
    assert _pick("Heimatjournal", {"Heimatjournal": "weg/gibts nicht"}) == ("Jens/hs 16", "für Heimatjournal")
    assert _pick("Doku", {"Doku": "test/helmut test"}) == ("test/helmut test", "Ihr Standard")


def test_ohne_standard_und_ohne_vorschlag_keine_vorauswahl():
    assert _pick("Maintower", default="") is None
    assert _pick("Maintower", default="gibts-nicht") is None


def test_merken_je_sendung():
    last = remember({"Doku": "Jens/Doku"}, "Heimatjournal", "test", "helmut test")
    assert last == {"Doku": "Jens/Doku", "Heimatjournal": "test/helmut test"}
    assert remember(last, "", "x", "y") == last
