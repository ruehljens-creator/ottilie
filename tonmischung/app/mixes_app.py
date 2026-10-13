"""„Gemachte Mischungen“: Verzeichnis der auf diesem Mac zurückgelieferten Beiträge und Havarie-Sendefiles
(Jens 09.10.2026). Leeren betrifft nur dieses Verzeichnis – Helmut, Sessions und Dateien bleiben."""
from __future__ import annotations

import logging

from tonmischung.app.bridge import Bridge
from tonmischung.app.context import AppContext
from tonmischung.core import mix_log as ML

log = logging.getLogger("tonmischung.app")


def mix_log(ctx: AppContext) -> ML.MixLog:
    """Verzeichnis der Mischungen dieses Macs (im Mock-Betrieb eine eigene Datei)."""
    if "mischungen" not in ctx.extra:
        ctx.extra["mischungen"] = ML.MixLog(ML.default_path(mock=ctx.mock is not None))
    return ctx.extra["mischungen"]


def record_mix(ctx: AppContext, record: ML.MixRecord) -> None:
    """Eintrag ins Verzeichnis; ein Fehler dabei darf eine gelungene Lieferung nie scheitern lassen."""
    try:
        mix_log(ctx).add(record)
    except OSError as e:
        log.warning("Mischung nicht ins Verzeichnis eingetragen: %s", e)


def list_mixes(ctx: AppContext, args: dict) -> dict:
    entries = mix_log(ctx).entries()
    return {"eintraege": entries, "anzahl": len(entries)}


def clear_mixes(ctx: AppContext, args: dict) -> dict:
    n = mix_log(ctx).clear()
    return {"text": f"Liste geleert ({n} Einträge) – Helmut, Sessions und Dateien bleiben, wie sie sind",
            "eintraege": [], "anzahl": 0}


def register(bridge: Bridge, ctx: AppContext) -> None:
    bridge.register("mischungen", lambda args: list_mixes(ctx, args))
    bridge.register("mischungen_leeren", lambda args: clear_mixes(ctx, args))
