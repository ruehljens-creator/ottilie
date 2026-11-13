"""Stufe 3 A: Übergabeordner beobachten (nur lesend eingebunden) und neue Beiträge sofort melden.

Gleiche Regeln wie Ottilie (`tonmischung/dropfolder/handover.py`, wiederverwendet): `uebergabe.json` kommt zuletzt und
muss seit der Wartezeit (Standard 3 s) unverändert sein; Dateien und Größen werden per `lstat` abgeglichen; „._*“,
„.DS_Store“, „_“- und „.“-Ordner werden übergangen; Verweise (Symlinks) zählen nicht. Gemeldet wird ein Film einmal,
sobald er „bereit“ ist – Ottilies lesen den Ordner dann nur noch alle 120 s als Rückfall.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections.abc import Callable

try:                                                 # im Abbild mitgeliefert (Dockerfile), im Repo daneben
    from tonmischung.dropfolder import handover as H
except ImportError:                                  # pragma: no cover – Entwicklung ohne Ottilie-Module
    H = None

POLL_S = 5.0


class FolderWatch:
    def __init__(self, root: str, report: Callable[[str, str, str], None], wait_s: Callable[[], float],
                 json_name: Callable[[], str] = lambda: "uebergabe.json", log=None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.root, self.report, self.wait_s, self.json_name = root, report, wait_s, json_name
        self.log, self.clock, self.wall = log, clock, wall
        self._seen: dict[str, tuple[tuple, float]] = {}
        self._reported: dict[str, tuple] = {}          # Ordner → Stand der gemeldeten Übergabe-JSON
        self.on_state: Callable[[str], None] = lambda s: None
        self._stop = threading.Event()
        self.state = "aus"

    def seed(self) -> None:
        """Erster Durchlauf ohne Meldung: vorhandene Filme gelten als bekannt (sonst käme nach jedem Neustart für
        jeden liegenden Film „übergeben“)."""
        report, self.report = self.report, lambda *a: None
        wait, self.wait_s = self.wait_s, lambda: 0.0             # was schon daliegt, ist fertig übertragen
        try:
            self.scan()
        finally:
            self.report, self.wait_s = report, wait
            self._seen.clear()

    def _state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.on_state(state)

    def scan(self) -> list[str]:
        """Ein Durchlauf; Rückgabe: neu gemeldete Filmordner. Gemeldet wird ein Film, sobald er „bereit“ ist – und
        noch einmal, wenn sich seine Übergabe-JSON ändert (neue Fassung)."""
        if H is None or not os.path.isdir(self.root):
            self._state("nicht erreichbar")
            return []
        try:
            names = sorted(os.listdir(self.root))
        except OSError:
            self._state("nicht lesbar")
            return []
        films = [n for n in names if not (H.ignored(n, ("_", "."), "Audio Export")
                                          or os.path.islink(os.path.join(self.root, n))
                                          or not os.path.isdir(os.path.join(self.root, n)))]
        if not films and self._reported:
            # plötzlich leer: eher die Einbindung des Hosts weg als alle Filme gelöscht – nichts vergessen
            self._state("nicht erreichbar (Ordner leer)")
            return []
        self._state("beobachtet")
        new = []
        for name in films:
            film, key = self._film(os.path.join(self.root, name))
            if film is None or film.state != H.READY or self._reported.get(name) == key:
                continue
            self._reported[name] = key
            new.append(name)
            self.report(name, film.display_title, film.edit_room, int(film.number or 0))
        for gone in set(self._reported) - set(films):
            del self._reported[gone]                # Ordner weg und wieder da → erneut melden
        return new

    def _film(self, folder: str):
        path = os.path.join(folder, self.json_name())
        try:
            st = os.lstat(path)
        except OSError:
            self._seen.pop(path, None)
            return None, None
        key = (st.st_mtime_ns, st.st_size)
        seen = self._seen.get(path)
        if seen is None or seen[0] != key:
            seen = (key, self.clock())
            self._seen[path] = seen
        if max(self.clock() - seen[1], self.wall() - st.st_mtime) < float(self.wait_s()):
            return None, key
        try:
            raw = H.read_regular(path)
            if raw is None:
                return None, key
            return H.check(folder, H.parse(json.loads(raw)), H.regular_size), key
        except Exception as e:  # noqa: BLE001 – fremde Daten: nie die Beobachtung kosten
            if self.log is not None:
                self.log.debug("ordner", f"{os.path.basename(folder)}: Übergabe nicht auswertbar ({type(e).__name__})")
            return None, key

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception as e:  # noqa: BLE001
                if self.log is not None:
                    self.log.error("ordner", f"Beobachtung gescheitert: {type(e).__name__}")
            self._stop.wait(POLL_S)

    def stop(self) -> None:
        self._stop.set()


def roots_from(main: str, more: str) -> list[tuple[str, str]]:
    """(Kennung, Pfad) – der erste Übergabeordner ohne Kennung, weitere aus „Name=/pfad;Name=/pfad“ mit der Kennung
    aus dem Namen, wie Ottilie sie bildet (Filme heißen dort „<Kennung>/<Ordner>“)."""
    from app.work import slug
    out = [("", main)] if main else []
    for part in (more or "").split(";"):
        name, _, path = part.partition("=")
        if name.strip() and path.strip() and slug(name):
            out.append((slug(name), path.strip()))
    return out


class Watches:
    """Mehrere Beobachtungen wie eine: „beobachtet“, solange eine läuft."""

    def __init__(self, watches: list[FolderWatch]) -> None:
        self.watches = watches

    @property
    def state(self) -> str:
        states = [w.state for w in self.watches]
        return "beobachtet" if "beobachtet" in states else (states[0] if states else "aus")

    def keys(self) -> list[str]:
        """Kennungen der Ordner, die gerade beobachtet werden ("" = erster Übergabeordner) – Ottilie liest nur die
        übrigen weiter im normalen Takt (Tontechniker, Programmierer 13.11.2026)."""
        return sorted(getattr(w, "key", "") for w in self.watches if w.state == "beobachtet")

    def stop(self) -> None:
        for w in self.watches:
            w.stop()


def start_all(ctx, loop: asyncio.AbstractEventLoop, roots: list[tuple[str, str]]) -> Watches | None:
    watches = [w for w in (start(ctx, loop, root, key) for key, root in roots) if w is not None]
    return Watches(watches) if watches else None


def start(ctx, loop: asyncio.AbstractEventLoop, root: str = "/mnt/uebergabe", key: str = "") -> FolderWatch | None:
    """Beobachtung im eigenen Thread; Meldungen gehen über die Ereignisschleife an alle Geräte. `key`: Kennung eines
    weiteren Übergabeordners – seine Filme werden als „<key>/<Ordner>“ gemeldet."""
    if not os.path.isdir(root):
        ctx.log.info("ordner", f"Kein Übergabeordner unter {root} – Beobachtung aus")
        return None

    def report(name: str, title: str, origin: str, number: int = 0) -> None:
        coro = ctx.flows.handed_over(f"{key}/{name}" if key else name, title, origin, "Übergabeordner", number)
        asyncio.run_coroutine_threadsafe(coro, loop)

    def state_changed(state: str) -> None:            # Ottilies lesen wieder öfter selbst, wenn die Beobachtung fehlt
        ctx.log.info("ordner", f"Ordnerbeobachtung: {state}")
        asyncio.run_coroutine_threadsafe(ctx.flows.push_settings(), loop)

    def wait() -> float:
        return float(ctx.work.folder_values().get("system.uebergabeordner.wartezeit_s", 3))

    def json_name() -> str:
        return str(ctx.work.folder_values().get("system.uebergabeordner.uebergabe_json", "uebergabe.json"))
    w = FolderWatch(root, report, wait, json_name, log=ctx.log)
    w.key = key
    w.seed()                                         # was beim Start schon bereit liegt, ist nicht „neu“
    w.on_state = state_changed
    threading.Thread(target=w.run, name="zentrale-ordner", daemon=True).start()
    ctx.log.info("ordner", f"Beobachte {root}")
    return w
