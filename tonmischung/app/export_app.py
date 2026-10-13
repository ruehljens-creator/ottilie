"""Datei-Exporte aus PunchBuddy in der App (Plan PunchBuddy-Übernahme, Schritt 5).

WAV-Export, AAF eingebettet und AAF-Referenz laufen über PunchBuddys Kern (`export.py`, v2.1.1 mit
Video-Ende-Wiederholung, Konsolidieren mit Nachweis und strenger Transport-Sperre). Sie laufen als
Auftrag – nie gleichzeitig mit Holen oder Zurückliefern („ein Pro-Tools-Vorgang zur Zeit“).
Die AAF-Exporte bedienen Pro-Tools-Dialoge (AppleScript); bei der Abnahme auf Pro Tools Ultimate
2026.04 neu abstimmen (Jens, 02.10.2026). Exportspuren und Lautheit kommen aus der Spurtabelle,
Ordner und AAF-Vorgaben aus „Datei-Exporte (WAV, AAF)“.
"""
from __future__ import annotations

import logging
from typing import Any

from tonmischung.app.bridge import CommandError
from tonmischung.punch.commands import PunchCommands
from tonmischung.punch.core import PunchCore
from tonmischung.punch.settings_bridge import export_tracks

log = logging.getLogger("tonmischung.export")
DEBOUNCE_EXPORT_S = 1.0
EXPORTS = {"export_wav": ("WAV-Export", "run_wav_export_standalone"),
           "export_aaf": ("AAF-Export (eingebettet)", "run_aaf_export_standalone"),
           "export_aaf_reference": ("AAF-Export (Referenz)", "run_aaf_reference_export_standalone")}


def add_export_commands(commands: PunchCommands, core: PunchCore, runner: Any) -> None:
    def handler(name: str) -> Any:
        title, function = EXPORTS[name]

        def run(args: list[str]) -> tuple[bool, str]:
            if not commands.debounce.ok(name, DEBOUNCE_EXPORT_S):
                return True, f"{name} debounced"
            tracks, _ = export_tracks(core.settings)
            if not tracks:
                return False, f"{name}: keine Exportspuren (Export → Spuren & Lautheit)"

            def work(_jc: Any) -> dict:
                getattr(core.module("export"), function)(tracks, core.settings_dict())
                return {"spuren": tracks}
            try:
                runner.start("export", title, [(title, 1.0)], work)
            except CommandError as e:
                return False, str(e)
            log.info(">>> %s: %s <<<", title, ", ".join(tracks))
            return True, f"{name} queued"
        return run
    for name in EXPORTS:
        commands.add(name, handler(name))


def menu(commands: PunchCommands, rumps: Any) -> Any:
    sub = rumps.MenuItem("Export")
    for name, (title, _fn) in EXPORTS.items():
        def run(_sender: Any, _n: str = name, _t: str = title) -> None:
            ok, msg = commands.dispatch(_n)
            if not ok:
                rumps.notification("Ottilie", _t, msg)
        sub.add(rumps.MenuItem(title, callback=run))
    return sub
