"""Stufe 2 und 3: Chat, Belegen, Ereignisse, verteilte Einstellungen, Ordnerbeobachtung, Helmut-Ereignis."""
import json
import os
import re
import threading

import pytest

from app.watch import FolderWatch
from test_stufe1 import _admin, _pair, _until, _ws

MARK = "KENNMARKE-7f3a"


def _send(w, typ, daten, rid=None):
    m = {"typ": typ, "daten": daten}
    if rid:
        m["anfrage_id"] = rid
    w.send_json(m)


def _answer(w, rid, limit=40):
    for _ in range(limit):
        m = w.receive_json()
        if m.get("anfrage_id") == rid and m["typ"] in ("ok", "fehler"):
            return m
    raise AssertionError("keine Antwort")


def _collect(w, typ, n=1, limit=60):
    got = []
    for _ in range(limit):
        m = w.receive_json()
        if m["typ"] == typ:
            got.append(m["daten"])
            if len(got) >= n:
                return got
    raise AssertionError(f"zu wenige {typ}: {got}")


def _all_bytes(folder):
    out = b""
    for root, _, files in os.walk(folder):
        for f in files:
            out += open(os.path.join(root, f), "rb").read()
    return out


@pytest.fixture
def two(client, ctx):
    k1 = _pair(client, ctx, "Ton 3")
    k2 = _pair(client, ctx, "Schnitt 4", "schnittraum", "PC")
    c1, w1, _ = _ws(client, k1)
    c2, w2, _ = _ws(client, k2)
    yield w1, w2
    c1.__exit__(None, None, None)
    c2.__exit__(None, None, None)


# ── Chat ───────────────────────────────────────────────────────────────────────
def test_chat_alle_zustellen_und_loeschen(two, ctx):
    w1, w2 = two
    _send(w1, "chat_senden", {"gespraech": "alle", "text": f"Hallo {MARK}"}, "s1")
    mid = _answer(w1, "s1")["daten"]["id"]
    got = _collect(w2, "chat_nachricht")[0]
    assert got["text"] == f"Hallo {MARK}" and got["von"] == "Ton 3" and got["id"] == mid
    assert ctx.chat.waiting() == 1
    _send(w2, "chat_empfangen", {"ids": [mid]})
    _send(w1, "chat_empfangen", {"ids": [mid]})
    _send(w1, "chat_gelesen", {"gespraech": "alle", "bis": mid}, "g1")
    _answer(w1, "g1")
    assert ctx.chat.waiting() == 0                          # allen zugestellt → weg


def test_chat_nie_in_protokoll_sicherung_oder_hauptdatenbank(two, ctx):
    w1, w2 = two
    ctx.log.debug_on(1, by="test")                          # auch das Debug-Protokoll
    _send(w1, "chat_senden", {"gespraech": "alle", "text": f"Geheim {MARK}", "dringend": True}, "s1")
    _answer(w1, "s1")
    ctx.backups.create("20261111-235900")
    ctx.db.execute("PRAGMA wal_checkpoint(FULL)")
    logs = json.dumps(ctx.log.query(levels=["fehler", "warnung", "info", "debug"]), ensure_ascii=False)
    assert MARK not in logs and "Zeichen" in logs
    main = b"".join(open(os.path.join(ctx.data_dir, f), "rb").read() for f in os.listdir(ctx.data_dir)
                    if f.startswith("zentrale.db"))
    assert MARK.encode() not in main
    assert MARK.encode() not in _all_bytes(os.path.join(ctx.data_dir, "sicherungen"))


def test_nach_neustart_nur_unzugestellte(client, ctx):
    k1 = _pair(client, ctx, "Ton 3")
    k2 = _pair(client, ctx, "Schnitt 4", "schnittraum", "PC")
    c1, w1, _ = _ws(client, k1)
    _send(w1, "chat_senden", {"gespraech": "alle", "text": "während Schnitt 4 weg war"}, "s1")
    _answer(w1, "s1")
    c1.__exit__(None, None, None)
    c2, w2, _ = _ws(client, k2)                               # Gerät startet neu: holt nur Wartendes
    _send(w2, "chat_abholen", {}, "a1")
    msgs = _collect(w2, "chat_nachricht")
    assert [m["text"] for m in msgs] == ["während Schnitt 4 weg war"]
    _send(w2, "chat_empfangen", {"ids": [msgs[0]["id"]]})
    c2.__exit__(None, None, None)
    c3, w3, _ = _ws(client, k2)
    _send(w3, "chat_abholen", {}, "a2")
    assert _answer(w3, "a2")["typ"] == "ok"
    c3.__exit__(None, None, None)
    assert not [m for m in ctx.chat.pending_for("Schnitt 4")]


def test_haltezeit(ctx, clock):
    ctx.auth.add_room("A")
    ctx.chat.add("alle", "A", "x", ["A", "B"])
    clock.advance(23 * 3600)
    assert ctx.chat.expire(24) == 0
    clock.advance(2 * 3600)
    assert ctx.chat.expire(24) == 1 and ctx.chat.waiting() == 0


def test_raum_an_raum_nur_fuer_die_beiden(client, ctx):
    ks = [_pair(client, ctx, r) for r in ("Ton 1", "Ton 2", "Ton 3")]
    c1, w1, _ = _ws(client, ks[0])
    c3, w3, _ = _ws(client, ks[2])
    _send(w1, "chat_oeffnen", {"mit": "Ton 2"}, "o1")
    cid = _answer(w1, "o1")["daten"]["id"]
    _send(w3, "chat_senden", {"gespraech": cid, "text": "eingeschlichen"}, "x")
    assert _answer(w3, "x")["typ"] == "fehler"
    _send(w1, "chat_oeffnen", {"mit": "Gibt es nicht"}, "o2")
    assert _answer(w1, "o2")["typ"] == "fehler"
    c1.__exit__(None, None, None)
    c3.__exit__(None, None, None)


def test_chat_aus_loescht_und_sperrt(client, ctx, two):
    w1, w2 = two
    _send(w1, "chat_senden", {"gespraech": "alle", "text": "vorher"}, "s1")
    _answer(w1, "s1")
    token = _admin(client)
    r = client.post("/admin/chat", data={"csrf": token, "hinweis": "", "haltezeit_h": "24"}, follow_redirects=False)
    assert r.status_code == 303
    assert ctx.chat.waiting() == 0
    assert _collect(w2, "chat_aus")
    _send(w1, "chat_senden", {"gespraech": "alle", "text": "nachher"}, "s2")
    assert _answer(w1, "s2")["typ"] == "fehler"


def test_hinweis_immer_da(client, ctx):
    token = _admin(client)
    client.post("/admin/chat", data={"csrf": token, "aktiv": "1", "art_alle": "1", "hinweis": "",
                                     "haltezeit_h": "24"})
    assert "keinem Fall private oder sensible Daten" in ctx.flows.client_settings()["chat"]["hinweis"]
    client.post("/admin/chat", data={"csrf": token, "aktiv": "1", "hinweis": "Eigener Hinweis", "haltezeit_h": "24"})
    assert ctx.flows.client_settings()["chat"]["hinweis"] == "Eigener Hinweis"


# ── Belegen ────────────────────────────────────────────────────────────────────
def test_belegen_erste_anfrage_gewinnt_parallel(client, ctx):
    rooms = [f"Ton {i}" for i in range(1, 9)]
    keys = [_pair(client, ctx, r) for r in rooms]
    results = []
    lock = threading.Lock()
    start = threading.Barrier(len(keys))

    def go(key):
        with client.websocket_connect("/ws") as w:
            w.send_json({"typ": "anmelden", "daten": {"schluessel": key}})
            _until(w, "willkommen")
            start.wait()
            w.send_json({"typ": "belegen", "anfrage_id": "b", "daten": {"beitrag_id": "film1", "titel": "Film"}})
            m = _answer(w, "b")
            with lock:
                results.append(m["typ"])
    threads = [threading.Thread(target=go, args=(k,)) for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert sorted(results) == ["fehler"] * 7 + ["ok"]
    assert len(ctx.work.claims()) == 1


def test_belegen_freigeben_systemmeldungen_und_beitraege(client, ctx, two):
    w1, w2 = two
    ctx.auth.set_aliases(ctx.auth.room_id("Schnitt 4"), ["SR4"])
    _send(w1, "belegen", {"beitrag_id": "film1", "titel": "Film", "schnittraum": "sr4"}, "b1")
    assert _answer(w1, "b1")["typ"] == "ok"
    conv = _collect(w2, "chat_gespraech")[0]
    assert conv["art"] == "beitrag" and sorted(conv["raeume"]) == ["Schnitt 4", "Ton 3"]
    msg = _collect(w2, "chat_nachricht")[0]
    assert msg["system"] and "belegt von Ton 3" in msg["text"]
    _send(w2, "beitraege", {}, "m1")
    items = _answer(w2, "m1")["daten"]["beitraege"]
    assert items[0]["schritt"] == "belegt" and items[0]["mischraum"] == "Ton 3"
    _send(w1, "ereignis", {"art": "audio_export_fertig", "beitrag_id": "film1", "titel": "Film"}, "e1")
    _answer(w1, "e1")
    assert "Audio Export fertig" in _collect(w2, "chat_nachricht")[0]["text"]
    _send(w1, "freigeben", {"beitrag_id": "film1", "grund": "Raumwechsel"}, "f1")
    _answer(w1, "f1")
    assert "Raumwechsel" in _collect(w2, "chat_nachricht")[0]["text"]
    assert ctx.work.claims() == []


def test_rueckkehr_meldet_belegungen_und_konflikte(client, ctx, clock):
    k1 = _pair(client, ctx, "Ton 3")
    k2 = _pair(client, ctx, "Ton 1")
    c1, w1, _ = _ws(client, k1)
    c2, w2, _ = _ws(client, k2)
    try:
        _send(w2, "belegen", {"beitrag_id": "film2", "titel": "Zwei"}, "b")
        _answer(w2, "b")
        _send(w1, "belegen", {"beitrag_id": "alt", "titel": "Alt"}, "b0")
        _answer(w1, "b0")
        _send(w1, "belegen", {"beitrag_id": "frisch", "titel": "Frisch"}, "b1")
        _answer(w1, "b1")
        ctx.db.execute("UPDATE claims SET t=t-600 WHERE beitrag='alt'")       # „alt“ ist älter als 2 min
        # Ton 3 kommt zurück: hält „film1“ (frei) und „film2“ (laut Zentrale Ton 1); „alt“ nicht mehr, „frisch“
        # fehlt in der Meldung, ist aber eben erst belegt (Ottilie las ihre Liste vorher) – bleibt
        _send(w1, "belegungen_melden", {"belegungen": [{"beitrag_id": "film1", "titel": "Eins"},
                                                       {"beitrag_id": "film2", "titel": "Zwei"}]}, "r")
        konflikte = _answer(w1, "r")["daten"]["konflikte"]
        assert konflikte == [{"beitrag_id": "film2", "titel": "Zwei", "belegt_von": "Ton 1"}]
        claims = {c["beitrag"]: c["room"] for c in ctx.work.claims()}
        assert claims == {"film1": "Ton 3", "film2": "Ton 1", "frisch": "Ton 3"}     # nicht selbst entschieden
        assert ctx.work.conflicts()[0]["holder"] == "Ton 1"
        _send(w1, "belegen", {"beitrag_id": "film2"}, "x")
        assert _answer(w1, "x")["daten"]["belegt_von"] == "Ton 1"
    finally:
        c1.__exit__(None, None, None)
        c2.__exit__(None, None, None)


def test_admin_loest_belegung_mit_grund(client, ctx):
    ctx.work.claim("film1", "Ton 3", "Film")
    token = _admin(client)
    r = client.post("/admin/belegungen", data={"csrf": token, "beitrag": "film1", "grund": ""},
                    follow_redirects=False)
    assert "Grund" in r.headers["location"] and ctx.work.claims()
    client.post("/admin/belegungen", data={"csrf": token, "beitrag": "film1", "grund": "Mac aus"})
    assert ctx.work.claims() == []
    assert "Mac aus" in json.dumps(ctx.log.query(), ensure_ascii=False)


# ── Verteilte Einstellungen ────────────────────────────────────────────────────
def test_uebergabeordner_einstellungen_verteilen(client, ctx, two):
    w1, w2 = two
    token = _admin(client)
    r = client.post("/admin/uebergabeordner", data={
        "csrf": token, "system.uebergabeordner.betriebsart": "uebergabeordner",
        "system.uebergabeordner.netzadresse": "smb://server/freigabe/ordner",
        "system.uebergabeordner.wartezeit_s": "5", "system.uebergabeordner.ordner_ignorieren": "_, ."},
        follow_redirects=False)
    assert r.status_code == 303 and "verteilt" in r.headers["location"]
    werte = _collect(w1, "einstellungen")[0]["werte"]
    assert werte["system.uebergabeordner.netzadresse"] == "smb://server/freigabe/ordner"
    assert werte["system.uebergabeordner.wartezeit_s"] == 5 and werte["system.uebergabeordner.ordner_ignorieren"] \
        == ["_", "."]
    r = client.post("/admin/uebergabeordner", data={"csrf": token, "system.uebergabeordner.netzadresse":
                                                    "https://boese"}, follow_redirects=False)
    assert r.status_code == 400 and "smb" in r.text and 'value="https://boese"' in r.text   # Eingabe bleibt
    r = client.post("/admin/uebergabeordner", data={"csrf": token, "system.uebergabeordner.wartezeit_s": "999"},
                    follow_redirects=False)
    assert r.status_code == 400 and "zwischen" in r.text and 'aria-invalid="true"' in r.text


# ── Stufe 3 ────────────────────────────────────────────────────────────────────
def _film(root, name, origin="SR4", last=True):
    f = root / name
    f.mkdir()
    (f / "f.aaf").write_bytes(b"a" * 10)
    if last:
        (f / "uebergabe.json").write_text(json.dumps({
            "format": "uebergabe/1", "beitrag": {"id": name, "titel": f"Titel {name}"},
            "herkunft": {"schnittraum": origin},
            "dateien": [{"typ": "aaf", "name": "f.aaf", "groesse": 10}]}))
    return f


def test_ordnerbeobachtung(tmp_path):
    root = tmp_path / "uebergabe"
    root.mkdir()
    _film(root, "schon_da")
    seen = []
    t = [100.0]
    w = FolderWatch(str(root), lambda n, ti, o, nr=0: seen.append((n, ti, o)), lambda: 3.0, clock=lambda: t[0],
                    wall=lambda: 0.0)
    w.seed()
    assert seen == []                                      # beim Start Vorhandenes nicht als neu melden
    _film(root, "neu")
    (root / "._neu").write_bytes(b"x")
    (root / "_privat").mkdir()
    assert w.scan() == []                                  # Wartezeit läuft
    t[0] += 3.1
    assert w.scan() == ["neu"] and seen == [("neu", "Titel neu", "SR4")]
    assert w.scan() == []                                  # nur einmal
    os.symlink(root / "neu", root / "verweis")
    t[0] += 10
    assert w.scan() == []


def test_neuer_beitrag_ueber_helmut_ereignis(client, ctx, two):
    w1, w2 = two
    ctx.auth.set_aliases(ctx.auth.room_id("Schnitt 4"), ["SR4"])
    r = client.post("/api/helmut/ereignis", json={"ereignis": "beitrag_uebergeben", "ordner": "f"})
    assert r.status_code == 401
    token = _admin(client)
    page = client.post("/admin/helmut", data={"csrf": token, "neuer_schluessel": "1"}).text
    key = re.search(r'word-break:break-all">([A-Za-z0-9_\-]{30,})<', page).group(1)
    r = client.post("/api/helmut/ereignis", headers={"Authorization": f"Bearer {key}"},
                    json={"ereignis": "beitrag_uebergeben", "ordner": "Film_1", "titel": "Film 1",
                          "schnittraum": "SR4"})
    assert r.status_code == 200
    got = _collect(w1, "neuer_beitrag")[0]
    assert got["beitrag_id"] == "Film_1" and got["quelle"] == "Helmut"
    item = _collect(w2, "beitrag")[0]
    assert item["schritt"] == "uebergeben" and item["schnittraum"] == "Schnitt 4"
    assert key not in json.dumps(ctx.log.query(), ensure_ascii=False)


def test_einmal_verarbeitung_speichert_keine_fehler(client, ctx):
    """Sicherheit 11.11.2026: Fehlerantworten (mit langem „typ“) füllten den Speicher der Einmal-Verarbeitung."""
    key = _pair(client, ctx)
    c, w, _ = _ws(client, key)
    try:
        texts = []
        for i in range(5):
            w.send_json({"typ": "x" * 5000, "anfrage_id": f"r{i}", "daten": {}})
            texts.append(_answer(w, f"r{i}")["daten"]["text"])
        assert all(len(t) < 80 for t in texts), texts
        assert not ctx.hub._done.get(ctx.auth.device(key).id)
    finally:
        c.__exit__(None, None, None)


def test_ordnerbeobachtung_neue_fassung_und_leerer_ordner(tmp_path):
    root = tmp_path / "uebergabe"
    root.mkdir()
    f = _film(root, "film")
    seen, states = [], []
    t = [100.0]
    w = FolderWatch(str(root), lambda n, ti, o, nr=0: seen.append((n, nr)), lambda: 3.0, clock=lambda: t[0],
                    wall=lambda: 0.0)
    w.on_state = states.append
    w.scan()
    t[0] += 4
    assert w.scan() == ["film"]
    data = json.loads((f / "uebergabe.json").read_text())
    (f / "uebergabe.json").write_text(json.dumps({**data, "uebergabe_nr": 2}))
    t[0] += 1
    assert w.scan() == []                                   # Wartezeit für die neue JSON
    t[0] += 4
    assert w.scan() == ["film"] and seen[-1] == ("film", 2)
    import shutil
    shutil.move(str(f), str(tmp_path / "weg"))              # Einbindung des Hosts weg: Ordner leer
    assert w.scan() == [] and "leer" in w.state and states[-1] == w.state
    shutil.move(str(tmp_path / "weg"), str(f))
    t[0] += 10
    assert w.scan() == [] and w.state == "beobachtet"     # nicht noch einmal als neu gemeldet


def test_belegen_nur_aus_mischraeumen_und_mit_angabe(two, ctx):
    w1, w2 = two
    _send(w2, "belegen", {"beitrag_id": "x"}, "b")         # Schnitt 4 (Web-App)
    assert "Mischräume" in _answer(w2, "b")["daten"]["text"]
    _send(w1, "belegen", {"beitrag_id": "x"}, "c")
    _answer(w1, "c")


def test_keine_gefaelschten_protokollzeilen(ctx, capsys):
    ctx.log.info("x", "harmlos\n2027-01-15 09:00:00 FEHLER  kopplung: gefälscht")
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and "FEHLER  kopplung" in out          # eine Zeile, Text bleibt sichtbar


def test_uebergeben_nur_fuer_den_eigenen_schnittraum(client, ctx):
    ctx.auth.add_room("Schnitt 1", "schnittraum")
    ctx.auth.set_aliases(ctx.auth.room_id("Schnitt 1"), ["SR1"])
    k = _pair(client, ctx, "Schnitt 4", "schnittraum", "PC")
    c, w, _ = _ws(client, k)
    try:
        _send(w, "ereignis", {"art": "uebergeben", "beitrag_id": "f1", "titel": "F", "schnittraum": "SR1"}, "u")
        _answer(w, "u")
        assert ctx.work.get_item("f1")["origin_room"] == "Schnitt 4"
    finally:
        c.__exit__(None, None, None)


def test_alte_freigabe_loest_keine_fremde_belegung(client, ctx):
    k3, k4 = _pair(client, ctx, "Ton 3"), _pair(client, ctx, "Ton 4")
    c3, w3, _ = _ws(client, k3)
    c4, w4, _ = _ws(client, k4)
    try:
        _send(w4, "belegen", {"beitrag_id": "X"}, "b")
        _answer(w4, "b")
        _send(w3, "freigeben", {"beitrag_id": "X"}, "f")
        assert _answer(w3, "f")["daten"].get("uebergangen")
        assert [c["room"] for c in ctx.work.claims()] == ["Ton 4"]
        for i in range(40):                                     # Bestätigungen bremsen das Belegen nicht
            _send(w4, "chat_empfangen", {"ids": [f"x{i}"]})
        _send(w4, "belegen", {"beitrag_id": "Y"}, "y")
        assert _answer(w4, "y", limit=200)["typ"] == "ok"
    finally:
        c3.__exit__(None, None, None)
        c4.__exit__(None, None, None)


def test_gleiche_fassung_setzt_nichts_zurueck(ctx):
    import asyncio
    ctx.work.item("film1", "in_mischung", "Film", mixroom="Ton 3")
    asyncio.run(ctx.flows.handed_over("film1", "Film", "", "Übergabeordner", 1))
    assert ctx.work.get_item("film1")["step"] == "in_mischung"            # Nummer nur nachgetragen
    asyncio.run(ctx.flows.handed_over("film1", "Film", "", "Übergabeordner", 2))
    assert ctx.work.get_item("film1")["step"] == "uebergeben"             # wirklich neue Fassung


# ── Mehrere Übergabeordner (Jens 13.11.2026) ───────────────────────────────────
def test_weitere_uebergabeordner_formular_und_verteilen(client, ctx, two):
    from app.work import AUTH_KINDS, folder_lines, parse_folders
    w1, _w2 = two
    token = _admin(client)
    text = "Schnitt Nord | smb://srv/nord | kerberos\nArchiv | | | /Volumes/Archiv"
    r = client.post("/admin/uebergabeordner", data={"csrf": token, "system.uebergabeordner.weitere_ordner": text},
                    follow_redirects=False)
    assert r.status_code == 303 and "verteilt" in r.headers["location"]
    werte = _collect(w1, "einstellungen")[0]["werte"]
    assert werte["system.uebergabeordner.weitere_ordner"] == [
        {"name": "Schnitt Nord", "netzadresse": "smb://srv/nord", "netz_anmeldung": "kerberos"},
        {"name": "Archiv", "netz_anmeldung": "gast", "wurzelordner": "/Volumes/Archiv"}]
    assert "Schnitt Nord | smb://srv/nord | kerberos" in client.get("/admin/uebergabeordner").text
    assert parse_folders(folder_lines(werte["system.uebergabeordner.weitere_ordner"]), AUTH_KINDS) == \
        werte["system.uebergabeordner.weitere_ordner"]                       # Formular ↔ Wert ohne Verlust
    for bad, hint in (("Nord | https://x", "smb"), ("Nord | smb://a\nnord | smb://b", "doppelt"),
                      ("Nord | smb://a | root", "Anmeldung"), ("Nord", "Netzadresse oder Pfad"),
                      ("/// | smb://a", "doppelt oder ohne")):
        with pytest.raises(ValueError, match=hint):
            parse_folders(bad, AUTH_KINDS)
    r = client.post("/admin/uebergabeordner", data={"csrf": token, "system.uebergabeordner.weitere_ordner":
                                                    "Nord | https://x"}, follow_redirects=False)
    assert r.status_code == 400 and "smb" in r.text and "Nord | https://x" in r.text
    r = client.post("/admin/uebergabeordner", data={"csrf": token, "system.uebergabeordner.weitere_ordner":
                                                    "Nord | smb://a | schluesselbund"}, follow_redirects=False)
    assert r.status_code == 400 and "Anmeldung" in r.text            # weitere Ordner: nur Gast oder Kerberos
    # Ordner mit Belegung: nicht umbenennen oder entfernen (die Kennung hängt am Namen)
    ctx.work.claim("schnitt-nord/film1", "Ton 3", "Film 1")
    r = client.post("/admin/uebergabeordner", data={"csrf": token, "system.uebergabeordner.weitere_ordner":
                                                    "Nord-Ost | smb://srv/nord | kerberos"}, follow_redirects=False)
    assert r.status_code == 400 and "Film 1" in r.text and "nichts mehr in Arbeit" in r.text
    ctx.work.release("schnitt-nord/film1")
    r = client.post("/admin/uebergabeordner", data={"csrf": token, "system.uebergabeordner.weitere_ordner":
                                                    "Nord-Ost | smb://srv/nord | kerberos"}, follow_redirects=False)
    assert r.status_code == 303


def test_beobachtung_mehrerer_ordner_meldet_mit_kennung(tmp_path):
    from app import watch
    assert watch.roots_from("/mnt/uebergabe", "Schnitt Nord=/mnt/nord; kaputt ;=/x;Ärger=/mnt/a") == \
        [("", "/mnt/uebergabe"), ("schnitt-nord", "/mnt/nord"), ("aerger", "/mnt/a")]
    assert watch.roots_from("", "") == []
    from tonmischung.core.naming import slug as ottilie_slug                # dieselbe Kennung wie in Ottilie
    from app.work import slug
    for name in ("Schnitt Nord", "Ärger & Ödnis", "  A--B  ", "Studio 7/8"):
        assert slug(name) == ottilie_slug(name)
