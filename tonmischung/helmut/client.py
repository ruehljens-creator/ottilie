"""HTTP-Client für Helmut (nur Standardbibliothek).

Endpunkte nach Auftrag Abschnitt 5, abgeglichen mit der offiziellen OpenAPI von Helmut4
(MoovIT) und dem Paket helmut4-client 2026.1b6.
Formate siehe `mapping.py`. Basisadresse ist der Server ohne Pfad (z. B.
`https://helmut.example.org`); die Dienste liegen unter /v1/members, /v1/fx, /v1/co.

Anmeldung: Benutzername/Kennwort (`/v1/members/auth/login/body`) oder OAuth-Schlüssel des
Kontos (`/v1/members/auth/oauth`, Formular). Beide liefern ein Bearer-Token im Header
`Authorization` (mit `responseWithToken=true` auch im Inhalt). Oder ein Bearer-Token (API-Token des
Kontos, Einstellung „token“): Es steht als Kennwort im Schlüsselbund und geht ohne Anmeldeaufruf mit jeder Anfrage.

Weiterleitungen folgt der Client nur bei GET/HEAD; das Token geht dabei nur an denselben Server.
"""
from __future__ import annotations

import base64
import errno
import html
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
    """Helmut ist nicht erreichbar (Netz, Server, Wartung). `sent_unknown`: die Anfrage ging hinaus, die Antwort kam
    nicht – ob Helmut sie ausgeführt hat, ist offen."""

    def __init__(self, message: str, status: int | None = None, sent_unknown: bool = False) -> None:
        super().__init__(message, status)
        self.sent_unknown = sent_unknown


class HelmutAuthError(HelmutError):
    """Anmeldung abgelehnt oder nicht möglich (Zugangsdaten, Bearer-Token, Schlüsselbund) – nicht ein einzelner
    Aufruf. Der Nachtrag wartet dann, statt Einträge zu zählen. Lehnt Helmut nur einen Aufruf ab, bleibt es ein
    `HelmutError` (status 401)."""


Credentials = Callable[[], "tuple[str, str]"]


def keychain_credentials(service: str) -> Credentials:
    """Zugangsdaten aus dem macOS-Schlüsselbund (generisches Kennwort, Konto = Benutzername) – gelesen erst beim
    Anmelden; Fehler als HelmutAuthError (ohne lesbaren Schlüsselbund ist keine Anmeldung möglich)."""
    from tonmischung.config.keychain import KeychainError, account_and_secret

    def read() -> tuple[str, str]:
        try:
            found = account_and_secret(service)
        except KeychainError as e:
            raise HelmutAuthError(str(e)) from e
        if found is None:
            raise HelmutAuthError(f"Kein Schlüsselbund-Eintrag „{service}“ für Helmut gefunden")
        return found
    return read


LOGIN_PASSWORD, LOGIN_OAUTH, LOGIN_TOKEN = "passwort", "oauth", "token"
_UNREACHABLE = {errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EHOSTDOWN, errno.ENETDOWN}


def not_sent(error: Exception) -> bool:
    """Kam die Verbindung gar nicht zustande (abgewiesen, Name unbekannt, Netz weg)? Dann hat Helmut die Anfrage
    sicher nicht bekommen. Zeitüberschreitung und abgebrochene Antwort zählen nicht – da ist es offen."""
    reason = getattr(error, "reason", error) if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
        return True
    return isinstance(reason, OSError) and not isinstance(reason, TimeoutError) and reason.errno in _UNREACHABLE


TOKEN_REJECTED = "Helmut lehnt das Bearer-Token ab (401) – Token im Schlüsselbund prüfen"
CALL_REJECTED = "die Anmeldung gilt, Helmut erlaubt diesen Aufruf aber nicht"
WHOAMI = "/v1/members/auth/whoami"


def _origin(url: str) -> tuple[str, str, int | None] | None:
    try:
        parts = urllib.parse.urlsplit(url)            # Schema und Host schon klein
        return parts.scheme, parts.hostname or "", parts.port or {"http": 80, "https": 443}.get(parts.scheme)
    except ValueError:                                # ungültiger Port in der Weiterleitung
        return None


def _same_server(old: str, new: str) -> bool:
    """Derselbe Server – oder derselbe Host von http auf https gehoben (Standardports)."""
    a, b = _origin(old), _origin(new)
    if a is None or b is None or a[1] != b[1]:
        return False
    return a == b or (a[0], a[2], b[0], b[2]) == ("http", 80, "https", 443)


class _Redirect(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen nur für GET/HEAD – aus einem POST machte urllib still einen GET, der als Erfolg galt, ohne dass
    Helmut etwas schrieb. Das Token geht nur an denselben Server (urllib gab `Authorization` sonst an jeden Host
    weiter). Review 10.11.2026."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.get_method() not in ("GET", "HEAD") or _origin(newurl) is None:
            return None                                # urllib meldet dann die Weiterleitung als Fehler
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and not _same_server(req.full_url, newurl):
            new.remove_header("Authorization")
        return new


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
        self._opener = urllib.request.build_opener(_Redirect)

    # ── Anmeldung und Anfragen ──────────────────────────────────────────────
    def reachable(self, max_age_s: float = 30.0, timeout_s: float = 3.0) -> None:
        """Für die Titelleiste: Hat Helmut vor kurzem geantwortet, gilt er als erreichbar; sonst ein kurzer
        Anmeldeversuch ohne Wiederholung. Vorher meldete sich jede Statusabfrage neu an – bei einem Ausfall
        dauerte sie bis zu 50 s und hielt die Befehle des Fensters auf (Review A13)."""
        if self._token is not None and self.last_ok is not None and self._clock() - self.last_ok < max_age_s:
            return
        self.check_auth(timeout_s=timeout_s)

    def check_auth(self, timeout_s: float | None = None) -> None:
        """Anmeldung prüfen, ohne Wiederholung: neu anmelden bzw. beim Token-Verfahren den Schlüsselbund neu lesen
        und `GET /v1/members/auth/whoami` fragen (ohne Anmeldeaufruf prüft sonst nichts Netz und Token)."""
        self.login(timeout_s=timeout_s, retries=0)
        if self.login_method == LOGIN_TOKEN:
            self._whoami(timeout_s)

    def _whoami(self, timeout_s: float | None = None) -> None:
        """Nimmt Helmut das Token? 401 → HelmutAuthError, sonst wie jede Anfrage."""
        try:
            self._send("GET", WHOAMI, timeout_s=timeout_s, retries=0)
        except HelmutError as e:
            if e.status == 401:
                raise HelmutAuthError(TOKEN_REJECTED, status=401) from None
            raise

    def login(self, timeout_s: float | None = None, retries: int | None = None) -> None:
        """Anmelden; das Konto kommt aus dem Schlüsselbund (bei OAuth: Schlüssel und Geheimnis, bei „token“: das
        Bearer-Token als Kennwort – dann ohne Aufruf an Helmut)."""
        user, secret = self._credentials()
        if self.login_method == LOGIN_TOKEN:
            token = mapping.bearer_token(secret)
            if not token:
                raise HelmutAuthError("Kein Bearer-Token für Helmut im Schlüsselbund")
            if not mapping.valid_bearer(token):     # Umbruch, Anführungszeichen … – den Wert nie nennen
                raise HelmutAuthError("Bearer-Token für Helmut im Schlüsselbund enthält unzulässige Zeichen")
            self._token = token
            return
        if self.login_method == LOGIN_OAUTH:
            path, content_type = "/v1/members/auth/oauth", "application/x-www-form-urlencoded"
            data: Any = urllib.parse.urlencode({"client_id": user, "client_secret": secret,
                                                "grant_type": "authorization", "scope": "platform"}).encode()
        else:
            path, content_type = "/v1/members/auth/login/body", "application/json"
            data = {"username": user, "password": secret}
        try:
            body, headers = self._send("POST", path, data, params={"responseWithToken": "true"}, auth=False,
                                       content_type=content_type, with_headers=True, idempotent=True,
                                       timeout_s=timeout_s, retries=retries)
        except HelmutError as e:
            if e.status == 401:
                raise HelmutAuthError(str(e), status=401) from None
            raise
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
            first = e
        # 401: neu anmelden bzw. den Schlüsselbund neu lesen (kein Netzaufruf). Dasselbe Token noch einmal zu schicken,
        # verdeckte nur die Ablehnung; ein inzwischen eingetragenes anderes Token wird einmal versucht.
        before = self._token
        self.login()
        if self.login_method != LOGIN_TOKEN or self._token != before:
            try:
                return self._send(method, path, body, params=params, idempotent=idempotent)
            except HelmutError as again:
                if again.status != 401:
                    raise
                first = again
        if self.login_method == LOGIN_TOKEN:
            self._whoami()                     # lehnt Helmut das Token ab → HelmutAuthError
        raise HelmutError(f"{first} – {CALL_REJECTED}", status=401) from None

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
                with self._opener.open(req, timeout=timeout) as resp:
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
                if 300 <= e.code < 400:
                    raise HelmutError(f"Helmut leitet {method} {path} weiter ({e.code}) – Adresse in den Einstellungen "
                                      "prüfen", status=e.code) from None
                # Antworttext nur, wenn er nichts Geheimes zurückspiegelt; bei der Anmeldung nie (Review 10.11.2026)
                detail = e.read().decode(errors="replace") if auth else ""
                detail = "" if self._reflects_secret(detail) else detail[:200]
                raise HelmutError(f"Helmut lehnt {method} {path} ab ({e.code})" + (f": {detail}" if detail else ""),
                                  status=e.code) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                if idempotent or not_sent(e):
                    continue
                break
        # Grundtext einer HTTP-Antwort wählt der Server – nicht ins Protokoll, nicht in die Kette (Review 10.11.2026)
        http = isinstance(last, urllib.error.HTTPError)
        cause = None if http else last
        if http and last.code in (500, 501):
            raise HelmutError(f"Helmut meldet einen Fehler bei {method} {path} ({last.code})",
                              status=last.code) from None
        log.warning("Helmut nicht erreichbar: %s %s (%s)", method, path, f"HTTP {last.code}" if http else last)
        status = last.code if http else None
        # Offen ist es, wenn die Anfrage hinausging und die Antwort ausblieb (Zeitlimit, Abbruch, 504) – und bei 502:
        # der Proxy kann die Verbindung nach der Annahme verloren haben. Ein 503 kommt von Wartungsseite oder Proxy
        # ohne Weitergabe – Helmut hat nichts ausgeführt (Prüfer 11.11.2026). # ANNAHME: Proxy vor Helmut wie üblich.
        if not idempotent and last is not None and not not_sent(last) and status in (None, 502, 504):
            raise HelmutUnavailableError(f"Helmut hat nicht geantwortet ({method} {path}) – ob es angekommen ist, ist "
                                         "offen; bitte in Helmut nachsehen, bevor es wiederholt wird",
                                         status=status, sent_unknown=True) from cause
        raise HelmutUnavailableError(f"Helmut ist nicht erreichbar ({method} {path})", status=status) from cause

    def _reflects_secret(self, text: str) -> bool:
        """Enthält ein Antworttext ein Stück des Tokens – roh, JSON-, HTML-, URL- oder Base64-kodiert? Dann wird er
        nicht gezeigt: Er stünde sonst in Meldung, Protokoll und Nachtrag-Datei (Review 10.11.2026)."""
        if not text or not self._token:
            return False
        hay = " ".join((text, text.replace("\\/", "/"), html.unescape(text), urllib.parse.unquote(text))).lower()
        forms = (self._token, base64.b64encode(self._token.encode()).decode(),
                 base64.b64encode(f"Bearer {self._token}".encode()).decode())
        return any(f[i:i + 8].lower() in hay for f in forms for i in range(max(1, len(f) - 7)))

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
