"""Bausteine aus den Einstellungen – ein Ort für Kommandozeile, App, MCP-Server und Abläufe.

Vorher lagen sie als private Helfer in `cli.py` und wurden von App, MCP-Server und Havarie mitbenutzt (Durchsicht
13.10.2026). Die Funktionen nehmen die Einstellungen als Objekt mit `get(pfad)` – `Settings` oder ein schlichtes
Objekt um eine `get`-Funktion.
"""
from __future__ import annotations

import getpass
import logging
import os
import socket
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from tonmischung.config.settings import Settings, read_managed
from tonmischung.core.identity import current_mixer, short_host
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


FOLDER = "system.uebergabeordner."


def operating_mode(s: SettingsLike) -> str:
    """„helmut_api“ (Standard) oder „uebergabeordner“ (Jens 11.11.2026)."""
    from tonmischung.dropfolder.client import MODE_API, MODE_FOLDER
    return MODE_FOLDER if s.get(FOLDER + "betriebsart") == MODE_FOLDER else MODE_API


def folder_config(s: SettingsLike, entry: dict | None = None):
    """Einstellungen der Betriebsart Übergabeordner. `entry`: ein weiterer Übergabeordner (Name, Pfad, Netzadresse,
    Anmeldung) – alles andere gilt für alle Ordner gleich."""
    from tonmischung.dropfolder.client import FolderConfig
    from tonmischung.dropfolder.notify import DEFAULT_TEMPLATE, NotifyConfig
    n = FOLDER + "fertigmeldung."
    notify = NotifyConfig(url=str(s.get(n + "url") or "").strip(), method=str(s.get(n + "methode") or "POST"),
                          timeout_s=float(s.get(n + "zeitlimit_s") or 15),
                          retries=int(s.get(n + "wiederholungen") or 0),
                          auth=str(s.get(n + "anmeldung") or "keine"),
                          key_header=str(s.get(n + "api_key_header") or "X-API-Key"),
                          keychain_entry=str(s.get(n + "schluesselbund_eintrag") or "ottilie-fertigmeldung"),
                          template=s.get(n + "body_vorlage") or DEFAULT_TEMPLATE,
                          success=tuple(str(x) for x in (s.get(n + "erfolgs_statuscodes") or ["2xx"])))
    if entry is None:
        root = os.path.expanduser(str(s.get(FOLDER + "wurzelordner") or "").strip())
        net = str(s.get(FOLDER + "netzadresse") or "").strip()
        auth = str(s.get(FOLDER + "netz_anmeldung") or "gast")
        root_central = s.source(FOLDER + "wurzelordner") == "zentral"
        from_zentrale = s.source(FOLDER + "netzadresse") == "zentral"
    else:
        root = os.path.expanduser(str(entry.get("wurzelordner") or "").strip())
        net = str(entry.get("netzadresse") or "").strip()
        auth = str(entry.get("netz_anmeldung") or "gast")
        root_central = from_zentrale = s.source(FOLDER + "weitere_ordner") == "zentral"
    allowed = tuple(str(x) for x in (s.get(FOLDER + "netz_erlaubte_server") or []))
    if allowed and root_central:
        root = ""                                 # mit Serverliste (Profil) gibt die Zentrale keinen Pfad vor
    ensure_fn: Callable[..., str] | None = None
    if net:                                       # Netzadresse: tatsächlicher Einhängepunkt statt fester Pfad
        from tonmischung.config.keychain import account_and_secret
        from tonmischung.dropfolder.netmount import MOUNTER
        from tonmischung.dropfolder.netmount import MountError, parse
        try:
            host = parse(net).host.split(":")[0].lower()
        except MountError:
            host = ""
        permitted = not allowed or host in {a.strip().lower() for a in allowed}
        root = (MOUNTER.root(net) or "") if permitted else ""     # nie eine schon verbundene, nicht erlaubte
        keychain = str(s.get(FOLDER + "netz_schluesselbund_eintrag") or "ottilie-uebergabeordner")

        def _ensure(now: bool = False) -> str:
            return MOUNTER.ensure(net, auth, lambda: account_and_secret(keychain), now=now,
                                  address_from_zentrale=from_zentrale, allowed=allowed)
        ensure_fn = _ensure
    return FolderConfig(root=root,
                        handover_json=str(s.get(FOLDER + "uebergabe_json") or "uebergabe.json"),
                        wait_s=float(s.get(FOLDER + "wartezeit_s") or 0),
                        ignore=tuple(str(x) for x in (s.get(FOLDER + "ordner_ignorieren") or []) if str(x)),
                        show_transfer=bool(s.get(FOLDER + "wird_uebertragen_zeigen")),
                        lock_name=str(s.get(FOLDER + "sperrdatei") or ".ottilie-sperre.json"),
                        stale_h=float(s.get(FOLDER + "sperre_verwaist_h") or 12),
                        export_folder=str(s.get(FOLDER + "exportordner") or "Audio Export"),
                        redelivery=str(s.get(FOLDER + "erneute_lieferung") or "ersetzen"),
                        notify_active=bool(s.get(n + "aktiv")), notify=notify, netzadresse=net, ensure=ensure_fn)


def folder_client(s: SettingsLike, identity_now=None, safe_root: str = ""):
    """Ordner-Client (Betriebsart Übergabeordner); `identity_now()` → (Mischer, Raum), Standard aus den Einstellungen.
    Zugangsdaten der Fertigmeldung nur aus dem Schlüsselbund. `safe_root` (Entwicklungsmodus): der Übergabeordner muss
    darin liegen – Sperrdateien und „Audio Export“ entstehen sonst nirgends (Sicherheitsregel Testordner)."""
    from tonmischung.dropfolder.client import FolderClient
    from tonmischung.dropfolder.multi import MultiFolderClient
    who = identity_now or (lambda: identity(s))

    def make(entry: dict | None = None) -> FolderClient:
        return FolderClient(lambda: folder_config(s, entry), who, credentials=keychain_credentials, safe_root=safe_root)
    return MultiFolderClient(make(), lambda: list(s.get(FOLDER + "weitere_ordner") or []), make,
                             lambda: str(s.get(FOLDER + "name") or ""))


def mode_client(s: SettingsLike) -> Any:
    """Client in der Betriebsart dieses Macs (Helmut-API oder Übergabeordner) – für die Kommandozeile."""
    from tonmischung.dropfolder.mode import ModeClient
    return ModeClient(helmut_client(s), lambda: folder_client(s, lambda: identity(s)), lambda: operating_mode(s))


def queue_target(client: Any) -> tuple[str, str]:
    """(Betriebsart, Fingerabdruck der Fertigmeldung) für neue Nachtrag-Einträge (`CatchUpQueue.target`)."""
    from tonmischung.dropfolder.client import MODE_API, MODE_FOLDER
    mode = getattr(client, "mode", "") or MODE_API
    return mode, (client.notify_target() if mode == MODE_FOLDER else "")


def identity(s: SettingsLike, central: bool = False) -> tuple[str, str]:
    """(Mischer, Raum) dieses Macs. Raum: „Raumname dieses Macs“, sonst der Rechnername; gekoppelt ersetzt ihn der
    Raum der Zentrale – in der App über `zentrale_app.initial_room`/`adopt_room`, auf der Kommandozeile mit `central`
    (Review 13.11.2026: sonst schrieb die Kommandozeile einen anderen Raum als die App)."""
    p = "system.raeume_nutzer."
    room = str(s.get(p + "raumname") or "").strip()
    mixer = current_mixer(s.get(p + "nutzer") or [], getpass.getuser(), s.get(p + "nutzer_modus"), "")
    if central:
        room = central_room(s) or room
    return mixer, room or short_host(socket.gethostname())


def central_room(s: SettingsLike) -> str:
    """Zuletzt bekannter Raum der Zentrale für diesen Mac ("" ohne Kopplung bzw. ohne bekannten Raum)."""
    from tonmischung.paths import support_dir
    from tonmischung.zentrale import device as D
    from tonmischung.zentrale import state as S
    dev = D.read()
    if dev is None:
        return ""
    return str(S.read(machine_dir(s)[0], S.ROOM, dev.fingerprint, legacy=support_dir()).get("raum") or "")


def machine_dir(s: SettingsLike) -> tuple[Path, str, list[str]]:
    """(gemeinsamer Datenordner, Hinweis wenn nicht nutzbar, Meldungen zum Umzug) – Auftrag „rechnerweit“."""
    from tonmischung.core import machine_data as MD
    from tonmischung.paths import support_dir
    folder, hint = MD.location(str(s.get(MD.SETTING) or ""), support_dir())
    notes = [] if hint else MD.migrate(support_dir(), folder)
    return folder, hint, notes


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


def track_table(s: SettingsLike) -> list[dict]:
    """Spurtabelle (Zeile = Kanalpaar im Sendefile) bzw. die des Spurprofils."""
    return s.get("export.spuren_lautheit.spurtabelle") or []


def export_names(s: SettingsLike) -> dict[str, str]:
    """Exportname je Exportspur (Spalte „exportname“ der Spurtabelle bzw. des Spurprofils)."""
    return {r["pt_spur"]: str(r["exportname"]) for r in track_table(s)
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
