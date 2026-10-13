#!/usr/bin/env python3
"""Paketversionen für den Bau festschreiben (`tools/requirements-bau.txt`).

Apple-Silicon-Umstellung (Jens 06.10.2026: „Wir stellen mit Einführung von Helmut auf Silicon-Macs um“): Intel- und
Silicon-Teil des Universal-Pakets müssen aus genau denselben Paketversionen gebaut werden, sonst passen die beiden
Hälften nicht zusammen. Festgeschrieben wird, was Ottilie, PunchBuddy (Laufzeitteil), Hedwig, Tests und
Bauwerkzeuge importieren – samt allen Abhängigkeiten, in der Version, die auf diesem Mac installiert ist. Pakete, die
nur zufällig in der Python-Umgebung liegen (andere Projekte), kommen nicht hinein.

    python3 tools/lock_requirements.py            # Datei neu schreiben
    python3 tools/lock_requirements.py --pruefen  # installierte Versionen gegen die Datei prüfen
                                                  # (Exit 1 bei Abweichung)
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from importlib import metadata

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCK = ROOT / "tools" / "requirements-bau.txt"
BUILD_TOOLS = ("pyinstaller", "pytest")       # Paketbau und Tests, nicht im Paket
# im Paket ausgeschlossen (Ottilie.spec) und für Bau und Tests nicht nötig
NOT_SHIPPED = {"av", "imageio-ffmpeg", "pywebview", "pynput"}


def source_files(root: pathlib.Path = ROOT) -> list[pathlib.Path]:
    vendor = root / "vendor" / "punchbuddy"
    return (list((root / "tonmischung").rglob("*.py")) + list((vendor / "punchbuddy").rglob("*.py"))
            + [vendor / n for n in ("vocaster_control.py", "vocaster_integration.py")]
            + list((root / "vendor" / "hedwig").glob("*.py")) + list((root / "tests").glob("*.py"))
            + list((root / "tools").glob("*.py")))


def imported_modules(files: Iterable[pathlib.Path]) -> set[str]:
    """Oberste Modulnamen aller absoluten Importe, ohne Standardbibliothek und eigene Module."""
    files = list(files)
    local = {p.stem for p in files} | {"tonmischung", "punchbuddy", "hedwig", "tests", "conftest"}
    mods: set[str] = set()
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mods.add(n.module.split(".")[0])
    return {m for m in mods if m not in sys.stdlib_module_names and m not in local}


def spec_modules(spec: pathlib.Path, known: Iterable[str]) -> set[str]:
    """Module, die erst die Paketbeschreibung einbindet (hiddenimports, collect_all – z. B. `hid`, `usb`)."""
    known = set(known)
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    return {n.value.split(".")[0] for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.split(".")[0] in known}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def closure(roots: Iterable[str], requires: Callable[[str], list[str]]) -> set[str]:
    """Alle Distributionen, die `roots` (kanonische Namen) brauchen – `requires` liefert die direkten Abhängigkeiten."""
    seen: set[str] = set()
    todo = [canonical(r) for r in roots]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += [canonical(d) for d in requires(name) if canonical(d) not in seen]
    return seen


def installed_requires(name: str) -> list[str]:
    """Direkte Abhängigkeiten einer installierten Distribution für diesen Mac (Marker ausgewertet, ohne Extras)."""
    from packaging.requirements import Requirement
    try:
        reqs = metadata.requires(name) or []
    except metadata.PackageNotFoundError:
        return []
    out = []
    for r in reqs:
        req = Requirement(r)
        if req.marker is None or req.marker.evaluate({"extra": ""}):
            out.append(req.name)
    return out


def roots_for(mods: Iterable[str], dists: Mapping[str, list[str]]) -> set[str]:
    out = {canonical(t) for t in BUILD_TOOLS}
    for m in mods:
        out |= {canonical(d) for d in dists.get(m, []) if canonical(d) not in NOT_SHIPPED}
    return out


def lock_lines(names: Iterable[str], version: Callable[[str], str | None]) -> list[str]:
    lines = []
    for n in sorted(names):
        v = version(n)
        if v:                                   # nicht installiert: hier nicht nötig (z. B. nur auf Windows)
            lines.append(f"{n}=={v}")
    return lines


def parse_lock(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, v = line.split("==", 1)
            out[canonical(name)] = v.strip()
    return out


def differences(locked: Mapping[str, str], version: Callable[[str], str | None]) -> list[str]:
    out = []
    for name, want in sorted(locked.items()):
        have = version(name)
        if have != want:
            out.append(f"{name}: festgeschrieben {want}, installiert {have or 'fehlt'}")
    return out


def _version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pruefen", action="store_true", help="installierte Versionen gegen die Datei prüfen")
    a = ap.parse_args()
    if a.pruefen:
        diff = differences(parse_lock(LOCK.read_text(encoding="utf-8")), _version)
        for d in diff:
            print("  !!", d)
        print("Paketversionen wie festgeschrieben" if not diff else f"{len(diff)} Abweichungen von {LOCK.name}")
        return 1 if diff else 0
    dists = metadata.packages_distributions()
    mods = imported_modules(source_files()) | spec_modules(ROOT / "tools" / "Ottilie.spec", dists)
    roots = roots_for(mods, dists)
    lines = lock_lines(closure(roots, installed_requires), _version)
    head = (f"# Paketversionen für den Bau von Ottilie (Intel und Apple Silicon gleich).\n"
            f"# Erzeugt mit tools/lock_requirements.py unter Python {sys.version.split()[0]} – nicht von Hand ändern.\n"
            f"# Einrichten: tools/venv_bau.sh\n")
    LOCK.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(lines)} Pakete → {LOCK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
