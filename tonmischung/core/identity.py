"""Wer mischt an diesem Mac, in welchem Raum steht er – reine Logik.

Raum (Jens 13.11.2026): gekoppelt der Raum der Zentrale (`app/zentrale_app`), sonst die Einstellung
„Raumname dieses Macs“ oder der Rechnername. Nutzer aus dem Mac-Benutzer oder manuell gewählt (ohne Passwort).
"""
from __future__ import annotations

from typing import Mapping, Sequence


def short_host(hostname: str) -> str:
    """„ton3-mac.example.org“ → „ton3-mac“ (Groß/klein egal)."""
    return hostname.split(".", 1)[0].lower()


def current_mixer(users: Sequence[Mapping], mac_user: str, mode: str = "mac_benutzer",
                  manual: str = "") -> str:
    """Anzeigename des Mischers; ohne Eintrag in der Nutzerliste der Mac-Benutzer selbst."""
    if mode == "manuell" and manual:
        for u in users:
            if manual in (u.get("id"), u.get("name")):
                return u.get("name", manual)
        return manual
    for u in users:
        if u.get("mac_benutzer") and u["mac_benutzer"] == mac_user:
            return u.get("name", mac_user)
    return mac_user
