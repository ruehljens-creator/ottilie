"""Start im Container: vorgemerkte Wiederherstellung einsetzen, dann uvicorn auf 0.0.0.0:8470 (im Container;
nach außen veröffentlicht compose nur 127.0.0.1:8470).

Umgebung: ZENTRALE_DATEN (/data), ZENTRALE_PORT (8470), ZENTRALE_PROXY (vertrauenswürdige Proxy-Adressen für
X-Forwarded-For, Standard 127.0.0.1), ZENTRALE_OEFFENTLICH (öffentliche Adresse; ihr Pfad wird root_path –
sonst gilt der Wert aus der Administration).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.parse

import uvicorn

from app.backup import prepare_restore

SAFE_PATH = re.compile(r"/[A-Za-z0-9._~/-]*")          # Unterpfad: nur harmlose Zeichen (Sicherheit: XSS)


def _root_path(data_dir: str) -> str:
    url = os.environ.get("ZENTRALE_OEFFENTLICH", "")
    if not url:
        try:
            con = sqlite3.connect(os.path.join(data_dir, "zentrale.db"))
            row = con.execute("SELECT v FROM settings WHERE k='server.oeffentliche_adresse'").fetchone()
            con.close()
            url = str(json.loads(row[0]) or "") if row else ""
        except (sqlite3.Error, ValueError):
            url = ""
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    return path if SAFE_PATH.fullmatch(path or "-") else ""


def main() -> None:
    data = os.environ.get("ZENTRALE_DATEN", "/data")
    os.makedirs(data, exist_ok=True)
    if prepare_restore(data):
        print("Vorgemerkte Sicherung eingesetzt; die bisherige Datenbank liegt unter sicherungen/.", flush=True)
    from app.main import create_app
    uvicorn.run(create_app(data), host=os.environ.get("ZENTRALE_HOST", "0.0.0.0"),
                port=int(os.environ.get("ZENTRALE_PORT", "8470")), root_path=_root_path(data),
                proxy_headers=True, forwarded_allow_ips=os.environ.get("ZENTRALE_PROXY", "127.0.0.1"),
                ws="websockets-sansio", ws_ping_interval=30, ws_ping_timeout=30, log_level="warning",
                access_log=False)


if __name__ == "__main__":
    main()
