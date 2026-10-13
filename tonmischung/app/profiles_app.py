"""Spurprofile je Template im Einstellungsfenster (Plan Spurprofile, Schritt 2).

Spuren der offenen Session per PTSL lesen (nur lesen), je Spur Häkchen setzen, Profil an Templategruppe und -name
knüpfen. Gespeichert wird in `import.spurprofile` – vorerst lokal je Mac, später zentral.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from tonmischung.app import choosers
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.config.settings import SettingLockedError
from tonmischung.core import track_profile as TP
from tonmischung.core.fileio import write_new, write_text
from tonmischung.ptsl.errors import PtslError

PATH = "import.spurprofile"
# Spalten der Tabelle: (Schlüssel, Beschriftung, Gruppe, Art)
COLUMNS = [("o_ton", "O-Ton", "Import", "triage"), ("atmo", "Atmo", "Import", "triage"),
           ("musik", "Musik", "Import", "triage"), ("rest", "Rest", "Import", "triage"),
           ("hide_when_empty", "Ausblenden, wenn leer", "Import", "bool"),
           ("export_name", "Exportname", "Export", "exportname"),
           ("export", "Export", "Export", "bool"), ("channel", "Kanäle", "Export", "channel"),
           ("content", "Inhalt", "Export", "text"), ("r128", "R128", "Export", "bool"),
           ("rec_a", "Record A", "Aufnahme", "bool"), ("monitor_a", "Abhören A", "Aufnahme", "bool"),
           ("rec_b", "Record B", "Aufnahme", "bool"), ("monitor_b", "Abhören B", "Aufnahme", "bool"),
           ("play_input", "Play Input", "Aufnahme", "bool")]
SWITCHES = {"show_clips": "Nach dem Import Spuren mit Clips einblenden",
            "hide_empty": "Nach dem Import leere angehakte Spuren ausblenden",
            "when_full": "Wenn die Zielspuren einer Kategorie voll sind",
            "track_data_preset": "Spurdaten-Preset für den AAF-Import (leer = Einstellung Import-Vorschau)"}
WHEN_FULL_LABELS = {"reservespuren": "Auf die Reservespuren (Rest)",
                    "verwandte_kategorie": "Auf die folgenden Spuren der nächsten Gruppe (O-Ton → Atmo → Musik → Rest)"}
KIND_LABEL = {"AudioTrack": "Audio", "AuxTrack": "Aux", "BasicFolder": "Ordner", "RoutingFolder": "Routing-Ordner",
              "VideoTrack": "Video", "MasterTrack": "Master", "VCATrack": "VCA", "MidiTrack": "MIDI",
              "InstrumentTrack": "Instrument"}


def _profiles(ctx: AppContext) -> list[TP.TrackProfile]:
    return TP.profiles_from_settings(ctx.settings.get(PATH))


def _save(ctx: AppContext, profiles: list[TP.TrackProfile]) -> None:
    try:
        ctx.settings.set_local(PATH, [TP.profile_to_dict(p) for p in profiles])
    except SettingLockedError as e:
        raise CommandError(str(e)) from e
    binder = ctx.extra.get("profile_binder")
    if binder is not None:                                   # offene Session sofort mit dem geänderten Profil
        binder.refresh()


def _pick(profiles: list[TP.TrackProfile], group: str, name: str) -> tuple[int, TP.TrackProfile]:
    for i, p in enumerate(profiles):
        if TP._key(p.template_name) == TP._key(name) and TP._key(p.template_group) == TP._key(group):
            return i, p
    raise CommandError(f"Kein Spurprofil für „{group}/{name}“")


def _row(t: TP.ProfileTrack) -> dict[str, Any]:
    row: dict[str, Any] = {"name": t.name, "kind": KIND_LABEL.get(t.kind, t.kind), "audio": t.is_audio,
                           "format": t.fmt.removeprefix("TF_"), "folder": t.folder, "missing": t.missing}
    for key, _label, _group, kind in COLUMNS:
        row[key] = (t.triage == key) if kind == "triage" else getattr(t, key)
    return row


def view(ctx: AppContext, group: str = "", name: str = "", message: str = "",
         template_list: Any = None) -> dict[str, Any]:
    """Alles für den Block „Spurprofile“: Templates, Profile, gewähltes Profil mit Zeilen und Hinweisen."""
    profiles = _profiles(ctx)
    try:
        from tonmischung.app.templates_app import library
        tl = template_list if template_list is not None else library(ctx)
        known = [(t["group"], t["name"]) for t in tl.get("mine", [])] + \
                [(t["group"], t["name"]) for t in tl.get("found", [])]
    except Exception:  # noqa: BLE001 – Templateordner nicht lesbar: nur vorhandene Profile zeigen
        known = []
    options = []
    for g, n in [(p.template_group, p.template_name) for p in profiles] + known:
        if (g, n) not in options:
            options.append((g, n))
    selected = next((p for p in profiles if (p.template_group, p.template_name) == (group, name)), None)
    if selected is None and not group and not name and profiles:
        selected = profiles[0]
    session = None
    try:
        info = ctx.ptsl.open_session()
        session = info.name if info else None
    except PtslError:
        pass
    sel = None
    if selected is not None:
        sel = {"group": selected.template_group, "name": selected.template_name, "read_from": selected.read_from,
               "read_at": selected.read_at, "rows": [_row(t) for t in selected.tracks],
               "problems": TP.problems(selected) + _unknown_names(ctx, selected),
               "switches": {"show_clips": selected.show_clips, "hide_empty": selected.hide_empty,
                            "when_full": selected.when_full, "track_data_preset": selected.track_data_preset}}
    return {"templates": [{"group": g, "name": n, "has_profile": any(
                (p.template_group, p.template_name) == (g, n) for p in profiles)} for g, n in options],
            "selected": sel, "chosen": {"group": group or (selected.template_group if selected else ""),
                                        "name": name or (selected.template_name if selected else "")},
            "session": session, "message": message,
            "columns": [{"key": k, "label": label, "group": grp, "kind": kind} for k, label, grp, kind in COLUMNS],
            "switch_labels": SWITCHES, "when_full_labels": WHEN_FULL_LABELS, "track_data_presets": _presets(),
            "export_names": [str(e.get("name")) for e in _export_names(ctx) if e.get("name")]}


def _unknown_names(ctx: AppContext, p: TP.TrackProfile) -> list[str]:
    known = {str(e.get("name")) for e in _export_names(ctx)}
    return [f"„{t.name}“: Exportname „{t.export_name}“ steht nicht mehr in der Auswahl"
            for t in p.present() if t.export_name and t.export_name not in known]


def _export_names(ctx: AppContext) -> list[dict]:
    """Auswahl der Exportnamen (Einstellung export.spuren_lautheit.exportnamen;
    Jens: ST, IT, DoE, AD, OGA, Musik, Spr)."""
    return [e for e in ctx.settings.get("export.spuren_lautheit.exportnamen") or [] if isinstance(e, dict)]


def _presets() -> list[str]:
    from tonmischung.app.settings_view import DEFAULT_TRACK_DATA_PRESET, track_data_presets
    try:
        names = track_data_presets()
    except OSError:
        names = []
    return names if DEFAULT_TRACK_DATA_PRESET in names else [DEFAULT_TRACK_DATA_PRESET, *names]


def read(ctx: AppContext, group: str, name: str, now: Any = None) -> dict[str, Any]:
    """Spuren der offenen Session ins Profil des Templates übernehmen (PTSL nur lesend)."""
    if not name:
        raise CommandError("Bitte zuerst ein Template wählen")
    try:
        info = ctx.ptsl.open_session()
        if info is None:
            raise CommandError("Keine Session offen – bitte eine Session aus diesem Template öffnen")
        tracks = ctx.ptsl.session_tracks()
    except PtslError as e:
        raise CommandError(f"Pro Tools: {e}") from e
    profiles = _profiles(ctx)
    try:
        i, old = _pick(profiles, group, name)
    except CommandError:
        i, old = -1, None
    when = now() if now else time.strftime("%d.%m.%Y %H:%M")
    new = TP.merge_read(tracks, old, TP.defaults_from_settings(ctx.settings.get), group, name, info.name, when)
    if i < 0:
        profiles.append(new)
    else:
        profiles[i] = new
    _save(ctx, profiles)
    added = sum(1 for t in new.tracks if old is not None and old.named(t.name) is None)
    missing = sum(1 for t in new.tracks if t.missing)
    msg = f"{len(tracks)} Spuren aus „{info.name}“ gelesen" + (
        " und nach den bisherigen Einstellungen vorbelegt" if old is None else
        (f", {added} neu" if added else "") + (f", {missing} fehlen in der Session" if missing else ""))
    return view(ctx, group, name, msg)


def set_cell(ctx: AppContext, group: str, name: str, track: str, column: str, value: Any) -> dict[str, Any]:
    profiles = _profiles(ctx)
    i, p = _pick(profiles, group, name)
    t = p.named(track)
    if t is None:
        raise CommandError(f"Spur „{track}“ steht nicht im Profil")
    kind = next((k for key, _l, _g, k in COLUMNS if key == column), None)
    if kind is None:
        raise CommandError(f"Unbekannte Spalte: {column}")
    if not t.is_audio:
        raise CommandError(f"„{track}“ ist keine Audiospur – hier wirken keine Häkchen")
    if kind == "triage":
        t = TP.replace(t, triage=column if value else ("" if t.triage == column else t.triage))
    elif kind == "channel":
        try:
            ch = int(value or 0)
        except (TypeError, ValueError):
            raise CommandError("Kanal: 1, 3, 5 … oder leer") from None
        t = TP.replace(t, channel=max(0, ch))
    elif kind == "text":
        t = TP.replace(t, content=str(value or "").strip())
    elif kind == "exportname":
        ename = str(value or "").strip()
        entry = next((e for e in _export_names(ctx) if str(e.get("name")) == ename), None)
        if ename and entry is None:
            raise CommandError(f"Exportname „{ename}“ steht nicht in der Auswahl "
                               "(Export › Spuren & Lautheit › Exportnamen)")
        t = TP.replace(t, export_name=ename)
        if entry is not None:                                # Haus-Standard: Kanäle und Inhalt vorbelegen, exportieren
            t = TP.replace(t, export=True, channel=int(entry.get("kanal") or 0) or t.channel,
                           content=str(entry.get("inhalt") or t.content))
    else:
        t = TP.replace(t, **{column: bool(value)})
        if column == "export" and not value:
            t = TP.replace(t, r128=False)                    # R128 wirkt nur auf exportierte Spuren
    profiles[i] = TP.replace(p, tracks=tuple(t if x.name == track else x for x in p.tracks))
    _save(ctx, profiles)
    return view(ctx, group, name)


def rename_export_names(ctx: AppContext, old: list, new: list) -> str:
    """Wurde in der Liste der Exportnamen ein Name geändert (gleiche Zeile, gleiche Länge), tragen die Spurprofile
    den neuen Namen. Rückgabe: „alt → neu, …“ oder leer."""
    if len(old) != len(new):
        return ""
    changes = {str(a.get("name") or ""): str(b.get("name") or "") for a, b in zip(old, new)
               if isinstance(a, dict) and isinstance(b, dict) and a.get("name") and b.get("name")
               and a.get("name") != b.get("name")}
    if not changes:
        return ""
    profiles = _profiles(ctx)
    touched = False
    for i, p in enumerate(profiles):
        tracks = tuple(TP.replace(t, export_name=changes[t.export_name]) if t.export_name in changes else t
                       for t in p.tracks)
        if tracks != p.tracks:
            profiles[i], touched = TP.replace(p, tracks=tracks), True
    if touched:
        _save(ctx, profiles)
    return ", ".join(f"{a} → {b}" for a, b in changes.items()) if touched else ""


def set_switch(ctx: AppContext, group: str, name: str, switch: str, value: Any) -> dict[str, Any]:
    profiles = _profiles(ctx)
    i, p = _pick(profiles, group, name)
    if switch in ("show_clips", "hide_empty"):
        p = TP.replace(p, **{switch: bool(value)})
    elif switch == "when_full" and value in TP.WHEN_FULL:
        p = TP.replace(p, when_full=value)
    elif switch == "track_data_preset":
        p = TP.replace(p, track_data_preset=str(value or ""))
    else:
        raise CommandError(f"Unbekannte Einstellung: {switch}")
    profiles[i] = p
    _save(ctx, profiles)
    return view(ctx, group, name)


def remove_track(ctx: AppContext, group: str, name: str, track: str) -> dict[str, Any]:
    """Nur Spuren, die in der Session fehlen, lassen sich aus dem Profil entfernen."""
    profiles = _profiles(ctx)
    i, p = _pick(profiles, group, name)
    t = p.named(track)
    if t is None or not t.missing:
        raise CommandError("Nur Spuren, die in der Session fehlen, können entfernt werden")
    profiles[i] = TP.replace(p, tracks=tuple(x for x in p.tracks if x.name != track))
    _save(ctx, profiles)
    return view(ctx, group, name, f"„{track}“ aus dem Profil entfernt")


def remove_profile(ctx: AppContext, group: str, name: str) -> dict[str, Any]:
    profiles = _profiles(ctx)
    i, _p = _pick(profiles, group, name)
    del profiles[i]
    _save(ctx, profiles)
    return view(ctx, message=f"Spurprofil „{group}/{name}“ entfernt – es gelten wieder die allgemeinen Einstellungen")


# ── Exportieren und Importieren (Datei) ─────────────────────────────────────
def choose_save(default_name: str, prompt: str = "Spurprofile exportieren") -> str:
    """macOS-Dialog „Sichern“; Abbrechen → ''."""
    return choosers.choose_save(prompt, default_name)


def choose_open(prompt: str = "Spurprofile importieren") -> str:
    """macOS-Dialog „Öffnen“ für .json; Abbrechen → ''."""
    return choosers.choose_file(prompt, ("public.json",))


def _free_name(path: str) -> str:
    """Nie überschreiben: vorhandene Datei → „Name 2.json“, „Name 3.json“ …"""
    if not path.lower().endswith(".json"):
        path += ".json"
    stem, n = path[:-5], 2
    while os.path.exists(path):
        path, n = f"{stem} {n}.json", n + 1
    return path


def target_chooser(target: str) -> Any:
    """Für die Schnittstelle: Ziel statt Dialog – ein Ordner (Dateiname wie im Dialog vorgeschlagen) oder eine .json."""
    if not target:
        return None
    target = os.path.expanduser(target)
    return lambda name: target if target.lower().endswith(".json") else os.path.join(target, name)


def export_profiles(ctx: AppContext, group: str = "", name: str = "", everything: bool = False,
                    choose: Any = None, now: Any = None) -> dict[str, Any]:
    profiles = _profiles(ctx)
    if not everything:
        profiles = [_pick(profiles, group, name)[1]]
    if not profiles:
        raise CommandError("Noch kein Spurprofil angelegt")
    label = "Spurprofile" if everything else f"Spurprofil {profiles[0].template_name}"
    target = (choose or choose_save)(f"{label}.json")
    if not target:
        return view(ctx, group, name, "Export abgebrochen")
    path = _free_name(target)
    when = now() if now else time.strftime("%d.%m.%Y %H:%M")
    doc = TP.export_document(profiles, _export_names(ctx), when, f"{ctx.mixer} · {ctx.room}")
    write_new(path, lambda tmp: write_text(tmp, json.dumps(doc, ensure_ascii=False, indent=1)))   # nie überschreiben
    return view(ctx, group, name, f"{len(profiles)} Spurprofil(e) exportiert: {path}")


def check_import(ctx: AppContext, path: str = "", choose: Any = None) -> dict[str, Any]:
    """Datei lesen und zeigen, was neu ist und was ein vorhandenes Profil ersetzen würde – noch nichts ändern."""
    path = path or (choose or choose_open)()
    if not path:
        return {"path": "", "items": [], "message": "Import abgebrochen"}
    try:
        with open(path, encoding="utf-8") as f:
            incoming, names = TP.read_document(json.load(f))
    except (OSError, ValueError) as e:
        raise CommandError(f"Nicht lesbar: {e}") from e
    items = [{"key": TP.profile_key(p), "group": p.template_group, "name": p.template_name, "status": st,
              "tracks": len(p.tracks)} for p, st in TP.import_plan(incoming, _profiles(ctx))]
    known = {str(e.get("name")) for e in _export_names(ctx)}
    return {"path": path, "items": items, "new_names": [n["name"] for n in names if n["name"] not in known],
            "message": ""}


def do_import(ctx: AppContext, path: str, keys: list[str]) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            incoming, names = TP.read_document(json.load(f))
    except (OSError, ValueError) as e:
        raise CommandError(f"Nicht lesbar: {e}") from e
    chosen = [p for p in incoming if TP.profile_key(p) in set(keys)]
    if not chosen:
        raise CommandError("Kein Profil gewählt")
    before = {str(e.get("name")) for e in _export_names(ctx)}
    merged = TP.merge_export_names(_export_names(ctx), names)
    added = [str(e["name"]) for e in merged if str(e.get("name")) not in before]
    if added:
        try:
            ctx.settings.set_local("export.spuren_lautheit.exportnamen", merged)
        except SettingLockedError as e:
            raise CommandError(str(e)) from e
    _save(ctx, TP.apply_import(_profiles(ctx), chosen))
    first = chosen[0]
    msg = f"{len(chosen)} Spurprofil(e) importiert" + (f", Exportnamen ergänzt: {', '.join(added)}" if added else "")
    return view(ctx, first.template_group, first.template_name, msg)


def register(bridge: Bridge, ctx: AppContext) -> None:
    def a(args: dict, key: str) -> str:
        return str(args.get(key) or "")
    bridge.register("spurprofile", lambda args: view(ctx, a(args, "gruppe"), a(args, "name")))
    bridge.register("spurprofil_lesen", lambda args: read(ctx, a(args, "gruppe"), a(args, "name")))
    bridge.register("spurprofil_setzen", lambda args: set_cell(ctx, a(args, "gruppe"), a(args, "name"),
                                                               a(args, "spur"), a(args, "spalte"), args.get("wert")))
    bridge.register("spurprofil_schalter", lambda args: set_switch(ctx, a(args, "gruppe"), a(args, "name"),
                                                                   a(args, "schalter"), args.get("wert")))
    bridge.register("spurprofil_spur_entfernen", lambda args: remove_track(ctx, a(args, "gruppe"), a(args, "name"),
                                                                           a(args, "spur")))
    bridge.register("spurprofil_entfernen", lambda args: remove_profile(ctx, a(args, "gruppe"), a(args, "name")))
    bridge.register("spurprofile_exportieren", lambda args: export_profiles(
        ctx, a(args, "gruppe"), a(args, "name"), everything=bool(args.get("alle")),
        choose=target_chooser(a(args, "ziel"))))
    bridge.register("spurprofile_import_pruefen", lambda args: check_import(ctx, a(args, "pfad")))
    bridge.register("spurprofile_importieren",
                    lambda args: do_import(ctx, a(args, "pfad"), list(args.get("auswahl") or [])))
