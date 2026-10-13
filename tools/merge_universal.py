#!/usr/bin/env python3
"""Zwei Bauten von Ottilie.app (arm64 und x86_64) zu einem Universal-Paket zusammenfügen.

Apple-Silicon-Umstellung (Jens 06.10.2026). Vorbild: PunchBuddy `merge_universal.py` (Silicon-Bau als Grundlage,
Maschinencode mit `lipo` vereinen). Hier zusätzlich: Dateien, die schon beide Architekturen enthalten (z. B. PyObjC,
Python selbst), werden übernommen statt doppelt vereint; Dateien, die es nur im Intel-Bau gibt (z. B. OpenBLAS von
scipy/numpy), kommen dazu; die Lizenzliste der Werkzeuge wird zusammengeführt; danach wird jede Datei mit
Maschinencode und das Paket neu ad hoc signiert (auf Apple Silicon startet unsignierter Code nicht).

    python3 tools/merge_universal.py <arm64/Ottilie.app> <x86_64/Ottilie.app> <Ziel/Ottilie.app>

Das Ziel darf es noch nicht geben (nichts wird überschrieben). Die beiden Bauten bleiben unverändert.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tonmischung.selftest import file_archs, runs_on  # noqa: E402

ARM, INTEL = "arm64", "x86_64"
MANIFEST = os.path.join("Contents", "Resources", "bin", "licenses", "manifest.json")
INFO = "build_info.json"
KEEP, TAKE_INTEL, LIPO, ODD = "behalten", "Intel-Fassung", "lipo", "unklar"


def action(arm: list[str], intel: list[str], same: bool) -> str:
    """Was mit einer Datei geschieht, die es in beiden Bauten gibt (Architekturen je Seite, gleicher Inhalt?)."""
    if not arm and not intel:
        return KEEP                                 # kein Maschinencode (Daten, Python-Archiv, Texte)
    a, i = set(arm), set(intel)
    if same or ((ARM in a or "arm64e" in a) and INTEL in a):
        return KEEP                                 # schon universal
    if (ARM in i or "arm64e" in i) and INTEL in i:
        return TAKE_INTEL
    if a and i and not (a & i):
        return LIPO
    return ODD


def merge_manifests(arm: dict, intel: dict) -> dict:
    """Lizenzliste beider Bauten: alles aus beiden; gleiche Bestandteile in verschiedenen Versionen beide nennen."""
    out = {k: dict(v) for k, v in arm.items()}
    for k, v in intel.items():
        if k not in out:
            out[k] = dict(v)
        elif out[k].get("version") != v.get("version"):
            out[k]["version"] = f"{out[k].get('version', '')} (Apple Silicon), {v.get('version', '')} (Intel)"
    return out


def merge_info(arm: dict, intel: dict) -> dict:
    """Bauangaben vereinen; Stand und Paketliste müssen gleich sein, sonst passen die Hälften nicht zusammen."""
    for key, what in (("commit", "Stand (Commit)"), ("lock", "Paketliste"), ("version", "Version")):
        if arm.get(key) != intel.get(key):
            raise SystemExit(f"{what} verschieden: Apple Silicon {arm.get(key)}, Intel {intel.get(key)} – "
                             "beide Hälften aus demselben Stand bauen")
    built = {**(intel.get("built") or {}), **(arm.get("built") or {})}
    return {**arm, "archs": sorted(set(arm.get("archs", [])) | set(intel.get("archs", []))), "built": built}


def _find_info(app: str) -> str | None:
    for dirpath, _d, files in os.walk(app):
        if INFO in files and os.path.basename(dirpath) == "tonmischung":
            p = os.path.join(dirpath, INFO)
            if not os.path.islink(p):
                return os.path.relpath(p, app)
    return None


def _same(a: str, b: str) -> bool:
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if x != y:
                return False
            if not x:
                return True


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def merge(arm_app: str, intel_app: str, out_app: str, allow_unknown: bool = False) -> dict[str, int]:
    if os.path.exists(out_app):
        raise SystemExit(f"Ziel gibt es schon: {out_app} – nichts überschrieben")
    for p, want in ((arm_app, ARM), (intel_app, INTEL)):
        exe = os.path.join(p, "Contents", "MacOS", "Ottilie")
        if not runs_on(file_archs(exe), want):
            raise SystemExit(f"{p}: kein {want}-Bau "
                             f"(Programm hat {', '.join(file_archs(exe)) or 'keinen Maschinencode'})")
    info_rel = _find_info(arm_app)
    merged_info = None
    if info_rel and os.path.isfile(os.path.join(intel_app, info_rel)):
        with open(os.path.join(arm_app, info_rel), encoding="utf-8") as fa, \
                open(os.path.join(intel_app, info_rel), encoding="utf-8") as fi:
            merged_info = merge_info(json.load(fa), json.load(fi))          # vor dem Kopieren: nichts halb fertig
    elif not allow_unknown:
        raise SystemExit("Bauangabe fehlt in einem der Pakete – mit tools/build_app.sh bauen")
    shutil.copytree(arm_app, out_app, symlinks=True)
    if merged_info is not None:
        with open(os.path.join(out_app, info_rel), "w", encoding="utf-8") as fo:
            json.dump(merged_info, fo, ensure_ascii=False, indent=1)
    count = {KEEP: 0, TAKE_INTEL: 0, LIPO: 0, ODD: 0, "nur Intel": 0, "nur Silicon": 0, "signiert": 0}
    odd, failed = [], []
    for dirpath, dirnames, files in os.walk(out_app):
        for f in files:
            out = os.path.join(dirpath, f)
            rel = os.path.relpath(out, out_app)
            intel = os.path.join(intel_app, rel)
            if os.path.islink(out):
                continue
            if not os.path.lexists(intel):
                count["nur Silicon"] += 1
                continue
            if rel == info_rel:
                continue
            if rel == MANIFEST:
                with open(out, encoding="utf-8") as fa, open(intel, encoding="utf-8") as fi:
                    merged = merge_manifests(json.load(fa), json.load(fi))
                with open(out, "w", encoding="utf-8") as fo:
                    json.dump(merged, fo, ensure_ascii=False, indent=1, sort_keys=True)
                continue
            a, i = file_archs(out), file_archs(intel)
            what = action(a, i, bool(a) and _same(out, intel))
            count[what] += 1
            if what == TAKE_INTEL:
                shutil.copy2(intel, out)
            elif what == LIPO:
                tmp = out + ".lipo"
                r = _run(["lipo", "-create", "-output", tmp, out, intel])
                if r.returncode != 0:
                    failed.append(f"{rel}: {r.stderr.strip()}")
                    continue
                shutil.copymode(out, tmp)
                os.replace(tmp, out)                    # eigene Kopie im Ziel
            elif what == ODD:
                odd.append(f"{rel} (Silicon: {', '.join(a) or '–'}, Intel: {', '.join(i) or '–'})")
    # Dateien, die es nur im Intel-Bau gibt
    for dirpath, dirnames, files in os.walk(intel_app):
        for name in dirnames + files:
            src = os.path.join(dirpath, name)
            rel = os.path.relpath(src, intel_app)
            dst = os.path.join(out_app, rel)
            if os.path.lexists(dst):
                continue
            if os.path.islink(src):
                os.symlink(os.readlink(src), dst)
            elif os.path.isdir(src):
                os.makedirs(dst)
                continue
            else:
                shutil.copy2(src, dst)
            count["nur Intel"] += 1
    # Neu signieren: erst jede Datei mit Maschinencode (tiefste zuerst), dann das Paket
    machos = []
    for dirpath, _d, files in os.walk(out_app):
        for f in files:
            p = os.path.join(dirpath, f)
            if not os.path.islink(p) and file_archs(p):
                machos.append(p)
    for p in sorted(machos, key=lambda x: -x.count(os.sep)):
        if os.path.realpath(p) == os.path.realpath(os.path.join(out_app, "Contents", "MacOS", "Ottilie")):
            continue                                    # wird mit dem Paket signiert
        r = _run(["codesign", "--force", "--sign", "-", p])
        if r.returncode != 0:
            failed.append(f"signieren {os.path.relpath(p, out_app)}: {r.stderr.strip()}")
        else:
            count["signiert"] += 1
    r = _run(["codesign", "--force", "--sign", "-", out_app])
    if r.returncode != 0:
        failed.append(f"Paket signieren: {r.stderr.strip()}")
    for o in odd:
        print("  ?? unklar, Silicon-Fassung behalten:", o)
    for f in failed:
        print("  !!", f)
    count["Fehler"] = len(failed)
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("arm64_app")
    ap.add_argument("x86_64_app")
    ap.add_argument("ziel")
    ap.add_argument("--ohne-bauangabe", action="store_true", help="Pakete ohne Bauangabe trotzdem zusammenfügen")
    a = ap.parse_args()
    count = merge(a.arm64_app, a.x86_64_app, a.ziel, allow_unknown=a.ohne_bauangabe)
    print("Zusammengefügt:", ", ".join(f"{k} {v}" for k, v in count.items()))
    exe = os.path.join(a.ziel, "Contents", "MacOS", "Ottilie")
    print(f"{a.ziel}: {', '.join(file_archs(exe))}")
    return 1 if count["Fehler"] else 0


if __name__ == "__main__":
    sys.exit(main())
