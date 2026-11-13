"""Ottilie-Seite der Zentrale ohne Netz: Ebene „zentral“, Zwischenspeicher, Prüfung zentraler Werte, Adresse aus der
Kopplung, Belegen über die Zentrale (Review und Sicherheit 11.11.2026)."""
import json
import types

import pytest

from test_dropfolder import _client, _film, _svc
from tonmischung.app import zentrale_app as Z
from tonmischung.config.settings import Settings
from tonmischung.helmut.client import HelmutError
from tonmischung.zentrale import device as D
from tonmischung.zentrale import state as S
from tonmischung.zentrale.hooks import CentralHooks
from tonmischung.zentrale.link import ZentraleRefused, ZentraleUnavailable

MODE = "system.uebergabeordner.betriebsart"


def _ctx(tmp_path, **extra):
    s = Settings(local_path=tmp_path / "einst.json", managed=None)
    return types.SimpleNamespace(settings=s, extra=dict(extra), queue=None, mixer="Jens", room="Ton 3",
                                 helmut=types.SimpleNamespace(list_for_mixing=lambda: []), mode="helmut_api",
                                 folder_mode=False)


def _paired(address="https://z.example:8443"):
    D.write(D.DeviceKey(address, "Ton 3", "Mac", "k" * 43))


def test_zentrale_vorgaben_ersetzen_sich_ganz(tmp_path):
    ctx = _ctx(tmp_path)
    Z.apply_central(ctx, {"system.uebergabeordner.wartezeit_s": 5, "gibt.es.nicht": 1})
    assert ctx.settings.get("system.uebergabeordner.wartezeit_s") == 5
    Z.apply_central(ctx, {"system.uebergabeordner.abfrageintervall_s": 30})
    assert ctx.settings.source("system.uebergabeordner.wartezeit_s") != "zentral"     # nicht mehr vorgegeben


def test_zwischenspeicher_nur_mit_kopplung(tmp_path):
    shared = tmp_path / "Shared"
    shared.mkdir()
    ctx = _ctx(tmp_path, datenordner=shared)
    S.write(shared, S.SETTINGS, D.fingerprint("k" * 43), {"werte": {"system.uebergabeordner.wartezeit_s": 9}})
    Z.load_cached(ctx)                                        # nicht gekoppelt, keine Adresse: wie ohne Zentrale
    assert ctx.settings.get("system.uebergabeordner.wartezeit_s") != 9
    _paired()
    Z.load_cached(ctx)
    assert ctx.settings.get("system.uebergabeordner.wartezeit_s") == 9


def test_zentrale_stand_gilt_fuer_alle_benutzer_aber_nur_fuer_diese_kopplung(tmp_path, monkeypatch):
    """Prüfrunde 13.11.2026: vorher je macOS-Benutzer – ein zweiter Benutzer arbeitete bei ausgefallener Zentrale
    unter dem Rechnernamen."""
    from tonmischung.app import commands
    monkeypatch.setattr(commands, "room_switch_blocker", lambda c, r: "")
    shared = tmp_path / "Shared"
    shared.mkdir()
    _paired()
    anna = _ctx(tmp_path, datenordner=shared)
    Z.adopt_room(anna, "MacBook")
    jens = _ctx(tmp_path, datenordner=shared)
    jens.room = "macmini-3"
    Z.initial_room(jens)
    assert jens.room == "MacBook"
    D.write(D.DeviceKey("https://z.example:8443", "", "Mac", "n" * 43))       # neu angemeldet (anderer Schlüssel)
    other = _ctx(tmp_path, datenordner=shared)
    other.room = "macmini-3"
    Z.initial_room(other)
    assert other.room == "macmini-3" and "raum_ausstehend" not in other.extra


def test_ohne_bekannten_raum_erst_beim_verbinden_und_ohne_arbeit(tmp_path):
    _paired()                                                  # Kopplung kennt „Ton 3“, hier noch nichts gemerkt
    ctx = _ctx(tmp_path, datenordner=tmp_path)
    ctx.room = "macmini-3"
    Z.initial_room(ctx)
    assert ctx.room == "macmini-3" and ctx.extra["raum_ausstehend"] == "Ton 3"


def test_zentrale_werte_werden_geprueft(tmp_path):
    ctx = _ctx(tmp_path)
    got = Z.central_values({"werte": {
        "system.uebergabeordner.wartezeit_s": "viel", "system.uebergabeordner.betriebsart": "irgendwas",
        "system.uebergabeordner.exportordner": "../../draussen", "system.uebergabeordner.netzadresse": "https://x",
        "system.uebergabeordner.netz_schluesselbund_eintrag": "ottilie-helmut",
        "system.uebergabeordner.fertigmeldung.url": "https://boese.example",
        "system.zentrale.adresse": "https://boese.example", "system.uebergabeordner.abfrageintervall_s": 30}},
        ctx.settings)
    assert got == {"system.uebergabeordner.abfrageintervall_s": 30}


def test_betriebsart_nur_wenn_umschalten_erlaubt(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    from tonmischung.app import commands
    monkeypatch.setattr(commands, "mode_switch_blocker", lambda c, r: "„Film“ ist hier noch übernommen")
    Z.apply_central(ctx, {MODE: "uebergabeordner"})
    assert ctx.settings.get(MODE) == "helmut_api"                       # vorgemerkt, nicht umgeschaltet
    monkeypatch.setattr(commands, "mode_switch_blocker", lambda c, r: "")
    Z.apply_central(ctx, {MODE: "uebergabeordner"})
    assert ctx.settings.get(MODE) == "uebergabeordner" and ctx.settings.source(MODE) == "zentral"


def test_adresse_aus_der_kopplung_fuer_alle_benutzer(tmp_path):
    ctx = _ctx(tmp_path)
    assert Z.effective_address(ctx) == ""
    _paired("https://z.example:8443")
    assert Z.effective_address(ctx) == "https://z.example:8443"
    ctx.settings.set_local(Z.ADDRESS, "https://andere.example")
    assert Z.effective_address(ctx) == "https://andere.example"         # Einstellung (z. B. MDM) geht vor


def test_kopplung_im_anmelde_schluesselbund(monkeypatch):
    store = {}
    import tonmischung.config.keychain as K
    monkeypatch.setattr(K, "write_secret", lambda s, a, t: store.__setitem__((s, a), t))
    monkeypatch.setattr(K, "read_secret_framework", lambda s, a: store.get((s, a), ""))
    D.write(D.DeviceKey("https://z.example", "Ton 3", "Mac", "k" * 43), where=D.LOGIN)
    assert (D.SERVICE, D.ACCOUNT) in store and D.read(D.LOGIN).room == "Ton 3"


# ── Belegen über die Zentrale (Betriebsart Übergabeordner) ─────────────────────
class _Link:
    def __init__(self, answer=None, available=True):
        self.answer, self.available, self.sent, self.requests = answer, available, [], []

    def request(self, typ, data, timeout_s=10.0):
        self.requests.append((typ, data))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer or {}

    def send(self, typ, data):
        self.sent.append((typ, data))

    send_any = send


def test_belegen_ohne_zentrale_wie_bisher(tmp_path):
    _film(tmp_path)
    c = _client(tmp_path)
    link = _Link(ZentraleUnavailable("weg"))
    c.central = CentralHooks(link)
    _svc(c).take("p1")
    assert c.get("p1").mix.mixer == "Jens" and link.requests[0][0] == "belegen"


def test_ereignisse_und_freigabe_gehen_an_die_zentrale(tmp_path):
    from test_dropfolder import _import
    _film(tmp_path)
    c = _client(tmp_path)
    link = _Link()
    c.central = CentralHooks(link)
    svc = _svc(c)
    svc.take("p1")
    _import(svc, c)()
    svc.release("p1", "Raumwechsel")
    kinds = [(t, d.get("art", ""), d.get("grund", "")) for t, d in link.sent]
    assert ("ereignis", "in_mischung", "") in kinds and ("freigeben", "", "Raumwechsel") in kinds


def test_andere_ablehnung_der_zentrale_heisst_nicht_belegt(tmp_path):
    _film(tmp_path)
    c = _client(tmp_path)
    c.central = CentralHooks(_Link(ZentraleRefused("Unbekannte Nachricht „belegen“", {})))
    _svc(c).take("p1")                                          # ältere Zentrale: Sperrdatei wie bisher
    assert c.get("p1").mix.mixer == "Jens"


def test_belegt_nur_mit_angabe(tmp_path):
    _film(tmp_path)
    c = _client(tmp_path)
    c.central = CentralHooks(_Link(ZentraleRefused("belegt von Ton 1 (Zentrale)", {"belegt_von": "Ton 1"})))
    with pytest.raises(HelmutError, match="schon in Ton 1 belegt"):
        _svc(c).take("p1")


def test_sperrdatei_scheitert_dann_zentrale_zuruecknehmen(tmp_path, monkeypatch):
    from tonmischung.dropfolder import lockfile as LF
    _film(tmp_path)
    c = _client(tmp_path)
    link = _Link({"gewonnen": True})
    c.central = CentralHooks(link)

    def taken(*a, **k):
        raise LF.LockTaken("Raum B war schneller")
    monkeypatch.setattr(LF, "claim", taken)
    with pytest.raises(HelmutError):
        _svc(c).take("p1")
    assert any(t == "freigeben" for t, _ in link.sent)


def test_abgleich_meldet_alle_belegungen_des_raums(tmp_path):
    from tonmischung.core.mixstate import MixState
    rows = [types.SimpleNamespace(id="a", title="A", mix=MixState(state="importiert", mixer="Anna", room="Ton 3")),
            types.SimpleNamespace(id="b", title="B", mix=MixState(state="importiert", mixer="Jens", room="Ton 1"))]
    ctx = _ctx(tmp_path)
    ctx.mode = "uebergabeordner"
    ctx.helmut = types.SimpleNamespace(folder=types.SimpleNamespace(list_for_mixing=lambda: rows))
    link = _Link({"konflikte": []})
    Z.report_claims(ctx, link, lambda *a: None)
    assert link.requests[0][1]["belegungen"] == [{"beitrag_id": "a", "titel": "A"}]   # anderer Mischer, gleicher Raum


# ── Raum aus der Zentrale (Jens 13.11.2026) ─────────────────────────────────────
def test_raum_der_zentrale_erst_wenn_nichts_in_arbeit(tmp_path, monkeypatch):
    _paired()
    ctx = _ctx(tmp_path)
    from tonmischung.app import commands
    monkeypatch.setattr(commands, "room_switch_blocker", lambda c, r: "„Film“ ist hier noch übernommen")
    Z.adopt_room(ctx, "MacBook")
    timer = ctx.extra.get("raum_zeitgeber")
    assert ctx.room == "Ton 3" and ctx.extra["raum_ausstehend"] == "MacBook" and timer is not None
    for _ in range(4):                                     # jedes Wiederverbinden: kein weiterer Zeitgeber
        Z.adopt_room(ctx, "MacBook")
    assert ctx.extra.get("raum_zeitgeber") is timer
    timer.cancel()
    monkeypatch.setattr(commands, "room_switch_blocker", lambda c, r: "")
    Z.adopt_room(ctx, "MacBook")
    assert ctx.room == "MacBook" and "raum_ausstehend" not in ctx.extra and Z.last_room(ctx) == "MacBook"


def test_raumwechsel_wartet_auch_auf_andere_mischer_und_unlesbare_quellen(tmp_path):
    from tonmischung.app.commands import room_switch_blocker
    from tonmischung.core.mixstate import MixState
    from tonmischung.helmut.client import HelmutUnavailableError
    ctx = _ctx(tmp_path)
    ctx.folder_mode = False
    rows = [types.SimpleNamespace(id="a", title="A", mix=MixState(state="importiert", mixer="Anna", room="Ton 3"))]
    ctx.helmut = types.SimpleNamespace(list_for_mixing=lambda: rows)
    assert "„A“" in room_switch_blocker(ctx, None)             # anderer Mischer desselben Raums

    def down():
        raise HelmutUnavailableError("weg")
    ctx.helmut = types.SimpleNamespace(list_for_mixing=down)
    assert "nicht erreichbar" in room_switch_blocker(ctx, None)   # Helmut weg: unbekannt = in Arbeit


def test_start_mit_zuletzt_bekanntem_raum_nur_wenn_gekoppelt(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    from tonmischung.app import commands
    monkeypatch.setattr(commands, "room_switch_blocker", lambda c, r: "")
    _paired()
    Z.adopt_room(ctx, "MacBook")                           # zuletzt bekannt …
    D.write(D.DeviceKey("https://z.example:8443", "Ton 3", "Mac", "k" * 43))
    ctx.room = "macbookpro"
    ctx.settings.set_local(Z.ADDRESS, "https://andere.example")
    Z.initial_room(ctx)
    assert ctx.room == "macbookpro"                        # … gilt aber nur mit Kopplung an diese Adresse
    ctx.settings.set_local(Z.ADDRESS, "https://z.example:8443")
    Z.initial_room(ctx)
    assert ctx.room == "MacBook"


def test_anzeige_mit_geraetekennung_und_raum(tmp_path):
    ctx = _ctx(tmp_path)
    _paired()
    got = Z.info(ctx)
    assert got["kennung"] == D.fingerprint("k" * 43) and len(got["kennung"]) == 6
    assert got["raum"] == "Ton 3" and got["wartet"] is False and got["raum_ausstehend"] == ""


def test_geraetekennung_wie_auf_der_zentrale():
    from app.auth import fingerprint as server_fingerprint     # Zentrale (pythonpath zentrale/)
    for key in ("a" * 43, "Zx-9_kL" * 6, ""):
        assert D.fingerprint(key) == server_fingerprint(key)


def test_betriebsart_gibt_die_zentrale_vor(tmp_path):
    """Jens 13.11.2026: Welche Betriebsart gilt, gibt die Zentrale vor – nicht jeder Mac (Schicht „zentral“)."""
    from tonmischung.app.bridge import Bridge
    from tonmischung.app.commands import _register_settings
    ctx = _ctx(tmp_path)
    bridge = Bridge(emit=lambda p: None, inline=True)
    _register_settings(bridge, ctx, None)
    bridge.call("einstellung_setzen", {"path": MODE, "value": "uebergabeordner"})        # ohne Vorgabe: geht
    assert ctx.settings.get(MODE) == "uebergabeordner"
    Z.apply_central(ctx, {MODE: "helmut_api"})
    view = bridge.call("einstellung_setzen", {"path": MODE, "value": "uebergabeordner"})
    assert ctx.settings.get(MODE) == "helmut_api" and view["errors"]
    fields = {f["path"]: f for g in bridge.call("einstellungen_abschnitt", {"id": "system.uebergabeordner"})["groups"]
              for f in g["fields"]}
    assert fields[MODE]["locked"]


def test_ordnerliste_der_zentrale_erst_ohne_arbeit(tmp_path, monkeypatch):
    from tonmischung.app import commands
    ctx = _ctx(tmp_path)
    more = "system.uebergabeordner.weitere_ordner"
    Z.apply_central(ctx, {more: [{"name": "Nord", "netzadresse": "smb://a/b"}]})
    monkeypatch.setattr(commands, "mode_switch_blocker", lambda c, r: "„Film“ ist hier noch übernommen")
    got = Z.apply_central(ctx, {more: [{"name": "Nord-Ost", "netzadresse": "smb://a/b"}]})
    assert ctx.settings.get(more)[0]["name"] == "Nord" and got[more][0]["name"] == "Nord"   # vorgemerkt
    ctx.extra["zentrale_zeitgeber"].cancel()
    monkeypatch.setattr(commands, "mode_switch_blocker", lambda c, r: "")
    Z.apply_central(ctx, {more: [{"name": "Nord-Ost", "netzadresse": "smb://a/b"}]})
    assert ctx.settings.get(more)[0]["name"] == "Nord-Ost"


def test_kein_abgleich_wenn_ein_ordner_fehlt(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.mode = "uebergabeordner"
    ctx.helmut = types.SimpleNamespace(folder=types.SimpleNamespace(
        list_with_problems=lambda: ([], [{"name": "Nord", "meldung": "weg"}])))
    link = _Link({"konflikte": []})
    assert Z.report_claims(ctx, link, lambda *a: None) == [] and link.requests == []
