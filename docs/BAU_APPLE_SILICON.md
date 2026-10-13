# Universal-Paket bauen (Apple Silicon + Intel)

Stand 06.10.2026. Anlass: Jens: „Wir stellen mit Einführung von Helmut auf Silicon-Macs um.“ Ziel ist **ein**
Paket, das auf beiden Mac-Arten nativ läuft (ohne Rosetta). Ältere Macs als das Entwicklungs-MacBook-Pro (2019,
macOS 26) werden nicht berücksichtigt; das Paket verlangt macOS 26.

Gebaut wird je Architektur auf einem passenden Mac; danach werden beide Hälften mit `lipo` zu einer App vereint
(Vorbild: PunchBuddy `build_dmg.sh` + `merge_universal.py`). Beide Hälften müssen aus **demselben Commit** und
derselben Paketliste stammen – das Zusammenfügen prüft das (Bauangabe im Paket) und verweigert sonst.

## Einmal einrichten (Apple-Silicon-Mac)

Gebaut wird auf dem Mac mini, auf dem schon PunchBuddy für Apple Silicon kompiliert wurde (Jens 08.10.2026).
`tools/pruefe_baumac.sh` zeigt, was dort noch fehlt (prüft nur, ändert nichts).

1. Xcode-Befehlszeilenwerkzeuge: `xcode-select --install`
2. Homebrew (legt sich unter `/opt/homebrew` an), dann die Werkzeuge:
   `brew install ffmpeg mediainfo cmake uriparser pkgconf` (ohne `pkgconf` scheitert der bmx-Bau an uriparser)
3. Python 3.14 vom python.org-Installer („macOS 64-bit universal2 installer“) – dasselbe wie auf dem Intel-Mac.
   Nur für ein reines Apple-Silicon-Paket genügt Python 3.14 aus Homebrew (`brew install python@3.14`, kein
   Kennwort nötig): `PYTHON_BASIS=/opt/homebrew/bin/python3.14 tools/venv_bau.sh`. So entstand das erste
   Apple-Silicon-DMG am 12.10.2026.
4. Projekt holen (mit Unterprojekten):
   `git clone --recurse-submodules https://github.com/ruehljens-creator/ottilie.git`
5. Im Projektordner:
   - `tools/venv_bau.sh` – Bauumgebung `build/venv-arm64` mit genau den Versionen aus `tools/requirements-bau.txt`
   - `tools/build_bmx.sh` – bmx v1.7 aus dem Git der EBU nach `build/bmx-arm64`
   - `build/venv-arm64/bin/python -m pytest -q` – alle Tests grün?

## Bauen

Über SSH (z. B. via Tailscale) fehlt Homebrew im Suchpfad: vorher `eval "$(/opt/homebrew/bin/brew shellenv)"`.

**Nur Apple Silicon** (auf dem Silicon-Mac): `tools/build_dmg.sh` – baut `dist/Ottilie.app` und
`dist/Ottilie-<Version>-apple-silicon.dmg`.

**Intel-Teil** (auf dem Intel-Mac, aus dem Commit, der auch auf dem Silicon-Mac ausgecheckt ist):

```
tools/venv_bau.sh          # einmal bzw. nach Änderung der Paketliste
tools/build_bmx.sh         # einmal
tools/build_app.sh dist/x86_64
ditto -c -k --keepParent dist/x86_64/Ottilie.app Ottilie-x86_64.zip
```

Das Zip auf den Silicon-Mac bringen (Stick, Netzlaufwerk) und dort auspacken, z. B. nach `dist/x86_64/`.

**Silicon-Teil und Zusammenfügen** (auf dem Silicon-Mac):

```
tools/build_universal.sh dist/x86_64/Ottilie.app
tools/build_dmg.sh --ohne-bau
```

`build_universal.sh` baut `dist/arm64/Ottilie.app`, fügt beide zu `dist/Ottilie.app` zusammen, signiert
neu (ad hoc) und lässt den Selbsttest zweimal laufen: nativ (Apple Silicon) und – wenn Rosetta da ist – als
Intel-Code. Ergebnis: `dist/Ottilie-<Version>-universal.dmg`.

Alternative ohne Intel-Mac: `tools/build_universal.sh --intel-hier` baut den Intel-Teil auf dem Silicon-Mac unter
Rosetta. Dafür braucht es zusätzlich Intel-Homebrew unter `/usr/local` (`arch -x86_64 /bin/bash` und dort den
Homebrew-Installer), `arch -x86_64 tools/venv_bau.sh` und `arch -x86_64 tools/build_bmx.sh`.

## Prüfen

Auf jedem Mac, auf dem das Paket laufen soll (auch später bei der Verteilung):

```
/Applications/Ottilie.app/Contents/MacOS/Ottilie selbsttest
```

Prüft Architektur (nativ oder Rosetta), macOS-Mindestversion, Python-Module mit eigenem Maschinencode (kurz benutzt:
Rechnen, Filter, WAV schreiben/lesen, Lautheit, PTSL, Fenster), alle mitgelieferten Werkzeuge (einmal gestartet,
passende Architektur) und deren Bibliotheken. Pro Tools und Helmut fasst er nicht an. Exit 0 = alles in Ordnung.

## Bausteine

| Datei | Zweck |
|---|---|
| `tools/pruefe_baumac.sh` | ist der Mac bereit zum Bauen? (Werkzeuge, Python, Homebrew, Platz) |
| `tools/requirements-bau.txt` | feste Paketversionen (nur was Ottilie braucht; alle gibt es fertig für arm64) |
| `tools/lock_requirements.py` | Liste neu schreiben bzw. `--pruefen`; ein Test meldet, wenn sie veraltet ist |
| `tools/venv_bau.sh` | Bauumgebung `build/venv-<Architektur>` |
| `tools/build_bmx.sh` | bmx v1.7 (https://github.com/ebu/bmx) nach `build/bmx-<Architektur>` |
| `tools/build_app.sh` | eine Architektur bauen, mit Bauangabe und Selbsttest |
| `tools/merge_universal.py` | zwei Bauten vereinen, Lizenzliste zusammenführen, neu signieren |
| `tools/build_universal.sh` | alles zusammen auf dem Silicon-Mac |
| `tools/build_dmg.sh` | DMG, Name mit Architektur |

## Gut zu wissen

- **macOS 26:** Homebrew liefert Bibliotheken für die macOS-Version des bauenden Macs (openssl, xz, libvmaf: 26.0);
  numpy/scipy verlangen 14.0. Der Bau ermittelt die höchste Anforderung und trägt sie als `LSMinimumSystemVersion`
  ein; das „Lies mich“ im DMG nennt sie.
- **Unterschiede zwischen den Hälften:** scipy/numpy rechnen auf Intel mit OpenBLAS, auf Apple Silicon mit
  Accelerate – Dateien, die es nur im Intel-Bau gibt, kommen mit ins Paket. Haben die Homebrew-Werkzeuge auf beiden
  Macs verschiedene Versionen, nennt die Lizenzliste beide.
- **Signatur:** bis zum Zertifikat des Hauses ad hoc; auf Apple Silicon startet Code nur signiert – deshalb signiert
  das Zusammenfügen jede Datei mit Maschinencode neu.
