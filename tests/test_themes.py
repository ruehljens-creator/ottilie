"""Farbschemata (Jens 08.10.2026): bisher, Ottilie, hoher Kontrast, rot-grün-sicher – vollständig und barrierefrei;
Ottilie hell seit 09.10.2026.

Jens: „Die ganze Barrierefreiheit muss natürlich erhalten bleiben.“ Deshalb: jedes Schema setzt jede Farbrolle, die
Stylesheets benutzen nur Rollen, und die Kontraste nach WCAG reichen (Text 4,5:1, Fokus und Bedienelemente 3:1).
"""
import json
import pathlib
import re

import pytest

UI = pathlib.Path(__file__).resolve().parents[1] / "tonmischung" / "app" / "ui"
CSS = (UI / "themes.css").read_text(encoding="utf-8")
SCHEMES = ("bisher", "ottilie", "ottilie_hell", "kontrast", "rotgruen")


def _blocks() -> dict[str, dict[str, str]]:
    out = {}
    for name, body in re.findall(r':root(?:\[data-theme="(\w+)"\])?[^{]*\{([^}]*)\}', CSS):
        roles = dict(re.findall(r"--([\w-]+):\s*([^;]+);", body))
        if roles:
            out[name or "bisher"] = {k: v.strip() for k, v in roles.items()}
    return out


def _lum(h: str) -> float:
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4   # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_jedes_schema_setzt_jede_rolle():
    blocks = _blocks()
    assert set(blocks) == set(SCHEMES)
    roles = set(blocks["bisher"])
    for name in SCHEMES:
        assert set(blocks[name]) == roles, f"{name}: fehlt {roles - set(blocks[name])}, zu viel {set(blocks[name]) - roles}"


def test_stylesheets_nur_mit_rollen():
    roles = set(_blocks()["bisher"])
    for f in ("app.css", "layout.css", "themes.css", "app.js"):
        text = (UI / f).read_text(encoding="utf-8")
        used = set(re.findall(r"var\(--([\w-]+)\)", text))
        assert used <= roles, f"{f}: unbekannte Rollen {used - roles}"
        if f.endswith(".css") and f != "themes.css":
            assert not re.findall(r"#[0-9A-Fa-f]{3,6}\b", re.sub(r"/\*.*?\*/", "", text, flags=re.S)), f"{f}: feste Farbe"


TEXT = [("text", "app"), ("text", "panel"), ("text", "nav"), ("text", "raise"), ("text", "sel"), ("text", "field"),
        ("text-2", "app"), ("text-2", "panel"), ("muted", "app"), ("muted", "panel"), ("muted", "nav"), ("muted-2", "app"),
        ("on-accent", "accent"), ("on-accent", "accent-hover"), ("ok-text", "ok-bg"), ("ok-banner-text", "ok-bg"),
        ("warn-text", "warn-bg"), ("warn-pill-text", "warn-bg"), ("info-text", "info-bg"),
        ("accent-text", "accent-soft-2"), ("accent-text", "accent-soft"), ("bad-text", "app"), ("bad", "app"),
        ("white", "ok-strong"), ("white", "bad-strong"), ("white", "warn-strong"),
        ("on-raise", "raise")]
GRAPHIC = [("focus", "app"), ("focus", "panel"), ("ctrl-border", "field"), ("ctrl-border", "app"), ("room-free", "app"),
           ("room-busy", "app"), ("room-act", "app"), ("room-off", "app"), ("act", "app")]


@pytest.mark.parametrize("name", SCHEMES)
def test_kontraste_reichen(name):
    v = _blocks()[name]
    low = [f"{a} auf {b}: {ratio(v[a], v[b]):.2f}" for a, b in TEXT if ratio(v[a], v[b]) < 4.5]
    low += [f"{a} auf {b}: {ratio(v[a], v[b]):.2f}" for a, b in GRAPHIC if ratio(v[a], v[b]) < 3.0]
    assert low == []


def test_rot_gruen_ohne_rot_gruen_paar_und_mit_formen():
    v = _blocks()["rotgruen"]
    assert v["room-free"] == "#56B4E9" and v["room-busy"] == "#E69F00" and v["room-act"] == "#CC79A7"   # Okabe-Ito
    assert ':root[data-theme="rotgruen"] .dot.st-mischt' in CSS and ':root[data-theme="rotgruen"] .dot.st-offline' in CSS
    assert ':root[data-theme="kontrast"] .dot.st-mischt' in CSS and "border-width: 2px" in CSS


def test_einstellung_je_nutzer_standard_ottilie():
    from tonmischung.config.settings import Settings
    schema = json.loads((UI.parents[1] / "config" / "einstellungen.schema.json").read_text(encoding="utf-8"))
    field = schema["properties"]["persoenlich"]["properties"]["farbschema"]
    assert field["enum"] == ["ottilie", "ottilie_hell", "bisher", "kontrast", "rotgruen"] and len(field["x-labels"]) == 5
    s = Settings(local_path=None)
    assert s.get("persoenlich.farbschema") == "ottilie" and s.is_user_path("persoenlich.farbschema")


def test_schema_wird_geladen():
    index = (UI / "index.html").read_text(encoding="utf-8")
    assert index.index("themes.css") > index.index("layout.css")          # zuletzt: Formen und Zusatzregeln gewinnen


def test_ottilie_hell_ist_hell_und_gewaehlte_reiter_lesbar():
    """Jens 09.10.2026: „Ottilie hell“ – heller Grund, dunkle Schrift; gewählte Reiter und Seiten nicht mehr fest Weiß."""
    v = _blocks()["ottilie_hell"]
    assert _lum(v["app"]) > 0.8 and _lum(v["text"]) < 0.05 and v["accent"] == _blocks()["ottilie"]["accent"]
    app = (UI / "app.css").read_text(encoding="utf-8")
    assert ".side-item.on{background:var(--raise);color:var(--on-raise)" in app
    assert ".tab.on{background:var(--raise);color:var(--on-raise)" in app
