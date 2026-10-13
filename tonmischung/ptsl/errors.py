"""Fehlerklassen der PTSL-Schicht. Jede Meldung ist für Mischer verständlich."""
from __future__ import annotations


class PtslError(Exception):
    """Oberklasse für alle Fehler im Umgang mit Pro Tools."""


class NotConnectedError(PtslError):
    """Pro Tools läuft nicht oder PTSL ist nicht aktiv."""


class PtslTimeoutError(PtslError):
    """Pro Tools hat nicht rechtzeitig geantwortet – oft ein offener Dialog."""


class UnsupportedError(PtslError):
    """Befehl wird von dieser Pro-Tools-Version nicht unterstützt."""


class CommandFailedError(PtslError):
    """Pro Tools hat den Befehl mit einem Fehler abgelehnt."""

    def __init__(self, message: str, error_name: str | None = None) -> None:
        super().__init__(message)
        self.error_name = error_name


class NoSessionError(CommandFailedError):
    """Es ist keine Session geöffnet."""


class TransportBusyError(PtslError):
    """Wiedergabe oder Aufnahme läuft noch; gespeichert und geschlossen wird nur bei Stillstand."""


class SaveFailedError(PtslError):
    """Speichern ist gescheitert; deshalb wird nicht geschlossen und nicht importiert."""


class DialogOpenError(PtslError):
    """Pro Tools zeigt einen Dialog; Session-Befehle warten, bis jemand ihn in Pro Tools bestätigt."""

    def __init__(self, message: str, dialogs: list[str] | None = None) -> None:
        super().__init__(message)
        self.dialogs = list(dialogs or [])


class ProToolsAssertionError(PtslError):
    """Pro Tools hat einen internen Fehler gemeldet („Assertion“); bis zum Neustart gehen keine Befehle mehr hin."""
