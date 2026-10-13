# Verteilung von Ottilie (Auftrag M9)

Stand 13.10.2026. Was gebaut ist und was noch fehlt.

## Paket bauen

```
tools/venv_bau.sh     # einmal: Bauumgebung mit festen Paketversionen (tools/requirements-bau.txt)
tools/build_bmx.sh    # einmal: bmx v1.7 aus dem Git der EBU
tools/build_app.sh
```

- Eine Architektur (die des bauenden Macs). **Universal-Paket** für Apple Silicon und Intel:
  `docs/BAU_APPLE_SILICON.md` (`tools/build_universal.sh` auf dem Silicon-Mac).
- Bündelt zuerst ffmpeg, ffprobe, mediainfo, bmxtranswrap und mxf2raw samt Bibliotheken (`tools/bundle_tools.py`,
  aus den Homebrew-Programmen des bauenden Macs; bmx aus `build/bmx-<Architektur>`).
- Baut `dist/Ottilie.app` mit PyInstaller (`tools/Ottilie.spec`), Bundle-ID `de.ottilie.app`.
- Trägt die macOS-Mindestversion ein, die die mitgelieferten Programme verlangen (heute 26.0), und eine Bauangabe
  (Stand, Paketliste, Architektur).
- Prüft am Ende: `Ottilie version` und `Ottilie selbsttest` (Architektur, Python-Module, Werkzeuge).
- Ein vorhandenes Paket wird beiseitegelegt (`Ottilie-vorher-<Zeit>.app`), nie überschrieben.

## DMG bauen

```
tools/build_dmg.sh            # baut die App neu und packt sie
tools/build_dmg.sh --ohne-bau # packt das vorhandene dist/Ottilie.app
```

Ergebnis `dist/Ottilie-<Version>-<universal|apple-silicon|intel>.dmg` (Stand 04.10.2026: 117 MB, Intel) mit allem,
was zum Betrieb nötig ist:

- `Ottilie.app` – Werkzeuge (ffmpeg, ffprobe, mediainfo, bmx) samt Bibliotheken und Lizenztexten
  (`Contents/Resources/bin/licenses`, mit `manifest.json`: Version, Lizenz, Quelltext je Bestandteil), Schrift IBM Plex,
  Logo, Spurdaten-Preset. Außer Pro Tools muss nichts installiert werden.
- `Programme` (Verknüpfung zum Hineinziehen), `Lies mich.txt` (Installation, erster Start, Freigaben, Anmeldung),
  `Lizenzen.md` (mitgelieferte Software, wie „Über Ottilie“).
- `Für die IT/`: Schlüssel und Vorlage (plist) des Konfigurationsprofils, LaunchAgent `de.ottilie.app.plist`, diese
  Anleitung.

Ein vorhandenes DMG gleichen Namens wird beiseitegelegt (`…-vorher-<Zeit>.dmg`), nie überschrieben.

## Einrichten über die Geräteverwaltung (MDM)

1. **Konfigurationsprofil:** Schlüssel und Vorlage siehe `docs/MDM.md` (`Ottilie mdm-doku --plist`).
2. **Zugangsdaten:** nur im Schlüsselbund (Eintrag `ottilie-helmut` u. a.), nie im Profil.
3. **Start bei der Anmeldung:** `Ottilie launchagent > de.ottilie.app.plist`, nach
   `/Library/LaunchAgents/` verteilen. Startet die App bei der Anmeldung; nach einem Absturz startet launchd sie neu,
   bewusstes Beenden bleibt beendet.
4. **Datenschutz-Freigaben (PPPC):** Automation „System Events“ (Dialog „Missing Files“ in Pro Tools), Bedienungshilfen
   für PunchBuddys Tastenkürzel, lokales Netzwerk (Bonjour-Raumstatus).

## Offen

- **Signatur und Notarisierung** mit dem festen Zertifikat (Auftrag M9) – das Paket ist bisher nur ad hoc signiert.
- **Universal-Paket:** Das Apple-Silicon-Paket ist gebaut (12.10.2026, `docs/BAU_APPLE_SILICON.md`). Für ein Paket
  für beide Mac-Arten braucht es auf beiden Macs Python vom python.org-Installer.
- **Installer-Paket (.pkg)** für die Geräteverwaltung mit App, LaunchAgent und Freigaben.
- **Mindestversion aus Helmut:** die App soll melden, wenn Helmut eine neuere Version verlangt.
