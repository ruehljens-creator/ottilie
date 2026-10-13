"""Einstellungen in Schichten: Geräteverwaltung (MDM, verwaltet) > zentral (Helmut) > lokal > Hausstandard.

Felder, Hausstandard und vorgesehene Quelle stehen in `einstellungen.schema.json`.
Ein Feld wird über seinen Punktpfad angesprochen, z. B.
``system.schnittstellen.ptsl.zeitlimit_s``. Dieselben Punktpfade sind die Schlüssel
im Konfigurationsprofil der Geräteverwaltung, in der lokalen Datei und in den zentralen Daten.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from tonmischung.core.fileio import CorruptFileError, read_json, set_aside, write_json
from tonmischung.core.settings_transfer import MACHINE_EXTRA
from tonmischung.paths import BUNDLE_ID, support_dir

log = logging.getLogger(__name__)
SCHEMA_PATH = Path(__file__).with_name("einstellungen.schema.json")

# Domäne der verwalteten Einstellungen = Bundle-ID der App (ANNAHME bis zur Abstimmung mit der IT)
PREFERENCE_DOMAIN = BUNDLE_ID
_DEFAULT = object()


def default_local_path() -> Path:
    """Einstellungsdatei des Macs – erst beim Gebrauch bestimmt, nicht beim Laden des Moduls (sonst landen Tests mit
    eigenem Home-Verzeichnis in der echten Datei; so kamen „Ton 3 ↔ ton3-mac“ und eine Probe-Helmut-Adresse hinein)."""
    return support_dir() / "einstellungen.json"


LAYER_MDM = "mdm"
LAYER_PROFILE = "spurprofil"      # Spurprofil des Templates der offenen Session (Plan Spurprofile, Schritt 4)
LAYER_USER = "nutzer"             # Einstellungen des angemeldeten Nutzers (Plan Nutzer & Räume, Schritt 2)
LAYER_CENTRAL = "zentral"
LAYER_LOCAL = "lokal"
LAYER_DEFAULT = "standard"

# Liest einen verwalteten Wert: Schlüssel -> (erzwungen, Wert)
ManagedReader = Callable[[str], "tuple[bool, Any]"]


class SettingLockedError(Exception):
    """Der Wert ist über die Geräteverwaltung festgelegt und lässt sich am Mac nicht ändern."""


@dataclass(frozen=True)
class FieldInfo:
    path: str
    source: str            # vorgesehene Quelle laut Schema (x-quelle)
    default: Any
    has_default: bool
    node: Mapping = field(default_factory=dict, compare=False, repr=False)   # Schemaknoten (für Formulare)


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def schema_fields(schema: Mapping) -> dict[str, FieldInfo]:
    """Alle Felder (Blätter) des Schemas mit Punktpfad, Quelle und Hausstandard."""
    defs = schema.get("$defs", {})
    fields: dict[str, FieldInfo] = {}

    def resolve(node: Mapping) -> Mapping:
        if "$ref" in node:
            base = dict(defs[node["$ref"].rsplit("/", 1)[-1]])
            base.update({k: v for k, v in node.items() if k != "$ref"})
            return base
        return node

    def walk(node: Mapping, path: str, source: str | None, inherited: Any = None) -> None:
        node = resolve(node)
        source = node.get("x-quelle", source)
        props = node.get("properties")
        if node.get("type") == "object" and props:
            # Standard am Objekt ({"von": "A1", "bis": "A4"}) gilt für die Felder darunter
            own = node.get("default", inherited)
            for key, child in props.items():
                sub = own.get(key) if isinstance(own, Mapping) else None
                walk(child, f"{path}.{key}" if path else key, source, sub)
            return
        has_default = "default" in node or "const" in node or inherited is not None
        default = node.get("default", node.get("const", inherited))
        fields[path] = FieldInfo(path, source or LAYER_LOCAL, default, has_default, node)

    walk(schema, "", None)
    return fields


def _to_python(value: Any) -> Any:
    """Werte aus CFPreferences (PyObjC) in einfache Python-Werte umwandeln."""
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if hasattr(value, "keys"):
        return {str(k): _to_python(value[k]) for k in value.keys()}
    if hasattr(value, "__iter__"):
        return [_to_python(v) for v in value]
    return value


def read_managed(domain: str = PREFERENCE_DOMAIN) -> ManagedReader:
    """Leser für verwaltete Voreinstellungen (Konfigurationsprofil der Geräteverwaltung).

    Nur erzwungene Werte zählen; was ein Nutzer selbst per `defaults` setzt, nicht.
    """
    from Foundation import CFPreferencesAppValueIsForced, CFPreferencesCopyAppValue

    def reader(key: str) -> tuple[bool, Any]:
        if not CFPreferencesAppValueIsForced(key, domain):
            return False, None
        return True, _to_python(CFPreferencesCopyAppValue(key, domain))

    return reader


# Was am Mac bleibt, auch wenn ein Nutzer angemeldet ist (Jens 04.10.2026: „Gerätebezogenes bleibt am Mac“): Quelle
# „lokal“ oder „mdm“, dazu diese Bereiche und der Raum (`MACHINE_EXTRA`). Alles andere hängt am Nutzer.
MAC_SECTIONS = ("import.session_ablage", "export.dateiexporte", "export.verarbeitung_helmut", "system")


class Settings:
    """Liest Einstellungen in der Rangfolge Geräteverwaltung > Spurprofil > Nutzer > zentral > lokal > Hausstandard.

    Ist ein Nutzer angemeldet, gelten für Nutzer-Einstellungen nur seine Werte (sonst zentral/Hausstandard) – die
    Werte des Macs gelten dann nicht; ohne Anmeldung gilt der Mac wie bisher."""

    def __init__(self, schema: Mapping | None = None, local_path: Path | None | object = _DEFAULT,
                 managed: ManagedReader | None = None,
                 central: Mapping[str, Any] | None = None) -> None:
        self.fields = schema_fields(schema if schema is not None else load_schema())
        if local_path is _DEFAULT:
            local_path = default_local_path()
        self.local_path = Path(local_path) if local_path else None  # type: ignore[arg-type]
        self.local_problem = ""                  # Hinweis fürs Fenster, wenn die Datei beschädigt/unlesbar war
        self._local_blocked = False              # unlesbar (nicht beschädigt): nie überschreiben
        self._lock = threading.RLock()           # Fenster, Aufträge und Takte schreiben aus mehreren Threads
        self._managed = managed
        self._central = dict(central or {})
        self._local = self._read_local()
        self._profile: dict[str, Any] = {}
        self.profile_label = ""
        self._user: dict[str, Any] = {}
        self._user_writer: Callable[[dict[str, Any]], None] | None = None
        self.user_label = ""

    # ── Lesen ────────────────────────────────────────────────────────────────
    def get(self, path: str) -> Any:
        return self._resolve(path)[0]

    def source(self, path: str) -> str:
        """Woher der Wert kommt: mdm, spurprofil, nutzer, zentral, lokal oder standard."""
        return self._resolve(path)[1]

    def locked(self, path: str) -> bool:
        return self.source(path) == LAYER_MDM

    def snapshot(self) -> dict[str, tuple[Any, str]]:
        return {p: self._resolve(p) for p in self.fields}

    def _resolve(self, path: str) -> tuple[Any, str]:
        if path not in self.fields:
            raise KeyError(f"Unbekannte Einstellung: {path}")
        if self._managed is not None:
            forced, value = self._managed(path)
            if forced:
                return value, LAYER_MDM
        if path in self._profile:
            return self._profile[path], LAYER_PROFILE
        by_user = self.user_active and self.is_user_path(path)
        if by_user and path in self._user:
            return self._user[path], LAYER_USER
        if path in self._central:
            return self._central[path], LAYER_CENTRAL
        if path in self._local and not by_user:
            return self._local[path], LAYER_LOCAL
        return self.fields[path].default, LAYER_DEFAULT

    # ── Nutzer ───────────────────────────────────────────────────────────────
    def is_user_path(self, path: str) -> bool:
        """Hängt am Nutzer (nicht am Mac)?"""
        info = self.fields.get(path)
        if info is None or info.source in ("lokal", "mdm") or path in MACHINE_EXTRA:
            return False
        return not any(path == s or path.startswith(s + ".") for s in MAC_SECTIONS)

    @property
    def user_active(self) -> bool:
        return self._user_writer is not None

    def set_user_layer(self, values: Mapping[str, Any], label: str,
                       writer: Callable[[dict[str, Any]], None]) -> None:
        """Nutzer angemeldet: seine Werte gelten, Änderungen an Nutzer-Einstellungen gehen an `writer`."""
        self._user = {k: v for k, v in values.items() if k in self.fields and self.is_user_path(k)}
        self._user_writer, self.user_label = writer, label

    def clear_user_layer(self) -> None:
        self._user, self._user_writer, self.user_label = {}, None, ""

    def user_values(self) -> dict[str, Any]:
        return dict(self._user)

    # ── Zentrale Daten (Helmut, später optional Datenbank) ───────────────────
    def update_central(self, values: Mapping[str, Any]) -> None:
        unknown = [k for k in values if k not in self.fields]
        if unknown:
            log.warning("Zentrale Daten mit unbekannten Einstellungen ignoriert: %s", unknown)
        self._central = {k: v for k, v in values.items() if k in self.fields}

    # ── Spurprofil der offenen Session ───────────────────────────────────────
    def set_profile_layer(self, values: Mapping[str, Any], label: str) -> None:
        """Werte aus dem Spurprofil des Templates der offenen Session (Spurtabelle, Aufnahmespuren)."""
        self._profile = {k: v for k, v in values.items() if k in self.fields}
        self.profile_label = label if self._profile else ""

    def clear_profile_layer(self) -> None:
        self._profile, self.profile_label = {}, ""

    # ── Lokal schreiben ──────────────────────────────────────────────────────
    def local_values(self) -> dict[str, Any]:
        """Was auf diesem Mac eingestellt ist (lokale Ebene) – für den Export der Einstellungen."""
        return dict(self._local)

    def set_local(self, path: str, value: Any, under_profile: bool = False) -> None:
        """`under_profile`: auch schreiben, wenn das Spurprofil der offenen Session den Wert gerade vorgibt
        (Import der Einstellungen); in den Formularen bleibt das gesperrt."""
        if path not in self.fields:
            raise KeyError(f"Unbekannte Einstellung: {path}")
        if self.locked(path):
            raise SettingLockedError(f"{path} ist über die Geräteverwaltung festgelegt und gesperrt")
        if path in self._profile and not under_profile:
            raise SettingLockedError(f"Gilt gerade aus dem Spurprofil „{self.profile_label}“ der offenen Session – "
                                     "dort ändern (Import › Spurprofile)")
        with self._lock:
            if self.user_active and self.is_user_path(path):
                self._user[path] = value
                self._user_writer(dict(self._user))        # type: ignore[misc]
                return
            self._local[path] = value
            self._write_local()

    def reset_local(self, path: str) -> None:
        """Lokalen Wert entfernen – danach gilt wieder zentral bzw. Hausstandard."""
        if path not in self.fields:
            raise KeyError(f"Unbekannte Einstellung: {path}")
        if self.locked(path):
            raise SettingLockedError(f"{path} ist über die Geräteverwaltung festgelegt und gesperrt")
        with self._lock:                         # auch ein gespeichertes null entfernen (vorher blieb es in der Datei)
            if self.user_active and self.is_user_path(path):
                if path in self._user:
                    del self._user[path]
                    self._user_writer(dict(self._user))    # type: ignore[misc]
                return
            if path in self._local:
                del self._local[path]
                self._write_local()

    def _read_local(self) -> dict[str, Any]:
        """Beschädigte Datei wird beiseitegelegt (nicht überschrieben, Review A4); eine unlesbare (Rechte) sperrt
        das Speichern, damit die Werte darin nicht verloren gehen."""
        if not self.local_path:
            return {}
        try:
            data = read_json(self.local_path, expect=dict)
        except CorruptFileError as e:
            moved = set_aside(self.local_path)
            self.local_problem = (f"Einstellungsdatei war beschädigt ({e.reason}) – beiseitegelegt als „{moved.name}“; "
                                  "es gelten die Vorgaben")
            log.error("Lokale Einstellungen: %s", self.local_problem)
            return {}
        except OSError as e:
            self._local_blocked = True
            self.local_problem = f"Einstellungsdatei nicht lesbar ({e}) – Änderungen werden nicht gespeichert"
            log.error("Lokale Einstellungen: %s", self.local_problem)
            return {}
        return {k: v for k, v in (data or {}).items() if k in self.fields}

    def _write_local(self) -> None:
        if not self.local_path:
            return
        if self._local_blocked:
            raise SettingLockedError(self.local_problem)
        with self._lock:
            write_json(self.local_path, dict(self._local), indent=2, sort_keys=True)


def validate(node: Mapping, value: Any, label: str = "Wert") -> list[str]:
    """Wert gegen einen Schemaknoten prüfen (Typ, Auswahl, Grenzen, Tabellenzeilen). Leer = in Ordnung."""
    errors: list[str] = []
    kind = node.get("type")
    if "enum" in node:
        if value not in node["enum"]:
            errors.append(f"{label}: nur {', '.join(map(str, node['enum']))}")
        return errors
    if kind == "boolean" and not isinstance(value, bool):
        errors.append(f"{label}: ja oder nein")
    elif kind in ("integer", "number"):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or (kind == "integer" and int(value) != value)):
            errors.append(f"{label}: {'ganze Zahl' if kind == 'integer' else 'Zahl'} erwartet")
        else:
            if "minimum" in node and value < node["minimum"]:
                errors.append(f"{label}: mindestens {node['minimum']}")
            if "maximum" in node and value > node["maximum"]:
                errors.append(f"{label}: höchstens {node['maximum']}")
            if "multipleOf" in node and value % node["multipleOf"]:
                errors.append(f"{label}: in Schritten von {node['multipleOf']}")
    elif kind == "string":
        if not isinstance(value, str):
            errors.append(f"{label}: Text erwartet")
        elif "pattern" in node and value and not __import__("re").fullmatch(node["pattern"], value):
            errors.append(f"{label}: Format stimmt nicht")
    elif kind == "array":
        if not isinstance(value, list):
            return [f"{label}: Liste erwartet"]
        items = node.get("items") or {}
        for i, item in enumerate(value, 1):
            if items.get("type") == "object":
                if not isinstance(item, Mapping):
                    errors.append(f"{label}, Zeile {i}: Eintrag erwartet")
                    continue
                for key, sub in (items.get("properties") or {}).items():
                    if key in item:
                        errors += validate(sub, item[key], f"{label}, Zeile {i}, {key}")
                for key in items.get("required") or []:
                    if item.get(key) is None:          # leer ist erlaubt (z. B. Kanäle 15/16 ohne Spur)
                        errors.append(f"{label}, Zeile {i}: {key} fehlt")
            elif items:
                errors += validate(items, item, f"{label}, Eintrag {i}")
    return errors
