"""macOS-Dateidialoge über AppleScript – ein Ort statt dreier (Review C).

Abbrechen oder Fehler ergeben "". Der Dialog wartet auf den Mischer, höchstens 10 Minuten.
"""
from __future__ import annotations

import subprocess

WAIT_S = 600.0


def _text(value: str) -> str:
    """Text für ein AppleScript-Literal (Anführungszeichen und Backslash dürfen den Befehl nicht beenden)."""
    return value.replace("\\", "/").replace('"', "'")


def _ask(script: str) -> str:
    try:
        r = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=WAIT_S)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def choose_file(prompt: str, types: tuple[str, ...] = ()) -> str:
    """„Öffnen“: eine Datei wählen; `types` sind Dateitypen (UTI), z. B. „public.json“."""
    listed = ", ".join('"' + _text(t) + '"' for t in types)
    of_type = " of type {" + listed + "}" if types else ""
    return _ask(f'POSIX path of (choose file{of_type} with prompt "{_text(prompt)}")')


def choose_save(prompt: str, default_name: str) -> str:
    """„Sichern“: Name und Ort für eine neue Datei."""
    return _ask(f'POSIX path of (choose file name with prompt "{_text(prompt)}" default name "{_text(default_name)}")')


def choose_folder(prompt: str) -> str:
    return _ask(f'POSIX path of (choose folder with prompt "{_text(prompt)}")')
