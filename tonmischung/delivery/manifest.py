"""Metadaten-Datei zum Zurückliefern (JSON neben den WAVs).

Jens 10.11.2026: beim Export der WAVs eine JSON-Datei mit den Metadaten des Beitrags, den Dateinamen der WAVs in der
richtigen Reihenfolge und den Pfaden zu Video und Audio. Metadaten fürs MXF: nur, was bmx in ein ARD-ZDF-konformes
Sendefile schreiben darf (Jens 11.11.2026) – das HDF-Profil verbietet Descriptive Metadata („shall not be present“),
AS-11/DPP und eine eigene Tonspur-Kennzeichnung (MCA) fallen damit weg. Bleiben:

- `bmx.clip` – Name des Material Package (`--clip`): „<VideoID> <Titel>“ (Jens 11.11.2026: VideoID in den Clipnamen),
- `bmx.create_date` – Erstellungsdatum im Identification Set (`--create-date`, bmx-Form UTC `YYYY-MM-DDThh:mm:ss:qm`).

Den Start-Timecode (`-y`) bestimmt der Sendefile-Bau selbst (Bild mit Startband); er steht als Programmbeginn unter
`mischung`.

Reihenfolge der Audiodateien = Zeilen der Spurtabelle (Zeile = Kanalpaar 1/2, 3/4 … im MXF, wie beim Sendefile),
16 Kanäle; leere oder nicht gelieferte Zeilen bleiben Stille, nichts rückt auf. Exportspuren ohne Kanal (nicht in der
Tabelle oder jenseits von Kanal 16) folgen danach.

In der Betriebsart Übergabeordner kommt `herkunft.schnittraum` aus der Übergabe-JSON dazu (Jens 11.11.2026).

Reine Logik ohne Seiteneffekte; geschrieben wird in `workflow/deliver.py`.
"""
from __future__ import annotations

import os
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from tonmischung.helmut.mapping import Asset, Contribution

FORMAT = "ottilie-auslieferung/1"
VIDEO_ID_MAX = 64
SILENCE = "Stille"
CHANNELS = 16                    # Haus-Standard: 16 Tonspuren im Sendefile


class VideoIdError(ValueError):
    """VideoID nicht zulässig – die Meldung sagt, warum."""


def clean_video_id(text: Any) -> str:
    """VideoID aus der Eingabe: Ränder weg; leer ist erlaubt (ob Pflicht, entscheidet die Einstellung).
    Keine Steuerzeichen (Zeilenumbruch, Tab aus der Zwischenablage), höchstens 64 Zeichen."""
    value = unicodedata.normalize("NFC", str(text or "")).strip()
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise VideoIdError("Die VideoID enthält einen Zeilenumbruch, Tab oder ein Steuerzeichen")
    if len(value) > VIDEO_ID_MAX:
        raise VideoIdError(f"Die VideoID ist länger als {VIDEO_ID_MAX} Zeichen")
    return value


def project_id(contribution: Contribution) -> str:
    """ID für Metadaten-Datei und Dateinamen: in der Betriebsart Übergabeordner die Beitrags-ID aus der Übergabe
    (geprüft auf Dateinamen-taugliche Zeichen), sonst die Projekt-ID."""
    raw = contribution.raw if isinstance(contribution.raw, dict) else {}
    return str(raw.get("beitrag_id") or contribution.id)


def file_name(project_id: str) -> str:
    """Name der Metadaten-Datei im Übergabeordner."""
    return f"{project_id}_metadaten.json"


def bmx_timestamp(moment: datetime) -> str:
    """Zeitpunkt in der Form, die bmx für `--create-date` verlangt: UTC, `YYYY-MM-DDThh:mm:ss:qm` (qm = 1/250 s)."""
    utc = moment.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S:") + str(utc.microsecond * 250 // 1_000_000)


def channel_layout(table: Sequence[Mapping], wavs: Mapping[str, str]) -> list[dict]:
    """Acht Kanalpaare aus der Spurtabelle (Zeile = Paar): Spur, Inhalt, WAV – leere, nicht gelieferte und fehlende
    Zeilen sind Stille (Haus-Standard: unbelegte Kanäle mit Stille füllen). Zeilen jenseits von Kanal 16 fallen weg."""
    rows = list(table)[:CHANNELS // 2]
    rows += [{}] * (CHANNELS // 2 - len(rows))
    out = []
    for i, row in enumerate(rows):
        track = str(row.get("pt_spur") or "").strip()
        wav = wavs.get(track) if track and row.get("export") else None
        out.append({"kanaele": [2 * i + 1, 2 * i + 2], "inhalt": str(row.get("inhalt") or ""),
                    "spur": track if wav else SILENCE, "datei": os.path.basename(wav) if wav else "",
                    "pfad": wav or ""})
    return out


def audio_files(layout: Sequence[Mapping], wavs: Mapping[str, str]) -> list[dict]:
    """Die gelieferten WAVs in Kanalreihenfolge (erste Belegung zählt), danach Spuren ohne Kanal in Lieferfolge."""
    by_track: dict[str, dict] = {}
    for pair in layout:
        if pair["pfad"]:
            a = by_track.setdefault(pair["spur"], {"spur": pair["spur"], "inhalt": pair["inhalt"], "kanaele": [],
                                                   "datei": pair["datei"], "pfad": pair["pfad"]})
            a["kanaele"] += pair["kanaele"]
    for track, wav in wavs.items():
        by_track.setdefault(track, {"spur": track, "inhalt": "", "kanaele": [], "datei": os.path.basename(wav),
                                    "pfad": wav})
    return [{"position": i, **a} for i, a in enumerate(by_track.values(), 1)]


def clip_name(video_id: str, title: str) -> str:
    """Name des Material Package im Sendefile: „<VideoID> <Titel>“, ohne VideoID nur der Titel."""
    return " ".join(x for x in (video_id.strip(), " ".join(title.split())) if x)


def build(contribution: Contribution, *, table: Sequence[Mapping], wavs: Mapping[str, str], video: Asset | None,
          video_id: str, created: datetime, program_start: str, program_end: str, mixer: str, room: str,
          target_lufs: float, max_truepeak: float, loudness_tracks: Sequence[str]) -> dict:
    """Inhalt der Metadaten-Datei. `wavs`: Spur → gelieferte WAV (Pfad im Übergabeordner), in Lieferfolge."""
    layout = channel_layout(table, wavs)
    data = {
        "format": FORMAT,
        "erstellt": created.isoformat(timespec="seconds"),
        "projekt": {"id": project_id(contribution), "titel": contribution.title, "sendung": contribution.show},
        "videoid": video_id,
        "video": {"name": video.name, "pfad": video.path} if video else None,
        "audio": audio_files(layout, wavs),
        "kanalbelegung": layout,
        "mischung": {"mischer": mixer, "raum": room, "session": contribution.mix.session_path,
                     "programmbeginn": program_start, "programmende": program_end,
                     "lautheit": {"ziel_lufs": target_lufs, "true_peak_max_dbtp": max_truepeak,
                                  "spuren": list(loudness_tracks)}},
        "bmx": {"profil": "ARD-ZDF HDF (ohne Descriptive Metadata)",
                "clip": clip_name(video_id, contribution.title), "create_date": bmx_timestamp(created)},
    }
    room = contribution.raw.get("schnittraum") if isinstance(contribution.raw, dict) else ""
    if room:                                  # Betriebsart Übergabeordner: Schnittraum aus der Übergabe-JSON
        data["herkunft"] = {"schnittraum": str(room)}
    return data
