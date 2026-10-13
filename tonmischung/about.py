"""„Über Ottilie“: mitgelieferte Software mit Lizenz, Urheber und Projekt-Link (Jens 04.10.2026: „irgendwo in der
App sollte es einen Bereich geben, in dem alle Software mit Lizenz und Projekt-Link und Nennung des Urhebers aufgelistet
ist“). Die Liste ist von Hand gepflegt; ein Test prüft, dass jedes Fremdpaket, das die App importiert, darin steht.
Versionen werden – wo möglich – zur Laufzeit gelesen.
"""
from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class Component:
    name: str
    license: str
    author: str
    url: str
    use: str                     # wofür die App es braucht
    kind: str = "python"         # python | werkzeug | bibliothek | schrift | grundlage
    dist: str = ""               # Python-Distribution (Version zur Laufzeit) oder ""
    version: str = ""            # feste Version, wenn nicht zur Laufzeit lesbar
    note: str = ""
    formula: str = ""            # Werkzeuge/Bibliotheken: Name im Lizenz-Manifest des Pakets (Version, Quelltext)
    source: str = ""             # Quelltext, wenn nicht aus dem Manifest


COMPONENTS: tuple[Component, ...] = (
    # ── Grundlagen ──────────────────────────────────────────────────────────
    Component("Python", "PSF-2.0", "Python Software Foundation", "https://www.python.org", "Laufzeit der App",
              "grundlage"),
    Component("PyInstaller", "GPL-2.0-or-later mit Bootloader-Ausnahme", "Hartmut Goebel, Giovanni Bajo u. a.",
              "https://pyinstaller.org", "Paketbau (Startprogramm der App)", "grundlage", "pyinstaller",
              note="Die Ausnahme erlaubt das Mitliefern des Startprogramms ohne GPL-Pflichten für die App."),
    Component("PunchBuddy", "MIT", "Jens Rühl (ruehljens-creator)",
              "https://github.com/ruehljens-creator/punchbuddy-protools",
              "Punch-In, Transport, Exporte, Lautheit, Stream Deck, Vocaster", "grundlage", version="2.1.1"),
    Component("Hedwig (AAF-Erkennung)", "alle Rechte vorbehalten", "Jens Rühl", "", "AAF-Triage: Paare, Inhalt", "grundlage"),
    Component("Pro Tools Scripting Library (PTSL)", "Avid PTSL SDK", "Avid Technology, Inc.",
              "https://developer.avid.com", "Steuerung von Pro Tools", "grundlage"),
    # ── Python-Pakete ────────────────────────────────────────────────────────
    Component("py-ptsl", "BSD-3-Clause", "Jamie Hardt", "https://github.com/iluvcapra/py-ptsl", "PTSL-Anbindung",
              dist="py-ptsl"),
    Component("grpcio", "Apache-2.0", "The gRPC Authors", "https://grpc.io", "Verbindung zu Pro Tools (gRPC)",
              dist="grpcio"),
    Component("protobuf", "BSD-3-Clause", "Google LLC", "https://protobuf.dev", "PTSL-Nachrichten", dist="protobuf"),
    Component("pyaaf2", "MIT", "Mark Reid", "https://github.com/markreidvfx/pyaaf2", "AAF lesen und schreiben",
              dist="pyaaf2"),
    Component("NumPy", "BSD-3-Clause (mit 0BSD, MIT, Zlib)", "NumPy Developers", "https://numpy.org",
              "Signalanalyse (Triage, Lautheit)", dist="numpy"),
    Component("SciPy", "BSD-3-Clause", "SciPy Developers", "https://scipy.org", "Signalanalyse (Triage)",
              dist="scipy"),
    Component("python-soundfile", "BSD-3-Clause", "Bastian Bechtold", "https://github.com/bastibe/python-soundfile",
              "WAV lesen und schreiben (mit libsndfile, LGPL-2.1)", dist="soundfile"),
    Component("cffi", "MIT", "Armin Rigo, Maciej Fijalkowski", "https://cffi.readthedocs.io", "für python-soundfile",
              dist="cffi"),
    Component("pyloudnorm", "MIT", "Christian Steinmetz", "https://github.com/csteinmetz1/pyloudnorm",
              "Lautheit nach EBU R 128", dist="pyloudnorm"),
    Component("rumps", "BSD-3-Clause", "Jared Suttles", "https://github.com/jaredks/rumps", "Menüleiste", dist="rumps"),
    Component("PyObjC", "MIT", "Ronald Oussoren u. a.", "https://github.com/ronaldoussoren/pyobjc",
              "Fenster, Dialoge, Bonjour, Schlüsselbund (macOS)", dist="pyobjc-core"),
    Component("PyUSB", "BSD-3-Clause", "Jonas Malaco u. a.", "https://pyusb.github.io/pyusb", "Focusrite Vocaster",
              dist="pyusb"),
    Component("hidapi (cython-hidapi)", "BSD-3-Clause (wahlweise GPL-3.0/HIDAPI)", "Pavol Rusnak u. a.",
              "https://github.com/trezor/cython-hidapi", "Stream Deck / Vocaster", dist="hidapi"),
    Component("MCP Python SDK", "MIT", "Anthropic, PBC", "https://modelcontextprotocol.io",
              "Schnittstelle für KI-Assistenten (optional)", dist="mcp"),
    # ── Werkzeuge (Havarie-Auslieferung) ─────────────────────────────────────
    Component("FFmpeg", "GPL-3.0-or-later (dieser Build: --enable-gpl --enable-version3)", "the FFmpeg developers",
              "https://ffmpeg.org", "Sendefile bauen, prüfen (ffmpeg, ffprobe)", "werkzeug", version="9.0.1",
              note="GPL wegen x264/x265 (AVC-Intra-Kodierung). Unverändert; Quelltext genau dieser Version unter "
                   "„Quelltext“, Lizenztexte im Paket (bin/licenses).", formula="ffmpeg"),
    Component("MediaInfo", "BSD-2-Clause", "MediaArea.net SARL", "https://mediaarea.net/MediaInfo",
              "Sendefile prüfen", "werkzeug", version="26.05", formula="media-info"),
    Component("bmx", "BSD-3-Clause", "British Broadcasting Corporation (BBC R&D), weitergeführt von der EBU",
              "https://github.com/ebu/bmx", "MXF nach ARD-ZDF-HDF verpacken (bmxtranswrap, mxf2raw)", "werkzeug",
              version="1.7", formula="bmx", source="https://github.com/ebu/bmx/tree/v1.7"),
    Component("x264", "GPL-2.0-or-later", "VideoLAN / x264 project", "https://www.videolan.org/developers/x264.html",
              "AVC-Intra kodieren (in FFmpeg)", "bibliothek", formula="x264"),
    Component("x265", "GPL-2.0-or-later", "MulticoreWare Inc.", "https://www.x265.org", "in FFmpeg",
              "bibliothek", formula="x265"),
    Component("libvpx", "BSD-3-Clause", "The WebM Project", "https://www.webmproject.org", "in FFmpeg",
              "bibliothek", formula="libvpx"),
    Component("dav1d", "BSD-2-Clause", "VideoLAN", "https://code.videolan.org/videolan/dav1d", "in FFmpeg",
              "bibliothek", formula="dav1d"),
    Component("SVT-AV1", "BSD-3-Clause-Clear", "Alliance for Open Media", "https://gitlab.com/AOMediaCodec/SVT-AV1",
              "in FFmpeg", "bibliothek", formula="svt-av1"),
    Component("LAME", "LGPL-2.0-or-later", "The LAME Project", "https://lame.sourceforge.io", "in FFmpeg",
              "bibliothek", formula="lame"),
    Component("mpg123", "LGPL-2.1", "Michael Hipp, Thomas Orgis u. a.", "https://www.mpg123.de", "in FFmpeg",
              "bibliothek", formula="mpg123"),
    Component("Opus", "BSD-3-Clause", "Xiph.Org Foundation u. a.", "https://opus-codec.org", "in FFmpeg",
              "bibliothek", formula="opus"),
    Component("libvmaf", "BSD-2-Clause-Patent", "Netflix, Inc.", "https://github.com/Netflix/vmaf", "in FFmpeg",
              "bibliothek", formula="libvmaf"),
    Component("OpenSSL", "Apache-2.0", "The OpenSSL Project", "https://www.openssl.org", "in FFmpeg",
              "bibliothek", formula="openssl@3"),
    Component("XZ Utils (liblzma)", "0BSD", "Lasse Collin u. a.", "https://tukaani.org/xz/", "in FFmpeg",
              "bibliothek", formula="xz"),
    Component("MediaInfoLib", "BSD-2-Clause", "MediaArea.net SARL", "https://mediaarea.net/MediaInfo", "in MediaInfo",
              "bibliothek", formula="libmediainfo"),
    Component("ZenLib", "Zlib", "MediaArea.net SARL", "https://github.com/MediaArea/ZenLib", "in MediaInfo",
              "bibliothek", formula="libzen"),
    Component("uriparser", "BSD-3-Clause", "Weijia Song, Sebastian Pipping", "https://uriparser.github.io",
              "in bmx", "bibliothek", formula="uriparser"),
    # ── Schrift ──────────────────────────────────────────────────────────────
    Component("IBM Plex Sans / Mono", "OFL-1.1", "IBM Corp. (Mike Abbink, Bold Monday)", "https://github.com/IBM/plex",
              "Hausschrift der Oberfläche (Lizenz: ui/fonts/LICENSE-IBM-Plex.txt)", "schrift",
              version="Sans 1.1.0 · Mono 2.5.0"),
)

KINDS = {"grundlage": "Grundlagen", "python": "Python-Pakete", "werkzeug": "Werkzeuge (Havarie-Auslieferung)",
         "bibliothek": "Bibliotheken in den Werkzeugen", "schrift": "Schrift"}


_MANIFEST: dict | None = None


def manifest() -> dict:
    """Lizenz-Manifest der mitgelieferten Werkzeuge (`bin/licenses/manifest.json`, beim Paketbau erzeugt)."""
    global _MANIFEST
    if _MANIFEST is None:
        from tonmischung.delivery.tools import bundle_dirs
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _MANIFEST = {"dir": "", "eintraege": {}}
        for d in bundle_dirs() + [os.path.join(root, "build", "bin")]:
            try:
                with open(os.path.join(d, "licenses", "manifest.json"), encoding="utf-8") as f:
                    _MANIFEST = {"dir": os.path.join(d, "licenses"), "eintraege": json.load(f)}
                    break
            except (OSError, ValueError):
                continue
    return _MANIFEST


def version_of(c: Component) -> str:
    entry = manifest()["eintraege"].get(c.formula, {}) if c.formula else {}
    if entry.get("version"):
        return entry["version"]
    if c.version:
        return c.version
    if c.kind == "grundlage" and c.name == "Python":
        return platform.python_version()
    if c.dist:
        try:
            from importlib import metadata
            return metadata.version(c.dist)
        except Exception:  # noqa: BLE001 – im Paket nicht immer lesbar
            return ""
    return ""


def source_of(c: Component) -> str:
    entry = manifest()["eintraege"].get(c.formula, {}) if c.formula else {}
    return entry.get("source") or c.source


def listing() -> list[dict]:
    return [{"name": c.name, "version": version_of(c), "license": c.license, "author": c.author, "url": c.url,
             "source": source_of(c), "use": c.use, "kind": c.kind, "group": KINDS[c.kind], "note": c.note}
            for c in COMPONENTS]


def markdown(app_version: str = "") -> str:
    """Für `docs/LIZENZEN.md` und die Kommandozeile."""
    out = [f"# Ottilie {app_version} – mitgelieferte Software".rstrip(), "",
           "Erzeugt mit `python3 -m tonmischung lizenzen`. Lizenz, Urheber, Projekt und – bei Werkzeugen und "
           "Bibliotheken – der Quelltext genau der mitgelieferten Version (unverändert). Die Lizenztexte liegen im "
           "Paket unter `bin/licenses/`, die der Schrift unter `ui/fonts/`.", ""]
    for kind, title in KINDS.items():
        rows = [r for r in listing() if r["kind"] == kind]
        if not rows:
            continue
        out += [f"## {title}", "", "| Software | Version | Lizenz | Urheber | Projekt | Quelltext | Wofür |",
                "|---|---|---|---|---|---|---|"]
        for r in rows:
            out.append(f"| {r['name']} | {r['version'] or '–'} | {r['license']} | {r['author']} | {r['url'] or '–'} | "
                       f"{r['source'] or '–'} | {r['use']}{(' – ' + r['note']) if r['note'] else ''} |")
        out.append("")
    return "\n".join(out)
