"""Geheimnisse im macOS-Schlüsselbund (Sicherheitsregel: Zugangsdaten nur dort).

Lesen über `security` (für den Helmut-Zugang und den Webtrigger-Schlüssel), Schreiben über das Security-Framework – so
steht das Geheimnis nie in einer Befehlszeile, die andere Prozesse sehen könnten.
"""
from __future__ import annotations

import re
import subprocess


NEW_PREFIX, LEGACY_PREFIX = "ottilie-", "tonmischung-"      # Einträge von vor der Umbenennung (08.10.2026)
NOT_FOUND = 44                                               # `security`: errSecItemNotFound
_HEX = re.compile(r"(?:[0-9a-f]{2})+")
TIMEOUT_S = 60.0                                             # Zeit für „Erlauben“ im Dialog des Schlüsselbunds


class KeychainError(RuntimeError):
    """Schlüsselbund nicht lesbar (gesperrt, Zugriff verweigert, keine Antwort) – nicht dasselbe wie „fehlt“."""


def legacy_service(service: str) -> str:
    """Name des Eintrags von vor der Umbenennung („ottilie-helmut“ → „tonmischung-helmut“), sonst ""."""
    return LEGACY_PREFIX + service[len(NEW_PREFIX):] if service.startswith(NEW_PREFIX) else ""


def read_secret(service: str, account: str) -> str:
    """Kennwort des Eintrags oder "" (Eintrag fehlt). Fehlt ein „ottilie-…“-Eintrag, gilt der alte „tonmischung-…“.
    Kein Zugriff → `KeychainError` (Review B14: vorher galt das als „fehlt“, und der Webtrigger-Schlüssel wurde durch
    einen neuen ersetzt – alle Stream-Deck-Tasten waren danach ungültig)."""
    for name in (service, legacy_service(service)):
        found = _security(["find-generic-password", "-s", name, "-a", account, "-w"], name) if name else None
        if found is not None:
            return _plain(found, ["-s", name, "-a", account], name)
    return ""


def account_and_secret(service: str) -> tuple[str, str] | None:
    """(Konto, Kennwort) des Eintrags – für Zugänge, bei denen das Konto im Eintrag steht (Helmut: Konto =
    Benutzername). Fehlt er, gilt der von vor der Umbenennung; None, wenn es keinen gibt.
    Kein Zugriff → KeychainError."""
    for name in (service, legacy_service(service)):
        info = _security(["find-generic-password", "-s", name], name) if name else None
        if info is None:
            continue
        secret = _security(["find-generic-password", "-s", name, "-w"], name) or ""
        account = next((line.split("=", 1)[1].strip().strip('"') for line in info.splitlines()
                        if '"acct"<blob>=' in line), "")
        return account, _plain(secret, ["-s", name], name)
    return None


def _plain(output: str, query: list[str], service: str) -> str:
    """Kennwort aus `security … -w`. Enthält es nicht druckbare Zeichen (z. B. einen mit eingefügten Zeilenumbruch),
    gibt `security` es hexkodiert aus – geprüft 10.11.2026. Ob eine Hexfolge kodiert ist oder das Kennwort selbst,
    zeigt nur `-g` (`password: 0x…` statt `password: "…"`); nur dann wird nachgefragt."""
    secret = output.rstrip("\n")
    if not _HEX.fullmatch(secret):
        return secret
    try:
        shown = _security(["find-generic-password", *query, "-g"], service, stderr=True) or ""
    except KeychainError:                 # zweite Abfrage verweigert: der Wert aus -w gilt (Review 10.11.2026)
        return secret
    if f"password: 0x{secret}" in shown.lower():
        return bytes.fromhex(secret).decode("utf-8", errors="replace").rstrip("\n")
    return secret


def _security(args: list[str], service: str, stderr: bool = False) -> str | None:
    """`security …` mit Zeitlimit (Review B14: wartet der Schlüsselbund auf „Erlauben“, hing es sonst ewig).
    Ausgabe, None bei „gibt es nicht“, sonst KeychainError."""
    try:
        out = subprocess.run(["/usr/bin/security", *args], capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise KeychainError(f"Schlüsselbund antwortet nicht (Eintrag „{service}“) – Zugriff erlauben?") from e
    except (OSError, subprocess.SubprocessError) as e:
        raise KeychainError(f"Schlüsselbund nicht lesbar: {e}") from e
    if out.returncode == 0:
        return out.stderr if stderr else out.stdout
    if out.returncode == NOT_FOUND:
        return None
    detail = (out.stderr or "").strip() or out.returncode
    raise KeychainError(f"Schlüsselbund: Eintrag „{service}“ nicht lesbar ({detail})")


def read_secret_framework(service: str, account: str) -> str:
    """Kennwort über das Security-Framework lesen ("" = fehlt). Für Einträge, die die App selbst angelegt hat: der
    Schreiber darf ohne Rückfrage lesen – `security` (ein anderes Programm) müsste erst „Erlauben“ (11.11.2026)."""
    import Security
    query = {Security.kSecClass: Security.kSecClassGenericPassword, Security.kSecAttrService: service,
             Security.kSecAttrAccount: account, Security.kSecReturnData: True,
             Security.kSecMatchLimit: Security.kSecMatchLimitOne}
    status, data = Security.SecItemCopyMatching(query, None)
    if status == Security.errSecItemNotFound:
        return ""
    if status != 0 or data is None:
        raise KeychainError(f"Schlüsselbund: Eintrag „{service}“ nicht lesbar (OSStatus {status})")
    return bytes(data).decode("utf-8", errors="replace")


def write_secret(service: str, account: str, secret: str) -> None:
    """Eintrag anlegen oder ersetzen. Fehler → RuntimeError mit dem OSStatus."""
    import Foundation
    import Security
    data = Foundation.NSData.dataWithBytes_length_(secret.encode(), len(secret.encode()))
    query = {Security.kSecClass: Security.kSecClassGenericPassword,
             Security.kSecAttrService: service, Security.kSecAttrAccount: account}
    status = Security.SecItemUpdate(query, {Security.kSecValueData: data})
    if status == Security.errSecItemNotFound:
        status, _ = Security.SecItemAdd({**query, Security.kSecValueData: data}, None)
    if status != 0:
        raise RuntimeError(f"Schlüsselbund: Eintrag „{service}“ nicht gespeichert (OSStatus {status})")
