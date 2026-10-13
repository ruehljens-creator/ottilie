"""Havarie: Ersatz für Helmut beim „Paket manuell öffnen“ (M9).

Liefert dem unveränderten Holen (`workflow/fetch.py`) einen Beitrag und seine Übergabe aus dem Paketordner.
Übernehmen und Zustand gehen in die Nachtrag-Warteschlange: sobald Helmut zurück ist, wird übernommen –
nur wenn der Beitrag dann noch frei ist, sonst meldet die App einen Konflikt. Ohne Helmut-Projekt-ID warten
die Einträge, bis die Havarie-Session einem Beitrag zugeordnet ist.
"""
from __future__ import annotations

import os

from tonmischung.core import mixstate
from tonmischung.core.catchup import CatchUpQueue
from tonmischung.core.havarie import Package
from tonmischung.core.mixstate import MixState
from tonmischung.helmut.mapping import Asset, Contribution, Handover

HINT = ("Havarie: ohne Helmut geöffnet – Übernahme und Zustand werden nachgetragen, sobald Helmut wieder da ist"
        "{zuordnen}")


class _OfflineClient:
    def __init__(self, service: "OfflineService") -> None:
        self.s = service

    def get(self, project_id: str) -> Contribution:
        return self.s.contribution

    def handover(self, project_id: str) -> Handover:
        p = self.s.package

        def asset(path: str) -> Asset | None:
            return Asset("", os.path.basename(path), path) if path else None
        return Handover(asset(p.aaf), asset(p.video), None)


class OfflineService:
    """Teilmenge von MixService, die das Holen braucht – ohne Helmut."""

    def __init__(self, package: Package, title: str, project_id: str, havarie_id: str, mixer: str, room: str,
                 queue: CatchUpQueue) -> None:
        self.package, self.mixer, self.room, self.queue = package, mixer, room, queue
        self.project_id, self.havarie_id = project_id, havarie_id
        self.contribution = Contribution(project_id or havarie_id, title, "", False, MixState(),
                                         {"name": title, "havarie": True})
        self.client = _OfflineClient(self)

    def take(self, project_id: str) -> Contribution:
        self.queue.add_take(self.project_id, self.contribution.title, self.mixer, self.room, havarie=self.havarie_id)
        mix = mixstate.after_take(self.contribution.mix, self.mixer, self.room, "")
        self.contribution = Contribution(self.contribution.id, self.contribution.title, "", False, mix,
                                         self.contribution.raw)
        return self.contribution

    def set_state_or_queue(self, known: Contribution, state: str, **changes) -> tuple[Contribution, str]:
        new = mixstate.with_state(known.mix, state, **changes)
        self.queue.add_mix(self.project_id, known.title, known.mix, new, self.mixer, self.room,
                           havarie=self.havarie_id)
        self.contribution = Contribution(known.id, known.title, "", False, new, known.raw)
        hint = HINT.format(zuordnen="" if self.project_id else
                           " (vorher in der App einem Beitrag zuordnen: Hinweis über der Liste)")
        return self.contribution, hint

    def add_asset_or_queue(self, known: Contribution, path: str, kind: str) -> str:
        from tonmischung.core.catchup import Entry, ASSET
        self.queue.add(Entry(ASSET, self.project_id, known.title, path=path, asset_kind=kind, havarie=self.havarie_id))
        return HINT.format(zuordnen="")
