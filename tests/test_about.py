"""„Über Tonmischung“: jede mitgelieferte Fremdsoftware steht mit Lizenz, Urheber und Projekt in der Liste."""
import ast
import pathlib
import sys
from importlib import metadata

from tonmischung import about

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOT_SHIPPED = {"av", "imageio-ffmpeg", "pywebview", "pynput"}          # im Paket ausgeschlossen (Tonmischung.spec)


def _imports():
    mods = set()
    files = list((ROOT / "tonmischung").rglob("*.py")) + list((ROOT / "vendor" / "punchbuddy" / "punchbuddy").rglob("*.py")) \
        + [ROOT / "vendor" / "punchbuddy" / n for n in ("vocaster_control.py", "vocaster_integration.py")] \
        + list((ROOT / "vendor" / "hedwig").glob("*.py"))
    local = {p.stem for p in files} | {"tonmischung", "punchbuddy"}
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mods.add(n.module.split(".")[0])
    return {m for m in mods if m not in sys.stdlib_module_names and m not in local}


def test_jedes_fremdpaket_steht_in_der_liste():
    listed = {c.dist for c in about.COMPONENTS if c.dist}
    dists = metadata.packages_distributions()
    missing = []
    for m in sorted(_imports()):
        names = set(dists.get(m, []))
        if not names:
            continue                                      # nicht installiert (z. B. nur auf anderen Systemen)
        if names & listed or names & NOT_SHIPPED or any(n.startswith("pyobjc") for n in names):
            continue
        missing.append(f"{m} ({', '.join(sorted(names))})")
    assert missing == [], "In tonmischung/about.py nachtragen: " + ", ".join(missing)


def test_angaben_vollstaendig_und_markdown():
    for c in about.COMPONENTS:
        assert c.name and c.license and c.author and c.use and c.kind in about.KINDS, c
        assert not c.url or c.url.startswith("https://"), c
    rows = about.listing()
    assert any(r["name"] == "FFmpeg" and "GPL-3.0" in r["license"] and "Quelltext" in r["note"] for r in rows)
    assert next(r for r in rows if r["name"] == "pyaaf2")["version"]                # zur Laufzeit gelesen
    md = about.markdown("1.0")
    assert md.startswith("# Ottilie 1.0 – mitgelieferte Software") and "| IBM Plex Sans / Mono |" in md
