from tonmischung.core.identity import current_mixer, short_host, this_room

RAEUME = [{"id": f"r{i}", "name": f"Ton {i}", "rechnername": f"ton{i}-mac"} for i in range(1, 7)]
NUTZER = [{"id": "u1", "name": "Jens", "mac_benutzer": "jens"},
          {"id": "u2", "name": "A. Wolf", "mac_benutzer": "awolf"},
          {"id": "u3", "name": "T. Nguyen", "mac_benutzer": "tnguyen"}]


def test_raum_aus_rechnername():       # Logik-Referenz: ton3-mac ergibt Ton 3
    assert this_room(RAEUME, "ton3-mac") == "Ton 3"
    assert this_room(RAEUME, "TON3-MAC.example.org") == "Ton 3"
    assert this_room(RAEUME, "fremder-mac") == ""


def test_raum_fest():
    assert this_room(RAEUME, "ton3-mac", mode="fest", fixed="r5") == "Ton 5"


def test_mischer_aus_mac_benutzer_oder_manuell():
    assert current_mixer(NUTZER, "awolf") == "A. Wolf"
    assert current_mixer(NUTZER, "gast") == "gast"
    assert current_mixer(NUTZER, "jens", mode="manuell", manual="u3") == "T. Nguyen"


def test_kurzname():
    assert short_host("ton1-mac.local") == "ton1-mac"
