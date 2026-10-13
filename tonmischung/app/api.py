"""Öffentliche Schnittstelle (API) von Ottilie.

Eine Befehlsschicht für alle Zugänge: das Fenster, den Unix-Socket (Stream Deck, Skripte) und den MCP-Server. Der
Katalog unten ist die Liste der öffentlichen Befehle; `docs/API.md` wird daraus erzeugt. Rechte prüft
`ApiDispatcher` nach Herkunft in drei Stufen (lesen, ändern, verwalten) und ob Fragen beantwortet werden dürfen –
für MCP gelten die Einstellungen `system.schnittstellen.mcp_*`.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from tonmischung.app.bridge import CommandError
from tonmischung.core.mixstate import RELEASE_REASONS

API_VERSION = 1
READ, CHANGE, ADMIN = "lesen", "ändern", "verwalten"   # drei Stufen (Jens 08.10.2026)
ORIGINS = ("fenster", "socket", "mcp")

log = logging.getLogger("tonmischung.api")


@dataclass(frozen=True)
class ApiCommand:
    name: str
    kind: str                       # lesen | ändern | verwalten
    summary: str
    handler: str                    # Name in der Befehlsschicht (Bridge)
    args: Mapping[str, str] = field(default_factory=dict)   # Argument → Beschreibung
    result: str = ""
    types: Mapping[str, str] = field(default_factory=dict)  # Argument → str | bool | int | float | list | dict | any
    required: tuple[str, ...] = ()                          # Pflicht über Socket/MCP (dort nie ein Dateidialog)
    timeout_s: float | None = None                          # länger als üblich (z. B. warten, Templates kopieren)


CATALOG: tuple[ApiCommand, ...] = (
    ApiCommand("status", READ, "Pro Tools (offene Session), Helmut, Mischer und Raum", "status",
               result="version, pro_tools {ok, session}, helmut {ok, mock}, mixer, room"),
    ApiCommand("beitraege", READ, "Beiträge „Zur Mischung“ (eigene mit Plakette „meine Mischung · …“)", "list",
               {"query": "Suchtext (Titel, Sendung)", "order": "zeit (Standard) oder sendung"},
               "rows [{id, title, show, state, pill …}], counts {list}"),
    ApiCommand("beitrag", READ, "Details eines Beitrags mit den erlaubten Aktionen", "detail",
               {"id": "Projekt-ID"}, "title, show, banners, kv, actions {fetch, deliver, release}"),
    ApiCommand("templates", READ, "Templates: „Meine Templates“ und die Pro-Tools-Templates dieses Macs",
               "templates", result="mine, found [{group, name}], default, triage, preview"),
    ApiCommand("triage", READ, "AAF beurteilen: Kategorie je Spur und Clip (ohne Pro Tools)", "triage",
               {"aaf": "Pfad zur AAF", "id": "oder Projekt-ID (AAF aus der Übergabe)"},
               "Bericht: spuren, paare, clips mit kategorie und grund"),
    ApiCommand("auftrag", READ, "Stand des laufenden oder letzten Auftrags", "auftrag",
               result="title, state (laeuft|frage|fertig|fehler|abgebrochen), steps, fraction, question, result, "
                      "error"),
    ApiCommand("pro_tools", READ, "Pro Tools abfragen (nur lesen): Transport, Spuren, Session, Auswahl, Marker",
               "pro_tools", {"was": "transport | spuren | session | auswahl | marker"},
               "je nach Abfrage, z. B. transport oder spuren [{name, typ, clips, format}]"),
    ApiCommand("zurueckliefern_info", READ, "Übersicht vor dem Zurückliefern", "zurueckliefern_info",
               {"id": "Projekt-ID"}, "session, tracks, missing, target, lufs, truepeak"),
    ApiCommand("holen", CHANGE, "Beitrag übernehmen und in Pro Tools importieren (mit AAF-Triage). "
               "Fragen (offene Session, Vorschau) erscheinen im Fenster beim Mischer.", "holen",
               {"id": "Projekt-ID", "group": "Templategruppe", "name": "Template", "triage": "true/false",
                "video": "Referenzvideo importieren, true/false"}, "Auftrag (siehe auftrag)"),
    ApiCommand("zurueckliefern", CHANGE, "Mischung zurückliefern: konsolidieren, R128, WAVs an Helmut",
               "zurueckliefern",
               {"id": "Projekt-ID", "tracks": "Exportspuren, kommagetrennt (sonst Spurtabelle)"}, "Auftrag"),
    ApiCommand("freigeben", CHANGE, "Übernahme aufheben (Zurückgeben)", "release",
               {"id": "Projekt-ID", "reason": " | ".join(RELEASE_REASONS)},
               "Beitrag wie bei beitrag"),
    ApiCommand("antworten", CHANGE, "Offene Frage eines Auftrags beantworten", "antworten",
               {"id": "Frage-ID aus auftrag.question.id",
                "value": "Antwort: einer der Werte aus auftrag.question.options (meist true/false)"}, "ok",
               types={"id": "int", "value": "any"}, required=("id",)),
    # PunchBuddy-Funktionen (Namen wie in PunchBuddy, damit Stream Deck und Skripte gleich bleiben)
    ApiCommand("record_a", CHANGE, "Record A starten bzw. stoppen (Aufnahmespuren aus den Einstellungen)",
               "punch_record_a", result="meldung (z. B. record_a queued)"),
    ApiCommand("record_b", CHANGE, "Record B starten bzw. stoppen", "punch_record_b", result="meldung"),
    ApiCommand("play", CHANGE, "Play Input: Wiedergabe mit Eingangsmonitor starten bzw. stoppen", "punch_play",
               result="meldung"),
    ApiCommand("play_custom", CHANGE, "Play Custom: Wiedergabe mit den Stumm-Regeln für Kanal 1/2", "punch_play_custom",
               result="meldung"),
    ApiCommand("goto_start", CHANGE, "Cursor auf den Programmbeginn", "punch_goto_start", result="meldung"),
    ApiCommand("move_audio", CHANGE, "Audio von den Quell- auf die Zielspuren verschieben", "punch_move_audio",
               result="meldung"),
    ApiCommand("preset", CHANGE, "Spur-Preset laden", "punch_preset", {"nr": "Preset-Nummer, 1 = erstes"},
               "meldung (preset n loaded)"),
    ApiCommand("export_wav", CHANGE, "WAV-Export der Exportspuren (konsolidieren, Lautheit, WAVs in den Export-Ordner)",
               "punch_export_wav", result="meldung; Fortschritt über auftrag"),
    ApiCommand("export_aaf", CHANGE, "AAF-Export mit eingebetteten Medien (über Pro-Tools-Dialoge)", "punch_export_aaf",
               result="meldung; Fortschritt über auftrag"),
    ApiCommand("export_aaf_reference", CHANGE, "AAF-Export mit Referenz auf die Medien (über Pro-Tools-Dialoge)",
               "punch_export_aaf_reference", result="meldung; Fortschritt über auftrag"),
)

# Vollständiger Zugriff (Jens 08.10.2026: „für Automation, einen vollständigen MCP-Zugriff, aber nicht über das
# Netzwerk“): alles, was das Fenster kann – außer den Befehlen in WINDOW_ONLY. Pfade statt Dateidialoge (`required`).
_G, _N = "Templategruppe", "Template-Name"
CATALOG += (
    # ── lesen ──────────────────────────────────────────────────────────────────────────────────────────────────
    ApiCommand("ping", READ, "Lebt die App? Version", "ping", result="pong, version"),
    ApiCommand("auftrag_warten", READ, "Für Automation: warten, bis sich der Auftrag ändert (Zustand, Schritt, Frage, "
               "Fortschritt in ganzen Prozent) – statt ständig nachzufragen", "auftrag_warten",
               {"sekunden": "höchstens so lange warten, 1–55 (Standard 25)"}, "geaendert (true/false), auftrag",
               types={"sekunden": "float"}, timeout_s=70),
    ApiCommand("raeume", READ, "Raumübersicht: je Raum Nutzer, Mischer, Zustand, Beitrag, Fortschritt", "raeume",
               result="cards [{name, here, st, label, user, title, pct, online}], busy, source"),
    ApiCommand("raumliste", READ, "Raumliste (Raum ↔ Mac) und die Macs zur Auswahl", "raumliste",
               result="rooms [{id, name, rechnername}], macs, this_mac, this_room, ablage"),
    ApiCommand("nutzer", READ, "Nutzer, angemeldeter Nutzer und Standard-Nutzer dieses Macs", "nutzer",
               result="users [{id, name}], current, standard, ablage, needs_login"),
    ApiCommand("vorlagen", READ, "„Meine Templates“ des angemeldeten Nutzers mit Standard und gefundenen Templates",
               "vorlagen", result="mine, found, standard"),
    ApiCommand("einstellungen", READ, "Bereiche und Abschnitte der Einstellungen", "einstellungen", result="areas"),
    ApiCommand("einstellungen_abschnitt", READ, "Felder eines Abschnitts: Wert, Herkunft (Nutzer, Mac, zentral, "
               "Geräteverwaltung, Hausstandard), Sperre, Auswahl", "einstellungen_abschnitt",
               {"id": "Abschnitt, z. B. export.spuren_lautheit"},
               "title, groups [{fields [{path, label, value, source, locked, options}]}]", required=("id",)),
    ApiCommand("einstellungen_import_pruefen", READ, "Einstellungsdatei lesen und zeigen, was übernommen würde (ändert "
               "nichts)", "einstellungen_import_pruefen", {"pfad": "Pfad der .json"}, "path, choices, unknown",
               required=("pfad",)),
    ApiCommand("spurprofile", READ, "Spurprofile je Template", "spurprofile", {"gruppe": _G, "name": _N},
               "templates, chosen, selected (Zeilen, Probleme, Schalter), session, columns"),
    ApiCommand("spurprofile_import_pruefen", READ, "Spurprofil-Datei lesen und zeigen, was übernommen würde",
               "spurprofile_import_pruefen", {"pfad": "Pfad der .json"}, "path, choices", required=("pfad",)),
    ApiCommand("spurraster", READ, "Spurraster für Punch-In (Aufnahme, Wiedergabe je Spur)", "spurraster",
               {"lesen": "true: Spuren der offenen Session aus Pro Tools lesen"}, "spuren, spalten, meldung",
               types={"lesen": "bool"}),
    ApiCommand("presets", READ, "Spur-Presets für Punch-In", "presets", result="presets [{nr, name}]"),
    ApiCommand("audio_verschieben", READ, "Audio verschieben: Spurpaare Quelle → Ziel und die Spuren der Session",
               "audio_verschieben", {"lesen": "true: Spuren der offenen Session aus Pro Tools lesen"},
               "paare [[quelle, ziel]], spuren, fehlen, verbunden, gesperrt, meldung", types={"lesen": "bool"}),
    ApiCommand("sicherungen", READ, "Sicherungen (Session File Backups) zur Session eines Beitrags", "sicherungen",
               {"id": "Projekt-ID"}, "folder, rows, reachable, can_open, availability, hint", required=("id",)),
    ApiCommand("archiv", READ, "Fremde Dateien der offenen Session und das Archiv", "archiv",
               result="session, folders, foreign, archive"),
    ApiCommand("havarie_info", READ, "Havarie-Paket prüfen: AAF, Video, Titel- und ID-Vorschlag", "havarie_info",
               {"path": "Paketordner (AAF darin oder eine Ebene tiefer)"},
               "folder, aaf, video, title, project_id, complete, hint",
               required=("path",)),
    ApiCommand("havarie_ausliefern_info", READ, "Vor der Havarie-Auslieferung: Paket, Session, Ziele (Schreibtisch, "
               "Havarie-Ordner, USB-Sticks), Presets", "havarie_ausliefern_info",
               {"path": "Paketordner (leer: Vorschlag zur offenen Session)", "auto": "true: Paket selbst suchen"},
               "path, title, schreibtisch, havarie_ordner, usb, presets", types={"auto": "bool"}),
    ApiCommand("offene_session_info", READ, "Vor „In offene Session holen“: offene Session, Spuren, Vorgaben",
               "offene_session_info", {"id": "Projekt-ID"}, "offer, name, path, defaults, choices, hints",
               required=("id",)),
    ApiCommand("vocaster_info", READ, "Vocaster: angeschlossen, Routing", "vocaster_info",
               result="geraet, kanaele, routing, routing_datei"),
    ApiCommand("ueber", READ, "Über Ottilie: Version und mitgelieferte Software mit Lizenzen", "ueber",
               result="version, groups, components [{name, version, license, author, url, source, use, kind}]"),
    ApiCommand("mischungen", READ, "Verzeichnis der auf diesem Mac gemachten Mischungen (neueste zuerst)", "mischungen",
               result="eintraege [{zeit, art, titel, beitrag, sendung, mischer, raum, session, dateien, ordner}], "
                      "anzahl"),
    # ── Arbeitsabläufe (ändern) ───────────────────────────────────────────────────────────────────────────────
    ApiCommand("holen_offen", CHANGE, "Beitrag in die offene Session holen (statt neue Session); Fragen wie bei holen",
               "holen_offen", {"id": "Projekt-ID",
                               "position": "Einfügestelle (wie import.session_ablage.offene_session.position)",
                               "clips": "Clips (wie Einstellung …clips)", "umzug": "Medien umziehen (wie …umzug)",
                               "bisheriger_beitrag": "Umgang mit dem bisherigen Beitrag (wie …bisheriger_beitrag)",
                               "video": "Referenzvideo importieren, true/false"},
               "Auftrag", types={"video": "bool"}, required=("id",)),
    ApiCommand("wiederoeffnen", CHANGE, "Stand eines übernommenen Beitrags wieder öffnen (Session aus der Ablage)",
               "wiederoeffnen", {"id": "Projekt-ID"}, "Auftrag", required=("id",)),
    ApiCommand("sicherung_oeffnen", CHANGE,
               "Sicherung als Kopie „…_wiederhergestellt_hhmm.ptx“ öffnen (Original bleibt)",
               "sicherung_oeffnen", {"id": "Projekt-ID", "path": "Pfad der Sicherung (aus sicherungen)"}, "Auftrag",
               required=("id", "path")),
    ApiCommand("havarie_holen", CHANGE, "Havarie: Paket ohne Helmut in Pro Tools holen (mit Triage)", "havarie_holen",
               {"path": "Paketordner (AAF darin oder eine Ebene tiefer)", "group": _G, "name": _N, "title": "Titel",
                "project_id": "Helmut-ID, falls bekannt",
                "triage": "true/false", "video": "true/false"}, "Auftrag",
               types={"triage": "bool", "video": "bool"}, required=("path",)),
    ApiCommand("havarie_zuordnen", CHANGE, "Havarie-Session einem Helmut-Beitrag zuordnen (Zustand wird nachgetragen)",
               "havarie_zuordnen", {"havarie": "Havarie-Eintrag (aus status/nachtrag)", "id": "Projekt-ID"},
               "text, offen, konflikte", required=("havarie", "id")),
    ApiCommand("havarie_ausliefern", CHANGE, "Havarie: Sendefile bauen, prüfen und an die Ziele liefern",
               "havarie_ausliefern",
               {"path": "Paketordner", "ziele": "Liste [{art: schreibtisch|havarieordner|usb|ordner, mount, pfad}]",
                "preset": "Sendeprofil (Standard ARD_ZDF_HDF01b)", "title": "Titel", "sendung": "Sendung",
                "bild": "Bildquelle"},
               "Auftrag", types={"ziele": "list"}, required=("path", "ziele")),
    ApiCommand("archiv_import", CHANGE, "Fremde Dateien ins Projekt holen (kopieren, nie verschieben)", "archiv_import",
               {"paths": "Audiodateien aus den Archiv-Ordnern (Einstellung)",
                "ziel": "clipliste = nur in die Clip-Liste, sonst neue Spur an der Einfügemarke; Pro Tools kopiert in "
                        "„Audio Files“ der Session"}, "Auftrag",
               types={"paths": "list"}, required=("paths",)),
    ApiCommand("triage_korrigieren", CHANGE, "In der offenen Triage-Vorschau eine Kategorie korrigieren",
               "triage_korrigieren",
               {"track": "Spurname der AAF aus der Vorschau", "index": "Clip-Nummer auf der Spur",
                "category": "o_ton | atmo | musik | rest"},
               "Vorschau", types={"index": "int"}, required=("track", "index", "category")),
    ApiCommand("duplizieren", CHANGE, "Beitrag in Helmut duplizieren", "duplizieren",
               {"id": "Projekt-ID", "title": "Titel des Duplikats", "keep": "Mischstand übernehmen, true/false",
                "take": "gleich übernehmen, true/false"}, "Beitrag wie bei beitrag",
               types={"keep": "bool", "take": "bool"}, required=("id",)),
    ApiCommand("auftrag_abbrechen", CHANGE, "Laufenden Auftrag (Holen, Havarie holen, Wiederöffnen) am nächsten "
               "sicheren Schritt abbrechen", "auftrag_abbrechen", {"id": "Auftrags-ID (aus auftrag)"}, "Auftrag"),
    ApiCommand("nachtragen", CHANGE, "Liegengebliebene Helmut-Änderungen jetzt nachtragen", "nachtragen",
               result="text, offen, konflikte"),
    ApiCommand("mischungen_leeren", CHANGE, "Verzeichnis der Mischungen leeren (Helmut, Sessions und Dateien bleiben)",
               "mischungen_leeren", result="text, eintraege, anzahl"),
    ApiCommand("nachtrag_verwerfen", CHANGE, "Änderung im Nachtrag verwerfen – offen oder Konflikt (Helmut bleibt, "
               "wie es ist)", "nachtrag_verwerfen",
               {"id": "Eintrag (aus status.nachtrag.eintraege oder .konflikte)",
                "havarie": "alle Einträge dieser Havarie-Session (aus status.nachtrag.ohne_beitrag)",
                "alle": "true: alle offenen Änderungen und Konflikte"},
               "text, offen, eintraege, konflikte", types={"alle": "bool"}),
    # ── verwalten ──────────────────────────────────────────────────────────────────────────────────────────────
    ApiCommand("einstellung_setzen", ADMIN, "Eine Einstellung ändern (gilt für den angemeldeten Nutzer bzw. diesen "
               "Mac; Werte der Geräteverwaltung bleiben gesperrt)", "einstellung_setzen",
               {"path": "Pfad, z. B. persoenlich.farbschema", "value": "neuer Wert"},
               "Feld mit value, source, errors", types={"value": "any"}, required=("path", "value")),
    ApiCommand("einstellung_zuruecksetzen", ADMIN, "Eine Einstellung auf den geerbten Wert zurücksetzen",
               "einstellung_zuruecksetzen", {"path": "Pfad"}, "Feld", required=("path",)),
    ApiCommand("einstellungen_exportieren", ADMIN,
               "Geänderte Einstellungen in eine Datei schreiben (nie überschreiben)",
               "einstellungen_exportieren", {"ziel": "Ordner oder .json-Pfad"}, "message, path", required=("ziel",)),
    ApiCommand("einstellungen_importieren", ADMIN, "Einstellungen aus einer Datei übernehmen (Auswahl aus "
               "einstellungen_import_pruefen)", "einstellungen_importieren",
               {"pfad": "Pfad der .json", "auswahl": "Liste der Schlüssel"}, "message", types={"auswahl": "list"},
               required=("pfad", "auswahl")),
    ApiCommand("nutzer_anlegen", ADMIN, "Nutzer anlegen (startet mit dem Hausstandard)", "nutzer_anlegen",
               {"name": "Name"}, "users, message", required=("name",)),
    ApiCommand("nutzer_umbenennen", ADMIN, "Nutzer umbenennen", "nutzer_umbenennen",
               {"id": "Nutzer-ID", "name": "neuer Name"}, "users, message", required=("id", "name")),
    ApiCommand("nutzer_duplizieren", ADMIN, "Nutzer mit Einstellungen und Templates duplizieren", "nutzer_duplizieren",
               {"id": "Nutzer-ID", "name": "Name des neuen Nutzers"}, "users, message", required=("id", "name")),
    ApiCommand("nutzer_loeschen", ADMIN, "Nutzer ins Archiv legen (nicht der angemeldete)", "nutzer_loeschen",
               {"id": "Nutzer-ID"}, "users, message", required=("id",)),
    ApiCommand("nutzer_anmelden", ADMIN, "Nutzer an diesem Mac anmelden (nicht während eines Auftrags)",
               "nutzer_anmelden",
               {"id": "Nutzer-ID", "standard": "auch als Standard-Nutzer dieses Macs, true/false"}, "users, current",
               types={"standard": "bool"}, required=("id",)),
    ApiCommand("raumliste_speichern", ADMIN, "Raumliste speichern (Raum ↔ Mac)", "raumliste_speichern",
               {"rooms": "Liste [{name, rechnername, id_code}]"}, "rooms, message, errors", types={"rooms": "list"},
               required=("rooms",)),
    ApiCommand("vorlage_hochladen", ADMIN, "Pro-Tools-Template (.ptxt oder Ordner mit Video Files) zu „Meine "
               "Templates“ hochladen und in Pro Tools bereitstellen", "vorlage_hochladen",
               {"path": "Pfad der .ptxt oder des Ordners"},
               "mine, message", required=("path",), timeout_s=600),
    ApiCommand("vorlage_uebernehmen", ADMIN, "Template aus der Pro-Tools-Bibliothek in „Meine Templates“ aufnehmen",
               "vorlage_uebernehmen", {"group": _G, "name": _N}, "mine, message", required=("group", "name")),
    ApiCommand("vorlage_standard", ADMIN, "Standard-Template festlegen", "vorlage_standard",
               {"id": "Template-ID (aus vorlagen)"}, "mine, standard", required=("id",)),
    ApiCommand("vorlage_loeschen", ADMIN, "Template aus „Meine Templates“ nehmen (hochgeladene ins Archiv)",
               "vorlage_loeschen", {"id": "Template-ID"}, "mine, message", required=("id",)),
    ApiCommand("spurprofil_lesen", ADMIN, "Spuren der offenen Session ins Spurprofil des Templates übernehmen",
               "spurprofil_lesen", {"gruppe": _G, "name": _N}, "wie spurprofile", required=("name",)),
    ApiCommand("spurprofil_setzen", ADMIN, "Eine Zelle im Spurprofil setzen", "spurprofil_setzen",
               {"gruppe": _G, "name": _N, "spur": "Spur", "spalte": "Spalte", "wert": "Wert"}, "wie spurprofile",
               types={"wert": "any"}, required=("name", "spur", "spalte")),
    ApiCommand("spurprofil_schalter", ADMIN, "Einen Schalter des Spurprofils setzen", "spurprofil_schalter",
               {"gruppe": _G, "name": _N, "schalter": "Schalter", "wert": "Wert"}, "wie spurprofile",
               types={"wert": "any"},
               required=("name", "schalter")),
    ApiCommand("spurprofil_spur_entfernen", ADMIN, "Eine Spur aus dem Spurprofil nehmen", "spurprofil_spur_entfernen",
               {"gruppe": _G, "name": _N, "spur": "Spur"}, "wie spurprofile", required=("name", "spur")),
    ApiCommand("spurprofil_entfernen", ADMIN, "Spurprofil eines Templates entfernen", "spurprofil_entfernen",
               {"gruppe": _G, "name": _N}, "templates", required=("name",)),
    ApiCommand("spurprofile_exportieren", ADMIN, "Spurprofil(e) in eine Datei schreiben (nie überschreiben)",
               "spurprofile_exportieren", {"ziel": "Ordner oder .json-Pfad", "gruppe": _G, "name": _N,
                                           "alle": "alle Spurprofile, true/false"}, "message, path",
               types={"alle": "bool"}, required=("ziel",)),
    ApiCommand("spurprofile_importieren", ADMIN, "Spurprofile aus einer Datei übernehmen", "spurprofile_importieren",
               {"pfad": "Pfad der .json", "auswahl": "Liste der Profile"}, "message", types={"auswahl": "list"},
               required=("pfad", "auswahl")),
    ApiCommand("spurraster_setzen", ADMIN, "Spurraster für Punch-In: Spur in einer Spalte an- oder abwählen",
               "spurraster_setzen", {"spalte": "Spalte", "spur": "Spur", "an": "true/false"}, "Spurraster",
               types={"an": "bool"}, required=("spalte", "spur")),
    ApiCommand("audio_verschieben_setzen", ADMIN, "Audio verschieben: Spurpaare speichern (nur vollständige Paare)",
               "audio_verschieben_setzen", {"paare": "Liste [[Quelle, Ziel], …]; leer = alle Paare entfernen"},
               "wie audio_verschieben", types={"paare": "list"}),
    ApiCommand("preset_speichern", ADMIN, "Spur-Preset speichern", "preset_speichern",
               {"name": "Name", "nr": "Nummer (leer: neues Preset)"}, "presets", types={"nr": "int"},
               required=("name",)),
    ApiCommand("preset_umbenennen", ADMIN, "Spur-Preset umbenennen", "preset_umbenennen",
               {"nr": "Nummer", "name": "Name"}, "presets", types={"nr": "int"}, required=("nr", "name")),
    ApiCommand("preset_loeschen", ADMIN, "Spur-Preset löschen", "preset_loeschen", {"nr": "Nummer"}, "presets",
               types={"nr": "int"}, required=("nr",)),
    ApiCommand("vocaster_routing_sichern", ADMIN, "Aktuelles Vocaster-Routing sichern", "vocaster_routing_sichern",
               result="wie vocaster_info, dazu meldung"),
)

# Einstellungen, die nur ein Mensch im Fenster ändert (Review 09.10.2026, A3): Rechte der Schnittstellen samt
# Helmut-Adresse und Werkzeugpfaden, Webtrigger (Netz, Schlüssel). Sonst könnte sich ein Werkzeug mit „verwalten“
# selbst mehr Rechte geben, den Webtrigger ins Netz öffnen oder Zugangsdaten umleiten. Die Transferziele kommen dazu,
# sobald sie wirken (heute ausgeblendet).
PROTECTED_SETTINGS = ("system.schnittstellen.", "system.streamdeck_webtrigger.")


def protected_setting(path: str) -> bool:
    return any(path == p.rstrip(".") or path.startswith(p) for p in PROTECTED_SETTINGS)


# Nur im Fenster – mit Grund (ein Test verlangt für jeden Befehl der Befehlsschicht: öffentlich oder hier)
WINDOW_ONLY: dict[str, str] = {
    "ordner_waehlen": "öffnet einen Auswahldialog auf dem Bildschirm (über die Schnittstelle: Pfad angeben)",
    "im_finder": "öffnet den Finder", "link_oeffnen": "öffnet den Browser",
    "ready": "meldet das Fenster bereit (intern)", "auswahl": "Auswahl im Fenster (für den Webtrigger /import)",
    "kopieren": "Zwischenablage des Fensters",
    "webtrigger_adressen": "enthält den Webtrigger-Schlüssel – der geht nie an KI-Assistenten",
    "id_code_erzeugen": "antwortet mit den Webtrigger-Adressen samt Schlüssel",
    "id_code_entfernen": "antwortet mit den Webtrigger-Adressen samt Schlüssel",
    "launcher_erzeugen": "legt Startprogramme an und öffnet den Finder",
}

BY_NAME = {c.name: c for c in CATALOG}


@dataclass(frozen=True)
class Rights:
    """Was ein Zugang darf. Fenster und Socket: alles; MCP laut Einstellungen (drei Stufen: lesen immer,
    Arbeitsabläufe ohne „nur lesen“, Verwalten zusätzlich mit eigener Freigabe; Fragen beantworten eigens)."""
    read_only: bool = False
    may_answer: bool = True
    may_admin: bool = True


def rights_for(origin: str, settings: Any) -> Rights:
    if origin == "mcp":
        return Rights(read_only=bool(settings.get("system.schnittstellen.mcp_nur_lesen")),
                      may_answer=bool(settings.get("system.schnittstellen.mcp_antworten_erlaubt")),
                      may_admin=bool(settings.get("system.schnittstellen.mcp_verwalten_erlaubt")))
    return Rights()


class ApiDispatcher:
    """Führt Katalogbefehle aus – mit Rechteprüfung und Protokoll der Herkunft."""

    def __init__(self, call: Callable[[str, dict], Any], settings: Any) -> None:
        self._call = call            # Befehlsschicht: (Handlername, args) → Ergebnis
        self._settings = settings

    def dispatch(self, name: str, args: dict | None, origin: str) -> Any:
        if origin not in ORIGINS:
            raise CommandError(f"Unbekannte Herkunft: {origin}")
        cmd = BY_NAME.get(name)
        if cmd is None:
            raise CommandError(f"Unbekannter Befehl: {name}")
        if origin == "mcp" and not self._settings.get("system.schnittstellen.mcp_aktiv"):
            raise CommandError("MCP ist in den Einstellungen abgeschaltet (system.schnittstellen.mcp_aktiv)")
        args = dict(args or {})
        if origin != "fenster":
            if (name in ("einstellung_setzen", "einstellung_zuruecksetzen")
                    and protected_setting(str(args.get("path") or ""))):
                raise CommandError(f"„{args.get('path')}“ lässt sich nur im Fenster ändern (Rechte, Webtrigger, "
                                   "Helmut-Adresse, Werkzeuge)")
            if name == "einstellungen_importieren":
                args["_ohne_geschuetzte"] = True           # geschützte Werte der Datei bleiben unberücksichtigt
        rights = rights_for(origin, self._settings)
        missing = [a for a in cmd.required if (args or {}).get(a) in (None, "", [])]
        if missing and origin != "fenster":
            raise CommandError(f"„{name}“: Angabe fehlt: {', '.join(missing)}")
        if cmd.kind == ADMIN and (rights.read_only or not rights.may_admin):
            raise CommandError(f"„{name}“ verwaltet Einstellungen, Nutzer, Räume oder Templates – über "
                               f"{origin.upper()} nicht freigegeben (Einstellungen "
                               "system.schnittstellen.mcp_nur_lesen aus und mcp_verwalten_erlaubt an)")
        if cmd.kind == CHANGE and rights.read_only:
            raise CommandError(f"„{name}“ ändert etwas – über {origin.upper()} ist nur Lesen erlaubt "
                               "(Einstellung system.schnittstellen.mcp_nur_lesen)")
        if name == "antworten" and not rights.may_answer:
            raise CommandError("Fragen eines Auftrags beantwortet der Mischer im Fenster "
                               "(Einstellung system.schnittstellen.mcp_antworten_erlaubt)")
        if cmd.kind in (CHANGE, ADMIN):
            log.info("API %s über %s: %s", name, origin, {k: v for k, v in (args or {}).items()})
        return self._call(cmd.handler, args)


KIND_TITLES = {READ: "Lesen", CHANGE: "Arbeitsabläufe (ändern)", ADMIN: "Verwalten"}


def _cell(text: str) -> str:
    """Text für eine Markdown-Tabellenzelle: „|“ maskieren, sonst zerfällt die Tabelle."""
    return text.replace("|", "\\|")


def markdown() -> str:
    """docs/API.md – aus dem Katalog erzeugt (`python3 -m tonmischung api-doku`)."""
    lines = [f"# Ottilie – Schnittstelle (API {API_VERSION})", "",
             "Erzeugt aus `tonmischung/app/api.py` (`python3 -m tonmischung api-doku > docs/API.md`).", "",
             "Alles, was das Fenster kann, geht auch über die Schnittstelle – nur auf diesem Mac, nicht über das "
             "Netzwerk (Jens 08.10.2026: „für Automation, einen vollständigen MCP-Zugriff“). Wo das Fenster einen "
             "Dateidialog zeigt, verlangt die Schnittstelle einen Pfad.", "",
             "## Zugänge und Rechte", "",
             "- **Fenster** (WKWebView) – alle Befehle",
             "- **Unix-Socket** der laufenden App (Skripte, Stream Deck), eine JSON-Zeile je Anfrage: "
             '`{"id": 1, "cmd": "status", "args": {}, "origin": "socket"}` → '
             '`{"id": 1, "ok": true, "result": …}` bzw. `{"id": 1, "ok": false, "error": "…"}`; '
             "Pfad `~/Library/Application Support/Ottilie/api.sock`, nur für den angemeldeten Benutzer (0600) "
             "– alle Rechte",
             "- **MCP-Server** `Ottilie mcp` bzw. `python3 -m tonmischung mcp` (stdio) – Werkzeuge mit denselben "
             "Namen, in drei Stufen:",
             "  - **Lesen** – immer",
             "  - **Arbeitsabläufe** – wenn `system.schnittstellen.mcp_nur_lesen` aus ist (Standard: an)",
             "  - **Verwalten** – zusätzlich `system.schnittstellen.mcp_verwalten_erlaubt` (Standard: aus)",
             "  - `antworten` (Fragen eines Auftrags) – eigens `system.schnittstellen.mcp_antworten_erlaubt` "
             "(Standard: aus)",
             "- **Nur im Fenster änderbar** (über Socket und MCP gesperrt, beim Einstellungsimport übergangen): "
             + ", ".join(f"`{p}…`" if p.endswith(".") else f"`{p}`" for p in PROTECTED_SETTINGS)
             + " – Rechte der Schnittstellen, Helmut-Adresse, Werkzeuge, Webtrigger.",
             "- Die Stufen schützen vor ungewollten Aktionen über MCP-Werkzeuge; sie sind keine Grenze gegen "
             "Programme, die unter demselben Benutzer selbst den Socket ansprechen.",
             "", f"{len(CATALOG)} Befehle: "
             + ", ".join(f"{sum(1 for c in CATALOG if c.kind == k)} {t}" for k, t in KIND_TITLES.items()) + ".", ""]
    for kind, title in KIND_TITLES.items():
        lines += [f"## {title}", ""]
        for c in (c for c in CATALOG if c.kind == kind):
            lines += [f"### `{c.name}`", "", c.summary, ""]
            if c.args:
                lines += ["| Argument | Typ | Pflicht | Bedeutung |", "|---|---|---|---|"] + \
                    [f"| `{k}` | {c.types.get(k, 'str')} | {'ja' if k in c.required else ''} | {_cell(v)} |"
                     for k, v in c.args.items()] + [""]
            if c.result:
                lines += [f"Ergebnis: {c.result}", ""]
    lines += ["## Nur im Fenster", "", "| Befehl | Grund |", "|---|---|"] + \
        [f"| `{k}` | {v} |" for k, v in WINDOW_ONLY.items()] + [""]
    lines += ["## Automation", "",
              "Aufträge (Holen, Havarie, Zurückliefern, Wiederöffnen …) laufen im Hintergrund. Ablauf: Befehl "
              "starten → `auftrag_warten` (wartet bis zu 55 s auf eine Änderung) → bei `state: frage` mit "
              "`antworten` die Frage beantworten (wenn freigegeben) oder dem Mischer im Fenster überlassen → bis "
              "`fertig`, `fehler` oder `abgebrochen`. Abbrechen: `auftrag_abbrechen`.", ""]
    lines += ["## MCP-Server einbinden", "",
              "Die App muss laufen (der MCP-Server spricht über ihren Socket); nur `triage` mit AAF-Pfad geht "
              "ohne App.",
              "", "Claude Code (Paket):", "", "```bash",
              "claude mcp add ottilie -- /Applications/Ottilie.app/Contents/MacOS/Ottilie mcp",
              "```", "", "Claude Code (Quelltext):", "", "```bash",
              "claude mcp add ottilie --env PYTHONPATH=\"/Pfad/zu/pt-helmut connector\" "
              "-- python3 -m tonmischung mcp",
              "```", "", "Claude Desktop (`claude_desktop_config.json`):", "", "```json",
              '{"mcpServers": {"ottilie": {"command": "/Applications/Ottilie.app/Contents/MacOS/Ottilie", '
              '"args": ["mcp"]}}}',
              "```", "",
              "Einstellungen (Geräteverwaltung): `system.schnittstellen.mcp_aktiv` (Standard an), "
              "`mcp_nur_lesen` (Standard an), `mcp_verwalten_erlaubt` (Standard aus), "
              "`mcp_antworten_erlaubt` (Standard aus).", ""]
    lines += ["## Ereignisse (Fenster)", "",
              "- `job` – Stand eines Auftrags (wie `auftrag`), gedrosselt", "",
              "Über Socket und MCP: `auftrag` bzw. `auftrag_warten`.", ""]
    return "\n".join(lines)
