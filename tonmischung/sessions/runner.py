"""Führt einen ImportPlan gegen Pro Tools aus.

Sicherheitsregel: scheitert das Speichern, wird weder geschlossen noch importiert.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from tonmischung.core.session_rules import ImportPlan, Outcome, Step
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import PtslError, SaveFailedError, TransportBusyError

log = logging.getLogger(__name__)

# ANNAHME: höchstens 120 s auf Stillstand warten (M0 Frage F, Vorschlag, noch nicht bestätigt).
TRANSPORT_WAIT_S = 120.0


def wait_for_transport_stop(client: PtslClient, max_wait_s: float = TRANSPORT_WAIT_S,
                            poll_s: float = 1.0,
                            sleep: Callable[[float], None] = time.sleep,
                            clock: Callable[[], float] = time.monotonic) -> None:
    end = clock() + max_wait_s
    while client.transport_running():
        if clock() >= end:
            raise TransportBusyError(
                f"Wiedergabe oder Aufnahme läuft seit {max_wait_s:.0f} s – Pro Tools bitte anhalten")
        sleep(poll_s)


def run_import_plan(client: PtslClient, plan: ImportPlan, **wait_kwargs) -> None:
    """Macht Pro Tools frei für den Import. Wirft, wenn das nicht sicher geht."""
    if plan.outcome is not Outcome.PROCEED:
        raise ValueError(f"Plan ist nicht ausführbar: {plan.outcome.value} ({plan.reason})")
    for step in plan.steps:
        log.info("Vor dem Import: %s", step.value)
        if step is Step.WAIT_TRANSPORT:
            wait_for_transport_stop(client, **wait_kwargs)
        elif step is Step.SAVE:
            try:
                client.save()
            except PtslError as e:
                raise SaveFailedError(
                    f"Speichern gescheitert – Session bleibt offen, nichts importiert ({e})") from e
        elif step is Step.CLOSE:
            client.close()
