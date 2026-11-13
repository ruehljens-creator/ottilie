"""Befehle im Container (Betrieb ohne Browser):

    python -m app.cli raum-anlegen "Ton 3" --art mischraum
    python -m app.cli raeume
    python -m app.cli code "Ton 3"                 # Kopplungscode (15 min, einmal)
    python -m app.cli kennwort-zuruecksetzen [admin]
    python -m app.cli sicherung

Die Befehle arbeiten direkt auf /data/zentrale.db (oder ZENTRALE_DATEN). Kopplungscodes und Kennwörter erscheinen nur
auf dem Bildschirm, nie im Protokoll.
"""
from __future__ import annotations

import argparse
import os
import sys

from app.auth import Auth, AuthError
from app.backup import Backups
from app.db import ROOM_KINDS, Database
from app.log import Log


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    p.add_argument("--daten", default=os.environ.get("ZENTRALE_DATEN", "/data"))
    sub = p.add_subparsers(dest="befehl", required=True)
    r = sub.add_parser("raum-anlegen")
    r.add_argument("name")
    r.add_argument("--art", choices=ROOM_KINDS, default="mischraum")
    sub.add_parser("raeume")
    c = sub.add_parser("code")
    c.add_argument("raum")
    k = sub.add_parser("kennwort-zuruecksetzen")
    k.add_argument("name", nargs="?", default="admin")
    sub.add_parser("sicherung")
    sub.add_parser("oeffentliche-adresse-zuruecksetzen",
                   help="Unterpfad aus der Administration entfernen (falls die Anmeldeseite nicht mehr erreichbar ist)")
    a = p.parse_args(argv)
    os.makedirs(a.daten, exist_ok=True)
    db = Database(os.path.join(a.daten, "zentrale.db"))
    log, auth = Log(db), None
    auth = Auth(db)
    try:
        if a.befehl == "raum-anlegen":
            auth.add_room(a.name, a.art)
            log.info("raeume", f"Raum angelegt: {a.name} ({a.art}) – Befehl im Container")
            print(f"Raum „{a.name}“ angelegt.")
        elif a.befehl == "raeume":
            for room in auth.rooms():
                print(f"{room['name']}\t{room['kind']}\t{', '.join(room['aliases'])}")
        elif a.befehl == "code":
            rid = auth.room_id(a.raum)
            if rid is None:
                print(f"Raum „{a.raum}“ gibt es nicht (python -m app.cli raeume).", file=sys.stderr)
                return 1
            code = auth.pairing_code(rid)
            log.info("kopplung", "Neuer Code zum Koppeln – Befehl im Container", room=a.raum)
            print(f"Kopplungscode für „{a.raum}“: {code}  (gilt 15 Minuten, einmal)")
        elif a.befehl == "kennwort-zuruecksetzen":
            temp = auth.reset_password(a.name)
            log.info("admin", f"Kennwort zurückgesetzt: {a.name} – Befehl im Container")
            print(f"Einmal-Kennwort für „{a.name}“: {temp}  (beim Anmelden sofort ändern)")
        elif a.befehl == "oeffentliche-adresse-zuruecksetzen":
            db.set_setting("server.oeffentliche_adresse", "")
            log.info("server", "Öffentliche Adresse zurückgesetzt – Befehl im Container")
            print("Öffentliche Adresse zurückgesetzt – Zentrale neu starten (docker compose restart).")
        elif a.befehl == "sicherung":
            print(Backups(db, os.path.join(a.daten, "sicherungen"), log).create())
    except AuthError as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
