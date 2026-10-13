from tonmischung import cli, factory
from tonmischung.vendor import use_punchbuddy


def test_punchbuddy_mit_lautheitskorrektur_eingebunden():
    use_punchbuddy()
    import punchbuddy.loudness as loudness
    # Stand mit 24-bit-Fix und True-Peak-Limiter
    assert hasattr(loudness, "_true_peak_limiter")


def test_cli_version(capsys):
    assert cli.main(["version"]) == 0
    assert "Ottilie" in capsys.readouterr().out


def test_cli_einstellungen_setzen_und_lesen(capsys):
    pfad = "system.schnittstellen.ptsl.zeitlimit_s"
    assert cli.main(["einstellungen", pfad, "45"]) == 0
    capsys.readouterr()
    assert cli.main(["einstellungen", pfad]) == 0
    assert f"{pfad} = 45  [lokal]" in capsys.readouterr().out


def test_cli_einstellungen_unbekannt(capsys):
    assert cli.main(["einstellungen", "gibt.es.nicht", "1"]) == 1
    assert "Unbekannte Einstellung" in capsys.readouterr().err


def test_cli_helmut_gegen_mock(monkeypatch, capsys):
    from tonmischung.helmut.mock import sample
    mock = sample()
    url = mock.start()
    try:
        monkeypatch.setattr(factory, "keychain_credentials", lambda service: lambda: ("test", "test"))
        monkeypatch.setattr(factory.getpass, "getuser", lambda: "jens")
        monkeypatch.setattr(factory.socket, "gethostname", lambda: "ton3-mac")
        assert cli.main(["einstellungen", "system.schnittstellen.helmut.adresse", f'"{url}"']) == 0
        assert cli.main(["einstellungen", "system.raeume_nutzer.raeume",
                         '[{"id": "r3", "name": "Ton 3", "rechnername": "ton3-mac"}]']) == 0
        capsys.readouterr()
        assert cli.main(["helmut", "liste"]) == 0
        assert "p3  Kita-Streik  [Heimatjournal]  in_mischung · A. Wolf (Ton 2)" in capsys.readouterr().out
        assert cli.main(["helmut", "uebernehmen", "p1"]) == 0
        assert "Ton 3" in capsys.readouterr().out
        assert mock.mix_value("p1", "raum") == "Ton 3"
        assert cli.main(["helmut", "uebernehmen", "p3"]) == 1
        assert "A. Wolf" in capsys.readouterr().err
        assert cli.main(["helmut", "freigeben", "p1", "--grund", "Raumwechsel"]) == 0
        assert mock.mix_value("p1", "zustand") == "zurueckgegeben"
    finally:
        mock.stop()


def test_exportspuren_aus_spurtabelle_und_ueberschrieben(tmp_path):
    from tonmischung.config.settings import Settings
    s = Settings(local_path=tmp_path / "e.json")
    assert factory.export_tracks(s) == (["ST", "A IT", "OGA", "Mus", "Spr"], ["ST"])
    assert factory.export_tracks(s, "ST, IT,OGA") == (["ST", "IT", "OGA"], ["ST"])
    assert factory.export_tracks(s, "IT") == (["IT"], [])
    s.set_local("export.spuren_lautheit.lautheit.aktiv", False)
    assert factory.export_tracks(s)[1] == []


def test_kommandozeile_holt_nicht_an_der_laufenden_app_vorbei(capsys, monkeypatch):
    """Review B29: holen/zurueckliefern über die Kommandozeile umgingen die Auftragssperre der laufenden App."""
    from tonmischung import cli
    real = cli._app_running
    assert real(lambda: []) is False and real(lambda: [4711]) is True
    monkeypatch.setattr(cli, "_app_running", lambda: True)
    for argv in (["holen", "p1"], ["zurueckliefern", "p1"]):
        assert cli.main(argv) == 1
        assert "Ottilie läuft" in capsys.readouterr().err
