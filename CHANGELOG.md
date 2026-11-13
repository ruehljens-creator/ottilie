# Versionen

Je ausgeliefertem Paket gibt es einen Git-Tag `v<Version>` und ein Release mit dem DMG. Die Versionsnummer steht nur
in `tonmischung/__init__.py`. v0.x heißt: in der Erprobung, gegen Pro Tools geprüft, gegen Helmut bisher nur gegen
den Mock.

## v0.3 – 13.11.2026 (nur mit Zentrale)

Ottilie ist jetzt auf den Betrieb mit der Ottilie-Zentrale ausgerichtet. Ohne Verbindung arbeitet sie mit dem
zuletzt bekannten Raum weiter; der Havariebetrieb bleibt auf das Nötigste beschränkt. Wer Ottilie ohne Zentrale
braucht, nimmt v0.2 (Zweig `standalone`). Plan und Entscheidungen: Plan „nur Zentrale“ (interne Unterlagen).

- **Räume nur in der Zentrale.**
  - Der Raum eines Macs kommt aus der Zentrale. Raumliste, Raumerkennung und fester Raum in Ottilie entfallen.
  - Der zuletzt bekannte Raum und die zuletzt empfangenen Vorgaben gelten für alle Benutzer des Macs, auch wenn die
    Zentrale ausfällt. Die Kommandozeile nutzt denselben Raum.
  - Was die alte Raumliste für diesen Mac enthielt (Raumname, Webtrigger-ID-Code), wird beim ersten Start einmal
    übernommen.
- **Koppeln ohne Code.**
  - Ottilie meldet sich bei der Zentrale an und zeigt einen sechsstelligen Code („Code dieses Macs“).
  - Die Administration vergleicht ihn unter „Räume & Geräte → Wartende Geräte“ und ordnet den Mac einem Raum zu
    (Pflichtauswahl) oder lehnt ab.
  - Ein wartender Mac sieht und sendet nichts. Wartende verfallen 24 h nach dem letzten Kontakt; je Adresse höchstens
    zwei.
  - Nach Ablehnen oder Verfallen bietet Ottilie „Neu anmelden …“ an.
  - Der Kopplungscode bleibt als zweiter Weg.
- **Umziehen und Umbenennen.**
  - Ein Gerät lässt sich in der Administration einem anderen Raum zuordnen, ein Raum lässt sich umbenennen.
    Beides geht nur, wenn im Raum nichts in Arbeit ist.
  - Ottilie übernimmt den neuen Raum erst, wenn auf dem Mac nichts mehr in Arbeit ist – bei keinem Mischer und
    keinem Benutzer, und nur, wenn Helmut bzw. alle Übergabeordner lesbar sind. Bis dahin steht in der Titelleiste
    „Raum → neuer Name (nach dem Zurückliefern)“.
- **Ohne Zentrale:**
  - Weiterarbeiten mit dem zuletzt bekannten Raum.
  - Raumstatus, Chat und Belegen über die Zentrale fehlen dann; die Titelleiste sagt es.
- **Betriebsart** (Helmut-API oder Übergabeordner) gibt die Zentrale vor (ohne Eintrag: Helmut-API); in Ottilie ist
  sie dann gesperrt. Betriebsart und Übergabeordner wechseln erst, wenn nichts in Arbeit ist.
- **Mehrere Übergabeordner.**
  - Die Liste wird in der Administration festgelegt (Name, `smb://…`, Anmeldung, Pfad auf dem Mac) und an alle Macs
    verteilt.
  - Jeder Mischraum sieht die Filme aller Ordner in einer Liste, mit Herkunft. Zurückgeliefert wird in den
    jeweiligen Filmordner.
  - Fällt ein Ordner aus, bleiben die anderen nutzbar; seine Filme stehen mit dem letzten Stand als „nicht
    erreichbar“ in der Liste, über der Liste steht ein Hinweis. Jede Freigabe wird für sich eingebunden.
  - An die Zentrale gehen die Filme als „<Kennung>/<Film>“ (die Kennung entsteht aus dem Namen). Einen Ordner mit
    Belegungen kann die Administration nicht umbenennen oder entfernen.
  - Weitere Ordner nur mit Anmeldung Gast oder Kerberos.
  - Die Ordnerbeobachtung der Zentrale beobachtet alle eingebundenen Ordner (`ZENTRALE_UEBERGABE_WEITERE`); einen
    nicht beobachteten liest Ottilie weiter im normalen Takt.
- **Nachtrag, Mischungsverzeichnis und Havarie-Pakete für alle Benutzer des Macs.**
  - Die Daten liegen gemeinsam in `/Users/Shared/Ottilie`, mit Dateisperre bei schneller Benutzerumschaltung.
  - Beim ersten Start je Benutzer werden die bisherigen Daten übernommen. Gelöscht wird nichts.
  - Den Pfad kann die Geräteverwaltung übersteuern (`system.raeume_nutzer.datenordner`). Empfohlen: den Ordner als
    root anlegen (`docs/MDM.md`). Ottilie folgt dort keinem Verweis und ändert vorhandene Ordner nie.
  - Jeder Nachtrag-Eintrag trägt den Benutzer, die Betriebsart und das Ziel der Fertigmeldung. Der 60-s-Takt trägt
    nur eigene Einträge nach; „Jetzt nachtragen“ auch die anderer Benutzer, wenn Betriebsart und Fertigmeldung gleich
    eingerichtet sind. Ein ausgefallener Übergabeordner hält die Einträge der anderen nicht auf.
- **Webtrigger-ID-Code je Mac** (`system.streamdeck_webtrigger.id_code`) statt an der Raumliste.
- **Zentrale (Server 0.2):** getrennte Bremsen für Anmelden und Koppeln; Zuordnen nur mit dem gesehenen Code;
  Umbenennen ohne halbe Zustände; Raum entfernen räumt den Chat auf; Protokoll als CSV ohne Formeln; Admin-Seiten
  mit besseren Kontrasten, deutlichen Fehlern und Namen für VoiceOver; Formular „Übergabeordner“ behält Eingaben.
- **Bedienung:** Der Zentrale-Chip öffnet die Einstellungen der Zentrale; Texte sagen, was zu tun ist; VoiceOver
  sagt Zuordnung und Raumwechsel an; Diagnosebericht mit Abschnitt „Zentrale und gemeinsamer Datenordner“.
- **Entfallen:**
  - Bonjour (Raumstatus im lokalen Netz).
  - Raumstatus über Helmut.
  - Einstellungen exportieren und importieren.
  - Rückfall der zentralen Vorgaben über Helmut.

## v0.2 – 13.11.2026 (letzte Fassung, die auch ohne Zentrale läuft)

Gesichert, bevor Ottilie auf den Betrieb mit der Ottilie-Zentrale ausgerichtet wird. Diese Fassung läuft mit und
ohne Zentrale, in beiden Betriebsarten (Helmut-API und Übergabeordner). Zweig `standalone` für spätere Korrekturen
genau dieser Fassung. Pakete: `Ottilie-0.2-intel.dmg` und `Ottilie-0.2-apple-silicon.dmg` (macOS 26), ad hoc
signiert.

- Anmeldedialog: Vorgewählt wird nur ein Nutzer, den es auf diesem Mac gibt. Vorher schickte der Dialog einen
  Standard-Nutzer, den die App nicht kannte, und meldete „Nutzer nicht gefunden“.

- **Ottilie-Zentrale** (neu, `zentrale/`): Server im Docker-Container.
  - **Räume und Geräte:** Kopplungscode, gilt je Mac für alle Benutzer, Ablage im System-Schlüsselbund.
  - **Raumstatus** über alle Räume.
  - **Chat:**
    - Gesprächsarten: alle Räume, Raum an Raum, Beitragsgespräch.
    - Fester Hinweis über jedem Gespräch, „dringend“, Systemmeldungen.
    - Der Verlauf liegt nur im Arbeitsspeicher; der Server hält Nachrichten nur bis zur Zustellung.
  - **Belegen über die Zentrale:** Wer zuerst fragt, bekommt den Film. Die Sperrdatei bleibt, ohne Zentrale geht es
    wie bisher.
  - **Vorgaben für den Übergabeordner** an alle Macs.
  - **Neue Beiträge sofort melden:** über die Ordnerbeobachtung oder ein Helmut-Ereignis.
  - **Administration im Browser:** mit Protokoll und Sicherungen.
  - **Web-App** für Schnitträume.

  In Ottilie: Einstellungen → System → Zentrale (Adresse, Koppeln), Anzeige in der Titelleiste, Bereich „Chat“.
  Ohne Zentrale bleibt alles wie bisher.
- **Übergabeordner selbst einbinden:** Mit einer Netzadresse `smb://Server/Freigabe/Ordner` verbindet Ottilie die
  Freigabe beim Start und vor dem Einlesen selbst (Gast, Kerberos oder Schlüsselbund), ohne Dialog. Eine schon
  verbundene Freigabe wird genutzt.
- **Betriebsart „Übergabeordner“** (Einstellungen → System → Übergabeordner), umschaltbar ohne Neustart, wenn nichts
  in Arbeit ist: Filme kommen aus Ordnern statt über die Helmut-API. Ein Filmordner erscheint, sobald Helmut darin
  `uebergabe.json` (Format `uebergabe/1`, mit Schnittraum) abgelegt hat; Dateien und Größen werden nur per `stat`
  abgeglichen („bereit“, „unvollständig“, „wird übertragen“, neue Fassung). Belegt wird über eine Sperrdatei im
  Filmordner, Freigeben schreibt sie um. Zurückgeliefert wird nach „Audio Export“ im Filmordner (vorige Lieferung
  nach „_alt/<Zeit>“ oder je Lieferung ein Zeitstempel-Ordner), die Metadaten-Datei mit Schnittraum zuletzt, danach
  eine Fertigmeldung an einen einstellbaren HTTP-Dienst (`ottilie-fertigmeldung/1`, „Testmeldung senden“); scheitert
  sie, wird sie nachgetragen. Duplizieren und Benachrichtigen gibt es dort nicht. Die Betriebsart steht in der
  Titelleiste und im Raumstatus; Räume in einer anderen Betriebsart werden gewarnt angezeigt.
- Übergabeordner auf SMB-Freigaben ohne Attributspeicher: keine `._`-Begleitdateien von macOS mehr in „Audio Export“
  und im Session-Spiegel; `._*` und `.DS_Store` im Übergabeordner werden übergangen. Fremde Begleitdateien bleiben.
- Holen brach ab, wenn Pro Tools eine harmlose Debug-Meldung der Steuerfläche ins Protokoll schrieb; jetzt zählen nur
  echte interne Fehler. Übergabeordner: Kam nach dem Ausliefern eine neue Fassung, fragt erneutes Zurückliefern jetzt
  nach; der Hinweis „Helmut ist nicht erreichbar“ entfällt dort, der Schritt heißt „Übergeben“.
- Liste: Knopf „Aktualisieren“ und ⌘R (Fenster neu laden jetzt ⌥⌘R); im Takt keine Fehler-Toasts mehr, die
  Titelleiste zeigt den Ausfall.
- **Metadaten-Datei beim Zurückliefern**: `{projektId}_metadaten.json` neben den WAVs, als letzte Datei und als Asset
  in Helmut. Darin: Beitrag, VideoID, Pfad zum Referenzvideo, die WAVs mit Pfad in Kanalreihenfolge der Spurtabelle,
  Kanalbelegung, Lautheit und die ARD-ZDF-konformen Angaben fürs MXF (Clipname „VideoID Titel“, Erstellungsdatum;
  das HDF-Profil verbietet weitere Descriptive Metadata). Die Datei entsteht, bevor Helmut etwas sieht; scheitert sie,
  ist nichts halb angemeldet. Das Havarie-Sendefile trägt die VideoID ebenfalls im Clipnamen.
- **VideoID im Zurückliefern-Dialog**: Eingabefeld, vorbelegt aus Helmut, „Aus Zwischenablage einfügen“, auf Wunsch
  Pflicht (`export.notweg_bericht.videoid_*`). Die VideoID steht im neuen Feld `videoid` im Set „Mischung“; ein
  geleertes Feld lässt die VideoID in Helmut stehen.
- Vor dem Übernehmen und Zurückliefern trägt Ottilie offene Nachträge des Beitrags erst nach – eine alte eingereihte
  Lieferung überholt die neue nicht mehr. Bei einem Konflikt im Nachtrag bricht sie vorher mit Hinweis ab. Fällt
  Helmut mitten in einer Lieferung aus, wird der Rest in derselben Reihenfolge eingereiht; das Ergebnis zeigt dann
  „In Helmut noch nicht eingetragen“. Leere Felder, die es im Set noch nicht gibt, legt Ottilie nicht mehr an.
  Dasselbe gilt vor dem Freigeben und vor jedem Zustandswechsel. Lehnt Helmut einen Nachtrag ab, warten die folgenden
  Einträge desselben Beitrags, und Ottilie sagt das (statt „nicht erreichbar“).
  Wartet noch eine ältere Änderung, wird eine neue dahinter eingereiht statt verloren zu gehen; „Stand
  wiederherstellen“ trägt vor dem Öffnen nach. Ausfälle zählen nicht mehr als Fehlversuch.
- Nachtrag: Antwortet Helmut, aber ein einzelner Eintrag scheitert dauerhaft (z. B. immer 503), wartet nur dessen
  Beitrag, und nach fünf Versuchen erscheint der Eintrag als Konflikt – vorher hing der ganze Nachtrag. Eine
  Benachrichtigung, auf die Helmut zu spät antwortet, wird nicht mehr jede Minute neu verschickt.
- Lehnt Helmut beim Zurückliefern ein WAV oder den Zustand ab, versucht Ottilie es im Nachtrag weiter, statt
  abzubrechen – kein erneutes Zurückliefern, keine doppelten WAVs. Nachgetragene WAVs landen nur am eigenen Beitrag.
  Eine Havarie-Session lässt sich keinem Beitrag zuordnen, der schon in Mischung ist.
- Ein in Helmut gesperrter Beitrag, dem kein Mischer zugeordnet ist, lässt sich wieder übernehmen (Ottilie wartet
  vorher und nachher kurz, ob nicht gerade ein anderer Raum übernimmt). Dass zwei Räume einen Beitrag gleichzeitig
  übernehmen, ist damit praktisch ausgeschlossen; ein Rest fällt beim nächsten Zustandswechsel auf.
- Havarie: VideoID im Dialog „Sendefile ausliefern“; Havarie-Sessions lassen sich nur bei erreichbarem Helmut und
  keinem Beitrag zuordnen, den inzwischen jemand übernommen oder ein anderer ausgeliefert hat.
- Verständlichere Meldungen im Nachtrag und nach dem Zurückliefern, jeweils mit dem nächsten Schritt.
- **Helmut-Anmeldung mit Bearer-Token** (`system.schnittstellen.helmut.anmeldeverfahren = token`): API-Token des
  Kontos als Kennwort im Schlüsselbund-Eintrag `ottilie-helmut`, ohne Anmeldeaufruf; nimmt auch `Bearer …` an, auch
  URL-kodiert (`Bearer%20…`). Lehnt Helmut das Token ab, liest Ottilie den Schlüsselbund neu – ein dort neu
  eingetragenes Token gilt sofort. Lehnt Helmut nur einen Aufruf ab, sagt die Meldung das.
- Weiterleitungen nur noch bei GET (ein POST wurde still zum GET); das Token geht nur an denselben Server.
  Meldungen und Protokoll enthalten weder Token noch Kennwort.
- Ist die Anmeldung nicht möglich (abgelehnt oder Schlüsselbund nicht lesbar), werden Änderungen eingereiht, und der
  Nachtrag wartet, statt Einträge zu Konflikten zu machen. Eine abgelehnte Benachrichtigung hält andere Nachträge
  desselben Beitrags nicht mehr auf; ein gescheitertes Entsperren nach dem Übernehmen wird nachgetragen.
- Kennwörter mit Zeilenumbruch im Schlüsselbund werden richtig gelesen (`security` gibt sie hexkodiert aus).

## v0.1 – 13.10.2026

Erste Version für die Erprobung in den Mischräumen. Paket: `Ottilie-0.1-apple-silicon.dmg` (nur Apple Silicon,
macOS 26), ad hoc signiert, mit ffmpeg 9.0.2, bmx 1.7.0 und mediainfo.

- **Holen** aus Helmut: Session aus Template, AAF-Import mit Triage (O-Ton, Atmo, Musik auf die Zielspuren), jeder
  Clip nachgeprüft, Referenzvideo; auch in die offene Session.
- **Zurückliefern**: konsolidieren, Lautheit nach EBU R 128 (−23 LUFS, True Peak −3 dBTP, 24 bit / 48 kHz), WAVs an
  Helmut, Zustand „ausgeliefert“.
- **Stand öffnen** und **Sicherung öffnen** als Kopie; Freigeben, Duplizieren.
- **Havarie** ohne Helmut: Paket öffnen, Sendefile nach ARD-ZDF-Profil bauen und prüfen, an lokale Ziele liefern,
  Nachtrag in Helmut.
- Nutzer, Räume, Templates und Spurprofile je Nutzer; Einstellungen in Schichten mit Geräteverwaltung (MDM).
- PunchBuddy-Funktionen (Punch-In, Transport, Exporte, Stream Deck, Webtrigger), Schnittstelle über Unix-Socket und
  MCP-Server.
- Kennung `de.ottilie.app` – wer das Testpaket vom 12.10.2026 (alte Kennung) installiert hatte, erteilt die
  macOS-Rechte einmal neu und stellt das Konfigurationsprofil auf die neue Domäne.
- „Missing AAX Plugins“ beim Öffnen bestätigt Ottilie selbst und nennt die fehlenden Plugins als Hinweis.
