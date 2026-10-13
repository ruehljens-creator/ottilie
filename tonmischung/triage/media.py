"""Medien eines AAF prüfen, bevor Pro Tools importiert (reine Logik plus Dateisuche, nur lesen).

Jens (03.10.2026): „Vermutlich Reference-AAF, aber es muss beides funktionieren.“ Ein AAF bringt seinen Ton entweder
eingebettet mit oder verweist auf Dateien. Premiere unter Windows schreibt Verweise wie `file:///C%3a/…` oder
`file://?/D%3a/…`; auf dem Mac liegen solche Dateien – wenn überhaupt – auf einem eingehängten Laufwerk unter demselben
Pfad ohne Laufwerksbuchstaben oder neben dem AAF. Fehlt eine Datei, kommen ihre Clips offline (ohne Ton) an – das
sagt die App vorher, statt es Pro Tools zu überlassen.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class MediaRef:
    name: str              # Dateiname
    url: str               # Verweis im AAF
    kind: str              # audio | video | andere
    found: str = ""        # Pfad auf diesem Mac oder ""


@dataclass(frozen=True)
class MediaReport:
    embedded: int                     # eingebettete Essenzen
    refs: tuple[MediaRef, ...]

    @property
    def missing_audio(self) -> list[MediaRef]:
        return [r for r in self.refs if r.kind == "audio" and not r.found]

    def summary(self) -> str:
        audio = [r for r in self.refs if r.kind == "audio"]
        if not audio:
            return f"Ton im AAF eingebettet ({self.embedded} Dateien)" if self.embedded else "keine Audioverweise"
        miss = self.missing_audio
        lead = f"{self.embedded} Audiodateien eingebettet, " if self.embedded else ""
        if not miss:
            return f"{lead}{len(audio)} verknüpfte Audiodateien, alle erreichbar"
        return (f"{lead}{len(miss)} von {len(audio)} verknüpften Audiodateien nicht erreichbar: "
                + ", ".join(r.name for r in miss) + " – ihre Clips kommen offline (ohne Ton) an")


def url_to_path(url: str) -> str:
    """AAF-Verweis → Pfad. Windows: „C:/Ordner/Datei“; POSIX bleibt POSIX."""
    u = urllib.parse.unquote(url or "")
    u = re.sub(r"^file:(//\?)?/*", "", u, flags=re.IGNORECASE) if u.lower().startswith("file:") else u
    if re.match(r"^[A-Za-z]:[/\\]", u):
        return u.replace("\\", "/")
    return "/" + u.lstrip("/") if u else ""


def candidates(path: str, aaf_dir: str, volumes: Iterable[str]) -> list[str]:
    """Wo eine Datei auf diesem Mac liegen könnte – in dieser Reihenfolge."""
    name = os.path.basename(path)
    out = [path] if path.startswith("/") else []
    out += [os.path.join(aaf_dir, name)]
    rest = re.sub(r"^[A-Za-z]:/", "", path).lstrip("/")
    parts = rest.split("/")
    for vol in volumes:
        for i in range(len(parts)):                           # D:/a/b/c.wav → /Volumes/X/a/b/c.wav, …/b/c.wav, …
            out.append(os.path.join(vol, *parts[i:]))
    return list(dict.fromkeys(out))


def find(path: str, aaf_dir: str, volumes: Iterable[str], exists: Callable[[str], bool] = os.path.isfile,
         listdir: Callable[[str], list[str]] = os.listdir) -> str:
    for c in candidates(path, aaf_dir, volumes):
        if exists(c):
            return c
    name = os.path.basename(path)
    try:                                                      # eine Ebene unter dem AAF (Paketordner)
        for sub in listdir(aaf_dir):
            p = os.path.join(aaf_dir, sub, name)
            if exists(p):
                return p
    except OSError:
        pass
    return ""


def _clips(seg: object, depth: int = 0):
    """SourceClips eines Segments (Sequenzen, Effekte, verschachtelte Bereiche, Auswahlen)."""
    if seg is None or depth > 12:
        return
    n = type(seg).__name__
    if n == "SourceClip":
        yield seg
    elif n == "Sequence":
        for c in seg.components:
            yield from _clips(c, depth + 1)
    elif n in ("OperationGroup",):
        for c in _value(seg, "InputSegments") or []:
            yield from _clips(c, depth + 1)
    elif n == "NestedScope":
        for c in _value(seg, "Slots") or []:
            yield from _clips(c, depth + 1)
    elif n == "EssenceGroup":
        for c in _value(seg, "Choices") or []:
            yield from _clips(c, depth + 1)
    elif n == "Selector":
        yield from _clips(_value(seg, "Selected"), depth + 1)


def _value(obj: object, key: str):
    try:
        return obj[key].value  # type: ignore[index]
    except Exception:  # noqa: BLE001 – Eigenschaft fehlt
        return None


def _file_mobs(clip: object, depth: int = 0):
    """Datei-Mobs hinter einem Clip: Master- und Kompositions-Mobs werden aufgelöst; Bänder und Import-Mobs
    (Herkunft vor der Datei) zählen nicht."""
    try:
        mob = clip.mob  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        mob = None
    if mob is None or depth > 12:
        return
    kind = type(mob).__name__
    if kind == "SourceMob":
        if type(getattr(mob, "descriptor", None)).__name__ not in ("ImportDescriptor", "TapeDescriptor", "NoneType"):
            yield mob
        return
    slot_id = _value(clip, "SourceMobSlotID")
    for slot in mob.slots:
        if slot_id is None or slot.slot_id == slot_id:
            for c in _clips(slot.segment):
                yield from _file_mobs(c, depth + 1)


def check(aaf_path: str, volumes: Iterable[str] | None = None) -> MediaReport:
    """Den Clips der Sequenz bis zur Datei folgen: eingebettet (Essenz im AAF) oder verknüpft (Verweis, auf
    diesem Mac gesucht). Nur was die Sequenz benutzt, zählt – ein Premiere-AAF trägt oft weitere Master-Clips mit
    Verweisen auf die Kameradateien, obwohl der Ton eingebettet ist (live 04.10.2026, „Test 7 Tage“)."""
    import aaf2
    vols = list(volumes) if volumes is not None else [os.path.join("/Volumes", v) for v in _volumes()]
    aaf_dir = os.path.dirname(os.path.abspath(aaf_path))
    refs: dict[str, MediaRef] = {}
    embedded: set[str] = set()
    with aaf2.open(aaf_path, "r") as f:
        essence = {str(e.mob_id) for e in f.content.essencedata}
        comps = list(f.content.toplevel()) or list(f.content.compositionmobs())
        for comp in comps:
            for slot in comp.slots:
                media_kind = getattr(slot.segment, "media_kind", "")
                kind = {"Sound": "audio", "Picture": "video"}.get(media_kind)
                if kind is None:
                    continue
                for clip in _clips(slot.segment):
                    for fm in _file_mobs(clip):
                        if str(fm.mob_id) in essence:
                            embedded.add(str(fm.mob_id))
                            continue
                        for loc in _value(fm.descriptor, "Locator") or []:
                            url = _value(loc, "URLString") or ""
                            path = url_to_path(url)
                            key = path.lower()
                            if not path or key in refs and refs[key].kind == "audio":
                                continue
                            refs[key] = MediaRef(os.path.basename(path), url, kind, find(path, aaf_dir, vols))
    return MediaReport(len(embedded), tuple(refs.values()))


def _volumes() -> list[str]:
    try:
        return sorted(v for v in os.listdir("/Volumes") if not v.startswith("."))
    except OSError:
        return []
