"""HTTP-Client für Helmut (nur Standardbibliothek).

Endpunkte nach Auftrag Abschnitt 5, abgeglichen mit der offiziellen OpenAPI von Helmut4
(MoovIT) und dem Paket helmut4-client 2026.1b6.
Formate siehe `mapping.py`. Basisadresse ist der Server ohne Pfad (z. B.
`https://helmut.example.org`); die Dienste liegen unter /v1/members, /v1/fx, /v1/co.

Anmeldung: Benutzername/Kennwort (`/v1/members/auth/login/body`) oder OAuth-Schlüssel des
Kontos (`/v1/members/auth/oauth`, Formular). Beide liefern ein Bearer-Token im Header
`Authorization` (mit `responseWithToken=true` auch im Inhalt).
"""
from __future__ import annotations

import errno
import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from tonmischung.helmut import mapping
from tonmischung.helmut.mapping import Contribution, Handover

log = logging.getLogger(__name__)


class HelmutError(Exception):
    """Helmut hat eine Anfrage abgelehnt."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class HelmutUnavailableError(HelmutError):
    """Helmut ist nicht erreichbar (Netz, Server, Wartung)."""


Credentials = Callable[[], "tuple[str, str]"]


def keychain_credentials(service: str) -> Credentials:
    """Zugangsdaten aus dem macOS-Schlüsselbund (generisches Kennwort, Konto = Benutzername) – gelesen erst beim
    Anmelden; Fehler als HelmutError."""
    from tonmischung.config.keychain import KeychainError, account_and_secret

    def read() -> tuple[str, str]:
        try:
            found = account_and_secret(service)
        except KeychainError as e:
            raise HelmutError(str(e)) from e
        if found is None:
            raise HelmutError(f"Kein Schlüsselbund-Eintrag „{service}“ für Helmut gefunden")
        return found
    return read


LOGIN_PASSWORD, LOGIN_OAUTH = "passwort", "oauth"
_UNREACHABLE = {errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EHOSTDOWN, errno.ENETDOWN}


def not_sent(error: Exception) -> bool:
    """Kam die Verbindung gar nicht zustande (abgewiesen, Name unbekannt, Netz weg)? Dann hat Helmut die Anfrage
    sicher nicht bekommen. Zeitüberschreitung und abgebrochene Antwort zählen nicht – da ist es offen."""
    reason = getattr(error, "reason", error) if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
        return True
    return isinstance(reason, OSError) and not isinstance(reason, TimeoutError) and reason.errno in _UNREACHABLE


class HelmutClient:
    def __init__(self, base_url: str, credentials: Credentials, timeout_s: float = 15.0,
                 retries: int = 2, backoff_s: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep, login_method: str = LOGIN_PASSWORD,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.base_url = base_url.rstrip("/")
        self._credentials = credentials
        self.login_method = login_method
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self._sleep = sleep
        self._clock = clock
        self._token: str | None = None
        self.last_ok: float | None = None          # wann Helmut zuletzt geantwortet hat (Uhr `clock`)

    # ── Anmeldung und Anfragen ──────────────────────────────────────────────
    def reachable(self, max_age_s: float = 30.0, timeout_s: float = 3.0) -> None:
        """Für die Titelleiste: Hat Helmut vor kurzem geantwortet, gilt er als erreichbar; sonst ein kurzer
        Anmeldeversuch ohne Wiederholung. Vorher meldete sich jede Statusabfrage neu an – bei einem Ausfall
        dauerte sie bis zu 50 s und hielt die Befehle des Fensters auf (Review A13)."""
        if self._token is not None and self.last_ok is not None and self._clock() - self.last_ok < max_age_s:
            return
        self.login(timeout_s=timeout_s, retries=0)

    def login(self, timeout_s: float | None = None, retries: int | None = None) -> None:
        """Anmelden; das Konto kommt aus dem Schlüsselbund (bei OAuth: Schlüssel und Geheimnis)."""
        user, secret = self._credentials()
        limits = {"timeout_s": timeout_s, "retries": retries}
        params = {"responseWithToken": "true"}
        if self.login_method == LOGIN_OAUTH:
            form = urllib.parse.urlencode({"client_id": user, "client_secret": secret,
                                           "grant_type": "authorization", "scope": "platform"})
            body, headers = self._send("POST", "/v1/members/auth/oauth", form.encode(), params=params,
                                       auth=False, content_type="application/x-www-form-urlencoded",
                                       with_headers=True, idempotent=True, **limits)
        else:
            body, headers = self._send("POST", "/v1/members/auth/login/body",
                                       {"username": user, "password": secret}, params=params,
                                       auth=False, with_headers=True, idempotent=True, **limits)
        token = mapping.token_from_login(headers, body)
        if not token:
            raise HelmutError("Anmeldung bei Helmut ohne Token")
        self._token = token

    def request(self, method: str, path: str, body: Any = None,
                params: dict | None = None, idempotent: bool | None = None) -> Any:
        if self._token is None:
            self.login()
        try:
            return self._send(method, path, body, params=params, idempotent=idempotent)
        except HelmutError as e:
            if e.status != 401:
                raise
            self.login()                       # Token abgelaufen: einmal neu anmelden
            return self._send(method, path, body, params=params, idempotent=idempotent)

    def _send(self, method: str, path: str, body: Any = None, params: dict | None = None,
              auth: bool = True, content_type: str = "application/json",
              with_headers: bool = False, idempotent: bool | None = None,
              timeout_s: float | None = None, retries: int | None = None) -> Any:
        """Wiederholt nur, was sich gefahrlos wiederholen lässt (Review A6): GET/PUT/DELETE und ausdrücklich als
        idempotent markierte POSTs (Suche, Anmeldung). Anlegende POSTs (Duplikat, Asset, Benachrichtigung) nur, wenn
        die Verbindung gar nicht zustande kam – sonst entstünden bei einer verspäteten Antwort Doppelte in Helmut.
        Bleibende Serverfehler (500/501) sind ein Fehler, kein „nicht erreichbar“ (Review B6)."""
        if idempotent is None:
            idempotent = method in ("GET", "PUT", "DELETE", "HEAD")
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        if isinstance(body, bytes):
            data: bytes | None = body
        else:
            data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": content_type, "Accept": "application/json"}
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        last: Exception | None = None
        timeout = self.timeout_s if timeout_s is None else timeout_s
        for attempt in range((self.retries if retries is None else retries) + 1):
            if attempt:
                self._sleep(self.backoff_s * attempt)
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    resp_headers = resp.headers
                self.last_ok = self._clock()
                try:
                    result = json.loads(raw) if raw else None
                except ValueError:
                    result = raw.decode(errors="replace")
                return (result, resp_headers) if with_headers else result
            except urllib.error.HTTPError as e:
                if e.code >= 500:
                    last = e
                    if idempotent:
                        continue
                    break
                detail = e.read().decode(errors="replace")[:200]
                raise HelmutError(f"Helmut lehnt {method} {path} ab ({e.code}): {detail}",
                                  status=e.code) from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                if idempotent or not_sent(e):
                    continue
                break
        if isinstance(last, urllib.error.HTTPError) and last.code in (500, 501):
            raise HelmutError(f"Helmut meldet einen Fehler bei {method} {path} ({last.code})",
                              status=last.code) from last
        log.warning("Helmut nicht erreichbar: %s %s (%s)", method, path, last)
        if not idempotent and last is not None and not not_sent(last):
            raise HelmutUnavailableError(f"Helmut hat nicht geantwortet ({method} {path}) – ob es angekommen ist, ist "
                                         "offen; bitte in Helmut nachsehen, bevor es wiederholt wird") from last
        raise HelmutUnavailableError(f"Helmut ist nicht erreichbar ({method} {path})") from last

    # ── Beiträge ────────────────────────────────────────────────────────────
    def list_for_mixing(self) -> list[Contribution]:
        projects = self.request("POST", "/v1/fx/projects/search", mapping.search_filter_for_mixing(),
                                params={"limit": 500, "page": 0}, idempotent=True)   # Suche ändert nichts
        out = []
        for p in projects if isinstance(projects, list) else []:
            try:
                out.append(mapping.contribution_from_project(p))
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                # ein falsch gepflegtes Projekt darf nicht die ganze Liste kosten (Review B7)
                log.warning("Helmut-Projekt übersprungen (%s): %r", e, p.get("id") if isinstance(p, dict) else p)
        return out

    def get(self, project_id: str) -> Contribution:
        return mapping.contribution_from_project(
            self.request("GET", f"/v1/fx/projects/{urllib.parse.quote(project_id)}"))

    def assets(self, project_id: str) -> list[dict]:
        return self.request("GET", f"/v1/co/assets/{urllib.parse.quote(project_id)}") or []

    def handover(self, project_id: str) -> Handover:
        return mapping.find_handover(self.assets(project_id))

    # ── Schreiben ───────────────────────────────────────────────────────────
    def write_mix(self, contribution: Contribution, mix) -> Contribution:
        """Set „Mischung“ schreiben – nur die Metadaten (PUT /projects/metadata/{id}), damit
        gleichzeitige Änderungen an anderen Projektfeldern nicht überschrieben werden."""
        patch = mapping.metadata_patch(contribution.raw, mix)
        updated = self.request("PUT", f"/v1/fx/projects/metadata/{urllib.parse.quote(contribution.id)}", patch)
        return mapping.contribution_from_project(updated or {**contribution.raw, **patch})

    def set_locked(self, project_id: str, locked: bool) -> None:
        self.request("PUT", f"/v1/fx/projects/status/{urllib.parse.quote(project_id)}",
                     mapping.status_body(locked))

    def add_asset(self, project_id: str, path: str, kind: str) -> dict:
        return self.request("POST", "/v1/co/assets", mapping.new_asset(project_id, path, kind),
                            params={"createBinStructure": "true"})

    def duplicate(self, contribution: Contribution, title: str) -> Contribution:
        project = self.request("POST", "/v1/fx/projects/duplicate", mapping.duplicate_body(contribution.raw, title))
        if not isinstance(project, dict) or not project.get("id") or str(project["id"]) == contribution.id:
            raise HelmutError("Helmut hat kein neues Projekt für das Duplikat geliefert")
        return mapping.contribution_from_project(project)

    def add_handover_asset(self, project_id: str, asset) -> dict:
        return self.request("POST", "/v1/co/assets", mapping.handover_asset(project_id, asset),
                            params={"createBinStructure": "true"})

    def notify(self, username: str, title: str, body: str) -> None:
        self.request("POST", f"/v1/members/users/notification/{urllib.parse.quote(username)}",
                     mapping.notification_body(title, body))
