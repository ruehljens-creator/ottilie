import asyncio
import json
import os
import stat
import tempfile
from pathlib import Path

import pytest

from fakes import FakeEngine
from tonmischung.app import commands
from tonmischung.app.api import CATALOG, CHANGE, ApiDispatcher, markdown
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import JobRunner
from tonmischung.app.socket_api import SocketApi, SocketClient
from tonmischung.config.settings import Settings
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.ptsl.client import PtslClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app(tmp_path):
    m = MockHelmut()
    m.add_project("p1", "Bahnstreik", "Heimatjournal", assets=["/H/p1/Bahnstreik.aaf"])
    m.add_project("p2", "Kita", "Heimatjournal", state="importiert", mischer="Jens", raum="Ton 3")
    url = m.start()
    settings = Settings(local_path=tmp_path / "e.json")
    ctx = AppContext(settings, HelmutClient(url, lambda: ("test", "test")),
                     PtslClient(engine_factory=lambda: FakeEngine()), "Jens", "Ton 3", str(tmp_path), m, "x")
    bridge = Bridge(emit=lambda p: None, inline=True)
    runner = JobRunner(bridge)
    commands.register_all(bridge, ctx, runner)
    from tonmischung.app import pt_queries
    pt_queries.register(bridge, ctx.ptsl)
    from tonmischung.app import punch_app
    punch = punch_app.start(ctx, runner.running_title, on_error=lambda t, m: None, on_busy=lambda b: None)
    punch_app.register(bridge, punch)
    from tonmischung.app import vocaster_app
    vocaster_app.register(bridge, vocaster_app.Vocaster())             # ohne Gerät
    yield bridge, ApiDispatcher(bridge.call, settings), settings
    punch.restore()
    m.stop()


def test_katalog_passt_zur_befehlsschicht(app):
    bridge, *_ = app
    missing = [c.handler for c in CATALOG if c.handler not in bridge._handlers]
    assert missing == []
    assert {"holen", "zurueckliefern", "freigeben", "antworten", "record_a", "record_b", "play", "play_custom",
            "goto_start", "move_audio", "preset", "export_wav", "export_aaf",
            "export_aaf_reference"} <= {c.name for c in CATALOG if c.kind == CHANGE}


def test_punchbuddy_befehle_ueber_die_api(app):
    bridge, api, settings = app
    settings.set_local("aufnahme.presets", [{"name": "Abend", "rec_a": ["A1"], "mon_a": [], "rec_b": [],
                                             "mon_b": [], "export": []}])
    assert api.dispatch("preset", {"nr": 1}, "socket") == {"ok": True, "meldung": "preset 1 loaded"}
    assert settings.get("aufnahme.spuren.rec_a") == ["A1"]
    with pytest.raises(CommandError, match="keine Aufnahmespuren"):
        api.dispatch("record_b", {}, "socket")
    with pytest.raises(CommandError, match="nur Lesen"):
        api.dispatch("record_a", {}, "mcp")             # MCP standardmäßig nur lesend


def test_api_doku_ist_aktuell():
    assert (ROOT / "docs" / "API.md").read_text(encoding="utf-8") == markdown(), \
        "docs/API.md neu erzeugen: python3 -m tonmischung api-doku > docs/API.md"


def test_rechte_nach_herkunft(app):
    bridge, api, settings = app
    assert api.dispatch("beitraege", {}, "mcp")["counts"]["list"] == 2
    with pytest.raises(CommandError, match="nur Lesen"):
        api.dispatch("freigeben", {"id": "p2", "reason": "Raumwechsel"}, "mcp")      # Standard: MCP nur lesen
    assert api.dispatch("freigeben", {"id": "p2", "reason": "Raumwechsel"}, "socket")["id"] == "p2"
    settings.set_local("system.schnittstellen.mcp_nur_lesen", False)
    with pytest.raises(CommandError, match="Mischer im Fenster"):
        api.dispatch("antworten", {"id": 1, "value": True}, "mcp")
    with pytest.raises(CommandError, match="Unbekannter Befehl"):
        api.dispatch("loeschen", {}, "socket")
    with pytest.raises(CommandError, match="Herkunft"):
        api.dispatch("status", {}, "irgendwo")


def test_socket_hin_und_zurueck(app):
    bridge, api, settings = app
    path = os.path.join(tempfile.mkdtemp(prefix="tm", dir="/tmp"), "api.sock")   # Unix-Socket-Pfade sind kurz
    server = SocketApi(api, path)
    server.start()
    try:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        c = SocketClient(path)
        assert c.available()
        assert c.call("beitrag", {"id": "p1"})["title"] == "Bahnstreik"
        with pytest.raises(CommandError, match="nur Lesen"):
            SocketClient(path, origin="mcp").call("freigeben", {"id": "p2", "reason": "Raumwechsel"})
        with pytest.raises(RuntimeError, match="belegt"):
            SocketApi(api, path).start()
    finally:
        server.stop()
    assert not os.path.exists(path)
    with pytest.raises(CommandError, match="läuft nicht"):
        SocketClient(path).call("status")


class _Client:
    def __init__(self):
        self.calls = []

    def available(self):
        return True

    def call(self, cmd, args=None, timeout_s=None):
        self.calls.append((cmd, args))
        if cmd == "beitrag":
            raise CommandError("Projekt fehlt")
        return {"cmd": cmd}


def _tools(settings, client):
    from tonmischung.mcp_server import build
    mcp = build(settings, client)
    return mcp, {t.name for t in asyncio.run(mcp.list_tools())}


def test_mcp_werkzeuge_nach_einstellung(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    _, names = _tools(s, _Client())
    assert {"status", "beitraege", "beitrag", "triage", "auftrag"} <= names and "holen" not in names
    s.set_local("system.schnittstellen.mcp_nur_lesen", False)
    _, names = _tools(s, _Client())
    assert {"holen", "zurueckliefern", "freigeben"} <= names and "antworten" not in names
    s.set_local("system.schnittstellen.mcp_antworten_erlaubt", True)
    assert "antworten" in _tools(s, _Client())[1]


def test_mcp_aufrufe(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local("system.schnittstellen.mcp_nur_lesen", False)
    client = _Client()
    mcp, _ = _tools(s, client)

    def text(name, args):
        out = asyncio.run(mcp.call_tool(name, args))
        content = out[0] if isinstance(out, tuple) else out
        return content[0].text
    assert json.loads(text("status", {})) == {"cmd": "status"}
    assert text("beitrag", {"id": "x"}) == "Fehler: Projekt fehlt"
    text("holen", {"id": "p1", "template": "test/helmut test"})
    assert client.calls[-1] == ("holen", {"id": "p1", "group": "test", "name": "helmut test", "triage": True, "video": True})
    assert json.loads(text("triage", {"aaf": "/x.aaf"})) == {"cmd": "triage"}       # App läuft: über die App
    client.available = lambda: False                                                 # App aus: direkt
    assert "AAF nicht gefunden" in text("triage", {"aaf": "/gibt/es/nicht.aaf"})


def test_pro_tools_abfragen_nur_lesen(app):
    bridge, api, settings = app
    assert api.dispatch("pro_tools", {"was": "transport"}, "mcp") == {"transport": "TS_TransportStopped"}
    spuren = api.dispatch("pro_tools", {"was": "spuren"}, "mcp")["spuren"]
    assert isinstance(spuren, list)
    with pytest.raises(CommandError, match="Unbekannte Abfrage"):
        api.dispatch("pro_tools", {"was": "loeschen"}, "mcp")


def test_alles_aus_dem_fenster_auch_ueber_die_schnittstelle():
    """Jens 08.10.2026: vollständiger Zugriff für Automation. Jeder Befehl der Befehlsschicht ist öffentlich oder steht
    mit Grund in WINDOW_ONLY – ein neuer Befehl ohne Eintrag fällt hier auf."""
    import re
    from tonmischung.app.api import WINDOW_ONLY
    from tonmischung.app.punch_app import BRIDGE_COMMANDS
    root = Path(__file__).resolve().parents[1] / "tonmischung"
    registered = {f"punch_{n}" for n in BRIDGE_COMMANDS}
    for f in root.rglob("*.py"):
        registered |= set(re.findall(r'register\("([\w_]+)"', f.read_text(encoding="utf-8")))
    public = {c.handler for c in CATALOG}
    assert sorted(registered - public - set(WINDOW_ONLY)) == []
    assert not (set(WINDOW_ONLY) & public) and set(WINDOW_ONLY) <= registered
    assert sorted(public - registered) == []


def test_drei_stufen_fuer_mcp(app):
    from tonmischung.app.api import ADMIN, BY_NAME
    bridge, api, settings = app
    assert BY_NAME["einstellung_setzen"].kind == ADMIN and BY_NAME["havarie_holen"].kind == CHANGE
    with pytest.raises(CommandError, match="nur Lesen"):
        api.dispatch("nachtragen", {}, "mcp")                                 # Standard: nur lesen
    settings.set_local("system.schnittstellen.mcp_nur_lesen", False)
    assert "offen" in api.dispatch("nachtragen", {}, "mcp")
    with pytest.raises(CommandError, match="nicht freigegeben"):
        api.dispatch("einstellung_setzen", {"path": "persoenlich.farbschema", "value": "kontrast"}, "mcp")
    settings.set_local("system.schnittstellen.mcp_verwalten_erlaubt", True)
    assert api.dispatch("einstellung_setzen", {"path": "persoenlich.farbschema", "value": "kontrast"}, "mcp")["value"] == "kontrast"
    settings.set_local("system.schnittstellen.mcp_nur_lesen", True)               # „nur lesen“ geht vor
    with pytest.raises(CommandError, match="nicht freigegeben"):
        api.dispatch("einstellung_zuruecksetzen", {"path": "persoenlich.farbschema"}, "mcp")
    assert api.dispatch("ping", {}, "mcp")["pong"] is True


def test_pfad_statt_dialog(app, tmp_path):
    bridge, api, settings = app
    with pytest.raises(CommandError, match="Angabe fehlt: ziel"):
        api.dispatch("einstellungen_exportieren", {}, "socket")               # nie ein Dateidialog
    with pytest.raises(CommandError, match="Angabe fehlt: pfad"):
        api.dispatch("einstellungen_import_pruefen", {}, "socket")
    settings.set_local("persoenlich.farbschema", "kontrast")
    out = api.dispatch("einstellungen_exportieren", {"ziel": str(tmp_path)}, "socket")
    assert out["path"].startswith(str(tmp_path)) and Path(out["path"]).is_file()
    check = api.dispatch("einstellungen_import_pruefen", {"pfad": out["path"]}, "socket")
    assert check["path"] == out["path"] and check["choices"]


def test_auftrag_warten():
    from tonmischung.app.commands import wait_for_job
    snaps = iter([{"id": 1, "state": "laeuft", "step": "a", "fraction": 0.1}] * 3 +
                 [{"id": 1, "state": "frage", "step": "a", "fraction": 0.1, "question": {"id": 7}}])
    runner = type("R", (), {"current": lambda self: next(snaps)})()
    t = iter(range(100))
    r = wait_for_job(runner, {"sekunden": 10}, clock=lambda: next(t), sleep=lambda s: None)
    assert r["geaendert"] and r["auftrag"]["state"] == "frage"
    runner = type("R", (), {"current": lambda self: None})()
    t = iter(range(100))
    assert wait_for_job(runner, {"sekunden": 2}, clock=lambda: next(t), sleep=lambda s: None) == {"geaendert": False, "auftrag": None}
    with pytest.raises(CommandError):
        wait_for_job(runner, {"sekunden": "bald"})


def test_mcp_werkzeuge_aus_dem_katalog():
    import asyncio
    from tonmischung.app.api import CATALOG as K
    from tonmischung.mcp_server import build

    class S(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

    class Client:
        calls = []

        def available(self):
            return True

        def call(self, cmd, args, timeout_s=None):
            self.calls.append((cmd, args, timeout_s))
            return {"ok": True}
    full = S({"system.schnittstellen.mcp_nur_lesen": False, "system.schnittstellen.mcp_verwalten_erlaubt": True,
              "system.schnittstellen.mcp_antworten_erlaubt": True})
    names = lambda st: {t.name for t in asyncio.run(build(st, Client()).list_tools())}   # noqa: E731
    assert names(full) == {c.name for c in K}                                  # vollständig
    assert names(S({"system.schnittstellen.mcp_nur_lesen": True})) == {c.name for c in K if c.kind == "lesen"}
    m = build(full, Client())
    tools = {t.name: t for t in asyncio.run(m.list_tools())}
    assert tools["havarie_ausliefern"].inputSchema["required"] == ["path", "ziele"]
    asyncio.run(m.call_tool("auftrag_warten", {"sekunden": 5}))
    assert Client.calls[-1] == ("auftrag_warten", {"sekunden": 5.0}, 70)


def test_geschuetzte_einstellungen_nur_im_fenster(app, tmp_path):
    """Review A3: mit „verwalten“ hätte sich MCP selbst Rechte geben, den Webtrigger ins Netz öffnen oder die
    Helmut-Adresse umleiten können – auch per Einstellungsimport."""
    from tonmischung.app.api import protected_setting
    bridge, api, settings = app
    settings.set_local("system.schnittstellen.mcp_nur_lesen", False)
    settings.set_local("system.schnittstellen.mcp_verwalten_erlaubt", True)
    for path in ("system.schnittstellen.mcp_antworten_erlaubt", "system.streamdeck_webtrigger.bind_adresse",
                 "system.schnittstellen.helmut.adresse"):
        assert protected_setting(path)
        for origin in ("mcp", "socket"):
            with pytest.raises(CommandError, match="nur im Fenster"):
                api.dispatch("einstellung_setzen", {"path": path, "value": True}, origin)
    assert not protected_setting("persoenlich.farbschema")
    datei = tmp_path / "e.json"
    import json as _j
    from tonmischung.core import settings_transfer as ST
    datei.write_text(_j.dumps(ST.export_document({"system.schnittstellen.mcp_antworten_erlaubt": True,
                                                  "persoenlich.farbschema": "kontrast"}, "jetzt", "test")))
    choices = api.dispatch("einstellungen_import_pruefen", {"pfad": str(datei)}, "mcp")["choices"]
    out = api.dispatch("einstellungen_importieren", {"pfad": str(datei), "auswahl": [c["id"] for c in choices]}, "mcp")
    assert "nur im Fenster" in out["message"]
    assert settings.get("system.schnittstellen.mcp_antworten_erlaubt") is False
    assert settings.get("persoenlich.farbschema") == "kontrast"
    settings.set_local("system.schnittstellen.mcp_aktiv", False)                       # wirkt sofort
    with pytest.raises(CommandError, match="abgeschaltet"):
        api.dispatch("ping", {}, "mcp")
