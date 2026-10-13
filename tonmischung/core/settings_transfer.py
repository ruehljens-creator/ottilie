"""Alle Einstellungen exportieren und an anderer Stelle importieren – mit Auswahl (reine Logik).

Jens (03.10.2026): „Die gesamten Einstellungen der App exportieren und an anderer Stelle wieder importieren; beim
Importieren abfragen, welche Daten importiert werden sollen – alles oder nur bestimmte Template-Einstellungen usw.“

Exportiert wird, was auf diesem Mac eingestellt ist (lokale Ebene); Hausstandard, Geräteverwaltung und Spurprofil der
offenen Session gehören nicht in die Datei. Zugangsdaten stehen nie in den Einstellungen (nur im Schlüsselbund) und
damit auch nicht in der Datei. Beim Import wird je Abschnitt gewählt, Spurprofile einzeln; Werte, die nur für einen
Mac gelten (Ordner, Aufnahmespuren, Stream Deck, Raum), sind eine eigene Gruppe und nicht vorgewählt.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tonmischung.core import track_profile as TP

FORMAT = "tonmischung-einstellungen"
FORMAT_VERSION = 1
PROFILES = "import.spurprofile"
# Gilt nur für diesen Mac, obwohl im Nutzerbereich (Raum); auch `config.settings` hält sie am Mac
MACHINE_EXTRA = {"system.raeume_nutzer.raum_fest", "system.raeume_nutzer.raum_erkennung"}
MACHINE_GROUP = "dieser_mac"


class TransferFormatError(ValueError):
    """Keine Einstellungsdatei von Ottilie oder aus einer neueren Version."""


def export_document(local_values: Mapping[str, Any], when: str = "", source: str = "") -> dict[str, Any]:
    return {"format": FORMAT, "version": FORMAT_VERSION, "exportiert_am": when, "von": source,
            "werte": {k: v for k, v in sorted(local_values.items()) if k != "schema_version"}}


def read_document(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, Mapping) or doc.get("format") != FORMAT:
        raise TransferFormatError("Keine Einstellungsdatei von Ottilie")
    if int(doc.get("version") or 0) > FORMAT_VERSION:
        raise TransferFormatError("Die Datei stammt aus einer neueren Version von Ottilie")
    values = doc.get("werte")
    if not isinstance(values, Mapping) or not values:
        raise TransferFormatError("Die Datei enthält keine Einstellungen")
    return dict(values)


@dataclass
class Choice:
    id: str                         # Abschnitt-ID, „spurprofil:<key>“ oder „dieser_mac“
    title: str
    area: str
    paths: list[str] = field(default_factory=list)
    changed: int = 0                # Werte, die hier anders sind
    locked: list[str] = field(default_factory=list)     # Geräteverwaltung: wird nicht übernommen
    default: bool = True
    status: str = ""                # bei Spurprofilen: neu | ersetzt | gleich
    profile: TP.TrackProfile | None = None

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "area": self.area,
                "count": len(self.paths) or (1 if self.profile else 0),
                "changed": self.changed, "locked": len(self.locked), "default": self.default, "status": self.status}


def section_of(path: str, sections: Sequence[str]) -> str:
    """Längster Abschnitt, zu dem der Pfad gehört (Allgemein = Bereich selbst)."""
    best = ""
    for s in sections:
        if (path == s or path.startswith(s + ".")) and len(s) > len(best):
            best = s
    return best or path.split(".")[0]


def plan(values: Mapping[str, Any], current: Any, areas: Sequence[Mapping]) -> tuple[list[Choice], list[str]]:
    """Auswahl für den Import. `current` = Settings (get, fields, locked); `areas` = settings_view.areas().
    Rückgabe: (Auswahl, unbekannte Pfade – z. B. aus einer anderen Version)."""
    titles = {s["id"]: (s["title"], a["title"]) for a in areas for s in a["sections"]}
    sections = list(titles)
    choices: dict[str, Choice] = {}
    unknown = []
    for path, value in values.items():
        if path not in current.fields:
            unknown.append(path)
            continue
        if path == PROFILES:
            continue
        machine = current.fields[path].source == "lokal" or path in MACHINE_EXTRA
        sid = MACHINE_GROUP if machine else section_of(path, sections)
        if sid not in choices:
            title, area = (("Nur für einen Mac: Ordner, Aufnahmespuren, Stream Deck, Raum …", "Dieser Mac")
                           if machine else titles.get(sid, (sid, "")))
            choices[sid] = Choice(sid, title, area, default=not machine)
        c = choices[sid]
        c.paths.append(path)
        if current.locked(path):
            c.locked.append(path)
        elif current.get(path) != value:
            c.changed += 1
    ordered = ([choices[s] for s in sections if s in choices]
               + ([choices[MACHINE_GROUP]] if MACHINE_GROUP in choices else []))
    incoming = TP.profiles_from_settings(values.get(PROFILES)) if PROFILES in values else []
    existing = TP.profiles_from_settings(current.get(PROFILES))
    for p, status in TP.import_plan(incoming, existing):
        ordered.append(Choice(f"spurprofil:{TP.profile_key(p)}", f"Spurprofil {p.template_group} / {p.template_name}",
                              "Spurprofile je Template", default=status != TP.SAME, status=status, profile=p,
                              changed=0 if status == TP.SAME else 1))
    return ordered, unknown


@dataclass
class Result:
    written: int = 0
    profiles: int = 0
    skipped_locked: list[str] = field(default_factory=list)


def apply(values: Mapping[str, Any], choices: Iterable[Choice], chosen: Iterable[str], current: Any) -> Result:
    """Gewählte Abschnitte und Spurprofile übernehmen (lokale Ebene). Von der Geräteverwaltung gesperrte Werte
    bleiben."""
    want = set(chosen)
    r = Result()
    picked_profiles = []
    for c in choices:
        if c.id not in want:
            continue
        if c.profile is not None:
            picked_profiles.append(c.profile)
            continue
        for path in c.paths:
            if path in c.locked:
                r.skipped_locked.append(path)
                continue
            current.set_local(path, values[path], under_profile=True)
            r.written += 1
    if picked_profiles:
        merged = TP.apply_import(TP.profiles_from_settings(current.get(PROFILES)), picked_profiles)
        current.set_local(PROFILES, [TP.profile_to_dict(p) for p in merged])
        r.profiles = len(picked_profiles)
    return r
