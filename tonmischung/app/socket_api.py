"""Unix-Socket der laufenden App: eine JSON-Zeile je Anfrage, eine JSON-Zeile Antwort.

Für Stream Deck, Skripte und den MCP-Server. Nur für den angemeldeten Benutzer (Ordner 0700,
Socket 0600, wie PunchBuddys `/tmp/punchbuddy.sock`). Aktionen laufen damit im Prozess der
App – mit Auftrags-Thread, Sperren und Fenster –, nie in einer zweiten Instanz.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
from typing import Any

from tonmischung.app.api import ApiDispatcher
from tonmischung.app.bridge import CommandError

DEFAULT_PATH = "~/Library/Application Support/Ottilie/api.sock"
log = logging.getLogger("tonmischung.api")


def default_path() -> str:
    return os.path.expanduser(DEFAULT_PATH)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            rid = None
            try:
                msg = json.loads(line)
                rid = msg.get("id")
                origin = msg.get("origin") if msg.get("origin") in ("socket", "mcp") else "socket"   # nie „fenster“
                result = self.server.dispatcher.dispatch(msg.get("cmd", ""), msg.get("args") or {}, origin)
                reply = {"id": rid, "ok": True, "result": result}
            except CommandError as e:
                reply = {"id": rid, "ok": False, "error": str(e)}
            except (ValueError, AttributeError) as e:
                reply = {"id": rid, "ok": False, "error": f"Unlesbare Anfrage: {e}"}
            except Exception as e:  # noqa: BLE001 – jeder Fehler geht als Antwort zurück
                log.exception("API-Anfrage gescheitert")
                reply = {"id": rid, "ok": False, "error": str(e) or e.__class__.__name__}
            self.wfile.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    dispatcher: ApiDispatcher


class SocketApi:
    def __init__(self, dispatcher: ApiDispatcher, path: str | None = None) -> None:
        self.path = path or default_path()
        self.dispatcher = dispatcher
        self._server: _Server | None = None

    def start(self) -> None:
        folder = os.path.dirname(self.path)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        if os.path.exists(self.path):
            if _alive(self.path):
                raise RuntimeError(f"Socket {self.path} ist schon belegt – läuft die App zweimal?")
            os.unlink(self.path)                       # Rest eines abgestürzten Laufs
        old = os.umask(0o177)
        try:
            self._server = _Server(self.path, _Handler)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        self._server.dispatcher = self.dispatcher
        threading.Thread(target=self._server.serve_forever, daemon=True, name="tm-api").start()
        log.info("API-Socket: %s", self.path)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if os.path.exists(self.path):
            os.unlink(self.path)


def _alive(path: str) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(path)
        return True
    except OSError:
        return False


class SocketClient:
    """Gegenstück für Skripte und den MCP-Server."""

    def __init__(self, path: str | None = None, origin: str = "socket", timeout_s: float = 30.0) -> None:
        self.path = path or default_path()
        self.origin = origin
        self.timeout_s = timeout_s
        self._next = 0

    def available(self) -> bool:
        return os.path.exists(self.path) and _alive(self.path)

    def call(self, cmd: str, args: dict | None = None, timeout_s: float | None = None) -> Any:
        if not os.path.exists(self.path):
            raise CommandError("Ottilie läuft nicht (kein API-Socket) – bitte die App starten")
        self._next += 1
        msg = {"id": self._next, "cmd": cmd, "args": args or {}, "origin": self.origin}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout_s or self.timeout_s)
                s.connect(self.path)
                s.sendall((json.dumps(msg) + "\n").encode("utf-8"))
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
        except socket.timeout as e:
            raise CommandError(f"Keine Antwort der App nach {timeout_s or self.timeout_s:.0f} s") from e
        except OSError as e:
            raise CommandError(f"App nicht erreichbar: {e}") from e
        reply = json.loads(buf.decode("utf-8"))
        if not reply.get("ok"):
            raise CommandError(reply.get("error", "Fehler"))
        return reply.get("result")
