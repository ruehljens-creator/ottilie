"""Nachtrag im Fenster: liegengebliebene Helmut-Änderungen nachtragen, verwerfen, Havarie-Sessions zuordnen.

Die Warteschlange selbst steht in `core/catchup.py`, das Schreiben in `helmut/catchup.py`.
"""
from __future__ import annotations

import threading

from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.core import mixstate
from tonmischung.helmut.client import HelmutAuthError, HelmutError, HelmutUnavailableError

_catchup_lock = threading.Lock()


def catch_up(ctx: AppContext, args: dict | None = None) -> dict:
    """Liegengebliebene Änderungen jetzt nachtragen. Der 60-s-Takt der App (`nur_eigene`) nimmt nur die Einträge
    dieses macOS-Benutzers; im Fenster („Jetzt nachtragen“) auch die anderer Benutzer des Macs."""
    from tonmischung.helmut.catchup import replay
    if ctx.queue is None:
        return {"text": "keine Warteschlange", "offen": 0, "konflikte": []}
    if not _catchup_lock.acquire(blocking=False):
        return {"text": "Nachtragen läuft schon", **ctx.queue.summary()}
    try:
        report = replay(ctx.queue, ctx.helmut, only_own=bool((args or {}).get("nur_eigene")))
    finally:
        _catchup_lock.release()
    return {"text": report.text, **ctx.queue.summary()}


def dismiss_conflict(ctx: AppContext, args: dict) -> dict:
    """Nachtrag verwerfen: ein Eintrag (`id`), alle einer Havarie-Session (`havarie`) oder alle (`alle`). Helmut bleibt,
    wie es ist. Nicht, während gerade nachgetragen wird (sonst schriebe der Nachtrag den Eintrag doch noch)."""
    if ctx.queue is None:
        raise CommandError("keine Warteschlange")
    with ctx.queue.replaying() as free:
        if not free:
            raise CommandError("Nachtragen läuft gerade – bitte gleich noch einmal versuchen")
        if args.get("alle") or args.get("havarie"):
            n = ctx.queue.dismiss_many(str(args["havarie"]) if args.get("havarie") else None)
            text = f"{n} Änderung{'en' if n != 1 else ''} verworfen – Helmut bleibt, wie es ist"
        else:
            try:
                ctx.queue.dismiss(str(args.get("id") or ""))
            except KeyError as e:
                raise CommandError(str(e)) from e
            text = "Verworfen – Helmut bleibt, wie es ist"
    return {"text": text, **ctx.queue.summary()}


def assign_havarie(ctx: AppContext, args: dict) -> dict:
    """Havarie-Session einem Helmut-Beitrag zuordnen und gleich nachtragen."""
    if ctx.queue is None:
        raise CommandError("keine Warteschlange")
    pid = str(args.get("id") or "").strip()
    _check_assignable(ctx, pid)
    n = ctx.queue.assign(str(args.get("havarie") or ""), pid)
    if not n:
        raise CommandError("Nichts zuzuordnen (schon zugeordnet oder unbekannt)")
    return catch_up(ctx)


def _check_assignable(ctx: AppContext, project_id: str) -> None:
    """Wurde der Beitrag inzwischen übernommen (auch von diesem Mischer, normal geholt) oder ausgeliefert, überschriebe
    die Havarie-Übernahme diesen Stand – dann nicht zuordnen (Ausfall R3, R7). Ist Helmut weg, prüft der
    Nachtrag später dasselbe (`mixstate.havarie_blocker`). Eine falsche ID wird gemeldet, nicht geschluckt."""
    try:
        m = ctx.helmut.get(project_id).mix
    except (HelmutUnavailableError, HelmutAuthError):
        # Ohne Helmut lässt sich nicht prüfen, ob der Beitrag inzwischen normal geholt wurde – der Nachtrag hielte das
        # für die eigene Havarie-Übernahme (Prüfer 11.11.2026). Also erst zuordnen, wenn Helmut antwortet.
        raise CommandError("Zuordnen geht nur, wenn Helmut erreichbar ist – bitte später erneut") from None
    except HelmutError as e:
        if e.status == 404:
            raise CommandError(f"Beitrag „{project_id}“ in Helmut nicht gefunden") from None
        raise CommandError(f"Helmut meldet einen Fehler ({e.status or 'unbekannt'}) – bitte später erneut") from None
    blocker = mixstate.havarie_blocker(m, ctx.mixer, ctx.room)
    if blocker:
        raise CommandError(f"Nicht zugeordnet – {blocker}. Verwerfen nimmt nur die Einträge aus dem Nachtrag; die "
                           "Havarie-Session bleibt auf der Platte erhalten.")


def register(bridge: Bridge, ctx: AppContext) -> None:
    bridge.register("nachtragen", lambda args: catch_up(ctx, args))
    bridge.register("nachtrag_verwerfen", lambda args: dismiss_conflict(ctx, args))
    bridge.register("havarie_zuordnen", lambda args: assign_havarie(ctx, args))
