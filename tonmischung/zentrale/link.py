"""Verbindung zur Zentrale (WebSocket, eigener Thread) – Raumstatus, Anfragen mit Antwort, Ereignisse.

- Verbindet nur, wenn eine Adresse eingestellt ist und der Mac für genau diese Adresse gekoppelt ist.
- Erste Nachricht `anmelden` mit dem Geräteschlüssel; danach Raumstatus senden und empfangen.
- Abbruch: neu verbinden mit wachsendem Abstand (2 s … 60 s); 90 s ohne Nachricht gilt als tot.
- `request()` wartet auf die Antwort zur `anfrage_id` (atomares Belegen, Stufe 2). Gibt der Aufrufer auf (Zeitlimit,
  Abbruch), wird die Anfrage verworfen und nie später doch noch gesendet (Review 11.11.2026: sonst belegte die
  Zentrale nach dem Wiederverbinden für einen Raum, der sich für nicht belegt hält). Einfache Meldungen (`send`)
  gehen bei einem Abbruch verloren – Belegungen gleicht Ottilie nach dem Verbinden vollständig ab.
- Als `PeerSource`: `peers()` liefert den Status der anderen verbundenen Räume, `room_names()` alle Räume der
  Zentrale, `publish()` den eigenen Status.
- Ohne Zentrale bleibt alles wie vorher: `available` ist False, `request()` wirft `ZentraleUnavailable`.
- Wartend (ohne Code angemeldet, noch keinem Raum zugeordnet): Zustand „wartet“ mit Gerätekennung; nach dem Zuordnen
  schließt die Zentrale mit 4409 und Ottilie verbindet sofort neu. Ebenso nach Umziehen und Umbenennen.
- Der Raum der Zentrale (`server_room`) geht mit `zentrale_raum` an die App – sie übernimmt ihn für alles.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from tonmischung.core.peers import FREE, STATES, PeerStatus
from tonmischung.zentrale import device as D

log = logging.getLogger("tonmischung.zentrale")

OFF, UNPAIRED, OTHER_ADDRESS, CONNECTING, CONNECTED, DISCONNECTED, REVOKED, WAITING = (
    "aus", "nicht_gekoppelt", "andere_adresse", "verbindet", "verbunden", "getrennt", "abgemeldet", "wartet")
# Texte sagen, was zu tun ist (Tontechniker 13.11.2026) – „Kennung“ ist der Code, den die Administration vergleicht
STATE_TEXT = {OFF: "ohne Zentrale", UNPAIRED: "Zentrale: Mac noch nicht angemeldet – Einstellungen → Zentrale",
              OTHER_ADDRESS: "Zentrale: für eine andere Adresse angemeldet – neu anmelden",
              CONNECTING: "Zentrale: verbinde …", CONNECTED: "Zentrale verbunden",
              DISCONNECTED: "Zentrale nicht erreichbar – Ottilie arbeitet mit dem letzten Raum weiter",
              REVOKED: "Zentrale kennt diesen Mac nicht mehr (abgelehnt, abgelaufen oder abgemeldet) – neu anmelden",
              WAITING: "Zentrale: Mac wartet auf Zuordnung – Administration anrufen"}
DEAD_S = 90.0
PING_S = 30.0
RECONNECT = 4409                                     # Zentrale: „bitte neu verbinden“ (Zuordnung, Umzug, Umbenennen)


class ZentraleUnavailable(Exception):
    """Zentrale nicht verbunden – Aufrufer fallen auf das bisherige Verfahren zurück."""


class ZentraleRefused(Exception):
    """Zentrale hat die Anfrage abgelehnt (`fehler`) – Text für die Anzeige, `data` mit Zusatzangaben."""

    def __init__(self, text: str, data: dict | None = None) -> None:
        super().__init__(text)
        self.data = data or {}


def _ws_url(address: str) -> str:
    return ("wss://" + address[len("https://"):] if address.startswith("https://")
            else "ws://" + address[len("http://"):]) + "/ws"


def to_status(p: PeerStatus | None) -> dict:
    """Eigener Status für die Zentrale – ohne Mischernamen (Räume statt Personen)."""
    if p is None:
        return {"zustand": FREE}
    return {"zustand": p.state, "beitrag_id": p.contribution, "beitrag_titel": p.title, "fortschritt": int(p.pct),
            "betriebsart": p.mode, "taetigkeit": p.state}


def from_status(d: dict) -> PeerStatus | None:
    room = str(d.get("raum") or "")
    if not room or not d.get("verbunden"):
        return None
    state = d.get("zustand") if d.get("zustand") in STATES else FREE
    try:
        pct = max(0, min(100, int(d.get("fortschritt") or 0)))
    except (TypeError, ValueError):
        pct = 0                                      # fremde Daten: nie die Raumübersicht kosten
    return PeerStatus(room, "", state, str(d.get("beitrag_titel") or "")[:200], str(d.get("beitrag_id") or "")[:200],
                      pct, mode=str(d.get("betriebsart") or "")[:40])


class ZentraleLink:
    def __init__(self, address: Callable[[], str], room: str, on_event: Callable[[str, dict], None] = lambda k, d: None,
                 read_device: Callable[[], D.DeviceKey | None] = D.read, connect: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._address, self.room, self.on_event = address, room, on_event
        self._read_device, self._connect = read_device, connect
        self.clock = clock
        self.state, self.detail = OFF, ""
        self.server_room = ""
        self.fingerprint = ""                              # Gerätekennung, solange das Gerät wartet
        self._rooms: dict[str, dict] = {}
        self._own: dict | None = None
        self._out: queue.Queue = queue.Queue()
        self._waiting: dict[str, tuple[threading.Event, list]] = {}
        self._dropped: set[str] = set()                    # aufgegebene Anfragen – nie mehr senden
        self._delay = 2.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.settings: dict = {}

    # ── PeerSource ────────────────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        return self.state == CONNECTED

    def peers(self) -> dict[str, PeerStatus]:
        with self._lock:
            rooms = list(self._rooms.values())
        out = {}
        for d in rooms:
            p = from_status(d)
            if p is not None and p.room != self.server_room:
                out[p.room] = p
        return out

    label = "Zentrale"

    def room_names(self) -> list[str]:
        """Alle Räume der Zentrale (auch nicht verbundene) – leer ohne Verbindung."""
        with self._lock:
            return sorted(self._rooms) if self.available else []

    def publish(self, status: PeerStatus | None) -> None:
        data = to_status(status)
        if data == self._own:
            return
        self._own = data
        if self.available:
            self.send("status", data)

    def text(self) -> str:
        if self.state == WAITING and self.fingerprint:
            return f"{STATE_TEXT[WAITING]} und Code {self.fingerprint} nennen"
        return STATE_TEXT.get(self.state, "")            # technische Details nur ins Protokoll (Mischer M1)

    # ── Senden und Anfragen ───────────────────────────────────────────────────
    def send(self, typ: str, data: dict, rid: str | None = None) -> str:
        rid = rid or uuid.uuid4().hex
        self._out.put({"typ": typ, "anfrage_id": rid, "daten": data})
        return rid

    def request(self, typ: str, data: dict, timeout_s: float = 10.0) -> dict:
        """Anfrage mit Antwort (ok → Daten; fehler → ZentraleRefused; nicht verbunden/Zeit um → ZentraleUnavailable)."""
        if not self.available:
            raise ZentraleUnavailable(self.text())
        rid = uuid.uuid4().hex
        ev, box = threading.Event(), []
        self._waiting[rid] = (ev, box)
        try:
            self.send(typ, data, rid)
            if not ev.wait(timeout_s) or not box:
                self._dropped.add(rid)
                raise ZentraleUnavailable("Zentrale antwortet nicht")
            msg = box[0]
        finally:
            self._waiting.pop(rid, None)
        if msg.get("typ") == "fehler":
            data = msg.get("daten") or {}
            raise ZentraleRefused(str(data.get("text") or "abgelehnt"), data)
        return msg.get("daten") or {}

    # ── Lauf ──────────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="tm-zentrale", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def reconnect_now(self) -> None:
        """⌘R / nach dem Koppeln: sofort neu versuchen."""
        self._wake.set()

    def send_any(self, typ: str, data: dict) -> str:
        """Senden, auch während eines kurzen Abbruchs einer bestehenden Kopplung (geht nach dem Wiederverbinden
        hinaus – nur für Freigaben). Ohne Kopplung oder Adresse: nichts einreihen (Review)."""
        if self.state in (CONNECTED, CONNECTING, DISCONNECTED):
            return self.send(typ, data)
        return ""

    def _set(self, state: str, detail: str = "") -> None:
        if (state, detail) != (self.state, self.detail):
            self.state, self.detail = state, detail
            self.on_event("zentrale", {"zustand": state, "text": self.text()})

    def _run(self) -> None:
        while not self._stop.is_set():
            address = (self._address() or "").strip()
            dev = self._read_device()
            if not address:
                self._set(OFF)
            elif dev is None:
                self._set(UNPAIRED)
            elif not dev.matches(address):
                self._set(OTHER_ADDRESS)
            else:
                try:
                    self._session(dev)
                except _Revoked:
                    self._set(REVOKED)
                except Exception as e:  # noqa: BLE001 – jede Störung: ohne Zentrale weiter, später erneut
                    code = getattr(getattr(e, "rcvd", None), "code", None)
                    if code == 4401:
                        self._set(REVOKED)                 # Zentrale schloss mit „nicht gekoppelt“
                    elif code == RECONNECT:
                        self._delay = 0.25                 # zugeordnet, umgezogen, umbenannt: gleich neu verbinden
                        self._set(CONNECTING)
                    else:
                        log.info("Zentrale nicht verbunden: %s", type(e).__name__)
                        self._set(DISCONNECTED, type(e).__name__)
                self._fail_waiting()
                self._drop_queued()
                with self._lock:
                    self._rooms.clear()
            self._wake.wait(self._delay if self.state != OFF else 10.0)
            self._wake.clear()
            self._delay = min(max(self._delay, 1.0) * 2, 60.0)

    def _drop_queued(self) -> None:
        """Nach einem Abbruch: Wartendes verwerfen (Status wird neu gesendet, Belegungen neu abgeglichen) – außer
        Freigaben: die gehen nach dem Wiederverbinden noch hinaus (Review: sonst blieb eine Belegung hängen)."""
        keep = []
        while True:
            try:
                msg = self._out.get_nowait()
            except queue.Empty:
                break
            if msg.get("typ") == "freigeben" and msg.get("anfrage_id") not in self._dropped:
                keep.append(msg)
        for msg in keep[-100:]:
            self._out.put(msg)
        self._dropped.clear()

    def _fail_waiting(self) -> None:
        for ev, _box in list(self._waiting.values()):
            ev.set()                                       # ohne Antwort → ZentraleUnavailable

    def _session(self, dev: D.DeviceKey) -> None:
        if self.state != DISCONNECTED:                     # beim Wiederholen nicht zwischen „verbinde“ und
            self._set(CONNECTING)                          # „nicht erreichbar“ flackern
        connect = self._connect or _default_connect
        with connect(_ws_url(dev.address)) as ws:
            ws.send(json.dumps({"typ": "anmelden", "daten": {"schluessel": dev.key}}))
            last_in = last_out = self.clock()
            while not self._stop.is_set():
                if self._wake.is_set() and self.state == CONNECTED:
                    self._wake.clear()                     # ⌘R bei bestehender Verbindung: nichts zu tun
                try:
                    raw = ws.recv(timeout=0.5)
                except TimeoutError:
                    raw = None
                now = self.clock()
                if raw is not None:
                    last_in = now
                    self._receive(json.loads(raw))
                elif now - last_in > DEAD_S:
                    raise TimeoutError("keine Nachricht seit 90 s")
                while True:
                    try:
                        msg = self._out.get_nowait()
                    except queue.Empty:
                        break
                    if msg.get("anfrage_id") in self._dropped:
                        self._dropped.discard(msg["anfrage_id"])
                        continue
                    if self.state != CONNECTED and msg["typ"] != "status":
                        self._out.put(msg)                 # erst nach dem Willkommen
                        break
                    ws.send(json.dumps(msg))
                    last_out = now
                if now - last_out > PING_S:
                    ws.send(json.dumps({"typ": "lebenszeichen", "daten": {}}))
                    last_out = now

    def _receive(self, msg: dict) -> None:
        typ, data = msg.get("typ"), msg.get("daten") or {}
        if typ == "wartet":
            self.fingerprint = str(data.get("kennung") or "")[:12]
            self._delay = 2.0
            self._set(WAITING)
        elif typ == "willkommen":
            self.server_room = str(data.get("raum") or "")
            self.fingerprint = ""
            with self._lock:
                self._rooms = {r["raum"]: r for r in data.get("raeume", []) if isinstance(r, dict) and r.get("raum")}
            self.settings = dict(data.get("einstellungen") or {})
            self._delay = 2.0                              # verbunden: nach dem nächsten Abbruch wieder schnell
            self._set(CONNECTED)
            if self.server_room:
                self.on_event("zentrale_raum", {"raum": self.server_room})
            self.on_event("zentrale_einstellungen", self.settings)
            if self._own is not None:
                self.send("status", self._own)
        elif typ == "raumstatus":
            if data.get("raum"):
                with self._lock:
                    self._rooms[data["raum"]] = data
                self.on_event("raumstatus", data)
        elif typ == "raeume":                              # Raum umbenannt: ganze Übersicht neu
            with self._lock:
                self._rooms = {r["raum"]: r for r in data.get("raeume", []) if isinstance(r, dict) and r.get("raum")}
            self.on_event("raumstatus", {})
        elif typ == "neu_verbinden":
            pass                                           # die Zentrale schließt gleich mit 4409
        elif typ == "einstellungen":
            self.settings = dict(data)
            self.on_event("zentrale_einstellungen", self.settings)
        elif typ == "abgemeldet":
            raise _Revoked()
        elif typ in ("ok", "fehler"):
            waiter = self._waiting.get(str(msg.get("anfrage_id") or ""))
            if waiter is not None:
                waiter[1].append(msg)
                waiter[0].set()
        elif typ == "lebenszeichen":
            pass
        else:
            self.on_event(str(typ), data)                  # Chat, Beiträge, Systemmeldungen (Stufe 2/3)


class _Revoked(Exception):
    pass


def _default_connect(url: str):
    from websockets.sync.client import connect
    return connect(url, ssl=D.tls_context() if url.startswith("wss://") else None, open_timeout=10,
                   close_timeout=2, max_size=1 << 20, user_agent_header="Ottilie")
