"""Externe Werkzeuge der Auslieferung finden und prüfen.

Herkunft: punchbuddy-helmut `punchbuddy/werkzeuge.py` (Stand 17.09.2026). Angepasst an Ottilie: Einstellungen
`system.schnittstellen.werkzeuge.<name>` (modus `mitgeliefert` | `eigener_pfad`, `pfad`); ein eigener Pfad, der
nicht ausführbar ist, wird gemeldet statt still übergangen.

Suchreihenfolge je Werkzeug: eigener Pfad (Einstellung) → mitgeliefert (App: `Contents/Resources/bin`,
Entwicklung: `tonmischung/resources/bin`) → PATH → Homebrew. Nur `version()` und `check()` starten das Programm
kurz mit `--version`.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass

# Name → (Zweck, Versionsargumente, Muster für die Version, Pflicht, Schlüssel in den Einstellungen)
TOOLS: dict[str, tuple[str, list[str], str, bool, str]] = {
    "ffmpeg": ("Umpacken, Neu-Kodieren, Lautheit", ["-version"], r"ffmpeg version (\S+)", True, "ffmpeg"),
    "ffprobe": ("Quellen und Ergebnis analysieren", ["-version"], r"ffprobe version (\S+)", True, "ffprobe"),
    "mediainfo": ("Formatangaben im Prüfbericht", ["--Version"], r"MediaInfoLib - v(\S+)", True, "mediainfo"),
    "bmxtranswrap": ("MXF der Sendeprofile (RDD9, AES3)", ["--version"], r"bmx v([\d.]+)", True, "bmx"),
    "mxf2raw": ("MXF-Struktur prüfen (Partitionen, Index)", ["--version"], r"bmx v([\d.]+)", False, ""),
}
HOMEBREW_DIRS = ["/opt/homebrew/bin", "/usr/local/bin"]
SETTINGS_PREFIX = "system.schnittstellen.werkzeuge"
CUSTOM, BUNDLED = "eigener_pfad", "mitgeliefert"


@dataclass(frozen=True)
class ToolStatus:
    name: str
    purpose: str
    path: str
    source: str             # Einstellung | mitgeliefert | PATH | Homebrew | ""
    version: str
    required: bool
    note: str = ""          # z. B. eigener Pfad nicht ausführbar

    @property
    def ok(self) -> bool:
        return bool(self.path and self.version)

    def line(self) -> str:
        note = f" – {self.note}" if self.note else ""
        if self.ok:
            return f"{self.name}: {self.version} ({self.source}: {self.path}){note}"
        if self.path:
            return f"{self.name}: gefunden, antwortet aber nicht ({self.path}){note}"
        return f"{self.name}: fehlt" + ("" if self.required else " (optional)") + note


def configured(get: Mapping[str, str] | None) -> dict[str, str]:
    """Eigene Pfade aus den Einstellungen: {Werkzeug: Pfad}, nur wo `eigener_pfad` gewählt ist.
    `get` ist ein Mapping Einstellungspfad → Wert (z. B. aus `Settings.get`)."""
    out: dict[str, str] = {}
    for name, (*_rest, key) in TOOLS.items():
        if not key or get is None:
            continue
        mode = get.get(f"{SETTINGS_PREFIX}.{key}.modus") or BUNDLED
        path = str(get.get(f"{SETTINGS_PREFIX}.{key}.pfad") or "").strip()
        if mode == CUSTOM and path:
            out[name] = os.path.expanduser(path)
    return out


def bundle_dirs() -> list[str]:
    """Kandidaten für den mitgelieferten bin-Ordner (nur vorhandene)."""
    cand = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand.append(os.path.join(meipass, "bin"))
    if getattr(sys, "frozen", False):
        contents = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
        cand += [os.path.join(contents, "Resources", "bin"), os.path.join(contents, "Frameworks", "bin")]
    cand.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "bin"))
    return [os.path.abspath(c) for c in cand if os.path.isdir(c)]


def _executable(p: str | None) -> bool:
    return bool(p) and os.path.isfile(p) and os.access(p, os.X_OK)


def locate(name: str, custom: Mapping[str, str] | None = None) -> tuple[str, str]:
    """(Pfad, Quelle) oder ("", "")."""
    own = (custom or {}).get(name, "")
    if _executable(own):
        return own, "Einstellung"
    for d in bundle_dirs():
        cand = os.path.join(d, name)
        if _executable(cand):
            return cand, BUNDLED
    found = shutil.which(name)
    if found:
        return found, "PATH"
    for d in HOMEBREW_DIRS:
        cand = os.path.join(d, name)
        if _executable(cand):
            return cand, "Homebrew"
    return "", ""


def find(name: str, custom: Mapping[str, str] | None = None) -> str:
    return locate(name, custom)[0]


def version(name: str, path: str, timeout: float = 10) -> str:
    """Kurze Versionsangabe oder '' wenn das Programm nicht antwortet."""
    if not _executable(path):
        return ""
    _p, args, pattern, *_ = TOOLS.get(name, ("", ["--version"], r"(\S+)", False, ""))
    try:
        proc = subprocess.run([path, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(pattern, text)
    return m.group(1) if m else (text.strip().splitlines() or [""])[0][:40]


def check(custom: Mapping[str, str] | None = None) -> list[ToolStatus]:
    out = []
    for name, (purpose, _a, _p, required, _k) in TOOLS.items():
        path, source = locate(name, custom)
        own = (custom or {}).get(name, "")
        note = f"eigener Pfad nicht ausführbar: {own}" if own and source != "Einstellung" else ""
        out.append(ToolStatus(name, purpose, path, source, version(name, path) if path else "", required, note))
    return out


def report(custom: Mapping[str, str] | None = None) -> str:
    """Mehrzeiliger Text für Protokoll, Diagnose und Einstellungen."""
    lines = [s.line() for s in check(custom)]
    b = bundle_dirs()
    lines.append("mitgelieferter Ordner: " + (b[0] if b else "keiner (Entwicklung ohne resources/bin)"))
    return "\n".join(lines)
