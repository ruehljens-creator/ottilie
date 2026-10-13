"""Nachtragen: liegengebliebene Helmut-Schreibvorgänge in Reihenfolge schreiben (Plan M2-Nachtrag).

Je Eintrag wird der Beitrag frisch gelesen. Gehört er noch dem erwarteten Mischer im erwarteten Raum,
wird geschrieben; steht schon der Zielwert, ist der Eintrag erledigt; sonst Konflikt – nichts wird
überschrieben, und spätere Einträge desselben Beitrags warten. Ist Helmut wieder weg, hört das
Nachtragen auf und versucht es später erneut.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from tonmischung.core import mixstate
from tonmischung.core.catchup import (APPLY, ASSET, CONFLICT, DONE, LOCK, MIX, NOTIFY, TAKE, CatchUpQueue,
                                      apply_changes, decide_mix)
from tonmischung.helmut.client import HelmutClient, HelmutError, HelmutUnavailableError
from tonmischung.helmut.service import MixService

log = logging.getLogger("tonmischung.nachtrag")
MAX_ATTEMPTS = 5          # Helmut lehnt einen Eintrag wiederholt ab → als Konflikt zeigen


@dataclass
class CatchUpReport:
    written: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unavailable: bool = False
    busy: bool = False

    @property
    def text(self) -> str:
        if self.busy:
            return "Nachtragen läuft schon"
        parts = []
        if self.written:
            parts.append(f"{len(self.written)} Änderungen nachgetragen")
        if self.conflicts:
            parts.append(f"{len(self.conflicts)} Konflikte")
        if self.unavailable:
            parts.append("Helmut weiterhin nicht erreichbar")
        return ", ".join(parts) or "nichts nachzutragen"


def replay(queue: CatchUpQueue, client: HelmutClient) -> CatchUpReport:
    """Nur ein Nachtragen zur Zeit (Threads der App und Kommandozeile, Review A8)."""
    report = CatchUpReport()
    with queue.replaying() as mine:
        if not mine:
            report.busy = True
        else:
            _replay(queue, client, report)
    return report


def _replay(queue: CatchUpQueue, client: HelmutClient, report: CatchUpReport) -> None:
    blocked = {e.project_id for e in queue.conflicts()}
    for e in queue.pending():
        if e.project_id in blocked:
            continue
        try:
            if e.kind == TAKE:
                current = client.get(e.project_id)
                try:
                    mixstate.check_take(current.mix, current.locked, e.expect_mixer, e.expect_room)
                except mixstate.ConflictError as err:
                    blocked.add(e.project_id)
                    report.conflicts.append(f"{e.describe()} – {err}")
                    queue.mark(e, CONFLICT, f"{err} – nicht übernommen")
                    continue
                MixService(client, e.expect_mixer, e.expect_room).take(e.project_id)
                message = "nachgetragen"
            elif e.kind == MIX:
                # Lesen–Ändern–Schreiben unter derselben Sperre wie Zustand und Raumstatus (Review A7)
                with MixService._write_lock:
                    current = client.get(e.project_id)
                    verdict, message = decide_mix(e, current.mix)
                    if verdict == APPLY:
                        client.write_mix(current, apply_changes(current.mix, e))
                        message = "nachgetragen"
                if verdict == CONFLICT:
                    blocked.add(e.project_id)
                    report.conflicts.append(f"{e.describe()} – {message}")
                    queue.mark(e, CONFLICT, message)
                    log.warning("Nachtrag-Konflikt: %s – %s", e.describe(), message)
                    continue
            elif e.kind == LOCK:
                with MixService._write_lock:
                    # Entsperren aus einer Freigabe: hat den Beitrag inzwischen jemand übernommen, ist die Sperre
                    # dessen – nicht aufheben (Review B8)
                    if not e.locked and client.get(e.project_id).mix.state in mixstate.OWNED:
                        message = "inzwischen neu übernommen – Sperre bleibt"
                    else:
                        client.set_locked(e.project_id, e.locked)
                        message = "nachgetragen"
            elif e.kind == ASSET:
                if any((a.get("filepath") or a.get("path")) == e.path for a in client.assets(e.project_id)):
                    message = "Asset war schon da"
                else:
                    client.add_asset(e.project_id, e.path, e.asset_kind)
                    message = "nachgetragen"
            elif e.kind == NOTIFY:
                client.notify(e.user, e.subject, e.text)
                message = "nachgetragen"
            else:
                raise HelmutError(f"unbekannte Art „{e.kind}“")
        except HelmutUnavailableError:
            queue.mark(e, e.status, e.message, attempts=e.attempts + 1)
            report.unavailable = True
            break
        except HelmutError as err:
            attempts = e.attempts + 1
            if attempts >= MAX_ATTEMPTS:
                blocked.add(e.project_id)
                report.conflicts.append(f"{e.describe()} – {err}")
                queue.mark(e, CONFLICT, f"Helmut lehnt ab: {err}", attempts=attempts)
            else:
                queue.mark(e, e.status, f"Versuch {attempts}: {err}", attempts=attempts)
            continue
        report.written.append(e.describe())
        queue.mark(e, DONE, message)
        log.info("Nachgetragen: %s (%s)", e.describe(), message)
    if report.written or report.conflicts:
        queue.forget_done()
