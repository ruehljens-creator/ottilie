"""Was das Fenster zeigt – reine Funktionen von Kerndaten auf JSON-fähige Werte.

Texte und Kennzeichnungen wie im Prototyp der Oberfläche. Felder, die
Helmut (noch) nicht liefert – Sendetermin, Länge, Cutter, Dringlichkeit –, erscheinen als
„–“ (ANNAHME bis zur API-Beschreibung).
"""
from __future__ import annotations

import os
import re
from collections.abc import Sequence
from datetime import datetime

from tonmischung.core import mixstate
from tonmischung.helmut.mapping import Contribution, Handover

NONE = "–"
RELEASE_REASONS = mixstate.RELEASE_REASONS

STATE_TEXT = mixstate.STATE_LABEL


def is_mine(c: Contribution, mixer: str, room: str) -> bool:
    return mixstate.owned_by(c.mix, mixer, room)


def _pill(c: Contribution, mixer: str, room: str) -> tuple[str, str]:
    """(Text, CSS-Klasse) der Status-Plakette."""
    s = c.mix.state
    if s in mixstate.OWNED:
        if is_mine(c, mixer, room):
            return f"meine Mischung · {STATE_TEXT[s]}", "mine"
        return f"in Mischung · {c.mix.mixer or 'jemand'} ({c.mix.room or '?'})", "busy"
    if s == mixstate.DELIVERED:
        return "ausgeliefert", "sent"
    if s == mixstate.RETURNED:
        return "zurückgegeben · bereit", "ready"
    return STATE_TEXT.get(s, s), "ready"


def _field(c: Contribution, *names: str) -> str:
    """ANNAHME: zusätzliche Projektfelder, falls Helmut sie liefert; sonst „–“."""
    for n in names:
        v = c.raw.get(n) if isinstance(c.raw, dict) else None
        if v not in (None, ""):
            return str(v)
    return NONE


def handover_time(c: Contribution) -> str:
    """Übergabezeit (ISO) – ANNAHME: Feld „uebergeben_um“ in den Metadaten, sonst Anlagezeit des Projekts
    (`createDate`); was Helmut bei der Übergabe genau setzt, klären wir am echten System."""
    raw = c.raw if isinstance(c.raw, dict) else {}
    for m in raw.get("metadata") or []:
        if str(m.get("name", "")).lower() in ("uebergeben_um", "uebergabe_um") and m.get("value"):
            return str(m["value"])
    return str(raw.get("createDate") or "")


def _matches(c: Contribution, q: str) -> bool:
    """Suche in Titel, Sendung, Cutter, Mischer und Raum."""
    hay = " ".join([c.title, c.show, _field(c, "cutter", "editor"), c.mix.mixer, c.mix.room]).lower()
    return all(word in hay for word in q.split())


ORDERS = ("zeit", "sendung")


def rows(contributions: Sequence[Contribution], mixer: str, room: str, query: str = "", order: str = "zeit") -> dict:
    """Listenzeilen „Zur Mischung“ und Zähler der Seitenleiste. Eigene Beiträge stehen mit der Plakette
    „meine Mischung · …“ in derselben Liste (Jens 09.10.2026: eigene Ansicht „Meine Mischungen“ gestrichen).
    order: „zeit“ = nach Übergabe, älteste zuerst (Warteschlange); „sendung“ = nach Sendung gruppiert,
    darin nach Zeit (Jens, 03.10.2026)."""
    q = query.strip().lower()
    shown = list(contributions)
    if q:
        shown = [c for c in shown if _matches(c, q)]
    by_time = lambda c: (handover_time(c) == "", handover_time(c), c.title.lower())  # noqa: E731
    if order == "sendung":
        shown.sort(key=lambda c: (c.show.strip() == "", c.show.strip().lower(), *by_time(c)))
    else:
        shown.sort(key=by_time)
    out = []
    for c in shown:
        text, cls = _pill(c, mixer, room)
        t = handover_time(c)
        out.append({"id": c.id, "title": c.title, "show": c.show, "air": _field(c, "airDate", "sendetermin"),
                    "due": _field(c, "due", "sendung_in"), "length": _field(c, "duration", "laenge"),
                    "cutter": _field(c, "cutter", "editor"), "urgent": _field(c, "urgent") == "True",
                    "state": text, "pill": cls, "tags": tags(c), "handed": local_time(t) if t else "",
                    "group": c.show.strip() or "ohne Sendung"})
    return {"rows": out, "counts": {"list": len(contributions)},
            "order": order if order in ORDERS else "zeit"}


def tags(c: Contribution) -> list[str]:
    """Kennzeichen in der Liste (Funktionsbeschreibung S. 6): „freigegeben“, „Duplikat“."""
    out = []
    if c.mix.return_reason and c.mix.state in (mixstate.RETURNED, mixstate.TAKEN):
        out.append("freigegeben")
    if c.mix.duplicate_of:
        out.append("Duplikat")
    return out


def detail(c: Contribution, handover: Handover | None, mixer: str, room: str) -> dict:
    """Detailbereich rechts: Kopf, Hinweise, Angaben, erlaubte Aktionen."""
    m = c.mix
    mine = is_mine(c, mixer, room)
    banners = []
    if m.state in mixstate.OWNED and not mine:
        since = f" seit {m.taken_at}" if m.taken_at else ""
        banners.append({"cls": "warn", "text": f"In Mischung bei {m.mixer or 'jemand'} in {m.room or '?'}{since}."})
    if m.return_reason and m.state in (mixstate.RETURNED, mixstate.TAKEN):
        nxt = ("zuletzt gespeicherte Session wird als Kopie geöffnet" if m.return_keep == mixstate.KEEP_LAST
               else "neuer Import")
        when = f" um {local_time(m.returned_at)}" if m.returned_at else ""
        banners.append({"cls": "warn", "text": f"Freigegeben{when} aus {m.returned_from_room or '?'}: "
                                               f"{m.return_reason}. Beim Übernehmen: {nxt}."})
    if m.duplicate_of:
        keep = ", mit übernommenem Mischstand" if m.duplicate_session else ""
        banners.append({"cls": "info", "text": f"Duplikat von „{m.duplicate_of}“{keep}."})
    if m.state == mixstate.EXPORTED and mine:                  # Altbestand (vor 09.10.2026)
        banners.append({"cls": "info", "text": "Zurückgeliefert an Helmut. Erneut zurückliefern ist möglich."})
    if mixstate.delivered_by(m, mixer, room):
        banners.append({"cls": "info", "text": "Zurückgeliefert an Helmut und freigegeben. Erneut zurückliefern ist "
                                               "möglich, solange niemand anders den Beitrag übernimmt."})
    kv = [
        ("Zustand", STATE_TEXT.get(m.state, m.state)),
        ("Mischer", f"{m.mixer} · {m.room}" if m.mixer else NONE),
        ("AAF", handover.aaf.name if handover and handover.aaf else "fehlt in der Übergabe"),
        ("Referenzvideo", handover.video.name if handover and handover.video else "keins"),
        ("Cutter", _field(c, "cutter", "editor")),
        ("Session", os.path.basename(m.session_path) if m.session_path else NONE),
    ]
    takeable = m.state in mixstate.TAKEABLE and not c.locked
    return {
        "id": c.id, "title": c.title, "show": c.show, "air": _field(c, "airDate", "sendetermin"),
        "banners": banners, "kv": [{"k": k, "v": v} for k, v in kv],
        "session_path": m.session_path,
        "actions": {
            "fetch": (takeable or mine) and bool(handover and handover.aaf),
            "fetch_label": "Holen" if mine else "Übernehmen",
            "deliver": mixstate.may_deliver(m, mixer, room)
            and m.state in (mixstate.IMPORTED, mixstate.MIXING, mixstate.EXPORTED, mixstate.DELIVERED)
            and bool(m.session_path),
            "release": m.state in mixstate.OWNED,
            "duplicate": True,
            "backups": bool(m.session_path),
            "finder": bool(m.session_path),
        },
        "duplicate_info": {"purposes": list(mixstate.DUPLICATE_PURPOSES),
                           "titles": {p: mixstate.duplicate_title(c.title, p) for p in mixstate.DUPLICATE_PURPOSES},
                           "can_keep": bool(m.session_path),
                           "keep_default": bool(m.session_path) and m.state != mixstate.READY},
        "release_reasons": list(RELEASE_REASONS),
        "release_info": release_info(c, mixer, room) if m.state in mixstate.OWNED else None,
        "reopen": reopen_view(c) if takeable or (mine and m.state == mixstate.TAKEN) else None,
    }


def reopen_view(c: Contribution) -> dict | None:
    """Gespeicherten Stand als Kopie öffnen statt neu importieren (freigegeben bzw. Duplikat)."""
    m = c.mix
    if m.return_keep == mixstate.KEEP_LAST and m.session_path:
        return {"kind": "datei", "source": m.session_path,
                "text": "Beim Freigeben wurde „Letzten gespeicherten Stand öffnen“ gewählt. Die Session wird als "
                        "Kopie „…_wiederhergestellt_hhmm.ptx“ im selben Ordner angelegt und geöffnet; Original "
                        "und Sicherungen bleiben unverändert."}
    if m.duplicate_session and not m.session_path:
        return {"kind": "ordner", "source": m.duplicate_session,
                "text": "Duplikat mit übernommenem Mischstand: Der Session-Ordner des Originals wird samt "
                        "Audiodateien für dieses Duplikat kopiert und geöffnet. Das dauert je nach Größe."}
    return None


def local_time(iso: str) -> str:
    """ISO-Zeit aus Helmut → „02.10. 23:44“ (Ortszeit); Unlesbares unverändert."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d.%m. %H:%M")
    except ValueError:
        return iso


def release_info(c: Contribution, mixer: str, room: str) -> dict:
    """Freigeben-Dialog: Wahl fürs nächste Öffnen, Warnung bei fremdem Raum, wer benachrichtigt wird."""
    m = c.mix
    here = m.room == room
    keep = mixstate.keep_options(m, here)
    labels = mixstate.KEEP_LABEL
    hint = ("Die Mischung beginnt von vorn: Triage und Import laufen erneut." if keep == [mixstate.KEEP_NEW]
            else "Beim Übernehmen wird die zuletzt gespeicherte Session als Kopie unter neuem Namen geöffnet; "
                 "Original und Sicherungen bleiben unverändert.")
    if keep != [mixstate.KEEP_NEW] and m.session_storage == mixstate.MIRROR:
        hint = ("In einem anderen Raum wird der zuletzt gespiegelte Stand geöffnet (Kopie im lokalen Ordner); "
                "was seit dem letzten Spiegeln dazukam, fehlt. Im selben Raum die lokale Session als Kopie.")
    if keep == [mixstate.KEEP_NEW] and m.session_path:
        hint = (f"Die Session liegt nur lokal in {m.room or 'einem anderen Raum'} und ist von hier nicht "
                "erreichbar. Deshalb bleibt nur ein neuer Import.")
    warn = "" if here and m.mixer == mixer else (
        f"„{c.title}“ ist in {m.room or '?'} bei {m.mixer or 'jemand'} übernommen. Nur freigeben, wenn dort "
        "sicher nicht mehr gearbeitet wird.")
    who = [n for n in (m.mixer, _field(c, "cutter", "editor")) if n and n != NONE]
    return {"keep": [{"value": k, "label": labels[k]} for k in keep], "keep_default": keep[0],
            "keep_hint": hint, "warn": warn, "notify": who}


# ── Triage-Vorschau ───────────────────────────────────────────────────────────
CAT_CSS = {"o_ton": "ot", "atmo": "atmo", "musik": "music", "rest": "rest", "sprecher": "rest"}
CAT_LABEL = {"o_ton": "O-Ton", "atmo": "Atmo", "musik": "Musik", "rest": "Nicht zugeordnet", "sprecher": "Sprecher"}
STANDARD_LANES = [f"A{i}" for i in range(1, 11)] + [f"Musik{i}" for i in range(1, 9)]


def _lane_category(track: str) -> str:
    if track.startswith("Musik"):
        return "musik"
    if track.lower().startswith("spr"):
        return "sprecher"
    m = re.fullmatch(r"A(\d+)", track)
    if m:
        n = int(m.group(1))
        return "o_ton" if n <= 4 else ("atmo" if n <= 10 else "rest")
    return "rest"


def _lane_order(track: str) -> tuple:
    cat = _lane_category(track)
    m = re.search(r"(\d+)$", track)
    n = int(m.group(1)) if m else 0
    return ({"o_ton": 0, "atmo": 1, "musik": 2, "rest": 3, "sprecher": 4}[cat], n, track)


def triage_view(result, placement, template: str = "") -> dict:
    """Vorschau wie im Prototyp: Zeitleiste je Zielspur, Clip-Liste, Hinweise, Zähler."""
    from tonmischung.core.timecode import frames_to_tc
    fps = result.analysis.fps or 25
    dec = {(d.clip.track, d.clip.index): d for d in result.decisions}
    moved = {(c.track, c.index) for m in placement.clip_moves for c in m.clips}
    keys = [k for k in placement.final if k in dec]
    if keys:
        t0 = min(dec[k].clip.start - dec[k].clip.fade_in for k in keys)
        t1 = max(dec[k].clip.end + dec[k].clip.fade_out for k in keys)
    else:
        t0, t1 = result.analysis.start_frames, result.analysis.start_frames + fps
    span = max(1, t1 - t0)

    def unsure(d) -> bool:
        return d.category == "rest" and not d.reason.startswith("zweite Fassung")

    used = {t for k in keys for t in placement.final[k]}
    lanes = {t: [] for t in sorted(set(STANDARD_LANES) | used, key=_lane_order)}
    rows = []
    for k in sorted(keys, key=lambda k: (dec[k].clip.start, k[0])):
        d = dec[k]
        c = d.clip
        key = f"{c.track}#{c.index}"
        where = placement.final[k]
        item = {"key": key, "track": c.track, "index": c.index, "name": c.source.strip() or c.track,
                "tc_in": frames_to_tc(c.start, fps), "tc_out": frames_to_tc(c.end, fps),
                "cat": CAT_CSS.get(d.category, "rest"), "category": d.category,
                "label": CAT_LABEL.get(d.category, d.category), "reason": d.reason,
                "confidence": round(c.confidence * 100) if c.signal else None, "signal": c.signal,
                "targets": "/".join(where), "unsure": unsure(d), "moved": k in moved}
        rows.append(item)
        for t in where:
            lanes[t].append({**item, "left": round((c.start - t0) / span * 100, 3),
                             "width": round(max(0.2, (c.end - c.start) / span * 100), 3)})
    ticks = [{"tc": frames_to_tc(t0 + span * i // 4, fps), "left": i * 25} for i in range(5)]
    counts = {}
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    return {
        "lanes": [{"id": t, "cat": CAT_CSS[_lane_category(t)], "label": CAT_LABEL[_lane_category(t)],
                   "clips": v} for t, v in lanes.items()],
        "rows": rows, "ticks": ticks, "counts": counts,
        "unsure": sum(1 for r in rows if r["unsure"]), "moves": len(placement.clip_moves),
        "notes": list(placement.notes), "tracks_used": len(used), "clips": len(rows), "template": template,
        "categories": [{"value": v, "label": CAT_LABEL[v], "cat": CAT_CSS[v]}
                       for v in ("o_ton", "atmo", "musik", "rest")],
    }
