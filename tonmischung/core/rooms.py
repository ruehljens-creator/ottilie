"""Räume über Mac-Namen (Plan Nutzer & Räume, Schritt 4; Jens 04.10.2026: „Die Räume definieren
sich über die Mac-Namen. Das muss in den Einstellungen zuzuordnen sein.“).

Die Raumliste liegt neben den Nutzern in der Ablage (`<Ablage>/raeume.json`) – lokal je Mac oder im gemeinsamen
Ordner, dann für alle Räume gleich. Sie füllt die Einstellung `system.raeume_nutzer.raeume` (Ebene „zentral“), die
Raumerkennung, Raumstatus und Webtrigger schon lesen. Reine Logik plus Datei lesen/schreiben.
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence

from tonmischung.core.fileio import CorruptFileError, read_json, write_json
from tonmischung.core.identity import short_host
from tonmischung.core.users import slug

FILE = "raeume.json"


class RoomsUnreadable(OSError):
    """Raumliste da, aber nicht lesbar (beschädigt, Rechte, Netz) – dann nie mit einer anderen Liste überschreiben."""


def load(root: str) -> list[dict] | None:
    """Raumliste der Ablage oder None, wenn es dort (noch) keine gibt. Beschädigt oder unlesbar → RoomsUnreadable
    (Review A5: vorher galt das wie „gibt es nicht“, und die lokale Liste überschrieb die gemeinsame aller Räume)."""
    path = os.path.join(root, FILE)
    try:
        data = read_json(path)
    except (CorruptFileError, OSError) as e:
        raise RoomsUnreadable(f"Raumliste {path} nicht lesbar: {e}") from e
    if data is None:
        return None
    rows = data.get("raeume") if isinstance(data, Mapping) else data
    if not isinstance(rows, list):
        raise RoomsUnreadable(f"Raumliste {path}: unerwartete Form")
    return [dict(r) for r in rows if isinstance(r, Mapping)]


def save(root: str, rows: Sequence[Mapping]) -> None:
    write_json(os.path.join(root, FILE), {"format": "tonmischung-raeume", "version": 1, "raeume": list(rows)})


def normalize(rows: Iterable[Mapping]) -> tuple[list[dict], list[str]]:
    """Leere Zeilen weg, Namen/Mac trimmen, ID aus dem Namen; Fehler: doppelte Namen oder Macs, Raum ohne Mac."""
    out: list[dict] = []
    errors: list[str] = []
    names: dict[str, str] = {}
    macs: dict[str, str] = {}
    for r in rows:
        name = " ".join(str(r.get("name") or "").split())
        mac = short_host(str(r.get("rechnername") or "").strip())
        code = str(r.get("id_code") or "").strip()
        if not name and not mac:
            continue
        if not name:
            errors.append(f"Mac „{mac}“: Raumname fehlt")
            continue
        if not mac:
            errors.append(f"„{name}“: Mac wählen")
        if name.lower() in names:
            errors.append(f"Raum „{name}“ steht doppelt in der Liste")
        if mac and mac in macs:
            errors.append(f"Mac „{mac}“ ist schon „{macs[mac]}“ zugeordnet")
        names[name.lower()] = name
        if mac:
            macs[mac] = name
        row = {"id": str(r.get("id") or "") or slug(name), "name": name, "rechnername": mac}
        if code:
            row["id_code"] = code
        out.append(row)
    return out, errors


def known_macs(this_host: str, peer_hosts: Iterable[str], rows: Iterable[Mapping]) -> list[str]:
    """Mac-Namen zur Auswahl: dieser Mac, die per Bonjour gefundenen, die schon zugeordneten."""
    found = {short_host(h) for h in peer_hosts if h} | {short_host(str(r.get("rechnername") or ""))
                                                        for r in rows if r.get("rechnername")}
    me = short_host(this_host)
    return [me] + sorted(found - {me, ""})
