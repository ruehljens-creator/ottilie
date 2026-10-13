"""AAF vor dem Import prüfen: lesbar, nicht an der 2-GiB-Grenze abgeschnitten, Lage im Timecode.

Befund (PunchBuddy §33, hier am 02.10.2026 bestätigt): Premiere bricht beim
Einbetten an der 2-GiB-Grenze ab. Die Datei ist dann knapp 2 GiB groß, die
Objektstruktur fehlt, Pro Tools lehnt den Import ab.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from fractions import Fraction

CFB_LIMIT = 2 ** 31


@dataclass(frozen=True)
class AafInfo:
    ok: bool
    message: str
    start_frames: int | None = None   # Start der Sequenz in Frames
    fps: int | None = None
    track_names: tuple[str, ...] = ()
    # je Tonspur die Clips (Anfang, Ende) in Frames, so wie Pro Tools sie zeigt
    clips: dict = field(default_factory=dict)

    @property
    def start_timecode(self) -> str | None:
        if self.start_frames is None or not self.fps:
            return None
        f = self.start_frames
        return (f"{f // (3600 * self.fps):02d}:{f // (60 * self.fps) % 60:02d}:"
                f"{f // self.fps % 60:02d}:{f % self.fps:02d}")


def check_aaf(path: str) -> AafInfo:
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return AafInfo(False, f"AAF nicht lesbar: {e}")
    at_limit = size >= CFB_LIMIT - 1024 * 1024
    try:
        import aaf2
        with aaf2.open(path, "r") as f:
            comps = list(f.content.toplevel())
            if not comps:
                return AafInfo(False, "AAF enthält keine Sequenz – kein Import möglich")
            seq = comps[0]
            start = fps = None
            for slot in seq.slots:
                if getattr(slot.segment, "media_kind", "") == "Timecode" and start is None:
                    tc = _timecode(slot.segment)
                    if tc is not None:
                        start, fps = int(tc.start), int(tc.fps)
            names, clips = [], {}
            sound = [s for s in seq.slots if getattr(s.segment, "media_kind", "") == "Sound"]
            for n, slot in enumerate(sound, 1):
                # Unbenannte Spuren (z. B. Media Composer) nennt Pro Tools A1, A2, … (live 02.10.2026)
                name = str(slot.name).strip() if slot.name else f"A{n}"
                names.append(name)
                out: list = []
                expected_clips(slot.segment, 0, out)
                # Lagen stehen in der Rate der Spur – DaVinci schreibt Ton in Samples (48000/s), Premiere in
                # Bildern (live 04.10.2026). Umrechnen in Bilder der Timecode-Spur.
                k = Fraction(fps or 25) / _rate(getattr(slot, "edit_rate", None), fps or 25)
                clips[name] = tuple(((start or 0) + floor_frames(a * k), (start or 0) + floor_frames(b * k))
                                    for a, b in out)
            return AafInfo(True, "AAF lesbar", start, fps, tuple(names), clips)
    except Exception as e:  # noqa: BLE001 – jede Lesestörung ist ein Befund
        if at_limit:
            return AafInfo(False, f"AAF ist an der 2-GiB-Grenze abgeschnitten ({size} Byte) – "
                                  "in Premiere mit verknüpften statt eingebetteten Medien "
                                  "oder in kleineren Teilen neu exportieren")
        return AafInfo(False, f"AAF nicht lesbar: {e}")


def _rate(value, default: int) -> Fraction:
    try:
        r = Fraction(value)
        return r if r > 0 else Fraction(default)
    except (TypeError, ValueError, ZeroDivisionError):
        return Fraction(default)


def floor_frames(x: Fraction) -> int:
    return int(x.numerator // x.denominator)


def at_rate(info: AafInfo, fps: int) -> AafInfo:
    """Lagen auf eine andere Bildrate umrechnen (z. B. 50-fps-Sequenz in eine 25-fps-Session; reine Logik).

    # ANNAHME: Pro Tools zeigt Lagen ohne Subframes abgerundet – deshalb abrunden."""
    if not info.ok or not info.fps or info.fps == fps:
        return info
    k = Fraction(fps, info.fps)
    conv = (lambda x: floor_frames(x * k))
    return replace(info, fps=fps, start_frames=None if info.start_frames is None else conv(info.start_frames),
                   clips={t: tuple((conv(a), conv(b)) for a, b in v) for t, v in info.clips.items()})


def _timecode(segment):
    if segment.__class__.__name__ == "Timecode":
        return segment
    for c in getattr(segment, "components", []) or []:
        if c.__class__.__name__ == "Timecode":
            return c
    return None


def expected_clips(seg, pos: int, out: list) -> None:
    """Clips (Anfang, Ende) in Frames so, wie Pro Tools sie nach dem Import zeigt.

    AAF: Überblendungen überlappen die Nachbarn. Pro Tools legt sie als Fades an und
    zeigt Clip-Grenzen ohne Fade-Bereich: Anfang + Fade-in, Ende − Fade-out
    (geprüft an 568 Clips einer Premiere-AAF, 02.10.2026).
    """
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
            expected_clips(c, pos, out)
            if len(out) == n + 1:
                a, b = out[-1]
                out[-1] = (a + fade_in, b - fade_out)
            pos += int(c.length)
    elif cls == "OperationGroup":
        segs = list(seg.segments)
        if segs:
            expected_clips(segs[0], pos, out)
    elif cls == "SourceClip":
        out.append((pos, pos + int(seg.length)))
