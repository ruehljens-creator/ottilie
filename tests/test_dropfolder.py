"""Betriebsart Übergabeordner (Jens 11.11.2026): Filmordner lesen, Sperrdatei, Lieferung, Fertigmeldung."""
import json
import os
import threading
import urllib.error

import pytest

from fakes import FakeEngine, _Track  # noqa: F401  (FakeEngine für _engine)
from test_deliver import EXPORT, _engine, _schnell  # noqa: F401  (_schnell: schnelle Wartezeiten, autouse)
from tonmischung.core import mixstate
from tonmischung.core.mixstate import ConflictError, MixState
from tonmischung.core.peers import PeerStatus
from tonmischung.dropfolder import handover as H
from tonmischung.dropfolder import lockfile as L
from tonmischung.dropfolder import notify as N
from tonmischung.dropfolder.client import (UNKNOWN_OWNER, FolderClient, FolderConfig, UnsafeTarget, export_target,
                                           overlay_peers)
from tonmischung.dropfolder.mode import ModeClient, mode_of, switch_blocker
from tonmischung.helmut.client import HelmutError, HelmutUnavailableError
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.workflow.deliver import deliver


def _film(root, name="p1", json_name="uebergabe.json", sizes=None, write_json=True, old=True, **beitrag):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / f"{name}.aaf").write_bytes(b"A" * 10)
    (folder / f"{name}.mxf").write_bytes(b"M" * 20)
    sizes = sizes or {}
    data = {"format": "uebergabe/1", "erstellt": "2026-11-12T09:00:00+01:00", "uebergabe_nr": 1,
            "beitrag": {"id": beitrag.pop("bid", "p1"), "titel": beitrag.pop("titel", "Bahnstreik"),
                        "sendung": "Heimatjournal", "cutter": "Cutterin", "dringend": True, **beitrag},
            "herkunft": {"schnittraum": "Schnitt 4"},
            "dateien": [{"typ": "aaf", "name": f"{name}.aaf", "groesse": sizes.get("aaf", 10)},
                        {"typ": "video", "name": f"{name}.mxf", "groesse": sizes.get("video", 20)}]}
    if write_json:
        (folder / json_name).write_text(json.dumps(data), encoding="utf-8")
        if old:                                           # Übergabe von vorhin (Wartezeit vorbei)
            os.utime(folder / json_name, (1, 1))
    return folder


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _client(root, mixer="Jens", room="Ton 3", clock=None, sent=None, wall=None, **cfg):
    config = FolderConfig(root=str(root), wait_s=cfg.pop("wait_s", 0), **cfg)
    sent = sent if sent is not None else []

    def send(conf, body, creds):
        sent.append(body)
        return 200
    kw = {"wall": wall} if wall else {}
    return FolderClient(lambda: config, lambda: (mixer, room), clock=clock or Clock(),
                        now=lambda: "2026-11-12T10:00:00+00:00", send=send, **kw)


# ── Übergabe-JSON ───────────────────────────────────────────────────────────────
def test_uebergabe_json_lesen_und_abgleichen(tmp_path):
    folder = _film(tmp_path)
    parsed = H.parse(json.loads((folder / "uebergabe.json").read_text()))
    film = H.check(str(folder), parsed, H.regular_size)
    assert film.state == H.READY and film.aaf.endswith(".aaf") and film.video.endswith(".mxf")
    got = (film.beitrag_id, film.title, film.edit_room, film.info["dringend"])
    assert got == ("p1", "Bahnstreik", "Schnitt 4", True)


def test_falsche_groesse_oder_fehlende_datei_ist_unvollstaendig(tmp_path):
    folder = _film(tmp_path, sizes={"video": 2_000_000_000})
    os.remove(folder / "p1.aaf")
    parsed = H.parse(json.loads((folder / "uebergabe.json").read_text()))
    film = H.check(str(folder), parsed, H.regular_size)
    assert film.state == H.INCOMPLETE and film.problems == ("p1.aaf fehlt",
                                                            "p1.mxf noch nicht vollständig (0,0 MB von 2,0 GB)")


def test_aaf_als_symlink_gilt_als_fehlend(tmp_path):
    folder = _film(tmp_path)
    outside = tmp_path / "fremd.aaf"
    outside.write_bytes(b"A" * 10)
    os.remove(folder / "p1.aaf")
    os.symlink(outside, folder / "p1.aaf")
    assert _client(tmp_path).get("p1").raw["probleme"] == ["p1.aaf fehlt"]


@pytest.mark.parametrize("data, why", [
    ([], "kein JSON-Objekt"), ({"format": "x/9", "dateien": [{"name": "a"}]}, "unbekanntes Format"),
    ({"dateien": []}, "dateien"), ({"dateien": [{"name": "../a.aaf"}]}, "Ordnerangabe"),
    ({"dateien": [{"name": "a\x00.aaf"}]}, "Steuerzeichen"),
    ({"dateien": [{"name": "a.aaf", "groesse": "10"}]}, "keine Bytezahl"),
    ({"dateien": [{"name": "a.aaf"}], "uebergabe_nr": 1e999}, "Ganzzahl"),
    ({"dateien": [{"name": f"{i}.aaf"} for i in range(65)]}, "mehr als 64")])
def test_uebergabe_json_abgelehnt(data, why):
    with pytest.raises(H.HandoverError, match=why):
        H.parse(data)


# ── Liste ──────────────────────────────────────────────────────────────────────
def test_liste_erst_nach_wartezeit_und_ohne_json_wird_uebertragen(tmp_path):
    _film(tmp_path, old=False)
    _film(tmp_path, "Kita", write_json=False)
    (tmp_path / "_intern").mkdir()
    (tmp_path / ".versteckt").mkdir()
    clock = Clock()
    mtime = os.stat(tmp_path / "p1" / "uebergabe.json").st_mtime
    c = _client(tmp_path, clock=clock, wait_s=3, wall=lambda: mtime + 1)
    first = {x.id: x.raw["uebergabe"] for x in c.list_for_mixing()}
    assert first == {"p1": H.TRANSFER, "Kita": H.TRANSFER}                # JSON eben entdeckt: 3 s warten
    assert c.get("p1").id == "p1"                                         # ID steht schon jetzt fest
    clock.t = 3.5
    rows = {x.id: x for x in c.list_for_mixing()}
    assert rows["p1"].raw["uebergabe"] == H.READY and rows["Kita"].raw["uebergabe"] == H.TRANSFER
    p1 = rows["p1"]
    assert (p1.title, p1.show, p1.raw["schnittraum"], p1.raw["cutter"], p1.raw["urgent"]) == (
        "Bahnstreik", "Heimatjournal", "Schnitt 4", "Cutterin", "True")
    assert c.handover("p1").aaf.name == "p1.aaf" and c.handover("Kita").aaf is None


def test_nach_neustart_vorhandene_uebergaben_sofort(tmp_path):
    _film(tmp_path)                                           # JSON von vorhin
    assert _client(tmp_path, wait_s=3).get("p1").raw["uebergabe"] == H.READY


def test_id_bleibt_ordnername(tmp_path):
    """Review 1, Ausfall F1: ID hängt nicht an der JSON (fehlt, Wartezeit, doppelte Beitrags-ID)."""
    _film(tmp_path, "A")
    _film(tmp_path, "B")                                      # gleiche Beitrags-ID p1
    c = _client(tmp_path)
    assert sorted(x.id for x in c.list_for_mixing()) == ["A", "B"]
    os.remove(tmp_path / "A" / "uebergabe.json")
    assert c.get("A").raw["uebergabe"] == H.TRANSFER
    with pytest.raises(HelmutError, match="nicht gefunden"):
        c.get("../A")


def test_ordner_ohne_json_ausblendbar(tmp_path):
    _film(tmp_path, "Kita", write_json=False)
    assert _client(tmp_path, show_transfer=False).list_for_mixing() == []


def test_symlink_ordner_werden_nicht_gelistet(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _film(tmp_path, "draussen")
    os.symlink(tmp_path / "draussen", root / "link")
    assert _client(root).list_for_mixing() == []


def test_unlesbare_json_erst_uebertragen_dann_fehlerhaft(tmp_path):
    folder = _film(tmp_path)
    (folder / "uebergabe.json").write_text("{halb", encoding="utf-8")
    os.utime(folder / "uebergabe.json", (1, 1))
    clock = Clock()
    c = _client(tmp_path, clock=clock)
    assert c.list_for_mixing()[0].raw["uebergabe"] == H.TRANSFER
    clock.t = 61
    got = c.list_for_mixing()[0]
    assert got.raw["uebergabe"] == H.INCOMPLETE and "fehlerhaft" in got.raw["probleme"][0]
    good = json.dumps({"dateien": [{"name": "p1.aaf", "groesse": 10}], "beitrag": {"titel": "Bahn"}})
    (folder / "uebergabe.json").write_text(good)
    os.utime(folder / "uebergabe.json", (5, 5))
    assert {x.raw["uebergabe"] for x in c.list_for_mixing()} == {H.READY}


def test_kaputte_fremddaten_kosten_nicht_die_liste(tmp_path):
    """Sicherheit 2: präparierte Dateien legen nur ihren Film lahm."""
    _film(tmp_path, "gut")
    bad = _film(tmp_path, "boese")
    (bad / "uebergabe.json").write_text('{"dateien": [{"name": "a.aaf"}], "uebergabe_nr": 1e999}')
    (bad / ".ottilie-sperre.json").write_text('{"uebergabe_nr": "abc", "gesperrt": true, "mischung": [1]}')
    deep = _film(tmp_path, "tief")
    (deep / "uebergabe.json").write_text("[" * 100000)
    rows = {x.id: x for x in _client(tmp_path).list_for_mixing()}
    assert rows["gut"].raw["uebergabe"] == H.READY and set(rows) == {"gut", "boese", "tief"}


def test_uebergabeordner_nicht_erreichbar(tmp_path):
    with pytest.raises(HelmutUnavailableError, match="nicht erreichbar"):
        _client(tmp_path / "weg").list_for_mixing()
    with pytest.raises(HelmutError, match="Kein Übergabeordner"):
        FolderClient(lambda: FolderConfig(root=""), lambda: ("J", "T")).reachable()


def test_entwicklungsmodus_nur_im_testordner(tmp_path):
    c = FolderClient(lambda: FolderConfig(root=str(tmp_path)), lambda: ("J", "T"), safe_root=str(tmp_path / "test"))
    with pytest.raises(HelmutError, match="nur im Testordner"):
        c.reachable()


# ── Sperrdatei ─────────────────────────────────────────────────────────────────
def test_sperrdatei_exklusiv(tmp_path):
    path = str(tmp_path / "sperre.json")
    L.claim(path, L.content(MixState(), True, "Ton 3", "Jens", "t", 1))
    with pytest.raises(L.LockTaken, match="Jens"):
        L.claim(path, L.content(MixState(), True, "Ton 2", "Anna", "t", 1))
    L.update(path, lambda cur: {**cur, "gesperrt": False})
    L.claim(path, L.content(MixState(), True, "Ton 2", "Anna", "t", 1))     # frei → neu belegt
    assert L.read(path)["raum"] == "Ton 2" and os.listdir(tmp_path) == ["sperre.json"]   # keine Reste
    assert os.stat(path).st_mode & 0o777 == 0o664                             # für andere Konten lesbar


def test_sperrdatei_gleichzeitig_gewinnt_genau_einer(tmp_path):
    path = str(tmp_path / "sperre.json")
    L.update(path, lambda cur: L.content(MixState(), False, "", "", "t", 1))   # freie Sperrdatei von früher
    wins, start = [], threading.Barrier(8)

    def go(i):
        start.wait()
        try:
            L.claim(path, L.content(MixState(), True, f"Ton {i}", f"M{i}", "t", 1))
            wins.append(i)
        except L.LockTaken:
            pass
    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1 and L.read(path)["raum"] == f"Ton {wins[0]}"


def test_alte_hilfsdatei_nach_speicherzeit_beiseite(tmp_path):
    path = str(tmp_path / "sperre.json")
    open(path + ".claim", "w").write("fremd")
    os.utime(path + ".claim", (1, 1))                        # nach der Uhr des Speichers uralt
    L.claim(path, L.content(MixState(), True, "Ton 3", "Jens", "t", 1))
    assert not os.path.exists(path + ".claim")
    open(path + ".claim", "w").write("fremd")                 # frisch: warten, dann aufgeben
    with pytest.raises(L.LockTaken, match="anderer Raum"):
        L.claim(path, L.content(MixState(), True, "Ton 2", "Anna", "t", 1), sleep=lambda s: None)
    assert open(path + ".claim").read() == "fremd"            # fremde Hilfsdatei bleibt


def test_unlesbare_sperrdatei_gilt_als_belegt(tmp_path):
    path = tmp_path / "sperre.json"
    path.write_text("{halb")
    with pytest.raises(L.LockTaken, match="unlesbar"):
        L.claim(str(path), L.content(MixState(), True, "Ton 3", "Jens", "t", 1))


def test_stand_rundreise():
    mix = MixState(state="importiert", mixer="Jens", room="Ton 3", progress=40, session_path="/S/a.ptx",
                   video_id="VID-1")
    assert L.mix_of(L.content(mix, True, "Ton 3", "Jens", "t", 2)) == mix


# ── Übernehmen, Freigeben, gleichzeitig ────────────────────────────────────────
def _svc(client, mixer="Jens", room="Ton 3", queue=None):
    return MixService(client, mixer, room, queue=queue, now=lambda: "jetzt", sleep=lambda s: None)


def _lock(folder):
    return json.loads((folder / ".ottilie-sperre.json").read_text())


def test_uebernehmen_und_freigeben_mit_sperrdatei(tmp_path):
    folder = _film(tmp_path)
    c = _client(tmp_path)
    taken = _svc(c).take("p1")
    lock = _lock(folder)
    assert (lock["gesperrt"], lock["zustand"], lock["mischer"], lock["raum"]) == (True, "uebernommen", "Jens", "Ton 3")
    assert taken.mix.state == mixstate.TAKEN and c.get("p1").locked
    with pytest.raises(ConflictError):                        # anderer Raum: belegt
        _svc(_client(tmp_path, "Anna", "Ton 2"), "Anna", "Ton 2").take("p1")
    _svc(c).release("p1", "Raumwechsel")
    lock = _lock(folder)
    assert (lock["gesperrt"], lock["zustand"]) == (False, "zurueckgegeben")    # umgeschrieben, nicht gelöscht
    assert lock["mischung"]["rueckgabe_grund"] == "Raumwechsel"
    assert _svc(_client(tmp_path, "Anna", "Ton 2"), "Anna", "Ton 2").take("p1").mix.mixer == "Anna"


def test_unlesbare_sperrdatei_niemand_uebernimmt_freigeben_von_hand(tmp_path):
    """Review 2: unlesbar = belegt von unbekannt, nicht „ohne Besitzer“."""
    folder = _film(tmp_path)
    (folder / ".ottilie-sperre.json").write_text('{"format": "ottilie-sperre/2"}')
    anna = _svc(_client(tmp_path, "Anna", "Ton 2"), "Anna", "Ton 2")
    assert anna.client.get("p1").mix == UNKNOWN_OWNER
    with pytest.raises(ConflictError):
        anna.take("p1")
    anna.release("p1", "Workstation abgestürzt")              # von Hand, wie bei Helmut
    assert _lock(folder)["zustand"] == "zurueckgegeben"
    assert any(".defekt-" in n for n in os.listdir(folder))   # beiseitegelegt, nicht gelöscht
    assert anna.take("p1").mix.mixer == "Anna"


def test_verspaetetes_schreiben_ueberschreibt_neuen_besitzer_nicht(tmp_path):
    """Ausfall F9: A schreibt mit altem Stand, B hat inzwischen übernommen."""
    _film(tmp_path)
    a, b = _client(tmp_path), _client(tmp_path, "Anna", "Ton 2")
    _svc(a).take("p1")
    seen_by_a = a.get("p1")
    _svc(a).release("p1", "Raumwechsel")
    _svc(b, "Anna", "Ton 2").take("p1")
    with pytest.raises(HelmutError, match="inzwischen bei Anna"):
        a.write_mix(seen_by_a, mixstate.with_state(seen_by_a.mix, mixstate.IMPORTED))
    a.set_locked("p1", False)                                 # verspätetes Entsperren: bleibt gesperrt
    assert b.get("p1").locked and b.get("p1").mix.mixer == "Anna"


def test_unvollstaendig_nicht_holbar(tmp_path):
    _film(tmp_path, sizes={"aaf": 1})
    c = _client(tmp_path)
    from tonmischung.app import viewmodel
    d = viewmodel.detail(c.get("p1"), c.handover("p1"), "Jens", "Ton 3", folder_mode=True)
    assert not d["actions"]["fetch"] and not d["actions"]["duplicate"]
    assert any("unvollständig" in b["text"] for b in d["banners"])
    row = viewmodel.rows([c.get("p1")], "Jens", "Ton 3")["rows"][0]
    assert (row["state"], row["origin"]) == ("unvollständig", "Schnitt 4")


def _import(svc, c, pid="p1", session="/S/a.ptx"):
    """Wie `workflow.fetch`: Übergabe lesen, (Pro Tools), dann „importiert“ mit der gelesenen Fassung."""
    from tonmischung.workflow.fetch import handover_nr
    h = c.handover(pid)
    return lambda: svc.set_state(pid, mixstate.IMPORTED, session_path=session, **handover_nr(h))


def _new_version(folder, nr):
    data = json.loads((folder / "uebergabe.json").read_text())
    (folder / "uebergabe.json").write_text(json.dumps({**data, "uebergabe_nr": nr}))
    os.utime(folder / "uebergabe.json", (nr, nr))


def test_neue_uebergabe_und_erneut_holen(tmp_path):
    """Review 3. Runde: erneutes Holen nach „importiert“ vermerkt die neue Fassung; Freigeben mit „letzter
    Stand“ und Wiederaufnehmen ändert sie nicht."""
    from tonmischung.app import viewmodel
    folder = _film(tmp_path)
    c = _client(tmp_path)
    svc = _svc(c)
    svc.take("p1")
    _import(svc, c)()                                     # Fassung 1 importiert
    _new_version(folder, 2)
    got = c.get("p1")
    assert got.raw["neue_uebergabe"] and "neue Übergabe" in viewmodel.tags(got) and got.raw["gemischte_nr"] == 1
    svc.take("p1")                                        # erneut holen (Zustand „importiert“ bleibt beim Übernehmen)
    _import(svc, c, session="/S/neu.ptx")()
    got = c.get("p1")
    assert not got.raw["neue_uebergabe"] and got.raw["gemischte_nr"] == 2
    _new_version(folder, 3)
    svc.release("p1", "Raumwechsel", mixstate.KEEP_LAST)
    svc.take("p1")                                        # letzter Stand als Kopie: kein Import
    got = c.get("p1")
    assert got.raw["gemischte_nr"] == 2 and got.raw["neue_uebergabe"]
    os.remove(folder / "uebergabe.json")                  # Helmut ersetzt gerade die Medien
    assert c.get("p1").raw["uebergabe"] == H.TRANSFER and c.get("p1").raw["neue_uebergabe"]


def test_nur_mit_helmut_api(tmp_path):
    _film(tmp_path)
    c = _client(tmp_path)
    for call in (lambda: c.notify("u", "t", "x"), lambda: c.duplicate(c.get("p1"), "x")):
        with pytest.raises(HelmutError, match="nur mit der Helmut-API"):
            call()


def test_uebernahmen_anderer_sofort_belegt(tmp_path):
    _film(tmp_path)
    items = _client(tmp_path).list_for_mixing()
    out = overlay_peers(items, {"Ton 2": PeerStatus("Ton 2", "Anna", "mischt", "Bahnstreik", "p1")}, "Jens", "Ton 3")
    assert (out[0].mix.state, out[0].mix.room, out[0].locked) == (mixstate.TAKEN, "Ton 2", True)
    assert overlay_peers(items, {"Ton 2": PeerStatus("Ton 2")}, "Jens", "Ton 3")[0].mix.state == mixstate.READY


# ── Lieferung nach „Audio Export“ ──────────────────────────────────────────────
def _stage(target, names):
    os.makedirs(target.copy_dir, exist_ok=True)
    out = []
    for n in names:
        p = os.path.join(target.copy_dir, n)
        open(p, "w").write(n)
        out.append(p)
    return out


def test_ersetzen_verschiebt_vorige_lieferung_nach_alt(tmp_path):
    film = tmp_path / "p1"
    film.mkdir()
    t1 = export_target(str(film), "Audio Export", "ersetzen", "20261112-100000")
    t1.finish(_stage(t1, ["ST.wav", "p1_metadaten.json"]))
    os.utime(film / "Audio Export" / "p1_metadaten.json", (0, 1_700_000_000))
    t2 = export_target(str(film), "Audio Export", "ersetzen", "20261112-110000")
    placed = t2.finish(_stage(t2, ["ST.wav", "IT.wav", "p1_metadaten.json"]))
    base = film / "Audio Export"
    assert sorted(os.listdir(base)) == ["IT.wav", "ST.wav", "_alt", "p1_metadaten.json"]     # Hilfsordner weg
    from datetime import datetime
    old = datetime.fromtimestamp(1_700_000_000).strftime("%Y%m%d-%H%M%S")      # Zeit der vorigen Lieferung
    assert sorted(os.listdir(base / "_alt" / old)) == ["ST.wav", "p1_metadaten.json"]
    assert placed[-1] == str(base / "p1_metadaten.json")


def test_zeitstempel_je_lieferung_ein_ordner(tmp_path):
    t = export_target(str(tmp_path), "Audio Export", "zeitstempel", "20261112-100000")
    assert t.copy_dir == t.final_dir == str(tmp_path / "Audio Export" / "20261112-100000")


@pytest.mark.parametrize("link", ["Audio Export", "film"])
def test_exportordner_als_symlink_abgelehnt(tmp_path, link):
    """Sicherheit 1: ein Symlink hätte die Lieferung (und das Verschieben nach _alt) nach draußen geleitet."""
    victim = tmp_path / "home"
    victim.mkdir()
    (victim / "wichtig.txt").write_text("x")
    root = tmp_path / "root"
    root.mkdir()
    if link == "film":
        os.symlink(victim, root / "p1")
    else:
        (root / "p1").mkdir()
        os.symlink(victim, root / "p1" / "Audio Export")
    with pytest.raises(UnsafeTarget):
        export_target(str(root / "p1"), "Audio Export", "ersetzen", "20261112-100000", root=str(root))
    assert os.listdir(victim) == ["wichtig.txt"]


_ENGINES: dict = {}


def _deliver_folder(tmp_path, c, svc, folder, minute=0):
    from datetime import datetime
    if str(tmp_path) not in _ENGINES:                    # eine Pro-Tools-Attrappe je Test
        _ENGINES[str(tmp_path)] = _engine(tmp_path)
    e = _ENGINES[str(tmp_path)]
    return deliver("p1", service=svc, ptsl=PtslClient(engine_factory=lambda: e),
                   export_tracks=EXPORT, loudness_tracks=["ST"], handover_root="",
                   now=lambda: datetime(2026, 11, 12, 10, minute), target=c.delivery_target("p1"))


def test_zurueckliefern_in_audio_export_mit_fertigmeldung(tmp_path):
    root = tmp_path / "uebergabe"
    folder = _film(root, bid="B-17")
    sent = []
    c = _client(root, sent=sent, notify_active=True)
    svc = _svc(c)
    svc.take("p1")
    svc.set_state("p1", mixstate.IMPORTED, session_path=str(tmp_path / "Bahnstreik" / "Bahnstreik.ptx"))
    r = _deliver_folder(tmp_path, c, svc, folder)
    base = folder / "Audio Export"
    assert all(os.path.dirname(p) == str(base) for p in r.delivered)
    assert r.metadata == str(base / "B-17_metadaten.json")                  # Beitrags-ID aus der Übergabe
    meta = json.loads(open(r.metadata, encoding="utf-8").read())
    assert meta["herkunft"] == {"schnittraum": "Schnitt 4"} and meta["projekt"]["id"] == "B-17"
    assert all(os.path.isfile(a["pfad"]) for a in meta["audio"])
    lock = _lock(folder)
    assert (lock["zustand"], lock["gesperrt"]) == ("ausgeliefert", False)
    assert len(sent) == 1
    body = sent[0]
    assert (body["ereignis"], body["format"], body["beitrag"], body["herkunft"]) == (
        "audio_export_fertig", "ottilie-fertigmeldung/1",
        {"id": "B-17", "titel": "Bahnstreik", "uebergabe_nr": 1, "aktuelle_uebergabe_nr": 1},
        {"schnittraum": "Schnitt 4"})
    assert (body["film_ordner"], body["export_ordner"], body["metadaten_json"]) == (
        "p1", "p1/Audio Export", "p1/Audio Export/B-17_metadaten.json")
    assert [f["name"] for f in body["dateien"]][-1] == "B-17_metadaten.json" and len(body["dateien"]) == 6
    assert all(isinstance(f["groesse"], int) for f in body["dateien"])
    first_id = body["meldung_id"]
    assert c.done_message("p1", r.metadata)["meldung_id"] == first_id       # Wiederholung: dieselbe ID
    _deliver_folder(tmp_path, c, svc, folder, minute=5)                      # erneut: vorige nach _alt
    assert len(os.listdir(base / "_alt")) == 1 and sent[1]["meldung_id"] != first_id
    assert c.add_asset("p1", str(base / "gibts-nicht_metadaten.json"), "metadaten") == {"ueberholt": True}


def test_fertigmeldung_scheitert_haelt_nichts_auf(tmp_path):
    """Review 4, Ausfall F3: Dienst weg – die Meldung wartet im Nachtrag, alles andere läuft weiter."""
    from tonmischung.core.catchup import CatchUpQueue
    from tonmischung.helmut.catchup import replay
    root = tmp_path / "uebergabe"
    folder = _film(root)
    _film(root, "p2")
    c = _client(root, notify_active=True)

    def down(conf, body, creds):
        raise HelmutUnavailableError("Fertigmeldung: Dienst nicht erreichbar")
    c._send = down
    q = CatchUpQueue(str(tmp_path / "nachtrag.json"))
    svc = _svc(c, queue=q)
    svc.take("p1")
    svc.set_state("p1", mixstate.IMPORTED, session_path=str(tmp_path / "Bahnstreik" / "Bahnstreik.ptx"))
    r = _deliver_folder(tmp_path, c, svc, folder)
    assert r.queued == "ausfall" and q.summary()["offen"] == 1 and "Metadaten" in q.summary()["eintraege"][0]["text"]
    q.add_lock("p2", "Kita", True)                                          # ein anderer Eintrag dahinter
    report = replay(q, c)
    assert len(report.written) == 1                                        # p2 nachgetragen trotz Ausfall
    assert not report.unavailable and q.summary()["offen"] == 1
    _deliver_folder(tmp_path, c, svc, folder, minute=5)                     # neue Fassung: nicht blockiert
    sent = []
    c._send = lambda conf, body, creds: sent.append(body) or 200
    replay(q, c)
    assert q.summary()["offen"] == 0
    # „ersetzen“: beide Einträge zeigen auf dieselbe Datei (jetzt die neue Lieferung) – genau eine Meldung
    assert len(sent) == 1 and sent[0]["zeit"]


def test_alte_meldung_kommt_nicht_nach_der_neuen(tmp_path):
    """Review 2, 2. Runde („zeitstempel“): Lieferung 1 eingereiht, Lieferung 2 gemeldet → 1 ist überholt."""
    from tonmischung.core.catchup import CatchUpQueue
    from tonmischung.helmut.catchup import replay
    root = tmp_path / "uebergabe"
    folder = _film(root)
    c = _client(root, notify_active=True, redelivery="zeitstempel")
    sent = []

    def down(conf, body, creds):
        raise HelmutUnavailableError("weg")
    c._send = down
    q = CatchUpQueue(str(tmp_path / "nachtrag.json"))
    svc = _svc(c, queue=q)
    svc.take("p1")
    svc.set_state("p1", mixstate.IMPORTED, session_path=str(tmp_path / "Bahnstreik" / "Bahnstreik.ptx"))
    _deliver_folder(tmp_path, c, svc, folder)
    c._send = lambda conf, body, creds: sent.append(body) or 200
    _deliver_folder(tmp_path, c, svc, folder, minute=5)
    assert [s["export_ordner"] for s in sent] == ["p1/Audio Export/20261112-100500"]
    replay(q, c)
    assert len(sent) == 1 and q.summary()["offen"] == 0                    # die alte ging nicht mehr hinterher


def test_neue_fassung_waehrend_des_imports_gilt_nicht_als_gemischt(tmp_path):
    folder = _film(tmp_path)
    c = _client(tmp_path)
    svc = _svc(c)
    svc.take("p1")
    done = _import(svc, c)                                # Übergabe (Fassung 1) gelesen, Import läuft
    _new_version(folder, 2)
    done()
    got = c.get("p1")
    assert got.raw["gemischte_nr"] == 1 and got.raw["neue_uebergabe"]


def test_abgelehnte_fertigmeldung_haelt_den_film_nicht_auf(tmp_path):
    from tonmischung.core.catchup import CatchUpQueue
    from tonmischung.helmut.catchup import replay
    root = tmp_path / "uebergabe"
    folder = _film(root)
    c = _client(root, notify_active=True)

    def refused(conf, body, creds):
        raise HelmutError("Fertigmeldung abgelehnt (401)", status=401)
    c._send = refused
    q = CatchUpQueue(str(tmp_path / "nachtrag.json"))
    svc = _svc(c, queue=q)
    svc.take("p1")
    svc.set_state("p1", mixstate.IMPORTED, session_path=str(tmp_path / "Bahnstreik" / "Bahnstreik.ptx"))
    assert _deliver_folder(tmp_path, c, svc, folder).queued == "abgelehnt"
    q.add_lock("p1", "Bahnstreik", False)                                  # späterer Eintrag desselben Films
    report = replay(q, c)
    assert len(report.written) == 1 and q.summary()["offen"] == 1


def test_lesefehler_der_sperrdatei_ist_ausfall_nicht_fremdbesitz(tmp_path, monkeypatch):
    _film(tmp_path)
    c = _client(tmp_path)

    def eio(path):
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(L, "read", eio)
    with pytest.raises(HelmutUnavailableError):
        c.get("p1")
    assert c.list_for_mixing()[0].raw["sperre_unlesbar"]                   # Liste bleibt, Film nicht holbar


# ── Fertigmeldung ──────────────────────────────────────────────────────────────
def test_vorlage_fuellen_typen_bleiben():
    body = N.fill(N.DEFAULT_TEMPLATE, {"ereignis": "test", "uebergabe_nr": 2, "dateien": [{"name": "a"}],
                                       "titel": "Bahn"})
    assert body["beitrag"]["uebergabe_nr"] == 2 and body["dateien"] == [{"name": "a"}]
    assert body["meldung_id"] == "{meldung_id}"                              # unbekannt bleibt stehen
    assert N.fill({"x": "Film {titel}!"}, {"titel": "Bahn"}) == {"x": "Film Bahn!"}


def test_statuscodes():
    assert N.is_success(204, ["2xx"]) and not N.is_success(302, ["2xx"]) and N.is_success(302, ["302"])


class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Opener:
    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def open(self, req, timeout):
        self.requests.append(req)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        if a >= 400:
            raise urllib.error.HTTPError(req.full_url, a, "x", {}, None)
        return _Resp(a)


def test_senden_mit_anmeldung_und_wiederholung():
    cfg = N.NotifyConfig(url="https://dienst.example/fertig", auth=N.AUTH_KEY, key_header="X-Key", retries=2)
    op = _Opener(urllib.error.URLError(ConnectionRefusedError()), 503, 202)
    assert N.send(cfg, {"a": 1}, lambda: ("konto", "geheim"), opener=op, sleep=lambda s: None) == 202
    assert len(op.requests) == 3 and op.requests[0].get_header("X-key") == "geheim"
    assert json.loads(op.requests[0].data) == {"a": 1}
    bearer = N.NotifyConfig(url="https://d.example", auth=N.AUTH_BEARER)
    op = _Opener(200)
    N.send(bearer, {}, lambda: ("", "tok"), opener=op)
    assert op.requests[0].get_header("Authorization") == "Bearer tok"


def test_senden_fehler():
    cfg = N.NotifyConfig(url="https://d.example", retries=1)
    with pytest.raises(HelmutError, match=r"abgelehnt \(400\)") as e:
        N.send(cfg, {}, opener=_Opener(400))
    assert not isinstance(e.value, HelmutUnavailableError)
    with pytest.raises(HelmutUnavailableError):
        N.send(cfg, {}, opener=_Opener(500, 502), sleep=lambda s: None)
    with pytest.raises(HelmutError, match="keine Adresse"):
        N.send(N.NotifyConfig(url="ftp://x"), {})
    with pytest.raises(HelmutError, match="Schlüsselbund"):
        N.send(N.NotifyConfig(url="https://d.example", auth=N.AUTH_BEARER), {}, None, opener=_Opener(200))


def test_fertigmeldung_aus_sendet_nichts(tmp_path):
    _film(tmp_path)
    sent = []
    c = _client(tmp_path, sent=sent)
    assert c.add_asset("p1", str(tmp_path / "x.json"), "metadaten") == {} and sent == []


# ── Betriebsart ────────────────────────────────────────────────────────────────
def test_umschalten_ohne_neustart():
    mode = ["helmut_api"]
    m = ModeClient(api=type("Api", (), {"name": "api"})(), folder=lambda: type("F", (), {"name": "ordner"})(),
                   mode=lambda: mode[0])
    assert (m.name, mode_of(m)) == ("api", "helmut_api")
    mode[0] = "uebergabeordner"
    assert (m.name, mode_of(m)) == ("ordner", "uebergabeordner")
    assert mode_of(object()) == "helmut_api"


def test_umschalten_nur_wenn_nichts_in_arbeit():
    assert "Auftrags" in switch_blocker("Holen: X", 0, [])
    assert "„Bahnstreik“" in switch_blocker("", 0, ["Bahnstreik"])
    assert "Nachtrag" in switch_blocker("", 2, [])
    assert switch_blocker("", 0, []) == ""


def test_betriebsart_im_raumstatus():
    from tonmischung.zentrale.link import from_status, to_status
    s = PeerStatus("Ton 2", "Anna", "mischt", "Bahn", "p1", mode="uebergabeordner")
    d = {**to_status(s), "raum": "Ton 2", "verbunden": True}
    assert from_status(d).mode == "uebergabeordner" and from_status(d).mixer == ""   # Räume statt Personen
    old = {k: v for k, v in d.items() if k != "betriebsart"}                         # ältere App ohne Betriebsart
    assert from_status(old).mode == ""


def test_umschalten_gesperrt_wenn_ordner_nicht_erreichbar(tmp_path):
    """Review 5, Ausfall F8: ist der Ordner weg, lässt sich nicht prüfen, ob hier etwas übernommen ist."""
    from types import SimpleNamespace

    from tonmischung.app.commands import mode_switch_blocker
    ctx = SimpleNamespace(helmut=_client(tmp_path / "weg"), queue=None, mixer="Jens", room="Ton 3", folder_mode=True)
    assert "nicht erreichbar" in mode_switch_blocker(ctx, None)
    ctx.helmut = FolderClient(lambda: FolderConfig(root=""), lambda: ("Jens", "Ton 3"))       # nichts eingestellt
    assert mode_switch_blocker(ctx, None) == ""
    _film(tmp_path)
    ctx.helmut = _client(tmp_path)
    _svc(ctx.helmut).take("p1")
    assert "„Bahnstreik“ ist hier noch übernommen" in mode_switch_blocker(ctx, None)


def test_raeume_in_anderer_betriebsart_gewarnt(tmp_path):
    from types import SimpleNamespace

    from tonmischung.app.roomstatus import rooms

    class Peers:
        available = True

        def peers(self):
            return {"Ton 2": PeerStatus("Ton 2", "Anna", mode="helmut_api")}
    from tonmischung.helmut.client import HelmutUnavailableError

    def unreachable():
        raise HelmutUnavailableError("Übergabeordner nicht erreichbar")
    ctx = SimpleNamespace(settings=None, room="Ton 3", mixer="Jens", user=None, mode="uebergabeordner",
                          helmut=SimpleNamespace(list_for_mixing=unreachable))
    r = rooms(ctx, Peers())
    card = next(c for c in r["cards"] if c["name"] == "Ton 2")
    assert card["modus_anders"] and card["modus"] == "Helmut-API" and "Ton 2 arbeitet" in r["gemischt"]


# ── Nachprüfung Sicherheit (11.11.2026) ──────────────────────────────────────────
def test_sperrdatei_als_fifo_oder_symlink_haelt_nichts_an(tmp_path):
    """Sicherheit C: FIFO hätte die Liste angehalten, Symlink fremde Daten gelesen – beides gilt als belegt von
    unbekannt."""
    a, b = _film(tmp_path, "a"), _film(tmp_path, "b")
    os.mkfifo(a / ".ottilie-sperre.json")
    (tmp_path / "fremd.json").write_text('{"gesperrt": false}')
    os.symlink(tmp_path / "fremd.json", b / ".ottilie-sperre.json")
    rows = {x.id: x for x in _client(tmp_path).list_for_mixing()}
    assert rows["a"].mix == UNKNOWN_OWNER and rows["b"].mix == UNKNOWN_OWNER


def test_fortschritt_unsinn_kostet_nicht_die_liste(tmp_path):
    folder = _film(tmp_path)
    (folder / ".ottilie-sperre.json").write_text('{"gesperrt": true, "mischung": {"fortschritt": "1e999"}}')
    assert _client(tmp_path).list_for_mixing()[0].mix.progress == 0


def test_hilfsdatei_toter_symlink_und_zukunft(tmp_path):
    """Sicherheit E/F: kein Hängen bei 100 % CPU, keine Dauersperre durch eine Zeit in der Zukunft."""
    path = str(tmp_path / "sperre.json")
    os.symlink(tmp_path / "gibts-nicht", path + ".claim")
    with pytest.raises(L.LockTaken):
        L.claim(path, L.content(MixState(), True, "Ton 3", "Jens", "t", 1), sleep=lambda s: None)
    os.remove(path + ".claim")
    open(path + ".claim", "w").write("fremd")
    os.utime(path + ".claim", (4_000_000_000, 4_000_000_000))              # weit in der Zukunft
    L.claim(path, L.content(MixState(), True, "Ton 3", "Jens", "t", 1))
    assert L.read(path)["raum"] == "Ton 3"


def test_kopierordner_gegen_symlink_getauscht(tmp_path):
    """Sicherheit A: während des Exports wird der Kopierordner gegen einen Verweis nach außen getauscht."""
    victim = tmp_path / "draussen"
    victim.mkdir()
    film = tmp_path / "root" / "p1"
    film.mkdir(parents=True)
    for mode in ("ersetzen", "zeitstempel"):
        t = export_target(str(film), "Audio Export", mode, f"2026-{mode}", root=str(tmp_path / "root"))
        os.rmdir(t.copy_dir)
        os.symlink(victim, t.copy_dir)
        with pytest.raises(UnsafeTarget):
            t.check()
        os.remove(t.copy_dir)
    assert os.listdir(victim) == []


def test_adresse_der_fertigmeldung_ueber_mcp_gekuerzt():
    from tonmischung.app.api import redact_section
    view = {"groups": [{"fields": [{"path": "system.uebergabeordner.fertigmeldung.url",
                                    "value": "https://u:pw@dienst.example:8443/hook/GEHEIM?token=GEHEIM"}]}]}
    shown = redact_section(view)["groups"][0]["fields"][0]["value"]
    assert shown == "https://dienst.example:8443/…?…" and "GEHEIM" not in shown and "pw" not in shown


def test_lieferung_ohne_begleitdateien_von_macos(tmp_path):
    """SMB ohne erweiterte Attribute: eigene „._*“ (auch verwaiste vom Umbenennen) bleiben nicht in „Audio Export“;
    fremde bleiben (Regel 03.10.2026), ebenso Helmuts Dateien im Filmordner."""
    ad = b"\x00\x05\x16\x07" + b"\x00" * 60
    film = tmp_path / "film"
    film.mkdir()
    (film / "._film.mxf").write_bytes(ad)                             # Helmuts – bleibt

    def deliver(stamp: str) -> None:
        t = export_target(str(film), "Audio Export", "ersetzen", stamp)
        (film / "._Audio Export").write_bytes(ad)                     # wie smbfs beim Anlegen des Ordners
        names = ("ST.wav", "f_metadaten.json")
        for n in names:
            open(os.path.join(t.copy_dir, n), "wb").write(b"x")
            open(os.path.join(t.copy_dir, "._" + n), "wb").write(ad)
        open(os.path.join(t.final_dir, "._ST.wav"), "wb").write(ad)    # verwaist
        t.finish([os.path.join(t.copy_dir, n) for n in names])
    deliver("20261111-100000")
    assert not (film / "._Audio Export").exists()                     # Ordner eben angelegt → eigene Begleitdatei
    exp = film / "Audio Export"
    (exp / "_notiz.txt").write_bytes(b"x")                            # „_“: keine Lieferung, wird nicht verschoben
    (exp / ".__notiz.txt").write_bytes(ad)                             # fremd – bleibt
    deliver("20261111-110000")
    assert (film / "._Audio Export").exists()                         # Ordner gab es schon → nicht unsere
    found = sorted(os.path.relpath(os.path.join(r, n), exp) for r, ds, fs in os.walk(exp)
                   for n in ds + fs if n.startswith("._"))
    assert found == [".__notiz.txt"]
    assert sorted(os.listdir(exp)) == [".__notiz.txt", "ST.wav", "_alt", "_notiz.txt", "f_metadaten.json"]
    assert (film / "._film.mxf").exists()


def test_uebergabe_uebergeht_begleitdateien(tmp_path):
    assert H.ignored("._Film 1", ("_",), "Audio Export") and H.ignored(".DS_Store", (), "Audio Export")
    data = {"format": "uebergabe/1", "beitrag": {"id": "A1", "titel": "T"},
            "dateien": [{"typ": "aaf", "name": "._t.aaf", "groesse": 4096}, {"typ": "aaf", "name": "t.aaf"}]}
    assert [f["name"] for f in H.parse(data)["dateien"]] == ["t.aaf"]


def test_aufraeumen_folgt_keinem_verweis_und_rmdir_bricht_nicht_ab(tmp_path):
    """Sicherheit 11.11.2026: „_alt“ als Verweis lenkt das Aufräumen nicht nach draußen; ein nicht leerer Hilfsordner
    lässt die schon vollständige Lieferung nicht scheitern."""
    ad = b"\x00\x05\x16\x07" + b"\x00" * 60
    outside = tmp_path / "draussen"
    outside.mkdir()
    (outside / "._fremd.wav").write_bytes(ad)
    film = tmp_path / "film"
    (film / "Audio Export").mkdir(parents=True)
    os.symlink(outside, film / "Audio Export" / "_alt")
    t = export_target(str(film), "Audio Export", "zeitstempel", "20261111-100000")
    t.finish([])
    assert (outside / "._fremd.wav").exists()
    os.unlink(film / "Audio Export" / "_alt")
    t = export_target(str(film), "Audio Export", "ersetzen", "20261111-110000")
    open(os.path.join(t.copy_dir, "ST.wav"), "wb").write(b"x")
    os.mkdir(os.path.join(t.copy_dir, "._fremd"))                     # Ordner – wird nicht gelöscht
    out = t.finish([os.path.join(t.copy_dir, "ST.wav")])
    assert out == [str(film / "Audio Export" / "ST.wav")] and os.path.isfile(out[0])


def test_neue_uebergabe_auch_nach_dem_ausliefern(tmp_path):
    """Live 11.11.2026: Nach dem Ausliefern kam Fassung 2 – erneutes Zurückliefern braucht die Bestätigung; für
    einen anderen Raum ist es kein Hinweis auf „deine Session“."""
    folder = _film(tmp_path)
    c = _client(tmp_path)
    svc = _svc(c)
    svc.take("p1")
    _import(svc, c)()
    svc.set_state("p1", mixstate.DELIVERED)
    _new_version(folder, 2)
    assert c.get("p1").raw["neue_uebergabe"]
    other = _client(tmp_path, mixer="Anna", room="Ton 1")
    assert not other.get("p1").raw["neue_uebergabe"]


def test_zeitstempel_ordner_den_es_schon_gab(tmp_path):
    """Sicherheit 11.11.2026: Gab es „Audio Export/<Zeit>“ schon, bleiben fremde Begleitdateien darin."""
    ad = b"\x00\x05\x16\x07" + b"\x00" * 60
    film = tmp_path / "film"
    d = film / "Audio Export" / "20261111-100000"
    d.mkdir(parents=True)
    (d / "fremd.wav").write_bytes(b"x")
    (d / "._fremd.wav").write_bytes(ad)
    t = export_target(str(film), "Audio Export", "zeitstempel", "20261111-100000")
    open(os.path.join(t.copy_dir, "ST.wav"), "wb").write(b"x")
    open(os.path.join(t.copy_dir, "._ST.wav"), "wb").write(ad)
    t.finish([os.path.join(t.copy_dir, "ST.wav")])
    assert sorted(os.listdir(d)) == ["._fremd.wav", "ST.wav", "fremd.wav"]


def test_alt_als_verweis_im_modus_ersetzen(tmp_path):
    """Review 11.11.2026: „_alt“ als Verweis – Lieferung abgelehnt, draußen bleibt alles."""
    ad = b"\x00\x05\x16\x07" + b"\x00" * 60
    outside = tmp_path / "draussen"
    outside.mkdir()
    (outside / "._fremd.wav").write_bytes(ad)
    film = tmp_path / "film"
    exp = film / "Audio Export"
    exp.mkdir(parents=True)
    (exp / "ST.wav").write_bytes(b"alt")
    os.symlink(outside, exp / "_alt")
    t = export_target(str(film), "Audio Export", "ersetzen", "20261111-110000")
    open(os.path.join(t.copy_dir, "ST.wav"), "wb").write(b"neu")
    with pytest.raises(UnsafeTarget):
        t.finish([os.path.join(t.copy_dir, "ST.wav")])
    assert sorted(os.listdir(outside)) == ["._fremd.wav"] and (exp / "ST.wav").read_bytes() == b"alt"


def test_ergebnis_nennt_den_nachtrag_auch_ohne_fertigmeldung():
    """Review 11.11.2026: Meldung aus, aber die Sperrdatei hakte – das Ergebnis nennt den Nachtrag trotzdem."""
    import types
    from tonmischung.app.deliver_app import _done_state

    def ctx(active):
        cfg = types.SimpleNamespace(notify_active=active)
        return types.SimpleNamespace(folder_mode=True, helmut=types.SimpleNamespace(
            folder=types.SimpleNamespace(config=cfg)))
    assert _done_state(ctx(False), "ausfall") == "nachtrag" and _done_state(ctx(False), "") == "aus"
    assert _done_state(ctx(True), "ausfall") == "wartet" and _done_state(ctx(True), "") == "gesendet"
