"""Spiegelung der Session-Ablage (Plan M7-Ablage): nie löschen, nie überschreiben, Zeitpunkte."""
import os
import types
from datetime import datetime

import pytest

from tonmischung.app.mirror_app import MirrorWatcher
from tonmischung.config.settings import Settings
from tonmischung.core.mixstate import MixState
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mapping import Contribution
from tonmischung.helmut.mock import MockHelmut
from tonmischung.helmut.service import MixService
from tonmischung.workflow.mirror import VERSIONS, mirror


def _session(tmp_path):
    src = tmp_path / "lokal" / "Wahl"
    (src / "Audio Files").mkdir(parents=True)
    (src / "Session File Backups").mkdir()
    (src / "Wahl.ptx").write_bytes(b"v1")
    (src / "Audio Files" / "A1.wav").write_bytes(b"audio")
    (src / "Session File Backups" / "Wahl.bak.000.ptx").write_bytes(b"bak")
    (src / ".DS_Store").write_bytes(b"x")
    return src


def _t(h, m, s=0):
    return lambda: datetime(2026, 10, 3, h, m, s)


def test_spiegeln_ohne_loeschen_und_ueberschreiben(tmp_path):
    src = _session(tmp_path)
    dst = tmp_path / "Helmut" / "Wahl"
    r = mirror(str(src), str(dst), now=_t(10, 0))
    assert sorted(os.path.relpath(p, dst) for p in r.copied) == ["Audio Files/A1.wav", "Session File Backups/Wahl.bak.000.ptx", "Wahl.ptx"]
    assert not (dst / ".DS_Store").exists() and r.text == "gespiegelt 10:00, 3 neu, 0 geändert"
    assert mirror(str(src), str(dst), now=_t(10, 1)).unchanged == 3
    (src / "Wahl.ptx").write_bytes(b"version2")
    (src / "Audio Files" / "A1.wav").unlink()                                   # lokal weg …
    r = mirror(str(src), str(dst), now=_t(10, 5, 7))
    assert [os.path.relpath(p, dst) for p in r.replaced] == ["Wahl.ptx"]
    assert (dst / "Wahl.ptx").read_bytes() == b"version2"
    assert (dst / VERSIONS / "20261003-100507" / "Wahl.ptx").read_bytes() == b"v1"    # alte Fassung aufgehoben
    assert (dst / "Audio Files" / "A1.wav").read_bytes() == b"audio"                  # … im Spiegel bleibt sie


def test_ohne_sicherungen_und_inhaltsvergleich(tmp_path):
    src = _session(tmp_path)
    dst = tmp_path / "Helmut" / "Wahl"
    mirror(str(src), str(dst), with_backups=False, now=_t(10, 0))
    assert not (dst / "Session File Backups").exists()
    st = os.stat(src / "Audio Files" / "A1.wav")
    (src / "Audio Files" / "A1.wav").write_bytes(b"AUDIO")                      # gleiche Größe, gleiche Zeit
    os.utime(src / "Audio Files" / "A1.wav", (st.st_atime, st.st_mtime))
    assert mirror(str(src), str(dst), with_backups=False, now=_t(10, 1)).replaced == []
    r = mirror(str(src), str(dst), by_content=True, with_backups=False, now=_t(10, 2))
    assert [os.path.basename(p) for p in r.replaced] == ["A1.wav"] and (dst / "Audio Files" / "A1.wav").read_bytes() == b"AUDIO"


@pytest.fixture
def watcher(tmp_path):
    src = _session(tmp_path)
    dst = tmp_path / "Helmut" / "Wahl"
    m = MockHelmut()
    m.add_project("w", "Wahl", "hs", state="in_mischung", mischer="Jens", raum="Ton 3", session_pfad=str(src / "Wahl.ptx"))
    url = m.start()
    client = HelmutClient(url, lambda: ("test", "test"), sleep=lambda s: None)
    c = Contribution("w", "Wahl", "hs", True, MixState("in_mischung", "Jens", "Ton 3", session_path=str(src / "Wahl.ptx"),
                                                       session_storage="lokal_spiegelung", session_mirror=str(dst)))
    opened = types.SimpleNamespace(name="Wahl", path=str(src / "Wahl.ptx"))
    ctx = types.SimpleNamespace(settings=Settings(local_path=tmp_path / "e.json"), helmut=client,
                                ptsl=types.SimpleNamespace(open_session=lambda: opened),
                                service=lambda: MixService(client, "Jens", "Ton 3"))
    clock = [0.0]
    owner = {"c": c}
    w = MirrorWatcher(ctx, owner=lambda p: owner["c"], clock=lambda: clock[0])
    yield w, ctx, m, src, dst, clock, owner
    m.stop()


def test_nach_dem_speichern_wenn_ruhig(watcher):
    w, ctx, m, src, dst, clock, owner = watcher
    w.tick()
    assert not dst.exists()                                                   # erst einmal gesehen
    w.tick()
    assert (dst / "Wahl.ptx").read_bytes() == b"v1" and w.state["zeit"]
    assert [a["filepath"] for a in m.assets["w"]] == [str(dst / "Wahl.ptx")]  # Session als Asset, einmal
    (src / "Wahl.ptx").write_bytes(b"v2")
    os.utime(src / "Wahl.ptx", (1e9, 1e9))
    w.tick()
    assert (dst / "Wahl.ptx").read_bytes() == b"v1"                           # noch nicht ruhig
    w.tick()
    assert (dst / "Wahl.ptx").read_bytes() == b"v2" and len(m.assets["w"]) == 1


def test_alle_n_minuten_und_nur_eigene(watcher):
    w, ctx, m, src, dst, clock, owner = watcher
    ctx.settings.set_local("import.session_ablage.spiegeln", "alle_n_minuten")
    ctx.settings.set_local("import.session_ablage.spiegel_intervall_min", 10)
    clock[0] = 100.0
    w.tick()
    assert dst.exists()                                                       # erster Lauf sofort
    (src / "Wahl.ptx").write_bytes(b"v2")
    os.utime(src / "Wahl.ptx", (1e9, 1e9))
    clock[0] = 300.0
    w.tick()
    assert (dst / "Wahl.ptx").read_bytes() == b"v1"
    clock[0] = 701.0
    w.tick()
    assert (dst / "Wahl.ptx").read_bytes() == b"v2"
    owner["c"] = None                                                          # fremde Session: nichts
    (src / "Wahl.ptx").write_bytes(b"v3")
    w.before_close()
    assert (dst / "Wahl.ptx").read_bytes() == b"v2"
