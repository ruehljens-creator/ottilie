"""Stream-Deck-Launcher: je Befehl eine winzige App, die ihn über den Socket schickt – ohne Netzwerk.

Wie PunchBuddys `streamdeck/make_launchers.command`. Im Stream Deck mit der eingebauten Aktion
„System → Öffnen“ belegen. Empfohlener Weg für gesperrte Studiorechner: Das Node-Plugin lädt seine
Laufzeit aus dem Netz und startet dort nicht (Erfahrung der produktiven PunchBuddy-Linie §6, §9).
"""
from __future__ import annotations

import plistlib
import re
import shutil
from pathlib import Path

from tonmischung.core.naming import slug
from tonmischung.paths import BUNDLE_ID


def default_dir() -> Path:
    """Ordner der Startprogramme – beim Gebrauch bestimmt (Tests mit eigenem Home schreiben nicht ins echte)."""
    return Path.home() / "Applications" / "Ottilie Launcher"


BUNDLE_PREFIX = f"{BUNDLE_ID}.launcher."
# Frühere Kennungen der Starter (vor 13.10.2026: „de.<Haus>.ottilie.launcher.…“ bzw. „…tonmischung…“) – damit
# vorhandene Starter weiter als eigene erkannt und ersetzt werden
LEGACY_ID = re.compile(r"^de\.[a-z]+\.(ottilie|tonmischung)\.launcher\.")
BASE = [("Record A", "record_a"), ("Record B", "record_b"), ("Play Input", "play"), ("Play Custom", "play_custom"),
        ("Zum Programmbeginn", "goto_start"), ("Audio verschieben", "move_audio"),
        ("Holen (gewählter Beitrag)", "holen"), ("WAV-Export", "export_wav"),
        ("AAF-Export (eingebettet)", "export_aaf"), ("AAF-Export (Referenz)", "export_aaf_reference"),
        ("Vocaster Autogain Host", "vocaster_autogain_host"), ("Vocaster Autogain Gast", "vocaster_autogain_guest"),
        ("Vocaster 48 V an", "vocaster_phantom_on"), ("Vocaster 48 V aus", "vocaster_phantom_off")]
SAFE = re.compile(r"^[a-z_]+( [0-9]+)?$")
# Jens 09.10.2026: „Ottilie …“ statt „TM …“. Ältere „TM …“-Starter bleiben liegen und funktionieren weiter, bis die
# Stream-Deck-Tasten neu zugewiesen sind.
NAME_PREFIX, LEGACY_NAME_PREFIX = "Ottilie", "TM"


def entries(presets: list[dict]) -> list[tuple[str, str]]:
    """(Anzeigename, Befehl) – Grundbefehle und je Spur-Preset „preset <n>“."""
    out = list(BASE)
    out += [(f"Preset {i} – {p.get('name', '')}".rstrip(" –"), f"preset {i}") for i, p in enumerate(presets, start=1)]
    return out


def _ours(app: Path) -> bool:
    try:
        with open(app / "Contents" / "Info.plist", "rb") as f:
            bundle_id = str(plistlib.load(f).get("CFBundleIdentifier", ""))
        return bundle_id.startswith(BUNDLE_PREFIX) or bool(LEGACY_ID.match(bundle_id))
    except (OSError, plistlib.InvalidFileException):
        return False


def make_launcher(dest: Path, label: str, payload: str, socket_path: str) -> Path:
    if not SAFE.match(payload):
        raise ValueError(f"Befehl nicht erlaubt: {payload!r}")
    if "'" in socket_path:
        raise ValueError("Socket-Pfad darf kein Hochkomma enthalten")
    name = f"{NAME_PREFIX} {label}".replace("/", "-")
    app = dest / f"{name}.app"
    if app.exists():
        if not _ours(app):
            raise FileExistsError(f"{app} gehört nicht zu Ottilie – nicht ersetzt")
        shutil.rmtree(app)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleName": name, "CFBundleDisplayName": label,
                       "CFBundleIdentifier": BUNDLE_PREFIX + slug(payload),
                       "CFBundleExecutable": "launcher", "CFBundlePackageType": "APPL",
                       "CFBundleVersion": "1.0", "CFBundleShortVersionString": "1.0",
                       "LSBackgroundOnly": True, "LSUIElement": True}, f)
    script = app / "Contents" / "MacOS" / "launcher"
    script.write_text("#!/bin/sh\n"
                      f"printf '%s\\n' '{payload}' | /usr/bin/nc -U '{socket_path}' >/dev/null 2>&1 || \\\n"
                      "/usr/bin/osascript -e 'display notification \"Ottilie nicht erreichbar – läuft die App?\" "
                      "with title \"Ottilie\"' >/dev/null 2>&1\n")
    script.chmod(0o755)
    return app


def make_launchers(socket_path: str, presets: list[dict], dest: Path | None = None) -> list[Path]:
    dest = dest or default_dir()
    dest.mkdir(parents=True, exist_ok=True)
    return [make_launcher(dest, label, payload, socket_path) for label, payload in entries(presets)]


def legacy_launchers(dest: Path | None = None) -> list[Path]:
    """Eigene Starter mit dem alten Namen („TM …“) – bleiben liegen, werden nur gemeldet."""
    folder = dest or default_dir()
    return sorted(p for p in folder.glob(f"{LEGACY_NAME_PREFIX} *.app") if _ours(p)) if folder.is_dir() else []
