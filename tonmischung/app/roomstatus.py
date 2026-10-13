"""Raumstatus (M7): eigenen Stand melden, die Räume lesen – über Helmut und direkt (Bonjour).

Prototyp: „Die Mac-App schreibt Raum, Mischer und Status ins Metadaten-Set „Mischung“ und liest die anderen
Räume von dort.“ Helmut kennt keine Räume als Objekt; ein Raum gilt als belegt, wenn ihm ein übernommener
Beitrag gehört. Während eines Holens oder Zurücklieferns schreibt die App Tätigkeit und Fortschritt in
Stufen (Einstellung 10 oder 25 %) – höchstens bei jeder neuen Stufe.
Direkt (Jens, 03.10.2026): Jede App meldet sich per Bonjour an; so zeigt die Übersicht auch, ob die App in
einem Raum läuft, und funktioniert ohne Helmut. Die direkte Quelle ist austauschbar (`PeerSource`) – ein
späterer eigener Server kann sie ersetzen, ohne dass die App umgebaut werden muss.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from tonmischung.core import mixstate
from tonmischung.core.peers import EXPORTING, FREE, IMPORTING, MIXING, PeerSource, PeerStatus, own_status
from tonmischung.helmut.client import HelmutError

log = logging.getLogger("tonmischung.raumstatus")
P = "system.raeume_nutzer.raumstatus."
ACTIVITY = {"holen": "import", "wiederoeffnen": "import", "havarie": "import", "zurueckliefern": "export"}


def step_of(fraction: float, step: int) -> int:
    pct = int(max(0.0, min(1.0, fraction)) * 100)
    return pct // step * step


OWNED_CACHE_S = 30.0


class RoomPublisher:
    """Takt (alle `intervall_s`): Fortschritt des laufenden Auftrags an Helmut (am Ende Tätigkeit löschen) und
    der eigene Stand an die direkte Quelle (Bonjour)."""

    def __init__(self, ctx: Any, running: Any, peers: PeerSource | None = None,
                 clock: Any = None) -> None:
        self.ctx, self.running, self.peers = ctx, running, peers
        self.clock = clock or time.monotonic
        self._last: tuple[str, str, int] | None = None       # (Beitrag, Tätigkeit, Stufe)
        self._owned: tuple[Any, float] | None = None          # (eigener Beitrag, wann gelesen)

    def owned(self) -> Any:
        """Beitrag, den dieser Mischer in diesem Raum übernommen hat (kurz zwischengespeichert)."""
        now = self.clock()
        if self._owned is None or now - self._owned[1] > OWNED_CACHE_S:
            try:
                mine = [c for c in self.ctx.helmut.list_for_mixing() if c.mix.state in mixstate.IN_ROOM
                        and c.mix.mixer == self.ctx.mixer and c.mix.room == self.ctx.room]
                self._owned = (max(mine, key=lambda c: c.mix.taken_at or "") if mine else None, now)
            except HelmutError:
                self._owned = (self._owned[0] if self._owned else None, now)   # Helmut weg: letzter Stand
        return self._owned[0]

    def tick(self) -> None:
        s = self.ctx.settings
        if not s.get(P + "teilen"):
            if self.peers is not None:
                self.peers.publish(None)
            return
        job = self.running()
        if self.peers is not None:
            self.peers.publish(own_status(self.ctx.room, self.ctx.mixer, self.owned(), job))
        act = ACTIVITY.get(job["kind"]) if job else None
        pid = job.get("contribution") if job else ""
        try:
            if job and act and pid:
                pct = step_of(job.get("fraction", 0.0), int(s.get(P + "stufen_prozent") or 10))
                if self._last != (pid, act, pct):
                    self.ctx.service().set_progress(pid, pct, act)
                    self._last = (pid, act, pct)
            elif self._last is not None:
                last_pid, _, _ = self._last
                self.ctx.service().set_progress(last_pid, 0, "")      # Auftrag vorbei
                self._last = None
        except HelmutError as e:
            log.debug("Raumstatus nicht gemeldet: %s", e)


def _card(name: str, here: bool, st: str, label: str, mixer: str = "", title: str = "", pct: int = 0,
          cid: str = "", busy_count: int = 0, online: bool | None = None, source: str = "") -> dict:
    return {"name": name, "here": here, "st": st, "label": label, "mixer": mixer, "title": title, "pct": pct,
            "id": cid, "can_release": bool(cid) and not here, "busy_count": busy_count, "online": online,
            "source": source}


def _label(state: str, pct: int) -> str:
    """„importiert · 40 %“ während Import/Export, sonst nur der Zustand."""
    return f"{state} · {pct} %" if state in (IMPORTING, EXPORTING) else state


def _from_peer(name: str, p: PeerStatus, here: bool) -> dict:
    label = _label(p.state, p.pct)
    return _card(name, here, p.state, label, p.mixer, p.title, p.pct if p.state != FREE else 0, p.contribution,
                 1 if p.state != FREE else 0, True, "direkt · Bonjour")


def rooms(ctx: Any, peers: PeerSource | None = None) -> dict:
    """Raumübersicht: je Raum Mischer, Zustand, Beitrag, Fortschritt; ob die App dort läuft (direkt).

    Quelle (Einstellung): „helmut_mit_rueckfall“ = Helmut, ergänzt um den direkten Stand und bei Ausfall nur
    direkt; „nur_helmut“; „nur_direkt“.
    """
    s = ctx.settings
    step = int(s.get(P + "stufen_prozent") or 10)
    mode = s.get(P + "quelle") or "helmut_mit_rueckfall"
    direct = peers if peers is not None and getattr(peers, "available", False) and mode != "nur_helmut" else None
    live = direct.peers() if direct is not None else {}
    contributions, helmut_ok = [], mode != "nur_direkt"
    if helmut_ok:
        try:
            contributions = ctx.helmut.list_for_mixing()
        except HelmutError:
            if direct is None:
                raise
            helmut_ok = False
    configured = [r.get("name", "") for r in (s.get("system.raeume_nutzer.raeume") or []) if r.get("name")]
    names = configured or sorted({c.mix.room for c in contributions if c.mix.room} | set(live) | {ctx.room})
    cards = []
    for name in names:
        here = name == ctx.room
        peer = live.get(name)
        # Wer dort angemeldet ist (Plan Nutzer & Räume, Schritt 5): hier der Nutzer, sonst die Bonjour-Meldung
        user = (ctx.user.name if here and getattr(ctx, "user", None) else (peer.mixer if peer is not None else ""))
        if not helmut_ok:                                   # nur direkt
            if peer is not None:
                cards.append({**_from_peer(name, peer, here), "user": user})
            else:
                cards.append({**_card(name, here, "offline" if not here else FREE,
                                      "nicht erreichbar" if not here else "frei", online=False if not here else None,
                                      source="weder per Bonjour erreichbar noch Helmut"), "user": user})
            continue
        owned = [c for c in contributions if c.mix.state in mixstate.IN_ROOM and c.mix.room == name]
        online = None if direct is None or here else (peer is not None)
        if not owned:
            mixer = peer.mixer if peer is not None else ""
            card = _card(name, here, FREE, "frei", mixer, online=online, source="aus Helmut")
        else:
            c = max(owned, key=lambda x: x.mix.taken_at or "")
            act = c.mix.activity
            st = IMPORTING if act == "import" else (EXPORTING if act == "export" else MIXING)
            pct = c.mix.progress // step * step if act else 0
            src = f"aus Helmut · Stufen von {step} %"
            if peer is not None and peer.contribution == c.id and peer.state in (IMPORTING, EXPORTING):
                st, pct, src = peer.state, peer.pct, "direkt · Bonjour"          # genauer und aktueller
            label = _label(st, pct)
            card = _card(name, here, st, label, c.mix.mixer, c.title, pct, c.id, len(owned), online, src)
        if online is False:
            card["label"] += " · nicht erreichbar"
        card["user"] = user
        cards.append(card)
    busy = sum(1 for c in cards if c["st"] not in (FREE, "offline"))
    how = ("Helmut, ergänzt direkt per Bonjour" if helmut_ok and direct is not None else
           "nur Helmut" if helmut_ok else "direkt per Bonjour (Helmut nicht erreichbar)" if mode != "nur_direkt"
           else "nur direkt per Bonjour")
    return {"cards": cards, "busy": busy, "step": step, "source": f"Quelle: {how}"}
