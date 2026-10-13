import io

import pytest

from tonmischung.core.progress import Progress
from tonmischung.ui.progress_sinks import TerminalSink


class Aufzeichnung:
    def __init__(self):
        self.werte, self.geschlossen = [], False

    def update(self, fraction, text):
        self.werte.append((round(fraction, 3), text))

    def close(self):
        self.geschlossen = True


class Uhr:
    t = 0.0

    def __call__(self):
        return self.t


def test_gewichtete_schritte_und_teilfortschritt():
    a, uhr = Aufzeichnung(), Uhr()
    p = Progress([("A", 1), ("B", 3)], [a], clock=uhr, tick_s=None)
    p.step("A", "los")
    p.update(0.5)
    p.step("B")
    p.update(0.5, "halb")
    p.close()
    assert [w for w, _ in a.werte] == [0.0, 0.125, 0.25, 0.625, 1.0]
    assert a.werte[3][1] == "B: halb" and a.werte[-1][1] == "Fertig" and a.geschlossen


def test_lebenszeichen_zeigt_vergangene_zeit():
    a, uhr = Aufzeichnung(), Uhr()
    p = Progress([("Lautheit", 1)], [a], clock=uhr, tick_s=None, show_elapsed_after_s=3)
    p.step("Lautheit", "messen")
    assert p.text() == "Lautheit: messen"
    uhr.t = 42
    assert p.text() == "Lautheit: messen (42 s)"


def test_lebenszeichen_laeuft_im_hintergrund():
    import time
    a = Aufzeichnung()
    p = Progress([("X", 1)], [a], tick_s=0.02)
    p.step("X")
    time.sleep(0.15)
    p.close()
    assert len(a.werte) >= 4                      # ohne eigene Meldungen weiter aktualisiert


def test_unbekannter_schritt():
    with pytest.raises(KeyError):
        Progress([("A", 1)], tick_s=None).step("B")


def test_anzeigefehler_stoeren_den_ablauf_nicht():
    class Kaputt:
        def update(self, f, t):
            raise RuntimeError("Fenster weg")

        def close(self):
            raise RuntimeError("Fenster weg")
    p = Progress([("A", 1)], [Kaputt()], tick_s=None)
    p.step("A")
    p.close()


def test_terminal_ohne_tty_schreibt_eine_zeile_je_text():
    buf = io.StringIO()
    t = TerminalSink(buf, width=10)
    t.update(0.5, "Konsolidieren: 50 %")
    t.update(0.5, "Konsolidieren: 50 % (4 s)")         # nur Sekundenzähler neu → keine Zeile
    t.update(1.0, "Fertig")
    assert buf.getvalue().splitlines() == ["[#####.....]  50 %  Konsolidieren: 50 %",
                                           "[##########] 100 %  Fertig"]
