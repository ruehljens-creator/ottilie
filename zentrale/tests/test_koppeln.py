"""Koppeln über die Administration (Jens 13.11.2026): Anmelden ohne Code, Zuordnen, Ablehnen, Umziehen, Umbenennen."""
import re

import pytest
from starlette.websockets import WebSocketDisconnect

from app.auth import MAX_PENDING, PENDING_TTL_S, AuthError, fingerprint
from app.hub import RECONNECT
from test_stufe1 import _admin, _pair, _until, _ws


def _register(client, name="Ottilie-Mac abc123"):
    r = client.post("/api/anmelden", json={"geraet": name})
    assert r.status_code == 200, r.text
    return r.json()


def _waiting(client, key):
    ws = client.websocket_connect("/ws")
    w = ws.__enter__()
    w.send_json({"typ": "anmelden", "daten": {"schluessel": key}})
    return ws, w


def _close_code(w) -> int:
    with pytest.raises(WebSocketDisconnect) as e:
        for _ in range(10):
            w.receive_json()
    return e.value.code


def test_anmelden_ohne_code_gibt_wartendes_geraet_mit_kennung(client, ctx):
    got = _register(client)
    assert got["wartet"] is True and got["kennung"] == fingerprint(got["schluessel"])
    assert len(got["kennung"]) == 6 and ctx.auth.device(got["schluessel"]) is None     # noch nicht gekoppelt
    assert ctx.auth.pending(got["schluessel"]).fingerprint == got["kennung"]
    assert "wartet auf Zuordnung" in ctx.log.query(area="kopplung")[0]["text"]


def test_wartendes_geraet_sieht_und_bewirkt_nichts(client, ctx):
    key = _register(client)["schluessel"]
    other = _pair(client, ctx, "Ton 3")
    c1, w1 = _waiting(client, key)
    first = w1.receive_json()
    assert first["typ"] == "wartet" and first["daten"]["kennung"] == fingerprint(key)
    assert "raeume" not in first["daten"] and "einstellungen" not in first["daten"]
    # Was es schickt, wird verworfen – kein Status, kein Chat, keine Belegung
    w1.send_json({"typ": "status", "anfrage_id": "s", "daten": {"zustand": "mischt"}})
    w1.send_json({"typ": "belegen", "anfrage_id": "b", "daten": {"beitrag_id": "film1"}})
    w1.send_json({"typ": "chat_senden", "anfrage_id": "c", "daten": {"gespraech": "alle", "text": "hallo"}})
    c2, w2, _ = _ws(client, other)
    w2.send_json({"typ": "status", "anfrage_id": "a", "daten": {"zustand": "mischt"}})
    _until(w2, "ok")
    assert ctx.work.claims() == [] and ctx.chat.waiting() == 0
    assert all(st.get("zustand") == "mischt" for _dev, st in ctx.hub.status.values())   # nur das gekoppelte
    assert not any(c.device.name.startswith("Ottilie-Mac abc") for cs in ctx.hub.conns.values() for c in cs)
    assert len(ctx.hub.waiting) == 1                      # Raumstatus und Chat gehen nie an wartende Verbindungen
    c1.__exit__(None, None, None)
    c2.__exit__(None, None, None)


def test_zuordnen_auf_der_adminseite_und_neu_verbinden(client, ctx):
    token = _admin(client)
    rid = ctx.auth.add_room("MacBook")
    got = _register(client)
    page = client.get("/admin/raeume").text
    assert "Wartende Geräte" in page and got["kennung"] in page
    c, w = _waiting(client, got["schluessel"])
    assert w.receive_json()["typ"] == "wartet"
    pid = int(re.search(r"/admin/wartend/(\d+)/zuordnen", page).group(1))
    r = client.post(f"/admin/wartend/{pid}/zuordnen", data={"csrf": token, "raum": str(rid)}, follow_redirects=False)
    assert r.status_code == 303
    assert _close_code(w) == RECONNECT                    # Ottilie verbindet neu …
    c.__exit__(None, None, None)
    c2, w2, hello = _ws(client, got["schluessel"])         # … und bekommt jetzt ihren Raum
    assert hello["daten"]["raum"] == "MacBook" and ctx.auth.pending_list() == []
    assert ctx.auth.device(got["schluessel"]).name == "Ottilie-Mac abc123"
    c2.__exit__(None, None, None)


def test_ablehnen_trennt_und_schluessel_gilt_nicht(client, ctx):
    token = _admin(client)
    got = _register(client)
    c, w = _waiting(client, got["schluessel"])
    w.receive_json()
    pid = ctx.auth.pending_list()[0].id
    client.post(f"/admin/wartend/{pid}/ablehnen", data={"csrf": token}, follow_redirects=False)
    assert w.receive_json()["typ"] == "abgemeldet"
    assert _close_code(w) == 4401
    c.__exit__(None, None, None)
    assert ctx.auth.pending(got["schluessel"]) is None and ctx.auth.device(got["schluessel"]) is None
    with client.websocket_connect("/ws") as w3:
        w3.send_json({"typ": "anmelden", "daten": {"schluessel": got["schluessel"]}})
        assert w3.receive_json()["typ"] == "abgemeldet"


def test_hoechstzahl_verfallen_und_bremse(client, ctx, clock):
    for i in range(MAX_PENDING):
        ctx.auth.register(f"Mac {i}", f"10.0.0.{i}")
    r = client.post("/api/anmelden", json={"geraet": "zu viel"})
    assert r.status_code == 429 and "wartende" in r.json()["fehler"]
    clock.advance(PENDING_TTL_S + 1)                       # 24 h ohne Kontakt: verfallen
    assert ctx.auth.pending_list() == []
    ctx.auth.register("A", "10.9.9.9")
    ctx.auth.register("B", "10.9.9.9")
    with pytest.raises(AuthError, match="dieser Adresse"):  # je Adresse höchstens zwei wartende
        ctx.auth.register("C", "10.9.9.9")
    for _ in range(10):                                    # Bremse: 10 je Adresse
        client.post("/api/anmelden", json={"geraet": "x"})
    assert client.post("/api/anmelden", json={"geraet": "x"}).status_code == 429
    assert ctx.pair_allowed("testclient")                  # Koppeln mit Code hat eine eigene Bremse


def test_verfall_zaehlt_ab_dem_letzten_kontakt(client, ctx, clock):
    key, pend = ctx.auth.register("Mac", "10.1.1.1")
    clock.advance(PENDING_TTL_S - 60)
    ctx.auth.seen_pending(pend.id)                         # Lebenszeichen (verbunden)
    clock.advance(120)
    assert ctx.auth.pending(key) is not None               # Freitag angemeldet, Montag noch da


def test_zuordnen_nur_mit_der_gesehenen_kennung(client, ctx):
    token = _admin(client)
    rid = ctx.auth.add_room("Ton 5")
    key, pend = ctx.auth.register("Mac", "10.1.1.2")
    r = client.post(f"/admin/wartend/{pend.id}/zuordnen", data={"csrf": token, "raum": str(rid), "kennung": "XXXXXX"},
                    follow_redirects=False)
    assert "fehler=1" in r.headers["location"] and ctx.auth.device(key) is None
    page = client.get("/admin/raeume").text
    assert '<option value="">– Raum wählen –</option>' in page and 'name="kennung"' in page
    form = {"csrf": token, "raum": str(rid), "kennung": pend.fingerprint}
    client.post(f"/admin/wartend/{pend.id}/zuordnen", data=form, follow_redirects=False)
    assert ctx.auth.device(key).room == "Ton 5"


def test_umbenennen_auf_namen_eines_geloeschten_raums(client, ctx):
    """Programmierer 13.11.2026: Altlasten im Chat ließen das Umbenennen halb stehen (HTTP 500)."""
    token = _admin(client)
    gone = ctx.auth.add_room("Ton 9")
    conv, _ = ctx.chat.room_conv("Ton 9", "Ton 3")
    ctx.chat.add(conv["id"], "Ton 9", "alt", ["Ton 3"])
    ctx.auth.remove_room(gone)                             # ohne Aufräumen (alte Zentrale)
    rid = ctx.auth.add_room("Ton 4")
    conv4, _ = ctx.chat.room_conv("Ton 4", "Ton 3")
    ctx.chat.add(conv4["id"], "Ton 4", "neu", ["Ton 3"])
    r = client.post(f"/admin/raeume/{rid}/umbenennen", data={"csrf": token, "name": "Ton 9"}, follow_redirects=False)
    assert r.status_code == 303 and ctx.auth.room_name(rid) == "Ton 9"


def test_protokoll_als_csv_ohne_formeln(client, ctx):
    _admin(client)
    client.post("/api/anmelden", json={"geraet": '=HYPERLINK("https://boese";"x")'})
    text = client.get("/admin/protokoll.csv").text
    assert "'=HYPERLINK" in text


def test_umziehen_nur_ohne_arbeit(client, ctx):
    token = _admin(client)
    key = _pair(client, ctx, "Ton 3")
    rid = ctx.auth.add_room("Ton 4")
    dev = ctx.auth.device(key)
    ctx.work.claim("film1", "Ton 3", "Film")
    r = client.post(f"/admin/geraete/{dev.id}/umziehen", data={"csrf": token, "raum": str(rid)})
    assert "in Arbeit" in r.text and ctx.auth.device(key).room == "Ton 3"
    ctx.work.release("film1")
    c, w, _ = _ws(client, key)
    client.post(f"/admin/geraete/{dev.id}/umziehen", data={"csrf": token, "raum": str(rid)}, follow_redirects=False)
    assert _until(w, "neu_verbinden")["typ"] == "neu_verbinden"
    c.__exit__(None, None, None)
    assert ctx.auth.device(key).room == "Ton 4"


def test_umbenennen_nur_ohne_arbeit_und_alles_zieht_mit(client, ctx):
    token = _admin(client)
    key = _pair(client, ctx, "macbookpro")
    _pair(client, ctx, "Schnitt 4", "schnittraum", "PC")
    rid = ctx.auth.room_id("macbookpro")
    ctx.work.claim("film1", "macbookpro", "Film")
    r = client.post(f"/admin/raeume/{rid}/umbenennen", data={"csrf": token, "name": "MacBook"})
    assert "in Arbeit" in r.text and ctx.auth.room_name(rid) == "macbookpro"
    ctx.work.release("film1")
    ctx.work.item("film2", "audio_export_fertig", "Film 2", origin_room="Schnitt 4", mixroom="macbookpro")
    conv, _ = ctx.chat.room_conv("macbookpro", "Schnitt 4")
    ctx.chat.add(conv["id"], "macbookpro", "Hallo", ["Schnitt 4"])
    c, w, _ = _ws(client, key)
    client.post(f"/admin/raeume/{rid}/umbenennen", data={"csrf": token, "name": "MacBook"}, follow_redirects=False)
    assert _until(w, "neu_verbinden")["typ"] == "neu_verbinden"
    c.__exit__(None, None, None)
    assert ctx.auth.room_name(rid) == "MacBook" and ctx.auth.device(key).room == "MacBook"
    assert ctx.work.get_item("film2")["mixroom"] == "MacBook"
    assert ctx.chat.conv("raum:MacBook|Schnitt 4") is not None and ctx.chat.conv(conv["id"]) is None
    assert ctx.chat.pending_for("Schnitt 4")[0]["von"] == "MacBook"
    # doppelte Namen und leere Namen werden abgelehnt
    r = client.post(f"/admin/raeume/{rid}/umbenennen", data={"csrf": token, "name": "Schnitt 4"})
    assert "gibt es schon" in r.text


def test_lebenszeichen_auch_an_wartende(client, ctx):
    key = _register(client)["schluessel"]
    c, w = _waiting(client, key)
    assert w.receive_json()["typ"] == "wartet"
    client.portal.call(ctx.hub.ping_all)                   # sonst hielte Ottilie die Verbindung nach 90 s für tot
    assert w.receive_json()["typ"] == "lebenszeichen"
    c.__exit__(None, None, None)


def test_neuer_raum_mit_altem_namen_bekommt_den_chat_sauber(client, ctx):
    gone = ctx.auth.add_room("Ton 3")
    conv, _ = ctx.chat.room_conv("Regie", "Ton 3")
    ctx.chat.add(conv["id"], "Regie", "alt", ["Ton 3"])
    ctx.auth.remove_room(gone)
    ctx.chat.remove_room("Ton 3")
    ctx.auth.add_room("Ton 3")
    conv2, new = ctx.chat.room_conv("Regie", "Ton 3")
    assert new and ctx.chat.pending_for("Ton 3") == []                  # nichts Altes geerbt
    assert ctx.chat.convs_of("Ton 3", {}) and ctx.chat.convs_of("Regie", {})   # beide Mitglied
