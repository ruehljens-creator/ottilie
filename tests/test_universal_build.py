"""Apple-Silicon-Umstellung (Jens 06.10.2026): Selbsttest, Universal-Paket zusammenfügen, Paketversionen festschreiben."""
import importlib.util
import json
import os
import pathlib
import plistlib
import shutil
import struct
import subprocess

import pytest

from tonmischung import selftest as S

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _tool("merge_universal")
L = _tool("lock_requirements")


def _thin(cpu, sub=0):
    return struct.pack("<Iii", 0xFEEDFACF, cpu, sub) + b"\0" * 20


def _fat(*cpus):
    head = struct.pack(">II", 0xCAFEBABE, len(cpus))
    for cpu, sub in cpus:
        head += struct.pack(">iiIII", cpu, sub, 4096, 100, 12)
    return head


def test_macho_architekturen_lesen():
    assert S.macho_archs(_thin(0x01000007)) == ["x86_64"]
    assert S.macho_archs(_thin(0x0100000C)) == ["arm64"]
    assert S.macho_archs(_thin(0x0100000C, 2)) == ["arm64e"]
    assert S.macho_archs(_fat((0x01000007, 3), (0x0100000C, 0))) == ["x86_64", "arm64"]
    assert S.macho_archs(struct.pack(">IHH", 0xCAFEBABE, 0, 65) + b"\0" * 40) == []      # Java-Klasse
    assert S.macho_archs(b"#!/bin/sh\n") == [] and S.macho_archs(b"") == []
    assert "x86_64" in S.file_archs("/bin/ls")                                          # macOS: universal
    assert S.file_archs(str(ROOT / "pytest.ini")) == []


def test_laeuft_auf():
    assert S.runs_on(["x86_64", "arm64"], "arm64") and S.runs_on(["arm64e"], "arm64")
    assert not S.runs_on(["x86_64"], "arm64") and not S.runs_on(["arm64"], "x86_64")


def test_module_pruefen_und_zusammenfassung():
    def kaputt():
        raise ImportError("dlopen: incompatible architecture (have 'x86_64', need 'arm64')")
    checks = S.module_checks([("gut", lambda: "1.0"), ("kaputt", kaputt)])
    assert [c.ok for c in checks] == [True, False] and "incompatible architecture" in checks[1].detail
    text = S.summary("Mac: Apple Silicon", checks)
    assert "✓ gut – 1.0" in text and "✕ kaputt" in text and "1 Prüfung(en) fehlgeschlagen." in text
    assert S.summary("Kopf", checks[:1]).endswith("Alles in Ordnung.")
    assert S.system_line("x86_64", True, True).endswith("unter Rosetta (x86_64)")
    assert "Intel" in S.system_line("x86_64", False, False)


def test_selbsttest_hier():
    head, checks = S.run()
    assert head.startswith("Mac: ")
    names = {c.name for c in checks}
    assert {"numpy", "grpc + PTSL", "Oberfläche und Schrift"} <= names
    assert all(c.ok for c in checks if c.group == "Python-Module"), [c for c in checks if not c.ok]


def test_was_beim_zusammenfuegen_geschieht():
    assert M.action([], [], False) == M.KEEP                                  # Daten
    assert M.action(["arm64"], ["x86_64"], False) == M.LIPO
    assert M.action(["x86_64", "arm64"], ["x86_64", "arm64"], False) == M.KEEP  # schon universal
    assert M.action(["arm64"], ["x86_64", "arm64"], False) == M.TAKE_INTEL
    assert M.action(["x86_64"], ["x86_64"], False) == M.ODD
    assert M.action(["arm64"], ["arm64"], True) == M.KEEP


def test_lizenzliste_zusammenfuehren():
    arm = {"ffmpeg": {"version": "9.0.1", "license": "GPL-3.0"}, "x265": {"version": "4.1"}}
    intel = {"ffmpeg": {"version": "9.0.1"}, "x265": {"version": "4.0"}, "openblas": {"version": "0.3"}}
    out = M.merge_manifests(arm, intel)
    assert out["ffmpeg"] == {"version": "9.0.1", "license": "GPL-3.0"}
    assert out["x265"]["version"] == "4.1 (Apple Silicon), 4.0 (Intel)" and out["openblas"] == {"version": "0.3"}


@pytest.mark.skipif(not (shutil.which("lipo") and shutil.which("codesign")), reason="lipo/codesign fehlen")
def test_universal_paket_zusammenfuegen(tmp_path):
    def app(root, exe_arch, extra):
        c = root / "Ottilie.app" / "Contents"
        for d in ("MacOS", "Frameworks", "Resources/bin/licenses"):
            (c / d).mkdir(parents=True)
        (c / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Ottilie",
                                                       "CFBundleIdentifier": "de.ottilie.app.test"}))
        subprocess.run(["lipo", "-thin", exe_arch, "/bin/ls", "-output", str(c / "MacOS" / "Ottilie")], check=True)
        subprocess.run(["lipo", "-thin", exe_arch, "/usr/bin/true", "-output", str(c / "Frameworks" / "libx.dylib")],
                       check=True)
        shutil.copyfile("/usr/bin/true", c / "Frameworks" / "fat.so")                 # schon universal
        os.chmod(c / "Frameworks" / "fat.so", 0o755)
        os.symlink("../Frameworks/fat.so", c / "Resources" / "fat.so")
        (c / "Resources" / "daten.txt").write_text(exe_arch)
        (c / "Resources" / "bin" / "licenses" / "manifest.json").write_text(json.dumps(extra["manifest"]))
        for name, data in extra.get("files", {}).items():
            (c / name).write_bytes(data)
        return root / "Ottilie.app"

    arm = app(tmp_path / "arm", "arm64e", {"manifest": {"x265": {"version": "4.1"}},
                                            "files": {"Resources/nur_silicon.txt": b"a"}})
    intel = app(tmp_path / "intel", "x86_64", {"manifest": {"x265": {"version": "4.0"}, "openblas": {"version": "0.3"}}})
    subprocess.run(["lipo", "-thin", "x86_64", "/usr/bin/true", "-output",
                    str(intel / "Contents" / "Frameworks" / "libopenblas.dylib")], check=True)
    out = tmp_path / "universal" / "Ottilie.app"
    count = M.merge(str(arm), str(intel), str(out), allow_unknown=True)
    c = out / "Contents"
    assert count["Fehler"] == 0 and count[M.LIPO] == 2 and count["nur Intel"] == 1
    assert set(S.file_archs(str(c / "MacOS" / "Ottilie"))) == {"arm64e", "x86_64"}
    assert set(S.file_archs(str(c / "Frameworks" / "libx.dylib"))) == {"arm64e", "x86_64"}
    assert S.file_archs(str(c / "Frameworks" / "libopenblas.dylib")) == ["x86_64"]       # nur Intel, dazu kopiert
    assert (c / "Resources" / "daten.txt").read_text() == "arm64e"                       # Silicon-Fassung
    assert (c / "Resources" / "fat.so").is_symlink() and (c / "Resources" / "nur_silicon.txt").exists()
    man = json.loads((c / "Resources" / "bin" / "licenses" / "manifest.json").read_text())
    assert man["x265"]["version"] == "4.1 (Apple Silicon), 4.0 (Intel)" and "openblas" in man
    assert subprocess.run(["codesign", "--verify", str(c / "Frameworks" / "libx.dylib")]).returncode == 0
    assert subprocess.run(["codesign", "--verify", str(out)]).returncode == 0
    assert (arm / "Contents" / "Resources" / "daten.txt").read_text() == "arm64e"         # Quelle unverändert
    with pytest.raises(SystemExit):
        M.merge(str(arm), str(intel), str(out), allow_unknown=True)                       # nie überschreiben
    with pytest.raises(SystemExit):
        M.merge(str(intel), str(arm), str(tmp_path / "falsch.app"), allow_unknown=True)   # vertauscht
    with pytest.raises(SystemExit):
        M.merge(str(arm), str(intel), str(tmp_path / "ohne.app"))                         # ohne Bauangabe nicht


def test_paketversionen_festschreiben():
    deps = {"a": ["B", "c"], "b": ["c"], "c": [], "d": ["a"]}
    assert L.closure(["A"], lambda n: deps.get(n, [])) == {"a", "b", "c"}
    assert L.canonical("PyObjC_Core.x") == "pyobjc-core-x"
    roots = L.roots_for({"numpy", "webview", "fremd"}, {"numpy": ["numpy"], "webview": ["pywebview"]})
    assert roots == {"numpy", "pyinstaller", "pytest"}                       # pywebview ist nicht im Paket
    assert L.lock_lines(["b", "a", "x"], {"a": "1", "b": "2"}.get) == ["a==1", "b==2"]
    locked = L.parse_lock("# Kopf\nNumPy==2.4.4  # Kommentar\nscipy==1.17.1\n")
    assert locked == {"numpy": "2.4.4", "scipy": "1.17.1"}
    assert L.differences(locked, {"numpy": "2.4.4", "scipy": "1.18.0"}.get) == ["scipy: festgeschrieben 1.17.1, installiert 1.18.0"]
    assert {"hid", "usb", "numpy"} <= L.spec_modules(ROOT / "tools" / "Ottilie.spec", {"hid", "usb", "numpy", "x"})


def test_festgeschriebene_versionen_aktuell():
    """Ändert sich eine Paketversion oder ein Import, `python3 tools/lock_requirements.py` neu laufen lassen."""
    from importlib import metadata
    dists = metadata.packages_distributions()
    mods = L.imported_modules(L.source_files()) | L.spec_modules(ROOT / "tools" / "Ottilie.spec", dists)
    want = L.lock_lines(L.closure(L.roots_for(mods, dists), L.installed_requires), L._version)
    have = [line for line in L.LOCK.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    assert have == want


def test_mindestversion_macos():
    assert S.min_macos("/bin/ls") is not None                       # Systemprogramme tragen eine Angabe
    assert S.min_macos(str(ROOT / "pytest.ini")) is None
    assert S.version_text((26, 0, 0)) == "26.0" and S.version_text((13, 5, 1)) == "13.5.1" and S.version_text(None) == ""
    assert len(S.os_version()) == 3 and S.os_version() >= (13, 0, 0)


def test_mindestversion_aus_paket(tmp_path):
    c = tmp_path / "Ottilie.app" / "Contents"
    (c / "MacOS").mkdir(parents=True)
    (c / "Info.plist").write_bytes(plistlib.dumps({"LSMinimumSystemVersion": "26.0"}))
    assert S.required_macos_of_package(str(c / "MacOS" / "Ottilie")) == (26, 0, 0)
    assert S.required_macos_of_package(str(tmp_path / "x" / "y")) is None


def test_bauangaben_vereinen():
    arm = {"version": "0.1.0", "commit": "abc", "lock": "11", "archs": ["arm64"], "built": {"arm64": "07.10.2026 09:00"}}
    intel = {**arm, "archs": ["x86_64"], "built": {"x86_64": "06.10.2026 18:00"}}
    out = M.merge_info(arm, intel)
    assert out["archs"] == ["arm64", "x86_64"] and set(out["built"]) == {"arm64", "x86_64"}
    with pytest.raises(SystemExit, match="Stand"):
        M.merge_info(arm, {**intel, "commit": "def"})
    with pytest.raises(SystemExit, match="Paketliste"):
        M.merge_info(arm, {**intel, "lock": "22"})
    assert S.build_line(out).startswith("Bau: Ottilie 0.1.0 · Stand abc · ") and S.build_line({}) == ""


def test_fehlendes_recht_bildschirmaufnahme_ist_ein_hinweis():
    """Review B24: ohne „Bildschirmaufnahme“ liefert macOS keine Fenstertitel – der Selbsttest sagt es, ohne den
    Bau-Mac (dort fehlt das Recht meist) scheitern zu lassen."""
    from tonmischung import selftest
    (c,) = selftest.permission_checks(lambda: False)
    assert c.ok and c.warn and "Bildschirm- und Systemaudioaufnahme" in c.detail
    text = selftest.summary("Kopf", [c])
    assert "! Bildschirmaufnahme" in text and text.endswith("Alles in Ordnung (1 Hinweis).")
    (c,) = selftest.permission_checks(lambda: True)
    assert c.ok and not c.warn and c.detail == "erteilt"
