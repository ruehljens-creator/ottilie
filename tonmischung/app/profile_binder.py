"""Welches Spurprofil gilt für die offene Session? (Plan Spurprofile, Schritt 4)

Die App merkt sich beim Holen, aus welchem Template eine Session angelegt wurde. Für andere Sessions (von Hand
geöffnet, wieder geöffnet, aus einem anderen Raum) schlägt sie das Profil mit den meisten gleichnamigen Spuren vor.
Das gefundene Profil legt die Einstellungsebene „Spurprofil“ (Spurtabelle, Aufnahmespuren) über die allgemeinen
Einstellungen – Export, Aufnahme und Stream Deck lesen sie von dort. Ohne passendes Profil gilt alles wie bisher.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from tonmischung.config.settings import default_local_path
from tonmischung.core import track_profile as TP
from tonmischung.core.fileio import CorruptFileError, read_json, set_aside, write_json
from tonmischung.ptsl.errors import PtslError

log = logging.getLogger("tonmischung.spurprofil")


class ProfileBinder:
    def __init__(self, ctx: Any, store: Path | None = None, busy: Any = lambda: False) -> None:
        self.ctx = ctx
        self.busy = busy                      # läuft ein Auftrag, prüft der Zeitgeber nicht (Pro Tools hat Vorrang)
        # neben der lokalen Einstellungsdatei (in Tests im Testordner)
        self.store = (Path(store) if store
                      else Path(ctx.settings.local_path or default_local_path()).with_name("session_templates.json"))
        self._lock = threading.Lock()
        self._session = None                 # Pfad der zuletzt geprüften Session
        self._profiles_hash = ""
        self.active: TP.TrackProfile | None = None
        self.how = ""                         # „Template“ | „Spurnamen“

    # ── Gedächtnis Session → Template ───────────────────────────────────────
    def _read(self) -> dict[str, str]:
        try:
            return read_json(self.store, expect=dict) or {}
        except CorruptFileError:
            set_aside(self.store)                # Merkzettel – beiseitelegen statt überschreiben
            return {}
        except OSError:
            return {}

    def bind(self, session_path: str, group: str, name: str) -> None:
        """Nach dem Holen: diese Session stammt aus Template group/name."""
        data = self._read()
        data[session_path] = f"{group}/{name}"
        try:
            write_json(self.store, data)         # eigene Datei der App
        except OSError as e:
            log.warning("Session-Template nicht gemerkt: %s", e)
        self._session = None                     # beim nächsten Prüfen neu zuordnen
        self.tick(force=True)

    # ── Prüfen (Zeitgeber, nach dem Holen, nach Änderungen am Profil) ─────────
    def refresh(self) -> None:
        self._profiles_hash = ""
        self.tick(force=True)

    def tick(self, force: bool = False) -> None:
        if not force and self.busy():
            return
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._tick()
        finally:
            self._lock.release()

    def _tick(self) -> None:
        s = self.ctx.settings
        raw = s.get("import.spurprofile") or []
        h = hashlib.sha1(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        try:
            info = self.ctx.ptsl.open_session()
        except PtslError:
            return                               # Pro Tools gerade nicht erreichbar oder beschäftigt: später
        path = info.path if info else ""
        if path == self._session and h == self._profiles_hash:
            return
        self._session, self._profiles_hash = path, h
        profiles = TP.profiles_from_settings(raw)
        profile, how = None, ""
        if path and profiles:
            group, _, name = self._read().get(path, "").partition("/")
            profile = TP.find(profiles, group, name) if name else None
            how = "Template" if profile else ""
            if profile is None:
                try:
                    names = [t.name for t in self.ctx.ptsl.session_tracks()]
                except PtslError:
                    self._session = None         # nächstes Mal erneut versuchen
                    return
                profile = TP.best_match(profiles, names)
                how = "Spurnamen" if profile else ""
        self._apply(profile, how)

    def _apply(self, profile: TP.TrackProfile | None, how: str) -> None:
        s = self.ctx.settings
        before = s.profile_label
        if profile is None:
            s.clear_profile_layer()
        else:
            s.set_profile_layer(TP.layer_values(profile), f"{profile.template_group}/{profile.template_name}")
        self.active, self.how = (profile, how) if profile is not None else (None, "")
        if s.profile_label != before:
            log.info("Spurprofil der offenen Session: %s",
                     f"{s.profile_label} ({how})" if s.profile_label else "keines")

    def status(self) -> dict[str, Any]:
        p = self.active
        return {"profil": f"{p.template_group}/{p.template_name}" if p else "", "zuordnung": self.how}
