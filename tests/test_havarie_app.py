"""Havarie: Dialog „Sendefile ausliefern“ – Übersicht, Ziele, Nachtrag des Zustands."""
import pytest

from fakes import FakeEngine
from tonmischung.app import havarie_app as HA
from tonmischung.app.bridge import CommandError
from tonmischung.app.context import AppContext
from tonmischung.config.settings import Settings
from tonmischung.core import mixstate
from tonmischung.core.catchup import CatchUpQueue
from tonmischung.helmut.client import HelmutClient
from tonmischung.ptsl.client import PtslClient


def _ctx(tmp_path, session=("Havarie", "/S/Havarie/Havarie.ptx")):
    s = Settings(local_path=tmp_path / "e.json")
    return AppContext(s, HelmutClient("http://127.0.0.1:9", lambda: ("t", "t")),
                      PtslClient(engine_factory=lambda: FakeEngine(session=session)), "Jens", "Ton 3", str(tmp_path),
                      None, "x", queue=CatchUpQueue(tmp_path / "n.json"))


def _paket(tmp_path, *videos):
    p = tmp_path / "Paket"
    p.mkdir()
    (p / "Wahl_forPT.aaf").write_bytes(b"aaf")
    for v in videos:
        (p / v).write_bytes(b"v")
    return str(p)


def test_uebersicht(tmp_path):
    ctx = _ctx(tmp_path)
    i = HA.info(ctx, {"path": _paket(tmp_path, "Wahl.mxf")}, volumes=lambda: [{"mount": "/Volumes/STICK", "name": "STICK", "writable": True}])
    assert i["title"] == "Wahl" and len(i["pictures"]) == 1 and i["session"]["name"] == "Havarie"
    assert i["preset"] == "xdcam16" and [p["id"] for p in i["presets"]][:2] == ["xdcam16", "xdcam8"]
    assert i["channels"][0] == {"kanaele": "1/2", "spur": "ST", "exportname": "", "inhalt": "Sendeton"}
    assert i["channels"][2]["spur"] == ""                                              # AD nicht exportiert → Stille
    assert i["usb"][0]["name"] == "STICK" and i["pruefung"] is True
    with pytest.raises(CommandError, match="Ordner nicht gefunden"):
        HA.info(ctx, {"path": str(tmp_path / "leer")})


def test_ziele_aus_dem_dialog(tmp_path):
    stick = tmp_path / "STICK"
    stick.mkdir()
    t = HA._targets({"ziele": [{"art": "usb", "mount": str(stick), "name": "STICK"}, {"art": "ordner", "pfad": "/Volumes/Playout/in/"},
                               {"art": "ordner", "pfad": ""}, {"art": "ftp"}]})
    assert [(x.protocol, x.name) for x in t] == [("usb", "USB STICK"), ("ordner", "in")]
    assert t[0].path.endswith("STICK/Ottilie Havarie") and t[1].path == "/Volumes/Playout/in"


def test_schreibtisch_ist_ziel(tmp_path, monkeypatch):
    """Jens 03.10.2026: Havarie-Ziele noch unbekannt – vorerst Schreibtisch oder USB-Stick."""
    monkeypatch.setenv("HOME", str(tmp_path))
    t = HA._targets({"ziele": [{"art": "schreibtisch"}]})
    assert [(x.name, x.path) for x in t] == [("Schreibtisch", str(tmp_path / "Desktop" / "Ottilie Havarie"))]
    i = HA.info(_ctx(tmp_path), {"path": _paket(tmp_path, "Wahl.mxf")}, volumes=lambda: [])
    assert i["schreibtisch"] == str(tmp_path / "Desktop" / "Ottilie Havarie")


def test_zustand_ausgeliefert_wird_nachgetragen(tmp_path):
    ctx = _ctx(tmp_path)
    before = mixstate.MixState()
    after = mixstate.with_state(before, mixstate.IMPORTED, session_path="/S/Havarie/Havarie.ptx")
    ctx.queue.add_mix("", "Wahl", before, after, "Jens", "Ton 3", havarie="havarie-1")
    assert HA._queue_delivered(ctx, "/S/Havarie/Havarie.ptx") == ""
    mix, lock = ctx.queue.entries()[-2:]
    assert mix.havarie == "havarie-1" and mix.changes["state"] == mixstate.DELIVERED
    assert (lock.kind, lock.locked, lock.havarie) == ("sperre", False, "havarie-1")   # entsperren, wenn zugeordnet
    assert ctx.queue.assign("havarie-1", "p9") == 3
    assert "keinen Havarie-Eintrag" in HA._queue_delivered(ctx, "/anders.ptx")


def test_start_prueft_vorher(tmp_path, monkeypatch):
    from tonmischung.app.bridge import Bridge
    from tonmischung.app.jobs import JobRunner
    runner = JobRunner(Bridge(emit=lambda p: None, inline=True))
    path = _paket(tmp_path, "Wahl.mxf")
    monkeypatch.setattr(HA, "_tools", lambda ctx: HA.H.Tools("", ""))
    with pytest.raises(CommandError, match="ffmpeg"):
        HA.start(_ctx(tmp_path), runner, {"path": path})
    monkeypatch.setattr(HA, "_tools", lambda ctx: HA.H.Tools("/x/ffmpeg", "/x/ffprobe"))
    with pytest.raises(CommandError, match="bmxtranswrap fehlt"):           # Review B36: vorher erst in der Prüfung
        HA.start(_ctx(tmp_path), runner, {"path": path})
    monkeypatch.setattr(HA, "_tools", lambda ctx: HA.H.Tools("/x/ffmpeg", "/x/ffprobe", "/x/bmxtranswrap"))
    with pytest.raises(CommandError, match="Keine Session offen"):
        HA.start(_ctx(tmp_path, session=None), runner, {"path": path})


def test_preset_avc_intra_720p50(tmp_path):
    """Jens 04.10.2026: „mach ein Preset daraus“ – AVC-Intra 100 720p50 (HDF03b), live mit dem DaVinci-Paket."""
    i = HA.info(_ctx(tmp_path), {"path": _paket(tmp_path, "Wahl.mxf")}, volumes=lambda: [])
    assert {"id": "avci720", "name": "AVC-Intra 100 720p50", "profil": "ARD_ZDF_HDF03b"} in i["presets"]
    p = HA._preset("avci720")
    assert p["mxf_profil"] == "ARD_ZDF_HDF03b" and p["modus"] == "auto"


def test_paketordner_der_offenen_session_wird_vorgeschlagen(tmp_path):
    """Jens 04.10.2026: „Beim Ausliefern werde ich nach einem AAF gefragt“ – nach dem Neuladen kannte der Dialog den
    Paketordner nicht mehr. Jetzt merkt sich die App beim Havarie-Import Session → Paket."""
    from tonmischung.core.havarie import PackageIndex
    ctx = _ctx(tmp_path)
    ctx.extra["havarie_pakete"] = idx = PackageIndex(tmp_path / "pakete.json")
    assert HA.info(ctx, {"path": "", "auto": True}) == {"path": ""}                   # nichts bekannt: still
    with pytest.raises(CommandError, match="Paketordner wählen"):
        HA.info(ctx, {"path": ""})
    paket = _paket(tmp_path, "Wahl.mxf")
    idx.remember("/S/Havarie/Havarie.ptx", paket)
    i = HA.info(ctx, {"path": "", "auto": True}, volumes=lambda: [])
    assert i["path"] == paket and i["title"] == "Wahl" and len(i["pictures"]) == 1
    assert PackageIndex(tmp_path / "pakete.json").lookup("/S/Havarie//Havarie.ptx") == paket     # dauerhaft
    assert idx.lookup("") == "" and idx.lookup("/S/anders.ptx") == ""


def test_ziele_vorwahl_havarieordner_und_usb(tmp_path, monkeypatch):
    """Jens 04.10.2026: automatisch Schreibtisch (oder festgelegter Havarie-Ordner) und USB-Stick, wenn vorhanden;
    der Havarie-Ordner bleibt über Neustarts gespeichert (lokale Einstellung)."""
    from tonmischung.app import settings_view as SV
    from tonmischung.config.settings import Settings
    big, small = tmp_path / "STICK", tmp_path / "RUFUS_BOOT"
    big.mkdir(), small.mkdir()
    free = {str(big): 200_000_000_000, str(small): 1_000_000}
    vols = [{"mount": str(big), "name": "STICK", "writable": True}, {"mount": str(small), "name": "RUFUS_BOOT", "writable": True},
            {"mount": "/Volumes/Testdaten", "name": "Testdaten", "writable": False}]
    assert [HA._with_auto(v, free=lambda p: free.get(p, 0))["auto"] for v in vols] == [True, False, False]
    ctx = _ctx(tmp_path)
    paket = _paket(tmp_path, "Wahl.mxf")
    assert HA.info(ctx, {"path": paket}, volumes=lambda: [])["havarie_ordner"] == ""
    ziel = tmp_path / "Havarie-Ordner"
    ziel.mkdir()
    from tonmischung.config.settings import load_schema
    view = SV.set_value(ctx.settings, load_schema(), "export.havarie_zielordner", str(ziel))
    assert not view["errors"] and view["kind"] == "folder"
    again = Settings(local_path=tmp_path / "e.json")                                   # nach einem Neustart
    assert again.get("export.havarie_zielordner") == str(ziel)
    assert HA.info(ctx, {"path": paket}, volumes=lambda: [])["havarie_ordner"] == str(ziel)
    t = HA._targets({"ziele": [{"art": "havarieordner"}]}, str(ziel))
    assert [(x.name, x.path, x.protocol) for x in t] == [("Havarie-Ordner Havarie-Ordner", str(ziel), "ordner")]
    assert HA._targets({"ziele": [{"art": "havarieordner"}]}, "") == []
