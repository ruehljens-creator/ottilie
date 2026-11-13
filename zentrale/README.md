# Ottilie-Zentrale

Kleiner Server für die Ottilies eines Hauses: Räume und Geräte, Raumstatus, Chat, atomares Belegen, zentrale
Einstellungen, Beobachtung der Übergabeordner. Kennt nur Räume, keine Personen. Seit Ottilie 0.3 legt die Zentrale
Raum, Betriebsart und Übergabeordner jedes Macs fest; fällt sie aus, arbeitet Ottilie mit dem zuletzt bekannten Stand
weiter (Sperrdatei statt Belegen, kein Chat, kein Raumstatus). Pläne: „Zentrale“ und „nur Zentrale“ (interne Unterlagen).

**Reihenfolge beim Umstieg auf Ottilie 0.3:** erst die Zentrale aktualisieren, Räume anlegen, Betriebsart und
Übergabeordner setzen – dann die Macs. Eine neue Ottilie an einer alten Zentrale kann sich nur mit Kopplungscode
koppeln (Anmelden ohne Code kennt die alte Zentrale nicht).

**Macs anmelden:** in Ottilie „Ohne Code anmelden …“ (Einstellungen → System → Zentrale); unter Räume & Geräte →
„Wartende Geräte“ den Code mit der Anzeige am Mac vergleichen und einem Raum zuordnen. Wartende verfallen 24 h nach
dem letzten Kontakt, je Adresse höchstens zwei, insgesamt höchstens 20. Der Kopplungscode bleibt als zweiter Weg.
Umziehen und Umbenennen gehen nur, wenn im Raum nichts in Arbeit ist. Ändert sich der Name oder Port der Zentrale,
müssen alle Macs neu angemeldet werden (die Kopplung gilt für genau eine Adresse).

## Betrieb mit Docker

```bash
cd zentrale
docker compose up -d --build          # Abbild bauen und starten (127.0.0.1:8470)
docker compose logs -f                # Protokoll (Fehler, Warnungen, Info; kein Debug, nie Chattext)
```

Davor gehört ein Proxy mit TLS, zum Beispiel `tailscale serve --bg --https=8443 http://127.0.0.1:8470` oder nginx
(Beispiel unter `nginx/`). Die Zentrale selbst spricht nur HTTP auf Port 8470.

Umgebungsvariablen (siehe `compose.yaml`):
- `ZENTRALE_OEFFENTLICH`: öffentliche Adresse. Ihr Pfad wird als `root_path` für den Betrieb unter einem Unterpfad
  verwendet.
- `ZENTRALE_PROXY`: Adressen, von denen `X-Forwarded-For` gilt.
- `UEBERGABEORDNER`: Pfad auf dem Host. Er wird nur lesend unter `/mnt/uebergabe` eingebunden.
- `ZENTRALE_UEBERGABE_WEITERE`: weitere Übergabeordner beobachten, `Name=/pfad;Name=/pfad` – die Namen genau wie
  unter Administration → Übergabeordner → „Weitere Übergabeordner“ (der Name ist die Kennung der Filme, z. B.
  „Schnitt Nord“ → `schnitt-nord/Film`), jeder Pfad als eigener nur lesender Eintrag unter `volumes`; danach den
  Container neu starten. Ottilie zeigt die Filme aller Ordner in einer Liste und liest einen Ordner, den die Zentrale
  nicht beobachtet, weiter selbst im normalen Takt. Weitere Ordner nur mit Anmeldung Gast oder Kerberos.

**Unterpfad** (z. B. `https://server/zentrale`): Der Proxy muss den Pfad abschneiden (nginx: `location /zentrale/ {
proxy_pass http://127.0.0.1:8470/; }`). Passt das nicht, ist die Anmeldeseite nicht erreichbar – dann im Container
`python -m app.cli oeffentliche-adresse-zuruecksetzen` und neu starten. Der Proxy muss `X-Forwarded-For` setzen bzw.
anhängen (nie ungeprüft vom Client übernehmen).

Ohne Docker auf einem Server mit Git-Zugang reicht das Repository. Für Server ohne Git baut `zentrale/paket.sh` ein
tar-Paket. Es enthält `zentrale/` und die wenigen Ottilie-Module, die das Abbild braucht.

## Erste Schritte

1. `https://<server>:8443/admin` öffnen und mit `admin` / `admin` anmelden. Danach muss sofort ein eigenes Kennwort
   mit mindestens 10 Zeichen gesetzt werden, vorher ist keine andere Seite erreichbar.
2. Unter **Räume & Geräte** Räume anlegen (Misch-, Schnitt- oder sonstiger Raum) und Schreibweisen aus der Übergabe
   eintragen, z. B. „SR4“ für Schnitt 4.
3. **Kopplungscode erzeugen** (6 Zeichen, 15 Minuten gültig, nur einmal einlösbar). In Ottilie: Einstellungen →
   System → Zentrale → Adresse und Code eingeben. Ein Administrator bestätigt einmal; danach gilt die Kopplung für
   alle Benutzer des Macs. In der Web-App wird der Code beim ersten Start abgefragt.

## Befehle im Container

```bash
docker compose exec zentrale python -m app.cli raum-anlegen "Ton 3" --art mischraum
docker compose exec zentrale python -m app.cli raeume
docker compose exec zentrale python -m app.cli code "Ton 3"
docker compose exec zentrale python -m app.cli kennwort-zuruecksetzen admin
docker compose exec zentrale python -m app.cli sicherung
```

## Daten und Sicherheit

- Alles liegt in `/data` (Volume `zentrale-daten`):
  - SQLite-Datenbank
  - Sicherungen: täglich eine, die letzten 7 bleiben
  - Wiederherstellen: die Sicherung wird beim nächsten Start eingesetzt, die bisherige Datenbank bleibt als
    Sicherung liegen.
- Geräteschlüssel und Kopplungscodes stehen nur als Hash in der Datenbank. Kennwörter sind mit scrypt
  gespeichert.
- Anmeldung der Administration:
  - 5 Fehlversuche sperren ein Konto für 5 Minuten.
  - Eine Sitzung gilt 8 h.
  - Schutz gegen fremde Formulare (CSRF).
- Gekoppelte Geräte haben keinen Zugang zur Administration.
- Protokoll in der Datenbank:
  - Aufbewahrung 1–365 Tage, Standard 30.
  - Debug lässt sich zeitweise zuschalten und schaltet sich selbst wieder ab.
  - Geheimnisse werden vor dem Schreiben geschwärzt, Chattext wird nie protokolliert.
- Chat:
  - Der Server hält eine Nachricht nur, bis sie allen Zielräumen zugestellt ist, höchstens 24 h (einstellbar).
  - Die Web-App und Ottilie halten den Chat nur im Arbeitsspeicher.

## Tests

```bash
.venv/bin/python -m pytest zentrale/tests tests/test_zentrale_client.py
```
