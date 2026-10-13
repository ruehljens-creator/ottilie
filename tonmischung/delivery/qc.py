"""Sendefile prüfen (Plan M5, Schritt 3): Format gegen das ARD-ZDF-Profil, Ton 24 bit / 48 kHz auf allen Spuren,
Timecode, Länge, Fremd-Metadaten, MXF-Struktur und – mit einer Dekodier-Passage – Schwarz- und Standbild.

Herkunft: punchbuddy-helmut `punchbuddy/qc.py` (Stand 17.09.2026). Angepasst an Ottilie: Sollwerte aus
`delivery.profiles.Profile` und dem tatsächlichen Start-Timecode des Umpackens (nicht nur „vorhanden“, sondern genau
der erwartete); Lautheit wird hier nicht noch einmal gemessen – korrigiert ist sie beim WAV-Export (R128). Kein
Fenster, keine Einstellungsdatei: `evaluate` ist reine Logik, `run_qc` sammelt mit ffprobe, MediaInfo und ffmpeg.

Bewusst kein Ersatz für ein zertifiziertes System (BATON): PSE, Lip-Sync und Prüfpläne gehören nicht hierher.
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from tonmischung.core.timecode import parse_rate
from tonmischung.delivery.audio_format import HOUSE_BITS, HOUSE_RATE, bits_from_probe
from tonmischung.delivery.profiles import Profile

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
_RANK = {PASS: 0, INFO: 0, WARN: 1, FAIL: 2}
DURATION_TOLERANCE_MS = 40           # Bild und Ton dürfen höchstens ein Bild auseinander liegen
# Tags, die MXF-Muxer regulär schreiben (auch die IRT-Referenzclips und bmx) – keine Fremd-Metadaten
STRUCTURAL_TAGS = {
    "timecode", "company_name", "product_name", "product_version", "product_uid", "application_platform",
    "toolkit_version", "uid", "generation_uid", "material_package_umid", "file_package_umid",
    "operational_pattern_ul", "modification_date", "material_package_name", "creation_time", "encoder",
    "major_brand", "minor_version", "compatible_brands", "handler_name", "vendor_id", "language", "comment",
    "file_package_name", "product_version_num", "toolkit_version_num", "track_name",
}
# Strukturprüfung je Profilfamilie (delivery.mxf_structure.PROFILES)
STRUCTURE = {"ARD_ZDF_HDF01": "rdd9_xdcam", "ARD_ZDF_HDF02": "avci_op1a", "ARD_ZDF_HDF03": "avci_op1a"}
# Einstellungen der Prüfung (export.pruefung)
STOP, ASK, LOG_ONLY = "stoppen", "freigabe_anfordern", "nur_protokollieren"


@dataclass(frozen=True)
class Finding:
    check: str
    status: str
    expected: str = ""
    actual: str = ""
    message: str = ""


@dataclass(frozen=True)
class Expectation:
    profile: Profile
    first_tc: str                         # Timecode des ersten Bildes (Ergebnis des Umpackens)
    check_lengths: bool = True            # export.pruefung.laenge_gleich
    black_max_s: float = 1.0              # export.pruefung.schwarzbild_ab_s
    freeze_max_s: float = 5.0             # export.pruefung.standbild_ab_s


@dataclass
class QcResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def overall(self) -> str:
        return overall(self.findings)

    def as_dicts(self) -> list[dict]:
        return [asdict(f) for f in self.findings]


def overall(findings: Sequence[Finding]) -> str:
    worst = PASS
    for f in findings:
        if _RANK.get(f.status, 0) > _RANK[worst]:
            worst = f.status
    return worst


def settings_expectation(get: Callable[[str], Any], profile: Profile, first_tc: str) -> Expectation:
    """Sollwerte aus den Einstellungen `export.pruefung.*`."""
    return Expectation(profile, first_tc, check_lengths=bool(get("export.pruefung.laenge_gleich")),
                       black_max_s=float(get("export.pruefung.schwarzbild_ab_s") or 1.0),
                       freeze_max_s=float(get("export.pruefung.standbild_ab_s") or 5.0))


def enabled(get: Callable[[str], Any]) -> bool:
    return bool(get("export.pruefung.formatpruefung"))


def blocks_transfer(result_overall: str, get: Callable[[str], Any]) -> str:
    """Was nach der Prüfung mit dem Transfer geschieht: "" = weiter, sonst Grund. `bei_fehler` (Einstellung):
    stoppen (Vorgabe) | freigabe_anfordern (Mischer entscheidet) | nur_protokollieren."""
    if result_overall != FAIL:
        return ""
    mode = str(get("export.pruefung.bei_fehler") or STOP)
    if mode == LOG_ONLY:
        return ""
    return "freigabe" if mode == ASK else "gestoppt"


# ── ffprobe-Helfer ────────────────────────────────────────────────────────────
def _video(probe: Mapping) -> Mapping:
    return next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})


def _audio(probe: Mapping) -> list[Mapping]:
    return [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]


def _fps(v: Mapping) -> float:
    return parse_rate(v.get("r_frame_rate") or v.get("avg_frame_rate"))


def _dur(obj: Mapping) -> float | None:
    try:
        return float(obj.get("duration"))
    except (TypeError, ValueError):
        return None


def _timecode(probe: Mapping) -> str | None:
    tc = (probe.get("format", {}).get("tags") or {}).get("timecode")
    if tc:
        return tc
    return next(((s.get("tags") or {}).get("timecode") for s in probe.get("streams", [])
                 if (s.get("tags") or {}).get("timecode")), None)


def _mi_track(mediainfo: Mapping | None, kind: str) -> Mapping:
    for tr in ((mediainfo or {}).get("media") or {}).get("track", []) or []:
        if tr.get("@type") == kind:
            return tr
    return {}


def _scan(field_order: str) -> str:
    fo = str(field_order or "").lower()
    return "progressive" if fo in ("", "progressive", "unknown") else fo


# ── Bewertung (rein) ──────────────────────────────────────────────────────────
def evaluate(exp: Expectation, probe: Mapping, mediainfo: Mapping | None = None,
             content: Mapping | None = None) -> list[Finding]:
    p = exp.profile
    f: list[Finding] = []
    v, a, fmt = _video(probe), _audio(probe), probe.get("format", {}) or {}

    name = str(fmt.get("format_name") or "").lower()
    prof = str(_mi_track(mediainfo, "General").get("Format_Profile") or "").lower()
    op1a = not prof or "op-1a" in prof or "op1a" in prof
    f.append(Finding("Container", PASS if "mxf" in name and op1a else FAIL, "MXF OP1a",
                     f"{name or '?'}{' / ' + prof if prof else ''}"))
    codec = str(v.get("codec_name") or "?").lower()
    f.append(Finding("Bild-Codec", PASS if codec == p.codec else FAIL, p.codec, codec))
    cname = (_mi_track(mediainfo, "Video").get("Format_Commercial_IfAny")
             or _mi_track(mediainfo, "Video").get("Format_Commercial"))
    if cname:
        f.append(Finding("Bildformat (MediaInfo)", INFO, "", str(cname)))
    size = f"{v.get('width', '?')}x{v.get('height', '?')}"
    f.append(Finding("Auflösung", PASS if size == p.size else FAIL, p.size, size))
    scan = _scan(str(v.get("field_order") or ""))
    f.append(Finding("Abtastung", PASS if scan == p.scan else FAIL, p.scan, scan or "?",
                     "" if scan == p.scan else "Halbbildfolge weicht vom Profil ab"))
    fps = _fps(v)
    f.append(Finding("Bildrate", PASS if abs(fps - p.fps) < 0.01 else FAIL, f"{p.fps:g}", f"{fps:g}"))

    f.append(Finding("Tonspuren", PASS if len(a) == p.tracks else FAIL, str(p.tracks), str(len(a)),
                     "" if len(a) == p.tracks else "Spurzahl weicht vom Profil ab – die Belegung verschiebt sich"))
    if a:
        mono = all(int(s.get("channels") or 0) == 1 for s in a)
        f.append(Finding("Monospuren", PASS if mono else FAIL, "je Spur 1 Kanal",
                         "alle mono" if mono else "nicht alle mono"))
        rates = sorted({str(s.get("sample_rate", "?")) for s in a})
        f.append(Finding("Abtastrate", PASS if rates == [str(HOUSE_RATE)] else FAIL, f"{HOUSE_RATE} Hz",
                         "/".join(rates) + " Hz", "" if rates == [str(HOUSE_RATE)] else "nicht alle Tonspuren"))
        bits = sorted({str(bits_from_probe(s) or "?") for s in a})
        f.append(Finding("Bittiefe", PASS if bits == [str(HOUSE_BITS)] else FAIL, f"{HOUSE_BITS} bit",
                         "/".join(bits) + " bit", "" if bits == [str(HOUSE_BITS)] else "nicht alle Tonspuren"))
        codecs = sorted({str(s.get("codec_name") or "?") for s in a})
        f.append(Finding("Ton-Codec", PASS if codecs == ["pcm_s24le"] else WARN, "pcm_s24le", "/".join(codecs)))

    company = (str(_mi_track(mediainfo, "General").get("Encoded_Application_CompanyName") or "") + " "
               + str(_mi_track(mediainfo, "General").get("Encoded_Application_Name") or "")).strip()
    if company and "ffmpeg" in company.lower():
        f.append(Finding("MXF-Nachlauf (bmx)", WARN, "bmxtranswrap", company,
                         "bmx ist nicht gelaufen – Essenz in Ordnung, MXF-Kopfdaten nicht normgerecht"))

    if exp.check_lengths:
        vd, ad = _dur(v) or _dur(fmt), (_dur(a[0]) if a else None)
        if vd and ad:
            diff = abs(vd - ad) * 1000
            f.append(Finding("Länge Bild/Ton", PASS if diff <= DURATION_TOLERANCE_MS else FAIL,
                             f"± {DURATION_TOLERANCE_MS} ms", f"Δ {diff:.0f} ms"))

    tc = _timecode(probe)
    if tc:
        f.append(Finding("Start-Timecode", PASS if tc == exp.first_tc else FAIL, exp.first_tc, tc))
    else:
        f.append(Finding("Start-Timecode", FAIL, exp.first_tc, "fehlt"))

    keys = set((fmt.get("tags") or {}).keys())
    for s in probe.get("streams", []):
        keys |= set((s.get("tags") or {}).keys())
    foreign = sorted(k for k in keys if k.lower() not in STRUCTURAL_TAGS)
    f.append(Finding("Fremd-Metadaten", WARN if foreign else PASS, "keine",
                     ", ".join(foreign[:12]) if foreign else "keine"))

    if content and content.get("available"):
        bt = float(content.get("black_total") or 0.0)
        f.append(Finding("Schwarzbild", WARN if bt > exp.black_max_s else PASS, f"höchstens {exp.black_max_s:g} s",
                         f"{bt:.1f} s in {content.get('black_count') or 0} Abschnitt(en)"))
        fc = int(content.get("freeze_count") or 0)
        f.append(Finding("Standbild", WARN if fc else PASS, f"keins ab {exp.freeze_max_s:g} s", str(fc)))
        if content.get("silence_count"):
            f.append(Finding("Stille (Spur 1)", INFO, "", f"{content['silence_count']} Abschnitt(e)"))
    elif content is not None:
        f.append(Finding("Inhaltsprüfung", INFO, "", "nicht möglich (ffmpeg fehlt)"))
    f.append(Finding("Lautheit", INFO, "", "beim WAV-Export nach R 128 korrigiert – hier nicht erneut gemessen"))
    return f


def structure_findings(path: str, profile: Profile) -> list[Finding]:
    """MXF-Struktur nach dem Profil (Abweichungen = WARN, wie die ARD/ZDF-Prüfung selbst)."""
    from tonmischung.delivery import mxf_structure as M
    family = profile.key[:-1]
    name = STRUCTURE.get(family)
    if not name:
        return [Finding("MXF-Struktur", INFO, "", f"für {profile.key} nicht hinterlegt")]
    try:
        st = M.read_structure(path)
        if st.get("abgeschnitten") is not None:
            return [Finding("MXF-Struktur", FAIL, "vollständige Datei",
                            f"endet mitten in einem Block (ab Byte {st['abgeschnitten']})",
                            "Datei unvollständig – bitte neu erstellen")]
        found = M.check(st, name, {"index_edit_rate": f"{profile.fps}/1"})
    except (OSError, ValueError, KeyError, IndexError, struct.error) as e:      # struct.error: Review A20
        return [Finding("MXF-Struktur", WARN, M.PROFILES[name]["name"], "nicht prüfbar", str(e)[:200])]
    total, off = M.summary(found)
    out = [Finding("MXF-Struktur", PASS if total == "PASS" else WARN, M.PROFILES[name]["name"],
                   f"{len(found) - len(off)} von {len(found)} Merkmalen wie im Profil",
                   "" if total == "PASS" else "Abweichungen sind Warnungen, keine Ablehnung")]
    out += [Finding(f"MXF-Struktur: {b['check']}", WARN, b["expected"], b["actual"], b["message"]) for b in off]
    return out


# ── Sammeln (Werkzeuge) ───────────────────────────────────────────────────────
def parse_blackdetect(text: str) -> tuple[int, float]:
    m = re.findall(r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)", text)
    return len(m), sum(float(d) for _, _, d in m)


def parse_count(text: str, word: str) -> int:
    return len(re.findall(word, text))


def gather_mediainfo(mediainfo: str, path: str) -> dict | None:
    if not mediainfo:
        return None
    try:
        r = subprocess.run([mediainfo, "--Output=JSON", path], capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def content_checks(ffmpeg: str, path: str, freeze_min_s: float = 5.0, timeout: float = 7200) -> dict:
    """Eine Dekodier-Passage: Schwarzbild, Standbild, Stille auf Spur 1."""
    out = {"available": False}
    if not ffmpeg:
        return out
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", path, "-map", "0:v:0?", "-map", "0:a:0?",
           "-vf", f"blackdetect=d=0.1:pic_th=0.98,freezedetect=n=-60dB:d={max(0.1, freeze_min_s):g}",
           "-af", "silencedetect=n=-60dB:d=2", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return out
    err = r.stderr or ""
    out["black_count"], out["black_total"] = parse_blackdetect(err)
    out["freeze_count"] = parse_count(err, "freeze_start")
    out["silence_count"] = parse_count(err, "silence_start")
    out["available"] = r.returncode == 0
    return out


def run_qc(path: str, exp: Expectation, *, ffprobe: str, ffmpeg: str = "", mediainfo: str = "",
           content: bool = True) -> QcResult:
    from tonmischung.delivery.mux import probe as _probe
    if not os.path.isfile(path):
        return QcResult([Finding("Sendefile", FAIL, "vorhanden", "fehlt", path)])
    info = _probe(ffprobe, path)
    mi = gather_mediainfo(mediainfo, path)
    cc = content_checks(ffmpeg, path, exp.freeze_max_s) if content else None
    return QcResult(evaluate(exp, info, mi, cc) + structure_findings(path, exp.profile))
