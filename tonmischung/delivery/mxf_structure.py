"""MXF-Struktur des Sendefiles prüfen: Partitionen, KAG, Index, RIP, System-Items, Kopfdaten, Ton-Descriptoren –
gegen ein Sendeprofil (XDCAM HD422 OP1a nach RDD 9, AVC-Intra OP1a).

Herkunft: punchbuddy-helmut `punchbuddy/mxfstruktur.py` (Stand 17.09.2026, gegen die IRT-Referenzclips HDF-01b und
HDF-02b geprüft). Übernommen: Lesen und Prüfen, reine Python-Logik ohne Werkzeuge, Essenz wird übersprungen.
Nicht übernommen: das Nachbessern von Kopfdaten in der Datei (bmx `--ard-zdf-hdf` schreibt sie richtig, live
03.10.2026). Die Namen im Modul sind wie im Original deutsch (`befunde`, `erwartung`); Abweichungen sind Warnungen.
Das Profil XDF-01 (AVC 1080p50, RDD 32) wurde am 13.10.2026 als ungenutzt entfernt – bei Bedarf (50p-Sendefile)
aus der Versionsgeschichte zurückholen.
"""
from __future__ import annotations

import os
import struct

# ── Schluessel ───────────────────────────────────────────────────────────────
_PART_PREFIX = bytes.fromhex("060e2b34020501010d01020101")     # + kind, status
_RIP_KEY = bytes.fromhex("060e2b34020501010d01020101110100")
_PRIMER_KEY = bytes.fromhex("060e2b34020501010d01020101050100")
_INDEX_KEY = bytes.fromhex("060e2b34025301010d01020101100100")
_SYSTEM_ITEM = bytes.fromhex("060e2b34020501010d01030104")       # System Item (GC)
_SET_PREFIX = bytes.fromhex("060e2b34025301010d01010101")       # Header-Metadaten-Sets

SETS = {   # letztes bedeutendes Byte des Set-Keys → Name
    0x2f: "Preface", 0x30: "Identification", 0x18: "ContentStorage",
    0x23: "EssenceContainerData", 0x36: "MaterialPackage", 0x37: "SourcePackage",
    0x3b: "Track", 0x0f: "Sequence", 0x14: "TimecodeComponent", 0x11: "SourceClip",
    0x28: "CDCIEssenceDescriptor", 0x51: "MPEGVideoDescriptor",
    0x42: "GenericSoundEssenceDescriptor", 0x44: "MultipleDescriptor",
    0x48: "WaveAudioDescriptor", 0x47: "AES3AudioDescriptor", 0x6e: "AVCSubDescriptor",
}

# Statische lokale Tags (ST 377-1) und ULs dynamischer Eigenschaften (ueber Primer)
TAGS = {
    0x3b05: "Version", 0x3b09: "OperationalPattern", 0x3b0a: "EssenceContainers",
    0x3c01: "CompanyName", 0x3c02: "ProductName", 0x3c03: "ProductVersion",
    0x3c04: "VersionString", 0x3c07: "ToolkitVersion", 0x3c08: "Platform",
    0x1901: "Packages", 0x4402: "Name", 0x4403: "Tracks",
    0x4801: "TrackID", 0x4804: "TrackNumber", 0x4802: "TrackName", 0x4803: "Sequence",
    0x4b01: "EditRate", 0x4b02: "Origin", 0x0201: "DataDefinition", 0x0202: "Duration",
    0x1501: "StartTimecode", 0x1502: "RoundedTimecodeBase", 0x1503: "DropFrame",
    0x4701: "Descriptor", 0x3001: "SampleRate", 0x3002: "ContainerDuration",
    0x3004: "EssenceContainer", 0x3006: "LinkedTrackID",
    0x3215: "SignalStandard", 0x320c: "FrameLayout", 0x3203: "StoredWidth", 0x3202: "StoredHeight",
    0x3205: "SampledWidth", 0x3204: "SampledHeight", 0x3206: "SampledXOffset", 0x3207: "SampledYOffset",
    0x3209: "DisplayWidth", 0x3208: "DisplayHeight", 0x320a: "DisplayXOffset", 0x320b: "DisplayYOffset",
    0x320e: "AspectRatio", 0x320d: "VideoLineMap", 0x3210: "CaptureGamma",
    0x3213: "ImageStartOffset", 0x3214: "ImageEndOffset", 0x3201: "PictureEssenceCoding",
    0x321a: "CodingEquations", 0x3219: "ColorPrimaries",
    0x3301: "ComponentDepth", 0x3302: "HorizontalSubsampling", 0x3308: "VerticalSubsampling",
    0x3303: "ColorSiting", 0x3304: "BlackRefLevel", 0x3305: "WhiteRefLevel", 0x3306: "ColorRange",
    0x3d03: "AudioSamplingRate", 0x3d02: "Locked", 0x3d07: "ChannelCount",
    0x3d01: "QuantizationBits", 0x3d06: "SoundEssenceCompression",
    0x3d0a: "BlockAlign", 0x3d09: "AvgBps", 0x3d10: "ChannelStatusMode", 0x3d11: "FixedChannelStatusData",
    0x3f01: "SubDescriptorUIDs",
    0x3f0b: "IndexEditRate", 0x3f0c: "IndexStartPosition", 0x3f0d: "IndexDuration",
    0x3f05: "EditUnitByteCount", 0x3f06: "IndexSID", 0x3f07: "BodySID", 0x3f08: "SliceCount",
    0x3f0e: "PosTableCount", 0x3f09: "DeltaEntryArray", 0x3f0a: "IndexEntryArray",
}
ULS = {   # UL (hex) → Name, fuer Eigenschaften ohne statischen Tag
    "060e2b34010101090601010406100000": "SubDescriptors",
    "060e2b34010101050402050102000000": "ChannelStatusMode",
    "060e2b34010101050402050103000000": "FixedChannelStatusData",
    "060e2b340101010e04010606010e0000": "AVCDecodingDelay",
    "060e2b340101010e0401060601030000": "AVCConstantBPictureFlag",
    "060e2b340101010e0401060601040000": "AVCCodedContentKind",
    "060e2b340101010e0401060601060000": "AVCClosedGOPIndicator",
    "060e2b340101010e0401060601070000": "AVCIdenticalGOPIndicator",
    "060e2b340101010e0401060601080000": "AVCMaximumGOPSize",
    "060e2b340101010e0401060601090000": "AVCMaximumBPictureCount",
    "060e2b340101010e04010606010b0000": "AVCMaximumBitrate",
    "060e2b340101010e0401060601140000": "AVCAverageBitrate",
    "060e2b340101010e04010606010a0000": "AVCProfile",
    "060e2b340101010e04010606010c0000": "AVCProfileConstraint",
    "060e2b340101010e04010606010d0000": "AVCLevel",
    "060e2b340101010e04010606010f0000": "AVCMaximumRefFrames",
    "060e2b340101010e0401060601100000": "AVCSequenceParameterSetFlag",
    "060e2b340101010e0401060601110000": "AVCPictureParameterSetFlag",
    "060e2b34010101050401060201060000": "ClosedGOP",
    "060e2b34010101050401060201080000": "MaxGOP",
    "060e2b340101010504010602010b0000": "BitRate",
    "060e2b340101010504010602010a0000": "ProfileAndLevel",
}

_STATUS = {1: "Open Incomplete", 2: "Closed Incomplete", 3: "Open Complete", 4: "Closed Complete"}
_KIND = {2: "Header", 3: "Body", 4: "Footer"}


# ── KLV-Leser ────────────────────────────────────────────────────────────────
def _ber(f):
    b = f.read(1)
    if not b:
        return None
    n = b[0]
    if n < 0x80:
        return n
    return int.from_bytes(f.read(n & 0x7f), "big")


def _u16(b): return struct.unpack(">H", b[:2])[0]
def _u32(b): return struct.unpack(">I", b[:4])[0]
def _u64(b): return struct.unpack(">Q", b[:8])[0]
def _i64(b): return struct.unpack(">q", b[:8])[0]
def _rat(b): return f"{struct.unpack('>i', b[:4])[0]}/{struct.unpack('>i', b[4:8])[0]}"


def _partition_pack(d):
    n_ec = _u32(d[80:84]) if len(d) >= 88 else 0
    ecs = [d[88 + 16 * i:104 + 16 * i].hex() for i in range(min(n_ec, 16))]
    return {"kag": _u32(d[4:8]), "this": _u64(d[8:16]), "prev": _u64(d[16:24]), "footer": _u64(d[24:32]),
            "header_bytes": _u64(d[32:40]), "index_bytes": _u64(d[40:48]), "index_sid": _u32(d[48:52]),
            "body_offset": _u64(d[52:60]), "body_sid": _u32(d[60:64]), "op": d[64:80].hex(),
            "essence_containers": ecs}


def _index_segment(d):
    seg, p = {}, 0
    while p + 4 <= len(d):
        tag, tl = _u16(d[p:p + 2]), _u16(d[p + 2:p + 4])
        v = d[p + 4:p + 4 + tl]
        p += 4 + tl
        name = TAGS.get(tag)
        if name == "IndexEditRate":
            seg["edit_rate"] = _rat(v)
        elif name == "IndexStartPosition":
            seg["start"] = _i64(v)
        elif name == "IndexDuration":
            seg["dur"] = _i64(v)
        elif name == "EditUnitByteCount":
            seg["eubc"] = _u32(v)
        elif name == "IndexSID":
            seg["index_sid"] = _u32(v)
        elif name == "BodySID":
            seg["body_sid"] = _u32(v)
        elif name == "SliceCount":
            seg["slices"] = v[0]
        elif name == "PosTableCount":
            seg["pos_tables"] = v[0]
        elif name == "DeltaEntryArray":
            seg["delta_count"] = _u32(v[:4])
            n, sz = _u32(v[:4]), _u32(v[4:8])
            seg["pos_table_index"] = ([struct.unpack(">b", v[8 + i * sz:9 + i * sz])[0] for i in range(min(n, 64))]
                                      if sz >= 6 else [])
        elif name == "IndexEntryArray":
            seg["entries"] = _u32(v[:4])
    return seg


def _decode(name, v):
    """Wert einer Eigenschaft in eine lesbare Form (nach Name)."""
    if name in ("CompanyName", "ProductName", "VersionString", "Platform", "Name", "TrackName"):
        try:
            return v.decode("utf-16-be").rstrip("\x00")
        except Exception:
            return v.hex()
    if name in ("ProductVersion", "ToolkitVersion"):
        return ".".join(str(_u16(v[i:i + 2])) for i in range(0, min(len(v), 10), 2))
    if name in ("EditRate", "SampleRate", "AspectRatio", "AudioSamplingRate", "IndexEditRate"):
        return _rat(v)
    if name == "VideoLineMap":
        n = _u32(v[:4])
        return [struct.unpack(">i", v[8 + 4 * i:12 + 4 * i])[0] for i in range(n)]
    if name in ("Version",):
        return f"{_u16(v[:2]) if len(v) == 2 else v.hex()}"
    if name in ("OperationalPattern", "PictureEssenceCoding", "EssenceContainer", "SoundEssenceCompression",
                "CaptureGamma", "ColorPrimaries", "CodingEquations", "DataDefinition"):
        return v.hex()
    if name in ("EssenceContainers", "Packages", "Tracks", "SubDescriptorUIDs", "SubDescriptors"):
        n = _u32(v[:4])
        return [v[8 + 16 * i:24 + 16 * i].hex() for i in range(n)]
    if name in ("Descriptor", "Sequence"):
        return v.hex()
    if name in ("FixedChannelStatusData", "AVCProfileConstraint", "AVCSequenceParameterSetFlag",
                "AVCPictureParameterSetFlag", "ProfileAndLevel"):
        return v.hex()
    if name == "ChannelStatusMode":
        return v.hex()
    if len(v) == 1:
        return v[0]
    if len(v) == 2:
        return _u16(v)
    if len(v) == 4:
        return _u32(v)
    if len(v) == 8:
        return _i64(v)
    return v.hex()


def _set_aus(key, val, primer):
    name = SETS.get(key[14], f"Set{key[14]:02x}")
    props, r = {}, 0
    while r + 4 <= len(val):
        tag, tl = _u16(val[r:r + 2]), _u16(val[r + 2:r + 4])
        v = val[r + 4:r + 4 + tl]
        r += 4 + tl
        pname = TAGS.get(tag) or ULS.get(primer.get(tag, ""))
        if pname:
            props[pname] = _decode(pname, v)
    return name, props


def _primer_aus(val):
    primer = {}
    n, sz = _u32(val[:4]), _u32(val[4:8])
    for i in range(n):
        e = val[8 + i * sz:8 + (i + 1) * sz]
        primer[_u16(e[:2])] = e[2:18].hex()
    return primer


def read_structure(path):
    """Struktur der MXF-Datei: Partitionen, Index-Segmente, RIP, System-Items,
    Kopfdaten-Sets der Header-Partition. Essenz wird uebersprungen (schnell
    auch bei GB-Dateien). Kopfdaten werden im selben KLV-Lauf gelesen – ein
    KAG-Fill zwischen Partition-Pack und Primer stoert so nicht."""
    st = {"datei": path, "groesse": os.path.getsize(path), "partitionen": [], "index": [],
          "rip": False, "system_items": 0, "sets": [], "op": ""}
    primer, in_header = {}, False
    with open(path, "rb") as f:
        size = st["groesse"]
        while f.tell() < size:
            pos = f.tell()
            key = f.read(16)
            if len(key) < 16:
                break
            ln = _ber(f)
            if ln is None:
                break
            dstart = f.tell()
            if dstart + ln > size:           # Block reicht über das Dateiende: abgeschnitten (Review A20)
                st["abgeschnitten"] = pos
                break
            if key[:13] == _PART_PREFIX and key[13] in _KIND:
                pp = _partition_pack(f.read(min(ln, 4096)))
                pp.update({"kind": _KIND[key[13]], "status": _STATUS.get(key[14], str(key[14])), "pos": pos})
                st["partitionen"].append(pp)
                in_header = pp["kind"] == "Header" and not st["sets"]
                if in_header:
                    st["op"] = pp["op"]
            elif key == _RIP_KEY:
                st["rip"] = True
            elif key == _INDEX_KEY:
                st["index"].append(_index_segment(f.read(ln)))
                continue
            elif key == _PRIMER_KEY and in_header:
                primer = _primer_aus(f.read(ln))
                continue
            elif key[:13] == _SET_PREFIX and key[13] == 0x01 and key[15] == 0x00 and in_header:
                st["sets"].append(_set_aus(key, f.read(ln), primer))
                continue
            elif key[:12] == _SYSTEM_ITEM[:12] and key[12] == 0x04:
                st["system_items"] += 1
            f.seek(dstart + ln)
    return st


def sets_named(st, name):
    return [p for n, p in st["sets"] if n == name]


# ── Profile (Erwartungen als Daten) ──────────────────────────────────────────
# Werte nach den IRT-Referenzclips HDF-01b (XDCAM) und HDF-02b (AVC-Intra).
PROFILES = {
    "rdd9_xdcam": {
        "name": "XDCAM HD422 OP1a (RDD 9, wie IRT HDF-01b)",
        "kag": 512, "rip": True, "system_items": True,
        "header_status": ["Closed Complete", "Open Incomplete"],
        "body_status": ["Closed Complete"], "footer_status": ["Closed Complete"],
        "index_max_dur": 240, "index_edit_rate": "25/1", "index_eubc": 0, "index_slices": 1,
        "identification": ["CompanyName", "ProductName", "ProductVersion"],
        "audio_tracks": [8, 16],
        "audio": {"AudioSamplingRate": "48000/1", "ChannelCount": 1, "QuantizationBits": 24, "BlockAlign": 3},
        "audio_container": "060e2b34040101010d01030102060300",
    },
    "avci_op1a": {
        "name": "AVC-Intra OP1a (wie IRT HDF-02b)",
        "rip": True, "system_items": True,
        "header_status": ["Closed Complete", "Open Incomplete"], "footer_status": ["Closed Complete"],
        "index_edit_rate": "25/1",
        "identification": ["CompanyName", "ProductName", "ProductVersion"],
        "audio_tracks": [8, 16],
        "audio": {"AudioSamplingRate": "48000/1", "ChannelCount": 1, "QuantizationBits": 24, "BlockAlign": 3},
        "audio_container": "060e2b34040101010d01030102060300",
    },
}


# Abweichungen sind in der ARD/ZDF-Prüfung Warnungen („[nnW]“); die Bildkodierung („[36F]“) prüft die QC selbst
def _finding(check, ok, soll, ist, hinweis="", severity="WARN"):
    return {"check": check, "status": "PASS" if ok else severity, "expected": str(soll), "actual": str(ist),
            "message": hinweis}


def check(st, profil, erwartung=None):  # noqa: C901 – Liste der Merkmale
    """Prueft eine gelesene Struktur gegen ein Profil (Name oder dict).
    Liefert eine Liste von Befunden (check/status/expected/actual/message).
    Abweichungen sind Warnungen – so stuft auch die ARD/ZDF-Profilpruefung ihre Strukturmerkmale ein
    (Kennung "[nnW]")."""
    p = dict(PROFILES[profil] if isinstance(profil, str) else profil)
    if erwartung:
        p.update(erwartung)

    def _f(c, ok, soll, ist, hinweis=""):
        return _finding(c, ok, soll, ist, hinweis)
    b = []
    parts = st["partitionen"]
    if not parts:
        return [_f("MXF-Partitionen", False, ">= 2", 0, "keine Partition-Packs gefunden – keine MXF-Datei?")]
    hdr = [x for x in parts if x["kind"] == "Header"]
    body = [x for x in parts if x["kind"] == "Body"]
    foot = [x for x in parts if x["kind"] == "Footer"]
    b.append(_f("MXF Header-Partition",
                len(hdr) == 1 and hdr[0]["status"] in p.get("header_status", [hdr[0]["status"]]),
                "/".join(p.get("header_status", [])), hdr[0]["status"] if hdr else "fehlt"))
    b.append(_f("MXF Footer-Partition",
                len(foot) == 1 and foot[0]["status"] in p.get("footer_status", [foot[0]["status"]]),
                "/".join(p.get("footer_status", [])), foot[0]["status"] if foot else "fehlt"))
    if "body_status" in p and body:
        schlecht = [x["status"] for x in body if x["status"] not in p["body_status"]]
        b.append(_f("MXF Body-Partitionen", not schlecht, "/".join(p["body_status"]),
                    f"{len(body)} Partition(en)" + (f", abweichend: {schlecht[:2]}" if schlecht else "")))
    if "kag" in p:
        kags = sorted({x["kag"] for x in parts})
        b.append(_f("MXF KAG-Größe", kags == [p["kag"]], p["kag"], ", ".join(map(str, kags)),
                    "muss in allen Partition-Packs gleich sein"))
    if p.get("rip"):
        b.append(_f("MXF Random Index Pack", st["rip"], "vorhanden", "vorhanden" if st["rip"] else "fehlt"))
    if p.get("system_items"):
        b.append(_f("MXF System-Items", st["system_items"] > 0, "je Bild eines", st["system_items"]))
    if "op" in p:
        b.append(_f("MXF Operational Pattern", st["op"].startswith(p["op"]), "OP1a", st["op"]))
    # Index
    idx = st["index"]
    if "index_edit_rate" in p:
        rates = sorted({s.get("edit_rate", "?") for s in idx})
        b.append(_f("Index Edit-Rate", rates == [p["index_edit_rate"]], p["index_edit_rate"],
                    ", ".join(rates) or "kein Index"))
    if "index_max_dur" in p:
        mx = max((s.get("dur", 0) for s in idx), default=0)
        b.append(_f("Index-Dauer je Segment", 0 < mx <= p["index_max_dur"], f"<= {p['index_max_dur']}", mx,
                    f"{len(idx)} Segment(e)"))
    if "index_eubc" in p:
        eub = sorted({s.get("eubc", -1) for s in idx})
        b.append(_f("Index Edit-Unit-Byte-Count", eub == [p["index_eubc"]], p["index_eubc"],
                    ", ".join(map(str, eub)) or "kein Index", "0 = variabel (Long GOP)"))
    if "index_slices" in p:
        sl = sorted({s.get("slices", -1) for s in idx})
        b.append(_f("Index Slice-Count", sl == [p["index_slices"]], p["index_slices"], ", ".join(map(str, sl))))
    if "index_pos_table_video" in p and idx:
        # Delta-Eintrag des Videos (nach dem System-Item der zweite, sonst der erste)
        pti = [s.get("pos_table_index") for s in idx if s.get("pos_table_index")]
        ok = (all(p["index_pos_table_video"] in (pt[0], pt[1] if len(pt) > 1 else None) for pt in pti)
              if pti else False)
        b.append(_f("Index Position-Table (Video)", ok, p["index_pos_table_video"], pti[0][:3] if pti else "fehlt",
                    "-1 = Temporal Reordering"))
    # Kopfdaten
    pref = sets_named(st, "Preface")
    if "mxf_version" in p and pref:
        b.append(_f("MXF-Version (Preface)", str(pref[0].get("Version")) == p["mxf_version"], "259 (1.3)",
                    pref[0].get("Version")))
    if "identification" in p:
        ident = sets_named(st, "Identification")
        fehlt = [k for k in p["identification"] if not ident or not ident[-1].get(k)]
        b.append(_f("Identification Set", bool(ident) and not fehlt, ", ".join(p["identification"]),
                    (f"{ident[-1].get('CompanyName', '')} / {ident[-1].get('ProductName', '')} "
                     f"{ident[-1].get('VersionString', '')}" if ident else "fehlt")
                    + (f" – fehlt: {fehlt}" if fehlt else "")))
    # Spuren im Material Package
    mp = sets_named(st, "MaterialPackage")
    if "audio_tracks" in p and mp:
        # Tonspuren ueber die Zahl der Ton-Descriptoren (eine je AES3-Spur)
        snd = [s for n, s in st["sets"]
               if n in ("WaveAudioDescriptor", "AES3AudioDescriptor", "GenericSoundEssenceDescriptor")]
        b.append(_f("Tonspuren (Descriptoren)", len(snd) in p["audio_tracks"], "/".join(map(str, p["audio_tracks"])),
                    len(snd)))
    # Bild-Descriptor
    vid = sets_named(st, "CDCIEssenceDescriptor") or sets_named(st, "MPEGVideoDescriptor")
    if "video" in p:
        if not vid:
            b.append(_f("Bild-Descriptor", False, "CDCI", "fehlt"))
        else:
            v = vid[0]
            for k, soll in p["video"].items():
                ist = v.get(k, "fehlt")
                b.append(_f(f"Bild {k}", str(ist).lower() == str(soll).lower(), soll, ist))
    if "video_container" in p and vid:
        b.append(_f("Bild Essence-Container",
                    vid[0].get("EssenceContainer", "").startswith(p["video_container"][:30]),
                    p["video_container"], vid[0].get("EssenceContainer", "fehlt")))
    # AVC-Sub-Descriptor
    if "avc" in p:
        avc = sets_named(st, "AVCSubDescriptor")
        if not avc:
            b.append(_f("AVC-Sub-Descriptor", False, "vorhanden (ST 381-3)", "fehlt"))
        else:
            a = avc[0]
            for k, soll in p["avc"].items():
                ist = a.get(k, "fehlt")
                if k == "AVCMaximumBitrate":
                    ok = isinstance(ist, int) and ist <= int(soll)
                    b.append(_f(f"AVC {k}", ok, f"<= {soll}", ist))
                else:
                    b.append(_f(f"AVC {k}", str(ist) == str(soll), soll, ist))
    # Ton-Descriptoren
    if "audio" in p:
        snd = [s for n, s in st["sets"] if n in ("WaveAudioDescriptor", "AES3AudioDescriptor")]
        if not snd:
            b.append(_f("Ton-Descriptoren", False, "AES3/Wave", "fehlen"))
        else:
            for k, soll in p["audio"].items():
                abw = [s.get(k, "fehlt") for s in snd if str(s.get(k, "fehlt")).lower() != str(soll).lower()]
                b.append(_f(f"Ton {k}", not abw, soll,
                            f"{len(snd)} Spuren ok" if not abw else f"abweichend: {abw[:3]}"))
    if "audio_container" in p:
        snd = [s for n, s in st["sets"] if n in ("WaveAudioDescriptor", "AES3AudioDescriptor")]
        abw = [s.get("EssenceContainer", "fehlt") for s in snd
               if not s.get("EssenceContainer", "").startswith(p["audio_container"][:28])]
        b.append(_f("Ton Essence-Container (AES3)", bool(snd) and not abw, p["audio_container"],
                    "alle AES3" if snd and not abw else (abw[:1] or "fehlt")))
    return b


def summary(befunde):
    """('PASS'|'WARN'|'FAIL', Liste der Abweichungen)."""
    abw = [b for b in befunde if b["status"] != "PASS"]
    if not abw:
        return "PASS", []
    return ("FAIL" if any(b["status"] == "FAIL" for b in abw) else "WARN"), abw
