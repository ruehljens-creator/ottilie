"""Spurprofile je Template: lesen, abgleichen, vorbelegen, ableiten – ohne feste Spurnamen."""
import pytest

from tonmischung.core import track_profile as TP
from tonmischung.core.track_profile import Defaults, ProfileTrack, SessionTrack, TrackProfile

HOUSE = Defaults(oton=("A1", "A2", "A3", "A4"), atmo=tuple(f"A{i}" for i in range(5, 11)),
                 music=tuple(f"Musik{i}" for i in range(1, 9)),
                 table=({"pt_spur": "ST", "inhalt": "Sendeton", "export": True, "r128": True},
                        {"pt_spur": "A  IT", "inhalt": "IT", "export": True, "r128": False},
                        {"pt_spur": "AD", "inhalt": "Audiodeskription", "export": False, "r128": False}),
                 recording={"rec_a": ["ST", "Spr"], "monitor_a": ["ST"]})

SESSION = [SessionTrack("Video1", "VideoTrack"), SessionTrack("ST", fmt="TF_Stereo"),
           SessionTrack("A  IT", fmt="TF_Stereo"), SessionTrack("Spr", fmt="TF_Mono"),
           SessionTrack("A1", fmt="TF_Mono", folder="O-Ton"), SessionTrack("A2", fmt="TF_Mono", folder="O-Ton"),
           SessionTrack("A5", fmt="TF_Mono"), SessionTrack("Musik1", fmt="TF_Mono"), SessionTrack("A11", fmt="TF_Mono"),
           SessionTrack("O-Ton", "BasicFolder")]


def test_erstes_lesen_belegt_nach_heutigen_einstellungen_vor():
    p = TP.merge_read(SESSION, None, HOUSE, "test", "helmut test", session="Probe", when="03.10.2026")
    by = {t.name: t for t in p.tracks}
    assert [t.name for t in p.tracks] == [t.name for t in SESSION]                    # Session-Reihenfolge
    assert (by["A1"].triage, by["A5"].triage, by["Musik1"].triage, by["A11"].triage) == ("o_ton", "atmo", "musik", "rest")
    assert by["A1"].hide_when_empty and by["Musik1"].hide_when_empty and not by["ST"].hide_when_empty
    assert (by["ST"].export, by["ST"].r128, by["ST"].channel, by["ST"].content) == (True, True, 1, "Sendeton")
    assert (by["A  IT"].channel, by["A  IT"].r128) == (3, False)                      # Name mit zwei Leerzeichen
    assert by["ST"].rec_a and by["ST"].monitor_a and by["Spr"].rec_a and not by["Spr"].monitor_a
    assert by["Video1"] == ProfileTrack("Video1", "VideoTrack")                       # keine Häkchen außer Audio
    assert by["A1"].folder == "O-Ton" and (p.read_from, p.read_at) == ("Probe", "03.10.2026")


def test_erneut_lesen_behaelt_haekchen_und_markiert_fehlende():
    first = TP.merge_read(SESSION, None, HOUSE, "test", "helmut test")
    edited = TrackProfile(first.template_group, first.template_name,
                          tuple(TP.replace(t, triage="atmo") if t.name == "A11" else t for t in first.tracks))
    again = TP.merge_read([t for t in SESSION if t.name != "A5"] + [SessionTrack("Geräusche", fmt="TF_Mono")],
                          edited, HOUSE, "", "")
    by = {t.name: t for t in again.tracks}
    assert by["A11"].triage == "atmo"                                                 # eigene Änderung bleibt
    assert by["Geräusche"] == ProfileTrack("Geräusche", fmt="TF_Mono")                # neu: ohne Häkchen
    assert by["A5"].missing and by["A5"].triage == "atmo"                             # nicht still gelöscht
    assert again.template_name == "helmut test" and again.tracks[-1].name == "A5"
    assert any("„A5“ fehlt in der Session" in x for x in TP.problems(again))


def test_ganz_anderes_template_ohne_hausnamen():
    """Flexibel: ein Template mit eigenen Namen bekommt beim ersten Lesen keine falschen Häkchen."""
    tracks = [SessionTrack("Dialog L"), SessionTrack("Dialog R"), SessionTrack("Ambience"), SessionTrack("Score")]
    p = TP.merge_read(tracks, None, HOUSE, "Jens", "Doku_lang")
    assert all(not (t.triage or t.export or t.hide_when_empty) for t in p.tracks)
    p = TrackProfile("Jens", "Doku_lang", (ProfileTrack("Dialog L", triage="o_ton", hide_when_empty=True),
                                           ProfileTrack("Dialog R", triage="o_ton", hide_when_empty=True),
                                           ProfileTrack("Ambience", triage="atmo"),
                                           ProfileTrack("Score", export=True, r128=True, content="Musik")))
    assert TP.triage_targets(p) == {"o_ton": ("Dialog L", "Dialog R"), "atmo": ("Ambience",), "musik": (), "rest": ()}
    view = TP.track_view(p)
    assert view["hideable"] == ["Dialog L", "Dialog R"] and view["targets"] == ["Dialog L", "Dialog R", "Ambience"]
    assert view["keep"] == ["Score"]
    assert TP.export_table(p) == [{"pt_spur": "Score", "inhalt": "Musik", "export": True, "r128": True,
                                   "exportname": ""}]


def test_exporttabelle_mit_kanaelen_und_luecken():
    p = TrackProfile("", "x", (ProfileTrack("ST", export=True, channel=1, content="Sendeton", r128=True),
                               ProfileTrack("Mus", export=True, channel=11, content="Musik"),
                               ProfileTrack("OGA", export=True, content="OGA"),          # ohne Kanal → erste Lücke
                               ProfileTrack("AD", export=False, channel=5),
                               ProfileTrack("Alt", export=True, channel=3, missing=True)))
    rows = TP.export_table(p)
    assert [r["pt_spur"] for r in rows] == ["ST", "OGA", "", "", "", "Mus"]
    assert rows[2] == {"pt_spur": "", "inhalt": "leer", "export": False, "r128": False}
    with pytest.raises(ValueError, match="Zu viele"):
        TP.export_table(TrackProfile("", "x", tuple(ProfileTrack(f"S{i}", export=True) for i in range(5))), 8)


def test_hinweise():
    p = TrackProfile("", "x", (ProfileTrack("ST", fmt="TF_Stereo", export=True, channel=1),
                               ProfileTrack("IT", export=True, channel=2),
                               ProfileTrack("Mus", export=True, channel=1),
                               ProfileTrack("Bus", "AuxTrack", export=True),
                               ProfileTrack("A1", fmt="TF_Stereo", triage="o_ton"),
                               ProfileTrack("Spr", r128=True)))
    text = "\n".join(TP.problems(p))
    assert "„IT“: Kanal 2" in text and "Kanal 1 doppelt: „ST“ und „Mus“" in text
    assert "„Bus“ ist keine Audiospur" in text and "„A1“ ist Stereo" in text and "„Spr“: R128 ohne Export" in text


def test_profil_finden_und_vorschlagen():
    a = TrackProfile("test", "helmut test", (ProfileTrack("ST"), ProfileTrack("A1"), ProfileTrack("A2")))
    b = TrackProfile("", "Doku_lang", (ProfileTrack("Dialog L"), ProfileTrack("Score")))
    assert TP.find([a, b], "test", "Helmut  Test") is a and TP.find([a, b], "andere", "helmut test") is None
    assert TP.find([a, b], "Jens", "doku_lang") is b                                   # ohne Gruppe: jede
    assert TP.best_match([a, b], ["ST", "A1", "Video"]) is a
    assert TP.best_match([a, b], ["ST"]) is None                                       # zu wenig Übereinstimmung


def test_einstellungen_hin_und_zurueck():
    p = TP.merge_read(SESSION, None, HOUSE, "test", "helmut test", session="S", when="heute")
    d = TP.profile_to_dict(p)
    assert d["template_name"] == "helmut test" and d["spuren"][1]["kanal"] == 1 and d["spuren"][1]["typ"] == "AudioTrack"
    back = TP.profiles_from_settings([d, {"template_name": ""}, "kaputt"])
    assert back == [p]
    odd = TP.profile_from_dict({"template_name": "x", "wenn_voll": "murks",
                                "spuren": [{"name": "A1", "triage": "falsch", "kanal": "3"}, {"name": ""}]})
    assert odd.when_full == "reservespuren" and odd.tracks == (ProfileTrack("A1", channel=3),)


def test_aufnahme_aus_dem_profil():
    p = TrackProfile("", "x", (ProfileTrack("ST", rec_a=True, monitor_a=True), ProfileTrack("Spr", rec_a=True),
                               ProfileTrack("Spr B", rec_b=True, play_input=True),
                               ProfileTrack("Alt", rec_a=True, missing=True)))
    assert TP.recording(p) == {"rec_a": ["ST", "Spr"], "monitor_a": ["ST"], "rec_b": ["Spr B"], "monitor_b": [],
                               "play_input": ["Spr B"]}


def test_vorbelegung_aus_den_echten_einstellungen(tmp_path):
    from tonmischung.config.settings import Settings
    d = TP.defaults_from_settings(Settings(local_path=tmp_path / "e.json").get)
    assert d.oton == ("A1", "A2", "A3", "A4") and d.atmo[-1] == "A10" and d.music[0] == "Musik1"
    assert (d.rest_prefix, d.rest_from, d.rest_to) == ("A", 11, None) and set(d.hide_prefixes) == {"A", "Musik"}
    assert d.table[0]["pt_spur"] == "ST" and "ST" in d.recording["rec_a"]


def test_triage_mit_profil_zielen():
    """Ausdrückliche Rest-Spuren aus dem Profil statt „ab A11“."""
    from tonmischung.triage.placement import _rest_tracks
    p = TrackProfile("", "x", (ProfileTrack("Dialog 1", triage="o_ton"), ProfileTrack("Dialog 2", triage="o_ton"),
                               ProfileTrack("Reserve 1", triage="rest"), ProfileTrack("Reserve 2", triage="rest"),
                               ProfileTrack("Bus", "AuxTrack", triage="atmo")))
    r = TP.target_ranges(p)
    assert r.oton == ("Dialog 1", "Dialog 2") and r.rest == ("Reserve 1", "Reserve 2") and r.atmo == ()
    assert r.rest_text() == "auf den Rest-Spuren"
    assert _rest_tracks(["A11", "A12"], 11) == ["A11", "A12"]                             # ohne Profil wie bisher


def test_spuransicht_mit_profil():
    from tonmischung.core.track_view import TrackState, plan_view
    states = [TrackState("Dialog 1", "AudioTrack", True, True), TrackState("Dialog 2", "AudioTrack", False, False),
              TrackState("Ambience", "AudioTrack", False, False), TrackState("A3", "AudioTrack", False, False)]
    p = TrackProfile("", "x", (ProfileTrack("Dialog 1", triage="o_ton", hide_when_empty=True),
                               ProfileTrack("Dialog 2", triage="o_ton", hide_when_empty=True),
                               ProfileTrack("Ambience", triage="atmo")))
    v = TP.track_view(p)
    plan = plan_view(states, v["keep"], True, True, targets=v["targets"], hideable=v["hideable"])
    assert plan.show == ("Dialog 1",) and plan.hide == ("Dialog 2",)                  # Ambience nicht angehakt, A3 fremd


def test_exportname_in_der_spurtabelle_und_doppelt():
    p = TrackProfile("", "x", (ProfileTrack("Sendeton Mix", export=True, channel=1, export_name="ST"),
                               ProfileTrack("Mix 2", export=True, channel=3, export_name="ST")))
    assert TP.export_table(p)[0]["exportname"] == "ST"
    assert "Exportname „ST“ doppelt: „Sendeton Mix“ und „Mix 2“" in TP.problems(p)
    d = TP.track_to_dict(p.tracks[0])
    assert d["exportname"] == "ST" and TP.track_from_dict(d) == p.tracks[0]


def test_export_und_import():
    a = TrackProfile("test", "helmut test", (ProfileTrack("ST", export=True, export_name="ST"),))
    b = TrackProfile("Jens", "Doku_lang", (ProfileTrack("Dialog", triage="o_ton"),))
    doc = TP.export_document([a, b], [{"name": "ST", "kanal": 1, "inhalt": "Sendeton"}], "heute", "Ton 3")
    import json
    doc = json.loads(json.dumps(doc))                                            # wie aus der Datei
    profiles, names = TP.read_document(doc)
    assert profiles == [a, b] and names == [{"name": "ST", "kanal": 1, "inhalt": "Sendeton"}]
    changed = TrackProfile("test", "Helmut Test", (ProfileTrack("ST"),))         # gleiches Template, anderer Inhalt
    plan = TP.import_plan(profiles, [changed, TrackProfile("x", "y")])
    assert [s for _, s in plan] == ["ersetzt", "neu"]
    assert [s for _, s in TP.import_plan([a], [a])] == ["gleich"]
    merged = TP.apply_import([changed, TrackProfile("x", "y")], [a, b])
    assert merged == [a, TrackProfile("x", "y"), b]
    assert TP.merge_export_names([{"name": "ST", "kanal": 1}], [{"name": "ST", "kanal": 9}, {"name": "Atmos", "kanal": 15}]) \
        == [{"name": "ST", "kanal": 1}, {"name": "Atmos", "kanal": 15}]
    with pytest.raises(TP.ImportFormatError, match="Keine Spurprofil-Datei"):
        TP.read_document({"profile": []})
    with pytest.raises(TP.ImportFormatError, match="kein Spurprofil"):
        TP.read_document({"format": TP.FORMAT, "version": 1, "profile": []})
    with pytest.raises(TP.ImportFormatError, match="neueren Version"):
        TP.read_document({"format": TP.FORMAT, "version": 99, "profile": [TP.profile_to_dict(a)]})
