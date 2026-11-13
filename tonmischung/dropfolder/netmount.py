"""Übergabeordner selbst einbinden (Nachtrag 11.11.2026, Jens: „Ja, nachtragen“).

Mit einer Netzadresse `smb://Server/Freigabe/Unterordner` bindet Ottilie die Freigabe für den angemeldeten Benutzer
ein – wie der Finder, über NetFS nach `/Volumes/…`, ohne Administrator und ohne Dialog. Der tatsächliche Einhängepunkt
(bei Namensgleichheit z. B. `/Volumes/Freigabe-1`) ergibt den Übergabeordner; ist die Freigabe schon verbunden (von
Hand, Finder, Geräteverwaltung), gilt diese Einbindung.

Anmeldung: Gast | Kerberos (Anmeldung am Firmennetz, kein Kennwort) | Schlüsselbund (Eintrag mit Konto und Kennwort).
Kennwörter nie in Einstellungen, Profil, Zentrale oder Protokoll. Schlüsselbund nur, wenn die Netzadresse am Mac
oder per Profil eingestellt ist – nie mit einer Adresse von der Zentrale (sonst könnte eine gekaperte Zentrale ein
Kennwort an einen fremden Server lenken).

Fehler (nicht erreichbar, Anmeldung abgelehnt): klare Meldung, neuer Versuch mit wachsendem Abstand (30 s … 5 min),
⌘R versucht sofort. Während eines Auftrags (Import/Export) wird nicht eingebunden.

Auswerfen: nie. Ottilie weiß nicht, ob andere Programme oder Benutzer die Freigabe nutzen; der Finder bzw. das
Abmelden wirft aus. So kann ein Beenden von Ottilie nie einen Kopiervorgang anderer abbrechen.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger("tonmischung.uebergabeordner")

GUEST, KERBEROS, KEYCHAIN = "gast", "kerberos", "schluesselbund"
_MOUNT_LINE = re.compile(r"^//(?:(?P<user>[^@]*)@)?(?P<host>[^/]+)/(?P<share>.+?) on (?P<path>.+?) \((?P<fs>[^,)]+)"
                         r"(?P<opts>[^)]*)\)")
BACKOFF = (30.0, 60.0, 120.0, 300.0)
CACHE_S = 5.0


class MountError(Exception):
    """Freigabe nicht verbunden – Grund für die Anzeige („Übergabeordner nicht verbunden: …“)."""


@dataclass(frozen=True)
class Share:
    host: str
    share: str
    sub: str = ""
    user: str = ""

    @property
    def url(self) -> str:
        user = f"{urllib.parse.quote(self.user)}@" if self.user else ""
        return f"smb://{user}{self.host}/{urllib.parse.quote(self.share)}"


def parse(address: str) -> Share:
    """smb://[Benutzer@]Server/Freigabe[/Unterordner] → Share. Nur smb://, kein Kennwort, kein „..“."""
    parts = urllib.parse.urlsplit(str(address or "").strip())
    if parts.scheme.lower() != "smb" or not parts.hostname:
        raise MountError("Netzadresse nur als smb://Server/Freigabe/Ordner")
    if parts.password is not None or parts.query or parts.fragment:
        raise MountError("Netzadresse ohne Kennwort und ohne Zusätze angeben")
    segs = [urllib.parse.unquote(s) for s in parts.path.split("/") if s]
    if not segs:
        raise MountError("In der Netzadresse fehlt die Freigabe (smb://Server/Freigabe)")
    # nach dem Entschlüsseln: kein „..“, kein „/“ oder „\“ (z. B. %2F) und keine Steuerzeichen (Sicherheit)
    if any(s in (".", "..") or "/" in s or "\\" in s or any(ord(c) < 32 for c in s) for s in segs):
        raise MountError("Netzadresse mit „..“ oder Sonderzeichen ist nicht erlaubt")
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return Share(host, segs[0], "/".join(segs[1:]), urllib.parse.unquote(parts.username or ""))


def _addresses(host: str) -> set[str]:
    import socket
    try:
        return {a[4][0] for a in socket.getaddrinfo(host.split(":")[0], None)}
    except OSError:
        return set()


def _mine(opts: str, me: str) -> bool:
    """Eingebunden von diesem Benutzer? (`mount` listet die Einbindungen aller angemeldeten Benutzer – die eines
    anderen sind für uns nicht lesbar; Review 11.11.2026)."""
    m = re.search(r"mounted by ([^,)]+)", opts)
    return m is None or m.group(1).strip() == me


def _readable(path: str) -> bool:
    return os.access(path, os.R_OK | os.X_OK)


def find_mounted(share: Share, mount_output: str, resolve: Callable[[str], set[str]] = _addresses,
                 me: str | None = None, access: Callable[[str], bool] = _readable) -> str | None:
    """Einhängepunkt einer schon verbundenen Freigabe (Freigabe ohne Groß/Klein; Server gleich geschrieben oder mit
    derselben Adresse – `mount` zeigt oft die IP, die Netzadresse den Namen) oder None."""
    import getpass
    me = me if me is not None else getpass.getuser()
    wanted: set[str] | None = None
    for line in mount_output.splitlines():
        m = _MOUNT_LINE.match(line.strip())
        if not m or m.group("fs") != "smbfs" or not _mine(m.group("opts") or "", me):
            continue
        if urllib.parse.unquote(m.group("share")).lower() != share.share.lower():
            continue
        host_port = m.group("host").lower()
        if ":" in share.host and host_port != share.host.lower():
            continue                                     # anderer Port = anderer Dienst
        if not access(m.group("path")):
            continue
        host = host_port.split(":")[0]
        if host == share.host.lower().split(":")[0]:
            return m.group("path")
        if wanted is None:
            wanted = resolve(share.host)
        if wanted and wanted & (resolve(host) or {host}):
            return m.group("path")
    return None


def _mount_output() -> str:
    try:
        return subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def netfs_mount(share: Share, auth: str, credentials: tuple[str, str] | None) -> str:
    """Über NetFS einbinden (wie der Finder, ohne Dialog) → Einhängepunkt. Fehler → MountError."""
    import objc
    from Foundation import NSBundle, NSMutableDictionary, NSURL
    funcs: dict = {}
    bundle = NSBundle.bundleWithPath_("/System/Library/Frameworks/NetFS.framework")
    objc.loadBundleFunctions(bundle, funcs, [("NetFSMountURLSync", b"i@@@@@@o^@")])
    open_opts = NSMutableDictionary.dictionaryWithDictionary_({"UIOption": "NoUI"})
    user = password = None
    if auth == GUEST:
        open_opts["Guest"] = True
    elif auth == KEYCHAIN:
        if not credentials:
            raise MountError("Schlüsselbund-Eintrag für die Freigabe fehlt – anlegen oder Anmeldung auf "
                             "„Kerberos“ stellen")
        user, password = credentials
    url = NSURL.URLWithString_(Share(share.host, share.share).url)
    if url is None:
        raise MountError("Netzadresse nicht lesbar")
    status, points = funcs["NetFSMountURLSync"](url, None, user, password, open_opts, None, None)
    if status == 17:                                      # EEXIST: schon verbunden – Einhängepunkt aus `mount`
        return ""
    if status != 0:
        raise MountError(_netfs_text(int(status)))
    paths = [str(p) for p in (points or [])]
    if not paths:
        raise MountError("Eingebunden, aber ohne Einhängepunkt")
    return paths[0]


def _netfs_text(status: int) -> str:
    known = {-6600: "Server nicht gefunden – Ottilie versucht es weiter (⌘R: sofort)",
             -6602: "Server nicht erreichbar – Ottilie versucht es weiter (⌘R: sofort)",
             2: "Freigabe nicht gefunden – Netzadresse prüfen",
             13: "Anmeldung abgelehnt – Konto im Schlüsselbund prüfen", 80: "Anmeldung abgelehnt – Konto prüfen",
             -5045: "Anmeldung abgelehnt – Konto prüfen", -128: "abgebrochen",
             60: "Server antwortet nicht – Ottilie versucht es weiter (⌘R: sofort)",
             64: "Server nicht erreichbar – Ottilie versucht es weiter (⌘R: sofort)",
             65: "Server nicht erreichbar (keine Route) – Netz prüfen", 17: "schon verbunden"}
    log.info("NetFS meldet Code %s", status)                 # Code nur ins Protokoll (Mischer M3)
    return known.get(status, "Einbinden gescheitert – bitte im Finder verbinden (⌘K)")


@dataclass
class _Attempt:
    """Stand des Einbindens einer Freigabe (je Netzadresse – Prüfrunde 13.11.2026: vorher galten Fehler, Wartezeit und
    laufender Versuch für alle Übergabeordner gemeinsam; ein ausgefallener Server hielt die anderen bis zu 5 Minuten
    auf und lieh ihnen seinen Fehlertext)."""
    error: str = ""
    failures: int = 0
    next: float = 0.0
    running: threading.Thread | None = None
    done: threading.Event = field(default_factory=threading.Event)


class NetMounter:
    """Hält den Stand der Einbindung je Freigabe: zuletzt gefundener Einhängepunkt, letzter Fehler, nächster Versuch.

    Eingebunden wird in einem eigenen Hintergrund-Thread: NetFS kann bei einem unerreichbaren Server lange brauchen,
    und kein Befehl des Fensters soll darauf warten (Review: sonst hing der Befehls-Pool). `ensure` wartet höchstens
    kurz und meldet sonst „wird verbunden …“."""

    WAIT_S = 3.0

    def __init__(self, mount: Callable[[Share, str, tuple[str, str] | None], str] = netfs_mount,
                 mounts: Callable[[], str] = _mount_output, clock: Callable[[], float] = time.monotonic,
                 resolve: Callable[[str], set[str]] = _addresses, access: Callable[[str], bool] = _readable) -> None:
        self._mount, self._mounts, self.clock, self._resolve = mount, mounts, clock, resolve
        self._access = access
        self._lock = threading.Lock()
        self._cache: tuple[float, str] | None = None
        self._roots: dict[str, tuple[float, str]] = {}
        self._attempts: dict[str, _Attempt] = {}
        self.busy: Callable[[], bool] = lambda: False      # Auftrag läuft? (die App setzt es)
        self.mounted_here: set[str] = set()

    def _attempt(self, share: Share) -> _Attempt:
        return self._attempts.setdefault(share.url, _Attempt())

    def error_of(self, address: str) -> str:
        """Letzter Fehler beim Einbinden dieser Freigabe ("" ohne)."""
        try:
            return self._attempts.get(parse(address).url, _Attempt()).error
        except MountError:
            return ""

    def _output(self) -> str:
        now = self.clock()
        cache = self._cache
        if cache is None or now - cache[0] > CACHE_S:
            cache = self._cache = (now, self._mounts())
            self._roots.clear()
        return cache[1]

    def _find(self, share: Share) -> str | None:
        return find_mounted(share, self._output(), resolve=self._resolve, access=self._access)

    def root(self, address: str) -> str:
        """Übergabeordner, wenn die Freigabe verbunden ist – sonst "" (ohne einzubinden; 5 s zwischengespeichert,
        auch die Namensauflösung)."""
        with self._lock:                                  # `_work` leert den Zwischenspeicher (Nachprüfung 13.11.2026)
            return self._root(address)

    def _root(self, address: str) -> str:
        out = self._output()
        hit = self._roots.get(address)
        if hit is not None and hit[0] == id(out):
            return hit[1]
        try:
            share = parse(address)
        except MountError:
            return ""
        point = self._find(share)
        root = os.path.join(point, share.sub) if point else ""
        self._roots[address] = (id(out), root)
        return root

    def ensure(self, address: str, auth: str, credentials: Callable[[], tuple[str, str] | None],
               now: bool = False, address_from_zentrale: bool = False, allowed: tuple[str, ...] = ()) -> str:
        """Verbunden halten → Übergabeordner. `now` (⌘R): Abstand übergehen. MountError mit Grund. `allowed`
        (per Profil): nur diese Server – eine gekaperte Zentrale lenkt die Mischung dann nicht woandershin."""
        share = parse(address)
        if allowed and share.host.split(":")[0].lower() not in {a.strip().lower() for a in allowed if a.strip()}:
            raise MountError(f"Server „{share.host}“ ist nicht als Übergabe-Server erlaubt (Konfigurationsprofil)")
        with self._lock:
            a = self._attempt(share)
            point = self._find(share)
            if point:
                a.error, a.failures = "", 0
                return os.path.join(point, share.sub)
            if self.busy():
                raise MountError("wird nach dem laufenden Auftrag eingebunden")
            if auth == KEYCHAIN and address_from_zentrale:
                a.error = ("Anmeldung per Schlüsselbund nur mit einer Netzadresse, die am Mac oder per Profil "
                           "eingestellt ist")
                raise MountError(a.error)
            if a.running is None:
                if not now and self.clock() < a.next:
                    raise MountError(a.error or "nicht verbunden")
                a.done.clear()
                a.running = threading.Thread(target=self._work, args=(share, auth, credentials, a),
                                             name="tm-einbinden", daemon=True)
                a.running.start()
            done = a.done
        done.wait(self.WAIT_S)
        with self._lock:
            if a.running is not None:
                raise MountError("wird verbunden …")
            point = self._find(share)
            if point:
                return os.path.join(point, share.sub)
            raise MountError(a.error or "nicht verbunden")

    def _work(self, share: Share, auth: str, credentials: Callable[[], tuple[str, str] | None],
              a: _Attempt) -> None:
        try:
            try:
                creds = credentials() if auth == KEYCHAIN else None
                point = self._mount(share, auth if auth in (GUEST, KERBEROS, KEYCHAIN) else GUEST, creds)
            except MountError as e:
                with self._lock:
                    self._fail(a, str(e))
                return
            except Exception as e:  # noqa: BLE001 – NetFS/PyObjC fehlen o. Ä.
                log.warning("Einbinden: %s: %s", type(e).__name__, e)
                with self._lock:
                    self._fail(a, "Einbinden nicht möglich – bitte im Finder verbinden (⌘K)")
                return
            with self._lock:
                self._cache = None
                if not point:                             # war schon verbunden (anderer Name derselben Freigabe)
                    point = self._find(share) or ""
                if not point:
                    self._fail(a, "Freigabe verbunden, Ordner aber nicht gefunden – Netzadresse prüfen")
                    return
                self.mounted_here.add(point)
                a.error, a.failures = "", 0
                log.info("Übergabeordner eingebunden: %s → %s", share.url, point)
        finally:
            with self._lock:
                a.running = None
            a.done.set()

    def _fail(self, a: _Attempt, why: str) -> None:
        a.error = why
        a.next = self.clock() + BACKOFF[min(a.failures, len(BACKOFF) - 1)]
        a.failures += 1
        log.warning("Übergabeordner nicht verbunden: %s", why)


MOUNTER = NetMounter()
