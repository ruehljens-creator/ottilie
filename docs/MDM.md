# Konfigurationsprofil für Ottilie (Geräteverwaltung, MDM)

Erzeugt aus `tonmischung/config/einstellungen.schema.json` (`python3 -m tonmischung mdm-doku > docs/MDM.md`). Nicht von Hand ändern.

## So wird es eingerichtet

- In der Geräteverwaltung ein Konfigurationsprofil mit eigenen Einstellungen (Custom Settings) für die Domäne **`de.ottilie.app`** anlegen – sie ist die Bundle-ID der App.
- Schlüssel ist der Punktpfad der Einstellung (Tabelle). Eine Vorlage mit allen Schlüsseln und den Hausstandard-Werten erzeugt `python3 -m tonmischung mdm-doku --plist`.
- Nur erzwungene Werte zählen. In der App erscheinen sie gesperrt („Geräteverwaltung – gesperrt“) und überstimmen Spurprofil, zentrale und lokale Werte.
- Zugangsdaten gehören nie ins Profil – nur der Name des Schlüsselbund-Eintrags.
- **Geräteverwaltung:** Konfigurationsprofil auf Computerebene (nicht Benutzerebene), eigene Einstellungen für die Domäne `de.ottilie.app`. Rechnerweit gesetzt gilt es für alle Benutzer des Macs – z. B. `system.zentrale.adresse` und `system.uebergabeordner.netzadresse`, damit kein Benutzer etwas eintragen muss.
- Die Kopplung mit der Zentrale gilt ebenfalls je Mac: einmal anmelden oder koppeln (Administrator bestätigt), danach für alle Benutzer. Den Raum und die Übergabeordner legt die Administration der Zentrale fest, nicht das Profil.
- **Reihenfolge beim Umstieg auf 0.3:** erst die Zentrale aktualisieren, dort Räume anlegen und Betriebsart sowie Übergabeordner setzen, dann die Macs aktualisieren und je Mac anmelden und zuordnen.

## Gemeinsamer Datenordner vorbereiten (empfohlen)

Nachtrag, Mischungsverzeichnis und Havarie-Pakete liegen für alle Benutzer eines Macs in `/Users/Shared/Ottilie`. Am sichersten legt ihn die Geräteverwaltung als root an – dann ändert Ottilie an Besitz und Rechten nichts:

```sh
install -d -o root -g staff -m 2775 /Users/Shared/Ottilie
```

- Alle Benutzer, die mischen, müssen in der Gruppe `staff` sein (lokale Benutzer sind es; bei Verzeichniskonten mit `dseditgroup -o checkmember -m <name> staff` prüfen).
- Ottilie nimmt keinen Verweis und keinen für alle beschreibbaren Ordner. Ist der Ordner nicht nutzbar, arbeitet Ottilie im Benutzerordner weiter und sagt es beim Start; der Diagnosebericht zeigt es im Abschnitt „Zentrale und gemeinsamer Datenordner“.
- Ein anderer Ort: `system.raeume_nutzer.datenordner` per Profil (für alle Benutzer) – im Fenster nur für den einzelnen Benutzer, nie über die Zentrale oder die Schnittstelle.
- Jeder Benutzer des Macs kann die Dateien dort ändern – der Ordner setzt Vertrauen unter den Benutzern eines Macs voraus. Einträge anderer Benutzer trägt Ottilie nicht von selbst nach.
- Sicherungen, die nur Benutzerordner erfassen, schließen `/Users/Shared/Ottilie` nicht ein.

## Entfallene Schlüssel (bis 0.2)

Diese Schlüssel wirken seit 0.3 nicht mehr – bitte aus alten Profilen entfernen:

| Schlüssel | Ersatz |
|---|---|
| `system.raeume_nutzer.raeume`, `raum_erkennung`, `raum_fest` | Raum aus der Zentrale (Administration → Räume & Geräte); ohne Zentrale `system.raeume_nutzer.raumname` |
| `system.raeume_nutzer.raumstatus.*` | Raumstatus über die Zentrale |
| ID-Code in der Raumliste | `system.streamdeck_webtrigger.id_code` (je Mac) |

## Vorgesehene Schlüssel (51)

| Schlüssel | Typ | Hausstandard | Erlaubt | Bedeutung |
|---|---|---|---|---|
| `import.session_ablage.lokaler_ordner` | String | /Volumes/ProTools-Lokal/Sessions/ |  | Lokaler ordner |
| `system.raeume_nutzer.raumname` | String | „“ |  | Raumname dieses Macs – Nur ohne Zentrale. Ist der Mac mit der Zentrale gekoppelt, gilt der Raum, dem die Administration ihn zugeordnet hat. Leer: der Rechnername. |
| `system.raeume_nutzer.datenordner` | String | „“ |  | Gemeinsamer Datenordner dieses Macs – Nachtrag, Mischungsverzeichnis und Havarie-Pakete gelten für alle Benutzer des Macs. Leer: /Users/Shared/Ottilie. Nicht über die Zentrale vorgebbar. |
| `system.raeume_nutzer.nutzer_modus` | String | mac_benutzer | mac_benutzer, manuell | Nutzer modus |
| `system.schnittstellen.helmut.adresse` | String | https://[helmut-server] |  | Adresse – Server ohne Pfad; die Dienste liegen unter /v1/members, /v1/fx, /v1/co (Helmut4-OpenAPI) |
| `system.schnittstellen.helmut.anmeldeverfahren` | String | passwort | passwort, oauth, token | Anmeldeverfahren – passwort: Benutzername und Kennwort; oauth: OAuth-Schlüssel und -Geheimnis des Kontos (im Schlüsselbund als Konto und Kennwort); token: Bearer-Token (API-Token) des Kontos als Kennwort im Schlüsselbund, ohne Anmeldeaufruf |
| `system.schnittstellen.helmut.schluesselbund_eintrag` | String | ottilie-helmut |  | Schluesselbund eintrag |
| `system.schnittstellen.ptsl.adresse` | String | localhost:31416 |  | Adresse |
| `system.schnittstellen.ptsl.zeitlimit_s` | Integer | 30 |  | Zeitlimit (s) |
| `system.schnittstellen.werkzeuge.bmx.modus` | String | mitgeliefert | mitgeliefert, eigener_pfad | Modus |
| `system.schnittstellen.werkzeuge.bmx.pfad` | String | – |  | Pfad |
| `system.schnittstellen.werkzeuge.ffmpeg.modus` | String | mitgeliefert | mitgeliefert, eigener_pfad | Modus |
| `system.schnittstellen.werkzeuge.ffmpeg.pfad` | String | – |  | Pfad |
| `system.schnittstellen.werkzeuge.ffprobe.modus` | String | mitgeliefert | mitgeliefert, eigener_pfad | Modus |
| `system.schnittstellen.werkzeuge.ffprobe.pfad` | String | – |  | Pfad |
| `system.schnittstellen.werkzeuge.mediainfo.modus` | String | mitgeliefert | mitgeliefert, eigener_pfad | Modus |
| `system.schnittstellen.werkzeuge.mediainfo.pfad` | String | – |  | Pfad |
| `system.schnittstellen.mcp_aktiv` | Boolean | true |  | Mcp aktiv |
| `system.schnittstellen.mcp_nur_lesen` | Boolean | true |  | Mcp nur lesen – KI-Assistenten (MCP) dürfen nur lesen. Aus: auch Arbeitsabläufe (Holen, Zurückliefern, Havarie, Abbrechen …) |
| `system.schnittstellen.mcp_antworten_erlaubt` | Boolean | false |  | Mcp antworten erlaubt – KI-Assistenten dürfen Fragen eines Auftrags beantworten; sonst nur der Mischer im Fenster |
| `system.schnittstellen.mcp_verwalten_erlaubt` | Boolean | false |  | Mcp verwalten erlaubt – KI-Assistenten (MCP) dürfen auch verwalten: Einstellungen, Nutzer, Räume, Templates, Spurprofile, Presets (nur wenn „nur lesen“ aus ist) |
| `system.uebergabeordner.betriebsart` | String | helmut_api | helmut_api, uebergabeordner | Betriebsart – Gibt die Zentrale vor (Administration → Übergabeordner; ohne Eintrag Helmut-API) – dann hier gesperrt. Gewechselt wird nur, wenn kein Auftrag läuft, kein Film übernommen ist und der Nachtrag leer ist. Die aktive Betriebsart steht in der Titelleiste. |
| `system.uebergabeordner.name` | String | „“ |  | Name des Übergabeordners – Herkunft in der Liste, wenn es mehrere Übergabeordner gibt. Leer: „Übergabeordner“. |
| `system.uebergabeordner.wurzelordner` | String | „“ |  | Übergabeordner – Darin je Film ein Ordner mit AAF, MXF und der Übergabe-JSON. |
| `system.uebergabeordner.netzadresse` | String | „“ |  | Netzadresse des Übergabeordners – smb://Server/Freigabe/Unterordner – Ottilie bindet die Freigabe beim Start und vor dem Einlesen selbst ein (wie der Finder, für den angemeldeten Benutzer) und nimmt den tatsächlichen Einhängepunkt als Übergabeordner. Leer: Übergabeordner wie eingestellt (Freigabe von Hand oder per Geräteverwaltung verbunden). Nur smb://. |
| `system.uebergabeordner.netz_anmeldung` | String | gast | gast, kerberos, schluesselbund | Anmeldung an der Freigabe – Kennwörter nie in Einstellungen, Profil oder Zentrale – nur im Schlüsselbund des Benutzers. |
| `system.uebergabeordner.netz_schluesselbund_eintrag` | String | ottilie-uebergabeordner |  | Schlüsselbund-Eintrag für die Freigabe – Name des Eintrags (Konto = Benutzername an der Freigabe) bei Anmeldung „Schlüsselbund“. |
| `system.uebergabeordner.netz_erlaubte_server` | Array | [] |  | Erlaubte Übergabe-Server – Nur diese Server (Name oder Adresse) bindet Ottilie ein – leer: jeder. Per Konfigurationsprofil vorgeben, damit eine fremde Netzadresse (z. B. von einer gekaperten Zentrale) nicht greift. |
| `system.uebergabeordner.weitere_ordner` | Array | [] |  | Weitere Übergabeordner – Jeder Mischraum sieht die Filme aller Übergabeordner in einer Liste, mit Herkunft; zurückgeliefert wird in den jeweiligen Filmordner. Je Ordner Name, Pfad auf dem Mac oder Netzadresse (smb://…) mit Anmeldung. Wird in der Zentrale festgelegt. |
| `system.uebergabeordner.uebergabe_json` | String | uebergabe.json |  | Name der Übergabe-Datei – Helmut schreibt sie als letzte Datei in den Filmordner; erst dann erscheint der Film in der Liste. |
| `system.uebergabeordner.wartezeit_s` | Integer | 3 |  | Wartezeit, bevor ein neuer Beitrag erscheint (s) |
| `system.uebergabeordner.abfrageintervall_s` | Integer | 15 |  | Liste neu einlesen alle (s) |
| `system.uebergabeordner.ordner_ignorieren` | Array | ["_", "."] |  | Ordner ignorieren, die so beginnen |
| `system.uebergabeordner.wird_uebertragen_zeigen` | Boolean | true |  | Ordner ohne Übergabe-JSON als „wird übertragen“ zeigen – Zeigt Ordner, die Helmut noch füllt, gesperrt in der Liste. |
| `system.uebergabeordner.sperrdatei` | String | .ottilie-sperre.json |  | Name der Sperrdatei |
| `system.uebergabeordner.sperre_verwaist_h` | Integer | 12 |  | Sperre gilt als verwaist nach (h) – Ohne Änderung so lange übernommen: Ottilie weist darauf hin, dass die Sperre verwaist sein kann. |
| `system.uebergabeordner.exportordner` | String | Audio Export |  | Name des Exportordners |
| `system.uebergabeordner.erneute_lieferung` | String | ersetzen | ersetzen, zeitstempel | Bei erneuter Lieferung – „_alt“ trägt die Zeit der vorigen Lieferung. Nichts wird gelöscht. |
| `system.uebergabeordner.fertigmeldung.aktiv` | Boolean | false |  | Fertigmeldung senden |
| `system.uebergabeordner.fertigmeldung.url` | String | „“ |  | Adresse (URL) – Mit Anmeldung nur https:// (außer localhost). |
| `system.uebergabeordner.fertigmeldung.methode` | String | POST | POST, PUT | Methode |
| `system.uebergabeordner.fertigmeldung.zeitlimit_s` | Integer | 15 |  | Zeitlimit (s) |
| `system.uebergabeordner.fertigmeldung.wiederholungen` | Integer | 2 |  | Wiederholungen |
| `system.uebergabeordner.fertigmeldung.anmeldung` | String | keine | keine, bearer, basic, api_key | Anmeldung – Geheimnis nur im Schlüsselbund (Eintrag unten): Kennwort = Token, Kennwort bzw. API-Key; bei Basic das Konto als Benutzer. |
| `system.uebergabeordner.fertigmeldung.api_key_header` | String | X-API-Key |  | Name des API-Key-Headers |
| `system.uebergabeordner.fertigmeldung.schluesselbund_eintrag` | String | ottilie-fertigmeldung |  | Schlüsselbund-Eintrag |
| `system.uebergabeordner.fertigmeldung.body_vorlage` | Dictionary | {"ereignis": "{ereignis}", "format": "ottilie-fertigmeldung/1", "meldung_id": "{meldung_id}", "zeit": "{zeit}", "beitrag": {"id": "{projekt_id}", "titel": "{titel}", "uebergabe_nr": "{uebergabe_nr}", "aktuelle_uebergabe_nr": "{aktuelle_uebergabe_nr}"}, "herkunft": {"schnittraum": "{schnittraum}"}, "mischung": {"mischer": "{mischer}", "raum": "{raum}"}, "film_ordner": "{film_ordner}", "export_ordner": "{export_ordner}", "metadaten_json": "{metadaten_json_pfad}", "dateien": "{dateien}"} |  | Body-Vorlage (JSON) – Platzhalter: {ereignis}, {meldung_id}, {zeit}, {projekt_id}, {titel}, {uebergabe_nr} (gemischte Fassung), {aktuelle_uebergabe_nr} (zuletzt übergebene), {schnittraum}, {mischer}, {raum}, {film_ordner}, {export_ordner}, {metadaten_json_pfad}, {dateien}. Pfade relativ zum Übergabeordner. |
| `system.uebergabeordner.fertigmeldung.erfolgs_statuscodes` | Array | ["2xx"] |  | Erfolgreiche Statuscodes |
| `system.zentrale.adresse` | String | „“ |  | Adresse der Zentrale – https://Server:Port (z. B. https://testserver.example.ts.net:8443). Am besten per Konfigurationsprofil vorgeben; nach dem Anmelden gilt sie aus der Kopplung für alle Benutzer. |
| `system.streamdeck_webtrigger.port` | Integer | 8899 |  | Port |
| `system.streamdeck_webtrigger.id_code` | String | „“ |  | ID-Code dieses Macs – Webtrigger aus dem Netz lösen nur mit ?id=<Code> aus. Erzeugen und entfernen unter „Adressen zum Kopieren“. Leer: keiner. Kein Zugriffsschutz – dafür ist das Token da. |

Jede andere Einstellung lässt sich ebenso erzwingen (Schlüssel = Pfad im Schema). Für alle Macs gleich sind die meisten oben genannten; **je Mac verschieden** sein müssen `system.raeume_nutzer.raumname` (nur ohne Zentrale) und `system.streamdeck_webtrigger.id_code` – diese nur in einem eigenen Profil je Mac setzen, nie in einem gemeinsamen (sonst lösen Netz-Trigger im falschen Raum aus).

## Je Mac und je Benutzer

Für alle Benutzer des Macs gemeinsam: Kopplung mit der Zentrale (System-Schlüsselbund); Nachtrag, Mischungsverzeichnis, Havarie-Pakete, zuletzt bekannter Raum und zuletzt empfangene Vorgaben der Zentrale im gemeinsamen Datenordner (`/Users/Shared/Ottilie`, Gruppe `staff`, anderer Ort: `system.raeume_nutzer.datenordner`).

| Was bleibt je Benutzer | Wo | Empfehlung |
|---|---|---|
| Einstellungen, die nicht per Profil kommen | `~/Library/Application Support/Ottilie/einstellungen.json` | Gemeinsames per Konfigurationsprofil (Computerebene) oder über die Zentrale (Übergabeordner) vorgeben |
| Helmut-Zugang (Token oder Kennwort) | Anmelde-Schlüsselbund, Eintrag `ottilie-helmut` | je Benutzer anlegen |
| Fertigmeldung mit Anmeldung | Anmelde-Schlüsselbund, Eintrag `ottilie-fertigmeldung` | je Benutzer; ohne Anmeldung nichts nötig |
| Anmeldung an der Übergabe-Freigabe | Anmelde-Schlüsselbund `ottilie-uebergabeordner` (nur Verfahren „Schlüsselbund“) | Gast oder Kerberos brauchen nichts je Benutzer |
| Webtrigger-Token | Anmelde-Schlüsselbund `ottilie-webtrigger` | entsteht von selbst |
| Pro-Tools-Templates, „Meine Templates“ | `~/Documents/Pro Tools/Session Templates` | je Benutzer (Pro Tools) |
| Rechte Bildschirmaufnahme, Bedienungshilfen, Automation | macOS-Datenschutz | am Mac bestätigen: das Paket ist ad hoc signiert, ein PPPC-Profil bräuchte eine feste Signatur (Developer ID). Bildschirmaufnahme lässt sich per Profil ohnehin nicht vorab erlauben, nur Standardbenutzern das Bestätigen |
| Start bei der Anmeldung, Stream-Deck-Launcher | `~/Library/LaunchAgents`, `~/Applications` | je Benutzer oder per Geräteverwaltung verteilen |
| Protokolle | `~/Library/Logs/Ottilie/Ottilie.log` (7 Tage) | Diagnosebericht des jeweiligen Benutzers |
| Webtrigger-Port bei schneller Benutzerumschaltung | – | die zweite Ottilie weicht auf einen anderen Port aus |
