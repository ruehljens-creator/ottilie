"""Mehrere Übergabeordner als ein Client (Jens 13.11.2026): erster Ordner wie bisher, weitere mit „<Kennung>/<Film>“."""
import json

import pytest

from test_dropfolder import _client, _film, _lock, _svc
from tonmischung.core import mixstate
from tonmischung.dropfolder.multi import MultiFolderClient, keys_for
from tonmischung.helmut.client import HelmutError, HelmutUnavailableError


def _multi(tmp_path, entries=None):
    main_root, nord = tmp_path / "haupt", tmp_path / "nord"
    main_root.mkdir()
    nord.mkdir()
    entries = [{"name": "Schnitt Nord", "wurzelordner": str(nord)}] if entries is None else entries
    roots = {e["name"]: e.get("wurzelordner", "") for e in entries}
    return MultiFolderClient(_client(main_root), lambda: entries, lambda e: _client(roots[e["name"]]),
                             lambda: "Haupt"), main_root, nord


def test_kennungen_eindeutig_ohne_schraegstrich():
    assert keys_for([{"name": "Schnitt Nord"}, {"name": "Schnitt-Nord"}, {"name": "///"}]) == \
        ["schnitt-nord", "schnitt-nord-2", "ordner-3"]


def test_filme_aller_ordner_in_einer_liste_mit_herkunft(tmp_path):
    m, main_root, nord = _multi(tmp_path)
    _film(main_root, "p1")
    _film(nord, "p1", bid="n1", titel="Wahl")                            # gleicher Ordnername, anderer Ordner
    by = {c.id: c for c in m.list_for_mixing()}
    assert set(by) == {"p1", "schnitt-nord/p1"}                          # erster Ordner: IDs wie bisher
    assert by["p1"].raw["uebergabeordner"] == "Haupt" and by["schnitt-nord/p1"].raw["uebergabeordner"] == "Schnitt Nord"
    assert m.get("schnitt-nord/p1").title == "Wahl" and m.folder_of("schnitt-nord/p1") == str(nord / "p1")
    with pytest.raises(HelmutError):
        m.get("archiv/p1")                                              # Ordner nicht (mehr) eingestellt


def test_uebernehmen_im_weiteren_ordner_schreibt_dort(tmp_path):
    m, main_root, nord = _multi(tmp_path)
    _film(main_root, "p1")
    folder = _film(nord, "p2", bid="n2")
    taken = _svc(m).take("schnitt-nord/p2")
    assert taken.id == "schnitt-nord/p2" and taken.mix.state == mixstate.TAKEN
    assert _lock(folder)["raum"] == "Ton 3" and not (main_root / "p1" / ".ottilie-sperre.json").exists()
    released = _svc(m).release("schnitt-nord/p2", "Raumwechsel", mixstate.KEEP_NEW)
    assert released.id == "schnitt-nord/p2" and not m.get("schnitt-nord/p2").locked


def test_ein_ordner_weg_die_anderen_bleiben(tmp_path):
    m, main_root, nord = _multi(tmp_path, [{"name": "Archiv", "wurzelordner": str(tmp_path / "gibt-es-nicht")}])
    _film(main_root, "p1")
    assert [c.id for c in m.list_for_mixing()] == ["p1"]
    assert [x["name"] for x in m.problems] == ["Archiv"] and not m.complete
    m.reachable()                                                       # einer genügt
    main_root.rename(tmp_path / "weg")
    with pytest.raises(HelmutUnavailableError):
        m.list_for_mixing()                                             # alle weg: nicht erreichbar


def test_geaenderte_liste_wirkt_ohne_neustart(tmp_path):
    entries = []
    nord = tmp_path / "nord"
    nord.mkdir()
    (tmp_path / "haupt").mkdir()
    m = MultiFolderClient(_client(tmp_path / "haupt"), lambda: entries, lambda e: _client(nord))
    _film(nord, "p9")
    assert m.list_for_mixing() == []
    entries.append({"name": "Nord", "wurzelordner": str(nord)})         # z. B. von der Zentrale verteilt
    assert [c.id for c in m.list_for_mixing()] == ["nord/p9"]
    entries.clear()
    assert m.list_for_mixing() == []


class _Hooks:
    def __init__(self):
        self.calls = []

    def claim(self, beitrag_id, title, origin):
        self.calls.append(("belegen", beitrag_id))

    def release(self, beitrag_id, title="", reason="", delivered=False):
        self.calls.append(("freigeben", beitrag_id))

    def event(self, kind, beitrag_id, title="", origin="", reason=""):
        self.calls.append((kind, beitrag_id))


def test_zentrale_bekommt_die_id_mit_kennung(tmp_path):
    """Prüfrunde 13.11.2026: vorher ging nur „p1“ an die Zentrale – gleichnamige Filme sperrten sich gegenseitig."""
    m, main_root, nord = _multi(tmp_path)
    _film(main_root, "p1")
    _film(nord, "p1", bid="n1")
    hooks = _Hooks()
    m.central = hooks
    _svc(m).take("schnitt-nord/p1")
    _svc(m).take("p1")
    _svc(m).release("schnitt-nord/p1", "Raumwechsel", mixstate.KEEP_NEW)
    assert ("belegen", "schnitt-nord/p1") in hooks.calls and ("belegen", "p1") in hooks.calls
    assert ("freigeben", "schnitt-nord/p1") in hooks.calls and ("freigeben", "p1") not in hooks.calls
    assert m.main.central is hooks                                       # erster Ordner: IDs wie bisher


def test_ausgefallener_ordner_behaelt_seinen_letzten_stand(tmp_path):
    m, main_root, nord = _multi(tmp_path)
    _film(main_root, "p1")
    _film(nord, "p2", bid="n2")
    _svc(m).take("schnitt-nord/p2")
    m.list_for_mixing()                                                 # die Liste liest regelmäßig
    nord.rename(tmp_path / "weg")
    rows = {c.id: c for c in m.list_for_mixing()}
    assert set(rows) == {"p1", "schnitt-nord/p2"} and rows["schnitt-nord/p2"].raw["ordner_nicht_erreichbar"]
    assert rows["schnitt-nord/p2"].mix.mixer == "Jens"                  # bleibt „übernommen“ – nicht frei
    assert not m.complete and m.problems[0]["name"] == "Schnitt Nord"
    with pytest.raises(HelmutError):
        m.get("schnitt-nord/p2")                                        # Aktionen erst, wenn er wieder da ist


def test_liste_aus_mehreren_threads(tmp_path):
    import threading
    entries = []
    (tmp_path / "haupt").mkdir()
    m = MultiFolderClient(_client(tmp_path / "haupt"), lambda: list(entries), lambda e: _client(tmp_path / "haupt"))
    errors = []

    def churn():
        for i in range(200):
            entries[:] = [{"name": f"N{i % 3}", "wurzelordner": "x"}] if i % 2 else []
            try:
                m.folders()
            except Exception as e:  # noqa: BLE001
                errors.append(e)
    threads = [threading.Thread(target=churn) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []


def test_nur_gemeinsames_faellt_auf_den_ersten_ordner(tmp_path):
    m, _main, _nord = _multi(tmp_path)
    assert m.config is m.main.config and m.writable()
    with pytest.raises(AttributeError):
        m.unbekannte_methode_mit_film_id                                # nie still beim ersten Ordner


def test_einstellungen_und_zentrale_werte(tmp_path):
    from tonmischung import factory
    from tonmischung.app import zentrale_app as Z
    from tonmischung.config.settings import Settings
    s = Settings(local_path=tmp_path / "e.json")
    nord = tmp_path / "nord"
    nord.mkdir()
    s.set_local("system.uebergabeordner.wurzelordner", str(tmp_path / "haupt"))
    s.set_local("system.uebergabeordner.weitere_ordner", [{"name": "Nord", "wurzelordner": str(nord)}])
    client = factory.folder_client(s, lambda: ("Jens", "Ton 3"))
    assert [(k, n) for k, n, _c in client.folders()] == [("", "Übergabeordner"), ("nord", "Nord")]
    assert client.folders()[1][2].config.root == str(nord)
    ok = {"werte": {"system.uebergabeordner.weitere_ordner": [{"name": "Süd", "netzadresse": "smb://srv/sued",
                                                               "netz_anmeldung": "kerberos"}]}}
    assert Z.central_values(ok, s) == ok["werte"]
    bad = {"werte": {"system.uebergabeordner.weitere_ordner": [{"name": "Süd", "netzadresse": "http://x"}]}}
    assert Z.central_values(bad, s) == {}
    wrong = {"werte": {"system.uebergabeordner.weitere_ordner": [{"name": "Süd", "netz_anmeldung": "root"}]}}
    assert Z.central_values(wrong, s) == {}
    assert json.dumps(Z.central_values(ok, s))


def test_kaputter_ordner_laesst_sich_entfernen_raumwechsel_wartet(tmp_path):
    """Nachprüfung 13.11.2026: ein dauerhaft fehlender Ordner verhinderte seine eigene Korrektur aus der Zentrale."""
    import types

    from tonmischung.app.commands import mode_switch_blocker, room_switch_blocker
    m, main_root, _nord = _multi(tmp_path, [{"name": "Nord", "wurzelordner": str(tmp_path / "gibt-es-nicht")}])
    _film(main_root, "p1")
    ctx = types.SimpleNamespace(folder_mode=True, room="Ton 3", mixer="Jens", queue=None,
                                helmut=types.SimpleNamespace(folder=m, list_for_mixing=m.list_for_mixing))
    assert mode_switch_blocker(ctx, None) == ""                          # Ordnerliste darf berichtigt werden
    assert "Nord" in room_switch_blocker(ctx, None)                      # der Raumname wartet


def test_nur_weitere_ordner_eingestellt_ist_vollstaendig(tmp_path):
    nord = tmp_path / "nord"
    nord.mkdir()
    _film(nord, "p1")
    m = MultiFolderClient(_client(""), lambda: [{"name": "Nord", "wurzelordner": str(nord)}],
                          lambda e: _client(nord))
    assert [c.id for c in m.list_for_mixing()] == ["nord/p1"] and m.complete


def test_nicht_eingebundener_ordner_heisst_nicht_nur_lesbar(tmp_path):
    m, _main, _nord = _multi(tmp_path, [{"name": "Nord", "wurzelordner": str(tmp_path / "gibt-es-nicht")}])
    assert m.writable()
