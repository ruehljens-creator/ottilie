"""Stufe 1: Administration, Kopplung, WebSocket, Raumstatus, Protokoll, Sicherung, CLI."""
import json
import os
import re
import sqlite3

import pytest
from starlette.websockets import WebSocketDisconnect

from app import cli
from app.auth import AuthError
from app.backup import prepare_restore
from app.log import redact

NEW_PW = "neues-Kennwort-123"


def _login(client, name="admin", pw="admin"):
    r = client.get("/admin/anmelden")
    nonce = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
    return client.post("/admin/anmelden", data={"csrf": nonce, "name": name, "kennwort": pw}, follow_redirects=False)


def _csrf(client, path="/admin"):
    r = client.get(path)
    return re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)


def _admin(client):
    """Erstanmeldung mit Kennwortwechsel – danach angemeldet."""
    r = _login(client)
    assert r.status_code == 303 and r.headers["location"].endswith("/admin/kennwort")
    token = _csrf(client, "/admin/kennwort")
    r = client.post("/admin/kennwort", data={"csrf": token, "alt": "admin", "neu": NEW_PW, "neu2": NEW_PW},
                    follow_redirects=False)
    assert r.status_code == 303
    return _csrf(client)


def _pair(client, ctx, room="Ton 3", kind="mischraum", name="Mac mini"):
    if ctx.auth.room_id(room) is None:
        ctx.auth.add_room(room, kind)
    code = ctx.auth.pairing_code(ctx.auth.room_id(room))
    r = client.post("/api/koppeln", json={"code": code.lower(), "geraet": name})
    assert r.status_code == 200, r.text
    return r.json()["schluessel"]


def _ws(client, key):
    ws = client.websocket_connect("/ws")
    w = ws.__enter__()
    w.send_json({"typ": "anmelden", "daten": {"schluessel": key}})
    hello = w.receive_json()
    assert hello["typ"] == "willkommen"
    return ws, w, hello


def _until(w, typ, n=20):
    for _ in range(n):
        m = w.receive_json()
        if m["typ"] == typ:
            return m
    raise AssertionError(f"keine Nachricht {typ}")


# ── Administration ─────────────────────────────────────────────────────────────
def test_gesund(client):
    assert client.get("/gesund").json()["ok"]


def test_erstanmeldung_erzwingt_kennwortwechsel(client):
    r = _login(client)
    assert r.headers["location"].endswith("/admin/kennwort")
    for page in ("/admin", "/admin/raeume", "/admin/protokoll", "/admin/betrieb"):
        r = client.get(page, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("/admin/kennwort"), page
    token = _csrf(client, "/admin/kennwort")
    r = client.post("/admin/kennwort", data={"csrf": token, "alt": "admin", "neu": "kurz", "neu2": "kurz"})
    assert "mindestens 10 Zeichen" in r.text
    r = client.post("/admin/kennwort", data={"csrf": token, "alt": "admin", "neu": NEW_PW, "neu2": NEW_PW},
                    follow_redirects=False)
    assert client.get("/admin").status_code == 200


def test_sperre_nach_fuenf_fehlversuchen(client, clock):
    for _ in range(5):
        assert _login(client, pw="falsch").status_code == 400
    r = _login(client, pw="admin")
    assert r.status_code == 400 and "gesperrt" in r.text
    clock.advance(301)
    assert _login(client, pw="admin").status_code == 303


def test_ohne_csrf_abgelehnt(client):
    _admin(client)
    r = client.post("/admin/raeume", data={"name": "X", "art": "mischraum"})
    assert r.status_code == 403
    r = client.post("/admin/raeume", data={"csrf": "falsch", "name": "X", "art": "mischraum"})
    assert r.status_code == 403


def test_raum_anlegen_und_code_ueber_die_seiten(client, ctx):
    token = _admin(client)
    client.post("/admin/raeume", data={"csrf": token, "name": "Schnitt 4", "art": "schnittraum"})
    rid = ctx.auth.room_id("Schnitt 4")
    r = client.post(f"/admin/raeume/{rid}/code", data={"csrf": token})
    code = re.search(r'class="code">([A-Z0-9]{6})<', r.text).group(1)
    assert client.post("/api/koppeln", json={"code": code, "geraet": "PC"}).status_code == 200
    client.post(f"/admin/raeume/{rid}/schreibweisen", data={"csrf": token, "schreibweisen": "SR4, schnitt vier"})
    assert ctx.auth.room_for_alias("sr4") == "Schnitt 4" and ctx.auth.room_for_alias("Schnitt 4") == "Schnitt 4"


def test_geraet_hat_keinen_adminzugang(client, ctx):
    key = _pair(client, ctx)
    client.cookies.set("zentrale_sitzung", key)
    r = client.get("/admin", headers={"Authorization": f"Bearer {key}"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/admin/anmelden")


# ── Kopplung ───────────────────────────────────────────────────────────────────
def test_kopplung_einmalig_und_befristet(client, ctx, clock):
    ctx.auth.add_room("Ton 3")
    code = ctx.auth.pairing_code(ctx.auth.room_id("Ton 3"))
    assert client.post("/api/koppeln", json={"code": code, "geraet": "A"}).status_code == 200
    assert client.post("/api/koppeln", json={"code": code, "geraet": "B"}).status_code == 400      # doppelt
    code2 = ctx.auth.pairing_code(ctx.auth.room_id("Ton 3"))
    clock.advance(15 * 60 + 1)
    r = client.post("/api/koppeln", json={"code": code2, "geraet": "C"})
    assert r.status_code == 400 and "abgelaufen" in r.json()["fehler"]


def test_kopplung_begrenzt_versuche(client, ctx):
    for _ in range(10):
        client.post("/api/koppeln", json={"code": "AAAAAA", "geraet": "x"})
    assert client.post("/api/koppeln", json={"code": "AAAAAA", "geraet": "x"}).status_code == 429


def test_schluessel_nur_als_hash(client, ctx, tmp_path):
    key = _pair(client, ctx)
    ctx.db.execute("PRAGMA wal_checkpoint(FULL)")
    raw = b"".join(open(os.path.join(ctx.data_dir, f), "rb").read() for f in os.listdir(ctx.data_dir)
                   if f.startswith("zentrale.db"))
    assert key.encode() not in raw


def test_abgemeldetes_geraet_wird_getrennt_und_abgewiesen(client, ctx):
    key = _pair(client, ctx)
    dev = ctx.auth.device(key)
    ctx.auth.revoke(dev.id)
    with client.websocket_connect("/ws") as w:
        w.send_json({"typ": "anmelden", "daten": {"schluessel": key}})
        assert w.receive_json()["typ"] == "abgemeldet"
        with pytest.raises(WebSocketDisconnect):
            w.receive_json()


# ── WebSocket und Raumstatus ───────────────────────────────────────────────────
def test_raumstatus_an_alle_und_ohne_personen(client, ctx):
    k1 = _pair(client, ctx, "Ton 3")
    k2 = _pair(client, ctx, "Schnitt 4", "schnittraum", "PC")
    c1, w1, hello = _ws(client, k1)
    c2, w2, _ = _ws(client, k2)
    assert hello["daten"]["raum"] == "Ton 3"
    w1.send_json({"typ": "status", "anfrage_id": "a1", "daten": {
        "betriebsart": "uebergabeordner", "zustand": "mischt", "beitrag_titel": "Film", "fortschritt": 140,
        "mischer": "Jens", "protools": True}})
    got = _until(w2, "raumstatus")
    while got["daten"]["raum"] != "Ton 3" or "zustand" not in got["daten"]:
        got = _until(w2, "raumstatus")
    d = got["daten"]
    assert (d["zustand"], d["fortschritt"], d["protools"], d["verbunden"]) == ("mischt", 100, True, True)
    assert "mischer" not in d                                    # keine Personen
    c1.__exit__(None, None, None)
    c2.__exit__(None, None, None)


def test_einmal_verarbeitung(client, ctx):
    k1 = _pair(client, ctx)
    calls = []
    orig = ctx.hub.handlers["status"]

    async def counting(dev, data):
        calls.append(data)
        return await orig(dev, data)
    ctx.hub.handlers["status"] = counting
    c, w, _ = _ws(client, k1)
    for _ in range(3):
        w.send_json({"typ": "status", "anfrage_id": "gleich", "daten": {"zustand": "frei"}})
        assert _until(w, "ok")["anfrage_id"] == "gleich"
    assert len(calls) == 1
    c.__exit__(None, None, None)


def test_ohne_anmeldung_kein_websocket(client):
    with client.websocket_connect("/ws") as w:
        w.send_json({"typ": "status", "daten": {}})
        assert w.receive_json()["typ"] == "abgemeldet"


def test_zugelassene_netze(client, ctx):
    ctx.db.set_setting("server.zugelassene_netze", ["10.0.0.0/8"])
    assert client.post("/api/koppeln", json={"code": "x"}).status_code == 403
    assert client.get("/admin/anmelden").status_code == 403
    ctx.db.set_setting("server.zugelassene_netze", ["testclient/32", "127.0.0.0/8"])
    assert ctx.allowed("127.0.0.1")


# ── Protokoll ──────────────────────────────────────────────────────────────────
def test_protokoll_schwaerzt_geheimnisse(client, ctx):
    key = _pair(client, ctx)
    ctx.log.info("test", f"schluessel={key} kennwort: geheim123 code=ABC123")
    ctx.log.info("test", f"lang {key}")
    rows = ctx.log.query()
    text = json.dumps(rows)
    assert key not in text and "geheim123" not in text and "ABC123" not in text
    assert redact("Kennwort = 'x y'") == "Kennwort = ••••"


def test_debug_schaltet_sich_selbst_ab(ctx, clock):
    ctx.log.debug("x", "vorher")
    ctx.log.debug_on(1, rooms=["Ton 3"], by="admin")
    ctx.log.debug("x", "anderer Raum", room="Ton 1")
    ctx.log.debug("x", "passt", room="Ton 3")
    clock.advance(3601)
    assert not ctx.log.debug_state()["aktiv"]
    ctx.log.debug("x", "danach", room="Ton 3")
    texts = [r["text"] for r in ctx.log.query(levels=["debug"])]
    assert texts == ["passt"]
    infos = " ".join(r["text"] for r in ctx.log.query(levels=["info"]))
    assert "Debug-Protokoll an" in infos and "selbst abgeschaltet" in infos


def test_debug_nicht_auf_stdout(ctx, capsys):
    ctx.log.debug_on(1, by="admin")
    ctx.log.debug("x", "nur in der Datenbank")
    ctx.log.info("x", "auch auf stdout")
    out = capsys.readouterr().out
    assert "auch auf stdout" in out and "nur in der Datenbank" not in out


def test_protokoll_aufraeumen(ctx, clock):
    ctx.log.info("x", "alt")
    ctx.log.debug_on(1)
    ctx.log.debug("x", "alt-debug")
    clock.advance(4 * 86400)
    ctx.log.info("x", "neu")
    assert ctx.log.cleanup() == 1                              # Debug nach 3 Tagen weg, Info bleibt (30)
    clock.advance(31 * 86400)
    ctx.log.cleanup()
    assert [r["text"] for r in ctx.log.query()] == []


# ── Sicherung ──────────────────────────────────────────────────────────────────
def test_sicherung_taeglich_sieben(ctx, clock):
    for _ in range(9):
        ctx.backups.daily()
        clock.advance(86400)
    daily = [b for b in ctx.backups.list() if re.match(r"^zentrale-\d{8}\.db$", b["name"])]
    assert len(daily) == 7


def test_wiederherstellen_beim_naechsten_start(ctx, client, tmp_path):
    ctx.auth.add_room("Vorher")
    path = ctx.backups.create("20261111-120000")
    ctx.auth.add_room("Nachher")
    with pytest.raises(ValueError):
        ctx.backups.stage_restore(b"keine datenbank")
    ctx.backups.stage_restore(open(path, "rb").read())
    ctx.db.close()
    assert prepare_restore(ctx.data_dir)
    con = sqlite3.connect(os.path.join(ctx.data_dir, "zentrale.db"))
    names = [r[0] for r in con.execute("SELECT name FROM rooms")]
    con.close()
    assert names == ["Vorher"]
    old = [n for n in os.listdir(os.path.join(ctx.data_dir, "sicherungen")) if n.startswith("zentrale-")]
    assert len(old) >= 2                                       # die bisherige Datenbank liegt als Sicherung da


def test_download_nur_eigene_sicherungen(client, ctx):
    _admin(client)
    ctx.backups.create("20261111-120000")
    assert client.get("/admin/sicherungen/zentrale-20261111-120000.db").status_code == 200
    assert client.get("/admin/sicherungen/..%2Fzentrale.db").status_code == 404


# ── CLI ────────────────────────────────────────────────────────────────────────
def test_cli(tmp_path, capsys):
    d = str(tmp_path / "cli")
    assert cli.main(["--daten", d, "raum-anlegen", "Ton 3"]) == 0
    assert cli.main(["--daten", d, "code", "Ton 3"]) == 0
    out = capsys.readouterr().out
    code = re.search(r": ([A-Z0-9]{6})", out).group(1)
    assert cli.main(["--daten", d, "code", "Gibt es nicht"]) == 1
    assert cli.main(["--daten", d, "kennwort-zuruecksetzen"]) == 0
    temp = re.search(r": ([A-Z0-9]{12})", capsys.readouterr().out).group(1)
    con = sqlite3.connect(os.path.join(d, "zentrale.db"))
    logs = " ".join(r[0] for r in con.execute("SELECT text FROM log"))
    con.close()
    assert code not in logs and temp not in logs


def test_letzter_admin_bleibt(ctx):
    with pytest.raises(AuthError):
        ctx.auth.remove_admin("admin")
