"""Übernehmen, Zustand setzen und Übernahme aufheben – mit Konfliktprüfung.

Vor jedem Schreiben wird der aktuelle Stand frisch aus Helmut gelesen. Ist der Beitrag
inzwischen anders zugewiesen, wird das gemeldet und nichts überschrieben.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from tonmischung.core import mixstate
from tonmischung.core.mixstate import ConflictError, MixState
from tonmischung.core.catchup import ASSET, HINT, HINT_AUTH, NOTIFY, CatchUpQueue
from tonmischung.helmut.client import HelmutAuthError, HelmutClient, HelmutError, HelmutUnavailableError

from tonmischung.helmut.mapping import Contribution

log = logging.getLogger(__name__)


class NotSettledError(HelmutUnavailableError):
    """Ältere Nachträge dieses Beitrags stehen noch aus (Helmut lehnt ab, Nachtrag läuft gerade, Helmut weg). Gilt als
    Ausfall: die *_or_queue-Wege reihen dahinter ein – die Reihenfolge bleibt, nichts geht verloren."""


# Was eingereiht wird: Helmut nicht erreichbar – oder die Anmeldung abgelehnt (der Nachtrag wartet dann, bis sie wieder
# gilt, Review 10.11.2026). Der Nachtrag prüft vor dem Schreiben, ob der Beitrag noch demselben Mischer gehört.
QUEUED = (HelmutUnavailableError, HelmutAuthError)


CONFLICT_OPEN = ("Für diesen Beitrag gibt es einen Konflikt im Nachtrag (oben in der Beitragsliste) – dort verwerfen "
                 "oder klären, dann erneut versuchen")
STALE_WAIT_S = 3.0          # Sperre ohne Besitzer: so lange warten, ob nicht gerade jemand übernimmt
REPLAY_BUSY = "Ottilie trägt gerade Änderungen in Helmut nach – in ein paar Sekunden erneut versuchen"
HINT_WAITING = ("Deine Änderung wird nachgetragen, sobald eine ältere Änderung an diesem Beitrag in Helmut steht "
                "(siehe „Nachtrag“ in der Beitragsliste)")


def _hint(error: Exception) -> str:
    if isinstance(error, NotSettledError):
        return HINT_WAITING
    return HINT_AUTH if isinstance(error, HelmutAuthError) else HINT


def _delivered(current: MixState, video_id: str | None) -> MixState:
    after = mixstate.after_delivery(current)
    return after if video_id is None else replace(after, video_id=video_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WriteBusyError(HelmutError):
    """Das Set „Mischung“ wird gerade von dieser App geschrieben – der Raumstatus wartet nicht darauf."""


class MixService:
    # Lesen–Ändern–Schreiben des Sets „Mischung“ nie gleichzeitig aus derselben App (z. B. Fortschritt
    # für den Raumstatus und Zustand des Auftrags) – sonst überschreibt einer den anderen.
    _write_lock = threading.RLock()

    def __init__(self, client: HelmutClient, mixer: str, room: str,
                 now: Callable[[], str] = _now, queue: CatchUpQueue | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.client = client
        self._sleep = sleep
        self.mixer = mixer
        self.room = room
        self._now = now
        self.queue = queue                  # Nachtrag-Warteschlange; None = Ausfall ist ein Fehler

    def take(self, project_id: str) -> Contribution:
        self.settle(project_id)
        with self._write_lock:
            return self._take(project_id)

    def set_state(self, project_id: str, state: str, **changes) -> Contribution:
        self.settle(project_id)
        with self._write_lock:
            return self._set_state(project_id, state, **changes)

    def mark_delivered(self, project_id: str, written: Callable[[], None] = lambda: None,
                       video_id: str | None = None) -> Contribution:
        """Nach dem Zurückliefern: Zustand „ausgeliefert“, dann entsperren – der Beitrag ist frei, Ottilie fragt vor
        dem nächsten Holen nicht mehr nach der offenen Session (Jens 09.10.2026). `written`: Zustand steht in Helmut.
        `video_id`: mit „ausgeliefert“ ins Set „Mischung“ (None = bleibt, wie es ist)."""
        with self._write_lock:
            current = self.client.get(project_id)
            self.check_deliverer(current)
            updated = self.client.write_mix(current, _delivered(current.mix, video_id))
            written()
            if current.locked:
                self.client.set_locked(project_id, False)
            log.info("Beitrag %s ausgeliefert von %s (%s)", project_id, self.mixer, self.room)
            return replace(updated, locked=False)

    def release(self, project_id: str, reason: str, keep: str = mixstate.KEEP_NEW) -> Contribution:
        self.settle(project_id)
        with self._write_lock:
            return self._release(project_id, reason, keep)

    def set_progress(self, project_id: str, progress: int, activity: str) -> Contribution | None:
        """Raumstatus: Fortschritt und Tätigkeit schreiben, Zustand unverändert; gehört der Beitrag nicht
        (mehr) diesem Mischer, geschieht nichts. Schreibt gerade ein Auftrag, wartet der Raumstatus nicht
        (`WriteBusyError`, Review B9) – er ist nur Anzeige und meldet sich beim nächsten Takt wieder."""
        if not self._write_lock.acquire(blocking=False):
            raise WriteBusyError("Set „Mischung“ wird gerade geschrieben")
        try:
            current = self.client.get(project_id)
            try:
                self.check_owner(current)
            except ConflictError:
                return None
            if current.mix.progress == progress and current.mix.activity == activity:
                return current
            return self.client.write_mix(current, replace(current.mix, progress=progress, activity=activity))
        finally:
            self._write_lock.release()

    def _take(self, project_id: str) -> Contribution:
        """Übernehmen: prüfen, sperren, Set „Mischung“ schreiben."""
        current = self.client.get(project_id)
        if current.locked and mixstate.stale_lock(current.mix):
            # Sieht genauso aus wie ein anderer Raum mitten im Übernehmen (gesperrt, Set noch nicht geschrieben):
            # kurz warten und neu lesen, erst dann als verwaist werten (Prüfer 11.11.2026)
            self._sleep(STALE_WAIT_S)
            current = self.client.get(project_id)
            if current.locked and mixstate.stale_lock(current.mix):
                log.warning("Beitrag %s gesperrt ohne Besitzer – wird von %s (%s) übernommen", project_id,
                            self.mixer, self.room)
        mixstate.check_take(current.mix, current.locked, self.mixer, self.room)
        stale = current.locked and mixstate.stale_lock(current.mix)
        locked_here = not current.locked
        try:
            if locked_here:                 # im try: kommt die Antwort nicht, wird die eigene Sperre zurückgenommen
                try:
                    self.client.set_locked(project_id, True)
                except HelmutUnavailableError:
                    raise
                except HelmutError as e:    # 4xx abgelehnt (z. B. gerade fremd gesperrt): nicht unsere Sperre;
                    if e.status is not None and e.status < 500:     # bei 5xx kann sie trotzdem stehen → zurücknehmen
                        locked_here = False
                    raise
            # nach dem Sperren noch einmal lesen: hat sich jemand dazwischengedrängt?
            current = self.client.get(project_id)
            mixstate.check_take(current.mix, False, self.mixer, self.room)
            new = mixstate.after_take(current.mix, self.mixer, self.room, self._now())
            updated = self.client.write_mix(current, new)
            if stale:                       # Sperre ohne Besitzer übernommen: wer sperrte, könnte noch schreiben –
                self._sleep(STALE_WAIT_S)   # nochmal warten, dann nachprüfen (Ausfall R8: langsame Antwort)
            after = self.client.get(project_id).mix      # zwei Räume zugleich: wer zuletzt schrieb, gewinnt
            if not mixstate.owned_by(after, self.mixer, self.room):
                raise ConflictError(f"Beitrag wurde gleichzeitig von {after.mixer or 'jemand'} "
                                    f"({after.room or '?'}) übernommen – hier nicht übernommen")
        except Exception:
            if locked_here:
                self._undo_lock(project_id, current.title)
            raise
        log.info("Beitrag %s übernommen von %s (%s)", project_id, self.mixer, self.room)
        return updated

    def _undo_lock(self, project_id: str, title: str) -> None:
        """Übernehmen nach dem Sperren gescheitert: die eigene Sperre zurücknehmen (Review B8: vorher blieb der
        Beitrag gesperrt). Gehört er inzwischen jemandem, ist es dessen Sperre – dann bleibt sie. Geht das Entsperren
        nicht, wird es nachgetragen (Review 10.11.2026; der Nachtrag lässt eine neue Übernahme unangetastet)."""
        try:
            if self.client.get(project_id).mix.state in mixstate.OWNED:
                return
            self.client.set_locked(project_id, False)
            log.info("Beitrag %s: Übernehmen gescheitert – Sperre zurückgenommen", project_id)
        except HelmutError as e:
            if self.queue is not None:
                self.queue.add_lock(project_id, title, False)
                log.warning("Beitrag %s: Übernehmen gescheitert, Entsperren eingereiht (%s)", project_id, e)
                return
            log.error("Beitrag %s bleibt in Helmut gesperrt: Übernehmen gescheitert, Entsperren nicht möglich (%s)",
                      project_id, e)

    def _set_state(self, project_id: str, state: str, **changes) -> Contribution:
        """Zustand fortschreiben (importiert, in_mischung, exportiert …) – nur durch den Besitzer."""
        current = self.client.get(project_id)
        self.check_owner(current)
        return self.client.write_mix(current, mixstate.with_state(current.mix, state, **changes))

    def _release(self, project_id: str, reason: str, keep: str = mixstate.KEEP_NEW) -> Contribution:
        """Übernahme aufheben: Set zurücksetzen, Grund und Wunsch fürs nächste Öffnen festhalten,
        entsperren. Den letzten Stand gibt es nur, wenn er erreichbar ist (`keep_options`)."""
        current = self.client.get(project_id)
        allowed = mixstate.keep_options(current.mix, here=current.mix.room == self.room)
        if keep not in allowed:
            raise ConflictError("Den letzten gespeicherten Stand gibt es hier nicht (keine Session oder nur "
                                f"lokal in {current.mix.room or 'einem anderen Raum'}) – nur „Neu importieren“")
        new = mixstate.after_release(current.mix, reason, self._now(), keep)
        updated = self.client.write_mix(current, new)
        self.client.set_locked(project_id, False)
        # ANNAHME A1 (Plan M4-Rest): jeder Mischer darf freigeben; festgehalten wird, wer es tat
        log.info("Beitrag %s freigegeben von %s (%s): %s, nächstes Öffnen: %s (vorher %s, %s)",
                 project_id, self.mixer, self.room, reason, keep, current.mix.mixer, current.mix.room)
        return updated

    def duplicate(self, project_id: str, title: str, keep_mix: bool = False, take: bool = False) -> Contribution:
        """Duplikat anlegen: neues Projekt mit derselben Übergabe; das Original bleibt unverändert.

        Was Helmut beim Duplizieren mitnimmt, ist offen (Funktionsbeschreibung S. 29). Deshalb wird
        das Set „Mischung“ am Duplikat vollständig neu geschrieben und eine fehlende Übergabe mit
        denselben Dateien registriert (keine Kopie der Dateien).
        """
        title = (title or "").strip()
        if not title:
            raise ValueError("Bitte einen Titel für das Duplikat angeben")
        original = self.client.get(project_id)
        mix = mixstate.for_duplicate(original.title, original.id, original.mix, keep_mix)
        handover = self.client.handover(project_id)
        dup = self.client.duplicate(original, title)
        dup = self.client.write_mix(dup, mix)
        if dup.locked:
            self.client.set_locked(dup.id, False)
        have = self.client.handover(dup.id)
        for kind in ("aaf", "video", "manifest"):
            asset = getattr(handover, kind)
            if asset is not None and getattr(have, kind) is None:
                self.client.add_handover_asset(dup.id, asset)
        log.info("Duplikat %s „%s“ von %s angelegt durch %s (%s), Mischstand: %s",
                 dup.id, title, project_id, self.mixer, self.room, keep_mix)
        return self.take(dup.id) if take else self.client.get(dup.id)

    # ── Bei Ausfall nachtragen (Plan M2-Nachtrag) ───────────────────────────
    def set_state_or_queue(self, known: Contribution, state: str, **changes) -> tuple[Contribution, str]:
        """Wie `set_state`; ist Helmut nicht erreichbar, wird die Änderung eingereiht.

        `known` ist der Stand nach der eigenen Übernahme. Rückgabe: (Beitrag, Hinweis) – der Hinweis ist
        leer, wenn Helmut sofort geschrieben wurde.
        """
        try:
            return self.set_state(known.id, state, **changes), ""
        except QUEUED as e:
            if self.queue is None:
                raise
            new = mixstate.with_state(known.mix, state, **changes)
            self.queue.add_mix(known.id, known.title, known.mix, new, self.mixer, self.room)
            log.warning("%s – Zustand „%s“ für %s eingereiht", e, state, known.id)
            return Contribution(known.id, known.title, known.show, known.locked, new, known.raw), _hint(e)

    def deliver_or_queue(self, known: Contribution, video_id: str | None = None,
                         queue_only: bool = False) -> tuple[Contribution, str]:
        """`mark_delivered`; bei Ausfall einreihen (Set „Mischung“, dann entsperren). Rückgabe: (Beitrag, Hinweis).
        Fällt Helmut zwischen Zustand und Entsperren aus, wird nur das Entsperren nachgetragen."""
        after = _delivered(known.mix, video_id)
        if queue_only and self.queue is not None:       # in diesem Lauf schon eingereiht: Reihenfolge halten
            self.queue.add_mix(known.id, known.title, known.mix, after, self.mixer, self.room)
            self.queue.add_lock(known.id, known.title, False)
            return replace(known, locked=False, mix=after), ""     # der erste Hinweis dieses Laufs genügt
        written: list[bool] = []
        try:
            return self.mark_delivered(known.id, lambda: written.append(True), video_id), ""
        except QUEUED as e:
            if self.queue is None:
                raise
            if not written:
                self.queue.add_mix(known.id, known.title, known.mix, after, self.mixer, self.room)
            self.queue.add_lock(known.id, known.title, False)
            log.warning("%s – „ausgeliefert“ für %s eingereiht", e, known.id)
            return replace(known, locked=False, mix=after), _hint(e)

    def release_or_queue(self, known: Contribution, reason: str, keep: str) -> str:
        """Freigeben; bei Ausfall einreihen (Set „Mischung“, dann entsperren). Rückgabe: Hinweis."""
        try:
            self.release(known.id, reason, keep)
            return ""
        except QUEUED as e:
            if self.queue is None:
                raise
            new = mixstate.after_release(known.mix, reason, self._now(), keep)
            self.queue.add_mix(known.id, known.title, known.mix, new, self.mixer, self.room)
            self.queue.add_lock(known.id, known.title, False)
            return _hint(e)

    def add_asset_or_queue(self, known: Contribution, path: str, kind: str, queue_only: bool = False) -> str:
        """Asset anmelden; bei Ausfall – oder mit `queue_only`, weil in diesem Lauf schon etwas wartet – einreihen."""
        if queue_only and self.queue is not None:
            self.queue.add_asset(known.id, known.title, path, kind, self.mixer, self.room)
            return ""                                   # der erste Hinweis dieses Laufs genügt
        try:
            self.client.add_asset(known.id, path, kind)
            return ""
        except QUEUED as e:
            if self.queue is None:
                raise
            self.queue.add_asset(known.id, known.title, path, kind, self.mixer, self.room)
            return _hint(e)

    def settle(self, project_id: str) -> None:
        """Vor dem Übernehmen und Zurückliefern: offene Nachträge dieses Beitrags erst schreiben – sonst überholte eine
        alte eingereihte Lieferung das Neue (Ausfall 10.11.2026). Konflikt → ConflictError; antwortet Helmut
        nicht, wie ein Ausfall (die *_or_queue-Wege reihen dann dahinter ein, die Reihenfolge bleibt)."""
        if self.queue is None:
            return

        folder_mode = getattr(self.client, "mode", "") == "uebergabeordner"

        def counts(e) -> bool:
            # Betriebsart Übergabeordner: eine noch nicht zugestellte Fertigmeldung hält nichts auf – die Dateien liegen
            # schon, sie ist der letzte Schritt (Review 4, Ausfall F3)
            return e.kind != NOTIFY and not (folder_mode and e.kind == ASSET and e.asset_kind == "metadaten")

        def mine(entries) -> bool:
            return any(e.project_id == project_id and counts(e) for e in entries)
        if mine(self.queue.conflicts()):
            raise ConflictError(CONFLICT_OPEN)
        if not mine(self.queue.pending()):
            return
        from tonmischung.helmut.catchup import replay     # spät: catchup braucht MixService
        report = replay(self.queue, self.client, project_id=project_id)
        if mine(self.queue.conflicts()):
            raise ConflictError(CONFLICT_OPEN)
        waiting = [e for e in self.queue.pending() if e.project_id == project_id and counts(e)]
        if not waiting:
            return
        if report.rejected:
            raise HelmutAuthError(report.rejected)
        if report.busy:
            raise NotSettledError(REPLAY_BUSY)
        if report.unavailable:
            raise NotSettledError("Ältere Änderungen an diesem Beitrag sind noch nicht in Helmut – Helmut antwortet "
                                  "nicht")
        raise NotSettledError("Die vorige Änderung an diesem Beitrag hängt noch in Helmut (siehe „Nachtrag“ oben in "
                              "der Beitragsliste). Muss eine neue Fassung raus: dort die alten Einträge verwerfen, "
                              "dann erneut versuchen.")

    def check_deliverer(self, current: Contribution) -> None:
        """Wie `check_owner`, aber auch für den, der den Beitrag ausgeliefert hat (erneut zurückliefern)."""
        if not mixstate.may_deliver(current.mix, self.mixer, self.room):
            self.check_owner(current)

    def check_owner(self, current: Contribution) -> None:
        mix: MixState = current.mix
        if not mixstate.owned_by(mix, self.mixer, self.room):
            raise ConflictError(
                f"Beitrag gehört nicht (mehr) {self.mixer} ({self.room}): "
                f"Zustand „{mix.state}“, Mischer „{mix.mixer}“, Raum „{mix.room}“")
