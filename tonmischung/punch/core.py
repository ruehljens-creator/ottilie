"""PunchBuddy-Kern in Ottilie: dieselbe PTSL-Verbindung, dieselbe Sperre, unsere Einstellungen.

PunchBuddys Module (`transport`, `export`, `loudness`) holen ihre Verbindung über
`_get_engine()` / `_ptsl_call()` aus `punchbuddy.engine` und ihre Einstellungen über
`load_settings()` bzw. `state.app_ref.settings` aus `~/.punchbuddy/settings.json`. Beides wird
hier beim Einbinden umgelenkt – ohne PunchBuddy zu ändern (Submodul, nur lesen):

- Verbindung: der `PtslClient` von Ottilie (eine Verbindung, eine Sperre, Zeitlimits).
  Sonst könnten Aufnahme und Holen gleichzeitig auf Pro Tools zugreifen.
- Einstellungen: aus unseren Einstellungen gebaut (`settings_bridge.punch_settings`), bei jedem
  Lesen neu – Änderungen im Einstellungsfenster wirken sofort.
- Meldungen (`_show_error`) und die Belegt-Anzeige (`state.app_ref.title`) gehen an Rückrufe
  der App statt an PunchBuddys AppleScript-Dialog bzw. Menüleisten-Titel.

PunchBuddy ruft Pro Tools an manchen Stellen auch direkt über die Engine auf (ohne
`_ptsl_call`) – wie in der produktiven App. Diese Aufrufe laufen ohne die Sperre; dass sie
nicht mit einem Auftrag von Ottilie kollidieren, regelt die Belegung „ein
Pro-Tools-Vorgang zur Zeit“ der Befehlsschicht (Schritt 2).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from types import ModuleType
from typing import Any

from tonmischung.config.settings import Settings
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import PtslError, PtslTimeoutError
from tonmischung.punch.settings_bridge import punch_settings
from tonmischung.vendor import import_isolated

log = logging.getLogger("tonmischung.punch")

MODULES = ("punchbuddy.engine", "punchbuddy.transport", "punchbuddy.loudness", "punchbuddy.export")
_ENGINE_NAMES = ("_get_engine", "_ptsl_call", "_reset_engine", "_close_engine", "_reset_if_rpc_error",
                 "_show_error", "load_settings")


class _AppRef:
    """Steht für `state.app_ref`: PunchBuddy liest `.settings` und setzt `.title` (Belegt-Symbol)."""

    def __init__(self, core: "PunchCore") -> None:
        self._core = core
        self._title = ""

    @property
    def settings(self) -> dict:
        return self._core.settings_dict()

    @property
    def title(self) -> str:
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        self._title = value
        self._core.on_busy(value == self._core.busy_icon)


class PunchCore:
    """Bindet PunchBuddys Kern an Ottilie. `install()` gibt eine Rückbau-Funktion zurück."""

    def __init__(self, ptsl: PtslClient, settings: Settings, schema: Mapping, *,
                 token: Callable[[], str] = lambda: "",
                 on_error: Callable[[str, str], None] | None = None,
                 on_busy: Callable[[bool], None] | None = None) -> None:
        self.ptsl = ptsl
        self.settings = settings
        self.schema = schema
        self._token = token
        self.on_error = on_error or (lambda title, msg: log.warning("%s: %s", title, msg))
        self.on_busy = on_busy or (lambda busy: None)
        self.busy_icon = ""
        self.modules: dict[str, ModuleType] = {}

    # ── Einstellungen ───────────────────────────────────────────────────────
    def settings_dict(self) -> dict:
        defaults = self.modules["punchbuddy.config"].DEFAULT_SETTINGS
        return punch_settings(self.settings, self.schema, defaults, token=self._token())

    # ── Verbindung im PunchBuddy-Format ─────────────────────────────────────
    def _get_engine(self) -> Any:
        try:
            return self.ptsl.engine()
        except PtslError as e:
            log.error("PTSL-Verbindung für den Kern: %s", e)
            return None

    def _ptsl_call(self, fn: Callable, *args: Any, label: str = "", timeout: float = 15.0) -> tuple[bool, Any]:
        """Wie `punchbuddy.engine._ptsl_call`: (ok, Ergebnis); bei Zeitüberschreitung (False, None),
        sonst (False, Fehler). Läuft über die Sperre und das Zeitlimit des PtslClient."""
        try:
            return True, self.ptsl.call(label or "Aufnahme", lambda _engine: fn(*args), timeout_s=timeout)
        except PtslTimeoutError as e:
            log.warning("[%s] %s", label, e)
            return False, None
        except PtslError as e:
            log.error("[%s] %s", label, e)
            return False, e.__cause__ or e
        except Exception as e:  # noqa: BLE001 – wie PunchBuddy: Fehler als Ergebnis zurückgeben
            log.error("[%s] Fehler: %s", label, e)
            return False, e

    def _reset_engine(self, stale: Any = None) -> None:
        if stale is None or stale is self.ptsl.current_engine:
            self.ptsl.reset()

    def _close_engine(self) -> None:
        self.ptsl.reset()

    def _reset_if_rpc_error(self, e: BaseException) -> bool:
        import grpc
        if isinstance(e, grpc.RpcError):
            self.ptsl.reset()
            return True
        return False

    def _show_error(self, title: str, msg: str) -> None:
        self.on_error(title, msg)

    # ── Einbinden ───────────────────────────────────────────────────────────
    def install(self) -> Callable[[], None]:
        """Module laden und umlenken. Rückgabe: Funktion, die alles zurückbaut (für Tests)."""
        saved: list[tuple[ModuleType, str, Any]] = []

        def patch(mod: ModuleType, name: str, value: Any) -> None:
            saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)

        self.modules["punchbuddy.config"] = import_isolated("punchbuddy.config")
        state = self.modules["punchbuddy.state"] = import_isolated("punchbuddy.state")
        i18n = import_isolated("punchbuddy.i18n")
        # Erst alle laden, dann umlenken: `from punchbuddy.engine import _get_engine` bindet beim
        # ersten Import den Namen – ein schon umgelenkter Wert würde sonst als „Original“ gesichert.
        for name in MODULES:
            self.modules[name] = import_isolated(name)
        for name in MODULES:
            mod = self.modules[name]
            for attr in _ENGINE_NAMES:
                if hasattr(mod, attr):
                    patch(mod, attr, self.settings_dict if attr == "load_settings" else getattr(self, attr))
        patch(state, "app_ref", _AppRef(self))
        self.busy_icon = state.ICON_BUSY
        old_lang = i18n.get_language()
        i18n.set_language("de")                       # Ottilie: nur Deutsch (Jens, 02.10.2026)
        # Fenstertitel der Fortschrittsanzeigen ohne den alten Namen
        de = i18n.TRANSLATIONS["de"]
        old_texts = {k: v for k, v in de.items() if k.startswith("prog_") and "PunchBuddy" in str(v)}
        for k, v in old_texts.items():
            de[k] = v.replace("PunchBuddy", "Ottilie")

        def restore() -> None:
            for mod, name, value in reversed(saved):
                setattr(mod, name, value)
            i18n.set_language(old_lang)
            de.update(old_texts)
        log.info("PunchBuddy-Kern eingebunden (Verbindung und Einstellungen von Ottilie)")
        return restore

    def module(self, name: str) -> ModuleType:
        return self.modules[f"punchbuddy.{name}"]
