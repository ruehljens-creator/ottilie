"""Pro-Tools-Abfragen, nur lesen – aus PunchBuddys MCP-Server (`tools/pt_mcp_server.py`) übernommen.

Laufen über die Verbindung der App (eine Verbindung, eine Sperre), erreichbar über die API
(`pro_tools`) und als MCP-Werkzeuge. Nichts davon verändert die Session.
"""
from __future__ import annotations

from typing import Any

KINDS = ("transport", "spuren", "session", "auswahl", "marker")


def _name(value: Any, enum: Any = None) -> str:
    if enum is not None and isinstance(value, int):
        try:
            return enum.Name(value)
        except ValueError:
            pass
    return str(value)


def query(ptsl: Any, kind: str) -> dict[str, Any]:
    if kind == "transport":
        return {"transport": ptsl.transport_state()}
    if kind == "spuren":
        return {"spuren": [{"name": t.name, "typ": t.type, "clips": t.has_clips, "format": t.format}
                           for t in ptsl.tracks()]}
    if kind == "session":
        import ptsl.PTSL_pb2 as pt
        s = ptsl.open_session()
        if s is None:
            return {"session": None}
        rate = ptsl.call("Abtastrate", lambda e: e.session_sample_rate())
        tc = ptsl.call("Timecode-Rate", lambda e: e.session_timecode_rate())
        return {"session": s.name, "pfad": s.path, "abtastrate": _name(rate, getattr(pt, "SampleRate", None)),
                "timecode_rate": _name(tc, getattr(pt, "SessionTimeCodeRate", None))}
    if kind == "auswahl":
        a, b = ptsl.call("Auswahl lesen", lambda e: e.get_timeline_selection())
        return {"von": a, "bis": b}
    if kind == "marker":
        locs = ptsl.call("Marker", lambda e: e.get_memory_locations())
        return {"marker": [{"nr": m.number, "name": m.name, "start": m.start_time} for m in locs or []]}
    raise ValueError(f"Unbekannte Abfrage: {kind} (möglich: {', '.join(KINDS)})")


def register(bridge: Any, ptsl: Any) -> None:
    from tonmischung.app.bridge import CommandError
    from tonmischung.ptsl.errors import PtslError

    def run(args: dict) -> dict:
        try:
            return query(ptsl, str(args.get("was", "")))
        except (ValueError, PtslError) as e:
            raise CommandError(str(e)) from e
    bridge.register("pro_tools", run)
