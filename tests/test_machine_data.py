"""Gemeinsamer Datenordner aller Benutzer eines Macs (Auftrag „rechnerweit“, 13.11.2026)."""
import json
import os
import stat
from datetime import datetime

from tonmischung.core import machine_data as MD
from tonmischung.core.catchup import CatchUpQueue
from tonmischung.core.fileio import SHARED_DIR_MODE, SHARED_FILE_MODE, ensure_shared_dir, file_lock, write_json
from tonmischung.core.havarie import PackageIndex
from tonmischung.core.mix_log import MixLog, MixRecord


def test_ordner_fuer_alle_benutzer_und_dateien_0664(tmp_path):
    d = ensure_shared_dir(tmp_path / "Shared" / "Ottilie")
    assert stat.S_IMODE(d.stat().st_mode) == SHARED_DIR_MODE                 # 2775: Gruppe erbt, nicht weltweit
    write_json(d / "x.json", {"a": 1}, mode=SHARED_FILE_MODE)
    assert stat.S_IMODE((d / "x.json").stat().st_mode) == SHARED_FILE_MODE
    with file_lock(d / "x.lock"):
        assert stat.S_IMODE((d / "x.lock").stat().st_mode) == SHARED_FILE_MODE
        with file_lock(d / "x.lock", wait=False) as got:                     # wie ein zweiter Benutzer
            assert got is False                                              # flock gilt je Öffnung der Datei
    with file_lock(d / "x.lock", wait=False) as got:
        assert got is True


def test_nicht_beschreibbar_bleibt_beim_benutzerordner(tmp_path):
    locked = tmp_path / "gesperrt"
    locked.mkdir()
    os.chmod(locked, 0o555)
    try:
        folder, hint = MD.location(str(locked / "Ottilie"), tmp_path / "benutzer")
        assert folder == tmp_path / "benutzer" and "nur für diesen Benutzer" in hint
    finally:
        os.chmod(locked, 0o755)
    folder, hint = MD.location(str(tmp_path / "frei"), tmp_path / "benutzer")
    assert folder == tmp_path / "frei" and hint == ""


def test_umzug_fuehrt_zusammen_ohne_doppelte_und_loescht_nichts(tmp_path):
    user, shared = tmp_path / "benutzer", tmp_path / "Ottilie"
    shared.mkdir()
    a = CatchUpQueue(user / MD.CATCH_UP)
    e1 = a.add_lock("p1", "Film 1", False)
    b = CatchUpQueue(shared / MD.CATCH_UP)
    b.add_lock("p2", "Film 2", False)
    both = (json.loads((shared / MD.CATCH_UP).read_text())["eintraege"]
            + [json.loads((user / MD.CATCH_UP).read_text())["eintraege"][0]])          # e1 steht schon in beiden
    write_json(shared / MD.CATCH_UP, {"version": 1, "eintraege": both})
    MixLog(user / MD.MIXES).add(MixRecord("geliefert", "Film 1", "p1", "", "Jens", "Ton 3", "/S/a.ptx", [], ""))
    PackageIndex(str(user / MD.PACKAGES)).remember("/S/a.ptx", "/P/a")
    PackageIndex(str(shared / MD.PACKAGES)).remember("/S/a.ptx", "/P/neu")
    notes = MD.migrate(user, shared, now=lambda: datetime(2026, 11, 13, 12, 0, 0))
    assert len(notes) == 3 and all("übernommen" in n for n in notes)
    ids = [e["id"] for e in json.loads((shared / MD.CATCH_UP).read_text())["eintraege"]]
    assert sorted(ids) == sorted({e1.id, *ids}) and len(ids) == len(set(ids)) == 2            # keine Doppelten
    assert len(MixLog(shared / MD.MIXES).entries()) == 1
    assert PackageIndex(str(shared / MD.PACKAGES)).lookup("/S/a.ptx") == "/P/neu"              # gemeinsamer gilt
    assert sorted(p.name for p in user.iterdir() if "migriert" in p.name) == [
        f"{n}.migriert-20261113-120000" for n in sorted((MD.CATCH_UP, MD.MIXES, MD.PACKAGES))]  # nur umbenannt
    assert MD.migrate(user, shared) == []                                                       # einmal genügt


def test_beschaedigte_gemeinsame_datei_wird_nicht_ueberschrieben(tmp_path):
    user, shared = tmp_path / "benutzer", tmp_path / "Ottilie"
    shared.mkdir()
    CatchUpQueue(user / MD.CATCH_UP).add_lock("p1", "Film 1", False)
    (shared / MD.CATCH_UP).write_text("{kaputt")
    notes = MD.migrate(user, shared)
    assert "beschädigt" in notes[0] and (user / MD.CATCH_UP).exists()
    assert (shared / MD.CATCH_UP).read_text() == "{kaputt"


def test_verweise_im_gemeinsamen_ordner_werden_nicht_befolgt(tmp_path):
    """Sicherheit 13.11.2026: ein anderer Benutzer ersetzt Sperrdatei, Datei oder Ordner durch einen Verweis."""
    import pytest
    victim = tmp_path / "opfer.txt"
    victim.write_text("geheim")
    os.chmod(victim, 0o600)
    d = ensure_shared_dir(tmp_path / "Ottilie")
    os.symlink(victim, d / "nachtrag.aendern.lock")
    with pytest.raises(OSError):
        with file_lock(d / "nachtrag.aendern.lock"):
            pass
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600                      # Rechte unverändert
    os.link(victim, d / "hart.lock")
    with pytest.raises(PermissionError):
        with file_lock(d / "hart.lock"):
            pass
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600
    os.symlink(victim, d / MD.MIXES)
    assert MixLog(d / MD.MIXES).entries() == []                             # beiseitegelegt, nicht gelesen
    assert victim.read_text() == "geheim" and not (d / MD.MIXES).is_symlink()
    home = tmp_path / "home"
    home.mkdir(mode=0o750)
    os.symlink(home, tmp_path / "Verweis")
    with pytest.raises(PermissionError):
        ensure_shared_dir(tmp_path / "Verweis")
    folder, hint = MD.location(str(home), tmp_path / "benutzer")             # vorhandener Ordner: nie umgestellt
    assert folder == home and stat.S_IMODE(home.stat().st_mode) == 0o750
    os.chmod(home, 0o777)
    folder, hint = MD.location(str(home), tmp_path / "benutzer")
    assert folder == tmp_path / "benutzer" and "für alle beschreibbar" in hint
    os.chmod(home, 0o750)


def test_abgebrochener_umzug_fuehrt_nicht_doppelt_zusammen(tmp_path, monkeypatch):
    user, shared = tmp_path / "benutzer", tmp_path / "Ottilie"
    shared.mkdir()
    CatchUpQueue(user / MD.CATCH_UP).add_lock("p1", "Film 1", False)
    real = MD.write_json

    def broken(*a, **k):
        raise OSError(28, "Platte voll")
    monkeypatch.setattr(MD, "write_json", broken)
    assert "nicht übernommen" in MD.migrate(user, shared)[0]
    assert (user / MD.CATCH_UP).exists()                                     # wieder an ihrem Platz
    monkeypatch.setattr(MD, "write_json", real)
    assert "übernommen" in MD.migrate(user, shared)[0] and not (user / MD.CATCH_UP).exists()
    assert MD.migrate(user, shared) == []
