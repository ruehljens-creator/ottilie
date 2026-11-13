"""Nachtrag-Warteschlange (Plan M2-Nachtrag): Ausfall simuliert, später nachgetragen, Konflikt gemeldet."""
import json

import pytest

from fakes import FakeEngine
from test_fetch import AAF_OK, CLIPS
from tonmischung.core.catchup import (APPLY, CONFLICT, DONE, HINT, OPEN, TAKE, CatchUpQueue, Entry, MIX, decide_mix,
                                      mix_changes)
from tonmischung.core.mixstate import MixState
from tonmischung.helmut.catchup import replay
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.workflow import fetch as fetch_mod
from tonmischung.workflow.fetch import fetch


@pytest.fixture
def mock():
    m = MockHelmut()
    m.add_project("p1", "Bahnstreik: Pendler warten", "Heimatjournal",
                  assets=["/H/p1/Bahnstreik.aaf", "/H/p1/Bahnstreik_ref.mxf"])
    m.url = m.start()
    yield m
    m.stop()


def _client(m):
    return HelmutClient(m.url, lambda: ("test", "test"), sleep=lambda s: None)


class _DownAt:
    """Fortschritt, der Helmut bei einem bestimmten Schritt ausfallen lässt."""
    def __init__(self, mock, step):
        self.mock, self.at = mock, step

    def step(self, name, text=""):
        if name == self.at:
            self.mock.unavailable = True

    def update(self, fraction, text=""):
        pass


def test_entscheidung():
    mine = MixState("importiert", "Jens", "Ton 3")
    e = Entry(MIX, "p1", expect_mixer="Jens", expect_room="Ton 3", changes={"state": "exportiert", "progress": 100})
    assert decide_mix(e, mine)[0] == APPLY
    assert decide_mix(e, MixState("exportiert", "Jens", "Ton 3", progress=100))[0] == DONE
    verdict, msg = decide_mix(e, MixState("uebernommen", "A. Wolf", "Ton 2"))
    assert verdict == CONFLICT and "A. Wolf (Ton 2)" in msg and "nicht überschrieben" in msg
    assert mix_changes(mine, MixState("exportiert", "Jens", "Ton 3", progress=100)) == {"state": "exportiert", "progress": 100}


def test_datei_bleibt_erhalten(tmp_path):
    q = CatchUpQueue(tmp_path / "n.json", now=lambda: "t")
    q.add_lock("p1", "Bahnstreik", False)
    q.add_asset("p1", "Bahnstreik", "/x/ST.wav", "tonspur")
    again = CatchUpQueue(tmp_path / "n.json")
    assert [(e.kind, e.status) for e in again.entries()] == [("sperre", OPEN), ("asset", OPEN)]
    assert json.loads((tmp_path / "n.json").read_text())["version"] == 1
    (tmp_path / "kaputt.json").write_text("{nicht json")
    q2 = CatchUpQueue(tmp_path / "kaputt.json")                      # App startet trotzdem (Review B11)
    assert q2.entries() == [] and "beiseitegelegt" in q2.problem
    kept = list(tmp_path.glob("kaputt.defekt-*.json"))
    assert len(kept) == 1 and kept[0].read_text() == "{nicht json"     # nichts verloren
    q2.add_lock("p2", "Kita", False)
    assert kept[0].read_text() == "{nicht json"


def test_ausfall_beim_holen_wird_nachgetragen(mock, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mod, "check_aaf", lambda path: AAF_OK)
    q = CatchUpQueue(tmp_path / "n.json")
    service = MixService(_client(mock), "Jens", "Ton 3", now=lambda: "jetzt", queue=q)
    engine = FakeEngine(session=None, aaf_tracks=("A1", "A2"), aaf_clips=CLIPS, video_track="Video")
    r = fetch("p1", service=service, ptsl=PtslClient(engine_factory=lambda: engine), template_group="test",
              template_name="SM Session", session_parent=str(tmp_path), progress=_DownAt(mock, "Helmut aktualisieren"))
    assert HINT in r.warnings and r.contribution.mix.state == "importiert"
    assert mock.mix_value("p1", "zustand") == "uebernommen"                      # Helmut noch alt
    assert [e.kind for e in q.pending()] == ["mischung"]
    # noch immer weg: bleibt offen
    rep = replay(q, _client(mock))
    assert rep.unavailable and q.pending()[0].attempts == 0                    # Ausfall zählt nicht als Versuch
    mock.unavailable = False
    rep = replay(q, _client(mock))
    assert rep.text == "1 Änderungen nachgetragen" and not q.pending()
    assert mock.mix_value("p1", "zustand") == "importiert" and mock.mix_value("p1", "session_pfad") == r.session_path


def test_konflikt_wird_gemeldet_nicht_ueberschrieben(mock, tmp_path):
    q = CatchUpQueue(tmp_path / "n.json")
    service = MixService(_client(mock), "Jens", "Ton 3", now=lambda: "jetzt", queue=q)
    c = service.take("p1")
    mock.unavailable = True
    c2, hint = service.set_state_or_queue(c, "exportiert", progress=100)
    assert hint == HINT and c2.mix.state == "exportiert"
    assert service.add_asset_or_queue(c, "/x/ST.wav", "tonspur") == HINT
    mock.unavailable = False
    # inzwischen in Helmut anders zugewiesen
    for e in mock.projects["p1"]["metadata"]:
        if e["name"] == "mischer":
            e["value"] = "A. Wolf"
        if e["name"] == "raum":
            e["value"] = "Ton 2"
    rep = replay(q, _client(mock))
    assert len(rep.conflicts) == 1 and "A. Wolf (Ton 2)" in rep.conflicts[0]
    assert mock.mix_value("p1", "zustand") == "uebernommen" and mock.mix_value("p1", "mischer") == "A. Wolf"
    assert [e.status for e in q.entries()] == [CONFLICT, OPEN]                    # Asset wartet dahinter
    assert not mock.assets["p1"][2:]                                             # nichts registriert
    s = q.summary()
    assert s["offen"] == 1 and s["konflikte"][0]["text"] == "Bahnstreik: Pendler warten: Zustand „zurückgeliefert“"
    q.dismiss(s["konflikte"][0]["id"])
    rep = replay(q, _client(mock))
    # Ausfall R2/N2 (11.11.2026): das WAV gehört zu Jens' Lieferung – an A. Wolfs Beitrag wird es nicht angemeldet
    assert not rep.written and "inzwischen bei A. Wolf (Ton 2)" in rep.conflicts[0]
    assert len(mock.assets["p1"]) == 2


def test_ausgeliefert_bei_ausfall_mit_entsperren_nachgetragen(mock, tmp_path):
    """Jens 09.10.2026: Zurückliefern setzt „ausgeliefert“ und entsperrt – bei Ausfall beides eingereiht."""
    q = CatchUpQueue(tmp_path / "n.json")
    service = MixService(_client(mock), "Jens", "Ton 3", now=lambda: "jetzt", queue=q)
    c = service.set_state(service.take("p1").id, "importiert")
    assert mock.status("p1") == "LOCKED"
    mock.unavailable = True
    c2, hint = service.deliver_or_queue(c)
    assert hint == HINT and c2.mix.state == "ausgeliefert" and not c2.locked
    assert [(e.kind, e.locked) for e in q.pending()] == [("mischung", False), ("sperre", False)]
    mock.unavailable = False
    rep = replay(q, _client(mock))
    assert len(rep.written) == 2 and not rep.conflicts
    assert mock.mix_value("p1", "zustand") == "ausgeliefert" and mock.status("p1") == "UNLOCKED"


def test_erneut_ausgeliefert_kein_konflikt():
    e = Entry(MIX, "p1", expect_mixer="Jens", expect_room="Ton 3", changes={"state": "ausgeliefert", "progress": 100})
    assert decide_mix(e, MixState("ausgeliefert", "Jens", "Ton 3", progress=40))[0] == APPLY
    assert decide_mix(e, MixState("ausgeliefert", "A. Wolf", "Ton 2", progress=40))[0] == CONFLICT
    other = Entry(MIX, "p1", expect_mixer="Jens", expect_room="Ton 3", changes={"state": "importiert"})
    assert decide_mix(other, MixState("ausgeliefert", "Jens", "Ton 3"))[0] == CONFLICT   # nur Ausliefern


def test_asset_doppelt_wird_nicht_angelegt(mock, tmp_path):
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_asset("p1", "Bahnstreik", "/H/p1/Bahnstreik.aaf", "uebergabe")
    rep = replay(q, _client(mock))
    assert rep.written and len(mock.assets["p1"]) == 2 and q.entries()[0].message == "Asset war schon da"


def test_ohne_warteschlange_bleibt_es_ein_fehler(mock):
    from tonmischung.helmut.client import HelmutUnavailableError
    service = MixService(_client(mock), "Jens", "Ton 3")
    c = service.take("p1")
    mock.unavailable = True
    with pytest.raises(HelmutUnavailableError):
        service.set_state_or_queue(c, "exportiert")


def test_app_und_kommandozeile_verlieren_nichts(tmp_path):
    """Review A8: App und Kommandozeile halten je eine Warteschlange auf dieselbe Datei."""
    app, cli = CatchUpQueue(tmp_path / "n.json"), CatchUpQueue(tmp_path / "n.json")
    a = app.add_lock("p1", "Bahnstreik", False)
    cli.add_asset("p1", "Bahnstreik", "/x/ST.wav", "tonspur")
    app.mark(a, DONE, "nachgetragen")                        # darf den Eintrag der Kommandozeile nicht verlieren
    assert [(e.kind, e.status) for e in cli.entries()] == [("sperre", DONE), ("asset", OPEN)]
    assert [e.kind for e in CatchUpQueue(tmp_path / "n.json").entries()] == ["sperre", "asset"]


def test_gleichzeitige_eintraege_aus_threads(tmp_path):
    import threading
    q = CatchUpQueue(tmp_path / "n.json")
    other = CatchUpQueue(tmp_path / "n.json")

    def fill(queue, n):
        for i in range(n):
            queue.add_lock(f"p{i}", "x", False)
    threads = [threading.Thread(target=fill, args=(q if i % 2 else other, 15)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(CatchUpQueue(tmp_path / "n.json").entries()) == 90


def test_nachtragen_nur_einmal_zur_zeit(mock, tmp_path):
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_lock("p1", "Bahnstreik", True)
    with CatchUpQueue(tmp_path / "n.json").replaying() as mine:
        assert mine
        rep = replay(q, _client(mock))
        assert rep.busy and rep.text == "Nachtragen läuft schon" and q.pending()
    assert replay(q, _client(mock)).written and not q.pending()


def test_nachtrag_schreibt_unter_der_schreibsperre(mock, tmp_path):
    """Review A7: der Raumstatus (gleiche Sperre) darf einen nachgetragenen Zustand nicht zurückdrehen."""
    import threading
    service = MixService(_client(mock), "Jens", "Ton 3", now=lambda: "jetzt")
    c = service.take("p1")
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_mix("p1", c.title, c.mix, MixState(**{**c.mix.__dict__, "state": "exportiert"}), "Jens", "Ton 3")
    done = threading.Event()
    with MixService._write_lock:
        t = threading.Thread(target=lambda: (replay(q, _client(mock)), done.set()))
        t.start()
        assert not done.wait(0.5)                            # wartet auf die Sperre
        assert mock.mix_value("p1", "zustand") == "uebernommen"
    assert done.wait(5) and mock.mix_value("p1", "zustand") == "exportiert"


def test_bleibender_serverfehler_haelt_den_nachtrag_nicht_auf(mock, tmp_path):
    """Review B6: ein 500er bei einem Eintrag hält andere Beiträge nicht auf. Ausfall R3 (10.11.2026): die
    folgenden Einträge desselben Beitrags warten – sonst stünde „ausgeliefert“ in Helmut, obwohl ein WAV fehlt."""
    mock.add_project("p2", "Kita-Streik", "Heimatjournal")
    q = CatchUpQueue(tmp_path / "n.json")
    first = q.add_lock("p1", "Bahnstreik", True)
    q.add_asset("p1", "Bahnstreik", "/x/ST.wav", "tonspur")
    q.add_asset("p2", "Kita-Streik", "/x/IT.wav", "tonspur")
    client = _client(mock)
    client.login()
    mock.fail_next = [500, 500, 500]
    rep = replay(q, client)
    assert not rep.unavailable and rep.written == ["Kita-Streik: WAV IT.wav"]
    e = next(x for x in q.entries() if x.id == first.id)
    assert e.status == OPEN and e.attempts == 1 and e.message.startswith("Versuch 1")
    assert [x.path for x in q.pending()] == ["", "/x/ST.wav"]                  # p1 wartet geschlossen


def test_nachgetragenes_entsperren_hebt_keine_neue_uebernahme_auf(mock, tmp_path):
    """Review B8: Freigabe halb geglückt (Set geschrieben, Entsperren offen) – inzwischen hat A. Wolf übernommen."""
    q = CatchUpQueue(tmp_path / "n.json")
    MixService(_client(mock), "A. Wolf", "Ton 2", now=lambda: "jetzt").take("p1")
    q.add_lock("p1", "Bahnstreik", False)
    rep = replay(q, _client(mock))
    assert rep.written and q.entries()[0].message == "inzwischen neu übernommen – Sperre bleibt"
    assert mock.status("p1") == "LOCKED"


def test_benachrichtigung_bei_ausfall_wird_nachgetragen(mock, tmp_path):
    """Freigeben bei Helmut-Ausfall: die Nachricht an den bisherigen Mischer geht nach, statt verloren zu gehen."""
    import types
    from tonmischung.app.commands import notify_release
    from tonmischung.core.mixstate import MixState
    from tonmischung.helmut.mapping import Contribution
    q = CatchUpQueue(tmp_path / "n.json")
    ctx = types.SimpleNamespace(helmut=_client(mock), queue=q, mixer="Jens",
                                settings=types.SimpleNamespace(get=lambda k: [{"name": "A. Wolf", "helmut_benutzer": "awolf"}]))
    before = Contribution("p1", "Bahnstreik", "Heimatjournal", True, MixState("importiert", "A. Wolf", "Ton 2"), {})
    mock.unavailable = True
    hints = notify_release(ctx, before, "Raumwechsel", "neu")
    assert hints == ["Benachrichtigung an awolf wird nachgetragen, sobald Helmut wieder da ist"]
    assert [e.kind for e in q.pending()] == ["nachricht"]
    mock.unavailable = False
    assert replay(q, _client(mock)).written and mock.notifications[-1][0] == "awolf"


def test_offene_aenderungen_verwerfen(tmp_path):
    """Jens 09.10.2026: offene Nachträge einzeln oder alle verwerfen – Helmut bleibt, wie es ist."""
    import types
    from tonmischung.app.bridge import CommandError
    from tonmischung.app.catchup_app import dismiss_conflict
    q = CatchUpQueue(tmp_path / "n.json")
    a = q.add_lock("p1", "Bahnstreik", False)
    q.add_asset("p2", "Kita", "/x/ST.wav", "tonspur")
    q.add(Entry(MIX, "", "Havarie Wahl", havarie="hv1", changes={"state": "exportiert"}))
    ctx = types.SimpleNamespace(queue=q)
    s = q.summary()
    assert [e["id"] for e in s["eintraege"]] == [a.id, q.pending()[1].id] and s["ohne_beitrag"][0]["id"] == "hv1"
    r = dismiss_conflict(ctx, {"id": a.id})
    assert r["offen"] == 1 and q.entries()[0].status == DONE and q.entries()[0].message.startswith("verworfen")
    assert dismiss_conflict(ctx, {"havarie": "hv1"})["ohne_beitrag"] == []
    assert dismiss_conflict(ctx, {"alle": True})["text"].startswith("1 Änderung verworfen") and not q.pending()
    with q.replaying():                                                   # Nachtragen läuft gerade
        with pytest.raises(CommandError, match="läuft gerade"):
            dismiss_conflict(ctx, {"alle": True})


def _token_client(m, token):
    from tonmischung.helmut.client import LOGIN_TOKEN
    return HelmutClient(m.url, lambda: ("", token), sleep=lambda s: None, login_method=LOGIN_TOKEN)


def test_abgelehnte_anmeldung_wird_kein_konflikt(mock, tmp_path):
    """Review 10.11.2026: ein abgelehntes Token machte nach fünf Takten alle Nachträge zu Konflikten."""
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_lock("p1", "Bahnstreik", False)
    for _ in range(7):
        report = replay(q, _token_client(mock, "abgelehnt"))
    assert report.rejected and "wartet" in report.text and not report.conflicts
    assert [e.status for e in q.pending()] == [OPEN] and q.conflicts() == []
    mock.tokens.add("neu")
    assert replay(q, _token_client(mock, "neu")).written


def test_abgelehnter_einzelner_aufruf_zaehlt_weiter(mock, tmp_path):
    """Lehnt Helmut nur diesen Aufruf ab (Benachrichtigung ohne Admin-Recht), die Anmeldung aber nicht, bleibt es bei
    der Zählung bis zum Konflikt – sonst stünde die Warteschlange für immer."""
    mock.tokens.add("gut")
    mock.notify_denied = 401
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_notify("p1", "Bahnstreik", "awolf", "Freigegeben", "Bahnstreik")
    q.add_lock("p1", "Bahnstreik", False)
    for _ in range(5):
        report = replay(q, _token_client(mock, "gut"))
    assert report.conflicts and not report.rejected and "Anmeldung gilt" in report.conflicts[0]
    q.add_lock("p1", "Bahnstreik", True)                 # die Nachricht sperrt den Beitrag nicht für Zustände
    assert replay(q, _token_client(mock, "gut")).written


def test_schluesselbund_gesperrt_wird_kein_konflikt(mock, tmp_path):
    """Review 10.11.2026: ein gesperrter Schlüsselbund machte nach fünf Takten alle Nachträge zu Konflikten."""
    from tonmischung.helmut.client import HelmutAuthError

    def locked():
        raise HelmutAuthError("Schlüsselbund antwortet nicht")
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_lock("p1", "Bahnstreik", False)
    for _ in range(7):
        report = replay(q, HelmutClient(mock.url, locked, sleep=lambda s: None))
    assert report.rejected and not report.conflicts and q.conflicts() == []


def test_abgelehnte_anmeldung_wird_eingereiht(mock, tmp_path):
    """Review 10.11.2026: Token beim Holen abgelehnt – die Änderung kommt in die Warteschlange, nicht verloren."""
    from tonmischung.core.catchup import HINT_AUTH
    mock.tokens.add("gut")
    q = CatchUpQueue(tmp_path / "n.json")
    service = MixService(_token_client(mock, "gut"), "Jens", "Ton 3", queue=q)
    known = service.take("p1")
    mock.tokens.clear()
    _, hint = service.set_state_or_queue(known, "importiert", session_path="/S/Bahnstreik.ptx")
    assert hint == HINT_AUTH and [e.kind for e in q.pending()] == [MIX]
    mock.tokens.add("gut")
    assert replay(q, _token_client(mock, "gut")).written
    assert mock.projects["p1"]["metadata"] and _client(mock).get("p1").mix.session_path == "/S/Bahnstreik.ptx"


def test_entsperren_nach_gescheitertem_uebernehmen_wird_eingereiht(mock, tmp_path):
    """Review 10.11.2026: scheiterte das Übernehmen nach dem Sperren und das Entsperren auch, blieb die Sperre."""
    q = CatchUpQueue(tmp_path / "n.json")
    service = MixService(_client(mock), "Jens", "Ton 3", queue=q)
    original = service.client.write_mix

    def down(*a, **kw):
        mock.unavailable = True
        return original(*a, **kw)
    service.client.write_mix = down
    with pytest.raises(Exception):
        service.take("p1")
    assert [(e.kind, e.locked) for e in q.pending()] == [("sperre", False)]
    mock.unavailable = False
    assert replay(q, _client(mock)).written and not _client(mock).get("p1").locked


def test_uebernehmen_im_nachtrag_mit_rennen_wird_konflikt(mock, tmp_path, monkeypatch):
    """Ausfall R3, F3: kommt beim Nachtragen einer Übernahme jemand dazwischen, wird es ein Konflikt, kein Absturz."""
    from tonmischung.core.mixstate import ConflictError
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_take("p1", "Bahnstreik", "Jens", "Ton 3")

    def raced(self, project_id):
        raise ConflictError("Beitrag ist bereits übernommen von Anna (Ton 5)")
    monkeypatch.setattr(MixService, "take", raced)
    rep = replay(q, _client(mock))
    assert rep.conflicts and q.conflicts()[0].kind == "uebernahme"


def test_zustand_hinter_abgelehntem_alten_eintrag_eingereiht(mock, tmp_path):
    """Ausfall R4, S1: lehnt Helmut einen älteren Eintrag ab, geht ein neuer Zustand nicht verloren, sondern wartet
    dahinter (vorher: Fehler nach dem Öffnen der Session, Änderung weg)."""
    from tonmischung.helmut.service import HINT_WAITING
    q = CatchUpQueue(tmp_path / "n.json")
    service = MixService(_client(mock), "Jens", "Ton 3", now=lambda: "jetzt", queue=q)
    c = service.take("p1")
    q.add_asset("p1", "Bahnstreik", "/alt/ST.wav", "tonspur")
    mock.fail_next = [400]
    c2, hint = service.set_state_or_queue(c, "in_mischung", session_path="/neu.ptx")
    assert hint == HINT_WAITING and c2.mix.session_path == "/neu.ptx"
    assert [e.kind for e in q.pending()] == ["asset", "mischung"]                 # Reihenfolge bleibt
    assert mock.mix_value("p1", "zustand") == "uebernommen"


def test_langer_ausfall_macht_die_erste_ablehnung_nicht_zum_konflikt(mock, tmp_path):
    """Ausfall R4, S3: Ausfälle zählen nicht als Versuch."""
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_asset("p1", "Bahnstreik", "/x/ST.wav", "tonspur")
    mock.unavailable = True
    for _ in range(6):
        replay(q, _client(mock))
    mock.unavailable = False
    mock.fail_next = [400]
    replay(q, _client(mock))
    assert not q.conflicts() and q.pending()[0].attempts == 1


def test_abgelehnte_anmeldung_im_settle_sagt_anmeldung(mock, tmp_path):
    """Ausfall R4, S4."""
    from tonmischung.helmut.client import HelmutAuthError
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_asset("p1", "Bahnstreik", "/x/ST.wav", "tonspur")
    service = MixService(_client(mock), "Jens", "Ton 3", now=lambda: "jetzt", queue=q)

    def denied(*a, **k):
        raise HelmutAuthError("Helmut lehnt die Anmeldung ab (401)")
    service.client.assets = denied
    with pytest.raises(HelmutAuthError):
        service.settle("p1")


def test_dauer_503_bei_einem_eintrag_haelt_andere_beitraege_nicht_auf(mock, tmp_path):
    """Ausfall R5, S5a: Helmut antwortet, nur ein Eintrag bekommt immer 503 – sein Beitrag wartet, andere laufen
    weiter, nach MAX_ATTEMPTS Takten wird er ein sichtbarer Konflikt (vorher hing der ganze Nachtrag für immer)."""
    from tonmischung.helmut.catchup import MAX_ATTEMPTS
    from tonmischung.helmut.client import HelmutUnavailableError
    mock.add_project("p2", "Kita-Streik", "Heimatjournal")
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_asset("p1", "Bahnstreik", "/x/kaputt.wav", "tonspur")
    q.add_asset("p1", "Bahnstreik", "/x/danach.wav", "tonspur")
    q.add_asset("p2", "Kita-Streik", "/x/IT.wav", "tonspur")
    client = _client(mock)
    real = client.add_asset

    def broken(pid, path, kind):
        if path == "/x/kaputt.wav":
            raise HelmutUnavailableError("Helmut ist nicht erreichbar (POST /v1/co/assets)", status=503)
        return real(pid, path, kind)
    client.add_asset = broken
    rep = replay(q, client)
    assert not rep.unavailable and rep.written == ["Kita-Streik: WAV IT.wav"]          # p2 kommt durch
    assert [x.path for x in q.pending()] == ["/x/kaputt.wav", "/x/danach.wav"]            # p1 wartet geschlossen
    assert "Serverfehler bei diesem Eintrag" in q.pending()[0].message
    for _ in range(MAX_ATTEMPTS - 1):
        replay(q, client)
    assert [x.path for x in q.conflicts()] == ["/x/kaputt.wav"]                          # sichtbar unter „Nachtrag“


def test_echter_ausfall_zaehlt_weiter_nicht(mock, tmp_path):
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_asset("p1", "Bahnstreik", "/x/ST.wav", "tonspur")
    mock.unavailable = True
    for _ in range(7):                                                            # mehr als MAX_ATTEMPTS
        assert replay(q, _client(mock)).unavailable
    assert q.pending()[0].attempts == 0 and not q.conflicts()


def test_benachrichtigung_mit_spaeter_antwort_wird_nicht_wiederholt(mock, tmp_path):
    """Ausfall R5, S5b: Nachricht ging hinaus, Antwort kam zu spät – nicht jede Minute neu verschicken."""
    from tonmischung.helmut.client import HelmutUnavailableError
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_notify("p1", "Bahnstreik", "anna", "Beitrag freigegeben", "Bahnstreik ist wieder frei")
    client = _client(mock)
    sent = []

    def late(user, subject, text):
        sent.append(user)
        raise HelmutUnavailableError("Helmut hat nicht geantwortet (POST …)", sent_unknown=True)
    client.notify = late
    for _ in range(3):
        replay(q, client)
    assert sent == ["anna"] and not q.pending() and not q.conflicts()


def test_benachrichtigung_nicht_gesendet_wird_wiederholt(mock, tmp_path):
    """Gegenprobe: Ging die Nachricht gar nicht hinaus (Helmut weg), wird sie später gesendet."""
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_notify("p1", "Bahnstreik", "anna", "Beitrag freigegeben", "Bahnstreik ist wieder frei")
    mock.unavailable = True
    assert replay(q, _client(mock)).unavailable and q.pending()
    mock.unavailable = False
    assert replay(q, _client(mock)).written and not q.pending()


def test_wartung_503_benachrichtigung_geht_nicht_verloren(mock, tmp_path):
    """Ausfall R6 (1): Wartungsseite antwortet 503 bei gültigem Token – die Nachricht wurde nicht ausgeführt, sie
    wartet und geht später hinaus (vorher: „vermutlich zugestellt“ erledigt, Nachricht verloren)."""
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_notify("p1", "Bahnstreik", "anna", "Beitrag freigegeben", "Bahnstreik ist wieder frei")
    client = _client(mock)
    client.login()                                                   # Token liegt schon vor
    mock.unavailable = True
    rep = replay(q, client)
    assert rep.unavailable and [e.kind for e in q.pending()] == ["nachricht"]
    mock.unavailable = False
    assert replay(q, client).written and not q.pending()


def test_zeitlimit_bei_einem_eintrag_bleibt_ausfall(mock, tmp_path):
    """Ausfall R6 (2): Zeitlimit ohne Statuscode zählt nicht als Ablehnung, auch wenn die Anmeldung antwortet."""
    from tonmischung.helmut.client import HelmutUnavailableError
    q = CatchUpQueue(tmp_path / "n.json")
    q.add_asset("p1", "Bahnstreik", "/x/ST.wav", "tonspur")
    client = _client(mock)

    def slow(pid):
        raise HelmutUnavailableError("Helmut ist nicht erreichbar (GET /v1/co/assets/p1)")
    client.assets = slow
    for _ in range(7):
        assert replay(q, client).unavailable
    assert q.pending()[0].attempts == 0 and not q.conflicts()


def test_post_503_ist_nicht_offen_zeitlimit_schon(mock):
    """502/503 kommen von Proxy/Wartung (nicht ausgeführt); offen ist es nur ohne Antwort."""
    from tonmischung.helmut.client import HelmutUnavailableError
    client = _client(mock)
    client.login()
    mock.unavailable = True
    with pytest.raises(HelmutUnavailableError) as err:
        client.notify("anna", "t", "x")
    assert err.value.status == 503 and not err.value.sent_unknown


def test_benachrichtigung_ohne_antwort_wird_nicht_doppelt_eingereiht(mock, tmp_path):
    """Ausfall R6: Nachricht ging hinaus, Antwort blieb aus – nicht einreihen (sonst käme sie doppelt)."""
    import types
    from tonmischung.app.commands import notify_release
    from tonmischung.core.mixstate import MixState
    from tonmischung.helmut.client import HelmutUnavailableError
    from tonmischung.helmut.mapping import Contribution
    q = CatchUpQueue(tmp_path / "n.json")
    client = _client(mock)

    def late(user, subject, text):
        raise HelmutUnavailableError("Helmut hat nicht geantwortet", sent_unknown=True)
    client.notify = late
    ctx = types.SimpleNamespace(helmut=client, queue=q, mixer="Jens",
                                settings=types.SimpleNamespace(get=lambda k: [{"name": "A. Wolf",
                                                                               "helmut_benutzer": "awolf"}]))
    before = Contribution("p1", "Bahnstreik", "Heimatjournal", True, MixState("importiert", "A. Wolf", "Ton 2"), {})
    hints = notify_release(ctx, before, "Raumwechsel", "neu")
    assert len(hints) == 1 and "awolf ist wahrscheinlich angekommen" in hints[0] and not q.pending()


def test_eingereihtes_asset_nach_eigener_auslieferung_wird_angemeldet(mock, tmp_path):
    """Gegenprobe zur Besitzprüfung: wer ausgeliefert hat, dessen WAV kommt nach."""
    q = CatchUpQueue(tmp_path / "n.json")
    service = MixService(_client(mock), "Jens", "Ton 3", now=lambda: "jetzt", queue=q)
    c = service.take("p1")
    q.add_asset("p1", "Bahnstreik", "/x/ST.wav", "tonspur", "Jens", "Ton 3")
    service.deliver_or_queue(c)
    assert replay(q, _client(mock)).written == ["Bahnstreik: WAV ST.wav"]


def test_havarie_uebernahme_wiederholt_nach_verlorener_antwort(mock, tmp_path):
    """Review 11.11.2026: kam die Havarie-Übernahme schon in Helmut an (Antwort verloren), ist „gehört mir“ beim
    nächsten Lauf die eigene Übernahme – kein Konflikt, die Havarie-Stände folgen. Den Fall „derselbe Mischer hat
    inzwischen normal geholt“ verhindert das Zuordnen, das nur mit erreichbarem Helmut geht."""
    q = CatchUpQueue(tmp_path / "n.json")
    MixService(_client(mock), "Jens", "Ton 3", now=lambda: "jetzt").take("p1")    # die angekommene Übernahme
    q.add(Entry(TAKE, "p1", "Bahnstreik", expect_mixer="Jens", expect_room="Ton 3", havarie="hv1"))
    q.add(Entry(MIX, "p1", "Bahnstreik", expect_mixer="Jens", expect_room="Ton 3", havarie="hv1",
                changes={"state": "importiert", "session_path": "/S/havarie.ptx"}))
    rep = replay(q, _client(mock))
    assert not rep.conflicts and mock.mix_value("p1", "session_pfad") == "/S/havarie.ptx"


def test_havarie_zuordnen_ohne_helmut_abgelehnt(mock, tmp_path):
    import types
    from tonmischung.app.bridge import CommandError
    from tonmischung.app.catchup_app import assign_havarie
    q = CatchUpQueue(tmp_path / "n.json")
    q.add(Entry(TAKE, "", "Bahnstreik", expect_mixer="Jens", expect_room="Ton 3", havarie="hv1"))
    ctx = types.SimpleNamespace(queue=q, helmut=_client(mock), mixer="Jens", room="Ton 3")
    mock.unavailable = True
    with pytest.raises(CommandError, match="nur, wenn Helmut erreichbar"):
        assign_havarie(ctx, {"havarie": "hv1", "id": "p1"})
    assert q.waiting()                                                          # bleibt unzugeordnet
