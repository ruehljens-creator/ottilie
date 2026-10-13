"""Webtrigger-Adressen je Platz zum Kopieren – reine Funktionen (Prototyp „Stream Deck & Webtrigger“).

Die Liste entsteht aus der Routing-Tabelle des Webtriggers (keine zweite Liste zum Pflegen), dazu je
Spur-Preset `/preset/<n>`. Mit ID-Code hängt jede Adresse `?id=…` an, mit Token `token=…`.
"""
from __future__ import annotations

import random
import re
import socket
from typing import Any
from urllib.parse import urlencode

from tonmischung.punch.triggers import LABELS, PATH_CMD, VOC_CMD

P = "system.streamdeck_webtrigger."
ROOMS = "system.raeume_nutzer.raeume"


def rooms(settings: Any) -> list[dict]:
    return [dict(r) for r in settings.get(ROOMS) or [] if r.get("name")]


def addresses(settings: Any, room: str, token: str = "", this_host: str | None = None) -> dict[str, Any]:
    """Adressen für den Platz `room`. Host = Rechnername des Raums (bzw. dieses Macs), oder 127.0.0.1."""
    entry = next((r for r in rooms(settings) if r["name"] == room), {})
    if settings.get(P + "adresse_in_url") == "127.0.0.1":
        host = "127.0.0.1"
    else:
        host = (entry.get("rechnername") or this_host or socket.gethostname().split(".")[0]).strip()
    port = int(settings.get(P + "port") or 8899)
    id_code = (entry.get("id_code") or "").strip()
    query = {k: v for k, v in (("id", id_code), ("token", token)) if v}
    suffix = ("?" + urlencode(query)) if query else ""
    base = f"http://{host}:{port}"
    paths = [p for p in PATH_CMD if p != "/export_aaf_embedded"] + ["/status", "/commands"]
    rows = [{"label": LABELS.get(p, p), "url": base + p + suffix} for p in paths]
    for i, preset in enumerate(settings.get("aufnahme.presets") or [], start=1):
        rows.append({"label": f"Preset {i} · {preset.get('name', '')}", "url": f"{base}/preset/{i}{suffix}"})
    rows += [{"label": LABELS[p], "url": base + p + suffix} for p in VOC_CMD]
    note = ("mit ID-Code" if id_code else
            ("ohne Code nur von diesem Mac auslösbar" if settings.get(P + "nur_mit_id_code") else "ohne ID-Code"))
    return {"room": room, "host": host, "port": port, "id_code": id_code, "rows": rows,
            "count_text": f"{len(rows)} Aufrufe · {note}", "rooms": [r["name"] for r in rooms(settings)]}


def new_id_code(room: str, rng: random.Random | None = None) -> str:
    """Wie im Prototyp: vier Zeichen aus dem Raumnamen + vier Hex-Ziffern, z. B. „TON3-7F2A“."""
    rng = rng or random.SystemRandom()
    head = re.sub(r"[^A-Za-z0-9]", "", room)[:4].upper() or "PLTZ"
    return f"{head}-{rng.getrandbits(16):04X}"


def set_id_code(settings: Any, room: str, code: str) -> None:
    """ID-Code am Raum setzen ("" = entfernen); lokal gespeichert, bis die Raumliste zentral kommt."""
    entries = rooms(settings)
    hit = [r for r in entries if r["name"] == room]
    if not hit:
        raise ValueError(f"Raum „{room}“ steht nicht in der Raumliste (System → Räume & Nutzer)")
    for r in hit:
        r["id_code"] = code
    settings.set_local(ROOMS, entries)
