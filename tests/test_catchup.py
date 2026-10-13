"""Nachtrag-Warteschlange (Plan M2-Nachtrag): Ausfall simuliert, später nachgetragen, Konflikt gemeldet."""
import json

import pytest

from fakes import FakeEngine
from test_fetch import AAF_OK, CLIPS
from tonmischung.core.catchup import (APPLY, CONFLICT, DONE, HINT, OPEN, CatchUpQueue, Entry, MIX, decide_mix,
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
    assert rep.unavailable and q.pending()[0].attempts == 1
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
    assert rep.written == ["Bahnstreik: Pendler warten: Asset ST.wav"] and len(mock.assets["p1"]) == 3


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
    """Review B6: ein 500er bei einem Eintrag ist ein Fehler dieses Eintrags – die anderen kommen trotzdem dran."""
    q = CatchUpQueue(tmp_path / "n.json")
    first = q.add_lock("p1", "Bahnstreik", True)
    q.add_asset("p1", "Bahnstreik", "/x/ST.wav", "tonspur")
    client = _client(mock)
    client.login()
    mock.fail_next = [500, 500, 500]
    rep = replay(q, client)
    assert not rep.unavailable and rep.written == ["Bahnstreik: Asset ST.wav"]
    e = next(x for x in q.entries() if x.id == first.id)
    assert e.status == OPEN and e.attempts == 1 and e.message.startswith("Versuch 1")


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
