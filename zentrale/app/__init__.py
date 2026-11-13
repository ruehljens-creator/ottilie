"""Ottilie-Zentrale: Räume, Geräte, Raumstatus, Chat und Administration für die Ottilies eines Hauses.

Läuft als eigener Dienst (Docker, Port 8470, ohne TLS – davor ein Proxy). Kennt nur Räume, keine Personen. Seit
Ottilie 0.3 legt die Zentrale Raum, Betriebsart und Übergabeordner fest; ohne Verbindung arbeitet Ottilie mit dem
zuletzt bekannten Stand weiter (Plan „Zentrale“, Plan „nur Zentrale“).
"""

VERSION = "0.2"
