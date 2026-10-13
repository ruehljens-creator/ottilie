"""MCP-Server von Ottilie (stdio) – für KI-Assistenten wie Claude Code und Claude Desktop.

Start: `Ottilie mcp` (Paket) bzw. `python3 -m tonmischung mcp`. Die Werkzeuge sind alle Befehle aus
`tonmischung/app/api.py` (Jens 08.10.2026: „vollständiger MCP-Zugriff für Automation, nicht über das Netzwerk“);
sie gehen über den Unix-Socket der laufenden App (Aktionen laufen so im richtigen Prozess, mit Auftrags-Thread und
Fenster). Nur `triage` mit einem AAF-Pfad geht auch ohne App. Drei Stufen: lesen immer; Arbeitsabläufe nur ohne
`system.schnittstellen.mcp_nur_lesen`; Verwalten zusätzlich mit `mcp_verwalten_erlaubt`; `antworten` nur mit
`mcp_antworten_erlaubt` – die App prüft das noch einmal. Die meisten Werkzeuge entstehen aus dem Katalog.

Wichtig für stdio: nichts auf stdout schreiben außer dem MCP-Protokoll (Protokoll auf stderr).
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import typing
from typing import Any

from tonmischung.app.bridge import CommandError
from tonmischung.app.socket_api import SocketClient
from tonmischung.core.mixstate import RELEASE_REASONS

INSTRUCTIONS = (
    "Ottilie: Beiträge aus Helmut holen (AAF-Triage, Import in Pro Tools) und Mischungen "
    "zurückliefern. Erst lesen (status, beitraege, beitrag, auftrag). Aktionen nur, wenn freigeschaltet; "
    "Fragen eines Auftrags (offene Session, Triage-Vorschau) beantwortet der Mischer im Fenster – "
    "danach mit auftrag nachsehen."
)


def _text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)


def build(settings: Any, client: SocketClient | None = None):
    from mcp.server.fastmcp import FastMCP
    client = client or SocketClient(origin="mcp")
    read_only = bool(settings.get("system.schnittstellen.mcp_nur_lesen"))
    may_answer = bool(settings.get("system.schnittstellen.mcp_antworten_erlaubt"))
    mcp = FastMCP("ottilie", instructions=INSTRUCTIONS)

    def call(cmd: str, _timeout: float | None = None, **args: Any) -> str:
        try:
            return _text(client.call(cmd, {k: v for k, v in args.items() if v not in (None, "")}, timeout_s=_timeout))
        except CommandError as e:
            return f"Fehler: {e}"

    @mcp.tool(description="Status: Pro Tools (offene Session), Helmut, Mischer und Raum")
    def status() -> str:
        return call("status")

    @mcp.tool(description="Pro Tools abfragen (nur lesen). was: transport | spuren | session | auswahl | marker")
    def pro_tools(was: str = "transport") -> str:
        return call("pro_tools", was=was)

    @mcp.tool(description="Beiträge in Helmut (eigene mit „meine Mischung · …“); query: Suchtext")
    def beitraege(query: str = "") -> str:
        return call("beitraege", query=query)

    @mcp.tool(description="Details eines Beitrags (Projekt-ID) mit Zustand, Übergabe und erlaubten Aktionen")
    def beitrag(id: str) -> str:
        return call("beitrag", id=id)

    @mcp.tool(description="Templates: „Meine Templates“ und die Pro-Tools-Templates dieses Macs")
    def templates() -> str:
        return call("templates")

    @mcp.tool(description="AAF-Triage ohne Pro Tools: Kategorie (O-Ton, Atmo, Musik, ab A11) je Spur und Clip. "
                          "aaf: Pfad zur AAF, oder id: Projekt-ID (AAF aus der Übergabe, braucht die App)")
    def triage(aaf: str = "", id: str = "") -> str:
        if aaf and not client.available():
            return _triage_offline(settings, aaf)
        return call("triage", aaf=aaf, id=id)

    @mcp.tool(description="Stand des laufenden oder letzten Auftrags (Holen, Zurückliefern) inkl. offener Frage")
    def auftrag() -> str:
        return call("auftrag")

    @mcp.tool(description="Übersicht vor dem Zurückliefern: Exportspuren, fehlende Spuren, Übergabeordner, Lautheit")
    def zurueckliefern_info(id: str) -> str:
        return call("zurueckliefern_info", id=id)

    if not read_only:
        @mcp.tool(description="Beitrag übernehmen und in Pro Tools importieren. template: „Gruppe/Name“. "
                              "Fragen erscheinen im Fenster beim Mischer; Stand mit auftrag.")
        def holen(id: str, template: str, triage: bool = True, video: bool = True) -> str:
            group, _, name = template.partition("/")
            return call("holen", id=id, group=group, name=name, triage=triage, video=video)

        @mcp.tool(description="Mischung an Helmut zurückliefern (konsolidieren, R128, WAVs). "
                              "tracks: Exportspuren kommagetrennt, sonst Spurtabelle")
        def zurueckliefern(id: str, tracks: str = "") -> str:
            return call("zurueckliefern", id=id, tracks=tracks)

        @mcp.tool(description="Übernahme aufheben. reason: " + " | ".join(RELEASE_REASONS))
        def freigeben(id: str, reason: str) -> str:
            return call("freigeben", id=id, reason=reason)

        if may_answer:
            @mcp.tool(description="Offene Frage eines Auftrags beantworten (id aus auftrag.question.id)")
            def antworten(id: int, value: bool) -> str:
                return call("antworten", id=id, value=value)
    may_admin = bool(settings.get("system.schnittstellen.mcp_verwalten_erlaubt"))
    for cmd in generated_commands(read_only, may_admin):
        mcp.add_tool(make_tool(cmd, call), name=cmd.name, description=describe(cmd))
    return mcp


# Werkzeuge mit eigener Logik oben (Triage ohne App, Template „Gruppe/Name“); alle anderen aus dem Katalog
HAND_WRITTEN = {"status", "pro_tools", "beitraege", "beitrag", "templates", "triage", "auftrag", "zurueckliefern_info",
                "holen", "zurueckliefern", "freigeben", "antworten"}
TYPES: dict[str, Any] = {"str": str, "bool": bool, "int": int, "float": float, "list": list, "dict": dict, "any": Any}


def generated_commands(read_only: bool, may_admin: bool) -> list:
    from tonmischung.app.api import ADMIN, CATALOG, CHANGE
    out = []
    for cmd in CATALOG:
        if cmd.name in HAND_WRITTEN:
            continue
        if cmd.kind == CHANGE and read_only:
            continue
        if cmd.kind == ADMIN and (read_only or not may_admin):
            continue
        out.append(cmd)
    return out


def describe(cmd: Any) -> str:
    parts = [f"{cmd.summary} ({cmd.kind})."]
    if cmd.args:
        parts.append("Argumente: " + "; ".join(f"{k}{'' if k in cmd.required else ' (optional)'} – {v}"
                                               for k, v in cmd.args.items()) + ".")
    if cmd.result:
        parts.append(f"Ergebnis: {cmd.result}.")
    return " ".join(parts)


def make_tool(cmd: Any, call: Any) -> Any:
    """Werkzeugfunktion mit echter Signatur (Namen, Typen, Pflichtangaben) – daraus baut FastMCP das Schema."""
    params = []
    for arg in cmd.args:
        t = TYPES.get(cmd.types.get(arg, "str"), str)
        if arg in cmd.required:
            params.append(inspect.Parameter(arg, inspect.Parameter.KEYWORD_ONLY, annotation=t))
        else:
            params.append(inspect.Parameter(arg, inspect.Parameter.KEYWORD_ONLY, default=None,
                                            annotation=typing.Optional[t]))

    def tool(**kwargs: Any) -> str:
        return call(cmd.name, _timeout=cmd.timeout_s, **kwargs)
    tool.__name__ = cmd.name
    tool.__doc__ = cmd.summary
    tool.__signature__ = inspect.Signature(params, return_annotation=str)        # type: ignore[attr-defined]
    tool.__annotations__ = {**{p.name: p.annotation for p in params}, "return": str}
    return tool


def _triage_offline(settings: Any, aaf: str) -> str:
    """Triage direkt (ohne App) – nur lesen, ohne Pro Tools."""
    from tonmischung.factory import triage_options
    from tonmischung.triage.analyse import triage_aaf
    from tonmischung.triage.report import as_dict
    if not os.path.exists(aaf):
        return f"Fehler: AAF nicht gefunden: {aaf}"
    o = triage_options(settings)
    try:
        r = triage_aaf(aaf, threshold=o.threshold, keywords=o.keywords, music_paths=o.music_paths,
                       use_track_names=o.use_track_names, use_source_names=o.use_source_names,
                       use_signal=o.use_signal)
    except (OSError, ValueError) as e:
        return f"Fehler: Triage nicht möglich: {e}"
    return _text(as_dict(r))


def run(settings: Any) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    if not settings.get("system.schnittstellen.mcp_aktiv"):
        print("MCP ist in den Einstellungen abgeschaltet (system.schnittstellen.mcp_aktiv)", file=sys.stderr)
        return 1
    build(settings).run()                    # stdio
    return 0
