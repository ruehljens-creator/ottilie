"""Fremde Audiodateien in der App: regelmäßig prüfen, nach Einstellung übernehmen (Plan M7 fremde Dateien).

Einstellung `import.session_ablage.fremde_dateien.uebernehmen`:
- `sofort`: bei jeder Abfrage (alle `abfrage_intervall_s`, Vorgabe 30 s) übernehmen,
- `beim_speichern`: wenn sich die Session-Datei geändert hat (Pro Tools hat gespeichert),
- `beim_schliessen_und_export`: nur vor dem Schließen.
Vor jedem Schließen und „Speichern unter“ durch die App und vor dem Freigeben wird immer übernommen
(Haken am PtslClient), mit Warnung, wenn etwas nicht übernommen werden konnte. Übernommen wird nur bei
Sessions, die zu einem Beitrag dieses Mischers gehören (ANNAHME F3); sonst wird nur gezählt.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from tonmischung.core.foreign import NEW, plan, summary
from tonmischung.core.session_rules import same_session
from tonmischung.helmut.client import HelmutError
from tonmischung.ptsl.errors import PtslError
from tonmischung.workflow.foreign import adopt

log = logging.getLogger("tonmischung.fremd")
P = "import.session_ablage.fremde_dateien."
OWNER_CACHE_S = 60.0


class ForeignWatcher:
    def __init__(self, ctx: Any, busy: Callable[[], bool] = lambda: False,
                 notify: Callable[[str, str], None] = lambda title, text: None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ctx, self.busy, self.notify, self.clock = ctx, busy, notify, clock
        self.lock = threading.Lock()
        self.state: dict = {"session": "", "neu": 0, "kopiert": 0, "fehlt": 0, "eigen": False, "text": ""}
        self._owner: tuple[str, Any, float] | None = None
        self._mtime: float | None = None

    def _setting(self, key: str) -> Any:
        return self.ctx.settings.get(P + key)

    def owner(self, session_path: str) -> Any:
        """Beitrag dieses Mischers, zu dem die Session gehört (kurz zwischengespeichert)."""
        from tonmischung.app.viewmodel import is_mine
        now = self.clock()
        if self._owner and self._owner[0] == session_path and now - self._owner[2] < OWNER_CACHE_S:
            return self._owner[1]
        try:
            found = next((c for c in self.ctx.helmut.list_for_mixing()
                          if c.mix.session_path
                          and same_session(c.mix.session_path, session_path)
                          and is_mine(c, self.ctx.mixer, self.ctx.room)), None)
        except HelmutError:
            found = self._owner[1] if self._owner and self._owner[0] == session_path else None
        self._owner = (session_path, found, now)
        return found

    def check(self, reason: str = "abfrage") -> dict:
        """reason: „abfrage“ (Takt), „schliessen“ (vor Schließen/Speichern unter/Freigeben)."""
        if reason == "abfrage" and self.busy():
            return self.state                              # Pro Tools gehört gerade einem Auftrag
        if not self.lock.acquire(blocking=reason != "abfrage"):
            return self.state
        try:
            return self._check(reason)
        finally:
            self.lock.release()

    def _check(self, reason: str) -> dict:
        ptsl = self.ctx.ptsl
        try:
            opened = ptsl.open_session()
            if opened is None:
                self.state = {"session": "", "neu": 0, "kopiert": 0, "fehlt": 0, "eigen": False, "text": ""}
                return self.state
            subfolder = self._setting("unterordner") or "Audio Files/Extern/"
            files = plan(ptsl.file_locations(), opened.path, subfolder)
        except PtslError as e:
            log.debug("Fremde Dateien nicht geprüft: %s", e)
            return self.state
        mine = self.owner(opened.path)
        mode = self._setting("uebernehmen") or "sofort"
        try:
            mtime = os.path.getmtime(opened.path)
        except OSError:
            mtime = None
        saved = self._mtime is not None and mtime is not None and mtime != self._mtime
        self._mtime = mtime
        now = reason != "abfrage" or mode == "sofort" or (mode == "beim_speichern" and saved)
        text = ""
        if any(f.status == NEW for f in files) and now and mine is not None:
            upload = None
            if self._setting("hochladen") and self.ctx.handover_root:
                upload = os.path.join(self.ctx.handover_root.replace("{projektId}", mine.id), "Extern")
            service = self.ctx.service()
            result = adopt(ptsl, opened.path, subfolder=subfolder, files=files, upload_dir=upload,
                           register=lambda p: service.add_asset_or_queue(mine, p, "extern"), log=log.info)
            text = result.text
            if result.failed and self._setting("warnen"):
                self.notify("Fremde Dateien nicht übernommen", "; ".join(result.failed))
            files = plan(ptsl.file_locations(), opened.path, subfolder)
        elif reason != "abfrage" and any(f.status == NEW for f in files) and self._setting("warnen"):
            n = sum(f.status == NEW for f in files)
            self.notify("Dateien nur lokal", f"{n} Audiodateien der Session „{opened.name}“ liegen außerhalb des "
                                             "Projekts und wurden nicht übernommen (Session gehört keinem Beitrag "
                                             "dieses Mischers).")
        self.state = {"session": opened.name, **summary(files), "eigen": mine is not None, "text": text}
        return self.state

    def before_close(self) -> None:
        self.check("schliessen")
