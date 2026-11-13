"""Raumstatus: eigenen Stand an die Zentrale melden, die Räume lesen (Jens 13.11.2026: nur mit Zentrale).

Welche Räume es gibt, weiß die Zentrale (Administration). Den Stand eines Raums meldet seine Ottilie über die
Zentrale (Tätigkeit, Fortschritt, Betriebsart – keine Personen); wem ein Beitrag gehört, steht in Helmut bzw. in den
Sperrdateien des Übergabeordners. Ist die Zentrale nicht erreichbar, zeigt die Übersicht, was Helmut bzw. die
Sperrdateien wissen. Bonjour und der Fortschritt im Takt über Helmut sind entfallen.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from tonmischung.core import mixstate
from tonmischung.core.peers import EXPORTING, FREE, IMPORTING, MIXING, PeerSource, PeerStatus, own_status
from tonmischung.helmut.client import HelmutError

log = logging.getLogger("tonmischung.raumstatus")
OWNED_CACHE_S = 30.0
TICK_S = 5.0


class RoomPublisher:
    """Takt (alle 5 s): eigener Stand an die Zentrale."""

    def __init__(self, ctx: Any, running: Any, peers: PeerSource | None = None,
                 clock: Any = None) -> None:
        self.ctx, self.running, self.peers = ctx, running, peers
        self.clock = clock or time.monotonic
        self._owned: tuple[Any, float] | None = None          # (eigener Beitrag, wann gelesen)

    def owned(self) -> Any:
        """Beitrag, den dieser Mischer in diesem Raum übernommen hat (kurz zwischengespeichert)."""
        now = self.clock()
        if self._owned is None or now - self._owned[1] > OWNED_CACHE_S:
            try:
                folder = getattr(self.ctx.helmut, "folder", None) if getattr(self.ctx, "folder_mode", False) else None
                if folder is not None and hasattr(folder, "list_with_problems"):
                    items, problems = folder.list_with_problems()
                else:
                    items, problems = self.ctx.helmut.list_for_mixing(), []
                mine = [c for c in items if c.mix.state in mixstate.IN_ROOM
                        and c.mix.mixer == self.ctx.mixer and c.mix.room == self.ctx.room]
                if not mine and problems and self._owned:
                    # Ein Übergabeordner fehlt: nicht „frei“ melden – sonst dürfte die Administration den Raum
                    # umbenennen, während hier gemischt wird (Ausfall 13.11.2026)
                    self._owned = (self._owned[0], now)
                    return self._owned[0]
                self._owned = (max(mine, key=lambda c: c.mix.taken_at or "") if mine else None, now)
            except HelmutError:
                self._owned = (self._owned[0] if self._owned else None, now)   # Helmut weg: letzter Stand
        return self._owned[0]

    def tick(self) -> None:
        if self.peers is None:
            return
        self.peers.publish(own_status(self.ctx.room, self.ctx.mixer, self.owned(), self.running(),
                                      mode=getattr(self.ctx, "mode", "")))


def _card(name: str, here: bool, st: str, label: str, mixer: str = "", title: str = "", pct: int = 0,
          cid: str = "", busy_count: int = 0, online: bool | None = None, source: str = "") -> dict:
    return {"name": name, "here": here, "st": st, "label": label, "mixer": mixer, "title": title, "pct": pct,
            "id": cid, "can_release": bool(cid) and not here, "busy_count": busy_count, "online": online,
            "source": source}


def _label(state: str, pct: int) -> str:
    """„importiert · 40 %“ während Import/Export, sonst nur der Zustand."""
    return f"{state} · {pct} %" if state in (IMPORTING, EXPORTING) else state


MODE_SHORT = {"helmut_api": "Helmut-API", "uebergabeordner": "Übergabeordner"}


def _mode_marks(card: dict, peer: PeerStatus | None, own: str) -> dict:
    """Betriebsart des Raums; `modus_anders`, wenn er anders läuft als dieser Mac (Jens 11.11.2026)."""
    mode = peer.mode if peer is not None and peer.mode else ""
    card["modus"] = MODE_SHORT.get(mode, mode)
    card["modus_anders"] = bool(mode) and mode != own
    return card


def _from_peer(name: str, p: PeerStatus, here: bool) -> dict:
    label = _label(p.state, p.pct)
    return _card(name, here, p.state, label, "", p.title, p.pct if p.state != FREE else 0, p.contribution,
                 1 if p.state != FREE else 0, True, "Zentrale")


def rooms(ctx: Any, peers: PeerSource | None = None) -> dict:
    """Raumübersicht: alle Räume der Zentrale mit Zustand, Beitrag, Fortschritt und ob dort Ottilie läuft.
    Belegung aus Helmut bzw. den Sperrdateien; Tätigkeit und Fortschritt live über die Zentrale."""
    own_mode = getattr(ctx, "mode", "helmut_api")
    hub = "Sperrdateien" if own_mode == "uebergabeordner" else "Helmut"
    central = "aus den Sperrdateien" if own_mode == "uebergabeordner" else "aus Helmut"
    direct = peers if peers is not None and getattr(peers, "available", False) else None
    live = direct.peers() if direct is not None else {}
    known = list(direct.room_names()) if direct is not None and hasattr(direct, "room_names") else []
    contributions, helmut_ok = [], True
    try:
        contributions = ctx.helmut.list_for_mixing()
    except HelmutError:
        if direct is None:
            raise
        helmut_ok = False
    names = sorted(set(known) | {c.mix.room for c in contributions if c.mix.room} | set(live) | {ctx.room})
    cards = []
    for name in names:
        here = name == ctx.room
        peer = live.get(name)
        user = ctx.user.name if here and getattr(ctx, "user", None) else ""     # Personen nur hier (Zentrale: Räume)
        if not helmut_ok:                                   # nur Zentrale
            if peer is not None:
                cards.append(_mode_marks({**_from_peer(name, peer, here), "user": user}, peer, own_mode))
            else:
                cards.append({**_card(name, here, "offline" if not here else FREE,
                                      "nicht erreichbar" if not here else "frei", online=False if not here else None,
                                      source=f"weder über die Zentrale erreichbar noch {hub}"), "user": user})
            continue
        owned = [c for c in contributions if c.mix.state in mixstate.IN_ROOM and c.mix.room == name]
        online = None if direct is None or here else (peer is not None)
        if not owned:
            card = _card(name, here, FREE, "frei", online=online, source=central)
        else:
            c = max(owned, key=lambda x: x.mix.taken_at or "")
            act = c.mix.activity
            st = IMPORTING if act == "import" else (EXPORTING if act == "export" else MIXING)
            pct = c.mix.progress if act else 0
            src = central
            if peer is not None and peer.contribution == c.id and peer.state in (IMPORTING, EXPORTING):
                st, pct, src = peer.state, peer.pct, "Zentrale"                 # genauer und aktueller
            card = _card(name, here, st, _label(st, pct), c.mix.mixer, c.title, pct, c.id, len(owned), online, src)
        if online is False:
            card["label"] += " · nicht erreichbar"
        card["user"] = user
        cards.append(_mode_marks(card, peer, own_mode))
    busy = sum(1 for c in cards if c["st"] not in (FREE, "offline"))
    how = (f"{hub} und Zentrale" if helmut_ok and direct is not None else
           f"nur {hub} (Zentrale nicht verbunden)" if helmut_ok else f"nur Zentrale ({hub} nicht erreichbar)")
    other = [c["name"] for c in cards if c.get("modus_anders")]
    note = f" · andere Betriebsart in: {', '.join(other)}" if other else ""
    verb = "arbeitet" if len(other) == 1 else "arbeiten"
    warn = (f"{', '.join(other)} {verb} in einer anderen Betriebsart – was dort übernommen ist, sieht dieser Mac nur "
            "teilweise. Vor dem Übernehmen nachfragen.") if other else ""
    return {"cards": cards, "busy": busy, "source": f"Quelle: {how}{note}",
            "betriebsart": MODE_SHORT.get(own_mode, own_mode), "gemischt": warn}
