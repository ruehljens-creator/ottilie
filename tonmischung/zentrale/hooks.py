"""Zentrale im Ablauf der Betriebsart Übergabeordner (Stufe 2): Belegen, Freigeben, Ereignisse.

Grundsatz: Die Zentrale darf Ottilie nur in einem Fall aufhalten – wenn sie sagt, dass ein anderer Raum den Film schon
belegt hat. Ist sie nicht erreichbar oder antwortet nicht, gilt das bisherige Verfahren (Sperrdatei); Freigaben und
Ereignisse gehen dann verloren bzw. werden beim Wiederverbinden über die gemeldeten Belegungen abgeglichen.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from tonmischung.zentrale.link import ZentraleRefused, ZentraleUnavailable

log = logging.getLogger("tonmischung.zentrale")
CLAIM_TIMEOUT_S = 5.0


class Refused(Exception):
    """Die Zentrale hat das Belegen abgelehnt (anderer Raum war schneller)."""


class CentralHooks:
    def __init__(self, link: Any) -> None:
        self.link = link

    def claim(self, beitrag_id: str, title: str, origin: str) -> None:
        if not getattr(self.link, "available", False):
            return                                          # ohne Zentrale: Sperrdatei wie bisher
        for attempt in (1, 2):
            try:
                self.link.request("belegen", {"beitrag_id": beitrag_id, "titel": title, "schnittraum": origin},
                                  timeout_s=CLAIM_TIMEOUT_S)
                return
            except ZentraleRefused as e:
                if attempt == 1 and "Zu viele" in str(e) and not e.data.get("belegt_von"):
                    time.sleep(2.0)                    # Bremse der Zentrale: einmal kurz warten und wiederholen
                    continue
                self._refused(beitrag_id, e)
                return
            except ZentraleUnavailable as e:
                log.info("Belegen ohne Zentrale (%s): %s", beitrag_id, e)
                return

    def _refused(self, beitrag_id: str, e: ZentraleRefused) -> None:
        holder = str(e.data.get("belegt_von") or "")
        if not holder:                                      # andere Ablehnung (ältere Zentrale, Fehler): wie weg
            log.info("Belegen %s: Zentrale lehnt ab ohne Belegung (%s) – Sperrdatei wie bisher", beitrag_id, e)
            return
        raise Refused(f"schon in {holder} belegt – anderen wählen oder mit {holder} klären")

    def release(self, beitrag_id: str, title: str = "", reason: str = "", delivered: bool = False) -> None:
        """Freigeben – geht auch nach einem kurzen Abbruch noch hinaus (sonst bliebe die Belegung hängen)."""
        try:
            self.link.send_any("freigeben", {"beitrag_id": beitrag_id, "titel": title, "grund": reason,
                                             "ausgeliefert": delivered})
        except Exception as e:  # noqa: BLE001
            log.info("Zentrale: Freigabe nicht gemeldet (%s)", e)

    def event(self, kind: str, beitrag_id: str, title: str = "", origin: str = "", reason: str = "") -> None:
        self._send("ereignis", {"art": kind, "beitrag_id": beitrag_id, "titel": title, "schnittraum": origin,
                                "grund": reason})

    def _send(self, typ: str, data: dict) -> None:
        try:
            if getattr(self.link, "available", False):
                self.link.send(typ, data)
        except Exception as e:  # noqa: BLE001 – die Zentrale stört den Ablauf nie
            log.info("Zentrale: %s nicht gesendet (%s)", typ, e)
