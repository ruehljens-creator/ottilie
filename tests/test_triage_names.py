import pytest

from tonmischung.triage.names import (
    ATMO, H_FADE, H_LIBRARY, H_LOCATION, H_MUSIC, MUSIC, OTON, base_track_name, is_speaker_track,
    second_versions, source_hint, track_keyword)

KW = dict(music_keywords=["Musik", "Mus", "Music", "Score"],
          oton_keywords=["O-Ton", "OT", "Int", "Interview", "Funk", "Angel"],
          atmo_keywords=["Atmo", "Amb", "SFX", "Geräusch"], music_paths=["/Musikarchiv/"])


@pytest.mark.parametrize("name, kind", [
    # echte Quellnamen aus Sender-AAFs (Spike 02.10.2026)
    ("Phoenix - Love Like a Sunset, Pt. II.mp3", H_MUSIC),
    ("UPM_BTV199_36_Connected_Movement_Main_Track_Bradley_1031040.mp3.new.02", H_MUSIC),
    ("ivox590_01_History-In-Motion_(Alexander-Suhanoff).mp3.new.02-02.A2", H_MUSIC),
    ("00dcad26-76bc-47a2-8112-f88188ae6f2c-other-C minor-160bpm-442hz.new.02", H_MUSIC),
    ("KPM_KPM0123_04_Something.wav", H_MUSIC),
    ("Mezerg - Sentimental Pilot.wav", H_MUSIC),
    ("/Volumes/Server/Musikarchiv/Titel 3.wav", H_MUSIC),
    ("PAVJ1496.MOV", H_LOCATION),
    ("MUSTERMANN MAX4410.new.02-01.A2", H_LOCATION),
    ("rohmaterialoffenbach--30092023.new.09-01.A4", H_LOCATION),
    ("OT Ausgerechnet im Elfenpark.new.02", H_LOCATION),
    ("Interview - Müller.wav", H_LOCATION),            # Interpret – Titel zählt erst danach
    ("AMBCnst_EXT_Parkhausdach mit Baugeruest Stadtatmo Totale_AMB", H_LIBRARY),
    ("MACHHvac_INT__Ventilator_12-12#04.wav.new.02-01", H_LIBRARY),
    ("Fade ", H_FADE),
])
def test_quellnamen(name, kind):
    hint = source_hint([name], **KW)
    assert hint is not None and hint.kind == kind, hint


@pytest.mark.parametrize("name", [
    "12099Z.new.02-01.A4", "A2.01_01", "Spr 2_02-02", "Trailer Opener.wav", "Soley _ AEventir.new.03",
    "Avid MediaFiles/MXF/1/Der kroatien krimi3775A618V.mxf",        # Avid: alles ist MXF
    "Museum Lorsch.wav", "PILOT.wav",
])
def test_ohne_hinweis(name):
    assert source_hint([name], **KW) is None


def test_vorrang_innerhalb_der_namenskette():
    assert source_hint(["Clip 1", "AMBMisc_EXT_Wind.wav", "Song.mp3"], **KW).kind == H_LIBRARY
    assert source_hint(["PAVJ1.MOV", "Song.mp3"], **KW).kind == H_MUSIC


def test_sprecherspuren():
    assert [is_speaker_track(t) for t in ("Spr 1", "Spr 2.01", "spr 3", "Spr", "Spr 4", "SLP Spr")] == \
        [True, True, True, False, False, False]
    assert base_track_name("A1.01") == "A1"


def test_stichwoerter_im_spurnamen():
    kw = {"o_ton": ["O-Ton", "OT"], "atmo": ["Atmo", "SFX"], "musik": ["Musik", "Mus"]}
    assert [track_keyword(t, kw) for t in ("Musik1", "Mus", "Atmo 2", "OT1", "A3", "Audio 9", "PILOT")] == \
        [MUSIC, MUSIC, ATMO, OTON, None, None, None]


def test_doppelfassungen():
    names = ["Audio 3_FX", "Audio 3_No FX", "Audio 4_FX", "Audio 4_No FX", "Audio 5"]
    assert second_versions(names) == {"Audio 3_No FX": "Audio 3_FX", "Audio 4_No FX": "Audio 4_FX"}
    assert second_versions(["Audio 3_FX", "Audio 4_No FX"]) == {}
