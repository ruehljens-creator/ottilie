"""Nutzer im Fenster: anmelden (ohne Passwort), anlegen, umbenennen, duplizieren, ins Archiv löschen, Standard-Nutzer
dieses Macs (Plan Nutzer & Räume, Schritt 1; Jens 04.10.2026).

Ohne Anmeldung laufen die PunchBuddy-Funktionen (Aufnahme, Stream Deck, Webtrigger) mit den Einstellungen des Macs;
Holen, Zurückliefern und Havarie brauchen einen angemeldeten Nutzer.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.core.users import User, UserError, UserStore, default_root

log = logging.getLogger(__name__)

P = "system.raeume_nutzer."


def store_root(ctx: AppContext) -> str:
    """Lokal je Mac (Vorgabe) oder der gemeinsame Ordner aus den Einstellungen."""
    s = ctx.settings
    shared = str(s.get(P + "gemeinsamer_ordner") or "")
    if s.get(P + "ablage") == "gemeinsamer_ordner" and shared:
        new, old = os.path.join(shared, "Ottilie Nutzer"), os.path.join(shared, "Tonmischung Nutzer")
        # gemeinsamer Ordner von vor dem 08.10.2026
        return old if os.path.isdir(old) and not os.path.isdir(new) else new
    return str(default_root(mock=ctx.mock is not None))


def store(ctx: AppContext) -> UserStore:
    root = store_root(ctx)
    if ctx.users is None or ctx.users.root != root:
        ctx.users = UserStore(root)
    return ctx.users


def startup(ctx: AppContext, selftest: bool = False) -> None:
    """Beim Start: ohne Nutzer einen aus dem bisherigen Mischernamen anlegen (nichts geht verloren); den
    Standard-Nutzer dieses Macs anmelden. Sonst fragt das Fenster."""
    st = store(ctx)
    try:
        mine = {p: v for p, v in ctx.settings.local_values().items() if ctx.settings.is_user_path(p)}
        if not st.users():
            u = st.create(ctx.mixer or "Mischer")
            st.save_settings(u.id, mine)                 # erster Nutzer übernimmt die bisherigen Werte des Macs
            log.info("Erster Nutzer angelegt: %s, %d Einstellungen des Macs übernommen", u.name, len(mine))
        for u in st.users():
            if not st.has_settings(u.id):
                # vor Schritt 2 angelegt (ohne eigene Datei): bisherige Werte des Macs übernehmen – nichts geht verloren
                st.save_settings(u.id, mine)
                log.info("Einstellungen des Macs an „%s“ übernommen: %d Werte", u.name, len(mine))
    except (OSError, UserError) as e:
        log.warning("Nutzer anlegen: %s", e)
    wanted = str(ctx.settings.get(P + "standard_nutzer") or "")
    u = st.get(wanted) if wanted else None
    if u is None and selftest:
        u = next(iter(st.users()), None)
    if u is not None:
        _login(ctx, u)


def _login(ctx: AppContext, u: User) -> None:
    st = store(ctx)
    try:
        values = st.load_settings(u.id)
    except UserError as e:
        raise CommandError(str(e)) from e
    ctx.settings.set_user_layer(values, u.name, writer=lambda vals, uid=u.id: st.save_settings(uid, vals))
    ctx.user = u
    ctx.mixer = u.name
    log.info("Angemeldet: %s (%s), %d eigene Einstellungen", u.name, u.id, len(values))


def _busy(runner: Any) -> bool:
    return runner is not None and runner.busy()


def view(ctx: AppContext) -> dict[str, Any]:
    st = store(ctx)
    users = [{"id": u.id, "name": u.name} for u in st.users()]
    return {"users": users, "current": {"id": ctx.user.id, "name": ctx.user.name} if ctx.user else None,
            "standard": str(ctx.settings.get(P + "standard_nutzer") or ""), "needs_login": ctx.user is None,
            "ablage": st.root}


def login(ctx: AppContext, runner: Any, args: dict) -> dict[str, Any]:
    if _busy(runner) and ctx.user is not None and ctx.user.id != args.get("id"):
        raise CommandError("Wechsel erst, wenn kein Auftrag läuft")
    u = store(ctx).get(str(args.get("id") or ""))
    if u is None:
        raise CommandError("Nutzer nicht gefunden")
    _login(ctx, u)
    if "standard" in args:
        current = str(ctx.settings.get(P + "standard_nutzer") or "")
        if args["standard"]:
            ctx.settings.set_local(P + "standard_nutzer", u.id)
        elif current == u.id:
            ctx.settings.set_local(P + "standard_nutzer", "")
    return view(ctx)


def require_user(ctx: AppContext) -> None:
    if ctx.user is None:
        raise CommandError("Bitte zuerst anmelden (oben rechts: Nutzer wählen)")


def _guard(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except UserError as e:
        raise CommandError(str(e)) from e
    except OSError as e:
        raise CommandError(f"Nutzerablage: {e}") from e


def create(ctx: AppContext, args: dict) -> dict[str, Any]:
    u = _guard(lambda: store(ctx).create(str(args.get("name") or "")))
    return {**view(ctx), "message": f"„{u.name}“ angelegt"}


def rename(ctx: AppContext, args: dict) -> dict[str, Any]:
    u = _guard(lambda: store(ctx).rename(str(args.get("id") or ""), str(args.get("name") or "")))
    if ctx.user is not None and ctx.user.id == u.id:
        _login(ctx, u)
    return {**view(ctx), "message": f"Umbenannt in „{u.name}“"}


def duplicate(ctx: AppContext, args: dict) -> dict[str, Any]:
    u = _guard(lambda: store(ctx).duplicate(str(args.get("id") or ""), str(args.get("name") or "")))
    return {**view(ctx), "message": f"„{u.name}“ angelegt – mit allen Einstellungen und Templates"}


def delete(ctx: AppContext, args: dict) -> dict[str, Any]:
    uid = str(args.get("id") or "")
    if ctx.user is not None and ctx.user.id == uid:
        raise CommandError("Der angemeldete Nutzer kann nicht gelöscht werden – erst einen anderen wählen")
    st = store(ctx)
    u = st.get(uid)
    dest = _guard(lambda: st.archive(uid))
    if str(ctx.settings.get(P + "standard_nutzer") or "") == uid:
        ctx.settings.set_local(P + "standard_nutzer", "")
    return {**view(ctx), "message": f"„{u.name if u else uid}“ ins Archiv verschoben: {dest}"}


def register(bridge: Bridge, ctx: AppContext, runner: Any = None) -> None:
    bridge.register("nutzer", lambda args: view(ctx))
    bridge.register("nutzer_anmelden", lambda args: login(ctx, runner, args))
    bridge.register("nutzer_anlegen", lambda args: create(ctx, args))
    bridge.register("nutzer_umbenennen", lambda args: rename(ctx, args))
    bridge.register("nutzer_duplizieren", lambda args: duplicate(ctx, args))
    bridge.register("nutzer_loeschen", lambda args: delete(ctx, args))
