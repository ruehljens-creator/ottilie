"""Benachrichtigungen über Helmut – wer, was (reine Logik).

Im Set „Mischung“ stehen Mischer nur mit Anzeigenamen. Helmut-Benutzernamen kommen aus der Liste
„Räume & Nutzer“ (`system.raeume_nutzer.nutzer`, Feld `helmut_benutzer`). Wer dort fehlt, wird
nicht benachrichtigt; die App sagt das, statt still nichts zu tun.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from tonmischung.core.mixstate import KEEP_LABEL


def helmut_users(names: Iterable[str], users: Iterable[Mapping]) -> tuple[list[str], list[str]]:
    """(Helmut-Benutzernamen, Namen ohne Helmut-Benutzer) – ohne Doppelte und Leere."""
    by_name = {str(u.get("name", "")).strip().lower(): str(u.get("helmut_benutzer") or "").strip()
               for u in users}
    found: list[str] = []
    missing: list[str] = []
    for n in names:
        n = (n or "").strip()
        if not n or n in ("–", "-"):
            continue
        user = by_name.get(n.lower(), "")
        if user and user not in found:
            found.append(user)
        elif not user and n not in missing:
            missing.append(n)
    return found, missing


def release_message(title: str, reason: str, keep: str, room: str, by: str) -> tuple[str, str]:
    """Titel und Text der Mitteilung „Beitrag freigegeben“."""
    where = f" aus {room}" if room else ""
    return ("Ottilie: Beitrag freigegeben",
            f"„{title}“ wurde{where} freigegeben ({reason}) von {by}. Beim nächsten Öffnen: "
            f"{KEEP_LABEL.get(keep, keep)}. Der Beitrag steht wieder unter „Zur Mischung“.")
