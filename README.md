# Ottilie

Tonmischung mit Helmut und Pro Tools – eine macOS-App für Mischräume im Fernsehen.

Ottilie holt Beiträge, die der Schnitt im Medien-Workflow-System **Helmut** (MoovIT) übergeben hat, legt in
**Pro Tools** eine Session aus einem Template an, importiert das AAF – auf Wunsch mit einer Triage, die O-Ton, Atmo
und Musik erkennt und auf die Spuren des Templates verteilt – und liefert die fertige Mischung als WAV-Dateien nach
EBU R 128 an Helmut zurück. Fällt Helmut aus, baut Ottilie das Sendefile selbst (ARD-ZDF-Profile, MXF mit
16 Monospuren), prüft es, liefert es aus und trägt in Helmut nach, sobald es wieder erreichbar ist.

Die Aufnahme- und Exportfunktionen von [PunchBuddy](https://github.com/ruehljens-creator/punchbuddy-protools)
(Punch-In, Transport, Exporte, Stream Deck, Webtrigger) sind eingebunden.

## Download

Fertiges Paket im Release [v0.1](../../releases/tag/v0.1): `Ottilie-0.1-apple-silicon.dmg` (Apple Silicon, macOS 26).
Was jede Version enthält: [CHANGELOG.md](CHANGELOG.md).

## Schnellstart

```
git clone --recurse-submodules https://github.com/ruehljens-creator/ottilie.git
cd ottilie
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements-bau.txt
.venv/bin/python -m pytest -q                  # Unit-Tests ohne Pro Tools und ohne Netz
.venv/bin/python -m tonmischung app --mock     # App mit eingebautem Helmut-Mock
```

Voraussetzungen: macOS 26, Python 3.14; für den Betrieb Pro Tools mit PTSL. Ohne Pro Tools zeigt die App den
Verbindungsfehler, alles andere lässt sich trotzdem ansehen.

## Unterlagen

| Datei | Inhalt |
|---|---|
| [docs/CODE.md](docs/CODE.md) | Aufbau des Codes: Schichten, Abläufe, Regeln, Tests – **Einstieg für ein Code-Review** |
| [docs/API.md](docs/API.md) | Schnittstelle der App (Unix-Socket, MCP), aus dem Code erzeugt |
| [docs/Ottilie_an_Helmut.html](docs/Ottilie_an_Helmut.html) | Alle Aufrufe an Helmut mit Ablaufdiagramm und offenen Annahmen |
| [docs/MDM.md](docs/MDM.md) | Schlüssel für das Konfigurationsprofil der Geräteverwaltung |
| [docs/VERTEILUNG.md](docs/VERTEILUNG.md), [docs/BAU_APPLE_SILICON.md](docs/BAU_APPLE_SILICON.md) | App-Paket bauen und verteilen |
| [docs/LIZENZEN.md](docs/LIZENZEN.md) | Mitgelieferte Software mit Lizenzen |

## Stand

Version 0.1, in der Erprobung. Gegen Pro Tools 2026 live geprüft; Helmut bisher nur gegen den Mock nach der
offiziellen OpenAPI. Was gegen die echte Helmut-Instanz noch zu prüfen ist, steht in
`docs/Ottilie_an_Helmut.html`.

## Lizenz

Keine freie Lizenz: Der Code ist zum Lesen und für ein Code-Review veröffentlicht, alle Rechte liegen beim
Entwickler. Verwenden, weitergeben oder ändern nur nach Absprache. Mitgelieferte Software Dritter steht unter ihren
eigenen Lizenzen ([docs/LIZENZEN.md](docs/LIZENZEN.md)); PunchBuddy (Submodul) steht unter MIT.

Entwickler: Jens Rühl
