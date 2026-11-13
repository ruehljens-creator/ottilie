"""Zentrale im Fenster: Verbindung, Kopplung, zentrale Einstellungen, Chat (Plan Zentrale, 11.11.2026).

- Die Verbindung (`ZentraleLink`) startet immer; ohne Adresse oder Kopplung bleibt sie aus (dann kein Raumstatus,
  kein Chat). Sie ist zugleich die Quelle des Raumstatus (`PeerSource`).
- Zentrale Einstellungen landen in der Schicht „zentral“ (MDM geht vor, lokal danach). Der zuletzt empfangene Stand
  bleibt rechnerweit im gemeinsamen Datenordner (`tonmischung/zentrale/state.py`) – ohne Zentrale gilt er weiter, für
  alle Benutzer des Macs. Die Betriebsart gibt die Zentrale immer vor (Jens 13.11.2026); gesperrt ist sie dadurch, dass
  sie in der Schicht „zentral“ steht.
- Chat lebt nur im Arbeitsspeicher dieses Prozesses (nie auf Platte, nie im Protokoll, nie über Socket/MCP): nach
  einem Neustart von Ottilie ist er leer.
- Raum (Jens 13.11.2026): Ist der Mac gekoppelt, gilt der Raum der Zentrale für alles (Helmut, Sperrdatei, Nachtrag,
  Mischungsverzeichnis, Fertigmeldung). Ein neuer Name (Zuordnen, Umziehen, Umbenennen) gilt erst, wenn hier nichts
  in Arbeit ist (`commands.room_switch_blocker`). Der zuletzt bekannte Raum steht rechnerweit mit der Gerätekennung
  und gilt weiter, wenn die Zentrale ausfällt.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from pathlib import Path

from tonmischung.paths import support_dir
from tonmischung.zentrale import device as D
from tonmischung.zentrale import state as S
from tonmischung.zentrale.link import (CONNECTED, OFF, OTHER_ADDRESS, REVOKED, UNPAIRED, WAITING, ZentraleLink,
                                       ZentraleRefused, ZentraleUnavailable)

log = logging.getLogger("tonmischung.zentrale")
ADDRESS = "system.zentrale.adresse"
MODE = "system.uebergabeordner.betriebsart"
# Ändert die Zentrale einen dieser Werte, gilt er erst, wenn hier nichts in Arbeit ist (Ausfall 13.11.2026: sonst
# verschwand ein übernommener Film mitten in der Arbeit aus der Liste, oder seine ID zeigte auf einen anderen Ordner)
FOLDER_SHAPE = (MODE, "system.uebergabeordner.name", "system.uebergabeordner.weitere_ordner",
                "system.uebergabeordner.wurzelordner", "system.uebergabeordner.netzadresse",
                "system.uebergabeordner.netz_anmeldung", "system.uebergabeordner.ordner_ignorieren",
                "system.uebergabeordner.uebergabe_json", "system.uebergabeordner.exportordner")
_timers = threading.Lock()
# Was die Zentrale vorgeben darf (Administration → Übergabeordner); alles andere bleibt lokal bzw. MDM
CENTRAL_KEYS = ("system.uebergabeordner.betriebsart", "system.uebergabeordner.name",
                "system.uebergabeordner.weitere_ordner", "system.uebergabeordner.wurzelordner",
                "system.uebergabeordner.netzadresse", "system.uebergabeordner.netz_anmeldung",
                "system.uebergabeordner.uebergabe_json",
                "system.uebergabeordner.wartezeit_s", "system.uebergabeordner.abfrageintervall_s",
                "system.uebergabeordner.ordner_ignorieren", "system.uebergabeordner.exportordner",
                "system.uebergabeordner.erneute_lieferung", "system.uebergabeordner.sperre_verwaist_h")
# Bewusst nicht zentral (Sicherheit 11.11.2026): Namen von Schlüsselbund-Einträgen und die Fertigmeldung (Adresse,
# Anmeldung) – eine gekaperte Zentrale könnte sonst ein Geheimnis des Macs an eine Adresse ihrer Wahl lenken.


def central_values(raw: Any, settings: Any = None) -> dict:
    """Nur erlaubte Schlüssel aus den verteilten Einstellungen der Zentrale – mit `settings` auch nur gültige Werte
    (Schema: Typ, Auswahl, Grenzen). Ungültiges wird verworfen und protokolliert (Sicherheit 11.11.2026)."""
    from tonmischung.config.settings import validate
    werte = raw.get("werte") if isinstance(raw, dict) else None
    if not isinstance(werte, dict):
        return {}
    out = {}
    for k, v in werte.items():
        if k not in CENTRAL_KEYS:
            continue
        if settings is not None and k in settings.fields:
            problems = validate(settings.fields[k].node, v, k)
            if problems:
                log.warning("Zentrale Einstellung verworfen: %s", "; ".join(problems))
                continue
        if k in ("system.uebergabeordner.exportordner", "system.uebergabeordner.uebergabe_json") and \
                not _plain_name(v):
            log.warning("Zentrale Einstellung verworfen: Exportordner „%s“ ist kein einfacher Name", v)
            continue
        if k == "system.uebergabeordner.netzadresse" and v and not str(v).lower().startswith("smb://"):
            log.warning("Zentrale Einstellung verworfen: Netzadresse nur smb://")
            continue
        if k == "system.uebergabeordner.weitere_ordner" and any(
                str(e.get("netzadresse") or "") and not str(e["netzadresse"]).lower().startswith("smb://")
                for e in v if isinstance(e, dict)):
            log.warning("Zentrale Einstellung verworfen: weitere Übergabeordner nur mit smb://-Netzadresse")
            continue
        if k == "system.uebergabeordner.weitere_ordner" and any(
                isinstance(e, dict) and e.get("netz_anmeldung") == "schluesselbund" for e in v):
            # Schlüsselbund nur für Netzadressen vom Mac selbst (MDM/lokal) – sonst lenkte eine gekaperte Zentrale
            # das Kennwort an einen Server ihrer Wahl (IT-Administrator, Sicherheit 13.11.2026)
            log.warning("Zentrale Einstellung verworfen: weitere Übergabeordner nicht mit Anmeldung „Schlüsselbund“")
            continue
        out[k] = v
    return out


def _plain_name(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip()) and "/" not in v and "\\" not in v and not v.startswith(".")


# ── Raum aus der Zentrale ──────────────────────────────────────────────────────
def _state_dir(ctx: AppContext) -> Path:
    """Gemeinsamer Datenordner des Macs (sonst der Ottilie-Ordner des Benutzers)."""
    return Path(ctx.extra.get("datenordner") or support_dir())


def _kennung(dev: D.DeviceKey | None = None) -> str:
    dev = dev if dev is not None else D.read()
    return dev.fingerprint if dev is not None else ""


def last_room(ctx: AppContext, dev: D.DeviceKey | None = None) -> str:
    """Zuletzt von der Zentrale bekannter Raum dieses Macs (dieser Kopplung) – "" ohne."""
    return str(S.read(_state_dir(ctx), S.ROOM, _kennung(dev), legacy=support_dir()).get("raum") or "")


def _remember_room(ctx: AppContext, room: str) -> None:
    S.write(_state_dir(ctx), S.ROOM, _kennung(), {"raum": room})


def initial_room(ctx: AppContext) -> None:
    """Beim Start: gekoppelt → zuletzt bekannter Raum dieses Macs. Kennt der Mac ihn noch nicht (erster Start nach
    dem Umstieg, neu angemeldet), bleibt der bisherige Raum, bis die Zentrale ihn nennt – übernommen wird er dann wie
    jeder neue Name erst, wenn nichts in Arbeit ist (Tontechniker 13.11.2026)."""
    dev = D.read()
    if dev is None or not dev.matches(effective_address(ctx, dev)):
        return
    room = last_room(ctx, dev)
    if room:
        if room != ctx.room:
            log.info("Raum aus der Zentrale (zuletzt bekannt): %s", room)
            ctx.room = room
    elif dev.room and dev.room != ctx.room:
        ctx.extra["raum_ausstehend"] = dev.room


def adopt_room(ctx: AppContext, room: str) -> None:
    """Raum der Zentrale übernehmen – aber erst, wenn hier nichts in Arbeit ist (ein übernommener Beitrag hinge sonst
    am alten Namen). Bis dahin vorgemerkt, alle 30 s neu geprüft (höchstens ein Zeitgeber)."""
    room = str(room or "").strip()
    if not room:
        return
    if room == ctx.room:
        ctx.extra.pop("raum_ausstehend", None)
        if last_room(ctx) != room:
            _remember_room(ctx, room)
        return
    try:
        from tonmischung.app.commands import room_switch_blocker
        blocker = room_switch_blocker(ctx, ctx.extra.get("runner"))
    except Exception as e:  # noqa: BLE001
        blocker = str(e)
    if blocker:
        ctx.extra["raum_ausstehend"] = room
        log.info("Raum „%s“ aus der Zentrale vorgemerkt, noch nicht übernommen: %s", room, blocker)
        _later(ctx, "raum_zeitgeber", lambda c: adopt_room(c, str(c.extra.get("raum_ausstehend") or "")))
    else:
        log.info("Raum aus der Zentrale: %s (bisher %s)", room, ctx.room)
        ctx.room = room
        ctx.extra.pop("raum_ausstehend", None)
        _remember_room(ctx, room)
    bridge = ctx.extra.get("bridge")
    if bridge is not None:
        bridge.event("zentrale", info(ctx))


def _later(ctx: AppContext, key: str, fn: Callable[[AppContext], None], delay_s: float = 30.0) -> None:
    """Höchstens ein wartender Zeitgeber je Zweck (Review, Programmierer 13.11.2026: vorher entfernte der Aufruf
    seinen eigenen Eintrag vorab – jedes Wiederverbinden startete eine weitere Kette). Der Eintrag verschwindet erst,
    wenn der Zeitgeber auslöst."""
    with _timers:
        old = ctx.extra.get(key)
        if old is not None and old.is_alive():
            return

        def fire() -> None:
            with _timers:
                if ctx.extra.get(key) is timer:
                    ctx.extra.pop(key, None)
            fn(ctx)
        timer = threading.Timer(delay_s, fire)
        timer.daemon = True
        ctx.extra[key] = timer
        timer.start()


def _retry_central(ctx: AppContext) -> None:
    """Vorgemerkte Betriebsart: mit dem zuletzt empfangenen Stand der Zentrale erneut anwenden, dann das Fenster
    benachrichtigen."""
    link = ctx.extra.get("zentrale")
    data = ctx.extra.get("zentrale_settings")
    if link is not None and link.state == CONNECTED and data:
        apply_central(ctx, central_values(data, ctx.settings))
        bridge = ctx.extra.get("bridge")
        if bridge is not None:
            bridge.event("zentrale", info(ctx))


def apply_central(ctx: AppContext, values: dict) -> dict:
    """Zentrale Werte in die Schicht „zentral“. Betriebsart und Übergabeordner (`FOLDER_SHAPE`) nur, wenn gerade
    nichts in Arbeit ist (sonst bleiben die bisherigen Werte, bis es so weit ist) – wie beim Umschalten von Hand."""
    changed = [k for k in FOLDER_SHAPE if values.get(k) != ctx.settings.get(k) and (k in values or ctx.settings.source(
        k) == "zentral")]
    if changed:
        try:
            from tonmischung.app.commands import mode_switch_blocker
            blocker = mode_switch_blocker(ctx, ctx.extra.get("runner"))
        except Exception as e:  # noqa: BLE001
            blocker = str(e)
        if blocker:
            log.info("Übergabeordner/Betriebsart aus der Zentrale vorgemerkt, noch nicht übernommen (%s): %s",
                     ", ".join(changed), blocker)
            _later(ctx, "zentrale_zeitgeber", _retry_central)      # nach dem Auftrag erneut (ein Zeitgeber)
            prev = ctx.extra.get("zentrale_werte") or {}
            values = {k: v for k, v in values.items() if k not in changed}
            values.update({k: prev[k] for k in changed if k in prev})
    ctx.settings.update_central(values)
    ctx.extra["zentrale_werte"] = values
    return values


def load_cached(ctx: AppContext) -> None:
    """Beim Start: zuletzt empfangene zentrale Einstellungen (ohne Zentrale gelten sie weiter) – nur, wenn der Mac
    noch gekoppelt und eine Adresse da ist (sonst wie ohne Zentrale)."""
    if not effective_address(ctx):
        return
    data = S.read(_state_dir(ctx), S.SETTINGS, _kennung(), legacy=support_dir())
    if data.get("werte"):
        ctx.extra["zentrale_werte"] = values = central_values(data, ctx.settings)
        ctx.settings.update_central(values)


class Chat:
    """Gespräche und Nachrichten nur im Speicher (je Gespräch höchstens 500 Nachrichten)."""

    MAX = 500

    def __init__(self) -> None:
        self.convs: dict[str, dict] = {}
        self.msgs: dict[str, list[dict]] = {}
        self.unread: dict[str, int] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self.convs.clear()
            self.msgs.clear()
            self.unread.clear()

    def conversation(self, c: dict) -> None:
        with self._lock:
            self.convs[str(c.get("id"))] = c

    def drop(self, cid: str) -> None:
        with self._lock:
            self.convs.pop(cid, None)
            self.msgs.pop(cid, None)
            self.unread.pop(cid, None)

    def add(self, m: dict, own_room: str) -> bool:
        cid = str(m.get("gespraech"))
        with self._lock:
            lst = self.msgs.setdefault(cid, [])
            if any(x.get("id") == m.get("id") for x in lst):
                return False
            lst.append(m)
            del lst[:-self.MAX]
            if m.get("von") != own_room:
                self.unread[cid] = self.unread.get(cid, 0) + 1
        return True

    def read(self, cid: str) -> str:
        with self._lock:
            self.unread.pop(cid, None)
            lst = self.msgs.get(cid) or []
            return str(lst[-1]["id"]) if lst else ""

    def view(self) -> dict:
        with self._lock:
            return {"gespraeche": list(self.convs.values()), "nachrichten": {k: list(v) for k, v in self.msgs.items()},
                    "ungelesen": dict(self.unread)}


def setup(ctx: AppContext, bridge: Bridge,
          notify: Callable[[str, str, bool], None] = lambda t, x, sound: None, runner: Any = None) -> ZentraleLink:
    """Verbindung anlegen (noch nicht gestartet); sie ist zugleich die Quelle des Raumstatus."""
    ctx.extra["runner"] = runner
    ctx.extra["bridge"] = bridge
    load_cached(ctx)
    chat = Chat()
    ctx.extra["chat"] = chat

    def on_event(kind: str, data: dict) -> None:
        if kind == "zentrale_raum":
            adopt_room(ctx, str(data.get("raum") or ""))
            return
        if kind == "zentrale" and data.get("zustand") in (OFF, UNPAIRED, OTHER_ADDRESS, REVOKED):
            clear_central(ctx)                           # entkoppelt/ohne Adresse: Vorgaben fallen weg – nie mitten
            chat.clear()                                 # in einem Auftrag (dann später, Mischer H4)
        if kind == "zentrale" and data.get("zustand") == CONNECTED:
            threading.Thread(target=report_claims, args=(ctx, link, notify), name="tm-zentrale-belegungen",
                             daemon=True).start()
            link.send("chat_abholen", {})                # Gespräche, Wartendes, „Meine Beiträge“ (Review)
        if kind == "zentrale_einstellungen":
            ctx.extra["zentrale_settings"] = data
            values = apply_central(ctx, central_values(data, ctx.settings))
            S.write(_state_dir(ctx), S.SETTINGS, _kennung(), {"werte": values})   # nur Einstellungen, nie Chat
            on = bool((data.get("chat") or {}).get("aktiv"))
            was_on = ctx.extra.get("chat_war_an", on)
            ctx.extra["chat_war_an"] = on
            if not on:
                chat.clear()
            elif link.state == CONNECTED and not was_on:
                link.send("chat_abholen", {})            # Chat wieder eingeschaltet: Gespräche holen (nur dann)
            bridge.event("zentrale", info(ctx))
            return
        if kind == "chat_gespraech":
            chat.conversation(data)
        elif kind == "chat_gespraech_weg":
            chat.drop(str(data.get("id")))
        elif kind == "chat_aus":
            chat.clear()

        elif kind == "chat_nachricht":
            if not chat.add(data, link.server_room):
                return
            link.send("chat_empfangen", {"ids": [data.get("id")]})
            if data.get("von") != link.server_room:
                threading.Thread(target=_announce, args=(ctx, data, notify), name="tm-chat-hinweis",
                                 daemon=True).start()   # PTSL-Abfrage nie im Verbindungs-Thread
            bridge.event("chat", {"neu": data, "ungelesen": chat.view()["ungelesen"]})
            return
        bridge.event("zentrale_" + kind if not kind.startswith(("zentrale", "raumstatus")) else kind, data)

    link = ZentraleLink(lambda: effective_address(ctx), ctx.room, on_event)
    ctx.extra["zentrale"] = link
    from tonmischung.zentrale.hooks import CentralHooks
    folder = getattr(ctx.helmut, "folder", None)
    if folder is not None:                          # Betriebsart Übergabeordner: Belegen über die Zentrale
        folder.central = CentralHooks(link)
    return link


def _announce(ctx: AppContext, data: dict, notify: Callable[[str, str, bool], None]) -> None:
    urgent = bool(data.get("dringend"))
    sender = "Ottilie" if data.get("system") else str(data.get("von"))
    # Ton nur bei „dringend“ (wenn eingestellt) und nie, solange Pro Tools aufnimmt oder abspielt
    sound = urgent and bool(((ctx.extra.get("zentrale_settings") or {}).get("chat") or {}).get(
        "dringend_ton", True)) and not _transport_running(ctx)
    text = str(data.get("text") or "")
    preview = (text[:80] + "…") if len(text) > 80 else text
    notify(("Dringend – " if urgent else "Nachricht – ") + sender, preview if urgent else "Chat der Zentrale", sound)


def clear_central(ctx: AppContext) -> None:
    """Zentrale Vorgaben entfernen – aber erst, wenn nichts in Arbeit ist (sonst sprängen Betriebsart, Übergabe- und
    Exportordner mitten im Export um). Bis dahin alle 30 s neu prüfen."""
    if not ctx.extra.get("zentrale_werte"):
        return
    try:
        from tonmischung.app.commands import mode_switch_blocker
        blocker = mode_switch_blocker(ctx, ctx.extra.get("runner"))
    except Exception as e:  # noqa: BLE001
        blocker = str(e)
    link = ctx.extra.get("zentrale")
    if link is not None and link.state == CONNECTED:
        return                                       # inzwischen wieder verbunden
    if blocker:
        log.info("Zentrale Vorgaben bleiben, bis nichts mehr in Arbeit ist: %s", blocker)
        _later(ctx, "zentrale_leeren_zeitgeber", clear_central)
        return
    ctx.settings.update_central({})
    ctx.extra["zentrale_werte"] = {}
    S.write(_state_dir(ctx), S.SETTINGS, _kennung(), {"werte": {}})


def report_claims(ctx: AppContext, link: ZentraleLink, notify: Callable[[str, str, bool], None]) -> list[dict]:
    """Nach dem (Wieder-)Verbinden: eigene Belegungen melden. Widersprüche nur anzeigen – nicht selbst entscheiden."""
    from tonmischung.core import mixstate
    from tonmischung.dropfolder.client import MODE_FOLDER
    if ctx.mode != MODE_FOLDER:
        return []
    try:
        # alle Belegungen dieses Raums (auch eines anderen Mischers / Benutzers) – sonst gäbe die Zentrale sie frei
        folder = ctx.helmut.folder
        if hasattr(folder, "list_with_problems"):
            items, problems = folder.list_with_problems()
        else:
            items, problems = folder.list_for_mixing(), []
        mine = [c for c in items if c.mix.state in mixstate.OWNED and c.mix.room == ctx.room]
        if problems:
            # Ein Übergabeordner fehlt: dessen Belegungen gäbe die Zentrale sonst frei (Ausfall 13.11.2026) – beim
            # nächsten Verbinden erneut
            log.info("Belegungen nicht gemeldet: nicht alle Übergabeordner erreichbar")
            return []
        answer = link.request("belegungen_melden", {"belegungen": [{"beitrag_id": c.id, "titel": c.title}
                                                                   for c in mine]})
    except Exception as e:  # noqa: BLE001 – Übergabeordner oder Zentrale gerade weg: beim nächsten Verbinden
        log.info("Belegungen nicht gemeldet: %s", e)
        return []
    conflicts = [c for c in answer.get("konflikte", []) if isinstance(c, dict)]
    ctx.extra["zentrale_konflikte"] = conflicts
    for c in conflicts:
        notify("Konflikt bei der Belegung", f"„{c.get('titel') or c.get('beitrag_id')}“ ist laut Zentrale bei "
                                            f"{c.get('belegt_von')} – bitte klären", False)
    return conflicts


def _transport_running(ctx: AppContext) -> bool:
    """Läuft Pro Tools (Aufnahme/Wiedergabe)? Im Zweifel ja – lieber still als stören (Mischer M6)."""
    try:
        return bool(ctx.ptsl.transport_running())
    except Exception:  # noqa: BLE001
        return True


def effective_address(ctx: AppContext, dev: D.DeviceKey | None = None) -> str:
    """Eingestellte Adresse (MDM, lokal); ohne Einstellung die aus der Kopplung – so gilt sie für alle Benutzer des
    Macs, auch ohne Konfigurationsprofil (Review 11.11.2026)."""
    set_ = str(ctx.settings.get(ADDRESS) or "").strip()
    if set_:
        return set_
    dev = dev if dev is not None else D.read()
    return dev.address if dev is not None else ""


def info(ctx: AppContext) -> dict:
    link: ZentraleLink | None = ctx.extra.get("zentrale")
    dev = D.read()
    address = effective_address(ctx, dev)
    chat_cfg = (ctx.extra.get("zentrale_settings") or {}).get("chat") or {}
    return {"adresse": address, "adresse_quelle": ctx.settings.source(ADDRESS),
            "zustand": link.state if link else "aus", "text": link.text() if link else "ohne Zentrale",
            "verbunden": bool(link and link.state == CONNECTED),
            "gekoppelt": dev is not None, "gekoppelt_fuer": dev.address if dev else "",
            "raum_zentrale": (link.server_room if link and link.server_room else "") or (dev.room if dev else ""),
            "geraet": dev.device if dev else "", "kennung": dev.fingerprint if dev else "",
            "wartet": bool(link and link.state == WAITING), "raum": ctx.room,
            # Nach Ablehnen, Verfallen oder Abmelden bleibt der Schlüssel liegen – dann neu anmelden anbieten
            # (Review, Tontechniker 13.11.2026)
            "neu_anmelden": bool(dev is not None and link is not None and link.state in (REVOKED, OTHER_ADDRESS)),
            "raum_ausstehend": str(ctx.extra.get("raum_ausstehend") or ""),
            "chat": {"aktiv": bool(chat_cfg.get("aktiv")), "verbunden": bool(link and link.state == CONNECTED),
                     "hinweis": chat_cfg.get("hinweis", ""), "arten": chat_cfg.get("arten", {})},
            "zentral_vorgegeben": sorted(k for k in CENTRAL_KEYS if ctx.settings.source(k) == "zentral"),
            "konflikte": list(ctx.extra.get("zentrale_konflikte") or []),
            "ordner_beobachtet": bool((ctx.extra.get("zentrale_settings") or {}).get("ordner_beobachtet"))}


def pair(ctx: AppContext, args: dict, install: Callable[[str], None] | None = None) -> dict:
    """Kopplungscode einlösen und rechnerweit ablegen (einmal Administrator-Bestätigung)."""
    address = str(args.get("adresse") or effective_address(ctx)).strip()
    code = str(args.get("code") or "").strip()
    if not code:
        raise CommandError("Kopplungscode fehlt")
    try:
        D.check_address(address)
        dev = D.pair(address, code, str(args.get("geraet") or "") or None)
        D.write(dev, install=install)
    except D.PairingError as e:
        log.warning("Koppeln an %s gescheitert: %s", address, e)
        raise CommandError(str(e)) from None
    # Die Adresse steht mit in der Kopplung (gilt für alle Benutzer) – keine lokale Einstellung je Benutzer nötig
    link: ZentraleLink | None = ctx.extra.get("zentrale")
    if link is not None:
        link.reconnect_now()
    return {"meldung": f"Gekoppelt als Raum „{dev.room}“ – gilt für alle Benutzer dieses Macs.", **info(ctx)}


def register_device(ctx: AppContext, args: dict, install: Callable[[str], None] | None = None) -> dict:
    """Ohne Code anmelden: Schlüssel holen und rechnerweit ablegen; der Mac wartet, bis die Administration ihn einem
    Raum zuordnet (Jens 13.11.2026)."""
    address = str(args.get("adresse") or effective_address(ctx)).strip()
    try:
        D.check_address(address)
        dev = D.register(address, str(args.get("geraet") or "") or None)
        D.write(dev, install=install)
    except D.PairingError as e:
        log.warning("Anmelden an %s gescheitert: %s", address, e)
        raise CommandError(str(e)) from None
    log.info("Bei der Zentrale %s angemeldet, Kennung %s – wartet auf Zuordnung", address, dev.fingerprint)
    link: ZentraleLink | None = ctx.extra.get("zentrale")
    if link is not None:
        link.reconnect_now()
    return {"meldung": f"Angemeldet – wartet auf Zuordnung. Der Administration den Code {dev.fingerprint} nennen; "
                       "sie ordnet den Mac unter „Räume & Geräte“ einem Raum zu.", **info(ctx)}


def _link(ctx: AppContext) -> ZentraleLink:
    link = ctx.extra.get("zentrale")
    if link is None or link.state != CONNECTED:
        raise CommandError("Zentrale nicht verbunden")
    return link


def chat_view(ctx: AppContext) -> dict:
    chat: Chat = ctx.extra["chat"]
    return {**chat.view(), "raum": getattr(ctx.extra.get("zentrale"), "server_room", ""),
            "chat": info(ctx)["chat"]}


def chat_send(ctx: AppContext, args: dict) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        raise CommandError("Nachricht ist leer")
    if len(text) > 2000:
        raise CommandError("Nachricht zu lang (höchstens 2000 Zeichen)")
    link = ctx.extra.get("zentrale")
    if link is None or link.state != CONNECTED:
        raise CommandError("Nachricht nicht gesendet – Zentrale nicht erreichbar. Text bleibt stehen.")
    try:                                          # erst mit Bestätigung der Zentrale gilt sie als gesendet (Mischer)
        link.request("chat_senden", {"gespraech": str(args.get("gespraech") or ""), "text": text,
                                     "dringend": bool(args.get("dringend"))}, timeout_s=8.0)
    except ZentraleRefused as e:
        raise CommandError(f"Nachricht nicht gesendet: {e}") from None
    except ZentraleUnavailable:
        raise CommandError("Nachricht nicht gesendet – Zentrale nicht erreichbar. Text bleibt stehen.") from None
    return {}


def chat_read(ctx: AppContext, args: dict) -> dict:
    chat: Chat = ctx.extra["chat"]
    cid = str(args.get("gespraech") or "")
    last = chat.read(cid)
    link = ctx.extra.get("zentrale")
    if last and link is not None and link.state == CONNECTED:
        link.send("chat_gelesen", {"gespraech": cid, "bis": last})
    return {"ungelesen": chat.view()["ungelesen"]}


def chat_open(ctx: AppContext, args: dict) -> dict:
    return _link(ctx).request("chat_oeffnen", {"mit": str(args.get("mit") or "")})


def register(bridge: Bridge, ctx: AppContext) -> None:
    bridge.register("zentrale", lambda args: info(ctx))
    bridge.register("zentrale_koppeln", lambda args: pair(ctx, args))
    bridge.register("zentrale_anmelden", lambda args: register_device(ctx, args))
    bridge.register("zentrale_neu_verbinden",
                    lambda args: (ctx.extra["zentrale"].reconnect_now(), info(ctx))[1] if "zentrale" in ctx.extra
                    else info(ctx))
    bridge.register("chat", lambda args: chat_view(ctx))
    bridge.register("chat_senden", lambda args: chat_send(ctx, args))
    bridge.register("chat_gelesen", lambda args: chat_read(ctx, args))
    bridge.register("chat_oeffnen", lambda args: chat_open(ctx, args))
