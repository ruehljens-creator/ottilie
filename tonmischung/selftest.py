"""Selbsttest: passt das Paket zu diesem Mac? (`Ottilie selbsttest`)

Apple-Silicon-Umstellung (Jens 06.10.2026): Mit Helmut kommen Apple-Silicon-Macs; das Paket wird Universal (arm64 und
x86_64). Der Selbsttest prüft auf dem Mac, auf dem er läuft, in einer Minute: Architektur (nativ oder unter Rosetta),
die Python-Module mit eigenem Maschinencode (kurz benutzt, nicht nur importiert), jedes mitgelieferte Werkzeug
(einmal gestartet, passende Architektur) und die Bibliotheken der Werkzeuge. Pro Tools und Helmut fasst er nicht an.

Reine Logik (`macho_archs`, `Check`, `summary`) ist ohne Seiteneffekte testbar; `run()` führt die Prüfungen aus.
"""
from __future__ import annotations

import json
import os
import platform
import struct
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass

# ── Mach-O-Kopf lesen (ohne lipo/file, die auf Studio-Macs ohne Entwicklerwerkzeuge fehlen) ─────────────────────
_ABI64 = 0x01000000
_CPU = {7: "i386", 7 | _ABI64: "x86_64", 12: "arm", 12 | _ABI64: "arm64", 12 | 0x02000000: "arm64_32",
        18: "ppc", 18 | _ABI64: "ppc64"}
_FAT = (0xCAFEBABE, 0xCAFEBABF)
_THIN = (0xFEEDFACE, 0xFEEDFACF)


def _arch(cputype: int, subtype: int) -> str:
    name = _CPU.get(cputype, f"cpu{cputype:#x}")
    if name == "arm64" and (subtype & 0x00FFFFFF) == 2:
        return "arm64e"
    return name


def macho_archs(head: bytes) -> list[str]:
    """Architekturen aus den ersten Bytes einer Datei (≥ 4 KB reichen); [] wenn es kein Mach-O ist."""
    if len(head) < 8:
        return []
    be = struct.unpack(">I", head[:4])[0]
    if be in _FAT:
        n = struct.unpack(">I", head[4:8])[0]
        size = 32 if be == 0xCAFEBABF else 20
        if not 0 < n <= 16 or len(head) < 8 + n * size:      # Java-Klassen beginnen ebenfalls mit CAFEBABE
            return []
        out = []
        for i in range(n):
            cpu, sub = struct.unpack(">ii", head[8 + i * size:16 + i * size])
            out.append(_arch(cpu & 0xFFFFFFFF, sub & 0xFFFFFFFF))
        return out
    le = struct.unpack("<I", head[:4])[0]
    if le in _THIN:
        cpu, sub = struct.unpack("<ii", head[4:12]) if len(head) >= 12 else (0, 0)
        return [_arch(cpu & 0xFFFFFFFF, sub & 0xFFFFFFFF)]
    if be in _THIN and len(head) >= 12:
        cpu, sub = struct.unpack(">ii", head[4:12])
        return [_arch(cpu & 0xFFFFFFFF, sub & 0xFFFFFFFF)]
    return []


def file_archs(path: str) -> list[str]:
    try:
        with open(path, "rb") as f:
            return macho_archs(f.read(4096))
    except OSError:
        return []


_LC_BUILD_VERSION, _LC_VERSION_MIN_MACOSX = 0x32, 0x24


def _min_os_of_slice(f, offset: int) -> tuple[int, int, int] | None:
    f.seek(offset)
    head = f.read(32)
    if len(head) < 28:
        return None
    magic = struct.unpack("<I", head[:4])[0]
    if magic not in _THIN:
        return None
    ncmds, sizeofcmds = struct.unpack("<II", head[16:24])
    f.seek(offset + (32 if magic == 0xFEEDFACF else 28))
    cmds = f.read(min(sizeofcmds, 1 << 20))
    pos = 0
    for _ in range(ncmds):
        if pos + 8 > len(cmds):
            break
        cmd, size = struct.unpack("<II", cmds[pos:pos + 8])
        field = None
        if cmd == _LC_BUILD_VERSION and pos + 16 <= len(cmds):
            platform_id, field = struct.unpack("<II", cmds[pos + 8:pos + 16])
            if platform_id != 1:                       # 1 = macOS
                field = None
        elif cmd == _LC_VERSION_MIN_MACOSX and pos + 12 <= len(cmds):
            field = struct.unpack("<I", cmds[pos + 8:pos + 12])[0]
        if field is not None:
            return field >> 16, (field >> 8) & 0xFF, field & 0xFF
        pos += max(size, 8)
    return None


def min_macos(path: str) -> tuple[int, int, int] | None:
    """Kleinste macOS-Version, die die Datei verlangt (höchste über alle Architekturen); None ohne Angabe."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
            if not macho_archs(head):
                return None
            be = struct.unpack(">I", head[:4])[0]
            if be in _FAT:
                n = struct.unpack(">I", head[4:8])[0]
                size = 32 if be == 0xCAFEBABF else 20
                offsets = []
                for i in range(n):
                    entry = head[8 + i * size:8 + (i + 1) * size]
                    offsets.append(struct.unpack(">Q", entry[8:16])[0] if size == 32
                                   else struct.unpack(">I", entry[8:12])[0])
            else:
                offsets = [0]
            found = [v for v in (_min_os_of_slice(f, o) for o in offsets) if v]
    except OSError:
        return None
    return max(found) if found else None


def version_text(v: tuple[int, int, int] | None) -> str:
    if not v:
        return ""
    return f"{v[0]}.{v[1]}" + (f".{v[2]}" if v[2] else "")


def runs_on(archs: Iterable[str], machine: str) -> bool:
    """Kann Code mit diesen Architekturen auf `machine` (arm64 | x86_64, so wie der Prozess läuft) laufen?"""
    archs = set(archs)
    return machine in archs or (machine == "arm64" and "arm64e" in archs)


# ── Prüfungen ───────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Check:
    group: str
    name: str
    ok: bool
    detail: str = ""
    warn: bool = False                  # Hinweis: kein Fehler, aber eine Funktion ist eingeschränkt


def translated() -> bool:
    """Läuft dieser Prozess unter Rosetta (x86_64-Code auf Apple Silicon)?"""
    try:
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return out == "1"


def apple_silicon() -> bool:
    """Ist der Mac selbst ein Apple-Silicon-Mac (auch wenn dieser Prozess unter Rosetta läuft)?"""
    try:
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return platform.machine() == "arm64"
    return out == "1"


def _numpy() -> str:
    import numpy as np
    return f"{np.__version__}, Summe {float(np.arange(10).sum()):.0f}"


def _scipy() -> str:
    import scipy
    from scipy import signal
    b, _a = signal.butter(2, 0.1)
    return f"{scipy.__version__}, Filter {len(b)} Koeffizienten"


def _soundfile() -> str:
    import numpy as np
    import soundfile as sf
    with tempfile.TemporaryDirectory() as d:          # nur eigener temporärer Ordner
        p = os.path.join(d, "probe.wav")
        sf.write(p, np.zeros((4800, 2), dtype="float32"), 48000, subtype="PCM_24")
        data, rate = sf.read(p)
    return f"{sf.__version__}, libsndfile {sf.__libsndfile_version__}, {len(data)} Samples bei {rate} Hz"


def _loudness() -> str:
    import numpy as np
    import pyloudnorm as pyln
    rng = np.random.default_rng(1)
    lufs = pyln.Meter(48000).integrated_loudness(0.1 * rng.standard_normal((48000 * 2, 2)))
    return f"Rauschen {lufs:.1f} LUFS"


def _grpc() -> str:
    import grpc
    from ptsl import PTSL_pb2
    return f"grpc {grpc.__version__}, PTSL {len(PTSL_pb2.DESCRIPTOR.message_types_by_name)} Nachrichten"


def _pyobjc() -> str:
    import AppKit  # noqa: F401
    import Foundation
    import objc
    import Quartz  # noqa: F401
    import Security  # noqa: F401
    import WebKit  # noqa: F401
    return f"PyObjC {objc.__version__}, {Foundation.NSProcessInfo.processInfo().operatingSystemVersionString()}"


def _aaf() -> str:
    import aaf2
    return f"pyaaf2 {getattr(aaf2, '__version__', '') or 'da'}"


def _hid_usb() -> str:
    import hid
    import usb.core  # noqa: F401
    return f"hidapi {getattr(hid, '__version__', '') or 'da'}, pyusb da"


def _cffi() -> str:
    import cffi
    return f"cffi {cffi.__version__}"


MODULES: list[tuple[str, Callable[[], str]]] = [
    ("numpy", _numpy), ("scipy", _scipy), ("soundfile + libsndfile", _soundfile), ("pyloudnorm (R 128)", _loudness),
    ("grpc + PTSL", _grpc), ("PyObjC (AppKit, WebKit, Quartz, Security)", _pyobjc), ("pyaaf2", _aaf),
    ("hidapi + pyusb (Stream Deck, Vocaster)", _hid_usb), ("cffi", _cffi),
]


def module_checks(modules: Iterable[tuple[str, Callable[[], str]]] = MODULES) -> list[Check]:
    out = []
    for name, probe in modules:
        try:
            out.append(Check("Python-Module", name, True, probe()))
        except Exception as e:                       # jeder Fehler zählt (ImportError, falsche Architektur, …)
            out.append(Check("Python-Module", name, False, f"{type(e).__name__}: {e}"[:300]))
    return out


def tool_checks(machine: str) -> list[Check]:
    from tonmischung.delivery import tools as W
    out = []
    for name, (_purpose, _a, _p, required, _k) in W.TOOLS.items():
        path, source = W.locate(name)
        if not path:
            out.append(Check("Werkzeuge", name, not required, "fehlt" + ("" if required else " (nicht zwingend)")))
            continue
        archs = file_archs(os.path.realpath(path))
        v = W.version(name, path)
        fits = runs_on(archs, machine) or not archs
        detail = f"{v or 'antwortet nicht'} · {', '.join(archs) or '?'} · {source}"
        out.append(Check("Werkzeuge", name, bool(v) and fits,
                         detail if fits else detail + f" · passt nicht zu {machine}"))
    seen = set()
    for d in W.bundle_dirs():
        lib = os.path.join(d, "lib")
        if not os.path.isdir(lib) or os.path.realpath(lib) in seen:      # im Paket über Verknüpfungen mehrfach
            continue
        seen.add(os.path.realpath(lib))
        names = sorted(f for f in os.listdir(lib) if f.endswith(".dylib"))
        wrong = [f for f in names if not runs_on(file_archs(os.path.join(lib, f)), machine)]
        out.append(Check("Werkzeuge", f"Bibliotheken ({len(names)})", not wrong,
                         "alle passen" if not wrong else "passen nicht: " + ", ".join(wrong[:8])))
    return out


def package_checks(machine: str) -> list[Check]:
    out = []
    exe = os.path.realpath(sys.executable)
    archs = file_archs(exe)
    kind = "universal" if {"arm64", "x86_64"} <= set(archs) else (archs[0] if len(archs) == 1 else "?")
    where = "Paket" if getattr(sys, "frozen", False) else "Python"
    out.append(Check("System", where, runs_on(archs, machine) or not archs,
                     f"{kind} ({', '.join(archs) or '?'}) – {exe}"))
    need = required_macos_of_package(exe)
    if need:
        have = os_version()
        out.append(Check("System", f"macOS {version_text(need)} oder neuer", have >= need,
                         f"dieser Mac: {version_text(have)}"))
    ui = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "ui")
    fonts = ([f for f in os.listdir(os.path.join(ui, "fonts")) if f.endswith(".woff2")]
             if os.path.isdir(os.path.join(ui, "fonts")) else [])
    out.append(Check("System", "Oberfläche und Schrift",
                     os.path.isfile(os.path.join(ui, "index.html")) and len(fonts) >= 7,
                     f"{len(fonts)} Schriftschnitte IBM Plex"))
    return out


def os_version() -> tuple[int, int, int]:
    parts = [int(x) for x in (platform.mac_ver()[0] or "0").split(".") if x.isdigit()]
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def required_macos_of_package(exe: str) -> tuple[int, int, int] | None:
    """LSMinimumSystemVersion aus dem Info.plist des Pakets (nur im Paket; beim Bau aus den Programmen ermittelt)."""
    import plistlib
    plist = os.path.join(os.path.dirname(os.path.dirname(exe)), "Info.plist")
    try:
        with open(plist, "rb") as f:
            text = str(plistlib.load(f).get("LSMinimumSystemVersion") or "")
    except (OSError, ValueError):
        return None
    parts = [int(x) for x in text.split(".") if x.isdigit()]
    return tuple((parts + [0, 0, 0])[:3]) if parts else None  # type: ignore[return-value]


def build_info() -> dict:
    """Bauangabe des Pakets (tools/build_app.sh, beim Zusammenfügen vereint); {} in der Entwicklung."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_info.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def build_line(info: dict) -> str:
    if not info:
        return ""
    built = info.get("built") or {}
    when = ", ".join(f"{a} {t}" for a, t in built.items()) if isinstance(built, dict) else str(built)
    return f"Bau: Ottilie {info.get('version', '?')} · Stand {info.get('commit', '?')} · {when}"


def system_line(machine: str, rosetta: bool, silicon: bool) -> str:
    mac = "Apple Silicon" if silicon else "Intel"
    how = "unter Rosetta (x86_64)" if rosetta else f"nativ ({machine})"
    return f"Mac: {mac} · macOS {platform.mac_ver()[0] or '?'} · Ottilie läuft {how}"


def permission_checks(allowed: Callable[[], bool | None] | None = None) -> list[Check]:
    """Rechte, ohne die Funktionen eingeschränkt sind – Hinweis, kein Fehler (auf dem Bau-Mac fehlen sie oft)."""
    from tonmischung.ptsl.dialogs import SCREEN_CAPTURE_HINT, screen_capture_allowed
    state = (allowed or screen_capture_allowed)()
    name = "Bildschirmaufnahme (Titel der Pro-Tools-Dialoge)"
    if state is None:
        return [Check("Rechte", name, True, "nicht feststellbar", warn=True)]
    return [Check("Rechte", name, True, "erteilt" if state else SCREEN_CAPTURE_HINT, warn=not state)]


def run() -> tuple[str, list[Check]]:
    machine = platform.machine()
    rosetta = translated()
    head = system_line(machine, rosetta, apple_silicon())
    if build_info():
        head += "\n" + build_line(build_info())
    checks = package_checks(machine) + module_checks() + tool_checks(machine) + permission_checks()
    if rosetta:
        checks.insert(0, Check("System", "Rosetta", True,      # geht, ist aber nur zum Prüfen des Intel-Teils gedacht
                               "Hinweis: läuft als Intel-Code – im Betrieb startet das Universal-Paket nativ"))
    return head, checks


def summary(head: str, checks: list[Check]) -> str:
    lines = [head, ""]
    group = None
    for c in checks:
        if c.group != group:
            group = c.group
            lines.append(f"{group}:")
        mark = "✕" if not c.ok else "!" if c.warn else "✓"
        lines.append(f"  {mark} {c.name}{' – ' + c.detail if c.detail else ''}")
    bad = [c for c in checks if not c.ok]
    notes = sum(c.ok and c.warn for c in checks)
    hint = f" ({notes} Hinweis{'e' if notes > 1 else ''})" if notes else ""
    lines += ["", f"Alles in Ordnung{hint}." if not bad else f"{len(bad)} Prüfung(en) fehlgeschlagen."]
    return "\n".join(lines)
