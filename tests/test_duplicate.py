"""Duplikat anlegen (Plan M4-Rest, Schritt 4) – gegen den Helmut-Mock."""
import pytest

from fakes import FakeEngine
from tonmischung.app import commands
from tonmischung.app.bridge import CommandError
from tonmischung.app.context import AppContext
from tonmischung.config.settings import Settings
from tonmischung.core.mixstate import duplicate_title
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.ptsl.client import PtslClient


@pytest.fixture
def ctx(tmp_path):
    m = MockHelmut()
    m.add_project("w", "Wahl: Ergebnis", "Heimatjournal", state="in_mischung", mischer="A. Wolf", raum="Ton 2",
                  session_pfad="/S/Wahl/Wahl.ptx", assets=["/H/w/Wahl.aaf", "/H/w/Wahl.mxf"])
    m.set_status("w", "LOCKED")
    m.add_project("f", "Frei", "Heimatjournal", assets=["/H/f/Frei.aaf"])
    url = m.start()
    c = AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient(url, lambda: ("test", "test")),
                   PtslClient(engine_factory=lambda: FakeEngine()), "Jens", "Ton 3", str(tmp_path), m, "x")
    yield c
    m.stop()


def test_titelvorschlag():
    assert duplicate_title("Wahl: Ergebnis", "Kurzfassung") == "Wahl: Ergebnis – Kurzfassung"


def test_duplikat_mit_mischstand_und_sofort_uebernehmen(ctx):
    m = ctx.mock
    info = commands.contribution_detail(ctx, {"id": "w"})["duplicate_info"]
    assert info["can_keep"] and info["keep_default"] and info["titles"]["Web / Social"] == "Wahl: Ergebnis – Web / Social"
    d = commands.duplicate(ctx, {"id": "w", "title": "Wahl: Ergebnis – Kurzfassung", "keep": True, "take": True})
    new = d["id"]
    assert new != "w" and d["title"] == "Wahl: Ergebnis – Kurzfassung"
    assert m.mix_value(new, "duplikat_von") == "Wahl: Ergebnis (w)"
    assert m.mix_value(new, "duplikat_mischstand") == "/S/Wahl/Wahl.ptx"
    assert (m.mix_value(new, "zustand"), m.mix_value(new, "mischer"), m.mix_value(new, "raum")) == ("uebernommen", "Jens", "Ton 3")
    assert m.mix_value(new, "session_pfad") == ""                              # Kopie erst beim Öffnen
    assert sorted(a["filepath"] for a in m.assets[new]) == ["/H/w/Wahl.aaf", "/H/w/Wahl.mxf"]   # gleiche Übergabe
    assert d["banners"][0]["text"] == "Duplikat von „Wahl: Ergebnis (w)“, mit übernommenem Mischstand."
    assert d["reopen"]["kind"] == "ordner" and d["reopen"]["source"] == "/S/Wahl/Wahl.ptx"
    # Original unverändert
    assert (m.mix_value("w", "mischer"), m.mix_value("w", "zustand"), m.status("w")) == ("A. Wolf", "in_mischung", "LOCKED")
    row = next(r for r in commands.list_contributions(ctx, {})["rows"] if r["id"] == new)
    assert row["tags"] == ["Duplikat"]


def test_duplikat_ohne_mischstand_bleibt_frei(ctx):
    m = ctx.mock
    d = commands.duplicate(ctx, {"id": "w", "title": "Wahl – Ersatz"})
    assert m.mix_value(d["id"], "zustand") == "bereit" and m.status(d["id"]) in (None, "UNLOCKED")
    assert m.mix_value(d["id"], "duplikat_mischstand") == "" and d["reopen"] is None
    assert d["actions"]["fetch_label"] == "Übernehmen"


def test_ohne_session_kein_mischstand_und_titel_pflicht(ctx):
    with pytest.raises(CommandError, match="keine gespeicherte Session"):
        commands.duplicate(ctx, {"id": "f", "title": "Frei 2", "keep": True})
    with pytest.raises(CommandError, match="Titel"):
        commands.duplicate(ctx, {"id": "f", "title": "  "})
    assert set(ctx.mock.projects) == {"w", "f"}                               # nichts angelegt
    assert commands.contribution_detail(ctx, {"id": "f"})["duplicate_info"]["can_keep"] is False
