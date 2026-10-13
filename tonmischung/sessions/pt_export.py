"""Pro-Tools-Teil des Exports: speichern, kürzen, konsolidieren, R128, WAV je Spur.

Ablauf wie PunchBuddy `run_wav_export_standalone` (Vorgabe Jens): Transport stoppen,
Programm = 10:00:00:00 bis Videoende, vorhandene Audiospuren kürzen, alle Exportspuren
einblenden (DoE, AD) und – auch leere – über die Videolänge konsolidieren (leere werden
digitale Null), R128, WAVs kopieren. Kein Bounce. Wiederverwendet werden
`_detect_video_track`, `_trim_overhangs`,
`normalize_track` (24 bit, True-Peak-Limiter) und `_do_wav_export`.
Das Ende des Konsolidierens wird an den neuen Dateien erkannt (nicht am
Fortschrittsfenster per AppleScript); ohne Nachweis neuer Dateien Abbruch.

Absicherungen aus PunchBuddy 2.0.3 (Vorgabe Jens): Videoende mit bis zu drei Versuchen,
ohne Videoende kein Export; Konsolidieren nur mit Nachweis neuer Dateien – entsteht keine,
wird die Auswahl erneuert und einmal wiederholt, dann abgebrochen. Transport-Sperre
(`transport_guard`): vor dem Export, vor dem Konsolidieren und vor jeder Lautheitskorrektur
muss Pro Tools nachweislich stehen.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable

from tonmischung.core.progress import NullProgress, Progress
from tonmischung.core.timecode import PROGRAM_START as PROGRAM_TC, tc_seconds
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import PtslError
from tonmischung.sessions.transport_guard import ensure_stopped
from tonmischung.vendor import import_isolated

PROGRAM_START = PROGRAM_TC + ".00"           # mit Subframes, wie PTSL sie für Auswahlen will
VIDEO_END_PAUSES_S = (0.3, 0.8, 1.5)       # PunchBuddy 2.0.3: drei Versuche, wachsende Pause
# ANNAHME: Pro Tools legt die Dateien zu Beginn des Konsolidierens an. Ist nach 60 s für keine
# Spur eine neue Datei da, hat Pro Tools den Befehl nicht ausgeführt → einmal wiederholen.
FIRST_FILE_S = 60.0


class ExportError(PtslError):
    """Der Pro-Tools-Teil ist gescheitert; die Meldung sagt, wo."""


class NothingConsolidated(ExportError):
    """Nach dem Konsolidieren ist für keine Exportspur eine neue Datei entstanden."""


@dataclass
class PtExportResult:
    wav_files: dict[str, str]                  # Spur → exportierte WAV
    in_time: str
    out_time: str
    loudness: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    report: str = ""                           # Lautheitsbericht im Exportordner


# Jens 04.10.2026: „Die Files sollten im Ordner vom Pro-Tools-Projekt in einem Ordner namens export landen und einen
# Bericht zur Lautheitskorrektur enthalten – so wie in PunchBuddy. Das sollte immer so sein, auch beim Helmut-Weg.“
EXPORT_FOLDER = "export"                       # wie PunchBuddy (`<session>/export`)
REPORT_NAME = "Loudness Correction Metadata.txt"
PREVIOUS = "Vorversionen"


def export_folder(session_dir: str) -> str:
    return os.path.join(session_dir, EXPORT_FOLDER)


def set_aside_previous(export_dir: str, names: list[str], stamp: str) -> list[str]:
    """Vorige Fassung (gleiche Dateinamen) nach `export/Vorversionen/<stamp>/` verschieben – nie überschreiben,
    nie löschen. Rückgabe: verschobene Namen."""
    moved: list[str] = []
    target = os.path.join(export_dir, PREVIOUS, stamp)
    for name in dict.fromkeys(names):
        src = os.path.join(export_dir, name)
        if not os.path.isfile(src):
            continue
        os.makedirs(target, exist_ok=True)
        dst = os.path.join(target, name)
        n = 2
        while os.path.exists(dst):
            base, ext = os.path.splitext(name)
            dst = os.path.join(target, f"{base}_{n}{ext}")
            n += 1
        os.rename(src, dst)
        moved.append(name)
    return moved


def loudness_report(session: str, target_lufs: float, max_truepeak: float, exported: list[str],
                    blocks: dict[str, str], when: str) -> str:
    """Ein Bericht für alle Spuren (PunchBuddy schreibt je Spur dieselbe Datei neu, die letzte gewinnt)."""
    line = "=" * 60
    out = [line, "  LOUDNESS CORRECTION METADATA – Ottilie", "  EBU R128 / ITU-R BS.1770", line, "",
           f"  Session:            {session}", f"  Datum:              {when}",
           f"  Ziel:               {target_lufs:.1f} LUFS, höchstens {max_truepeak:.1f} dBTP",
           f"  Exportiert:         {', '.join(exported)}",
           f"  Korrigiert:         {', '.join(blocks) or 'keine Spur'}",
           f"  Ohne Korrektur:     {', '.join(t for t in exported if t not in blocks) or '–'}", ""]
    for track, text in blocks.items():
        out += ["-" * 60, f"  SPUR {track}", "-" * 60, text.rstrip(), ""]
    return "\n".join(out) + "\n"


def matches_track(file_base: str, track: str) -> bool:
    """Dateiname (ohne Endung) gehört zur Spur – Regel wie in PunchBuddy."""
    return (file_base == track or file_base.startswith(track + "_")
            or file_base.startswith(track + ".") or file_base.startswith(track + "-"))


def track_of(file_base: str, tracks: list[str]) -> str | None:
    """Spur, zu der die Datei gehört. Passen mehrere („ST“ und „ST_Mono“ für „ST_Mono_01“), gilt die längste – vorher
    bekam „ST“ womöglich die Datei von „ST_Mono“, und die Lautheitskorrektur traf die falsche WAV (Review A11)."""
    hits = [t for t in tracks if matches_track(file_base, t)]
    return max(hits, key=len) if hits else None


def ambiguous_tracks(tracks: list[str]) -> list[tuple[str, str]]:
    """Spurpaare, deren Dateien sich nicht sicher trennen lassen: der längere Name sieht aus wie eine Datei des
    kürzeren („ST“ und „ST_01“ – „ST_01.wav“ kann zu beiden gehören)."""
    return [(a, b) for a in tracks for b in tracks
            if a != b and matches_track(b, a) and b[len(a) + 1:len(a) + 2].isdigit()]


def newest_files(audio_dir: str, tracks: list[str], since: float) -> dict[str, str]:
    """Je Spur die neueste WAV, die nach `since` geschrieben wurde."""
    found: dict[str, tuple[float, str]] = {}
    for f in os.listdir(audio_dir):
        if not f.lower().endswith(".wav"):
            continue
        full = os.path.join(audio_dir, f)
        mtime = os.path.getmtime(full)
        if mtime < since:
            continue
        t = track_of(os.path.splitext(f)[0], tracks)
        if t is not None and mtime > found.get(t, (-1.0, ""))[0]:
            found[t] = (mtime, full)
    return {t: p for t, (_, p) in found.items()}


def wait_for_consolidated(audio_dir: str, tracks: list[str], since: float, timeout_s: float,
                          stable_s: float = 1.0, poll_s: float = 0.5,
                          sleep: Callable[[float], None] = time.sleep,
                          clock: Callable[[], float] = time.monotonic,
                          expected_bytes: int = 0,
                          on_progress: Callable[[float], None] | None = None,
                          first_file_s: float | None = None) -> dict[str, str]:
    """Wartet, bis für jede Spur eine neue, nicht mehr wachsende WAV da ist.

    `expected_bytes` (Summe der erwarteten Dateigrößen) ergibt den Fortschritt.
    `first_file_s`: ist bis dahin für keine Spur eine neue Datei da → NothingConsolidated
    (Pro Tools hat den Befehl nicht ausgeführt; der Aufrufer kann wiederholen).
    """
    begin = clock()
    end = begin + timeout_s
    sizes: dict[str, tuple[int, float]] = {}
    while True:
        files = newest_files(audio_dir, tracks, since)
        now = clock()
        if on_progress and expected_bytes:
            on_progress(min(1.0, sum(os.path.getsize(p) for p in files.values()) / expected_bytes))
        done = {}
        for t, path in files.items():
            size = os.path.getsize(path)
            last, since_t = sizes.get(t, (-1, now))
            if size != last:
                sizes[t] = (size, now)
            elif size > 0 and now - since_t >= stable_s:
                done[t] = path
        if len(done) == len(tracks):
            return done
        if not files and first_file_s is not None and now - begin >= first_file_s:
            raise NothingConsolidated(f"Konsolidieren hat nach {first_file_s:.0f} s keine neue Datei "
                                      "erzeugt – Pro Tools hat den Befehl nicht ausgeführt "
                                      "(Auswahl leer oder Hinweisfenster offen?)")
        if now >= end:
            missing = [t for t in tracks if t not in done]
            raise ExportError("Konsolidieren nicht nachweisbar – keine neue Datei für: "
                              + ", ".join(missing))
        sleep(poll_s)


def consolidate_timeout(seconds_of_audio: float) -> float:
    """Wartezeit wie in PunchBuddy: 60 s + 0,5 s je Sekunde Material, höchstens 600 s."""
    return min(600.0, 60.0 + 0.5 * max(0.0, seconds_of_audio))


def video_extent(ptsl: PtslClient, video: str, pauses_s: tuple[float, ...] = VIDEO_END_PAUSES_S,
                 log: Callable[[str], None] = lambda m: None) -> tuple[str, str]:
    """(Anfang, Ende) der Clips auf der Videospur – mit Wiederholung wie PunchBuddy 2.0.3.

    Unter Last setzt Pro Tools die Clip-Auswahl nicht immer rechtzeitig; eine leere Auswahl
    oder ein Fehler gilt als „noch nicht“. Ohne Ergebnis nach allen Versuchen: ExportError.
    """
    reason = ""
    for i, pause in enumerate(pauses_s):
        try:
            start, end = ptsl.clips_extent(video, settle_s=pause)
            if end and not end.startswith("00:00:00:00") and end != start:
                if i:
                    log(f"Videoende im {i + 1}. Versuch: {start} – {end}")
                return start, end
            reason = f"leere Auswahl ({start} – {end})"
        except PtslError as e:
            reason = str(e)
        log(f"Videoende, Versuch {i + 1}/{len(pauses_s)} ohne Ergebnis: {reason}")
    raise ExportError(f"Videoende auf Spur „{video}“ nach {len(pauses_s)} Versuchen nicht ermittelt "
                      f"({reason}) – ohne Videoende wird nicht exportiert")


def with_subframes(tc: str) -> str:
    """„10:00:00:00“ → „10:00:00:00.00“ (PTSL-Zeitangaben mit Unterframes)."""
    tc = (tc or PROGRAM_START).strip()
    return tc if "." in tc else tc + ".00"


def program_range(ptsl: PtslClient, export_mod, in_time: str = PROGRAM_START,
                  log: Callable[[str], None] = lambda m: None) -> tuple[str, str, list[str]]:
    """Wie PunchBuddy: 10:00:00:00 bis zum Ende der Clips auf der Videospur.

    Das Audio wird damit exakt auf die Videolänge abgelängt (Vorgabe Jens).
    Sperre: Liegt das Videoende nicht hinter dem Programmbeginn, wird abgebrochen
    statt gekürzt – das Kürzen löscht sonst das ganze Programm (live 02.10.2026).
    """
    video = ptsl.call("Videospur", lambda e: export_mod._detect_video_track(e, {}))
    v_start, out_time = video_extent(ptsl, video, log=log)
    warnings = []
    if not out_time or tc_seconds(out_time) <= tc_seconds(in_time):
        raise ExportError(f"Videoende {out_time or 'unbekannt'} (Spur „{video}“) liegt nicht hinter dem "
                          f"Programmbeginn {in_time} – Referenzvideo an den Programmbeginn legen")
    if abs(tc_seconds(v_start) - tc_seconds(in_time)) > 1.0:
        warnings.append(f"Video beginnt bei {v_start}, Programmbeginn ist {in_time}")
    return in_time, out_time, warnings


def apply_export_names(wavs: dict[str, str], names: dict[str, str],
                       log: Callable[[str], None] = lambda m: None) -> dict[str, str]:
    """Exportierte WAVs nach dem Exportnamen der Spur benennen (Spurprofil, Jens 03.10.2026: ST, IT, DoE …).
    Umbenannt wird nur die eigene Kopie im Exportordner; ein vorhandener Name wird nie überschrieben."""
    wanted = {t: str(names.get(t) or "").strip() for t in wavs}
    taken: dict[str, str] = {}
    for t, n in wanted.items():
        if n and n in taken:
            raise ExportError(f"Exportname „{n}“ doppelt: „{taken[n]}“ und „{t}“")
        if n:
            taken[n] = t
    out = {}
    for t, path in wavs.items():
        n = wanted[t]
        if not n or n == t:
            out[t] = path
            continue
        if "/" in n or n.startswith("."):
            raise ExportError(f"Exportname „{n}“ ist als Dateiname nicht möglich")
        dst = os.path.join(os.path.dirname(path), f"{n}.wav")
        if os.path.exists(dst):
            raise ExportError(f"{n}.wav gibt es im Exportordner schon – nichts überschrieben")
        os.rename(path, dst)
        log(f"{t}.wav → {n}.wav (Exportname)")
        out[t] = dst
    return out


def run_pt_export(ptsl: PtslClient, export_tracks: list[str], export_dir: str | None = None, *,
                  loudness_tracks: list[str], target_lufs: float = -23.0,
                  max_truepeak: float = -3.0, log: Callable[[str], None] = lambda m: None,
                  wait_kwargs: dict | None = None,
                  progress: Progress | None = None,
                  export_names: dict[str, str] | None = None,
                  program_start: str = PROGRAM_START) -> PtExportResult:
    """`program_start`: Programmbeginn aus der Einstellung (Review B18: vorher fest 10:00:00:00)."""
    progress = progress or NullProgress()
    clash = ambiguous_tracks(export_tracks)
    if clash:
        raise ExportError("Exportspuren lassen sich nicht sicher ihren Dateien zuordnen: "
                          + ", ".join(f"„{a}“ und „{b}“" for a, b in clash) + " – bitte eine davon umbenennen")
    export = import_isolated("punchbuddy.export")
    loudness = import_isolated("punchbuddy.loudness")
    progress.step("Session vorbereiten", "Transport stoppen, speichern")
    session = ptsl.open_session()
    if session is None:
        raise ExportError("Keine Session offen")
    session_dir = os.path.dirname(session.path)
    audio_dir = os.path.join(session_dir, "Audio Files")
    export_dir = export_dir or export_folder(session_dir)

    ensure_stopped(ptsl, "Export", log=log)
    log("Session speichern (Stand vor dem Export)")
    ptsl.save()

    present = {t.name: t for t in ptsl.tracks()}
    missing = [t for t in export_tracks if t not in present]
    if missing:
        raise ExportError(f"Exportspuren fehlen in der Session: {', '.join(missing)}")
    with_clips = [t for t in export_tracks if present[t].has_clips]
    if not with_clips:
        raise ExportError("Keine der Exportspuren enthält Material")

    in_time, out_time, warnings = program_range(ptsl, export, in_time=with_subframes(program_start), log=log)
    log(f"Programm {in_time} – {out_time} (Videolänge)")
    started = time.time() - 1.0
    # Ablauf nach Jens: 1. vorhandene Audiospuren kürzen, 2. DoE/AD (alle Exportspuren)
    # einblenden, 3. alle Exportspuren – auch leere – über die Videolänge konsolidieren.
    # Leere Spuren werden so zu digitaler Null, die Spurzahl stimmt, und der Mischer
    # sieht die Clips in der Session.
    progress.step("Kürzen und einblenden", ", ".join(with_clips))
    with ptsl.edit_selection():
        log(f"Überhänge kürzen: {', '.join(with_clips)}")
        ptsl.call("Überhänge kürzen",
                  lambda e: export._trim_overhangs(e, with_clips, in_time, out_time), timeout_s=120)
        log(f"Spuren einblenden: {', '.join(export_tracks)}")
        try:
            ptsl.call("Spuren einblenden", lambda e: e.set_track_hidden_state(export_tracks, False))
        except PtslError as e:
            log(f"Spuren einblenden: {e}")
        ensure_stopped(ptsl, "Konsolidieren", log=log)
        log(f"Konsolidieren über die Videolänge: {', '.join(export_tracks)}")
        progress.step("Konsolidieren", f"{len(export_tracks)} Spuren, {in_time[:11]} – {out_time[:11]}")
        ptsl.consolidate(export_tracks, in_time, out_time)
    empty = [t for t in export_tracks if t not in with_clips]
    if empty:
        warnings.append(f"Leer, als digitale Null konsolidiert: {', '.join(empty)}")
    seconds = tc_seconds(out_time) - tc_seconds(in_time)
    expected = int(seconds * 48000 * 3 * sum(present[t].channels for t in export_tracks))

    def wait() -> dict[str, str]:
        timeout = consolidate_timeout(seconds)
        return wait_for_consolidated(
            audio_dir, export_tracks, started, timeout, expected_bytes=expected,
            on_progress=lambda f: progress.update(f, f"{f * 100:.0f} % geschrieben"),
            first_file_s=min(FIRST_FILE_S, timeout), **(wait_kwargs or {}))
    try:
        consolidated = wait()
    except NothingConsolidated as e:
        # PunchBuddy 2.0.3: Auswahl erneuern und einmal wiederholen, dann Abbruch
        log(f"{e} – Auswahl erneuern, 2. Versuch")
        warnings.append("Konsolidieren im 2. Versuch (der erste erzeugte keine Dateien)")
        with ptsl.edit_selection():
            ensure_stopped(ptsl, "Konsolidieren", log=log)
            ptsl.consolidate(export_tracks, in_time, out_time, settle_s=0.4)
        consolidated = wait()

    loud_info = {}
    blocks: dict[str, str] = {}
    pb_report = os.path.join(session_dir, REPORT_NAME)       # PunchBuddy schreibt je Spur hierhin
    loud = [t for t in loudness_tracks if t in consolidated]
    progress.step("Lautheit", ", ".join(loud) or "keine Spur")
    for i, t in enumerate(loud):
        # Die Korrektur überschreibt die WAV in der Session – nie bei laufendem Transport (Jens)
        ensure_stopped(ptsl, f"Lautheit {t}", log=log)
        log(f"R128 auf {t}: {target_lufs} LUFS, höchstens {max_truepeak} dBTP")

        def cb(frac: float, msg: str, i: int = i, t: str = t) -> None:
            progress.update((i + frac) / len(loud), f"{t}: {msg}")
        before = time.time() - 1.0
        ptsl.call(f"R128 {t}", lambda e, t=t, cb=cb: loudness.normalize_track(
            e, session_dir, t, target_lufs, max_truepeak, progress_cb=cb), timeout_s=600)
        loud_info[t] = f"{target_lufs} LUFS / {max_truepeak} dBTP"
        try:
            if os.path.getmtime(pb_report) >= before:
                with open(pb_report, encoding="utf-8") as f:
                    blocks[t] = f.read()
        except OSError:
            pass
        if t not in blocks:
            warnings.append(f"Lautheitsbericht für {t} fehlt (die Lautheitskorrektur hat keinen geschrieben)")
    ptsl.save()

    log(f"WAVs kopieren nach {export_dir}")
    progress.step("WAVs kopieren", f"{len(export_tracks)} Dateien")
    os.makedirs(export_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    names = ([f"{t}.wav" for t in export_tracks] + [f"{n}.wav" for n in (export_names or {}).values() if n]
             + [REPORT_NAME])
    moved = set_aside_previous(export_dir, names, stamp)
    if moved:
        log(f"Vorige Fassung nach {EXPORT_FOLDER}/{PREVIOUS}/{stamp}: {', '.join(moved)}")
        warnings.append(f"Vorige Fassung im Exportordner nach „{PREVIOUS}/{stamp}“ verschoben ({len(moved)} Dateien)")
    export._do_wav_export(export_tracks, session_dir, min_mtime=started - 10.0, export_dir=export_dir)
    wavs = {t: os.path.join(export_dir, f"{t}.wav") for t in export_tracks
            if os.path.exists(os.path.join(export_dir, f"{t}.wav"))}
    lost = [t for t in export_tracks if t not in wavs]
    if lost:
        raise ExportError(f"WAV-Export unvollständig, fehlt: {', '.join(lost)}")
    wavs = apply_export_names(wavs, export_names or {}, log)
    report = os.path.join(export_dir, REPORT_NAME)
    with open(report, "x", encoding="utf-8") as f:              # Vorfassung ist oben beiseitegelegt
        f.write(loudness_report(session.path, target_lufs, max_truepeak, export_tracks, blocks,
                                time.strftime("%Y-%m-%d %H:%M:%S")))
    log(f"Lautheitsbericht: {report}")
    ptsl.save()
    return PtExportResult(wavs, in_time, out_time, loud_info, warnings, report)
