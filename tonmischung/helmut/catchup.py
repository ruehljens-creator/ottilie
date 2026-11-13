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
from tonmischung.core.catchup import (APPLY, ASSET, CONFLICT, DONE, LOCK, MIX, NOTIFY, TAKE, CatchUpQueue, Entry,
                                      apply_changes, current_user, decide_mix)
from tonmischung.helmut.client import HelmutAuthError, HelmutClient, HelmutError, HelmutUnavailableError
from tonmischung.helmut.service import MixService

log = logging.getLogger("tonmischung.nachtrag")
MAX_ATTEMPTS = 5          # Helmut lehnt einen Eintrag wiederholt ab → als Konflikt zeigen
FOLDER = "uebergabeordner"
MODE_LABEL = {"helmut_api": "Helmut-API", FOLDER: "Übergabeordner"}


@dataclass
class CatchUpReport:
    written: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unavailable: bool = False
    rejected: str = ""                 # Helmut lehnt die Anmeldung ab: Nachtrag wartet, nichts wird Konflikt
    busy: bool = False
    mode: str = ""
    waiting: int = 0                   # Einträge anderer Benutzer bzw. mit anderem Ziel – warten, zählen nicht

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
            parts.append("Übergabeordner weiterhin nicht erreichbar" if self.mode == FOLDER
                         else "Helmut weiterhin nicht erreichbar")
        if self.waiting:
            parts.append(f"{self.waiting} warten auf einen anderen Benutzer dieses Macs")
        if self.rejected:
            parts.append(f"wartet: {self.rejected}")
        return ", ".join(parts) or "nichts nachzutragen"


def replay(queue: CatchUpQueue, client: HelmutClient, only_own: bool = False,
           project_id: str | None = None) -> CatchUpReport:
    """Nur ein Nachtragen zur Zeit (Threads der App und Kommandozeile, Review A8). `only_own` (Takt der App): nur
    Einträge dieses macOS-Benutzers – die anderer Benutzer trägt man im Fenster bewusst nach (Sicherheit 13.11.2026).
    `project_id` (vor Übernehmen/Zurückliefern): nur die Einträge dieses Films (Nachprüfung 13.11.2026: vorher trug
    jedes Übernehmen auch fremde Einträge anderer Filme nach)."""
    report = CatchUpReport(mode=getattr(client, "mode", "") or "helmut_api")    # wie `factory.queue_target`
    with queue.replaying() as mine:
        if not mine:
            report.busy = True
        else:
            _replay(queue, client, report, only_own, project_id)
    return report


def folder_key(project_id: str) -> str:
    """Kennung des Übergabeordners einer Film-ID („nord/Film“ → „nord“, erster Ordner → "")."""
    return project_id.split("/", 1)[0] if "/" in project_id else ""


def wrong_target(e: Entry, mode: str, notify_target: str) -> str:
    """Grund, warum dieser Prozess den Eintrag nicht nachtragen darf ("" = darf). Ausfall 13.11.2026: ein zweiter
    Benutzer des Macs trug sonst mit seinen eigenen Einstellungen nach – eine Fertigmeldung galt als erledigt, obwohl
    sie bei ihm abgeschaltet war, oder ging an sein Ziel."""
    who = e.benutzer or "der Benutzer, der geliefert hat"
    if e.betriebsart and mode and e.betriebsart != mode:
        return f"wartet auf die Betriebsart „{MODE_LABEL.get(e.betriebsart, e.betriebsart)}“"
    if e.kind == ASSET and e.asset_kind == "metadaten" and mode == FOLDER:
        if not notify_target:
            return f"Fertigmeldung ist für diesen Benutzer nicht eingerichtet – nachtragen kann {who}"
        if e.meldung and e.meldung != notify_target:
            return f"Fertigmeldung ist für diesen Benutzer anders eingerichtet – nachtragen kann {who}"
    return ""


def _replay(queue: CatchUpQueue, client: HelmutClient, report: CatchUpReport, only_own: bool = False,
            project_id: str | None = None) -> None:
    blocked = {e.project_id for e in queue.conflicts() if e.kind != NOTIFY}
    me = current_user()
    try:
        note = client.notify_target() if report.mode == FOLDER else ""
    except Exception:  # noqa: BLE001 – ohne Fertigmeldung bzw. Helmut-Client
        note = ""
    down: set[str] = set()                     # Übergabeordner, die in diesem Durchlauf nicht erreichbar waren
    for e in queue.pending():
        if e.project_id in blocked or (report.mode == FOLDER and folder_key(e.project_id) in down) or (
                project_id is not None and e.project_id != project_id):
            continue
        if only_own and e.benutzer != me:                # ohne Urheber: unbekannt, nicht „eigen“ (Nachprüfung)
            blocked.add(e.project_id)          # Reihenfolge je Film: spätere Einträge warten mit
            report.waiting += 1
            continue
        why = wrong_target(e, report.mode, note)
        if why:
            if not (e.kind == ASSET and e.asset_kind == "metadaten"):
                blocked.add(e.project_id)      # die Fertigmeldung ist der letzte Schritt und hält nichts auf
            report.waiting += 1
            if e.message != why:
                queue.mark(e, e.status, why)
            continue
        try:
            if e.kind == TAKE:
                current = client.get(e.project_id)
                try:
                    blocker = mixstate.havarie_blocker(current.mix, e.expect_mixer, e.expect_room, replay=True) \
                        if e.havarie else ""
                    if blocker:
                        raise mixstate.ConflictError(f"Havarie nicht nachgetragen – {blocker}")
                    mixstate.check_take(current.mix, current.locked, e.expect_mixer, e.expect_room)
                except mixstate.ConflictError as err:
                    blocked.add(e.project_id)
                    report.conflicts.append(f"{e.describe()} – {err}")
                    queue.mark(e, CONFLICT, f"{err} – nicht übernommen")
                    continue
                try:
                    MixService(client, e.expect_mixer, e.expect_room).take(e.project_id)
                except mixstate.ConflictError as err:     # zwischen Prüfen und Sperren kam jemand dazwischen
                    blocked.add(e.project_id)
                    report.conflicts.append(f"{e.describe()} – {err}")
                    queue.mark(e, CONFLICT, f"{err} – nicht übernommen")
                    continue
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
                if e.expect_mixer:          # nur an den eigenen (oder selbst ausgelieferten) Beitrag – Ausfall R2
                    current = client.get(e.project_id).mix
                    if not mixstate.may_deliver(current, e.expect_mixer, e.expect_room):
                        where = (f"inzwischen bei {current.mixer} ({current.room or '?'})" if current.mixer
                                 else "inzwischen freigegeben")
                        message = (f"nicht in Helmut eingetragen – der Beitrag ist {where}. Die Datei liegt im "
                                   "Übergabeordner; bei Bedarf Bescheid geben, dann Verwerfen")
                        blocked.add(e.project_id)
                        report.conflicts.append(f"{e.describe()} – {message}")
                        queue.mark(e, CONFLICT, message)
                        continue
                if any((a.get("filepath") or a.get("path")) == e.path for a in client.assets(e.project_id)):
                    message = "Asset war schon da"
                else:
                    result = client.add_asset(e.project_id, e.path, e.asset_kind)
                    message = ("überholt – eine neuere Lieferung ist schon gemeldet"
                               if isinstance(result, dict) and result.get("ueberholt") else "nachgetragen")
            elif e.kind == NOTIFY:
                client.notify(e.user, e.subject, e.text)
                message = "nachgetragen"
            else:
                raise HelmutError(f"unbekannte Art „{e.kind}“")
        except HelmutUnavailableError as err:
            if getattr(err, "deferrable", False):
                # Betriebsart Übergabeordner: Fertigmeldungsdienst weg – nur dieser Eintrag wartet, die anderen laufen
                # weiter, kein Versuch gezählt (Review 4, Ausfall F3)
                queue.mark(e, e.status, "Fertigmeldung noch nicht zugestellt – wird wiederholt")
                log.info("Nachtrag: %s – %s", e.describe(), err)
                continue
            if e.kind == NOTIFY and err.sent_unknown:
                # Nachricht ging hinaus, die Antwort kam zu spät: nicht wiederholen – sonst bekäme der Empfänger sie
                # jede Minute neu (Ausfall R5)
                message = ("wahrscheinlich angekommen (Helmut hat den Empfang nicht bestätigt) – wird nicht noch "
                           "einmal geschickt; im Zweifel kurz Bescheid geben")
                log.warning("Nachtrag: %s – %s", e.describe(), message)
            elif err.status is not None and _answers(client, report):
                # Helmut antwortet, nur dieser Eintrag bekommt eine 5xx-Antwort (z. B. Dauer-503 hinter einem Proxy):
                # wie eine Ablehnung zählen – sein Beitrag wartet, die anderen laufen weiter, nach MAX_ATTEMPTS
                # Konflikt (Ausfall R5). Zeitlimit/Netz (ohne Status) bleibt ein Ausfall – sonst würde ein langsames
                # oder flatterndes Netz falsche Konflikte erzeugen (Ausfall R6).
                log.warning("Nachtrag: %s – Helmut antwortet, nur hierauf nicht: %s", e.describe(), err)
                _count_rejection(queue, report, blocked, e, HelmutError("Serverfehler bei diesem Eintrag"))
                continue
            elif report.rejected:
                break
            elif report.mode == FOLDER:
                # Mehrere Übergabeordner (Ausfall 13.11.2026): nur die Einträge dieses Ordners warten, die anderen
                # laufen weiter
                report.unavailable = True
                down.add(folder_key(e.project_id))
                continue
            else:                                 # echter Ausfall: zählt nicht als Versuch – sonst würde nach einem
                report.unavailable = True         # langen Ausfall die erste Ablehnung sofort ein Konflikt (Ausfall R4)
                break
        except HelmutAuthError as err:
            # Anmeldung abgelehnt oder nicht möglich (Token, Kennwort, Schlüsselbund): warten, nicht zählen – sonst
            # wurden nach fünf Takten alle Nachträge zu Konflikten (Review 10.11.2026). Lehnt Helmut nur diesen Aufruf
            # ab, kommt ein gewöhnlicher HelmutError und wird unten gezählt.
            report.rejected = str(err)
            break
        except HelmutError as err:
            # Übergabeordner: eine abgelehnte Fertigmeldung hält die übrigen Einträge des Films nicht auf (Review 3,
            # 2. Runde) – sie ist der letzte Schritt, die Dateien liegen schon
            done_msg = getattr(client, "mode", "") == "uebergabeordner" and e.kind == ASSET \
                and e.asset_kind == "metadaten"
            _count_rejection(queue, report, blocked, e, err, block=not done_msg)
            continue
        report.written.append(e.describe())
        queue.mark(e, DONE, message)
        log.info("Nachgetragen: %s (%s)", e.describe(), message)
    if report.written or report.conflicts:
        queue.forget_done()


def _plain(err: Exception) -> str:
    """Kurzer Grund ohne Methode und Pfad (die stehen im Protokoll): Statuscode bzw. „Serverfehler“."""
    status = getattr(err, "status", None)
    text = str(err)
    if text == "Serverfehler bei diesem Eintrag":
        return text
    return f"Code {status}" if status else "Fehler"


def _answers(client: HelmutClient, report: CatchUpReport) -> bool:
    """Antwortet Helmut überhaupt (kurze Anmeldeprüfung ohne Wiederholung)? Anmeldung abgelehnt → `report.rejected`."""
    try:
        client.check_auth(timeout_s=3.0)
    except HelmutAuthError as err:
        report.rejected = str(err)
        return False
    except HelmutError:
        return False
    return True


def _count_rejection(queue: CatchUpQueue, report: CatchUpReport, blocked: set, e, err: Exception,
                     block: bool = True) -> None:
    """Helmut lehnt den Eintrag ab: Versuch zählen, nach MAX_ATTEMPTS Konflikt. Die folgenden Einträge desselben
    Beitrags warten schon jetzt – sonst stünde z. B. „ausgeliefert“ in Helmut, obwohl ein WAV fehlt (Ausfall R3)."""
    if e.kind != NOTIFY and block:
        blocked.add(e.project_id)
    attempts = e.attempts + 1
    if attempts >= MAX_ATTEMPTS:
        report.conflicts.append(f"{e.describe()} – {err}")
        log.warning("Nachtrag-Konflikt: %s – %s", e.describe(), err)
        queue.mark(e, CONFLICT, f"Helmut nimmt diese Änderung nicht an ({_plain(err)}) – Helmut-Betreuung anrufen; "
                                "Dateien liegen im Übergabeordner", attempts=attempts)
    else:
        queue.mark(e, e.status, f"Versuch {attempts} von {MAX_ATTEMPTS}: Helmut nimmt es nicht an ({_plain(err)})",
                   attempts=attempts)
