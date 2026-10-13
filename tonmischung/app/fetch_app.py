"""Holen im Fenster: Beitrag übernehmen und importieren, in die offene Session holen, Triage-Vorschau und -Bericht.

Die Abläufe selbst stehen in `workflow/fetch.py` und `workflow/fetch_open.py`; hier werden die Einstellungen
zusammengestellt und die Fragen des Ablaufs ans Fenster gegeben.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import replace
from typing import Any

from tonmischung import factory
from tonmischung.app import templates_app, viewmodel
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.app.jobs import Cancelled, JobContext, JobRunner
from tonmischung.app.shared import confirming, contribution, is_delivered, placement
from tonmischung.core.session_rules import same_session
from tonmischung.core.track_match import FROM_A11, MAP, SKIP, TrackDecision
from tonmischung.helmut.client import HelmutError
from tonmischung.ptsl.errors import PtslError

log = logging.getLogger("tonmischung.app")
IMPORT_TEXTS = ("wird importiert", "Speichern, schließen und importieren",
                "Nicht importiert – die offene Session bleibt, wie sie ist")


# ── Einstellungen für den Import ─────────────────────────────────────────────────
def import_options(ctx: AppContext, profile: Any = None) -> dict:
    """Was beim AAF-Import auf die Spuren kommt (Einstellungen „Import-Vorschau“): Clips immer, Clip Gain und
    Lautstärke-Automation nach Einstellung, sonst bleiben die Spuren unverändert (Jens 03.10.2026).
    Ein Spurprofil kann ein eigenes Spurdaten-Preset vorgeben."""
    from tonmischung.app.settings_view import track_data_preset_path
    s = ctx.settings
    name = str((profile.track_data_preset if profile is not None else "")
               or s.get("import.import_vorschau.trackdaten_preset") or "")
    path = track_data_preset_path(name)
    if name and not path:
        log.warning("Spurdaten-Preset „%s“ fehlt auf diesem Mac – Import ohne", name)
    return {"clip_gain": bool(s.get("import.import_vorschau.clip_gain_uebernehmen")),
            "volume_automation": bool(s.get("import.import_vorschau.automation_uebernehmen")),
            "track_data_preset": path}


NO_PRESET_HINT = ("Ohne Spurdaten-Preset übernimmt Pro Tools Farbe und Höhe der Spuren aus dem AAF – in den "
                  "Einstellungen (Import › Import-Vorschau) das Preset „Ottilie Clips und Automation“ wählen")


def preset_hint(opts: dict) -> list[str]:
    return [] if opts.get("track_data_preset") else [NO_PRESET_HINT]


def track_view(ctx: AppContext) -> dict:
    """Spuransicht nach dem Import (Einstellungen): nur A- und Musik-Spuren (Zielspuren der Triage); Stems, Spr
    und Video bleiben unberührt, Export-, Aufnahme- und Wiedergabespuren werden zusätzlich nie ausgeblendet."""
    from tonmischung.core.track_view import prefixes_from_ranges
    s = ctx.settings
    keep = {str(r.get("pt_spur") or "").strip() for r in (s.get("export.spuren_lautheit.spurtabelle") or [])
            if r.get("export")}
    for key in ("rec_a", "monitor_a", "rec_b", "monitor_b", "play_input"):
        keep |= {str(t) for t in (s.get(f"aufnahme.spuren.{key}") or [])}
    keep |= {str(s.get("aufnahme.play_custom.kanal_1_spur") or ""),
             str(s.get("aufnahme.play_custom.kanal_2_spur") or "")}
    return {"show_clips": bool(s.get("import.import_vorschau.spuren_mit_clips_einblenden")),
            "hide_empty": bool(s.get("import.import_vorschau.leere_spuren_ausblenden")),
            "keep": sorted(k for k in keep if k),
            "prefixes": list(prefixes_from_ranges({k: {e: s.get(f"import.zielspuren.bereiche.{k}.{e}")
                                                       for e in ("von", "bis")}
                                                   for k in ("o_ton", "atmo", "musik", "rest")}))}


# ── Fragen des Ablaufs ans Fenster ────────────────────────────────────────────────
def ask_missing(jc: JobContext, where: str = "im Template"):
    """Fehlende Zielspuren (Match Tracks): Frage im Fenster statt im Terminal. `where`: „im Template“ bzw. „in der
    offenen Session“ (In offene Session holen)."""
    def decide(missing: list[str], available: list[str]) -> dict[str, TrackDecision]:
        answer = jc.ask({"kind": "fehlende_spuren", "title": f"Spuren fehlen {where}",
                         "text": f"Diese AAF-Spuren haben keine gleichnamige Spur {where}. "
                                 "Was soll mit ihnen geschehen?",
                         "missing": missing, "available": available})
        if not answer:
            raise Cancelled("Nicht importiert – keine Entscheidung zu den fehlenden Spuren")
        out = {}
        for track, choice in answer.items():
            if choice == FROM_A11:
                out[track] = TrackDecision(FROM_A11)
            elif choice == SKIP:
                out[track] = TrackDecision(SKIP)
            else:
                out[track] = TrackDecision(MAP, str(choice))
        return out
    return decide


def ask_media(jc: JobContext):
    """Frage ans Fenster, wenn Audiodateien des AAF fehlen (verknüpftes AAF, Dateien nicht erreichbar)."""
    def ask(report) -> bool:
        return bool(jc.ask({"kind": "medien", "title": "Audiodateien des AAF fehlen",
                            "text": f"{report.summary()}. Trotzdem importieren?",
                            "options": [{"value": True, "label": "Trotzdem importieren (Clips ohne Ton)"},
                                        {"value": False, "label": "Abbrechen", "primary": True}]}))
    return ask


class TriagePreview:
    """Offene Triage-Vorschau eines Auftrags: Korrekturen sammeln, neu verteilen."""

    def __init__(self, result, placement, replan, template: str) -> None:
        self.result, self.placement, self.replan, self.template = result, placement, replan, template
        self.lock = threading.Lock()

    def view(self) -> dict:
        return viewmodel.triage_view(self.result, self.placement, self.template)

    def correct(self, track: str, index: int, category: str) -> dict:
        from tonmischung.triage.corrections import apply_corrections
        from tonmischung.triage.placement import PlacementError
        with self.lock:
            try:
                result = apply_corrections(self.result, [(track, int(index), category)])
                placement = self.replan(result) if self.replan else self.placement
            except (ValueError, PlacementError) as e:
                raise CommandError(str(e)) from e
            self.result, self.placement = result, placement
            return self.view()


def confirm_triage(jc: JobContext, ctx: AppContext | None = None, template: str = ""):
    """Triage-Vorschau im Fenster: Zeitleiste/Liste mit Korrekturen, dann „Import starten“."""
    def confirm(result, placement, replan=None):
        preview = TriagePreview(result, placement, replan, template)
        if ctx is not None:
            ctx.extra["preview"] = preview
        try:
            ok = jc.ask({"kind": "triage_vorschau", "title": "Vorschau der AAF-Triage", "view": preview.view(),
                         "options": [{"value": True, "label": "Import starten", "primary": True},
                                     {"value": False, "label": "Abbrechen"}]})
        finally:
            if ctx is not None:
                ctx.extra.pop("preview", None)
        return preview.result if ok else False
    return confirm


def triage_correct(ctx: AppContext, args: dict) -> dict:
    preview = ctx.extra.get("preview")
    if preview is None:
        raise CommandError("Keine Triage-Vorschau offen")
    return preview.correct(args["track"], args["index"], args["category"])


def fetch_summary(result) -> dict:
    """Ergebnis fürs Fenster: Session, Clips (angekommen von gesamt), Triage, Hinweise."""
    n = sum(v[0] for v in result.clip_report.values())
    ok = sum(v[1] for v in result.clip_report.values())
    return {"session": result.session_path, "clips": n, "clips_ok": ok,
            "moves": len(result.placement.clip_moves) if getattr(result, "placement", None) else 0,
            "triage": getattr(result, "placement", None) is not None, "warnings": list(result.warnings),
            "new_tracks": list(getattr(result, "new_tracks", []))}


# ── Holen ─────────────────────────────────────────────────────────────────────────
def start_fetch(ctx: AppContext, runner: JobRunner, args: dict) -> dict:
    """Holen im Auftrags-Thread; Fragen gehen ans Fenster."""
    from tonmischung.core import track_profile as TP
    from tonmischung.workflow.fetch import FETCH_STEPS, fetch
    pid, group, name = str(args["id"]), args.get("group", ""), args.get("name", "")
    if not group or not name:
        raise CommandError("Bitte ein Template wählen")
    templates_app.ensure_installed(ctx, group, name)       # hochgeladenes Template in die Pro-Tools-Bibliothek
    c = contribution(ctx, pid)
    title, show = c.title, c.show
    pl = placement(ctx, c)
    use_triage = bool(args.get("triage", ctx.settings.get("import.zuordnungsregeln.aktiv")))
    options = factory.triage_options(ctx.settings) if use_triage else None
    profile = templates_app.profile_for(ctx, group, name)
    if profile is not None and options is not None:
        options = replace(options, ranges=TP.target_ranges(profile))      # Triage-Ziele aus dem Spurprofil
    view = TP.track_view(profile) if profile is not None else track_view(ctx)
    import_opts = import_options(ctx, profile)
    start = factory.program_start(ctx.settings)

    def work(jc: JobContext) -> dict:
        result = confirming(jc, lambda confirmed: fetch(
            pid, service=ctx.service(), ptsl=ctx.ptsl, template_group=group, template_name=name,
            session_parent=pl.parent, session_storage=pl.mode, mirror_parent=pl.mirror_parent,
            confirmed=confirmed, is_delivered=lambda p: is_delivered(ctx, p), with_video=bool(args.get("video", True)),
            progress=jc.progress, decide_missing=ask_missing(jc), triage=options,
            confirm_triage=confirm_triage(jc, ctx, f"{group}/{name}"), goto=start, program_start=start,
            import_options=import_opts, track_view=view, confirm_media=ask_media(jc)), *IMPORT_TEXTS)
        templates_app.remember(ctx, show, group, name)
        binder = ctx.extra.get("profile_binder")
        if binder is not None:                       # Spurprofil gilt ab jetzt für diese Session
            binder.bind(result.session_path, group, name)
        if pl.hint:
            result.warnings.append(pl.hint)
        result.warnings += preset_hint(import_opts)
        if profile is not None:
            result.warnings += [f"Spurprofil „{group}/{name}“: {x}" for x in TP.problems(profile)]
        return fetch_summary(result)
    return runner.start("holen", f"Holen: {title}", FETCH_STEPS, work, contribution=pid)


# ── Import in die offene Session ──────────────────────────────────────────────────
OPEN_PREFIX = "import.session_ablage.offene_session."
RELOCATE_HINT = {
    "kopieren": "Sauber und unabhängig: Die offene Session wird gespeichert, geschlossen, mit allen Audiodateien in "
                "den Projektordner des neuen Beitrags kopiert und dort geöffnet. Die alte bleibt unverändert.",
    "speichern_unter": "Schnell: Die Session wird per „Speichern unter“ im neuen Projektordner gespeichert. Vorhandene "
                       "Audiodateien bleiben im alten Ordner referenziert, neue landen im neuen.",
    "bleibt": "Die Session bleibt, wo sie ist; der neue Beitrag verweist in Helmut auf sie. Nicht empfohlen, wenn "
              "das alte Projekt später archiviert wird.",
}


def open_session_info(ctx: AppContext, args: dict) -> dict:
    """Angebot „In offene Session importieren“: offene Session, Vorgaben, Wahlmöglichkeiten."""
    from tonmischung.core import open_import as oi
    s = ctx.settings
    offer = bool(s.get(OPEN_PREFIX + "anbieten"))
    try:
        opened = ctx.ptsl.open_session()
    except PtslError:
        opened = None
    if opened is not None and args.get("id"):
        try:
            if same_session(ctx.helmut.get(str(args["id"])).mix.session_path, opened.path):
                opened = None                                # das ist schon die Session dieses Beitrags
        except HelmutError:
            pass

    def choices(values):
        return [{"value": v, "label": oi.LABELS[v]} for v in values]
    return {"offer": offer and opened is not None, "name": opened.name if opened else "",
            "path": opened.path if opened else "",
            "defaults": {k: s.get(OPEN_PREFIX + k) for k in ("umzug", "clips", "position", "bisheriger_beitrag")},
            "choices": {"umzug": choices(oi.RELOCATE), "clips": choices(oi.CLIPS), "position": choices(oi.POSITION),
                        "bisheriger_beitrag": choices(oi.PREVIOUS)},
            "hints": RELOCATE_HINT}


def start_fetch_open(ctx: AppContext, runner: JobRunner, args: dict) -> dict:
    """Beitrag in die offene Session holen; Fragen (fehlende Spuren, Überdecken) ans Fenster."""
    from tonmischung.core.open_import import LABELS, OpenImportOptions
    from tonmischung.workflow.fetch_open import OPEN_STEPS, fetch_into_open
    pid = str(args["id"])
    s = ctx.settings
    options = OpenImportOptions(
        relocate=args.get("umzug") or s.get(OPEN_PREFIX + "umzug"),
        clips=args.get("clips") or s.get(OPEN_PREFIX + "clips"),
        position=args.get("position") or s.get(OPEN_PREFIX + "position"),
        previous=args.get("bisheriger_beitrag") or s.get(OPEN_PREFIX + "bisheriger_beitrag"))
    errors = options.check()
    if errors:
        raise CommandError(" ".join(errors))
    c = contribution(ctx, pid)
    title, pl = c.title, placement(ctx, c)
    import_opts = import_options(ctx)

    def work(jc: JobContext) -> dict:
        def confirm_overlap(hit: dict) -> bool:
            text = ", ".join(f"{t} ({n} Clips)" for t, n in hit.items())
            return bool(jc.ask({"kind": "ueberdecken", "title": "Vorhandene Clips würden überdeckt",
                                "text": f"Die neuen Clips liegen über vorhandenen auf: {text}. Pro Tools ersetzt "
                                        "sie an diesen Stellen. Trotzdem einfügen?",
                                "options": [{"value": True, "label": "Trotzdem einfügen"},
                                            {"value": False, "label": "Abbrechen", "primary": True}]}))
        r = fetch_into_open(pid, service=ctx.service(), ptsl=ctx.ptsl,
                            options=options, session_parent=pl.parent, session_storage=pl.mode,
                            mirror_parent=pl.mirror_parent, decide_missing=ask_missing(jc, "in der offenen Session"),
                            confirm_overlap=confirm_overlap, with_video=bool(args.get("video", True)),
                            progress=jc.progress, program_start=factory.program_start(s),
                            import_options=import_opts, track_view=track_view(ctx), confirm_media=ask_media(jc))
        return {**fetch_summary(r), "warnings": list(r.warnings) + preset_hint(import_opts), "new_tracks": [],
                "open_import": True, "previous": r.previous, "previous_action": r.previous_action,
                "how": f"{LABELS[options.relocate]} · {LABELS[options.clips]} · {LABELS[options.position]}"}
    return runner.start("holen", f"In offene Session holen: {title}", OPEN_STEPS, work, contribution=pid)


# ── Triage ohne Pro Tools ─────────────────────────────────────────────────────────
def triage_report(ctx: AppContext, args: dict) -> dict:
    """AAF beurteilen (Pfad oder Projekt-ID) – derselbe Bericht wie `tonmischung triage --json`."""
    from tonmischung.triage.analyse import triage_aaf
    from tonmischung.triage.report import as_dict
    path = args.get("aaf") or ""
    if not path and args.get("id"):
        try:
            handover = ctx.helmut.handover(str(args["id"]))
        except HelmutError as e:
            raise CommandError(f"Helmut: {e}") from e
        if handover.aaf is None:
            raise CommandError("In der Übergabe fehlt das AAF")
        path = handover.aaf.path
    if not path or not os.path.exists(path):
        raise CommandError(f"AAF nicht gefunden: {path or '–'}")
    o = factory.triage_options(ctx.settings)
    try:
        r = triage_aaf(path, threshold=o.threshold, keywords=o.keywords, music_paths=o.music_paths,
                       use_track_names=o.use_track_names, use_source_names=o.use_source_names,
                       use_signal=o.use_signal)
    except (OSError, ValueError) as e:
        raise CommandError(f"Triage nicht möglich: {e}") from e
    return as_dict(r)


def register(bridge: Bridge, ctx: AppContext, runner: JobRunner | None) -> None:
    bridge.register("triage", lambda args: triage_report(ctx, args))
    if runner is not None:
        bridge.register("holen", lambda args: start_fetch(ctx, runner, args))
        bridge.register("holen_offen", lambda args: start_fetch_open(ctx, runner, args))
        bridge.register("offene_session_info", lambda args: open_session_info(ctx, args))
        bridge.register("triage_korrigieren", lambda args: triage_correct(ctx, args))
