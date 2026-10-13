"""Dünne Schicht über py-ptsl: ein Aufrufweg mit Zeitlimit, Sperre, Protokoll und Fehlerklassen.

Grundlage ist das Verfahren aus PunchBuddy (`engine._install_grpc_deadline`,
`engine._ptsl_call`): py-ptsl reicht kein Zeitlimit an gRPC durch, deshalb wird
jedem Aufruf eine echte Deadline mitgegeben. Ein hängender Befehl – meist ein
offener Dialog in Pro Tools – endet so mit einer klaren Meldung statt zu blockieren.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, TypeVar

from tonmischung.core.timecode import PROGRAM_START
from tonmischung.ptsl.assertions import AssertionWatch
from tonmischung.ptsl.dialogs import DASHBOARD_CLOSED, MISSING_PLUGINS, PROGRESS, UNNAMED, unnamed_kind
from tonmischung.ptsl.dialogs import pro_tools_dialogs as _real_dialogs
from tonmischung.ptsl.errors import (
    CommandFailedError, DialogOpenError, NoSessionError, NotConnectedError, ProToolsAssertionError, PtslError,
    PtslTimeoutError, UnsupportedError)

log = logging.getLogger(__name__)

UNNAMED_CHECK_S = 5.0          # titelloses Fenster: Fortschritt oder Meldung? höchstens so oft fragen (osascript)
SAVE_TIMEOUT_S = 120.0         # Speichern/Schließen großer Sessions auf dem Netz dauert länger als 30 s (Review B20)

T = TypeVar("T")


def _named(enum: Any, value: Any) -> str:
    """PTSL-Aufzählungswert als Name – py-ptsl liefert je nach Version Zahl oder Text."""
    return enum.Name(value) if isinstance(value, int) else str(value)


COMPANY_NAME = "Ottilie"
APPLICATION_NAME = "Ottilie"

_UNSUPPORTED = {"PT_UnsupportedCommand", "SDK_NotImplemented", "SDK_VersionMismatch"}
_BUSY = {"PT_HostIsBusy", "PT_HostNotReady", "PT_CommandTimeout"}
_STOPPED = {"TS_TransportStopped", "TState_TransportStopped"}


@dataclass(frozen=True)
class SessionInfo:
    name: str
    path: str


class PtslClient:
    """Alle PTSL-Aufrufe der App laufen über `call`.

    `engine_factory` ersetzt in Tests das echte `ptsl.Engine`.
    """

    def __init__(self, address: str = "localhost:31416", timeout_s: float = 30.0,
                 min_version: int | None = None, lock_wait_s: float = 10.0,
                 engine_factory: Callable[[], Any] | None = None,
                 connect_timeout_s: float = 15.0) -> None:
        self.address = address
        self.timeout_s = timeout_s
        self.connect_timeout_s = connect_timeout_s
        self.min_version = min_version
        self.lock_wait_s = lock_wait_s
        self._engine_factory = engine_factory or self._default_engine
        self._engine: Any = None
        # RLock: Code, der eine Verbindung bereits über `call` hält, darf sie im selben Thread erneut
        # holen (PunchBuddy-Kern über `engine()`); andere Threads warten wie bisher.
        self._lock = threading.RLock()
        self._deadline = threading.local()
        self.before_close: Callable[[], None] | None = None     # Haken vor Schließen/„Speichern unter“
        self._hook_running = threading.local()
        # Offene Pro-Tools-Dialoge (alle Spaces); in Tests mit Attrappen-Engine keine Prüfung
        self.dialogs: Callable[[], list[str]] | None = None if engine_factory else _real_dialogs
        # So lange wird auf das Bestätigen eines Dialogs gewartet (z. B. „Missing Files“, laut Jens ein
        # Fehlalarm zu Clips aus dem AAF); `on_dialog` bekommt den Hinweis für die Anzeige, "" = weiter
        self.dialog_wait_s = 0.0 if engine_factory else 600.0
        self.on_dialog: Callable[[str], None] | None = None
        self._sleep: Callable[[float], None] = time.sleep
        # Beantwortet bekannte Dialoge nach Einstellung („Missing Files“); Rückgabe: Meldung oder None
        self.dialog_handler: Callable[[list[str]], str | None] | None = None
        self.dialog_events: list[str] = []                      # was beantwortet wurde (für Warnungen)
        # Interner Fehler von Pro Tools („Assertion“, live 03.10.2026): danach keine Befehle mehr bis zum Neustart
        self.assert_watch: Any = None if engine_factory else AssertionWatch()
        self.halted = ""
        self._assert_lock = threading.Lock()
        # Titelloses Fenster: Fortschrittsbalken oder Meldung? (live 04.10.2026)
        self.unnamed_kind: Callable[[], str] | None = None if engine_factory else unnamed_kind
        # Verbindungsversuch, der nach dem Zeitlimit noch läuft: der nächste Aufruf wartet auf ihn, statt einen
        # weiteren zu starten – sonst stapelten sich Threads und gRPC-Kanäle (Review B16)
        self._connecting: threading.Thread | None = None
        self._connect_box: dict[str, Any] = {}

    # ── Verbindung ───────────────────────────────────────────────────────────
    def _default_engine(self) -> Any:
        import ptsl
        return ptsl.Engine(company_name=COMPANY_NAME, application_name=APPLICATION_NAME,
                           address=self.address)

    def _connect(self) -> Any:
        if self._engine is not None:
            return self._engine
        import grpc
        try:
            engine = self._create_engine()
        except grpc.RpcError as e:
            raise NotConnectedError(
                "Keine Verbindung zu Pro Tools – läuft Pro Tools und ist PTSL aktiv?") from e
        self._install_deadline(engine)
        self._engine = engine
        if self.min_version is not None:
            version = self._run("GetPTSLVersion", lambda e: e.ptsl_version(), self.timeout_s)
            if version < self.min_version:
                self.reset()
                raise UnsupportedError(
                    f"PTSL-Version {version} ist zu alt, mindestens {self.min_version} nötig")
        log.info("PTSL verbunden (%s)", self.address)
        return engine

    def _create_engine(self) -> Any:
        """`engine_factory` mit Zeitlimit: py-ptsl meldet sich im Konstruktor ohne Deadline an
        (HostReadyCheck, RegisterConnection). Hängt Pro Tools (Dialog, Video Engine), blieb der
        Aufruf sonst ewig stehen (live 02.10.2026)."""
        worker, box = self._connecting, self._connect_box
        if worker is None:
            box = {}

            def create() -> None:
                try:
                    box["engine"] = self._engine_factory()
                except BaseException as e:  # noqa: BLE001 – im aufrufenden Thread weiterreichen
                    box["error"] = e

            worker = threading.Thread(target=create, name="ptsl-connect", daemon=True)
            self._connecting, self._connect_box = worker, box
            worker.start()
        worker.join(self.connect_timeout_s)
        if worker.is_alive():
            raise PtslTimeoutError(
                f"Verbinden: keine Antwort von Pro Tools nach {self.connect_timeout_s:.0f} s – "
                "ist in Pro Tools ein Dialog offen oder hängt Pro Tools?")
        self._connecting = None
        if "error" in box:
            raise box["error"]
        return box["engine"]

    def _install_deadline(self, engine: Any) -> None:
        raw = getattr(getattr(engine, "client", None), "raw_client", None)
        if raw is None or getattr(raw.SendGrpcRequest, "_tm_deadline", False):
            return
        original = raw.SendGrpcRequest

        def with_deadline(request: Any, *args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("timeout", getattr(self._deadline, "value", self.timeout_s))
            return original(request, *args, **kwargs)

        with_deadline._tm_deadline = True  # type: ignore[attr-defined]
        raw.SendGrpcRequest = with_deadline

    def reset(self) -> None:
        """Verbindung verwerfen; der nächste Aufruf verbindet neu."""
        engine, self._engine = self._engine, None
        if engine is not None:
            try:
                engine.close()
            except Exception:  # noqa: BLE001 – beim Verwerfen egal
                pass

    def check_assertion(self, label: str = "") -> None:
        """Hat Pro Tools einen internen Fehler gemeldet, geht kein Befehl mehr hin – bis Pro Tools neu startet."""
        if self.assert_watch is not None:
            with self._assert_lock:
                found, restarted = self.assert_watch.poll()
                if restarted and self.halted:
                    log.info("Pro Tools neu gestartet – Befehle wieder frei")
                    self.halted = ""
                if found and not self.halted:
                    self.halted = found
                    log.error("Pro Tools: %s – keine weiteren Befehle bis zum Neustart", found)
        if self.halted:
            raise ProToolsAssertionError(
                f"{label + ': ' if label else ''}Pro Tools hat einen internen Fehler gemeldet ({self.halted}) – "
                "die App schickt keine Befehle mehr. Pro Tools stürzt nach dem Bestätigen der Meldung "
                "erfahrungsgemäß ab; Pro Tools neu starten, dann erneut versuchen. Seit der Meldung hat die App "
                "nichts mehr gespeichert.")

    def engine(self) -> Any:
        """Verbundene Engine (verbindet bei Bedarf) – für Code, der py-ptsl direkt anspricht
        (PunchBuddy-Kern). Verbinden läuft unter der Sperre und mit Zeitlimit."""
        self.check_assertion("Pro Tools")
        if not self._lock.acquire(timeout=self.lock_wait_s):
            raise PtslTimeoutError("Verbinden: Pro Tools ist noch mit einem anderen Befehl beschäftigt")
        try:
            return self._connect()
        finally:
            self._lock.release()

    @property
    def current_engine(self) -> Any:
        """Die aktuelle Engine oder None – ohne zu verbinden."""
        return self._engine

    # ── Der eine Aufrufweg ───────────────────────────────────────────────────
    def call(self, label: str, fn: Callable[[Any], T], timeout_s: float | None = None) -> T:
        """Führt `fn(engine)` aus: gesperrt, mit Zeitlimit, protokolliert, Fehler übersetzt."""
        self.check_assertion(label)
        if not self._lock.acquire(timeout=self.lock_wait_s):
            raise PtslTimeoutError(f"{label}: Pro Tools ist noch mit einem anderen Befehl beschäftigt")
        try:
            self._connect()
            return self._run(label, fn, timeout_s or self.timeout_s)
        finally:
            self._lock.release()

    def _run(self, label: str, fn: Callable[[Any], T], timeout_s: float) -> T:
        import grpc
        from ptsl.errors import CommandError

        previous = getattr(self._deadline, "value", self.timeout_s)
        self._deadline.value = timeout_s
        t0 = time.monotonic()
        try:
            result = fn(self._engine)
        except grpc.RpcError as e:
            self.reset()
            code = e.code() if hasattr(e, "code") else None
            if code == grpc.StatusCode.DEADLINE_EXCEEDED:
                names = self.dialogs() if self.dialogs is not None else []
                hint = (f"in Pro Tools ist „{'“, „'.join(names)}“ offen – bitte bestätigen" if names
                        else "ist in Pro Tools ein Dialog offen?")
                raise PtslTimeoutError(
                    f"{label}: keine Antwort von Pro Tools nach {timeout_s:.0f} s – {hint}") from e
            if code == grpc.StatusCode.UNAVAILABLE:
                raise NotConnectedError(f"{label}: keine Verbindung zu Pro Tools") from e
            raise PtslError(f"{label}: Übertragungsfehler ({code})") from e
        except CommandError as e:
            raise self._translate(label, e) from e
        finally:
            # verschachtelte Aufrufe (z. B. im PunchBuddy-Kern): das Zeitlimit des äußeren gilt weiter (Review B17)
            self._deadline.value = previous
        log.info("PTSL %s: %.2f s", label, time.monotonic() - t0)
        return result

    @staticmethod
    def _translate(label: str, e: Any) -> PtslError:
        name = getattr(e, "error_name", None)
        message = getattr(e, "message", "") or str(e)
        if name == "PT_NoOpenedSession":
            return NoSessionError(f"{label}: keine Session geöffnet", name)
        if name in _UNSUPPORTED:
            return UnsupportedError(f"{label}: von dieser Pro-Tools-Version nicht unterstützt ({name})")
        if name in _BUSY:
            return PtslTimeoutError(f"{label}: Pro Tools ist beschäftigt ({name}) – offener Dialog?")
        return CommandFailedError(f"{label}: {message or name}", name)

    # ── Befehle ──────────────────────────────────────────────────────────────
    def version(self) -> int:
        return self.call("GetPTSLVersion", lambda e: e.ptsl_version())

    def open_session(self) -> SessionInfo | None:
        """Offene Session oder None."""
        try:
            name = self.call("GetSessionName", lambda e: e.session_name())
            path = self.call("GetSessionPath", lambda e: e.session_path())
        except NoSessionError:
            return None
        return SessionInfo(name=name, path=path)

    def transport_state(self) -> str:
        return self.call("GetTransportState", lambda e: e.transport_state())

    def transport_running(self) -> bool:
        """Alles außer eindeutigem Stillstand gilt als laufend (lieber warten als stören).

        Ohne offene Session antwortet Pro Tools mit PT_NoOpenedSession – dann läuft nichts.
        """
        try:
            return self.transport_state() not in _STOPPED
        except NoSessionError:
            return False

    def save(self) -> None:
        self.call("SaveSession", lambda e: e.save_session(), timeout_s=max(self.timeout_s, SAVE_TIMEOUT_S))

    def _before_close(self) -> None:
        """Haken vor jedem Schließen und „Speichern unter“ (z. B. fremde Dateien übernehmen).
        Ein Fehler darin verhindert das Schließen nie, er wird nur protokolliert."""
        hook = self.before_close
        if hook is None or getattr(self._hook_running, "on", False):
            return
        self._hook_running.on = True
        try:
            hook()
        except Exception as e:  # noqa: BLE001
            log.warning("Vor dem Schließen: %s", e)
        finally:
            self._hook_running.on = False

    def guard_dialogs(self, label: str) -> None:
        """Kein Schließen, Öffnen oder Anlegen, solange Pro Tools einen Dialog zeigt: Pro Tools führt
        PTSL-Befehle auch innerhalb eines Dialogs aus und stürzte so bei CloseSession ab (live 03.10.2026).
        Bekannte Dialoge beantwortet `dialog_handler` („Missing Files“ nach Einstellung, „Missing AAX Plugins“,
        Dashboard); bei allen anderen wird gewartet, bis jemand ihn in Pro Tools bestätigt (höchstens
        `dialog_wait_s`)."""
        self.check_assertion(label)
        names = self.dialogs() if self.dialogs is not None else []
        if not names:
            return
        waited = 0.0
        start = time.monotonic()
        notice = ""
        answered = 0
        kind_at, busy = -1e9, False
        while names:
            self.check_assertion(label)                  # die Meldung ist selbst ein Dialog: nicht darauf warten
            if self.dialog_handler is not None and answered < 3:
                answered += 1
                done = self.dialog_handler(names)
                if done:
                    log.info("%s: %s", label, done)
                    if done != DASHBOARD_CLOSED:
                        self.dialog_events.append(done)
                    self._sleep(1.0)
                    names = self.dialogs() if self.dialogs is not None else []
                    continue
            shown = "“, „".join(names)
            if names != [UNNAMED] or self.unnamed_kind is None:
                busy = False
            elif time.monotonic() - kind_at >= UNNAMED_CHECK_S:   # osascript höchstens alle 5 s (Review B15)
                busy, kind_at = self.unnamed_kind() == PROGRESS, time.monotonic()
            if waited >= self.dialog_wait_s:
                raise DialogOpenError(f"{label}: in Pro Tools ist „{shown}“ offen – erst in Pro Tools "
                                      "bestätigen, dann erneut", names)
            text = (f"Pro Tools arbeitet noch – es geht gleich weiter ({label})" if busy else
                    f"Pro Tools zeigt „{shown}“ – bitte in Pro Tools bestätigen, danach geht es weiter ({label})")
            if text != notice:
                notice = text
                (logging.info if busy else logging.warning)(text)
                if self.on_dialog:
                    self.on_dialog(text)
            self._sleep(1.0)
            # echte Zeit zählt: Fensterliste und osascript brauchen selbst Zeit – vorher wurden aus 10 min bis zu 40
            waited = max(waited + 1.0, time.monotonic() - start)
            names = self.dialogs() if self.dialogs is not None else []
        log.info("%s: Dialog bestätigt, weiter", label)
        if self.on_dialog:
            self.on_dialog("")

    @contextmanager
    def notices(self, show: Callable[[str], None]) -> Iterator[None]:
        """Für die Dauer eines Auftrags: Dialoghinweise („Pro Tools zeigt … – bitte bestätigen“) an `show`.
        Vorher bekam nur „Holen“ sie zu sehen (Review B21); innen gesetzte Hinweise (Holen) haben Vorrang."""
        previous = self.on_dialog
        self.on_dialog = lambda text: show(text) if text else None
        try:
            yield
        finally:
            self.on_dialog = previous

    def close(self) -> None:
        """Schließen, immer mit save_on_close als zusätzlicher Absicherung."""
        self.guard_dialogs("CloseSession")
        self._before_close()
        self.guard_dialogs("CloseSession")
        self.call("CloseSession", lambda e: e.close_session(save_on_close=True),
                  timeout_s=max(self.timeout_s, SAVE_TIMEOUT_S))

    def open(self, path: str) -> None:
        self.guard_dialogs("OpenSession")
        with self._answering(MISSING_PLUGINS, "OpenSession"):
            self.call("OpenSession", lambda e: e.open_session(path))

    @contextmanager
    def _answering(self, dialog: str, label: str, every_s: float = 1.0) -> Iterator[None]:
        """Während eines Befehls `dialog` beantworten, sobald Pro Tools ihn zeigt – er hält den Befehl sonst bis zum
        Zeitlimit an („Missing AAX Plugins“ bei OpenSession, live 13.10.2026: über fünf Minuten)."""
        if self.dialog_handler is None or self.dialogs is None:
            yield
            return
        stop = threading.Event()

        def watch() -> None:
            while not stop.wait(every_s):
                try:
                    if dialog in self.dialogs():
                        done = self.dialog_handler([dialog])
                        if done:
                            log.info("%s: %s", label, done)
                            self.dialog_events.append(done)
                except Exception as e:  # noqa: BLE001 – die Wache darf den Befehl nie stören
                    log.warning("%s: Dialog nicht beantwortet: %s", label, e)
        t = threading.Thread(target=watch, name=f"Dialogwache {label}", daemon=True)
        t.start()
        try:
            yield
        finally:
            stop.set()
            t.join(timeout=every_s + 25.0)          # ein laufendes osascript (Zeitlimit 20 s) zu Ende kommen lassen

    def save_as(self, name: str, location: str) -> None:
        """Offene Session unter neuem Namen in `location` speichern; Pro Tools arbeitet danach in der
        neuen Datei weiter, die alte bleibt unverändert (PTSL SaveSessionAs). Pro Tools legt die Datei
        als `<location>/<name>.ptx` an, ohne eigenen Ordner; Audiodateien bleiben, wo sie sind.
        Der Ordner muss existieren und mit „/“ enden – sonst Assertion „parent is not a dir“ (live 02.10.2026)."""
        folder = location.rstrip("/") + "/"
        self.guard_dialogs("SaveSessionAs")
        self._before_close()
        self.call("SaveSessionAs", lambda e: e.save_session_as(folder, name),
                  timeout_s=max(self.timeout_s, SAVE_TIMEOUT_S))

    def file_locations(self, audio_only: bool = True, page: int = 1000) -> list[tuple[str, bool]]:
        """Dateien der Session (GetFileLocation): [(Pfad, online)], seitenweise.

        py-ptsl fragt nur die erste Seite ab (1000 Einträge) – größere Sessions hätten sonst
        unbemerkt Dateien gefehlt (live 03.10.2026: 1052 Audiodateien).
        """
        import ptsl.ops as ops
        import ptsl.PTSL_pb2 as pt
        filters = [pt.Audio_Files] if audio_only else [pt.All_Files]

        def read(e: Any) -> list[tuple[str, bool]]:
            out: list[tuple[str, bool]] = []
            offset = 0
            while True:
                op = ops.CId_GetFileLocation(pagination_request=pt.PaginationRequest(limit=page, offset=offset),
                                             file_filters=filters)
                e.client.run(op)
                batch = list(op.response.file_locations)
                out += [(f.path, bool(f.info.is_online)) for f in batch]
                if len(batch) < page:
                    return out
                offset += page
        return self.call("GetFileLocation", read, timeout_s=max(self.timeout_s, 60.0))

    def import_audio(self, paths: list[str], *, to_clip_list: bool = False, copy_to: str | None = None,
                     timeout_s: float = 300.0) -> None:
        """Audiodateien importieren (CId_Import, import_type Audio).

        `copy_to`: Ordner, in den Pro Tools die Dateien kopiert (Session-Ordner) – sonst nur verweisen.
        Ziel: neue Spur an der Einfügemarke oder nur die Clip-Liste.
        """
        import ptsl.PTSL_pb2 as pt

        def run(e: Any) -> Any:
            # Lage ist Pflicht (sonst „Import: location_data“, live 03.10.2026): Anfang der Einfügemarke
            at = e.get_timeline_selection()[0].split(".")[0]
            audio = {"file_list": list(paths), "audio_operations": "CopyAudio" if copy_to else "AddAudio",
                     "destination_path": copy_to or "",
                     "destination": "MD_ClipList" if to_clip_list else "MD_NewTrack",
                     "location": "ML_Spot",
                     "location_data": {"location_type": "SLType_Start",
                                       "location": {"location": at, "time_type": "TLType_TimeCode"}}}
            return e.client.run_command(pt.Import, {"import_type": "Audio", "audio_data": audio})
        self.call("Import", run, timeout_s=timeout_s)

    def goto(self, timecode: str) -> None:
        """Einfügemarke (und damit den Wiedergabestart) auf `timecode` setzen – wie PunchBuddys „Zum
        Programmbeginn“: Zeitauswahl auf einen Punkt zusammenziehen."""
        tc = timecode if "." in timecode else f"{timecode}.00"
        self.call("SetTimelineSelection", lambda e: e.set_timeline_selection(in_time=tc, out_time=tc), timeout_s=10.0)

    def session_start(self) -> str:
        """Sessionbeginn (GetSessionStartTime), z. B. „09:58:00:00“."""
        return self.call("GetSessionStartTime", lambda e: e.session_start_time(), timeout_s=10.0)

    def timeline_start(self) -> str:
        """Anfang der Zeitauswahl bzw. Einfügemarke (GetTimelineSelection)."""
        return self.call("GetTimelineSelection", lambda e: e.get_timeline_selection()[0])

    def tracks(self) -> list[TrackInfo]:
        def read(e: Any) -> list[TrackInfo]:
            import ptsl.PTSL_pb2 as pt
            out = []
            for t in e.track_list():
                attrs = getattr(t, "track_attributes", None)
                out.append(TrackInfo(t.name, _named(pt.TrackType, t.type),
                                     bool(getattr(attrs, "contains_clips", False)),
                                     _named(pt.TrackFormat, getattr(t, "format", 0))))
            return out
        return self.call("GetTrackList", read)

    def track_states(self) -> list:
        """Spuren mit Ausblend-, Ordner- und Automationsangaben (für die Ansicht nach dem Import)."""
        from tonmischung.core.track_view import TrackState

        def read(e: Any) -> list:
            import ptsl.PTSL_pb2 as pt
            out = []
            for t in e.track_list():
                kind = _named(pt.TrackType, t.type)
                a = getattr(t, "track_attributes", None)
                h = getattr(a, "is_hidden", False)
                # TrackAttributeState: 1 = None (sichtbar), 2–4 = ausgeblendet (ausdrücklich/indirekt) – live 03.10.2026
                hidden = h if isinstance(h, bool) else int(h or 0) >= 2
                out.append(TrackState(t.name, kind, bool(getattr(a, "contains_clips", False)),
                                      hidden, str(getattr(t, "parent_folder_name", "") or ""),
                                      bool(getattr(a, "is_open", True)),
                                      bool(getattr(a, "contains_automation", False))))
            return out
        return self.call("GetTrackList", read)

    def session_tracks(self) -> list:
        """Alle Spuren der offenen Session für ein Spurprofil: Name, Typ, Format, Ordner (nur lesen)."""
        from tonmischung.core.track_profile import SessionTrack

        def read(e: Any) -> list:
            import ptsl.PTSL_pb2 as pt
            out = []
            for t in e.track_list():
                out.append(SessionTrack(t.name, _named(pt.TrackType, t.type),
                                        _named(pt.TrackFormat, getattr(t, "format", 0)),
                                        str(getattr(t, "parent_folder_name", "") or "")))
            return out
        return self.call("GetTrackList", read)

    def set_hidden(self, names: list[str], hidden: bool) -> None:
        if names:
            self.call("SetTrackHiddenState", lambda e: e.client.run_command(
                __import__("ptsl.PTSL_pb2", fromlist=["x"]).SetTrackHiddenState,
                {"track_names": list(names), "enabled": bool(hidden)}))

    def set_open(self, names: list[str], is_open: bool) -> None:
        if names:
            self.call("SetTrackOpenState", lambda e: e.client.run_command(
                __import__("ptsl.PTSL_pb2", fromlist=["x"]).SetTrackOpenState,
                {"track_names": list(names), "enabled": bool(is_open)}))

    def create_session_from_template(self, template_group: str, template_name: str,
                                     session_name: str, location: str, wait_s: float = 30.0,
                                     sleep: Callable[[float], None] = time.sleep) -> None:
        """Neue Session aus einem Template; Pro Tools öffnet sie danach.

        Haus-Standard 48 kHz / 24 bit / WAVE. Danach wird gewartet, bis die Session wirklich
        offen ist: kurz nach einem Neustart nahm Pro Tools CreateSession an, legte aber nur den
        Ordner an (live 02.10.2026) – dann klare Meldung statt eines späteren Folgefehlers.
        """
        def create(e: Any) -> None:
            b = e.create_session_from_template(template_group, template_name, session_name, location)
            b.sample_rate(48000)
            b.bit_depth(24)
            b.wave_format()
            # ANNAHME: interleaved, damit Konsolidieren eine Stereodatei je Spur ergibt
            b.interleaved(True)
            b.create()
        self.guard_dialogs("CreateSession")
        self.call("CreateSession", create, timeout_s=max(self.timeout_s, 120.0))
        waited = 0.0
        while True:
            self.guard_dialogs(f"CreateSession „{session_name}“")      # z. B. „Missing Files“ aus dem Template
            info = self.open_session()
            if info is not None and info.name == session_name:
                return
            if waited >= wait_s:
                raise CommandFailedError(
                    f"CreateSession: „{session_name}“ ist nach {wait_s:.0f} s nicht offen – in Pro Tools "
                    "nachsehen (Dialog offen? gerade neu gestartet?)")
            sleep(1.0)
            waited += 1.0

    def import_aaf(self, aaf_path: str, *, as_new_tracks: bool = True,
                   map_start_to: str | None = None, handles_ms: int = 0,
                   playlist: str = "ImportOverlayNewOnExistingPlaylists",
                   clip_gain: bool = True, volume_automation: bool = False, track_data_preset: str = "",
                   timeout_s: float = 900.0) -> None:
        """AAF in die offene Session importieren (CId_Import, import_type Session).

        Einstellungen wie im live erprobten PunchBuddy-Arbeitsstand: eingebettete Medien kopieren, Clips, Clip Gain
        und Automation nach `import_options`. `as_new_tracks=False` = Match Tracks auf gleichnamige Spuren (Holen),
        sonst neue Spuren; `playlist` regelt Überlagern bzw. neue Playlist.
        """
        import ptsl.PTSL_pb2 as pt
        session_data = {
            "audio_options": "CopyFromSourceAudio",
            "audio_handle_size": int(handles_ms),
            "video_options": "LinkToSourceVideo",
            "match_options": "MT_ImportAsNewTrack" if as_new_tracks else "MT_MatchTracks",
            "playlist_options": playlist,
            "timecode_mapping_units": "MapStartTimeCodeTo" if map_start_to
            else "MaintainAbsoluteTimeCodeValues",
            # Pflichtfeld, auch beim Beibehalten des Timecodes – leer → PT_InvalidParameter
            # (live 02.10.2026, wie im PunchBuddy-Arbeitsstand)
            "timecode_mapping_start_time": map_start_to or PROGRAM_START,
            "adjust_session_start_time_to_match_source": False,
            # Nur Clips (und Clip Gain) auf die Spuren – die Spuren selbst bleiben, wie das Template sie
            # vorgibt (Jens 03.10.2026: Höhe, Farbe usw. nicht verändern). Mehr Spurdaten steuert nur ein
            # Pro-Tools-Preset „Track Data to Import“ (`track_data_preset`).
            "track_data_to_import": {"clip_gain": bool(clip_gain), "clips_and_media": True,
                                     "volume_automation": bool(volume_automation),
                                     **({"track_data_preset_path": track_data_preset} if track_data_preset else {})},
        }
        req = {"session_path": aaf_path, "import_type": "Session", "session_data": session_data}

        def run(e: Any) -> Any:
            # Eine Zeitauswahl begrenzt den Overlay-Import still auf diesen Bereich – mit der
            # Startband-Auswahl kam kein einziger Clip an (live 02.10.2026). Daher vorher auf
            # einen Punkt zusammenziehen.
            start = e.get_timeline_selection()[0]
            e.set_timeline_selection(in_time=start, out_time=start)
            return e.client.run_command(pt.Import, req)
        try:
            self.call("Import", run, timeout_s=timeout_s)
        finally:
            # z. B. „Missing Files“ (der AAF fehlt die Video-Verknüpfung): nach Einstellung beantworten oder warten –
            # sonst gingen Clips verteilen, Spuren zurückbenennen usw. in den offenen Dialog (Review A12)
            self.guard_dialogs("Nach dem AAF-Import")

    def rename_track(self, current: str, new: str) -> None:
        self.run("RenameTargetTrack", "RenameTargetTrack", {"current_name": current, "new_name": new})

    def session_text(self, timeout_s: float = 60.0) -> str:
        """„Session Info as Text“ mit Spur-EDLs in Timecode – als Text, ohne Dateidialog.

        Wie ptsl-mcp-server; Clip-Grenzen ohne Fade-Bereich (DontShowCrossfades).
        """
        def export(e: Any) -> str:
            import ptsl.ops as ops
            import ptsl.PTSL_pb2 as pt
            op = ops.CId_ExportSessionInfoAsText(
                include_clip_list=False, include_file_list=False, include_markers=False,
                include_plugin_list=False, include_track_edls=True, show_sub_frames=False,
                track_list_type=pt.AllTracks, include_user_timestamps=False,
                fade_handling_type=pt.DontShowCrossfades, track_offset_options=pt.TimeCode,
                text_as_file_format=pt.UTF8, output_type=pt.ESI_String, output_path="")
            e.client.run(op)
            return op.response.session_info
        return self.call("ExportSessionInfoAsText", export, timeout_s=timeout_s)

    def import_video(self, video_path: str, at: str = PROGRAM_START,
                     timeout_s: float = 300.0) -> None:
        """Referenzvideo auf die Hauptvideospur, Anfang am Programmbeginn (verknüpft, nicht kopiert).

        Nicht „Session-Anfang“: der liegt im Template bei 00:00:00:00 (live 02.10.2026).
        py-ptsl 601 hat dafür keine Methode; CId_ImportVideo wird direkt aufgerufen.
        """
        import ptsl.PTSL_pb2 as pt
        req = {"video_file_list": [video_path], "destination": "MD_MainVideoTrack",
               "location": "ML_Spot",
               "spot_location_data": {"location_type": "SLType_Start",
                                      "location": {"location": at, "time_type": "TLType_TimeCode"}},
               "import_audio_from_file": False,
               "remove_existing_video_tracks": False, "remove_existing_video_clips": False,
               "clear_destination_video_track_playlist": False}
        resp = self.call("ImportVideo", lambda e: e.client.run_command(pt.ImportVideo, req),
                         timeout_s=timeout_s)
        failures = (resp or {}).get("failure_list") if isinstance(resp, dict) else None
        if failures:
            raise CommandFailedError(f"ImportVideo: {failures}")

    # ── Auswahl und Bereiche (für Konsolidieren und Kürzen) ──────────────────
    def run(self, label: str, command: str, request: dict | None = None,
            timeout_s: float | None = None) -> Any:
        """Beliebigen PTSL-Befehl mit JSON-Anfrage ausführen (auch ohne py-ptsl-Methode)."""
        import ptsl.PTSL_pb2 as pt
        cid = pt.CommandId.Value(command)
        return self.call(label, lambda e: e.client.run_command(cid, request or {}), timeout_s)

    @contextmanager
    def edit_selection(self) -> Iterator[None]:
        """Selector-Werkzeug und „Link Track/Timeline and Edit Selection“ für die Dauer setzen.

        Ohne die Verknüpfung landen Auswahl, Clear und Paste nicht auf den gewählten
        Spuren (live 02.10.2026). Danach wird der vorige Zustand wiederhergestellt.
        """
        opts = (self.run("GetEditModeOptions", "GetEditModeOptions") or {}).get("edit_mode_options", {})
        tool = (self.run("GetEditTool", "GetEditTool") or {}).get("current_setting")
        mode = (self.run("GetEditMode", "GetEditMode") or {}).get("current_setting")
        self.run("SetEditTool", "SetEditTool", {"edit_tool": "ET_Selector"})
        # Slip: in Shuffle würden Ausschneiden/Einfügen alles Folgende verschieben
        self.run("SetEditMode", "SetEditMode", {"edit_mode": "EMO_Slip"})
        self.run("SetEditModeOptions", "SetEditModeOptions", {"edit_mode_options": {
            **opts, "link_timeline_and_edit_selection": True, "link_track_and_edit_selection": True}})
        try:
            yield
        finally:
            try:
                self.run("SetEditModeOptions", "SetEditModeOptions", {"edit_mode_options": opts})
                if mode:
                    self.run("SetEditMode", "SetEditMode", {"edit_mode": mode})
                if tool:
                    self.run("SetEditTool", "SetEditTool", {"edit_tool": tool})
            except PtslError as e:
                log.warning("Edit-Optionen nicht wiederhergestellt: %s", e)

    def select_range(self, tracks: list[str], in_time: str, out_time: str) -> None:
        """Spuren und Zeitbereich wählen (innerhalb von `edit_selection`)."""
        def run(e: Any) -> None:
            e.select_tracks_by_name(tracks)
            e.set_timeline_selection(in_time=in_time, out_time=out_time)
        self.call("Auswahl", run)

    def show_tracks(self, tracks: list[str]) -> None:
        """Spuren einblenden – auf ausgeblendeten Spuren entsteht keine Edit-Auswahl (live 02.10.2026)."""
        self.call("Spuren einblenden", lambda e: e.set_track_hidden_state(tracks, False))

    def edit(self, command: str) -> None:
        """„Cut“, „Copy“ oder „Paste“ auf die aktuelle Auswahl."""
        self.run(command, command, {})

    def track_selection(self, track: str) -> tuple[str, str]:
        """Alle Clips einer Spur wählen und den Bereich liefern (Auswahl bleibt stehen)."""
        def run(e: Any) -> tuple[str, str]:
            e.select_all_clips_on_track(track)
            sel = e.get_timeline_selection()
            return sel[0], sel[1]
        return self.call(f"Clips {track}", run)

    def clips_extent(self, track_name: str, settle_s: float = 0.0) -> tuple[str, str]:
        """(Anfang, Ende) aller Clips einer Spur als Timecode mit Subframes.

        Die vorige Zeitauswahl wird wiederhergestellt – eine liegen gebliebene Auswahl
        begrenzt z. B. den folgenden AAF-Import (live 02.10.2026). `settle_s`: Pause
        zwischen Auswählen und Lesen; unter Last setzt Pro Tools die Auswahl verzögert
        (PunchBuddy 2.0.3).
        """
        def extent(e: Any) -> tuple[str, str]:
            before = e.get_timeline_selection()
            e.select_all_clips_on_track(track_name)
            if settle_s:
                time.sleep(settle_s)
            sel = e.get_timeline_selection()
            e.set_timeline_selection(in_time=before[0], out_time=before[1])
            return sel[0], sel[1]
        return self.call(f"Clips {track_name}", extent)

    def consolidate(self, tracks: list[str], in_time: str, out_time: str, settle_s: float = 0.0) -> None:
        """Spuren über den Bereich auswählen und konsolidieren. `settle_s`: Pausen zwischen den
        Auswahlschritten (für den zweiten Versuch, wie PunchBuddy 2.0.3)."""
        def run(e: Any) -> None:
            e.select_tracks_by_name(tracks)
            if settle_s:
                time.sleep(settle_s)
            e.set_timeline_selection(in_time=in_time, out_time=out_time)
            if settle_s:
                time.sleep(settle_s)
            e.extend_selection_to_target_tracks(tracks)
            if settle_s:
                time.sleep(settle_s * 1.5)
            e.consolidate_clip()
        self.call("ConsolidateClip", run, timeout_s=max(self.timeout_s, 120.0))


@dataclass(frozen=True)
class TrackInfo:
    name: str
    type: str          # PTSL-Name, z. B. AudioTrack, VideoTrack
    has_clips: bool
    format: str = ""   # PTSL-Name, z. B. TF_Stereo, TF_Mono

    @property
    def channels(self) -> int:
        return {"TF_Mono": 1, "TF_Stereo": 2}.get(self.format, 2)

    @property
    def is_video(self) -> bool:
        return "Video" in self.type
