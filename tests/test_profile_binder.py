"""Spurprofil der offenen Session: Zuordnung über das Template oder die Spurnamen, Einstellungsebene „Spurprofil“."""
import pytest

from fakes import FakeEngine, _Track
from tonmischung.app.context import AppContext
from tonmischung.app.profile_binder import ProfileBinder
from tonmischung.config.settings import Settings, SettingLockedError
from tonmischung.core import track_profile as TP
from tonmischung.helmut.client import HelmutClient
from tonmischung.ptsl.client import PtslClient
from tonmischung.punch.settings_bridge import export_tracks

TRACKS = ["ST", "IT", "_AD", "DoE", "OGA", "Mus", "Spr", "A1", "A2"]
PROFILE = TP.TrackProfile("test", "helmut test neues startband", (
    TP.ProfileTrack("ST", export=True, channel=1, content="Sendeton", r128=True, rec_a=True, monitor_a=True),
    TP.ProfileTrack("IT", export=True, channel=3, content="IT"),
    TP.ProfileTrack("_AD"), TP.ProfileTrack("DoE"),
    TP.ProfileTrack("OGA", export=True, channel=9, content="OGA", rec_a=True),
    TP.ProfileTrack("Mus", export=True, channel=11, content="Musik"),
    TP.ProfileTrack("Spr", export=True, channel=13, content="Kommentar", rec_a=True),
    TP.ProfileTrack("A1", triage="o_ton"), TP.ProfileTrack("A2", triage="o_ton")))


def _ctx(tmp_path, session=("Beitrag", "/S/Beitrag/Beitrag.ptx"), names=TRACKS):
    engine = FakeEngine(session=session)
    engine.track_objs = [_Track(t, "AudioTrack", False) for t in names]
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local("import.spurprofile", [TP.profile_to_dict(PROFILE)])
    ctx = AppContext(s, HelmutClient("http://127.0.0.1:9", lambda: ("t", "t")), PtslClient(engine_factory=lambda: engine),
                     "Jens", "Ton 3", str(tmp_path), None, "x")
    return ctx, engine


def test_zuordnung_ueber_das_template(tmp_path):
    ctx, _ = _ctx(tmp_path, names=["ST", "Anders"])                   # Spurnamen passen kaum
    b = ProfileBinder(ctx)
    b.tick()
    assert ctx.settings.profile_label == ""                                   # ohne Gedächtnis: kein Profil
    b.bind("/S/Beitrag/Beitrag.ptx", "test", "helmut test neues startband")
    assert b.status() == {"profil": "test/helmut test neues startband", "zuordnung": "Template"}
    assert (tmp_path / "session_templates.json").exists()
    assert ProfileBinder(ctx)._read() == {"/S/Beitrag/Beitrag.ptx": "test/helmut test neues startband"}


def test_zuordnung_ueber_spurnamen_und_wirkung_auf_export_und_aufnahme(tmp_path):
    ctx, _ = _ctx(tmp_path)
    s = ctx.settings
    assert export_tracks(s)[0] == ["ST", "A IT", "OGA", "Mus", "Spr"]               # allgemeine Spurtabelle
    b = ProfileBinder(ctx)
    b.tick()
    assert b.status()["zuordnung"] == "Spurnamen" and s.source("export.spuren_lautheit.spurtabelle") == "spurprofil"
    assert export_tracks(s) == (["ST", "IT", "OGA", "Mus", "Spr"], ["ST"])
    assert s.get("aufnahme.spuren.rec_a") == ["ST", "OGA", "Spr"] and s.get("aufnahme.spuren.monitor_a") == ["ST"]
    with pytest.raises(SettingLockedError, match="Spurprofil „test/helmut test neues startband“"):
        s.set_local("export.spuren_lautheit.spurtabelle", [])
    s.set_local("import.zuordnungsregeln.mindest_sicherheit", 70)              # anderes bleibt änderbar


def test_session_zu_oder_profil_geaendert(tmp_path):
    ctx, engine = _ctx(tmp_path)
    b = ProfileBinder(ctx)
    b.tick()
    assert ctx.settings.profile_label
    engine.session = None                                                    # Session geschlossen
    b.tick()
    assert ctx.settings.profile_label == "" and ctx.settings.source("aufnahme.spuren.rec_a") == "standard"
    engine.session = ("Beitrag", "/S/Beitrag/Beitrag.ptx")
    only_import = TP.TrackProfile("test", "helmut test neues startband",
                                  tuple(TP.ProfileTrack(t, triage="o_ton" if t.startswith("A") else "") for t in TRACKS))
    ctx.settings.set_local("import.spurprofile", [TP.profile_to_dict(only_import)])
    b.tick()                                                                 # Profil geändert → neu angewendet
    assert b.status()["profil"] == "test/helmut test neues startband"
    assert ctx.settings.source("export.spuren_lautheit.spurtabelle") != "spurprofil"   # reines Import-Profil
    assert ctx.settings.source("aufnahme.spuren.rec_a") != "spurprofil"


def test_waehrend_eines_auftrags_nur_auf_anstoss(tmp_path):
    ctx, _ = _ctx(tmp_path)
    b = ProfileBinder(ctx, busy=lambda: True)
    b.tick()
    assert ctx.settings.profile_label == ""
    b.refresh()                                                              # ausdrücklich: auch während eines Auftrags
    assert ctx.settings.profile_label == "test/helmut test neues startband"


def test_feldansicht_zeigt_herkunft(tmp_path):
    from tonmischung.app import settings_view as sv
    from tonmischung.config.settings import load_schema
    ctx, _ = _ctx(tmp_path)
    ProfileBinder(ctx).tick()
    v = sv.field_view(ctx.settings, load_schema(), "export.spuren_lautheit.spurtabelle")
    assert v["locked"] and v["layer_text"] == "aus dem Spurprofil „test/helmut test neues startband“ – dort ändern"
