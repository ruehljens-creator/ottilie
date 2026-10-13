import threading

import grpc
import pytest

from fakes import FakeEngine, FakeRpcError, command_error
from tonmischung.ptsl.client import PtslClient, SessionInfo
from tonmischung.ptsl.dialogs import dialog_names
from tonmischung.ptsl.errors import (
    CommandFailedError, DialogOpenError, NotConnectedError, PtslTimeoutError, UnsupportedError)


def _client(engine=None, **kwargs):
    engine = engine or FakeEngine()
    return PtslClient(engine_factory=lambda: engine, **kwargs), engine


def test_offene_session_und_keine_session():
    client, _ = _client()
    assert client.open_session() == SessionInfo("Beitrag", "/Sessions/Beitrag/Beitrag.ptx")
    client, _ = _client(FakeEngine(session=None))
    assert client.open_session() is None


def test_jeder_aufruf_bekommt_eine_deadline():
    client, engine = _client(timeout_s=12.0)
    client.call("Test", lambda e: e.client.raw_client.SendGrpcRequest("x"), timeout_s=3.0)
    client.call("Test", lambda e: e.client.raw_client.SendGrpcRequest("x"))
    assert engine.client.raw_client.timeouts == [3.0, 12.0]


def test_zeitlimit_wird_verstaendlich_und_verbindung_verworfen():
    engine = FakeEngine(fail={"save_session": FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED)})
    client, _ = _client(engine)
    with pytest.raises(PtslTimeoutError, match="Dialog"):
        client.save()
    assert engine.closed


def test_nicht_erreichbar():
    engine = FakeEngine(fail={"transport_state": FakeRpcError(grpc.StatusCode.UNAVAILABLE)})
    client, _ = _client(engine)
    with pytest.raises(NotConnectedError):
        client.transport_state()


def test_verbindungsaufbau_scheitert():
    def factory():
        raise FakeRpcError(grpc.StatusCode.UNAVAILABLE)
    with pytest.raises(NotConnectedError, match="läuft Pro Tools"):
        PtslClient(engine_factory=factory).version()



def test_verbindungsaufbau_haengt_endet_mit_zeitlimit():
    """py-ptsl meldet sich im Konstruktor ohne Deadline an – hängt Pro Tools, greift unser Limit."""
    import threading
    release = threading.Event()

    def factory():
        release.wait(5)
        return FakeEngine()
    client = PtslClient(engine_factory=factory, connect_timeout_s=0.1)
    try:
        with pytest.raises(PtslTimeoutError, match="Verbinden: keine Antwort"):
            client.version()
    finally:
        release.set()

@pytest.mark.parametrize("name, klasse", [
    ("PT_UnsupportedCommand", UnsupportedError),
    ("PT_HostIsBusy", PtslTimeoutError),
    ("PT_FileNotFound", CommandFailedError),
])
def test_pro_tools_fehler_werden_uebersetzt(name, klasse):
    engine = FakeEngine(fail={"save_session": command_error(name, "Meldung")})
    client, _ = _client(engine)
    with pytest.raises(klasse):
        client.save()


def test_versionspruefung_beim_verbinden():
    client, _ = _client(FakeEngine(version=2023), min_version=2025)
    with pytest.raises(UnsupportedError, match="zu alt"):
        client.transport_state()
    client, _ = _client(FakeEngine(version=2025), min_version=2025)
    assert client.transport_running() is False


def test_schliessen_immer_mit_save_on_close():
    client, engine = _client()
    client.close()
    assert "close_session(save_on_close=True)" in engine.calls


def test_zweiter_aufruf_wartet_auf_die_sperre():
    client, _ = _client(lock_wait_s=0.05)
    gestartet, weiter = threading.Event(), threading.Event()

    def lang(e):
        gestartet.set()
        weiter.wait(2)

    t = threading.Thread(target=client.call, args=("Lang", lang))
    t.start()
    gestartet.wait(2)
    with pytest.raises(PtslTimeoutError, match="beschäftigt"):
        client.save()
    weiter.set()
    t.join()


def test_ohne_session_laeuft_kein_transport():
    engine = FakeEngine(session=None, fail={"transport_state": command_error("PT_NoOpenedSession")})
    client, _ = _client(engine)
    assert client.transport_running() is False


def test_session_anlegen_wartet_bis_sie_offen_ist():
    engine = FakeEngine(session=None)
    client, _ = _client(engine)
    client.create_session_from_template("test", "helmut test", "Beitrag", "/tmp/x", sleep=lambda s: None)
    assert client.open_session().name == "Beitrag"


def test_session_angelegt_aber_nicht_offen():
    engine = FakeEngine(session=None)
    engine.created_but_not_opened = True
    client, _ = _client(engine)
    with pytest.raises(CommandFailedError, match="nicht offen"):
        client.create_session_from_template("test", "helmut test", "Beitrag", "/tmp/x", wait_s=3,
                                            sleep=lambda s: None)


def test_dialog_sperrt_schliessen_oeffnen_und_speichern_unter():
    """Live 03.10.2026: Pro Tools führte CloseSession innerhalb von „Missing Files“ aus und stürzte ab."""
    client, engine = _client()
    client.dialogs = lambda: ["Missing Files"]
    for action in (client.close, lambda: client.open("/S/x.ptx"), lambda: client.save_as("x", "/S")):
        with pytest.raises(DialogOpenError, match="Missing Files") as e:
            action()
        assert e.value.dialogs == ["Missing Files"]
    assert not any(c.startswith(("close_session", "open_session", "save_session_as")) for c in engine.calls)
    client.dialogs = lambda: []
    client.close()
    assert "close_session(save_on_close=True)" in engine.calls


def test_dialog_nach_dem_anlegen_stoppt_den_ablauf():
    engine = FakeEngine(session=None)
    client, _ = _client(engine)
    seen = iter([[], ["Missing Files"]])                    # vor CreateSession frei, danach Dialog
    client.dialogs = lambda: next(seen, ["Missing Files"])
    with pytest.raises(DialogOpenError, match="„Beitrag“: in Pro Tools ist „Missing Files“ offen"):
        client.create_session_from_template("test", "helmut test", "Beitrag", "/tmp/x", sleep=lambda s: None)


def test_dialoge_auf_allen_spaces_erkennen():
    windows = [{"kCGWindowOwnerName": "Pro Tools", "kCGWindowLayer": 0, "kCGWindowName": "Edit: Beitrag"},
               {"kCGWindowOwnerName": "Pro Tools", "kCGWindowLayer": 8, "kCGWindowName": "Missing Files",
                "kCGWindowIsOnscreen": False},
               {"kCGWindowOwnerName": "Pro Tools", "kCGWindowLayer": 8, "kCGWindowName": "Unsichtbar",
                "kCGWindowAlpha": 0},
               {"kCGWindowOwnerName": "Finder", "kCGWindowLayer": 8, "kCGWindowName": "Kopieren"}]
    assert dialog_names(windows) == ["Missing Files"]
    # live 03.10.2026: Assertion-Meldung ohne Titel (Ebene 8) zählt, Transport-Palette (3) und Menüleiste (1000) nicht
    more = [{"kCGWindowOwnerName": "Pro Tools", "kCGWindowLayer": 8, "kCGWindowName": ""},
            {"kCGWindowOwnerName": "Pro Tools", "kCGWindowLayer": 3, "kCGWindowName": "Transport"},
            {"kCGWindowOwnerName": "Pro Tools", "kCGWindowLayer": 1000, "kCGWindowName": ""}]
    assert dialog_names(more) == ["Meldung von Pro Tools"]
    # live 04.10.2026: Fensterchen 120×18 auf Ebene 103 (Tooltip/Popup) hielt das Anlegen auf – kein Dialog
    tips = [{"kCGWindowOwnerName": "Pro Tools", "kCGWindowLayer": 103, "kCGWindowName": "",
             "kCGWindowBounds": {"Width": 120, "Height": 18}},
            {"kCGWindowOwnerName": "Pro Tools", "kCGWindowLayer": 8, "kCGWindowName": "",
             "kCGWindowBounds": {"Width": 120, "Height": 18}},
            {"kCGWindowOwnerName": "Pro Tools", "kCGWindowLayer": 8, "kCGWindowName": "",
             "kCGWindowBounds": {"Width": 475, "Height": 110}}]
    assert dialog_names(tips) == ["Meldung von Pro Tools"]                       # nur der Fortschrittsbalken
    assert dialog_names(tips[:2]) == []


def test_mit_attrappe_keine_fensterpruefung():
    client, _ = _client()
    assert client.dialogs is None and PtslClient().dialogs is not None


def test_wartet_bis_der_dialog_bestaetigt_ist():
    """Jens: „Missing Files“ meldet fälschlich fehlende Clips aus dem AAF – bestätigen, dann geht es weiter."""
    client, engine = _client()
    seen = iter([["Missing Files"], ["Missing Files"], []])
    client.dialogs = lambda: next(seen, [])
    client.dialog_wait_s = 10
    client._sleep = lambda s: None
    notes = []
    client.on_dialog = notes.append
    client.close()
    assert "close_session(save_on_close=True)" in engine.calls
    assert notes == ["Pro Tools zeigt „Missing Files“ – bitte in Pro Tools bestätigen, danach geht es weiter "
                     "(CloseSession)", ""]


def test_zu_lange_offen_bricht_ab():
    client, engine = _client()
    client.dialogs = lambda: ["Missing Files"]
    client.dialog_wait_s, client._sleep = 3, (lambda s: None)
    with pytest.raises(DialogOpenError, match="erst in Pro Tools bestätigen"):
        client.close()
    assert not any(c.startswith("close_session") for c in engine.calls)


def test_bekannter_dialog_wird_nach_einstellung_beantwortet():
    client, engine = _client()
    state = {"open": True}
    client.dialogs = lambda: ["Missing Files"] if state["open"] else []
    client._sleep = lambda s: None

    def handler(names):
        state["open"] = False
        return "Pro Tools meldete fehlende Dateien – automatisch gesucht und verknüpft (Einstellung)"
    client.dialog_handler = handler
    client.close()                                         # kein Warten nötig
    assert "close_session(save_on_close=True)" in engine.calls
    assert client.dialog_events == ["Pro Tools meldete fehlende Dateien – automatisch gesucht und verknüpft "
                                    "(Einstellung)"]


def test_missing_files_antwort_per_system_events():
    from tonmischung.ptsl.dialogs import answer_missing_files, make_handler
    scripts = []

    def run(script):
        scripts.append(script)
        return 0, "ok|0 Audio File(s), 1 Video File(s) … are missing. Would you like to:"
    msg = answer_missing_files("automatisch_suchen", run)
    assert 'radio button "Automatically Find & Relink" of w' in scripts[0] and 'click button "OK" of w' in scripts[0]
    assert msg == "Pro Tools meldete 1 fehlende Videodatei – automatisch gesucht und verknüpft (Einstellung)"
    assert "Skip All" in (answer_missing_files("alle_ueberspringen", run) and scripts[-1])
    n = len(scripts)
    assert answer_missing_files("selbst_bestaetigen", run) is None and len(scripts) == n     # nichts geklickt
    assert answer_missing_files("automatisch_suchen", lambda s: (0, "fehlt")) is None
    assert answer_missing_files("automatisch_suchen", lambda s: (1, "Fehler")) is None
    handle = make_handler(lambda: "automatisch_suchen", run)
    assert handle(["Missing Files"]) and handle(["Save Session"]) is None
    assert make_handler(lambda: None, run)(["Missing Files"]) is None                     # ohne Wert: selbst


def test_missing_aax_plugins_wird_bestaetigt_und_gemeldet():
    """Jens 13.10.2026: OK klicken, die fehlenden Plugins als Hinweis melden (Text wie live ausgelesen)."""
    from tonmischung.ptsl.dialogs import answer_missing_plugins, make_handler
    shown = ("One or more tracks contain plugins that don't exist in your \"Plug-Ins\" folder.\n"
             "     FabFilter Pro-C 2 (3)\n     NUGEN ISL2 (3)\n"
             "One or more tracks contain plugins that don't exist in your \"Plug-Ins\" folder.\n"
             "     FabFilter Pro-C 2 (3)\n     NUGEN ISL2 (3)\n"
             "Plugins in your Avid account may be available to install within Avid Link's Products tab.\n"
             "Missing AAX Plugins\n")
    scripts = []

    def run(script):
        scripts.append(script)
        return 0, "ok|" + shown
    msg = answer_missing_plugins(run)
    assert 'click button "OK" of w' in scripts[0] and 'window "Missing AAX Plugins"' in scripts[0]
    assert msg == ("Pro Tools meldete fehlende Plugins: FabFilter Pro-C 2 (3), NUGEN ISL2 (3) – bestätigt; "
                   "die Spuren spielen ohne diese Plugins")
    assert answer_missing_plugins(lambda s: (0, "ok|")).startswith("Pro Tools meldete fehlende Plugins –")
    for answer in ((0, "fehlt"), (0, "anders"), (1, "Fehler")):
        assert answer_missing_plugins(lambda s, a=answer: a) is None
    assert make_handler(lambda: "selbst_bestaetigen", run)(["Missing AAX Plugins"]) == msg   # unabhängig von der Vorgabe


def test_oeffnen_bestaetigt_plugin_dialog_waehrend_der_befehl_laeuft():
    """Live 13.10.2026: OpenSession hing über fünf Minuten an „Missing AAX Plugins“ – die Wache klickt während des
    Befehls, nicht erst nach dem Zeitlimit."""
    import threading
    engine = FakeEngine()
    clicked = threading.Event()
    opened = engine.open_session

    def open_session(path):                       # Pro Tools antwortet erst, wenn der Dialog bestätigt ist
        assert clicked.wait(5)
        return opened(path)
    engine.open_session = open_session
    client = PtslClient(engine_factory=lambda: engine)
    client.dialogs = lambda: [] if clicked.is_set() else ["Missing AAX Plugins"]
    client.dialog_handler = lambda names: clicked.set() or f"beantwortet: {names[0]}"
    client.open("/S/Neu.ptx")
    assert client.dialog_events == ["beantwortet: Missing AAX Plugins"]
    assert engine.session_path() == "/S/Neu.ptx"


def test_fehlende_dateien_zusammenfassen():
    from tonmischung.ptsl.dialogs import missing_summary
    text = ("0 Audio File(s), 1 Video File(s), 0 Rendered Elastic Audio File(s), and 0 Rendered Frozen Audio "
            "File(s), are missing. Would you like to:")
    assert missing_summary(text) == "1 fehlende Videodatei"
    assert missing_summary("3 Audio File(s), 2 Video File(s)") == "3 fehlende Audiodateien, 2 fehlende Videodateien"
    assert missing_summary("") == "fehlende Dateien"


def test_aaf_import_wartet_auf_dialoge_bevor_es_weitergeht():
    """Review A12: Nach dem Import (auch beim Hinzufügen in eine offene Session) kommt kein Befehl in einen Dialog."""
    engine = FakeEngine(session=("S", "/S/S.ptx"))
    client = PtslClient(engine_factory=lambda: engine)
    answered = []
    client.dialogs = lambda: (["Missing Files"] if any(n == "Import" for n, _ in engine.requests)
                              and not answered else [])
    client.dialog_handler = lambda names: answered.append(len(engine.requests)) or "automatisch gesucht"
    client._sleep = lambda s: None
    client.import_aaf("/H/p1/B.aaf", as_new_tracks=False)
    assert answered == [len(engine.requests)]                 # beantwortet, bevor irgendein weiterer Befehl lief
    assert client.dialog_events == ["automatisch gesucht"]


def test_verschachtelte_aufrufe_behalten_das_aeussere_zeitlimit():
    """Review B17: der innere Aufruf setzte das Zeitlimit auf den Standard zurück – der Rest des äußeren
    (z. B. Import mit 900 s) lief danach mit 30 s."""
    client, engine = _client(timeout_s=30.0)

    def outer(e):
        client.call("innen", lambda e2: e2.client.raw_client.SendGrpcRequest("a"))
        e.client.raw_client.SendGrpcRequest("b")
    client.call("aussen", outer, timeout_s=900.0)
    assert engine.client.raw_client.timeouts == [30.0, 900.0]


def test_haengendes_verbinden_wird_nicht_gestapelt():
    """Review B16: jeder Aufruf startete nach einem Zeitlimit einen neuen Verbindungsversuch (Thread + gRPC-Kanal)."""
    release, made = threading.Event(), []

    def factory():
        made.append(1)
        release.wait(5)
        return FakeEngine()
    client = PtslClient(engine_factory=factory, connect_timeout_s=0.2)
    for _ in range(3):
        with pytest.raises(PtslTimeoutError, match="Verbinden"):
            client.version()
    release.set()
    assert client.version() and len(made) == 1                       # der späte Versuch wird übernommen


def test_dialogwartezeit_ist_echte_zeit(monkeypatch):
    """Review B15: gezählt wurden nur die Pausen – mit Fensterliste und osascript wurden aus 10 min bis zu 40."""
    from tonmischung.ptsl import client as C
    from tonmischung.ptsl.dialogs import UNNAMED
    now = [0.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: now[0])
    client, _ = _client()
    asked = []

    def slow_kind():
        asked.append(now[0])
        now[0] += 3.0                                                  # osascript braucht 3 s
        return "meldung"
    client.dialogs = lambda: [UNNAMED]
    client.unnamed_kind = slow_kind
    client.dialog_wait_s = 60.0
    client._sleep = lambda s: now.__setitem__(0, now[0] + s)
    with pytest.raises(DialogOpenError):
        client.guard_dialogs("Test")
    assert 60.0 <= now[0] < 70.0 and len(asked) <= 60 / C.UNNAMED_CHECK_S + 1
