"""Transfer in der Havarie: Ordner, USB, FTP/FTPS – atomar, nie überschreiben, Wiederholung, nur lokale Testziele."""
import os
import plistlib

import pytest

from tonmischung.delivery import transfer as T


def _files(tmp_path):
    src = tmp_path / "out"
    src.mkdir()
    (src / "S.mxf").write_bytes(b"m" * 5000)
    (src / "S.mxf.sha256").write_text("abc  S.mxf\n")
    return str(src / "S.mxf"), str(src / "S.mxf.sha256")


def test_ziel_aus_den_einstellungen_und_pruefung():
    t = T.target_from_dict({"key": "playout", "name": "Playout", "protokoll": "FTPS", "host": "ftp.local",
                            "benutzer": "sender", "zielpfad": "/in", "schluesselbund_eintrag": "tm-playout"})
    assert (t.protocol, t.retries, t.atomic, t.send_sha256) == ("ftps", 3, True, True)
    assert T.problems(T.Target("x", "X", "ftp")) == ["Ziel „X“: kein Server angegeben"]
    assert T.problems(T.Target("x", "X", "ordner")) == ["Ziel „X“: kein Ordner gewählt"]
    assert "unbekanntes Protokoll" in T.problems(T.Target("x", "X", "http"))[0]


def test_ordner_atomar_mit_sha256_und_kennzeichen(tmp_path):
    sf, sha = _files(tmp_path)
    dest = tmp_path / "playout"
    dest.mkdir()
    seen = []
    r = T.send(T.Target("p", "Playout", "ordner", path=str(dest), done_marker=".done"), sf, sha, on_progress=seen.append)
    assert r.ok and r.sent == ["S.mxf", "S.mxf.sha256", "S.mxf.done"] and r.attempts == 1
    assert (dest / "S.mxf").read_bytes() == b"m" * 5000 and seen[-1] == 1.0
    assert not any(n.endswith(T.TEMP_SUFFIX) for n in os.listdir(dest))


def test_ordner_ueberschreibt_nie(tmp_path):
    sf, sha = _files(tmp_path)
    dest = tmp_path / "playout"
    dest.mkdir()
    (dest / "S.mxf").write_bytes(b"alt")
    sleeps = []
    r = T.send(T.Target("p", "Playout", "ordner", path=str(dest)), sf, sha, sleep=sleeps.append)
    assert not r.ok and "nichts überschrieben" in r.error and r.attempts == 1 and sleeps == []
    assert (dest / "S.mxf").read_bytes() == b"alt"
    r = T.send(T.Target("p", "Playout", "ordner", path=str(tmp_path / "fehlt"), retries=2), sf, sleep=sleeps.append)
    assert "nicht erreichbar" in r.error and r.attempts == 3 and sleeps == [2.0, 4.0]


class FakeFTP:
    def __init__(self, existing=(), fail_store=0):
        self.files, self.cmds, self.fail_store = dict.fromkeys(existing, b"x"), [], fail_store
        self.cwd_path = "/"

    def connect(self, host, port, timeout):
        self.cmds.append(f"connect {host}:{port}")

    def login(self, user, pw):
        self.cmds.append(f"login {user}:{'*' * len(pw)}")

    def prot_p(self):
        self.cmds.append("prot_p")

    def set_pasv(self, v):
        pass

    def cwd(self, d):
        self.cmds.append(f"cwd {d}")

    def mkd(self, d):
        self.cmds.append(f"mkd {d}")

    def nlst(self):
        return list(self.files)

    def storbinary(self, cmd, fh, blocksize=8192, callback=None):
        if self.fail_store:
            self.fail_store -= 1
            raise EOFError("Verbindung weg")
        data = fh.read()
        self.files[cmd.split(" ", 1)[1]] = data
        if callback:
            callback(data)

    def voidcmd(self, c):
        pass

    def size(self, name):
        return len(self.files[name])

    def rename(self, a, b):
        self.files[b] = self.files.pop(a)
        self.cmds.append(f"rename {a} {b}")

    def quit(self):
        pass

    def close(self):
        pass


def test_ftps_atomar_mit_kennwort_aus_dem_schluesselbund(tmp_path):
    sf, sha = _files(tmp_path)
    ftp = FakeFTP()
    t = T.Target("p", "Playout", "ftps", host="ftp.local", user="sender", path="/in/sender", keychain="tm-playout")
    r = T.send(t, sf, sha, password=lambda tt: "geheim", ftp_factory=lambda tls: ftp)
    assert r.ok and sorted(ftp.files) == ["S.mxf", "S.mxf.sha256"]
    assert "prot_p" in ftp.cmds and "login sender:******" in ftp.cmds and "rename S.mxf.uploading S.mxf" in ftp.cmds
    assert "cwd /" in ftp.cmds and "cwd in" in ftp.cmds


def test_ftp_nie_ueberschreiben_und_wiederholen(tmp_path):
    sf, _ = _files(tmp_path)
    t = T.Target("p", "Playout", "ftp", host="ftp.local", retries=2)
    r = T.send(t, sf, password=lambda tt: "", ftp_factory=lambda tls: FakeFTP(existing=["S.mxf"]))
    assert "nichts überschrieben" in r.error and r.attempts == 1
    ftp = FakeFTP(fail_store=1)
    sleeps = []
    r = T.send(t, sf, password=lambda tt: "", ftp_factory=lambda tls: ftp, sleep=sleeps.append)
    assert r.ok and r.attempts == 2 and sleeps == [2.0]


def test_sftp_braucht_freigabe(tmp_path):
    sf, _ = _files(tmp_path)
    r = T.send(T.Target("s", "Austausch", "sftp", host="x", user="u"), sf)
    assert "paramiko" in r.error and r.attempts == 0


def test_usb_datentraeger(tmp_path):
    """Review B4: nur USB-Wechselmedien sind Sticks; externe SSDs/Thunderbolt sind Platten, DMGs gar nichts."""
    vols_dir = {n: tmp_path / n for n in ("STICK", "SSD", "VLC")}
    for d in vols_dir.values():
        d.mkdir()
    listing = plistlib.dumps({"AllDisksAndPartitions": [
        {"DeviceIdentifier": "disk2", "Partitions": [{"MountPoint": str(vols_dir["STICK"]), "VolumeName": "STICK"}]},
        {"DeviceIdentifier": "disk3", "Partitions": [{"MountPoint": str(vols_dir["SSD"]), "VolumeName": "SSD"}]},
        {"DeviceIdentifier": "disk4", "Partitions": [{"MountPoint": str(vols_dir["VLC"]), "VolumeName": "VLC"}]},
        {"DeviceIdentifier": "disk5", "Partitions": [{"MountPoint": "/gibt/es/nicht"}]}]})
    details = {"disk2": {"BusProtocol": "USB", "RemovableMedia": True},
               "disk3": {"BusProtocol": "Thunderbolt", "RemovableMedia": False},
               "disk4": {"BusProtocol": "Disk Image", "RemovableMedia": True}}

    def run(cmd):
        return listing if cmd[1] == "list" else plistlib.dumps(details.get(cmd[-1], {}))
    vols = T.removable_volumes(run=run, fs=lambda p: "exfat")
    assert [(v["name"], v["kind"], v["bus"]) for v in vols] == [("STICK", "stick", "USB"), ("SSD", "platte", "Thunderbolt")]
    assert vols[0] == {"mount": str(vols_dir["STICK"]), "name": "STICK", "writable": True, "fs": "exfat",
                       "kind": "stick", "bus": "USB"}
    assert T.removable_volumes(run=lambda cmd: b"") == []
    t = T.usb_target(vols[0])
    assert t.protocol == "usb" and t.name == "USB STICK" and t.path.endswith("STICK/Ottilie Havarie")
    assert os.path.isdir(t.path) and T.usb_target(vols[1]).name == "Platte SSD"
    from tonmischung.app.havarie_app import _with_auto
    big = lambda p: 10 ** 12                                             # noqa: E731
    assert _with_auto(vols[0], free=big)["auto"] and not _with_auto(vols[1], free=big)["auto"]
    sf, sha = _files(tmp_path)
    assert T.send(t, sf, sha).ok and T.free_space_ok(t.path, 10)


MOUNT = """/dev/disk1s1 on / (apfs, sealed, local, read-only, journaled)
/dev/disk3s1 on /Volumes/TMFAT (msdos, local, nodev, nosuid, noowners, noatime, fskit, mounted by admin)
/dev/disk4s1 on /Volumes/Mein Stick (exfat, local, nodev, nosuid, noowners, noatime, fskit, mounted by admin)
"""


def test_dateisystem_aus_mount():
    assert T.filesystem_from_mount(MOUNT, "/Volumes/TMFAT/Ottilie Havarie") == "msdos"
    assert T.filesystem_from_mount(MOUNT, "/Volumes/Mein Stick/Ottilie Havarie") == "exfat"
    assert T.filesystem_from_mount(MOUNT, "/Users/mischer/Desktop") == "apfs"
    assert T.filesystem_from_mount(MOUNT, "/Volumes/TMFATX") == "apfs"                 # kein Präfix-Treffer
    assert T.filesystem_from_mount("", "/x") == ""


def test_platz_und_fat32_grenze(tmp_path):
    big = tmp_path / "gross.mxf"
    with open(big, "wb") as f:
        f.truncate(T.FAT32_MAX + 1)                                                    # dünn belegt, kostet nichts
    t = T.Target("usb:S", "USB S", "usb", path=str(tmp_path))
    with pytest.raises(T.TransferError, match="FAT32 – Dateien über 4 GB"):
        T.check_space(t, [str(big)], "msdos", free=10 ** 12)
    T.check_space(t, [str(big)], "exfat", free=10 ** 12)
    with pytest.raises(T.TransferError, match="zu wenig Platz – 1.0 GB frei, 4.3 GB nötig"):
        T.check_space(t, [str(big)], "exfat", free=10 ** 9)


def test_ohne_hardlinks_wird_umbenannt(tmp_path, monkeypatch):
    """FAT32/exFAT: os.link → ENOTSUP (live 03.10.2026); dann umbenennen, nie überschreiben."""
    import errno

    def no_link(a, b):
        raise OSError(errno.ENOTSUP, "Operation not supported")
    monkeypatch.setattr(T.os, "link", no_link)
    sf, sha = _files(tmp_path)
    ziel = tmp_path / "Stick"
    ziel.mkdir()
    t = T.Target("usb:S", "USB S", "usb", path=str(ziel), retries=0)
    r = T.send(t, sf, sha)
    assert r.ok and sorted(os.listdir(ziel)) == sorted(r.sent) and not any(n.endswith(T.TEMP_SUFFIX) for n in os.listdir(ziel))
    assert (ziel / os.path.basename(sf)).read_bytes() == open(sf, "rb").read()
    r2 = T.send(t, sf, sha)
    assert "nichts überschrieben" in r2.error


def test_schreibtisch_als_ziel(tmp_path):
    t = T.desktop_target(home=str(tmp_path))
    assert (t.name, t.protocol) == ("Schreibtisch", "ordner") and t.path == str(tmp_path / "Desktop" / "Ottilie Havarie")
    assert os.path.isdir(t.path)


def test_eigene_appledouble_dateien_werden_entfernt(tmp_path, monkeypatch):
    """Jens 03.10.2026: „._Name“ (macOS auf FAT/exFAT) nach dem Kopieren löschen – nur eigene, nie fremde."""
    ad = T.APPLEDOUBLE_MAGIC + b"\0" * 4092
    f = tmp_path / "a.mxf"
    f.write_bytes(b"x")
    (tmp_path / "._a.mxf").write_bytes(ad)
    assert T.drop_own_appledouble(str(f), existed_before=True) is False and (tmp_path / "._a.mxf").exists()
    assert T.drop_own_appledouble(str(f), existed_before=False) is True and not (tmp_path / "._a.mxf").exists()
    (tmp_path / "._a.mxf").write_bytes(b"kein AppleDouble")
    assert T.drop_own_appledouble(str(f), existed_before=False) is False and (tmp_path / "._a.mxf").exists()

    # Beim Transfer: macOS legt die Begleitdatei während des Schreibens an (hier nachgestellt)
    write = T._write_new

    def write_with_ad(src, dst, tmp, atomic, on_bytes):
        write(src, dst, tmp, atomic, on_bytes)
        open(T.appledouble(dst), "wb").write(ad)
    monkeypatch.setattr(T, "_write_new", write_with_ad)
    sf, sha = _files(tmp_path)
    ziel = tmp_path / "Stick"
    ziel.mkdir()
    (ziel / "._fremd.mxf").write_bytes(ad)                                              # fremde bleibt
    r = T.send(T.Target("usb:S", "USB S", "usb", path=str(ziel), retries=0), sf, sha)
    assert r.ok and sorted(os.listdir(ziel)) == sorted(r.sent + ["._fremd.mxf"])


def test_unterbrochene_lieferung_wird_fortgesetzt(tmp_path):
    """Review B2: Sendefile kam an, die Prüfsumme nicht – der nächste Versuch ergänzt den Rest."""
    sf, sha = _files(tmp_path)
    dest = tmp_path / "playout"
    dest.mkdir()
    (dest / "S.mxf").write_bytes(open(sf, "rb").read())                 # vom abgebrochenen Versuch, gleicher Inhalt
    target = T.Target("p", "Playout", "ordner", path=str(dest), done_marker=".done")
    r = T.send(target, sf, sha)
    assert r.ok and r.sent == ["S.mxf", "S.mxf.sha256", "S.mxf.done"]
    assert (dest / "S.mxf.sha256").exists() and (dest / "S.mxf.done").exists()
    r = T.send(target, sf, sha)                                          # alles schon da
    assert not r.ok and "schon vollständig" in r.error and r.attempts == 1


def test_falsche_groesse_wird_nie_veroeffentlicht(tmp_path, monkeypatch):
    """Review B3: die Größe wird an der Hilfsdatei geprüft – vorher stand die falsche Datei schon am Ziel."""
    sf, sha = _files(tmp_path)
    dest = tmp_path / "playout"
    dest.mkdir()
    monkeypatch.setattr(T, "full_sync", lambda fd: os.ftruncate(fd, 10))
    r = T.send(T.Target("p", "Playout", "ordner", path=str(dest), retries=0), sf, sha)
    assert not r.ok and "Größe am Ziel stimmt nicht" in r.error
    assert os.listdir(dest) == []                                        # weder S.mxf noch Hilfsdatei


def test_usb_wird_wirklich_festgeschrieben(tmp_path, monkeypatch):
    """Review B5: F_FULLFSYNC statt nur fsync; kann das Dateisystem es nicht, bleibt fsync."""
    import fcntl
    calls = []

    def fake_fcntl(fd, cmd, *a):
        calls.append(cmd)
        if len(calls) > 1:
            raise OSError(45, "Operation not supported")
    monkeypatch.setattr(fcntl, "fcntl", fake_fcntl)
    synced = []
    monkeypatch.setattr(os, "fsync", synced.append)
    fd = os.open(tmp_path / "x", os.O_CREAT | os.O_WRONLY)
    try:
        T.full_sync(fd)
        T.full_sync(fd)
    finally:
        os.close(fd)
    assert calls == [fcntl.F_FULLFSYNC] * 2 and synced == [fd]


def test_abgebrochene_eigene_teilkopie_wird_ersetzt(tmp_path):
    """Jens 09.10.2026 (B1): die eigene „….uploading“ eines abgebrochenen Versuchs wird gelöscht, der neue klappt."""
    import time
    sf, sha = _files(tmp_path)
    dest = tmp_path / "playout"
    dest.mkdir()
    part = dest / "S.mxf.uploading"
    part.write_bytes(b"halb")
    old = time.time() - T.STALE_UPLOAD_S - 5
    os.utime(part, (old, old))
    r = T.send(T.Target("p", "Playout", "ordner", path=str(dest)), sf, sha)
    assert r.ok and (dest / "S.mxf").read_bytes() == b"m" * 5000 and not part.exists()


def test_frische_teilkopie_bleibt(tmp_path):
    """Gerade geschrieben (vielleicht von einem anderen Platz): nicht anfassen."""
    sf, sha = _files(tmp_path)
    dest = tmp_path / "playout"
    dest.mkdir()
    (dest / "S.mxf.uploading").write_bytes(b"halb")
    r = T.send(T.Target("p", "Playout", "ordner", path=str(dest), retries=0), sf, sha)
    assert not r.ok and "gerade geschrieben" in r.error and (dest / "S.mxf.uploading").read_bytes() == b"halb"


def test_ftp_teilkopie_nach_alter():
    import time
    ftp = FakeFTP(existing=["S.mxf.uploading"])
    ten_minutes_ago = time.strftime("%Y%m%d%H%M%S", time.gmtime(time.time() - 600))
    ftp.voidcmd = lambda c: "213 " + ten_minutes_ago if c.startswith("MDTM") else None
    ftp.delete = lambda name: ftp.files.pop(name)
    T._ftp_drop_stale_upload(ftp, "S.mxf.uploading")
    assert "S.mxf.uploading" not in ftp.files
    ftp = FakeFTP(existing=["S.mxf.uploading"])
    with pytest.raises(T.TransferError, match="Alter nicht feststellbar"):
        T._ftp_drop_stale_upload(ftp, "S.mxf.uploading")                    # Server ohne MDTM
