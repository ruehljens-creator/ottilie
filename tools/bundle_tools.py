#!/usr/bin/env python3
"""Werkzeuge der Havarie-Auslieferung (ffmpeg, ffprobe, mediainfo, bmxtranswrap, mxf2raw) mit allen Bibliotheken in
einen verschiebbaren Ordner bündeln, damit Ottilie.app sie mitbringt und auf den Studio-Macs kein Homebrew nötig
ist (Auftrag M9). Herkunft: punchbuddy-helmut `tools/werkzeuge_buendeln.py`.

    python3 tools/bundle_tools.py [--ziel build/bin]

Je Programm: kopieren, Nicht-System-Bibliotheken (otool -L, rekursiv) nach <ziel>/lib, Ladepfade auf
@executable_path/lib bzw. @loader_path umschreiben, ad hoc signieren, einmal starten. Danach darf kein absoluter
Homebrew-Pfad mehr vorkommen (geprüft). Die Architektur ist die der Homebrew-Programme dieses Macs.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tonmischung.delivery import tools as W  # noqa: E402

FOREIGN = ("/usr/local/", "/opt/homebrew/", "/opt/local/")
MARKER = ".ottilie-werkzeuge"                    # Kennung: diesen Ordner hat bundle_tools angelegt


def clear_target(target: str, root: str = ROOT) -> None:
    """Alten Zielordner entfernen – nur, wenn er unter <Projekt>/build liegt oder die Kennung dieses Skripts trägt.
    Sonst Abbruch: vorher löschte `--ziel ~/Desktop` ungeprüft den ganzen Ordner (Review A21)."""
    if not os.path.lexists(target):
        return
    real = os.path.realpath(target)
    build = os.path.realpath(os.path.join(root, "build"))
    inside_build = real.startswith(build + os.sep)
    if (os.path.islink(target) or not os.path.isdir(real)
            or not (inside_build or os.path.isfile(os.path.join(real, MARKER)))):
        raise SystemExit(f"{target}: kein Bauordner von bundle_tools – wird nicht gelöscht. Bitte einen leeren oder "
                         f"neuen Ordner angeben (Vorgabe: {os.path.join(root, 'build', 'bin')}).")
    shutil.rmtree(real)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _deps(path: str) -> list[str]:
    out = []
    for line in _run(["otool", "-L", path]).stdout.splitlines()[1:]:
        p = line.strip().split(" (")[0]
        if p and not p.startswith(("/usr/lib/", "/System/", "@")):
            out.append(p)
    return out


def bundle(target: str, names: list[str]) -> bool:
    clear_target(target)
    lib = os.path.join(target, "lib")
    os.makedirs(lib)
    open(os.path.join(target, MARKER), "w").close()
    copied: dict[str, str] = {}
    missing = []

    def take_lib(src: str) -> str:
        real = os.path.realpath(src)
        if real not in copied:
            name = os.path.basename(real)
            dst = os.path.join(lib, name)
            shutil.copy2(real, dst)
            os.chmod(dst, 0o755)
            copied[real] = name
            for d in _deps(dst):
                take_lib(d)
        return copied[real]

    for name in names:
        src = W.find(name)
        if not src:
            missing.append(name)
            print(f"  !! {name}: nicht gefunden – nicht gebündelt")
            continue
        dst = os.path.join(target, name)
        shutil.copy2(os.path.realpath(src), dst)
        os.chmod(dst, 0o755)
        for d in _deps(dst):
            take_lib(d)
        print(f"  {name} ← {os.path.realpath(src)}")

    for prog in [os.path.join(target, n) for n in names if os.path.isfile(os.path.join(target, n))]:
        for d in _deps(prog):
            new = copied.get(os.path.realpath(d))
            if new:
                _run(["install_name_tool", "-change", d, f"@executable_path/lib/{new}", prog])
        _run(["codesign", "--force", "-s", "-", prog])
    for name in copied.values():
        p = os.path.join(lib, name)
        _run(["install_name_tool", "-id", f"@loader_path/{name}", p])
        for d in _deps(p):
            new = copied.get(os.path.realpath(d))
            if new:
                _run(["install_name_tool", "-change", d, f"@loader_path/{new}", p])
        _run(["codesign", "--force", "-s", "-", p])

    collect_licenses(target, [os.path.realpath(W.find(n) or "") for n in names] + list(copied))

    problems = []
    for f in [os.path.join(target, n) for n in names] + [os.path.join(lib, n) for n in copied.values()]:
        if os.path.isfile(f):
            problems += [f"{os.path.basename(f)} lädt noch {p}" for p in
                         (line.strip().split(" (")[0] for line in _run(["otool", "-L", f]).stdout.splitlines()[1:])
                         if p.startswith(FOREIGN)]
    for name in names:
        p = os.path.join(target, name)
        if os.path.isfile(p):
            v = W.version(name, p)
            print(f"  {name} {v or 'ANTWORTET NICHT'}")
            if not v:
                problems.append(f"{name} startet nicht")
    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(target) for f in fs)
    print(f"  {len(copied)} Bibliotheken, {total / 1e6:.0f} MB in {target}")
    for p in problems:
        print("  !!", p)
    return not problems and not missing


LICENSE_PREFIXES = ("copying", "license", "licence", "notice", "authors")
BMX = {"bmxtranswrap", "mxf2raw"}


def _keg(path: str) -> tuple[str, str, str] | None:
    """`/usr/local/Cellar/<Formel>/<Version>/…` → (Formel, Version, Keg-Ordner)."""
    parts = path.split(os.sep)
    if "Cellar" not in parts:
        return None
    i = parts.index("Cellar")
    if len(parts) < i + 3:
        return None
    return parts[i + 1], parts[i + 2], os.sep.join(parts[:i + 3])


def _recipe(keg: str, formula: str) -> dict:
    """Quelltext-Adresse und Lizenz aus dem beim Installieren benutzten Rezept (`.brew/<Formel>.rb`)."""
    import re
    try:
        text = open(os.path.join(keg, ".brew", f"{formula}.rb"), encoding="utf-8").read()
    except OSError:
        return {}
    url = re.search(r'^\s*url\s+"([^"]+)"', text, re.M)
    home = re.search(r'^\s*homepage\s+"([^"]+)"', text, re.M)
    lic = ""
    block = re.search(r'^\s*license\s+(all_of|any_of|one_of):\s*\[(.*?)\]', text, re.M | re.S)
    if block:                                    # z. B. xz: all_of: ["0BSD", "GPL-2.0-or-later", …]
        parts = re.findall(r'"([^"]+)"', block.group(2))
        lic = (" AND " if block.group(1) == "all_of" else " OR ").join(parts)
    else:
        m = re.search(r'^\s*license\s+"([^"]+)"', text, re.M)
        lic = m.group(1) if m else ""
    return {"source": url.group(1) if url else "", "license": lic, "homepage": home.group(1) if home else ""}


BMX_REPO = "https://github.com/ebu/bmx"            # BBC-Repository archiviert, EBU führt bmx weiter (Jens 06.10.2026)


def _bmx_source(scm: str) -> str:
    """Quelltext genau des gebauten Stands: Tag, sonst Commit (aus „v1.7-2-g<Commit>“ oder dem Commit selbst)."""
    m = re.search(r"-g([0-9a-f]{7,})(-dirty)?$", scm)
    ref = m.group(1) if m else scm.removesuffix("-dirty")
    return f"{BMX_REPO}/tree/{ref}" if ref else BMX_REPO


def collect_licenses(target: str, sources: list[str]) -> dict:
    """Lizenztexte, Version und Quelltext-Adresse je Werkzeug/Bibliothek nach `<target>/licenses/` (Jens 04.10.2026:
    „Link auf den Quelltext beifügen“; GPL/BSD verlangen die Lizenztexte beim Weitergeben)."""
    import json
    out = os.path.join(target, "licenses")
    os.makedirs(out, exist_ok=True)
    manifest: dict[str, dict] = {}
    for src in sources:
        if os.path.basename(src) in BMX and "bmx" not in manifest:        # aus dem Quelltext gebaut, nicht Homebrew
            v = _run([src, "-v"]).stdout + _run([src, "-v"]).stderr
            ver = re.search(r"bmx v([\d.]+)", v)
            scm = re.search(r"scm ([\w.\-]+)\)", v)          # Tag (v1.7), Commit oder v1.7-2-g<Commit>
            dest = os.path.join(out, "bmx")
            os.makedirs(dest, exist_ok=True)
            shutil.copy2(os.path.join(ROOT, "tools", "licenses", "bmx-COPYING"), os.path.join(dest, "COPYING"))
            manifest["bmx"] = {"version": ver.group(1) if ver else "", "license": "BSD-3-Clause",
                               "source": _bmx_source(scm.group(1) if scm else "")}
            continue
        k = _keg(src)
        if k is None or k[0] in manifest:
            continue
        formula, version, keg = k
        dest = os.path.join(out, f"{formula}-{version}")
        os.makedirs(dest, exist_ok=True)
        files = [f for f in os.listdir(keg)
                 if f.lower().startswith(LICENSE_PREFIXES) and os.path.isfile(os.path.join(keg, f))]
        for f in files:
            shutil.copy2(os.path.join(keg, f), os.path.join(dest, f))
        manifest[formula] = {"version": re.sub(r"_\d+$", "", version), "files": files, **_recipe(keg, formula)}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"  Lizenzen: {len(manifest)} Bestandteile → {out}")
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ziel", default=os.path.join(ROOT, "build", "bin"))
    ap.add_argument("--nur", nargs="*", default=list(W.TOOLS))
    a = ap.parse_args()
    sys.exit(0 if bundle(a.ziel, a.nur) else 1)
