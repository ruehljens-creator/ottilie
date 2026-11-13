"""Übergabeordner selbst einbinden (Nachtrag 11.11.2026) – ohne echtes Netz."""
import pytest

from tonmischung.dropfolder import netmount as M

MOUNT = """/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
//guest:@100.105.81.98/samba on /Users/mischer/smbtest (smbfs, nodev, nosuid, noowners, mounted by mischer)
//mischer@NAS.local/Uebergabe%20TV on /Volumes/Uebergabe TV-1 (smbfs, nodev, nosuid, mounted by mischer)
"""


def test_adresse_nur_smb_ohne_kennwort():
    s = M.parse("smb://NAS.local/Uebergabe%20TV/Filme/2026")
    assert (s.host, s.share, s.sub) == ("nas.local", "Uebergabe TV", "Filme/2026")
    for bad in ("afp://nas/x", "smb://u:pw@nas/x", "smb://nas", "smb://nas/x/../y", "https://nas/x"):
        with pytest.raises(M.MountError):
            M.parse(bad)


def test_schon_verbunden_wird_genutzt():
    ok = {"access": lambda p: True, "me": "mischer"}
    assert M.find_mounted(M.parse("smb://100.105.81.98/samba"), MOUNT, **ok) == "/Users/mischer/smbtest"
    assert M.find_mounted(M.parse("smb://nas.local/uebergabe tv"), MOUNT, **ok) == "/Volumes/Uebergabe TV-1"
    assert M.find_mounted(M.parse("smb://nas.local/andere"), MOUNT, **ok) is None
    assert M.find_mounted(M.parse("smb://nas.local/uebergabe tv"), MOUNT, me="mischer", access=lambda p: False) is None


def test_einbinden_mit_abstand_und_ohne_auftrag():
    t = [0.0]
    calls = []
    mounted = {"out": ""}

    def mount(share, auth, creds):
        calls.append((share.share, auth, creds))
        if len(calls) == 1:
            raise M.MountError("Server nicht erreichbar (Code 64)")
        mounted["out"] = "//guest:@srv/samba on /Volumes/samba-1 (smbfs, nodev)"
        return "/Volumes/samba-1"
    m = M.NetMounter(mount=mount, mounts=lambda: mounted["out"], clock=lambda: t[0], access=lambda p: True)
    with pytest.raises(M.MountError, match="nicht erreichbar"):
        m.ensure("smb://srv/samba/ottilie", "gast", lambda: None)
    with pytest.raises(M.MountError):                          # Abstand: nicht hämmern
        m.ensure("smb://srv/samba/ottilie", "gast", lambda: None)
    assert len(calls) == 1
    m.busy = lambda: True
    with pytest.raises(M.MountError, match="Auftrag"):
        m.ensure("smb://srv/samba/ottilie", "gast", lambda: None, now=True)
    m.busy = lambda: False
    assert m.ensure("smb://srv/samba/ottilie", "gast", lambda: None, now=True) == "/Volumes/samba-1/ottilie"
    t[0] += 10
    assert m.root("smb://srv/samba/ottilie") == "/Volumes/samba-1/ottilie"


def test_schluesselbund_nie_mit_adresse_von_der_zentrale():
    m = M.NetMounter(mount=lambda *a: "/Volumes/x", mounts=lambda: "")
    asked = []
    with pytest.raises(M.MountError, match="Schlüsselbund"):
        m.ensure("smb://boese/x", "schluesselbund", lambda: asked.append(1) or ("u", "p"),
                 address_from_zentrale=True)
    assert asked == []                                         # Kennwort gar nicht erst gelesen


def test_ordnerclient_meldet_den_grund(tmp_path):
    from tonmischung.dropfolder.client import FolderClient, FolderConfig
    from tonmischung.helmut.client import HelmutUnavailableError

    def ensure(now=False):
        raise M.MountError("Anmeldung abgelehnt (Code 13)")
    c = FolderClient(lambda: FolderConfig(root="", netzadresse="smb://x/y", ensure=ensure), lambda: ("J", "T"))
    with pytest.raises(HelmutUnavailableError, match="nicht verbunden: Anmeldung abgelehnt"):
        c.reachable()


def test_einbindung_eines_anderen_benutzers_zaehlt_nicht():
    out = "//guest:@srv/samba on /Volumes/samba (smbfs, nodev, nosuid, mounted by anna)\n"
    assert M.find_mounted(M.parse("smb://srv/samba"), out, me="mischer", access=lambda p: True) is None
    assert M.find_mounted(M.parse("smb://srv/samba"), out, me="anna", access=lambda p: True) == "/Volumes/samba"


def test_haengendes_netfs_blockiert_keinen_befehl():
    import threading
    import time
    release = threading.Event()

    def slow(share, auth, creds):
        release.wait(5)
        raise M.MountError("Server nicht erreichbar")
    m = M.NetMounter(mount=slow, mounts=lambda: "")
    m.WAIT_S = 0.2
    t0 = time.monotonic()
    with pytest.raises(M.MountError, match="wird verbunden"):
        m.ensure("smb://srv/samba", "gast", lambda: None, now=True)
    with pytest.raises(M.MountError, match="wird verbunden"):
        m.ensure("smb://srv/samba", "gast", lambda: None, now=True)      # kein zweiter Versuch parallel
    assert time.monotonic() - t0 < 2
    release.set()


def test_namensaufloesung_zwischengespeichert():
    calls = []

    def resolve(host):
        calls.append(host)
        return {"10.0.0.1"}
    m = M.NetMounter(mount=lambda *a: "", mounts=lambda: "//g:@10.0.0.1/samba on /Volumes/samba (smbfs, nodev)",
                     resolve=resolve, access=lambda p: True)
    for _ in range(10):
        assert m.root("smb://nas.example/samba/x") == "/Volumes/samba/x"
    assert len(calls) <= 2


def test_adresse_mit_verstecktem_schraegstrich_und_erlaubte_server():
    for bad in ("smb://srv/share/sub%2F..%2F..%2Fetc", "smb://srv/share/%2FUsers%2Fmischer", "smb://srv/a%2Fb/x"):
        with pytest.raises(M.MountError):
            M.parse(bad)
    m = M.NetMounter(mount=lambda *a: "/Volumes/x", mounts=lambda: "")
    with pytest.raises(M.MountError, match="nicht als Übergabe-Server erlaubt"):
        m.ensure("smb://boese/x", "gast", lambda: None, allowed=("nas.local",))


def test_nicht_erlaubte_einbindung_wird_nicht_genutzt(tmp_path, monkeypatch):
    from tonmischung import factory
    from tonmischung.config.settings import Settings
    s = Settings(local_path=tmp_path / "e.json", managed=None)
    s.set_local("system.uebergabeordner.netzadresse", "smb://fremd/privat")
    s.set_local("system.uebergabeordner.netz_erlaubte_server", ["nas-schnitt"])
    monkeypatch.setattr(M.MOUNTER, "root", lambda net: "/Volumes/privat")       # schon verbunden
    assert factory.folder_config(s).root == ""


def test_jede_freigabe_hat_ihren_eigenen_stand():
    """Prüfrunde 13.11.2026: ein ausgefallener Server hielt die anderen Übergabeordner bis zu 5 Minuten auf."""
    t = [100.0]
    mounted = {"out": ""}

    def mount(share, auth, creds):
        if share.host == "nord":
            raise M.MountError("Server nicht erreichbar (Code 64)")
        mounted["out"] = "//guest:@sued/samba on /Volumes/samba-1 (smbfs, nodev)"
        return "/Volumes/samba-1"
    m = M.NetMounter(mount=mount, mounts=lambda: mounted["out"], clock=lambda: t[0], access=lambda p: True)
    with pytest.raises(M.MountError, match="nicht erreichbar"):
        m.ensure("smb://nord/samba/a", "gast", lambda: None)
    assert m.ensure("smb://sued/samba/b", "gast", lambda: None) == "/Volumes/samba-1/b"   # nicht aufgehalten
    assert m.error_of("smb://sued/samba/b") == "" and "nicht erreichbar" in m.error_of("smb://nord/samba/a")
