"""Was die Oberfläche braucht: Einstellungen, Helmut, Pro Tools, Mischer und Raum.

Im Entwicklungsmodus (`--mock`) läuft Helmut als Mock mit Beiträgen, deren Übergaben auf echte Test-AAFs zeigen;
Sessions landen dann immer im Testordner (Sicherheitsregel). Beides steht je Mac in `testfiles/entwicklung.json`
(`tonmischung.devlocal`), nicht im Code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from tonmischung import devlocal
from tonmischung.config.settings import Settings
from tonmischung.core.catchup import CatchUpQueue, default_path
from tonmischung.helmut.client import HelmutClient
from tonmischung.helmut.mock import MockHelmut
from tonmischung.ptsl.client import PtslClient


def dev_mock() -> MockHelmut:
    """Helmut-Mock für den Entwicklungsmodus: Beispielbeiträge aus `testfiles/entwicklung.json` (nur die, deren
    Übergabe-Dateien auf diesem Mac liegen) und ein belegter Beitrag ohne Übergabe."""
    mock = MockHelmut()
    folder = devlocal.test_folder()
    for pid, title, show, assets in devlocal.dev_projects():
        if all(os.path.exists(a) for a in assets):
            # Projektordner im Testordner (Sicherheitsregel), wie ein Helmut-Projekt mit Premiere-Datei
            mock.add_project(pid, title, show, assets=assets,
                             path=os.path.join(folder, "Helmut-Projekte", pid, f"{pid}.prproj"))
    mock.add_project("kita", "Kita-Streik", "Heimatjournal", state="in_mischung", mischer="A. Wolf", raum="Ton 2")
    return mock


@dataclass
class AppContext:
    settings: Settings
    helmut: HelmutClient
    ptsl: PtslClient
    mixer: str
    room: str
    session_parent: str
    mock: MockHelmut | None = None
    version: str = ""
    handover_root: str = ""                      # Übergabeordner für das Zurückliefern ({projektId})
    extra: dict = field(default_factory=dict)
    queue: CatchUpQueue | None = None            # Nachtrag-Warteschlange (Helmut-Ausfall)
    safe_root: str = ""                          # Entwicklungsmodus: Sessions nur hier (Sicherheitsregel)
    users: Any = None                            # UserStore (Plan Nutzer & Räume)
    user: Any = None                             # angemeldeter Nutzer oder None

    def placement(self, contribution):
        """Ablageort der Session (Plan M7-Ablage); im Mock-Modus nur im Testordner (Sicherheitsregel)."""
        from tonmischung.core import storage
        from tonmischung.core.fileio import inside
        s = self.settings
        pl = storage.placement(s.get("import.session_ablage.modus") or storage.MIRROR, contribution,
                               self.session_parent,
                               s.get("import.session_ablage.ordner_im_projekt") or "{projekt}/Ton/ProTools/")
        if self.safe_root:
            for path in (pl.parent, pl.mirror_parent):
                if path and not inside(path, self.safe_root):
                    raise storage.StorageError(f"Im Entwicklungsmodus nur im Testordner – {path} liegt außerhalb")
        return pl

    def service(self):
        """MixService für diesen Mischer und Raum – mit Nachtrag bei Helmut-Ausfall."""
        from tonmischung.helmut.service import MixService
        return MixService(self.helmut, self.mixer, self.room, queue=self.queue)

    @classmethod
    def create(cls, mock: bool = False) -> "AppContext":
        from tonmischung import __version__, factory
        s = factory.load_settings()
        mixer, room = factory.identity(s)
        if mock:
            m = dev_mock()
            url = m.start()
            folder = devlocal.test_folder()
            return cls(s, HelmutClient(url, lambda: ("test", "test")), factory.ptsl_client(s), mixer, room,
                       folder, m, __version__,
                       handover_root=os.path.join(folder, "Helmut-Uebergabe", "{projektId}"),
                       queue=CatchUpQueue(default_path(mock=True)), safe_root=folder)
        return cls(s, factory.helmut_client(s), factory.ptsl_client(s), mixer, room,
                   s.get("import.session_ablage.lokaler_ordner"), None, __version__,
                   handover_root=s.get("export.verarbeitung_helmut.uebergabeordner"),
                   queue=CatchUpQueue(default_path()))

    def close(self) -> None:
        """Beim Beenden: PTSL-Verbindung schließen (sonst CLOSE_WAIT in Pro Tools), Mock stoppen."""
        self.ptsl.reset()
        if self.mock is not None:
            self.mock.stop()
