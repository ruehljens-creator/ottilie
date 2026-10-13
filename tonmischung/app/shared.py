"""Gemeinsame Hilfen der Fenster-Befehle: Beitrag lesen, Ablageort, Rückfrage „andere Session offen“.

Die Rückfrage stand vorher fünfmal ausgeschrieben in den Aufträgen (Holen, Stand öffnen, Sicherung öffnen, Havarie,
Zurückliefern); `confirming` führt einen Ablauf aus und wiederholt ihn nach einem Ja (Durchsicht 13.10.2026).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from tonmischung.app.bridge import CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import Cancelled, JobContext
from tonmischung.core.mixstate import DELIVERED
from tonmischung.core.session_rules import same_session
from tonmischung.helmut.client import HelmutError

T = TypeVar("T")


def contribution(ctx: AppContext, pid: str):
    """Beitrag aus Helmut; Fehler als Meldung fürs Fenster."""
    try:
        return ctx.helmut.get(pid)
    except HelmutError as e:
        raise CommandError(f"Helmut: {e}") from e


def placement(ctx: AppContext, c: Any):
    """Ablageort für einen Beitrag (Plan M7-Ablage) – Fehler als klare Meldung im Fenster."""
    from tonmischung.core.storage import StorageError
    try:
        return ctx.placement(c)
    except StorageError as e:
        raise CommandError(str(e)) from e


def is_delivered(ctx: AppContext, path: str) -> bool:
    """Gehört die Session zu einem ausgelieferten Beitrag? (Session-Regeln: dann ohne Rückfrage schließen)"""
    return any(same_session(b.mix.session_path, path) and b.mix.state == DELIVERED
               for b in ctx.helmut.list_for_mixing())


def ask_close_open(jc: JobContext, e: Exception, then: str, label: str, refused: str,
                   title: str = "Offene Session") -> None:
    """Rückfrage: andere Session offen – speichern, schließen und weiter? Nein → `Cancelled(refused)`."""
    if not jc.ask({"kind": "offene_session", "title": title,
                   "text": f"{e}. Sie wird gespeichert und geschlossen, dann {then}.",
                   "options": [{"value": True, "label": label, "primary": True},
                               {"value": False, "label": "Abbrechen"}]}):
        raise Cancelled(refused)


def confirming(jc: JobContext, run: Callable[[bool | None], T], then: str, label: str, refused: str,
               title: str = "Offene Session") -> T:
    """`run(confirmed)` ausführen. Ist eine andere Session offen (`NeedsConfirmation`), fragen und nach einem Ja mit
    `confirmed=True` wiederholen; nach einem Nein bricht der Auftrag mit `refused` ab."""
    from tonmischung.workflow.fetch import NeedsConfirmation      # spät: Abläufe ziehen Pro Tools und AAF nach
    confirmed: bool | None = None
    while True:
        try:
            return run(confirmed)
        except NeedsConfirmation as e:
            ask_close_open(jc, e, then, label, refused, title)
            confirmed = True
