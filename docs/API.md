# Ottilie – Schnittstelle (API 1)

Erzeugt aus `tonmischung/app/api.py` (`python3 -m tonmischung api-doku > docs/API.md`).

Alles, was das Fenster kann, geht auch über die Schnittstelle – nur auf diesem Mac, nicht über das Netzwerk (Jens 08.10.2026: „für Automation, einen vollständigen MCP-Zugriff“). Wo das Fenster einen Dateidialog zeigt, verlangt die Schnittstelle einen Pfad.

## Zugänge und Rechte

- **Fenster** (WKWebView) – alle Befehle
- **Unix-Socket** der laufenden App (Skripte, Stream Deck), eine JSON-Zeile je Anfrage: `{"id": 1, "cmd": "status", "args": {}, "origin": "socket"}` → `{"id": 1, "ok": true, "result": …}` bzw. `{"id": 1, "ok": false, "error": "…"}`; Pfad `~/Library/Application Support/Ottilie/api.sock`, nur für den angemeldeten Benutzer (0600) – alle Rechte
- **MCP-Server** `Ottilie mcp` bzw. `python3 -m tonmischung mcp` (stdio) – Werkzeuge mit denselben Namen, in drei Stufen:
  - **Lesen** – immer
  - **Arbeitsabläufe** – wenn `system.schnittstellen.mcp_nur_lesen` aus ist (Standard: an)
  - **Verwalten** – zusätzlich `system.schnittstellen.mcp_verwalten_erlaubt` (Standard: aus)
  - `antworten` (Fragen eines Auftrags) – eigens `system.schnittstellen.mcp_antworten_erlaubt` (Standard: aus)
- **Nur im Fenster änderbar** (über Socket und MCP gesperrt, beim Einstellungsimport übergangen): `system.schnittstellen.…`, `system.streamdeck_webtrigger.…` – Rechte der Schnittstellen, Helmut-Adresse, Werkzeuge, Webtrigger.
- Die Stufen schützen vor ungewollten Aktionen über MCP-Werkzeuge; sie sind keine Grenze gegen Programme, die unter demselben Benutzer selbst den Socket ansprechen.

84 Befehle: 30 Lesen, 27 Arbeitsabläufe (ändern), 27 Verwalten.

## Lesen

### `status`

Pro Tools (offene Session), Helmut, Mischer und Raum

Ergebnis: version, pro_tools {ok, session}, helmut {ok, mock}, mixer, room

### `beitraege`

Beiträge „Zur Mischung“ (eigene mit Plakette „meine Mischung · …“)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `query` | str |  | Suchtext (Titel, Sendung) |
| `order` | str |  | zeit (Standard) oder sendung |

Ergebnis: rows [{id, title, show, state, pill …}], counts {list}

### `beitrag`

Details eines Beitrags mit den erlaubten Aktionen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str |  | Projekt-ID |

Ergebnis: title, show, banners, kv, actions {fetch, deliver, release}

### `templates`

Templates: „Meine Templates“ und die Pro-Tools-Templates dieses Macs

Ergebnis: mine, found [{group, name}], default, triage, preview

### `triage`

AAF beurteilen: Kategorie je Spur und Clip (ohne Pro Tools)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `aaf` | str |  | Pfad zur AAF |
| `id` | str |  | oder Projekt-ID (AAF aus der Übergabe) |

Ergebnis: Bericht: spuren, paare, clips mit kategorie und grund

### `auftrag`

Stand des laufenden oder letzten Auftrags

Ergebnis: title, state (laeuft|frage|fertig|fehler|abgebrochen), steps, fraction, question, result, error

### `pro_tools`

Pro Tools abfragen (nur lesen): Transport, Spuren, Session, Auswahl, Marker

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `was` | str |  | transport \| spuren \| session \| auswahl \| marker |

Ergebnis: je nach Abfrage, z. B. transport oder spuren [{name, typ, clips, format}]

### `zurueckliefern_info`

Übersicht vor dem Zurückliefern

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str |  | Projekt-ID |

Ergebnis: session, tracks, missing, target, lufs, truepeak

### `ping`

Lebt die App? Version

Ergebnis: pong, version

### `auftrag_warten`

Für Automation: warten, bis sich der Auftrag ändert (Zustand, Schritt, Frage, Fortschritt in ganzen Prozent) – statt ständig nachzufragen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `sekunden` | float |  | höchstens so lange warten, 1–55 (Standard 25) |

Ergebnis: geaendert (true/false), auftrag

### `raeume`

Raumübersicht: je Raum Nutzer, Mischer, Zustand, Beitrag, Fortschritt

Ergebnis: cards [{name, here, st, label, user, title, pct, online}], busy, source

### `raumliste`

Raumliste (Raum ↔ Mac) und die Macs zur Auswahl

Ergebnis: rooms [{id, name, rechnername}], macs, this_mac, this_room, ablage

### `nutzer`

Nutzer, angemeldeter Nutzer und Standard-Nutzer dieses Macs

Ergebnis: users [{id, name}], current, standard, ablage, needs_login

### `vorlagen`

„Meine Templates“ des angemeldeten Nutzers mit Standard und gefundenen Templates

Ergebnis: mine, found, standard

### `einstellungen`

Bereiche und Abschnitte der Einstellungen

Ergebnis: areas

### `einstellungen_abschnitt`

Felder eines Abschnitts: Wert, Herkunft (Nutzer, Mac, zentral, Geräteverwaltung, Hausstandard), Sperre, Auswahl

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Abschnitt, z. B. export.spuren_lautheit |

Ergebnis: title, groups [{fields [{path, label, value, source, locked, options}]}]

### `einstellungen_import_pruefen`

Einstellungsdatei lesen und zeigen, was übernommen würde (ändert nichts)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `pfad` | str | ja | Pfad der .json |

Ergebnis: path, choices, unknown

### `spurprofile`

Spurprofile je Template

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `gruppe` | str |  | Templategruppe |
| `name` | str |  | Template-Name |

Ergebnis: templates, chosen, selected (Zeilen, Probleme, Schalter), session, columns

### `spurprofile_import_pruefen`

Spurprofil-Datei lesen und zeigen, was übernommen würde

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `pfad` | str | ja | Pfad der .json |

Ergebnis: path, choices

### `spurraster`

Spurraster für Punch-In (Aufnahme, Wiedergabe je Spur)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `lesen` | bool |  | true: Spuren der offenen Session aus Pro Tools lesen |

Ergebnis: spuren, spalten, meldung

### `presets`

Spur-Presets für Punch-In

Ergebnis: presets [{nr, name}]

### `audio_verschieben`

Audio verschieben: Spurpaare Quelle → Ziel und die Spuren der Session

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `lesen` | bool |  | true: Spuren der offenen Session aus Pro Tools lesen |

Ergebnis: paare [[quelle, ziel]], spuren, fehlen, verbunden, gesperrt, meldung

### `sicherungen`

Sicherungen (Session File Backups) zur Session eines Beitrags

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Projekt-ID |

Ergebnis: folder, rows, reachable, can_open, availability, hint

### `archiv`

Fremde Dateien der offenen Session und das Archiv

Ergebnis: session, folders, foreign, archive

### `havarie_info`

Havarie-Paket prüfen: AAF, Video, Titel- und ID-Vorschlag

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `path` | str | ja | Paketordner (AAF darin oder eine Ebene tiefer) |

Ergebnis: folder, aaf, video, title, project_id, complete, hint

### `havarie_ausliefern_info`

Vor der Havarie-Auslieferung: Paket, Session, Ziele (Schreibtisch, Havarie-Ordner, USB-Sticks), Presets

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `path` | str |  | Paketordner (leer: Vorschlag zur offenen Session) |
| `auto` | bool |  | true: Paket selbst suchen |

Ergebnis: path, title, schreibtisch, havarie_ordner, usb, presets

### `offene_session_info`

Vor „In offene Session holen“: offene Session, Spuren, Vorgaben

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Projekt-ID |

Ergebnis: offer, name, path, defaults, choices, hints

### `vocaster_info`

Vocaster: angeschlossen, Routing

Ergebnis: geraet, kanaele, routing, routing_datei

### `ueber`

Über Ottilie: Version und mitgelieferte Software mit Lizenzen

Ergebnis: version, groups, components [{name, version, license, author, url, source, use, kind}]

### `mischungen`

Verzeichnis der auf diesem Mac gemachten Mischungen (neueste zuerst)

Ergebnis: eintraege [{zeit, art, titel, beitrag, sendung, mischer, raum, session, dateien, ordner}], anzahl

## Arbeitsabläufe (ändern)

### `holen`

Beitrag übernehmen und in Pro Tools importieren (mit AAF-Triage). Fragen (offene Session, Vorschau) erscheinen im Fenster beim Mischer.

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str |  | Projekt-ID |
| `group` | str |  | Templategruppe |
| `name` | str |  | Template |
| `triage` | str |  | true/false |
| `video` | str |  | Referenzvideo importieren, true/false |

Ergebnis: Auftrag (siehe auftrag)

### `zurueckliefern`

Mischung zurückliefern: konsolidieren, R128, WAVs an Helmut

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str |  | Projekt-ID |
| `tracks` | str |  | Exportspuren, kommagetrennt (sonst Spurtabelle) |

Ergebnis: Auftrag

### `freigeben`

Übernahme aufheben (Zurückgeben)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str |  | Projekt-ID |
| `reason` | str |  | Workstation abgestürzt \| Raumwechsel \| Versehentlich übernommen |

Ergebnis: Beitrag wie bei beitrag

### `antworten`

Offene Frage eines Auftrags beantworten

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | int | ja | Frage-ID aus auftrag.question.id |
| `value` | any |  | Antwort: einer der Werte aus auftrag.question.options (meist true/false) |

Ergebnis: ok

### `record_a`

Record A starten bzw. stoppen (Aufnahmespuren aus den Einstellungen)

Ergebnis: meldung (z. B. record_a queued)

### `record_b`

Record B starten bzw. stoppen

Ergebnis: meldung

### `play`

Play Input: Wiedergabe mit Eingangsmonitor starten bzw. stoppen

Ergebnis: meldung

### `play_custom`

Play Custom: Wiedergabe mit den Stumm-Regeln für Kanal 1/2

Ergebnis: meldung

### `goto_start`

Cursor auf den Programmbeginn

Ergebnis: meldung

### `move_audio`

Audio von den Quell- auf die Zielspuren verschieben

Ergebnis: meldung

### `preset`

Spur-Preset laden

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `nr` | str |  | Preset-Nummer, 1 = erstes |

Ergebnis: meldung (preset n loaded)

### `export_wav`

WAV-Export der Exportspuren (konsolidieren, Lautheit, WAVs in den Export-Ordner)

Ergebnis: meldung; Fortschritt über auftrag

### `export_aaf`

AAF-Export mit eingebetteten Medien (über Pro-Tools-Dialoge)

Ergebnis: meldung; Fortschritt über auftrag

### `export_aaf_reference`

AAF-Export mit Referenz auf die Medien (über Pro-Tools-Dialoge)

Ergebnis: meldung; Fortschritt über auftrag

### `holen_offen`

Beitrag in die offene Session holen (statt neue Session); Fragen wie bei holen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Projekt-ID |
| `position` | str |  | Einfügestelle (wie import.session_ablage.offene_session.position) |
| `clips` | str |  | Clips (wie Einstellung …clips) |
| `umzug` | str |  | Medien umziehen (wie …umzug) |
| `bisheriger_beitrag` | str |  | Umgang mit dem bisherigen Beitrag (wie …bisheriger_beitrag) |
| `video` | bool |  | Referenzvideo importieren, true/false |

Ergebnis: Auftrag

### `wiederoeffnen`

Stand eines übernommenen Beitrags wieder öffnen (Session aus der Ablage)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Projekt-ID |

Ergebnis: Auftrag

### `sicherung_oeffnen`

Sicherung als Kopie „…_wiederhergestellt_hhmm.ptx“ öffnen (Original bleibt)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Projekt-ID |
| `path` | str | ja | Pfad der Sicherung (aus sicherungen) |

Ergebnis: Auftrag

### `havarie_holen`

Havarie: Paket ohne Helmut in Pro Tools holen (mit Triage)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `path` | str | ja | Paketordner (AAF darin oder eine Ebene tiefer) |
| `group` | str |  | Templategruppe |
| `name` | str |  | Template-Name |
| `title` | str |  | Titel |
| `project_id` | str |  | Helmut-ID, falls bekannt |
| `triage` | bool |  | true/false |
| `video` | bool |  | true/false |

Ergebnis: Auftrag

### `havarie_zuordnen`

Havarie-Session einem Helmut-Beitrag zuordnen (Zustand wird nachgetragen)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `havarie` | str | ja | Havarie-Eintrag (aus status/nachtrag) |
| `id` | str | ja | Projekt-ID |

Ergebnis: text, offen, konflikte

### `havarie_ausliefern`

Havarie: Sendefile bauen, prüfen und an die Ziele liefern

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `path` | str | ja | Paketordner |
| `ziele` | list | ja | Liste [{art: schreibtisch\|havarieordner\|usb\|ordner, mount, pfad}] |
| `preset` | str |  | Sendeprofil (Standard ARD_ZDF_HDF01b) |
| `title` | str |  | Titel |
| `sendung` | str |  | Sendung |
| `bild` | str |  | Bildquelle |

Ergebnis: Auftrag

### `archiv_import`

Fremde Dateien ins Projekt holen (kopieren, nie verschieben)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `paths` | list | ja | Audiodateien aus den Archiv-Ordnern (Einstellung) |
| `ziel` | str |  | clipliste = nur in die Clip-Liste, sonst neue Spur an der Einfügemarke; Pro Tools kopiert in „Audio Files“ der Session |

Ergebnis: Auftrag

### `triage_korrigieren`

In der offenen Triage-Vorschau eine Kategorie korrigieren

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `track` | str | ja | Spurname der AAF aus der Vorschau |
| `index` | int | ja | Clip-Nummer auf der Spur |
| `category` | str | ja | o_ton \| atmo \| musik \| rest |

Ergebnis: Vorschau

### `duplizieren`

Beitrag in Helmut duplizieren

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Projekt-ID |
| `title` | str |  | Titel des Duplikats |
| `keep` | bool |  | Mischstand übernehmen, true/false |
| `take` | bool |  | gleich übernehmen, true/false |

Ergebnis: Beitrag wie bei beitrag

### `auftrag_abbrechen`

Laufenden Auftrag (Holen, Havarie holen, Wiederöffnen) am nächsten sicheren Schritt abbrechen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str |  | Auftrags-ID (aus auftrag) |

Ergebnis: Auftrag

### `nachtragen`

Liegengebliebene Helmut-Änderungen jetzt nachtragen

Ergebnis: text, offen, konflikte

### `mischungen_leeren`

Verzeichnis der Mischungen leeren (Helmut, Sessions und Dateien bleiben)

Ergebnis: text, eintraege, anzahl

### `nachtrag_verwerfen`

Änderung im Nachtrag verwerfen – offen oder Konflikt (Helmut bleibt, wie es ist)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str |  | Eintrag (aus status.nachtrag.eintraege oder .konflikte) |
| `havarie` | str |  | alle Einträge dieser Havarie-Session (aus status.nachtrag.ohne_beitrag) |
| `alle` | bool |  | true: alle offenen Änderungen und Konflikte |

Ergebnis: text, offen, eintraege, konflikte

## Verwalten

### `einstellung_setzen`

Eine Einstellung ändern (gilt für den angemeldeten Nutzer bzw. diesen Mac; Werte der Geräteverwaltung bleiben gesperrt)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `path` | str | ja | Pfad, z. B. persoenlich.farbschema |
| `value` | any | ja | neuer Wert |

Ergebnis: Feld mit value, source, errors

### `einstellung_zuruecksetzen`

Eine Einstellung auf den geerbten Wert zurücksetzen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `path` | str | ja | Pfad |

Ergebnis: Feld

### `einstellungen_exportieren`

Geänderte Einstellungen in eine Datei schreiben (nie überschreiben)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `ziel` | str | ja | Ordner oder .json-Pfad |

Ergebnis: message, path

### `einstellungen_importieren`

Einstellungen aus einer Datei übernehmen (Auswahl aus einstellungen_import_pruefen)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `pfad` | str | ja | Pfad der .json |
| `auswahl` | list | ja | Liste der Schlüssel |

Ergebnis: message

### `nutzer_anlegen`

Nutzer anlegen (startet mit dem Hausstandard)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `name` | str | ja | Name |

Ergebnis: users, message

### `nutzer_umbenennen`

Nutzer umbenennen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Nutzer-ID |
| `name` | str | ja | neuer Name |

Ergebnis: users, message

### `nutzer_duplizieren`

Nutzer mit Einstellungen und Templates duplizieren

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Nutzer-ID |
| `name` | str | ja | Name des neuen Nutzers |

Ergebnis: users, message

### `nutzer_loeschen`

Nutzer ins Archiv legen (nicht der angemeldete)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Nutzer-ID |

Ergebnis: users, message

### `nutzer_anmelden`

Nutzer an diesem Mac anmelden (nicht während eines Auftrags)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Nutzer-ID |
| `standard` | bool |  | auch als Standard-Nutzer dieses Macs, true/false |

Ergebnis: users, current

### `raumliste_speichern`

Raumliste speichern (Raum ↔ Mac)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `rooms` | list | ja | Liste [{name, rechnername, id_code}] |

Ergebnis: rooms, message, errors

### `vorlage_hochladen`

Pro-Tools-Template (.ptxt oder Ordner mit Video Files) zu „Meine Templates“ hochladen und in Pro Tools bereitstellen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `path` | str | ja | Pfad der .ptxt oder des Ordners |

Ergebnis: mine, message

### `vorlage_uebernehmen`

Template aus der Pro-Tools-Bibliothek in „Meine Templates“ aufnehmen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `group` | str | ja | Templategruppe |
| `name` | str | ja | Template-Name |

Ergebnis: mine, message

### `vorlage_standard`

Standard-Template festlegen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Template-ID (aus vorlagen) |

Ergebnis: mine, standard

### `vorlage_loeschen`

Template aus „Meine Templates“ nehmen (hochgeladene ins Archiv)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `id` | str | ja | Template-ID |

Ergebnis: mine, message

### `spurprofil_lesen`

Spuren der offenen Session ins Spurprofil des Templates übernehmen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `gruppe` | str |  | Templategruppe |
| `name` | str | ja | Template-Name |

Ergebnis: wie spurprofile

### `spurprofil_setzen`

Eine Zelle im Spurprofil setzen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `gruppe` | str |  | Templategruppe |
| `name` | str | ja | Template-Name |
| `spur` | str | ja | Spur |
| `spalte` | str | ja | Spalte |
| `wert` | any |  | Wert |

Ergebnis: wie spurprofile

### `spurprofil_schalter`

Einen Schalter des Spurprofils setzen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `gruppe` | str |  | Templategruppe |
| `name` | str | ja | Template-Name |
| `schalter` | str | ja | Schalter |
| `wert` | any |  | Wert |

Ergebnis: wie spurprofile

### `spurprofil_spur_entfernen`

Eine Spur aus dem Spurprofil nehmen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `gruppe` | str |  | Templategruppe |
| `name` | str | ja | Template-Name |
| `spur` | str | ja | Spur |

Ergebnis: wie spurprofile

### `spurprofil_entfernen`

Spurprofil eines Templates entfernen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `gruppe` | str |  | Templategruppe |
| `name` | str | ja | Template-Name |

Ergebnis: templates

### `spurprofile_exportieren`

Spurprofil(e) in eine Datei schreiben (nie überschreiben)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `ziel` | str | ja | Ordner oder .json-Pfad |
| `gruppe` | str |  | Templategruppe |
| `name` | str |  | Template-Name |
| `alle` | bool |  | alle Spurprofile, true/false |

Ergebnis: message, path

### `spurprofile_importieren`

Spurprofile aus einer Datei übernehmen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `pfad` | str | ja | Pfad der .json |
| `auswahl` | list | ja | Liste der Profile |

Ergebnis: message

### `spurraster_setzen`

Spurraster für Punch-In: Spur in einer Spalte an- oder abwählen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `spalte` | str | ja | Spalte |
| `spur` | str | ja | Spur |
| `an` | bool |  | true/false |

Ergebnis: Spurraster

### `audio_verschieben_setzen`

Audio verschieben: Spurpaare speichern (nur vollständige Paare)

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `paare` | list |  | Liste [[Quelle, Ziel], …]; leer = alle Paare entfernen |

Ergebnis: wie audio_verschieben

### `preset_speichern`

Spur-Preset speichern

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `name` | str | ja | Name |
| `nr` | int |  | Nummer (leer: neues Preset) |

Ergebnis: presets

### `preset_umbenennen`

Spur-Preset umbenennen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `nr` | int | ja | Nummer |
| `name` | str | ja | Name |

Ergebnis: presets

### `preset_loeschen`

Spur-Preset löschen

| Argument | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `nr` | int | ja | Nummer |

Ergebnis: presets

### `vocaster_routing_sichern`

Aktuelles Vocaster-Routing sichern

Ergebnis: wie vocaster_info, dazu meldung

## Nur im Fenster

| Befehl | Grund |
|---|---|
| `ordner_waehlen` | öffnet einen Auswahldialog auf dem Bildschirm (über die Schnittstelle: Pfad angeben) |
| `im_finder` | öffnet den Finder |
| `link_oeffnen` | öffnet den Browser |
| `ready` | meldet das Fenster bereit (intern) |
| `auswahl` | Auswahl im Fenster (für den Webtrigger /import) |
| `kopieren` | Zwischenablage des Fensters |
| `webtrigger_adressen` | enthält den Webtrigger-Schlüssel – der geht nie an KI-Assistenten |
| `id_code_erzeugen` | antwortet mit den Webtrigger-Adressen samt Schlüssel |
| `id_code_entfernen` | antwortet mit den Webtrigger-Adressen samt Schlüssel |
| `launcher_erzeugen` | legt Startprogramme an und öffnet den Finder |

## Automation

Aufträge (Holen, Havarie, Zurückliefern, Wiederöffnen …) laufen im Hintergrund. Ablauf: Befehl starten → `auftrag_warten` (wartet bis zu 55 s auf eine Änderung) → bei `state: frage` mit `antworten` die Frage beantworten (wenn freigegeben) oder dem Mischer im Fenster überlassen → bis `fertig`, `fehler` oder `abgebrochen`. Abbrechen: `auftrag_abbrechen`.

## MCP-Server einbinden

Die App muss laufen (der MCP-Server spricht über ihren Socket); nur `triage` mit AAF-Pfad geht ohne App.

Claude Code (Paket):

```bash
claude mcp add ottilie -- /Applications/Ottilie.app/Contents/MacOS/Ottilie mcp
```

Claude Code (Quelltext):

```bash
claude mcp add ottilie --env PYTHONPATH="/Pfad/zu/pt-helmut connector" -- python3 -m tonmischung mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{"mcpServers": {"ottilie": {"command": "/Applications/Ottilie.app/Contents/MacOS/Ottilie", "args": ["mcp"]}}}
```

Einstellungen (Geräteverwaltung): `system.schnittstellen.mcp_aktiv` (Standard an), `mcp_nur_lesen` (Standard an), `mcp_verwalten_erlaubt` (Standard aus), `mcp_antworten_erlaubt` (Standard aus).

## Ereignisse (Fenster)

- `job` – Stand eines Auftrags (wie `auftrag`), gedrosselt

Über Socket und MCP: `auftrag` bzw. `auftrag_warten`.
