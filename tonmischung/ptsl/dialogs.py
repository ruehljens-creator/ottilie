"""Offene Pro-Tools-Dialoge erkennen – geklickt wird nur in drei bekannten: „Missing Files“ (nach Einstellung),
„Missing AAX Plugins“ (OK, die Liste wird gemeldet) und das Dashboard (Abbrechen), alles andere bleibt dem Mischer.

Ein Dialog („Missing AAX Plugins“, Speicherfragen …) blockiert PTSL-Befehle, bis jemand ihn
bestätigt (live 02.10.2026: OpenSession hing an „Missing AAX Plugins“). Die App nennt den Dialog,
statt nur „keine Antwort“ zu melden. Lehre aus der produktiven PunchBuddy-Linie: Fensterliste
ansehen statt blind klicken.

Live 03.10.2026: Der Dialog „Missing Files“ nach CreateSession lag auf einem anderen Space – die
Liste der sichtbaren Fenster zeigte ihn nicht. Pro Tools führte PTSL-Befehle *innerhalb* des
Dialogs aus (Import, Speichern) und stürzte bei CloseSession ab (Access Violation). Deshalb werden
die Fenster aller Spaces gelesen, und vor Befehlen, die eine Session schließen oder anlegen, wird
geprüft (`PtslClient.guard_dialogs`).
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable, Mapping


MODAL_LEVEL = 8          # kCGModalPanelWindowLevel: Dialoge und Meldungen; schwebende Paletten (Transport) = 3
# Ab Ebene 101 liegen Popup-Menüs, Tooltips und Overlays (kCGPopUpMenuWindowLevel …) – keine Dialoge. Live 04.10.2026:
# ein Pro-Tools-Fensterchen 120×18 auf Ebene 103 (nicht einmal auf dem Bildschirm) hielt das Anlegen 10 Minuten auf.
MAX_DIALOG_LEVEL = 100
MIN_W, MIN_H = 150, 40   # kleiner ist kein Dialog (Fortschritt ~475×110, Meldungen größer)
UNNAMED = "Meldung von Pro Tools"


def dialog_names(windows: Iterable[Mapping]) -> list[str]:
    """Namen der modalen Pro-Tools-Fenster (Dialoge, Meldungen) aus einer Quartz-Fensterliste.

    Live 03.10.2026: Die Meldung „Could not complete your request because Assertion …“ hat keinen Fenstertitel – sie
    zählt trotzdem (als „Meldung von Pro Tools“). Schwebende Paletten (Ebene 3), Popups/Tooltips (≥ 101), die
    Menüleiste (≥ 1000) und winzige Fenster nicht."""
    names: list[str] = []
    for w in windows:
        level = int(w.get("kCGWindowLayer") or 0)
        if "Pro Tools" not in str(w.get("kCGWindowOwnerName", "")) or not MODAL_LEVEL <= level < MAX_DIALOG_LEVEL:
            continue
        if float(w.get("kCGWindowAlpha", 1) or 0) <= 0:
            continue
        bounds = w.get("kCGWindowBounds") or {}
        if bounds and (float(bounds.get("Width", 0) or 0) < MIN_W or float(bounds.get("Height", 0) or 0) < MIN_H):
            continue
        name = str(w.get("kCGWindowName") or "").strip() or UNNAMED
        if name not in names:
            names.append(name)
    return names


SCREEN_CAPTURE_HINT = ("Ohne das Recht „Bildschirmaufnahme“ erkennt Ottilie Pro-Tools-Dialoge nur als „Meldung von "
                       "Pro Tools“ – „Missing Files“ und „Missing AAX Plugins“ werden dann nicht beantwortet. "
                       "Systemeinstellungen → Datenschutz & Sicherheit → Bildschirm- und Systemaudioaufnahme → Ottilie "
                       "einschalten.")


def screen_capture_allowed() -> bool | None:
    """Darf Ottilie Fenstertitel lesen (Recht „Bildschirmaufnahme“, ohne Rückfrage geprüft)? None: nicht feststellbar.
    Ohne das Recht liefert macOS keine Titel – jeder Dialog wäre „titellos“ (Review B24)."""
    try:
        import Quartz
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:  # noqa: BLE001 – ohne pyobjc oder älteres macOS
        return None


def pro_tools_dialogs() -> list[str]:
    """Offene Pro-Tools-Dialoge auf allen Spaces; leer, wenn nichts erkennbar ist."""
    try:
        import Quartz
    except ImportError:                                   # ohne pyobjc: nichts erkennbar
        return []
    try:
        windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll,
                                                    Quartz.kCGNullWindowID) or []
    except Exception:  # noqa: BLE001 – Diagnose darf nie stören
        return []
    return dialog_names(windows)


# ── Fortschrittsbalken von echten Meldungen unterscheiden (live 04.10.2026) ──────────
# Beim Anlegen aus dem Template zeigt Pro Tools mehrmals kurz ein titelloses Fenster (~475×110, Ebene 8) – den
# Fortschrittsbalken. Solange er läuft, antwortet Pro Tools nicht auf Abfragen über System Events (6,4 s gemessen).
# Eine echte Meldung (z. B. die Assertion) hat einen Knopf, und Pro Tools antwortet, weil es auf den Mischer wartet.
ALERT, PROGRESS = "meldung", "fortschritt"
UNNAMED_SCRIPT = '''tell application "System Events" to tell process "Pro Tools"
  repeat with w in windows
    set nm to ""
    try
      set nm to name of w as text
    end try
    if nm is "" or nm is "missing value" then
      if (count of buttons of w) > 0 then return "meldung"
    end if
  end repeat
  return "fortschritt"
end tell'''


def _osascript_quick(script: str, timeout_s: float = 3.0) -> tuple[int, str]:
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return 124, ""
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return r.returncode, (r.stdout or r.stderr or "").strip()


def unnamed_kind(run: Callable[[str], tuple[int, str]] = _osascript_quick) -> str:
    """Titelloses Pro-Tools-Fenster: „meldung“ (hat Knöpfe – bestätigen) oder „fortschritt“ (keine Knöpfe oder Pro
    Tools antwortet nicht, weil es arbeitet)."""
    rc, out = run(UNNAMED_SCRIPT)
    return ALERT if rc == 0 and out == ALERT else PROGRESS


# ── „Missing Files“ beantworten (Jens, 03.10.2026) ───────────────────────────
# Dem AAF fehlt die Verknüpfung zum Video; Pro Tools meldet die Datei als fehlend. Jens: „Das File muss
# automatisch gesucht werden – das kannst du in dem Fall selbst anhaken“, als einstellbare Vorgabe
# (`import.import_vorschau.fehlende_dateien`). Geklickt wird nur in genau diesem Dialog und nur auf die
# erwarteten Knöpfe (live 03.10.2026: Optionsfelder „Automatically Find & Relink“, „Manually Find & Relink“,
# „Skip All“, Knopf „OK“); alles andere bleibt dem Mischer.
MISSING_FILES = "Missing Files"
AUTO, SKIP, ASK = "automatisch_suchen", "alle_ueberspringen", "selbst_bestaetigen"
MISSING_FILES_CHOICES = {AUTO: "Automatically Find & Relink", SKIP: "Skip All"}


def _osascript(script: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return r.returncode, (r.stdout or r.stderr or "").strip()


def missing_files_script(option: str) -> str:
    return f'''tell application "System Events" to tell process "Pro Tools"
  if not (exists window "{MISSING_FILES}") then return "fehlt"
  set w to window "{MISSING_FILES}"
  set info to ""
  try
    set info to value of (first static text of w whose value contains "missing")
  end try
  set rb to radio button "{option}" of w
  click rb
  delay 0.3
  if (value of rb) is not 1 then return "nicht gesetzt"
  click button "OK" of w
  return "ok|" & info
end tell'''


def answer_missing_files(choice: str, run: Callable[[str], tuple[int, str]] = _osascript) -> str | None:
    """Wählt im Dialog „Missing Files“ die eingestellte Antwort und bestätigt. Rückgabe: Meldung für das
    Protokoll, oder None, wenn nichts geklickt wurde (Vorgabe „selbst bestätigen“, Dialog anders, Fehler)."""
    option = MISSING_FILES_CHOICES.get(choice)
    if option is None:
        return None
    rc, out = run(missing_files_script(option))
    if rc != 0 or not out.startswith("ok"):
        return None
    what = "automatisch gesucht und verknüpft" if choice == AUTO else "alle übersprungen"
    return f"Pro Tools meldete {missing_summary(out.partition('|')[2])} – {what} (Einstellung)"


def missing_summary(text: str) -> str:
    """„0 Audio File(s), 1 Video File(s), … are missing“ → „1 fehlende Videodatei“."""
    kinds = (("Audio File", "Audiodatei", "Audiodateien"), ("Video File", "Videodatei", "Videodateien"),
             ("Rendered Elastic Audio File", "Elastic-Audio-Datei", "Elastic-Audio-Dateien"),
             ("Rendered Frozen Audio File", "eingefrorene Audiodatei", "eingefrorene Audiodateien"))
    parts = []
    for en, one, many in kinds:
        m = re.search(rf"(\d+) {en}\(s\)", text or "")
        if m and int(m.group(1)):
            n = int(m.group(1))
            parts.append(f"{n} fehlende {one if n == 1 else many}")
    return ", ".join(parts) or "fehlende Dateien"


# ── „Missing AAX Plugins“ bestätigen (Jens, 13.10.2026) ───────────────────────
# Beim Öffnen einer Session aus einem anderen Raum fehlen oft Plugins; Pro Tools hält OpenSession an, bis jemand OK
# klickt (live 13.10.2026: über fünf Minuten). Der Dialog hat nur OK (dazu „Save Report…“, „Avid Link…“) – Ottilie
# klickt OK und meldet die Liste als Hinweis.
MISSING_PLUGINS = "Missing AAX Plugins"
MISSING_PLUGINS_SCRIPT = f'''tell application "System Events" to tell process "Pro Tools"
  if not (exists window "{MISSING_PLUGINS}") then return "fehlt"
  set w to window "{MISSING_PLUGINS}"
  if not (exists button "OK" of w) then return "anders"
  set info to ""
  set parts to entire contents of w
  repeat with e in parts
    try
      if role of e is "AXStaticText" then set info to info & (value of e as text) & linefeed
    end try
  end repeat
  click button "OK" of w
  return "ok|" & info
end tell'''
_PLUGIN_ROW = re.compile(r"^(.+?) \((\d+)\)$")


def plugin_list(text: str) -> list[str]:
    """Zeilen „     FabFilter Pro-C 2 (3)“ aus dem Dialogtext → „FabFilter Pro-C 2 (3)“, ohne Doppelte (die Tabelle
    erscheint zweimal, als Zeilen und als Spalte) und ohne Kopf- und Fußtext."""
    out: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if _PLUGIN_ROW.match(line) and line not in out:
            out.append(line)
    return out


def answer_missing_plugins(run: Callable[[str], tuple[int, str]] = _osascript) -> str | None:
    """Bestätigt „Missing AAX Plugins“ mit OK. Rückgabe: Hinweis mit den fehlenden Plugins, oder None, wenn nichts
    geklickt wurde (Dialog fehlt oder sieht anders aus)."""
    rc, out = run(MISSING_PLUGINS_SCRIPT)
    if rc != 0 or not out.startswith("ok"):
        return None
    names = plugin_list(out.partition("|")[2])
    return ("Pro Tools meldete fehlende Plugins" + (f": {', '.join(names)}" if names else "")
            + " – bestätigt; die Spuren spielen ohne diese Plugins")


# ── Startfenster (Dashboard) schließen (Jens, 03.10.2026) ─────────────────────
# Nach jedem Start zeigt Pro Tools das Dashboard („Pro Tools Studio 2026.4.1“: New, Open, Create, Cancel …). Es ist
# modal; Session-Befehle der App warteten darauf. Die App schließt es mit „Cancel“ – nur wenn Titel und Knöpfe
# passen und nur in dem Moment, in dem sie selbst eine Session öffnen, anlegen oder schließen will.
DASHBOARD_CLOSED = "Startfenster von Pro Tools geschlossen"
_DASHBOARD = re.compile(r"^(Dashboard|Pro Tools(?: \| )?(?: ?(?:Studio|Ultimate|Artist|Intro|First|Carbon))? "
                        r"\d{4}\.\d+(?:\.\d+)?)$")


def is_dashboard(name: str) -> bool:
    return bool(_DASHBOARD.match(name.strip()))


def dashboard_script(name: str) -> str:
    title = name.replace('"', "")
    return f'''tell application "System Events" to tell process "Pro Tools"
  if not (exists window "{title}") then return "fehlt"
  set w to window "{title}"
  if not ((exists button "Create" of w) and (exists button "Cancel" of w)) then return "anders"
  click button "Cancel" of w
  return "ok"
end tell'''


def close_dashboard(name: str, run: Callable[[str], tuple[int, str]] = _osascript) -> str | None:
    rc, out = run(dashboard_script(name))
    return DASHBOARD_CLOSED if rc == 0 and out == "ok" else None


def make_handler(choice: Callable[[], str], run: Callable[[str], tuple[int, str]] = _osascript
                 ) -> Callable[[list[str]], str | None]:
    """Antwort auf bekannte Dialoge für `PtslClient.dialog_handler`; `choice` liest die Einstellung."""
    def handle(names: list[str]) -> str | None:
        for name in names:
            if is_dashboard(name):
                done = close_dashboard(name, run)
                if done:
                    return done
        if MISSING_PLUGINS in names:
            done = answer_missing_plugins(run)
            if done:
                return done
        if MISSING_FILES in names:
            return answer_missing_files(str(choice() or ASK), run)
        return None
    return handle
