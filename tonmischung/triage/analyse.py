"""AAF-Triage: jeden Clip lesen und beurteilen – Lage, Quellname, Signal.

Lage wie in `aafcheck.expected_clips` (so, wie Pro Tools die Clips zeigt; 568/568 an
der Premiere-AAF bestätigt). Essenz und Inhaltserkennung kommen aus Hedwig
(vendor/hedwig, unverändert).
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from tonmischung.triage.aafcheck import _timecode
from tonmischung.triage.decide import ClipDecision, ClipFacts, TrackVerdict, triage_tracks
from tonmischung.triage.names import ATMO, OTON, source_hint
from tonmischung.vendor import use_hedwig

# ANNAHME: Unter 1 s ist das Urteil aus dem Signal nicht belastbar – solche Clips folgen
# ihrer Spur.
MIN_SIGNAL_S = 1.0

# Avid-Belegung des Hauses (Jens, 28.07.2026, aus Hedwig): unbenannte Spuren A1/A2 O-Ton,
# A3/A4 Atmo. Nur Rückfall, wenn die Spur selbst nichts Klares sagt.
AVID_FALLBACK = {"A1": OTON, "A2": OTON, "A3": ATMO, "A4": ATMO}

Classify = Callable[[Any, int], tuple[str, float]]
Progress = Callable[[float, str], None]


@dataclass
class AafTrack:
    name: str
    unnamed: bool


@dataclass
class Analysis:
    path: str
    producer: str
    fps: int
    start_frames: int
    tracks: list[AafTrack]
    clips: list[ClipFacts]
    unreadable: int = 0          # Clips ohne lesbare Essenz


@dataclass
class TriageResult:
    analysis: Analysis
    verdicts: dict[str, TrackVerdict]
    decisions: list[ClipDecision] = field(default_factory=list)
    pairs: list[tuple[str, str]] = field(default_factory=list)


def _walk(seg: Any, pos: Fraction, out: list, max_len: Fraction | None = None) -> None:
    """Clips (Anfang, Ende, SourceClip, Länge, Blende ein, Blende aus) in Edit Units der Spur;
    Grenzen ohne Fade-Bereich (so zeigt Pro Tools sie)."""
    cls = seg.__class__.__name__
    if cls == "Sequence":
        comps = list(seg.components)
        for i, c in enumerate(comps):
            if c.__class__.__name__ == "Transition":
                pos -= int(c.length)
                continue
            fade_in = int(comps[i - 1].length) if i > 0 and comps[i - 1].__class__.__name__ == "Transition" else 0
            fade_out = (int(comps[i + 1].length)
                        if i + 1 < len(comps) and comps[i + 1].__class__.__name__ == "Transition" else 0)
            n = len(out)
            _walk(c, pos, out)
            if len(out) == n + 1:
                a, b, clip, length, _, _ = out[-1]
                out[-1] = (a + fade_in, b - fade_out, clip, length, fade_in, fade_out)
            pos += int(c.length)
    elif cls == "OperationGroup":
        segs = list(seg.segments)
        if segs:
            _walk(segs[0], pos, out, Fraction(int(seg.length)))
    elif cls == "SourceClip":
        length = Fraction(int(seg.length))
        if max_len is not None:
            length = min(length, max_len)
        out.append((pos, pos + length, seg, length, 0, 0))


def _source_names(clip: Any, essenz: Any, mob: Any) -> list[str]:
    """Namen entlang der Mob-Kette bis zur Datei, dazu Locator-Pfade."""
    names: list[str] = []
    m, depth = getattr(clip, "mob", None), 0
    while m is not None and depth < 8:
        if m.name:
            names.append(str(m.name))
        nxt = None
        for s in m.slots:
            seg = s.segment
            if seg.__class__.__name__ == "SourceClip" and getattr(seg, "mob", None) is not None:
                nxt = seg.mob
                break
        m, depth = nxt, depth + 1
    if mob is not None:
        try:
            names += [p for p in essenz.locator_pfade(mob) if p]
        except Exception:  # noqa: BLE001 – Pfade sind nur Hinweise
            pass
    return list(dict.fromkeys(names))


def _hedwig_classify(x: Any, sr: int) -> tuple[str, float]:
    import hedwig_klassifikation as kl
    r = kl.klassifiziere(x, sr)
    return r["art"], float(r["konfidenz"])


def analyse(path: str, *, keywords: Mapping[str, Sequence[str]] | None = None,
            music_paths: Sequence[str] = (), classify: Classify | None = None,
            use_source_names: bool = True, use_signal: bool = True,
            progress: Progress | None = None) -> Analysis:
    """Alle Clips der AAF mit Lage, Quellnamen-Hinweis und Signal-Urteil.

    `use_source_names` / `use_signal` entsprechen den Regeln „quelldatei_herkunft“ und
    „inhaltserkennung“ der Einstellung `import.zuordnungsregeln.kaskade`.
    """
    use_hedwig()
    import aaf2
    import hedwig_aaf_essenz as essenz
    keywords = keywords or {}
    classify = classify or _hedwig_classify
    store = essenz.Essenzspeicher(budget_mb=1024, suchorte=[os.path.dirname(path)])
    tracks: list[AafTrack] = []
    facts: list[ClipFacts] = []
    unreadable = 0
    with aaf2.open(path, "r") as f:
        try:
            producer = str(f.header["IdentificationList"].value[-1]["ProductName"].value)
        except Exception:  # noqa: BLE001
            producer = ""
        comps = list(f.content.toplevel())
        if not comps:
            raise ValueError("AAF enthält keine Sequenz")
        seq = comps[0]
        start, fps = 0, 25
        for slot in seq.slots:
            if getattr(slot.segment, "media_kind", "") == "Timecode":
                tc = _timecode(slot.segment)
                if tc is not None:
                    start, fps = int(tc.start), int(tc.fps)
                    break
        sound = [s for s in seq.slots if getattr(s.segment, "media_kind", "") == "Sound"]
        walked = []
        for n, slot in enumerate(sound, 1):
            name = str(slot.name).strip() if slot.name else ""
            tracks.append(AafTrack(name or f"A{n}", not name))
            rate = Fraction(str(essenz.zahl(slot.edit_rate) or fps)).limit_denominator(1001)
            out: list = []
            _walk(slot.segment, Fraction(0), out)
            walked.append((tracks[-1].name, rate, out))
        total = sum(len(o) for _, _, o in walked) or 1
        done = 0
        for name, rate, out in walked:
            for i, (a, b, clip, length, f_in, f_out) in enumerate(out):
                done += 1
                if progress:
                    progress(done / total, f"{name}: Clip {i + 1} von {len(out)}")
                q = essenz.aufloesen(clip, float(rate))
                names = _source_names(clip, essenz, q.mob if q else None)
                hint = source_hint(names, music_keywords=keywords.get("musik", ()),
                                   oton_keywords=keywords.get("o_ton", ()),
                                   atmo_keywords=keywords.get("atmo", ()),
                                   music_paths=music_paths) if use_source_names else None
                if hint is None and names and names[0].strip().lower() == "fade":
                    hint = source_hint(names)                  # Überblendung immer erkennen
                signal, conf = None, 0.0
                got = store.hole(q.mob) if q and use_signal else None
                if got is None:
                    unreadable += 1 if use_signal else 0
                else:
                    audio, sr = got
                    x = audio[:, 0] if getattr(audio, "ndim", 1) > 1 else audio
                    i0 = int(q.offset_s * sr)
                    x = x[i0:i0 + int(float(length / rate) * sr)]
                    if x.size >= MIN_SIGNAL_S * sr:
                        signal, conf = classify(x, sr)
                to_frames = Fraction(fps) / rate
                facts.append(ClipFacts(name, i, start + int(round(a * to_frames)),
                                       start + int(round(b * to_frames)),
                                       names[0] if names else "", hint, signal, conf,
                                       int(round(f_in * to_frames)), int(round(f_out * to_frames))))
    return Analysis(path, producer, fps, start, tracks, facts, unreadable)


def triage_aaf(path: str, *, threshold: float, keywords: Mapping[str, Sequence[str]] | None = None,
               music_paths: Sequence[str] = (), classify: Classify | None = None,
               use_track_names: bool = True, use_source_names: bool = True, use_signal: bool = True,
               progress: Progress | None = None) -> TriageResult:
    """Ganze AAF beurteilen: Kategorie je Spur und je Clip (noch ohne Zielspuren)."""
    a = analyse(path, keywords=keywords, music_paths=music_paths, classify=classify,
                use_source_names=use_source_names, use_signal=use_signal, progress=progress)
    fallback = ({t.name: AVID_FALLBACK[t.name] for t in a.tracks if t.unnamed and t.name in AVID_FALLBACK}
                if "media composer" in a.producer.lower() else {})
    pairs, verdicts, decisions = triage_tracks([t.name for t in a.tracks], a.clips, threshold=threshold,
                                               keywords=keywords if use_track_names else None,
                                               fallback=fallback)
    return TriageResult(a, verdicts, decisions, pairs)
