"""Webtrigger und Stream-Deck-Socket wie PunchBuddy (Plan PunchBuddy-Übernahme, Schritt 3)."""
import os
import socket
import urllib.request

import pytest

from tonmischung.punch.triggers import (LineSocket, TriggerConfig, Webtrigger, is_punchbuddy, parse_line,
                                        socket_alive, token_ok)


def _dispatcher():
    gerufen = []

    def dispatch(cmd, args):
        gerufen.append((cmd, args))
        if cmd == "list":
            return True, "goto_start,list,ping,play,preset,record_a"
        if cmd == "preset":
            return (True, f"preset {args[0]} loaded") if args[0] == "1" else (False, f"invalid preset index: {args[0]}")
        if cmd.startswith("vocaster"):
            return False, "no vocaster connected"
        if cmd == "gibtsnicht":
            return False, "unknown command: gibtsnicht"
        if cmd == "status":
            return True, "ok export_running=False import_running=False http_port=8899"
        return True, f"{cmd} queued"
    return dispatch, gerufen


def _trigger(**cfg):
    dispatch, gerufen = _dispatcher()
    t = Webtrigger(dispatch, lambda: TriggerConfig(**cfg), spawn=lambda fn: fn())
    return t, gerufen


@pytest.mark.parametrize("pfad, befehl", [("/trigger", "record_a"), ("/trigger2", "record_b"), ("/play", "play"),
                                          ("/play_custom", "play_custom"), ("/start", "goto_start"),
                                          ("/move_audio", "move_audio"), ("/export_aaf_embedded", "export_aaf"),
                                          ("/import", "holen")])
def test_pfade_wie_punchbuddy(pfad, befehl):
    t, gerufen = _trigger()
    assert t.handle(pfad, {}) == (200, "OK")
    assert gerufen == [(befehl, [])]


def test_interplay_gibt_es_nicht():
    t, gerufen = _trigger()
    assert t.handle("/export_interplay", {}) == (404, "not found")
    assert t.handle("/export", {}) == (404, "not found") and gerufen == []


def test_befehl_generisch_und_liste():
    t, gerufen = _trigger()
    assert t.handle("/command/preset/1", {}) == (200, "preset 1 loaded")
    assert t.handle("/command?c=preset&arg=1", {}) == (200, "preset 1 loaded")
    assert t.handle("/command/gibtsnicht", {}) == (400, "unknown command: gibtsnicht")
    assert t.handle("/command", {}) == (400, "missing command name")
    assert t.handle("/commands", {})[1].startswith("goto_start,list")
    assert t.handle("/status", {}) == (200, "ok export_running=False import_running=False http_port=8899")


def test_presets_und_vocaster_antworten():
    t, _ = _trigger()
    assert t.handle("/preset/1", {}) == (200, "Preset 1 geladen")
    assert t.handle("/preset/9", {}) == (400, "Ungueltiger Preset Index")
    assert t.handle("/preset/x", {}) == (400, "Ungueltiger Preset Index")
    assert t.handle("/vocaster/phantom/on", {}) == (503, "Kein Vocaster angeschlossen")
    assert t.handle("/vocaster/irgendwas", {}) == (404, "not found")


def test_token():
    t, gerufen = _trigger(token="geheim")
    assert t.handle("/play", {}) == (401, "Unauthorized")
    assert t.handle("/play?token=falsch", {}) == (401, "Unauthorized")
    assert t.handle("/play?token=geheim", {}) == (200, "OK")
    assert t.handle("/play", {"X-Auth-Token": "geheim"}) == (200, "OK")
    assert len(gerufen) == 2
    assert token_ok("", "") and not token_ok("a", "")


def test_id_code_je_platz():
    t, gerufen = _trigger(id_code="T3-7F2A")
    assert t.handle("/play?id=T3-7F2A", {}) == (200, "OK")
    assert t.handle("/play?id=T2-0000", {})[0] == 403
    assert t.handle("/play", {}) == (200, "OK")                    # ohne Pflicht: ohne Code erlaubt
    t, gerufen = _trigger(id_code="T3-7F2A", require_id=True)
    assert t.handle("/play", {}, "10.1.2.3") == (403, "ID-Code fehlt") and gerufen == []
    assert t.handle("/play?id=T3-7F2A", {}, "10.1.2.3") == (200, "OK")


def test_id_code_nur_fuer_aufrufe_uebers_netz():
    """Jens 09.10.2026: „Nur mit ID-Code“ gilt für Webtrigger übers Netzwerk, lokale brauchen keinen."""
    from tonmischung.punch.triggers import is_local
    t, gerufen = _trigger(id_code="T3-7F2A", require_id=True)
    assert t.handle("/play", {}, "127.0.0.1") == (200, "OK") and t.handle("/play", {}, "::1") == (200, "OK")
    assert t.handle("/play?id=T2-0000", {}, "127.0.0.1")[0] == 403          # falscher Platz bleibt falsch
    t, gerufen = _trigger(require_id=True)                                   # kein Code vergeben
    assert t.handle("/play", {}, "10.1.2.3")[0] == 403 and t.handle("/play?id=X", {}, "10.1.2.3")[0] == 403
    assert t.handle("/play", {}, "127.0.0.1") == (200, "OK")
    assert is_local("::ffff:127.0.0.1") and not is_local("192.168.1.20")


def test_server_echt_auf_freiem_port():
    dispatch, gerufen = _dispatcher()
    t = Webtrigger(dispatch, lambda: TriggerConfig(port=0), spawn=lambda fn: fn())
    port = t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/command/ping", timeout=5) as r:
            assert r.status == 200 and r.read().decode() == "ping queued"
    finally:
        t.stop()


@pytest.fixture
def sock_path():
    path = f"/tmp/tm-test-{os.getpid()}.sock"                    # AF_UNIX: höchstens 104 Zeichen
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _send(path, text):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(path)
        s.sendall(text.encode())
        return s.recv(4096).decode()


def test_socket_zeilenprotokoll(sock_path):
    dispatch, gerufen = _dispatcher()
    ls = LineSocket(dispatch, sock_path)
    ls.start()
    try:
        assert oct(os.stat(sock_path).st_mode & 0o777) == "0o600"
        assert _send(sock_path, "play\n") == "OK play queued\n"
        assert _send(sock_path, "preset 9 token=abc\n") == "ERR invalid preset index: 9\n"
        assert gerufen[-1] == ("preset", ["9"])                    # token= ignoriert
        assert _send(sock_path, "\n") == "ERR empty\n"
        assert _send(sock_path, "x" * 5000) == "ERR line too long\n"
    finally:
        ls.stop()
    assert not os.path.exists(sock_path)


def test_belegter_socket_wird_nicht_weggenommen(sock_path):
    dispatch, _ = _dispatcher()
    erste = LineSocket(dispatch, sock_path)
    erste.start()
    try:
        with pytest.raises(RuntimeError, match="läuft PunchBuddy noch"):
            LineSocket(dispatch, sock_path).start()
        assert socket_alive(sock_path)
    finally:
        erste.stop()


def test_reste_eines_absturzes_werden_ersetzt(sock_path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(sock_path)
    s.close()                                                   # Datei bleibt, niemand hört zu
    dispatch, _ = _dispatcher()
    ls = LineSocket(dispatch, sock_path)
    ls.start()
    ls.stop()


def test_fremde_socketdatei_bleibt_beim_beenden(sock_path):
    dispatch, _ = _dispatcher()
    ls = LineSocket(dispatch, sock_path)
    ls.start()
    ls._srv.close()
    os.unlink(sock_path)
    fremd = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    fremd.bind(sock_path)                                       # eine andere Instanz hat übernommen
    try:
        ls.stop()
        assert os.path.exists(sock_path)
    finally:
        fremd.close()


def test_zeile_zerlegen_und_punchbuddy_erkennen():
    assert parse_line("preset 3 token=x") == ("preset", ["3"])
    assert parse_line("   ") is None
    assert is_punchbuddy("/Applications/PunchBuddy.app/Contents/MacOS/PunchBuddy\n/usr/bin/login")
    assert is_punchbuddy("/usr/bin/python3 auto_punch_in.py")
    assert not is_punchbuddy("/Applications/PunchBuddy_Watchdog.app/Contents/MacOS/PunchBuddy_Watchdog")
    assert not is_punchbuddy("/Applications/Pro Tools.app/Contents/MacOS/Pro Tools")


# ── Einbindung in die App ──────────────────────────────────────────────────
def _settings(tmp_path, **werte):
    from tonmischung.config.settings import Settings
    s = Settings(local_path=tmp_path / "e.json")
    for k, v in werte.items():
        s.set_local(k, v)
    return s


def test_token_nur_im_schluesselbund(tmp_path):
    from tonmischung.app.triggers_app import ensure_token
    bund = {}
    read = lambda svc, acc: bund.get((svc, acc), "")                       # noqa: E731
    write = lambda svc, acc, tok: bund.__setitem__((svc, acc), tok)        # noqa: E731
    s = _settings(tmp_path)
    assert ensure_token(s, read, write) == "" and bund == {}               # 127.0.0.1: ohne Token
    s.set_local("system.streamdeck_webtrigger.bind_adresse", "netzwerk")
    token = ensure_token(s, read, write)
    assert len(token) >= 16 and bund[("ottilie-webtrigger", "webtrigger")] == token
    assert ensure_token(s, read, write) == token                            # nicht neu erzeugt
    assert token not in (tmp_path / "e.json").read_text()                   # nie in den Einstellungen


def test_konfiguration_mit_id_code_des_raums(tmp_path):
    from tonmischung.app.triggers_app import trigger_config
    s = _settings(tmp_path, **{"system.raeume_nutzer.raeume": [
        {"name": "Ton 3", "rechnername": "ton3-mac", "id_code": "T3-7F2A"}, {"name": "Ton 2", "rechnername": "ton2-mac"}]})
    cfg = trigger_config(s, "Ton 3", "tok")
    assert (cfg.port, cfg.bind, cfg.token, cfg.id_code, cfg.require_id) == (8899, "127.0.0.1", "tok", "T3-7F2A", False)
    assert trigger_config(s, "Ton 2", "").id_code == ""


def test_import_holt_den_gewaehlten_beitrag(tmp_path):
    from tonmischung.app.triggers_app import add_fetch_command

    class Ctx:
        extra = {}

    class Cmds:
        def __init__(self):
            self.table = {}

        def add(self, name, handler, pro_tools=True):
            self.table[name] = handler
    gestartet = []
    cmds, ctx = Cmds(), Ctx()
    add_fetch_command(cmds, ctx, gestartet.append, lambda: {"group": "test", "name": "helmut test"})
    assert cmds.table["holen"]([]) == (False, "Kein Beitrag im Fenster ausgewählt")
    ctx.extra["auswahl"] = "kro"
    assert cmds.table["holen"]([]) == (True, "holen kro gestartet")
    assert gestartet == [{"id": "kro", "group": "test", "name": "helmut test"}]


def test_import_nur_mit_angemeldetem_nutzer(tmp_path):
    """Review A17: /import und Stream Deck gehen über den Befehl des Fensters – ohne Anmeldung startet nichts."""
    from pathlib import Path
    from types import SimpleNamespace
    from tonmischung.app.bridge import Bridge
    from tonmischung.app.triggers_app import add_fetch_command
    from tonmischung.app.users_app import require_user
    ctx = SimpleNamespace(extra={"auswahl": "kro"}, user=None)
    bridge, started = Bridge(emit=lambda m: None, inline=True), []
    bridge.register("holen", lambda args: (require_user(ctx), started.append(args))[1])
    cmds = SimpleNamespace(table={})
    cmds.add = lambda name, handler, pro_tools=True: cmds.table.__setitem__(name, handler)
    add_fetch_command(cmds, ctx, lambda a: bridge.call("holen", a), lambda: {"group": "test", "name": "t"})
    ok, text = cmds.table["holen"]([])
    assert not ok and "Bitte zuerst anmelden" in text and started == []
    main = (Path(__file__).resolve().parent.parent / "tonmischung" / "app" / "main.py").read_text(encoding="utf-8")
    assert 'add_fetch_command(punch.commands, ctx, lambda a: bridge.call("holen", a)' in main


@pytest.fixture
def sock_dir():
    import shutil
    import tempfile
    folder = tempfile.mkdtemp(prefix="tm", dir="/tmp")             # kurzer Pfad, eigener Ordner
    yield os.path.join(folder, "s.sock")
    shutil.rmtree(folder, ignore_errors=True)


def test_socket_erst_nach_listen_sichtbar(sock_dir, monkeypatch):
    """Race aus dem Live-Test: Die Datei darf erst existieren, wenn der Server lauscht."""
    dispatch, _ = _dispatcher()
    gesehen = []
    echtes_listen = socket.socket.listen

    def listen(self, *a):
        gesehen.append(os.path.exists(sock_dir))               # vor dem Lauschen: noch nicht da
        return echtes_listen(self, *a)
    monkeypatch.setattr(socket.socket, "listen", listen)
    ls = LineSocket(dispatch, sock_dir)
    ls.start()
    try:
        assert gesehen == [False]
        assert _send(sock_dir, "ping\n") == "OK ping queued\n"
        assert os.listdir(os.path.dirname(sock_dir)) == [os.path.basename(sock_dir)]   # kein Hilfsname übrig
    finally:
        ls.stop()


def test_fehler_beim_lauschen_hinterlaesst_nichts(sock_dir, monkeypatch):
    dispatch, _ = _dispatcher()

    def listen(self, *a):
        raise OSError("kaputt")
    monkeypatch.setattr(socket.socket, "listen", listen)
    with pytest.raises(OSError, match="kaputt"):
        LineSocket(dispatch, sock_dir).start()
    assert os.listdir(os.path.dirname(sock_dir)) == []
