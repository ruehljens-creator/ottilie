"""ARD-ZDF-Profile des Sendefiles: Bildformat, Kodierung, Tonspuren, bmx-Aufruf (reine Logik).

Herkunft: punchbuddy-helmut `punchbuddy/delivery.py` (Presets „ARD_ZDF_HDF01a“, „ARD_ZDF_HDF02a“, Haus-Standard
16 Spuren; live gegen die IRT-Referenzclips geprüft, Stand 16./17.09.2026) und Prototyp (Logik-Referenz:
„HDF01: XDCAM HD422 1080i/25 · HDF02: AVC-Intra 100 1080i/25 · HDF03: AVC-Intra 100 720p/50 · a = 8, b = 16
Tonspuren“; bmx `-t rdd9` für HDF01, sonst `-t op1a`, jeweils `--ard-zdf-hdf`, `--afd`).

Ein Preset (`delivery.presets`) wählt nur das Profil und wenige Schalter (modus, werkzeug, monospuren, afd,
startband, Dateiname); alles Weitere steht hier.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from tonmischung.core.timecode import parse_rate

AUTO, REWRAP, RECODE = "auto", "rewrap", "recode"
COPY = "copy"

# XDCAM HD422 50 Mbit/s, 1080i25 TFF, 4:2:2 – Parameter wie punchbuddy-helmut (live 16.09.2026, ffmpeg 9)
_XDCAM = ("-c:v", "mpeg2video", "-profile:v", "0", "-level:v", "2", "-b:v", "50M", "-minrate", "50M",
          "-maxrate", "50M", "-bufsize", "17825792", "-s", "1920x1080", "-r", "25", "-aspect", "16:9",
          "-pix_fmt", "yuv422p", "-flags", "+ildct+ilme", "-field_order", "tt", "-g", "12", "-bf", "2",
          "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
          "-non_linear_quant", "1", "-intra_vlc", "1", "-intra_dc_precision", "0", "-qmin", "3", "-qmax", "28",
          "-alternate_scan", "1", "-seq_disp_ext", "1", "-video_format", "component",
          "-chroma_sample_location", "topleft", "-signal_standard", "4", "-rc_init_occupancy", "17825792")


def _avci(size: str, fps: str, scan: tuple[str, ...]) -> tuple[str, ...]:
    # AVC-Intra 100 (x264, 10 bit): Klasse 100, CAVLC, nur I-Bilder – punchbuddy-helmut HDF02a
    return ("-c:v", "libx264", "-coder", "0", "-avcintra-class", "100", "-s", size, "-r", fps, "-aspect", "16:9",
            "-pix_fmt", "yuv422p10le", *scan, "-g", "1", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709", "-color_range", "tv", "-vtag", "ai12", "-tune", "psnr")


_TFF = ("-flags", "+ildct+ilme", "-field_order", "tt")


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    codec: str                    # ffprobe codec_name des Bildes
    codec_profile: str            # ffprobe profile (Teilstring), z. B. „4:2:2“, „High 4:2:2 Intra“
    width: int
    height: int
    fps: int
    scan: str                     # tt (interlaced TFF) | progressive
    pix_fmt: str
    bitrate_mbit: int
    tracks: int                   # Tonspuren im Sendefile (Mono)
    encode: tuple[str, ...]       # ffmpeg-Argumente für das Neu-Kodieren
    bmx_type: str                 # rdd9 | op1a

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"


def _pair(key: str, label: str, **kw) -> dict[str, Profile]:
    return {f"{key}a": Profile(f"{key}a", f"{label}, 8 Tonspuren", tracks=8, **kw),
            f"{key}b": Profile(f"{key}b", f"{label}, 16 Tonspuren", tracks=16, **kw)}


PROFILES: dict[str, Profile] = {
    **_pair("ARD_ZDF_HDF01", "XDCAM HD422 1080i/25", codec="mpeg2video", codec_profile="4:2:2", width=1920,
            height=1080, fps=25, scan="tt", pix_fmt="yuv422p", bitrate_mbit=50, encode=_XDCAM, bmx_type="rdd9"),
    **_pair("ARD_ZDF_HDF02", "AVC-Intra 100 1080i/25", codec="h264", codec_profile="Intra", width=1920,
            height=1080, fps=25, scan="tt", pix_fmt="yuv422p10le", bitrate_mbit=100,
            encode=_avci("1920x1080", "25", _TFF), bmx_type="op1a"),
    # ANNAHME: HDF03 = AVC-Intra 100 720p/50 wie im Prototyp; nicht gegen eine Referenz geprüft
    **_pair("ARD_ZDF_HDF03", "AVC-Intra 100 720p/50", codec="h264", codec_profile="Intra", width=1280,
            height=720, fps=50, scan="progressive", pix_fmt="yuv422p10le", bitrate_mbit=100,
            encode=_avci("1280x720", "50", ()), bmx_type="op1a"),
}


class ProfileError(ValueError):
    """Profil unbekannt oder Bild passt nicht – klare Meldung."""


def profile(key: str) -> Profile:
    try:
        return PROFILES[key]
    except KeyError:
        raise ProfileError(f"MXF-Profil „{key}“ ist nicht hinterlegt") from None


def _fps(rate: str) -> float:
    return parse_rate(rate)


def _scan(field_order: str) -> str:
    fo = str(field_order or "").lower()
    if fo in ("", "progressive", "unknown"):
        return "progressive"
    # ANNAHME: „tb“ (oberes Halbbild zuerst kodiert) gilt als TFF. ffmpeg schreibt in sein eigenes MXF eine
    # Halbbild-Dominanz, die ffprobe als „tb“ liest; dieselbe Essenz nach bmx liest sich als „tt“ (live 03.10.2026)
    return "tt" if fo == "tb" else fo


def mismatches(p: Profile, stream: Mapping) -> list[str]:
    """Was am Bild nicht zum Profil passt (leer = kann unverändert umgepackt werden)."""
    out = []
    if str(stream.get("codec_name") or "").lower() != p.codec:
        out.append(f"Codec {stream.get('codec_name') or '?'} statt {p.codec}")
    if p.codec_profile.lower() not in str(stream.get("profile") or "").lower():
        out.append(f"Profil {stream.get('profile') or '?'} statt {p.codec_profile}")
    if (int(stream.get("width") or 0), int(stream.get("height") or 0)) != (p.width, p.height):
        out.append(f"Größe {stream.get('width')}x{stream.get('height')} statt {p.size}")
    if abs(_fps(str(stream.get("r_frame_rate") or "")) - p.fps) > 0.01:
        out.append(f"{stream.get('r_frame_rate')} statt {p.fps} Bilder/s")
    if _scan(str(stream.get("field_order") or "")) != p.scan:
        out.append(f"Abtastung {stream.get('field_order') or 'progressiv'} statt {p.scan}")
    if str(stream.get("pix_fmt") or "") != p.pix_fmt:
        out.append(f"Pixelformat {stream.get('pix_fmt') or '?'} statt {p.pix_fmt}")
    try:
        rate = int(stream.get("bit_rate") or 0) / 1e6
    except (TypeError, ValueError):
        rate = 0
    if rate and abs(rate - p.bitrate_mbit) > p.bitrate_mbit * 0.1:
        out.append(f"{rate:.0f} Mbit/s statt {p.bitrate_mbit}")
    return out


def decide_mode(preset_mode: str, p: Profile, stream: Mapping) -> tuple[str, str]:
    """(copy | recode, Begründung). „rewrap“ verlangt ein passendes Bild – sonst Fehler statt falschem Sendefile."""
    diff = mismatches(p, stream)
    if preset_mode == RECODE:
        return RECODE, "Preset: immer neu kodieren"
    if not diff:
        return COPY, "Bild passt zum Profil – nur umpacken"
    if preset_mode == REWRAP:
        raise ProfileError("Bild passt nicht zum Profil, Preset verlangt Umpacken: " + "; ".join(diff))
    return RECODE, "Neu kodieren: " + "; ".join(diff)


def bmx_args(p: Profile, afd: int | None = None) -> list[str]:
    """bmxtranswrap-Optionen (ohne Eingang/Ausgang): ARD-ZDF-HDF-Profil von bmx, AFD wie im Preset."""
    a = ["-t", p.bmx_type, "--ard-zdf-hdf"]
    if afd is not None:
        a += ["--afd", str(int(afd))]
    return a
