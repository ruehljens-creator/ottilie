"""Focusrite Vocaster in Tonmischung (Plan PunchBuddy-Übernahme, Schritt 4) – mit Geräte-Ersatz, ohne USB."""
import itertools
import json

import pytest

from fakes import FakeEngine
from tonmischung.app import vocaster_app as va
from tonmischung.config.settings import Settings, load_schema
from tonmischung.ptsl.client import PtslClient
from tonmischung.punch.commands import PunchCommands
from tonmischung.punch.core import PunchCore


class FakeController:
    def __init__(self, notify=None):
        self.notify = notify
        self.calls = []
        self.routing = None

    def request_autogain(self, ch, label):
        self.calls.append(("autogain", ch, label))

    def set_phantom(self, on, announce=True):
        self.calls.append(("phantom", on))
        if self.notify:
            self.notify("PunchBuddy – Vocaster", "48V eingeschaltet", "Vocaster Two")

    def has_saved_routing(self):
        return self.routing is not None

    def apply_saved_routing(self):
        self.calls.append(("apply",))
        return True, "Routing angewendet"

    def capture_routing_now(self):
        self.routing = {"saved_at": "2026-10-02 22:00:00", "model": "Vocaster Two", "pid": 0x8217}
        return True, "Routing gesichert"

    def saved_routing_info(self):
        return self.routing


def _module(info, tmp_path):
    class Module:
        ROUTING_PATH = str(tmp_path / "vocaster_routing.json")

        class VocasterController(FakeController):
            detect = staticmethod(lambda: info)
    return Module


@pytest.fixture
def cmds(tmp_path):
    settings = Settings(local_path=tmp_path / "e.json")
    core = PunchCore(PtslClient(engine_factory=lambda: FakeEngine()), settings, load_schema())
    restore = core.install()
    uhr = itertools.count(0, 10)
    c = PunchCommands(core, settings, spawn=lambda fn, *a: fn(*a), clock=lambda: next(uhr))
    yield c
    restore()


def test_ohne_geraet_antwort_wie_punchbuddy(cmds, tmp_path):
    voc = va.detect(lambda *a: None, module=_module(None, tmp_path))
    va.add_commands(cmds, voc)
    assert not voc.present
    assert cmds.dispatch("vocaster_phantom_on") == (False, "no vocaster connected")


def test_befehle_mit_geraet_und_titel_ohne_punchbuddy(cmds, tmp_path):
    mitteilungen = []
    voc = va.detect(lambda *a: mitteilungen.append(a), module=_module({"name": "Vocaster Two", "channels": 2}, tmp_path))
    va.add_commands(cmds, voc)
    assert cmds.dispatch("vocaster_autogain_guest") == (True, "vocaster autogain_guest queued")
    assert cmds.dispatch("vocaster_phantom_on") == (True, "vocaster phantom_on queued")
    assert voc.controller.calls == [("autogain", 1, "Guest"), ("phantom", True)]
    assert mitteilungen == [("Ottilie – Vocaster", "48V eingeschaltet", "Vocaster Two")]


def test_vocaster_befehle_auch_waehrend_eines_auftrags(cmds, tmp_path):
    """Der Vocaster hängt nicht an Pro Tools – Belegung durch Holen sperrt ihn nicht."""
    voc = va.detect(lambda *a: None, module=_module({"name": "Vocaster One", "channels": 1}, tmp_path))
    va.add_commands(cmds, voc)
    cmds.busy = lambda: "Holen: Test"
    assert cmds.dispatch("vocaster_phantom_off")[0]


def test_start_optionen(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    voc = va.Vocaster(FakeController(), {"name": "Vocaster Two", "channels": 2})
    voc.controller.routing = {"saved_at": "x", "model": "y"}
    va.apply_start_options(voc, s, lambda *a: None)
    assert voc.controller.calls == []                          # Standard: nichts beim Start
    s.set_local("system.vocaster.phantom_beim_start", True)
    s.set_local("system.vocaster.routing_beim_start", True)
    va.apply_start_options(voc, s, lambda *a: None)
    import time
    for _ in range(50):
        if ("apply",) in voc.controller.calls:
            break
        time.sleep(0.02)
    assert ("phantom", True) in voc.controller.calls and ("apply",) in voc.controller.calls


def test_info_und_routing_sichern(tmp_path):
    from tonmischung.app.bridge import Bridge, CommandError
    modul = _module(None, tmp_path)
    (tmp_path / "vocaster_routing.json").write_text(json.dumps({"saved_at": "2026-09-01 10:00:00", "model": "Vocaster One"}))
    ohne = va.Vocaster()
    assert va.info_view(ohne, module=modul)["routing"]["model"] == "Vocaster One"   # aus PunchBuddy gesichert
    bridge = Bridge(emit=lambda p: None, inline=True)
    va.register(bridge, ohne)
    with pytest.raises(CommandError, match="Kein Vocaster"):
        bridge.call("vocaster_routing_sichern", {})
    mit = va.Vocaster(FakeController(), {"name": "Vocaster Two", "channels": 2})
    bridge2 = Bridge(emit=lambda p: None, inline=True)
    va.register(bridge2, mit)
    r = bridge2.call("vocaster_routing_sichern", {})
    assert r["meldung"] == "Routing gesichert" and r["geraet"] == "Vocaster Two"


def test_vendor_trennt_sicher():
    """PunchBuddys disconnect() hält erst den Poller an, dann USB schließen (sonst SIGSEGV, live 02.10.2026)."""
    import threading
    import time

    from tonmischung.vendor import import_isolated
    vc = import_isolated("vocaster_control")
    calls, im_transfer = [], threading.Event()

    class Lib:
        def libusb_interrupt_transfer(self, *a):
            im_transfer.set()
            calls.append("transfer")
            time.sleep(0.05)
            return -7

        def libusb_release_interface(self, *a):
            calls.append("release")

        def libusb_close(self, *a):
            calls.append("close")
    usb = vc.VocasterUSB.__new__(vc.VocasterUSB)                 # ohne libusb
    usb._lib, usb._handle = Lib(), object()
    usb._start_intr_poller()
    assert im_transfer.wait(2)
    thread = usb._intr_thread
    usb.disconnect()
    assert not thread.is_alive()
    assert calls[-2:] == ["release", "close"]
