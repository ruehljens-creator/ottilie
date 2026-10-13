"""Triage T2: Clips auf die Zielspuren des Templates verteilen – reine Logik.

Weg C (Jens, 02.10.2026): Jede AAF-Spur kommt per Match Tracks mit Umbenennen auf eine
Template-Spur ihrer Kategorie; danach werden nur die abweichenden Clips verschoben.

- Zielspuren laufen paarweise (Doppelmono/Stereo): A1/A2, A3/A4 (O-Ton), A5/A6 … A9/A10
  (Atmo), Musik1/2 … Musik7/8, Rest ab A11/A12 – nur vorhandene Template-Spuren, keine neue
  Spur (Jens). Belegte Template-Spuren (mit Clips) werden übersprungen.
- Eine einzelne Mono-Spur kommt auf die linke Spur eines Paares und wird auf die rechte
  kopiert (Mono-Ereignisse immer doppelt, Jens).
- „Spr 1–3“ gehen auf die gleichnamigen Spuren.
- Reichen die Spuren einer Kategorie nicht, geht es ab A11 weiter; reicht auch das nicht,
  wird das gemeldet (PlacementError) – nichts wird still weggelassen.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tonmischung.triage.decide import LABEL, ClipDecision, ClipFacts, TrackVerdict
from tonmischung.triage.names import ATMO, MUSIC, OTON, REST, SPEAKER, base_track_name

Pair = tuple[str, ...]          # (links, rechts) oder (eine Spur)


class PlacementError(Exception):
    pass


@dataclass(frozen=True)
class TrackMove:
    """AAF-Spur → Template-Spur (Match Tracks, Template-Spur dafür umbenannt)."""
    aaf_track: str
    target: str


@dataclass(frozen=True)
class ClipMove:
    """Bereich von Quellspur(en) auf Zielspur(en) verschieben."""
    sources: tuple[str, ...]        # Session-Spuren (Template-Namen), 1 oder 2
    targets: tuple[str, ...]        # gleich viele – oder 2 bei verdoppelter Mono-Spur
    start: int                      # Frames, einschließlich Blenden
    end: int
    category: str
    clips: tuple[ClipFacts, ...]
    parts: tuple[tuple[ClipFacts, ...], ...] = ()   # je Zielspur die dort erwarteten Clips


@dataclass(frozen=True)
class TrackCopy:
    """Alle Clips einer Spur auf die Partnerspur kopieren (Doppelmono)."""
    source: str
    target: str


@dataclass
class Placement:
    track_moves: list[TrackMove] = field(default_factory=list)
    clip_moves: list[ClipMove] = field(default_factory=list)
    copies: list[TrackCopy] = field(default_factory=list)
    # wo jeder Clip am Ende liegt: (AAF-Spur, Nr.) → Session-Spuren
    final: dict[tuple[str, int], tuple[str, ...]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


ORDER = (OTON, ATMO, MUSIC, REST)          # Gruppen in Template-Reihenfolge („nächste Gruppe“)


@dataclass(frozen=True)
class TargetRanges:
    """Zielspuren je Kategorie, paarweise in Template-Reihenfolge (Einstellung import.zielspuren.bereiche)."""
    oton: tuple[str, ...] = ("A1", "A2", "A3", "A4")
    atmo: tuple[str, ...] = ("A5", "A6", "A7", "A8", "A9", "A10")
    music: tuple[str, ...] = tuple(f"Musik{i}" for i in range(1, 9))
    rest_from: int = 11             # Rest: alle vorhandenen Spuren ab A11 …
    rest_prefix: str = "A"
    rest_to: int | None = None      # … bis hierher (None = ohne Ende)
    rest: tuple[str, ...] | None = None   # ausdrückliche Rest-Spuren (Spurprofil); None = ab rest_from
    # Wenn die Zielspuren einer Kategorie voll sind (Jens 03.10.2026): „reservespuren“ = auf die Rest-Spuren,
    # „verwandte_kategorie“ = auf die folgenden Spuren der nächsten Gruppe (O-Ton → Atmo → Musik → Rest)
    when_full: str = "reservespuren"

    def chain(self, category: str) -> list[str]:
        """Reihenfolge der Kategorien, in denen für `category` gesucht wird."""
        if category == REST or category not in ORDER:
            return [REST] if category == REST else [category, REST]
        if self.when_full == "verwandte_kategorie":
            return list(ORDER[ORDER.index(category):])
        return [category, REST]

    def rest_text(self) -> str:
        return "auf den Rest-Spuren" if self.rest is not None else f"ab {self.rest_prefix}{self.rest_from}"


def expand_range(von: str, bis: str) -> tuple[str, ...]:
    """„A1“ bis „A4“ → A1, A2, A3, A4 (gleicher Präfix, Nummern aufsteigend)."""
    m1, m2 = re.fullmatch(r"(\D*?)\s*(\d+)", von.strip()), re.fullmatch(r"(\D*?)\s*(\d+)", bis.strip())
    if not m1 or not m2 or m1.group(1) != m2.group(1):
        raise ValueError(f"Spurbereich „{von}“ bis „{bis}“ nicht lesbar (gleicher Name mit Nummer erwartet)")
    a, b = int(m1.group(2)), int(m2.group(2))
    return tuple(f"{m1.group(1)}{n}" for n in range(a, b + 1))


def ranges_from_settings(settings) -> TargetRanges:
    p = "import.zielspuren.bereiche."
    rest_von = settings.get(p + "rest.von") or "A11"
    m = re.fullmatch(r"(\D*?)\s*(\d+)", rest_von.strip())
    if not m:
        raise ValueError(f"Rest-Bereich „{rest_von}“ nicht lesbar")
    rest_bis = (settings.get(p + "rest.bis") or "").strip()
    return TargetRanges(
        when_full=str(settings.get("import.zielspuren.wenn_voll") or "reservespuren"),
        oton=expand_range(settings.get(p + "o_ton.von"), settings.get(p + "o_ton.bis")),
        atmo=expand_range(settings.get(p + "atmo.von"), settings.get(p + "atmo.bis")),
        music=expand_range(settings.get(p + "musik.von"), settings.get(p + "musik.bis")),
        rest_from=int(m.group(2)), rest_prefix=m.group(1),
        rest_to=int(re.search(r"(\d+)$", rest_bis).group(1)) if rest_bis and re.search(r"(\d+)$", rest_bis) else None)


def pairs_of(names: Sequence[str]) -> list[Pair]:
    return [tuple(names[i:i + 2]) for i in range(0, len(names) - 1, 2)]


def _rest_tracks(template: Sequence[str], first: int, prefix: str = "A", last: int | None = None) -> list[str]:
    numbered = sorted((int(m.group(1)), t) for t in template
                      if (m := re.fullmatch(re.escape(prefix) + r"(\d+)", t)) and int(m.group(1)) >= first
                      and (last is None or int(m.group(1)) <= last))
    return [t for _, t in numbered]


def _overlaps(busy: list[tuple[int, int]], a: int, b: int) -> bool:
    return any(a < y and x < b for x, y in busy)


def _span(clips: Sequence[ClipFacts]) -> tuple[int, int]:
    return (min(c.start - c.fade_in for c in clips), max(c.end + c.fade_out for c in clips))


def plan(track_names: Sequence[str], pairs: Sequence[tuple[str, str]],
         verdicts: Mapping[str, TrackVerdict], decisions: Sequence[ClipDecision],
         template_tracks: Sequence[str], occupied: set[str],
         ranges: TargetRanges = TargetRanges()) -> Placement:
    """Verteilung berechnen. `occupied` = Template-Spuren, auf denen schon Clips liegen."""
    p = Placement()
    template = list(template_tracks)
    by_track: dict[str, list[ClipDecision]] = {t: [] for t in track_names}
    for d in decisions:
        by_track.setdefault(d.clip.track, []).append(d)

    # Zielpaare je Kategorie (nur vorhandene, freie Template-Spuren)
    def pool(names: Sequence[str]) -> list[Pair]:
        return [pr for pr in pairs_of([n for n in names if n in template])
                if not set(pr) & occupied]
    pools = {OTON: pool(ranges.oton), ATMO: pool(ranges.atmo), MUSIC: pool(ranges.music),
             REST: pool(ranges.rest if ranges.rest is not None
                        else _rest_tracks(template, ranges.rest_from, ranges.rest_prefix, ranges.rest_to))}
    used: set[Pair] = set()
    busy: dict[str, list[tuple[int, int]]] = {t: [] for t in template}

    def take(category: str) -> tuple[Pair, str]:
        for cat in ranges.chain(category):
            for pr in pools[cat]:
                if pr not in used:
                    used.add(pr)
                    return pr, cat
        raise PlacementError(f"Nicht genug freie Zielspuren für {LABEL[category]} – auch {ranges.rest_text()} "
                             "ist nichts mehr frei (es wird keine Spur angelegt)")

    # 1. Spuren: Paare und Einzelspuren in AAF-Reihenfolge (Spuren ohne Clips entfallen)
    partner = {a: b for a, b in pairs}
    left_of = {b: a for a, b in pairs}
    units: list[tuple[str, ...]] = []
    for t in track_names:
        if not by_track[t]:
            continue
        if t in left_of and by_track[left_of[t]]:
            continue                                   # schon mit der linken Hälfte erfasst
        units.append((t, partner[t]) if t in partner and by_track[partner[t]] else (t,))

    def clip_spans(aaf: str) -> list[tuple[int, int]]:
        return [(d.clip.start - d.clip.fade_in, d.clip.end + d.clip.fade_out) for d in by_track[aaf]]

    unit_target: dict[tuple[str, ...], tuple[str, ...]] = {}   # Einheit → Session-Spuren
    for unit in units:
        cat = verdicts[unit[0]].category
        if cat == SPEAKER and len(unit) == 1:
            same = base_track_name(unit[0])
            if same in template and same not in occupied:
                unit_target[unit] = (same,)
                p.track_moves.append(TrackMove(unit[0], same))
                busy[same] += clip_spans(unit[0])
                continue
            p.notes.append(f"„{unit[0]}“: gleichnamige Spur fehlt oder ist belegt – {ranges.rest_text()}")
            cat = REST
        pr, got = take(cat)
        if got != cat:
            p.notes.append(f"{' + '.join(unit)}: keine freie {LABEL[cat]}-Spur mehr – auf {'/'.join(pr)}")
        unit_target[unit] = pr
        for aaf, tgt in zip(unit, pr):
            p.track_moves.append(TrackMove(aaf, tgt))
            busy[tgt] += clip_spans(aaf)
        if len(unit) == 1:                             # Mono ohne Partner: verdoppeln
            p.copies.append(TrackCopy(pr[0], pr[1]))
            busy[pr[1]] += clip_spans(unit[0])
            p.notes.append(f"„{unit[0]}“ ist mono ohne Partner – wird auf {pr[1]} verdoppelt")

    # 2. Abweichende Clips: zusammenhängende Clips gleicher Kategorie als ein Bereich
    for unit in units:
        tg = unit_target[unit]
        halves = [by_track[t] for t in unit]
        joint = len(unit) == 2 and len(halves[0]) == len(halves[1]) and \
            all(x.category == y.category for x, y in zip(*halves))
        if joint:
            lanes = [(tg, list(zip(*halves)))]
        elif len(unit) == 2:
            lanes = [((tg[0],), [(d,) for d in halves[0]]), ((tg[1],), [(d,) for d in halves[1]])]
        else:
            lanes = [((tg[0],), [(d,) for d in halves[0]])]
        for sources, lane in lanes:
            groups: list[list[tuple[ClipDecision, ...]]] = []
            for item in lane:
                if not item[0].deviates:
                    continue
                last = groups[-1][-1][0] if groups else None
                # berührend oder über eine Blende überlappend → ein Bereich
                if last and last.category == item[0].category and \
                        item[0].clip.start - item[0].clip.fade_in <= last.clip.end + last.clip.fade_out + 1:
                    groups[-1].append(item)
                else:
                    groups.append([item])
            # verdoppelte Mono-Spur: Ziel ist ein Paar, Quelle auch die Kopie auf der Partnerspur
            width = 2 if (joint or len(unit) == 1) else 1
            freed = tg if len(unit) == 1 else sources
            for g in groups:
                clips = tuple(c.clip for item in g for c in item)
                per_half = tuple(tuple(item[h].clip for item in g) for h in range(len(g[0])))
                a, b = _span(clips)
                cat = g[0][0].category
                targets = _free_targets(cat, a, b, width, pools, busy, ranges.chain(cat))
                if targets is None:
                    p.notes.append(f"{' + '.join(unit)} {a}–{b}: kein freier Platz für {LABEL[cat]} – "
                                   "bleibt auf der Spur")
                    continue
                for s_ in freed:
                    busy[s_] = [iv for iv in busy[s_] if not (iv[0] >= a and iv[1] <= b)]
                for t in targets:
                    busy[t].append((a, b))
                parts = per_half if len(per_half) == len(targets) else per_half * len(targets)
                p.clip_moves.append(ClipMove(tuple(sources), targets, a, b, cat, clips, parts))

    # 3. Endlage je Clip (für die Prüfung nach dem Import)
    moved = {(c.track, c.index): m.targets for m in p.clip_moves for c in m.clips}
    for unit in units:
        tg = unit_target[unit]
        for i, aaf in enumerate(unit):
            for d in by_track[aaf]:
                key = (aaf, d.clip.index)
                where = moved.get(key, tg)
                p.final[key] = where if len(unit) == 1 or len(where) == 1 else (where[i],)
    return p


def _free_targets(cat: str, a: int, b: int, width: int, pools: Mapping[str, list[Pair]],
                  busy: Mapping[str, list[tuple[int, int]]], chain: Sequence[str] | None = None
                  ) -> tuple[str, ...] | None:
    """Erstes Zielpaar der Kategorie (sonst nach „wenn voll“), das im Bereich frei ist."""
    for c in (chain or ([cat, REST] if cat != REST else [REST])):
        for pr in pools.get(c, []):
            if not any(_overlaps(busy[t], a, b) for t in pr):
                return pr if width == 2 else pr[:1]
    return None
