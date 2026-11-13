"""Ottilie ↔ Zentrale über einen echten Server auf 127.0.0.1 (uvicorn im Thread, kein Netz nach außen)."""
import json
import os
import socket
import threading
import time

import pytest

uvicorn = pytest.importorskip("uvicorn")
from app.main import create_app  # noqa: E402  (Zentrale, pythonpath zentrale/)

from tonmischung.core.peers import MIXING, PeerStatus  # noqa: E402
from tonmischung.zentrale import device as D  # noqa: E402
from tonmischung.zentrale.link import (CONNECTED, OTHER_ADDRESS, REVOKED, UNPAIRED, WAITING,  # noqa: E402
                                       ZentraleLink)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    app = create_app(str(tmp_path / "zentrale"), background=False)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on",
                                        ws="websockets-sansio"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield app.state.ctx, f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _paired(ctx, address, room, path):
    if ctx.auth.room_id(room) is None:
        ctx.auth.add_room(room)
    code = ctx.auth.pairing_code(ctx.auth.room_id(room))
    dev = D.pair(address, code, f"Mac {room}")
    D.write(dev, where=str(path))
    return dev


def test_adresse_nur_https_ausser_localhost():
    assert D.check_address("https://Server.ts.net:8443/") == "https://server.ts.net:8443"
    assert D.check_address("http://127.0.0.1:8470") == "http://127.0.0.1:8470"
    for bad in ("http://server:8470", "ftp://x", "https://user:pw@server", "https://server/?k=1", ""):
        with pytest.raises(D.PairingError):
            D.check_address(bad)


def test_koppeln_ablegen_lesen(server, tmp_path):
    ctx, address = server
    path = tmp_path / "geraet.json"
    dev = _paired(ctx, address, "Ton 3", path)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    got = D.read(str(path))
    assert got.room == "Ton 3" and got.key == dev.key and got.matches(address + "/")
    with pytest.raises(D.PairingError, match="ungültig oder abgelaufen"):
        D.pair(address, "ZZZZZZ")


def test_rechnerweit_ueber_den_system_schluesselbund():
    """Standard ist der System-Schlüsselbund (Administrator-Dialog) – hier ein Ersatz, der nur mitschreibt."""
    seen = []
    D.write(D.DeviceKey("https://z.example:8443", "Ton 3", "Mac", "k" * 43), where=D.MACHINE, install=seen.append)
    data = json.loads(seen[0])
    assert data["schluessel"] == "k" * 43 and data["format"] == D.FORMAT
    assert D._decode(seen[0].encode().hex()).key == "k" * 43           # -w liefert evtl. hexkodiert


def test_geraetename_ohne_person():
    assert D.device_name().startswith("Ottilie-Mac ") and socket.gethostname() not in D.device_name()


def test_zwei_macs_sehen_sich_ohne_personen(server, tmp_path):
    ctx, address = server
    a = _paired(ctx, address, "Ton 3", tmp_path / "a.json")
    b = _paired(ctx, address, "Ton 1", tmp_path / "b.json")
    events = []
    la = ZentraleLink(lambda: address, "Ton 3", lambda k, d: events.append(k), read_device=lambda: a)
    lb = ZentraleLink(lambda: address, "Ton 1", read_device=lambda: b)
    la.start()
    lb.start()
    try:
        assert _wait(lambda: la.state == CONNECTED and lb.state == CONNECTED)
        la.publish(PeerStatus("Ton 3", "Jens", MIXING, "Film", "p1", 40, mode="uebergabeordner"))
        assert _wait(lambda: "Ton 3" in lb.peers() and lb.peers()["Ton 3"].state == MIXING)
        p = lb.peers()["Ton 3"]
        assert (p.title, p.pct, p.mode, p.mixer) == ("Film", 40, "uebergabeordner", "")    # kein Mischername
        assert "zentrale_einstellungen" in events
        dev_id = ctx.auth.device(a.key).id
        ctx.auth.revoke(dev_id)
        import asyncio
        fut = asyncio.run_coroutine_threadsafe(ctx.hub.revoke(dev_id), ctx.hub.loop)
        fut.result(5)
        assert _wait(lambda: la.state == REVOKED)
        assert _wait(lambda: "Ton 3" not in lb.peers())
    finally:
        la.stop()
        lb.stop()


def test_schluessel_nie_an_eine_andere_adresse(server, tmp_path):
    ctx, address = server
    dev = _paired(ctx, address, "Ton 3", tmp_path / "a.json")
    sent = []

    def connect(url):
        sent.append(url)
        raise OSError("nie aufrufen")
    link = ZentraleLink(lambda: "https://boese.example:8443", "Ton 3", read_device=lambda: dev, connect=connect)
    link.start()
    try:
        assert _wait(lambda: link.state == OTHER_ADDRESS)
        assert sent == []
    finally:
        link.stop()
    unpaired = ZentraleLink(lambda: address, "Ton 3", read_device=lambda: None, connect=connect)
    unpaired.start()
    try:
        assert _wait(lambda: unpaired.state == UNPAIRED) and sent == []
    finally:
        unpaired.stop()


def test_ohne_adresse_aus_und_anfragen_scheitern_sauber():
    from tonmischung.zentrale.link import OFF, ZentraleUnavailable
    link = ZentraleLink(lambda: "", "Ton 3", read_device=lambda: None)
    link.start()
    try:
        assert _wait(lambda: link.state == OFF)
        assert not link.available and link.peers() == {}
        with pytest.raises(ZentraleUnavailable):
            link.request("belegen", {})
    finally:
        link.stop()


def test_ohne_code_anmelden_warten_zuordnen_raum(server, tmp_path):
    """Jens 13.11.2026: anmelden ohne Code → wartet mit Kennung → Administration ordnet zu → Raum aus der Zentrale."""
    import asyncio
    ctx, address = server
    rid = ctx.auth.add_room("MacBook")
    dev = D.register(address, "Ottilie-Mac abc123")
    assert dev.room == "" and dev.fingerprint == ctx.auth.pending_list()[0].fingerprint
    events = []
    link = ZentraleLink(lambda: address, "macbookpro", lambda k, d: events.append((k, d)), read_device=lambda: dev)
    link.start()
    try:
        assert _wait(lambda: link.state == WAITING)
        assert link.fingerprint == dev.fingerprint and dev.fingerprint in link.text()
        assert not link.available and link.peers() == {}              # sieht nichts
        pid = ctx.auth.pending_list()[0].id
        asyncio.run_coroutine_threadsafe(ctx.flows.assign_device(pid, rid, "admin"), ctx.hub.loop).result(5)
        assert _wait(lambda: link.state == CONNECTED, timeout=10)     # neu verbunden, ohne Wartezeit
        assert link.server_room == "MacBook"
        assert ("zentrale_raum", {"raum": "MacBook"}) in events
    finally:
        link.stop()
