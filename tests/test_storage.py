"""Session-Ablage (Plan M7-Ablage): Ablageort je Art, Platzhalter, Schutz im Entwicklungsmodus."""
import pytest

from fakes import FakeEngine
from tonmischung.app.context import AppContext
from tonmischung.config.settings import Settings
from tonmischung.core.mixstate import MixState
from tonmischung.core.storage import HELMUT, LOCAL, MIRROR, StorageError, placement, project_folder, resolve
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mapping import Contribution
from tonmischung.helmut.mock import MockHelmut
from tonmischung.ptsl.client import PtslClient

TPL = "{projekt}/Ton/ProTools/"


def _c(path="/Volumes/Helmut/hs/Wahl/Wahl.prproj", meta=()):
    return Contribution("p1", "Wahl: Ergebnis", "Heimatjournal", False, MixState(),
                        {"path": path, "metadata": list(meta)})


def test_projektordner_und_platzhalter():
    assert project_folder({"path": "/V/H/Wahl/Wahl.prproj"}) == "/V/H/Wahl"
    assert project_folder({"path": "", "savePath": "/V/H/Wahl/"}) == "/V/H/Wahl"
    # Review B13: Ordner mit Punkt im Namen sind keine Dateien
    assert project_folder({"path": "/V/H/Heimatjournal 09.10.2026"}) == "/V/H/Heimatjournal 09.10.2026"
    assert project_folder({"path": "/V/H/Wahl v1.final"}, isdir=lambda p: True) == "/V/H/Wahl v1.final"
    assert project_folder({"path": "/V/H/Wahl/Wahl.aaf"}, isdir=lambda p: False) == "/V/H/Wahl"
    assert project_folder({}) is None
    assert resolve("{projekt}/Ton/{sendung}_{titel}_{version}/", "/V/H/Wahl", "heimat/journal", "Wahl: Ergebnis", "v3") \
        == "/V/H/Wahl/Ton/heimat_journal_Wahl_v3"
    with pytest.raises(StorageError, match="Projektordner"):
        resolve(TPL, None, "hs", "Wahl")


def test_ablage_je_art():
    assert placement(LOCAL, _c(), "/Lokal/Sessions/", TPL) == placement(LOCAL, _c(), "/Lokal/Sessions", TPL)
    pl = placement(HELMUT, _c(), "/Lokal", TPL)
    assert (pl.parent, pl.mirror_parent) == ("/Volumes/Helmut/hs/Wahl/Ton/ProTools", "")
    pl = placement(MIRROR, _c(), "/Lokal", TPL)
    assert (pl.parent, pl.mirror_parent) == ("/Lokal", "/Volumes/Helmut/hs/Wahl/Ton/ProTools")
    assert pl.mirror_dir("Wahl") == "/Volumes/Helmut/hs/Wahl/Ton/ProTools/Wahl"
    fallback = placement(MIRROR, _c(path=""), "/Lokal", TPL)
    assert fallback.mode == LOCAL and "ohne Spiegelung" in fallback.hint
    with pytest.raises(StorageError):
        placement(HELMUT, _c(path=""), "/Lokal", TPL)
    v = placement(HELMUT, _c(meta=[{"name": "version", "value": "v2"}]), "/L", "{projekt}/{version}")
    assert v.parent.endswith("/Wahl/v2")


def test_entwicklungsmodus_nur_im_testordner(tmp_path):
    m = MockHelmut()
    url = m.start()
    try:
        ctx = AppContext(Settings(local_path=tmp_path / "e.json"), HelmutClient(url, lambda: ("t", "t")),
                         PtslClient(engine_factory=lambda: FakeEngine()), "Jens", "Ton 3", str(tmp_path / "lokal"),
                         m, "x", safe_root=str(tmp_path))
        ctx.settings.set_local("import.session_ablage.modus", HELMUT)
        with pytest.raises(StorageError, match="nur im Testordner"):
            ctx.placement(_c())
        assert ctx.placement(_c(path=str(tmp_path / "H" / "Wahl.prproj"))).parent == str(tmp_path / "H" / "Ton" / "ProTools")
    finally:
        m.stop()
