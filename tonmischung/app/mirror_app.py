"""Spiegelung in der App (Plan M7-Ablage): wann gespiegelt wird und was die Titelleiste zeigt.

Einstellung `import.session_ablage.spiegeln`:
- `nach_jedem_speichern`: die Session-Datei hat sich geändert und bei zwei Takten (je 5 s) gleich ausgesehen,
- `beim_export_und_schliessen`: vor jedem Schließen/„Speichern unter“ durch die App und nach dem Zurückliefern,
- `alle_n_minuten`: alle `spiegel_intervall_min` Minuten, wenn sich seit dem letzten Spiegeln etwas geändert hat.
Gespiegelt wird nur die offene Session eines eigenen Beitrags mit Ablage „lokal mit Spiegelung“.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from tonmischung.core.storage import MIRROR
from tonmischung.ptsl.errors import PtslError
from tonmischung.workflow.mirror import mirror

log = logging.getLogger("tonmischung.spiegel")
P = "import.session_ablage."
SESSION_CHECK_S = 30.0


class MirrorWatcher:
    def __init__(self, ctx: Any, owner: Callable[[str], Any], notify: Callable[[str, str], None] = lambda t, x: None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ctx, self.owner, self.notify, self.clock = ctx, owner, notify, clock
        self.lock = threading.Lock()
        self.state: dict = {"zeit": "", "fehler": "", "ziel": "", "text": ""}
        self._session: tuple[str, float] | None = None          # (Pfad, wann geprüft)
        self._seen_mtime: float | None = None
        self._mirrored_mtime: float | None = None
        self._last_run: float | None = None
        self._assets: set[str] = set()

    def _s(self, key: str) -> Any:
        return self.ctx.settings.get(P + key)

    def _open_path(self) -> str | None:
        now = self.clock()
        if self._session is None or now - self._session[1] > SESSION_CHECK_S:
            try:
                s = self.ctx.ptsl.open_session()
            except PtslError:
                return self._session[0] if self._session else None
            self._session = (s.path if s else "", now)
        return self._session[0] or None

    def target(self, path: str) -> tuple[Any, str] | None:
        """(Beitrag, Spiegelordner) – nur für eigene Beiträge mit „lokal mit Spiegelung“."""
        c = self.owner(path)
        if c is None or c.mix.session_storage != MIRROR or not c.mix.session_mirror:
            return None
        return c, c.mix.session_mirror

    def tick(self) -> dict:
        """Takt (alle paar Sekunden): je nach Einstellung spiegeln."""
        path = self._open_path()
        if not path:
            return self.state
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return self.state
        mode = self._s("spiegeln") or "nach_jedem_speichern"
        changed = self._mirrored_mtime is None or mtime != self._mirrored_mtime
        if mode == "nach_jedem_speichern":
            quiet = self._seen_mtime == mtime and changed     # zweimal gleich gesehen → Pro Tools ist fertig
            self._seen_mtime = mtime
            if quiet:
                self.run(path, mtime, blocking=False)
        elif mode == "alle_n_minuten":
            every = max(float(self._s("spiegel_intervall_min") or 10), 1.0) * 60
            if changed and (self._last_run is None or self.clock() - self._last_run >= every):
                self.run(path, mtime, blocking=False)
        return self.state

    def run(self, path: str | None = None, mtime: float | None = None, reason: str = "",
            blocking: bool = True) -> dict:
        """Jetzt spiegeln (Takt, vor dem Schließen, nach dem Zurückliefern). Läuft schon eine Spiegelung,
        wartet der Takt nicht (`blocking=False`), das Schließen schon."""
        path = path or self._open_path()
        if not path:
            return self.state
        t = self.target(path)
        if t is None:
            return self.state
        c, dst = t
        if not self.lock.acquire(blocking=blocking):
            return self.state
        try:
            try:
                mtime = mtime if mtime is not None else os.path.getmtime(path)
                r = mirror(os.path.dirname(path), dst, by_content=self._s("audiodateien_spiegeln") == "alle",
                           with_backups=bool(self._s("sicherungen_mitfuehren")))
            except OSError as e:
                self.state = {**self.state, "fehler": str(e), "text": f"Spiegeln nicht möglich: {e}"}
                self.notify("Spiegeln nicht möglich", str(e))
                return self.state
            self._last_run = self.clock()
            self._mirrored_mtime = mtime
            self.state = {"zeit": r.at, "fehler": "; ".join(r.failed), "ziel": dst, "text": r.text}
            log.info("Spiegel %s: %s%s", dst, r.text, f" ({reason})" if reason else "")
            if r.failed:
                self.notify("Spiegeln unvollständig", "; ".join(r.failed[:3]))
            mirrored = os.path.join(dst, os.path.basename(path))
            if self._s("session_als_asset") and mirrored not in self._assets and os.path.exists(mirrored):
                self._register(c, mirrored)
        finally:
            self.lock.release()
        return self.state

    def _register(self, c: Any, path: str) -> None:
        """Gespiegelte Session einmal als Asset melden (nicht doppelt, bei Ausfall nachgetragen)."""
        from tonmischung.helmut.client import HelmutError, HelmutUnavailableError
        try:
            if not any((a.get("filepath") or a.get("path")) == path for a in self.ctx.helmut.assets(c.id)):
                self.ctx.service().add_asset_or_queue(c, path, "session")
            self._assets.add(path)
        except HelmutUnavailableError:
            pass                                               # beim nächsten Spiegeln erneut
        except HelmutError as e:
            log.warning("Session nicht als Asset gemeldet: %s", e)

    def before_close(self) -> None:
        """Vor jedem Schließen den letzten Stand spiegeln (in jeder Einstellung – danach ist er sicher)."""
        self._session = None                                   # frisch fragen: welche Session schließt gerade?
        self.run(reason="vor dem Schließen")
