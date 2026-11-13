"""Verbindungen der Geräte (WebSocket) und Raumstatus.

Nachrichten sind JSON: `{"typ": …, "anfrage_id": …, "daten": {…}}`. Die erste Nachricht eines Geräts ist
`anmelden` mit dem Geräteschlüssel (Browser können beim WebSocket keine Kopfzeilen setzen). Jede Anfrage mit
`anfrage_id` wird genau einmal verarbeitet; eine Wiederholung (nach Verbindungsabbruch) bekommt dieselbe Antwort.
Lebenszeichen alle 30 s in beide Richtungen.

Raumstatus: was ein Raum über sich meldet (Betriebsart, Beitrag, Tätigkeit, Fortschritt, Pro Tools offen) – nur
im Arbeitsspeicher, an alle verbundenen Geräte. Keine Personen: Mischernamen werden nicht angenommen.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.auth import Auth, Device, PendingDevice
from app.log import Log

HEARTBEAT_S = 30.0
LOGIN_TIMEOUT_S = 10.0
MAX_MESSAGE = 64 * 1024
DEDUPE = 1000
MAX_CONNS = 4                                       # je Gerät (z. B. mehrere angemeldete macOS-Benutzer)
STATUS_FIELDS = {"betriebsart": str, "zustand": str, "beitrag_id": str, "beitrag_titel": str, "fortschritt": int,
                 "protools": bool, "taetigkeit": str}
STATES = ("frei", "uebernommen", "importiert", "mischt", "exportiert")
RECONNECT = 4409                                    # Schließen „bitte neu verbinden“ (Zuordnung, Umzug, Umbenennen)
WAITING_TEXT = "Wartet auf Zuordnung in der Administration der Zentrale"


class Refusal(ValueError):
    """Abgelehnt mit Zusatzangaben für den Client (z. B. `belegt_von`) – nur dann gilt „belegt“ (Review)."""

    def __init__(self, text: str, **data: Any) -> None:
        super().__init__(text)
        self.data = data


def clean_status(data: Any) -> dict:
    """Nur bekannte Felder, Typen erzwungen, Texte gekürzt."""
    out: dict = {}
    if not isinstance(data, dict):
        return out
    for key, kind in STATUS_FIELDS.items():
        v = data.get(key)
        if v is None:
            continue
        if kind is int:
            try:
                out[key] = max(0, min(100, int(v)))
            except (TypeError, ValueError):
                continue
        elif kind is bool:
            out[key] = bool(v)
        else:
            out[key] = str(v)[:200]
    if out.get("zustand") not in STATES:
        out["zustand"] = "frei"
    return out


@dataclass
class Conn:
    device: Device
    send: Callable[[dict], Awaitable[None]]
    close: Callable[[int], Awaitable[None]]
    since: float = field(default_factory=time.time)
    host: str = ""
    moving: bool = False            # Raum wird gerade geändert (Umziehen, Umbenennen): nichts mehr annehmen


@dataclass
class Waiting:
    """Verbindung eines wartenden Geräts: bekommt nur „wartet“ – keinen Raumstatus, keinen Chat, nichts."""
    pending: PendingDevice
    send: Callable[[dict], Awaitable[None]]
    close: Callable[[int], Awaitable[None]]
    host: str = ""


class Hub:
    def __init__(self, auth: Auth, log: Log, clock: Callable[[], float] = time.time) -> None:
        self.auth, self.log, self.clock = auth, log, clock
        self.conns: dict[int, list[Conn]] = {}          # Geräte-ID → Verbindungen
        self.waiting: dict[int, list[Waiting]] = {}     # wartendes Gerät → Verbindungen (sehen nichts)
        self.status: dict[int, tuple[int, dict]] = {}   # Verbindung → (Gerät, Status) – zwei Benutzer an einem Mac
        self._conn: Conn | None = None                  # gerade bearbeitete Verbindung (asyncio: eine zur Zeit)
        self._buckets: dict[int, dict] = {}
        self._done: dict[int, OrderedDict] = {}         # Geräte-ID → anfrage_id → Antwort
        self.handlers: dict[str, Callable[[Device, dict], Awaitable[dict | None]]] = {"status": self._status}
        self.loop: asyncio.AbstractEventLoop | None = None     # für Aufrufe aus anderen Threads (Ordnerbeobachtung)

    # ── Raumübersicht ─────────────────────────────────────────────────────────
    def room_status(self, room: str) -> dict:
        """Status eines Raums: von seinen verbundenen Geräten der aktivste (nicht „frei“ vor „frei“), sonst der
        jüngste – zwei angemeldete macOS-Benutzer an einem Mac überschreiben sich nicht (Review 11.11.2026)."""
        live = [st for dev_id, st in self.status.values()
                if self.conns.get(dev_id) and self.conns[dev_id][0].device.room == room]
        if not live:
            return {}
        return max(live, key=lambda st: (st.get("zustand") != "frei", st.get("zeit", 0)))

    def connected_rooms(self) -> set[str]:
        return {c[0].device.room for c in self.conns.values() if c}

    def overview(self) -> list[dict]:
        online = self.connected_rooms()
        out = []
        for r in self.auth.rooms():
            st = self.room_status(r["name"])
            out.append({"raum": r["name"], "art": r["kind"], "verbunden": r["name"] in online, **st})
        return out

    async def broadcast(self, msg: dict, rooms: set[str] | None = None) -> None:
        for conns in list(self.conns.values()):
            for c in list(conns):
                if rooms is None or c.device.room in rooms:
                    try:
                        await c.send(msg)
                    except Exception:  # noqa: BLE001 – eine tote Verbindung hält die anderen nicht auf
                        pass

    # ── Verbindung ────────────────────────────────────────────────────────────
    async def attach(self, conn: Conn) -> None:
        self.loop = asyncio.get_running_loop()
        if len(self.conns.get(conn.device.id, [])) >= MAX_CONNS:
            raise ValueError("zu viele Verbindungen dieses Geräts")
        self.conns.setdefault(conn.device.id, []).append(conn)
        self.auth.seen(conn.device.id)
        self.log.info_limited(f"conn:{conn.device.id}", "verbindung", f"Gerät verbunden ({conn.host or '?'})",
                              room=conn.device.room,
                              device=conn.device.name)
        await conn.send({"typ": "willkommen", "daten": {
            "raum": conn.device.room, "geraet": conn.device.name, "server_zeit": self.clock(),
            "raeume": self.overview(), "einstellungen": self.client_settings()}})
        await self.broadcast({"typ": "raumstatus", "daten": self._room_entry(conn.device.room)})

    async def detach(self, conn: Conn) -> None:
        conns = self.conns.get(conn.device.id, [])
        if conn in conns:
            conns.remove(conn)
        self.status.pop(id(conn), None)
        if not conns:
            self.conns.pop(conn.device.id, None)
            self._done.pop(conn.device.id, None)
        self.log.info_limited(f"disc:{conn.device.id}", "verbindung", "Gerät getrennt", room=conn.device.room,
                              device=conn.device.name)
        if self.auth.room_id(conn.device.room) is not None:     # nach dem Umbenennen kein Geisterraum (Programmierer)
            await self.broadcast({"typ": "raumstatus", "daten": self._room_entry(conn.device.room)})

    # ── Wartende Geräte ──────────────────────────────────────────────────────
    async def attach_waiting(self, w: Waiting) -> None:
        self.loop = asyncio.get_running_loop()
        conns = self.waiting.setdefault(w.pending.id, [])
        if len(conns) >= MAX_CONNS:
            raise ValueError("zu viele Verbindungen dieses Geräts")
        conns.append(w)
        await w.send({"typ": "wartet", "daten": {"kennung": w.pending.fingerprint, "geraet": w.pending.name,
                                                 "text": WAITING_TEXT}})

    def detach_waiting(self, w: Waiting) -> None:
        conns = self.waiting.get(w.pending.id, [])
        if w in conns:
            conns.remove(w)
        if not conns:
            self.waiting.pop(w.pending.id, None)

    async def end_waiting(self, pid: int, accepted: bool) -> None:
        """Zugeordnet: schließen mit „neu verbinden“ (dann Willkommen mit Raum); abgelehnt: „abgemeldet“."""
        for w in list(self.waiting.get(pid, [])):
            try:
                if not accepted:
                    await w.send({"typ": "abgemeldet", "daten": {"text": "Anmeldung abgelehnt"}})
                await w.close(RECONNECT if accepted else 4401)
            except Exception:  # noqa: BLE001
                pass

    def hold(self, dev_ids: set[int]) -> None:
        """Vor dem Ändern des Raums: diese Geräte nehmen nichts mehr an, bis sie neu verbunden sind."""
        for dev_id in dev_ids:
            for c in self.conns.get(dev_id, []):
                c.moving = True

    async def reconnect(self, dev_id: int) -> None:
        """Raum des Geräts geändert: Verbindungen schließen – das Gerät verbindet neu und bekommt den neuen Raum."""
        for c in list(self.conns.get(dev_id, [])):
            try:
                await c.send({"typ": "neu_verbinden", "daten": {}})
                await c.close(RECONNECT)
            except Exception:  # noqa: BLE001
                pass

    async def revoke(self, dev_id: int) -> None:
        """Gerät abgemeldet: alle seine Verbindungen schließen (gilt für alle Benutzer des Macs)."""
        for c in list(self.conns.get(dev_id, [])):
            try:
                await c.send({"typ": "abgemeldet", "daten": {}})
                await c.close(4401)
            except Exception:  # noqa: BLE001
                pass

    def _room_entry(self, room: str) -> dict:
        return next((r for r in self.overview() if r["raum"] == room), {"raum": room, "verbunden": False})

    def client_settings(self) -> dict:
        """Zentrale Einstellungen für die Geräte (Stufe 2 füllt sie)."""
        return dict(self.auth.db.setting("verteilt", {}) or {})

    # ── Nachrichten ───────────────────────────────────────────────────────────
    async def handle(self, conn: Conn, raw: str) -> None:
        if len(raw) > MAX_MESSAGE:
            await conn.send({"typ": "fehler", "daten": {"text": "Nachricht zu groß"}})
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            await conn.send({"typ": "fehler", "daten": {"text": "Kein JSON"}})
            return
        if not isinstance(msg, dict):
            return
        kind, rid = msg.get("typ"), msg.get("anfrage_id")
        if not self._allow(conn, str(kind)):
            await conn.send({"typ": "fehler", "anfrage_id": str(rid or "")[:100],
                             "daten": {"text": "Zu viele Nachrichten – bitte kurz warten"}})
            return
        self.auth.seen(conn.device.id)
        if kind == "lebenszeichen":
            return
        rid = str(rid)[:100] if rid not in (None, "") else ""
        done = self._done.setdefault(conn.device.id, OrderedDict())
        if rid and rid in done:
            await conn.send(done[rid])                  # Wiederholung: gleiche Antwort, nichts doppelt
            return
        handler = self.handlers.get(str(kind))
        if conn.moving and handler is not None:
            # Zwischen Umbenennen/Umziehen und dem Neuverbinden gälte sonst noch der alte Raumname (Programmierer
            # 13.11.2026: eine Belegung landete unter einem Raum, den es nicht mehr gibt)
            answer = {"typ": "fehler", "anfrage_id": rid, "daten": {"text": "Raum wird gerade geändert – Ottilie "
                                                                            "verbindet gleich neu"}}
            await conn.send(answer)
            return
        if handler is None:
            answer = {"typ": "fehler", "anfrage_id": rid, "daten": {"text": f"Unbekannte Nachricht „{str(kind)[:40]}“"}}
        else:
            try:
                self._conn = conn
                daten = msg.get("daten")
                data = await handler(conn.device, daten if isinstance(daten, dict) else {})
                answer = {"typ": "ok", "anfrage_id": rid, "daten": data or {}}
            except ValueError as e:
                answer = {"typ": "fehler", "anfrage_id": rid, "daten": {"text": str(e), **getattr(e, "data", {})}}
        self.log.debug("nachricht", f"{kind} {rid}", room=conn.device.room, device=conn.device.name)
        if rid and answer["typ"] == "ok":                  # nur Erfolge merken (Fehler dürfen wiederholt werden)
            done[rid] = answer
            while len(done) > DEDUPE:
                done.popitem(last=False)
        await conn.send(answer)

    def _allow(self, conn: Conn, kind: str) -> bool:
        """Bremse je Verbindung: 30 Nachrichten in 10 s, Chat 60 je Minute (Sicherheit: Platz und Protokoll)."""
        t = self.clock()
        b = self._buckets.setdefault(conn.device.id, {"all": [], "chat": []})      # je Gerät, nicht je Verbindung
        b["all"] = [x for x in b["all"] if x > t - 10][-60:]
        if kind in ("lebenszeichen", "chat_empfangen", "chat_gelesen"):
            return True                                 # Bestätigungen nie bremsen (Review: sonst scheiterte Belegen)
        if len(b["all"]) >= 30:
            return False
        b["all"].append(t)
        if kind == "chat_senden":
            b["chat"] = [x for x in b["chat"] if x > t - 60]
            if len(b["chat"]) >= 60:
                return False
            b["chat"].append(t)
        return True

    async def _status(self, device: Device, data: dict) -> dict:
        st = clean_status(data)
        st["zeit"] = self.clock()
        key = id(self._conn) if self._conn is not None and self._conn.device.id == device.id else device.id
        self.status[key] = (device.id, st)
        await self.broadcast({"typ": "raumstatus", "daten": self._room_entry(device.room)})
        return {}

    async def heartbeat(self) -> None:
        """Alle 30 s: Lebenszeichen an alle (hält Proxys offen, zeigt tote Verbindungen) – auch an wartende Geräte,
        sonst hielten sie die Verbindung nach 90 s für tot."""
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            await self.ping_all()

    async def ping_all(self) -> None:
        msg = {"typ": "lebenszeichen", "daten": {"zeit": self.clock()}}
        await self.broadcast(msg)
        for conns in list(self.waiting.values()):
            for w in list(conns):
                try:
                    await w.send(msg)
                except Exception:  # noqa: BLE001
                    pass
