"""Holen über die Oberfläche: Auftrag, Fragen ans Fenster, Ergebnis – gegen Fake und Mock."""
import threading
import time

import pytest

from fakes import FakeEngine
from test_fetch_triage import AAF, TEMPLATE, _triage
from tonmischung.app import fetch_app, templates_app
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import JobRunner
from tonmischung.config.settings import Settings
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.ptsl.client import PtslClient
from tonmischung.triage import aafcheck
from tonmischung.workflow import fetch as fetch_mod


def _library(ctx, root, show=""):
    """Templates aus einem Testordner statt der Pro-Tools-Bibliothek."""
    ctx.extra["pt_template_root"] = str(root)
    return templates_app.library(ctx, show=show)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    names = tuple(AAF)
    monkeypatch.setattr(fetch_mod, "check_aaf", lambda p: aafcheck.AafInfo(
        True, "AAF lesbar", 900000, 25, names, {t: tuple(v) for t, v in AAF.items()}))
    monkeypatch.setattr(fetch_mod, "triage_aaf", lambda path, **kw: _triage())
    m = MockHelmut()
    m.add_project("p1", "Bahnstreik", "Heimatjournal", assets=["/H/p1/Bahnstreik.aaf", "/H/p1/Bahnstreik.mxf"])
    url = m.start()
    engine = FakeEngine(template_tracks=TEMPLATE, aaf_tracks=names, aaf_clips=AAF, video_track="Video")
    ctx = AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient(url, lambda: ("test", "test")),
                     PtslClient(engine_factory=lambda: engine), "Jens", "Ton 3", str(tmp_path), m, "x")
    runner = JobRunner(Bridge(emit=lambda p: None, inline=True))
    yield ctx, runner, engine, m
    m.stop()


def _wait(runner, states, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        cur = runner.current()
        if cur and cur["state"] in states:
            return cur
        time.sleep(0.01)
    raise AssertionError(runner.current())


def test_holen_mit_frage_offene_session(setup):
    ctx, runner, engine, m = setup
    job = fetch_app.start_fetch(ctx, runner, {"id": "p1", "group": "test", "name": "helmut test"})
    assert job["title"] == "Holen: Bahnstreik" and job["contribution"] == "p1"
    q = _wait(runner, {"frage"})
    assert q["question"]["kind"] == "offene_session" and "Beitrag" in q["question"]["text"]
    runner.answer(q["question"]["id"], True)
    done = _wait(runner, {"fertig", "fehler"})
    assert done["state"] == "fertig", done["error"]
    r = done["result"]
    assert (r["clips"], r["clips_ok"], r["moves"], r["triage"]) == (11, 11, 1, True)
    assert m.mix_value("p1", "zustand") == "importiert"


def test_offene_session_abgelehnt(setup):
    ctx, runner, engine, m = setup
    fetch_app.start_fetch(ctx, runner, {"id": "p1", "group": "test", "name": "helmut test"})
    q = _wait(runner, {"frage"})
    runner.answer(q["question"]["id"], False)
    end = _wait(runner, {"abgebrochen"})
    assert "offene Session bleibt" in end["error"] and m.mix_value("p1", "zustand") == "bereit"


def test_fehlende_spuren_ohne_triage(setup):
    ctx, runner, engine, m = setup
    engine.session = None
    fetch_app.start_fetch(ctx, runner, {"id": "p1", "group": "test", "name": "helmut test", "triage": False})
    q = _wait(runner, {"frage"})
    assert q["question"]["kind"] == "fehlende_spuren"
    assert q["question"]["missing"] == ["Audio 1", "Audio 2", "Atmo L", "Atmo R"]
    runner.answer(q["question"]["id"], {"Audio 1": "A1", "Audio 2": "A2", "Atmo L": "ab A11", "Atmo R": "weglassen"})
    done = _wait(runner, {"fertig", "fehler"})
    assert done["state"] == "fertig", done["error"]
    assert any("bewusst weggelassen" in w for w in done["result"]["warnings"])


def test_ohne_template_und_zweiter_auftrag(setup):
    ctx, runner, engine, m = setup
    with pytest.raises(CommandError, match="Template"):
        fetch_app.start_fetch(ctx, runner, {"id": "p1"})
    hold = threading.Event()
    runner.start("holen", "läuft", [("Prüfen", 1)], lambda jc: hold.wait(2))
    with pytest.raises(CommandError, match="läuft schon"):
        fetch_app.start_fetch(ctx, runner, {"id": "p1", "group": "test", "name": "helmut test"})
    hold.set()


def test_templates_aus_ordner(setup, tmp_path, monkeypatch):
    ctx, *_ = setup
    from tonmischung import devlocal         # Test-Templates sonst aus testfiles/entwicklung.json dieses Macs
    monkeypatch.setattr(devlocal, "dev_templates", lambda: ("test/helmut test neues startband", "test/helmut test"))
    (tmp_path / "T" / "test").mkdir(parents=True)
    (tmp_path / "T" / "test" / "SM Session.ptxt").write_text("x")
    (tmp_path / "T" / "test" / "helmut test").mkdir()
    (tmp_path / "T" / "test" / "helmut test" / "helmut test.ptxt").write_text("x")
    t = _library(ctx, tmp_path / "T")
    assert [(x["group"], x["name"]) for x in t["found"]] == [("test", "SM Session"), ("test", "helmut test")]
    assert t["default"] == {"group": "test", "name": "helmut test"}           # Entwicklungsmodus
    (tmp_path / "T" / "test" / "helmut test neues startband").mkdir()
    (tmp_path / "T" / "test" / "helmut test neues startband" / "helmut test neues startband.ptxt").write_text("x")
    t = _library(ctx, tmp_path / "T")
    assert t["default"] == {"group": "test", "name": "helmut test neues startband"}   # mit DNxHD-Startband zuerst


def test_triage_vorschau_mit_korrektur(setup):
    ctx, runner, engine, m = setup
    engine.session = None
    ctx.settings.set_local("import.import_vorschau.triage_vorschau", "immer")
    fetch_app.start_fetch(ctx, runner, {"id": "p1", "group": "test", "name": "helmut test"})
    q = _wait(runner, {"frage"})["question"]
    assert q["kind"] == "triage_vorschau"
    v = q["view"]
    assert v["moves"] == 1 and v["unsure"] == 0 and v["template"] == "test/helmut test"
    lanes = {l["id"]: l for l in v["lanes"]}
    assert [c["key"] for c in lanes["Musik1"]["clips"]] == ["Atmo L#1"] and lanes["Musik1"]["cat"] == "music"
    assert {"A1", "A10", "Musik8", "Spr 2"} <= set(lanes)                         # Standardzeilen + benutzte
    row = next(r for r in v["rows"] if r["key"] == "Atmo L#1")
    assert (row["label"], row["targets"], row["moved"]) == ("Musik", "Musik1", True)
    v2 = fetch_app.triage_correct(ctx, {"track": "Atmo L", "index": 1, "category": "atmo"})
    assert v2["moves"] == 0
    with pytest.raises(CommandError):
        fetch_app.triage_correct(ctx, {"track": "Atmo L", "index": 1, "category": "quatsch"})
    runner.answer(q["id"], True)
    done = _wait(runner, {"fertig", "fehler"})
    assert done["state"] == "fertig", done["error"]
    assert done["result"]["moves"] == 0 and not engine.clips.get("Musik1")
    with pytest.raises(CommandError, match="Keine Triage-Vorschau"):
        fetch_app.triage_correct(ctx, {"track": "Atmo L", "index": 1, "category": "atmo"})


def test_triage_vorschau_abgebrochen(setup):
    ctx, runner, engine, m = setup
    engine.session = None
    ctx.settings.set_local("import.import_vorschau.triage_vorschau", "immer")
    fetch_app.start_fetch(ctx, runner, {"id": "p1", "group": "test", "name": "helmut test"})
    q = _wait(runner, {"frage"})["question"]
    runner.answer(q["id"], False)
    end = _wait(runner, {"fehler", "abgebrochen"})
    assert "nicht bestätigt" in end["error"]
    assert m.mix_value("p1", "zustand") == "uebernommen"           # Fenster bietet dann „Freigeben“ an


def test_template_vorauswahl_und_merken(setup, tmp_path):
    ctx, runner, engine, m = setup
    (tmp_path / "T" / "test").mkdir(parents=True)
    (tmp_path / "T" / "test" / "SM Session.ptxt").write_text("x")
    ctx.settings.set_local("persoenlich.zuletzt_je_sendung", {"Heimatjournal": "test/SM Session"})
    t = _library(ctx, tmp_path / "T", show="Heimatjournal")
    assert t["default"] == {"group": "test", "name": "SM Session"} and t["hint"] == "zuletzt für Heimatjournal"
    engine.session = None
    fetch_app.start_fetch(ctx, runner, {"id": "p1", "group": "test", "name": "helmut test"})
    done = _wait(runner, {"fertig", "fehler"})
    assert done["state"] == "fertig", done["error"]
    assert ctx.settings.get("persoenlich.zuletzt_je_sendung") == {"Heimatjournal": "test/helmut test"}


def test_holen_schreibt_ablage_und_spiegel(setup, tmp_path):
    ctx, runner, engine, m = setup
    m.projects["p1"]["path"] = str(tmp_path / "Helmut" / "Bahnstreik" / "Bahnstreik.prproj")
    engine.session = None
    fetch_app.start_fetch(ctx, runner, {"id": "p1", "group": "test", "name": "helmut test"})
    done = _wait(runner, {"fertig", "fehler"})
    assert done["state"] == "fertig", done["error"]
    assert m.mix_value("p1", "session_ablage") == "lokal_spiegelung"
    assert m.mix_value("p1", "session_spiegel") == str(tmp_path / "Helmut" / "Bahnstreik" / "Ton" / "ProTools" / "Bahnstreik")


def test_holen_mit_spurprofil_des_templates(setup):
    """Spurprofil (Einstellungen › Import › Spurprofile): eigene Zielspuren und Ausblend-Häkchen statt A1–A4."""
    from tonmischung.core import track_profile as TP
    ctx, runner, engine, m = setup
    engine.session = None
    tracks = [TP.ProfileTrack("A1", hide_when_empty=True), TP.ProfileTrack("A2"),
              TP.ProfileTrack("A3", triage="o_ton"), TP.ProfileTrack("A4", triage="o_ton"),
              TP.ProfileTrack("A11", triage="atmo"), TP.ProfileTrack("A12", triage="atmo"),
              TP.ProfileTrack("Musik1", triage="musik"), TP.ProfileTrack("Musik2", triage="musik"),
              TP.ProfileTrack("Gibt es nicht", export=True, missing=True)]
    ctx.settings.set_local("import.spurprofile",
                           [TP.profile_to_dict(TP.TrackProfile("test", "helmut test", tuple(tracks)))])
    fetch_app.start_fetch(ctx, runner, {"id": "p1", "group": "test", "name": "helmut test"})
    done = _wait(runner, {"fertig", "fehler", "frage"})
    if done["state"] == "frage":                                  # Triage-Vorschau bestätigen
        runner.answer(done["question"]["id"], True)
        done = _wait(runner, {"fertig", "fehler"})
    assert done["state"] == "fertig", done["error"]
    assert engine.clips.get("A3") and engine.clips.get("A4") and not engine.clips.get("A1")  # O-Ton auf A3/A4
    assert engine.clips.get("A11") and engine.clips.get("A12") and not engine.clips.get("A5")  # Atmo
    hidden = {t.name for t in engine.track_objs if t.track_attributes.is_hidden}
    assert "A1" in hidden and "A2" not in hidden and "A5" not in hidden               # nur angehakte leere
    assert m.mix_value("p1", "template") == "test/helmut test"
    assert any("„Gibt es nicht“ fehlt in der Session" in w for w in done["result"]["warnings"])


def test_frage_fehlende_spuren_nennt_den_ort():
    """In offene Session holen fragt nach Spuren „in der offenen Session“, nicht „im Template“ (live 13.10.2026)."""
    asked = []

    class Jc:
        def ask(self, q):
            asked.append(q)
            return {"Audio 1": "weglassen"}

    fetch_app.ask_missing(Jc())(["Audio 1"], ["A1"])
    fetch_app.ask_missing(Jc(), "in der offenen Session")(["Audio 1"], ["A1"])
    assert asked[0]["title"] == "Spuren fehlen im Template"
    assert asked[1]["title"] == "Spuren fehlen in der offenen Session"
    assert "gleichnamige Spur in der offenen Session" in asked[1]["text"]
