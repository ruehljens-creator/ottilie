"""Schlüssel für das Konfigurationsprofil der Geräteverwaltung (MDM) – aus dem Schema erzeugt (Auftrag M9:
Schlüssel dokumentiert). `python3 -m tonmischung mdm-doku > docs/MDM.md`, `… mdm-doku --plist`.

Die App liest verwaltete Werte über CFPreferences in der Domain `PREFERENCE_DOMAIN`; Schlüssel ist der Punktpfad
der Einstellung. Gezählt wird nur, was das Profil erzwingt; im Fenster erscheint der Wert gesperrt.
"""
from __future__ import annotations

import json
import plistlib
from collections.abc import Mapping
from typing import Any

from tonmischung.config.settings import LAYER_MDM, PREFERENCE_DOMAIN, load_schema, schema_fields

_TYPE = {"boolean": "Boolean", "integer": "Integer", "number": "Real", "string": "String", "array": "Array",
         "object": "Dictionary"}


def _type(node: Mapping) -> str:
    if "enum" in node:
        return "String" if all(isinstance(v, str) for v in node["enum"]) else "Integer"
    return _TYPE.get(str(node.get("type")), "String")


def mdm_fields(schema: Mapping | None = None) -> list[tuple[str, Any]]:
    """Einstellungen, die für das Profil vorgesehen sind (Quelle „mdm“) und schon wirken (ohne `x-ab`)."""
    fields = schema_fields(schema or load_schema())
    return [(p, f) for p, f in fields.items() if f.source == LAYER_MDM and not f.node.get("x-ab")]


def _md(value: Any) -> str:
    if value is None:
        return "–"
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return (text or "„“").replace("|", "\\|")


def markdown(schema: Mapping | None = None) -> str:
    from tonmischung.app.settings_view import label     # Beschriftung wie im Fenster
    rows = mdm_fields(schema)
    lines = ["# Konfigurationsprofil für Ottilie (Geräteverwaltung, MDM)", "",
             "Erzeugt aus `tonmischung/config/einstellungen.schema.json` (`python3 -m tonmischung mdm-doku > "
             "docs/MDM.md`). Nicht von Hand ändern.", "",
             "## So wird es eingerichtet", "",
             "- In der Geräteverwaltung ein Konfigurationsprofil mit eigenen Einstellungen (Custom Settings) für die "
             f"Domäne **`{PREFERENCE_DOMAIN}`** anlegen – sie ist die Bundle-ID der App.",
             "- Schlüssel ist der Punktpfad der Einstellung (Tabelle). Eine Vorlage mit allen Schlüsseln und den "
             "Hausstandard-Werten erzeugt `python3 -m tonmischung mdm-doku --plist`.",
             "- Nur erzwungene Werte zählen. In der App erscheinen sie gesperrt („Geräteverwaltung – gesperrt“) und "
             "überstimmen Spurprofil, zentrale und lokale Werte.",
             "- Zugangsdaten gehören nie ins Profil – nur der Name des Schlüsselbund-Eintrags.", "",
             f"## Vorgesehene Schlüssel ({len(rows)})", "",
             "| Schlüssel | Typ | Hausstandard | Erlaubt | Bedeutung |", "|---|---|---|---|---|"]
    for path, f in rows:
        node = f.node
        allowed = ", ".join(map(str, node["enum"])) if "enum" in node else ""
        meaning = label(path, node) + (f" – {node['description']}" if node.get("description") else "")
        lines.append(f"| `{path}` | {_type(node)} | {_md(f.default)} | {_md(allowed) if allowed else ''} | "
                     f"{_md(meaning) if meaning else ''} |")
    lines += ["", "Jede andere Einstellung lässt sich ebenso erzwingen (Schlüssel = Pfad im Schema); vorgesehen sind "
                  "die oben genannten, weil sie für alle Macs gleich sein sollen.", ""]
    return "\n".join(lines)


def plist(schema: Mapping | None = None) -> bytes:
    """Vorlage für die Geräteverwaltung: alle vorgesehenen Schlüssel mit dem Hausstandard (leere Werte weggelassen)."""
    data = {p: f.default for p, f in mdm_fields(schema) if f.default not in (None, "", [], {})}
    return plistlib.dumps(data, sort_keys=True)
