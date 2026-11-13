# Test vor Ort: Betriebsart „Übergabeordner“

Zum Abarbeiten am Standort, mit zwei Macs (Mac A, Mac B) und dem echten Speicher. Je Schritt: was tun, was soll
passieren, Ergebnis eintragen (✅ / ❌ und kurz, was zu sehen war). Bei ❌ bitte den Diagnosebericht beilegen
(`/Applications/Ottilie.app/Contents/MacOS/Ottilie diagnose` bzw. `python3 -m tonmischung diagnose`, landet auf dem
Schreibtisch).

**Sicherheit:** Nur einen Testordner auf dem Speicher benutzen (z. B. `<Freigabe>/_ottilie_test/`), nie den echten
Übergabeordner mit laufenden Sendungen. Pro Tools nur mit Test-Sessions. Die Fertigmeldung nur an einen
Testempfänger.

## 0. Vorbereitung

| # | Tun | Erwartet | Ergebnis |
|---|---|---|---|
| 0.1 | Auf beiden Macs dieselbe Ottilie-Version starten (Über Ottilie) | gleiche Versionsnummer | |
| 0.2 | Freigabe auf beiden Macs im Finder einbinden (gleicher Pfad, z. B. `/Volumes/<Freigabe>`) | Ordner sichtbar, beschreibbar | |
| 0.3 | Im Testordner zwei Filmordner anlegen: je AAF + MXF eines Testbeitrags, dazu `uebergabe.json` (Format `uebergabe/1`, Muster unten), die JSON **zuletzt** kopieren | – | |
| 0.4 | Auf beiden Macs: Einstellungen → System → Übergabeordner: Übergabeordner = Testordner, Betriebsart = Übergabeordner | Titelleiste: „Ohne Helmut-API – Übergabeordner erreichbar“; Block „Stand“ zeigt „erreichbar und beschreibbar“ und die Zahl der Filme | |

## 1. Automatischer Test auf dem Speicher (einmal, auf einem Mac)

| # | Tun | Erwartet | Ergebnis |
|---|---|---|---|
| 1.1 | Im Terminal im Ottilie-Quelltext: `tools/smb_test.sh /Volumes/<Freigabe>` | 7 Tests „passed“ (Symlink-Test darf „skipped“ sein, wenn der Server keine Symlinks erlaubt) | |
| 1.2 | Bericht `testfiles/smb-test-<Zeit>.md` öffnen | Uhrabweichung Speicher − Mac steht drin (sollte unter einigen Sekunden liegen), Dateirechte, Einbindung (smbfs, Version) | Uhrabweichung: ____ s |
| 1.3 | Auf der Freigabe nachsehen | kein `_ottilie_smbtest-…`-Ordner mehr übrig | |

Ohne Person vor Ort (11.11.2026, Mac mini, Testserver): Abschnitt 1 (8 von 8, dreimal), dazu mit echtem Pro Tools
über `tools/pt_uebergabe_test.py` (vorbereiten → holen → liefern → nachtragen) 2.1, 4.1–4.6 (ohne Fenster), 4.7 nach
dem Ausliefern, 5.3–5.5 mit einem Empfänger auf 127.0.0.1. Offen bleiben Fenster, zwei Macs, Raumstatus und das
Umschalten. Seit 0.3 kommt der Raumstatus nur über die Zentrale, die Betriebsart gibt die Zentrale vor (Abschnitt 7
dann über Administration → Übergabeordner).

## 2. Liste und Erkennung

| # | Tun | Erwartet | Ergebnis |
|---|---|---|---|
| 2.1 | Liste ansehen | beide Filme „bereit“, darunter „aus <Schnittraum>“ | |
| 2.2 | Einen dritten Filmordner langsam kopieren (großes MXF), JSON erst danach | während des Kopierens „wird übertragen“ (grau, nicht übernehmbar); einige Sekunden nach der JSON „bereit“ | |
| 2.3 | In einer `uebergabe.json` eine Größe falsch eintragen | „unvollständig“, im Detail „… noch nicht vollständig (x von y)“, Übernehmen gesperrt | |
| 2.4 | ⌘R drücken (auch: Darstellung → Liste aktualisieren) | Liste liest sofort neu ein, „Lese neu ein …“, danach „aktualisiert hh:mm“; das Fenster lädt **nicht** neu | |
| 2.5 | Knopf „Aktualisieren“ mehrmals schnell klicken | nur ein Einlesen, kein Stau | |
| 2.6 | Speicher kurz trennen (Finder: Auswerfen) | Titelleiste rot „Übergabeordner nicht erreichbar – ist der Speicher im Finder verbunden?“; **keine** wiederholten Fehlermeldungen alle 15 s | |
| 2.7 | Speicher wieder einbinden | Titelleiste nach spätestens 15 s wieder grün | |

## 3. Zwei Macs: Belegung

| # | Tun | Erwartet | Ergebnis |
|---|---|---|---|
| 3.1 | Mac A und Mac B: denselben Film fast gleichzeitig „Übernehmen“ | genau einer bekommt ihn; der andere: „belegt von … (Raum)“ | Gewinner: ____ |
| 3.2 | Auf dem anderen Mac die Liste ansehen (ohne ⌘R) | Film sofort als „in Mischung · <Mischer> (<Raum>)“ (über die Zentrale), spätestens nach 15 s aus der Sperrdatei | |
| 3.3 | Im Filmordner `.ottilie-sperre.json` ansehen (Finder: ⌘⇧. für versteckte Dateien) | lesbar, `gesperrt: true`, Raum, Mischer, Zustand | |
| 3.4 | Auf dem Gewinner-Mac „Freigeben“ (Grund wählen) | Sperrdatei bleibt, `gesperrt: false`, Zustand „zurueckgegeben“; der andere Mac kann jetzt übernehmen | |

## 4. Echtes Pro Tools: Holen, Mischen, Zurückliefern

| # | Tun | Erwartet | Ergebnis |
|---|---|---|---|
| 4.1 | Mac A: Film übernehmen und holen (Template, Triage nach Wunsch) | Session aus Template, AAF und Video importiert; Ende: „Session ist in Pro Tools geöffnet. Beitrag ist für dich gesperrt.“ | |
| 4.2 | Session-Spiegel prüfen | Spiegel unter `<Filmordner>/Ton/ProTools/` (Einstellung „lokal mit Spiegelung“) | |
| 4.3 | Kurz mischen, speichern, „Zurückliefern“ | Dialog: Überschrift „Zurückliefern nach „Audio Export““, Ziel = `<Filmordner>/Audio Export` | |
| 4.4 | Zurückliefern starten | WAVs und `<ID>_metadaten.json` in „Audio Export“, Ende: „n WAVs liegen in „Audio Export“. …“; kein „_in_arbeit-…“-Ordner übrig | |
| 4.5 | Metadaten-JSON öffnen | `herkunft.schnittraum` gesetzt, Pfade zeigen auf „Audio Export“ | |
| 4.6 | Noch einmal zurückliefern | vorige Lieferung liegt in `Audio Export/_alt/<Zeit>/`, nichts gelöscht | |
| 4.7 | Neue Fassung: während A mischt, `uebergabe.json` entfernen, Medien ersetzen, neue JSON mit `uebergabe_nr: 2` | Mac A: Kennzeichen „neue Übergabe“, Hinweis im Detail; beim Zurückliefern rote Warnung und Haken „Ich habe geklärt …“ – ohne Haken kein Start | |

## 5. Fertigmeldung

| # | Tun | Erwartet | Ergebnis |
|---|---|---|---|
| 5.1 | Einstellungen → Übergabeordner: „Fertigmeldung senden“ an, Adresse eines Testempfängers (z. B. `http://<Testrechner>:<Port>/fertig`) | Knopf „Testmeldung senden“ wird aktiv | |
| 5.2 | „Testmeldung senden“ | „Testmeldung ist angekommen.“; beim Empfänger `"ereignis": "test"` | |
| 5.3 | Zurückliefern | Empfänger bekommt `"ereignis": "audio_export_fertig"` mit `meldung_id`, Pfaden relativ zum Übergabeordner, Dateiliste mit Größen | |
| 5.4 | Empfänger abschalten, zurückliefern | Ende: „Die Fertigmeldung ist noch nicht angekommen und wird wiederholt“; Titelleiste „Nachtrag: 1 offen“ | |
| 5.5 | Empfänger wieder an, „Jetzt nachtragen“ | genau eine Meldung, dieselbe `meldung_id` wie bei 5.4 | |

## 6. Raumstatus (über die Zentrale) und Raumübersicht

| # | Tun | Erwartet | Ergebnis |
|---|---|---|---|
| 6.1 | Ansicht „Räume“ auf beiden Macs | beide Räume mit Mischer, Beitrag, Fortschritt (Raumstatus der Zentrale, ergänzt aus den Sperrdateien) | |
| 6.2 | Während eines Holens auf Mac A | Mac B zeigt „Import nn %“ für Raum A | |
| 6.3 | Mac B auf Betriebsart Helmut-API stellen (nichts übernommen, Nachtrag leer) | Mac A: Karte von Raum B mit Kennzeichen „Helmut-API“ und Warnhinweis „arbeitet in einer anderen Betriebsart“ | |

## 7. Umschalten der Betriebsart

| # | Tun | Erwartet | Ergebnis |
|---|---|---|---|
| 7.1 | Mit übernommenem Film umschalten | abgelehnt: „„<Titel>“ ist hier noch übernommen – erst zurückliefern oder freigeben“ (rot im Einstellungsblock) | |
| 7.2 | Während eines Auftrags umschalten | abgelehnt mit Hinweis auf den Auftrag | |
| 7.3 | Speicher getrennt, umschalten | abgelehnt: „Übergabeordner ist nicht erreichbar …“ | |
| 7.4 | Nichts in Arbeit, umschalten | sofort wirksam: Titelleiste, Liste, Ansage „Betriebsart: …“ | |

## 8. Zentrale (Testserver)

| # | Tun | Erwartet | Ergebnis |
|---|---|---|---|
| 8.1 | `https://testserver.…:8443/admin` öffnen, mit admin/admin anmelden | sofort „Kennwort ändern“, andere Seiten gesperrt; nach dem Ändern Übersicht | |
| 8.2 | Räume & Geräte: eigenen Raum anlegen bzw. prüfen, Kopplungscode erzeugen | Code mit 6 Zeichen, gilt 15 min | |
| 8.3 | Ottilie: Einstellungen → System → Zentrale: Adresse eintragen, „Ohne Code anmelden …“ | macOS fragt einmal nach einem Administrator; danach „Code dieses Macs“ und in der Titelleiste „Mac wartet auf Zuordnung“ | |
| 8.3a | Administration → Räume & Geräte → Wartende Geräte: Code vergleichen, Raum wählen, „Zuordnen“ | Ottilie: „Zentrale verbunden“, Raum der Zentrale in der Titelleiste; VoiceOver sagt „zugeordnet“ | |
| 8.4 | Am Mac mit einem zweiten macOS-Benutzer anmelden, Ottilie starten | ohne Eingabe „Zentrale verbunden“, derselbe Raum (Kopplung und Raum gelten für alle Benutzer) | |
| 8.4a | Zentrale stoppen, zweiten Benutzer neu starten | derselbe Raum wie bei Benutzer 1 (zuletzt bekannter Raum gilt rechnerweit) | |
| 8.4b | Mehrere Übergabeordner: zweiten Ordner unter „Weitere Übergabeordner“ eintragen | Liste zeigt Filme beider Ordner mit „Ordner …“; Belegen in der Zentrale mit „<kennung>/<Film>“ | |
| 8.4c | Zweite Freigabe trennen | Hinweis „Übergabeordner nicht erreichbar: …“, Filme des ersten Ordners gehen weiter, übernommener Film des zweiten bleibt sichtbar | |
| 8.5 | Web-App im Schnittraum (Edge/Chrome) öffnen, koppeln, „App installieren“ | Raumübersicht, Chat; installierbar | |
| 8.6 | Film in Ottilie übernehmen | Web-App: im Beitragsgespräch „belegt von …“, unter „Meine Beiträge“ „belegt“; zweiter Mac: „belegt von … (Zentrale)“ | |
| 8.7 | Chat Mischraum ↔ Schnittraum, einmal „dringend“ | Hinweistext immer sichtbar; bei laufender Wiedergabe in Pro Tools kein Ton | |
| 8.8 | Ottilie neu starten | Chat leer, nur nicht zugestellte Nachrichten kommen | |
| 8.9 | Zentrale stoppen (`docker compose stop`) | Titelleiste „ohne Zentrale“, Übernehmen geht weiter über die Sperrdatei; nach dem Start Belegungen abgeglichen | |
| 8.10 | Administration → Chat: Chat aus | Chatbereich überall weg, wartende Nachrichten gelöscht | |

## 9. Übergabeordner selbst einbinden

| # | Tun | Erwartet | Ergebnis |
|---|---|---|---|
| 9.1 | Freigabe im Finder auswerfen; Einstellungen → Übergabeordner: Netzadresse `smb://Server/Freigabe/Ordner`, Anmeldung wählen | Ottilie bindet selbst ein (`/Volumes/…`), Liste erscheint | |
| 9.2 | Mit Anmeldung „Schlüsselbund“: Eintrag `ottilie-uebergabeordner` (Konto = Benutzer an der Freigabe) anlegen | Einbinden ohne Dialog | |
| 9.3 | Server kurz nicht erreichbar | Titelleiste „Übergabeordner nicht verbunden: …“, neuer Versuch nach 30 s … 5 min, ⌘R sofort | |

## Muster `uebergabe.json`

```json
{"format": "uebergabe/1", "erstellt": "2026-11-11T09:00:00+01:00", "uebergabe_nr": 1,
 "beitrag": {"id": "TEST-1", "titel": "Testbeitrag", "sendung": "Test", "cutter": "Test", "dringend": false},
 "herkunft": {"schnittraum": "Schnitt Test"},
 "dateien": [{"typ": "aaf", "name": "Testbeitrag.aaf", "groesse": 123456},
             {"typ": "video", "name": "Testbeitrag.mxf", "groesse": 9876543}]}
```

Die Größen in Bytes: im Terminal `stat -f %z <Datei>`.
