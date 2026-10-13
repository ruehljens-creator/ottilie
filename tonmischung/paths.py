"""Name und Ablageorte der App.

Jens 08.10.2026: „Die App soll ab jetzt nicht mehr Tonmischung heißen, sondern Ottilie, und das Bild ist das Logo.“
Alles, was Nutzer, Geräteverwaltung und das System sehen, heißt Ottilie (Bundle-ID, Datenordner, Protokolle, Bonjour,
Schlüsselbund). Der Code-Name `tonmischung` bleibt intern (Python-Paket, `python3 -m tonmischung`, Formatkennungen
in Dateien).

Beim ersten Start unter dem neuen Namen kopiert `migrate()` die Daten aus dem alten Ordner – der alte bleibt
unverändert.
"""
from __future__ import annotations

import fcntl
import os
import shutil
from pathlib import Path

APP_NAME = "Ottilie"
LEGACY_NAME = "Tonmischung"
BUNDLE_ID = "de.ottilie.app"
SKIP = shutil.ignore_patterns("*.sock", "*.tmp", "*.lock")   # Sockets und Zwischendateien gehören zum laufenden Prozess
LAST = ""                                       # Meldung der letzten Übernahme (fürs App-Protokoll)


def support_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "Application Support" / APP_NAME


def legacy_support_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "Application Support" / LEGACY_NAME


def log_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "Logs" / APP_NAME


def migrate(home: Path | None = None) -> str:
    """Einmalig: „Application Support/Tonmischung“ nach „…/Ottilie“ kopieren, solange es den neuen Ordner nicht gibt.

    Nutzer, Einstellungen, Räume, Templates, Warteschlangen – alles kommt mit; der alte Ordner bleibt, wie er ist.
    Rückgabe: Meldung fürs Protokoll oder "" (nichts zu tun). Scheitert die Kopie, startet Ottilie trotzdem und meldet
    es (Review B10: vorher startete die App nicht, und launchd startete sie im Kreis). Starten App und MCP-Server
    gleichzeitig, kopiert nur einer (Dateisperre)."""
    global LAST
    new, old = support_dir(home), legacy_support_dir(home)
    if new.exists() or not old.is_dir():
        return ""
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        with open(new.parent / f".{APP_NAME}-umzug.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if new.exists():                        # der andere Prozess war schneller
                return ""
            tmp = new.with_name(new.name + ".kopie")
            if tmp.exists():
                shutil.rmtree(tmp)                  # eigene, abgebrochene Kopie eines früheren Starts
            shutil.copytree(old, tmp, symlinks=True, ignore=SKIP)
            os.replace(tmp, new)                    # erst fertig kopiert, dann unter dem richtigen Namen
    except Exception as e:  # noqa: BLE001 – die App muss trotzdem starten
        LAST = (f"Daten aus „{old}“ nicht übernommen ({type(e).__name__}: {e}) – der alte Ordner bleibt unverändert; "
                "bitte melden")
        return LAST
    LAST = f"Daten aus „{old}“ nach „{new}“ übernommen (Kopie – der alte Ordner bleibt unverändert)"
    return LAST
