"""Ersatz für Hedwigs `hedwig_log` (nicht übernommen): leitet an Pythons `logging` weiter.

Hedwigs Original schreibt eine eigene Logdatei und einen Ringpuffer für seine
Oberfläche. Tonmischung nutzt nur die Meldungen – sie gehen an den Logger
`tonmischung.hedwig.<modul>`.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator


class Protokoll:
    def __init__(self, name: str) -> None:
        self._log = logging.getLogger(f"tonmischung.hedwig.{name}")

    @staticmethod
    def _zeile(text: str, werte: dict[str, Any]) -> str:
        return text + "".join(f" {k}={v}" for k, v in werte.items())

    def spur(self, text: str, **werte: Any) -> None:
        self._log.debug(self._zeile(text, werte))

    debug = spur

    def info(self, text: str, **werte: Any) -> None:
        self._log.info(self._zeile(text, werte))

    def warnung(self, text: str, **werte: Any) -> None:
        self._log.warning(self._zeile(text, werte))

    def fehler(self, text: str, **werte: Any) -> None:
        self._log.error(self._zeile(text, werte))

    def ausnahme(self, text: str, **werte: Any) -> None:
        self._log.warning(self._zeile(text, werte), exc_info=True)

    @contextmanager
    def schritt(self, name: str, **werte: Any) -> Iterator[None]:
        yield

    def signal(self, *args: Any, **kwargs: Any) -> None:
        pass


def hole(name: str) -> Protokoll:
    return Protokoll(name.rsplit(".", 1)[-1].replace("hedwig_", ""))
