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


def _token_client(mock, secret):
    from tonmischung.helmut.client import LOGIN_TOKEN
    secrets = [secret] if isinstance(secret, str) else secret        # Liste: Schlüsselbund ändert sich je Lesen
    return HelmutClient(mock.url, lambda: ("", secrets.pop(0) if len(secrets) > 1 else secrets[0]),
                        sleep=lambda s: None, login_method=LOGIN_TOKEN)


def test_anmeldung_per_bearer_token(mock):
    mock.tokens.add("api-token")
    assert _token_client(mock, "Bearer api-token\n").get("p1").title
    assert not any(path.startswith("/v1/members/auth") for _, path in mock.requests)   # kein Anmeldeaufruf


def test_bearer_token_abgelehnt_ohne_neuanmeldung(mock):
    from tonmischung.helmut.client import TOKEN_REJECTED, HelmutAuthError
    with pytest.raises(HelmutAuthError) as e:
        _token_client(mock, "falsch").get("p1")
    assert e.value.status == 401 and str(e.value) == TOKEN_REJECTED
    assert mock.requests == [("GET", "/v1/fx/projects/p1"), ("GET", "/v1/members/auth/whoami")]  # kein Anmeldeaufruf


def test_abgelehnter_einzelner_aufruf_ist_kein_anmeldefehler(mock):
    """Review 10.11.2026: Lehnt Helmut nur diesen Aufruf ab (Benachrichtigung ohne Admin-Rolle), hieß es fälschlich
    „Token prüfen“ – und der Nachtrag hätte gewartet statt gezählt."""
    from tonmischung.helmut.client import CALL_REJECTED, HelmutAuthError
    mock.tokens.add("gut")
    mock.notify_denied = 401
    with pytest.raises(HelmutError) as e:
        _token_client(mock, "gut").notify("awolf", "Freigegeben", "Bahnstreik")
    assert not isinstance(e.value, HelmutAuthError) and e.value.status == 401 and CALL_REJECTED in str(e.value)
    with pytest.raises(HelmutError) as e:
        _client(mock).notify("awolf", "Freigegeben", "Bahnstreik")          # Kennwort: dasselbe nach Neuanmeldung
    assert not isinstance(e.value, HelmutAuthError) and CALL_REJECTED in str(e.value)


def test_neues_token_im_schluesselbund_gilt_sofort(mock):
    """Review 10.11.2026: nach dem Eintragen eines neuen Tokens kam bis zum nächsten Takt der Titelleiste 401."""
    mock.tokens.add("neu")
    client = _token_client(mock, ["alt", "neu"])
    assert client.get("p1").id == "p1"
    assert mock.requests.count(("GET", "/v1/fx/projects/p1")) == 2


@pytest.mark.parametrize("secret, match", [("  ", "Kein Bearer-Token"), ("abc\ndef", "unzulässige Zeichen"),
                                           ("„abc“", "unzulässige Zeichen"), ("ab cd", "unzulässige Zeichen")])
def test_bearer_token_ungeeignet(mock, secret, match):
    """Review 10.11.2026: ein Umbruch mitten im Token gab einen ValueError samt Token im Text."""
    from tonmischung.helmut.client import HelmutAuthError
    with pytest.raises(HelmutAuthError, match=match) as e:
        _token_client(mock, secret).get("p1")
    assert (not secret.strip() or secret.strip() not in str(e.value)) and mock.requests == []


def test_bearer_token_formen():
    from tonmischung.helmut.mapping import bearer_token, valid_bearer
    assert bearer_token("abc") == bearer_token("Bearer abc") == bearer_token("bearer%20abc\n") == "abc"
    assert bearer_token("Bearer%20abc%3D%3D") == "abc==" and bearer_token("a+b/c==") == "a+b/c=="
    assert valid_bearer("eyJh.eyJz-_~+/.sig==") and not valid_bearer("a\nb") and not valid_bearer("")


def test_erreichbar_mit_bearer_token_fragt_helmut(mock):
    from tonmischung.helmut.client import TOKEN_REJECTED, HelmutAuthError
    client = _token_client(mock, "falsch")
    with pytest.raises(HelmutAuthError) as e:
        client.reachable()
    assert str(e.value) == TOKEN_REJECTED                               # Titelleiste: dieselbe Meldung
    mock.tokens.add("falsch")
    client.reachable()
    assert mock.requests.count(("GET", "/v1/members/auth/whoami")) == 2 and client.last_ok is not None


def test_falsches_kennwort_ist_ein_anmeldefehler(mock):
    from tonmischung.helmut.client import HelmutAuthError
    with pytest.raises(HelmutAuthError) as e:
        _client(mock, password="falsch").get("p1")
    assert e.value.status == 401


def _serve(handler):
    import http.server
    import threading
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_token_nur_an_denselben_server_weitergeleitet():
    """Review 10.11.2026: urllib gab `Authorization` bei einer Weiterleitung an jeden Host weiter."""
    import http.server
    seen = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"id": "p1"}')

        def log_message(self, *a):
            pass
    target = _serve(Target)
    other = f"http://localhost:{target.server_address[1]}"

    class Redirect(Target):
        def do_GET(self):
            if self.path.startswith("/weiter"):
                return super().do_GET()
            self.send_response(302)
            self.send_header("Location", "/weiter" if "gleich" in self.path else other + "/fremd")
            self.end_headers()
    helmut = _serve(Redirect)
    from tonmischung.helmut.client import LOGIN_TOKEN
    client = HelmutClient(f"http://127.0.0.1:{helmut.server_address[1]}", lambda: ("", "geheim"),
                          sleep=lambda s: None, login_method=LOGIN_TOKEN)
    client.request("GET", "/gleich")
    client.request("GET", "/anders")
    assert seen == [("/weiter", "Bearer geheim"), ("/fremd", None)]
    helmut.shutdown()
    target.shutdown()


@pytest.mark.parametrize("echo", ["{a}", "{json}", "{b64}", "{pct}", "{ent}", "{part}"])
def test_token_steht_nie_in_der_fehlermeldung(echo):
    """Review 10.11.2026: spiegelt ein Server die Anfrage zurück, stand das Token in der Meldung – auch kodiert."""
    import base64
    import http.server
    token = "geheim.tok/en-1234"

    class Echo(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            a = self.headers.get("Authorization")
            text = echo.format(a=a, json=a.replace("/", "\\/"), b64=base64.b64encode(a.encode()).decode(),
                               pct=token.replace("/", "%2f"), ent=token.replace("/", "&#x2F;"), part=token[:9])
            self.send_response(403)
            self.end_headers()
            self.wfile.write(f"verboten: {text}".encode())

        def log_message(self, *a):
            pass
    srv = _serve(Echo)
    from tonmischung.helmut.client import LOGIN_TOKEN
    client = HelmutClient(f"http://127.0.0.1:{srv.server_address[1]}", lambda: ("", token),
                          sleep=lambda s: None, login_method=LOGIN_TOKEN)
    with pytest.raises(HelmutError) as e:
        client.request("GET", "/x")
    assert str(e.value) == "Helmut lehnt GET /x ab (403)" and e.value.__cause__ is None
    srv.shutdown()


def test_anmeldung_spiegelt_keine_zugangsdaten():
    """Review 10.11.2026: Antworten der Anmeldung (Kennwort, OAuth-Geheimnis) kommen nie in die Meldung."""
    import http.server

    class Echo(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"invalid credentials: " + body)

        def log_message(self, *a):
            pass
    srv = _serve(Echo)
    from tonmischung.helmut.client import LOGIN_OAUTH, HelmutAuthError
    for method in ("passwort", LOGIN_OAUTH):
        client = HelmutClient(f"http://127.0.0.1:{srv.server_address[1]}", lambda: ("konto", "Geheim-42"),
                              sleep=lambda s: None, login_method=method)
        with pytest.raises(HelmutAuthError) as e:
            client.get("p1")
        assert "Geheim" not in str(e.value) and e.value.__cause__ is None
    srv.shutdown()


def test_post_wird_nicht_weitergeleitet():
    """Review 10.11.2026: aus einem POST machte urllib bei 302 still einen GET, der als Erfolg galt."""
    import http.server

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", "/anders")
            self.end_headers()

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:99999/kaputt")       # ungültiger Port
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = _serve(Redirect)
    client = HelmutClient(f"http://127.0.0.1:{srv.server_address[1]}", lambda: ("", "tok"),
                          sleep=lambda s: None, login_method="token")
    with pytest.raises(HelmutError, match="leitet POST /v1/co/assets weiter"):
        client.request("POST", "/v1/co/assets", {"x": 1})
    with pytest.raises(HelmutError, match="weiter"):                      # kein roher ValueError
        client.request("GET", "/x")
    srv.shutdown()


def test_derselbe_server():
    from tonmischung.helmut.client import _same_server
    assert _same_server("https://h/a", "https://H:443/b") and _same_server("http://h/a", "https://h/b")
    assert not _same_server("https://h/a", "http://h/b") and not _same_server("http://h/a", "http://h:8080/b")
    assert not _same_server("http://h/a", "http://anders/b") and not _same_server("http://h/a", "http://h:99999/b")


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


def test_401_fuehrt_zu_neuanmeldung(mock):
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


def test_asset_art_und_mimetype():
    from tonmischung.helmut.mapping import new_asset
    assert [new_asset("p", "/x", k)["mimeType"] for k in ("tonspur", "extern", "session", "metadaten")] == [
        "AUDIO", "AUDIO", "AUDIO", "DATA"]


def test_leere_neue_felder_werden_nicht_angelegt():
    from tonmischung.core.mixstate import MixState
    from tonmischung.helmut.mapping import metadata_with_mix
    project = {"metadata": [{"name": "videoid", "value": "VID-1", "parent": [{"name": "Mischung"}]}]}
    names = {m["name"]: m["value"] for m in metadata_with_mix(project, MixState(mixer="Jens"))}
    assert names["mischer"] == "Jens" and names["videoid"] == ""                 # vorhandenes Feld wird geleert
    assert "template" not in names and "rueckgabe_grund" not in names            # fehlt und leer: nicht anlegen


def test_sperre_ohne_antwort_wird_zurueckgenommen_und_spaeter_uebernehmbar(mock):
    """Ausfall: die Sperre kommt an, die Antwort nicht – Ottilie nimmt die eigene Sperre zurück; bleibt sie doch
    stehen, darf derselbe Mischer den Beitrag trotzdem übernehmen (Jens 11.11.2026)."""
    from tonmischung.helmut.client import HelmutUnavailableError
    from tonmischung.helmut.service import MixService
    client = _client(mock)
    real = client.set_locked
    calls = []

    def lost(pid, locked):
        calls.append(locked)
        real(pid, locked)
        if locked:
            raise HelmutUnavailableError("Helmut hat nicht geantwortet", sent_unknown=True)
    client.set_locked = lost
    svc = MixService(client, "Jens", "Ton 3", now=lambda: "jetzt", sleep=lambda s: None)
    with pytest.raises(HelmutUnavailableError):
        svc.take("p1")
    assert calls == [True, False] and mock.status("p1") == "UNLOCKED"          # eigene Sperre zurückgenommen
    mock.set_status("p1", "LOCKED")                                             # … oder sie blieb doch stehen
    client.set_locked = real
    assert svc.take("p1").mix.mixer == "Jens"



def test_zwei_raeume_zugleich_nur_einer_uebernimmt(mock):
    """Prüfer 11.11.2026: A hat gesperrt, schreibt aber noch – B sieht „gesperrt, kein Mischer“. B wartet kurz und
    liest neu; steht dann A drin, ist es ein Konflikt (vorher meldeten beide Erfolg)."""
    from tonmischung.core.mixstate import ConflictError
    from tonmischung.helmut.service import STALE_WAIT_S, MixService
    a = MixService(_client(mock), "Jens", "Ton 3", now=lambda: "jetzt")
    mock.set_status("p1", "LOCKED")                         # A hat gesperrt …
    waited = []

    def a_writes_meanwhile(s):
        waited.append(s)
        c = a.client.get("p1")                              # … und schreibt, während B wartet
        from tonmischung.core import mixstate
        a.client.write_mix(c, mixstate.after_take(c.mix, "Jens", "Ton 3", "jetzt"))
    b = MixService(_client(mock), "B. Bauer", "Ton 2", now=lambda: "jetzt", sleep=a_writes_meanwhile)
    with pytest.raises(ConflictError, match="Jens"):
        b.take("p1")
    assert waited == [STALE_WAIT_S] and mock.mix_value("p1", "mischer") == "Jens"


def test_gleichzeitig_geschrieben_letzter_gewinnt_der_andere_merkt_es(mock):
    """Schreibt der andere nach uns, merkt es die Nachprüfung nach dem eigenen Schreiben."""
    from tonmischung.core import mixstate
    from tonmischung.core.mixstate import ConflictError
    from tonmischung.helmut.service import MixService
    client = _client(mock)
    real = client.write_mix

    def overtaken(current, mix):
        r = real(current, mix)
        c = client.get("p1")
        real(c, mixstate.after_take(mixstate.MixState(), "A. Wolf", "Ton 2", "jetzt"))
        return r
    client.write_mix = overtaken
    with pytest.raises(ConflictError, match="gleichzeitig von A. Wolf"):
        MixService(client, "Jens", "Ton 3", now=lambda: "jetzt").take("p1")
    assert mock.status("p1") == "LOCKED"                    # die Sperre gehört jetzt A. Wolf – bleibt


def test_abgelehnte_sperre_wird_nicht_zurueckgenommen(mock):
    """Ausfall R7 (K2): lehnt Helmut das Sperren ab, ist es nicht unsere Sperre – nicht entsperren."""
    from tonmischung.helmut.client import HelmutError
    from tonmischung.helmut.service import MixService
    client = _client(mock)
    calls = []

    def refuse(pid, locked):
        calls.append(locked)
        raise HelmutError("Helmut lehnt PUT … ab (409)", status=409)
    client.set_locked = refuse
    with pytest.raises(HelmutError):
        MixService(client, "Jens", "Ton 3", now=lambda: "jetzt").take("p1")
    assert calls == [True]


def test_eigener_gesperrter_beitrag_ohne_wartezeit(mock):
    """Erneutes Holen des eigenen (gesperrten) Beitrags wartet nicht – nur eine Sperre ohne Besitzer kostet Zeit."""
    from tonmischung.helmut.service import MixService
    slept = []
    svc = MixService(_client(mock), "Jens", "Ton 3", now=lambda: "jetzt", sleep=slept.append)
    svc.take("p1")
    svc.take("p1")
    assert slept == [] and mock.status("p1") == "LOCKED"


def test_sperre_ohne_besitzer_wartet_vor_und_nach_dem_schreiben(mock):
    from tonmischung.helmut.service import STALE_WAIT_S, MixService
    mock.set_status("p1", "LOCKED")
    slept = []
    MixService(_client(mock), "Jens", "Ton 3", now=lambda: "jetzt", sleep=slept.append).take("p1")
    assert slept == [STALE_WAIT_S, STALE_WAIT_S] and mock.mix_value("p1", "mischer") == "Jens"
