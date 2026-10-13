import os

import numpy as np
import pytest
import soundfile as sf

from tonmischung import cli, devlocal
from tonmischung.core.timecode import frames_to_tc
from tonmischung.triage.analyse import analyse, triage_aaf
from tonmischung.triage.names import ATMO, H_LOCATION, H_MUSIC, MUSIC, OTON
from tonmischung.triage.report import as_dict, format_report

SR = 48000


def _wav(path, secs):
    x = (0.1 * np.sin(2 * np.pi * 440 * np.arange(int(SR * secs)) / SR)).astype("float32")
    sf.write(path, x, SR, subtype="PCM_24")
    return str(path)


def build_aaf(tmp_path, producer_avid=False):
    """Zwei Tonspuren: „Atmo 1“ (Lücke, Kameraton, Blende, Musik) und eine unbenannte (kurz)."""
    import aaf2
    path = str(tmp_path / "beitrag.aaf")
    with aaf2.open(path, "w") as f:
        def master(name, secs):
            m = f.create.MasterMob(name)
            f.content.mobs.append(m)
            m.import_audio_essence(_wav(tmp_path / f"{len(f.content.mobs)}.wav", secs), 25)
            return m
        cam, mus, kurz = master("PAVJ1496.MOV", 4), master("Phoenix - Sunset.mp3", 4), master("Ton 7", 2)
        xfade = f.create.OperationDef(aaf2.auid.AUID("0c3bea44-fc05-11d2-8a29-0050040ef7d2"), "Mixdown")
        xfade.media_kind, xfade.number_inputs = "sound", 2
        xfade["IsTimeWarp"].value = False
        f.dictionary.register_def(xfade)
        comp = f.create.CompositionMob("Sequenz")
        comp.usage = "Usage_TopLevel"
        f.content.mobs.append(comp)
        tc = f.create.Timecode(25, False)
        tc.start, tc.length = 900000, 250
        comp.create_timeline_slot(25).segment = tc
        s1 = comp.create_timeline_slot(25)
        s1.name = "Atmo 1"
        seq = f.create.Sequence(media_kind="sound")
        seq.components.append(f.create.Filler("sound", 25))
        seq.components.append(cam.create_source_clip(slot_id=1, start=0, length=50))
        tr = f.create.Transition("sound", 10)
        tr["OperationGroup"].value = f.create.OperationGroup(xfade, length=10)
        tr["CutPoint"].value = 5
        seq.components.append(tr)
        seq.components.append(mus.create_source_clip(slot_id=1, start=0, length=50))
        s1.segment = seq
        s2 = comp.create_timeline_slot(25)
        seq2 = f.create.Sequence(media_kind="sound")
        seq2.components.append(kurz.create_source_clip(slot_id=1, start=0, length=20))   # 0,8 s
        s2.segment = seq2
    return path


def test_clips_lage_namen_und_signal(tmp_path):
    seen = []

    def classify(x, sr):
        seen.append((x.size, sr))
        return "musik", 0.97
    a = analyse(build_aaf(tmp_path), classify=classify)
    assert (a.fps, a.start_frames) == (25, 900000)
    assert [(t.name, t.unnamed) for t in a.tracks] == [("Atmo 1", False), ("A2", True)]
    lage = [(c.track, frames_to_tc(c.start), frames_to_tc(c.end)) for c in a.clips]
    # wie Pro Tools: Grenzen ohne Blendenbereich (Blende 10 Frames, Lücke 1 s)
    assert lage == [("Atmo 1", "10:00:01:00", "10:00:02:15"), ("Atmo 1", "10:00:03:00", "10:00:04:15"),
                    ("A2", "10:00:00:00", "10:00:00:20")]
    assert [c.hint.kind if c.hint else None for c in a.clips] == [H_LOCATION, H_MUSIC, None]
    assert [(c.fade_in, c.fade_out) for c in a.clips] == [(0, 10), (10, 0), (0, 0)]
    assert seen == [(2 * SR, SR), (2 * SR, SR)]          # 2 s je Clip; der kurze nicht beurteilt
    assert a.clips[2].signal is None and a.unreadable == 0


def test_triage_end_zu_ende(tmp_path):
    r = triage_aaf(build_aaf(tmp_path), threshold=0.8, keywords={"atmo": ["Atmo"]},
                   classify=lambda x, sr: ("musik", 0.97))
    assert r.verdicts["Atmo 1"].category == ATMO                     # Stichwort im Spurnamen
    cats = [d.category for d in r.decisions]
    assert cats[:2] == [ATMO, MUSIC]          # Kameraton bleibt (Hintergrundmusik), Musik per Name
    assert r.decisions[1].deviates
    text = format_report(r)
    assert "Atmo 1" in text and "Musikdatei (.mp3)" in text
    assert as_dict(r)["clips"][1]["kategorie"] == MUSIC


def test_schalter_der_regelkaskade(tmp_path):
    r = triage_aaf(build_aaf(tmp_path), threshold=0.8, keywords={"atmo": ["Atmo"]},
                   use_source_names=False, classify=lambda x, sr: ("musik", 0.97))
    assert r.decisions[0].category == MUSIC                 # ohne Quellnamen zählt nur das Signal
    called = []
    triage_aaf(build_aaf(tmp_path), threshold=0.8, use_signal=False,
               classify=lambda x, sr: called.append(1) or ("musik", 1.0))
    assert called == []


def test_befehl_triage(tmp_path, capsys, monkeypatch):
    path = build_aaf(tmp_path)
    monkeypatch.setattr("tonmischung.triage.analyse._hedwig_classify", lambda x, sr: ("atmo", 0.9))
    out_json = tmp_path / "t.json"
    assert cli.main(["triage", path, "--alle", "--json", str(out_json)]) == 0
    out = capsys.readouterr().out
    assert "Alle Clips:" in out and "Summe:" in out
    assert out_json.exists()
    assert cli.main(["triage", str(tmp_path / "fehlt.aaf")]) == 1


AAF_TESTS = os.environ.get("TONMISCHUNG_AAF_TESTS") == "1"           # echte AAFs lesen dauert – nur auf Wunsch
STROMNETZE = devlocal.real_file("aaf_stromnetze")                     # siehe testfiles/entwicklung.json


@pytest.mark.skipif(not AAF_TESTS or STROMNETZE is None,
                    reason="echte AAF nur mit TONMISCHUNG_AAF_TESTS=1 und „aaf_stromnetze“ in testfiles/entwicklung.json")
def test_echte_aaf_stromnetze():
    kw = {"o_ton": ["O-Ton", "OT", "Int", "Interview", "Funk", "Angel"], "atmo": ["Atmo", "Amb", "SFX", "Geräusch"],
          "musik": ["Musik", "Mus", "Music", "Score"]}
    r = triage_aaf(STROMNETZE, threshold=0.8, keywords=kw)
    assert {t: v.category for t, v in r.verdicts.items()} == {
        "Spr 2": "sprecher", "A1": OTON, "A2": OTON, "A3": ATMO, "A4": ATMO, "Musik1": MUSIC, "Musik2": MUSIC}
    assert not any(d.deviates for d in r.decisions)         # Archiv-Atmo und Kameraton nicht als Musik


TRINKWASSER = devlocal.real_file("aaf_trinkwasser")


@pytest.mark.skipif(not AAF_TESTS or TRINKWASSER is None,
                    reason="echte AAF nur mit TONMISCHUNG_AAF_TESTS=1 und „aaf_trinkwasser“ in testfiles/entwicklung.json")
def test_echte_aaf_trinkwasser_ohne_musik():
    r = triage_aaf(TRINKWASSER, threshold=0.8, keywords={"musik": ["Musik", "Mus"]})
    assert ("A3", "A4") in r.pairs
    assert not [d for d in r.decisions if d.category == MUSIC]       # Jens: Trinkwasser ist keine Musik
