"""Bild für das Sendefile: liegt neben dem AAF (Jens, 03.10.2026); Programmbeginn aus dem Bild-Timecode."""
import pytest

from tonmischung.delivery.picture import PictureError, candidates, find_picture, program_start


def _folder(tmp_path, *names):
    d = tmp_path / "Uebergabe"
    d.mkdir()
    for n in ("Beitrag_forPT.aaf", *names):
        (d / n).write_bytes(b"x")
    return d


def test_ein_bild_neben_dem_aaf(tmp_path):
    d = _folder(tmp_path, "Beitrag.mxf", "notizen.txt", ".versteckt.mov")
    (tmp_path / "anderswo.mxf").write_bytes(b"x")
    p = find_picture(str(d / "Beitrag_forPT.aaf"))
    assert p.path == str(d / "Beitrag.mxf") and p.candidates == (str(d / "Beitrag.mxf"),)


def test_mehrere_bilder_muessen_gewaehlt_werden(tmp_path):
    d = _folder(tmp_path, "Beitrag_ref.mp4", "Beitrag_master.MXF")
    aaf = str(d / "Beitrag_forPT.aaf")
    with pytest.raises(PictureError, match="bitte wählen: Beitrag_master.MXF, Beitrag_ref.mp4"):
        find_picture(aaf)
    assert find_picture(aaf, chosen=str(d / "Beitrag_master.MXF")).path.endswith("Beitrag_master.MXF")
    with pytest.raises(PictureError, match="nicht neben dem AAF"):
        find_picture(aaf, chosen=str(tmp_path / "fremd.mxf"))
    assert len(candidates(aaf)) == 2


def test_kein_bild_oder_kein_aaf(tmp_path):
    d = _folder(tmp_path)
    with pytest.raises(PictureError, match="Kein Bild neben dem AAF"):
        find_picture(str(d / "Beitrag_forPT.aaf"))
    with pytest.raises(PictureError, match="Kein AAF"):
        find_picture("")


def test_programmbeginn_aus_dem_timecode():
    assert program_start("10:00:00:00").offset_s == 0.0
    s = program_start("09:59:50:00")
    assert s.offset_s == 10.0 and "Startband im Bild: 10.00 s" in s.note
    assert program_start(None).offset_s == 0.0 and "ohne Timecode" in program_start(None).note
    with pytest.raises(PictureError, match="nach dem Programmbeginn"):
        program_start("10:00:05:00")
    with pytest.raises(PictureError, match="mehr als 30 s Startband"):
        program_start("01:00:00:00")
    assert program_start("09:59:20:00", max_leader_s=60).offset_s == 40.0


def test_verschobene_sequenz_misst_am_sequenzstart():
    """DaVinci ab 01:00:00:00 (50 fps), Premiere ab 00:00:00:00: der Import legte die Sequenz auf 10:00:00:00 –
    das Bild aus derselben Timeline beginnt dann am Programmbeginn (live 04.10.2026)."""
    s = program_start("01:00:00:00", fps=50, sequence_tc="01:00:00:00", sequence_fps=50)
    assert s.offset_s == 0.0 and "wie die Sequenz" in s.note and "Programmbeginn am Bildanfang" in s.note
    s = program_start("00:59:50:00", fps=25, sequence_tc="01:00:00:00", sequence_fps=50)
    assert s.offset_s == 10.0 and "Startband im Bild: 10.00 s" in s.note
    with pytest.raises(PictureError, match="mehr als 30 s Startband"):                  # ohne Verschiebung wie bisher
        program_start("01:00:00:00")
    with pytest.raises(PictureError, match="mehr als 30 s Startband"):                  # passt auch zur Sequenz nicht
        program_start("00:58:00:00", sequence_tc="01:00:00:00", sequence_fps=50)
    assert program_start("10:00:00:00", sequence_tc="01:00:00:00").offset_s == 0.0     # Bild schon auf 10 Uhr
