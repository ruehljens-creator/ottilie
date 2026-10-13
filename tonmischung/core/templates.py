"""Persönliche Templates: Vorauswahl beim Holen und „zuletzt je Sendung“ – reine Logik.

Funktionsbeschreibung S. 8: „Vorausgewählt ist sein Standard oder, wenn eingeschaltet, das zuletzt
für diese Sendung benutzte Template („zuletzt“).“ Der Prototyp (`pickTplFor`) vergleicht nur das feste
Feld „Sendung“ eines Templates. Ottilie merkt sich zusätzlich, was wirklich zuletzt je Sendung
benutzt wurde, und nimmt das vorrangig.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

STANDARD = "Ihr Standard"


@dataclass(frozen=True)
class Template:
    group: str
    name: str
    label: str = ""
    show: str = ""
    id: str = ""

    @property
    def key(self) -> str:
        return f"{self.group}/{self.name}"


@dataclass(frozen=True)
class Preselection:
    template: Template
    hint: str                       # „Ihr Standard“, „zuletzt für …“, „für …“


def from_settings(entries: Iterable[Mapping]) -> list[Template]:
    """„Meine Templates“ aus den Einstellungen (`persoenlich.templates`)."""
    return [Template(str(t.get("gruppe", "")), str(t.get("pt_name", "")),
                     str(t.get("anzeigename") or t.get("pt_name", "")), str(t.get("sendung", "")),
                     str(t.get("id", ""))) for t in entries if t.get("pt_name")]


def preselect(mine: list[Template], found: list[Template], default_id: str,
              last_by_show: Mapping[str, str], show: str, suggest: bool) -> Preselection | None:
    """Vorauswahl für einen Beitrag der Sendung `show`.

    Mit Vorschlag nach Sendung: 1. zuletzt für diese Sendung benutzt (wenn es das Template noch gibt),
    2. erstes eigenes Template mit dieser Sendung (wie im Prototyp), 3. Standard. Ohne: nur der Standard.
    """
    known = {t.key: t for t in [*found, *mine]}           # eigene Einträge gewinnen (Anzeigename)
    default = next((t for t in mine if default_id and t.id == default_id), None)
    if suggest and show:
        last = known.get(last_by_show.get(show, ""))
        if last is not None:
            return Preselection(last, STANDARD if default and last.key == default.key else f"zuletzt für {show}")
        by_show = next((t for t in mine if t.show and t.show == show), None)
        if by_show is not None:
            return Preselection(by_show, STANDARD if default and by_show.key == default.key else f"für {show}")
    return Preselection(default, STANDARD) if default else None


def remember(last_by_show: Mapping[str, str], show: str, group: str, name: str) -> dict[str, str]:
    """Neue Zuordnung Sendung → zuletzt benutztes Template (`persoenlich.zuletzt_je_sendung`)."""
    out = dict(last_by_show)
    if show:
        out[show] = f"{group}/{name}"
    return out
