"""Hinweise aus Namen: Quellname eines Clips und Spurname – reine Logik.

Der Quellname sagt bei Musik mehr als das Signal (Spike 02.10.2026): Kameraton
(`PAVJ1496.MOV`) und Atmo-Archiv (`AMBCnst_EXT_…`) hielt die Inhaltserkennung mit 0,97
für Musik, echte Musik heißt dagegen `Interpret - Titel.mp3`, `UPM_BTV199_36_…` oder
trägt Tonart und Tempo im Namen.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Kategorien wie im Einstellungsschema ($defs/kategorie), dazu die Sprecher-Ausnahme
OTON, ATMO, MUSIC, REST = "o_ton", "atmo", "musik", "rest"
SPEAKER = "sprecher"

# Arten von Namenshinweisen
H_MUSIC, H_LOCATION, H_LIBRARY, H_FADE = "musik", "drehort", "archiv", "blende"


@dataclass(frozen=True)
class NameHint:
    kind: str
    reason: str


# Rand eines Stichworts: kein Buchstabe davor oder danach (Ziffern erlaubt: „Musik1“,
# „OT2“). Nicht \b – der Unterstrich zählt dort als Wortzeichen (Hedwig).
def _word(word: str) -> str:
    return r"(?<![^\W\d_])" + word + r"(?![^\W\d_])"


# UCS-Kategorien (Universal Category System) der Geräusch-/Atmo-Archive, z. B.
# „AMBCnst_EXT_…“, „MACHHvac_INT_…“. MUSC (Instrumente) bewusst nicht: kein Archiv-Atmo.
UCS_CATEGORIES = frozenset("""
AIR ALRM AMB ANML ARCH AVIA BEEP BELL BIRD BLLT BOAT BRKN BUZZ CAR CART CERM CHAIN CHEM
CLOCK CLOTH COMM COMP CREA CRWD DATA DEST DIRT DOOR DRWR DSGN EARTH ELEC EQUIP EXPL FARM
FGHT FIRE FIRWRK FOLY FOOD FOOT GAME GEAR GLASS GORE GUN HAIR HORN HUM IMPCT KEYS LASR
LETHR LIQ MACH MAGC MECH METL MOTR MOVE NATR OBJ PAPR PLAS RAIN ROBT ROCK ROPE RUBR SCFI
SNOW SPRT SWSH TOOL TOYS TRAIN TRSH UI VEH VOX WATR WEAP WTHR WHSH WIND WNDW WING WOOD
""".split())
_UCS = re.compile(r"(?:^|[/\\])([A-Z]{2,6})([A-Z][a-z]{2,})_")

_MUSIC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\.(mp3|m4a|aac|ogg|flac)(?![a-z0-9])", re.I), "Musikdatei ({0})"),
    (re.compile(r"(?<!\d)\d{2,3}\s?bpm(?![a-z])", re.I), "Tempoangabe ({0})"),
    (re.compile(r"(?<![a-z])[a-g](#|b|is|es)?[\s_-]?(minor|major|moll|dur)(?![a-z])", re.I),
     "Tonart ({0})"),
    # Archivkennung: „UPM_BTV199_36_“, „ivox590_01_“, „clk157_03_“, „KPM_0123_04_“
    (re.compile(r"(?:^|[/\\])(?:[A-Za-z]{2,6}_)?[A-Za-z]{2,6}\d{2,4}_\d{1,3}_"), "Archivkennung ({0})"),
    (re.compile(_word("main[_ ]track"), re.I), "Musiktitel ({0})"),
)
# „Interpret - Titel.wav“ – schwächer: zählt erst nach den Drehort-Regeln
_ARTIST_TITLE = re.compile(r"^[^/\\]{2,40}\s-\s[^/\\]{2,80}\.(wav|aif+)", re.I)

# Drehort: Kamera-/Rohmaterial. Nicht .mxf – Avid legt jede Essenz als MXF ab, auch Musik.
# ANNAHME: „NACHNAME VORNAME1234“ ist eine Kamera-Quelle (Sender-Material, Stromnetze 02.10.2026).
_LOCATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\.(mov|mp4|mts|m2ts|avi|r3d|braw|crm)(?![a-z0-9])", re.I), "Kameradatei ({0})"),
    (re.compile(r"rohmaterial", re.I), "Rohmaterial"),
    (re.compile(r"^[A-ZÄÖÜ]{3,}\s[A-ZÄÖÜ]{3,}\d{3,}"), "Kamera-Quelle (Name + Nummer)"),
)


def _clean(name: str) -> str:
    return (name or "").strip()


def source_hint(names: Iterable[str], *, music_keywords: Sequence[str] = (),
                music_paths: Sequence[str] = (), oton_keywords: Sequence[str] = (),
                atmo_keywords: Sequence[str] = ()) -> NameHint | None:
    """Hinweis aus den Quellnamen eines Clips (Mob-Namen, Dateinamen, Pfade).

    Vorrang: Überblendung, Musikpfad, Geräusch-/Atmo-Archiv, Musik, Drehort, „Interpret – Titel“.
    """
    names = [n for n in (_clean(x) for x in names) if n]
    if any(n.lower() == "fade" for n in names):
        return NameHint(H_FADE, "Überblendung")
    for n in names:
        for p in music_paths:
            if p and p.lower() in n.lower():
                return NameHint(H_MUSIC, f"Musikpfad {p}")
    for n in names:
        m = _UCS.search(n)
        if m and m.group(1) in UCS_CATEGORIES:
            return NameHint(H_LIBRARY, f"Geräusch-/Atmo-Archiv ({m.group(1)})")
        for w in atmo_keywords:
            if w and re.search(_word(re.escape(w)), n, re.I):
                return NameHint(H_LIBRARY, f"Stichwort „{w}“")
    for n in names:
        for rx, text in _MUSIC_PATTERNS:
            m = rx.search(n)
            if m:
                return NameHint(H_MUSIC, text.format(m.group(0).strip(" _/\\")))
        for w in music_keywords:
            if w and re.search(_word(re.escape(w)), n, re.I):
                return NameHint(H_MUSIC, f"Stichwort „{w}“")
    for n in names:
        for rx, text in _LOCATION_PATTERNS:
            m = rx.search(n)
            if m:
                return NameHint(H_LOCATION, text.format(m.group(0)))
        for w in oton_keywords:
            if w and re.search(_word(re.escape(w)), n, re.I):
                return NameHint(H_LOCATION, f"Stichwort „{w}“")
    for n in names:
        if _ARTIST_TITLE.search(n):
            return NameHint(H_MUSIC, "Interpret – Titel")
    return None


_SUFFIX = re.compile(r"\.\d{2}$")          # Pro-Tools-Endung „A1.01“


def base_track_name(name: str) -> str:
    """Spurname ohne Pro-Tools-Endung „.01“ (ANNAHME: zählt nicht zum Namen)."""
    return _SUFFIX.sub("", _clean(name))


def is_speaker_track(name: str) -> bool:
    """„Spr 1“, „Spr 2“, „Spr 3“ – Ausnahme: per Name auf die gleichnamige Spur (Jens)."""
    return re.fullmatch(r"spr [123]", base_track_name(name), re.I) is not None


def track_keyword(name: str, keywords: dict[str, Sequence[str]]) -> str | None:
    """Kategorie aus Stichwörtern im Spurnamen (Einstellung schluesselwoerter)."""
    n = base_track_name(name)
    for category in (MUSIC, OTON, ATMO):
        for w in keywords.get(category, ()):
            if w and re.search(_word(re.escape(w)), n, re.I):
                return category
    return None


# Doppelfassungen (Premiere): „Audio 3_FX“ / „Audio 3_No FX“ – Hedwig, hedwig_fassungen
_WITHOUT = re.compile(r"(?<![a-z0-9])(no|ohne)[\s_-]*fx(?![a-z0-9])", re.I)
_WITH = re.compile(r"(?<![a-z0-9])fx(?![a-z0-9])", re.I)


def _version(name: str) -> str | None:
    if _WITHOUT.search(name):
        return "ohne"
    if _WITH.search(name):
        return "mit"
    return None


def _stem(name: str) -> str:
    n = _WITH.sub(" ", _WITHOUT.sub(" ", name.lower()))
    return re.sub(r"[\s_-]+", " ", n).strip()


def second_versions(track_names: Sequence[str]) -> dict[str, str]:
    """{zweite Fassung: erste Fassung} – gleicher Name bis auf _FX / _No FX.

    ANNAHME: Die `_FX`-Fassung (so hat der Cutter es gehört) ist die erste, `_No FX`
    kommt ab A11 (Jens: nichts weglassen).
    """
    result = {}
    for a in track_names:
        for b in track_names:
            if a != b and _stem(a) == _stem(b) and _version(a) == "ohne" and _version(b) == "mit":
                result[a] = b
    return result
