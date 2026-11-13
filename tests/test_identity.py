from tonmischung.core.identity import current_mixer, short_host

NUTZER = [{"id": "u1", "name": "Jens", "mac_benutzer": "jens"},
          {"id": "u2", "name": "A. Wolf", "mac_benutzer": "awolf"},
          {"id": "u3", "name": "T. Nguyen", "mac_benutzer": "tnguyen"}]


def test_mischer_aus_mac_benutzer_oder_manuell():
    assert current_mixer(NUTZER, "awolf") == "A. Wolf"
    assert current_mixer(NUTZER, "gast") == "gast"
    assert current_mixer(NUTZER, "jens", mode="manuell", manual="u3") == "T. Nguyen"


def test_kurzname():
    assert short_host("ton1-mac.local") == "ton1-mac"
