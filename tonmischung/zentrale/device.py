"""Kopplung mit der Zentrale – einmal je Mac, gilt für alle macOS-Benutzer (Auftrag 11.11.2026).

Ablage im Schlüsselbund (Sicherheitsregel: Zugangsdaten nur dort; Review 11.11.2026):
- Standard: **System-Schlüsselbund** (`/Library/Keychains/System.keychain`), Eintrag „ottilie-zentrale“ / „geraet“,
  für alle Programme lesbar (`security … -A`) – so liest jeder angemeldete Benutzer den Schlüssel ohne Rückfrage.
  Schreiben braucht einmal die Bestätigung eines Administrators (macOS-Dialog). Das Geheimnis geht hexkodiert über
  `security -i` aus einer eigenen Hilfsdatei, nie über eine Befehlszeile, die andere Prozesse sehen könnten.
- `TONMISCHUNG_ZENTRALE_GERAET=login`: Anmelde-Schlüsselbund des Benutzers (Test, oder wenn kein Administrator da ist –
  gilt dann nur für diesen Benutzer).
- `TONMISCHUNG_ZENTRALE_GERAET=<Pfad>`: Datei – nur für automatische Tests mit Attrappen-Schlüsseln.
Der Schlüssel gehört dem Raum, nicht einer Person.

Der Schlüssel geht nur an die Adresse, für die er ausgegeben wurde (Schema, Rechner, Port, Pfad) – eine untergeschobene
andere Adresse (lokale Einstellung, Zentrale, Profil) bekommt ihn nie. Ohne TLS nur an localhost.

Zwei Wege (Jens 13.11.2026): Kopplungscode (`pair`) oder Anmelden ohne Code (`register`) – dann wartet der Mac, bis
die Administration ihn einem Raum zuordnet. Die Gerätekennung (`fingerprint`) zeigen Ottilie und die Admin-Seite
gleich an; so lässt sich vergleichen, welcher Mac es ist.
"""
from __future__ import annotations

import json
import os
import shlex
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

SYSTEM_KEYCHAIN = "/Library/Keychains/System.keychain"
SERVICE, ACCOUNT = "ottilie-zentrale", "geraet"
MACHINE = "system"                               # Ablage: System-Schlüsselbund (Standard)
LOGIN = "login"                                  # Anmelde-Schlüsselbund des Benutzers
FORMAT = "ottilie-geraet/1"
ENV_FILE = "TONMISCHUNG_ZENTRALE_GERAET"         # „login“ oder (Tests) ein Dateipfad
FINGERPRINT_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"   # wie die Kopplungscodes der Zentrale


def fingerprint(key: str) -> str:
    """Gerätekennung aus dem Schlüssel (6 Zeichen) – die Zentrale rechnet sie gleich (`app.auth.fingerprint`)."""
    import hashlib
    digest = hashlib.sha256(("ottilie-geraet:" + key).encode("utf-8")).digest()
    return "".join(FINGERPRINT_ALPHABET[b % len(FINGERPRINT_ALPHABET)] for b in digest[:6])


class PairingError(Exception):
    """Kopplung nicht möglich – Text für die Anzeige."""


@dataclass(frozen=True)
class DeviceKey:
    address: str
    room: str
    device: str
    key: str
    paired_at: str = ""

    def matches(self, address: str) -> bool:
        return normalize_address(address) == normalize_address(self.address)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.key)


def storage() -> str:
    """„system“, „login“ oder ein Dateipfad (nur Tests)."""
    return os.environ.get(ENV_FILE) or MACHINE


def normalize_address(address: str) -> str:
    """„https://server:8443/pfad/“ → „https://server:8443/pfad“ (Kleinschreibung bei Schema und Rechner)."""
    parts = urllib.parse.urlsplit(str(address or "").strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    netloc = (f"[{host}]" if ":" in host else host) + (f":{parts.port}" if parts.port else "")
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, parts.path.rstrip("/"), "", ""))


def local_address(address: str) -> bool:
    host = (urllib.parse.urlsplit(address).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def check_address(address: str) -> str:
    """Zulässige Adresse der Zentrale oder PairingError: https, ohne TLS nur localhost; nichts Geheimes in der URL."""
    norm = normalize_address(address)
    if not norm:
        raise PairingError("Adresse der Zentrale fehlt oder ist ungültig (https://…)")
    parts = urllib.parse.urlsplit(str(address).strip())
    if parts.username or parts.password or parts.query or parts.fragment:
        raise PairingError("Adresse der Zentrale ohne Benutzer, Kennwort oder Zusätze angeben")
    if norm.startswith("http://") and not local_address(norm):
        raise PairingError("Die Zentrale nur über https:// – sonst ginge der Geräteschlüssel unverschlüsselt durchs "
                           "Netz")
    return norm


def tls_context() -> ssl.SSLContext:
    """Systemzertifikate plus certifi (im Paket), Hostname wird geprüft."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return ctx


def _decode(text: str) -> DeviceKey | None:
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = json.loads(bytes.fromhex(text.strip()).decode("utf-8"))
        except ValueError:
            return None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return None
    try:
        return DeviceKey(check_address(str(data.get("adresse", ""))), str(data.get("raum", "")),
                         str(data.get("geraet", "")), str(data["schluessel"]), str(data.get("gekoppelt", "")))
    except (KeyError, PairingError):
        return None


def read(where: str | None = None) -> DeviceKey | None:
    """Gekoppelt? None, wenn nicht (oder nicht lesbar – dann bleibt Ottilie wie ohne Zentrale)."""
    where = where or storage()
    try:
        if where == MACHINE:
            r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w",
                                SYSTEM_KEYCHAIN], capture_output=True, text=True, timeout=20)
            return _decode(r.stdout) if r.returncode == 0 else None
        if where == LOGIN:
            from tonmischung.config.keychain import KeychainError, read_secret_framework
            try:
                text = read_secret_framework(SERVICE, ACCOUNT)
            except (KeychainError, ImportError):
                return None
            return _decode(text) if text else None
        with open(where, encoding="utf-8") as f:
            return _decode(f.read())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _admin_keychain(text: str) -> None:
    """In den System-Schlüsselbund: einmal Administrator-Bestätigung (macOS-Dialog). Der Befehl für `security -i`
    steht in einer eigenen Hilfsdatei (0600), das Geheimnis hexkodiert – nie in einer sichtbaren Befehlszeile."""
    command = (f"add-generic-password -U -A -s {SERVICE} -a {ACCOUNT} -l \"Ottilie-Zentrale (Kopplung)\" "
               f"-X {text.encode('utf-8').hex()} {SYSTEM_KEYCHAIN}\n")
    fd, tmp = tempfile.mkstemp(prefix=".ottilie-kopplung-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(command)
        os.chmod(tmp, 0o600)
        # Die Hilfsdatei gehört dem Benutzer – während der Dialog offen ist, könnte ein anderer Prozess desselben
        # Benutzers sie tauschen (Sicherheit 11.11.2026). Darum liest root erst in eine eigene Datei und führt sie nur
        # aus, wenn die Prüfsumme stimmt.
        import hashlib
        digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
        # /private/tmp hat das Sticky-Bit: dort kann der Benutzer die Datei von root nicht tauschen (Sicherheit)
        shell = ("t=$(/usr/bin/mktemp /private/tmp/.ottilie-kopplung.XXXXXX) && "
                 f"/bin/cat {shlex.quote(tmp)} > \"$t\" && "
                 f"[ \"$(/usr/bin/shasum -a 256 < \"$t\" | /usr/bin/cut -d' ' -f1)\" = {digest} ] && "
                 f"/usr/bin/security -i < \"$t\"; r=$?; /bin/rm -f \"$t\"; exit $r")
        prompt = "Ottilie möchte die Kopplung mit der Zentrale für alle Benutzer dieses Macs ablegen."
        script = (f"do shell script {_applescript_string(shell)} with prompt {_applescript_string(prompt)} "
                  "with administrator privileges")
        r = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=300)
    finally:
        os.unlink(tmp)                                  # nur die eigene Hilfsdatei
    if r.returncode != 0:
        if "-128" in r.stderr:
            raise PairingError("Abgebrochen – ohne Bestätigung eines Administrators bleibt der Mac ungekoppelt")
        raise PairingError(f"Ablegen nicht möglich: {r.stderr.strip()[:200]}")


def write(dev: DeviceKey, where: str | None = None, install: Callable[[str], None] | None = None) -> None:
    """Kopplung ablegen: System-Schlüsselbund (Standard, Administrator-Dialog bzw. `install`), Anmelde-Schlüsselbund
    oder – nur in Tests – eine Datei mit Rechten 0600."""
    where = where or storage()
    text = json.dumps({"format": FORMAT, "adresse": dev.address, "raum": dev.room, "geraet": dev.device,
                       "schluessel": dev.key, "gekoppelt": dev.paired_at}, ensure_ascii=False)
    if where == MACHINE:
        (install or _admin_keychain)(text)
        return
    if where == LOGIN:
        from tonmischung.config.keychain import write_secret
        try:
            write_secret(SERVICE, ACCOUNT, text)
        except (RuntimeError, ImportError) as e:
            raise PairingError(f"Schlüsselbund: {e}") from None
        return
    os.makedirs(os.path.dirname(where) or ".", exist_ok=True)
    fd = os.open(where, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def device_name() -> str:
    """Name des Geräts für die Zentrale – ohne Personenbezug (der Rechnername enthält oft einen Vornamen)."""
    import hashlib
    return "Ottilie-Mac " + hashlib.sha256(socket.gethostname().encode()).hexdigest()[:6]


def pair(address: str, code: str, name: str | None = None, opener=None, timeout_s: float = 15.0) -> DeviceKey:
    """Kopplungscode bei der Zentrale einlösen → Schlüssel (noch nicht abgelegt)."""
    return _post(address, "/api/koppeln", {"code": code, "geraet": name or device_name()}, opener, timeout_s)


def register(address: str, name: str | None = None, opener=None, timeout_s: float = 15.0) -> DeviceKey:
    """Ohne Code anmelden → Schlüssel eines wartenden Geräts (noch nicht abgelegt, Raum noch leer)."""
    return _post(address, "/api/anmelden", {"geraet": name or device_name()}, opener, timeout_s)


def _post(address: str, path: str, payload: dict, opener, timeout_s: float) -> DeviceKey:
    base = check_address(address)
    body = json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=body, method="POST", headers={"Content-Type": "application/json"})
    open_ = opener or urllib.request.build_opener(urllib.request.HTTPSHandler(context=tls_context())).open
    try:
        with open_(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("fehler", "")
        except (ValueError, OSError):
            msg = ""
        if e.code == 404 and path == "/api/anmelden":
            raise PairingError("Diese Zentrale kennt das Anmelden ohne Code noch nicht – mit Kopplungscode koppeln "
                               "oder die Zentrale aktualisieren") from None
        raise PairingError(msg or f"Zentrale lehnt ab ({e.code})") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = str(getattr(e, "reason", e))
        if "CERTIFICATE_VERIFY_FAILED" in reason or "certificate" in reason.lower():
            raise PairingError("Zertifikat der Zentrale nicht vertrauenswürdig – Administration fragen") from None
        raise PairingError("Zentrale nicht erreichbar – Adresse und Netz prüfen") from None
    key = str(data.get("schluessel") or "")
    if not key:
        raise PairingError("Antwort der Zentrale ohne Schlüssel")
    return DeviceKey(base, str(data.get("raum", "")), str(data.get("geraet", "")), key,
                     time.strftime("%Y-%m-%dT%H:%M:%S%z"))
