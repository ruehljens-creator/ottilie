"""Medien eines AAF: eingebettet oder verknüpft (Windows-Pfade aus Premiere), vor dem Import suchen."""

import pytest

from tonmischung import devlocal
from tonmischung.triage import media as MD

PREMIERE = devlocal.real_file("premiere_aaf")                          # echte Dateien, siehe testfiles/entwicklung.json
PREMIERE_EMBEDDED = devlocal.real_file("premiere_eingebettet_aaf")


def test_verweise_aus_windows_und_mac():
    assert MD.url_to_path("file:///C%3a/Heruntergeladenes/Musik/A.wav") == "C:/Heruntergeladenes/Musik/A.wav"
    assert MD.url_to_path("file://?/D%3a/export-test-helmut/B%20C.mxf") == "D:/export-test-helmut/B C.mxf"
    assert MD.url_to_path("file:///Volumes/Storage/Ton/A.wav") == "/Volumes/Storage/Ton/A.wav"


def test_suche_neben_dem_aaf_und_auf_den_laufwerken(tmp_path):
    vol = tmp_path / "Testdaten"
    (vol / "export-test-helmut").mkdir(parents=True)
    (vol / "export-test-helmut" / "B.mxf").write_bytes(b"x")
    pkg = tmp_path / "Paket"
    (pkg / "Audio").mkdir(parents=True)
    (pkg / "Audio" / "Musik.wav").write_bytes(b"x")
    (pkg / "Neben.wav").write_bytes(b"x")
    vols = [str(vol)]
    assert MD.find("D:/export-test-helmut/B.mxf", str(pkg), vols) == str(vol / "export-test-helmut" / "B.mxf")
    assert MD.find("C:/Heruntergeladenes/Neben.wav", str(pkg), vols) == str(pkg / "Neben.wav")
    assert MD.find("C:/x/Musik.wav", str(pkg), vols) == str(pkg / "Audio" / "Musik.wav")      # eine Ebene tiefer
    assert MD.find("C:/Heruntergeladenes/Fehlt.wav", str(pkg), vols) == ""


def test_zusammenfassung():
    r = MD.MediaReport(0, (MD.MediaRef("A.wav", "u", "audio", ""), MD.MediaRef("B.wav", "u", "audio", "/x/B.wav"),
                           MD.MediaRef("V.mxf", "u", "video", "")))
    assert [x.name for x in r.missing_audio] == ["A.wav"]
    assert r.summary() == "1 von 2 verknüpften Audiodateien nicht erreichbar: A.wav – ihre Clips kommen offline (ohne Ton) an"
    assert MD.MediaReport(87, ()).summary() == "Ton im AAF eingebettet (87 Dateien)"
    assert MD.MediaReport(2, (MD.MediaRef("A.wav", "u", "audio", ""),)).summary().startswith(
        "2 Audiodateien eingebettet, 1 von 1 verknüpften")


@pytest.mark.skipif(PREMIERE is None, reason="Testdatei „premiere_aaf“ nicht da (testfiles/entwicklung.json)")
def test_premiere_testpaket():
    r = MD.check(PREMIERE)
    assert r.embedded == 0 and len(r.missing_audio) == 3
    assert next(x for x in r.refs if x.kind == "video").found.endswith("test-fuer-helmut2 Gerendert.mxf")


# ── Kette Sequenz → Master-Clip → Datei (Attrappen mit den Klassennamen von pyaaf2) ──────────────────────────────
class _P:
    def __init__(self, value):
        self.value = value


class _Obj:
    def __init__(self, **props):
        self._props = props

    def __getitem__(self, key):
        if key not in self._props:
            raise KeyError(key)
        return _P(self._props[key])


def _cls(name, base=_Obj):
    return type(name, (base,), {})


SourceClip, Sequence, Slot = _cls("SourceClip"), _cls("Sequence"), _cls("Slot")
MasterMob, SourceMob, CompositionMob = _cls("MasterMob"), _cls("SourceMob"), _cls("CompositionMob")
WAVEDescriptor, MultipleDescriptor, ImportDescriptor = _cls("WAVEDescriptor"), _cls("MultipleDescriptor"), _cls("ImportDescriptor")


def _clip(mob, slot_id=1):
    c = SourceClip(SourceMobSlotID=slot_id)
    c.mob = mob
    return c


def _slot(slot_id, segment, kind="Sound"):
    s = Slot()
    s.slot_id, s.segment = slot_id, segment
    segment.media_kind = kind
    return s


def _seq(*comps):
    q = Sequence()
    q.components = list(comps)
    return q


def _file(name, mob_id, url=""):
    d = (WAVEDescriptor if not url else MultipleDescriptor)(Locator=[_Obj(URLString=url)] if url else [])
    m = SourceMob()
    m.name, m.mob_id, m.descriptor, m.slots = name, mob_id, d, []
    return m


def _master(name, *files):
    m = MasterMob()
    m.name, m.mob_id = name, "master-" + name
    m.slots = [_slot(1, _seq(*[_clip(f) for f in files]))]
    return m


def test_nur_benutzte_clips_zaehlen(monkeypatch, tmp_path):
    """Premiere: Sequenz-Clips zeigen auf eingebetteten Ton; daneben Master-Clips mit Verweisen auf die
    Kameradateien, die die Sequenz nicht benutzt (live 04.10.2026) – kein Fehlalarm."""
    import sys
    import types
    emb = _file("REC00001.wav", "emb-1")
    cam = _file("A009.mov", "cam-1", "file:///Volumes/Karte/A009.mov")
    tape = SourceMob()
    tape.name, tape.mob_id, tape.descriptor, tape.slots = "Import", "imp-1", ImportDescriptor(Locator=[]), []
    used = _master("REC00001.wav", emb)
    unused = _master("A009.mov", cam)
    comp = CompositionMob()
    comp.slots = [_slot(2, _seq(_clip(used), _clip(tape)))]
    content = types.SimpleNamespace(essencedata=[types.SimpleNamespace(mob_id="emb-1")],
                                    toplevel=lambda: [comp], compositionmobs=lambda: [comp, unused])

    class _F:
        def __enter__(self):
            return types.SimpleNamespace(content=content)

        def __exit__(self, *a):
            return False
    monkeypatch.setitem(sys.modules, "aaf2", types.SimpleNamespace(open=lambda p, m: _F()))
    r = MD.check(str(tmp_path / "x.aaf"), volumes=[])
    assert (r.embedded, r.refs) == (1, ())
    comp.slots[0].segment.components.append(_clip(unused))            # jetzt benutzt die Sequenz die Kameradatei
    r = MD.check(str(tmp_path / "x.aaf"), volumes=[])
    assert r.embedded == 1 and [(x.name, x.kind, x.found) for x in r.refs] == [("A009.mov", "audio", "")]


@pytest.mark.skipif(PREMIERE_EMBEDDED is None,
                    reason="Testdatei „premiere_eingebettet_aaf“ nicht da (testfiles/entwicklung.json)")
def test_premiere_eingebettet_mit_kameraverweisen():
    r = MD.check(PREMIERE_EMBEDDED)
    assert r.embedded == 16 and r.missing_audio == [] and r.summary() == "Ton im AAF eingebettet (16 Dateien)"
