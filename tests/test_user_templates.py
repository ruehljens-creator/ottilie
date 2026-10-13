"""Templates je Nutzer (Plan Nutzer & Räume, Schritt 3): hochladen, in Pro Tools bereitstellen, Standard, Archiv."""
import os
from datetime import datetime

import pytest

from fakes import FakeEngine
from tonmischung.app import templates_app as TA
from tonmischung.app import users_app as UA
from tonmischung.app.bridge import CommandError
from tonmischung.app.context import AppContext
from tonmischung.config.settings import Settings
from tonmischung.core import user_templates as UT
from tonmischung.helmut.client import HelmutClient
from tonmischung.ptsl.client import PtslClient

NOW = datetime(2026, 10, 4, 19, 0)


def _template(root, name, video=True, own_folder=True):
    folder = root / name if own_folder else root
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.ptxt").write_bytes(b"PTXT " + name.encode())
    if video and own_folder:
        (folder / "Video Files").mkdir()
        (folder / "Video Files" / "Startband.mxf").write_bytes(b"MXF")
    return folder / f"{name}.ptxt"


def test_hochladen_bereitstellen_archiv(tmp_path):
    user = tmp_path / "Nutzer" / "jens"
    pt = tmp_path / "PT"
    src = _template(tmp_path / "Quelle", "Haus Standard")
    assert UT.upload(str(user), str(src)) == "Haus Standard"
    assert (user / "Templates" / "Haus Standard" / "Video Files" / "Startband.mxf").exists()   # Ordner mit Video Files
    with pytest.raises(UT.TemplateError, match="gibt es bei diesem Nutzer schon"):
        UT.upload(str(user), str(src))
    single = _template(tmp_path / "Gruppe", "Einzeln", own_folder=False)                     # nur die Datei
    assert UT.upload(str(user), str(single)) == "Einzeln" and (user / "Templates" / "Einzeln" / "Einzeln.ptxt").exists()
    with pytest.raises(UT.TemplateError, match=".ptxt"):
        UT.upload(str(user), str(tmp_path / "x.txt"))
    arch = str(tmp_path / "Nutzer" / "Archiv")
    # Vor der Umbenennung (08.10.2026) hochgeladene Templates stehen unter „Tonmischung <Nutzer>“ und gelten weiter
    assert UT.group_for("Jens") == "Ottilie Jens" and UT.is_user_group("Tonmischung Jens") and not UT.is_user_group("Haus")
    dst = UT.install(str(user), "Haus Standard", "Tonmischung Jens", arch, pt_root=str(pt), now=NOW)
    assert dst == str(pt / "Tonmischung Jens" / "Haus Standard") and os.path.isfile(os.path.join(dst, "Haus Standard.ptxt"))
    assert UT.install(str(user), "Haus Standard", "Tonmischung Jens", arch, pt_root=str(pt), now=NOW) == dst   # gleich
    (user / "Templates" / "Haus Standard" / "Haus Standard.ptxt").write_bytes(b"PTXT neu")       # neue Fassung
    UT.install(str(user), "Haus Standard", "Tonmischung Jens", arch, pt_root=str(pt), now=NOW)
    assert (pt / "Tonmischung Jens" / "Haus Standard" / "Haus Standard.ptxt").read_bytes() == b"PTXT neu"
    assert (tmp_path / "Nutzer" / "Archiv" / "Templates" / "installiert-Tonmischung Jens-Haus Standard-20261004-190000"
            / "Haus Standard.ptxt").read_bytes() == b"PTXT Haus Standard"                       # alte Kopie im Archiv
    dest = UT.archive(str(user), "Haus Standard", "Tonmischung Jens", arch, "jens", pt_root=str(pt), now=NOW)
    assert dest.endswith("Templates/jens-Haus Standard-20261004-190000") and not (user / "Templates" / "Haus Standard").exists()
    assert not (pt / "Tonmischung Jens" / "Haus Standard").exists()


def _ctx(tmp_path):
    s = Settings(local_path=tmp_path / "e.json")
    s.set_local("system.raeume_nutzer.ablage", "gemeinsamer_ordner")
    s.set_local("system.raeume_nutzer.gemeinsamer_ordner", str(tmp_path / "Storage"))
    ctx = AppContext(s, HelmutClient("http://127.0.0.1:9", lambda: ("t", "t")),
                     PtslClient(engine_factory=lambda: FakeEngine()), "Jens", "Ton 3", str(tmp_path))
    ctx.extra["pt_template_root"] = str(tmp_path / "PT")
    return ctx


def test_meine_templates_im_fenster(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    with pytest.raises(CommandError, match="anmelden"):
        TA.upload(ctx, {"path": "x"})
    UA.startup(ctx)
    UA.login(ctx, None, {"id": "jens"})
    src = _template(tmp_path / "Quelle", "Haus Standard")
    v = TA.upload(ctx, {"path": str(src)})
    assert v["mine"] == [{"id": "tm-haus-standard", "name": "Haus Standard", "group": "Ottilie Jens",
                          "label": "Haus Standard", "uploaded": True, "standard": True}]          # erstes = Standard
    _template(tmp_path / "PT" / "test", "helmut test")
    v = TA.view(ctx)
    assert [(f["group"], f["name"]) for f in v["found"]] == [("test", "helmut test")]       # bereitgestellte zählt nicht doppelt
    v = TA.adopt(ctx, {"group": "test", "name": "helmut test"})
    assert [m["id"] for m in v["mine"]] == ["tm-haus-standard", "pt-test-helmut-test"] and v["found"] == []
    with pytest.raises(CommandError, match="schon"):
        TA.adopt(ctx, {"group": "test", "name": "helmut test"})
    TA.set_standard(ctx, {"id": "pt-test-helmut-test"})
    lib = TA.library(ctx)
    assert lib["standard"] == {"group": "test", "name": "helmut test"}
    v = TA.delete(ctx, {"id": "pt-test-helmut-test"})                                      # nur aus der Liste
    assert "bleibt in Pro Tools" in v["message"] and v["standard"] == "" and (tmp_path / "PT" / "test" / "helmut test").exists()
    v = TA.delete(ctx, {"id": "tm-haus-standard"})
    assert "ins Archiv" in v["message"] and v["mine"] == []
    # andere Mac: Template fehlt in der Bibliothek → vor dem Anlegen bereitstellen
    TA.upload(ctx, {"path": str(_template(tmp_path / "Q2", "Zweit"))})
    import shutil
    shutil.rmtree(tmp_path / "PT" / "Ottilie Jens")                                    # Testaufbau: „anderer Mac“
    TA.ensure_installed(ctx, "Ottilie Jens", "Zweit")
    assert (tmp_path / "PT" / "Ottilie Jens" / "Zweit" / "Zweit.ptxt").exists()
    TA.ensure_installed(ctx, "test", "helmut test")                                         # Pro-Tools-Template: nichts tun
