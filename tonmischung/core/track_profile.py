"""Spurprofile je Template (Plan Spurprofile) – reine Logik.

Jens (03.10.2026): Spuren der offenen Pro-Tools-Session lesen, dahinter Häkchen (O-Ton, Atmo, Musik; Export, R128;
Ausblenden, wenn ungenutzt), an einen Template-Namen geknüpft – „so flexibel, dass es mit unterschiedlichsten
Templates funktioniert“. Deshalb kennt dieser Teil keine festen Spurnamen: was eine Spur tut, steht im Profil; die
heutigen Einstellungen dienen nur zum Vorbelegen beim ersten Lesen und für Templates ohne Profil.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from typing import Any

CATEGORIES = ("o_ton", "atmo", "musik", "rest")
RECORDING = ("rec_a", "monitor_a", "rec_b", "monitor_b", "play_input")
AUDIO = "AudioTrack"
WHEN_FULL = ("reservespuren", "verwandte_kategorie", "nachfragen")


@dataclass(frozen=True)
class ProfileTrack:
    name: str
    kind: str = AUDIO                 # PTSL-Spurtyp
    fmt: str = ""                     # TF_Mono, TF_Stereo …
    folder: str = ""
    triage: str = ""                  # "" | o_ton | atmo | musik | rest
    hide_when_empty: bool = False
    export_name: str = ""             # Name der exportierten Datei (ST, IT …); leer = Spurname
    export: bool = False
    channel: int = 0                  # erster Kanal im Sendefile (1 = 1/2), 0 = nach der Reihenfolge
    content: str = ""
    r128: bool = False
    rec_a: bool = False
    monitor_a: bool = False
    rec_b: bool = False
    monitor_b: bool = False
    play_input: bool = False
    missing: bool = False             # beim letzten Lesen nicht mehr in der Session

    @property
    def is_audio(self) -> bool:
        return self.kind == AUDIO


@dataclass(frozen=True)
class TrackProfile:
    template_group: str
    template_name: str
    tracks: tuple[ProfileTrack, ...] = ()
    read_from: str = ""
    read_at: str = ""
    show_clips: bool = True
    hide_empty: bool = True
    when_full: str = "reservespuren"
    track_data_preset: str = ""       # leer = globale Einstellung

    def named(self, name: str) -> ProfileTrack | None:
        return next((t for t in self.tracks if t.name == name), None)

    def present(self) -> tuple[ProfileTrack, ...]:
        return tuple(t for t in self.tracks if not t.missing)


# ── Einstellungen (deutsche Schlüssel wie im Schema) ⇄ Objekte ───────────────
_TRACK_KEYS = {"name": "name", "typ": "kind", "format": "fmt", "ordner": "folder", "triage": "triage",
               "ausblenden_wenn_leer": "hide_when_empty", "exportname": "export_name", "export": "export",
               "kanal": "channel",
               "inhalt": "content", "r128": "r128", "rec_a": "rec_a", "monitor_a": "monitor_a", "rec_b": "rec_b",
               "monitor_b": "monitor_b", "play_input": "play_input", "fehlt": "missing"}
_PROFILE_KEYS = {"template_gruppe": "template_group", "template_name": "template_name", "gelesen_aus": "read_from",
                 "gelesen_am": "read_at", "spuren_mit_clips_einblenden": "show_clips",
                 "leere_spuren_ausblenden": "hide_empty", "wenn_voll": "when_full",
                 "trackdaten_preset": "track_data_preset"}


def track_from_dict(d: Mapping[str, Any]) -> ProfileTrack:
    kw: dict[str, Any] = {}
    defaults = {f.name: f.default for f in fields(ProfileTrack)}
    for de, en in _TRACK_KEYS.items():
        if de in d and d[de] is not None:
            v = d[de]
            kw[en] = int(v or 0) if en == "channel" else (bool(v) if isinstance(defaults.get(en), bool) else str(v))
    t = ProfileTrack(**kw)
    return t if t.triage in ("",) + CATEGORIES else replace(t, triage="")


def track_to_dict(t: ProfileTrack) -> dict[str, Any]:
    return {de: getattr(t, en) for de, en in _TRACK_KEYS.items()}


def profile_from_dict(d: Mapping[str, Any]) -> TrackProfile:
    kw: dict[str, Any] = {en: d[de] for de, en in _PROFILE_KEYS.items() if de in d and d[de] is not None}
    for b in ("show_clips", "hide_empty"):
        if b in kw:
            kw[b] = bool(kw[b])
    kw.setdefault("template_group", "")
    kw.setdefault("template_name", "")
    if kw.get("when_full") not in WHEN_FULL:
        kw["when_full"] = "reservespuren"
    return TrackProfile(tracks=tuple(track_from_dict(x) for x in d.get("spuren") or [] if x.get("name")), **kw)


def profile_to_dict(p: TrackProfile) -> dict[str, Any]:
    out = {de: getattr(p, en) for de, en in _PROFILE_KEYS.items()}
    out["spuren"] = [track_to_dict(t) for t in p.tracks]
    return out


def profiles_from_settings(value: Iterable[Mapping] | None) -> list[TrackProfile]:
    return [profile_from_dict(d) for d in value or [] if isinstance(d, Mapping) and d.get("template_name")]


# ── Welches Profil gilt? ──────────────────────────────────────────────────────
def _key(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def find(profiles: Sequence[TrackProfile], group: str, name: str) -> TrackProfile | None:
    """Profil zu Templategruppe und -name; ein Profil ohne Gruppe passt auf jede Gruppe."""
    for p in profiles:
        if _key(p.template_name) == _key(name) and (not p.template_group or _key(p.template_group) == _key(group)):
            return p
    return None


def best_match(profiles: Sequence[TrackProfile], track_names: Iterable[str], min_share: float = 0.6
               ) -> TrackProfile | None:
    """Für Sessions von außerhalb: das Profil mit den meisten gleichnamigen Spuren (Anteil an den Profilspuren)."""
    have = set(track_names)
    best, score = None, 0.0
    for p in profiles:
        own = [t.name for t in p.present()]
        if not own:
            continue
        share = sum(n in have for n in own) / len(own)
        if share > score:
            best, score = p, share
    return best if score >= min_share else None


# ── Lesen und Abgleichen ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class SessionTrack:
    """Was PTSL über eine Spur liefert (GetTrackList)."""
    name: str
    kind: str = AUDIO
    fmt: str = ""
    folder: str = ""


@dataclass(frozen=True)
class Defaults:
    """Heutige Einstellungen – nur zum Vorbelegen eines neuen Profils."""
    oton: tuple[str, ...] = ()
    atmo: tuple[str, ...] = ()
    music: tuple[str, ...] = ()
    rest_prefix: str = "A"
    rest_from: int = 11
    rest_to: int | None = None
    hide_prefixes: tuple[str, ...] = ("A", "Musik")
    table: tuple[Mapping[str, Any], ...] = ()          # export.spuren_lautheit.spurtabelle
    recording: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def category(self, name: str) -> str:
        if name in self.oton:
            return "o_ton"
        if name in self.atmo:
            return "atmo"
        if name in self.music:
            return "musik"
        m = re.fullmatch(re.escape(self.rest_prefix) + r"(\d+)", name)
        if m and int(m.group(1)) >= self.rest_from and (self.rest_to is None or int(m.group(1)) <= self.rest_to):
            return "rest"
        return ""


def prefill(t: SessionTrack, d: Defaults) -> ProfileTrack:
    """Neue Spur nach den heutigen Einstellungen vorbelegen – nur Audiospuren bekommen Häkchen."""
    pt = ProfileTrack(t.name, t.kind, t.fmt, t.folder)
    if t.kind != AUDIO:
        return pt
    cat = d.category(t.name)
    hide = any(re.fullmatch(re.escape(p) + r"\s*\d+", t.name) for p in d.hide_prefixes)
    rows = list(d.table)
    row = next((i for i, r in enumerate(rows) if str(r.get("pt_spur") or "").strip() == t.name.strip()), None)
    exp = {}
    if row is not None:
        r = rows[row]
        exp = {"export": bool(r.get("export")), "r128": bool(r.get("r128")) and bool(r.get("export")),
               "content": str(r.get("inhalt") or ""), "channel": 2 * row + 1 if r.get("export") else 0}
    rec = {k: t.name in set(d.recording.get(k) or ()) for k in RECORDING}
    return replace(pt, triage=cat, hide_when_empty=hide or bool(cat), **exp, **rec)


def merge_read(read: Sequence[SessionTrack], previous: TrackProfile | None, defaults: Defaults,
               group: str, name: str, session: str = "", when: str = "") -> TrackProfile:
    """Spuren aus Pro Tools übernehmen: gleichnamige behalten ihre Häkchen (Typ/Format/Ordner neu), neue werden
    vorbelegt (erstes Lesen) bzw. ohne Häkchen ergänzt (Profil vorhanden), verschwundene bleiben als „fehlt“."""
    seen = {t.name for t in read}
    out: list[ProfileTrack] = []
    for t in read:
        old = previous.named(t.name) if previous else None
        if old is not None:
            out.append(replace(old, kind=t.kind, fmt=t.fmt, folder=t.folder, missing=False))
        elif previous is None:
            out.append(prefill(t, defaults))
        else:
            out.append(ProfileTrack(t.name, t.kind, t.fmt, t.folder))
    if previous:
        out += [replace(t, missing=True) for t in previous.tracks if t.name not in seen]
    base = previous or TrackProfile(group, name)
    return replace(base, template_group=group or base.template_group, template_name=name or base.template_name,
                   tracks=tuple(out), read_from=session, read_at=when)


# ── Prüfen ────────────────────────────────────────────────────────────────────
def problems(p: TrackProfile, channels_total: int = 16) -> list[str]:
    """Hinweise zum Profil (blockieren nichts außer überlappenden Kanälen beim Export)."""
    out = []
    for t in p.tracks:
        if t.missing and (t.export or t.triage or any(getattr(t, k) for k in RECORDING)):
            out.append(f"„{t.name}“ fehlt in der Session, ist aber angehakt")
        if not t.is_audio and (t.triage or t.export or t.r128):
            out.append(f"„{t.name}“ ist keine Audiospur ({t.kind}) – Häkchen wirken nicht")
        if t.triage and t.fmt and t.fmt != "TF_Mono":
            out.append(f"„{t.name}“ ist {t.fmt.removeprefix('TF_')} – die Triage verteilt Mono-Paare")
        if t.r128 and not t.export:
            out.append(f"„{t.name}“: R128 ohne Export wirkt nicht")
        if t.channel and (t.channel % 2 == 0 or t.channel > channels_total - 1):
            out.append(f"„{t.name}“: Kanal {t.channel} – erwartet 1, 3, 5 … bis {channels_total - 1}")
    names: dict[str, str] = {}
    for t in p.present():
        if t.export and t.export_name:
            if t.export_name in names:
                out.append(f"Exportname „{t.export_name}“ doppelt: „{names[t.export_name]}“ und „{t.name}“")
            names.setdefault(t.export_name, t.name)
    used: dict[int, str] = {}
    for t in p.present():
        if t.export and t.channel:
            for c in (t.channel, t.channel + 1):
                if c in used:
                    out.append(f"Kanal {c} doppelt: „{used[c]}“ und „{t.name}“")
                used[c] = t.name
    return out


# ── Ableiten für Triage, Spuransicht, Export und Aufnahme ─────────────────────
def triage_targets(p: TrackProfile) -> dict[str, tuple[str, ...]]:
    """Zielspuren je Kategorie in Profil- (= Session-)Reihenfolge."""
    return {c: tuple(t.name for t in p.present() if t.is_audio and t.triage == c) for c in CATEGORIES}


def track_view(p: TrackProfile, keep: Iterable[str] = ()) -> dict[str, Any]:
    """Einstellungen für `workflow.fetch.apply_track_view`: eingeblendet werden Triage-Ziele und angehakte Spuren
    mit Clips, ausgeblendet nur angehakte leere Spuren."""
    hideable = [t.name for t in p.present() if t.hide_when_empty]
    targets = [t.name for t in p.present() if t.triage or t.hide_when_empty]
    never = {t.name for t in p.present() if t.export or any(getattr(t, k) for k in RECORDING)} | set(keep)
    return {"show_clips": p.show_clips, "hide_empty": p.hide_empty, "keep": sorted(never),
            "targets": targets, "hideable": hideable}


def export_table(p: TrackProfile, channels_total: int = 16) -> list[dict[str, Any]]:
    """Spurtabelle wie `export.spuren_lautheit.spurtabelle` (Zeile = Kanalpaar): Spuren mit Kanal an ihren Platz,
    die übrigen Exportspuren der Reihe nach in freie Paare; freie Paare bleiben leer (Stille)."""
    pairs = channels_total // 2
    rows: list[dict[str, Any] | None] = [None] * pairs
    later = []
    for t in p.present():
        if not (t.is_audio and t.export):
            continue
        row = {"pt_spur": t.name, "inhalt": t.content, "export": True, "r128": t.r128, "exportname": t.export_name}
        i = (t.channel - 1) // 2 if t.channel else -1
        if 0 <= i < pairs and rows[i] is None and t.channel % 2 == 1:
            rows[i] = row
        else:
            later.append(row)
    for row in later:
        free = next((i for i, r in enumerate(rows) if r is None), None)
        if free is None:
            raise ValueError(f"Zu viele Exportspuren für {channels_total} Kanäle")
        rows[free] = row
    last = max((i for i, r in enumerate(rows) if r is not None), default=-1)
    return [r if r is not None else {"pt_spur": "", "inhalt": "leer", "export": False, "r128": False}
            for r in rows[:last + 1]]


def recording(p: TrackProfile) -> dict[str, list[str]]:
    """Aufnahme- und Abhörspuren wie `aufnahme.spuren.*` (Spurnamen in Session-Reihenfolge)."""
    return {k: [t.name for t in p.present() if t.is_audio and getattr(t, k)] for k in RECORDING}


def target_ranges(p: TrackProfile):
    """Zielspuren für die Triage (`triage.placement.TargetRanges`) aus dem Profil – Rest ausdrücklich."""
    from tonmischung.triage.placement import TargetRanges
    t = triage_targets(p)
    return TargetRanges(oton=t["o_ton"], atmo=t["atmo"], music=t["musik"], rest=t["rest"], when_full=p.when_full)


def defaults_from_settings(get) -> Defaults:
    """Vorbelegung aus den heutigen Einstellungen (`get(pfad)`); unlesbare Bereiche bleiben leer."""
    from tonmischung.triage.placement import expand_range
    base = "import.zielspuren.bereiche."

    def rng(cat: str) -> tuple[str, ...]:
        try:
            return expand_range(str(get(base + cat + ".von") or ""), str(get(base + cat + ".bis") or ""))
        except ValueError:
            return ()
    m = re.fullmatch(r"(\D*?)\s*(\d+)", str(get(base + "rest.von") or "A11").strip())
    bis = re.search(r"(\d+)$", str(get(base + "rest.bis") or "").strip())
    from tonmischung.core.track_view import prefixes_from_ranges
    prefixes = prefixes_from_ranges({k: {e: get(f"{base}{k}.{e}") for e in ("von", "bis")} for k in CATEGORIES})
    return Defaults(oton=rng("o_ton"), atmo=rng("atmo"), music=rng("musik"),
                    rest_prefix=m.group(1) if m else "A", rest_from=int(m.group(2)) if m else 11,
                    rest_to=int(bis.group(1)) if bis else None, hide_prefixes=tuple(prefixes),
                    table=tuple(get("export.spuren_lautheit.spurtabelle") or ()),
                    recording={k: list(get(f"aufnahme.spuren.{k}") or ()) for k in RECORDING})


def layer_values(p: TrackProfile) -> dict[str, Any]:
    """Einstellungen, die das Profil für die offene Session vorgibt (Ebene „Spurprofil“). Nur was im Profil
    angehakt ist: ohne Exportspur bleibt die allgemeine Spurtabelle, ohne Aufnahme-Häkchen bleiben die
    allgemeinen Aufnahmespuren – ein reines Import-Profil stört Export und Aufnahme nicht."""
    out: dict[str, Any] = {}
    if any(t.export and t.is_audio for t in p.present()):
        out["export.spuren_lautheit.spurtabelle"] = export_table(p)
    rec = recording(p)
    if any(rec.values()):
        out.update({f"aufnahme.spuren.{k}": v for k, v in rec.items()})
    return out


# ── Exportieren und Importieren (Jens 03.10.2026: „wir machen die Profile exportierbar und importierbar“) ──
FORMAT = "tonmischung-spurprofile"
FORMAT_VERSION = 1
NEW, REPLACE, SAME = "neu", "ersetzt", "gleich"


class ImportFormatError(ValueError):
    """Datei ist keine Spurprofil-Datei von Ottilie."""


def export_document(profiles: Sequence[TrackProfile], export_names: Sequence[Mapping] = (),
                    when: str = "", source: str = "") -> dict[str, Any]:
    """Datei-Inhalt: Profile und die Exportnamen-Auswahl (damit die Namen am anderen Mac vorhanden sind)."""
    return {"format": FORMAT, "version": FORMAT_VERSION, "exportiert_am": when, "von": source,
            "exportnamen": [dict(e) for e in export_names],
            "profile": [profile_to_dict(p) for p in profiles]}


def read_document(doc: Any) -> tuple[list[TrackProfile], list[dict]]:
    if not isinstance(doc, Mapping) or doc.get("format") != FORMAT:
        raise ImportFormatError("Keine Spurprofil-Datei von Ottilie")
    if int(doc.get("version") or 0) > FORMAT_VERSION:
        raise ImportFormatError("Die Datei stammt aus einer neueren Version von Ottilie")
    profiles = profiles_from_settings(doc.get("profile"))
    if not profiles:
        raise ImportFormatError("Die Datei enthält kein Spurprofil")
    names = [dict(e) for e in doc.get("exportnamen") or [] if isinstance(e, Mapping) and e.get("name")]
    return profiles, names


def profile_key(p: TrackProfile) -> str:
    return f"{_key(p.template_group)}/{_key(p.template_name)}"


def import_plan(incoming: Sequence[TrackProfile], existing: Sequence[TrackProfile]) -> list[tuple[TrackProfile, str]]:
    """Je eingelesenem Profil: neu, ersetzt (anderes Profil für dasselbe Template) oder gleich."""
    have = {profile_key(p): p for p in existing}
    out = []
    for p in incoming:
        old = have.get(profile_key(p))
        out.append((p, NEW if old is None else SAME if profile_to_dict(old) == profile_to_dict(p) else REPLACE))
    return out


def apply_import(existing: Sequence[TrackProfile], chosen: Sequence[TrackProfile]) -> list[TrackProfile]:
    """Gewählte Profile übernehmen: gleiches Template wird ersetzt, sonst angehängt."""
    out = list(existing)
    for p in chosen:
        i = next((k for k, q in enumerate(out) if profile_key(q) == profile_key(p)), None)
        if i is None:
            out.append(p)
        else:
            out[i] = p
    return out


def merge_export_names(current: Sequence[Mapping], incoming: Sequence[Mapping]) -> list[dict]:
    """Fehlende Exportnamen ergänzen; vorhandene bleiben, wie sie sind."""
    out = [dict(e) for e in current]
    known = {str(e.get("name")) for e in out}
    for e in incoming:
        if str(e.get("name")) not in known:
            out.append(dict(e))
            known.add(str(e.get("name")))
    return out
