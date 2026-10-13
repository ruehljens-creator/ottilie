from tonmischung.triage.decide import ClipFacts, decide, track_verdicts
from tonmischung.triage.names import (
    ATMO, H_FADE, H_LIBRARY, H_LOCATION, H_MUSIC, MUSIC, OTON, REST, SPEAKER, NameHint)

T = 0.8
KW = {"o_ton": ["OT"], "atmo": ["Atmo"], "musik": ["Musik"]}


def clip(track, i, signal=None, conf=0.0, hint=None, start=None, end=None, source="x"):
    start = i * 100 if start is None else start
    return ClipFacts(track, i, start, start + 90 if end is None else end, source,
                     NameHint(hint, hint) if hint else None, signal, conf)


def run(tracks, clips, fallback=None):
    v = track_verdicts(tracks, clips, threshold=T, keywords=KW, fallback=fallback)
    return v, {(d.clip.track, d.clip.index): d for d in decide(clips, v, threshold=T)}


def atmo_track(name="A5", n=4):
    return [clip(name, i, "atmo", 0.9) for i in range(n)]


def test_spurkategorie_aus_mehrheit_der_sicheren_clips():
    clips = atmo_track("A5", 3) + [clip("A5", 3, "sprache", 0.9), clip("A5", 4, "sprache", 0.5)]
    v, _ = run(["A5"], clips)
    assert v["A5"].category == ATMO and v["A5"].reason == "3 von 4 sicheren Clips Atmo"


def test_musik_per_name_verlaesst_die_atmospur():
    clips = atmo_track() + [clip("A5", 4, "atmo", 0.6, hint=H_MUSIC)]
    _, d = run(["A5"], clips)
    assert d["A5", 4].category == MUSIC and d["A5", 4].deviates


def test_musik_im_drehortton_bleibt_hintergrundmusik():
    clips = atmo_track() + [clip("A5", 4, "musik", 0.97, hint=H_LOCATION)]
    _, d = run(["A5"], clips)
    assert d["A5", 4].category == ATMO and not d["A5", 4].deviates


def test_musik_per_signal_nur_bei_hoher_sicherheit():
    clips = atmo_track() + [clip("A5", 4, "musik", 0.85), clip("A5", 5, "musik", 0.7)]
    _, d = run(["A5"], clips)
    assert d["A5", 4].category == MUSIC
    assert d["A5", 5].category == ATMO


def test_o_ton_auf_atmospur_nur_bei_hoher_sicherheit():
    clips = atmo_track() + [clip("A5", 4, "sprache", 0.9), clip("A5", 5, "sprache", 0.6)]
    _, d = run(["A5"], clips)
    assert d["A5", 4].category == OTON and d["A5", 4].deviates
    assert d["A5", 5].category == ATMO


def test_archiv_ist_atmo():
    clips = [clip("A1", i, "sprache", 0.95) for i in range(3)] + [clip("A1", 3, "musik", 0.97, hint=H_LIBRARY)]
    _, d = run(["A1"], clips)
    assert d["A1", 3].category == ATMO


def test_auf_der_musikspur_bleibt_musik():
    clips = [clip("Musik1", 0, "musik", 0.9), clip("Musik1", 1, "sprache", 0.95)]
    v, d = run(["Musik1"], clips)
    assert v["Musik1"].reason == "Stichwort im Spurnamen"
    assert d["Musik1", 1].category == MUSIC


def test_sprecher_und_zweite_fassung():
    clips = [clip("Spr 2", 0, "musik", 0.97, hint=H_MUSIC), clip("Audio 3_No FX", 0, "sprache", 0.97),
             clip("Audio 3_FX", 0, "sprache", 0.97)]
    v, d = run(["Spr 2", "Audio 3_FX", "Audio 3_No FX"], clips)
    assert d["Spr 2", 0].category == SPEAKER
    assert d["Audio 3_No FX", 0].category == REST and "zweite Fassung" in d["Audio 3_No FX", 0].reason
    assert d["Audio 3_FX", 0].category == OTON


def test_spur_ohne_mehrheit_und_unsichere_clips_ab_a11():
    clips = [clip("A7", 0, "sprache", 0.9), clip("A7", 1, "atmo", 0.9), clip("A7", 2, "atmo", 0.5)]
    v, d = run(["A7"], clips)
    assert v["A7"].category == REST
    assert [d["A7", i].category for i in range(3)] == [OTON, ATMO, REST]


def test_rueckfall_spurbelegung():
    v, d = run(["A3"], [clip("A3", 0, "atmo", 0.4)], fallback={"A3": ATMO})
    assert v["A3"].category == ATMO and d["A3", 0].category == ATMO


def test_ueberblendung_folgt_dem_beruehrenden_nachbarn():
    clips = atmo_track("A5", 2) + [
        clip("A5", 2, hint=H_MUSIC, start=300, end=390),
        clip("A5", 3, hint=H_FADE, start=390, end=400),       # zwischen Musik und Lücke
        clip("A5", 4, hint=H_FADE, start=600, end=610),       # ohne Nachbarn
        clip("A5", 5, hint=H_FADE, start=90, end=100),        # zwischen Atmo 0 und Atmo 1
    ]
    _, d = run(["A5"], clips)
    assert d["A5", 3].category == MUSIC
    assert d["A5", 4].category == ATMO and d["A5", 4].reason == "Überblendung, Spur"
    assert d["A5", 5].category == ATMO and d["A5", 5].reason == "Überblendung, folgt Nachbarclip"


def test_ueberblendung_zwischen_verschiedenen_kategorien_bleibt_bei_der_spur():
    clips = [clip("A5", 0, "atmo", 0.9, start=0, end=100), clip("A5", 1, hint=H_FADE, start=100, end=110),
             clip("A5", 2, hint=H_MUSIC, start=110, end=200), clip("A5", 3, "atmo", 0.9, start=300, end=400)]
    _, d = run(["A5"], clips)
    assert d["A5", 1].category == ATMO and "verschiedenen" in d["A5", 1].reason


# ── Paare ─────────────────────────────────────────────────────────────────────
from tonmischung.triage.decide import find_pairs, triage_tracks  # noqa: E402


def two(track, signals, offset=0):
    return [clip(track, i, s, c, start=i * 100 + (offset if i == 1 else 0)) for i, (s, c) in enumerate(signals)]


def test_paare_nach_name_und_schnitt():
    clips = (two("A3", [("atmo", .9), ("atmo", .9)]) + two("A4", [("atmo", .9), ("atmo", .9)], offset=1)
             + two("A5", [("atmo", .9)]) + two("ST", [("musik", .9)]) + two("ST", [("musik", .9)]))
    assert find_pairs(["A3", "A4", "A5", "ST"], clips) == [("A3", "A4")]   # ±1 Frame zählt als gleich
    assert find_pairs(["Spr 1", "Spr 2", "ST", "ST.01"], []) == [("ST", "ST.01")]
    clips = two("A3", [("atmo", .9)]) + two("A4", [("atmo", .9)], offset=0)
    clips[1] = clip("A4", 0, "atmo", .9, start=5)                          # anderer Schnitt
    assert find_pairs(["A3", "A4"], clips) == []


def test_fassungen_werden_getrennt_gepaart():
    names = ["Audio 3_FX", "Audio 3_No FX", "Audio 4_FX", "Audio 4_No FX"]
    clips = [clip(t, 0, "sprache", .9) for t in names]                     # alle gleicher Schnitt
    assert find_pairs(names, clips) == [("Audio 3_FX", "Audio 4_FX"), ("Audio 3_No FX", "Audio 4_No FX")]


def test_paar_wird_gemeinsam_entschieden():
    # Trinkwasser „12099Z“: nur eine Hälfte sicher Musik → bleibt Atmo (Jens: keine Musik)
    a3 = [clip("A3", i, "atmo", .9) for i in range(3)] + [clip("A3", 3, "musik", .50)]
    a4 = [clip("A4", i, "atmo", .9) for i in range(3)] + [clip("A4", 3, "musik", .97)]
    pairs, v, out = triage_tracks(["A3", "A4"], a3 + a4, threshold=T)
    assert pairs == [("A3", "A4")] and v["A4"].category == ATMO and "Paar mit „A3“" in v["A4"].reason
    assert [d.category for d in out] == [ATMO] * 8
    # beide Hälften sicher → beide Musik
    a4[3] = clip("A4", 3, "musik", .9)
    a3[3] = clip("A3", 3, "musik", .85)
    _, _, out = triage_tracks(["A3", "A4"], a3 + a4, threshold=T)
    assert [d.category for d in out if d.clip.index == 3] == [MUSIC, MUSIC]
    assert all(d.deviates for d in out if d.clip.index == 3)


def test_paar_nur_nach_name_teilt_die_spurkategorie():
    st1 = [clip("ST", 0, "musik", .9, start=0)]
    st2 = [clip("ST.01", 0, "musik", .9, start=500)]
    _, v, out = triage_tracks(["ST", "ST.01"], st1 + st2, threshold=T)
    assert v["ST"].category == v["ST.01"].category == MUSIC
    assert len(out) == 2
