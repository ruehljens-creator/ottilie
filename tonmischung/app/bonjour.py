"""Raumstatus per Bonjour (Jens, 03.10.2026): anmelden, die anderen Räume finden und mitlesen.

Über die macOS-Klassen NSNetService/NSNetServiceBrowser (pyobjc, keine neue Abhängigkeit). Alles, was
Bonjour anfasst, läuft im Haupt-Thread (Run-Loop von AppKit); `on_main` reicht Aufrufe dorthin weiter.
Die App öffnet dafür keinen Port – der eingetragene Port (Webtrigger) ist nur eine Angabe.
Grenzen: nur im selben Netzsegment; macOS fragt einmal nach „Lokales Netzwerk“.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from tonmischung.core.peers import SERVICE_TYPE, PeerStatus, from_txt, to_txt

log = logging.getLogger("tonmischung.bonjour")
_delegate_class: Any = None


def _delegate() -> Any:
    """NSObject-Unterklasse erst bei Bedarf anlegen (pyobjc erlaubt jeden Klassennamen nur einmal)."""
    global _delegate_class
    if _delegate_class is not None:
        return _delegate_class
    from Foundation import NSObject

    class OttilieBonjourDelegate(NSObject):
        def netServiceBrowser_didFindService_moreComing_(self, browser, service, more):  # noqa: N802
            self.owner._found(service)

        def netServiceBrowser_didRemoveService_moreComing_(self, browser, service, more):  # noqa: N802
            self.owner._removed(service)

        def netServiceDidResolveAddress_(self, service):  # noqa: N802
            self.owner._update(service, service.TXTRecordData())

        def netService_didUpdateTXTRecordData_(self, service, data):  # noqa: N802
            self.owner._update(service, data)

        def netService_didNotPublish_(self, service, error):  # noqa: N802
            log.warning("Bonjour: Anmeldung nicht möglich: %s", error)

        def netService_didNotResolve_(self, service, error):  # noqa: N802
            log.debug("Bonjour: %s nicht auflösbar: %s", service.name(), error)

    _delegate_class = OttilieBonjourDelegate
    return _delegate_class


class BonjourPeers:
    """PeerSource über Bonjour."""

    def __init__(self, room: str, port: int, on_main: Callable[..., Any], browse: bool = True) -> None:
        self.room, self.port, self.on_main, self.browse = room, int(port or 8899), on_main, browse
        self.available = False
        self._lock = threading.Lock()
        self._peers: dict[str, PeerStatus] = {}          # Dienstname → Status
        self._services: dict[str, Any] = {}             # Dienstname → NSNetService (Referenz halten)
        self._own: Any = None
        self._own_status: PeerStatus | None = None
        self._browser: Any = None
        self._d: Any = None

    # ── Steuerung (aus jedem Thread) ─────────────────────────────────────────
    def start(self) -> None:
        self.on_main(self._start)

    def stop(self) -> None:
        self._own_status = None                         # danach meldet sich die App neu an
        self.on_main(self._stop)

    def publish(self, status: PeerStatus | None) -> None:
        if status == self._own_status and (status is None or self._own is not None):
            return
        self._own_status = status
        self.on_main(self._publish, status)

    def peers(self) -> dict[str, PeerStatus]:
        with self._lock:
            return {s.room: s for s in self._peers.values() if s.room != self.room}

    # ── Haupt-Thread ─────────────────────────────────────────────────────────
    def _start(self) -> None:
        try:
            from Foundation import NSNetServiceBrowser
            self._d = _delegate().alloc().init()
            self._d.owner = self
            if self.browse:
                self._browser = NSNetServiceBrowser.alloc().init()
                self._browser.setDelegate_(self._d)
                self._browser.searchForServicesOfType_inDomain_(SERVICE_TYPE, "local.")
            self.available = True
        except Exception as e:  # noqa: BLE001 – ohne Bonjour läuft der Raumstatus über Helmut weiter
            log.warning("Bonjour nicht verfügbar: %s", e)

    def _stop(self) -> None:
        if self._own is not None:
            self._own.stop()
            self._own = None
        if self._browser is not None:
            self._browser.stop()
            self._browser = None
        for s in self._services.values():
            s.stopMonitoring()
        self._services.clear()
        with self._lock:
            self._peers.clear()
        self.available = False

    def _publish(self, status: PeerStatus | None) -> None:
        from Foundation import NSNetService
        if status is None:
            if self._own is not None:
                self._own.stop()
                self._own = None
            return
        data = NSNetService.dataFromTXTRecordDictionary_(to_txt(status))
        if self._own is None:
            self._own = NSNetService.alloc().initWithDomain_type_name_port_(
                "local.", SERVICE_TYPE, f"Ottilie · {self.room}", self.port)
            if self._d is not None:
                self._own.setDelegate_(self._d)
            self._own.setTXTRecordData_(data)
            self._own.publish()
        else:
            self._own.setTXTRecordData_(data)

    def _found(self, service: Any) -> None:
        name = str(service.name())
        self._services[name] = service
        service.setDelegate_(self._d)
        service.resolveWithTimeout_(5.0)
        service.startMonitoring()

    def _removed(self, service: Any) -> None:
        name = str(service.name())
        s = self._services.pop(name, None)
        if s is not None:
            s.stopMonitoring()
        with self._lock:
            self._peers.pop(name, None)

    def _update(self, service: Any, data: Any) -> None:
        if data is None:
            return
        from Foundation import NSNetService
        try:
            txt = NSNetService.dictionaryFromTXTRecordData_(data) or {}
        except Exception:  # noqa: BLE001
            return
        status = from_txt(dict(txt), host=str(service.hostName() or ""))
        if status is None:
            return
        with self._lock:
            self._peers[str(service.name())] = status
