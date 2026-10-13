"""Einstellungen exportieren und importieren (System › „Einstellungen übertragen“) – Befehle für das Fenster."""
from __future__ import annotations

import json
import time
from typing import Any

from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.profiles_app import _free_name, choose_open, choose_save
from tonmischung.config.settings import SettingLockedError
from tonmischung.core import settings_transfer as ST
from tonmischung.core.fileio import write_new, write_text


def _areas() -> list[dict]:
    from tonmischung.app import settings_view as sv
    from tonmischung.config.settings import load_schema
    return sv.areas(load_schema())


def export_all(ctx: AppContext, choose: Any = None, now: Any = None) -> dict[str, Any]:
    s = ctx.settings
    # Mit Anmeldung: die Werte des Nutzers für Nutzer-Einstellungen, die des Macs für Gerätebezogenes (Schritt 2)
    values = {p: v for p, v in s.local_values().items() if not (s.user_active and s.is_user_path(p))}
    values.update(s.user_values())
    if not values:
        return {"message": "Auf diesem Mac ist nichts geändert – es gilt überall der Hausstandard"}
    name = f"Ottilie Einstellungen {ctx.room}.json".replace("  ", " ")
    target = choose(name) if choose else choose_save(name, "Einstellungen exportieren")
    if not target:
        return {"message": "Export abgebrochen"}
    path = _free_name(target)
    doc = ST.export_document(values, now() if now else time.strftime("%d.%m.%Y %H:%M"), f"{ctx.mixer} · {ctx.room}")
    write_new(path, lambda tmp: write_text(tmp, json.dumps(doc, ensure_ascii=False, indent=1)))   # nie überschreiben
    return {"message": f"{len(doc['werte'])} Einstellungen exportiert: {path}", "path": path}


def _read(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return ST.read_document(json.load(f))
    except (OSError, ValueError) as e:
        raise CommandError(f"Nicht lesbar: {e}") from e


def check(ctx: AppContext, path: str = "", choose: Any = None) -> dict[str, Any]:
    """Datei lesen und die Auswahl zeigen – noch nichts ändern."""
    path = path or (choose() if choose else choose_open("Einstellungen importieren"))
    if not path:
        return {"path": "", "choices": [], "message": "Import abgebrochen"}
    values = _read(path)
    choices, unknown = ST.plan(values, ctx.settings, _areas())
    return {"path": path, "choices": [c.view() for c in choices], "unknown": unknown,
            "message": "" if choices else "Nichts zu übernehmen"}


def do_import(ctx: AppContext, path: str, chosen: list[str], skip_protected: bool = False) -> dict[str, Any]:
    if not chosen:
        raise CommandError("Nichts gewählt")
    values = _read(path)
    skipped = []
    if skip_protected:                              # über Socket/MCP: geschützte Werte nur im Fenster (Review A3)
        from tonmischung.app.api import protected_setting
        skipped = [k for k in values if protected_setting(k)]
        values = {k: v for k, v in values.items() if k not in skipped}
    choices, _unknown = ST.plan(values, ctx.settings, _areas())
    try:
        r = ST.apply(values, choices, chosen, ctx.settings)
    except SettingLockedError as e:
        raise CommandError(str(e)) from e
    binder = ctx.extra.get("profile_binder")
    if binder is not None:
        binder.refresh()
    parts = [f"{r.written} Einstellungen übernommen"] + ([f"{r.profiles} Spurprofil(e)"] if r.profiles else []) + \
        ([f"{len(r.skipped_locked)} von der Geräteverwaltung gesperrt, nicht geändert"] if r.skipped_locked else []) + \
        ([f"{len(skipped)} nur im Fenster änderbar, nicht übernommen"] if skipped else [])
    return {"message": ", ".join(parts)}


def register(bridge: Bridge, ctx: AppContext) -> None:
    from tonmischung.app.profiles_app import target_chooser
    bridge.register("einstellungen_exportieren",
                    lambda args: export_all(ctx, choose=target_chooser(str(args.get("ziel") or ""))))
    bridge.register("einstellungen_import_pruefen", lambda args: check(ctx, str(args.get("pfad") or "")))
    bridge.register("einstellungen_importieren",
                    lambda args: do_import(ctx, str(args.get("pfad") or ""), list(args.get("auswahl") or []),
                                           skip_protected=bool(args.get("_ohne_geschuetzte"))))
