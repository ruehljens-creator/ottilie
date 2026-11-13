"""Gemeinsamer Datenordner aller Benutzer eines Macs (Auftrag „rechnerweit“, Jens 13.11.2026).

Bleibt bei Benutzer A ein Eintrag im Nachtrag hängen, muss Benutzer B ihn sehen und nachtragen können. Darum liegen
Nachtrag, Mischungsverzeichnis und Havarie-Pakete nicht mehr im Benutzerordner, sondern einmal je Mac:

- Ort: `/Users/Shared/Ottilie` – ohne Administrator anlegbar (/Users/Shared ist für alle beschreibbar), Gruppe `staff`
  (alle lokalen Benutzer), Ordner 2775, Dateien 0664; nicht weltweit beschreibbar. Ein Pfad in der Einstellung
  `system.raeume_nutzer.datenordner` (Geräteverwaltung oder lokal) geht vor – bewusst nicht über die Zentrale: eine
  gekaperte Zentrale könnte sonst die Daten des Macs in einen Ordner ihrer Wahl lenken.
- Gleichzeitigkeit (schnelle Benutzerumschaltung): jede Datei mit Dateisperre (`fileio.file_lock`) und atomarem
  Schreiben; die Kennungen der Nachtrag-Einträge bleiben eindeutig.
- Umzug beim ersten Start je Benutzer: vorhandene Einträge aus dem Benutzerordner werden zusammengeführt (keine
  Doppelten, nichts verloren), die alte Datei heißt danach „…migriert-<Zeit>“ – gelöscht wird nichts.
- Nicht nutzbar (Rechte, Pfad): Ottilie bleibt beim Benutzerordner und sagt es (`location` liefert den Hinweis).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from tonmischung.core.fileio import (SHARED_FILE_MODE, CorruptFileError, ensure_shared_dir, file_lock, read_json,
                                     write_json)

log = logging.getLogger(__name__)

DEFAULT = "/Users/Shared/Ottilie"
SETTING = "system.raeume_nutzer.datenordner"
CATCH_UP, MIXES, PACKAGES = "nachtrag.json", "mischungen.json", "havarie-pakete.json"


def location(override: str, fallback: Path) -> tuple[Path, str]:
    """(Ordner, Hinweis) – der gemeinsame Ordner, sonst der Benutzerordner mit Hinweis fürs Fenster."""
    folder = Path(os.path.expanduser(override.strip())) if override and override.strip() else Path(DEFAULT)
    try:
        return ensure_shared_dir(folder), ""
    except OSError as e:
        hint = (f"Gemeinsamer Datenordner {folder} nicht nutzbar ({e.strerror or e}) – Nachtrag, Mischungen und "
                "Havarie-Pakete gelten vorerst nur für diesen Benutzer")
        log.warning("%s", hint)
        return fallback, hint


def lock_for(path: Path) -> Path:
    """Sperrdatei, unter der die jeweilige Ablage ändert (dieselbe wie in `CatchUpQueue`, `MixLog`, `PackageIndex`)."""
    return path.with_name(f"{path.stem}.aendern.lock" if path.name == CATCH_UP else f"{path.stem}.lock")


# ── Zusammenführen ─────────────────────────────────────────────────────────────
def merge_catch_up(shared: dict | None, old: dict | None, user: str = "") -> dict:
    """Nachtrag: Einträge beider Dateien, je Kennung einmal, in der Reihenfolge ihres Entstehens. Einträge aus dem
    Benutzerordner (bis 0.2) bekommen `user` als Urheber – sie stammen sicher von ihm (Nachprüfung 13.11.2026: sonst
    trug ein anderer Benutzer eine alte Fertigmeldung mit seinen Einstellungen nach)."""
    seen: dict[str, dict] = {}
    own = [{**e, "benutzer": e.get("benutzer") or user} if isinstance(e, dict) and user else e
           for e in (old or {}).get("eintraege", [])]
    for e in list((shared or {}).get("eintraege", [])) + own:
        if isinstance(e, dict) and e.get("id") and e["id"] not in seen:
            seen[e["id"]] = e
    return {"version": 1, "eintraege": sorted(seen.values(), key=lambda e: str(e.get("created") or ""))}


def merge_mixes(shared: dict | None, old: dict | None, keep: int = 2000) -> dict:
    """Mischungsverzeichnis: beide Listen ohne Doppelte, nach Zeit geordnet, höchstens `keep` (die neuesten)."""
    out: list[dict] = []
    for e in list((shared or {}).get("eintraege", [])) + list((old or {}).get("eintraege", [])):
        if isinstance(e, dict) and e not in out:
            out.append(e)
    out.sort(key=lambda e: str(e.get("zeit") or ""))
    return {"version": 1, "eintraege": out[-keep:]}


def merge_packages(shared: dict | None, old: dict | None) -> dict:
    """Havarie-Pakete (Session → Paketordner): beide; bei gleicher Session gilt der gemeinsame Stand."""
    return {**(old or {}), **(shared or {})}


def _merge_catch_up_own(shared: Any, old: Any) -> dict:
    from tonmischung.core.catchup import current_user
    return merge_catch_up(shared, old, current_user())


MERGE: dict[str, Callable[[Any, Any], dict]] = {CATCH_UP: _merge_catch_up_own, MIXES: merge_mixes,
                                                PACKAGES: merge_packages}


def migrate(old_dir: Path, new_dir: Path, now: Callable[[], datetime] = datetime.now) -> list[str]:
    """Dateien aus dem Benutzerordner in den gemeinsamen übernehmen → Meldungen (leer: nichts zu tun)."""
    notes: list[str] = []
    if old_dir.resolve() == new_dir.resolve():
        return notes
    stamp = now().strftime('%Y%m%d-%H%M%S')
    for name, merge in MERGE.items():
        old, new = old_dir / name, new_dir / name
        if not old.is_file():
            continue
        moved = old.with_name(f"{old.name}.migriert-{stamp}")
        try:
            with file_lock(lock_for(new)):
                try:
                    shared = read_json(new, expect=dict, shared=True)
                except CorruptFileError:
                    notes.append(f"{new} beschädigt – {old.name} bleibt im Benutzerordner, bitte prüfen")
                    continue
                # Erst umbenennen, dann zusammenführen (Ausfall 13.11.2026): bricht es dazwischen ab, wird beim
                # nächsten Start nichts ein zweites Mal übernommen – sonst kämen inzwischen erledigte und gekürzte
                # Einträge als offen zurück. Die Datei bleibt unter dem neuen Namen erhalten.
                os.rename(old, moved)                       # nie löschen – nur umbenennen
                try:
                    write_json(new, merge(shared, read_json(moved, expect=dict, shared=True)),
                               mode=SHARED_FILE_MODE)
                except BaseException:
                    os.rename(moved, old)                   # nichts übernommen: alte Datei wieder an ihren Platz
                    raise
            notes.append(f"{old.name} in den gemeinsamen Datenordner übernommen (bisherige Datei: {moved.name})")
        except (CorruptFileError, OSError) as e:
            notes.append(f"{old.name} nicht übernommen ({e}) – bleibt im Benutzerordner")
    for n in notes:
        log.info("%s", n)
    return notes
