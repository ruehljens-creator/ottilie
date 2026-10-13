import pytest

from fakes import FakeEngine
from tonmischung.app import commands, viewmodel
from tonmischung.app.bridge import CommandError
from tonmischung.app.context import AppContext
from tonmischung.config.settings import Settings
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.ptsl.client import PtslClient


@pytest.fixture
def ctx(tmp_path):
    m = MockHelmut()
    m.add_project("a", "Bahnstreik", "Heimatjournal", assets=["/H/a/Bahnstreik.aaf", "/H/a/Bahnstreik.mxf"])
    m.add_project("b", "Kita-Streik", "Heimatjournal", state="in_mischung", mischer="A. Wolf", raum="Ton 2")
    m.add_project("c", "Apfelwein", "Heimatjournal", state="importiert", mischer="Jens", raum="Ton 3",
                  assets=["/H/c/Apfelwein.aaf"])
    m.add_project("d", "Messe", "Main-Spitze", state="ausgeliefert", assets=["/H/d/Messe.aaf"])
    url = m.start()
    c = AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient(url, lambda: ("test", "test")),
                   PtslClient(engine_factory=lambda: FakeEngine()), "Jens", "Ton 3", str(tmp_path), m, "x")
    yield c
    m.stop()


def test_liste_und_zaehler(ctx):
    r = commands.list_contributions(ctx, {})
    by = {row["id"]: row for row in r["rows"]}
    assert r["counts"] == {"list": 4}
    assert (by["a"]["state"], by["a"]["pill"]) == ("bereit für Mischung", "ready")
    assert (by["b"]["state"], by["b"]["pill"]) == ("in Mischung · A. Wolf (Ton 2)", "busy")
    assert (by["c"]["state"], by["c"]["pill"]) == ("meine Mischung · importiert", "mine")
    assert (by["d"]["state"], by["d"]["pill"]) == ("ausgeliefert", "sent")
    assert by["a"]["cutter"] == "–"                                   # Helmut liefert (noch) keinen Cutter
    assert [x["id"] for x in commands.list_contributions(ctx, {"query": "kita"})["rows"]] == ["b"]


def test_detail_und_aktionen(ctx):
    a = commands.contribution_detail(ctx, {"id": "a"})
    assert a["actions"] == {"fetch": True, "fetch_label": "Übernehmen", "deliver": False, "release": False,
                            "duplicate": True, "backups": False, "finder": False}
    assert {"k": "AAF", "v": "Bahnstreik.aaf"} in a["kv"] and {"k": "Referenzvideo", "v": "Bahnstreik.mxf"} in a["kv"]
    b = commands.contribution_detail(ctx, {"id": "b"})
    assert b["actions"]["fetch"] is False and b["banners"][0]["text"].startswith("In Mischung bei A. Wolf in Ton 2")
    c = commands.contribution_detail(ctx, {"id": "c"})
    assert c["actions"]["fetch_label"] == "Holen" and c["actions"]["release"]
    assert c["actions"]["deliver"] is False                           # noch keine Session gespeichert


def test_freigeben(ctx):
    with pytest.raises(CommandError, match="Grund"):
        commands.release(ctx, {"id": "c", "reason": "weil"})
    d = commands.release(ctx, {"id": "c", "reason": "Raumwechsel"})
    text = d["banners"][0]["text"]
    assert text.startswith("Freigegeben um ") and text.endswith("aus Ton 3: Raumwechsel. Beim Übernehmen: neuer Import.")
    assert d["actions"]["fetch_label"] == "Übernehmen"
    assert d["hinweise"] == ["Nicht benachrichtigt (kein Helmut-Benutzer in „Räume & Nutzer“): Jens"]
    with pytest.raises(CommandError, match="nicht übernommen"):
        commands.release(ctx, {"id": "a", "reason": "Raumwechsel"})


def test_zurueckliefern_nur_mit_session(ctx):
    from tonmischung.helmut.mapping import Contribution
    from tonmischung.core.mixstate import MixState
    c = Contribution("x", "T", "S", True, MixState("in_mischung", "Jens", "Ton 3", session_path="/s/T.ptx"))
    assert viewmodel.detail(c, None, "Jens", "Ton 3")["actions"]["deliver"] is True
    assert viewmodel.detail(c, None, "Jens", "Ton 3")["actions"]["fetch"] is False      # keine Übergabe


def test_ausgeliefert_erneut_zurueckliefern_nur_wer_es_war(ctx):
    """Jens 09.10.2026: nach dem Zurückliefern „ausgeliefert“ und frei; erneut zurückliefern bleibt dem Mischer."""
    from tonmischung.helmut.mapping import Contribution
    from tonmischung.core.mixstate import MixState
    c = Contribution("x", "T", "S", False, MixState("ausgeliefert", "Jens", "Ton 3", session_path="/s/T.ptx"))
    mine = viewmodel.detail(c, None, "Jens", "Ton 3")
    assert mine["actions"]["deliver"] is True and mine["actions"]["release"] is False
    assert any("Erneut zurückliefern" in b["text"] for b in mine["banners"])
    other = viewmodel.detail(c, None, "A. Wolf", "Ton 2")
    assert other["actions"]["deliver"] is False and not other["banners"]
    assert viewmodel.rows([c], "Jens", "Ton 3")["rows"][0]["state"] == "ausgeliefert"


def test_freigeben_dialog_und_letzter_stand(ctx):
    m = ctx.mock
    m.add_project("e", "Wahl", "Heimatjournal", state="in_mischung", mischer="Jens", raum="Ton 3")
    m.projects["e"]["metadata"].append(m.meta_entry("session_pfad", "/S/Wahl/Wahl.ptx"))
    m.projects["e"]["metadata"].append(m.meta_entry("session_ablage", "lokal"))
    info = commands.contribution_detail(ctx, {"id": "e"})["release_info"]
    assert [k["value"] for k in info["keep"]] == ["letzter_stand", "neu_importieren"]
    assert info["keep_default"] == "letzter_stand" and info["warn"] == "" and info["notify"] == ["Jens"]
    ctx.settings.set_local("system.raeume_nutzer.nutzer", [{"id": "u1", "name": "Jens", "helmut_benutzer": "jmuster"}])
    d = commands.release(ctx, {"id": "e", "reason": "Workstation abgestürzt"})
    assert d["hinweise"] == [] and m.mix_value("e", "rueckgabe_naechstes_oeffnen") == "letzter_stand"
    assert m.mix_value("e", "session_pfad") == "/S/Wahl/Wahl.ptx"                     # Pfad bleibt
    assert "zuletzt gespeicherte Session wird als Kopie geöffnet" in d["banners"][0]["text"]
    assert m.notifications[0][0] == "jmuster" and "„Wahl“ wurde aus Ton 3 freigegeben" in m.notifications[0][1]["msg"]


def test_fremder_raum_lokal_nur_neu_importieren(ctx):
    m = ctx.mock
    m.projects["b"]["metadata"].append(m.meta_entry("session_pfad", "/S/Kita/Kita.ptx"))
    m.projects["b"]["metadata"].append(m.meta_entry("session_ablage", "lokal"))
    info = commands.contribution_detail(ctx, {"id": "b"})["release_info"]
    assert [k["value"] for k in info["keep"]] == ["neu_importieren"]
    assert "nur lokal in Ton 2" in info["keep_hint"] and "bei A. Wolf übernommen" in info["warn"]
    with pytest.raises(CommandError, match="nur „Neu importieren“"):
        commands.release(ctx, {"id": "b", "reason": "Workstation abgestürzt", "keep": "letzter_stand"})
    d = commands.release(ctx, {"id": "b", "reason": "Workstation abgestürzt", "notify": False})
    assert d["hinweise"] == [] and m.mix_value("b", "zustand") == "zurueckgegeben" and not m.notifications


def test_ansichten_nach_zeit_und_sendung_und_suche():
    from tonmischung.core.mixstate import MixState
    from tonmischung.helmut.mapping import Contribution

    def c(pid, title, show, created, **mix):
        return Contribution(pid, title, show, False, MixState(**mix), {"createDate": created})
    items = [c("1", "Wahl", "Heimatjournal", "2026-10-03T09:00:00+00:00"),
             c("2", "Krimi", "Kroatien-Krimi", "2026-10-03T07:00:00+00:00", state="in_mischung", mixer="A. Wolf", room="Ton 2"),
             c("3", "Streik", "Heimatjournal", "2026-10-03T08:00:00+00:00"),
             c("4", "Ohne", "", "")]
    zeit = viewmodel.rows(items, "Jens", "Ton 3")
    assert [r["id"] for r in zeit["rows"]] == ["2", "3", "1", "4"] and zeit["order"] == "zeit"   # älteste zuerst
    assert zeit["rows"][0]["handed"] and zeit["rows"][3]["handed"] == ""
    send = viewmodel.rows(items, "Jens", "Ton 3", order="sendung")
    assert [(r["group"], r["id"]) for r in send["rows"]] == [("Heimatjournal", "3"), ("Heimatjournal", "1"),
                                                             ("Kroatien-Krimi", "2"), ("ohne Sendung", "4")]
    assert [r["id"] for r in viewmodel.rows(items, "Jens", "Ton 3", query="wolf")["rows"]] == ["2"]      # Mischer
    assert [r["id"] for r in viewmodel.rows(items, "Jens", "Ton 3", query="ton 2")["rows"]] == ["2"]     # Raum
    assert [r["id"] for r in viewmodel.rows(items, "Jens", "Ton 3", query="heimat streik")["rows"]] == ["3"]
