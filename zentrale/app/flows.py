"""Stufe 2 und 3 im Hub: Chat, Belegen, Ereignisse, verteilte Einstellungen, Systemmeldungen.

Nachrichten der Geräte (alle mit `anfrage_id`, außer `chat_empfangen`):
  chat_abholen {}                       → Gespräche und noch nicht zugestellte Nachrichten des Raums
  chat_senden {gespraech, text, dringend} → {id}
  chat_empfangen {ids}                  (Zustellbestätigung; allen zugestellt → gelöscht)
  chat_gelesen {gespraech, bis}
  chat_oeffnen {mit: Raum}              → {id} (Raum an Raum)
  belegen {beitrag_id, titel, schnittraum} → {gewonnen: true} oder fehler „belegt von …“
  freigeben {beitrag_id, titel, grund}
  belegungen_melden {belegungen: [{beitrag_id, titel}]} → {konflikte: […]}
  ereignis {art, beitrag_id, titel, schnittraum, grund}   (Systemmeldungen, „Meine Beiträge“)
  beitraege {}                          → „Meine Beiträge“ des Raums
"""
from __future__ import annotations

import re
from collections.abc import Callable

from app.auth import Device
from app.chat import DEFAULT_NOTICE, KINDS, SYSTEM_KINDS, ChatStore
from app.hub import Hub, Refusal
from app.work import Work, item_message

_CTRL = re.compile(r"[\x00-\x1f\x7f]+")


def one_line(text: str, limit: int = 200) -> str:
    """Für Systemmeldungen: eine Zeile ohne Steuerzeichen (Sicherheit: sonst ließen sich Meldungen fälschen)."""
    return _CTRL.sub(" ", str(text or "")).strip()[:limit]


STEP_OF = {"uebergeben": "uebergeben", "belegt": "belegt", "in_mischung": "in_mischung",
           "audio_export_fertig": "audio_export_fertig", "fertigmeldung": "audio_export_fertig",
           "freigegeben": "uebergeben"}


def chat_settings(db) -> dict:
    arten = db.setting("chat.arten", {}) or {}
    system = db.setting("chat.systemmeldungen", {}) or {}
    return {"aktiv": bool(db.setting("chat.aktiv", True)),
            "arten": {k: bool(arten.get(k, True)) for k in KINDS},
            "systemmeldungen": {k: bool(system.get(k, True)) for k in SYSTEM_KINDS},
            "status_bei_aus": bool(db.setting("chat.status_bei_aus", False)),
            "hinweis": str(db.setting("chat.hinweis", "") or DEFAULT_NOTICE),
            "haltezeit_h": float(db.setting("chat.haltezeit_h", 24) or 24),
            "dringend_ton": bool(db.setting("chat.dringend_ton", True))}


class Flows:
    def __init__(self, hub: Hub, chat: ChatStore, work: Work) -> None:
        self.hub, self.chat, self.work = hub, chat, work
        self.db, self.log, self.auth = hub.auth.db, hub.log, hub.auth
        hub.client_settings = self.client_settings           # type: ignore[method-assign]
        self.watching = lambda: False                         # Ordnerbeobachtung aktiv? (main setzt es)
        self.watched_keys: Callable[[], list[str]] = lambda: []      # welche Ordner (Kennungen) beobachtet werden
        for name in ("chat_abholen", "chat_senden", "chat_empfangen", "chat_gelesen", "chat_oeffnen", "belegen",
                     "freigeben", "belegungen_melden", "ereignis", "beitraege"):
            hub.handlers[name] = getattr(self, name)

    # ── Einstellungen ─────────────────────────────────────────────────────────
    def client_settings(self) -> dict:
        cs = chat_settings(self.db)
        public = {k: cs[k] for k in ("aktiv", "arten", "hinweis", "dringend_ton")}
        # Die Betriebsart gibt die Zentrale immer vor (Jens 13.11.2026: „die Zentrale gibt vor, nicht jeder Mac“) –
        # ohne Eintrag in der Administration gilt die Helmut-API
        werte = {"system.uebergabeordner.betriebsart": "helmut_api", **self.work.folder_values()}
        return {"werte": werte, "chat": public, "ordner_beobachtet": bool(self.watching()),
                "ordner_beobachtet_kennungen": self.watched_keys()}

    async def push_settings(self) -> None:
        await self.hub.broadcast({"typ": "einstellungen", "daten": self.client_settings()})

    async def chat_switched_off(self) -> int:
        n = self.chat.wipe()
        await self.hub.broadcast({"typ": "chat_aus", "daten": {}})
        await self.push_settings()
        return n

    def _rooms(self) -> list[str]:
        return [r["name"] for r in self.auth.rooms()]

    def _enabled(self, kind: str) -> bool:
        cs = chat_settings(self.db)
        return cs["aktiv"] and cs["arten"].get(kind, True)

    async def _deliver(self, msg: dict, rooms: list[str]) -> None:
        await self.hub.broadcast({"typ": "chat_nachricht", "daten": msg}, set(rooms))

    # ── Chat ──────────────────────────────────────────────────────────────────
    async def chat_abholen(self, dev: Device, data: dict) -> dict:
        cs = chat_settings(self.db)
        conns = self.hub.conns.get(dev.id, [])
        for item in self.work.items_of(dev.room):          # „Meine Beiträge“ auch bei ausgeschaltetem Chat
            for conn in conns:
                await conn.send({"typ": "beitrag", "daten": item_message(item)})
        if not cs["aktiv"]:
            return {}
        convs = self.chat.convs_of(dev.room, cs["arten"])
        for c in convs:
            for conn in conns:
                await conn.send({"typ": "chat_gespraech", "daten": self._conv_view(c)})
        visible = {c["id"] for c in convs}
        for m in self.chat.pending_for(dev.room):
            if m["gespraech"] in visible:
                for conn in conns:
                    await conn.send({"typ": "chat_nachricht", "daten": m})
        return {}

    def _conv_view(self, c: dict) -> dict:
        return {"id": c["id"], "art": c["kind"], "titel": c["title"], "beitrag_id": c.get("beitrag", ""),
                "raeume": self.chat.members(c["id"], self._rooms())}

    async def chat_senden(self, dev: Device, data: dict) -> dict:
        conv = self.chat.conv(str(data.get("gespraech") or ""))
        if conv is None or not self._enabled(conv["kind"]):
            raise ValueError("Gespräch nicht verfügbar (Chat oder Gesprächsart ausgeschaltet)")
        members = self.chat.members(conv["id"], self._rooms())
        if dev.room not in members:
            raise ValueError("Dieser Raum gehört nicht zu dem Gespräch")
        text = str(data.get("text") or "").strip()
        if not text:
            raise ValueError("Nachricht ist leer")
        if self.chat.waiting_from(dev.room) > 500:      # je Absender – ein Raum kann den Chat nicht für alle sperren
            raise ValueError("Zu viele wartende Nachrichten aus diesem Raum – bitte später erneut")
        msg = self.chat.add(conv["id"], dev.room, text, members, urgent=bool(data.get("dringend")))
        # Protokoll: nie der Text – nur Räume, Zeit, Länge, ID
        self.log.info("chat", f"Nachricht {msg['id']} ({len(text)} Zeichen) an {', '.join(members)}"
                      f"{' · dringend' if msg['dringend'] else ''}", room=dev.room, device=dev.name)
        await self._deliver(msg, members)
        return {"id": msg["id"]}

    async def chat_empfangen(self, dev: Device, data: dict) -> dict:
        ids = [str(x) for x in (data.get("ids") or []) if x][:500]
        self.chat.delivered(dev.room, ids)
        return {}

    async def chat_gelesen(self, dev: Device, data: dict) -> dict:
        conv = str(data.get("gespraech") or "")[:200]
        if dev.room in self.chat.members(conv, self._rooms()):       # nur eigene Gespräche (Sicherheit: Platz)
            self.chat.set_read(conv, dev.room, str(data.get("bis") or "")[:64])
        return {}

    async def chat_oeffnen(self, dev: Device, data: dict) -> dict:
        if not self._enabled("raum"):
            raise ValueError("Gespräche Raum an Raum sind ausgeschaltet")
        other = str(data.get("mit") or "")
        if other == dev.room or other not in self._rooms():
            raise ValueError("Unbekannter Raum")
        conv, new = self.chat.room_conv(dev.room, other)
        if new:
            await self.hub.broadcast({"typ": "chat_gespraech", "daten": self._conv_view(conv)}, {dev.room, other})
        return {"id": conv["id"]}

    # ── Systemmeldungen und Beiträge ──────────────────────────────────────────
    async def system(self, art: str, beitrag: str, title: str, text: str, rooms: list[str]) -> None:
        text, title = one_line(text, 400), one_line(title)
        cs = chat_settings(self.db)
        if not cs["systemmeldungen"].get(art, True):
            return
        rooms = [r for r in dict.fromkeys(rooms) if r]
        if cs["aktiv"] and cs["arten"].get("beitrag", True):
            conv, added = self.chat.item_conv(beitrag, title, rooms)
            members = self.chat.members(conv["id"], self._rooms())
            if added:
                await self.hub.broadcast({"typ": "chat_gespraech", "daten": self._conv_view(conv)}, set(members))
            msg = self.chat.add(conv["id"], "Ottilie", text, members, system=True)
            await self._deliver(msg, members)
        elif cs["status_bei_aus"]:
            note = {"art": art, "text": text, "beitrag_id": beitrag}
            await self.hub.broadcast({"typ": "systemmeldung", "daten": note}, set(rooms))

    async def _item(self, beitrag: str, step: str, title: str = "", origin: str = "", origin_room: str = "",
                    mixroom: str | None = None) -> dict:
        item = self.work.item(beitrag, step, title, origin, origin_room, mixroom)
        targets = {r for r in (item.get("origin_room"), item.get("mixroom")) if r}
        if targets:
            await self.hub.broadcast({"typ": "beitrag", "daten": item_message(item)}, targets)
        return item

    async def handed_over(self, beitrag: str, title: str, origin: str, source: str, number: int = 0) -> None:
        """Neuer Beitrag (Ordnerbeobachtung 3 A oder Helmut-Ereignis 3 B): an alle melden. Eine höhere Fassung gilt
        wieder als „übergeben“ (Review: sonst sah Ottilie eine neue Fassung erst nach dem Rückfall-Einlesen)."""
        beitrag, title, origin = one_line(beitrag), one_line(title), one_line(origin, 100)
        origin_room = self.auth.room_for_alias(origin) or "" if origin else ""
        if source.startswith("Schnittraum "):            # vom Gerät gemeldet: Herkunft ist immer der eigene Raum
            origin_room = source[len("Schnittraum "):]
        known = self.work.get_item(beitrag)
        known_nr = int((known or {}).get("nr") or 0)
        newer = bool(known and number and known_nr and number > known_nr)   # 0 = unbekannt: nur nachtragen
        if known and number and not known_nr:
            self.work.set_number(beitrag, number)
        if known and known.get("step") != "uebergeben" and not newer:
            return                                               # schon in Arbeit – kein zweites „übergeben“
        if newer:
            known = None                                         # neue Fassung: wie neu melden
        await self._item(beitrag, "uebergeben", title, origin, origin_room, mixroom="")
        if number:
            self.work.set_number(beitrag, number)
        news = {"beitrag_id": beitrag, "titel": title, "schnittraum": origin, "quelle": source}
        await self.hub.broadcast({"typ": "neuer_beitrag", "daten": news})
        if not known:
            self.log.info("beitraege", f"Beitrag übergeben: {beitrag} ({source})", room=origin_room)
            await self.system("uebergeben", beitrag, title,
                              f"„{title or beitrag}“ übergeben" + (f" aus {origin}" if origin else ""),
                              [origin_room] if origin_room else [])

    # ── Belegen ───────────────────────────────────────────────────────────────
    def _mixroom(self, dev: Device) -> None:
        """Belegen, Freigeben und Melden nur aus Mischräumen (Review: eine Web-App im Schnittraum könnte sonst
        beliebige Filme belegen)."""
        row = self.db.one("SELECT kind FROM rooms WHERE id=?", (dev.room_id,))
        if row is None or row["kind"] != "mischraum":
            raise ValueError("Nur Mischräume belegen und geben frei")

    async def belegen(self, dev: Device, data: dict) -> dict:
        self._mixroom(dev)
        beitrag = one_line(data.get("beitrag_id"))
        if not beitrag:
            raise ValueError("beitrag_id fehlt")
        title = one_line(data.get("titel"))
        before = next((c for c in self.work.claims() if c["beitrag"] == beitrag), None)
        won, holder = self.work.claim(beitrag, dev.room, title)
        if not won:
            self.log.info("belegen", f"{beitrag}: abgelehnt, belegt von {holder}", room=dev.room, device=dev.name)
            raise Refusal(f"belegt von {holder} (Zentrale)", belegt_von=holder)
        if before is None:
            self.log.info("belegen", f"{beitrag}: belegt", room=dev.room, device=dev.name)
            origin = str(data.get("schnittraum") or "")
            origin_room = self.auth.room_for_alias(origin) or "" if origin else ""
            await self._item(beitrag, "belegt", title, origin, origin_room, mixroom=dev.room)
            await self.system("belegt", beitrag, title, f"„{title or beitrag}“ belegt von {dev.room}",
                              [origin_room, dev.room])
        return {"gewonnen": True}

    async def freigeben(self, dev: Device, data: dict) -> dict:
        """Freigabe von einem Mac: wirkt nur auf die eigene Belegung (Review: eine alte, eingereihte Freigabe löste
        sonst die Belegung eines anderen Raums). Fremde Belegungen löst die Administration mit Grund."""
        self._mixroom(dev)
        beitrag = one_line(data.get("beitrag_id"))
        holder = next((c["room"] for c in self.work.claims() if c["beitrag"] == beitrag), "")
        if holder and holder != dev.room:
            self.log.info("belegen", f"{beitrag}: Freigabe von {dev.room} übergangen – belegt von {holder}",
                          room=dev.room, device=dev.name)
            return {"uebergangen": True}
        had = self.work.release(beitrag)
        if had:
            reason = one_line(data.get("grund"))
            self.log.info("belegen", f"{beitrag}: freigegeben (war {had})", room=dev.room, device=dev.name)
            item = self.work.get_item(beitrag) or {}
            title = item.get("title") or one_line(data.get("titel"))
            if not data.get("ausgeliefert"):
                await self._item(beitrag, "uebergeben", title, mixroom="")
                await self.system("freigegeben", beitrag, title, f"„{title or beitrag}“ freigegeben von {dev.room}"
                                  + (f": {reason}" if reason else ""), [item.get("origin_room", ""), dev.room])
        return {}

    async def belegungen_melden(self, dev: Device, data: dict) -> dict:
        self._mixroom(dev)
        held = [h for h in (data.get("belegungen") or []) if isinstance(h, dict)][:500]
        conflicts, released = self.work.report(dev.room, held)
        for c in conflicts:
            self.log.warn("belegen", f"Konflikt: {c['beitrag_id']} hier gemeldet, belegt von {c['belegt_von']}",
                          room=dev.room, device=dev.name)
        for b in released:
            self.log.info("belegen", f"{b}: nach der Rückkehr nicht mehr gemeldet – freigegeben", room=dev.room)
        return {"konflikte": conflicts}

    def _kind(self, dev: Device) -> str:
        row = self.db.one("SELECT kind FROM rooms WHERE id=?", (dev.room_id,))
        return row["kind"] if row else ""

    async def ereignis(self, dev: Device, data: dict) -> dict:
        art = str(data.get("art") or "")
        if art not in SYSTEM_KINDS and art != "in_mischung":
            raise ValueError("Unbekanntes Ereignis")
        beitrag = one_line(data.get("beitrag_id"))
        if not beitrag:
            raise ValueError("beitrag_id fehlt")
        title = one_line(data.get("titel"))
        origin = one_line(data.get("schnittraum"), 100)
        if art == "uebergeben":
            if self._kind(dev) != "schnittraum":
                raise ValueError("„übergeben“ melden nur Schnitträume")
            await self.handed_over(beitrag, title, origin, f"Schnittraum {dev.room}")
            return {}
        # Ereignisse zu einem Film nur vom Raum, der ihn belegt hat oder zuletzt mischte (Sicherheit: sonst trat ein
        # beliebiges Gerät dem Beitragsgespräch bei und fälschte Meldungen)
        holder = next((c["room"] for c in self.work.claims() if c["beitrag"] == beitrag), "")
        known = self.work.get_item(beitrag) or {}
        if dev.room not in (holder, known.get("mixroom", "")):
            raise ValueError("Ereignis nur vom Raum, der den Beitrag belegt hat")
        title = known.get("title") or title
        origin_room = self.auth.room_for_alias(origin) or "" if origin else ""
        mixroom = "" if art == "freigegeben" else dev.room          # vom Halter angenommen: er mischt dort
        item = await self._item(beitrag, STEP_OF[art], title, origin, origin_room, mixroom=mixroom)
        texts = {"in_mischung": None,
                 "audio_export_fertig": f"„{title or beitrag}“: Audio Export fertig ({dev.room})",
                 "fertigmeldung": f"„{title or beitrag}“: Fertigmeldung gesendet",
                 "belegt": f"„{title or beitrag}“ belegt von {dev.room}",
                 "freigegeben": f"„{title or beitrag}“ freigegeben von {dev.room}"
                                + (f": {one_line(data.get('grund'))}" if data.get("grund") else "")}
        if texts.get(art):
            await self.system(art, beitrag, title, texts[art], [item.get("origin_room", ""), dev.room])
        return {}

    async def beitraege(self, dev: Device, data: dict) -> dict:
        return {"beitraege": [item_message(i) for i in self.work.items_of(dev.room)]}

    # ── Administration: Räume und Geräte (Jens 13.11.2026) ───────────────────
    def room_busy(self, room: str) -> str:
        """Grund, warum der Raum gerade nicht umziehen oder umbenannt werden darf – oder ""."""
        held = self.work.in_work(room)
        if held:
            names = ", ".join(f"„{t}“" for t in held[:3]) + (" …" if len(held) > 3 else "")
            return f"In {room} ist noch etwas in Arbeit ({names}) – erst zurückliefern oder freigeben"
        st = self.hub.room_status(room)
        if st and st.get("zustand", "frei") != "frei":
            return f"{room} arbeitet gerade ({st.get('beitrag_titel') or st.get('zustand')}) – später erneut"
        return ""

    async def assign_device(self, pid: int, room_id: int, by: str, kennung: str = "") -> Device:
        dev = self.auth.assign(pid, room_id, kennung)
        await self.hub.end_waiting(pid, accepted=True)
        self.log.info("kopplung", f"Wartendes Gerät zugeordnet – {by}", room=dev.room, device=dev.name)
        return dev

    async def reject_device(self, pid: int, by: str) -> None:
        dev = self.auth.reject(pid)
        await self.hub.end_waiting(pid, accepted=False)
        self.log.info("kopplung", f"Anmeldung abgelehnt (Kennung {dev.fingerprint}, {dev.host}) – {by}",
                      device=dev.name)

    async def move_device(self, dev_id: int, room_id: int, by: str) -> Device:
        old = self.auth.device_by_id(dev_id)
        if old.room_id == room_id:
            raise ValueError(f"Das Gerät gehört schon zu {old.room}")
        reason = self.room_busy(old.room)
        if reason:
            raise ValueError(reason)
        self.hub.hold({dev_id})
        try:
            dev = self.auth.move_device(dev_id, room_id)
        finally:
            await self.hub.reconnect(dev_id)
        self.log.info("raeume", f"Gerät von {old.room} nach {dev.room} umgezogen – {by}", room=dev.room,
                      device=dev.name)
        return dev

    async def rename_room(self, room_id: int, new_name: str, by: str) -> str:
        """Raum umbenennen → neuer Name. Nur, wenn dort nichts in Arbeit ist; Belegungen, Beiträge und Chat ziehen mit,
        die Geräte des Raums verbinden neu und bekommen den neuen Namen. Das Protokoll behält den alten."""
        old = self.auth.room_name(room_id)
        if old is None:
            raise ValueError("Raum nicht gefunden")
        reason = self.room_busy(old)
        if reason:
            raise ValueError(reason)
        devices = {c.device.id for conns in list(self.hub.conns.values()) for c in conns
                   if c.device.room_id == room_id}
        self.hub.hold(devices)
        try:
            self.auth.rename_room(room_id, new_name)          # prüft den Namen – scheitert vor jeder Änderung
            new = self.auth.room_name(room_id) or new_name
            self.chat.rename_room(old, new)                   # Altlasten eines gelöschten Raums: ohne Abbruch
            self.work.rename_room(old, new)
        finally:
            for dev_id in devices:                            # auch bei einem Fehler: neu verbinden, Liste senden
                await self.hub.reconnect(dev_id)
            await self.hub.broadcast({"typ": "raeume", "daten": {"raeume": self.hub.overview()}})
        self.log.info("raeume", f"Raum {old} umbenannt in {new} – {by}", room=new)
        return new

    # ── Administration ────────────────────────────────────────────────────────
    async def admin_release(self, beitrag: str, reason: str, by: str) -> str:
        had = self.work.release(beitrag)
        if had:
            self.log.info("belegen", f"{beitrag}: Belegung von {had} gelöst – {reason} ({by})")
            item = self.work.get_item(beitrag) or {}
            await self._item(beitrag, "uebergeben", mixroom="")
            await self.system("freigegeben", beitrag, item.get("title", ""),
                              f"„{item.get('title') or beitrag}“: Belegung von {had} in der Administration gelöst"
                              f" – {reason}", [item.get("origin_room", ""), had])
        return had
