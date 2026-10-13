"""Session-Name und Ablageort – reine Logik."""
from __future__ import annotations

import os
import re
from typing import Callable

_FORBIDDEN = re.compile(r'[/\\:*?"<>|]+')
_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}


def ascii_umlauts(text: str) -> str:
    """„Größe“ → „Groesse“ (Kennungen, Dateinamen für Sendefiles) – eine Tabelle statt zweier (Review C)."""
    return "".join(_UMLAUTS.get(c, c) for c in str(text))


def clean_name(text: str) -> str:
    """Zeichen, die Pro Tools oder der Finder nicht mögen (/ \\ : * ? " < > |), werden „_“; Ränder ohne „ ._“."""
    return _FORBIDDEN.sub("_", text).strip(" ._")


def slug(text: str) -> str:
    """„Record A (Gäste)“ → „record-a-gaeste“ (Kennungen, z. B. der Stream-Deck-Starter)."""
    return re.sub(r"[^a-z0-9]+", "-", ascii_umlauts(text).lower()).strip("-")


def session_name_for(title: str, project_id: str) -> str:
    """Titel bis zum Doppelpunkt, ohne Zeichen, die Pro Tools/Finder nicht mögen.

    „Bahnstreik: Pendler warten“ → „Bahnstreik“. Leerer Titel → Projekt-ID.
    """
    base = title.split(":", 1)[0].strip()
    return clean_name(base)[:60] or project_id


def unique_session_dir(parent: str, name: str,
                       exists: Callable[[str], bool] = os.path.exists) -> tuple[str, str]:
    """(Ordner, Name) für eine neue Session, die nichts Vorhandenes überschreibt.

    Pro Tools legt in `session_location` einen Ordner mit dem Session-Namen an.
    Gibt es ihn schon, wird _2, _3 … angehängt.
    """
    candidate, n = name, 1
    while exists(os.path.join(parent, candidate)):
        n += 1
        candidate = f"{name}_{n}"
    return parent, candidate
