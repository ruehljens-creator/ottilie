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
             "- Zugangsdaten gehören nie ins Profil – nur der Name des Schlüsselbund-Eintrags.",
             "- **Geräteverwaltung:** Konfigurationsprofil auf Computerebene (nicht Benutzerebene), eigene "
             f"Einstellungen für die Domäne `{PREFERENCE_DOMAIN}`. Rechnerweit gesetzt gilt "
             "es für alle Benutzer des Macs – z. B. `system.zentrale.adresse` und "
             "`system.uebergabeordner.netzadresse`, damit kein Benutzer etwas eintragen muss.",
             "- Die Kopplung mit der Zentrale gilt ebenfalls je Mac: einmal anmelden oder koppeln (Administrator "
             "bestätigt), danach für alle Benutzer. Den Raum und die Übergabeordner legt die Administration der "
             "Zentrale fest, nicht das Profil.",
             "- **Reihenfolge beim Umstieg auf 0.3:** erst die Zentrale aktualisieren, dort Räume anlegen und "
             "Betriebsart sowie Übergabeordner setzen, dann die Macs aktualisieren und je Mac anmelden und zuordnen.",
             "", "## Gemeinsamer Datenordner vorbereiten (empfohlen)", "",
             "Nachtrag, Mischungsverzeichnis und Havarie-Pakete liegen für alle Benutzer eines Macs in "
             "`/Users/Shared/Ottilie`. Am sichersten legt ihn die Geräteverwaltung als root an – dann ändert Ottilie "
             "an Besitz und Rechten nichts:", "",
             "```sh", "install -d -o root -g staff -m 2775 /Users/Shared/Ottilie", "```", "",
             "- Alle Benutzer, die mischen, müssen in der Gruppe `staff` sein (lokale Benutzer sind es; bei "
             "Verzeichniskonten mit `dseditgroup -o checkmember -m <name> staff` prüfen).",
             "- Ottilie nimmt keinen Verweis und keinen für alle beschreibbaren Ordner. Ist der Ordner nicht nutzbar, "
             "arbeitet Ottilie im Benutzerordner weiter und sagt es beim Start; der Diagnosebericht zeigt es im "
             "Abschnitt „Zentrale und gemeinsamer Datenordner“.",
             "- Ein anderer Ort: `system.raeume_nutzer.datenordner` per Profil (für alle Benutzer) – im Fenster nur "
             "für den einzelnen Benutzer, nie über die Zentrale oder die Schnittstelle.",
             "- Jeder Benutzer des Macs kann die Dateien dort ändern – der Ordner setzt Vertrauen unter den Benutzern "
             "eines Macs voraus. Einträge anderer Benutzer trägt Ottilie nicht von selbst nach.",
             "- Sicherungen, die nur Benutzerordner erfassen, schließen `/Users/Shared/Ottilie` nicht ein.", "",
             "## Entfallene Schlüssel (bis 0.2)", "",
             "Diese Schlüssel wirken seit 0.3 nicht mehr – bitte aus alten Profilen entfernen:", "",
             "| Schlüssel | Ersatz |", "|---|---|",
             "| `system.raeume_nutzer.raeume`, `raum_erkennung`, `raum_fest` | Raum aus der Zentrale "
             "(Administration → Räume & Geräte); ohne Zentrale `system.raeume_nutzer.raumname` |",
             "| `system.raeume_nutzer.raumstatus.*` | Raumstatus über die Zentrale |",
             "| ID-Code in der Raumliste | `system.streamdeck_webtrigger.id_code` (je Mac) |", "",
             f"## Vorgesehene Schlüssel ({len(rows)})", "",
             "| Schlüssel | Typ | Hausstandard | Erlaubt | Bedeutung |", "|---|---|---|---|---|"]
    for path, f in rows:
        node = f.node
        allowed = ", ".join(map(str, node["enum"])) if "enum" in node else ""
        meaning = label(path, node) + (f" – {node['description']}" if node.get("description") else "")
        lines.append(f"| `{path}` | {_type(node)} | {_md(f.default)} | {_md(allowed) if allowed else ''} | "
                     f"{_md(meaning) if meaning else ''} |")
    lines += ["", "Jede andere Einstellung lässt sich ebenso erzwingen (Schlüssel = Pfad im Schema). Für alle Macs "
                  "gleich sind die meisten oben genannten; **je Mac verschieden** sein müssen "
                  "`system.raeume_nutzer.raumname` (nur ohne Zentrale) und `system.streamdeck_webtrigger.id_code` – "
                  "diese nur in einem eigenen Profil je Mac setzen, nie in einem gemeinsamen (sonst lösen Netz-Trigger "
                  "im falschen Raum aus).", ""]
    lines += PER_MAC_AND_USER
    return "\n".join(lines)


# Auftrag „rechnerweit“ (13.11.2026): was je Mac gilt und was je Benutzer bleibt – mit Empfehlung
PER_MAC_AND_USER = [
    "## Je Mac und je Benutzer", "",
    "Für alle Benutzer des Macs gemeinsam: Kopplung mit der Zentrale (System-Schlüsselbund); Nachtrag, "
    "Mischungsverzeichnis, Havarie-Pakete, zuletzt bekannter Raum und zuletzt empfangene Vorgaben der Zentrale im "
    "gemeinsamen Datenordner (`/Users/Shared/Ottilie`, Gruppe `staff`, anderer Ort: "
    "`system.raeume_nutzer.datenordner`).", "",
    "| Was bleibt je Benutzer | Wo | Empfehlung |", "|---|---|---|",
    "| Einstellungen, die nicht per Profil kommen | `~/Library/Application Support/Ottilie/einstellungen.json` | "
    "Gemeinsames per Konfigurationsprofil (Computerebene) oder über die Zentrale (Übergabeordner) vorgeben |",
    "| Helmut-Zugang (Token oder Kennwort) | Anmelde-Schlüsselbund, Eintrag `ottilie-helmut` | je Benutzer anlegen |",
    "| Fertigmeldung mit Anmeldung | Anmelde-Schlüsselbund, Eintrag `ottilie-fertigmeldung` | je Benutzer; ohne "
    "Anmeldung nichts nötig |",
    "| Anmeldung an der Übergabe-Freigabe | Anmelde-Schlüsselbund `ottilie-uebergabeordner` (nur Verfahren "
    "„Schlüsselbund“) | Gast oder Kerberos brauchen nichts je Benutzer |",
    "| Webtrigger-Token | Anmelde-Schlüsselbund `ottilie-webtrigger` | entsteht von selbst |",
    "| Pro-Tools-Templates, „Meine Templates“ | `~/Documents/Pro Tools/Session Templates` | je Benutzer (Pro Tools) |",
    "| Rechte Bildschirmaufnahme, Bedienungshilfen, Automation | macOS-Datenschutz | am Mac bestätigen: das Paket ist "
    "ad hoc signiert, ein PPPC-Profil bräuchte eine feste Signatur (Developer ID). Bildschirmaufnahme lässt sich per "
    "Profil ohnehin nicht vorab erlauben, nur Standardbenutzern das Bestätigen |",
    "| Start bei der Anmeldung, Stream-Deck-Launcher | `~/Library/LaunchAgents`, `~/Applications` | je Benutzer oder "
    "per Geräteverwaltung verteilen |",
    "| Protokolle | `~/Library/Logs/Ottilie/Ottilie.log` (7 Tage) | Diagnosebericht des jeweiligen Benutzers |",
    "| Webtrigger-Port bei schneller Benutzerumschaltung | – | die zweite Ottilie weicht auf einen anderen Port aus |",
    ""]


def plist(schema: Mapping | None = None) -> bytes:
    """Vorlage für die Geräteverwaltung: alle vorgesehenen Schlüssel mit dem Hausstandard (leere Werte weggelassen)."""
    data = {p: f.default for p, f in mdm_fields(schema) if f.default not in (None, "", [], {})}
    return plistlib.dumps(data, sort_keys=True)
