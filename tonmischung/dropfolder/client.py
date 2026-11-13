"""`FolderClient`: Helmut-Client-Ersatz für die Betriebsart Übergabeordner.

Dieselben Aufrufe wie `helmut.client.HelmutClient` (Liste, Beitrag, Übergabe, Set schreiben, sperren, Asset), damit
`MixService`, Holen, Zurückliefern, Nachtrag und Raumübersicht unverändert laufen:

- Beitrag = Filmordner mit Übergabe-JSON (`handover`), Stand der Mischung = Sperrdatei (`lockfile`).
- `set_locked(True)` legt die Sperrdatei exklusiv an, `write_mix` schreibt den Stand hinein, `set_locked(False)`
  setzt „gesperrt: false“ (nichts wird gelöscht).
- `add_asset(…, "metadaten")` – die Metadaten-Datei der Auslieferung, angemeldet als letzte Datei – löst die
  Fertigmeldung aus (`notify`); WAVs und Session brauchen keine Anmeldung.
- Benachrichtigen und Duplizieren gibt es nur mit der Helmut-API (Jens 11.11.2026: ausblenden).
- Fehler wie beim Helmut-Client: Ordner nicht erreichbar → `HelmutUnavailableError` (Nachtrag), belegt → `HelmutError`
  (409), Filmordner fehlt → `HelmutError` (404).
"""
from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from tonmischung.core import mixstate
from tonmischung.core.fileio import CorruptFileError, drop_appledouble, inside, set_aside, sweep_appledouble
from tonmischung.core.mixstate import MixState
from tonmischung.dropfolder import handover as H
from tonmischung.dropfolder import lockfile as L
from tonmischung.dropfolder import notify as N
from tonmischung.helmut.client import HelmutError, HelmutUnavailableError
from tonmischung.helmut.mapping import Contribution, Handover, handover_from_paths
from tonmischung.workflow.deliver import DeliveryTarget

log = logging.getLogger("tonmischung.uebergabeordner")

MODE_API, MODE_FOLDER = "helmut_api", "uebergabeordner"
REPLACE, STAMPED = "ersetzen", "zeitstempel"
OLD = "_alt"
STAGING_PREFIX = "_in_arbeit-"
ONLY_API = "In der Betriebsart Übergabeordner nicht verfügbar (nur mit der Helmut-API)"


@dataclass(frozen=True)
class FolderConfig:
    root: str = ""
    handover_json: str = "uebergabe.json"
    wait_s: float = 3.0
    ignore: tuple[str, ...] = ("_", ".")
    show_transfer: bool = True
    lock_name: str = ".ottilie-sperre.json"
    stale_h: float = 12.0
    export_folder: str = "Audio Export"
    redelivery: str = REPLACE
    notify_active: bool = False
    notify: N.NotifyConfig = N.NotifyConfig()
    netzadresse: str = ""                         # smb://… – Ottilie bindet selbst ein (Nachtrag 11.11.2026)
    ensure: Callable[..., str] | None = None      # einbinden → Übergabeordner, sonst MountError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _later(a: str, b: str) -> bool:
    """Ist Zeitpunkt `a` (ISO) später als `b`? Unlesbares zählt nicht als später."""
    try:
        return datetime.fromisoformat(a) > datetime.fromisoformat(b)
    except (TypeError, ValueError):
        return False


def _int(value: Any, default: int = 0) -> int:
    """Zahl aus fremden Daten (Sperrdatei eines anderen Macs) – Unsinn wird zum Standard, nie zum Absturz."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10_000 else default


UNKNOWN_OWNER = MixState(state=mixstate.TAKEN, mixer="unbekannt", room="Sperrdatei unlesbar")
BROKEN_AFTER_S = 60.0          # unlesbare Übergabe-JSON so lange als „wird übertragen“, danach „fehlerhaft“


def _os_error(e: OSError, what: str) -> HelmutError:
    """Dateifehler beim Schreiben im Filmordner als Meldung mit Klartext (Ausfall F7): voll und schreibgeschützt sind
    keine Ausfälle zum Nachtragen."""
    if e.errno == errno.ENOSPC:
        return HelmutError(f"{what}: Speicher voll", status=507)
    if e.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return HelmutError(f"{what}: keine Schreibrechte im Filmordner", status=403)
    return HelmutUnavailableError(f"{what}: Filmordner nicht erreichbar ({e.strerror or e})")


class FolderClient:
    mode = MODE_FOLDER

    def __init__(self, config: Callable[[], FolderConfig], identity: Callable[[], tuple[str, str]],
                 credentials: Callable[[str], Callable[[], tuple[str, str]]] | None = None,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], str] = _utc_now,
                 send: Callable[..., int] = N.send, safe_root: str = "", wall: Callable[[], float] = time.time) -> None:
        self._config, self._identity, self._safe_root = config, identity, safe_root
        self._credentials = credentials
        self._clock, self._now, self._send, self._wall = clock, now, send, wall
        # JSON-Pfad → (mtime/Größe, erstmals gesehen, Inhalt oder None, Fehlergrund)
        self._seen: dict[str, tuple[tuple[int, int], float, dict | None, str]] = {}
        self.last_ok: float | None = None
        self.central: Any = None                    # Zentrale (Stufe 2): Belegen, Freigeben, Ereignisse – optional

    # ── Ordner lesen ────────────────────────────────────────────────────────
    @property
    def config(self) -> FolderConfig:
        return self._config()

    def connect(self, now: bool = False) -> None:
        """Netzadresse eingestellt und Freigabe nicht verbunden: einbinden (⌘R: `now` übergeht den Abstand)."""
        cfg = self.config
        if cfg.ensure is None or (cfg.root and os.path.isdir(cfg.root) and not now):
            return
        from tonmischung.dropfolder.netmount import MountError
        try:
            cfg.ensure(now=now)
        except MountError as e:
            raise HelmutUnavailableError(f"Übergabeordner nicht verbunden: {e}") from None

    def reachable(self, max_age_s: float = 30.0, timeout_s: float = 3.0) -> None:
        self.connect()
        cfg = self.config
        if not cfg.root.strip():
            raise HelmutError("Kein Übergabeordner eingestellt (Einstellungen → System → Übergabeordner)")
        if self._safe_root and not inside(cfg.root, self._safe_root):
            raise HelmutError(f"Im Entwicklungsmodus nur im Testordner – {cfg.root} liegt außerhalb")
        if not os.path.isdir(cfg.root) or not os.access(cfg.root, os.R_OK | os.X_OK):
            raise HelmutUnavailableError(f"Übergabeordner nicht erreichbar ({cfg.root}) – ist der Speicher im Finder "
                                         "verbunden?")
        self.last_ok = self._clock()

    def writable(self) -> bool:
        return os.access(self.config.root, os.W_OK)

    def check_auth(self, timeout_s: float | None = None) -> None:
        self.reachable()

    def _parsed(self, json_path: str, wait_s: float) -> tuple[dict | None, str]:
        """(Inhalt, Fehler) der Übergabe-JSON. Erst lesen, wenn sie seit `wait_s` unverändert ist (seit dem Entdecken
        oder laut Änderungszeit – nach einem Neustart sind vorhandene Übergaben sofort da). Unlesbar → beim nächsten
        Durchlauf erneut; bleibt sie `BROKEN_AFTER_S` unverändert unlesbar, kommt der Grund."""
        try:
            st = os.lstat(json_path)
        except (OSError, ValueError):
            self._seen.pop(json_path, None)
            return None, ""
        key = (st.st_mtime_ns, st.st_size)
        seen = self._seen.get(json_path)
        if seen is None or seen[0] != key:
            seen = (key, self._clock(), None, "")
            self._seen[json_path] = seen
        age = max(self._clock() - seen[1], self._wall() - st.st_mtime)
        if age < wait_s:
            return None, ""
        if seen[2] is not None or seen[3]:
            return seen[2], (seen[3] if self._clock() - seen[1] >= BROKEN_AFTER_S else "")
        try:
            raw = H.read_regular(json_path)
            if raw is None:
                raise H.HandoverError("fehlt")
            parsed = H.parse(json.loads(raw))
        except Exception as e:                       # fremde Daten: nie die ganze Liste kosten (Sicherheit 2)
            log.info("Übergabe-JSON %s nicht lesbar: %s", json_path, e)
            why = (str(e) if isinstance(e, H.HandoverError) else e.strerror if isinstance(e, H.NotRegular)
                   else "kein lesbares JSON")
            self._seen[json_path] = (key, seen[1], None, why)
            return None, ""
        self._seen[json_path] = (key, seen[1], parsed, "")
        return parsed, ""

    def _film(self, folder: str, cfg: FolderConfig) -> H.FilmFolder:
        try:
            parsed, broken = self._parsed(os.path.join(folder, cfg.handover_json), cfg.wait_s)
            if broken:
                return H.unreadable(folder, f"Übergabe-Datei fehlerhaft: {broken}")
            return H.in_transfer(folder) if parsed is None else H.check(folder, parsed, H.regular_size)
        except Exception as e:                       # noqa: BLE001 – ein kaputter Ordner, nicht die Liste
            log.warning("Filmordner %s nicht auswertbar: %s", folder, e)
            return H.unreadable(folder, "Übergabe nicht auswertbar")

    def _films(self) -> list[H.FilmFolder]:
        cfg = self.config
        self.reachable()
        try:
            names = sorted(os.listdir(cfg.root))
        except OSError as e:
            raise HelmutUnavailableError(f"Übergabeordner nicht lesbar ({e.strerror or e})") from None
        films = []
        for name in names:
            folder = os.path.join(cfg.root, name)
            # Symlinks nicht: sie führten aus dem Übergabeordner hinaus (Sicherheit 1)
            if H.ignored(name, cfg.ignore, cfg.export_folder) or os.path.islink(folder) or not os.path.isdir(folder):
                continue
            film = self._film(folder, cfg)
            if film.state != H.TRANSFER or cfg.show_transfer or self._lock_data(folder, cfg)[0]:
                films.append(film)
        return films

    def _lock_data(self, folder: str, cfg: FolderConfig) -> tuple[dict | None, bool]:
        """(Inhalt der Sperrdatei, unlesbar?). Beschädigt → unlesbar (belegt von unbekannt); ein Lesefehler des
        Speichers (EIO, Zeitlimit, Rechte) ist dagegen ein Ausfall, kein fremder Besitzer (Review 5)."""
        try:
            return L.read(os.path.join(folder, cfg.lock_name)), False
        except CorruptFileError:
            return None, True
        except OSError as e:
            raise HelmutUnavailableError(f"Sperrdatei nicht lesbar ({e.strerror or e})") from None

    def _contribution(self, film: H.FilmFolder, cfg: FolderConfig, broken: bool = False) -> Contribution:
        """ID = Ordnername – fest, auch wenn die Übergabe-JSON fehlt, sich ändert oder eine Beitrags-ID doppelt
        vorkommt (Review 1, Ausfall F1). Die Beitrags-ID aus der JSON geht in Metadaten-Datei und Fertigmeldung."""
        pid = film.name
        data, corrupt = (None, True) if broken else self._lock_data(film.folder, cfg)
        # Unlesbare Sperrdatei: belegt von unbekannt, nicht „ohne Besitzer“ – sonst übernähme ein zweiter Raum einen
        # Film, an dem gerade gemischt wird (Review 2). Lösen nur von Hand (Freigeben).
        mix = UNKNOWN_OWNER if corrupt else L.mix_of(data)
        is_locked = corrupt or L.locked(data)
        # Auch nach dem Ausliefern durch diesen Raum: ein erneutes Zurückliefern schickte sonst die alte Mischung zur
        # neuen Fassung (live 11.11.2026)
        mine = mix.state in mixstate.OWNED or mixstate.delivered_by(mix, *self._identity())
        stale = False
        if is_locked and data and data.get("zeit"):
            try:
                since = datetime.fromisoformat(str(data["zeit"]))
                stale = datetime.now(timezone.utc) - since > timedelta(hours=cfg.stale_h)
            except (TypeError, ValueError, OverflowError):
                stale = False
        mixed_nr = int(mix.handover_nr) if (not corrupt and mix.handover_nr.isdigit()) else 0
        new_handover = mine and not corrupt and (film.state == H.TRANSFER or (mixed_nr and film.number > mixed_nr))
        info = film.info
        bid = film.beitrag_id
        # „path“ = Projektordner für die Session-Ablage ({projekt}): der Filmordner. # ANNAHME: Spiegel der Session
        # nach „<Filmordner>/Ton/ProTools/“ wie im Helmut-Projektordner (Einstellung ordner_im_projekt)
        raw = {"id": pid, "name": film.display_title, "betriebsart": MODE_FOLDER, "ordner": film.folder,
               "path": film.folder,
               "ordner_name": film.name, "uebergabe": film.state, "uebergabe_text": H.STATE_TEXT[film.state],
               "probleme": list(film.problems), "schnittraum": film.edit_room, "herkunft": dict(film.origin),
               "uebergabe_nr": film.number, "gemischte_nr": mixed_nr or film.number,
               "beitrag_id": bid if bid and H.SAFE_ID.fullmatch(bid) else pid,
               "sendetermin": info.get("sendetermin", ""), "laenge": info.get("laenge", ""),
               "cutter": info.get("cutter", ""), "urgent": "True" if info.get("dringend") is True else "",
               "videoid": info.get("videoid", ""), "createDate": film.created,
               "sperre_alt": stale, "sperre_unlesbar": corrupt, "neue_uebergabe": bool(new_handover)}
        return Contribution(pid, film.display_title, film.show, is_locked, mix, raw)

    def list_for_mixing(self) -> list[Contribution]:
        cfg = self.config
        out = []
        for f in self._films():
            try:
                out.append(self._contribution(f, cfg))
            except Exception as e:                   # noqa: BLE001 – ein kaputter Film, nicht die Liste (Sicherheit B)
                log.warning("Filmordner %s nicht auswertbar: %s", f.folder, e)
                out.append(self._contribution(H.unreadable(f.folder, "Sperrdatei nicht auswertbar"), cfg, broken=True))
        return out

    def folder_of(self, project_id: str) -> str:
        """Filmordner zur ID (= Ordnername)."""
        cfg = self.config
        self.reachable()
        name = str(project_id)
        folder = os.path.join(cfg.root, name)
        if (not name or os.path.basename(name) != name or name in (".", "..")
                or H.ignored(name, cfg.ignore, cfg.export_folder) or os.path.islink(folder)
                or not os.path.isdir(folder)):
            raise HelmutError(f"Filmordner „{project_id}“ nicht gefunden", status=404)
        return folder

    def get(self, project_id: str) -> Contribution:
        cfg = self.config
        return self._contribution(self._film(self.folder_of(project_id), cfg), cfg)

    def handover(self, project_id: str) -> Handover:
        film = self._film(self.folder_of(project_id), self.config)
        if film.state != H.READY:
            return Handover(None, None, None)
        return replace(handover_from_paths(film.aaf, film.video), number=film.number)

    def assets(self, project_id: str) -> list[dict]:
        h = self.handover(project_id)
        return [{"filepath": a.path, "name": a.name} for a in (h.aaf, h.video) if a is not None]

    # ── Schreiben ───────────────────────────────────────────────────────────
    def _lock_path(self, project_id: str) -> str:
        return os.path.join(self.folder_of(project_id), self.config.lock_name)

    def write_mix(self, contribution: Contribution, mix: MixState) -> Contribution:
        """Stand in die Sperrdatei – nur, wenn der Film noch dem gehört, den der Aufrufer eben gelesen hat (unter der
        Hilfsdatei geprüft; Ausfall F9). Die gemischte Fassung steht im Stand (`handover_nr`, gesetzt beim Import)."""
        path = self._lock_path(contribution.id)
        seen = contribution.mix

        def change(current: dict | None) -> dict:
            now = L.mix_of(current)
            if now.state in mixstate.OWNED and (now.mixer, now.room) != (seen.mixer, seen.room):
                raise HelmutError(f"Film inzwischen bei {now.mixer or 'jemand'} ({now.room or '?'})", status=409)
            nr = int(mix.handover_nr) if mix.handover_nr.isdigit() else 0
            room, mixer = (mix.room, mix.mixer) if mix.mixer else self._identity()[::-1]
            # nach dem Beiseitelegen einer beschädigten Datei: gesperrt, solange belegt (Review 8)
            lock = L.locked(current) if current is not None else mix.state in mixstate.OWNED
            return {**(current or {}), **L.content(mix, lock, room, mixer, self._now(), nr)}
        try:
            try:
                L.update(path, change)
            except CorruptFileError:
                set_aside(path)                 # beschädigte Sperrdatei beiseite, nie überschreiben
                L.update(path, change)
        except L.LockTaken as e:
            raise HelmutUnavailableError(f"Sperrdatei: {e}") from None
        except OSError as e:
            raise _os_error(e, "Sperrdatei nicht geschrieben") from None
        self._tell_central(contribution, mix)
        return self.get(contribution.id)

    def _tell_central(self, contribution: Contribution, mix: MixState) -> None:
        """Zustandswechsel an die Zentrale (Systemmeldungen, „Meine Beiträge“) – nie störend."""
        central = self.central
        if central is None or seen_state(contribution) == mix.state:
            return
        origin = str((contribution.raw or {}).get("schnittraum") or "")
        try:
            if mix.state in (mixstate.IMPORTED, mixstate.MIXING):
                central.event("in_mischung", contribution.id, contribution.title, origin)
            elif mix.state == mixstate.DELIVERED:
                central.event("audio_export_fertig", contribution.id, contribution.title, origin)
            elif mix.state == mixstate.RETURNED:
                central.release(contribution.id, contribution.title, mix.return_reason)
        except Exception as e:  # noqa: BLE001
            log.info("Zentrale nicht benachrichtigt: %s", e)

    def set_locked(self, project_id: str, locked: bool) -> None:
        path = self._lock_path(project_id)
        try:
            if locked:
                if self.central is not None:          # Zentrale zuerst: wer zuerst fragt, gewinnt (Stufe 2)
                    from tonmischung.zentrale.hooks import Refused
                    c = self.get(project_id)
                    try:
                        self.central.claim(project_id, c.title, str((c.raw or {}).get("schnittraum") or ""))
                    except Refused as e:
                        raise HelmutError(f"Film ist {e}", status=409) from None    # „schon in … belegt …“
                mixer, room = self._identity()

                def data(current: dict | None) -> dict:
                    mix = L.mix_of(current)
                    nr = int(mix.handover_nr) if mix.handover_nr.isdigit() else 0
                    return {**(current or {}), **L.content(mix, True, room, mixer, self._now(), nr)}
                try:
                    L.claim(path, data)
                except BaseException as e:
                    if self.central is not None:      # Zentrale gewonnen, Sperrdatei nicht: dort zurücknehmen
                        try:
                            self.central.release(project_id, "", "Sperrdatei nicht geschrieben")
                        except Exception:  # noqa: BLE001
                            pass
                    if isinstance(e, L.LockTaken):
                        raise HelmutError(f"Film ist belegt: {e}", status=409) from None
                    raise
                return

            def unlock(current: dict | None) -> dict | None:
                # Nie die Sperre eines übernommenen Films lösen – sie gehört dann dem, der übernommen hat (Ausfall F9)
                if current is None or not L.locked(current) or L.mix_of(current).state in mixstate.OWNED:
                    return None
                return {**current, "gesperrt": False, "zeit": self._now()}
            try:
                L.update(path, unlock)
            except CorruptFileError:
                return                          # unlesbar: nichts überschreiben (gilt weiter als belegt)
            except L.LockTaken as e:
                raise HelmutUnavailableError(f"Sperrdatei: {e}") from None
            if self.central is not None:
                try:
                    c = self.get(project_id)
                    if c.mix.state not in mixstate.OWNED:
                        self.central.release(project_id, c.title, "", delivered=c.mix.state == mixstate.DELIVERED)
                except Exception as e:  # noqa: BLE001 – die Zentrale stört den Ablauf nie
                    log.info("Zentrale: Freigabe nicht gemeldet (%s)", e)
        except OSError as e:
            raise _os_error(e, "Sperrdatei nicht geschrieben") from None

    def notify_target(self) -> str:
        """Fingerabdruck des Fertigmeldungs-Ziels ("" = abgeschaltet) – ein Nachtrag-Eintrag wird nur von einem
        Prozess mit demselben Ziel nachgetragen (gemeinsamer Nachtrag aller Benutzer, Ausfall 13.11.2026)."""
        cfg = self.config
        if not cfg.notify_active:
            return ""
        return hashlib.sha256(f"{cfg.notify.method} {cfg.notify.url}".encode()).hexdigest()[:12]

    def add_asset(self, project_id: str, path: str, kind: str) -> dict:
        """WAVs und Session brauchen keine Anmeldung; die Metadaten-Datei (zuletzt angemeldet) löst die
        Fertigmeldung aus. Überholt (nicht gesendet) ist sie, wenn die Datei nicht mehr da ist oder schon eine neuere
        Lieferung gemeldet wurde; schon gemeldet, wenn dieselbe `meldung_id` in der Sperrdatei steht (Ausfall F4,
        Review 2 der 2. Runde: sonst kam eine eingereihte alte Meldung nach der neuen an)."""
        if kind != "metadaten":
            return {}
        cfg = self.config
        if not cfg.notify_active:
            log.info("Fertigmeldung abgeschaltet – %s nicht gemeldet", project_id)
            return {}
        if H.regular_size(path) is None:
            log.info("Fertigmeldung für %s überholt – %s gibt es nicht mehr", project_id, path)
            return {"ueberholt": True}
        values = self.done_values(project_id, path)
        lock_path = self._lock_path(project_id)
        try:
            last = (L.read(lock_path) or {}).get("fertigmeldung")
        except (CorruptFileError, OSError):
            last = None
        if isinstance(last, dict):
            if last.get("id") == values["meldung_id"]:
                return {"schon_gemeldet": True}
            if _later(str(last.get("erstellt") or ""), values["lieferung_erstellt"]):
                log.info("Fertigmeldung für %s überholt – eine neuere Lieferung ist schon gemeldet", project_id)
                return {"ueberholt": True}
        body = N.fill(cfg.notify.template, values)
        try:
            self._send(cfg.notify, body, self._creds(cfg))
        except HelmutUnavailableError as e:
            raise N.NotifyPending(str(e), status=e.status) from None
        record = {"id": values["meldung_id"], "erstellt": values["lieferung_erstellt"], "zeit": self._now()}
        if self.central is not None:
            try:
                c = self.get(project_id)
                self.central.event("fertigmeldung", project_id, c.title, str((c.raw or {}).get("schnittraum") or ""))
            except Exception as e:  # noqa: BLE001
                log.info("Zentrale: Fertigmeldung nicht gemeldet (%s)", e)
        try:                                         # vermerken, was gemeldet ist (nur ergänzen, nie den Besitz ändern)
            L.update(lock_path, lambda cur: {**cur, "fertigmeldung": record} if cur else None)
        except (L.LockTaken, CorruptFileError, OSError) as e:
            log.warning("Fertigmeldung %s nicht in der Sperrdatei vermerkt: %s", values["meldung_id"], e)
        return {"meldung_id": values["meldung_id"]}

    def _creds(self, cfg: FolderConfig):
        if cfg.notify.auth == N.AUTH_NONE or self._credentials is None:
            return None
        return self._credentials(cfg.notify.keychain_entry)

    def done_values(self, project_id: str, meta_path: str, event: str = N.EVENT_DONE) -> dict:
        """Werte für die Platzhalter der Fertigmeldung – aus der Metadaten-Datei der Auslieferung."""
        cfg = self.config
        try:
            raw = H.read_regular(meta_path)
            meta = json.loads(raw) if raw is not None else None
        except (OSError, ValueError, RecursionError) as e:
            raise HelmutError(f"Metadaten-Datei nicht lesbar ({e})") from None
        if not isinstance(meta, dict):
            raise HelmutError("Metadaten-Datei fehlt oder ist kein JSON-Objekt")
        film = self.folder_of(project_id)
        contribution = self.get(project_id)
        files = []
        for a in meta.get("audio") if isinstance(meta.get("audio"), list) else []:
            p = str(a.get("pfad") or "") if isinstance(a, dict) else ""
            files.append({"name": os.path.basename(p), "groesse": H.regular_size(p)})
        files.append({"name": os.path.basename(meta_path), "groesse": H.regular_size(meta_path)})
        mischung = meta.get("mischung") if isinstance(meta.get("mischung"), dict) else {}
        root = cfg.root
        return {"ereignis": event,
                "meldung_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ottilie:{meta_path}:{meta.get('erstellt', '')}")),
                "zeit": self._now(), "projekt_id": contribution.raw.get("beitrag_id") or project_id,
                "titel": contribution.title, "uebergabe_nr": contribution.raw.get("gemischte_nr") or 1,
                "aktuelle_uebergabe_nr": contribution.raw.get("uebergabe_nr") or 1,
                "schnittraum": contribution.raw.get("schnittraum") or "",
                "mischer": mischung.get("mischer") or "", "raum": mischung.get("raum") or "",
                "film_ordner": os.path.relpath(film, root),
                "export_ordner": os.path.relpath(os.path.dirname(meta_path), root),
                "metadaten_json_pfad": os.path.relpath(meta_path, root), "dateien": files,
                # Reihenfolge der Lieferungen nach der Uhr des Speichers (Änderungszeit der Datei), nicht nach den Uhren
                # verschiedener Macs (Review 3, 3. Runde)
                "lieferung_erstellt": datetime.fromtimestamp(os.lstat(meta_path).st_mtime, timezone.utc).isoformat()}

    def done_message(self, project_id: str, meta_path: str, event: str = N.EVENT_DONE) -> Any:
        return N.fill(self.config.notify.template, self.done_values(project_id, meta_path, event))

    def test_message(self) -> dict:
        """„Testmeldung senden“: dieselbe Struktur mit „ereignis: test“ und Beispielwerten."""
        cfg = self.config
        mixer, room = self._identity()
        values = {"ereignis": N.EVENT_TEST, "meldung_id": str(uuid.uuid4()), "zeit": self._now(),
                  "projekt_id": "test", "titel": "Testmeldung", "uebergabe_nr": 1, "aktuelle_uebergabe_nr": 1,
                  "schnittraum": "",
                  "mischer": mixer, "raum": room, "film_ordner": "test",
                  "export_ordner": f"test/{cfg.export_folder}",
                  "metadaten_json_pfad": f"test/{cfg.export_folder}/test_metadaten.json", "dateien": []}
        body = N.fill(cfg.notify.template, values)
        status = self._send(cfg.notify, body, self._creds(cfg))
        return {"status": status, "body": body}

    def export_dir(self, project_id: str) -> str:
        return os.path.join(self.folder_of(project_id), self.config.export_folder)

    def delivery_target(self, project_id: str) -> Callable[[str, str], DeliveryTarget]:
        cfg = self.config
        film = self.folder_of(project_id)
        return lambda pid, stamp: export_target(film, cfg.export_folder, cfg.redelivery, stamp, root=cfg.root)

    # ── nur mit der Helmut-API ──────────────────────────────────────────────
    def notify(self, username: str, title: str, body: str) -> None:
        raise HelmutError(ONLY_API, status=501)

    def duplicate(self, contribution: Contribution, title: str) -> Contribution:
        raise HelmutError(ONLY_API, status=501)

    def add_handover_asset(self, project_id: str, asset) -> dict:
        raise HelmutError(ONLY_API, status=501)


# ── Lieferung nach „Audio Export“ ───────────────────────────────────────────────
class UnsafeTarget(OSError):
    """Exportordner ist ein Symlink oder liegt außerhalb des Filmordners – nichts geliefert (Sicherheit 1)."""


def _no_link(path: str) -> None:
    if os.path.islink(path):
        raise UnsafeTarget(errno.ELOOP, f"„{os.path.basename(path)}“ ist ein Verweis (Symlink) – nicht geliefert")


def _check_inside(path: str, folder: str) -> None:
    real, base = os.path.realpath(path), os.path.realpath(folder)
    if real != base and not real.startswith(base.rstrip(os.sep) + os.sep):
        raise UnsafeTarget(errno.EXDEV, f"{path} liegt außerhalb von {folder} – nicht geliefert")


def export_target(film: str, export_folder: str, mode: str, stamp: str, root: str = "") -> DeliveryTarget:
    """„zeitstempel“: jede Lieferung in „Audio Export/<Zeit>/“. „ersetzen“ (Standard): neue Lieferung direkt in
    „Audio Export/“, die vorige vorher nach „Audio Export/_alt/<Zeit der vorigen>/“ verschoben (nichts gelöscht).
    Kopiert wird in „_in_arbeit-<Zeit>“; erst wenn alles da ist, wird umbenannt – die Metadaten-Datei zuletzt. Ein
    Rest nach einem Abbruch bleibt sichtbar liegen (Ausfall F7). Symlinks und Wege aus dem Filmordner hinaus werden
    abgelehnt (Sicherheit 1). Der Exportordner ist ein einfacher Name – geprüft, bevor etwas angelegt wird."""
    name = str(export_folder or "")
    if not name.strip() or os.sep in name or "/" in name or name.startswith(".") or "\0" in name:
        raise UnsafeTarget(f"Exportordner „{name}“ ist kein einfacher Ordnername")
    base = os.path.join(film, name)
    _no_link(film)
    if root:
        _check_inside(film, root)
    _no_link(base)
    if os.path.exists(base):
        _check_inside(base, film)

    def safe(path: str) -> Callable[[], None]:
        def check() -> None:
            for p in (film, base, path):
                _no_link(p)
            _check_inside(path, film)
        return check
    if mode == STAMPED:
        d = os.path.join(base, stamp)
        stamp_new = not os.path.lexists(d)

        def stamped(staged: list[str]) -> list[str]:
            safe(d)()
            _tidy(base, created, {stamp}, *([d] if stamp_new else []))
            if not stamp_new:                           # Ordner gab es schon: nur die eigenen Dateien
                sweep_appledouble(d, {os.path.basename(p) for p in staged}, orphans=False)
            return list(staged)
        target = DeliveryTarget(d, d, stamped, safe(d))
    else:
        staging = os.path.join(base, f"{STAGING_PREFIX}{stamp}")
        check = safe(staging)

        def finish(staged: list[str]) -> list[str]:
            check()
            old = [n for n in os.listdir(base) if not n.startswith(("_", "."))]
            # Metadaten-Datei zuerst weg: bricht das Verschieben ab, steht keine „fertige“ alte Datei mehr da, deren
            # WAVs schon fehlen (Ausfall F6)
            old.sort(key=lambda n: (not n.endswith("_metadaten.json"), n))
            if old:
                old_dir = os.path.join(base, OLD)
                _no_link(old_dir)
                first = dest = os.path.join(old_dir, _old_stamp(base, old, stamp))
                n = 1
                while os.path.exists(dest):
                    n += 1
                    dest = f"{first}_{n}"
                os.makedirs(dest)
                for name in old:
                    os.rename(os.path.join(base, name), os.path.join(dest, name))
                sweep_appledouble(base, set(old))       # verwaiste „._“ vor den neuen Dateien weg
            out = []
            for path in staged:                         # Reihenfolge wie geliefert: Metadaten-Datei zuletzt
                check()
                final = os.path.join(base, os.path.basename(path))
                if os.path.exists(final):
                    raise FileExistsError(errno.EEXIST, f"{final} gibt es schon – nichts überschrieben")
                os.rename(path, final)
                out.append(final)
            sweep_appledouble(staging)                  # verwaiste „._“-Dateien, sonst ist er nicht leer
            try:
                os.rmdir(staging)                       # leerer Hilfsordner
            except OSError as e:                        # Lieferung ist schon vollständig – nur vermerken (Sicherheit)
                log.warning("Hilfsordner %s nicht entfernt: %s", staging, e)
            names = {os.path.basename(p) for p in out} | set(old) | {OLD}
            _tidy(base, created, names, *([dest] if old else []))
            return out
        target = DeliveryTarget(staging, base, finish, check)
    created = not os.path.lexists(base)
    os.makedirs(base, exist_ok=True)
    _no_link(base)
    _check_inside(base, film)
    os.makedirs(target.copy_dir, exist_ok=True)
    target.check()
    return target


def _tidy(base: str, created: bool, names: set[str], *new: str) -> None:
    """Begleitdateien von macOS („._*“) der eigenen Lieferung entfernen (SMB-Test 11.11.2026; Regel 03.10.2026: nie eine
    fremde). In „Audio Export“ die der gelieferten und verschobenen Dateien und verwaiste, in den eben angelegten
    Ordnern (`new`) alle; „._Audio Export“ im Filmordner nur, wenn die App den Ordner eben angelegt hat."""
    sweep_appledouble(base, names)
    for folder in new:
        sweep_appledouble(folder)
        sweep_appledouble(os.path.dirname(folder), {os.path.basename(folder)})
    if created:
        drop_appledouble(base)


def _old_stamp(base: str, names: list[str], fallback: str) -> str:
    """Zeit der vorigen Lieferung (Änderungszeit ihrer Metadaten-Datei) als Ordnername in „_alt“."""
    meta = [n for n in names if n.endswith("_metadaten.json")]
    try:
        return datetime.fromtimestamp(os.lstat(os.path.join(base, meta[0])).st_mtime).strftime("%Y%m%d-%H%M%S")
    except (IndexError, OSError):
        return fallback


def seen_state(contribution: Contribution) -> str:
    return contribution.mix.state if contribution.mix is not None else ""


def overlay_peers(contributions: list[Contribution], peers: dict, mixer: str, room: str) -> list[Contribution]:
    """Meldet eine andere Ottilie (über die Zentrale), dass sie einen Film übernommen hat, gilt er sofort als belegt –
    auch bevor ihre Sperrdatei hier gelesen ist (nur Anzeige; geschrieben wird nichts)."""
    from tonmischung.core.peers import FREE
    busy = {p.contribution: (name, p) for name, p in peers.items() if p.contribution and p.state != FREE}
    out = []
    for c in contributions:
        hit = busy.get(c.id)
        if hit and c.mix.state not in mixstate.OWNED and hit[0] != room:
            name, p = hit
            mix = replace(c.mix, state=mixstate.TAKEN, mixer=p.mixer, room=name)
            c = Contribution(c.id, c.title, c.show, True, mix, {**c.raw, "belegt_direkt": True})
        out.append(c)
    return out
