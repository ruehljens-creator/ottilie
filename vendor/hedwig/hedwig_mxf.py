#!/usr/bin/env python3
"""MXF-Tonleser für Avid-OP-Atom — direkt, ohne FFmpeg.

Warum eigener Code statt FFmpeg: Media Composer schreibt bei verlinkten AAFs
je Tonkanal eine OP-Atom-MXF in ``Avid MediaFiles``. Ein Teil dieser Dateien
hat einen unvollstaendigen Index; FFmpeg bricht dann mit *„could not resolve
material track sequence strong ref / broken or empty index"* ab und meldet
**null Tonspuren** — obwohl die Essenz vollstaendig und unbeschaedigt in der
Datei liegt. Fuer den Sendealltag ist das der Normalfall, nicht die Ausnahme:
Das gesamte Archivmaterial kommt so.

Der Aufbau ist einfach genug, um ihn selbst zu lesen. Eine MXF-Datei ist eine
Folge von KLV-Paketen (16-Byte-Key, BER-Laenge, Daten). Der Ton steckt im
Essenz-Element mit dem Schluessel::

    06 0e 2b 34 01 02 01 01 0d 01 03 01 <art> <typ> <nr> <sub>

``art`` 0x16 ist das AES3/BWF-Element, ``typ`` 0x02 dessen BWF-Variante —
und die enthaelt schlicht rohes PCM, so wie es auch in einer WAV steht.

Die Formatangaben (Bittiefe, Kanaele, Rate) stehen im Sound-Descriptor der
Kopfdaten; sie koennen alternativ von aussen gesetzt werden, wenn der Aufrufer
sie ohnehin schon aus dem AAF kennt.

Grenze: bewusst nur unkomprimiertes PCM. MXF mit MPEG- oder AAC-Ton wird
nicht angefasst — dafuer ist FFmpeg da, und dort funktioniert es auch.
"""
from __future__ import annotations

import os

import numpy as np

import hedwig_wavio

# Generischer Container-Schluessel; nur Byte 12–15 unterscheiden die Essenzart.
_ESSENZ_PRAEFIX = bytes.fromhex("060e2b34010201010d010301")
_ART_AES3_BWF = 0x16       # AES3/BWF-Element
_ART_TON = 0x06            # unkomprimierter Ton (seltener)

# Sound-Descriptor: lokale Tags nach SMPTE 377/382.
_TAG_QUANTISIERUNG = 0x3D01
_TAG_ABTASTRATE = 0x3D03
_TAG_KANAELE = 0x3D07
_TAG_BLOCKGROESSE = 0x3D0A


def _ber_laenge(f) -> int | None:
    """BER-kodierte Laenge lesen (kurz- oder langform)."""
    b = f.read(1)
    if not b:
        return None
    v = b[0]
    if v < 0x80:
        return v
    n = v & 0x7F
    if n == 0 or n > 8:
        return None
    roh = f.read(n)
    if len(roh) < n:
        return None
    return int.from_bytes(roh, "big")


def klv_pakete(f, groesse: int):
    """Laeuft die KLV-Kette ab und liefert (key, datenposition, laenge)."""
    pos = 0
    while pos < groesse:
        f.seek(pos)
        key = f.read(16)
        if len(key) < 16:
            return
        laenge = _ber_laenge(f)
        if laenge is None or laenge < 0:
            return
        daten = f.tell()
        if daten + laenge > groesse:
            laenge = groesse - daten          # abgeschnittene Datei: Rest nehmen
            if laenge <= 0:
                return
        yield key, daten, laenge
        pos = daten + laenge


def _ist_tonessenz(key: bytes) -> bool:
    return key[:12] == _ESSENZ_PRAEFIX and key[12] in (_ART_AES3_BWF, _ART_TON)


def _lies_descriptor(f, daten: int, laenge: int) -> dict:
    """Lokale Tags eines Metadaten-Sets auslesen (Tag, Laenge, Wert)."""
    f.seek(daten)
    roh = f.read(laenge)
    gefunden: dict[int, bytes] = {}
    i = 0
    while i + 4 <= len(roh):
        tag = int.from_bytes(roh[i:i + 2], "big")
        ln = int.from_bytes(roh[i + 2:i + 4], "big")
        if i + 4 + ln > len(roh):
            break
        gefunden[tag] = roh[i + 4:i + 4 + ln]
        i += 4 + ln
    return gefunden


def tonformat(pfad: str) -> dict:
    """Bittiefe, Kanaele und Abtastrate aus den Kopfdaten (soweit vorhanden)."""
    ergebnis: dict = {}
    groesse = os.path.getsize(pfad)
    with open(pfad, "rb") as f:
        for key, daten, laenge in klv_pakete(f, groesse):
            if _ist_tonessenz(key):
                break                     # ab hier kommen nur noch Nutzdaten
            # Metadaten-Sets beginnen alle mit 06 0e 2b 34 02 53 01 01.
            if key[:8] != bytes.fromhex("060e2b3402530101") or laenge > 1 << 16:
                continue
            tags = _lies_descriptor(f, daten, laenge)
            if _TAG_QUANTISIERUNG in tags and _TAG_KANAELE in tags:
                ergebnis["bits"] = int.from_bytes(tags[_TAG_QUANTISIERUNG], "big")
                ergebnis["kanaele"] = int.from_bytes(tags[_TAG_KANAELE], "big")
                if _TAG_BLOCKGROESSE in tags:
                    ergebnis["blockgroesse"] = int.from_bytes(
                        tags[_TAG_BLOCKGROESSE], "big")
                rate = tags.get(_TAG_ABTASTRATE)
                if rate and len(rate) >= 8:
                    zaehler = int.from_bytes(rate[:4], "big")
                    nenner = int.from_bytes(rate[4:8], "big") or 1
                    ergebnis["rate"] = int(round(zaehler / nenner))
                break
    return ergebnis


def lies_pcm(pfad: str, bits: int | None = None, kanaele: int | None = None,
             rate: int | None = None) -> tuple[np.ndarray, int] | None:
    """Liest den PCM-Ton einer OP-Atom-MXF.

    Liefert (samples[frames, kanaele] als float32, abtastrate) oder None, wenn
    die Datei keine unkomprimierte Tonessenz enthaelt.

    `bits`/`kanaele`/`rate` uebersteuern die Kopfdaten — sinnvoll, wenn der
    Aufrufer die Werte ohnehin schon aus dem AAF-Descriptor kennt.
    """
    if not os.path.isfile(pfad):
        return None
    kopf = tonformat(pfad)
    bits = bits or kopf.get("bits")
    kanaele = kanaele or kopf.get("kanaele")
    rate = rate or kopf.get("rate") or 48000
    if not bits or not kanaele:
        return None

    groesse = os.path.getsize(pfad)
    bloecke: list[bytes] = []
    with open(pfad, "rb") as f:
        for key, daten, laenge in klv_pakete(f, groesse):
            if not _ist_tonessenz(key) or laenge <= 0:
                continue
            # AES3 (typ 0x01) traegt einen eigenen Kopf je Element und andere
            # Wortbreiten — das waere geraten. Nur BWF/PCM wird gelesen.
            if key[12] == _ART_AES3_BWF and key[13] not in (0x02, 0x01):
                continue
            f.seek(daten)
            bloecke.append(f.read(laenge))
    if not bloecke:
        return None

    pcm = b"".join(bloecke) if len(bloecke) > 1 else bloecke[0]
    werte = hedwig_wavio.pcm_zu_float(pcm, bits)
    frames = werte.size // kanaele
    if frames <= 0:
        return None
    return werte[:frames * kanaele].reshape(frames, kanaele), int(rate)


def ist_mxf(pfad: str) -> bool:
    """Schnelltest über die MXF-Kennung am Dateianfang."""
    try:
        with open(pfad, "rb") as f:
            return f.read(4) == b"\x06\x0e\x2b\x34"
    except OSError:
        return False
