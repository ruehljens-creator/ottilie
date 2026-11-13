"""Betriebsart Übergabeordner im Fenster: Stand des Ordners, „Testmeldung senden“ (Jens 11.11.2026)."""
from __future__ import annotations

import os

from tonmischung import factory
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.dropfolder import handover as H
from tonmischung.dropfolder.client import FolderClient
from tonmischung.dropfolder.mode import MODE_TEXT
from tonmischung.dropfolder.notify import shown_url
from tonmischung.helmut.client import HelmutError


def _client(ctx: AppContext) -> FolderClient:
    folder = getattr(ctx.helmut, "folder", None)
    return folder if folder is not None else factory.folder_client(ctx.settings, lambda: (ctx.mixer, ctx.room),
                                                                   safe_root=ctx.safe_root)


def info(ctx: AppContext) -> dict:
    """Für die Einstellungen: Ordner erreichbar und beschreibbar? Wie viele Filme in welchem Stand?"""
    client = _client(ctx)
    cfg = client.config
    out = {"betriebsart": ctx.mode, "betriebsart_text": MODE_TEXT[ctx.mode], "ordner": cfg.root,
           "netzadresse": cfg.netzadresse,
           "erreichbar": False, "beschreibbar": False, "meldung": "",
           "filme": {H.READY: 0, H.INCOMPLETE: 0, H.TRANSFER: 0},
           "fertigmeldung": {"aktiv": cfg.notify_active, "url": shown_url(cfg.notify.url) if cfg.notify.url else ""}}
    try:
        client.reachable()
        out["erreichbar"] = True
        out["beschreibbar"] = os.access(cfg.root, os.W_OK)
        for c in client.list_for_mixing():
            state = c.raw.get("uebergabe")
            if state in out["filme"]:
                out["filme"][state] += 1
        if not out["beschreibbar"]:
            out["meldung"] = "Nur lesbar – Sperrdateien und „Audio Export“ lassen sich nicht anlegen"
    except HelmutError as e:
        out["meldung"] = str(e)
    folders = getattr(client, "folders", None)
    if callable(folders) and len(folders()) > 1:         # mehrere Übergabeordner (Jens 13.11.2026)
        out["ordner_liste"] = [_one(name, c) for _key, name, c in folders()]
    return out


def _one(name: str, client) -> dict:
    """Stand eines Übergabeordners für die Übersicht."""
    cfg = client.config
    row = {"name": name, "ordner": cfg.root, "netzadresse": cfg.netzadresse, "erreichbar": False, "filme": 0,
           "meldung": ""}
    try:
        client.reachable()
        row["erreichbar"] = True
        row["filme"] = len(client.list_for_mixing())
    except HelmutError as e:
        row["meldung"] = str(e)
    return row


def send_test(ctx: AppContext) -> dict:
    """Testmeldung mit „ereignis: test“ an die eingestellte Adresse."""
    try:
        _client(ctx).test_message()
    except HelmutError as e:
        if e.status in (401, 403):
            raise CommandError("Testmeldung abgelehnt – Anmeldung falsch? Adresse und Schlüsselbund-Eintrag "
                               "prüfen.") from None
        raise CommandError(f"Testmeldung nicht angekommen: {e}") from None
    return {"meldung": "Testmeldung ist angekommen."}


def register(bridge: Bridge, ctx: AppContext) -> None:
    bridge.register("uebergabeordner", lambda args: info(ctx))
    bridge.register("fertigmeldung_testen", lambda args: send_test(ctx))
