"""Session-Ordner in den Projektordner spiegeln (Plan M7-Ablage, „lokal mit Spiegelung“).

Nie löschen, nie überschreiben: Neue Dateien werden kopiert; was sich geändert hat, wird erst kopiert,
nachdem die bisherige Fassung im Spiegel nach `.Spiegel-Vorversionen/<Zeit>/` verschoben wurde. Was im
lokalen Ordner fehlt, bleibt im Spiegel liegen. Kein `rsync`: das von macOS (openrsync 2.6.9) nimmt
`--backup-dir` an, legt aber keine Vorversionen an (live 03.10.2026).
"""
from __future__ import annotations

import filecmp
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from tonmischung.core.backups import BACKUP_DIR
from tonmischung.workflow.foreign import copy_new

VERSIONS = ".Spiegel-Vorversionen"


@dataclass
class MirrorResult:
    target: str
    copied: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    unchanged: int = 0
    failed: list[str] = field(default_factory=list)
    at: str = ""

    @property
    def text(self) -> str:
        parts = [f"gespiegelt {self.at}"]
        if self.copied or self.replaced:
            parts.append(f"{len(self.copied)} neu, {len(self.replaced)} geändert")
        if self.failed:
            parts.append(f"{len(self.failed)} Fehler")
        return ", ".join(parts)


def _same(src: str, dst: str, by_content: bool) -> bool:
    a, b = os.stat(src), os.stat(dst)
    if a.st_size != b.st_size:
        return False
    if by_content:
        return filecmp.cmp(src, dst, shallow=False)
    return int(a.st_mtime) == int(b.st_mtime)


def mirror(src_dir: str, dst_dir: str, *, by_content: bool = False, with_backups: bool = True,
           now: Callable[[], datetime] = datetime.now, copy: Callable[[str, str], None] = copy_new) -> MirrorResult:
    """Einmal spiegeln. `by_content`: Audiodateien „alle“ – Inhalt vergleichen statt Größe/Zeit."""
    stamp = now()
    result = MirrorResult(dst_dir, at=stamp.strftime("%H:%M"))
    versions = os.path.join(dst_dir, VERSIONS, stamp.strftime("%Y%m%d-%H%M%S"))
    for root, dirs, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and (with_backups or d != BACKUP_DIR or rel != "."))
        for name in sorted(files):
            if name.startswith(".") or name.endswith(".part"):
                continue
            src = os.path.join(root, name)
            dst = os.path.normpath(os.path.join(dst_dir, rel, name))
            try:
                if os.path.exists(dst):
                    if _same(src, dst, by_content):
                        result.unchanged += 1
                        continue
                    old = os.path.normpath(os.path.join(versions, rel, name))
                    os.makedirs(os.path.dirname(old), exist_ok=True)
                    os.rename(dst, old)                     # bisherige Fassung aufheben, nicht überschreiben
                    copy(src, dst)
                    result.replaced.append(dst)
                else:
                    copy(src, dst)
                    result.copied.append(dst)
            except OSError as e:
                result.failed.append(f"{os.path.join(rel, name)}: {e}")
    return result
