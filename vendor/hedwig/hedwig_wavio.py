#!/usr/bin/env python3
"""WAV-Lesen/-Schreiben für die Python-Schicht (numpy).

Pythons `wave`-Modul kann kein 24 bit und kein Float — beides kommt in
Broadcast-Material ständig vor. Daher ein eigener, kleiner RIFF-Parser.
Intern wird durchgängig **float32** verwendet (wie im C++-Kern, §3.9).
"""
from __future__ import annotations

import struct

import numpy as np

WAVE_FORMAT_PCM = 1
WAVE_FORMAT_FLOAT = 3
WAVE_FORMAT_EXTENSIBLE = 0xFFFE


def pcm_zu_float(pcm: bytes, bits: int, float_format: bool = False) -> np.ndarray:
    """Roher PCM-Block -> float32 (-1..1), verschachtelt wie im Eingang.

    Eigene Funktion, weil nicht nur WAV rohes PCM liefert: Avid-MXF (OP-Atom)
    enthaelt denselben Block ohne jeden RIFF-Kopf.
    """
    if float_format and bits == 32:
        return np.frombuffer(pcm, dtype="<f4")
    if bits == 16:
        return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if bits == 24:
        raw = np.frombuffer(pcm[:len(pcm) - len(pcm) % 3], dtype=np.uint8).reshape(-1, 3)
        # 24-bit little-endian, vorzeichenbehaftet -> int32 -> float
        v = (raw[:, 0].astype(np.int32)
             | (raw[:, 1].astype(np.int32) << 8)
             | (raw[:, 2].astype(np.int32) << 16))
        v = np.where(v & 0x800000, v - (1 << 24), v)
        return v.astype(np.float32) / 8388608.0
    if bits == 32:
        return np.frombuffer(pcm, dtype="<i4").astype(np.float32) / 2147483648.0
    if bits == 8:
        return (np.frombuffer(pcm, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    raise ValueError(f"Bittiefe {bits} nicht unterstuetzt")


def wav_format(path: str) -> tuple[int, bool]:
    """(Bittiefe, ist_gleitkomma) einer WAV-Datei — ohne sie ganz zu lesen.

    Gebraucht, um das Eingangsformat **beizubehalten**: 32-bit-Material bleibt
    32 bit. Bei 32 bit *Float* ist das keine Formalie, sondern der ganze Sinn
    der Aufnahme — dort steckt die Reserve oberhalb 0 dBFS, die ein
    Feldrecorder mitschreibt. Als Ganzzahl zurueckgeschrieben waere sie weg.
    """
    with open(path, "rb") as f:
        kopf = f.read(4096)
    if len(kopf) < 12 or kopf[:4] != b"RIFF":
        return 24, False
    pos = 12
    while pos + 8 <= len(kopf):
        cid = kopf[pos:pos + 4]
        sz = struct.unpack("<I", kopf[pos + 4:pos + 8])[0]
        if cid == b"fmt " and pos + 8 + 16 <= len(kopf):
            body = kopf[pos + 8:pos + 8 + sz]
            fmt, _ch, _sr, _br, _ba, bits = struct.unpack("<HHIIHH", body[:16])
            if fmt == WAVE_FORMAT_EXTENSIBLE and len(body) >= 26:
                fmt = struct.unpack("<H", body[24:26])[0]
            return int(bits), fmt == WAVE_FORMAT_FLOAT
        pos += 8 + sz + (sz & 1)
    return 24, False


def wav_kopf(path: str) -> dict:
    """Kenndaten einer WAV-Datei, ohne die Abtastwerte zu lesen.

    Gebraucht ueberall dort, wo eine Datei zu gross ist, um sie am Stueck in
    den Speicher zu nehmen: Eine Spur eines 43-Minuten-Beitrags hat 125
    Millionen Abtastwerte und belegt als float32 eine halbe Gigabyte. Fuer
    eine Wellenform oder einen kurzen Ausschnitt ist das absurd — dafuer
    braucht es nur zu wissen, wo die Daten liegen.
    """
    with open(path, "rb") as f:
        kopf = f.read(65536)
        if len(kopf) < 12 or kopf[:4] != b"RIFF" or kopf[8:12] != b"WAVE":
            raise ValueError(f"Keine RIFF/WAVE-Datei: {path}")
        f.seek(0, 2)
        dateigroesse = f.tell()

    fmt, ch, sr, bits = WAVE_FORMAT_PCM, 1, 48000, 16
    offset = bytes_ = 0
    pos = 12
    while pos + 8 <= len(kopf):
        cid = kopf[pos:pos + 4]
        sz = struct.unpack("<I", kopf[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            body = kopf[pos + 8:pos + 8 + max(sz, 16)]
            if len(body) >= 16:
                fmt, ch, sr, _br, _ba, bits = struct.unpack("<HHIIHH", body[:16])
                if fmt == WAVE_FORMAT_EXTENSIBLE and len(body) >= 26:
                    fmt = struct.unpack("<H", body[24:26])[0]
        elif cid == b"data":
            offset = pos + 8
            # Die angegebene Groesse kann luegen (abgebrochene Aufnahme,
            # RF64): gegen die reale Dateigroesse begrenzen.
            bytes_ = min(sz, dateigroesse - offset)
            break
        pos += 8 + sz + (sz & 1)

    if not ch or not bits:
        raise ValueError(f"Kanalzahl oder Bittiefe 0 in {path}")
    if not offset:
        raise ValueError(f"Kein data-Chunk in {path}")
    rahmen_bytes = ch * (bits // 8)
    return {"format": fmt, "kanaele": int(ch), "rate": int(sr), "bits": int(bits),
            "gleitkomma": fmt == WAVE_FORMAT_FLOAT,
            "daten_offset": offset, "daten_bytes": bytes_,
            "rahmen_bytes": rahmen_bytes,
            "frames": bytes_ // rahmen_bytes if rahmen_bytes else 0,
            "sekunden": (bytes_ // rahmen_bytes) / sr if rahmen_bytes and sr else 0.0}


def lies_bereich(path: str, von_frame: int, frames: int,
                 kopf: dict | None = None) -> tuple[np.ndarray, int]:
    """Liest nur einen Ausschnitt — fuer tiefe Zoomstufen und Vorschauen.

    Bei starker Vergroesserung will die Anzeige die echten Abtastwerte sehen,
    nicht eine Zusammenfassung. Die ganze Datei dafuer zu laden waere bei
    einer 376-MB-Spur unverhaeltnismaessig; hier wird gezielt gesprungen.
    """
    k = kopf or wav_kopf(path)
    von_frame = max(0, min(int(von_frame), k["frames"]))
    frames = max(0, min(int(frames), k["frames"] - von_frame))
    if not frames:
        return np.zeros((0, k["kanaele"]), dtype=np.float32), k["rate"]
    with open(path, "rb") as f:
        f.seek(k["daten_offset"] + von_frame * k["rahmen_bytes"])
        roh = f.read(frames * k["rahmen_bytes"])
    a = pcm_zu_float(roh, k["bits"], k["gleitkomma"])
    n = len(a) // k["kanaele"]
    return a[:n * k["kanaele"]].reshape(n, k["kanaele"]), k["rate"]


def lies_bloecke(path: str, block_frames: int = 1 << 19, kopf: dict | None = None):
    """Liest eine WAV-Datei blockweise — fuer Arbeiten ueber die ganze Laenge.

    Eine halbe Million Rahmen je Block (rund 11 Sekunden bei 48 kHz) ist der
    Kompromiss: gross genug, dass numpy nicht an der Blockgrenze
    verhungert, klein genug, dass 25 Spuren nacheinander laufen koennen,
    ohne dass der Speicher zulaeuft.
    """
    k = kopf or wav_kopf(path)
    gelesen = 0
    with open(path, "rb") as f:
        f.seek(k["daten_offset"])
        while gelesen < k["frames"]:
            n = min(block_frames, k["frames"] - gelesen)
            roh = f.read(n * k["rahmen_bytes"])
            if not roh:
                break
            a = pcm_zu_float(roh, k["bits"], k["gleitkomma"])
            m = len(a) // k["kanaele"]
            if not m:
                break
            yield gelesen, a[:m * k["kanaele"]].reshape(m, k["kanaele"])
            gelesen += m


def read_wav(path: str) -> tuple[np.ndarray, int]:
    """Liest eine WAV-Datei. Liefert (samples[frames, channels] als float32, samplerate)."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"Keine RIFF/WAVE-Datei: {path}")

    fmt, ch, sr, bits, pcm = WAVE_FORMAT_PCM, 1, 48000, 16, b""
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        sz = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        sz = min(sz, len(data) - pos - 8)  # kaputte Header nicht vertrauen
        body = data[pos + 8:pos + 8 + sz]
        if cid == b"fmt " and len(body) >= 16:
            fmt, ch, sr, _br, _ba, bits = struct.unpack("<HHIIHH", body[:16])
            if fmt == WAVE_FORMAT_EXTENSIBLE and len(body) >= 26:
                fmt = struct.unpack("<H", body[24:26])[0]  # echtes Format im SubFormat
        elif cid == b"data":
            pcm = body
        pos += 8 + sz + (sz & 1)  # ungerade Chunks sind gepaddet

    if not ch:
        raise ValueError(f"Kanalzahl 0 in {path}")

    try:
        a = pcm_zu_float(pcm, bits, float_format=(fmt == WAVE_FORMAT_FLOAT))
    except ValueError as e:
        raise ValueError(f"Format nicht unterstuetzt: fmt={fmt} bits={bits} ({path})") from e

    frames = len(a) // ch
    return a[:frames * ch].reshape(frames, ch), sr


def write_wav(path: str, samples: np.ndarray, sample_rate: int, bits: int = 24,
              gleitkomma: bool = False) -> str:
    """Schreibt float32-Samples[frames, channels] als WAV.

    `gleitkomma=True` schreibt 32-bit-Float statt Ganzzahl — dann wird
    **nicht** auf ±1,0 begrenzt, damit die Reserve oberhalb 0 dBFS erhalten
    bleibt (siehe `wav_format`).
    """
    if samples.ndim == 1:
        samples = samples.reshape(-1, 1)
    frames, ch = samples.shape

    if gleitkomma:
        if bits != 32:
            raise ValueError("Gleitkomma-WAV nur mit bits=32")
        pcm = np.asarray(samples, dtype="<f4").tobytes()
        block = ch * 4
        fmt_chunk = struct.pack("<HHIIHH", WAVE_FORMAT_FLOAT, ch, sample_rate,
                                sample_rate * block, block, 32)
        return _schreibe(path, fmt_chunk, pcm)

    x = np.clip(samples, -1.0, 1.0)

    if bits == 16:
        pcm = (x * 32767.0).round().astype("<i2").tobytes()
    elif bits == 24:
        v = (x * 8388607.0).round().astype(np.int32).ravel()
        b = np.empty((v.size, 3), dtype=np.uint8)
        b[:, 0] = v & 0xFF
        b[:, 1] = (v >> 8) & 0xFF
        b[:, 2] = (v >> 16) & 0xFF
        pcm = b.tobytes()
    elif bits == 32:
        # In doppelter Genauigkeit und mit hartem Deckel.
        #
        # 2147483647 laesst sich in float32 nicht darstellen — der naechste
        # Wert ist 2147483648. Ein voll ausgesteuerter Abtastwert lief damit
        # beim Umwandeln in int32 ueber und wurde zum Vollausschlag mit
        # falschem Vorzeichen: ein Knacks an der lautesten Stelle. Bei 16 und
        # 24 bit passt der Faktor exakt in float32, dort gibt es das nicht.
        v = np.clip(np.asarray(x, dtype=np.float64) * 2147483647.0,
                    -2147483648.0, 2147483647.0)
        pcm = v.round().astype("<i4").tobytes()
    else:
        raise ValueError(f"bits={bits} nicht unterstuetzt (16/24/32)")

    block = ch * (bits // 8)
    fmt_chunk = struct.pack("<HHIIHH", WAVE_FORMAT_PCM, ch, sample_rate,
                            sample_rate * block, block, bits)
    return _schreibe(path, fmt_chunk, pcm)


def riff_kopf(kanaele: int, rate: int, bits: int, gleitkomma: bool,
              daten_bytes: int) -> bytes:
    """Ein WAV-Kopf fuer rohe Abtastwerte, die schon vorliegen.

    Fuer das fensterweise Abhoeren: Dort wird ein Ausschnitt aus einer
    bestehenden Datei ausgeliefert, und der Weg soll ihn **nicht anfassen**.
    Ihn zu dekodieren, um ihn gleich wieder zu kodieren, kostet Zeit und
    verschiebt bei jeder Wandlung das letzte Bit — ausgerechnet beim
    Abhoeren, wo man beurteilen will, was wirklich in der Datei steht.

    Der Browser dagegen braucht einen Kopf: `decodeAudioData` verlangt einen
    gueltigen Container, ein blosser Byte-Bereich hat keinen.
    """
    block = kanaele * (bits // 8)
    fmt = WAVE_FORMAT_FLOAT if gleitkomma else WAVE_FORMAT_PCM
    fmt_chunk = struct.pack("<HHIIHH", fmt, kanaele, rate,
                            rate * block, block, bits)
    return (b"RIFF"
            + struct.pack("<I", 4 + 8 + len(fmt_chunk) + 8 + daten_bytes)
            + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
            + b"data" + struct.pack("<I", daten_bytes))


def _schreibe(path: str, fmt_chunk: bytes, pcm: bytes) -> str:
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 4 + 8 + len(fmt_chunk) + 8 + len(pcm) + (len(pcm) & 1)))
        f.write(b"WAVE")
        f.write(b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk)
        f.write(b"data" + struct.pack("<I", len(pcm)) + pcm)
        if len(pcm) & 1:
            f.write(b"\0")
    return path
