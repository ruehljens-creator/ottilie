"""Einstellungen zwischen Ottilie und dem PunchBuddy-Kern übersetzen – reine Funktionen.

Jedes übernommene PunchBuddy-Feld trägt im Schema `x-punchbuddy` = alter Schlüssel. Daraus entstehen
- `punch_settings`: das Wörterbuch, das PunchBuddys Kern (transport, export) erwartet, aus unseren
  Einstellungen gebaut – PunchBuddys eigene `~/.punchbuddy/settings.json` wird nie gelesen;
- `import_punchbuddy`: einmalige Übernahme einer vorhandenen PunchBuddy-Datei in die lokale Schicht
  (die Datei selbst bleibt unverändert).

Nicht übernommen: Interplay (entfällt), Sprache (nur Deutsch), ungenutzte Schlüssel. Die Spurtabelle
(Exportspuren/R 128) und der Webtrigger-Token werden gemeldet, nicht automatisch gesetzt.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tonmischung.config.settings import LAYER_MDM, LAYER_LOCAL, Settings, SettingLockedError, validate

SPURTABELLE = "export.spuren_lautheit.spurtabelle"
LAUTHEIT_AKTIV = "export.spuren_lautheit.lautheit.aktiv"
TOKEN = "webtrigger_token"
# Schlüssel, die es in Ottilie bewusst nicht gibt
DROPPED = {
    "language": "nur Deutsch",
    "extend_count": "in PunchBuddy ungenutzt",
    "wav_export_enabled": "in PunchBuddy nur im ungenutzten run_export",
    "aaf_export_enabled": "in PunchBuddy nur im ungenutzten run_export",
    "import_close_session": "Interplay entfällt",
    "export_error_keywords": "Interplay entfällt",
    "export_success_keywords": "Interplay entfällt",
}
BIND = {"127.0.0.1": "127.0.0.1", "netzwerk": "0.0.0.0"}       # unser Wert → PunchBuddy


def mapping(schema: Mapping) -> dict[str, str]:
    """PunchBuddy-Schlüssel → Pfad in unseren Einstellungen (aus `x-punchbuddy`)."""
    out: dict[str, str] = {}

    def walk(node: Mapping, path: str) -> None:
        key = node.get("x-punchbuddy")
        if isinstance(key, str) and "/" not in key:                # „export_tracks / loudness_tracks“ = Spurtabelle
            out[key] = path
        for sub, child in (node.get("properties") or {}).items():
            walk(child, f"{path}.{sub}" if path else sub)
    walk(schema, "")
    return out


def export_tracks(settings: Settings) -> tuple[list[str], list[str]]:
    """(Exportspuren, Spuren mit R 128) aus der Spurtabelle."""
    table = settings.get(SPURTABELLE) or []
    export = [r["pt_spur"] for r in table if r.get("export") and r.get("pt_spur")]
    loud = [r["pt_spur"] for r in table if r.get("export") and r.get("r128") and r.get("pt_spur")]
    if not settings.get(LAUTHEIT_AKTIV):
        loud = []
    return export, loud


def punch_settings(settings: Settings, schema: Mapping, defaults: Mapping[str, Any],
                   token: str = "") -> dict[str, Any]:
    """Einstellungs-Wörterbuch im PunchBuddy-Format.

    `defaults` = PunchBuddys DEFAULT_SETTINGS (für alles, was Ottilie nicht führt);
    `token` = Webtrigger-Token aus dem Schlüsselbund.
    """
    out: dict[str, Any] = {k: v for k, v in defaults.items() if not k.startswith("interplay_")}
    for key, path in mapping(schema).items():
        value = settings.get(path)
        if key == "http_bind_host":
            value = BIND.get(value, "127.0.0.1")
        elif key == TOKEN:
            value = token
        out[key] = value
    out["export_tracks"], out["loudness_tracks"] = export_tracks(settings)
    out["language"] = "de"
    return out


@dataclass(frozen=True)
class ImportRow:
    key: str
    path: str
    value: Any
    status: str          # übernommen | gleich | übersprungen | gesperrt | ungültig | nicht übernommen | entfällt
    note: str = ""


def _to_ours(key: str, value: Any) -> Any:
    if key == "http_bind_host":
        return "127.0.0.1" if value in ("127.0.0.1", "localhost") else "netzwerk"
    return value


def import_punchbuddy(settings: Settings, schema: Mapping, pb: Mapping[str, Any], *,
                      overwrite: bool = False, dry_run: bool = False,
                      node_for: Callable[[str], Mapping] | None = None) -> list[ImportRow]:
    """PunchBuddy-Werte in die lokale Schicht übernehmen.

    Schon lokal gesetzte Werte bleiben, außer mit `overwrite`. Werte der Geräteverwaltung bleiben immer.
    Jeder Wert wird gegen das Schema geprüft. `dry_run` zeigt nur, was geschähe.
    """
    node_for = node_for or (lambda path: settings.fields[path].node)
    rows: list[ImportRow] = []
    for key, path in mapping(schema).items():
        if key not in pb:
            continue
        if key == TOKEN:
            note = "Token nicht übernommen – nur im Schlüsselbund; in den Einstellungen neu erzeugen" if pb[key] else ""
            rows.append(ImportRow(key, path, "••••" if pb[key] else "", "nicht übernommen", note))
            continue
        value = _to_ours(key, pb[key])
        layer = settings.source(path)
        if layer == LAYER_MDM:
            rows.append(ImportRow(key, path, value, "gesperrt", "Geräteverwaltung"))
            continue
        if settings.get(path) == value:
            rows.append(ImportRow(key, path, value, "gleich"))
            continue
        if layer == LAYER_LOCAL and not overwrite:
            rows.append(ImportRow(key, path, value, "übersprungen", "lokal schon gesetzt"))
            continue
        errors = validate(node_for(path), value, path)
        if errors:
            rows.append(ImportRow(key, path, value, "ungültig", "; ".join(errors)))
            continue
        if not dry_run:
            try:
                settings.set_local(path, value)
            except SettingLockedError as e:
                rows.append(ImportRow(key, path, value, "gesperrt", str(e)))
                continue
        rows.append(ImportRow(key, path, value, "übernommen"))
    if "export_tracks" in pb or "loudness_tracks" in pb:
        rows.append(ImportRow("export_tracks / loudness_tracks", SPURTABELLE,
                              {"export": pb.get("export_tracks"), "r128": pb.get("loudness_tracks")},
                              "nicht übernommen",
                              "Spurtabelle bitte in Export → Spuren & Lautheit prüfen (Kanalbelegung des Sendefiles)"))
    for key, why in DROPPED.items():
        if key in pb:
            rows.append(ImportRow(key, "", pb[key], "entfällt", why))
    for key in sorted(k for k in pb if k.startswith("interplay_")):
        rows.append(ImportRow(key, "", pb[key], "entfällt", "Interplay entfällt"))
    return rows


# ── Spur-Presets ───────────────────────────────────────────────────────────
PRESET_TRACKS = {"rec_a": "aufnahme.spuren.rec_a", "mon_a": "aufnahme.spuren.monitor_a",
                 "rec_b": "aufnahme.spuren.rec_b", "mon_b": "aufnahme.spuren.monitor_b",
                 "play_monitor_tracks": "aufnahme.spuren.play_input"}
PRESET_VALUES = {"loudness_enabled": LAUTHEIT_AKTIV,
                 "target_lufs": "export.spuren_lautheit.lautheit.ziel_lufs",
                 "max_truepeak": "export.spuren_lautheit.lautheit.true_peak_max_dbtp",
                 "export_start_tc": "import.timecode_startband.programmbeginn",
                 "video_track": "export.dateiexporte.videospur"}


def preset_changes(settings: Settings, preset: Mapping[str, Any]) -> dict[str, Any]:
    """Was das Laden eines Presets ändert: Pfad → neuer Wert (wie PunchBuddy `load_preset_by_index`).

    Spuren Rec A/B, Abhör- und Play-Input-Spuren; Exportspuren und R 128 setzen die Haken in der
    Spurtabelle (Zeilen, deren Pro-Tools-Spur im Preset steht). ANNAHME: Ein Preset ohne
    Exportspuren lässt die Spurtabelle unverändert – sonst ginge nichts mehr ins Sendefile.
    Interplay- und sonstige PunchBuddy-Werte im Preset entfallen.
    """
    changes: dict[str, Any] = {}
    for key, path in PRESET_TRACKS.items():
        if key in preset or key in ("rec_a", "mon_a", "rec_b", "mon_b"):
            changes[path] = list(preset.get(key) or [])
    for key, path in PRESET_VALUES.items():
        if key in preset:
            changes[path] = preset[key]
    export = list(preset.get("export_tracks") or preset.get("export") or [])
    loud = preset.get("loudness_tracks")
    if export:
        table = [dict(r) for r in settings.get(SPURTABELLE) or []]
        for row in table:
            name = row.get("pt_spur")
            if not name:
                continue
            row["export"] = name in export
            if loud is not None:
                row["r128"] = name in loud and row["export"]
        changes[SPURTABELLE] = table
    return changes


def apply_preset(settings: Settings, preset: Mapping[str, Any]) -> list[str]:
    """Preset in die lokale Schicht schreiben; Rückgabe: geänderte Pfade (gesperrte bleiben)."""
    changed = []
    for path, value in preset_changes(settings, preset).items():
        if settings.get(path) == value:
            continue
        try:
            settings.set_local(path, value)
            changed.append(path)
        except SettingLockedError:
            pass
    return changed
