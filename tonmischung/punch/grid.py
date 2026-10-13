"""Spurraster und Presets für das Einstellungsfenster – reine Funktionen.

Spurraster wie PunchBuddys Reiter „Spuren“: je Pro-Tools-Spur Häkchen für Rec A, Rec B, Export,
Lautheit und Play Input. Die Spurnamen kommen aus Pro Tools (Spurabfrage, Jens 02.10.2026).
Rec A, Rec B und Play Input sind Listen in „Aufnahme & Wiedergabe“; Export und Lautheit sind die
Haken der Spurtabelle (Kanalbelegung des Sendefiles) – eine Spur, die dort keinem Kanalpaar
zugeordnet ist, kann hier nicht exportiert werden.
"""
from __future__ import annotations

from typing import Any

from tonmischung.config.settings import Settings
from tonmischung.punch.settings_bridge import SPURTABELLE, export_tracks

LISTS = {"rec_a": "aufnahme.spuren.rec_a", "rec_b": "aufnahme.spuren.rec_b",
         "play_input": "aufnahme.spuren.play_input"}
COLUMNS = [("rec_a", "Rec A"), ("rec_b", "Rec B"), ("export", "Export"), ("r128", "Lautheit"),
           ("play_input", "Play Input")]
PRESETS = "aufnahme.presets"
MOVE_SRC, MOVE_DST = "aufnahme.audio_verschieben.quellspuren", "aufnahme.audio_verschieben.zielspuren"


class GridError(ValueError):
    """Änderung im Spurraster nicht möglich; die Meldung sagt, was zu tun ist."""


def grid(settings: Settings, session_tracks: list[str] | None) -> dict[str, Any]:
    """Zeilen = Spuren der Session (Reihenfolge wie in Pro Tools), dazu Spuren aus den Einstellungen,
    die in der Session fehlen (markiert). `session_tracks` None = Pro Tools nicht erreichbar."""
    lists = {key: list(settings.get(path) or []) for key, path in LISTS.items()}
    table = {r["pt_spur"]: r for r in settings.get(SPURTABELLE) or [] if r.get("pt_spur")}
    names = list(session_tracks or [])
    for name in [*lists["rec_a"], *lists["rec_b"], *lists["play_input"], *table]:
        if name not in names:
            names.append(name)
    rows = []
    for name in names:
        row = table.get(name)
        rows.append({"spur": name, "in_session": session_tracks is not None and name in session_tracks,
                     "rec_a": name in lists["rec_a"], "rec_b": name in lists["rec_b"],
                     "play_input": name in lists["play_input"],
                     "export": bool(row.get("export")) if row else None,
                     "r128": bool(row.get("r128")) if row else None})
    return {"columns": [{"key": k, "label": label} for k, label in COLUMNS], "rows": rows,
            "verbunden": session_tracks is not None}


def toggle(settings: Settings, column: str, track: str, on: bool) -> str:
    """Ein Häkchen setzen oder entfernen. Rückgabe: geänderter Einstellungspfad."""
    if column in LISTS:
        path = LISTS[column]
        values = list(settings.get(path) or [])
        if on and track not in values:
            values.append(track)
        elif not on:
            values = [v for v in values if v != track]
        settings.set_local(path, values)
        return path
    if column not in ("export", "r128"):
        raise GridError(f"Unbekannte Spalte: {column}")
    table = [dict(r) for r in settings.get(SPURTABELLE) or []]
    hit = [r for r in table if r.get("pt_spur") == track]
    if not hit:
        raise GridError(f"„{track}“ steht nicht in der Spurtabelle – erst unter Export → Spuren & Lautheit "
                        "einem Kanalpaar zuordnen")
    for r in hit:
        r[column] = on
        if column == "r128" and on:
            r["export"] = True                     # Lautheit nur für exportierte Spuren
        if column == "export" and not on:
            r["r128"] = False
    settings.set_local(SPURTABELLE, table)
    return SPURTABELLE


# ── Presets ────────────────────────────────────────────────────────────────
def move_view(settings: Settings, session_tracks: list[str] | None) -> dict[str, Any]:
    """„Audio verschieben“ wie in PunchBuddy (Jens 09.10.2026): Spurpaare Quelle → Ziel, je Zeile zwei Auswahlfelder
    mit den Spuren der Session. Gespeicherte Spuren, die in der Session fehlen, bleiben wählbar (markiert)."""
    src, dst = list(settings.get(MOVE_SRC) or []), list(settings.get(MOVE_DST) or [])
    n = max(len(src), len(dst))
    pairs = [[src[i] if i < len(src) else "", dst[i] if i < len(dst) else ""] for i in range(n)]
    tracks = list(session_tracks or [])
    missing = [t for t in dict.fromkeys(src + dst) if t and t not in tracks]
    return {"paare": pairs or [["", ""]], "spuren": tracks + missing, "fehlen": missing,
            "verbunden": session_tracks is not None, "gesperrt": settings.locked(MOVE_SRC) or settings.locked(MOVE_DST)}


def set_move_pairs(settings: Settings, pairs: list[list[str]]) -> int:
    """Speichern wie PunchBuddy: nur vollständige Paare (Quelle und Ziel gesetzt) – so sind Quell- und Zielliste
    immer gleich lang. Rückgabe: Zahl der gespeicherten Paare."""
    full = []
    for pair in pairs:
        s, t = (str(x or "").strip() for x in (list(pair) + ["", ""])[:2])
        if s and t:
            full.append((s, t))
    same = [s for s, t in full if s == t]
    if same:
        raise GridError(f"Quelle und Ziel sind dieselbe Spur: {', '.join(same)}")
    for path in (MOVE_SRC, MOVE_DST):                # beide prüfen, bevor eine geschrieben wird
        if settings.locked(path):
            raise GridError("Audio verschieben ist über die Geräteverwaltung festgelegt und gesperrt")
    settings.set_local(MOVE_SRC, [s for s, _ in full])
    settings.set_local(MOVE_DST, [t for _, t in full])
    return len(full)


def preset_from_current(settings: Settings, name: str) -> dict[str, Any]:
    """Aktuellen Stand als Preset (Format wie PunchBuddys track_presets)."""
    export, loud = export_tracks(settings)
    return {"name": name,
            "rec_a": list(settings.get("aufnahme.spuren.rec_a") or []),
            "mon_a": list(settings.get("aufnahme.spuren.monitor_a") or []),
            "rec_b": list(settings.get("aufnahme.spuren.rec_b") or []),
            "mon_b": list(settings.get("aufnahme.spuren.monitor_b") or []),
            "export": export, "export_tracks": export, "loudness_tracks": loud,
            "play_monitor_tracks": list(settings.get("aufnahme.spuren.play_input") or [])}


def presets_view(settings: Settings) -> list[dict[str, Any]]:
    out = []
    for i, p in enumerate(settings.get(PRESETS) or [], start=1):
        parts = []
        for key, label in (("rec_a", "Rec A"), ("rec_b", "Rec B")):
            if p.get(key):
                parts.append(f"{label}: {', '.join(p[key])}")
        export = p.get("export_tracks") or p.get("export") or []
        if export:
            parts.append(f"Export: {', '.join(export)}")
        out.append({"nr": i, "name": p.get("name", f"Preset {i}"), "inhalt": " · ".join(parts) or "leer"})
    return out


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise GridError("Bitte einen Namen für das Preset angeben")
    return name


def save_preset(settings: Settings, name: str, nr: int | None = None) -> int:
    """Aktuellen Stand als neues Preset (nr None) oder über Preset `nr` speichern. Rückgabe: Nummer."""
    presets = list(settings.get(PRESETS) or [])
    preset = preset_from_current(settings, _check_name(name))
    if nr is None:
        presets.append(preset)
        nr = len(presets)
    else:
        if not 1 <= nr <= len(presets):
            raise GridError(f"Preset {nr} gibt es nicht")
        presets[nr - 1] = preset
    settings.set_local(PRESETS, presets)
    return nr


def rename_preset(settings: Settings, nr: int, name: str) -> None:
    presets = [dict(p) for p in settings.get(PRESETS) or []]
    if not 1 <= nr <= len(presets):
        raise GridError(f"Preset {nr} gibt es nicht")
    presets[nr - 1]["name"] = _check_name(name)
    settings.set_local(PRESETS, presets)


def delete_preset(settings: Settings, nr: int) -> None:
    """Preset entfernen; die folgenden rücken eine Nummer auf (wie PunchBuddy)."""
    presets = list(settings.get(PRESETS) or [])
    if not 1 <= nr <= len(presets):
        raise GridError(f"Preset {nr} gibt es nicht")
    del presets[nr - 1]
    settings.set_local(PRESETS, presets)
