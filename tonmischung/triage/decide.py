"""Triage-Entscheidung: Kategorie je Spur und je Clip – reine Logik.

Vorgaben (Jens, 02.10.2026, Plan Triage):
- im Wesentlichen Musik erkennen und auf die Musikspuren legen; Musik liegt nicht auf
  O-Ton- oder Atmospuren, außer als Hintergrundmusik in der Atmo
- Quellname vor Signal; O-Ton/Atmo nach der Spur, ein Clip weicht nur bei hoher Sicherheit ab
- „Spr 1–3“ per Name auf die gleichnamigen Spuren
- nichts weglassen: was nicht zuzuordnen ist (auch die zweite Fassung), kommt ab A11
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tonmischung.triage.names import (
    ATMO, H_FADE, H_LIBRARY, H_LOCATION, H_MUSIC, MUSIC, OTON, REST, SPEAKER, NameHint,
    base_track_name, is_speaker_track, second_versions, track_keyword)

SIGNAL_CATEGORY = {"sprache": OTON, "atmo": ATMO, "musik": MUSIC}
LABEL = {OTON: "O-Ton", ATMO: "Atmo", MUSIC: "Musik", REST: "ab A11", SPEAKER: "Sprecher"}

# ANNAHME: Eine Spur hat eine Kategorie, wenn mindestens 60 % ihrer sicheren Clips sie haben.
TRACK_MAJORITY = 0.6


@dataclass(frozen=True)
class ClipFacts:
    track: str
    index: int                  # Reihenfolge auf der Spur
    start: int                  # Frames, so wie Pro Tools den Clip zeigt
    end: int
    source: str                 # sprechendster Quellname (für den Bericht)
    hint: NameHint | None = None
    signal: str | None = None   # sprache / atmo / musik / stille; None = nicht beurteilt
    confidence: float = 0.0
    fade_in: int = 0            # Blende davor (Frames), liegt vor `start`
    fade_out: int = 0           # Blende danach (Frames), liegt nach `end`


@dataclass(frozen=True)
class TrackVerdict:
    track: str
    category: str
    reason: str


@dataclass(frozen=True)
class ClipDecision:
    clip: ClipFacts
    category: str
    reason: str
    deviates: bool = False      # weicht von der Kategorie seiner Spur ab


def _vote(c: ClipFacts, threshold: float) -> str | None:
    """Sichere Aussage eines Clips über seine Kategorie, sonst None."""
    if c.hint and c.hint.kind == H_MUSIC:
        return MUSIC
    if c.hint and c.hint.kind == H_LIBRARY:
        return ATMO
    if c.signal in SIGNAL_CATEGORY and c.confidence >= threshold:
        if c.signal == "musik" and c.hint and c.hint.kind == H_LOCATION:
            return None                      # Musik im Drehort-Ton: Hintergrundmusik
        return SIGNAL_CATEGORY[c.signal]
    return None


def track_verdicts(track_names: Sequence[str], clips: Sequence[ClipFacts], *, threshold: float,
                   keywords: Mapping[str, Sequence[str]] | None = None,
                   fallback: Mapping[str, str] | None = None) -> dict[str, TrackVerdict]:
    """Kategorie je AAF-Spur.

    Reihenfolge: Sprecherspur, zweite Fassung, Stichwort im Spurnamen, Mehrheit der
    sicheren Clips, Rückfall (z. B. Avid-Belegung des Hauses), sonst „ab A11“.
    """
    keywords = keywords or {}
    fallback = fallback or {}
    seconds = second_versions(list(track_names))
    by_track: dict[str, list[ClipFacts]] = {t: [] for t in track_names}
    for c in clips:
        by_track.setdefault(c.track, []).append(c)
    result = {}
    for t in track_names:
        if is_speaker_track(t):
            result[t] = TrackVerdict(t, SPEAKER, "Sprecherspur, per Name auf die gleichnamige Spur")
            continue
        if t in seconds:
            result[t] = TrackVerdict(t, REST, f"zweite Fassung von „{seconds[t]}“")
            continue
        kw = track_keyword(t, dict(keywords))
        if kw:
            result[t] = TrackVerdict(t, kw, "Stichwort im Spurnamen")
            continue
        votes = Counter(v for v in (_vote(c, threshold) for c in by_track[t]) if v)
        total = sum(votes.values())
        if total:
            category, n = votes.most_common(1)[0]
            if n / total >= TRACK_MAJORITY:
                result[t] = TrackVerdict(t, category, f"{n} von {total} sicheren Clips {LABEL[category]}")
                continue
        if t in fallback:
            result[t] = TrackVerdict(t, fallback[t], "Spurbelegung (Rückfall)")
            continue
        why = "keine sicheren Clips" if not total else "keine klare Mehrheit"
        result[t] = TrackVerdict(t, REST, f"Spur ohne klare Kategorie ({why})")
    return result


def _decide_one(c: ClipFacts, track: TrackVerdict, threshold: float) -> tuple[str, str]:
    cat = track.category
    if cat == SPEAKER:
        return SPEAKER, "Sprecherspur"
    if track.reason.startswith("zweite Fassung"):
        return REST, track.reason
    h = c.hint
    if h and h.kind == H_MUSIC:
        return MUSIC, h.reason
    if h and h.kind == H_LIBRARY:
        return ATMO, h.reason
    sure = c.signal in SIGNAL_CATEGORY and c.confidence >= threshold
    if h and h.kind == H_LOCATION:
        # Drehort ist nie dazugelegte Musik; hörbare Musik darin bleibt in der Atmo
        if cat in (OTON, ATMO):
            return cat, f"{h.reason}, Spur"
        if sure and c.signal != "musik":
            return SIGNAL_CATEGORY[c.signal], f"{h.reason}, Signal {c.confidence:.2f}"
        return REST, f"{h.reason}, Spur ohne passende Kategorie"
    if sure and c.signal == "musik":
        return MUSIC, f"Signal Musik {c.confidence:.2f}"
    if cat == MUSIC:
        return MUSIC, "Musikspur"                     # Gesang o. Ä. bleibt auf der Musik
    if cat in (OTON, ATMO):
        if sure and SIGNAL_CATEGORY[c.signal] != cat:
            return SIGNAL_CATEGORY[c.signal], f"Signal {LABEL[SIGNAL_CATEGORY[c.signal]]} {c.confidence:.2f}"
        return cat, "Spur"
    if sure:
        return SIGNAL_CATEGORY[c.signal], f"Signal {c.confidence:.2f}"
    return REST, "nicht zuzuordnen"


def decide(clips: Sequence[ClipFacts], verdicts: Mapping[str, TrackVerdict], *,
           threshold: float) -> list[ClipDecision]:
    """Kategorie je Clip. Überblendungen („Fade“) folgen dem Nachbarclip ihrer Spur."""
    out: list[ClipDecision] = []
    for c in clips:
        track = verdicts[c.track]
        if c.hint and c.hint.kind == H_FADE:
            out.append(ClipDecision(c, "", ""))          # unten aufgelöst
            continue
        cat, why = _decide_one(c, track, threshold)
        out.append(ClipDecision(c, cat, why, deviates=cat != track.category))
    for i, d in enumerate(out):
        if d.category:
            continue
        track_cat = verdicts[d.clip.track].category
        same = [o for o in out if o.clip.track == d.clip.track and o.category and o is not d]
        touching = {o.category for o in same
                    if abs(o.clip.end - d.clip.start) <= 1 or abs(o.clip.start - d.clip.end) <= 1}
        if len(touching) == 1:
            cat, why = touching.pop(), "Überblendung, folgt Nachbarclip"
        elif touching:
            cat, why = track_cat, "Überblendung zwischen verschiedenen Kategorien, bleibt bei der Spur"
        else:
            cat, why = track_cat, "Überblendung, Spur"
        out[i] = ClipDecision(d.clip, cat, why, cat != track_cat)
    return out


# ── Paare (Doppelmono / Stereo) ──────────────────────────────────────────────

# Gleicher Schnitt: gleich viele Clips, jede Grenze auf ±1 Frame. Die Toleranz braucht es,
# weil Blenden auf den beiden Hälften um einen Frame verschieden gerundet sein können
# („make it“ Audio 9/10, Premiere: 2 von 42 Clips).
CUT_TOLERANCE = 1


def same_cut(a: Sequence[ClipFacts], b: Sequence[ClipFacts]) -> bool:
    return bool(a) and len(a) == len(b) and all(
        abs(x.start - y.start) <= CUT_TOLERANCE and abs(x.end - y.end) <= CUT_TOLERANCE
        for x, y in zip(a, b))


def find_pairs(track_names: Sequence[str], clips: Sequence[ClipFacts]) -> list[tuple[str, str]]:
    """Benachbarte Spuren mit gleichem Namen oder identischen Clipgrenzen (Hedwig: Name, Schnitt).

    Pan aus der AAF wird ignoriert (Jens). Erste und zweite Fassungen werden getrennt
    gepaart – sonst bildeten „Audio 3_FX“ und „Audio 3_No FX“ (gleicher Schnitt) ein Paar.
    Sprecherspuren werden nicht gepaart.
    """
    seconds = second_versions(list(track_names))
    cuts: dict[str, list[ClipFacts]] = {t: [] for t in track_names}
    for c in clips:
        cuts.setdefault(c.track, []).append(c)

    def adjacent(names: list[str]) -> list[tuple[str, str]]:
        pairs, i = [], 0
        while i < len(names) - 1:
            a, b = names[i], names[i + 1]
            same_name = base_track_name(a).lower() == base_track_name(b).lower()
            if same_name or same_cut(cuts[a], cuts[b]):
                pairs.append((a, b))
                i += 2
            else:
                i += 1
        return pairs
    candidates = [t for t in track_names if not is_speaker_track(t)]
    return (adjacent([t for t in candidates if t not in seconds])
            + adjacent([t for t in candidates if t in seconds]))


_HINT_ORDER = {H_FADE: 0, H_MUSIC: 1, H_LIBRARY: 2, H_LOCATION: 3}


def _joint(a: ClipFacts, b: ClipFacts) -> ClipFacts:
    """Beide Hälften als ein Clip: stärkster Namenshinweis; Signal nur, wenn beide dasselbe
    sagen, mit der kleineren Sicherheit – sicher ist der Clip nur, wenn beide Hälften es sind."""
    hints = [h for h in (a.hint, b.hint) if h]
    hint = min(hints, key=lambda h: _HINT_ORDER[h.kind]) if hints else None
    if a.signal == b.signal:
        signal, conf = a.signal, min(a.confidence, b.confidence)
    else:
        signal, conf = None, 0.0
    return ClipFacts(a.track, a.index, a.start, a.end, a.source, hint, signal, conf, a.fade_in, a.fade_out)


def triage_tracks(track_names: Sequence[str], clips: Sequence[ClipFacts], *, threshold: float,
                  keywords: Mapping[str, Sequence[str]] | None = None,
                  fallback: Mapping[str, str] | None = None
                  ) -> tuple[list[tuple[str, str]], dict[str, TrackVerdict], list[ClipDecision]]:
    """Paare bilden, dann je Paar gemeinsam entscheiden; einzelne Spuren für sich."""
    pairs = find_pairs(track_names, clips)
    right_of = {b: a for a, b in pairs}
    by_track: dict[str, list[ClipFacts]] = {t: [] for t in track_names}
    for c in clips:
        by_track.setdefault(c.track, []).append(c)
    joined: dict[str, list[ClipFacts]] = {}
    for a, b in pairs:
        if same_cut(by_track[a], by_track[b]):
            joined[a] = [_joint(x, y) for x, y in zip(by_track[a], by_track[b])]
    lead = [t for t in track_names if t not in right_of]
    lead_clips = [c for t in lead for c in joined.get(t, by_track[t])]
    # Spurkategorie eines Paares aus den Stimmen beider Hälften (ohne gleichen Schnitt) bzw. gemeinsam
    vote_clips = list(lead_clips) + [c for b, a in right_of.items() if a not in joined
                                     for c in (ClipFacts(a, *_rest(x)) for x in by_track[b])]
    verdicts = track_verdicts(lead, vote_clips, threshold=threshold, keywords=keywords, fallback=fallback)
    for b, a in right_of.items():
        v = verdicts[a]
        verdicts[b] = TrackVerdict(b, v.category, f"{v.reason} – Paar mit „{a}“")
    lead_decisions = decide(lead_clips, verdicts, threshold=threshold)
    out: list[ClipDecision] = []
    by_lead = {}
    for d in lead_decisions:
        by_lead.setdefault(d.clip.track, []).append(d)
    for t in track_names:
        if t in right_of and right_of[t] in joined:
            out += [ClipDecision(c, d.category, d.reason, d.deviates)
                    for c, d in zip(by_track[t], by_lead[right_of[t]])]
        elif t in right_of:
            out += decide(by_track[t], verdicts, threshold=threshold)
        elif t in joined:
            out += [ClipDecision(c, d.category, d.reason, d.deviates)
                    for c, d in zip(by_track[t], by_lead[t])]
        else:
            out += by_lead.get(t, [])
    return pairs, verdicts, out


def _rest(c: ClipFacts) -> tuple:
    return (c.index, c.start, c.end, c.source, c.hint, c.signal, c.confidence, c.fade_in, c.fade_out)
