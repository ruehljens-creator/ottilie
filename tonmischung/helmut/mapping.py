"""Übersetzung zwischen Helmut-JSON und den Objekten der App.

Formate nach der offiziellen OpenAPI-Beschreibung von Helmut4 (MoovIT, Stand 02.10.2026,
Objekte in `objectDefinitions.yaml`). Die Beschreibung gilt
für die jeweils neueste Helmut4-Version; die Instanz des Hauses kann abweichen – geprüft wird am
echten System. Was die Beschreibung offenlässt, ist mit ANNAHME markiert. Alle Formate
stehen nur hier; die App arbeitet mit `Contribution`, `MixState` und `Handover`.
"""
from __future__ import annotations

import copy
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from tonmischung.core.mixstate import MixState

log = logging.getLogger(__name__)

# ANNAHME: Name des Metadaten-Sets und Namen der Felder (Auftrag Abschnitt 4); in Helmut als
# Metadaten-Einträge mit diesem Set als `parent` angelegt
SET_MIX = "Mischung"
MIX_KEYS = {
    "state": "zustand", "mixer": "mischer", "room": "raum", "progress": "fortschritt",
    "session_path": "session_pfad", "session_storage": "session_ablage",
    "taken_at": "uebernommen_um", "return_reason": "rueckgabe_grund",
    "returned_at": "rueckgabe_um", "returned_from_room": "rueckgabe_von_raum",
    "continued_in": "weitergefuehrt_in", "return_keep": "rueckgabe_naechstes_oeffnen",
    "duplicate_of": "duplikat_von", "duplicate_session": "duplikat_mischstand",
    "session_mirror": "session_spiegel", "activity": "taetigkeit",
    "template": "template",           # ANNAHME: Feld im Set „Mischung“ wie die übrigen (am echten Helmut anlegen)
}

# Projektstatus (ProjectStatus.status). Die OpenAPI kennt LOCKED, HOUSEKEEPER, RESTORED, ARCHIVED.
LOCKED = "LOCKED"
# ANNAHME: Entsperren mit „UNLOCKED“ wie in der Funktionsbeschreibung und im Paket helmut4-client;
# steht nicht in der Aufzählung der OpenAPI – am echten System prüfen
UNLOCKED = "UNLOCKED"

# ANNAHME: woran die Übergabe-Dateien erkannt werden
AAF_EXT = (".aaf",)
VIDEO_EXT = (".mxf", ".mov", ".mp4")
MANIFEST_NAMES = ("manifest",)


@dataclass(frozen=True)
class Asset:
    id: str
    name: str
    path: str


@dataclass(frozen=True)
class Handover:
    aaf: Asset | None
    video: Asset | None
    manifest: Asset | None

    @property
    def complete(self) -> bool:
        return self.aaf is not None and self.video is not None


@dataclass
class Contribution:
    """Ein Beitrag (in Helmut: ein Projekt)."""
    id: str
    title: str
    show: str
    locked: bool
    mix: MixState
    raw: dict = field(default_factory=dict, repr=False)


# ── Anmeldung ───────────────────────────────────────────────────────────────
def token_from_login(headers: Any, body: Any) -> str | None:
    """Bearer-Token aus der Antwort von /auth/login/body bzw. /auth/oauth.

    Die OpenAPI liefert es im Header `Authorization`; mit `responseWithToken=true` auch im Inhalt.
    """
    auth = (headers.get("Authorization") if headers is not None else None) or ""
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip() or None
    if isinstance(body, dict) and body.get("token"):
        return str(body["token"])
    return None


# ── Projekt ─────────────────────────────────────────────────────────────────
def _metadata(project: dict) -> list[dict]:
    """Metadaten des Projekts: Liste von `Metadata` (name, value, parent = Sets, …); Fremdformen werden übergangen."""
    meta = project.get("metadata")
    return [m for m in meta if isinstance(m, dict)] if isinstance(meta, list) else []


def _in_set(entry: dict, set_name: str) -> bool:
    parents = entry.get("parent")
    return isinstance(parents, list) and any(isinstance(p, dict) and p.get("name") == set_name for p in parents)


def parse_progress(value: Any) -> int:
    """Fortschritt aus Helmut (von Hand gepflegt: „12,5“, „40 %“) – Unlesbares gilt als 0 statt die ganze Liste
    scheitern zu lassen (Review B7)."""
    try:
        return max(0, min(100, int(float(str(value).replace(",", ".").replace("%", "").strip()))))
    except ValueError:
        log.warning("Fortschritt „%s“ in Helmut nicht lesbar – gilt als 0", value)
        return 0


def _key(entry: dict) -> str:
    # ANNAHME: Feld wird über seinen Namen erkannt, ersatzweise über customId
    return entry.get("name") or entry.get("customId") or ""


def mix_from_project(project: dict) -> MixState:
    values = {_key(m): m.get("value") for m in _metadata(project) if _in_set(m, SET_MIX)}
    kwargs: dict[str, Any] = {}
    for f in fields(MixState):
        key = MIX_KEYS[f.name]
        if values.get(key) not in (None, ""):
            kwargs[f.name] = parse_progress(values[key]) if f.name == "progress" else str(values[key])
    return MixState(**kwargs)


def is_locked(project: dict) -> bool:
    status = project.get("status")
    if isinstance(status, dict):                     # ProjectStatus laut OpenAPI
        return str(status.get("status") or "").upper() == LOCKED
    return str(status or "").upper() == LOCKED      # ältere Instanzen: nur der Text


def contribution_from_project(project: dict) -> Contribution:
    return Contribution(
        id=str(project["id"]),
        title=project.get("name", ""),
        # ANNAHME: Sendung steht in "group" (Helmut-Projektgruppe)
        show=project.get("group", ""),
        locked=is_locked(project),
        mix=mix_from_project(project),
        raw=project,
    )


def metadata_with_mix(project: dict, mix: MixState) -> list[dict]:
    """Vollständige Metadatenliste mit aktualisiertem Set „Mischung“.

    Fremde Einträge bleiben unverändert; vorhandene Felder des Sets behalten id, Typ und
    Set-Verweis (mit dessen id), nur `value` ändert sich. Fehlende Felder kommen neu dazu.
    ANNAHME: PUT /projects/metadata/{id} ersetzt die Liste – deshalb immer die ganze Liste.
    """
    wanted = {MIX_KEYS[k]: "" if v is None else str(v) for k, v in asdict(mix).items()}
    out, seen, parent = [], set(), None
    for m in _metadata(project):
        m = copy.deepcopy(m)
        if _in_set(m, SET_MIX):
            parent = parent or next(p for p in m["parent"] if p.get("name") == SET_MIX)
            key = _key(m)
            if key in wanted:
                m["value"] = wanted[key]
                seen.add(key)
        out.append(m)
    for key, value in wanted.items():
        if key not in seen:
            out.append({"name": key, "value": value, "type": "STRING",
                        "parent": [copy.deepcopy(parent) if parent else {"name": SET_MIX}]})
    return out


def metadata_patch(project: dict, mix: MixState) -> dict:
    """Inhalt für PUT /v1/fx/projects/metadata/{id} (ProjectPatchMetadata)."""
    return {"metadata": metadata_with_mix(project, mix)}


def status_body(locked: bool) -> dict:
    """Inhalt für PUT /v1/fx/projects/status/{id} (ProjectStatus)."""
    return {"status": LOCKED if locked else UNLOCKED}


def search_filter_for_mixing() -> dict:
    """SearchFilter für POST /v1/fx/projects/search.

    ANNAHME: Ohne Filterbedingung, neueste zuerst; die App sortiert nach dem Set „Mischung“
    selbst. Die Schlüssel für Filter auf Metadaten oder Gruppen sind in der OpenAPI nicht
    beschrieben – am echten System klären (sonst kommen bis zu 500 Projekte).
    """
    return {"type": "FX", "any": False, "filter": [],
            "sortParameter": {"key": "modifyDate", "direction": "DESC"}}


# ── Assets ──────────────────────────────────────────────────────────────────
def asset_from_json(a: dict) -> Asset:
    path = a.get("filepath") or a.get("path") or ""
    return Asset(id=str(a.get("id", "")), name=a.get("name") or os.path.basename(path), path=path)


def find_handover(assets: list[dict]) -> Handover:
    """AAF, Referenzvideo und Manifest unter den Assets eines Projekts finden (jeweils das neueste)."""
    aaf = video = manifest = None
    for a in sorted(assets, key=lambda x: str(x.get("lastModified") or x.get("createdAt") or "")):
        asset = asset_from_json(a)
        name = (asset.name or os.path.basename(asset.path)).lower()
        if name.endswith(AAF_EXT):
            aaf = asset
        elif name.endswith(VIDEO_EXT):
            video = asset
        elif any(name.startswith(m) for m in MANIFEST_NAMES):
            manifest = asset
    return Handover(aaf=aaf, video=video, manifest=manifest)


def new_asset(project_id: str, path: str, kind: str) -> dict:
    """Asset für POST /v1/co/assets (Rückgabe der Tonspuren); Pflicht: filepath, mimeType."""
    return {"projectId": project_id, "filepath": path, "mimeType": "AUDIO",
            "customMetadata": [{"key": "art", "value": kind}]}


def handover_asset(project_id: str, asset: Asset) -> dict:
    """Übergabe-Datei des Originals am Duplikat registrieren (gleiche Datei, keine Kopie).
    ANNAHME: AAF und Manifest als DATA, Video als VIDEO."""
    mime = "VIDEO" if asset.path.lower().endswith(VIDEO_EXT) else "DATA"
    return {"projectId": project_id, "filepath": asset.path, "mimeType": mime,
            "customMetadata": [{"key": "art", "value": "uebergabe"}]}


def duplicate_body(project: dict, title: str) -> dict:
    """Inhalt für POST /v1/fx/projects/duplicate.
    ANNAHME A2 (Plan M4-Rest): das Projekt des Originals mit neuem Namen; Antwort = neues Projekt."""
    body = copy.deepcopy(project)
    body["name"] = title
    return body


def notification_body(title: str, text: str) -> dict:
    """Message für POST /v1/members/users/notification/{userName}."""
    return {"msg": f"{title}: {text}" if title else text, "expandDialog": False}
