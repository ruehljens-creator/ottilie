"""Gemeinsamer Nachtrag aller Benutzer eines Macs (Prüfrunde 13.11.2026: Ausfall, Sicherheit)."""
import json

from tonmischung.core import catchup as C
from tonmischung.core.catchup import ASSET, DONE, LOCK, OPEN, CatchUpQueue, Entry
from tonmischung.helmut import catchup as R
from tonmischung.helmut.catchup import replay
from tonmischung.helmut.client import HelmutUnavailableError


class _Folder:
    """Übergabeordner-Client mit Fertigmeldung; `down`: nicht erreichbare Ordner-Kennungen."""
    mode = "uebergabeordner"

    def __init__(self, note="ziel-a", down=()):
        self.note, self.down, self.locks, self.sent = note, set(down), [], []

    def notify_target(self):
        return self.note

    def set_locked(self, pid, locked):
        if R.folder_key(pid) in self.down:
            raise HelmutUnavailableError("Übergabeordner nicht erreichbar")
        self.locks.append(pid)

    def assets(self, pid):
        return []

    def add_asset(self, pid, path, kind):
        self.sent.append(pid)
        return {"meldung_id": "m1"}


def _as(monkeypatch, user):
    monkeypatch.setattr(C, "current_user", lambda: user)
    monkeypatch.setattr(R, "current_user", lambda: user)


def _queue(tmp_path, client):
    q = CatchUpQueue(tmp_path / "nachtrag.json")
    q.target = lambda: ("uebergabeordner", client.note)
    return q


def test_eintrag_traegt_urheber_und_ziel(tmp_path, monkeypatch):
    _as(monkeypatch, "anna")
    q = _queue(tmp_path, _Folder())
    e = q.add(Entry(ASSET, "p1", "Film", path="/x/meta.json", asset_kind="metadaten"))
    lock = q.add_lock("p2", "Film 2", True)
    assert (e.benutzer, e.betriebsart, e.meldung) == ("anna", "uebergabeordner", "ziel-a")
    assert (lock.benutzer, lock.meldung) == ("anna", "")
    _as(monkeypatch, "jens")
    s = q.summary()
    assert s["fremde"] == ["anna"] and {x["benutzer"] for x in s["eintraege"]} == {"anna"}


def test_takt_traegt_nur_eigene_nach_im_fenster_auch_fremde(tmp_path, monkeypatch):
    client = _Folder()
    _as(monkeypatch, "anna")
    q = _queue(tmp_path, client)
    q.add_lock("p1", "Film 1", True)
    _as(monkeypatch, "jens")
    q.add_lock("p2", "Film 2", True)
    report = replay(q, client, only_own=True)
    assert client.locks == ["p2"] and report.waiting == 1 and "anderen Benutzer" in report.text
    replay(q, client)                                                    # „Jetzt nachtragen“ im Fenster
    assert client.locks == ["p2", "p1"] and q.pending() == []


def test_fertigmeldung_nur_mit_gleichem_ziel_nie_ungesendet_erledigt(tmp_path, monkeypatch):
    _as(monkeypatch, "anna")
    q = _queue(tmp_path, _Folder(note="ziel-a"))
    q.add(Entry(ASSET, "p1", "Film", path="/x/meta.json", asset_kind="metadaten"))
    _as(monkeypatch, "jens")
    off = _Folder(note="")                                               # bei Jens abgeschaltet
    replay(q, off)
    e = q.pending()[0]
    assert off.sent == [] and e.status == OPEN and "nicht eingerichtet" in e.message and "anna" in e.message
    other = _Folder(note="ziel-b")                                       # bei Jens ein anderes Ziel
    replay(q, other)
    assert other.sent == [] and "anders eingerichtet" in q.pending()[0].message
    same = _Folder(note="ziel-a")
    replay(q, same)
    assert same.sent == ["p1"] and q.pending() == []


def test_andere_betriebsart_wartet(tmp_path, monkeypatch):
    _as(monkeypatch, "jens")
    q = CatchUpQueue(tmp_path / "nachtrag.json")
    q.target = lambda: ("helmut_api", "")
    q.add_lock("p1", "Film", True)
    client = _Folder()
    replay(q, client)
    assert client.locks == [] and "Betriebsart „Helmut-API“" in q.pending()[0].message


def test_ausgefallener_ordner_haelt_die_anderen_nicht_auf(tmp_path, monkeypatch):
    _as(monkeypatch, "jens")
    client = _Folder(down={"nord"})
    q = _queue(tmp_path, client)
    q.add_lock("nord/p1", "Film Nord", True)
    q.add_lock("nord/p3", "Film Nord 2", True)
    q.add_lock("p2", "Film Haupt", True)
    report = replay(q, client)
    assert client.locks == ["p2"] and report.unavailable and "Übergabeordner weiterhin" in report.text
    assert {e.project_id for e in q.pending()} == {"nord/p1", "nord/p3"}
    assert all(e.attempts == 0 for e in q.pending())                     # Ausfall zählt nicht als Versuch


def test_fremde_felder_in_der_datei_brechen_nichts_ab(tmp_path):
    path = tmp_path / "nachtrag.json"
    path.write_text(json.dumps({"version": 1, "eintraege": [
        {"kind": "mischung", "project_id": "p1", "changes": {"state": "ausgeliefert", "boese": 1}},
        {"kind": LOCK, "project_id": "p2", "changes": "kaputt", "status": DONE}]}))
    q = CatchUpQueue(path)
    assert [e.changes for e in q.entries()] == [{"state": "ausgeliefert"}, {}]


def test_ohne_urheber_nicht_von_selbst_und_vor_uebernehmen_nur_der_eigene_film(tmp_path, monkeypatch):
    """Nachprüfung 13.11.2026: ein leerer Urheber galt als „eigen“; settle() trug fremde Einträge anderer Filme nach."""
    client = _Folder()
    _as(monkeypatch, "anna")
    q = _queue(tmp_path, client)
    q.add(Entry(LOCK, "p9", "Fremd", locked=True, benutzer="?"))
    raw = json.loads((tmp_path / "nachtrag.json").read_text())
    raw["eintraege"][0]["benutzer"] = ""                                 # von Hand eingetragen
    (tmp_path / "nachtrag.json").write_text(json.dumps(raw))
    q.add_lock("p1", "Eigen", True)
    replay(q, client, only_own=True)
    assert client.locks == ["p1"]
    replay(q, client, project_id="p2")
    assert client.locks == ["p1"]                                        # anderer Film: nichts


def test_helmut_client_ohne_betriebsart_traegt_keine_ordner_eintraege_nach(tmp_path, monkeypatch):
    _as(monkeypatch, "jens")
    q = _queue(tmp_path, _Folder())
    q.add_lock("nord/p1", "Film", True)

    class _Api(_Folder):
        mode = ""
    api = _Api()
    replay(q, api)
    assert api.locks == [] and "Übergabeordner" in q.pending()[0].message


def test_umzug_traegt_den_urheber_ein(tmp_path, monkeypatch):
    from tonmischung.core import machine_data as MD
    monkeypatch.setattr(C, "current_user", lambda: "anna")
    old = {"eintraege": [{"id": "a", "kind": LOCK, "project_id": "p1", "created": "1"}]}
    merged = MD.MERGE[MD.CATCH_UP]({"eintraege": []}, old)
    assert merged["eintraege"][0]["benutzer"] == "anna"
