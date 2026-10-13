"""Bauskript der Werkzeuge: der Zielordner wird nur gelöscht, wenn er sicher ein eigener Bauordner ist (Review A21)."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "bundle_tools.py"


@pytest.fixture(scope="module")
def bt():
    spec = importlib.util.spec_from_file_location("bundle_tools", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fremder_ordner_wird_nicht_geloescht(bt, tmp_path):
    root, desk = tmp_path / "projekt", tmp_path / "Schreibtisch"
    (root / "build").mkdir(parents=True)
    desk.mkdir()
    (desk / "Mischung.ptx").write_text("x")
    with pytest.raises(SystemExit, match="wird nicht gelöscht"):
        bt.clear_target(str(desk), root=str(root))
    with pytest.raises(SystemExit):
        bt.clear_target(str(root / "build"), root=str(root))               # build selbst nicht, nur darunter
    (tmp_path / "link").symlink_to(desk)
    with pytest.raises(SystemExit):
        bt.clear_target(str(tmp_path / "link"), root=str(root))
    assert (desk / "Mischung.ptx").exists()


def test_eigene_bauordner_werden_ersetzt(bt, tmp_path):
    root = tmp_path / "projekt"
    (root / "build" / "bin" / "lib").mkdir(parents=True)
    bt.clear_target(str(root / "build" / "bin"), root=str(root))
    assert not (root / "build" / "bin").exists()
    other = tmp_path / "anderswo"
    other.mkdir()
    (other / bt.MARKER).write_text("")
    bt.clear_target(str(other), root=str(root))                            # früher von bundle_tools angelegt
    assert not other.exists()
    bt.clear_target(str(tmp_path / "gibtsnicht"), root=str(root))
