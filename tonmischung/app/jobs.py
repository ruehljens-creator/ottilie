"""Aufträge der Oberfläche: Holen, Zurückliefern, Stand öffnen, Havarie, Archiv-Import, Exporte – im Hintergrund,
immer nur einer.

Pro Tools hat nur eine Session offen; zwei Aufträge gleichzeitig würden sich stören. Stand, Fortschritt und offene
Frage gehen als Ereignis „job“ ans Fenster (gedrosselt); der Auftrag wartet auf die Antwort (Befehl „antworten“).
Schließt der Mischer das Fenster, läuft der Auftrag weiter – beim Öffnen holt sich das Fenster den Stand mit „auftrag“.
"""
from __future__ import annotations

import itertools
import logging
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.core.progress import Progress, Stopped

log = logging.getLogger("tonmischung.app")

_ids = itertools.count(1)


@dataclass
class Job:
    id: int
    kind: str                       # holen, zurueckliefern, wiederoeffnen, havarie, havarie_ausliefern, archiv …
    title: str
    steps: list[str]
    state: str = "laeuft"           # laeuft | frage | fertig | fehler | abgebrochen
    step: str | None = None
    fraction: float = 0.0
    text: str = ""
    question: dict | None = None
    result: Any = None
    error: str = ""
    started: float = field(default_factory=time.monotonic)
    contribution: str = ""          # Beitrag, um den es geht (Raumstatus in Helmut)
    cancel_requested: bool = False  # Abbrechen-Knopf: wirkt beim nächsten Schrittwechsel

    def snapshot(self) -> dict:
        idx = self.steps.index(self.step) if self.step in self.steps else -1
        steps = []
        for i, name in enumerate(self.steps):
            if self.state == "fertig" or i < idx:
                st = "done"
            elif i == idx:
                st = "error" if self.state in ("fehler", "abgebrochen") else "run"
            else:
                st = "wait"
            steps.append({"name": name, "state": st})
        return {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state,
                "step": self.step, "fraction": round(self.fraction, 4), "text": self.text,
                "steps": steps, "question": self.question, "result": self.result, "error": self.error,
                "contribution": self.contribution, "cancellable": self.kind in CANCELLABLE,
                "cancel_requested": self.cancel_requested}


class Cancelled(Exception):
    """Der Mischer hat in einer Frage abgebrochen."""


# Abbrechen-Knopf (Jens 04.10.2026: „um fehlerhafte Imports abzubrechen, sowohl im Havarie- als auch im Helmut-Weg“)
CANCELLABLE = {"holen", "havarie", "wiederoeffnen"}
CANCEL_HINT = ("Was schon erledigt war, bleibt so (z. B. Übernahme in Helmut, angelegte Session); gespeichert wurde "
               "danach nichts mehr. Die Session bitte prüfen, bei Bedarf den Beitrag freigeben.")


class _WindowSink:
    """Fortschritt ans Fenster – höchstens alle `every` Sekunden, Schrittwechsel sofort."""

    def __init__(self, runner: "JobRunner", job: Job, every: float = 0.25) -> None:
        self.runner, self.job, self.every = runner, job, every
        self.progress: Progress | None = None
        self._last = 0.0

    def update(self, fraction: float, text: str) -> None:
        step = self.progress.current_step if self.progress else None
        changed = step != self.job.step
        self.job.fraction, self.job.text, self.job.step = fraction, text, step
        now = time.monotonic()
        if changed or now - self._last >= self.every:
            self._last = now
            self.runner.publish(self.job)

    def close(self) -> None:
        pass


@dataclass
class JobContext:
    job: Job
    progress: Progress
    ask: Callable[[dict], Any]


class JobRunner:
    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge
        self._lock = threading.Lock()
        self._current: Job | None = None
        self._last: Job | None = None
        self._answers: dict[int, tuple[threading.Event, list]] = {}
        self.ptsl: Any = None              # PtslClient: Dialoghinweise während jedes Auftrags anzeigen (Review B21)

    def publish(self, job: Job) -> None:
        self.bridge.event("job", job.snapshot())

    def current(self) -> dict | None:
        job = self._current or self._last
        return job.snapshot() if job else None

    def running(self) -> dict | None:
        """Snapshot des laufenden Auftrags oder None."""
        job = self._current
        return job.snapshot() if job is not None else None

    def running_title(self) -> str | None:
        """Titel des laufenden Auftrags (er belegt Pro Tools) oder None."""
        job = self._current
        return job.title if job is not None else None

    def busy(self) -> bool:
        """Läuft gerade ein Auftrag?"""
        return self._current is not None

    def start(self, kind: str, title: str, steps: Sequence[tuple[str, float]],
              work: Callable[[JobContext], Any], contribution: str = "") -> dict:
        with self._lock:
            if self._current is not None:
                raise CommandError(f"Es läuft schon ein Auftrag: {self._current.title}")
            job = Job(next(_ids), kind, title, [n for n, _ in steps], contribution=contribution)
            self._current = job
        sink = _WindowSink(self, job)
        progress = Progress(steps, [sink], stop_requested=lambda: job.cancel_requested)
        sink.progress = progress
        threading.Thread(target=self._run, args=(job, progress, work), daemon=True,
                         name=f"tm-auftrag-{job.id}").start()
        self.publish(job)
        return job.snapshot()

    def _run(self, job: Job, progress: Progress, work: Callable[[JobContext], Any]) -> None:
        notices = self.ptsl.notices(progress.note) if self.ptsl is not None else nullcontext()
        try:
            with notices:
                job.result = work(JobContext(job, progress, lambda q: self.ask(job, q)))
            job.state = "fertig"
        except Cancelled as e:
            job.state, job.error = "abgebrochen", str(e) or "Abgebrochen"
        except Stopped as e:
            job.state, job.error = "abgebrochen", f"{e}. {CANCEL_HINT}"
        except Exception as e:  # noqa: BLE001 – jeder Fehler wird dem Mischer gezeigt
            log.exception("Auftrag %s gescheitert", job.title)
            job.state, job.error = "fehler", str(e) or e.__class__.__name__
        finally:
            progress.close("Fertig" if job.state == "fertig" else job.state)
            job.question = None
            with self._lock:
                self._current, self._last = None, job
            self.publish(job)

    def ask(self, job: Job, question: dict) -> Any:
        """Frage ans Fenster und auf die Antwort warten (ohne Zeitlimit – der Mischer entscheidet)."""
        qid = next(_ids)
        event, box = threading.Event(), []
        self._answers[qid] = (event, box)
        job.question, job.state = {"id": qid, **question}, "frage"
        self.publish(job)
        event.wait()
        self._answers.pop(qid, None)
        job.question, job.state = None, "laeuft"
        self.publish(job)
        if job.cancel_requested:
            raise Stopped(f"Abgebrochen bei der Frage „{question.get('title') or 'Frage'}“")
        return box[0] if box else None

    def cancel(self, job_id: Any = None) -> dict:
        """Abbrechen-Knopf: beim nächsten Schrittwechsel (oder sofort, wenn eine Frage offen ist) aufhören."""
        job = self._current
        if job is None or (job_id is not None and int(job_id) != job.id):
            raise CommandError("Kein laufender Auftrag")
        if job.kind not in CANCELLABLE:
            raise CommandError("Dieser Auftrag lässt sich nicht abbrechen")
        job.cancel_requested = True
        job.text = "Wird abgebrochen – beim nächsten sicheren Schritt …"
        self.publish(job)
        for event, _box in list(self._answers.values()):      # offene Frage beenden
            event.set()
        return job.snapshot()

    def answer(self, qid: int, value: Any) -> None:
        entry = self._answers.get(int(qid))
        if entry is None:
            raise CommandError("Diese Frage ist nicht mehr offen")
        job = self._current
        if job is not None and job.question and job.question.get("id") == int(qid):
            value = checked_answer(job.question, value)
        entry[1].append(value)
        entry[0].set()


def checked_answer(question: dict, value: Any) -> Any:
    """Antwort gegen die angebotenen Möglichkeiten prüfen (Review B26: über MCP kam „false“ als Text an – und galt als
    Ja). „true“/„false“ als Text wird zum Wahrheitswert, wenn die Möglichkeiten Wahrheitswerte sind. Fragen ohne
    Möglichkeiten (fehlende Spuren) nehmen jede Antwort."""
    options = [o.get("value") for o in question.get("options") or [] if isinstance(o, dict)]
    if not options:
        return value
    if isinstance(value, str) and all(isinstance(o, bool) for o in options):
        value = {"true": True, "ja": True, "false": False, "nein": False}.get(value.strip().lower(), value)
    if not any(type(value) is type(o) and value == o for o in options):
        shown = ", ".join(repr(o) for o in options)
        raise CommandError(f"Antwort {value!r} passt nicht zu dieser Frage – möglich: {shown}")
    return value
