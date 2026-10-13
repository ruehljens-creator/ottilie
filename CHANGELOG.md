# Versionen

Je ausgeliefertem Paket gibt es einen Git-Tag `v<Version>` und ein Release mit dem DMG. Die Versionsnummer steht nur
in `tonmischung/__init__.py`. v0.x heißt: in der Erprobung, gegen Pro Tools geprüft, gegen Helmut bisher nur gegen
den Mock.

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
