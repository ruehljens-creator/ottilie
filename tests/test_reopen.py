"""Gespeicherten Stand als Kopie öffnen (Plan M4-Rest, Schritt 3) – gegen Mock-Helmut und Fake-Pro-Tools."""
import os
from datetime import datetime

import pytest

from fakes import FakeEngine, _Track, command_error
from tonmischung.core.mixstate import MixState
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mapping import Contribution
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.workflow.fetch import FetchError, NeedsConfirmation
from tonmischung.workflow.reopen import (FILE, FOLDER, CopyPlan, check_copy, plan_copy, reopen,
                                         restore_target)

NOW = datetime(2026, 10, 2, 23, 44)


def _session(folder, name="Wahl", content=b"PTX-ORIGINAL"):
    os.makedirs(os.path.join(folder, "Audio Files"), exist_ok=True)
    with open(os.path.join(folder, "Audio Files", "A1_01.wav"), "wb") as f:
        f.write(b"RIFF....")
    path = os.path.join(folder, f"{name}.ptx")
    with open(path, "wb") as f:
        f.write(content)
    return path


@pytest.fixture
def mock():
    m = MockHelmut()
    m.url = m.start()
    yield m
    m.stop()


def _engine(**kw):
    e = FakeEngine(**kw)
    e.track_objs = [_Track("ST", "AudioTrack", True), _Track("Video 1", "VideoTrack", True)]
    return e


def _run(mock, engine, tmp_path, pid, **kw):
    service = MixService(HelmutClient(mock.url, lambda: ("test", "test"), sleep=lambda s: None), "Jens", "Ton 3",
                         now=lambda: "jetzt")
    return reopen(pid, service=service, ptsl=PtslClient(engine_factory=lambda: engine),
                  session_parent=str(tmp_path / "neu"), now=lambda: NOW, **kw)


def test_name_der_kopie_ueberschreibt_nichts(tmp_path):
    src = _session(str(tmp_path / "Wahl"))
    assert restore_target(src, NOW) == str(tmp_path / "Wahl" / "Wahl_wiederhergestellt_2344.ptx")
    open(tmp_path / "Wahl" / "Wahl_wiederhergestellt_2344.ptx", "w").close()
    assert restore_target(src, NOW).endswith("Wahl_wiederhergestellt_2344_2.ptx")


def test_plan_je_fall():
    def c(**mix):
        return Contribution("p9", "Wahl: Ergebnis", "hs", False, MixState("zurueckgegeben", **mix))
    assert plan_copy(c(), "/neu", NOW, lambda p: False) is None
    assert plan_copy(c(session_path="/S/W.ptx"), "/neu", NOW, lambda p: False) is None   # neu importieren
    p = plan_copy(c(session_path="/S/W.ptx", return_keep="letzter_stand"), "/neu", NOW, lambda p: False)
    assert (p.kind, p.target) == (FILE, "/S/W_wiederhergestellt_2344.ptx")
    d = plan_copy(c(duplicate_session="/S/Orig/Orig.ptx"), "/neu", NOW, lambda p: False)
    assert (d.kind, d.source_dir, d.target_dir, d.target) == (FOLDER, "/S/Orig", "/neu/Wahl", "/neu/Wahl/Wahl.ptx")


def test_pruefung_quelle_und_platz(tmp_path):
    with pytest.raises(FetchError, match="nicht erreichbar"):
        check_copy(CopyPlan(FILE, str(tmp_path / "weg.ptx"), str(tmp_path / "neu.ptx")))
    src = _session(str(tmp_path / "Orig"), "Orig")
    plan = CopyPlan(FOLDER, src, str(tmp_path / "Neu" / "Neu.ptx"), str(tmp_path / "Orig"), str(tmp_path / "Neu"))
    with pytest.raises(FetchError, match="Zu wenig Platz"):
        check_copy(plan, free=lambda p: 10)
    check_copy(plan)


def test_freigegebenen_stand_als_kopie_oeffnen(mock, tmp_path):
    src = _session(str(tmp_path / "Wahl"))
    mock.add_project("p1", "Wahl", "Heimatjournal", state="zurueckgegeben", session_pfad=src, session_ablage="lokal",
                     rueckgabe_naechstes_oeffnen="letzter_stand", rueckgabe_grund="Workstation abgestürzt",
                     rueckgabe_von_raum="Ton 2")
    engine = _engine(session=("Andere", "/x/Andere.ptx"))
    with pytest.raises(NeedsConfirmation):
        _run(mock, engine, tmp_path, "p1")
    assert mock.mix_value("p1", "zustand") == "zurueckgegeben"                   # nichts verändert
    r = _run(mock, engine, tmp_path, "p1", confirmed=True)
    target = str(tmp_path / "Wahl" / "Wahl_wiederhergestellt_2344.ptx")
    assert (r.session_path, r.kind, r.tracks, r.warnings) == (target, FILE, 2, [])
    assert open(target, "rb").read() == open(src, "rb").read() == b"PTX-ORIGINAL"   # Original unverändert
    assert "close_session(save_on_close=True)" in engine.calls and engine.session[1] == target
    assert engine.calls.index("save_session") < engine.calls.index("close_session(save_on_close=True)")
    assert mock.mix_value("p1", "zustand") == "importiert" and mock.mix_value("p1", "session_pfad") == target
    assert mock.mix_value("p1", "rueckgabe_naechstes_oeffnen") == "" and mock.mix_value("p1", "rueckgabe_grund") == ""
    assert mock.mix_value("p1", "mischer") == "Jens"


def test_speichern_scheitert_nichts_kopiert(mock, tmp_path):
    src = _session(str(tmp_path / "Wahl"))
    mock.add_project("p1", "Wahl", "Heimatjournal", state="zurueckgegeben", session_pfad=src,
                     rueckgabe_naechstes_oeffnen="letzter_stand")
    engine = _engine(session=("Andere", "/x/Andere.ptx"), fail={"save_session": command_error("PT_UnknownError")})
    with pytest.raises(Exception):
        _run(mock, engine, tmp_path, "p1", confirmed=True)
    assert not any("close_session" in c for c in engine.calls)
    assert sorted(os.listdir(tmp_path / "Wahl")) == ["Audio Files", "Wahl.ptx"]


def test_duplikat_mit_mischstand_kopiert_den_ordner(mock, tmp_path):
    src = _session(str(tmp_path / "Orig"), "Orig")
    open(tmp_path / "Orig" / "Orig_wiederhergestellt_2200.ptx", "w").close()      # anderer Stand im Ordner
    mock.add_project("p2", "Wahl: Kurzfassung", "Heimatjournal", duplikat_von="Wahl (p1)", duplikat_mischstand=src)
    r = _run(mock, _engine(session=None), tmp_path, "p2")
    target = str(tmp_path / "neu" / "Wahl" / "Wahl.ptx")
    assert (r.session_path, r.kind) == (target, FOLDER)
    assert open(target, "rb").read() == b"PTX-ORIGINAL"
    assert os.path.exists(tmp_path / "neu" / "Wahl" / "Audio Files" / "A1_01.wav")
    assert sorted(os.listdir(tmp_path / "neu" / "Wahl")) == ["Audio Files", "Wahl.ptx"]   # andere Stände nicht mit
    assert sorted(os.listdir(tmp_path / "Orig")) == ["Audio Files", "Orig.ptx", "Orig_wiederhergestellt_2200.ptx"]
    assert mock.mix_value("p2", "session_pfad") == target and mock.mix_value("p2", "duplikat_von") == "Wahl (p1)"


def test_ohne_stand_kein_oeffnen(mock, tmp_path):
    mock.add_project("p1", "Wahl", "Heimatjournal", state="zurueckgegeben", session_pfad="/S/W.ptx",
                     rueckgabe_naechstes_oeffnen="neu_importieren")
    with pytest.raises(FetchError, match="neu importieren"):
        _run(mock, _engine(session=None), tmp_path, "p1")


def test_pro_tools_oeffnet_nicht(mock, tmp_path):
    src = _session(str(tmp_path / "Wahl"))
    mock.add_project("p1", "Wahl", "Heimatjournal", state="zurueckgegeben", session_pfad=src,
                     rueckgabe_naechstes_oeffnen="letzter_stand")
    engine = _engine(session=None)
    engine.open_session = lambda path: None                                   # Dialog blockiert
    with pytest.raises(FetchError, match="nicht geöffnet"):
        _run(mock, engine, tmp_path, "p1")
    assert mock.mix_value("p1", "zustand") == "uebernommen"                      # bleibt übernommen


def test_app_bietet_stand_an_und_oeffnet(mock, tmp_path):
    import time

    from tonmischung.app import backups_app, commands
    from tonmischung.app.bridge import Bridge
    from tonmischung.app.context import AppContext
    from tonmischung.app.jobs import JobRunner
    from tonmischung.config.settings import Settings
    src = _session(str(tmp_path / "Wahl"))
    mock.add_project("p1", "Wahl", "Heimatjournal", state="zurueckgegeben", session_pfad=src,
                     rueckgabe_naechstes_oeffnen="letzter_stand", rueckgabe_grund="Raumwechsel")
    engine = _engine(session=None)
    ctx = AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient(mock.url, lambda: ("test", "test")),
                     PtslClient(engine_factory=lambda: engine), "Jens", "Ton 3", str(tmp_path / "neu"), mock, "x")
    d = commands.contribution_detail(ctx, {"id": "p1"})
    assert d["reopen"]["kind"] == "datei" and d["reopen"]["source"] == src
    assert "Beim Übernehmen: zuletzt gespeicherte Session wird als Kopie geöffnet." in d["banners"][0]["text"]
    runner = JobRunner(Bridge(emit=lambda p: None, inline=True))
    job = backups_app.start_reopen(ctx, runner, {"id": "p1"})
    assert job["title"] == "Stand öffnen: Wahl"
    t0 = time.monotonic()
    while runner.current()["state"] not in ("fertig", "fehler") and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    done = runner.current()
    assert done["state"] == "fertig", done["error"]
    assert os.path.basename(done["result"]["session"]).startswith("Wahl_wiederhergestellt_")
    d2 = commands.contribution_detail(ctx, {"id": "p1"})
    assert d2["reopen"] is None and not d2["banners"]


def test_dialog_beim_oeffnen_wird_genannt_und_abgewartet(tmp_path):
    from tonmischung.ptsl.errors import PtslTimeoutError
    from tonmischung.workflow.reopen import open_and_wait

    class Pt:
        def __init__(self):
            self.polls = 0
            self.dialog_events = []
            self.dialog_handler = None

        def open(self, path):
            raise PtslTimeoutError("OpenSession: keine Antwort")

        def open_session(self):
            self.polls += 1
            if self.polls < 3:
                raise PtslTimeoutError("blockiert")
            return type("S", (), {"name": "W", "path": "/S/W.ptx"})()

    texts = []

    class Prog:
        def update(self, f, t):
            texts.append(t)
    t = [0.0]
    assert open_and_wait(Pt(), "/S/W.ptx", Prog(), dialogs=lambda: ["Save Session"],
                         sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0]) == []
    assert texts[0] == "Pro Tools zeigt „Save Session“ – bitte in Pro Tools bestätigen"
    # Erst nach dem Zeitlimit erschienen: der bekannte Dialog wird beantwortet, der Hinweis kommt zurück
    pt, shown = Pt(), ["Missing AAX Plugins"]
    pt.dialog_handler = lambda names: shown.clear() or "fehlende Plugins bestätigt"
    hints = open_and_wait(pt, "/S/W.ptx", Prog(), dialogs=lambda: list(shown),
                          sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert hints == ["fehlende Plugins bestätigt"] == pt.dialog_events
    pt = Pt()
    pt.open_session = lambda: (_ for _ in ()).throw(PtslTimeoutError("blockiert"))
    with pytest.raises(FetchError, match="wartet auf Bestätigung"):
        open_and_wait(pt, "/S/W.ptx", Prog(), wait_s=10, dialogs=lambda: [],
                      sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])


def test_aus_dem_spiegel_in_einem_anderen_raum(mock, tmp_path):
    from tonmischung.core.mixstate import MixState, keep_options
    mirror_dir = tmp_path / "Helmut" / "Wahl" / "Ton" / "ProTools" / "Wahl"
    _session(str(mirror_dir), "Wahl", content=b"GESPIEGELT")
    (mirror_dir / ".Spiegel-Vorversionen" / "20261003-1000").mkdir(parents=True)
    (mirror_dir / ".Spiegel-Vorversionen" / "20261003-1000" / "Wahl.ptx").write_bytes(b"ALT")
    m = MixState("in_mischung", "A. Wolf", "Ton 2", session_path="/Lokal/Ton2/Wahl/Wahl.ptx",
                 session_storage="lokal_spiegelung", session_mirror=str(mirror_dir))
    assert keep_options(m, here=False) == ["letzter_stand", "neu_importieren"]
    assert keep_options(MixState("in_mischung", session_path="/x.ptx", session_storage="lokal_spiegelung"),
                        here=False) == ["neu_importieren"]
    mock.add_project("p1", "Wahl", "hs", state="zurueckgegeben", session_pfad="/Lokal/Ton2/Wahl/Wahl.ptx",
                     session_ablage="lokal_spiegelung", session_spiegel=str(mirror_dir),
                     rueckgabe_naechstes_oeffnen="letzter_stand", rueckgabe_von_raum="Ton 2")
    r = _run(mock, _engine(session=None), tmp_path, "p1")
    target = str(tmp_path / "neu" / "Wahl" / "Wahl.ptx")
    assert r.session_path == target and r.kind == FOLDER and open(target, "rb").read() == b"GESPIEGELT"
    assert sorted(os.listdir(tmp_path / "neu" / "Wahl")) == ["Audio Files", "Wahl.ptx"]   # ohne Vorversionen
    assert (mirror_dir / "Wahl.ptx").read_bytes() == b"GESPIEGELT"                       # Spiegel unverändert
    assert mock.mix_value("p1", "session_spiegel") == str(mirror_dir)                     # spiegelt weiter dorthin
    assert mock.mix_value("p1", "session_ablage") == "lokal_spiegelung"
