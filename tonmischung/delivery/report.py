"""Prüfsumme und Auslieferungsbericht (Plan M5, Schritt 3).

Herkunft: punchbuddy-helmut `punchbuddy/report.py` (Journal) und die SHA-256-Datei des Transfers. Angepasst: Bericht
als Text für Menschen und als JSON für Helmut/Programme, neben dem Sendefile; nie überschreiben (vorhandene Namen
bekommen eine Nummer). Der Bericht wird auch geschrieben, wenn ein Schritt scheitert – gerade dann hilft er.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tonmischung.core.fileio import sha256_file, write_new, write_text


sha256 = sha256_file                     # Prüfsumme des Sendefiles (mit Fortschritt)


def free_path(path: str) -> str:
    """Nie überschreiben: „Name.ext“, sonst „Name 2.ext“, „Name 3.ext“ …"""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem} {n}{ext}"):
        n += 1
    return f"{stem} {n}{ext}"


def _write_new(path: str, text: str) -> str:
    """Schreibt eine neue Datei (Hilfsname, dann Hardlink – nie über eine vorhandene)."""
    path = free_path(path)
    write_new(path, lambda tmp: write_text(tmp, text))
    return path


def write_sha256(path: str, digest: str) -> str:
    """`<Sendefile>.sha256` im üblichen Format („<Prüfsumme>  <Dateiname>“), wird beim Transfer mitgesendet."""
    return _write_new(path + ".sha256", f"{digest}  {os.path.basename(path)}\n")


@dataclass
class Journal:
    """Ablauf einer Auslieferung mit Zeitstempeln – für den Bericht."""
    title: str
    started: datetime = field(default_factory=datetime.now)
    meta: dict[str, Any] = field(default_factory=dict)
    steps: list[tuple[datetime, str, str]] = field(default_factory=list)       # (Zeit, Schritt, Ergebnis)

    def step(self, name: str, result: str = "", now: Callable[[], datetime] = datetime.now) -> None:
        self.steps.append((now(), name, result))


def render(j: Journal, findings: Sequence[Mapping], overall: str, ended: datetime | None = None) -> str:
    ended = ended or datetime.now()
    w = 72
    lines = ["=" * w, f"Ottilie – Auslieferungsbericht: {j.title}", "=" * w,
             f"{'Beginn:':<16}{j.started:%d.%m.%Y %H:%M:%S}", f"{'Ende:':<16}{ended:%d.%m.%Y %H:%M:%S}"]
    lines += [f"{(k + ':'):<16}{v}" for k, v in j.meta.items()]
    lines += ["", "-" * w, "ABLAUF", "-" * w]
    lines += [f"{t:%H:%M:%S}  {name}{('  – ' + res) if res else ''}" for t, name, res in j.steps]
    if findings:
        counts: dict[str, int] = {}
        for f in findings:
            counts[f["status"]] = counts.get(f["status"], 0) + 1
        lines += ["", "-" * w,
                  f"PRÜFUNG   Gesamt: {overall}   " + "  ".join(f"{k}: {v}" for k, v in sorted(counts.items())),
                  "-" * w]
        for f in findings:
            line = f"[{f['status']:<4}] {f['check']}"
            if f.get("expected") or f.get("actual"):
                line += f"\n        soll: {f.get('expected') or '–'}\n        ist:  {f.get('actual') or '–'}"
            if f.get("message"):
                line += f"\n        Hinweis: {f['message']}"
            lines.append(line)
    lines += ["", "-" * w, "Die Prüfung ist eine hauseigene Formatprüfung, keine zertifizierte (BATON).", "=" * w]
    return "\n".join(lines) + "\n"


def write_report(sendefile: str, j: Journal, findings: Sequence[Mapping], overall: str,
                 ended: datetime | None = None) -> tuple[str, str]:
    """`<Sendefile ohne Endung>.bericht.txt` und `.bericht.json` neben dem Sendefile. Rückgabe: beide Pfade."""
    stem = os.path.splitext(sendefile)[0]
    ended = ended or datetime.now()
    txt = _write_new(stem + ".bericht.txt", render(j, findings, overall, ended))
    data = {"titel": j.title, "beginn": j.started.isoformat(timespec="seconds"),
            "ende": ended.isoformat(timespec="seconds"), "angaben": j.meta,
            "ablauf": [{"zeit": t.isoformat(timespec="seconds"), "schritt": n, "ergebnis": r} for t, n, r in j.steps],
            "pruefung": {"gesamt": overall, "befunde": list(findings)}}
    js = _write_new(stem + ".bericht.json", json.dumps(data, ensure_ascii=False, indent=1))
    return txt, js
