"""Umbenennung in Ottilie (Jens 08.10.2026): Ablageorte, einmalige Datenübernahme, Schlüsselbund von vorher."""
import os
import subprocess
import types

from tonmischung import paths
from tonmischung.config import keychain
from tonmischung.config.settings import Settings


def test_daten_einmalig_kopieren(tmp_path):
    old = paths.legacy_support_dir(tmp_path)
    (old / "Nutzer" / "jens").mkdir(parents=True)
    (old / "Nutzer" / "jens" / "nutzer.json").write_text('{"name": "Jens"}')
    (old / "einstellungen.json").write_text("{}")
    (old / "api.sock").write_text("")                                   # gehört zum laufenden Prozess
    msg = paths.migrate(tmp_path)
    new = paths.support_dir(tmp_path)
    assert "übernommen" in msg and new.name == "Ottilie"
    assert (new / "Nutzer" / "jens" / "nutzer.json").read_text() == '{"name": "Jens"}'
    assert not (new / "api.sock").exists() and not (tmp_path / "Library" / "Application Support" / "Ottilie.kopie").exists()
    assert (old / "einstellungen.json").exists() and (old / "Nutzer" / "jens" / "nutzer.json").exists()   # alt bleibt
    (old / "spaeter.json").write_text("{}")
    assert paths.migrate(tmp_path) == "" and not (new / "spaeter.json").exists()     # nur einmal


def test_ohne_alten_ordner_nichts_zu_tun(tmp_path):
    assert paths.migrate(tmp_path) == "" and not paths.support_dir(tmp_path).exists()


def test_abgebrochene_kopie_wird_ersetzt(tmp_path):
    old = paths.legacy_support_dir(tmp_path)
    old.mkdir(parents=True)
    (old / "einstellungen.json").write_text("{}")
    half = tmp_path / "Library" / "Application Support" / "Ottilie.kopie"
    half.mkdir()
    (half / "halb").write_text("")
    assert paths.migrate(tmp_path) and not (paths.support_dir(tmp_path) / "halb").exists()


def test_gescheiterte_uebernahme_haelt_den_start_nicht_auf(tmp_path, monkeypatch):
    """Review B10: ein Fehler beim Kopieren ließ die App nicht starten (launchd startete sie im Kreis)."""
    old = paths.legacy_support_dir(tmp_path)
    old.mkdir(parents=True)
    (old / "nutzer.json").write_text("{}")

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(paths.shutil, "copytree", full)
    text = paths.migrate(tmp_path)
    assert "nicht übernommen" in text and "No space left" in text and paths.LAST == text
    assert not paths.support_dir(tmp_path).exists() and (old / "nutzer.json").exists()


def test_gleichzeitiger_start_kopiert_nur_einmal(tmp_path):
    import threading
    old = paths.legacy_support_dir(tmp_path)
    old.mkdir(parents=True)
    for i in range(200):
        (old / f"d{i}.json").write_text("{}")
    results = []
    threads = [threading.Thread(target=lambda: results.append(paths.migrate(tmp_path))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(bool(r) for r in results) == 1 and all("nicht übernommen" not in r for r in results)
    assert len(list(paths.support_dir(tmp_path).iterdir())) == 200


def test_einstellungen_folgen_dem_home_zur_laufzeit(tmp_path, monkeypatch):
    """Früher beim Laden des Moduls festgelegt – Tests schrieben dann in die echte Datei des Macs."""
    monkeypatch.setenv("HOME", str(tmp_path))
    s = Settings()
    assert s.local_path == tmp_path / "Library" / "Application Support" / "Ottilie" / "einstellungen.json"
    assert Settings(local_path=None).local_path is None


def test_schluesselbund_eintrag_von_vorher(monkeypatch):
    store = {("tonmischung-webtrigger", "webtrigger"): "alt", ("ottilie-helmut", "jens"): "neu"}

    def fake_run(cmd, **kw):
        key = (cmd[cmd.index("-s") + 1], cmd[cmd.index("-a") + 1])
        return types.SimpleNamespace(returncode=0 if key in store else 44, stdout=store.get(key, "") + "\n")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert keychain.read_secret("ottilie-helmut", "jens") == "neu"
    assert keychain.read_secret("ottilie-webtrigger", "webtrigger") == "alt"           # Rückfall auf den alten Namen
    assert keychain.read_secret("eigener-eintrag", "x") == "" and keychain.legacy_service("eigener") == ""


def test_kein_zugriff_ist_nicht_fehlt(monkeypatch):
    """Review B14: gesperrter Schlüsselbund oder verweigerter Zugriff galt als „fehlt“ – der Webtrigger erzeugte
    dann einen neuen Schlüssel, und alle Stream-Deck-Tasten waren ungültig."""
    import pytest
    from tonmischung.app.triggers_app import ensure_token
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=51, stdout="", stderr="User interaction is not allowed."))
    with pytest.raises(keychain.KeychainError, match="nicht lesbar"):
        keychain.read_secret("ottilie-webtrigger", "webtrigger")
    written = []
    settings = types.SimpleNamespace(get=lambda k: {"system.streamdeck_webtrigger.token_schluesselbund": "ottilie-webtrigger",
                                                     "system.streamdeck_webtrigger.bind_adresse": "netzwerk"}.get(k))
    with pytest.raises(RuntimeError, match="nicht lesbar"):
        ensure_token(settings, write=lambda *a: written.append(a))
    assert written == []                                                  # kein neuer Schlüssel

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(keychain.KeychainError, match="antwortet nicht"):
        keychain.read_secret("ottilie-webtrigger", "webtrigger")
    from tonmischung.helmut.client import HelmutError, keychain_credentials
    with pytest.raises(HelmutError, match="antwortet nicht"):
        keychain_credentials("ottilie-helmut")()


def test_name_und_kennungen():
    from tonmischung.core import launchagent, peers
    from tonmischung.delivery import transfer
    assert (paths.APP_NAME, paths.BUNDLE_ID, launchagent.LABEL) == ("Ottilie", "de.ottilie.app", "de.ottilie.app")
    assert peers.SERVICE_TYPE == "_ottilie._tcp." and transfer.HAVARIE_FOLDER == "Ottilie Havarie"
    assert os.path.basename(str(paths.log_dir())) == "Ottilie"
