import itertools
import os

import pyloudnorm as pyln
import pytest
import soundfile as sf

from fakes import FakeEngine, _Track
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import PtslError
from tonmischung.sessions import pt_export
from tonmischung.sessions.pt_export import ExportError, run_pt_export, wait_for_consolidated
from tonmischung.workflow.deliver import copy_atomic, deliver
from tonmischung.workflow.fetch import FetchError

WAIT = {"stable_s": 0, "poll_s": 0, "sleep": lambda s: None}
EXPORT = ["ST", "IT", "OGA", "Mus", "Spr"]


@pytest.fixture(autouse=True)
def _schnell(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)        # PunchBuddy-Wartezeiten
    real = pt_export.wait_for_consolidated
    # Testuhr: jeder Blick auf die Uhr ist 1 s später – Zeitlimits laufen schnell ab
    monkeypatch.setattr(pt_export, "wait_for_consolidated", lambda *a, **k: real(
        *a, **{**k, **WAIT, "clock": itertools.count(0, 1).__next__}))


def _engine(tmp_path, empty=("Spr",)):
    session = str(tmp_path / "Bahnstreik" / "Bahnstreik.ptx")
    os.makedirs(tmp_path / "Bahnstreik" / "Audio Files")
    e = FakeEngine(session=("Bahnstreik", session))
    e.track_objs = [_Track(t, "AudioTrack", t not in empty) for t in EXPORT] + \
                   [_Track("Video 1", "VideoTrack", True)]
    return e


def test_pro_tools_teil(tmp_path):
    e = _engine(tmp_path)
    r = run_pt_export(PtslClient(engine_factory=lambda: e), EXPORT, str(tmp_path / "out"),
                      loudness_tracks=["ST"])
    assert list(r.wav_files) == ["ST", "IT", "OGA", "Mus", "Spr"]        # leere Spr mit dabei
    assert r.warnings == ["Leer, als digitale Null konsolidiert: Spr"]
    assert (r.in_time, r.out_time) == ("10:00:00:00.00", "10:00:30:00.00")   # bis Videoende
    st, rate = sf.read(r.wav_files["ST"])
    assert sf.info(r.wav_files["ST"]).subtype == "PCM_24"
    assert pyln.Meter(rate).integrated_loudness(st) == pytest.approx(-23.0, abs=0.1)
    it, _ = sf.read(r.wav_files["IT"])
    assert pyln.Meter(rate).integrated_loudness(it) < -26          # IT nicht normalisiert
    # Reihenfolge nach Jens: speichern, Spuren mit Material kürzen, alle Exportspuren
    # einblenden, alle – auch leere – konsolidieren, dann R128 und speichern
    i_save = e.calls.index("save_session")
    i_clear = e.calls.index("clear ST,IT,OGA,Mus")
    i_hidden = e.calls.index("hidden=False ST,IT,OGA,Mus,Spr")
    i_cons = e.calls.index("consolidate ST,IT,OGA,Mus,Spr")
    assert i_save < i_clear < i_hidden < i_cons
    assert e.calls[-1] == "save_session"


def test_audio_wird_auf_videolaenge_abgelaengt(tmp_path):
    e = _engine(tmp_path)
    e.video_end = "10:12:34:05.00"
    r = run_pt_export(PtslClient(engine_factory=lambda: e), ["ST"], str(tmp_path / "out"),
                      loudness_tracks=[])
    assert (r.in_time, r.out_time) == ("10:00:00:00.00", "10:12:34:05.00")
    assert "selection 00:00:00:00.00–10:00:00:00.00" in e.calls      # vorher weg
    assert "selection 10:12:34:05.00–23:59:59:24.00" in e.calls      # nachher weg
    assert "selection 10:00:00:00.00–10:12:34:05.00" in e.calls      # konsolidiert
    assert "select_all_clips_on_track(Video 1)" in e.calls


def test_ohne_neue_dateien_abbruch(tmp_path):
    """PunchBuddy 2.0.3: entsteht keine neue Datei, einmal mit erneuerter Auswahl wiederholen, dann Abbruch."""
    e = _engine(tmp_path)
    e.consolidate_writes = False
    with pytest.raises(ExportError, match="keine neue Datei erzeugt"):
        run_pt_export(PtslClient(engine_factory=lambda: e), ["ST"], str(tmp_path / "out"),
                      loudness_tracks=["ST"])
    assert e.calls.count("consolidate ST") == 2
    assert not os.path.exists(tmp_path / "out")                      # nichts exportiert


def test_konsolidieren_im_zweiten_versuch(tmp_path):
    e = _engine(tmp_path)
    e.consolidate_writes = False
    real = e.consolidate_clip

    def consolidate():
        real()
        e.consolidate_writes = True                                   # erst der 2. Versuch wirkt
    e.consolidate_clip = consolidate
    r = run_pt_export(PtslClient(engine_factory=lambda: e), ["ST"], str(tmp_path / "out"),
                      loudness_tracks=["ST"])
    assert e.calls.count("consolidate ST") == 2 and list(r.wav_files) == ["ST"]
    assert any("2. Versuch" in w for w in r.warnings)


def test_teilweise_konsolidiert_wird_nicht_wiederholt(tmp_path):
    """Hat Pro Tools für einige Spuren Dateien geschrieben, lief der Befehl – kein zweiter Versuch."""
    e = _engine(tmp_path)
    real = e.consolidate_clip

    def nur_st():
        e.selected = [t for t in e.selected if t == "ST"]
        real()
    e.consolidate_clip = nur_st
    with pytest.raises(ExportError, match="nicht nachweisbar.*IT"):
        run_pt_export(PtslClient(engine_factory=lambda: e), ["ST", "IT"], str(tmp_path / "out"),
                      loudness_tracks=[])
    assert e.calls.count("consolidate ST") == 1


def test_videoende_im_zweiten_versuch(tmp_path):
    """PunchBuddy 2.0.3: Pro Tools setzt die Clip-Auswahl unter Last verzögert."""
    e = _engine(tmp_path)
    real = e.select_all_clips_on_track
    versuche = []

    def langsam(name):
        real(name)
        if name == "Video 1" and not versuche:
            versuche.append(name)
            e._extent = ("00:00:00:00.00", "00:00:00:00.00")         # Auswahl noch leer
    e.select_all_clips_on_track = langsam
    meldungen = []
    r = run_pt_export(PtslClient(engine_factory=lambda: e), ["ST"], str(tmp_path / "out"),
                      loudness_tracks=[], log=meldungen.append)
    assert r.out_time == "10:00:30:00.00"
    assert any("2. Versuch" in m for m in meldungen)


def test_ohne_videoende_kein_export(tmp_path):
    e = _engine(tmp_path)
    e.video_start = e.video_end = "00:00:00:00.00"
    with pytest.raises(ExportError, match="nach 3 Versuchen nicht ermittelt"):
        run_pt_export(PtslClient(engine_factory=lambda: e), ["ST"], str(tmp_path / "out"), loudness_tracks=[])
    assert e.calls.count("select_all_clips_on_track(Video 1)") == 3
    assert not any(c.startswith(("clear", "consolidate")) for c in e.calls)


# ── Transport-Sperre (Jens: kein Export bei Play oder Record) ───────────────
def test_laufende_wiedergabe_wird_vor_dem_export_angehalten(tmp_path):
    e = _engine(tmp_path)
    e.transport = ["TS_TransportPlaying", "TS_TransportStopped"]
    run_pt_export(PtslClient(engine_factory=lambda: e), ["ST"], str(tmp_path / "out"), loudness_tracks=["ST"])
    assert e.calls.index("toggle_play_state") < e.calls.index("save_session") < e.calls.index("consolidate ST")


def test_aufnahme_haelt_nicht_an_kein_export(tmp_path):
    e = _engine(tmp_path)
    e.transport = ["TS_TransportRecording"]                          # Stopp wird nie bestätigt
    with pytest.raises(PtslError, match="Export abgebrochen: Pro Tools steht nicht \\(Aufnahme läuft\\)"):
        run_pt_export(PtslClient(engine_factory=lambda: e), ["ST"], str(tmp_path / "out"), loudness_tracks=["ST"])
    assert e.calls.count("toggle_play_state") == 1                   # nur einmal umschalten
    assert "save_session" not in e.calls and not any(c.startswith("consolidate") for c in e.calls)


def test_vor_der_lautheit_wird_erneut_geprueft(tmp_path):
    """Startet jemand während des Exports die Wiedergabe, wird vor der Lautheitskorrektur angehalten."""
    e = _engine(tmp_path)
    e.transport = ["TS_TransportStopped", "TS_TransportStopped", "TS_TransportPlaying", "TS_TransportStopped"]
    r = run_pt_export(PtslClient(engine_factory=lambda: e), ["ST"], str(tmp_path / "out"), loudness_tracks=["ST"])
    assert "ST" in r.loudness
    assert e.calls.index("consolidate ST") < e.calls.index("toggle_play_state")


def test_lautheit_bei_laufendem_transport_abgebrochen(tmp_path):
    e = _engine(tmp_path)
    e.transport = ["TS_TransportStopped", "TS_TransportStopped", "TS_TransportPlaying"]
    with pytest.raises(PtslError, match="Lautheit ST abgebrochen"):
        run_pt_export(PtslClient(engine_factory=lambda: e), ["ST"], str(tmp_path / "out"), loudness_tracks=["ST"])
    assert not os.path.exists(tmp_path / "out")


def test_fehlende_exportspur(tmp_path):
    with pytest.raises(ExportError, match="fehlen"):
        run_pt_export(PtslClient(engine_factory=lambda: _engine(tmp_path)), ["ST", "Gibtsnicht"],
                      str(tmp_path / "out"), loudness_tracks=[])


def test_alte_dateien_zaehlen_nicht(tmp_path):
    audio = tmp_path / "A"
    audio.mkdir()
    (audio / "ST_01.wav").write_bytes(b"x" * 100)
    os.utime(audio / "ST_01.wav", (1000, 1000))
    with pytest.raises(ExportError):
        wait_for_consolidated(str(audio), ["ST"], since=2000, timeout_s=0, **WAIT)


def test_aehnliche_spurnamen_bekommen_ihre_eigene_datei(tmp_path):
    """Review A11: „ST_Mono_01.wav“ ist neuer als „ST_01.wav“ – darf trotzdem nicht als Datei von „ST“ gelten."""
    audio = tmp_path / "A"
    audio.mkdir()
    for name, t in (("ST_01.wav", 3000), ("ST_Mono_01.wav", 3001)):
        (audio / name).write_bytes(b"x" * 100)
        os.utime(audio / name, (t, t))
    assert pt_export.newest_files(str(audio), ["ST", "ST_Mono"], since=2000) == {
        "ST": str(audio / "ST_01.wav"), "ST_Mono": str(audio / "ST_Mono_01.wav")}
    assert pt_export.ambiguous_tracks(["ST", "ST_Mono", "IT"]) == []
    assert pt_export.ambiguous_tracks(["ST", "ST_01"]) == [("ST", "ST_01")]


def test_nicht_trennbare_spurnamen_vor_dem_export_abgelehnt(tmp_path):
    e = _engine(tmp_path)
    with pytest.raises(ExportError, match="„ST“ und „ST.2“ – bitte eine davon umbenennen"):
        run_pt_export(PtslClient(engine_factory=lambda: e), ["ST", "ST.2"], str(tmp_path / "out"), loudness_tracks=[])
    assert e.calls == []                                     # nichts an Pro Tools geschickt


def test_kopie_ueberschreibt_nie(tmp_path):
    src = tmp_path / "ST.wav"
    src.write_bytes(b"1")
    assert copy_atomic(str(src), str(tmp_path / "ziel")).endswith("ziel/ST.wav")
    with pytest.raises(FileExistsError):
        copy_atomic(str(src), str(tmp_path / "ziel"))
    assert sorted(os.listdir(tmp_path / "ziel")) == ["ST.wav"]


# ── Zurückliefern an Helmut ─────────────────────────────────────────────────
@pytest.fixture
def mock(tmp_path):
    m = MockHelmut()
    m.add_project("p1", "Bahnstreik", "Heimatjournal", state="in_mischung", mischer="Jens", raum="Ton 3",
                  session_pfad=str(tmp_path / "Bahnstreik" / "Bahnstreik.ptx"))
    m.url = m.start()
    yield m
    m.stop()


def _deliver(mock, e, tmp_path, mixer="Jens", **kw):
    svc = MixService(HelmutClient(mock.url, lambda: ("test", "test"), sleep=lambda s: None),
                     mixer, "Ton 3", now=lambda: "jetzt")
    return deliver("p1", service=svc, ptsl=PtslClient(engine_factory=lambda: e),
                   export_tracks=EXPORT, loudness_tracks=["ST"],
                   handover_root=str(tmp_path / "helmut" / "{projektId}"), **kw)


def test_zurueckliefern(mock, tmp_path):
    e = _engine(tmp_path)
    r = _deliver(mock, e, tmp_path)
    assert len(r.delivered) == 5 and all(p.endswith(".wav") for p in r.delivered)   # leere Spr dabei
    assert all(os.path.dirname(os.path.dirname(p)) == str(tmp_path / "helmut" / "p1") for p in r.delivered)
    assert len(mock.assets["p1"]) == 5
    assert mock.mix_value("p1", "zustand") == "ausgeliefert"
    assert not any(f.endswith(".part") for _, _, fs in os.walk(tmp_path / "helmut") for f in fs)


def test_ausgeliefert_und_entsperrt(mock, tmp_path):
    """Jens 09.10.2026: Zurückliefern setzt „ausgeliefert“ und gibt den Beitrag frei – vor dem nächsten Holen keine
    Frage mehr nach der offenen Session; Mischer und Raum bleiben stehen, die Tätigkeit des Raumstatus endet."""
    mock.add_project("p1", "Bahnstreik", "Heimatjournal", state="in_mischung", mischer="Jens", raum="Ton 3",
                     session_pfad=str(tmp_path / "Bahnstreik" / "Bahnstreik.ptx"), taetigkeit="export")
    mock.set_status("p1", "LOCKED")
    r = _deliver(mock, _engine(tmp_path), tmp_path)
    assert mock.status("p1") == "UNLOCKED" and not r.contribution.locked
    assert (mock.mix_value("p1", "mischer"), mock.mix_value("p1", "raum")) == ("Jens", "Ton 3")
    assert mock.mix_value("p1", "taetigkeit") in ("", None) and mock.mix_value("p1", "fortschritt") == "100"


def test_erneut_zurueckliefern_nach_ausgeliefert(mock, tmp_path):
    from datetime import datetime
    e = _engine(tmp_path)
    _deliver(mock, e, tmp_path, now=lambda: datetime(2026, 10, 9, 10, 0))
    r = _deliver(mock, e, tmp_path, now=lambda: datetime(2026, 10, 9, 10, 5))   # Korrektur: noch einmal
    assert mock.mix_value("p1", "zustand") == "ausgeliefert" and len(r.delivered) == 5


def test_nach_fremder_uebernahme_nicht_mehr(mock, tmp_path):
    e = _engine(tmp_path)
    _deliver(mock, e, tmp_path)
    MixService(HelmutClient(mock.url, lambda: ("test", "test"), sleep=lambda s: None), "A. Wolf", "Ton 2",
               now=lambda: "jetzt").take("p1")                    # ausgeliefert ist frei – jemand übernimmt
    with pytest.raises(Exception, match="gehört nicht"):
        _deliver(mock, e, tmp_path)


def test_falsche_session_offen_wird_erst_gefragt(mock, tmp_path):
    e = _engine(tmp_path)
    e.session = ("Andere", "/S/Andere/Andere.ptx")
    from tonmischung.workflow.fetch import NeedsConfirmation
    with pytest.raises(NeedsConfirmation):
        _deliver(mock, e, tmp_path)
    _deliver(mock, e, tmp_path, confirmed=True)
    assert f"open_session({tmp_path / 'Bahnstreik' / 'Bahnstreik.ptx'})" in e.calls


def test_oeffnen_wird_geprueft_bevor_exportiert_wird(mock, tmp_path):
    """Review B20: Öffnen ohne Prüfung – blieb die andere Session offen, ging der Export an sie."""
    e = _engine(tmp_path)
    e.session = ("Andere", "/S/Andere/Andere.ptx")
    e.open_session = lambda path: e.calls.append(f"open_session({path})")      # Pro Tools öffnet nicht
    with pytest.raises(FetchError, match="nicht geöffnet"):
        _deliver(mock, e, tmp_path, confirmed=True)
    assert not any(c.startswith("consolidate") or "Export" in c for c in e.calls)


def test_nur_der_besitzer_liefert(mock, tmp_path):
    with pytest.raises(Exception, match="gehört nicht"):
        _deliver(mock, _engine(tmp_path), tmp_path, mixer="A. Wolf")
    assert mock.assets["p1"] == []


def test_scheitert_pro_tools_teil_geht_nichts_an_helmut(mock, tmp_path):
    e = _engine(tmp_path)
    e.consolidate_writes = False
    with pytest.raises(FetchError, match="nichts an Helmut"):
        _deliver(mock, e, tmp_path)
    assert mock.assets["p1"] == [] and mock.mix_value("p1", "zustand") == "in_mischung"


def test_videoende_vor_programmbeginn_wird_nicht_gekuerzt(tmp_path):
    e = _engine(tmp_path)
    e.video_start, e.video_end = "00:00:00:00.00", "00:00:10:00.00"   # Testvideo am Session-Anfang
    with pytest.raises(ExportError, match="nicht hinter dem Programmbeginn"):
        run_pt_export(PtslClient(engine_factory=lambda: e), ["ST"], str(tmp_path / "out"), loudness_tracks=[])
    assert not any(c.startswith(("clear", "consolidate")) for c in e.calls)


def test_video_mit_vorlauf_gibt_hinweis(tmp_path):
    e = _engine(tmp_path)
    e.video_start = "09:59:30:00.00"
    r = run_pt_export(PtslClient(engine_factory=lambda: e), ["ST"], str(tmp_path / "out"), loudness_tracks=[])
    assert any("09:59:30:00" in w for w in r.warnings)


def test_kopie_meldet_bytes(tmp_path):
    src = tmp_path / "ST.wav"
    src.write_bytes(b"x" * 1000)
    gemeldet = []
    copy_atomic(str(src), str(tmp_path / "z"), on_bytes=gemeldet.append, chunk=300)
    assert gemeldet == [300, 300, 300, 100]


def test_zurueckliefern_meldet_alle_schritte(mock, tmp_path):
    from tonmischung.core.progress import Progress
    from tonmischung.workflow.deliver import DELIVER_STEPS
    from test_progress import Aufzeichnung
    a = Aufzeichnung()
    p = Progress(DELIVER_STEPS, [a], tick_s=None)
    _deliver(mock, _engine(tmp_path), tmp_path, progress=p)
    p.close()
    schritte = []
    for _, text in a.werte:
        name = text.split(":")[0]
        if not schritte or schritte[-1] != name:
            schritte.append(name)
    assert schritte == ["Session vorbereiten", "Kürzen und einblenden", "Konsolidieren", "Lautheit",
                        "WAVs kopieren", "An Helmut übergeben", "Fertig"]
    werte = [w for w, _ in a.werte]
    assert werte == sorted(werte) and werte[-1] == 1.0          # nur vorwärts
    assert any("ST: " in t for _, t in a.werte)                   # Zwischenschritte der Lautheit


def test_export_ordner_wie_punchbuddy_mit_lautheitsbericht(tmp_path):
    """Jens 04.10.2026: WAVs im Ordner „export“ des Pro-Tools-Projekts, dazu ein Bericht zur Lautheitskorrektur – auf
    beiden Wegen. Ein zweiter Export überschreibt nichts: die vorige Fassung kommt nach export/Vorversionen/."""
    e = _engine(tmp_path)
    r = run_pt_export(PtslClient(engine_factory=lambda: e), EXPORT, loudness_tracks=["ST"])
    out = tmp_path / "Bahnstreik" / "export"
    assert os.path.dirname(r.wav_files["ST"]) == str(out) and r.report == str(out / pt_export.REPORT_NAME)
    txt = open(r.report, encoding="utf-8").read()
    assert "Korrigiert:         ST" in txt and "Ohne Korrektur:     IT, OGA, Mus, Spr" in txt
    assert "SPUR ST" in txt and "Gain-Korrektur" in txt and "Endgueltige Lautheit: -23.0 LUFS" in txt
    first = open(r.wav_files["IT"], "rb").read()
    r2 = run_pt_export(PtslClient(engine_factory=lambda: e), EXPORT, loudness_tracks=["ST"])
    prev = out / pt_export.PREVIOUS
    (stamp,) = os.listdir(prev)
    assert sorted(os.listdir(prev / stamp)) == sorted([f"{t}.wav" for t in EXPORT] + [pt_export.REPORT_NAME])
    assert (prev / stamp / "IT.wav").read_bytes() == first and os.path.exists(r2.wav_files["IT"])
    assert any("Vorige Fassung" in w for w in r2.warnings)


def test_vorversionen_und_bericht_rein():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "ST.wav"), "w").write("alt")
        assert pt_export.set_aside_previous(d, ["ST.wav", "IT.wav", "ST.wav"], "20261004-1700") == ["ST.wav"]
        open(os.path.join(d, "ST.wav"), "w").write("neu")
        assert pt_export.set_aside_previous(d, ["ST.wav"], "20261004-1700") == ["ST.wav"]      # gleiche Sekunde
        assert sorted(os.listdir(os.path.join(d, "Vorversionen", "20261004-1700"))) == ["ST.wav", "ST_2.wav"]
    txt = pt_export.loudness_report("/S/B.ptx", -23, -3, ["ST", "IT"], {"ST": "  Gain-Korrektur:     -5.4 dB\n"}, "jetzt")
    assert "Ziel:               -23.0 LUFS, höchstens -3.0 dBTP" in txt and "Ohne Korrektur:     IT" in txt


def test_programmbeginn_aus_der_einstellung(tmp_path, monkeypatch):
    """Review B18: der Export nahm fest 10:00:00:00, auch wenn die Einstellung anders lautet."""
    seen = []

    def capture(ptsl, export_mod, in_time="", log=None):
        seen.append(in_time)
        raise ExportError("genug gesehen")
    monkeypatch.setattr(pt_export, "program_range", capture)
    with pytest.raises(ExportError, match="genug gesehen"):
        run_pt_export(PtslClient(engine_factory=lambda: _engine(tmp_path)), ["ST"], str(tmp_path / "out"),
                      loudness_tracks=[], program_start="09:59:30:00")
    assert seen == ["09:59:30:00.00"] and pt_export.with_subframes("10:00:00:00.12") == "10:00:00:00.12"
