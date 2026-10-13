"""Haus-Tonformat der Auslieferung (24 bit / 48 kHz): Tonformat einer WAV lesen, Bittiefe aus ffprobe.

Herkunft: punchbuddy-helmut `punchbuddy/audioformat.py` (Stand 16.09.2026). Das Angleichen abweichender WAVs ist am
13.10.2026 als ungenutzt entfernt worden: Die Auslieferung schreibt den Ton ohnehin als PCM 24 bit / 48 kHz ins
Sendefile (`mux.build_command`), und die Prüfung meldet Abweichungen (`qc`).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

BITS = {"PCM_16": 16, "PCM_24": 24, "PCM_32": 32, "FLOAT": 32, "DOUBLE": 64, "PCM_U8": 8, "PCM_S8": 8}
HOUSE_BITS, HOUSE_RATE = 24, 48000


@dataclass(frozen=True)
class AudioInfo:
    bits: int | None
    rate: int
    channels: int
    subtype: str


def analyse(path: str) -> AudioInfo | None:
    try:
        import soundfile as sf
        info = sf.info(path)
    except Exception:
        return None
    return AudioInfo(BITS.get(info.subtype), int(info.samplerate), int(info.channels), str(info.subtype))


def bits_from_probe(stream: Mapping) -> int | None:
    """Bittiefe eines ffprobe-Tonstroms: bits_per_sample, sonst aus dem Codec (pcm_s24le → 24)."""
    for k in ("bits_per_sample", "bits_per_raw_sample"):
        try:
            v = int(stream.get(k) or 0)
        except (TypeError, ValueError):
            v = 0
        if v:
            return v
    name = str(stream.get("codec_name") or "").lower()
    for b in (16, 24, 32):
        if f"s{b}" in name or f"f{b}" in name:
            return b
    return None
