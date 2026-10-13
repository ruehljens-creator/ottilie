"""Nach dem Import: A- und Musik-Spuren mit Clips sichtbar machen, leere ausblenden (reine Logik).

Jens (03.10.2026): „alle Spuren, auf denen sich Clips befinden, einblenden … dafür kannst du auch gerne Spuren,
die keine Clips enthalten, ausblenden“ – „unsere Stem- und Spr-Spuren und auch die Videospur sollen immer erhalten
bleiben. Es geht nur um die A-Spuren und die Musik-Spuren.“ Angefasst werden deshalb nur die Zielspuren des
Imports (Namen wie A1, A12, Musik3 – Vorsilben aus den Zielspuren der Triage); deren Ordnerspuren werden
aufgeklappt, wenn darin Clips liegen. Alles andere bleibt, wie es ist.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

AUDIO = "AudioTrack"


@dataclass(frozen=True)
class TrackState:
    name: str
    kind: str                     # TrackType-Name aus PTSL, z. B. „AudioTrack“, „BasicFolder“
    has_clips: bool
    hidden: bool
    folder: str = ""              # Name der Ordnerspur, in der die Spur liegt
    is_open: bool = True          # bei Ordnerspuren: aufgeklappt
    has_automation: bool = False


@dataclass(frozen=True)
class ViewPlan:
    show: tuple[str, ...]
    hide: tuple[str, ...]
    open_folders: tuple[str, ...]


DEFAULT_PREFIXES = ("A", "Musik")


def prefixes_from_ranges(ranges: Mapping | None) -> tuple[str, ...]:
    """Vorsilben der Zielspuren aus `import.zielspuren.bereiche` (A1–A4, Musik1–Musik8, A11–… → A, Musik)."""
    out: list[str] = []
    for r in (ranges or {}).values():
        for name in ((r or {}).get("von"), (r or {}).get("bis")):
            m = re.fullmatch(r"(.*?)\s*\d+", str(name or ""))
            if m and m.group(1) and m.group(1) not in out:
                out.append(m.group(1))
    return tuple(out) or DEFAULT_PREFIXES


def is_target(name: str, prefixes: Sequence[str] = DEFAULT_PREFIXES) -> bool:
    return any(re.fullmatch(re.escape(p) + r"\s*\d+", name) for p in prefixes)


def plan_view(tracks: Sequence[TrackState], keep: Iterable[str] = (), show_clips: bool = True,
              hide_empty: bool = True, prefixes: Sequence[str] = DEFAULT_PREFIXES,
              targets: Iterable[str] | None = None, hideable: Iterable[str] | None = None) -> ViewPlan:
    """Ohne Spurprofil: A- und Musik-Spuren (Vorsilben). Mit Spurprofil: `targets` (Triage-Ziele und angehakte
    Spuren) werden mit Clips eingeblendet, nur `hideable` (Häkchen „Ausblenden, wenn leer“) leer ausgeblendet."""
    keep = set(keep)
    by_name = {t.name: t for t in tracks}
    if targets is not None:
        wanted = set(targets)
        tracks = [t for t in tracks if t.name in wanted]
    else:
        tracks = [t for t in tracks if is_target(t.name, prefixes)]      # nur A- und Musik-Spuren
    may_hide = set(hideable) if hideable is not None else {t.name for t in tracks}
    show: list[str] = []
    folders: list[str] = []
    if show_clips:
        for t in tracks:
            if not t.has_clips:
                continue
            if t.hidden:
                show.append(t.name)
            parent, seen = t.folder, set()
            while parent and parent not in seen:                 # alle Ordner darüber aufklappen und zeigen
                seen.add(parent)
                f = by_name.get(parent)
                if f is None:
                    break
                if not f.is_open and f.name not in folders:
                    folders.append(f.name)
                if f.hidden and f.name not in show:
                    show.append(f.name)
                parent = f.folder
    hide = []
    if hide_empty:
        hide = [t.name for t in tracks if t.kind == AUDIO and not t.has_clips and not t.hidden
                and not t.has_automation and t.name not in keep and t.name in may_hide]
    return ViewPlan(tuple(show), tuple(hide), tuple(folders))
