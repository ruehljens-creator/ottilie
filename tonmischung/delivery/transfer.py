"""Sendefile an seine Ziele bringen – nur im Havariemodus (Plan M5, Schritt 4; Jens 03.10.2026: im Normalbetrieb
gehen nur die WAVs an Helmut).

Herkunft: punchbuddy-helmut `punchbuddy/transfer.py` und `usb.py` (Stand 17.09.2026). Angepasst an Ottilie:
- Ziele: Ordner (eingehängte Freigabe), USB-Datenträger, FTP, FTPS. Im Fenster angeboten werden bisher Schreibtisch,
  Havarie-Ordner, USB und gewählte Ordner; FTP/FTPS und SFTP sind **vorbereitet, nicht angebunden** (fest
  eingerichtete Ziele in der Einstellung „Transfer-Ziele“, noch ausgeblendet). SFTP bräuchte das Paket `paramiko`.
- **Nie überschreiben:** Gibt es den Namen am Ziel schon, bricht der Transfer mit Meldung ab – der Arbeitsstand
  löschte die alte Datei vorher. Hochgeladen wird unter einem Hilfsnamen und erst danach umbenannt (ein
  Watchfolder sieht nie eine halbe Datei); bei FTP wird die Größe geprüft.
- **Zugangsdaten nur im Schlüsselbund** (Eintrag `schluesselbund_eintrag`, Konto = Benutzer), nie in den
  Einstellungen.
- Wiederholungen mit wachsender Pause; `.sha256` wird mitgesendet, wenn das Ziel es will.
"""
from __future__ import annotations

import fcntl
import logging
import os
import plistlib
import shutil
import ssl
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tonmischung.core.fileio import publish_new, same_content

log = logging.getLogger(__name__)

FOLDER, USB, FTP, FTPS, SFTP = "ordner", "usb", "ftp", "ftps", "sftp"
PROTOCOLS = (FOLDER, USB, FTP, FTPS, SFTP)
TEMP_SUFFIX = ".uploading"
STALE_UPLOAD_S = 120.0                         # so lange unverändert → eigene Teilkopie eines abgebrochenen Versuchs
FAT32_MAX = 4 * 1024 ** 3 - 1                  # größte Datei auf FAT32
HAVARIE_FOLDER = "Ottilie Havarie"         # Unterordner auf Schreibtisch und USB-Stick
APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"
BLOCK = 1 << 16


class TransferError(Exception):
    """Transfer an ein Ziel gescheitert – die Meldung nennt Ziel und Grund."""


@dataclass(frozen=True)
class Target:
    key: str
    name: str
    protocol: str
    host: str = ""
    port: int = 0
    user: str = ""
    path: str = ""                       # Zielordner (Server-Pfad bzw. Ordner auf dem Mac/USB-Datenträger)
    keychain: str = ""                   # Schlüsselbund-Eintrag für das Kennwort
    enabled: bool = True
    atomic: bool = True
    send_sha256: bool = True
    retries: int = 3
    done_marker: str = ""                # z. B. „.done“ – leere Datei nach dem Sendefile
    timeout_s: int = 30
    passive: bool = True
    tls_verify: bool = True


def target_from_dict(d: Mapping[str, Any]) -> Target:
    """Eintrag der Transfer-Ziele in den Einstellungen (Schema `$defs.transfer_ziel`)."""
    return Target(key=str(d.get("key") or d.get("name") or ""), name=str(d.get("name") or d.get("key") or ""),
                  protocol=str(d.get("protokoll") or "").lower(), host=str(d.get("host") or ""),
                  port=int(d.get("port") or 0), user=str(d.get("benutzer") or ""), path=str(d.get("zielpfad") or ""),
                  keychain=str(d.get("schluesselbund_eintrag") or ""), enabled=bool(d.get("aktiv", True)),
                  atomic=bool(d.get("atomar", True)), send_sha256=bool(d.get("sha256_mitsenden", True)),
                  retries=max(0, int(d.get("wiederholungen", 3) or 0)),
                  done_marker=str(d.get("fertig_kennzeichen") or ""))


def problems(t: Target) -> list[str]:
    out = []
    if t.protocol not in PROTOCOLS:
        out.append(f"Ziel „{t.name}“: unbekanntes Protokoll „{t.protocol}“")
    if t.protocol in (FTP, FTPS, SFTP) and not t.host:
        out.append(f"Ziel „{t.name}“: kein Server angegeben")
    if t.protocol in (FOLDER, USB) and not t.path:
        out.append(f"Ziel „{t.name}“: kein Ordner gewählt")
    if t.protocol == SFTP and not t.user:
        out.append(f"Ziel „{t.name}“: SFTP braucht einen Benutzer")
    return out


@dataclass
class TransferResult:
    target: Target
    sent: list[str] = field(default_factory=list)       # Namen am Ziel
    attempts: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


# ── Ordner und USB ──────────────────────────────────────────────────────────
def appledouble(path: str) -> str:
    return os.path.join(os.path.dirname(path), "._" + os.path.basename(path))


def drop_own_appledouble(path: str, existed_before: bool) -> bool:
    """Die eben entstandene Begleitdatei „._Name“ entfernen (Jens 03.10.2026).

    macOS legt auf FAT32/exFAT (und manchen Freigaben) zu jeder neuen Datei und jedem neuen Ordner eine
    AppleDouble-Datei an (Attribut `com.apple.provenance`, nicht abschaltbar, live 03.10.2026); ein
    Playout-Watchfolder könnte sie für Material halten. Entfernt wird nur, was es vorher nicht gab und was die
    AppleDouble-Kennung trägt – nie eine fremde Datei."""
    ad = appledouble(path)
    if existed_before or not os.path.isfile(ad):
        return False
    try:
        if os.path.getsize(ad) > 1 << 20:
            return False
        with open(ad, "rb") as f:
            if f.read(4) != APPLEDOUBLE_MAGIC:
                return False
        os.unlink(ad)
        return True
    except OSError:
        return False


def make_target_dir(path: str) -> None:
    """Zielordner anlegen; seine eben entstandene „._“-Begleitdatei gleich wieder entfernen."""
    existed = os.path.isdir(path)
    ad_before = os.path.exists(appledouble(path))
    os.makedirs(path, exist_ok=True)
    if not existed:
        drop_own_appledouble(path, ad_before)


def _copy_new(src: str, dst: str, atomic: bool, on_bytes: Callable[[int], None]) -> None:
    if os.path.exists(dst):
        raise TransferError(f"{os.path.basename(dst)} gibt es am Ziel schon – nichts überschrieben")
    tmp = dst + TEMP_SUFFIX if atomic else dst
    if atomic and os.path.exists(tmp):
        drop_stale_upload(tmp)
    ad_before = {p: os.path.exists(appledouble(p)) for p in (dst, tmp)}
    try:
        _write_new(src, dst, tmp, atomic, on_bytes)
    finally:
        for p, before in ad_before.items():
            drop_own_appledouble(p, before)


def drop_stale_upload(tmp: str, now: Callable[[], float] = time.time) -> None:
    """Teilkopie „….uploading“ eines abgebrochenen Versuchs entfernen, damit ein neuer klappt (Jens 09.10.2026,
    Review B1). Nur die eigene Hilfsdatei (Name mit `.uploading`) und nur, wenn sie seit `STALE_UPLOAD_S` unverändert
    ist – sonst überträgt sie vielleicht gerade ein anderer Platz."""
    age = now() - os.path.getmtime(tmp)
    if age < STALE_UPLOAD_S:
        raise TransferError(f"{os.path.basename(tmp)} wird am Ziel gerade geschrieben (anderer Platz?) – "
                            "bitte später erneut versuchen")
    os.unlink(tmp)
    drop_own_appledouble(tmp, False)                         # deren „._“-Begleitdatei, falls es eine gibt
    log.info("Abgebrochene eigene Teilkopie entfernt: %s (seit %.0f s unverändert)", tmp, age)


def full_sync(fd: int) -> None:
    """Wirklich auf den Datenträger: `fsync` allein lässt Daten im Cache des Laufwerks – ein USB-Stick könnte zu früh
    abgezogen werden (Review B5). `F_FULLFSYNC` können nicht alle Dateisysteme (z. B. SMB); dann `fsync`."""
    try:
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    except (OSError, AttributeError):
        os.fsync(fd)


def _sync_dir(path: str) -> None:
    """Ordnereintrag (neuer Name) ebenfalls festschreiben; scheitert das, ist die Datei trotzdem geschrieben."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        full_sync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_new(src: str, dst: str, tmp: str, atomic: bool, on_bytes: Callable[[int], None]) -> None:
    with open(src, "rb") as fi, open(tmp, "xb") as fo:          # „x“: nie eine vorhandene Datei öffnen
        while True:
            data = fi.read(8 << 20)
            if not data:
                break
            fo.write(data)
            on_bytes(len(data))
        fo.flush()
        full_sync(fo.fileno())
    try:
        # vor dem Veröffentlichen prüfen – sonst stünde eine falsche Datei unter dem endgültigen Namen (Review B3)
        if os.path.getsize(tmp) != os.path.getsize(src):
            raise TransferError(f"{os.path.basename(dst)}: Größe am Ziel stimmt nicht")
        if atomic:
            _publish(tmp, dst)
    finally:
        if atomic and os.path.exists(tmp):
            os.unlink(tmp)                                       # eigener Hilfsname
    _sync_dir(os.path.dirname(dst))


def _publish(tmp: str, dst: str) -> None:
    """Hilfsdatei unter dem endgültigen Namen sichtbar machen, ohne je zu überschreiben. FAT32 und exFAT (übliche
    USB-Sticks) kennen keine Hardlinks (ENOTSUP, live 03.10.2026) – `publish_new` prüft dort unmittelbar vorher und
    benennt um (ein Stick hat nur einen Schreiber: diese App)."""
    try:
        publish_new(tmp, dst)
    except FileExistsError:
        raise TransferError(f"{os.path.basename(dst)} ist am Ziel inzwischen entstanden – "
                            "nichts überschrieben") from None


def filesystem_from_mount(mount_text: str, path: str) -> str:
    """Dateisystem (msdos, exfat, apfs …) des Ordners `path` aus der Ausgabe von `mount` (reine Logik)."""
    best, fs = "", ""
    target = os.path.abspath(path).rstrip("/") + "/"
    for line in mount_text.splitlines():
        if " on " not in line or " (" not in line:
            continue
        mp = line.split(" on ", 1)[1].rsplit(" (", 1)[0]
        kind = line.rsplit(" (", 1)[1].split(",", 1)[0].strip(" )")
        prefix = mp.rstrip("/") + "/"
        if target.startswith(prefix) and len(prefix) > len(best):
            best, fs = prefix, kind
    return fs


def filesystem(path: str) -> str:
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return filesystem_from_mount(out, path)


def check_space(t: Target, files: Sequence[str], fs: str, free: int) -> None:
    """Vor dem Kopieren: passt alles aufs Ziel? FAT32 nimmt keine Datei ab 4 GiB."""
    sizes = [os.path.getsize(f) for f in files]
    if fs == "msdos" and any(n > FAT32_MAX for n in sizes):
        raise TransferError(f"Ziel „{t.name}“ ist FAT32 – Dateien über 4 GB passen nicht (Sendefile "
                            f"{max(sizes) / 1e9:.1f} GB). Stick als exFAT formatieren oder anderes Ziel wählen")
    need = sum(sizes)
    if free < need * 1.02:
        raise TransferError(f"Ziel „{t.name}“: zu wenig Platz – {free / 1e9:.1f} GB frei, {need / 1e9:.1f} GB nötig")


def _send_folder(t: Target, files: Sequence[str], on_progress: Callable[[float], None]) -> list[str]:
    if not os.path.isdir(t.path):
        raise TransferError(f"Ziel „{t.name}“: Ordner {t.path} nicht erreichbar (eingehängt?)")
    marker = ""
    if t.done_marker:
        marker = os.path.join(t.path, os.path.basename(files[0]) + t.done_marker if t.done_marker.startswith(".")
                              else t.done_marker)
    # Liegt eine Datei schon am Ziel, zählt sie nur bei gleichem Inhalt als geliefert – ein unterbrochener Versuch
    # (Sendefile da, Prüfsumme fehlt) wird so fortgesetzt statt als „gibt es schon“ gemeldet (Review B2).
    pending = []
    for f in files:
        dst = os.path.join(t.path, os.path.basename(f))
        if not os.path.exists(dst):
            pending.append(f)
        elif not same_content(f, dst):
            raise TransferError(f"{os.path.basename(dst)} gibt es am Ziel schon – nichts überschrieben")
    if not pending and (not marker or os.path.exists(marker)):
        raise TransferError(f"{os.path.basename(files[0])} ist am Ziel schon vollständig (gleicher Inhalt) – "
                            "nichts überschrieben")
    check_space(t, pending, filesystem(t.path), shutil.disk_usage(t.path).free)
    total = sum(os.path.getsize(f) for f in pending) or 1
    done = [0]

    def bytes_(n: int) -> None:
        done[0] += n
        on_progress(min(1.0, done[0] / total))
    for f in pending:
        _copy_new(f, os.path.join(t.path, os.path.basename(f)), t.atomic, bytes_)
    sent = [os.path.basename(f) for f in files]
    if marker:
        if not os.path.exists(marker):
            ad_before = os.path.exists(appledouble(marker))
            with open(marker, "xb"):
                pass
            drop_own_appledouble(marker, ad_before)
            _sync_dir(t.path)
        sent.append(os.path.basename(marker))
    on_progress(1.0)
    return sent


# ── FTP und FTPS (Standardbibliothek) ─────────────────────────────────────────
def _ftp_enter(ftp: Any, remote_dir: str) -> None:
    if remote_dir.startswith("/"):
        ftp.cwd("/")
    for seg in [s for s in remote_dir.split("/") if s]:
        try:
            ftp.cwd(seg)
        except Exception:  # noqa: BLE001 – Ordner fehlt: anlegen
            ftp.mkd(seg)
            ftp.cwd(seg)


def _ftp_exists(ftp: Any, name: str) -> bool:
    try:
        return name in [os.path.basename(n) for n in ftp.nlst()]
    except Exception:  # noqa: BLE001 – leerer Ordner liefert bei manchen Servern einen Fehler
        return False


def _ftp_drop_stale_upload(ftp: Any, tmp: str, now: Callable[[], float] = time.time) -> None:
    """Wie `drop_stale_upload`, über FTP: Alter aus MDTM; kennt der Server MDTM nicht, bleibt die Datei liegen."""
    import calendar
    try:
        stamp = ftp.voidcmd(f"MDTM {tmp}").split()[-1][:14]
        age = now() - calendar.timegm(time.strptime(stamp, "%Y%m%d%H%M%S"))
    except Exception as e:  # noqa: BLE001 – MDTM fehlt oder unlesbar
        raise TransferError(f"{tmp} liegt am Ziel (abgebrochener Versuch?) – Alter nicht feststellbar, "
                            "bitte prüfen") from e
    if age < STALE_UPLOAD_S:
        raise TransferError(f"{tmp} wird am Ziel gerade geschrieben (anderer Platz?) – bitte später erneut versuchen")
    ftp.delete(tmp)
    log.info("Abgebrochene eigene Teilkopie per FTP entfernt: %s (seit %.0f s unverändert)", tmp, age)


def _send_ftp(t: Target, files: Sequence[str], on_progress: Callable[[float], None], password: str,
              ftp_factory: Callable[[bool], Any] | None = None) -> list[str]:
    from ftplib import FTP as _FTP, FTP_TLS
    tls = t.protocol == FTPS
    if ftp_factory is not None:
        ftp = ftp_factory(tls)
    elif tls:
        ctx = ssl.create_default_context()
        if not t.tls_verify:
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        ftp = FTP_TLS(context=ctx)
    else:
        ftp = _FTP()
    total = sum(os.path.getsize(f) for f in files) or 1
    done = [0]
    sent = []
    try:
        ftp.connect(t.host, t.port or 21, timeout=t.timeout_s)
        ftp.login(t.user or "anonymous", password)
        if tls:
            ftp.prot_p()
        ftp.set_pasv(t.passive)
        _ftp_enter(ftp, t.path)
        for f in files:
            final = os.path.basename(f)
            tmp = final + TEMP_SUFFIX if t.atomic else final
            if _ftp_exists(ftp, final):
                raise TransferError(f"{final} gibt es am Ziel schon – nichts überschrieben")
            if t.atomic and _ftp_exists(ftp, tmp):
                _ftp_drop_stale_upload(ftp, tmp)

            def cb(block: bytes) -> None:
                done[0] += len(block)
                on_progress(min(1.0, done[0] / total))
            with open(f, "rb") as fh:
                ftp.storbinary(f"STOR {tmp}", fh, blocksize=BLOCK, callback=cb)
            try:
                ftp.voidcmd("TYPE I")
                size = ftp.size(tmp)
            except Exception:  # noqa: BLE001 – SIZE kennt nicht jeder Server
                size = None
            if size is not None and size != os.path.getsize(f):
                raise TransferError(f"{final}: Größe am Ziel {size} statt {os.path.getsize(f)} Bytes")
            if tmp != final:
                ftp.rename(tmp, final)
            sent.append(final)
        if t.done_marker:
            import io
            marker = os.path.basename(files[0]) + t.done_marker if t.done_marker.startswith(".") else t.done_marker
            ftp.storbinary(f"STOR {marker}", io.BytesIO(b""))
            sent.append(marker)
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            ftp.close()
    return sent


# ── Senden mit Wiederholung ───────────────────────────────────────────────────
def send(t: Target, sendefile: str, sha256_file: str = "", *, on_progress: Callable[[float], None] = lambda f: None,
         password: Callable[[Target], str] | None = None, sleep: Callable[[float], None] = time.sleep,
         ftp_factory: Callable[[bool], Any] | None = None) -> TransferResult:
    """Sendefile (und `.sha256`) an ein Ziel. Fehler stehen im Ergebnis, nicht als Ausnahme."""
    r = TransferResult(t)
    issues = problems(t)
    if issues:
        r.error = "; ".join(issues)
        return r
    if t.protocol == SFTP:
        r.error = (f"Ziel „{t.name}“: SFTP wird noch nicht unterstützt (das Paket „paramiko“ fehlt) – "
                   "Ziel als Ordner (eingehängte Freigabe) oder FTPS einrichten")
        return r
    files = [sendefile] + ([sha256_file] if t.send_sha256 and sha256_file else [])
    pw = ""
    if t.protocol in (FTP, FTPS):
        from tonmischung.config.keychain import KeychainError, read_secret
        try:
            pw = (password or (lambda tt: read_secret(tt.keychain, tt.user)))(t)
        except KeychainError as e:
            r.error = f"Ziel „{t.name}“: {e}"
            return r
    for attempt in range(t.retries + 1):
        r.attempts = attempt + 1
        try:
            if t.protocol in (FOLDER, USB):
                r.sent = _send_folder(t, files, on_progress)
            else:
                r.sent = _send_ftp(t, files, on_progress, pw, ftp_factory)
            r.error = ""
            return r
        except TransferError as e:
            r.error = str(e)
            if "nichts überschrieben" in r.error or "bitte prüfen" in r.error:
                return r                                         # Wiederholen hilft nicht
        except (OSError, EOFError, Exception) as e:  # noqa: BLE001 – Netz, Login, Server
            r.error = f"Ziel „{t.name}“: {type(e).__name__}: {e}"
        if attempt < t.retries:
            sleep(min(60.0, 2.0 * 2 ** attempt))
    return r


# ── USB-Datenträger (Turnschuh-Netz in der Havarie) ───────────────────────────
STICK, DRIVE = "stick", "platte"


def removable_volumes(run: Callable[[list[str]], bytes] | None = None,
                      fs: Callable[[str], str] = filesystem) -> list[dict[str, Any]]:
    """Externe Datenträger: [{mount, name, writable, fs, kind, bus}] (diskutil, mount; nur lesen).

    `kind` „stick“ nur bei USB mit Wechselmedium – externe SSDs, Thunderbolt-Platten und RAIDs sind „platte“ und
    werden nicht vorgewählt; eingebundene Disk-Images (DMG) sind keine Datenträger (Review B4: vorher zählte alles
    Externe als „USB-Stick“). Ohne Angaben von diskutil gilt ein Datenträger als „platte“."""
    def _run(cmd: list[str]) -> bytes:
        p = subprocess.run(cmd, capture_output=True, timeout=10)
        return p.stdout if p.returncode == 0 else b""
    run = run or _run

    def info(ident: str) -> dict:
        try:
            return plistlib.loads(run(["diskutil", "info", "-plist", ident]) or b"") if ident else {}
        except Exception:  # noqa: BLE001 – diskutil nicht lesbar: als Platte behandeln
            return {}
    out = []
    try:
        data = plistlib.loads(run(["diskutil", "list", "-plist", "external"]) or b"")
    except Exception:  # noqa: BLE001 – kein externer Datenträger oder diskutil nicht lesbar
        return out
    for disk in data.get("AllDisksAndPartitions", []) or []:
        details = info(str(disk.get("DeviceIdentifier") or ""))
        bus = str(details.get("BusProtocol") or "")
        if bus == "Disk Image":
            continue
        kind = STICK if bus == "USB" and details.get("RemovableMedia") else DRIVE
        parts = (disk.get("Partitions") or []) + (disk.get("APFSVolumes") or [])
        if not parts and disk.get("MountPoint"):
            parts = [disk]
        for part in parts:
            mp = part.get("MountPoint")
            if mp and os.path.isdir(mp):
                out.append({"mount": mp, "name": part.get("VolumeName") or os.path.basename(mp),
                            "writable": os.access(mp, os.W_OK), "fs": fs(mp), "kind": kind, "bus": bus})
    return out


def usb_target(volume: Mapping[str, Any], folder: str = HAVARIE_FOLDER) -> Target:
    """Ziel auf einem externen Datenträger (USB-Stick oder Platte): eigener Unterordner (wird angelegt)."""
    path = os.path.join(str(volume["mount"]), folder)
    make_target_dir(path)
    label = "USB" if volume.get("kind", STICK) == STICK else "Platte"
    return Target(key=f"usb:{volume['name']}", name=f"{label} {volume['name']}", protocol=USB, path=path, retries=0)


def desktop_target(home: str | None = None, folder: str = HAVARIE_FOLDER) -> Target:
    """Schreibtisch dieses Macs als Ziel (Jens 03.10.2026: Havarie-Ziele noch unbekannt – vorerst Schreibtisch
    oder USB-Stick). Eigener Unterordner, wird angelegt."""
    path = os.path.join(home or os.path.expanduser("~"), "Desktop", folder)
    make_target_dir(path)
    return Target(key="schreibtisch", name="Schreibtisch", protocol=FOLDER, path=path, retries=0)


def free_space_ok(path: str, need_bytes: int) -> bool:
    try:
        return shutil.disk_usage(path).free > need_bytes * 1.05
    except OSError:
        return False
