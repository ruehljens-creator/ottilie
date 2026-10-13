"""Zugriff auf PunchBuddy (punchbuddy-protools) als Submodul unter vendor/punchbuddy.

PunchBuddy wird importiert, nicht kopiert. Vor dem ersten `import punchbuddy...`
`use_punchbuddy()` aufrufen; für Module mit Nebenwirkungen beim Import
`import_isolated()` verwenden.
"""
from __future__ import annotations

import faulthandler
import importlib
import logging
import sys
import threading
from pathlib import Path
from types import ModuleType

PUNCHBUDDY_DIR = Path(__file__).resolve().parent.parent / "vendor" / "punchbuddy"
HEDWIG_DIR = Path(__file__).resolve().parent.parent / "vendor" / "hedwig"


def use_punchbuddy() -> Path:
    if not (PUNCHBUDDY_DIR / "punchbuddy" / "__init__.py").exists():
        raise RuntimeError(
            f"PunchBuddy fehlt unter {PUNCHBUDDY_DIR} – `git submodule update --init` ausführen")
    path = str(PUNCHBUDDY_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    return PUNCHBUDDY_DIR


def import_isolated(name: str) -> ModuleType:
    """PunchBuddy-Modul importieren und dessen Eingriffe in den Prozess zurücknehmen.

    `punchbuddy.export` zieht `punchbuddy.log` nach: Datei-Handler auf
    ~/.punchbuddy/PunchBuddy.log am Root-Logger, faulthandler ins PunchBuddy-Crash-Log,
    eigene excepthooks. Ottilie soll nicht in PunchBuddys Log schreiben (auf den
    Macs läuft PunchBuddy daneben). ANNAHME: Übergang bis zur Zusammenführung (MZ).
    """
    use_punchbuddy()
    if name in sys.modules:
        return sys.modules[name]
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    hooks = (sys.excepthook, threading.excepthook)
    fault_on = faulthandler.is_enabled()
    try:
        return importlib.import_module(name)
    finally:
        # PunchBuddys log.py leert die Handler-Liste (`handlers.clear()`) – genau den alten Stand
        # wiederherstellen, sonst ginge Ottilies Protokolldatei verloren (live 02.10.2026)
        for h in list(root.handlers):
            if h not in handlers:
                root.removeHandler(h)
        root.handlers[:] = handlers
        root.setLevel(level)
        sys.excepthook, threading.excepthook = hooks
        faulthandler.disable()
        if fault_on:
            faulthandler.enable()


def use_hedwig() -> Path:
    """Hedwigs übernommene Module (vendor/hedwig, flache Modulnamen) importierbar machen."""
    path = str(HEDWIG_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    return HEDWIG_DIR
