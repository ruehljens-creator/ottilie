"""Fertigmeldung an Helmut (Betriebsart Übergabeordner), wenn Export und Kopieren abgeschlossen sind.

Die API dafür ist noch nicht festgelegt (Jens 11.11.2026) – deshalb generisch: Adresse, Methode, Zeitlimit,
Wiederholungen, Anmeldung und eine Body-Vorlage mit Platzhaltern aus den Einstellungen. Standard-Vorlage
„ottilie-fertigmeldung/1“ wie in der Spezifikation für den Helmut-Entwickler. Jede Lieferung hat eine `meldung_id`;
eine Wiederholung (auch aus dem Nachtrag) schickt dieselbe – der Dienst kann Doppel daran erkennen.

Fehler kommen als Helmut-Fehler zurück, damit Zurückliefern und Nachtrag sie wie gewohnt behandeln: nicht erreichbar
(Netz, Zeitlimit, 5xx) → `HelmutUnavailableError` (nachtragen), abgelehnt (andere Codes) → `HelmutError`,
Zugangsdaten fehlen → `HelmutAuthError`. Geheimnisse nur aus dem Schlüsselbund, nie in Meldung oder Protokoll.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tonmischung.helmut.client import HelmutAuthError, HelmutError, HelmutUnavailableError

log = logging.getLogger("tonmischung.fertigmeldung")

FORMAT = "ottilie-fertigmeldung/1"
EVENT_DONE, EVENT_TEST = "audio_export_fertig", "test"
AUTH_NONE, AUTH_BEARER, AUTH_BASIC, AUTH_KEY = "keine", "bearer", "basic", "api_key"
METHODS = ("POST", "PUT")
DEFAULT_TEMPLATE: dict = {
    "ereignis": "{ereignis}", "format": FORMAT, "meldung_id": "{meldung_id}", "zeit": "{zeit}",
    "beitrag": {"id": "{projekt_id}", "titel": "{titel}", "uebergabe_nr": "{uebergabe_nr}",
                "aktuelle_uebergabe_nr": "{aktuelle_uebergabe_nr}"},
    "herkunft": {"schnittraum": "{schnittraum}"}, "mischung": {"mischer": "{mischer}", "raum": "{raum}"},
    "film_ordner": "{film_ordner}", "export_ordner": "{export_ordner}", "metadaten_json": "{metadaten_json_pfad}",
    "dateien": "{dateien}"}
# uebergabe_nr = gemischte Fassung, aktuelle_uebergabe_nr = zuletzt übergebene (beide bis der Helmut-Entwickler
# etwas anderes sagt; Empfehlung der Sitzung „Ottilie auf Mac Mini“, 11.11.2026)
PLACEHOLDERS = ("ereignis", "meldung_id", "zeit", "projekt_id", "titel", "uebergabe_nr", "aktuelle_uebergabe_nr",
                "schnittraum", "mischer",
                "raum", "film_ordner", "export_ordner", "metadaten_json_pfad", "dateien")
_ONLY = re.compile(r"\{([a-z_]+)\}")


class NotifyPending(HelmutUnavailableError):
    """Fertigmeldung nicht zugestellt (Dienst weg): im Nachtrag wiederholen, ohne die anderen Einträge aufzuhalten –
    der Übergabeordner selbst ist erreichbar (Review 4, Ausfall F3)."""
    deferrable = True


@dataclass(frozen=True)
class NotifyConfig:
    url: str = ""
    method: str = "POST"
    timeout_s: float = 15.0
    retries: int = 2
    auth: str = AUTH_NONE
    key_header: str = "X-API-Key"
    keychain_entry: str = "ottilie-fertigmeldung"
    template: Any = field(default_factory=lambda: DEFAULT_TEMPLATE)
    success: tuple[str, ...] = ("2xx",)


def fill(template: Any, values: Mapping[str, Any]) -> Any:
    """Platzhalter einsetzen. Steht ein Platzhalter allein im Text („{dateien}“), wird der Wert mit seinem Typ
    eingesetzt (Liste, Zahl); sonst als Text. Unbekannte Platzhalter bleiben stehen."""
    if isinstance(template, dict):
        return {k: fill(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [fill(v, values) for v in template]
    if isinstance(template, str):
        whole = _ONLY.fullmatch(template)
        if whole and whole.group(1) in values:
            return values[whole.group(1)]
        return _ONLY.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)
    return template


def is_success(status: int, patterns: Sequence[str]) -> bool:
    """„2xx“, „200“, „202“ … – ohne Angabe gilt 2xx."""
    for p in patterns or ("2xx",):
        p = str(p).strip().lower()
        if len(p) == 3 and p.endswith("xx") and p[0].isdigit() and status // 100 == int(p[0]):
            return True
        if p.isdigit() and status == int(p):
            return True
    return False


def _headers(config: NotifyConfig, credentials: Callable[[], tuple[str, str]] | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.auth == AUTH_NONE:
        return headers
    if credentials is None:
        raise HelmutAuthError("Fertigmeldung: Anmeldung eingestellt, aber kein Schlüsselbund-Eintrag")
    account, secret = credentials()
    if not secret or any(c in secret for c in "\r\n"):
        raise HelmutAuthError(f"Fertigmeldung: Schlüsselbund-Eintrag „{config.keychain_entry}“ leer oder unzulässig")
    if config.auth == AUTH_BEARER:
        headers["Authorization"] = f"Bearer {secret}"
    elif config.auth == AUTH_BASIC:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{account}:{secret}".encode()).decode()
    elif config.auth == AUTH_KEY:
        name = config.key_header.strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", name):
            raise HelmutError("Fertigmeldung: Name des API-Key-Headers unzulässig")
        headers[name] = secret
    else:
        raise HelmutError(f"Fertigmeldung: unbekannte Anmeldung „{config.auth}“")
    return headers


def local_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def shown_url(url: str) -> str:
    """Adresse zum Anzeigen (auch über MCP): nur Schema, Rechner und Port – in Benutzer, Pfad und Abfrageteil stecken
    bei Webhooks oft Schlüssel (Sicherheit 5)."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    netloc = host + (f":{parts.port}" if parts.port else "")
    path = "/…" if parts.path.strip("/") else ""
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, "…" if parts.query else "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keine Weiterleitung: Anmeldedaten gingen sonst an eine andere Adresse, und ein POST würde zum GET."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(config: NotifyConfig, body: Any, credentials: Callable[[], tuple[str, str]] | None = None,
         opener: Any = None, sleep: Callable[[float], None] = time.sleep) -> int:
    """Meldung schicken; Rückgabe: Statuscode. Wiederholt bei Netzfehlern und 5xx (die `meldung_id` bleibt gleich)."""
    if not config.url.startswith(("https://", "http://")):
        raise HelmutError("Fertigmeldung: keine Adresse eingestellt (http:// oder https://)")
    if config.auth != AUTH_NONE and config.url.startswith("http://") and not local_url(config.url):
        raise HelmutError("Fertigmeldung: Mit Anmeldung nur über https:// – sonst ginge das Geheimnis unverschlüsselt "
                          "durchs Netz")
    method = config.method.upper() if config.method.upper() in METHODS else "POST"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = _headers(config, credentials)
    opener = opener or urllib.request.build_opener(_NoRedirect)     # Systemproxy wie beim Helmut-Client
    last = "keine Antwort"
    status: int | None = None
    for attempt in range(max(0, int(config.retries)) + 1):
        if attempt:
            sleep(float(attempt))
        req = urllib.request.Request(config.url, data=data, method=method, headers=headers)
        try:
            with opener.open(req, timeout=config.timeout_s) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last, status = type(getattr(e, "reason", e)).__name__, None
            continue
        if is_success(status, config.success):
            log.info("Fertigmeldung %s → %s", method, status)
            return status
        if status >= 500:
            last = f"HTTP {status}"
            continue
        raise HelmutError(f"Fertigmeldung abgelehnt ({status})", status=status)
    log.warning("Fertigmeldung nicht zugestellt: %s", last)
    raise HelmutUnavailableError(f"Fertigmeldung: Dienst nicht erreichbar ({last})", status=status)
