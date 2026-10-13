"""Triage-Bericht als Text – reine Logik."""
from __future__ import annotations

from collections import Counter

from tonmischung.core.timecode import frames_to_tc
from tonmischung.triage.analyse import TriageResult
from tonmischung.triage.decide import LABEL
from tonmischung.triage.placement import Placement

ORDER = ("sprecher", "o_ton", "atmo", "musik", "rest")


def _counts(c: Counter) -> str:
    return ", ".join(f"{LABEL[k]} {c[k]}" for k in ORDER if c[k])


def format_report(r: TriageResult, all_clips: bool = False) -> str:
    a = r.analysis
    fps = a.fps or 25
    lines = [f"AAF: {a.path.rsplit('/', 1)[-1]}",
             f"Erzeuger: {a.producer or 'unbekannt'} · Beginn {frames_to_tc(a.start_frames, fps)} · "
             f"{len(a.tracks)} Spuren · {len(a.clips)} Clips"
             + (f" · {a.unreadable} ohne lesbaren Ton" if a.unreadable else ""), "", "Spuren:"]
    width = max((len(t.name) for t in a.tracks), default=4)
    paired = {x for p in r.pairs for x in p}
    for t in a.tracks:
        v = r.verdicts[t.name]
        own = Counter(d.category for d in r.decisions if d.clip.track == t.name)
        alone = " · einzeln" if t.name not in paired and v.category != "sprecher" and own else ""
        lines.append(f"  {t.name:<{width}}  → {LABEL[v.category]:<8} ({v.reason})  "
                     f"{sum(own.values())} Clips: {_counts(own)}{alone}")
    shown = [d for d in r.decisions if all_clips or d.deviates]
    lines += ["", "Alle Clips:" if all_clips else "Abweichende Clips (anders als ihre Spur):"]
    if not shown:
        lines.append("  keine")
    for d in shown:
        c = d.clip
        sig = f" · Signal {c.signal} {c.confidence:.2f}" if c.signal and "Signal" not in d.reason else ""
        lines.append(f"  {c.track:<{width}}  {frames_to_tc(c.start, fps)}–{frames_to_tc(c.end, fps)}  "
                     f"{LABEL[d.category]:<8} {d.reason}{sig} · {c.source.strip()[:50]}")
    total = Counter(d.category for d in r.decisions)
    lines += ["", f"Summe: {_counts(total)}"]
    return "\n".join(lines)


def as_dict(r: TriageResult) -> dict:
    """Bericht als Daten (für --json und später für Helmut)."""
    a = r.analysis
    fps = a.fps or 25
    return {
        "aaf": a.path, "erzeuger": a.producer, "beginn": frames_to_tc(a.start_frames, fps),
        "paare": [list(p) for p in r.pairs],
        "spuren": [{"name": t.name, "unbenannt": t.unnamed, "kategorie": r.verdicts[t.name].category,
                    "grund": r.verdicts[t.name].reason} for t in a.tracks],
        "clips": [{"spur": d.clip.track, "nr": d.clip.index,
                   "von": frames_to_tc(d.clip.start, fps), "bis": frames_to_tc(d.clip.end, fps),
                   "quelle": d.clip.source.strip(), "kategorie": d.category, "grund": d.reason,
                   "weicht_ab": d.deviates,
                   "hinweis": d.clip.hint.kind if d.clip.hint else None,
                   "signal": d.clip.signal, "sicherheit": round(d.clip.confidence, 3)}
                  for d in r.decisions],
    }


def format_placement(p: Placement, fps: int = 25) -> str:
    """Verteilung auf das Template: Spuren, verschobene Bereiche, Doppelmono, Hinweise."""
    lines = ["Verteilung auf das Template:",
             "  " + ", ".join(f"{m.aaf_track} → {m.target}" for m in p.track_moves)]
    if p.clip_moves:
        lines.append(f"  Verschieben ({len(p.clip_moves)} Bereiche):")
        for m in p.clip_moves:
            lines.append(f"    {'/'.join(m.sources)} {frames_to_tc(m.start, fps)}–{frames_to_tc(m.end, fps)} "
                         f"→ {'/'.join(m.targets)} ({LABEL[m.category]})")
    for c in p.copies:
        lines.append(f"  Doppelmono: {c.source} → {c.target}")
    for n in p.notes:
        lines.append(f"  Hinweis: {n}")
    return "\n".join(lines)
