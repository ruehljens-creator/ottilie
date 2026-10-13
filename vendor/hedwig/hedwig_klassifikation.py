#!/usr/bin/env python3
"""Inhaltsbasierte Erkennung: Was ist auf dieser Spur — Sprache, Atmo, Musik?

Warum das sein muss: Von acht echten Sender-AAFs tragen **sechs gar keine
Spurnamen**. Eine Zuordnung ueber Namen (A1 = O-Ton, A3 = Atmo) greift dort
nicht, und selbst wo Namen stehen, sind sie pro Haus und Produktion anders.
Hedwig muss also hoeren, was da liegt.

Kein Modell, sondern messbare Eigenschaften des Signals — bewusst keine festen
Pegelschwellen, sondern Merkmale, die beschreiben, wie ein Signal *aufgebaut*
ist.

Der entscheidende Punkt, den die erste Fassung uebersehen hat: **Musik
moduliert im selben Band wie Sprache.** Ein Beat bei 120 bpm sind 2 Hz,
Achtel sind 4 Hz — mitten im Silbenbereich. Der blosse Energieanteil in
2–8 Hz trennt Sprache und Musik deshalb *nicht*. Was sie trennt, ist die
**Form** dieser Modulation:

* Musik ist **periodisch** — ein Takt erzeugt im Modulationsspektrum eine
  scharfe Linie und in der Autokorrelation der Anschlagshuellkurve eine
  wiederkehrende Spitze.
* Sprache ist **unregelmaessig** — Silben schwanken staendig in der Laenge,
  das ergibt einen breiten Huegel ohne Linie.

Dazu kommen zwei weitere Musikmarker, die Sprache prinzipiell nicht haben
kann: Toene liegen auf einem **Halbtonraster**, und sie werden **gehalten**.
Sprache gleitet dauernd; ein Vokal steht keine 300 ms still.

Die Merkmale sind bewusst offen zugaenglich (`merkmale()`), damit der
Expertenmodus sie zeigen und der Nutzer die Entscheidung ueberstimmen kann.

Laufzeit: Fuer die Beurteilung wird auf 16 kHz heruntergesetzt und hoechstens
eine begrenzte Menge klingendes Material betrachtet (`MAX_SEKUNDEN`). Damit
kostet eine Spur ungefaehr gleich viel, egal wie lang der Beitrag ist.
"""
from __future__ import annotations

import numpy as np

TYPEN = ("sprache", "atmo", "musik", "stille")

ANALYSE_RATE = 16000     # fuer alle Merkmale ausreichend, drittelt die Rechenzeit
MAX_SEKUNDEN = 45.0      # mehr aendert das Urteil nicht mehr, kostet aber Zeit
ENV_RATE = 100.0         # Huellkurvenrate (hop = 10 ms)


# --- Vorbereitung -----------------------------------------------------------

def _tiefpass_dezimieren(x: np.ndarray, sr: int, ziel: int = ANALYSE_RATE
                         ) -> tuple[np.ndarray, int]:
    """Setzt das Signal auf `ziel` herunter (mit Anti-Alias-Filter).

    Alle Merkmale hier brauchen hoechstens 8 kHz Bandbreite. Das Heruntersetzen
    kostet einmal O(n) und spart danach ueberall Faktor drei.
    """
    if sr <= ziel:
        return x, sr
    faktor = int(round(sr / ziel))
    if faktor < 2:
        return x, sr
    # 31-taps Fenster-Sinc, Grenze knapp unter der neuen Nyquist-Frequenz.
    n = 31
    t = np.arange(n) - (n - 1) / 2
    fc = 0.45 / faktor
    kern = np.sinc(2 * fc * t) * np.hamming(n)
    kern /= kern.sum()
    gefiltert = np.convolve(x, kern, mode="same")
    return gefiltert[::faktor], int(round(sr / faktor))


# --- Einzelmerkmale ---------------------------------------------------------

def huellkurve(x: np.ndarray, sr: int, hop_ms: float = 10.0) -> np.ndarray:
    """RMS-Hüllkurve (100 Werte je Sekunde bei hop_ms=10)."""
    hop = max(1, int(sr * hop_ms / 1000.0))
    n = x.size // hop
    if n < 4:
        return np.zeros(0)
    return np.sqrt(np.mean(np.square(x[:n * hop].reshape(n, hop), dtype=np.float64), axis=1))


def _modulationsspektrum(env: np.ndarray, env_rate: float
                         ) -> tuple[np.ndarray, np.ndarray]:
    """Spektrum der Hüllkurve — die Grundlage aller Takt-/Silbenmerkmale."""
    if env.size < 32:
        return np.zeros(0), np.zeros(0)
    e = env - env.mean()
    if not np.any(e):
        return np.zeros(0), np.zeros(0)
    spek = np.abs(np.fft.rfft(e * np.hanning(e.size)))
    frq = np.fft.rfftfreq(e.size, d=1.0 / env_rate)
    return spek, frq


def silbenmodulation(env: np.ndarray, env_rate: float) -> float:
    """Anteil der Hüllkurven-Energie im Silbenbereich 2–8 Hz (0..1).

    Notwendige, aber **nicht hinreichende** Bedingung für Sprache: Musik
    moduliert im selben Band. Erst zusammen mit `modulationsschaerfe`
    unterscheidet dieser Wert die beiden.
    """
    spek, frq = _modulationsspektrum(env, env_rate)
    if spek.size == 0:
        return 0.0
    gesamt = float(np.sum(spek[frq > 0.2]))
    if gesamt <= 0:
        return 0.0
    return float(np.sum(spek[(frq >= 2.0) & (frq <= 8.0)])) / gesamt


def modulationsschaerfe(env: np.ndarray, env_rate: float) -> float:
    """Wie *linienhaft* ist die Modulation? (0 = breiter Hügel, 1 = klare Linie)

    Das ist das Merkmal, das Takt von Silbe trennt. Ein gleichmaessiger Takt
    erzeugt im Modulationsspektrum eine schmale Spitze weit ueber dem
    Untergrund; unregelmaessige Silben erzeugen einen breiten Huegel.
    Gemessen als Verhaeltnis der hoechsten Spitze zum Median im Band 1–10 Hz.
    """
    spek, frq = _modulationsspektrum(env, env_rate)
    if spek.size == 0:
        return 0.0
    band = spek[(frq >= 1.0) & (frq <= 10.0)]
    if band.size < 8:
        return 0.0
    med = float(np.median(band))
    if med <= 1e-12:
        return 0.0
    verhaeltnis = float(np.max(band)) / med
    # 3x Median = unauffaellig, 15x = deutliche Linie.
    return float(np.clip((verhaeltnis - 3.0) / 12.0, 0.0, 1.0))


def taktbindung(env: np.ndarray, env_rate: float) -> float:
    """Wiederkehr im Anschlagsmuster (0..1) — Musik hat einen Puls, Sprache nicht.

    Autokorrelation der halbwegs gleichgerichteten Huellkurvenaenderung
    (Onset-Huellkurve) im Bereich 0,25–2 s Verzoegerung, also 30–240 bpm.
    """
    if env.size < 64:
        return 0.0
    onset = np.diff(env)
    onset[onset < 0] = 0.0                    # nur Anstiege zaehlen
    if not np.any(onset):
        return 0.0
    onset = onset - onset.mean()
    n = 1 << int(np.ceil(np.log2(onset.size * 2)))
    spek = np.fft.rfft(onset, n)
    ak = np.fft.irfft(np.abs(spek) ** 2, n)[:onset.size]
    if ak[0] <= 0:
        return 0.0
    ak = ak / ak[0]
    lo, hi = int(0.25 * env_rate), min(int(2.0 * env_rate), ak.size - 1)
    if hi <= lo:
        return 0.0
    return float(np.clip(np.max(ak[lo:hi]), 0.0, 1.0))


def spektrale_flachheit(x: np.ndarray, sr: int, n_fft: int = 1024) -> float:
    """Wiener-Entropie im Nutzband (0..1): 1 = rauschartig, 0 = tonal.

    Wichtig ist die Bandgrenze: ueber das volle Spektrum gerechnet druecken die
    praktisch leeren obersten Oktaven das geometrische Mittel gegen null, und
    das Merkmal liefert fuer *jedes* Material fast 0 — also nichts. Beurteilt
    wird deshalb nur 200 Hz – 6 kHz, wo sich Sprache, Musik und Atmo
    tatsaechlich unterscheiden.
    """
    if x.size < n_fft:
        return 0.0
    fenster = np.hanning(n_fft)
    frq = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    band = (frq >= 200.0) & (frq <= 6000.0)
    if not np.any(band):
        return 0.0
    schritte = min(x.size // n_fft, 120)
    werte = []
    for i in range(schritte):
        seg = x[i * n_fft:(i + 1) * n_fft]
        if seg.size < n_fft:
            break
        p = np.abs(np.fft.rfft(seg * fenster)) ** 2
        p = p[band] + 1e-12
        werte.append(float(np.exp(np.mean(np.log(p))) / np.mean(p)))
    return float(np.median(werte)) if werte else 0.0


def tonhoehen_spur(x: np.ndarray, sr: int, max_rahmen: int = 600
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Grundton je Rahmen (Hz) und Klarheit je Rahmen (0..1).

    Autokorrelation über alle Rahmen auf einmal (FFT) statt Rahmen für Rahmen —
    das ist der Unterschied zwischen „geht" und „bremst den Import".
    """
    rahmen = int(sr * 0.05)                   # 50 ms
    schritt = int(sr * 0.025)
    lo, hi = int(sr / 400), int(sr / 60)      # 60–400 Hz Grundton
    if x.size < rahmen * 4 or hi <= lo:
        return np.zeros(0), np.zeros(0)

    starts = np.arange(0, x.size - rahmen, schritt)[:max_rahmen]
    if starts.size < 4:
        return np.zeros(0), np.zeros(0)
    bloecke = np.stack([x[s:s + rahmen] for s in starts]).astype(np.float64)
    bloecke -= bloecke.mean(axis=1, keepdims=True)

    n = 1 << int(np.ceil(np.log2(rahmen * 2)))
    spek = np.fft.rfft(bloecke, n, axis=1)
    ak = np.fft.irfft(np.abs(spek) ** 2, n, axis=1)[:, :rahmen]
    null = ak[:, 0:1]
    with np.errstate(divide="ignore", invalid="ignore"):
        ak = np.where(null > 0, ak / np.where(null > 0, null, 1.0), 0.0)

    fenster = ak[:, lo:hi]
    idx = np.argmax(fenster, axis=1)
    zeile = np.arange(fenster.shape[0])
    klarheit = fenster[zeile, idx]

    # Parabel durch die drei Punkte um die Spitze — ohne diese Verfeinerung
    # ist die Periode ganzzahlig, und die Quantisierung taeuscht bei einer
    # ruhigen Stimme sowohl gehaltene Toene als auch ein Halbtonraster vor
    # (bei 200 Hz sind 16 kHz nur 80 Samples Periode: ein Sample = 1,25 %).
    mitte = np.clip(idx, 1, fenster.shape[1] - 2)
    y0 = fenster[zeile, mitte - 1]
    y1 = fenster[zeile, mitte]
    y2 = fenster[zeile, mitte + 1]
    nenner = y0 - 2 * y1 + y2
    with np.errstate(divide="ignore", invalid="ignore"):
        delta = np.where(np.abs(nenner) > 1e-12, 0.5 * (y0 - y2) / nenner, 0.0)
    delta = np.clip(np.nan_to_num(delta), -0.5, 0.5)
    perioden = (lo + mitte + delta).astype(np.float64)
    tonhoehen = np.where(perioden > 0, sr / np.maximum(perioden, 1.0), 0.0)
    # Stille Rahmen zaehlen nicht mit.
    energie = np.sqrt(np.mean(bloecke ** 2, axis=1))
    gueltig = energie > 1e-5
    return np.where(gueltig, tonhoehen, 0.0), np.where(gueltig, klarheit, 0.0)


def halbtonraster(tonhoehen: np.ndarray, klarheit: np.ndarray) -> float:
    """Liegen die Toene auf einem Halbtonraster? (0,5 = zufaellig, 1 = exakt)

    Musik benutzt diskrete Tonhoehen, Sprache gleitet stufenlos. Die Stimmung
    (443 Hz statt 440, historische Instrumente) wird vorher herausgerechnet,
    sonst misst man nur die Abweichung des Kammertons.
    """
    gut = (klarheit > 0.35) & (tonhoehen > 50)
    if np.count_nonzero(gut) < 12:
        return 0.5
    halbtoene = 12.0 * np.log2(tonhoehen[gut] / 440.0)
    rest = halbtoene - np.round(halbtoene)
    # Globale Stimmungsabweichung als Kreismittel abziehen.
    winkel = 2 * np.pi * rest
    versatz = np.angle(np.mean(np.exp(1j * winkel))) / (2 * np.pi)
    rest = rest - versatz
    rest = rest - np.round(rest)
    return float(np.clip(1.0 - 2.0 * np.mean(np.abs(rest)), 0.0, 1.0))


def haltetoene(tonhoehen: np.ndarray, klarheit: np.ndarray,
               rahmen_ms: float = 25.0, min_ms: float = 150.0) -> float:
    """Anteil der Zeit in *gehaltenen* Toenen (0..1).

    Ein Ton gilt als gehalten, wenn die Tonhoehe ueber mindestens 150 ms
    innerhalb eines Viertel-Halbtons bleibt. Gesungene und gespielte Toene tun
    das dauernd, Sprachvokale so gut wie nie — die gleiten immer.
    """
    gut = (klarheit > 0.35) & (tonhoehen > 50)
    if np.count_nonzero(gut) < 8:
        return 0.0
    mindest = max(2, int(min_ms / rahmen_ms))
    gehalten = 0
    lauf = 0
    bezug = 0.0
    for i, ok in enumerate(gut):
        if ok and lauf and abs(np.log2(tonhoehen[i] / bezug)) < 1.0 / 48.0:
            lauf += 1
            continue
        if lauf >= mindest:
            gehalten += lauf
        if ok:
            lauf, bezug = 1, float(tonhoehen[i])
        else:
            lauf, bezug = 0, 0.0
    if lauf >= mindest:
        gehalten += lauf
    return float(gehalten / max(gut.size, 1))


def pausenanteil(env: np.ndarray) -> float:
    """Anteil der Zeit deutlich unter dem lauten Mittel — Sprache atmet."""
    if env.size < 8:
        return 0.0
    laut = float(np.percentile(env, 85))
    if laut <= 1e-9:
        return 1.0
    return float(np.mean(env < laut * 0.12))


# --- Gesamtbeurteilung ------------------------------------------------------

def aktive_teile(x: np.ndarray, sr: int, hop_ms: float = 10.0) -> tuple[np.ndarray, float]:
    """Schneidet die Leerstellen der Spur heraus und liefert (Signal, Leeranteil).

    Notwendig, weil eine importierte Spur meist aus einzelnen Clips mit Stille
    dazwischen besteht (im Sender-Material bis 42 % der Laenge). Diese Luecken
    taeuschen sonst genau die Merkmale vor, auf die es ankommt: Das Ein- und
    Ausschalten der Clips sieht aus wie Silbentakt, und die Stille zaehlt als
    Sprechpause. Beurteilt wird deshalb nur, was tatsaechlich klingt.
    """
    env = huellkurve(x, sr, hop_ms)
    if env.size < 4:
        return x, 0.0
    hop = max(1, int(sr * hop_ms / 1000.0))
    # Schwelle relativ zum lauten Anteil der Spur, nicht absolut.
    laut = float(np.percentile(env, 90))
    if laut <= 1e-9:
        return x, 1.0
    aktiv = env > laut * 0.02          # ~ -34 dB unter dem lauten Anteil
    leeranteil = float(1.0 - np.mean(aktiv))
    if not np.any(aktiv):
        return x, 1.0
    idx = np.repeat(aktiv, hop)[:x.size]
    if idx.size < x.size:
        idx = np.concatenate([idx, np.zeros(x.size - idx.size, dtype=bool)])
    gefiltert = x[idx]
    # Zu wenig uebrig -> lieber das Original beurteilen als Bruchstuecke.
    return (gefiltert if gefiltert.size > sr * 0.5 else x), leeranteil


def merkmale(x: np.ndarray, sr: int) -> dict:
    """Alle Merkmale einer Monospur (float32/64, -1..1).

    Bewertet werden nur die klingenden Abschnitte (siehe `aktive_teile`), auf
    16 kHz heruntergesetzt und auf `MAX_SEKUNDEN` begrenzt.
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    x, sr = _tiefpass_dezimieren(x, sr)
    x, leeranteil = aktive_teile(x, sr)
    grenze = int(MAX_SEKUNDEN * sr)
    if x.size > grenze:
        x = x[:grenze]

    env = huellkurve(x, sr)
    rms = float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0
    spitze = float(np.abs(x).max(initial=0.0)) if x.size else 0.0
    tonhoehen, klarheit = tonhoehen_spur(x, sr)
    tonal = float(np.mean(klarheit > 0.35)) if klarheit.size else 0.0
    return {
        "rms_db": 20 * float(np.log10(rms)) if rms > 1e-12 else -180.0,
        "spitze_db": 20 * float(np.log10(spitze)) if spitze > 1e-12 else -180.0,
        "leeranteil": round(leeranteil, 4),
        "silbenmodulation": round(silbenmodulation(env, ENV_RATE), 4),
        "modulationsschaerfe": round(modulationsschaerfe(env, ENV_RATE), 4),
        "taktbindung": round(taktbindung(env, ENV_RATE), 4),
        "flachheit": round(spektrale_flachheit(x, sr), 4),
        "tonal": round(tonal, 4),
        "halbtonraster": round(halbtonraster(tonhoehen, klarheit), 4),
        "haltetoene": round(haltetoene(tonhoehen, klarheit), 4),
        "pausenanteil": round(pausenanteil(env), 4),
    }


def klassifiziere(x: np.ndarray, sr: int) -> dict:
    """Beurteilt eine Monospur. Liefert art, konfidenz, begruendung, merkmale."""
    m = merkmale(x, sr)

    # Stille am Spitzenwert festmachen, nicht am Mittelwert: eine sehr leise,
    # lueckenhafte Atmo hat einen RMS von -65 dBFS, aber Spitzen bei -36 —
    # das ist Material, kein Nichts. Der Mittelwert allein wuerde sie
    # faelschlich als leere Spur abtun.
    if m["spitze_db"] < -60:
        return {"art": "stille", "konfidenz": 0.95, "punkte": {},
                "begruendung": "praktisch kein Signal", "merkmale": m}

    # Punktwertung statt fester Schwellen: jedes Merkmal stimmt gewichtet ab.
    p = {"sprache": 0.0, "atmo": 0.0, "musik": 0.0}

    sm, ms = m["silbenmodulation"], m["modulationsschaerfe"]
    takt, halt = m["taktbindung"], m["haltetoene"]
    raster, fl, pa = m["halbtonraster"], m["flachheit"], m["pausenanteil"]

    # Grundsatz der Wertung: Jede Klasse sammelt nur Punkte fuer das, was sie
    # *selbst* auszeichnet. Die Abwesenheit eines fremden Merkmals zaehlt
    # nicht — sonst gewinnt Sprache jedes Mal, wenn etwas weder Takt noch
    # gehaltene Toene hat, und genau das trifft auf Atmo ebenso zu.
    band = np.clip((sm - 0.16) / 0.20, 0, 1)          # Modulation im Silbenband
    halt_n = np.clip(halt / 0.30, 0, 1)               # gehaltene Toene
    raster_n = np.clip((raster - 0.55) / 0.25, 0, 1)  # Halbtonraster
    takt_n = np.clip((takt - 0.12) / 0.25, 0, 1)      # Puls

    # SPRACHE: unregelmaessige Silbenmodulation, dazu Stimmhaftigkeit, deren
    # Tonhoehe gleitet statt zu stehen. Beides zusammen kann sonst nichts.
    p["sprache"] += 3.0 * band * (1.0 - ms)
    p["sprache"] += 2.2 * m["tonal"] * (1.0 - halt_n) * (1.0 - raster_n)
    p["sprache"] += 1.6 * np.clip((pa - 0.10) / 0.25, 0, 1) * (1.0 - takt)

    # MUSIK: Puls, gehaltene Toene, Halbtonraster, regelmaessige Modulation.
    p["musik"] += 2.5 * takt_n
    p["musik"] += 3.0 * halt_n
    p["musik"] += 2.0 * raster_n
    p["musik"] += 2.0 * band * ms
    p["musik"] += 0.8 * np.clip((0.12 - pa) / 0.12, 0, 1)

    # ATMO: gleichfoermig, spektral breit, kaum Tonhoehe.
    p["atmo"] += 2.5 * np.clip((fl - 0.10) / 0.20, 0, 1)
    p["atmo"] += 2.0 * np.clip((0.30 - m["tonal"]) / 0.30, 0, 1)
    p["atmo"] += 1.5 * np.clip((0.20 - sm) / 0.15, 0, 1)
    p["atmo"] += 0.8 * np.clip((0.12 - pa) / 0.12, 0, 1)

    # Tonale Struktur spricht gegen diffuse Atmo — hier ist der Abzug richtig,
    # weil Tonhoehe ein Merkmal *ist* und nicht bloss fehlt.
    p["sprache"] += 1.0 * np.clip((0.14 - fl) / 0.10, 0, 1)
    p["musik"]   += 1.0 * np.clip((0.14 - fl) / 0.10, 0, 1)

    art = max(p, key=lambda k: p[k])
    werte = sorted(p.values(), reverse=True)
    summe = sum(p.values()) or 1.0
    # Konfidenz aus dem Abstand zum zweitbesten — knappe Faelle sind ehrlich knapp.
    konfidenz = float(np.clip((werte[0] - werte[1]) / summe * 2.2 + 0.30, 0.30, 0.97))

    gruende = {
        "sprache": f"unregelmaessiger Silbentakt {sm:.2f} (Schaerfe {ms:.2f}), "
                   f"Pausen {pa:.0%}, keine gehaltenen Toene",
        "atmo": f"gleichfoermig (Silbentakt {sm:.2f}), spektral flach {fl:.2f}, "
                f"{m['tonal']:.0%} tonal",
        "musik": f"Puls {takt:.2f}, gehaltene Toene {halt:.0%}, "
                 f"Halbtonraster {raster:.2f}",
    }
    return {"art": art, "konfidenz": round(konfidenz, 3),
            "begruendung": gruende[art], "punkte": {k: round(v, 2) for k, v in p.items()},
            "merkmale": m}


# --- Rolle daraus ableiten --------------------------------------------------

def rolle_aus_inhalt(art: str, ist_kommentarverdaechtig: bool = False) -> str | None:
    """Uebersetzt die erkannte Art in eine Hedwig-Rolle.

    Sprache allein sagt noch nicht, ob Kommentar oder O-Ton — das entscheidet
    der Zusammenhang (Kommentar laeuft durch den ganzen Beitrag und ist trocken,
    O-Ton ist stueckweise). Ohne diesen Zusammenhang wird Sprache zu O-Ton, weil
    das der haeufigere Fall im importierten Schnittmaterial ist.
    """
    return {
        "sprache": "Kommentar" if ist_kommentarverdaechtig else "OTon",
        "atmo": "Atmo",
        "musik": "Musik",
        "stille": None,
    }.get(art)
