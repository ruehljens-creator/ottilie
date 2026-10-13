"""Sendefile bauen: Bild umpacken oder neu kodieren, Tonspuren einmuxen, bmx-Nachlauf (Plan M5, Schritt 2).

Herkunft: punchbuddy-helmut `punchbuddy/remux.py` (Stand 17.09.2026), an Ottilie angepasst:
- Kein Fenster, kein globaler Zustand: `build_command()` ist reine Logik, `run()` führt aus.
- Startband nach Preset (`entfernen` | `behalten`) mit bekanntem Programmbeginn statt Bildanalyse:
  `program_offset_s` sagt, wo 10:00:00:00 im Bild liegt (der Aufrufer kennt es aus Pro Tools oder dem
  Timecode des Bildes). Beim Umpacken beginnt das Bild nur an einem Keyframe – der Rest wird mit echter
  Stille im Ton ausgeglichen (`adelay`, nicht `-itsoffset`; Erkenntnis §12 des Arbeitsstands).
- Ton nach der Kanalbelegung aus der Spurtabelle (Stereo-WAV je Spur oder Stille), als Monospuren,
  wenn das Preset es will; jede Spur auf Bildlänge (`apad`), sonst kappt der ffmpeg-MXF-Muxer das Bild im
  GOP (Erkenntnis 16.09.).
- Zeitlimit gegen hängendes ffmpeg (keine Fortschrittsmeldung für `stall_s`), Abbruch von außen möglich.
- Nichts wird überschrieben: Zwischendateien mit eigenem Namen, das Ergebnis per Hardlink, nur wenn der
  Zielname frei ist.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from tonmischung.core.fileio import publish_new
from tonmischung.core.timecode import PROGRAM_START, frames_to_tc, tc_to_frames
from tonmischung.delivery.profiles import COPY, Profile, bmx_args

FPS_TC = 25
SAMPLE_RATE = 48000


class MuxError(RuntimeError):
    """Sendefile nicht gebaut – die Meldung sagt, woran es lag."""


@dataclass(frozen=True)
class AudioSource:
    path: str | None              # None = Stille
    channels: int = 2             # belegte Kanäle im Sendefile (ein Paar = 2)
    file_channels: int = 0        # Kanäle der WAV (0 = wie `channels`); Mono-WAV auf einem Paar → beidseitig


@dataclass(frozen=True)
class MuxJob:
    video: str
    audio: tuple[AudioSource, ...]          # in Kanalreihenfolge (1/2, 3/4, …)
    out: str
    profile: Profile
    mode: str                               # copy | recode
    split_mono: bool = True
    program_offset_s: float = 0.0           # wo 10:00:00:00 im Bild liegt (Startband davor)
    keep_leader: bool = False               # Preset „startband: behalten“
    start_tc: str = PROGRAM_START
    afd: int | None = 8
    use_bmx: bool = True


@dataclass(frozen=True)
class LeaderPlan:
    cut_s: float                  # Bild ab hier
    delay_ms: int                 # Ton um so viel später
    first_tc: str                 # Timecode des ersten Bildes im Sendefile
    note: str


def offset_from_timecode(video_tc: str | None, program_tc: str = PROGRAM_START, fps: int = FPS_TC) -> float | None:
    """Programmbeginn im Bild aus dessen Start-Timecode: 09:59:50:00 → 10 s. None ohne Timecode;
    negativ (Bild beginnt nach dem Programmbeginn) ist ein Fehler des Bildes."""
    if not video_tc:
        return None
    return (tc_to_frames(program_tc, fps) - tc_to_frames(video_tc, fps)) / fps


def plan_leader(job: MuxJob, keyframe_before: Callable[[float], float], fps: int = FPS_TC) -> LeaderPlan:
    """Wo das Bild beginnt, wie weit der Ton verschoben wird, welcher Timecode am ersten Bild steht."""
    off = max(0.0, float(job.program_offset_s or 0.0))
    program = tc_to_frames(job.start_tc, fps)
    if off <= 0:
        return LeaderPlan(0.0, 0, job.start_tc, "")
    if job.keep_leader:
        return LeaderPlan(0.0, round(off * 1000), frames_to_tc(program - round(off * fps), fps),
                          f"Startband bleibt ({off:.2f} s), Ton beginnt bei {job.start_tc}")
    cut = off if job.mode != COPY else max(0.0, min(off, keyframe_before(off)))
    rest = off - cut
    if rest < 0.5 / fps:
        return LeaderPlan(cut, 0, job.start_tc, f"Startband entfernt ({off:.2f} s, bildgenau)")
    return LeaderPlan(cut, round(rest * 1000), frames_to_tc(program - round(rest * fps), fps),
                      f"Startband bis auf {rest:.2f} s entfernt (Bild beginnt nur an einem Keyframe), "
                      "Ton um diesen Rest verschoben – Bild und Ton bleiben synchron")


def _audio_graph(job: MuxJob, duration_s: float, delay_ms: int) -> tuple[list[str], list[str], list[str]]:
    """(Eingänge, Filter, Ausgangsmarken). Jede echte Spur: verschieben, ggf. teilen, auf Bildlänge auffüllen."""
    inputs, filters, labels = [], [], []
    dur = f"{duration_s:.3f}"
    pre = f"adelay={delay_ms}:all=1," if delay_ms else ""
    idx = 1
    for n, src in enumerate(job.audio, start=1):
        ch = max(1, int(src.channels))
        if src.path is None:
            if job.split_mono:
                for c in range(ch):
                    lbl = f"s{n}_{c}"
                    filters.append(f"anullsrc=channel_layout=mono:sample_rate={SAMPLE_RATE}:duration={dur}[{lbl}]")
                    labels.append(lbl)
            else:
                lbl = f"s{n}"
                layout = "stereo" if ch == 2 else "mono"
                filters.append(f"anullsrc=channel_layout={layout}:sample_rate={SAMPLE_RATE}:duration={dur}[{lbl}]")
                labels.append(lbl)
            continue
        inputs += ["-i", src.path]
        fc = max(1, int(src.file_channels or ch))
        fit = f"apad=whole_dur={dur},atrim=0:{dur}"         # genau Bildlänge, nie länger
        if job.split_mono:
            if fc == ch and ch > 1:
                layout = "stereo" if ch == 2 else f"{ch}c"
                filters.append(f"[{idx}:a:0]{pre}channelsplit=channel_layout={layout}"
                               + "".join(f"[t{n}_{c}]" for c in range(ch)))
            elif fc == 1:
                # ANNAHME: Mono-WAV auf einem Kanalpaar liegt links und rechts (Doppel-Mono)
                filters.append(f"[{idx}:a:0]{pre}asplit={ch}" + "".join(f"[t{n}_{c}]" for c in range(ch)))
            else:
                raise MuxError(f"{os.path.basename(src.path)}: {fc} Kanäle, belegt sind {ch}")
            for c in range(ch):
                filters.append(f"[t{n}_{c}]{fit}[a{n}_{c}]")
                labels.append(f"a{n}_{c}")
        else:
            if fc == ch:
                filters.append(f"[{idx}:a:0]{pre}{fit}[a{n}]")
            elif fc == 1 and ch == 2:
                filters.append(f"[{idx}:a:0]{pre}pan=stereo|c0=c0|c1=c0,{fit}[a{n}]")
            else:
                raise MuxError(f"{os.path.basename(src.path)}: {fc} Kanäle, belegt sind {ch}")
            labels.append(f"a{n}")
        idx += 1
    return inputs, filters, labels


def expected_streams(job: MuxJob) -> int:
    return sum(max(1, s.channels) if job.split_mono else 1 for s in job.audio)


def build_command(job: MuxJob, ffmpeg: str, video_duration_s: float, leader: LeaderPlan,
                  intermediate: str) -> list[str]:
    """ffmpeg-Aufruf für Bild + Ton → `intermediate` (MXF, vor dem bmx-Nachlauf)."""
    want = job.profile.tracks if job.split_mono else job.profile.tracks // 2
    if expected_streams(job) != want:
        raise MuxError(f"{expected_streams(job)} Tonspuren statt {want} für {job.profile.key} – "
                       "Kanalbelegung prüfen (fehlende Kanäle als Stille)")
    if not any(s.path for s in job.audio):
        raise MuxError("Keine Tonspur mit Material")
    duration = max(0.0, video_duration_s - leader.cut_s)
    if duration <= 0:
        raise MuxError("Bild ist nach dem Startband leer")
    cmd = [ffmpeg, "-nostdin", "-n", "-hide_banner", "-nostats", "-progress", "pipe:1"]
    if leader.cut_s > 0:
        cmd += ["-ss", f"{leader.cut_s:.3f}"]          # vor -i: beim Umpacken auf das Keyframe
    cmd += ["-i", job.video]
    inputs, filters, labels = _audio_graph(job, duration, leader.delay_ms)
    video = "0:v:0"
    if job.mode != COPY and job.profile.scan == "tt":
        # Halbbildreihenfolge ausdrücklich: ffmpeg 8 schreibt bei progressiver Quelle trotz -field_order tt „unteres
        # Halbbild zuerst“ (tb) – gefunden beim Bau auf dem Apple-Silicon-Mac (ffmpeg 8.1.1, 12.10.2026); ffmpeg 9
        # macht es richtig. fieldorder dreht echte BFF-Quellen um, setfield markiert progressive als TFF.
        filters.insert(0, "[0:v:0]fieldorder=tff,setfield=tff[v]")
        video = "[v]"
    cmd += inputs + ["-filter_complex", ";".join(filters), "-map", video]
    for lbl in labels:
        cmd += ["-map", f"[{lbl}]"]
    cmd += (["-c:v", "copy"] if job.mode == COPY else list(job.profile.encode))
    cmd += ["-c:a", "pcm_s24le", "-ar", str(SAMPLE_RATE)]
    if job.mode != COPY:
        cmd += ["-t", f"{duration:.3f}"]               # beim Umpacken kein -t: es kappte mitten im GOP
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1", "-timecode", leader.first_tc, "-f", "mxf", intermediate]
    return cmd


def bmx_command(job: MuxJob, bmx: str, intermediate: str, part: str, first_tc: str) -> list[str]:
    return [bmx, *bmx_args(job.profile, job.afd), "-y", first_tc, "-o", part, intermediate]


# ── Ausführen ───────────────────────────────────────────────────────────────
def probe(ffprobe: str, path: str, timeout: float = 60) -> dict:
    proc = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise MuxError(f"ffprobe: {os.path.basename(path)} nicht lesbar – {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout or "{}")


def video_stream(info: dict) -> dict:
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    raise MuxError("Bild enthält keinen Videostrom")


def timecode(info: dict) -> str | None:
    tc = (info.get("format", {}).get("tags") or {}).get("timecode")
    if tc:
        return tc
    for s in info.get("streams", []):
        if (s.get("tags") or {}).get("timecode"):
            return s["tags"]["timecode"]
    return None


def duration_s(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def keyframe_before(ffprobe: str, video: str, t_s: float) -> float:
    """Letztes Keyframe bei oder vor t_s (0.0, wenn keins gefunden)."""
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-read_intervals",
           f"%+{int(t_s) + 2}", "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", video]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout or ""
    start = 0.0
    best = 0.0
    times = []
    for line in out.splitlines():
        try:
            times.append(float(line.strip().rstrip(",")))
        except ValueError:
            continue
    if times:
        start = min(times)                             # Zeitstempel relativ zum Dateianfang
    for v in times:
        if v - start <= t_s + 1e-6:
            best = max(best, v - start)
    return best


def _low_priority(cmd: list[str], on: bool) -> list[str]:
    """ffmpeg/bmx mit niedriger Priorität (taskpolicy -b), damit Pro Tools Vorrang hat (Prototyp)."""
    tp = "/usr/sbin/taskpolicy"
    return [tp, "-b", *cmd] if on and os.path.exists(tp) else cmd


def _remove_own(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def run_ffmpeg(cmd: list[str], total_s: float, on_progress: Callable[[float], None], stall_s: float = 300.0,
               cancelled: Callable[[], bool] = lambda: False) -> None:
    """ffmpeg mit Fortschritt; hängt es `stall_s` ohne Meldung oder wird abgebrochen → MuxError.

    Die Fehlerausgabe liest ein eigener Thread laufend mit (Review A9): Vorher wurde sie erst am Ende gelesen – schrieb
    ffmpeg viele Warnungen, lief die Leitung voll, ffmpeg blieb stehen und wurde nach `stall_s` mit falscher Meldung
    abgebrochen."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
                            errors="replace")
    err_tail: deque[str] = deque(maxlen=20)

    def drain() -> None:
        for line in proc.stderr:
            if line.strip():
                err_tail.append(line.strip())
    reader = threading.Thread(target=drain, daemon=True, name="ffmpeg-stderr")
    reader.start()
    try:
        last = time.monotonic()
        while True:
            if cancelled():
                proc.kill()
                proc.wait(timeout=10)
                raise MuxError("Abgebrochen")
            ready, _, _ = select.select([proc.stdout], [], [], 1.0)
            if ready:
                line = proc.stdout.readline()
                if not line:
                    break
                last = time.monotonic()
                if line.startswith("out_time_us=") and total_s > 0:
                    try:
                        on_progress(min(0.99, int(line.split("=", 1)[1]) / 1e6 / total_s))
                    except ValueError:
                        pass
            elif proc.poll() is not None:
                break
            elif stall_s > 0 and time.monotonic() - last > stall_s:
                proc.kill()
                proc.wait(timeout=10)
                raise MuxError(f"ffmpeg kam {stall_s:.0f} s lang nicht voran und wurde abgebrochen "
                               "(hängender Zugriff auf Quelle oder Ziel?)")
        proc.wait()
        reader.join(timeout=5)
        if proc.returncode != 0:
            raise MuxError(f"ffmpeg-Fehler ({proc.returncode}): {' | '.join(list(err_tail)[-3:])[:400]}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        reader.join(timeout=5)
        for stream in (proc.stdout, proc.stderr):
            if stream:
                stream.close()


BMX_MISSING = ("bmxtranswrap fehlt – das Preset verlangt den bmx-Nachlauf (ARD/ZDF-Struktur). "
               "Einstellungen › Verbindungen › Werkzeuge prüfen oder ein Preset mit Werkzeug „ffmpeg“ wählen")


@dataclass(frozen=True)
class MuxResult:
    path: str
    mode: str
    leader: LeaderPlan
    bmx: bool
    command: tuple[str, ...]


def run(job: MuxJob, *, ffmpeg: str, ffprobe: str, bmx: str = "", work_dir: str = "",
        on_progress: Callable[[float], None] = lambda f: None, stall_s: float = 300.0,
        cancelled: Callable[[], bool] = lambda: False, low_priority: bool = True) -> MuxResult:
    """Baut das Sendefile `job.out`. Zwischendateien liegen in `work_dir` (Vorgabe: neben dem Ziel)."""
    if os.path.exists(job.out):
        raise MuxError(f"{os.path.basename(job.out)} gibt es schon – wird nicht überschrieben")
    if job.use_bmx and not bmx:             # vorher still ohne bmx gebaut – erst die Prüfung merkte es (Review B36)
        raise MuxError(BMX_MISSING)

    from tonmischung.delivery.audio_format import analyse
    sources = []
    for src in job.audio:
        if src.path:
            info_a = analyse(src.path) if os.path.isfile(src.path) else None
            if info_a is None:
                raise MuxError(f"Tonspur fehlt oder ist nicht lesbar: {src.path}")
            src = replace(src, file_channels=info_a.channels)
        sources.append(src)
    job = replace(job, audio=tuple(sources))
    info = probe(ffprobe, job.video)
    video_stream(info)
    total = duration_s(info)
    leader = plan_leader(job, lambda t: keyframe_before(ffprobe, job.video, t))
    work = work_dir or os.path.dirname(os.path.abspath(job.out))
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(job.out)), exist_ok=True)
    stamp = f"{os.getpid()}-{time.time_ns()}"
    base = os.path.splitext(os.path.basename(job.out))[0]
    intermediate = os.path.join(work, f".{base}.{stamp}.ff.mxf")
    part = os.path.join(os.path.dirname(os.path.abspath(job.out)), f".{base}.{stamp}.part.mxf")
    cmd = build_command(job, ffmpeg, total, leader, intermediate)
    try:
        run_ffmpeg(_low_priority(cmd, low_priority), max(0.0, total - leader.cut_s),
                   lambda f: on_progress(f * (0.9 if job.use_bmx and bmx else 1.0)), stall_s, cancelled)
        used_bmx = False
        if job.use_bmx and bmx:
            on_progress(0.92)
            b = subprocess.run(_low_priority(bmx_command(job, bmx, intermediate, part, leader.first_tc), low_priority),
                               capture_output=True, text=True, timeout=max(600.0, total * 4))
            if b.returncode != 0:
                raise MuxError(f"bmx: {(b.stderr or b.stdout).strip()[-300:]}")
            used_bmx = True
            src = part
        else:
            src = intermediate
        try:
            publish_new(src, job.out)                  # nie überschreiben: scheitert, wenn das Ziel existiert
        except FileExistsError:
            raise MuxError(f"{os.path.basename(job.out)} ist inzwischen entstanden – nicht überschrieben") from None
        on_progress(1.0)
        return MuxResult(job.out, job.mode, leader, used_bmx, tuple(cmd))
    finally:
        _remove_own(intermediate)                      # eigene Zwischendateien
        _remove_own(part)


def audio_sources(channel_tracks: Sequence[str], wavs: dict[str, str],
                  silence: str = "Stille") -> tuple[AudioSource, ...]:
    """Kanalbelegung (je Kanal der Spurname, Paare L/R) → Quellen je Paar: Stereo-WAV der Spur oder Stille."""
    out: list[AudioSource] = []
    for i in range(0, len(channel_tracks), 2):
        name = channel_tracks[i]
        if name == silence:
            out.append(AudioSource(None, 2))
        elif name in wavs:
            out.append(AudioSource(wavs[name], 2))
        else:
            raise MuxError(f"Keine WAV für Spur „{name}“")
    return tuple(out)
