"""Umschaltbare Betriebsart: Helmut-API oder Übergabeordner – ein Objekt an der Stelle des Helmut-Clients.

`ModeClient` reicht jeden Aufruf an den Client der eingestellten Betriebsart weiter. So bleibt `ctx.helmut` überall,
wo es heute steht, und das Umschalten in den Einstellungen wirkt ohne Neustart. Umschalten nur, wenn nichts in Arbeit
ist (`switch_blocker`).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tonmischung.core import mixstate
from tonmischung.dropfolder.client import MODE_API, MODE_FOLDER

MODE_TEXT = {MODE_API: "Helmut-API", MODE_FOLDER: "Übergabeordner"}


class ModeClient:
    def __init__(self, api: Any, folder: Callable[[], Any], mode: Callable[[], str]) -> None:
        self.api, self._folder_factory, self._mode = api, folder, mode
        self._folder: Any = None

    @property
    def mode(self) -> str:
        return MODE_FOLDER if self._mode() == MODE_FOLDER else MODE_API

    @property
    def folder(self) -> Any:
        if self._folder is None:
            self._folder = self._folder_factory()
        return self._folder

    @property
    def current(self) -> Any:
        return self.folder if self.mode == MODE_FOLDER else self.api

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.current, name)


def mode_of(client: Any) -> str:
    """Betriebsart eines Clients (ein schlichter Helmut-Client gilt als Helmut-API)."""
    return client.mode if isinstance(client, ModeClient) else MODE_API


def switch_blocker(job_title: str, open_entries: int, owned_titles: list[str]) -> str:
    """Grund, warum jetzt nicht umgeschaltet werden darf – oder "". Ein übernommener Film bliebe sonst in der anderen
    Betriebsart gesperrt, und Nachträge gingen an das falsche Ziel."""
    if job_title:
        return f"Während eines Auftrags nicht möglich – bitte warten, bis „{job_title}“ fertig ist"
    if owned_titles:
        names = [f"„{t}“" for t in owned_titles[:3]]
        text = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " und " + names[-1]
        more = " …" if len(owned_titles) > 3 else ""
        verb = "ist" if len(owned_titles) == 1 else "sind"
        return f"{text}{more} {verb} hier noch übernommen – erst zurückliefern oder freigeben"
    if open_entries:
        what = "1 Änderung wartet" if open_entries == 1 else f"{open_entries} Änderungen warten"
        return f"{what} im Nachtrag – erst nachtragen oder verwerfen"
    return ""


def owned_here(contributions: list, mixer: str, room: str) -> list[str]:
    return [c.title for c in contributions if mixstate.owned_by(c.mix, mixer, room)]
