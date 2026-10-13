"""Bausteine aus den Einstellungen – ein Ort für Kommandozeile, App, MCP-Server und Abläufe.

Vorher lagen sie als private Helfer in `cli.py` und wurden von App, MCP-Server und Havarie mitbenutzt (Durchsicht
13.10.2026). Die Funktionen nehmen die Einstellungen als Objekt mit `get(pfad)` – `Settings` oder ein schlichtes
Objekt um eine `get`-Funktion.
"""
from __future__ import annotations

import getpass
import logging
import socket
from collections.abc import Sequence
from typing import Any, Protocol

from tonmischung.config.settings import Settings, read_managed
from tonmischung.core.identity import current_mixer, this_room
from tonmischung.core.timecode import PROGRAM_START
from tonmischung.helmut.client import HelmutClient, keychain_credentials
from tonmischung.ptsl.client import PtslClient
from tonmischung.punch.settings_bridge import export_tracks as table_tracks

log = logging.getLogger("tonmischung.factory")


class SettingsLike(Protocol):
    def get(self, path: str) -> Any: ...


def load_settings() -> Settings:
    """Einstellungen dieses Macs; verwaltete Werte der Geräteverwaltung, wenn PyObjC da ist."""
    try:
        managed = read_managed()
    except ImportError:   # ohne PyObjC (z. B. in Tests) keine verwalteten Einstellungen
        managed = None
    return Settings(managed=managed)


def ptsl_client(s: SettingsLike) -> PtslClient:
    """PTSL-Client mit Adresse, Zeitlimit und dem Umgang mit „Missing Files“ aus den Einstellungen."""
    from tonmischung.ptsl.dialogs import make_handler
    client = PtslClient(address=s.get("system.schnittstellen.ptsl.adresse"),
                        timeout_s=float(s.get("system.schnittstellen.ptsl.zeitlimit_s")))
    client.dialog_handler = make_handler(lambda: s.get("import.import_vorschau.fehlende_dateien"))
    return client


def helmut_client(s: SettingsLike) -> HelmutClient:
    """Helmut-Client; Zugangsdaten nur aus dem Schlüsselbund."""
    return HelmutClient(s.get("system.schnittstellen.helmut.adresse"),
                        keychain_credentials(s.get("system.schnittstellen.helmut.schluesselbund_eintrag")),
                        login_method=s.get("system.schnittstellen.helmut.anmeldeverfahren") or "passwort")


def identity(s: SettingsLike) -> tuple[str, str]:
    """(Mischer, Raum) dieses Macs; ohne zugeordneten Raum der Rechnername."""
    p = "system.raeume_nutzer."
    room = this_room(s.get(p + "raeume") or [], socket.gethostname(),
                     s.get(p + "raum_erkennung"), s.get(p + "raum_fest") or "")
    mixer = current_mixer(s.get(p + "nutzer") or [], getpass.getuser(), s.get(p + "nutzer_modus"), "")
    return mixer, room or socket.gethostname()


def program_start(s: SettingsLike) -> str:
    """Programmbeginn in Pro Tools (Einstellung, sonst Haus-Standard 10:00:00:00)."""
    return str(s.get("import.timecode_startband.programmbeginn") or PROGRAM_START)


def export_tracks(s: SettingsLike, given: str | Sequence[str] | None = None) -> tuple[list[str], list[str]]:
    """(Exportspuren, Spuren mit R 128) aus der Spurtabelle bzw. dem Spurprofil. `given` (Liste oder kommagetrennt)
    ersetzt die Exportspuren; R 128 gilt dann nur für die gegebenen."""
    export, loud = table_tracks(s)
    if given:
        names = given.split(",") if isinstance(given, str) else list(given)
        export = [t.strip() for t in names if str(t).strip()]
        loud = [t for t in loud if t in export]
    return export, loud


def export_names(s: SettingsLike) -> dict[str, str]:
    """Exportname je Exportspur (Spalte „exportname“ der Spurtabelle bzw. des Spurprofils)."""
    return {r["pt_spur"]: str(r["exportname"]) for r in s.get("export.spuren_lautheit.spurtabelle") or []
            if r.get("export") and r.get("pt_spur") and r.get("exportname")}


def triage_options(s: SettingsLike, percent: int | None = None):
    """Triage-Einstellungen (import.zuordnungsregeln.*, Vorschau aus import.import_vorschau)."""
    from tonmischung.workflow.fetch import TriageOptions        # spät: Abläufe ziehen Pro-Tools- und AAF-Module
    p = "import.zuordnungsregeln."
    cascade = {r.get("id"): r.get("aktiv", True) for r in s.get(p + "kaskade") or []}
    percent = percent if percent is not None else s.get(p + "mindest_sicherheit")
    return TriageOptions(
        threshold=float(percent) / 100,
        keywords={k: s.get(f"{p}schluesselwoerter.{k}") or [] for k in ("o_ton", "atmo", "musik")},
        music_paths=s.get(p + "musik_pfade") or [],
        use_track_names=cascade.get("premiere_spurname", True),
        use_source_names=cascade.get("quelldatei_herkunft", True),
        use_signal=cascade.get("inhaltserkennung", True),
        preview=s.get("import.import_vorschau.triage_vorschau") or "nur_bei_unsicherheit",
        ranges=_ranges(s))


def _ranges(s: SettingsLike):
    """Zielspuren aus den Einstellungen; unlesbar → Hausstandard mit Warnung im Protokoll."""
    from tonmischung.triage.placement import TargetRanges, ranges_from_settings
    try:
        return ranges_from_settings(s)
    except (ValueError, TypeError) as e:
        log.warning("Zielspuren in den Einstellungen unlesbar (%s) – Hausstandard", e)
        return TargetRanges()
