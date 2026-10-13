"""Barrierefreiheit der Oberfläche (WCAG 2.2 AA) – was sich ohne Browser prüfen lässt.

Kontraste der Farbtabelle (Text ≥ 4,5 : 1, Rahmen/Grafik ≥ 3 : 1) und Auszeichnung im
Markup von app.js (Dialoge, Beschriftungen, Live-Regionen). Was nur live geht (VoiceOver,
Tastatur im Fenster), wird von Hand abgenommen.
"""
import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "tonmischung" / "app" / "ui"
JS = (UI / "app.js").read_text(encoding="utf-8")
CSS = (UI / "app.css").read_text(encoding="utf-8") + (UI / "layout.css").read_text(encoding="utf-8")
HTML = (UI / "index.html").read_text(encoding="utf-8")


def _lum(h):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


THEMES = (UI / "themes.css").read_text(encoding="utf-8")
SCHEMES = ("bisher", "ottilie", "ottilie_hell", "kontrast", "rotgruen")   # Farbschemata (Jens 08./09.10.2026)


def roles(scheme):
    """Farbrollen eines Schemas aus themes.css – die Stylesheets benutzen nur diese."""
    for name, body in re.findall(r':root(?:\[data-theme="(\w+)"\])?[^{]*\{([^}]*)\}', THEMES):
        if (name or "bisher") == scheme and "--app:" in body:
            return {k: v.strip() for k, v in re.findall(r"--([\w-]+):\s*([^;]+);", body)}
    raise KeyError(scheme)


BACKGROUNDS = ["app", "panel", "nav", "topbar", "field", "row-hover", "sel"]


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("text", ["text", "text-2", "muted", "muted-2"])
def test_textfarben_aa(scheme, text):
    r = roles(scheme)
    assert min(contrast(r[text], r[bg]) for bg in BACKGROUNDS) >= 4.5


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("fg, bg", [("ok-text", "ok-bg"), ("warn-pill-text", "warn-bg"), ("accent-text", "accent-soft-2"),
                                    ("warn-text", "warn-bg"), ("info-text", "info-bg"), ("on-accent", "accent"),
                                    ("white", "lane-ot"), ("lane-atmo-text", "lane-atmo"), ("lane-music-text", "lane-music"),
                                    ("white", "lane-rest"), ("white", "bad-toast")])
def test_plaketten_und_clips(scheme, fg, bg):
    r = roles(scheme)
    assert contrast(r[fg], r[bg]) >= 4.5


def test_ersatzfarben_stehen_im_stil():
    assert ".nav-head, .row-head, .grp, .prog.wait, table.clips th { color: var(--muted-2); }" in CSS
    assert ".toast.err { background: var(--bad-toast);" in CSS
    assert roles("bisher")["muted-2"] == "#949AA3" and roles("bisher")["bad-toast"] == "#B42318"   # bisher unverändert


@pytest.mark.parametrize("scheme", SCHEMES)
def test_rahmen_und_fokus_mindestens_3_zu_1(scheme):
    assert re.search(r"\.btn, \.inp, \.chip, \.opt, \.seg, \.mv, select\.inp \{ border-color: var\(--ctrl-border\)", CSS), \
        "Rahmenfarbe für Bedienelemente fehlt in layout.css"
    r = roles(scheme)
    for bg in BACKGROUNDS + ["raise"]:
        assert contrast(r["ctrl-border"], r[bg]) >= 3.0
    assert contrast(r["focus"], r["app"]) >= 3.0                          # Fokusrahmen
    assert ":focus-visible" in CSS and "prefers-reduced-motion" in CSS


def test_seite_und_live_regionen():
    assert '<html lang="de">' in HTML
    assert 'aria-live="polite"' in HTML and 'aria-live="assertive"' in HTML


def test_dialoge_sind_ausgezeichnet():
    frame = JS[JS.index("function dialogFrame("):][:400]          # ein Rahmen für alle Dialoge
    assert 'aria-modal="true"' in frame and 'aria-labelledby="dlg-title"' in frame and '"alertdialog" : "dialog"' in frame
    assert JS.count("return dialogFrame(`") >= 8 and 'class="overlay"' not in JS.replace(frame, "")
    for body in re.findall(r"return dialogFrame\(`(.*?)`(?:, \{[^}]*\})?\);", JS, flags=re.S):
        assert 'id="dlg-title"' in body and 'id="dlg-text"' in body


def test_eingaben_haben_namen():
    for tag in re.findall(r"<(?:input|select)\b[^>]*>", JS):
        if 'type="checkbox"' in tag and "${common}" not in tag:
            continue                                       # stecken in <label class="chk">
        if "${common}" in tag:                             # Tabellenzellen der Einstellungen
            assert re.search(r"const lab = `aria-label=", JS) and "${lab}`" in JS
            continue
        ident = re.search(r'id="([^"]+)"', tag)
        named = "aria-label=" in tag or (ident and f'for="{ident.group(1)}"' in JS)
        assert named, f"Eingabe ohne Beschriftung: {tag[:80]}"


def test_radios_und_fortschritt():
    for tag in re.findall(r'<button[^>]*role="radio"[^>]*>', JS):
        assert "aria-checked=" in tag
    assert 'role="progressbar"' in JS and "aria-valuenow" in JS
    assert "aria-current" in JS                          # Seitenleiste, Schritte, Liste


def test_escape_in_rueckfragen_heisst_abbrechen():
    """Review 09.10.2026: Escape wählte die erste Nicht-Hauptoption – bei „Clips überdeckt“ also „Trotzdem einfügen“."""
    esc_block = JS[JS.index('if (ev.key === "Escape")'):][:900]
    assert "o.value === false" in esc_block and "!o.primary" not in esc_block
    assert "data-default-focus" in JS and 'root.querySelector("[data-default-focus]")' in JS


def test_nutzerknoepfe_kollidieren_nicht_mit_ablaufmodus():
    """Review 09.10.2026: `data-mode` an den Nutzerknöpfen landete im Ablauf-Handler (TypeError, ohne Funktion)."""
    assert 'data-act="usr-edit" data-mode=' not in JS and 'data-usrmode="umbenennen"' in JS
    assert "if (t.dataset.mode && state.flow)" in JS


def test_veraltete_details_und_dialoge_am_richtigen_beitrag():
    """Review A14: eine späte Detail-Antwort darf den gewählten Beitrag nicht ersetzen; Freigeben, Duplizieren und
    Sicherungen arbeiten mit dem Beitrag, für den der Dialog geöffnet wurde."""
    load = JS[JS.index("async function loadDetail()"):][:300]
    assert "if (state.selected === id) state.detail = d" in load
    assert "inert aria-busy" in JS and ".detail.stale" in CSS
    for act in ('"release-ok"', '"dup-ok"', '"bak-folder"'):
        block = JS[JS.index(f"act === {act}"):][:500]
        assert "state.detail.id" not in block.split("else if")[0], act
    assert JS.count('kind: "release", id: state.detail.id') == 2 and "state.detail.id === j.contribution" in JS


def test_doppelklick_loest_nicht_zweimal_aus():
    """Review A15: solange die Aktion eines Knopfs läuft, bleiben weitere Klicks darauf ohne Wirkung."""
    listener = JS[JS.index("const runningClicks = new Set()"):][:600]
    assert "runningClicks.has(key)) return" in listener and "finally { if (key) runningClicks.delete(key); }" in listener
    buttons = re.findall(r"<button\b[^>]*>", JS)
    assert buttons and not [b for b in buttons if "data-act=" in b and "data-k=" not in b]   # jeder Knopf geschützt


def test_breite_nach_dem_fenster_nicht_nach_dem_inhalt():
    """Review B30: `body` zentrierte die App als Flex-Element – war sie breiter als das Fenster (Zoom), wurde links
    die Seitenleiste abgeschnitten."""
    assert "justify-content:center" not in CSS.replace(" ", "") or "#stage" not in CSS
    assert ".app { height: 100vh; width: 100%;" in CSS and "overflow-y: auto" in CSS.split(".nav {")[1].split("}")[0]


def test_warnungen_einmal_ansagen_und_fokus_zurueck():
    """Review B31: role="alert" wurde bei jedem Neuzeichnen erneut vorgelesen; nach Dialogen war der Fokus weg."""
    assert not re.search(r'<[^>]*role="alert"', JS) and "function announceAlerts" in JS and JS.count("data-alert") >= 8
    assert "state.returnFocus && document.querySelector" in JS


def test_takt_und_nutzerwechsel_in_den_einstellungen():
    """Review B32: Nutzerwechsel lud die Einstellungen nicht neu; der Takt zeichnete mitten in Eingaben."""
    assert "state.settings.areas = null; await openSettings(state.settings.sectionId)" in JS
    assert "typing.value !== typing.defaultValue" in JS and "!state.flow && !state.settings" in JS


def test_triage_frage_nur_im_eigenen_ablauf():
    """Review B33: die Triage-Frage stellte jeden offenen Ablauf auf „triage“; „Zur Frage“ führte im Kreis."""
    assert "state.flow && state.flow.jobId !== j.id" in JS and "function flowForJob" in JS
    open_job = JS[JS.index('act === "open-job"'):][:200]
    assert "flowForJob(state.job)" in open_job and "state.selected" not in open_job


def test_verwerfen_und_liste_leeren_nur_mit_rueckfrage():
    """Jens 09.10.2026: Nachträge verwerfen und „Liste leeren“ – je mit Rückfrage, vorgewählt „Abbrechen“."""
    confirm = JS[JS.index('if (g.kind === "confirm")'):][:600]
    assert 'data-act="close" data-default-focus>Abbrechen' in confirm and "alert: true" in confirm
    assert 'askConfirm(t, "Nachtrag verwerfen"' in JS and 'askConfirm(t, "Alle Nachträge verwerfen"' in JS
    assert 'askConfirm(t, "Liste leeren"' in JS and 'item("history", "Gemachte Mischungen")' in JS
    assert '<caption class="sr-only">Gemachte Mischungen</caption>' in JS


def test_audio_verschieben_als_spurpaare():
    """Jens 09.10.2026: wie in PunchBuddy – je Zeile Quelle → Ziel als Auswahl, „−“ je Paar, „+ Spurpaar“."""
    block = JS[JS.index("function moveBlock()"):][:2400]
    assert 'aria-label="Quelle, Paar ${i + 1}"' in block and 'aria-label="Ziel, Paar ${i + 1}"' in block
    assert 'aria-label="Paar ${i + 1} entfernen"' in block and "+ Spurpaar" in block and "Spuren aus Pro Tools lesen" in block
    assert '"aufnahme.audio_verschieben.quellspuren"' in JS and '"aufnahme.audio_verschieben.zielspuren"' in JS
