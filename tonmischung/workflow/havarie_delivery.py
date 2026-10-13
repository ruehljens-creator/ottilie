"""Havarie: Sendefile erstellen und ausliefern (Plan M5, Schritt 5).

Jens (03.10.2026): „Das müssen wir nur im Havariemodus machen, im Normalbetrieb geben wir nur Audiofiles an Helmut
zurück.“ Ablauf, wenn Helmut ausfällt:

1. Pro-Tools-Teil wie beim Zurückliefern: speichern, kürzen, konsolidieren, R128, WAVs (Exportnamen).
2. Bild neben dem AAF des Havarie-Pakets; Programmbeginn aus dessen Timecode.
3. Sendefile nach dem Preset (ARD-ZDF-Profil): umpacken oder neu kodieren, Kanäle nach der Spurtabelle, Stille.
4. Prüfen (wenn eingestellt); bei Fehler je nach Einstellung nicht übertragen, nachfragen oder nur vermerken.
5. SHA-256, Bericht neben dem Sendefile – auch wenn ein Schritt scheitert.
6. Transfer an die gewählten Ziele (Schreibtisch, Ordner, USB; FTP/FTPS vorbereitet, im Fenster noch nicht).
7. Zustand „ausgeliefert“ und Entsperren für Helmut über die Nachtrag-Warteschlange.

# ANNAHME: ein Auftrag für alles (Pro-Tools-Teil und Dateiarbeit nacheinander); die Teile sind getrennt
# (`pt_part`, `file_part`), damit die Dateiarbeit später im Hintergrund laufen kann.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from tonmischung import factory
from tonmischung.core.naming import ascii_umlauts
from tonmischung.core.progress import NullProgress, Progress
from tonmischung.core.session_rules import same_session
from tonmischung.core.timecode import parse_rate
from tonmischung.delivery import mux as M
from tonmischung.delivery import qc as Q
from tonmischung.delivery import report as R
from tonmischung.delivery import transfer as T
from tonmischung.delivery.picture import find_picture, program_start
from tonmischung.sessions.pt_export import EXPORT_FOLDER
from tonmischung.delivery.profiles import decide_mode, profile

SILENCE = "Stille"
STEPS = [("Session vorbereiten", 3), ("Kürzen und einblenden", 3), ("Konsolidieren", 25), ("Lautheit", 25),
         ("WAVs kopieren", 8), ("Sendefile bauen", 20), ("Prüfen", 8), ("Bericht", 2), ("Transfer", 15)]


class HavarieDeliveryError(RuntimeError):
    """Havarie-Auslieferung gescheitert – die Meldung sagt, wo; der Bericht liegt trotzdem vor."""


# ── reine Helfer ──────────────────────────────────────────────────────────────
def channel_tracks(table: Sequence[Mapping], expected: int) -> list[str]:
    """Belegung je Kanal aus der Spurtabelle (Zeile = Kanalpaar, Reihenfolge = Kanäle): Spurname oder „Stille“.
    Leere oder nicht exportierte Zeilen bleiben Stille – die folgenden Spuren rücken nicht auf."""
    out: list[str] = []
    for row in table:
        name = str(row.get("pt_spur") or "").strip()
        use = name if name and row.get("export") else SILENCE
        out += [use, use]
    if len(out) > expected:
        extra = [t for t in out[expected:] if t != SILENCE]
        if extra:
            raise HavarieDeliveryError(f"Spurtabelle belegt mehr als {expected} Kanäle: "
                                       f"{', '.join(dict.fromkeys(extra))}")
        out = out[:expected]
    return out + [SILENCE] * (expected - len(out))


def safe_name(text: str) -> str:
    """Dateiname nur aus 0-9 a-z A-Z - _ (ZDF-Regel, gilt für alle Ziele): Umlaute umschreiben, Rest „_“."""
    text = ascii_umlauts(str(text))
    text = re.sub(r"[^0-9A-Za-z_-]+", "_", text).strip("_")
    return re.sub(r"_{2,}", "_", text) or "Sendefile"


def sendefile_name(pattern: str, values: Mapping[str, Any]) -> str:
    """Muster wie „{sendung}_{datum}_{titel}_v{version}“ → sicherer Dateiname ohne Endung."""
    class _Missing(dict):
        def __missing__(self, key: str) -> str:
            return ""
    try:
        raw = (pattern or "{titel}").format_map(_Missing({k: str(v) for k, v in values.items()}))
    except (ValueError, IndexError):
        raw = str(values.get("titel") or "Sendefile")
    return safe_name(raw)


def free_version(folder: str, pattern: str, values: Mapping[str, Any], ext: str = ".mxf",
                 also: Sequence[str] = ()) -> tuple[str, int]:
    """Erste Version, deren Dateiname im Ordner – und in den Zielordnern `also` – noch frei ist (nie überschreiben).
    Ohne {version} im Muster: Nummer anhängen. Live 04.10.2026: Ein zweiter Lauf aus einer anderen Session bekam
    wieder „_v1“ und scheiterte am Ziel an der gleichnamigen Datei."""
    folders = [folder, *[f for f in also if f]]
    for v in range(1, 1000):
        name = sendefile_name(pattern, {**values, "version": v})
        if "{version}" not in (pattern or "") and v > 1:
            name = f"{name}_{v}"
        if not any(os.path.exists(os.path.join(d, name + ext)) for d in folders):
            return name, v
    raise HavarieDeliveryError("Kein freier Dateiname für das Sendefile")


@dataclass
class Request:
    """Was der Mischer im Dialog wählt bzw. was aus dem Havarie-Paket kommt."""
    aaf: str
    preset: Mapping[str, Any]
    targets: Sequence[T.Target]
    title: str
    show: str = ""
    project_id: str = ""
    picture: str = ""                    # gewähltes Bild, wenn mehrere neben dem AAF liegen
    videoid: str = ""


@dataclass
class Result:
    sendefile: str = ""
    sha256: str = ""
    report: tuple[str, str] = ("", "")
    qc_overall: str = ""
    blocked: str = ""                    # "" | gestoppt | abgelehnt
    transfers: list[T.TransferResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    wavs: dict[str, str] = field(default_factory=dict)


@dataclass
class Tools:
    ffmpeg: str
    ffprobe: str
    bmx: str = ""
    mediainfo: str = ""


# ── Ablauf ────────────────────────────────────────────────────────────────────
def pt_part(ptsl: Any, get: Callable[[str], Any], export_dir: str, progress: Progress, log: Callable[[str], None],
            run_export: Callable[..., Any] | None = None) -> tuple[dict[str, str], list[str]]:
    """Pro-Tools-Teil (exklusiv): WAVs nach Spurtabelle bzw. Spurprofil, R128, Exportnamen."""
    from tonmischung.sessions.pt_export import run_pt_export
    settings = SimpleNamespace(get=get)
    export, loud = factory.export_tracks(settings)
    if not export:
        raise HavarieDeliveryError("Keine Exportspuren (Spurtabelle bzw. Spurprofil)")
    lp = "export.spuren_lautheit.lautheit."
    r = (run_export or run_pt_export)(ptsl, export, export_dir, loudness_tracks=loud,
                                      target_lufs=float(get(lp + "ziel_lufs")),
                                      max_truepeak=float(get(lp + "true_peak_max_dbtp")), log=log, progress=progress,
                                      export_names=factory.export_names(settings))
    return dict(r.wav_files), list(r.warnings)


def shifted_sequence(aaf_path: str) -> tuple[str | None, int | None]:
    """Start-Timecode und Bildrate der Sequenz, wenn der Import sie auf den Programmbeginn verschoben hat
    (`workflow.fetch.sequence_offset`), sonst (None, None)."""
    from tonmischung.triage.aafcheck import check_aaf
    from tonmischung.workflow.fetch import sequence_offset
    a = check_aaf(aaf_path)
    if not a.ok or a.start_frames is None or not sequence_offset(a.start_frames, a.fps or 25):
        return None, None
    return a.start_timecode, a.fps


def file_part(req: Request, wavs: Mapping[str, str], get: Callable[[str], Any], tools: Tools, export_dir: str,
              journal: R.Journal, progress: Progress, ask: Callable[[dict], Any] = lambda q: False,
              now: Callable[[], datetime] = datetime.now) -> Result:
    """Dateiarbeit (ohne Pro Tools): Sendefile, Prüfung, Prüfsumme, Transfer."""
    res = Result(wavs=dict(wavs))
    prof = profile(str(req.preset.get("mxf_profil") or ""))
    progress.step("Sendefile bauen", prof.key)
    pic = find_picture(req.aaf, req.picture)
    info = M.probe(tools.ffprobe, pic.path)
    seq_tc, seq_fps = shifted_sequence(req.aaf)
    pic_fps = max(1, round(parse_rate(M.video_stream(info).get("r_frame_rate"), 25.0) or 25.0))
    ps = program_start(M.timecode(info), factory.program_start(SimpleNamespace(get=get)),
                       float(get("import.timecode_startband.startband_max_s") or 30), fps=pic_fps,
                       sequence_tc=seq_tc, sequence_fps=seq_fps)
    mode, why = decide_mode(str(req.preset.get("modus") or "auto"), prof, M.video_stream(info))
    journal.step("Bild", f"{os.path.basename(pic.path)} – {ps.note}; {why}", now=now)
    table = get("export.spuren_lautheit.spurtabelle") or []
    split = bool(req.preset.get("monospuren", True))
    names = channel_tracks(table, prof.tracks)        # Kanäle; ohne Monospuren werden daraus Stereo-Paare
    sources = M.audio_sources(names, {t: p for t, p in wavs.items()})
    out_dir = export_dir                              # Sendefile neben den WAVs (Name mit Version, nie überschreiben)
    os.makedirs(out_dir, exist_ok=True)
    local_targets = [t.path for t in req.targets if t.protocol in (T.FOLDER, T.USB) and t.path]
    stem, version = free_version(out_dir, str(req.preset.get("dateiname_muster") or "{titel}"),
                                 {"sendung": req.show, "datum": now().strftime("%Y%m%d"), "titel": req.title,
                                  "videoid": req.videoid, "projektId": req.project_id}, also=local_targets)
    job = M.MuxJob(pic.path, sources, os.path.join(out_dir, stem + ".mxf"), prof, mode, split_mono=split,
                   program_offset_s=ps.offset_s, keep_leader=str(req.preset.get("startband")) == "behalten",
                   afd=int(req.preset.get("afd") or 8), use_bmx=str(req.preset.get("werkzeug") or "bmx") == "bmx")
    mr = M.run(job, ffmpeg=tools.ffmpeg, ffprobe=tools.ffprobe, bmx=tools.bmx, on_progress=progress.update)
    res.sendefile = mr.path
    journal.meta.update({"Sendefile": mr.path, "Profil": prof.key, "Version": str(version)})
    journal.step("Sendefile", f"{os.path.basename(mr.path)} ({'umgepackt' if mode == 'copy' else 'neu kodiert'}"
                 f"{', bmx' if mr.bmx else ''}); {mr.leader.note or 'ohne Startband'}", now=now)

    findings: list[dict] = []
    if Q.enabled(get):
        progress.step("Prüfen", os.path.basename(mr.path))
        qr = Q.run_qc(mr.path, Q.settings_expectation(get, prof, mr.leader.first_tc), ffprobe=tools.ffprobe,
                      ffmpeg=tools.ffmpeg, mediainfo=tools.mediainfo)
        findings, res.qc_overall = qr.as_dicts(), qr.overall
        journal.step("Prüfung", res.qc_overall, now=now)
        verdict = Q.blocks_transfer(res.qc_overall, get)
        if verdict == "freigabe":
            fails = [f["check"] for f in findings if f["status"] == Q.FAIL]
            ok = ask({"kind": "pruefung", "title": "Prüfung des Sendefiles",
                      "text": f"Die Prüfung fand Fehler: {', '.join(fails)}. Trotzdem übertragen?",
                      "options": [{"value": True, "label": "Trotzdem übertragen"},
                                  {"value": False, "label": "Nicht übertragen", "primary": True}]})
            res.blocked = "" if ok else "abgelehnt"
        elif verdict:
            res.blocked = verdict
    else:
        journal.step("Prüfung", "ausgeschaltet (Einstellung)", now=now)

    progress.step("Bericht", "SHA-256")
    res.sha256 = R.write_sha256(mr.path, R.sha256(mr.path, progress.update))
    journal.step("Prüfsumme", os.path.basename(res.sha256), now=now)

    progress.step("Transfer", ", ".join(t.name for t in req.targets) or "kein Ziel")
    if res.blocked:
        journal.step("Transfer", f"nicht übertragen – Prüfung {res.blocked}", now=now)
        res.warnings.append("Sendefile nicht übertragen: die Prüfung fand Fehler (siehe Bericht)")
    else:
        for i, t in enumerate(req.targets):
            tr = T.send(t, mr.path, res.sha256,
                        on_progress=lambda f, i=i: progress.update((i + f) / max(1, len(req.targets))))
            res.transfers.append(tr)
            journal.step(f"Transfer {t.name}", "ok: " + ", ".join(tr.sent) if tr.ok else f"FEHLER: {tr.error}", now=now)
            if not tr.ok:
                res.warnings.append(f"Transfer an „{t.name}“ gescheitert: {tr.error}")
    res.report = R.write_report(mr.path, journal, findings, res.qc_overall or "nicht geprüft", ended=now())
    return res


def deliver_havarie(req: Request, *, ptsl: Any, get: Callable[[str], Any], tools: Tools, session_path: str,
                    progress: Progress | None = None, ask: Callable[[dict], Any] = lambda q: False,
                    log: Callable[[str], None] = lambda m: None, now: Callable[[], datetime] = datetime.now,
                    run_export: Callable[..., Any] | None = None) -> Result:
    """Ganzer Ablauf. Scheitert ein Schritt, liegt ein Bericht mit dem Fehler im Exportordner."""
    progress = progress or NullProgress()
    stamp = now().strftime("%Y%m%d-%H%M%S")
    export_dir = os.path.join(os.path.dirname(session_path), EXPORT_FOLDER)   # wie PunchBuddy, auch beim Helmut-Weg
    journal = R.Journal(f"Havarie-Auslieferung {req.title}", started=now())
    journal.meta.update({"Session": session_path, "Preset": str(req.preset.get("name") or req.preset.get("id")),
                         "Ziele": ", ".join(t.name for t in req.targets) or "keine"})
    try:
        open_session = ptsl.open_session()
        if open_session is None or not same_session(open_session.path, session_path):
            raise HavarieDeliveryError("Die Havarie-Session ist nicht offen – bitte in Pro Tools öffnen")
        wavs, warn = pt_part(ptsl, get, export_dir, progress, log, run_export)
        journal.step("Pro-Tools-Teil", f"{len(wavs)} WAVs: {', '.join(os.path.basename(p) for p in wavs.values())}",
                     now=now)
        res = file_part(req, wavs, get, tools, export_dir, journal, progress, ask, now)
        res.warnings = warn + res.warnings
        return res
    except Exception as e:
        journal.step("Abbruch", str(e), now=now)
        os.makedirs(export_dir, exist_ok=True)
        R.write_report(os.path.join(export_dir, f"Havarie-Abbruch-{stamp}.mxf"), journal, [], "abgebrochen",
                       ended=now())
        if isinstance(e, HavarieDeliveryError):
            raise
        raise HavarieDeliveryError(f"{e} – Bericht im Exportordner {export_dir}") from e
