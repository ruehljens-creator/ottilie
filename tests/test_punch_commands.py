"""Befehle der PunchBuddy-Funktionen (Plan PunchBuddy-Übernahme, Schritt 2)."""
import itertools

import pytest

from fakes import FakeEngine
from tonmischung.config.settings import Settings, load_schema
from tonmischung.ptsl.client import PtslClient
from tonmischung.punch.commands import PunchCommands
from tonmischung.punch.core import PunchCore
from tonmischung.punch.settings_bridge import preset_changes

SCHEMA = load_schema()


@pytest.fixture
def cmds(tmp_path, monkeypatch):
    engine = FakeEngine()
    settings = Settings(local_path=tmp_path / "e.json")
    core = PunchCore(PtslClient(engine_factory=lambda: engine), settings, SCHEMA)
    restore = core.install()
    transport = core.module("transport")
    gerufen = []
    for name in ("run_punch_in", "run_play", "run_play_custom", "run_goto_start", "run_move_audio"):
        monkeypatch.setattr(transport, name, lambda *a, _n=name: gerufen.append((_n, a)))
    uhr = itertools.count(0, 10)                       # jeder Blick auf die Uhr 10 s später
    c = PunchCommands(core, settings, spawn=lambda fn, *a: fn(*a), clock=lambda: next(uhr))
    c.gerufen, c.engine = gerufen, engine
    yield c
    restore()


def test_record_a_mit_spuren_aus_den_einstellungen(cmds):
    cmds.settings.set_local("aufnahme.spuren.rec_a", ["A1", "A2"])
    assert cmds.dispatch("record_a") == (True, "record_a queued")
    assert cmds.gerufen == [("run_punch_in", (["A1", "A2"], ["ST", "A  IT", "OGA", "Mus", "Spr"]))]


@pytest.mark.parametrize("alias, befehl", [("trigger", "record_a"), ("A", "record_a"), ("trigger2", "record_b"),
                                           ("start", "goto_start"), ("move", "move_audio")])
def test_aliase_wie_punchbuddy(cmds, alias, befehl):
    cmds.settings.set_local("aufnahme.spuren.rec_b", ["A3"])
    cmds.settings.set_local("aufnahme.audio_verschieben.quellspuren", ["A1"])
    cmds.settings.set_local("aufnahme.audio_verschieben.zielspuren", ["A3"])
    ok, msg = cmds.dispatch(alias)
    assert ok and msg == f"{befehl} queued"


def test_record_b_ohne_spuren(cmds):
    assert cmds.dispatch("record_b") == (False, "record_b: keine Aufnahmespuren eingestellt")
    assert cmds.gerufen == []


def test_wiedergabe_und_programmbeginn(cmds):
    for befehl in ("play", "play_custom", "goto_start"):
        assert cmds.dispatch(befehl)[0]
    assert [g[0] for g in cmds.gerufen] == ["run_play", "run_play_custom", "run_goto_start"]


def _move_session(cmds, hidden=(), clips=None):
    from fakes import _Track
    cmds.engine.track_objs = [_Track(n, "AudioTrack", bool((clips or {}).get(n)), hidden=n in hidden)
                              for n in ("A1", "A2", "A3", "A4")]
    cmds.engine.clips = clips or {"A1": [(900000, 900050, "Ton")], "A2": [(900100, 900150, "Ton 2")]}
    cmds.fehler = []
    cmds.core.on_error = lambda title, msg: cmds.fehler.append(msg)


def test_audio_verschieben_paarweise(cmds):
    assert not cmds.dispatch("move_audio")[0]
    _move_session(cmds)
    cmds.settings.set_local("aufnahme.audio_verschieben.quellspuren", ["A1", "A2"])
    cmds.settings.set_local("aufnahme.audio_verschieben.zielspuren", ["A3", "A4"])
    assert cmds.dispatch("move_audio") == (True, "move_audio queued")
    assert cmds.gerufen == [("run_move_audio", (["A1", "A2"], ["A3", "A4"]))]
    assert "nichts angekommen" in cmds.fehler[0]                          # die Attrappe verschiebt nicht


def test_ausgeblendete_zielspur_wird_vorher_eingeblendet(cmds):
    """Live 09.10.2026: Ziel ausgeblendet – ausgeschnitten, Einfügen ins Leere, Clip nur noch in der Clip-Liste."""
    _move_session(cmds, hidden=("A3",))
    cmds.settings.set_local("aufnahme.audio_verschieben.quellspuren", ["A1"])
    cmds.settings.set_local("aufnahme.audio_verschieben.zielspuren", ["A3"])
    cmds.dispatch("move_audio")
    state = {t.name: t.hidden for t in cmds.core.ptsl.track_states()}
    assert state["A3"] is False and cmds.gerufen == [("run_move_audio", (["A1"], ["A3"]))]


def test_nichts_ausschneiden_wenn_es_schiefgehen_wuerde(cmds):
    _move_session(cmds, clips={"A1": [(900000, 900050, "Ton")], "A3": [(900020, 900080, "Alt")]})
    cmds.settings.set_local("aufnahme.audio_verschieben.quellspuren", ["A1", "Fehlt"])
    cmds.settings.set_local("aufnahme.audio_verschieben.zielspuren", ["A3", "A4"])
    cmds.dispatch("move_audio")
    assert cmds.gerufen == [] and "„Fehlt“ gibt es in der Session nicht" in cmds.fehler[0]
    assert "auf „A3“ liegt im selben Bereich schon Material (Alt)" in cmds.fehler[0]


def test_vorpruefung_kanalformate():
    from types import SimpleNamespace
    from tonmischung.punch.commands import move_check
    states = {n: SimpleNamespace(hidden=n == "Spr 3") for n in ("OGA", "Spr", "Spr 3")}
    problems, hidden = move_check(["OGA", "Spr"], ["Spr 3", "Spr 3"], states,
                                  {"OGA": "TF_Stereo", "Spr": "TF_Mono", "Spr 3": "TF_Mono"}, {})
    assert problems == ["„OGA“ (Stereo) und „Spr 3“ (Mono) haben verschiedene Kanalformate"] and hidden == ["Spr 3"]


def test_entprellen_gegen_salven(tmp_path, monkeypatch, cmds):
    zeiten = iter([0.0, 0.2, 0.5])
    cmds.debounce._clock = lambda: next(zeiten)
    assert cmds.dispatch("play") == (True, "play queued")
    assert cmds.dispatch("play") == (True, "play debounced")      # 0,2 s danach
    assert cmds.dispatch("play") == (True, "play queued")         # 0,5 s nach dem ersten
    assert len(cmds.gerufen) == 2


def test_belegt_durch_auftrag(cmds):
    cmds.busy = lambda: "Holen: Bahnstreik"
    ok, msg = cmds.dispatch("play")
    assert not ok and "Pro Tools ist belegt (Holen: Bahnstreik)" in msg and cmds.gerufen == []
    assert cmds.dispatch("ping") == (True, "pong")               # Auskunft geht immer
    cmds.settings.set_local("system.streamdeck_webtrigger.ablehnen_wenn_belegt", False)
    assert cmds.dispatch("play")[0]


def test_unbekannt_liste_ping_status(cmds):
    assert cmds.dispatch("gibtsnicht") == (False, "unknown command: gibtsnicht")
    ok, liste = cmds.dispatch("list")
    assert ok and liste.split(",") == sorted(liste.split(",")) and "record_a" in liste
    assert "import" not in liste and "export_interplay" not in liste
    assert cmds.dispatch("status")[1].startswith("ok export_running=False import_running=False http_port=8899")


def test_preset_laden(cmds):
    cmds.settings.set_local("aufnahme.presets", [
        {"name": "Abend", "rec_a": ["A1", "A2"], "mon_a": [], "rec_b": ["A5"], "mon_b": [],
         "export": ["ST", "OGA"], "loudness_tracks": ["ST"]}])
    assert cmds.dispatch("preset", ["1"]) == (True, "preset 1 loaded")
    assert cmds.settings.get("aufnahme.spuren.rec_a") == ["A1", "A2"]
    assert cmds.settings.get("aufnahme.spuren.rec_b") == ["A5"]
    tabelle = {r["pt_spur"]: r for r in cmds.settings.get("export.spuren_lautheit.spurtabelle") if r["pt_spur"]}
    assert tabelle["ST"]["export"] and tabelle["ST"]["r128"] and tabelle["OGA"]["export"]
    assert not tabelle["Mus"]["export"] and not tabelle["OGA"]["r128"]
    assert cmds.dispatch("preset", ["2"]) == (False, "invalid preset index: 2")
    assert cmds.dispatch("preset", ["x"]) == (False, "invalid preset number: x")
    assert cmds.dispatch("preset") == (False, "preset needs a number, e.g. 'preset 3'")


def test_preset_ohne_exportspuren_laesst_spurtabelle(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    changes = preset_changes(s, {"name": "Leer", "rec_a": [], "mon_a": [], "rec_b": [], "mon_b": [], "export": []})
    assert "export.spuren_lautheit.spurtabelle" not in changes
    assert changes["aufnahme.spuren.rec_a"] == []


def test_fehler_im_befehl_wird_antwort(cmds, monkeypatch):
    def kaputt(*a):
        raise RuntimeError("Pro Tools weg")
    monkeypatch.setattr(cmds.core.module("transport"), "run_play", kaputt)
    assert cmds.dispatch("play") == (False, "error: Pro Tools weg")
