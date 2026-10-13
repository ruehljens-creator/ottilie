"""Einstellungsfenster (M6b): Bereiche, Abschnitte und Felder aus dem Schema – reine Funktionen.

Feldarten je Schemaknoten: Schalter, Auswahl, Zahl, Text, Liste (je Zeile ein Eintrag), Tabelle
(Liste von Einträgen mit Spalten) und JSON für alles Übrige. Je Feld: wirksamer Wert, Herkunft
(Geräteverwaltung – gesperrt, zentral, lokal, Standard) und Hinweise.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from typing import Any

from tonmischung.config.settings import LAYER_MDM, Settings, SettingLockedError, validate

# Titel, die im Schema fehlen (wie im Prototyp)
TITLES = {
    "import.zuordnungsregeln": "Zuordnungsregeln", "import.zielspuren": "Zielspuren",
    "import.timecode_startband": "Timecode & Startband", "import.import_vorschau": "Import & Vorschau",
    "export.baton": "BATON", "export.aktives_preset": "Aktives Preset",
}
LAYER_TEXT = {"mdm": "Geräteverwaltung – gesperrt", "spurprofil": "aus dem Spurprofil der offenen Session",
              "zentral": "zentral", "lokal": "lokal geändert", "standard": "Hausstandard", "nutzer": "Nutzer"}
SOURCE_HINT = {"helmut": "wird später zentral über Helmut verwaltet – bis dahin lokal",
               "helmut-nutzer": "wird später je Nutzer in Helmut gespeichert – bis dahin lokal",
               "mdm": "kommt aus der Geräteverwaltung, wenn dort festgelegt"}
SKIP = {"schema_version"}


def _resolve(schema: Mapping, node: Mapping) -> Mapping:
    if "$ref" in node:
        base = dict(schema.get("$defs", {})[node["$ref"].rsplit("/", 1)[-1]])
        base.update({k: v for k, v in node.items() if k != "$ref"})
        return base
    return node


# Wörter aus Schlüsseln, die als Beschriftung lesbar sein sollen (Umlaute, Abkürzungen)
WORDS = {"o": "O", "ton": "Ton", "lufs": "LUFS", "dbtp": "dBTP", "wav": "WAV", "aaf": "AAF", "pt": "PT",
         "id": "ID", "url": "URL", "tc": "TC", "r128": "R 128", "mxf": "MXF", "pse": "PSE", "qc": "QC",
         "fuer": "für", "ueber": "über", "schluesselwoerter": "Schlüsselwörter", "ueberhaenge": "Überhänge",
         "anhaengen": "anhängen", "kanaele": "Kanäle", "pruefung": "Prüfung", "pruefen": "prüfen",
         "groesse": "Größe", "loeschen": "löschen", "loescht": "löscht", "spuren": "Spuren", "zurueck": "zurück",
         "kuerzen": "kürzen",
         "max": "max.", "min": "min.", "s": "(s)", "ms": "(ms)", "faellt": "fällt", "laenge": "Länge",
         "verfuegbar": "verfügbar", "kanal": "Kanal", "oeffnen": "öffnen", "schliessen": "schließen",
         "standard": "Standard", "aktiv": "aktiv", "ziel": "Ziel", "true": "True", "peak": "Peak"}
LABELS = {"o_ton": "O-Ton", "kanal_12": "Kanal 1/2", "kanal_34": "Kanal 3/4",
          "pt_spur": "Pro-Tools-Spur", "r128": "R 128", "von": "von", "bis": "bis"}


def _label(key: str) -> str:
    if key in LABELS:
        return LABELS[key]
    words = [WORDS.get(w, w) for w in key.split("_") if w]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def _title(path: str, node: Mapping) -> str:
    return node.get("title") or TITLES.get(path) or _label(path.rsplit(".", 1)[-1])


def label(path: str, node: Mapping) -> str:
    """Beschriftung einer Einstellung wie im Fenster (auch für erzeugte Dokumentation)."""
    return _title(path, node)


def hidden(node: Mapping) -> bool:
    """Einstellung ohne Funktion (noch): im Schema `x-ab` = Meilenstein, der sie bringt (Jens 03.10.2026: ausblenden,
    was noch nicht wirkt). Der gespeicherte Wert bleibt."""
    return bool(node.get("x-ab"))


def _visible(schema: Mapping, node: Mapping) -> bool:
    node = _resolve(schema, node)
    if hidden(node):
        return False
    if node.get("type") == "object" and node.get("properties"):
        return any(_visible(schema, c) for c in node["properties"].values())
    return True


def areas(schema: Mapping) -> list[dict]:
    """Bereiche (Persönlich, Import, Export, System) mit ihren Abschnitten – nur, was sichtbar ist."""
    out = []
    for key, node in schema.get("properties", {}).items():
        if key in SKIP:
            continue
        node = _resolve(schema, node)
        sections, loose = [], False
        for sub, child in (node.get("properties") or {}).items():
            child = _resolve(schema, child)
            path = f"{key}.{sub}"
            if not _visible(schema, child):
                continue
            if child.get("type") == "object" and child.get("properties") or child.get("x-abschnitt"):
                sections.append({"id": path, "title": _title(path, child)})
            else:
                loose = True
        if loose:
            sections.insert(0, {"id": key, "title": "Allgemein"})
        if key == "system":                                   # ohne eigene Felder: Einstellungen übertragen
            sections.append({"id": "system.einstellungen_uebertragen", "title": "Einstellungen übertragen"})
            sections.append({"id": "system.ueber", "title": "Über Ottilie"})        # Software und Lizenzen
        out.append({"id": key, "title": _title(key, node), "sections": sections})
    return out


def _kind(node: Mapping) -> str:
    if "enum" in node:
        return "enum"
    if node.get("x-ordner"):
        return "folder"                       # über „Wählen …“ setzen, nicht tippen (Jens 03.10.2026)
    if node.get("x-nutzerwahl"):
        return "user"                         # Auswahl aus den angelegten Nutzern (Jens 04.10.2026)
    t = node.get("type")
    if t == "boolean":
        return "bool"
    if t in ("integer", "number"):
        return "number"
    if t == "string":
        return "text"
    if t == "array":
        items = node.get("items") or {}
        if items.get("type") == "string" and "enum" not in items:
            return "list"
        if items.get("type") == "object" and items.get("properties"):
            return "table"
    return "json"


def _columns(schema: Mapping, node: Mapping) -> list[dict]:
    items = _resolve(schema, node.get("items") or {})
    cols = []
    for key, sub in (items.get("properties") or {}).items():
        sub = _resolve(schema, sub)
        cols.append({"key": key, "label": sub.get("title") or _label(key), "kind": _kind(sub),
                     "options": list(sub.get("enum", [])), "required": key in (items.get("required") or [])})
    return cols


TRACK_DATA_DIR = "~/Documents/Pro Tools/Pro Tools Presets/Track Data to Import"
# Standard-Preset der App: Kopie des Studio-Presets „Quick Preset 1“, mit dem der produktive PunchBuddy-Import
# (Interplay-Weg, Ctrl+1 im Dialog „Track Data to Import“) arbeitet. Live 03.10.2026: Spurfarben bleiben erhalten.
DEFAULT_TRACK_DATA_PRESET = "Ottilie Clips und Automation"   # bis 08.10.2026 „Tonmischung …“ (bleibt in Pro Tools)


def install_default_preset(folder: str = TRACK_DATA_DIR) -> str:
    """Standard-Preset in den Pro-Tools-Ordner legen, falls es dort fehlt (nie überschreiben). Rückgabe: Pfad."""
    target = os.path.join(os.path.expanduser(folder), f"{DEFAULT_TRACK_DATA_PRESET}.ptpreset")
    if not os.path.exists(target):
        source = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources",
                              f"{DEFAULT_TRACK_DATA_PRESET}.ptpreset")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        from tonmischung.core.fileio import write_new
        try:
            write_new(target, lambda tmp: shutil.copyfile(source, tmp))
        except FileExistsError:
            pass                                                  # inzwischen entstanden: bleibt, wie es ist
    return target


def track_data_presets(folder: str = TRACK_DATA_DIR) -> list[str]:
    """Namen der Pro-Tools-Presets „Track Data to Import“ auf diesem Mac."""
    root = os.path.expanduser(folder)
    try:
        return sorted(n[:-9] for n in os.listdir(root) if n.endswith(".ptpreset") and not n.startswith("."))
    except OSError:
        return []


def track_data_preset_path(name: str, folder: str = TRACK_DATA_DIR) -> str:
    """Pfad zum Preset oder leer, wenn keins gewählt bzw. nicht vorhanden."""
    if not name:
        return ""
    path = os.path.join(os.path.expanduser(folder), f"{name}.ptpreset")
    if not os.path.exists(path) and name == DEFAULT_TRACK_DATA_PRESET:
        try:
            install_default_preset(folder)
        except OSError:
            return ""
    return path if os.path.exists(path) else ""


def field_view(settings: Settings, schema: Mapping, path: str, errors: list[str] | None = None) -> dict:
    info = settings.fields[path]
    node = _resolve(schema, info.node)
    value, layer = settings._resolve(path)
    kind = _kind(node)
    hints = [h for h in (node.get("description"), node.get("x-hinweis")) if h]
    mine = settings.user_active and settings.is_user_path(path)
    if mine:
        hints.append(f"gilt für den Nutzer „{settings.user_label}“")             # Plan Nutzer & Räume, Schritt 2
    elif layer != LAYER_MDM and info.source in SOURCE_HINT and not settings.is_user_path(path):
        hints.append(SOURCE_HINT[info.source])
    elif layer != LAYER_MDM and settings.is_user_path(path):
        hints.append("hängt am Nutzer – ohne Anmeldung gilt der Wert dieses Macs")
    view = {"path": path, "label": _title(path, node), "kind": kind, "value": value, "default": info.default,
            "layer": layer, "layer_text": (f"aus dem Spurprofil „{settings.profile_label}“ – dort ändern"
                                           if layer == "spurprofil" else
                                           f"Nutzer „{settings.user_label}“" if layer == "nutzer" else
                                           LAYER_TEXT.get(layer, layer)),
            "locked": layer in (LAYER_MDM, "spurprofil"),
            "resettable": layer in ("lokal", "nutzer"), "hints": hints, "errors": errors or [],
            "options": list(node.get("enum", [])), "min": node.get("minimum"), "max": node.get("maximum"),
            "step": node.get("multipleOf"), "integer": node.get("type") == "integer"}
    if node.get("x-labels") and len(node["x-labels"]) == len(view["options"]):
        view["option_labels"] = list(node["x-labels"])           # lesbare Beschriftung je Auswahl
    if kind == "table":
        view["columns"] = _columns(schema, node)
    if path == "persoenlich.standard_template_id":           # Auswahl aus „Meine Templates“
        view["kind"] = "enum"
        view["options"] = [""] + [t.get("id", "") for t in (settings.get("persoenlich.templates") or []) if t.get("id")]
        view["option_labels"] = ["– keiner –"] + [
            t.get("anzeigename") or t.get("pt_name", "") for t in (settings.get("persoenlich.templates") or [])
            if t.get("id")]
    if path == "import.import_vorschau.trackdaten_preset":    # Auswahl aus den Pro-Tools-Presets
        names = track_data_presets()
        if DEFAULT_TRACK_DATA_PRESET not in names:
            names.insert(0, DEFAULT_TRACK_DATA_PRESET)           # bringt die App mit, wird bei Bedarf angelegt
        if value and value not in names:
            names.append(str(value))                             # gewählt, aber (noch) nicht auf diesem Mac
        view["kind"] = "enum"
        view["options"] = [""] + names
        view["option_labels"] = ["– kein Preset (Spureigenschaften können sich ändern) –"] + names
    if "wird von der App geschrieben" in view["label"]:
        view["locked"], view["layer_text"] = True, "wird von der App geschrieben"
    return view


def section(settings: Settings, schema: Mapping, section_id: str) -> dict:
    """Felder eines Abschnitts; tiefere Objekte erscheinen als Gruppen mit Überschrift."""
    top = section_id.split(".")[0]
    loose = "." not in section_id
    groups: dict[str, list] = {}
    for path in settings.fields:
        if path in SKIP or not path.startswith(section_id + ".") and path != section_id:
            continue
        if hidden(settings.fields[path].node):
            continue                                  # wirkt noch nicht (x-ab)
        if settings.fields[path].node.get("x-eigene-ansicht"):
            continue                                  # eigene Ansicht statt Formularfeld (z. B. Meine Templates)
        rest = path[len(section_id) + 1:].split(".")
        if loose and (len(rest) > 1 or settings.fields[path].node.get("x-abschnitt")):
            continue                                  # gehört zu einem eigenen Abschnitt
        group = ".".join(rest[:-1])
        groups.setdefault(group, []).append(field_view(settings, schema, path))
    area = next(a for a in areas(schema) if a["id"] == top)
    title = next((s["title"] for s in area["sections"] if s["id"] == section_id), section_id)
    out = [{"title": _group_title(schema, section_id, g) if g else "", "fields": f} for g, f in groups.items()]
    for path, info in settings.fields.items():               # Felder, die zusätzlich hier erscheinen (x-auch-in)
        if section_id in (info.node.get("x-auch-in") or []):
            view = field_view(settings, schema, path)
            out.append({"title": info.node.get("x-gruppe") or view["label"], "fields": [view]})
    return {"id": section_id, "title": title, "area": area["title"], "groups": out}


def _group_title(schema: Mapping, section_id: str, group: str) -> str:
    node: Any = schema
    for key in (section_id + "." + group).split("."):
        node = _resolve(schema, (node.get("properties") or {}).get(key, {}))
    return _title(section_id + "." + group, node)


def set_value(settings: Settings, schema: Mapping, path: str, value: Any) -> dict:
    """Wert prüfen und lokal speichern; Fehler stehen im Feld (nicht als Ausnahme)."""
    if path not in settings.fields:
        raise KeyError(f"Unbekannte Einstellung: {path}")
    node = _resolve(schema, settings.fields[path].node)
    errors = validate(node, value, _title(path, node))
    if not errors:
        try:
            settings.set_local(path, value)
        except SettingLockedError as e:
            errors = [str(e)]
    view = field_view(settings, schema, path, errors)
    if errors:
        view["value"] = value                          # Eingabe stehen lassen, damit sie korrigiert werden kann
    return view


def reset_value(settings: Settings, schema: Mapping, path: str) -> dict:
    try:
        settings.reset_local(path)
        return field_view(settings, schema, path)
    except SettingLockedError as e:
        return field_view(settings, schema, path, [str(e)])
