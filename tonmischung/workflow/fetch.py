"""Beitrag aus Helmut holen: übernehmen, Session aus Template, AAF-Import.

Reihenfolge: erst alles prüfen (Übergabe, AAF lesbar, offene Session), dann in Helmut
übernehmen, dann Pro Tools. Scheitert eine Prüfung, ist nirgends etwas verändert.
Mit AAF-Triage (Einstellung, Standard an): jeder Clip wird als O-Ton, Atmo oder Musik
eingeordnet und auf die Zielspuren des Templates verteilt (Plan Triage).
Ohne Triage: Match Tracks auf gleichnamige Template-Spuren, fehlende werden erfragt.
Neue Clips kommen über vorhandene, nichts wird gelöscht; danach wird jeder AAF-Clip in
der Session nachgeprüft.
"""
from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Callable

from tonmischung.core import mixstate
from tonmischung.core.naming import session_name_for, unique_session_dir
from tonmischung.core.progress import NullProgress, Progress
from tonmischung.core.session_rules import ImportPlan, OpenSessionState, Outcome, decide_before_import
from tonmischung.core.session_text import missing, track_clips
from tonmischung.core.timecode import PROGRAM_START, frames_to_tc, tc_parts, tc_to_frames
from tonmischung.core.track_match import (
    MAP, SKIP, TrackDecision, assign_from_a11, check_decisions, missing_tracks)
from tonmischung.helmut.mapping import Contribution, Handover
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import PtslError
from tonmischung.sessions.runner import run_import_plan
from tonmischung.sessions.triage_runner import apply_clip_moves, apply_copies, renamed_for_import
from tonmischung.triage.aafcheck import AafInfo, at_rate, check_aaf, floor_frames
from tonmischung.triage.analyse import TriageResult, triage_aaf
from tonmischung.triage.names import REST
from tonmischung.triage.placement import Placement, PlacementError, TargetRanges
from tonmischung.triage.placement import plan as plan_placement

# ANNAHME: Session-Timecode nach Haus-Standard 25 fps. Kommen 50p-Templates (Sony-Codec, Jens 03.10.2026),
# wird das eine Einstellung bzw. aus Pro Tools gelesen (GetSessionTimeCodeRate).
SESSION_FPS = 25
MAX_LEADER_S = 30            # Startband höchstens so lang (wie import.timecode_startband.startband_max_s)


def sequence_offset(start_frames: int | None, fps: int, program_tc: str = PROGRAM_START,
                    max_leader_s: int = MAX_LEADER_S) -> int:
    """Um wie viele Frames die Sequenz beim Import verschoben wird. 0 = Lage bleibt (beginnt am Programmbeginn oder
    höchstens `max_leader_s` davor – Startband). Sonst (z. B. Premiere-Sequenz ab 00:00:00:00, live 03.10.2026)
    wird der Sequenzbeginn auf den Programmbeginn gelegt; das Bild liegt dort ohnehin."""
    if start_frames is None:
        return 0
    prog = tc_to_frames(program_tc, fps)
    if prog - max_leader_s * fps <= start_frames <= prog:
        return 0
    return prog - start_frames


def before_session_start(import_start: int | None, session_start_tc: str, fps: int) -> str | None:
    """Fehlertext, wenn der Import vor dem Sessionbeginn läge, sonst None (reine Logik).

    Pro Tools 2026.4 bricht dann mit „Assertion in Cmn_Interval.h, line 143“ ab (Intervall-Beginn < 0 beim
    Überlagern vorhandener Playlists) und stürzt kurz darauf ab – live 03.10.2026 mit einer Premiere-Sequenz ab
    00:00:00:00 in einer Session ab 09:5x. Deshalb vorher prüfen und nicht importieren."""
    if import_start is None or not session_start_tc:
        return None
    try:
        session_start = tc_to_frames(session_start_tc.replace(";", ":"), fps)
    except ValueError:
        return None
    if import_start >= session_start:
        return None
    return (f"Das AAF beginnt bei {frames_to_tc(import_start, fps)}, die Session erst bei "
            f"{frames_to_tc(session_start, fps)} – Pro Tools würde beim Import abbrechen und abstürzen")


def check_session_start(ptsl: PtslClient, import_start: int | None, fps: int,
                        log: Callable[[str], None] = lambda m: None) -> str | None:
    """`before_session_start` mit dem Sessionbeginn aus Pro Tools; nicht lesbar → nur protokollieren."""
    if import_start is None:
        return None
    try:
        tc = ptsl.session_start()
    except PtslError as e:
        log(f"Sessionbeginn nicht lesbar: {e}")
        return None
    return before_session_start(import_start, str(tc or ""), fps)


def media_check(aaf_path: str, log: Callable[[str], None] = lambda m: None):
    """Medienbericht (`triage.media`) oder None, wenn das AAF dafür nicht lesbar ist (nur protokolliert)."""
    from tonmischung.triage import media
    try:
        report = media.check(aaf_path)
    except Exception as e:  # noqa: BLE001 – die Prüfung darf den Import nie verhindern
        log(f"Medien des AAF nicht prüfbar: {e}")
        return None
    log(f"Medien: {report.summary()}")
    return report


def _shift_aaf(aaf, delta: int):
    return replace(aaf, start_frames=(aaf.start_frames or 0) + delta,
                   clips={t: tuple((a + delta, b + delta) for a, b in v) for t, v in aaf.clips.items()})


def _map_triage(tr: TriageResult, pos: Callable[[int], int], span: Callable[[int], int] = lambda n: n,
                fps: int | None = None) -> TriageResult:
    """Alle Lagen der Triage umrechnen (`pos` für Zeitpunkte, `span` für Blendenlängen)."""

    def clip(c):
        return replace(c, start=pos(c.start), end=pos(c.end), fade_in=span(c.fade_in), fade_out=span(c.fade_out))
    moved = {id(c): clip(c) for c in tr.analysis.clips}
    analysis = replace(tr.analysis, start_frames=pos(tr.analysis.start_frames),
                       clips=[moved[id(c)] for c in tr.analysis.clips], fps=fps or tr.analysis.fps)
    decisions = [replace(d, clip=moved.get(id(d.clip)) or clip(d.clip)) for d in tr.decisions]
    return replace(tr, analysis=analysis, decisions=decisions)


def _shift_triage(tr: TriageResult, delta: int) -> TriageResult:
    return _map_triage(tr, lambda x: x + delta)


def _rescale_triage(tr: TriageResult, fps: int) -> TriageResult:
    from fractions import Fraction
    old = tr.analysis.fps
    if not old or old == fps:
        return tr
    k = Fraction(fps, old)
    return _map_triage(tr, lambda x: floor_frames(x * k), lambda n: floor_frames(n * k), fps)


# Schritte mit Gewicht (grob nach Dauer) für die Fortschrittsanzeige
FETCH_STEPS = [("Prüfen", 5), ("AAF-Triage", 15), ("Übernehmen", 3), ("Session wechseln", 5),
               ("Session anlegen", 7), ("AAF importieren", 40), ("Clips verteilen", 8),
               ("Referenzvideo", 10), ("Spurliste prüfen", 5), ("Helmut aktualisieren", 5)]


@dataclass(frozen=True)
class TriageOptions:
    """AAF-Triage beim Holen (Einstellungen `import.zuordnungsregeln.*`)."""
    threshold: float = 0.8
    keywords: Mapping[str, Sequence[str]] = field(default_factory=dict)
    music_paths: Sequence[str] = ()
    use_track_names: bool = True
    use_source_names: bool = True
    use_signal: bool = True
    preview: str = "nur_bei_unsicherheit"      # immer | nur_bei_unsicherheit | nie
    ranges: TargetRanges | None = None         # Zielspuren (import.zielspuren.bereiche), sonst Hausstandard


# Vorschau vor dem Import: (Triage, Verteilung, neu verteilen) -> True = so importieren,
# False/None = abbrechen, TriageResult = mit diesen Korrekturen importieren
ConfirmTriage = Callable[[TriageResult, Placement, Callable[[TriageResult], Placement]], "bool | TriageResult | None"]


def needs_preview(result: TriageResult, placement: Placement) -> bool:
    """Unsicher: Clips ohne Zuordnung (außer zweiter Fassung) oder Verteilungs-Hinweise."""
    unclear = any(d.category == REST and not d.reason.startswith("zweite Fassung") for d in result.decisions)
    return unclear or any("verdoppelt" not in n for n in placement.notes)


NEW_VIDEO_TRACK = "Video 1"                 # Name einer neu angelegten Videospur (Jens 04.10.2026)


def restore_video_track(ptsl: PtslClient, old, before: tuple[str, str] | None) -> list[str]:
    """Nach AAF- und Videoimport: Videospur-Name aus dem Template, ersetzte Videoclips melden.

    Pro Tools benennt die Videospur nach dem neuen Clip – wir benennen sie zurück. Pro Tools
    Studio erlaubt nur einen Videoclip und ersetzt beim Import (Video und AAF mit verknüpftem
    Video) den vorhandenen, z. B. das Startband im Template (live 02.10.2026: „does not
    support requested amount of video tracks“). Per PTSL nicht zu verhindern – es wird
    gemeldet, nicht verschwiegen.
    """
    try:
        return _check_video_track(ptsl, old, before)
    except PtslError as e:
        # nur ein Hinweis – der Import ist da schon gelungen und darf daran nicht scheitern (Review B22)
        return [f"Videospur nicht geprüft: {e}"]


def _check_video_track(ptsl: PtslClient, old, before: tuple[str, str] | None) -> list[str]:
    # Ohne Videospur vorher legt Pro Tools eine neue an und nennt sie nach dem Clip – Jens 04.10.2026: „neu
    # angelegte Videospuren wieder in Video 1 umbenennen“
    want = old.name if old is not None else NEW_VIDEO_TRACK
    hints, name = [], want
    now = [t for t in ptsl.tracks() if t.is_video]
    if len(now) == 1 and now[0].name != want:
        try:
            ptsl.rename_track(now[0].name, want)
        except PtslError as e:
            name = now[0].name
            hints.append(f"Videospur heißt jetzt „{name}“ statt „{want}“: {e}")
    elif not now:
        return hints
    if before:
        a, b = ptsl.clips_extent(name)
        if tc_parts(a) > tc_parts(before[0]) or tc_parts(b) < tc_parts(before[1]):
            hints.append(f"Vorhandener Videoclip {before[0][:11]}–{before[1][:11]} wurde ersetzt "
                         "(diese Pro-Tools-Version erlaubt nur einen Videoclip)")
    return hints


def apply_track_view(ptsl: PtslClient, view: dict | None, warnings: list[str],
                     log: Callable[[str], None] = lambda m: None) -> None:
    """Nach dem Import: A- und Musik-Spuren mit Clips einblenden (Ordner aufklappen), leere ausblenden
    (Jens 03.10.2026); Stems, Spr und Video bleiben, wie sie sind.
    Fehler sind nur Hinweise – der Import ist zu diesem Zeitpunkt fertig."""
    from tonmischung.core.track_view import plan_view
    if not view or not (view.get("show_clips") or view.get("hide_empty")):
        return
    try:
        p = plan_view(ptsl.track_states(), view.get("keep") or (), bool(view.get("show_clips")),
                      bool(view.get("hide_empty")), view.get("prefixes") or ("A", "Musik"),
                      targets=view.get("targets"), hideable=view.get("hideable"))
        if p.open_folders:
            ptsl.set_open(list(p.open_folders), True)
        if p.show:
            ptsl.set_hidden(list(p.show), False)
            log(f"Eingeblendet: {', '.join(p.show)}")
        if p.hide:
            ptsl.set_hidden(list(p.hide), True)
            log(f"Ausgeblendet (leer): {', '.join(p.hide)}")
    except PtslError as e:
        warnings.append(f"Spuransicht nicht angepasst: {e}")


class FetchError(Exception):
    """Holen nicht möglich; die Meldung sagt, warum und was zu tun ist."""


# Fragt, was mit AAF-Spuren geschehen soll, die im Template fehlen:
# (fehlende Spuren, verfügbare Template-Spuren) -> {AAF-Spur: TrackDecision}
DecideMissing = Callable[[list[str], list[str]], "dict[str, TrackDecision]"]


class NeedsConfirmation(Exception):
    """Offene Session ist nicht ausgeliefert – Mischer fragen."""

    def __init__(self, session_name: str) -> None:
        super().__init__(f"Offene Session „{session_name}“ ist noch nicht ausgeliefert")
        self.session_name = session_name


# ── Bausteine, die Holen und „In offene Session holen“ teilen (Durchsicht 13.10.2026) ──────────────────────────
_ASK_PRO_TOOLS = object()


def plan_for_open_session(ptsl: PtslClient, is_delivered: Callable[[str], bool], confirmed: bool | None = None,
                          unattended: bool = False, target_session_path: str | None = None,
                          opened: object = _ASK_PRO_TOOLS):
    """Session-Regeln vor dem Öffnen oder Anlegen: Was geschieht mit der offenen Session? Fragt über
    `NeedsConfirmation` (offen und nicht ausgeliefert), bricht über `FetchError` ab (Mischer hat abgelehnt).
    `opened`: schon gelesene offene Session (spart eine Abfrage). Rückgabe: (offene Session oder None, Plan)."""
    if opened is _ASK_PRO_TOOLS:
        opened = ptsl.open_session()
    state = OpenSessionState(opened.path, is_delivered(opened.path)) if opened else None
    plan: ImportPlan = decide_before_import(state, target_session_path=target_session_path, confirmed=confirmed,
                                            unattended=unattended)
    if plan.outcome is Outcome.ASK:
        raise NeedsConfirmation(opened.name)
    if plan.outcome is Outcome.ABORT:
        raise FetchError(plan.reason)
    return opened, plan


def load_handover(service: MixService, project_id: str,
                  log: Callable[[str], None] = lambda m: None) -> tuple[Contribution, Handover, AafInfo]:
    """Beitrag lesen, prüfen, ob er übernommen werden darf, Übergabe und AAF prüfen – ohne etwas zu ändern."""
    contribution = service.client.get(project_id)
    mixstate.check_take(contribution.mix, contribution.locked, service.mixer, service.room)
    handover = service.client.handover(project_id)
    if handover.aaf is None:
        raise FetchError("In der Übergabe fehlt das AAF")
    log(f"AAF prüfen: {handover.aaf.path}")
    aaf = check_aaf(handover.aaf.path)
    if not aaf.ok:
        raise FetchError(aaf.message)
    return contribution, handover, aaf


def at_session_rate(aaf: AafInfo, session_fps: int, warnings: list[str],
                    log: Callable[[str], None] = lambda m: None) -> AafInfo:
    """Andere Bildrate als die Session (DaVinci Resolve mit 50 fps, Ton in Samples; Jens 04.10.2026: „das ist auch
    etwas, was wir können müssen“): alle Lagen in Bilder der Session umrechnen."""
    if not aaf.fps or aaf.fps == session_fps:
        return aaf
    warnings.append(f"Sequenz mit {aaf.fps} fps – Lagen auf {session_fps} fps der Session umgerechnet")
    log(warnings[-1])
    return at_rate(aaf, session_fps)


def confirm_missing_media(aaf_path: str, warnings: list[str], confirm_media: Callable[[object], bool] | None,
                          log: Callable[[str], None] = lambda m: None) -> None:
    """Medien des AAF (Jens 03.10.2026: eingebettet und verknüpft muss beides gehen): fehlenden Ton vorher sagen und
    fragen; ein Nein bricht ab, bevor irgendetwas verändert ist."""
    media = media_check(aaf_path, log)
    if media is not None and media.missing_audio:
        warnings.append(media.summary())
        if confirm_media is not None and not confirm_media(media):
            raise FetchError("Nicht importiert – Audiodateien des AAF fehlen; nichts verändert")


def decide_missing_tracks(missing_names: list[str], audio: list[str], names: set[str], aaf_tracks: Sequence[str],
                          occupied: set[str], decide_missing: DecideMissing | None, progress: Progress,
                          where: str = "im Template") -> dict[str, TrackDecision]:
    """Match Tracks: Fehlt eine Spur, würde Pro Tools deren Clips still verwerfen (PunchBuddy §29) – deshalb fragen.
    „ab A11“ heißt nächste freie Spur, keine neue Spur (Jens 02.10.2026)."""
    if not missing_names:
        return {}
    if decide_missing is None:
        raise FetchError(f"{where[0].upper() + where[1:]} fehlen Spuren für Match Tracks: {', '.join(missing_names)} – "
                         "keine Möglichkeit zu fragen")
    progress.update(0.0, f"Frage: {len(missing_names)} AAF-Spuren fehlen {where}")
    decisions = decide_missing(missing_names, sorted(audio))
    errors = check_decisions(missing_names, decisions, names, aaf_tracks)
    if errors:
        raise FetchError("Entscheidung zu fehlenden Spuren ungültig: " + "; ".join(errors))
    try:
        return assign_from_a11(decisions, audio, occupied)
    except ValueError as e:
        raise FetchError(str(e)) from e


@contextmanager
def mapped_names(ptsl: PtslClient, decisions: Mapping[str, TrackDecision], warnings: list[str]) -> Iterator[None]:
    """Für den Import: Zielspuren vorübergehend wie die AAF-Spuren benennen (Match Tracks), danach zurück – auch nach
    einem Fehler."""
    renamed: list[tuple[str, str]] = []
    try:
        for track, d in decisions.items():
            if d.action == MAP:
                ptsl.rename_track(d.target, track)
                renamed.append((track, d.target))
        yield
    finally:
        for track, target in renamed:
            try:
                ptsl.rename_track(track, target)
            except PtslError as e:
                warnings.append(f"Spur „{track}“ nicht zurück in „{target}“ umbenannt: {e}")


@dataclass
class FetchResult:
    contribution: Contribution
    session_path: str
    new_tracks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # je AAF-Spur: (Clips in der AAF, davon exakt in der Session angekommen)
    clip_report: dict[str, tuple[int, int]] = field(default_factory=dict)
    triage: TriageResult | None = None
    placement: Placement | None = None


def fetch(project_id: str, *, service: MixService, ptsl: PtslClient,
          template_group: str, template_name: str, session_parent: str,
          confirmed: bool | None = None, unattended: bool = False,
          is_delivered: Callable[[str], bool] = lambda path: False,
          with_video: bool = True, log: Callable[[str], None] = lambda m: None,
          progress: Progress | None = None,
          decide_missing: DecideMissing | None = None,
          triage: TriageOptions | None = None,
          confirm_triage: ConfirmTriage | None = None,
          session_storage: str = "lokal", mirror_parent: str = "",
          goto: str | None = PROGRAM_START, import_options: dict | None = None,
          track_view: dict | None = None, confirm_media: Callable[[object], bool] | None = None,
          session_fps: int = SESSION_FPS, program_start: str = PROGRAM_START) -> FetchResult:
    """Beitrag holen. `triage` = None: ohne AAF-Triage (Match Tracks, fehlende Spuren erfragen).
    `program_start`: Programmbeginn aus der Einstellung (Review B18)."""
    progress = progress or NullProgress()
    progress.step("Prüfen", "Übergabe und AAF")

    # 1. Prüfen, ohne etwas zu ändern
    contribution, handover, aaf = load_handover(service, project_id, log)
    open_session, plan = plan_for_open_session(ptsl, is_delivered, confirmed, unattended)

    tr: TriageResult | None = None
    if triage is not None:
        progress.step("AAF-Triage", "jeden Clip einordnen")
        log("AAF-Triage: O-Ton, Atmo, Musik je Clip")
        try:
            tr = triage_aaf(handover.aaf.path, threshold=triage.threshold, keywords=triage.keywords,
                            music_paths=triage.music_paths, use_track_names=triage.use_track_names,
                            use_source_names=triage.use_source_names, use_signal=triage.use_signal,
                            progress=lambda f, t: progress.update(f, t))
        except (OSError, ValueError) as e:
            raise FetchError(f"AAF-Triage nicht möglich: {e} – nichts verändert") from e

    pre_warnings: list[str] = []
    confirm_missing_media(handover.aaf.path, pre_warnings, confirm_media, log)
    if tr is not None and aaf.fps and aaf.fps != session_fps:
        tr = _rescale_triage(tr, session_fps)
    aaf = at_session_rate(aaf, session_fps, pre_warnings, log)

    # Sequenz weit weg vom Programmbeginn: beim Import dorthin legen, alle Zeiten mitverschieben
    offset = sequence_offset(aaf.start_frames, aaf.fps or 25, program_start)
    map_tc = program_start if offset else None
    if offset:
        old = aaf.start_timecode
        aaf = _shift_aaf(aaf, offset)
        if tr is not None:
            tr = _shift_triage(tr, offset)
        pre_warnings.append(f"Sequenz beginnt bei {old} – beim Import auf {program_start} gelegt "
                            "(dort beginnt das Bild)")
        log(pre_warnings[-1])

    # 2. In Helmut übernehmen
    progress.step("Übernehmen", contribution.title)
    log(f"Übernehmen: {contribution.title} · {service.mixer} ({service.room})")
    contribution = service.take(project_id)

    # 3. Pro Tools frei machen, Session aus Template anlegen
    warnings: list[str] = list(pre_warnings)
    previous_notice = ptsl.on_dialog
    events_before = len(ptsl.dialog_events)
    ptsl.on_dialog = lambda text: (log(text), progress.update(0.0, text)) if text else None
    try:
        if plan.steps:
            progress.step("Session wechseln", f"„{open_session.name}“ speichern und schließen")
            log(f"Offene Session „{open_session.name}“ speichern und schließen")
        run_import_plan(ptsl, plan)
        parent, name = unique_session_dir(session_parent, session_name_for(contribution.title, project_id))
        session_path = os.path.join(parent, name, f"{name}.ptx")
        progress.step("Session anlegen", f"Template {template_group}/{template_name}")
        log(f"Session aus Template „{template_group}/{template_name}“ anlegen: {session_path}")
        ptsl.create_session_from_template(template_group, template_name, name, parent)
        problem = check_session_start(ptsl, aaf.start_frames, aaf.fps or 25, log)
        if problem:
            raise FetchError(f"{problem}. Nichts importiert; Beitrag bleibt übernommen – Sessionbeginn im Template "
                             "prüfen")
        before_tracks = ptsl.tracks()
        before_clips = track_clips(ptsl.session_text(), aaf.fps or 25)
        video_old = next((t for t in before_tracks if t.is_video), None)
        video_extent = ptsl.clips_extent(video_old.name) if video_old and video_old.has_clips else None

        names = {t.name for t in before_tracks}
        audio_in_order = [t.name for t in before_tracks if not t.is_video]
        placement: Placement | None = None
        if tr is not None:
            # Triage: jede AAF-Spur auf eine Zielspur ihrer Kategorie, Abweichler danach verschieben
            occupied = {t for t, c in before_clips.items() if c}
            try:
                placement = plan_placement([t.name for t in tr.analysis.tracks], tr.pairs, tr.verdicts,
                                           tr.decisions, audio_in_order, occupied, triage.ranges or TargetRanges())
            except PlacementError as e:
                raise FetchError(f"Triage: {e} – nichts importiert; Beitrag bleibt übernommen") from e

            def replan(result: TriageResult) -> Placement:
                return plan_placement([t.name for t in result.analysis.tracks], result.pairs, result.verdicts,
                                      result.decisions, audio_in_order, occupied, triage.ranges or TargetRanges())
            show = triage.preview == "immer" or (
                triage.preview == "nur_bei_unsicherheit" and needs_preview(tr, placement))
            if show and confirm_triage is not None:
                progress.update(0.0, "Frage: Vorschau der Triage bestätigen")
                answer = confirm_triage(tr, placement, replan)
                if answer is None or answer is False:
                    raise FetchError("Triage nicht bestätigt – nichts importiert; Beitrag bleibt übernommen, "
                                     "erneut holen oder freigeben")
                if isinstance(answer, TriageResult) and answer is not tr:
                    tr = answer                               # Korrekturen aus der Vorschau
                    try:
                        placement = replan(tr)
                    except PlacementError as e:
                        raise FetchError(f"Triage: {e} – nichts importiert; Beitrag bleibt übernommen") from e
        # Ohne Triage – Match Tracks (Vorgabe Jens): Clips landen auf den gleichnamigen Template-Spuren
        decisions = decide_missing_tracks(
            missing_tracks(aaf.track_names, names) if tr is None else [], audio_in_order, names, aaf.track_names,
            {t for t, c in before_clips.items() if c} | set(aaf.track_names), decide_missing, progress)
        for track, d in decisions.items():
            if d.action == MAP:
                log(f"Zuordnen: {track} → {d.target}")

        # 4. AAF importieren: Match Tracks, neue Clips über vorhandene (nichts wird gelöscht). Liegt die Sequenz
        # nicht am Programmbeginn (und wurde nicht verschoben), steht es als Hinweis da.
        if aaf.start_timecode and aaf.start_timecode != program_start:
            warnings.append(f"Sequenz beginnt bei {aaf.start_timecode}, nicht bei {program_start} "
                            "– Lage übernommen, bitte prüfen")
        if placement is not None:
            progress.step("AAF importieren", f"{len(placement.track_moves)} Tonspuren auf ihre Zielspuren")
            log(f"AAF importieren ({len(placement.track_moves)} Tonspuren, Triage)")
            try:
                with renamed_for_import(ptsl, placement.track_moves, audio_in_order, warnings):
                    ptsl.import_aaf(handover.aaf.path, as_new_tracks=False, map_start_to=map_tc,
                                    **(import_options or {}))
            except PlacementError as e:
                raise FetchError(f"Triage: {e} – nichts importiert; Beitrag bleibt übernommen") from e
            progress.step("Clips verteilen", f"{len(placement.clip_moves)} Bereiche, "
                                             f"{len(placement.copies)} Doppelmono-Kopien")
            log(f"Clips verteilen: {len(placement.clip_moves)} Bereiche, {len(placement.copies)} Kopien")
            warnings += apply_clip_moves(ptsl, placement.clip_moves, aaf.fps or 25,
                                         on_move=lambda i, n: progress.update(i / n, f"Bereich {i} von {n}"))
            warnings += apply_copies(ptsl, placement.copies)
            warnings += placement.notes
        else:
            progress.step("AAF importieren", f"{len(aaf.track_names)} Tonspuren auf gleichnamige Spuren")
            log(f"AAF importieren ({len(aaf.track_names)} Tonspuren, Match Tracks)")
            with mapped_names(ptsl, decisions, warnings):
                ptsl.import_aaf(handover.aaf.path, as_new_tracks=False, map_start_to=map_tc, **(import_options or {}))

        # 5. Referenzvideo auf die vorhandene Videospur – Fehler verhindern das Mischen nicht
        if with_video and handover.video:
            progress.step("Referenzvideo", os.path.basename(handover.video.path))
            log(f"Referenzvideo: {handover.video.path}")
            try:
                ptsl.import_video(handover.video.path, at=program_start)
            except PtslError as e:
                warnings.append(f"Referenzvideo nicht importiert: {e}")
        elif with_video:
            warnings.append("Kein Referenzvideo in der Übergabe")

        # 6. Prüfen – die Antwort des Imports ist nicht zuverlässig: jeder AAF-Clip muss
        # exakt auf seiner Spur liegen, und kein vorhandener Clip darf verschwunden sein.
        progress.step("Spurliste prüfen", "Clips mit der AAF vergleichen")
        warnings += restore_video_track(ptsl, video_old, video_extent)
        after = ptsl.tracks()
        after_clips = track_clips(ptsl.session_text(), aaf.fps or 25)
        report = {}
        if tr is not None and placement is not None:
            report = _triage_report(tr, placement, after_clips, warnings, aaf.fps or 25)
        for track in (aaf.track_names if tr is None else ()):
            expected = list(aaf.clips.get(track, ()))
            d = decisions.get(track)
            if d and d.action == SKIP:
                report[track] = (len(expected), 0)
                warnings.append(f"{track}: bewusst weggelassen ({len(expected)} Clips nicht importiert)")
                continue
            ziel = d.target if d and d.action == MAP else track
            fehlt = missing(expected, after_clips.get(ziel, []))
            report[track] = (len(expected), len(expected) - len(fehlt))
            if fehlt:
                warnings.append(f"{track}: {len(fehlt)} von {len(expected)} Clips nicht exakt angekommen, "
                                f"zuerst {frames_to_tc(fehlt[0][0], aaf.fps or 25)}")
        for track, clips in before_clips.items():
            weg = missing([(a, b) for a, b, _ in clips], after_clips.get(track, []))
            if weg:
                warnings.append(f"{track}: {len(weg)} vorhandene Clips verändert oder entfernt")
        video_before = [t.name for t in before_tracks if t.is_video]
        new_video = [t.name for t in after if t.is_video and t.name not in video_before]
        if video_before and len([t for t in after if t.is_video]) > len(video_before):
            warnings.append(f"Video auf neuer Spur statt auf der vorhandenen: {', '.join(new_video)}")
        new = [t for t in after if t.name not in names and not t.is_video]   # angelegte Spuren
        apply_track_view(ptsl, track_view, warnings, log)
        if goto:                                  # letzter Schritt: zum Programmbeginn (Jens, 03.10.2026)
            try:
                ptsl.goto(goto)
                log(f"Einfügemarke auf {goto}")
            except PtslError as e:
                warnings.append(f"Nicht auf {goto} gesprungen: {e}")
        ptsl.guard_dialogs("Vor dem Speichern")
        warnings += ptsl.dialog_events[events_before:]
        ptsl.save()
    except PtslError as e:
        raise FetchError(f"{e} – Beitrag bleibt übernommen; erneut holen oder freigeben") from e
    finally:
        ptsl.on_dialog = previous_notice

    # 7. Helmut aktualisieren
    progress.step("Helmut aktualisieren", "Zustand „importiert“")
    mirror = os.path.join(mirror_parent, name) if mirror_parent else ""
    contribution, hint = service.set_state_or_queue(contribution, mixstate.IMPORTED, session_path=session_path,
                                                    session_storage=session_storage, session_mirror=mirror,
                                                    template=f"{template_group}/{template_name}")
    if hint:
        warnings.append(hint)
    log(f"Fertig: {session_path}")
    return FetchResult(contribution, session_path, [t.name for t in new], warnings, report,
                       tr, placement)


def _triage_report(tr: TriageResult, placement: Placement, after_clips: dict, warnings: list[str],
                   fps: int) -> dict[str, tuple[int, int]]:
    """Je AAF-Spur: (Clips, davon exakt in ihrer Endlage – auch beide Hälften bei Doppelmono)."""
    have = {t: {(a, b) for a, b, _ in v} for t, v in after_clips.items()}
    clip = {(d.clip.track, d.clip.index): d.clip for d in tr.decisions}
    report: dict[str, tuple[int, int]] = {}
    for t in tr.analysis.tracks:
        keys = [k for k in placement.final if k[0] == t.name]
        if not keys:
            continue
        bad = [k for k in keys if not all((clip[k].start, clip[k].end) in have.get(w, set())
                                          for w in placement.final[k])]
        report[t.name] = (len(keys), len(keys) - len(bad))
        if bad:
            c = clip[bad[0]]
            warnings.append(f"{t.name}: {len(bad)} von {len(keys)} Clips nicht exakt in ihrer Endlage "
                            f"({'/'.join(placement.final[bad[0]])}), zuerst {frames_to_tc(c.start, fps)}")
    return report
