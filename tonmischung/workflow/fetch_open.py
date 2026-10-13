"""Beitrag in die offene Session holen (Plan M4-Rest, Schritt 5).

Ablauf (Funktionsbeschreibung S. 11): offene Session speichern → umziehen nach Einstellung → AAF in
die offene Session importieren → Referenzvideo → Spurliste prüfen → Session-Pfad am neuen Beitrag
speichern, bisherigen Beitrag nach Einstellung behandeln.

Wie beim Holen erst prüfen (Übergabe, AAF, offene Session, Spuren, Überschneidungen), dann übernehmen,
dann Pro Tools. Die offene Session wird vorher immer gespeichert; scheitert das, geschieht nichts
weiter. Beim Kopieren und bei „Speichern unter“ bleibt die bisherige Session-Datei unverändert.
ANNAHME A4: ohne Clip-Verschiebungen der Triage – Match Tracks auf gleichnamige Spuren, fehlende
Spuren werden erfragt.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from tonmischung.core import mixstate
from tonmischung.core.naming import session_name_for, unique_session_dir
from tonmischung.core.open_import import (LABELS, PLAYLIST, OpenImportOptions, expected_clips, overlaps,
                                          start_frame)
from tonmischung.core.progress import NullProgress, Progress
from tonmischung.core.session_rules import same_session
from tonmischung.core.session_text import missing, track_clips
from tonmischung.core.timecode import frames_to_tc, tc_to_frames
from tonmischung.core.track_match import MAP, SKIP, TrackDecision, missing_tracks
from tonmischung.helmut.client import HelmutUnavailableError
from tonmischung.helmut.mapping import Contribution
from tonmischung.helmut.service import MixService
from tonmischung.ptsl.client import PtslClient
from tonmischung.ptsl.errors import PtslError
from tonmischung.sessions.runner import wait_for_transport_stop
from tonmischung.workflow.fetch import (PROGRAM_START, SESSION_FPS, DecideMissing, FetchError, apply_track_view,
                                        at_session_rate, check_session_start, confirm_missing_media,
                                        decide_missing_tracks, load_handover, mapped_names, restore_video_track,
                                        sequence_offset)
from tonmischung.workflow.reopen import FOLDER, CopyPlan, check_copy, copy_session, open_and_wait

OPEN_STEPS = [("Prüfen", 8), ("Übernehmen", 3), ("Session speichern", 4), ("Session umziehen", 20),
              ("AAF importieren", 40), ("Referenzvideo", 8), ("Spurliste prüfen", 10), ("Helmut aktualisieren", 7)]

# (Spuren mit Überschneidung) -> True = trotzdem einfügen
ConfirmOverlap = Callable[[dict[str, int]], bool]


@dataclass
class OpenImportResult:
    contribution: Contribution
    session_path: str
    previous: str = ""                                 # Titel des bisherigen Beitrags
    previous_action: str = ""                          # was mit ihm geschah
    clip_report: dict[str, tuple[int, int]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def fetch_into_open(project_id: str, *, service: MixService, ptsl: PtslClient, options: OpenImportOptions,
                    session_parent: str, session_storage: str = "lokal", mirror_parent: str = "",
                    decide_missing: DecideMissing | None = None,
                    confirm_overlap: ConfirmOverlap | None = None, with_video: bool = True,
                    log: Callable[[str], None] = lambda m: None, progress: Progress | None = None,
                    check_target: Callable[[CopyPlan], None] | None = None,
                    copy: Callable[[CopyPlan], None] = copy_session,
                    makedirs: Callable[[str], None] = lambda p: os.makedirs(p, exist_ok=False),
                    program_start: str = PROGRAM_START, import_options: dict | None = None,
                    track_view: dict | None = None,
                    confirm_media: Callable[[object], bool] | None = None,
                    session_fps: int = SESSION_FPS) -> OpenImportResult:
    progress = progress or NullProgress()
    errors = options.check()
    if errors:
        raise FetchError(" ".join(errors))

    # 1. Prüfen, ohne etwas zu ändern
    progress.step("Prüfen", "Übergabe, AAF und offene Session")
    c, handover, aaf = load_handover(service, project_id, log)
    pre_warnings: list[str] = []
    aaf = at_session_rate(aaf, session_fps, pre_warnings, log)       # wie beim Holen: z. B. DaVinci mit 50 fps
    fps = aaf.fps or 25
    opened = ptsl.open_session()
    if opened is None:
        raise FetchError("In Pro Tools ist keine Session offen – „Neue Session aus Template“ wählen")
    wait_for_transport_stop(ptsl)
    previous = next((b for b in service.client.list_for_mixing()
                     if b.mix.session_path and same_session(b.mix.session_path, opened.path)), None)
    if previous is not None and previous.id == project_id:
        previous = None                                 # derselbe Beitrag: nichts umzuhängen
    tracks = ptsl.tracks()
    names = {t.name for t in tracks}
    audio = [t.name for t in tracks if not t.is_video]
    before = track_clips(ptsl.session_text(), fps)
    insertion = tc_to_frames(ptsl.timeline_start(), fps) if options.position == "einfuegemarke" else None
    start = start_frame(options.position, before, insertion, fps)
    if start is None and sequence_offset(aaf.start_frames, fps, program_start):
        start = tc_to_frames(program_start, fps)     # wie beim Holen: Premiere-Sequenz ab 00:00:00:00
        pre_warnings.append(f"Sequenz beginnt bei {aaf.start_timecode} – beim Import auf {program_start} gelegt "
                            "(dort beginnt das Bild)")
    problem = check_session_start(ptsl, start if start is not None else aaf.start_frames, fps, log)
    if problem:
        raise FetchError(f"{problem} – nichts verändert")
    new_tracks = options.clips == "neue_spuren"
    decisions: dict[str, TrackDecision] = decide_missing_tracks(
        [] if new_tracks else missing_tracks(aaf.track_names, names), audio, names, aaf.track_names,
        {t for t, v in before.items() if v} | set(aaf.track_names), decide_missing, progress,
        where="in der offenen Session")
    expected = expected_clips(aaf.clips, aaf.start_frames, start, decisions)
    if options.clips == "einfuegen":
        hit = overlaps(expected, before)
        if hit and (confirm_overlap is None or not confirm_overlap(hit)):
            raise FetchError("Die neuen Clips würden vorhandene überdecken ("
                             + ", ".join(f"{t}: {n}" for t, n in hit.items())
                             + ") – nichts verändert. Andere Position oder „neue Spuren“ wählen.")
    confirm_missing_media(handover.aaf.path, pre_warnings, confirm_media, log)   # wie beim Holen
    target_plan: CopyPlan | None = None
    parent, name = unique_session_dir(session_parent, session_name_for(c.title, project_id))
    if options.relocate == "kopieren":
        target_dir = os.path.join(parent, name)
        target_plan = CopyPlan(FOLDER, opened.path, os.path.join(target_dir, f"{name}.ptx"),
                               os.path.dirname(opened.path), target_dir)
        (check_target or check_copy)(target_plan)

    # 2. In Helmut übernehmen
    progress.step("Übernehmen", c.title)
    log(f"Übernehmen: {c.title} · {service.mixer} ({service.room}) – Import in „{opened.name}“")
    c = service.take(project_id)

    warnings: list[str] = list(pre_warnings)
    try:
        # 3. Offene Session speichern – scheitert das, geschieht nichts weiter
        progress.step("Session speichern", opened.name)
        ptsl.save()

        # 4. Umziehen
        progress.step("Session umziehen", LABELS[options.relocate])
        if target_plan is not None:
            log(f"Session kopieren: {target_plan.source_dir} → {target_plan.target_dir}")
            ptsl.close()
            try:
                copy(target_plan)
            except OSError as e:
                raise FetchError(f"Kopie nicht angelegt: {e} – die bisherige Session ist gespeichert und "
                                 "geschlossen; Beitrag bleibt übernommen") from e
            warnings += open_and_wait(ptsl, target_plan.target, progress)
        elif options.relocate == "speichern_unter":
            folder = os.path.join(parent, name)
            makedirs(folder)
            log(f"Speichern unter: {folder}/{name}.ptx")
            ptsl.save_as(name, folder)
            now_open = ptsl.open_session()
            want = os.path.join(folder, f"{name}.ptx")
            if now_open is None or not same_session(now_open.path, want):
                raise FetchError(f"Speichern unter hat nicht geklappt "
                                 f"(offen: {now_open.path if now_open else 'keine'})")
        session_path = ptsl.open_session().path

        # 5. AAF importieren
        tc = frames_to_tc(start, fps) if start is not None else None
        where = LABELS[options.position] + (f" ({tc})" if tc else "")
        progress.step("AAF importieren", f"{LABELS[options.clips]}, {where}")
        log(f"AAF importieren: {LABELS[options.clips]}, {where}")
        with mapped_names(ptsl, decisions, warnings):
            ptsl.import_aaf(handover.aaf.path, as_new_tracks=new_tracks, map_start_to=tc,
                            playlist=PLAYLIST[options.clips], **(import_options or {}))

        # 6. Referenzvideo des neuen Beitrags
        if with_video and handover.video:
            progress.step("Referenzvideo", os.path.basename(handover.video.path))
            try:
                ptsl.import_video(handover.video.path, at=tc or program_start)
                warnings += restore_video_track(ptsl, next((t for t in tracks if t.is_video), None), None)
            except PtslError as e:
                warnings.append(f"Referenzvideo nicht importiert: {e}")
        elif with_video:
            warnings.append("Kein Referenzvideo in der Übergabe")

        # 7. Prüfen – jeder AAF-Clip exakt an seiner Stelle, vorhandene nicht verschwunden
        progress.step("Spurliste prüfen", "Clips mit der AAF vergleichen")
        after_tracks = ptsl.tracks()
        after = track_clips(ptsl.session_text(), fps)
        report: dict[str, tuple[int, int]] = {}
        added = [t.name for t in after_tracks if t.name not in names and not t.is_video]
        for track in aaf.track_names:
            d = decisions.get(track)
            want_clips = [(a, b) for a, b in expected_clips({track: aaf.clips.get(track, ())}, aaf.start_frames,
                                                            start, {}).get(track, [])]
            if d is not None and d.action == SKIP:
                report[track] = (len(want_clips), 0)
                warnings.append(f"{track}: bewusst weggelassen ({len(want_clips)} Clips nicht importiert)")
                continue
            if new_tracks:
                cands = [t for t in added if t == track or t.startswith(track + ".") or t.startswith(track + " ")]
                have = [c for t in cands for c in after.get(t, [])]
            else:
                have = after.get(d.target if d is not None and d.action == MAP else track, [])
            fehlt = missing(want_clips, have)
            report[track] = (len(want_clips), len(want_clips) - len(fehlt))
            if fehlt:
                warnings.append(f"{track}: {len(fehlt)} von {len(want_clips)} Clips nicht exakt angekommen, "
                                f"zuerst {frames_to_tc(fehlt[0][0], fps)}")
        for track, clips in before.items():
            weg = missing([(a, b) for a, b, _ in clips], after.get(track, []))
            if weg and options.clips == "neue_playlist":
                warnings.append(f"{track}: {len(weg)} bisherige Clips liegen nicht mehr auf der Hauptplaylist "
                                "(neue Playlist) – die bisherige Session-Datei ist unverändert")
            elif weg:
                warnings.append(f"{track}: {len(weg)} vorhandene Clips verändert oder entfernt")
        apply_track_view(ptsl, track_view, warnings, log)
        # Letzter Schritt: an den Anfang des neuen Materials – bei „Programmbeginn“ also 10:00:00:00
        target_tc = tc or program_start
        try:
            ptsl.goto(target_tc)
        except PtslError as e:
            warnings.append(f"Nicht auf {target_tc} gesprungen: {e}")
        ptsl.save()
    except PtslError as e:
        raise FetchError(f"{e} – Beitrag bleibt übernommen; erneut holen oder freigeben") from e

    # 8. Helmut: neuer Beitrag mit Session-Pfad, bisheriger nach Einstellung
    progress.step("Helmut aktualisieren", "Zustand „importiert“")
    if options.relocate == "bleibt" and previous is not None:      # Session bleibt, wo sie war
        storage, mirror = previous.mix.session_storage or session_storage, previous.mix.session_mirror
    elif options.relocate == "bleibt":
        storage, mirror = session_storage, ""
    else:
        storage = session_storage
        mirror = os.path.join(mirror_parent, name) if mirror_parent else ""
    c, hint = service.set_state_or_queue(c, mixstate.IMPORTED, session_path=session_path, session_storage=storage,
                                         session_mirror=mirror)
    if hint:
        warnings.append(hint)
    prev_title, prev_action = "", ""
    if previous is not None:
        prev_title = previous.title
        prev_action, hint = _handle_previous(service, previous, options.previous, c)
        if hint and hint not in warnings:
            warnings.append(hint)
    log(f"Fertig: {session_path}")
    return OpenImportResult(c, session_path, prev_title, prev_action, report, warnings)


def _handle_previous(service: MixService, previous: Contribution, how: str, new: Contribution) -> tuple[str, str]:
    """(was geschah, Hinweis). Geändert wird nur ein Beitrag, der diesem Mischer in diesem Raum gehört.
    Ist Helmut weg, gilt der Stand aus der Liste vor dem Import, und die Änderung wird eingereiht."""
    if how == "bleibt_in_mischung":
        return "bleibt in Mischung", ""
    try:
        current = service.client.get(previous.id)
    except HelmutUnavailableError:
        if service.queue is None:
            raise
        current = previous
    if not mixstate.owned_by(current.mix, service.mixer, service.room):
        return "unverändert", (f"„{previous.title}“ gehört nicht {service.mixer} ({service.room}) – "
                               "in Helmut nicht verändert")
    later = " (wird nachgetragen)"
    if how == "zurueckgeben":
        keep = mixstate.keep_options(current.mix, here=True)[0]
        hint = service.release_or_queue(current, f"weitergeführt in „{new.title}“", keep)
        return "freigegeben" + (later if hint else ""), hint
    _, hint = service.set_state_or_queue(current, current.mix.state, continued_in=f"{new.title} ({new.id})")
    return f"als weitergeführt in „{new.title}“ markiert" + (later if hint else ""), hint
