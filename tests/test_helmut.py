import pytest

from tonmischung.core.mixstate import ConflictError, MixState
from tonmischung.helmut.client import HelmutClient, HelmutError, HelmutUnavailableError
from tonmischung.helmut.mapping import find_handover
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService


@pytest.fixture
def mock():
    m = MockHelmut()
    m.add_project("p1", "Bahnstreik", "Heimatjournal",
                  assets=["/H/p1/Bahnstreik.aaf", "/H/p1/Bahnstreik_ref.mxf", "/H/p1/manifest.json"])
    m.add_project("p3", "Kita-Streik", "Heimatjournal", state="in_mischung",
                  mischer="A. Wolf", raum="Ton 2")
    m.url = m.start()
    yield m
    m.stop()


def _client(mock, user="test", password="test"):
    return HelmutClient(mock.url, lambda: (user, password), timeout_s=5, sleep=lambda s: None)


def _service(mock, mixer="Jens", room="Ton 3"):
    return MixService(_client(mock), mixer, room, now=lambda: "2026-10-02T10:00:00+00:00")


# ── Client ──────────────────────────────────────────────────────────────────
def test_beitraege_lesen(mock):
    beitraege = {b.id: b for b in _client(mock).list_for_mixing()}
    assert beitraege["p1"].mix.state == "bereit"
    assert beitraege["p3"].mix == MixState(state="in_mischung", mixer="A. Wolf", room="Ton 2")
    assert beitraege["p1"].show == "Heimatjournal"


def test_uebergabe_finden(mock):
    h = _client(mock).handover("p1")
    assert h.complete
    assert (h.aaf.path, h.video.path, h.manifest.path) == (
        "/H/p1/Bahnstreik.aaf", "/H/p1/Bahnstreik_ref.mxf", "/H/p1/manifest.json")


def test_neueste_aaf_gewinnt():
    h = find_handover([{"filepath": "/a/v1.aaf", "lastModified": "1"},
                       {"filepath": "/a/v2.aaf", "lastModified": "2"}])
    assert h.aaf.path == "/a/v2.aaf" and h.aaf.name == "v2.aaf"
    assert not h.complete


def test_fremde_metadaten_bleiben_erhalten(mock):
    cutter = MockHelmut.meta_entry("cutter", "M. Kurz", set_name="Schnitt", set_id="set-schnitt")
    mock.projects["p1"]["metadata"].append(cutter)
    client = _client(mock)
    b = client.get("p1")
    client.write_mix(b, MixState(state="uebernommen", mixer="Jens", room="Ton 3"))
    assert cutter in mock.projects["p1"]["metadata"]
    assert mock.mix_value("p1", "mischer") == "Jens"
    assert ("PUT", "/v1/fx/projects/metadata/p1") in mock.requests     # nur Metadaten, nicht das Projekt


# ── Formate laut OpenAPI (Helmut-OpenAPI von MoovIT) ─────────────────────
def test_metadaten_behalten_id_und_set_verweis(mock):
    client = _client(mock)
    vorher = {m["name"]: m for m in mock.projects["p1"]["metadata"]}
    client.write_mix(client.get("p1"), MixState(state="uebernommen", mixer="Jens", room="Ton 3", progress=40))
    nachher = {m["name"]: m for m in mock.projects["p1"]["metadata"]}
    assert nachher["zustand"]["id"] == vorher["zustand"]["id"]
    assert nachher["zustand"]["parent"] == vorher["zustand"]["parent"]          # Set-id bleibt
    assert nachher["mischer"]["parent"][0]["id"] == "set-mischung"            # neue Felder im selben Set
    assert nachher["fortschritt"]["value"] == "40" and nachher["fortschritt"]["type"] == "STRING"
    assert client.get("p1").mix.progress == 40


def test_status_ist_ein_objekt():
    from tonmischung.helmut.mapping import is_locked, status_body
    assert is_locked({"status": {"status": "LOCKED", "lockedBy": "jens"}})
    assert not is_locked({"status": {"status": "ARCHIVED"}}) and not is_locked({"status": None})
    assert is_locked({"status": "LOCKED"})                                     # ältere Form
    assert status_body(True) == {"status": "LOCKED"} and status_body(False) == {"status": "UNLOCKED"}


def test_token_aus_header_oder_inhalt():
    from tonmischung.helmut.mapping import token_from_login
    assert token_from_login({"Authorization": "Bearer abc"}, {}) == "abc"
    assert token_from_login({}, {"token": "xyz"}) == "xyz"
    assert token_from_login({}, {}) is None


def test_anmeldung_per_oauth(mock):
    from tonmischung.helmut.client import LOGIN_OAUTH
    client = HelmutClient(mock.url, lambda: ("tm-client", "tm-secret"), sleep=lambda s: None,
                          login_method=LOGIN_OAUTH)
    assert client.get("p1").title
    assert ("POST", "/v1/members/auth/oauth") in mock.requests


def test_rueckgabe_asset_und_nachricht(mock):
    client = _client(mock)
    a = client.add_asset("p1", "/H/p1/Rueckgabe/ST.wav", "tonspur")
    assert a["filepath"] == "/H/p1/Rueckgabe/ST.wav" and a["mimeType"] == "AUDIO" and a["name"] == "ST.wav"
    assert next(c["value"] for c in a["customMetadata"] if c["key"] == "art") == "tonspur"
    client.notify("jens", "Mischung fertig", "Bahnstreik")
    assert mock.notifications[-1] == ("jens", {"msg": "Mischung fertig: Bahnstreik", "expandDialog": False})


def test_falsche_zugangsdaten(mock):
    with pytest.raises(HelmutError) as e:
        _client(mock, password="falsch").get("p1")
    assert e.value.status == 401


def test_abgelaufenes_token_fuehrt_zu_neuanmeldung(mock):
    client = _client(mock)
    client.get("p1")
    mock.expire_tokens()
    assert client.get("p1").id == "p1"


def test_serverfehler_wird_wiederholt(mock):
    mock.fail_next = [500, 503]
    assert _client(mock).get("p1").id == "p1"


def test_anlegendes_post_wird_nicht_wiederholt(mock):
    """Review A6: Duplikat, Asset und Nachricht legen etwas an – eine Wiederholung nach 5xx könnte Doppelte erzeugen."""
    client = _client(mock)
    original = client.get("p1")
    before = len(mock.projects)
    mock.fail_next = [503]
    with pytest.raises(HelmutUnavailableError):
        client.duplicate(original, "Bahnstreik (2)")
    assert mock.requests.count(("POST", "/v1/fx/projects/duplicate")) == 1
    assert len(mock.projects) == before


def test_anlegendes_post_nach_zeitueberschreitung_bleibt_offen(mock):
    client = HelmutClient(mock.url, lambda: ("test", "test"), timeout_s=0.3, sleep=lambda s: None)
    client.get("p1")
    mock.late_next = [1.0]                                   # Helmut legt an, antwortet aber zu spät
    with pytest.raises(HelmutUnavailableError, match="offen"):
        client.add_asset("p1", "/H/p1/Rueckgabe/ST.wav", "tonspur")
    assert mock.requests.count(("POST", "/v1/co/assets")) == 1
    assert sum(a.get("filepath") == "/H/p1/Rueckgabe/ST.wav" for a in mock.assets["p1"]) == 1


def test_lesen_nach_zeitueberschreitung_wird_wiederholt(mock):
    client = HelmutClient(mock.url, lambda: ("test", "test"), timeout_s=0.3, sleep=lambda s: None)
    client.get("p1")
    mock.late_next = [1.0]
    assert client.get("p1").id == "p1"
    assert mock.requests.count(("GET", "/v1/fx/projects/p1")) == 3


def test_suche_ist_wiederholbar(mock):
    client = _client(mock)
    client.login()
    mock.fail_next = [502]
    assert "p1" in {c.id for c in client.list_for_mixing()}
    assert mock.requests.count(("POST", "/v1/fx/projects/search")) == 2


def test_bleibender_serverfehler_ist_kein_ausfall(mock):
    """Review B6: 500 nach allen Versuchen ist ein Fehler in Helmut, nicht „nicht erreichbar“ – sonst bliebe der
    Nachtrag ewig daran hängen."""
    client = _client(mock)
    client.login()
    mock.fail_next = [500, 500, 500]
    with pytest.raises(HelmutError) as e:
        client.get("p1")
    assert not isinstance(e.value, HelmutUnavailableError) and e.value.status == 500


def test_abgewiesene_verbindung_darf_wiederholt_werden():
    import socket
    import urllib.error
    from tonmischung.helmut.client import not_sent
    assert not_sent(urllib.error.URLError(ConnectionRefusedError()))
    assert not_sent(urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")))
    assert not not_sent(urllib.error.URLError(TimeoutError("timed out")))
    assert not not_sent(TimeoutError("timed out"))
    assert not not_sent(ConnectionResetError())
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                                # niemand hört auf diesem Port
    pauses = []
    client = HelmutClient(f"http://127.0.0.1:{port}", lambda: ("test", "test"), timeout_s=2, sleep=pauses.append)
    client._token = "t"
    with pytest.raises(HelmutUnavailableError, match="nicht erreichbar"):
        client.add_asset("p1", "/H/p1/x.wav", "tonspur")
    assert len(pauses) == client.retries


def test_statusabfrage_meldet_sich_nicht_jedes_mal_an(mock):
    """Review A13: die Titelleiste fragt alle 15 s – ohne neue Anmeldung, bei Ausfall nur ein kurzer Versuch."""
    now, pauses = [0.0], []
    client = HelmutClient(mock.url, lambda: ("test", "test"), sleep=pauses.append, clock=lambda: now[0])
    client.reachable()
    client.get("p1")
    n = len(mock.requests)
    now[0] = 20.0
    client.reachable()                                       # vor 20 s geantwortet: kein Netz
    assert len(mock.requests) == n
    now[0] = 60.0
    mock.unavailable = True
    with pytest.raises(HelmutUnavailableError):
        client.reachable()
    assert len(mock.requests) == n + 1 and pauses == []      # ein Versuch, keine Wiederholung


def test_helmut_nicht_erreichbar(mock):
    mock.unavailable = True
    with pytest.raises(HelmutUnavailableError):
        _client(mock).get("p1")


def test_unbekannter_beitrag(mock):
    with pytest.raises(HelmutError) as e:
        _client(mock).get("gibtsnicht")
    assert e.value.status == 404


# ── Übernehmen, Zustand, Zurückgeben ────────────────────────────────────────
def test_uebernehmen_sperrt_und_schreibt_mischung(mock):
    b = _service(mock).take("p1")
    assert mock.status("p1") == "LOCKED"
    assert (b.mix.state, b.mix.mixer, b.mix.room) == ("uebernommen", "Jens", "Ton 3")
    assert mock.mix_value("p1", "uebernommen_um") == "2026-10-02T10:00:00+00:00"


def test_fremd_uebernommener_beitrag_wird_nicht_ueberschrieben(mock):
    mock.set_status("p3", "LOCKED")
    with pytest.raises(ConflictError, match="A. Wolf"):
        _service(mock).take("p3")
    assert ("PUT", "/v1/fx/projects") not in mock.requests
    assert mock.mix_value("p3", "mischer") == "A. Wolf"


def test_zustand_nur_durch_den_besitzer(mock):
    _service(mock).take("p1")
    _service(mock).set_state("p1", "importiert", session_path="/S/Bahnstreik.ptx")
    assert mock.mix_value("p1", "session_pfad") == "/S/Bahnstreik.ptx"
    with pytest.raises(ConflictError):
        _service(mock, mixer="A. Wolf", room="Ton 2").set_state("p1", "in_mischung")


def test_zurueckgeben_entsperrt_und_haelt_grund_fest(mock):
    svc = _service(mock)
    svc.take("p1")
    svc.set_state("p1", "in_mischung", session_path="/S/Bahnstreik.ptx")
    b = svc.release("p1", "Workstation abgestürzt")
    assert mock.status("p1") == "UNLOCKED"
    assert (b.mix.state, b.mix.return_reason, b.mix.returned_from_room) == (
        "zurueckgegeben", "Workstation abgestürzt", "Ton 3")
    assert mock.mix_value("p1", "session_pfad") == "/S/Bahnstreik.ptx"
    # danach darf ein anderer Raum übernehmen
    assert _service(mock, mixer="A. Wolf", room="Ton 2").take("p1").mix.mixer == "A. Wolf"


def test_helmut_faellt_beim_uebernehmen_aus(mock):
    mock.unavailable = True
    with pytest.raises(HelmutUnavailableError):
        _service(mock).take("p1")
    assert mock.status("p1") != "LOCKED"                    # nie gesperrt worden


def test_falsch_gepflegtes_projekt_kostet_nicht_die_liste(mock):
    """Review B7: „12,5“ als Fortschritt oder ein Projekt ohne Kennung ließen die ganze Liste scheitern."""
    from tonmischung.helmut.mapping import parse_progress
    assert [parse_progress(v) for v in ("12,5", "40 %", "x", "250", "-3")] == [12, 40, 0, 100, 0]
    mock.projects["p1"]["metadata"].append(mock.meta_entry("fortschritt", "12,5"))
    mock.projects["kaputt"] = {"name": "ohne Kennung", "metadata": "keine Liste"}
    items = {c.id: c for c in _client(mock).list_for_mixing()}
    assert "p1" in items and items["p1"].mix.progress == 12 and "kaputt" not in items


def test_gescheitertes_uebernehmen_nimmt_die_eigene_sperre_zurueck(mock, monkeypatch):
    """Review B8: scheitert das Schreiben nach dem Sperren, blieb der Beitrag gesperrt."""
    service = _service(mock)

    def broken(current, mix):
        raise HelmutError("Helmut lehnt PUT ab (400)")
    monkeypatch.setattr(service.client, "write_mix", broken)
    with pytest.raises(HelmutError):
        service.take("p1")
    assert mock.status("p1") != "LOCKED"


def test_sperre_eines_anderen_bleibt(mock, monkeypatch):
    """Drängt sich jemand zwischen Sperren und Schreiben, gehört die Sperre ihm – nicht zurücknehmen."""
    service = _service(mock)
    real_lock = service.client.set_locked

    def lock_and_race(pid, locked):
        real_lock(pid, locked)
        if locked:                                           # A. Wolf übernimmt im selben Augenblick
            _service(mock, mixer="A. Wolf", room="Ton 2").client.write_mix(
                service.client.get(pid), MixState("uebernommen", "A. Wolf", "Ton 2"))
    monkeypatch.setattr(service.client, "set_locked", lock_and_race)
    with pytest.raises(ConflictError):
        service.take("p1")
    assert mock.status("p1") == "LOCKED" and mock.mix_value("p1", "mischer") == "A. Wolf"
