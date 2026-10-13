"""Sendefile bauen (M5 Schritt 2): Profile, Startband, Kanalbelegung – Logik ohne Werkzeuge, dazu echte
Läufe mit ffmpeg und bmx auf kurzen synthetischen Clips (übersprungen, wenn die Werkzeuge fehlen)."""
import os
import shutil
import subprocess

import numpy as np
import pytest

from tonmischung.delivery import mux as M
from tonmischung.delivery.profiles import COPY, RECODE, ProfileError, bmx_args, decide_mode, mismatches, profile

FFMPEG, FFPROBE, BMX = shutil.which("ffmpeg"), shutil.which("ffprobe"), shutil.which("bmxtranswrap")
needs_tools = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe nicht installiert")

XDCAM_STREAM = {"codec_name": "mpeg2video", "profile": "4:2:2", "width": 1920, "height": 1080, "pix_fmt": "yuv422p",
                "field_order": "tt", "r_frame_rate": "25/1", "bit_rate": "50000000"}


def test_profile_und_modus():
    p = profile("ARD_ZDF_HDF01b")
    assert (p.tracks, p.bmx_type, p.size) == (16, "rdd9", "1920x1080") and profile("ARD_ZDF_HDF01a").tracks == 8
    assert profile("ARD_ZDF_HDF03b").size == "1280x720" and profile("ARD_ZDF_HDF02a").bmx_type == "op1a"
    with pytest.raises(ProfileError, match="nicht hinterlegt"):
        profile("ARD_ZDF_HDF09")
    assert mismatches(p, XDCAM_STREAM) == []
    assert decide_mode("auto", p, XDCAM_STREAM)[0] == COPY
    h264 = dict(XDCAM_STREAM, codec_name="h264", profile="High", field_order="progressive", bit_rate="8000000")
    mode, why = decide_mode("auto", p, h264)
    assert mode == RECODE and "Codec h264" in why and "Abtastung" in why
    with pytest.raises(ProfileError, match="Umpacken"):
        decide_mode("rewrap", p, h264)
    assert decide_mode("recode", p, XDCAM_STREAM)[0] == RECODE
    assert bmx_args(p, 8) == ["-t", "rdd9", "--ard-zdf-hdf", "--afd", "8"]


def _job(**kw):
    base = dict(video="/v.mxf", audio=(M.AudioSource("/ST.wav"), M.AudioSource("/IT.wav"))
                + (M.AudioSource(None),) * 6, out="/o/S.mxf", profile=profile("ARD_ZDF_HDF01b"), mode=COPY)
    base.update(kw)
    return M.MuxJob(**base)


def test_timecode_rechnen():
    assert M.offset_from_timecode("09:59:50:00") == 10.0 and M.offset_from_timecode(None) is None
    assert M.frames_to_tc(M.tc_to_frames("10:00:00:00") - 1) == "09:59:59:24"


def test_startband_entfernen_beim_umpacken_auf_keyframe():
    lp = M.plan_leader(_job(program_offset_s=10.0), keyframe_before=lambda t: 9.6)
    assert (lp.cut_s, lp.delay_ms, lp.first_tc) == (9.6, 400, "09:59:59:15") and "Keyframe" in lp.note
    exact = M.plan_leader(_job(program_offset_s=10.0), keyframe_before=lambda t: 10.0)
    assert (exact.cut_s, exact.delay_ms, exact.first_tc) == (10.0, 0, "10:00:00:00")
    recode = M.plan_leader(_job(program_offset_s=10.0, mode=RECODE), keyframe_before=lambda t: 0.0)
    assert (recode.cut_s, recode.delay_ms, recode.first_tc) == (10.0, 0, "10:00:00:00")


def test_startband_behalten_verschiebt_den_ton():
    lp = M.plan_leader(_job(program_offset_s=10.0, keep_leader=True), keyframe_before=lambda t: 0.0)
    assert (lp.cut_s, lp.delay_ms, lp.first_tc) == (0.0, 10000, "09:59:50:00")
    assert M.plan_leader(_job(), keyframe_before=lambda t: 0.0).first_tc == "10:00:00:00"


def test_kommando_16_monospuren_mit_stille():
    lp = M.LeaderPlan(9.6, 400, "09:59:59:15", "")
    cmd = M.build_command(_job(), "ffmpeg", 70.0, lp, "/w/x.ff.mxf")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert cmd[cmd.index("-ss") + 1] == "9.600" and cmd.index("-ss") < cmd.index("-i")
    assert "[1:a:0]adelay=400:all=1,channelsplit=channel_layout=stereo[t1_0][t1_1]" in graph
    assert "[t1_0]apad=whole_dur=60.400,atrim=0:60.400[a1_0]" in graph
    assert graph.count("anullsrc=channel_layout=mono:sample_rate=48000:duration=60.400") == 12
    assert cmd.count("-map") == 17 and ["-c:v", "copy"] == cmd[cmd.index("-c:v"):cmd.index("-c:v") + 2]
    assert "-t" not in cmd and cmd[cmd.index("-timecode") + 1] == "09:59:59:15" and "-n" in cmd
    recode = M.build_command(_job(mode=RECODE), "ffmpeg", 70.0, M.LeaderPlan(10, 0, "10:00:00:00", ""), "/w/y")
    assert "mpeg2video" in recode and recode[recode.index("-t") + 1] == "60.000"
    # Halbbildreihenfolge ausdrücklich (ffmpeg 8 schrieb sonst „tb“); beim Umpacken unverändert
    assert recode[recode.index("-filter_complex") + 1].startswith("[0:v:0]fieldorder=tff,setfield=tff[v];")
    assert recode[recode.index("-map") + 1] == "[v]" and recode.count("-map") == 17
    assert "fieldorder" not in graph and cmd[cmd.index("-map") + 1] == "0:v:0"


def test_falsche_spurzahl_und_ohne_material():
    with pytest.raises(M.MuxError, match="14 Tonspuren statt 16"):
        M.build_command(_job(audio=(M.AudioSource("/ST.wav"),) + (M.AudioSource(None),) * 6), "ffmpeg", 10,
                        M.LeaderPlan(0, 0, "10:00:00:00", ""), "/w/x")
    with pytest.raises(M.MuxError, match="ohne Material|Keine Tonspur"):
        M.build_command(_job(audio=(M.AudioSource(None),) * 8), "ffmpeg", 10, M.LeaderPlan(0, 0, "10:00:00:00", ""), "/w/x")


def test_kanalbelegung_aus_der_spurtabelle():
    from tonmischung.workflow.havarie_delivery import channel_tracks
    table = [{"pt_spur": "ST", "export": True}, {"pt_spur": "IT", "export": True}]
    src = M.audio_sources(channel_tracks(table, 16), {"ST": "/e/ST.wav", "IT": "/e/IT.wav"})
    assert [s.path for s in src] == ["/e/ST.wav", "/e/IT.wav"] + [None] * 6
    with pytest.raises(M.MuxError, match="Keine WAV"):
        M.audio_sources(["AD", "AD"], {})


# ── Echte Läufe ────────────────────────────────────────────────────────────
def _wav(path, seconds, freq, channels=2):
    sf = pytest.importorskip("soundfile")
    t = np.arange(int(48000 * seconds)) / 48000
    sf.write(str(path), np.column_stack([0.25 * np.sin(2 * np.pi * freq * t)] * channels), 48000, subtype="PCM_24")
    return str(path)


def _source(path, *args):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25", *args,
                    "-an", str(path)], check=True, timeout=120)
    return str(path)


@pytest.fixture(scope="module")
def xdcam_source(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg nicht installiert")
    d = tmp_path_factory.mktemp("bild")
    # 3 s XDCAM HD422 mit 1 s Startband davor: Timecode 09:59:59:00, Keyframes alle 12 Bilder
    return _source(d / "bild.mxf", "-t", "3", "-c:v", "mpeg2video", "-profile:v", "0", "-level:v", "2", "-b:v", "50M",
                   "-minrate", "50M", "-maxrate", "50M", "-bufsize", "17825792", "-pix_fmt", "yuv422p",
                   "-flags", "+ildct+ilme", "-field_order", "tt", "-g", "12", "-bf", "2", "-aspect", "16:9",
                   "-timecode", "09:59:59:00", "-f", "mxf")


def _probe(path):
    return M.probe(FFPROBE, str(path))


@needs_tools
def test_echt_umpacken_xdcam_16_monospuren(tmp_path, xdcam_source):
    info = _probe(xdcam_source)
    p = profile("ARD_ZDF_HDF01b")
    assert decide_mode("auto", p, M.video_stream(info))[0] == COPY
    off = M.offset_from_timecode(M.timecode(info))
    assert off == 1.0
    st, it = _wav(tmp_path / "ST.wav", 2.0, 440), _wav(tmp_path / "IT.wav", 2.0, 880)
    job = _job(video=xdcam_source, audio=(M.AudioSource(st), M.AudioSource(it)) + (M.AudioSource(None),) * 6,
               out=str(tmp_path / "out" / "Sendefile.mxf"), program_offset_s=off, use_bmx=bool(BMX))
    seen = []
    r = M.run(job, ffmpeg=FFMPEG, ffprobe=FFPROBE, bmx=BMX or "", on_progress=seen.append, low_priority=False)
    assert r.path == job.out and os.path.isfile(r.path) and seen[-1] == 1.0
    # Keyframe vor 1,0 s liegt bei 0,96 s → 1 Bild Startband bleibt, Ton 40 ms später, TC 09:59:59:24
    assert (r.leader.cut_s, r.leader.delay_ms, r.leader.first_tc) == (0.96, 40, "09:59:59:24")
    out = _probe(r.path)
    audio = [s for s in out["streams"] if s["codec_type"] == "audio"]
    assert len(audio) == 16 and {(s["channels"], s["sample_rate"], s["codec_name"]) for s in audio} == {(1, "48000", "pcm_s24le")}
    assert M.video_stream(out)["codec_name"] == "mpeg2video" and M.timecode(out) == "09:59:59:24"
    assert abs(M.duration_s(out) - 2.04) < 0.05
    assert sorted(os.listdir(tmp_path / "out")) == ["Sendefile.mxf"]          # keine Zwischendateien
    with pytest.raises(M.MuxError, match="nicht überschrieben"):
        M.run(job, ffmpeg=FFMPEG, ffprobe=FFPROBE, bmx=BMX or "", low_priority=False)


@needs_tools
def test_echt_neu_kodieren_mit_startband_behalten(tmp_path):
    src = _source(tmp_path / "ref.mov", "-t", "2", "-s", "640x360", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                  "-timecode", "09:59:59:12")
    info = _probe(src)
    p = profile("ARD_ZDF_HDF01a")
    mode, _ = decide_mode("auto", p, M.video_stream(info))
    assert mode == RECODE
    mono = _wav(tmp_path / "Spr.wav", 1.5, 300, channels=1)                 # Mono-WAV auf einem Paar
    job = M.MuxJob(src, (M.AudioSource(mono),) + (M.AudioSource(None),) * 3, str(tmp_path / "S8.mxf"), p, mode,
                   program_offset_s=M.offset_from_timecode(M.timecode(info)), keep_leader=True, use_bmx=bool(BMX))
    r = M.run(job, ffmpeg=FFMPEG, ffprobe=FFPROBE, bmx=BMX or "", low_priority=False)
    assert (r.leader.cut_s, r.leader.delay_ms, r.leader.first_tc) == (0.0, 520, "09:59:59:12")   # 13 Bilder
    out = _probe(r.path)
    v = M.video_stream(out)
    assert (v["codec_name"], v["width"], v["height"], v.get("field_order")) == ("mpeg2video", 1920, 1080, "tt")
    assert len([s for s in out["streams"] if s["codec_type"] == "audio"]) == 8
    assert M.timecode(out) == "09:59:59:12" and abs(M.duration_s(out) - 2.0) < 0.05


@needs_tools
def test_haengendes_ffmpeg_wird_abgebrochen(tmp_path):
    with pytest.raises(M.MuxError, match="nicht voran"):
        M.run_ffmpeg(["/bin/sleep", "5"], 1.0, lambda f: None, stall_s=1.0)
    with pytest.raises(M.MuxError, match="Abgebrochen"):
        M.run_ffmpeg(["/bin/sleep", "5"], 1.0, lambda f: None, cancelled=lambda: True)


def test_viel_fehlerausgabe_blockiert_ffmpeg_nicht():
    """Review A9: 1 MB Warnungen auf stderr – vorher lief die Leitung voll und das Programm blieb stehen."""
    import sys
    import time
    spam = "import sys\nfor i in range(20000): sys.stderr.write('Warnung %05d: Past duration too large\\n' % i)\n"
    t = time.monotonic()
    M.run_ffmpeg([sys.executable, "-c", spam], 1.0, lambda f: None, stall_s=5.0)
    assert time.monotonic() - t < 5
    fail = spam + "sys.stderr.write('Datei kaputt\\n'); sys.exit(3)\n"
    with pytest.raises(M.MuxError, match=r"ffmpeg-Fehler \(3\): .*Warnung 19999.*\| Datei kaputt"):
        M.run_ffmpeg([sys.executable, "-c", fail], 1.0, lambda f: None, stall_s=5.0)
