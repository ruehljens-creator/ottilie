"""Mehrere Übergabeordner als ein Client (Jens 13.11.2026: „mehr als einen Übergabeordner festlegen können“).

Der bisherige Übergabeordner bleibt der erste: seine Filme behalten ihre ID (= Ordnername), Sperrdateien, Nachträge und
Belegungen gelten weiter. Weitere Ordner kommen aus `system.uebergabeordner.weitere_ordner` (in der Zentrale
festgelegt); ihre Filme heißen „<Kennung>/<Ordnername>“ – ein „/“ kommt in keinem Ordnernamen vor, die ID bleibt
eindeutig. Jeder Aufruf geht an den Client des Ordners, in dem der Film liegt; die Liste zeigt die Filme aller Ordner
mit Herkunft.

Prüfrunde 13.11.2026:
- Die Zentrale bekommt dieselbe ID wie Liste und Ordnerbeobachtung („nord/Film“) – vorher meldete das Belegen nur
  „Film“, gleichnamige Filme zweier Ordner sperrten sich gegenseitig, Belegungen blieben hängen.
- Fällt ein Ordner aus, bleiben die anderen sichtbar; vom ausgefallenen bleibt der zuletzt gelesene Stand in der Liste
  (gekennzeichnet), damit ein übernommener Film nicht verschwindet. `problems` nennt die Ordner, `complete` sagt, ob
  die Liste vollständig ist – Raumwechsel, Umschalten und der Abgleich mit der Zentrale warten sonst (Ausfall).
- Der Zwischenspeicher der Clients ist threadsicher.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from tonmischung.core.naming import slug
from tonmischung.dropfolder.client import MODE_FOLDER, FolderClient
from tonmischung.helmut.client import HelmutError
from tonmischung.helmut.mapping import Contribution

log = logging.getLogger(__name__)

SEP = "/"
MAIN_NAME = "Übergabeordner"
STALE = "ordner_nicht_erreichbar"               # in `raw`: Eintrag aus dem letzten Stand eines ausgefallenen Ordners
# Was ohne Film-ID für alle Ordner gleich gilt und deshalb beim ersten Ordner bleibt (Fertigmeldung, Anmeldung)
SHARED = frozenset({"config", "check_auth", "test_message", "notify", "notify_target", "last_ok"})


def keys_for(entries: list[dict]) -> list[str]:
    """Kennung je weiterem Ordner aus seinem Namen – eindeutig, nie leer, ohne „/“."""
    out: list[str] = []
    for i, e in enumerate(entries, start=1):
        key = slug(str(e.get("name") or "")) or f"ordner-{i}"
        base, n = key, 2
        while key in out:
            key, n = f"{base}-{n}", n + 1
        out.append(key)
    return out


class _Prefixed:
    """Zentrale-Haken eines weiteren Ordners: jede Film-ID mit Kennung."""

    def __init__(self, hooks: Any, key: str) -> None:
        self.hooks, self.key = hooks, key

    def _id(self, beitrag_id: str) -> str:
        return f"{self.key}{SEP}{beitrag_id}"

    def claim(self, beitrag_id: str, *args: Any, **kwargs: Any) -> Any:
        return self.hooks.claim(self._id(beitrag_id), *args, **kwargs)

    def release(self, beitrag_id: str, *args: Any, **kwargs: Any) -> Any:
        return self.hooks.release(self._id(beitrag_id), *args, **kwargs)

    def event(self, kind: str, beitrag_id: str, *args: Any, **kwargs: Any) -> Any:
        return self.hooks.event(kind, self._id(beitrag_id), *args, **kwargs)


class MultiFolderClient:
    mode = MODE_FOLDER

    def __init__(self, main: FolderClient, entries: Callable[[], list[dict]], make: Callable[[dict], FolderClient],
                 main_name: Callable[[], str] = lambda: "") -> None:
        self.main, self._entries, self._make, self._main_name = main, entries, make, main_name
        self._clients: dict[str, tuple[str, FolderClient]] = {}       # Kennung → (Eintrag als JSON, Client)
        self._last: dict[str, list[Contribution]] = {}               # Kennung → zuletzt gelesene Filme
        self._lock = threading.RLock()
        self._central: Any = None
        self.problems: list[dict] = []                                # [{name, meldung}] zuletzt nicht lesbar

    @property
    def complete(self) -> bool:
        """War die letzte Liste vollständig (alle Ordner gelesen)?"""
        return not self.problems

    # ── Ordner ──────────────────────────────────────────────────────────────
    def _hooks(self, key: str) -> Any:
        return _Prefixed(self._central, key) if self._central is not None and key else self._central

    def folders(self) -> list[tuple[str, str, FolderClient]]:
        """(Kennung, Name, Client) – der erste Ordner mit Kennung "", dann die weiteren in ihrer Reihenfolge."""
        entries = [e for e in (self._entries() or []) if isinstance(e, dict)]
        with self._lock:
            # Der erste Ordner zählt nur, wenn er eingestellt ist – sind nur weitere Ordner eingestellt, wäre die Liste
            # sonst nie vollständig (Nachprüfung 13.11.2026)
            out = [("", self._main_name() or MAIN_NAME, self.main)] if not entries or self._configured(self.main) \
                else []
            seen = set()
            for key, e in zip(keys_for(entries), entries):
                sig = json.dumps(e, sort_keys=True)
                cached = self._clients.get(key)
                if cached is None or cached[0] != sig:                 # neu oder geändert (z. B. von der Zentrale)
                    client = self._make(e)
                    client.central = self._hooks(key)
                    self._clients[key] = cached = (sig, client)
                    self._last.pop(key, None)
                out.append((key, str(e.get("name") or key), cached[1]))
                seen.add(key)
            for key in set(self._clients) - seen:                     # entfernt
                self._clients.pop(key, None)
                self._last.pop(key, None)
            return out

    @staticmethod
    def _configured(client: FolderClient) -> bool:
        try:
            cfg = client.config
        except Exception:  # noqa: BLE001 – im Zweifel als eingestellt zählen
            return True
        return bool(cfg.root or getattr(cfg, "netzadresse", ""))

    def _route(self, project_id: str) -> tuple[str, str, FolderClient, str]:
        """(Kennung, Name, Client, ID im Ordner)."""
        pid = str(project_id)
        key, local = pid.split(SEP, 1) if SEP in pid else ("", pid)
        for k, name, client in self.folders():
            if k == key:
                return key, name, client, local
        raise HelmutError(f"Übergabeordner „{key}“ ist nicht (mehr) eingestellt – Film „{local}“ nicht erreichbar",
                          status=404)

    @staticmethod
    def _out(key: str, name: str, c: Contribution, stale: bool = False) -> Contribution:
        """Ergebnis eines Ordners: ID mit Kennung (weitere Ordner), Herkunft in `raw`."""
        pid = f"{key}{SEP}{c.id}" if key else c.id
        raw = {**(c.raw or {}), "id": pid, "uebergabeordner": name}
        if stale:
            raw[STALE] = True
        return replace(c, id=pid, raw=raw)

    # ── Zentrale (Belegen) an alle Ordner ───────────────────────────────────
    @property
    def central(self) -> Any:
        return self._central

    @central.setter
    def central(self, value: Any) -> None:
        with self._lock:
            self._central = value
            self.main.central = value
            for key, (_sig, client) in list(self._clients.items()):
                client.central = self._hooks(key)

    # ── Lesen ───────────────────────────────────────────────────────────────
    def list_for_mixing(self) -> list[Contribution]:
        items, self.problems = self.list_with_problems()
        return items

    def list_with_problems(self) -> tuple[list[Contribution], list[dict]]:
        """(Filme, nicht lesbare Ordner) aus genau diesem Aufruf – für Prüfungen, die beides zusammen brauchen
        (Nachprüfung 13.11.2026: `problems` konnte von einem anderen Thread stammen)."""
        out, problems, errors = [], [], []
        folders = self.folders()
        for key, name, client in folders:
            try:
                items = client.list_for_mixing()
            except HelmutError as e:
                problems.append({"name": name, "meldung": str(e)})
                errors.append(e)
                log.warning("Übergabeordner %s nicht lesbar: %s", name, e)
                with self._lock:
                    out += [self._out(key, name, c, stale=True) for c in self._last.get(key, [])]
                continue
            with self._lock:
                self._last[key] = items
            out += [self._out(key, name, c) for c in items]
        if errors and len(errors) == len(folders):
            self.problems = problems
            raise errors[0]                                           # alle weg: wie ein einzelner Ordner
        return out, problems

    def _each(self, call: Callable[[FolderClient], Any], what: str) -> None:
        """Für alle Ordner; ein Fehler erst, wenn alle scheitern."""
        folders = self.folders()
        errors = []
        for _k, name, client in folders:
            try:
                call(client)
            except HelmutError as e:
                errors.append(e)
                log.info("Übergabeordner %s %s: %s", name, what, e)
        if errors and len(errors) == len(folders):
            raise errors[0]

    def reachable(self, *args: Any, **kwargs: Any) -> None:
        self._each(lambda c: c.reachable(*args, **kwargs), "nicht erreichbar")

    def connect(self, now: bool = False) -> None:
        self._each(lambda c: c.connect(now=now), "nicht verbunden")

    def writable(self) -> bool:
        """Alle eingebundenen Ordner beschreibbar? (Hinweis „nur lesbar“ im Fenster) – ein nicht eingebundener zählt
        nicht, der steht schon als „nicht erreichbar“ über der Liste (Nachprüfung 13.11.2026)."""
        return all(c.writable() for _k, _n, c in self.folders() if c.config.root and os.path.isdir(c.config.root))

    def get(self, project_id: str) -> Contribution:
        key, name, client, local = self._route(project_id)
        return self._out(key, name, client.get(local))

    def folder_of(self, project_id: str) -> str:
        _k, _n, client, local = self._route(project_id)
        return client.folder_of(local)

    def handover(self, project_id: str):
        _k, _n, client, local = self._route(project_id)
        return client.handover(local)

    def assets(self, project_id: str) -> list[dict]:
        _k, _n, client, local = self._route(project_id)
        return client.assets(local)

    def export_dir(self, project_id: str) -> str:
        _k, _n, client, local = self._route(project_id)
        return client.export_dir(local)

    def delivery_target(self, project_id: str):
        _k, _n, client, local = self._route(project_id)
        inner = client.delivery_target(local)
        return lambda pid, stamp: inner(local, stamp)

    def done_values(self, project_id: str, *args: Any, **kwargs: Any) -> dict:
        _k, _n, client, local = self._route(project_id)
        return client.done_values(local, *args, **kwargs)

    def done_message(self, project_id: str, *args: Any, **kwargs: Any) -> Any:
        _k, _n, client, local = self._route(project_id)
        return client.done_message(local, *args, **kwargs)

    # ── Schreiben ───────────────────────────────────────────────────────────
    def write_mix(self, contribution: Contribution, mix: Any) -> Contribution:
        key, name, client, local = self._route(contribution.id)
        inner = replace(contribution, id=local, raw={**(contribution.raw or {}), "id": local})
        return self._out(key, name, client.write_mix(inner, mix))

    def set_locked(self, project_id: str, locked: bool) -> None:
        _k, _n, client, local = self._route(project_id)
        client.set_locked(local, locked)

    def add_asset(self, project_id: str, *args: Any, **kwargs: Any) -> dict:
        _k, _n, client, local = self._route(project_id)
        return client.add_asset(local, *args, **kwargs)

    def add_handover_asset(self, project_id: str, *args: Any, **kwargs: Any) -> dict:
        return self.main.add_handover_asset(project_id, *args, **kwargs)     # gibt es in der Betriebsart nicht

    def duplicate(self, contribution: Contribution, title: str) -> Contribution:
        return self.main.duplicate(contribution, title)                     # gibt es in der Betriebsart nicht

    def __getattr__(self, name: str) -> Any:
        if name in SHARED:
            return getattr(self.main, name)
        raise AttributeError(name)            # alles mit Film-ID muss oben über `_route` gehen
