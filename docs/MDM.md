# Konfigurationsprofil für Ottilie (Geräteverwaltung, MDM)

Erzeugt aus `tonmischung/config/einstellungen.schema.json` (`python3 -m tonmischung mdm-doku > docs/MDM.md`). Nicht von Hand ändern.

## So wird es eingerichtet

- In der Geräteverwaltung ein Konfigurationsprofil mit eigenen Einstellungen (Custom Settings) für die Domäne **`de.ottilie.app`** anlegen – sie ist die Bundle-ID der App.
- Schlüssel ist der Punktpfad der Einstellung (Tabelle). Eine Vorlage mit allen Schlüsseln und den Hausstandard-Werten erzeugt `python3 -m tonmischung mdm-doku --plist`.
- Nur erzwungene Werte zählen. In der App erscheinen sie gesperrt („Geräteverwaltung – gesperrt“) und überstimmen Spurprofil, zentrale und lokale Werte.
- Zugangsdaten gehören nie ins Profil – nur der Name des Schlüsselbund-Eintrags.

## Vorgesehene Schlüssel (22)

| Schlüssel | Typ | Hausstandard | Erlaubt | Bedeutung |
|---|---|---|---|---|
| `import.session_ablage.lokaler_ordner` | String | /Volumes/ProTools-Lokal/Sessions/ |  | Lokaler ordner |
| `system.raeume_nutzer.raum_erkennung` | String | rechnername | rechnername, fest | Raum erkennung |
| `system.raeume_nutzer.raum_fest` | String | – |  | Raum fest |
| `system.raeume_nutzer.nutzer_modus` | String | mac_benutzer | mac_benutzer, manuell | Nutzer modus |
| `system.schnittstellen.helmut.adresse` | String | https://[helmut-server] |  | Adresse – Server ohne Pfad; die Dienste liegen unter /v1/members, /v1/fx, /v1/co (Helmut4-OpenAPI) |
| `system.schnittstellen.helmut.anmeldeverfahren` | String | passwort | passwort, oauth | Anmeldeverfahren – passwort: Benutzername und Kennwort; oauth: OAuth-Schlüssel und -Geheimnis des Kontos (im Schlüsselbund als Konto und Kennwort) |
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
| `system.streamdeck_webtrigger.port` | Integer | 8899 |  | Port |

Jede andere Einstellung lässt sich ebenso erzwingen (Schlüssel = Pfad im Schema); vorgesehen sind die oben genannten, weil sie für alle Macs gleich sein sollen.
