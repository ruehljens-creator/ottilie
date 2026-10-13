"""Protokolldatei der App (Lehre aus PunchBuddy: volles Protokoll mit Millisekunden)."""
import logging
import re

from tonmischung.app import logfile


def test_protokoll_mit_millisekunden_im_home(tmp_path):
    root = logging.getLogger()
    vorher = list(root.handlers), root.level
    try:
        path = logfile.setup()
        assert str(path).startswith(str(tmp_path))                      # conftest: HOME im Test-Ordner
        logging.getLogger("tonmischung.test").info("Probe %s", 42)
        for h in root.handlers:
            h.flush()
        line = path.read_text().splitlines()[-1]
        assert re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} INFO    tonmischung.test: Probe 42$", line)
        logfile.setup()                                                  # zweimal: kein doppelter Handler
        assert sum(getattr(h, "_tonmischung", False) for h in root.handlers) == 1
    finally:
        for h in list(root.handlers):
            if h not in vorher[0]:
                root.removeHandler(h)
                h.close()
        root.setLevel(vorher[1])


def test_punchbuddy_import_nimmt_das_protokoll_nicht_weg():
    import sys
    from tonmischung.vendor import import_isolated
    root = logging.getLogger()
    eigener = logging.NullHandler()
    root.addHandler(eigener)
    try:
        sys.modules.pop("punchbuddy.log", None)
        import_isolated("punchbuddy.log")                  # leert in PunchBuddy root.handlers
        assert eigener in root.handlers
    finally:
        root.removeHandler(eigener)


def test_schluessel_stehen_nicht_im_protokoll(tmp_path):
    """Review A18: mit TM_DEBUG landeten Webtrigger-Adressen samt Schlüssel im Protokoll (und im Diagnosebericht)."""
    root = logging.getLogger()
    vorher = list(root.handlers), root.level
    try:
        path = logfile.setup(debug=True)
        log = logging.getLogger("tonmischung.test")
        log.debug("Fenster → Kern: %s", '{"cmd":"kopieren","args":{"text":"http://ton3:8899/play?token=Zx9q\\tPlay"}}')
        log.info("Helmut: Authorization: Bearer eyJh.bG.ci")
        try:
            raise ValueError("http://x/import?token=geheim")
        except ValueError:
            log.exception("Webtrigger")
        for h in root.handlers:
            h.flush()
        text = path.read_text()
        assert "Zx9q" not in text and "eyJh" not in text and "geheim" not in text
        assert text.count("••••") == 4                     # Ausnahme zweimal: Quelltextzeile und Meldung im Traceback
        from tonmischung.diagnose import app_log
        path.write_text(path.read_text() + "\nalt: GET /stop?token=vorher\n")
        assert "vorher" not in app_log() and "token=••••" in app_log()
    finally:
        for h in list(root.handlers):
            if h not in vorher[0]:
                root.removeHandler(h)
                h.close()
        root.setLevel(vorher[1])
