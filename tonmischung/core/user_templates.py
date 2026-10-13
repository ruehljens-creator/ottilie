"""Templates je Nutzer (Plan Nutzer & Räume, Schritt 3; Jens 04.10.2026: „Templates in die App
hochladen und löschen können, und das pro User“).

Ein Pro-Tools-Template ist eine `.ptxt`-Datei, meist in einem eigenen Ordner mit „Video Files“ (z. B. das Startband).
Hochgeladen wird in die Ablage des Nutzers (`<Ablage>/<id>/Templates/<Name>/`), damit es mit dem Nutzer wandert. Pro
Tools legt Sessions nur aus seiner Template-Bibliothek an (PTSL: Gruppe + Name); deshalb installiert die App die Kopie
dort unter der Gruppe „Ottilie <Nutzer>“ (vor dem 08.10.2026: „Tonmischung <Nutzer>“, gilt weiter) – vor dem Anlegen,
falls sie dort fehlt oder veraltet ist. Nichts wird überschrieben oder gelöscht: Ältere Kopien und gelöschte Templates
wandern ins Archiv.
"""
from __future__ import annotations

import filecmp
import os
import shutil
from datetime import datetime

PT_ROOT = "~/Documents/Pro Tools/Session Templates"     # ANNAHME: Standardort von Pro Tools
GROUP_PREFIX = "Ottilie "
LEGACY_GROUP_PREFIX = "Tonmischung "                    # vor dem 08.10.2026 hochgeladene Templates
EXT = ".ptxt"


class TemplateError(ValueError):
    """Template so nicht hochladbar/installierbar – Meldung für das Fenster."""


def group_for(user_name: str) -> str:
    return f"{GROUP_PREFIX}{user_name}".strip()


def is_user_group(group: str) -> bool:
    return group.startswith((GROUP_PREFIX, LEGACY_GROUP_PREFIX))


def source_of(path: str) -> tuple[str, str]:
    """(Quelle, Name): eigener Ordner `<Name>/<Name>.ptxt` (mit Video Files) oder einzelne Datei."""
    path = os.path.abspath(os.path.expanduser(path)).rstrip("/")
    if os.path.isdir(path):
        name = os.path.basename(path)
        if not os.path.isfile(os.path.join(path, name + EXT)):
            raise TemplateError(f"Im Ordner „{name}“ liegt kein „{name}{EXT}“")
        return path, name
    if not path.lower().endswith(EXT) or not os.path.isfile(path):
        raise TemplateError("Bitte ein Pro-Tools-Template (.ptxt) wählen")
    name = os.path.splitext(os.path.basename(path))[0]
    parent = os.path.dirname(path)
    return (parent, name) if os.path.basename(parent) == name else (path, name)


def stored(user_dir: str, name: str) -> str:
    return os.path.join(user_dir, "Templates", name)


def upload(user_dir: str, path: str) -> str:
    """In die Ablage des Nutzers kopieren; Rückgabe: Name. Ein gleichnamiges Template wird nicht überschrieben."""
    src, name = source_of(path)
    if "/" in name or name.startswith("."):
        raise TemplateError(f"„{name}“ ist als Template-Name nicht möglich")
    dest = stored(user_dir, name)
    if os.path.exists(dest):
        raise TemplateError(f"„{name}“ gibt es bei diesem Nutzer schon – erst löschen oder umbenennen")
    if os.path.isdir(src):
        shutil.copytree(src, dest)
    else:
        os.makedirs(dest)
        shutil.copy2(src, os.path.join(dest, name + EXT))
    return name


def _same(a: str, b: str) -> bool:
    try:
        return filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def install(user_dir: str, name: str, group: str, archive_dir: str, pt_root: str = PT_ROOT,
            now: datetime | None = None) -> str:
    """Kopie in der Pro-Tools-Bibliothek sicherstellen; eine veraltete eigene Kopie wandert ins Archiv."""
    src = stored(user_dir, name)
    if not os.path.isfile(os.path.join(src, name + EXT)):
        raise TemplateError(f"Template „{name}“ fehlt in der Ablage des Nutzers")
    dst = os.path.join(os.path.expanduser(pt_root), group, name)
    if os.path.isdir(dst):
        if _same(os.path.join(src, name + EXT), os.path.join(dst, name + EXT)):
            return dst
        _move_aside(dst, os.path.join(archive_dir, "Templates", f"installiert-{group}-{name}"), now)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copytree(src, dst)
    return dst


def archive(user_dir: str, name: str, group: str, archive_dir: str, user_id: str, pt_root: str = PT_ROOT,
            now: datetime | None = None) -> str:
    """Löschen = ins Archiv (Jens 04.10.2026): Kopie des Nutzers und die installierte Kopie verschieben."""
    src = stored(user_dir, name)
    if not os.path.isdir(src):
        raise TemplateError(f"Template „{name}“ nicht gefunden")
    dest = _move_aside(src, os.path.join(archive_dir, "Templates", f"{user_id}-{name}"), now)
    installed = os.path.join(os.path.expanduser(pt_root), group, name)
    if os.path.isdir(installed):
        _move_aside(installed, os.path.join(archive_dir, "Templates", f"installiert-{group}-{name}"), now)
    return dest


def _move_aside(path: str, base: str, now: datetime | None) -> str:
    dest = f"{base}-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}"
    n = 2
    while os.path.exists(dest):
        dest = f"{dest.rsplit('~', 1)[0]}~{n}"
        n += 1
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.rename(path, dest) if _same_fs(path, os.path.dirname(dest)) else shutil.move(path, dest)
    return dest


def _same_fs(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False
