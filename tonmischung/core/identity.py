"""Wer mischt an diesem Mac, in welchem Raum steht er – reine Logik.

Wie im Prototyp (Logik-Referenz `curUserId`, `thisRoom`):
Raum aus dem Rechnernamen oder fest eingestellt; Nutzer aus dem Mac-Benutzer
oder manuell gewählt (ohne Passwort).
"""
from __future__ import annotations

from typing import Mapping, Sequence


def short_host(hostname: str) -> str:
    """„ton3-mac.example.org“ → „ton3-mac“ (Groß/klein egal)."""
    return hostname.split(".", 1)[0].lower()


def this_room(rooms: Sequence[Mapping], hostname: str, mode: str = "rechnername",
              fixed: str = "") -> str:
    """Name des Raums dieses Macs; leer, wenn er nicht zugeordnet werden kann."""
    if mode == "fest" and fixed:
        for r in rooms:
            if fixed in (r.get("id"), r.get("name")):
                return r.get("name", fixed)
        return fixed
    host = short_host(hostname)
    for r in rooms:
        if short_host(r.get("rechnername", "")) == host:
            return r.get("name", "")
    return ""


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
