"""Nutzer (Plan Nutzer & Räume, Schritt 1): anlegen, umbenennen, duplizieren, ins Archiv löschen, anmelden."""
import json
import os
from datetime import datetime

import pytest

from fakes import FakeEngine
from tonmischung.app import users_app as UA
from tonmischung.app.bridge import CommandError
from tonmischung.app.context import AppContext
from tonmischung.config.settings import Settings
from tonmischung.core.users import UserError, UserStore, slug
from tonmischung.helmut.client import HelmutClient
from tonmischung.ptsl.client import PtslClient

NOW = lambda: datetime(2026, 10, 4, 18, 0)   # noqa: E731


def test_ablage_anlegen_umbenennen_duplizieren_archiv(tmp_path):
    st = UserStore(tmp_path / "Nutzer", now=NOW)
    assert st.users() == [] and slug("Jens Rühl") == "jens-ruehl" and slug("!!") == "nutzer"
    jens = st.create("Jens Rühl")
    assert (jens.id, jens.name) == ("jens-ruehl", "Jens Rühl")
    with pytest.raises(UserError, match="gibt es schon"):
        st.create("jens rühl")
    with pytest.raises(UserError, match="Namen"):
        st.create("   ")
    os.makedirs(tmp_path / "Nutzer" / "jens-ruehl" / "Templates")
    (tmp_path / "Nutzer" / "jens-ruehl" / "einstellungen.json").write_text('{"a": 1}')
    kopie = st.duplicate("jens-ruehl", "Gast")
    assert kopie.id == "gast" and (tmp_path / "Nutzer" / "gast" / "einstellungen.json").read_text() == '{"a": 1}'
    meta = json.loads((tmp_path / "Nutzer" / "gast" / "nutzer.json").read_text())
    assert meta["name"] == "Gast" and meta["kopie_von"] == "Jens Rühl"
    assert st.rename("gast", "Gast 2").name == "Gast 2" and st.get("gast").name == "Gast 2"
    with pytest.raises(UserError, match="gibt es schon"):
        st.rename("gast", "Jens Rühl")
    zweit = st.create("Jens-Rühl")                                                   # anderer Name, gleicher Slug
    assert zweit.id == "jens-ruehl-2"
    dest = st.archive("gast")
    assert dest.endswith("Archiv/gast-20261004-180000") and os.path.exists(os.path.join(dest, "einstellungen.json"))
    assert [u.name for u in st.users()] == ["Jens Rühl", "Jens-Rühl"]                   # Archiv zählt nicht
    assert st.get("../x") is None and st.get("") is None


def _ctx(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local("system.raeume_nutzer.ablage", "gemeinsamer_ordner")
    s.set_local("system.raeume_nutzer.gemeinsamer_ordner", str(tmp_path / "Storage"))
    return AppContext(s, HelmutClient("http://127.0.0.1:9", lambda: ("t", "t")),
                      PtslClient(engine_factory=lambda: FakeEngine()), "Jens", "Ton 3", str(tmp_path))


class Runner:
    def __init__(self, state=None):
        self.state = state

    def current(self):
        return {"state": self.state} if self.state else None

    def busy(self):
        return self.state in ("laeuft", "frage")


def test_start_anmelden_standard_und_sperre(tmp_path):
    """Jens 04.10.2026: beim Start fragen; Standard-Nutzer meldet sich automatisch an; Holen usw. nur angemeldet."""
    ctx = _ctx(tmp_path)
    UA.startup(ctx)                                     # erster Start: Nutzer aus dem bisherigen Mischernamen
    v = UA.view(ctx)
    assert [u["name"] for u in v["users"]] == ["Jens"] and v["needs_login"] and v["standard"] == ""
    assert v["ablage"] == str(tmp_path / "Storage" / "Ottilie Nutzer")
    with pytest.raises(CommandError, match="zuerst anmelden"):
        UA.require_user(ctx)
    UA.create(ctx, {"name": "A. Wolf"})
    v = UA.login(ctx, Runner(), {"id": "a-wolf", "standard": True})
    assert v["current"]["name"] == "A. Wolf" and ctx.mixer == "A. Wolf" and v["standard"] == "a-wolf"
    UA.require_user(ctx)
    with pytest.raises(CommandError, match="kein Auftrag"):
        UA.login(ctx, Runner("laeuft"), {"id": "jens"})
    with pytest.raises(CommandError, match="angemeldete Nutzer"):
        UA.delete(ctx, {"id": "a-wolf"})
    again = _ctx(tmp_path)                              # Neustart: Standard-Nutzer ist sofort angemeldet
    again.settings = ctx.settings
    UA.startup(again)
    assert again.user.name == "A. Wolf" and again.mixer == "A. Wolf"
    UA.login(ctx, Runner(), {"id": "jens", "standard": False})
    r = UA.delete(ctx, {"id": "a-wolf"})
    assert "ins Archiv" in r["message"] and r["standard"] == "" and [u["name"] for u in r["users"]] == ["Jens"]
    r = UA.duplicate(ctx, {"id": "jens", "name": "Jens (Kopie)"})
    assert [u["name"] for u in r["users"]] == ["Jens", "Jens (Kopie)"]
    r = UA.rename(ctx, {"id": "jens", "name": "Jens R."})
    assert r["current"]["name"] == "Jens R." and ctx.mixer == "Jens R."
    with pytest.raises(CommandError, match="gibt es schon"):
        UA.create(ctx, {"name": "jens r."})


def test_selbsttest_meldet_den_ersten_nutzer_an(tmp_path):
    ctx = _ctx(tmp_path)
    UA.startup(ctx, selftest=True)
    assert ctx.user is not None and ctx.user.name == "Jens"


def test_holen_braucht_anmeldung(tmp_path):
    from tonmischung.app import commands
    from tonmischung.app.bridge import Bridge
    from tonmischung.app.jobs import JobRunner
    ctx = _ctx(tmp_path)
    bridge = Bridge(emit=lambda p: None)
    commands.register_all(bridge, ctx, JobRunner(bridge))
    for cmd in ("holen", "havarie_holen", "zurueckliefern", "havarie_ausliefern", "holen_offen"):
        with pytest.raises(CommandError, match="zuerst anmelden"):
            bridge._handlers[cmd]({"id": "x"})


def test_waehrend_eines_auftrags_gesperrt(tmp_path):
    """Review B25: Nutzer umbenennen mitten im Auftrag änderte den Mischer – Helmut meldete „gehört nicht mehr“."""
    import threading
    from tonmischung.app import commands
    from tonmischung.app.bridge import Bridge
    from tonmischung.app.jobs import JobRunner
    ctx = _ctx(tmp_path)
    bridge = Bridge(emit=lambda p: None)
    runner = JobRunner(bridge)
    commands.register_all(bridge, ctx, runner)
    release = threading.Event()
    runner.start("holen", "Holen: Bahnstreik", [("Import", 1)], lambda jc: release.wait(5))
    try:
        for cmd in commands.NOT_DURING_JOB:
            with pytest.raises(CommandError, match="„Holen: Bahnstreik“ fertig ist"):
                bridge._handlers[cmd]({"id": "jens", "name": "J. R."})
    finally:
        release.set()


def test_einstellungen_haengen_am_nutzer(tmp_path):
    """Schritt 2: Nutzer-Einstellungen gelten je Nutzer; Gerätebezogenes bleibt am Mac; ohne Anmeldung gilt der Mac;
    beim ersten Start gehen die bisherigen Werte des Macs an den ersten Nutzer."""
    ctx = _ctx(tmp_path)
    s = ctx.settings
    assert s.is_user_path("import.zuordnungsregeln.mindest_sicherheit")
    assert s.is_user_path("export.spuren_lautheit.lautheit.ziel_lufs") and s.is_user_path("persoenlich.templates")
    for mac in ("import.session_ablage.lokaler_ordner", "export.havarie_zielordner", "system.vocaster.aktiv",
                "export.dateiexporte.wav_ordner", "system.raeume_nutzer.standard_nutzer"):
        assert not s.is_user_path(mac), mac
    s.set_local("import.zuordnungsregeln.mindest_sicherheit", 90)                   # vor Schritt 2: am Mac
    s.set_local("export.havarie_zielordner", "/Volumes/Havarie")
    UA.startup(ctx)                                                                  # erster Nutzer übernimmt
    UA.login(ctx, Runner(), {"id": "jens"})
    assert s.get("import.zuordnungsregeln.mindest_sicherheit") == 90 and s.source("import.zuordnungsregeln.mindest_sicherheit") == "nutzer"
    UA.create(ctx, {"name": "A. Wolf"})
    UA.login(ctx, Runner(), {"id": "a-wolf"})
    assert s.get("import.zuordnungsregeln.mindest_sicherheit") == 80                 # neuer Nutzer: Hausstandard
    assert s.get("export.havarie_zielordner") == "/Volumes/Havarie"                  # Mac bleibt
    s.set_local("import.zuordnungsregeln.mindest_sicherheit", 70)
    s.set_local("export.havarie_zielordner", "/Volumes/Anders")                      # geht an den Mac
    UA.login(ctx, Runner(), {"id": "jens"})
    assert s.get("import.zuordnungsregeln.mindest_sicherheit") == 90 and s.get("export.havarie_zielordner") == "/Volumes/Anders"
    UA.login(ctx, Runner(), {"id": "a-wolf"})
    assert s.get("import.zuordnungsregeln.mindest_sicherheit") == 70
    s.reset_local("import.zuordnungsregeln.mindest_sicherheit")
    assert s.get("import.zuordnungsregeln.mindest_sicherheit") == 80
    UA.duplicate(ctx, {"id": "jens", "name": "Jens 2"})                              # Kopie mit Einstellungen
    UA.login(ctx, Runner(), {"id": "jens-2"})
    assert s.get("import.zuordnungsregeln.mindest_sicherheit") == 90
    from tonmischung.app import settings_view as SV
    from tonmischung.config.settings import load_schema
    v = SV.field_view(s, load_schema(), "import.zuordnungsregeln.mindest_sicherheit")
    assert v["layer_text"] == "Nutzer „Jens 2“" and v["resettable"] and any("Jens 2" in h for h in v["hints"])
    s.clear_user_layer()                                                             # ohne Anmeldung: Mac
    assert s.get("import.zuordnungsregeln.mindest_sicherheit") == 90 and s.source("import.zuordnungsregeln.mindest_sicherheit") == "lokal"



def test_vor_schritt_2_angelegte_nutzer_uebernehmen_den_mac(tmp_path):
    """Live 04.10.2026: „admin“ und „Jens“ entstanden vor Schritt 2 (ohne Einstellungsdatei) – beide übernehmen die
    bisherigen Werte des Macs; später angelegte Nutzer starten mit dem Hausstandard."""
    ctx = _ctx(tmp_path)
    ctx.settings.set_local("import.zuordnungsregeln.mindest_sicherheit", 95)
    st = UA.store(ctx)
    for name in ("admin", "Jens"):
        uid = st._free_id(name)
        os.makedirs(os.path.join(st.root, uid))
        open(os.path.join(st.root, uid, "nutzer.json"), "w").write(json.dumps({"name": name}))
    UA.startup(ctx)
    for uid in ("admin", "jens"):
        assert st.load_settings(uid) == {"import.zuordnungsregeln.mindest_sicherheit": 95}
    UA.create(ctx, {"name": "Neu"})
    UA.startup(ctx)                                                                  # zweiter Start: nichts doppelt
    assert st.load_settings("neu") == {}
