"""Übernehmen, Zustand setzen und Übernahme aufheben – mit Konfliktprüfung.

Vor jedem Schreiben wird der aktuelle Stand frisch aus Helmut gelesen. Ist der Beitrag
inzwischen anders zugewiesen, wird das gemeldet und nichts überschrieben.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from tonmischung.core import mixstate
from tonmischung.core.mixstate import ConflictError, MixState
from tonmischung.core.catchup import HINT, CatchUpQueue
from tonmischung.helmut.client import HelmutClient, HelmutError, HelmutUnavailableError
from tonmischung.helmut.mapping import Contribution

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WriteBusyError(HelmutError):
    """Das Set „Mischung“ wird gerade von dieser App geschrieben – der Raumstatus wartet nicht darauf."""


class MixService:
    # Lesen–Ändern–Schreiben des Sets „Mischung“ nie gleichzeitig aus derselben App (z. B. Fortschritt
    # für den Raumstatus und Zustand des Auftrags) – sonst überschreibt einer den anderen.
    _write_lock = threading.RLock()

    def __init__(self, client: HelmutClient, mixer: str, room: str,
                 now: Callable[[], str] = _now, queue: CatchUpQueue | None = None) -> None:
        self.client = client
        self.mixer = mixer
        self.room = room
        self._now = now
        self.queue = queue                  # Nachtrag-Warteschlange; None = Ausfall ist ein Fehler

    def take(self, project_id: str) -> Contribution:
        with self._write_lock:
            return self._take(project_id)

    def set_state(self, project_id: str, state: str, **changes) -> Contribution:
        with self._write_lock:
            return self._set_state(project_id, state, **changes)

    def mark_delivered(self, project_id: str, written: Callable[[], None] = lambda: None) -> Contribution:
        """Nach dem Zurückliefern: Zustand „ausgeliefert“, dann entsperren – der Beitrag ist frei, Ottilie fragt vor
        dem nächsten Holen nicht mehr nach der offenen Session (Jens 09.10.2026). `written`: Zustand steht in Helmut."""
        with self._write_lock:
            current = self.client.get(project_id)
            self.check_deliverer(current)
            updated = self.client.write_mix(current, mixstate.after_delivery(current.mix))
            written()
            if current.locked:
                self.client.set_locked(project_id, False)
            log.info("Beitrag %s ausgeliefert von %s (%s)", project_id, self.mixer, self.room)
            return replace(updated, locked=False)

    def release(self, project_id: str, reason: str, keep: str = mixstate.KEEP_NEW) -> Contribution:
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
        mixstate.check_take(current.mix, current.locked, self.mixer, self.room)
        locked_here = not current.locked
        if locked_here:
            self.client.set_locked(project_id, True)
        try:
            # nach dem Sperren noch einmal lesen: hat sich jemand dazwischengedrängt?
            current = self.client.get(project_id)
            mixstate.check_take(current.mix, False, self.mixer, self.room)
            new = mixstate.after_take(current.mix, self.mixer, self.room, self._now())
            updated = self.client.write_mix(current, new)
        except Exception:
            if locked_here:
                self._undo_lock(project_id)
            raise
        log.info("Beitrag %s übernommen von %s (%s)", project_id, self.mixer, self.room)
        return updated

    def _undo_lock(self, project_id: str) -> None:
        """Übernehmen nach dem Sperren gescheitert: die eigene Sperre zurücknehmen (Review B8: vorher blieb der
        Beitrag gesperrt). Gehört er inzwischen jemandem, ist es dessen Sperre – dann bleibt sie."""
        try:
            if self.client.get(project_id).mix.state in mixstate.OWNED:
                return
            self.client.set_locked(project_id, False)
            log.info("Beitrag %s: Übernehmen gescheitert – Sperre zurückgenommen", project_id)
        except HelmutError as e:
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
        except HelmutUnavailableError:
            if self.queue is None:
                raise
            new = mixstate.with_state(known.mix, state, **changes)
            self.queue.add_mix(known.id, known.title, known.mix, new, self.mixer, self.room)
            log.warning("Helmut nicht erreichbar – Zustand „%s“ für %s eingereiht", state, known.id)
            return Contribution(known.id, known.title, known.show, known.locked, new, known.raw), HINT

    def deliver_or_queue(self, known: Contribution) -> tuple[Contribution, str]:
        """`mark_delivered`; bei Ausfall einreihen (Set „Mischung“, dann entsperren). Rückgabe: (Beitrag, Hinweis).
        Fällt Helmut zwischen Zustand und Entsperren aus, wird nur das Entsperren nachgetragen."""
        after = mixstate.after_delivery(known.mix)
        written: list[bool] = []
        try:
            return self.mark_delivered(known.id, lambda: written.append(True)), ""
        except HelmutUnavailableError:
            if self.queue is None:
                raise
            if not written:
                self.queue.add_mix(known.id, known.title, known.mix, after, self.mixer, self.room)
            self.queue.add_lock(known.id, known.title, False)
            log.warning("Helmut nicht erreichbar – „ausgeliefert“ für %s eingereiht", known.id)
            return replace(known, locked=False, mix=after), HINT

    def release_or_queue(self, known: Contribution, reason: str, keep: str) -> str:
        """Freigeben; bei Ausfall einreihen (Set „Mischung“, dann entsperren). Rückgabe: Hinweis."""
        try:
            self.release(known.id, reason, keep)
            return ""
        except HelmutUnavailableError:
            if self.queue is None:
                raise
            new = mixstate.after_release(known.mix, reason, self._now(), keep)
            self.queue.add_mix(known.id, known.title, known.mix, new, self.mixer, self.room)
            self.queue.add_lock(known.id, known.title, False)
            return HINT

    def add_asset_or_queue(self, known: Contribution, path: str, kind: str) -> str:
        try:
            self.client.add_asset(known.id, path, kind)
            return ""
        except HelmutUnavailableError:
            if self.queue is None:
                raise
            self.queue.add_asset(known.id, known.title, path, kind)
            return HINT

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
