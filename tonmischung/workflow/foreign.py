"""Fremde Audiodateien ins Projekt übernehmen (Plan M7 fremde Dateien, Schritt 1).

Kopiert jede Audiodatei der offenen Session, die außerhalb des Session-Ordners liegt, nach
`<Session-Ordner>/Audio Files/Extern/` und – wenn eingestellt – zusätzlich in den Übergabeordner des
Beitrags (`…/<Projekt>/Extern/`) samt Asset in Helmut, wie beim Zurückliefern (ANNAHME F4).
Nie überschreiben, nie verschieben: das Original bleibt, wo es ist. PTSL kann Clips nicht umverknüpfen –
an einem anderen Platz findet Pro Tools die Kopie über „Automatisch suchen und neu verknüpfen“.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field

from tonmischung.core.fileio import write_new
from tonmischung.core.foreign import DEFAULT_SUBFOLDER, MISSING, NEW, ForeignFile, plan, same_content
from tonmischung.ptsl.client import PtslClient

PREF_HINT = ("Pro Tools hat diese Dateien nicht selbst kopiert – die Voreinstellung „Beim Import automatisch "
             "kopieren“ (Voreinstellungen › Verarbeitung › Import) ist offenbar aus.")


def copy_new(src: str, dst: str) -> None:
    """Kopie unter Hilfsnamen, dann ohne Überschreiben an den Zielnamen (link schlägt fehl, wenn es ihn gibt)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    write_new(dst, lambda tmp: shutil.copy2(src, tmp))


def free_name(path: str, exists: Callable[[str], bool] = os.path.exists,
              same: Callable[[str], bool] = lambda p: False) -> tuple[str, bool]:
    """(Zielpfad, schon_vorhanden): gleicher Inhalt → vorhanden; sonst nächster freier Name _2, _3 …"""
    stem, ext = os.path.splitext(path)
    n, candidate = 1, path
    while exists(candidate):
        if same(candidate):
            return candidate, True
        n += 1
        candidate = f"{stem}_{n}{ext}"
    return candidate, False


@dataclass
class AdoptResult:
    files: list[ForeignFile] = field(default_factory=list)
    copied: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        return [f.source for f in self.files if f.status == MISSING]

    @property
    def text(self) -> str:
        def n(k: int) -> str:
            return f"{k} Datei" if k == 1 else f"{k} Dateien"
        parts = []
        if self.copied:
            parts.append(f"{n(len(self.copied))} ins Projekt übernommen")
        if self.failed:
            parts.append(f"{n(len(self.failed))} nicht übernommen")
        if self.missing:
            parts.append(f"{n(len(self.missing))} {'fehlt' if len(self.missing) == 1 else 'fehlen'} (offline)")
        return ", ".join(parts) or "keine Dateien außerhalb des Projekts"


def adopt(ptsl: PtslClient, session_path: str, *, subfolder: str = DEFAULT_SUBFOLDER,
          upload_dir: str | None = None, register: Callable[[str], str] | None = None,
          copy: Callable[[str, str], None] = copy_new, log: Callable[[str], None] = lambda m: None,
          files: list[ForeignFile] | None = None) -> AdoptResult:
    """Fremde Dateien der offenen Session übernehmen. `register(pfad) -> Hinweis` meldet die Kopie in Helmut.
    `files`: schon geplante Liste (spart eine zweite Abfrage)."""
    result = AdoptResult(files=files if files is not None else plan(ptsl.file_locations(), session_path, subfolder))
    for f in result.files:
        if f.status != NEW:
            continue
        try:
            copy(f.source, f.target)
            result.copied.append(f.target)
            log(f"Fremde Datei übernommen: {f.source} → {f.target}")
            if upload_dir:
                dst, there = free_name(os.path.join(upload_dir, os.path.basename(f.source)),
                                       same=lambda p: same_content(f.source, p))
                if not there:
                    copy(f.source, dst)
                    if register is not None:
                        hint = register(dst)
                        if hint and hint not in result.hints:
                            result.hints.append(hint)
        except OSError as e:
            result.failed.append(f"{os.path.basename(f.source)}: {e}")
    if any(f.status == NEW for f in result.files):
        result.hints.append(PREF_HINT)
    return result
