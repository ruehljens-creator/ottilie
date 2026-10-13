#!/usr/bin/env python3
"""AAF-Essenz: vom SourceClip zum tatsächlichen Audio.

Getrennt von der Timeline-Logik, weil das zwei verschiedene Probleme sind:
`hedwig_aaf_timeline` beantwortet *wann* etwas klingt, dieses Modul *was*
überhaupt klingt.

Die Verweiskette ist in echten AAFs tiefer, als man erwartet. Ein
Musikclip aus Media Composer läuft zum Beispiel so::

    SourceClip (Timeline)
      -> CompositionMob            (Unterclip, trägt oft eigenen Gain)
        -> OperationGroup          "Audio Gain"
          -> SourceClip  slot_id=2 (rechter Kanal!)
            -> MasterMob
              -> SourceClip
                -> SourceMob       PCM-Essenz — hier liegt der Ton

Wer nur eine Ebene tief schaut, verliert solche Clips ersatzlos. Und wer
`slot_id` ignoriert, holt bei Stereo-Material zweimal den linken Kanal.

Drei Essenzarten kommen vor:
  * **PCMDescriptor**  — roher PCM-Strom, pyaaf2 kann ihn selbst schreiben
  * **WAVEDescriptor** — der Strom *ist* eine komplette RIFF-Datei
  * **verlinkt**       — gar keine Essenz in der AAF, nur ein Locator-Pfad
    auf eine externe MXF/WAV (Avid MediaFiles)
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass, field

import numpy as np

import hedwig_log
import hedwig_mxf
import hedwig_wavio

log = hedwig_log.hole(__name__)

# Tiefer als das wird keine reale Verweiskette; schützt vor Zyklen.
MAX_TIEFE = 16

# Elemente ohne eigene Essenz, die nur die Sequenz strukturieren.
STRUKTUR = ("Filler", "ScopeReference", "EssenceGroup", "Timecode", "EdgeCode")


# ---------------------------------------------------------------- Hilfsmittel

def zahl(v) -> float | None:
    """AAF-Zahlen robust zu float: int/float, Rational (num/den), 'a/b'-Strings."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    for attr in ("numerator", "num"):
        den = getattr(v, "denominator", None) or getattr(v, "den", None)
        num = getattr(v, attr, None)
        if num is not None and den:
            try:
                return float(num) / float(den)
            except (TypeError, ZeroDivisionError):
                return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    s = str(v)
    if "/" in s:
        a, _, b = s.partition("/")
        try:
            return float(a) / float(b)
        except (ValueError, ZeroDivisionError):
            return None
    return None


def _art(o) -> str:
    return o.__class__.__name__ if o is not None else "None"


# ------------------------------------------------------------ Gain-Hüllkurven

# Parameter werden ueber ihre **AUID** erkannt, nicht ueber den Namen. Im
# echten Material ist der Name bei zwei Dritteln der Parameter schlicht leer,
# waehrend die AUID immer stimmt — sie ist der einzige exakte Weg.
_AUID_LAUTSTAERKE = {
    "e4962320-2267-11d3-8a4c-0050040ef7d2",   # ParameterDef_Level (SMPTE)
    "e4962321-2267-11d3-8a4c-0050040ef7d2",   # ParameterDef_Amplitude (SMPTE)
    "47c00f7d-fb55-11d3-9ff7-0004ac969f50",   # AvidControlClipRatio
}
_AUID_IGNORIEREN = {
    "e4962322-2267-11d3-8a4c-0050040ef7d2",   # ParameterDef_Pan — bewusst nicht
    "922d458d-8f22-11d4-a03c-0004ac969f50",   # PanVol_IsTrimGainEffect (Flag)
}
# Nur noch Rueckfalle fuer unbekannte AUIDs aus fremden Schnittsystemen.
_LAUTSTAERKE_NAMEN = ("amplitude", "gain", "level", "volume", "ratio")
_KEINE_LAUTSTAERKE = ("pan",)


def _ist_lautstaerke(prm) -> bool:
    """Ist dieser Parameter die Lautstärke — oder etwas anderes daneben?

    Der Name allein reicht nicht und richtet Schaden an: Avid legt neben die
    Kurve ein Flag namens ``PanVol_IsTrimGainEffect``. Das enthaelt „Gain",
    ist aber ein Ja/Nein-Wert — und steht auf 0. Wer es als Verstaerkung liest,
    schaltet die Spur stumm (so verschwand die Musik aus einer Test-AAF).
    Umgekehrt heisst Avids echte Kurve ``AvidControlClipRatio`` und faellt
    durch jeden naheliegenden Namensfilter.

    Verlaesslich ist die AUID der Parameterdefinition. Sie steht auch dann
    richtig da, wenn der Name leer ist — was im Sender-Material bei zwei Dritteln
    aller Parameter der Fall ist.

    **Panning wird bewusst ignoriert** (Projektentscheidung): Hedwig pant
    selbst, siehe `hedwig_stereo`. Ebenso EQ und Kompressor aus dem Schnitt —
    Hedwig nimmt das saubere Signal und bearbeitet es mit eigenen Mitteln.
    Effekt-OperationGroups werden dabei nicht uebersprungen, sondern
    durchgereicht: Der Clip darunter kommt unveraendert an.
    """
    pdef = getattr(prm, "parameterdef", None)
    auid = str(getattr(pdef, "auid", "") or "").lower()
    if auid in _AUID_IGNORIEREN:
        return False
    if auid in _AUID_LAUTSTAERKE:
        return True

    # Unbekannte Kennung (anderes Schnittsystem): vorsichtig ueber Typ + Name.
    # Lautstaerken sind Rationale, Schalter ganzzahlig.
    typ = (getattr(getattr(prm, "typedef", None), "name", "") or "").lower()
    if "integer" in typ:
        return False
    name = (getattr(pdef, "name", "") or "").lower()
    if any(w in name for w in _KEINE_LAUTSTAERKE):
        return False
    return not name or any(w in name for w in _LAUTSTAERKE_NAMEN)


_AUID_PAN = "e4962322-2267-11d3-8a4c-0050040ef7d2"   # ParameterDef_Pan


def pan_der_spur(segment, tiefe: int = 0) -> float | None:
    """Wohin ist diese Spur gepannt? 0.0 = links, 1.0 = rechts, None = keine Angabe.

    Zum MISCHEN wird Pan weiterhin ignoriert (siehe `_ist_lautstaerke`):
    Hedwig pant selbst. Hier geht es um etwas anderes — um die Frage, welche
    zwei Monospuren die beiden Haelften EINER Stereospur sind. Premiere legt
    genau das ab, und ohne diese Angabe summiert Hedwig beide Haelften in
    beide Kanaele.

    Geliefert wird nur ein eindeutiger Wert. Wandert der Pan ueber die Spur,
    ist sie keine feste Stereohaelfte, und Raten waere hier schlimmer als
    Schweigen.
    """
    werte: set[float] = set()

    def durch(s, t=0):
        if t > 6 or len(werte) > 1:
            return
        if _art(s) == "OperationGroup":
            for prm in getattr(s, "parameters", []) or []:
                pdef = getattr(prm, "parameterdef", None)
                if str(getattr(pdef, "auid", "") or "").lower() != _AUID_PAN:
                    continue
                punkte = getattr(prm, "points", None)
                if punkte is not None:
                    for p in punkte:
                        v = zahl(getattr(p, "value", None))
                        if v is not None:
                            werte.add(round(v, 3))
                else:
                    v = zahl(getattr(prm, "value", None))
                    if v is not None:
                        werte.add(round(v, 3))
        for feld in ("components", "segments", "input_segments"):
            for kind in getattr(s, feld, []) or []:
                durch(kind, t + 1)

    durch(segment, tiefe)
    return werte.pop() if len(werte) == 1 else None


def gain_huellkurve(op) -> list[tuple[float, float]] | None:
    """Gain-Hüllkurve/Clip-Gain einer OperationGroup (Avid).

    Liefert Stützpunkte [(t, gain)], t normiert 0..1 über die Gruppenlänge.
    Deckt beides ab: VaryingValue (Blende, mehrere Punkte) und ConstantValue
    (fester Clip-Gain — damit legen Cutter die Atmo unter die Sprache).
    """
    try:
        params = list(op.parameters)
    except Exception:
        log.ausnahme("Parameter einer OperationGroup nicht lesbar — "
                     "Lautstaerke dieses Clips bleibt unberuecksichtigt",
                     gruppe=op.__class__.__name__)
        return None

    for prm in params:
        if not _ist_lautstaerke(prm):
            continue
        cls = _art(prm)
        if cls == "VaryingValue":
            # pyaaf2 nennt die Stützpunkte 'pointlist' (nicht 'points').
            punkte = getattr(prm, "pointlist", None) or getattr(prm, "points", None) or []
            pts = []
            for p in punkte:
                t, v = zahl(getattr(p, "time", None)), zahl(getattr(p, "value", None))
                if t is not None and v is not None:
                    pts.append((min(max(t, 0.0), 1.0), v))
            if pts:
                return sorted(pts)
        elif cls == "ConstantValue":
            v = zahl(getattr(prm, "value", None))
            if v is not None:
                return [(0.0, v), (1.0, v)]  # konstanter Gain über den Clip
    return None


def kombiniere(huellkurven: list[list[tuple[float, float]]]
               ) -> list[tuple[float, float]] | None:
    """Multipliziert mehrere Hüllkurven auf einer gemeinsamen Zeitachse.

    Verschachtelte Gains sind der Normalfall: einer am Clip in der Timeline,
    einer im Unterclip. Konstante Gains werden dabei exakt multipliziert.
    Grenze: Sind zwei *fahrende* Kurven verschachtelt, deren Bezugslängen
    sich unterscheiden, ist das Ergebnis nur näherungsweise richtig —
    in der Praxis ist die innere Kurve dann konstant.
    """
    kurven = [h for h in huellkurven if h]
    if not kurven:
        return None
    if len(kurven) == 1:
        return kurven[0]
    stuetzen = sorted({t for h in kurven for t, _ in h} | {0.0, 1.0})
    out = []
    for t in stuetzen:
        g = 1.0
        for h in kurven:
            xs = [p[0] for p in h]
            ys = [p[1] for p in h]
            g *= float(np.interp(t, xs, ys))
        out.append((t, g))
    return out


# --------------------------------------------------- Auflösung der Verweiskette

@dataclass
class Quelle:
    """Ein aufgelöster Clip: wo die Essenz liegt und wie weit hinein."""
    mob: object                       # SourceMob mit Essenz (oder Locator)
    offset_s: float                   # Startversatz in der Quelle, in Sekunden
    gains: list = field(default_factory=list)   # Hüllkurven von aussen nach innen


def _slot_mit_id(mob, slot_id):
    """Slot per ID holen — bei Stereo entscheidet sie über links/rechts."""
    slots = list(mob.slots)
    if slot_id is not None:
        for s in slots:
            if zahl(getattr(s, "slot_id", None)) == zahl(slot_id):
                return s
    # Kein Treffer: erster Tonslot. Besser als gar nichts, aber bei
    # Stereo-Quellen potenziell der falsche Kanal.
    for s in slots:
        if getattr(s.segment, "media_kind", None) == "Sound":
            return s
    return slots[0] if slots else None


def _clip_an_position(segment, pos_eu: float, tiefe: int = 0):
    """Sucht in einem Slot-Inhalt den SourceClip an Position `pos_eu`.

    Liefert (SourceClip, gains, rest_eu) — `rest_eu` ist der Versatz
    innerhalb des gefundenen Clips.
    """
    if tiefe > MAX_TIEFE:
        return None, [], 0.0
    cls = _art(segment)

    if cls == "SourceClip":
        return segment, [], pos_eu

    if cls == "Sequence":
        p = 0.0
        for c in segment.components:
            ln = float(getattr(c, "length", 0) or 0)
            if _art(c) == "Transition":
                p -= ln          # Blende: die Nachbarn überlappen, siehe timeline
                continue
            if p <= pos_eu < p + ln or (ln == 0 and p == pos_eu):
                sub, g, rest = _clip_an_position(c, pos_eu - p, tiefe + 1)
                if sub is not None:
                    return sub, g, rest
            p += ln
        # Nichts an der Position: ersten echten Clip nehmen (Position 0).
        for c in segment.components:
            if _art(c) not in STRUKTUR and _art(c) != "Transition":
                return _clip_an_position(c, 0.0, tiefe + 1)
        return None, [], 0.0

    if cls == "OperationGroup":
        eigen = gain_huellkurve(segment)
        try:
            teile = list(segment.segments)
        except Exception:
            log.ausnahme("Inhalt einer OperationGroup nicht lesbar — "
                         "Clips darunter gehen verloren")
            teile = []
        for s in teile:
            sub, g, rest = _clip_an_position(s, pos_eu, tiefe + 1)
            if sub is not None:
                return sub, ([eigen] if eigen else []) + g, rest
        return None, [], 0.0

    if cls in STRUKTUR:
        return None, [], 0.0

    if hasattr(segment, "components"):
        for c in segment.components:
            sub, g, rest = _clip_an_position(c, pos_eu, tiefe + 1)
            if sub is not None:
                return sub, g, rest
    return None, [], 0.0


def hat_essenz(mob) -> bool:
    """Trägt dieser Mob selbst Audio (eingebettet oder verlinkt)?"""
    if mob is None:
        return False
    d = getattr(mob, "descriptor", None)
    if _art(d) not in ("PCMDescriptor", "WAVEDescriptor", "AIFCDescriptor"):
        return False
    if getattr(mob, "essence", None) is not None:
        return True
    return bool(locator_pfade(mob))       # verlinkt: Essenz liegt daneben


def aufloesen(clip, edit_rate: float, tiefe: int = 0) -> Quelle | None:
    """Folgt einem SourceClip bis zur Essenz und sammelt Versatz und Gains.

    `edit_rate` ist die Rate der Ebene, auf der `clip` liegt — jede Ebene
    darf ihre eigene haben, deshalb wird der Versatz in **Sekunden**
    weitergereicht statt in Edit Units.
    """
    if tiefe > MAX_TIEFE or clip is None:
        return None
    mob = getattr(clip, "mob", None)
    if mob is None:
        return None

    rate = edit_rate or 1.0
    offset_s = float(getattr(clip, "start", 0) or 0) / rate

    if hat_essenz(mob):
        return Quelle(mob, offset_s, [])

    slot = _slot_mit_id(mob, getattr(clip, "slot_id", None))
    if slot is None:
        return None
    slot_rate = zahl(getattr(slot, "edit_rate", None)) or rate

    sub, gains, rest_eu = _clip_an_position(slot.segment, offset_s * slot_rate)
    if sub is None:
        return None

    tiefer = aufloesen(sub, slot_rate, tiefe + 1)
    if tiefer is None:
        return None
    tiefer.offset_s += rest_eu / slot_rate
    tiefer.gains = gains + tiefer.gains
    return tiefer


# ------------------------------------------------------- Essenz wirklich lesen

def locator_pfade(mob) -> list[str]:
    """Externe Pfade eines Mobs (verlinkte AAF), als lokale Pfade."""
    d = getattr(mob, "descriptor", None)
    if d is None:
        return []
    pfade = []
    try:
        locs = list(getattr(d, "locator", None) or [])
    except Exception:
        log.ausnahme("Locator-Liste nicht lesbar — verlinkte Medien "
                     "koennen nicht gefunden werden",
                     descriptor=_art(d))
        locs = []
    for loc in locs:
        url = None
        for schluessel in ("URLString", "urlstring", "path"):
            try:
                url = loc[schluessel].value
            except Exception:
                url = getattr(loc, schluessel, None)
            if url:
                break
        if url:
            p = _url_zu_pfad(str(url))
            if p:
                pfade.append(p)
    return pfade


def _url_zu_pfad(url: str) -> str | None:
    """file://-URL zu lokalem Pfad — inklusive Windows-Laufwerk und UNC."""
    if not url.startswith("file:"):
        return url if os.path.sep in url else None
    rest = urllib.parse.unquote(url[5:])
    rest = rest.replace("/", os.sep) if os.sep == "\\" else rest
    if rest.startswith(os.sep * 3):          # file:///G:/... -> G:\...
        return rest[3:]
    if rest.startswith(os.sep * 2):          # file://server/share -> UNC
        return rest
    return rest.lstrip(os.sep) or None


def finde_datei(pfad: str, suchorte: list[str]) -> str | None:
    """Sucht eine verlinkte Datei: erst am notierten Ort, dann daneben.

    Der Pfad im AAF zeigt fast immer auf einen Rechner, der nicht dieser
    ist (`file://fad-nexis/...`). Deshalb wird zusätzlich am AAF-Ort und in
    den üblichen Avid-Verzeichnissen nach dem Dateinamen gesucht.
    """
    if pfad and os.path.isfile(pfad):
        return pfad
    name = os.path.basename(pfad.replace("\\", os.sep).replace("/", os.sep))
    if not name:
        return None
    for ort in suchorte:
        if not ort or not os.path.isdir(ort):
            continue
        direkt = os.path.join(ort, name)
        if os.path.isfile(direkt):
            return direkt
        # Avid legt Medien in "Avid MediaFiles/MXF/<n>/" ab.
        for wurzel, dirs, dateien in os.walk(ort):
            if name in dateien:
                return os.path.join(wurzel, name)
            # Tiefe begrenzen: Medienordner sind flach, Projektbäume nicht.
            if wurzel.count(os.sep) - ort.count(os.sep) > 4:
                dirs[:] = []
    return None


def _lies_ueber_ffmpeg(pfad: str) -> tuple[np.ndarray, int] | None:
    """Externe Medien (MXF, MP3, …) über PyAV bzw. FFmpeg dekodieren."""
    try:
        import av
    except ImportError:
        av = None
    if av is not None:
        try:
            log.debug("externe Datei ueber PyAV dekodieren", pfad=os.path.basename(pfad))
            with av.open(pfad) as c:
                strom = next((s for s in c.streams if s.type == "audio"), None)
                if strom is None:
                    return None
                sr = int(strom.rate or 48000)
                bloecke = []
                for frame in c.decode(strom):
                    a = frame.to_ndarray()
                    # PyAV liefert (kanaele, n) bei planar, (1, n*kanaele) sonst.
                    if a.ndim == 2 and a.shape[0] <= 8 and a.shape[0] > 0:
                        a = a.T
                    else:
                        a = a.reshape(-1, 1)
                    bloecke.append(np.asarray(a, dtype=np.float32))
                if not bloecke:
                    return None
                audio = np.vstack(bloecke)
                if audio.dtype != np.float32 or np.abs(audio).max(initial=0.0) > 1.5:
                    audio = audio.astype(np.float32) / 32768.0
                return audio, sr
        except Exception:
            log.ausnahme("PyAV konnte die Datei nicht dekodieren — "
                         "es wird FFmpeg als Programm versucht",
                         pfad=os.path.basename(pfad))

    exe = _ffmpeg_exe()
    if not exe:
        return None
    tmp = tempfile.mktemp(suffix=".wav", prefix="hedwig_ext_")
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-y", "-i", pfad, "-map", "0:a:0",
             "-c:a", "pcm_s24le", tmp],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not os.path.isfile(tmp):
            return None
        return hedwig_wavio.read_wav(tmp)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        log.debug("imageio_ffmpeg nicht verfuegbar — es wird im PATH gesucht")
    from shutil import which
    return which("ffmpeg")


class Essenzspeicher:
    """Hält entpackte Essenz im Speicher — sonst wird jeder Clip neu entpackt.

    Im echten Material teilen sich bis zu 40 Clips einer Spur dieselbe
    Quelldatei; ohne Zwischenspeicher dominiert das Entpacken die Importzeit.
    Begrenzt auf ein Budget, damit lange Beiträge den Speicher nicht sprengen.
    """

    def __init__(self, budget_mb: int = 512, suchorte: list[str] | None = None):
        self._daten: dict = {}
        self._reihe: list = []
        self._bytes = 0
        self._budget = budget_mb * 1024 * 1024
        self.suchorte = suchorte or []
        self.fehler: dict = {}

    def hole(self, mob) -> tuple[np.ndarray, int] | None:
        schluessel = getattr(mob, "mob_id", None) or id(mob)
        if schluessel in self._daten:
            self._reihe.remove(schluessel)
            self._reihe.append(schluessel)
            return self._daten[schluessel]
        if schluessel in self.fehler:
            return None

        ergebnis = self._lade(mob)
        if ergebnis is None:
            self.fehler[schluessel] = True
            return None

        audio, _ = ergebnis
        self._daten[schluessel] = ergebnis
        self._reihe.append(schluessel)
        self._bytes += audio.nbytes
        while self._bytes > self._budget and len(self._reihe) > 1:
            alt = self._reihe.pop(0)
            weg, _ = self._daten.pop(alt)
            self._bytes -= weg.nbytes
        return ergebnis

    def _lade(self, mob) -> tuple[np.ndarray, int] | None:
        d = getattr(mob, "descriptor", None)
        art = _art(d)
        ess = getattr(mob, "essence", None)

        if ess is not None:
            if art == "WAVEDescriptor":
                return self._aus_strom(ess, ".wav")
            if art == "AIFCDescriptor":
                gelesen = self._aus_strom(ess, ".aif")
                if gelesen:
                    return gelesen
            if art == "PCMDescriptor":
                return self._aus_pcm(mob)

        # Keine eingebettete Essenz: verlinktes AAF.
        for pfad in locator_pfade(mob):
            treffer = finde_datei(pfad, self.suchorte)
            if not treffer:
                continue
            gelesen = self._lies_extern(treffer, d)
            if gelesen:
                return gelesen
        return None

    @staticmethod
    def _lies_extern(pfad: str, descr) -> tuple[np.ndarray, int] | None:
        """Externe Datei lesen — WAV direkt, MXF selbst, alles Weitere via FFmpeg."""
        if pfad.lower().endswith((".wav", ".bwf", ".w64")):
            try:
                return hedwig_wavio.read_wav(pfad)
            except Exception:
                log.ausnahme("WAV nicht direkt lesbar — es wird ueber FFmpeg "
                             "versucht", pfad=os.path.basename(pfad))

        if hedwig_mxf.ist_mxf(pfad):
            # Die Formatangaben stehen schon im AAF — damit liest der eigene
            # MXF-Leser auch Dateien, deren Index Avid unvollstaendig
            # hinterlassen hat (dort steigt FFmpeg aus).
            def feld(name):
                try:
                    return int(descr[name].value)
                except Exception:
                    log.spur("Descriptor-Feld fehlt, MXF-Kopf entscheidet",
                             feld=name)
                    return None
            gelesen = hedwig_mxf.lies_pcm(pfad, bits=feld("QuantizationBits"),
                                          kanaele=feld("Channels"),
                                          rate=feld("AudioSamplingRate"))
            if gelesen:
                return gelesen

        return _lies_ueber_ffmpeg(pfad)

    @staticmethod
    def _aus_strom(ess, endung: str) -> tuple[np.ndarray, int] | None:
        """Der Essenz-Strom ist bei WAVE/AIFC bereits eine komplette Datei."""
        tmp = tempfile.mktemp(suffix=endung, prefix="hedwig_ess_")
        try:
            with open(tmp, "wb") as f:
                strom = ess.open("r")
                while True:
                    stueck = strom.read(1 << 20)
                    if not stueck:
                        break
                    f.write(stueck)
            if endung == ".wav":
                return hedwig_wavio.read_wav(tmp)
            return _lies_ueber_ffmpeg(tmp)
        except Exception:
            log.ausnahme("Eingebettete Essenz nicht lesbar — Clip faellt aus",
                         art=endung)
            return None
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    @staticmethod
    def _aus_pcm(mob) -> tuple[np.ndarray, int] | None:
        tmp = tempfile.mktemp(suffix=".wav", prefix="hedwig_ess_")
        try:
            mob.export_audio(tmp)
            return hedwig_wavio.read_wav(tmp)
        except Exception:
            log.ausnahme("PCM-Essenz nicht auslesbar — Clip faellt aus",
                         mob=str(getattr(mob, "name", "") or getattr(mob, "mob_id", ""))[:60])
            return None
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
