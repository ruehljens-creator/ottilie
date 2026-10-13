"""Webtrigger (HTTP) und Stream-Deck-Socket – Verhalten wie PunchBuddy (`auto_punch_in.py` 551–845).

Adressen bleiben (Jens, 02.10.2026): Port 8899, dieselben Pfade, Socket `/tmp/punchbuddy.sock` mit dem
Zeilenprotokoll „<befehl> [arg …]“ → „OK …“/„ERR …“. Damit laufen vorhandene Stream-Deck-Tasten,
Launcher und Companion-Seiten ohne Änderung weiter.

Gegenüber PunchBuddy (Lehren aus dem Betrieb):
- jede HTTP-Anfrage wird mit Pfad und Antwortcode protokolliert (Token geschwärzt), die Startmeldung
  entsteht aus der Routing-Tabelle (PunchBuddy pries `/export` an, das es nicht gab);
- der Socket gehört nur einer lebenden Instanz: ist er belegt, wird er nicht weggenommen, und beim
  Beenden wird nur die eigene Socket-Datei entfernt;
- ID-Code je Platz (Prototyp): Mit gesetztem Code tragen die Adressen `?id=…`; ist „nur mit ID-Code“
  an, löst eine Anfrage ohne passenden Code nichts aus.
Interplay entfällt; `/import` holt den im Fenster gewählten Beitrag (Jens).
"""
from __future__ import annotations

import hmac
import http.server
import logging
import os
import socket
import socketserver
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("tonmischung.trigger")

Dispatch = Callable[[str, list[str]], "tuple[bool, str]"]

# Pfad → Befehl; Antwort sofort „200 OK“, Ausführung im Hintergrund (wie PunchBuddy)
PATH_CMD = {"/trigger": "record_a", "/trigger2": "record_b", "/play": "play", "/play_custom": "play_custom",
            "/start": "goto_start", "/move_audio": "move_audio", "/export_wav": "export_wav",
            "/export_aaf": "export_aaf", "/export_aaf_embedded": "export_aaf",
            "/export_aaf_reference": "export_aaf_reference", "/import": "holen"}
VOC_CMD = {"/vocaster/autogain/host": "vocaster_autogain_host", "/vocaster/autogain/guest": "vocaster_autogain_guest",
           "/vocaster/phantom/on": "vocaster_phantom_on", "/vocaster/phantom/off": "vocaster_phantom_off"}
LABELS = {"/trigger": "Record A", "/trigger2": "Record B", "/play": "Play Input", "/play_custom": "Play Custom",
          "/start": "Zum Programmbeginn", "/move_audio": "Audio verschieben", "/export_wav": "WAV-Export",
          "/export_aaf": "AAF-Export (eingebettet)", "/export_aaf_reference": "AAF-Export (Referenz)",
          "/import": "Holen (gewählter Beitrag)", "/status": "Status", "/commands": "Befehlsliste",
          "/vocaster/autogain/host": "Vocaster Autogain Host", "/vocaster/autogain/guest": "Vocaster Autogain Gast",
          "/vocaster/phantom/on": "Vocaster 48 V an", "/vocaster/phantom/off": "Vocaster 48 V aus"}
DEFAULT_SOCKET = "/tmp/punchbuddy.sock"


def token_ok(expected: str, supplied: str) -> bool:
    """Leeres `expected` = kein Schutz (nur bei 127.0.0.1 sinnvoll); sonst Vergleich in konstanter Zeit."""
    if not expected:
        return True
    return hmac.compare_digest(supplied or "", expected)


def is_local(client: str) -> bool:
    """Kommt der Aufruf von diesem Mac (127.0.0.1, ::1)?"""
    return client in ("127.0.0.1", "::1", "localhost") or client.startswith(("127.", "::ffff:127."))


def _redact(query: str) -> str:
    parts = [p if not p.startswith("token=") else "token=••••" for p in query.split("&") if p]
    return ("?" + "&".join(parts)) if parts else ""


@dataclass
class TriggerConfig:
    port: int = 8899
    bind: str = "127.0.0.1"          # 127.0.0.1 oder 0.0.0.0
    token: str = ""
    id_code: str = ""                # ID-Code dieses Platzes (Prototyp), leer = keiner
    require_id: bool = False


# ── HTTP-Webtrigger ────────────────────────────────────────────────────────
class Webtrigger:
    def __init__(self, dispatch: Dispatch, config: Callable[[], TriggerConfig],
                 spawn: Callable[[Callable[[], None]], None] | None = None) -> None:
        self.dispatch = dispatch
        self.config = config
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True, name="tm-webtrigger").start())
        self.httpd: socketserver.TCPServer | None = None
        self.port: int | None = None

    def handle(self, raw_path: str, headers: dict, client: str = "127.0.0.1") -> tuple[int, str]:
        """Eine GET-Anfrage → (Status, Text). Ohne Netz testbar. `client`: Adresse des Aufrufers."""
        cfg = self.config()
        parsed = urlparse(raw_path)
        path, query = parsed.path, parse_qs(parsed.query)
        supplied = query.get("token", [""])[0] or headers.get("X-Auth-Token", "")
        if not token_ok(cfg.token, supplied):
            return 401, "Unauthorized"
        given_id = query.get("id", [""])[0]
        if cfg.id_code and given_id and not hmac.compare_digest(given_id, cfg.id_code):
            return 403, "ID-Code passt nicht zu diesem Platz"
        # „Nur mit ID-Code“ gilt für Aufrufe übers Netzwerk; lokale (Stream Deck an diesem Mac) brauchen keinen
        # (Jens 09.10.2026). Ist für den Platz kein Code vergeben, sind Aufrufe übers Netz dann gesperrt.
        if cfg.require_id and not is_local(client):
            if not cfg.id_code:
                return 403, "Kein ID-Code für diesen Platz vergeben – Aufrufe übers Netzwerk sind gesperrt"
            if not given_id:
                return 403, "ID-Code fehlt"
        if path in PATH_CMD:
            cmd = PATH_CMD[path]
            self.spawn(lambda: self.dispatch(cmd, []))
            return 200, "OK"
        if path == "/commands":
            return 200, self.dispatch("list", [])[1]
        if path == "/status":
            ok, msg = self.dispatch("status", [])
            return (200 if ok else 500), msg
        if path == "/command" or path.startswith("/command/"):
            name, args = "", []
            if path.startswith("/command/"):
                parts = [p for p in path.split("/") if p][1:]
                if parts:
                    name, args = parts[0], parts[1:]
            if not name:
                name = query.get("c", [""])[0] or query.get("cmd", [""])[0]
            if not args:
                args = query.get("arg", [])
            if not name:
                return 400, "missing command name"
            ok, msg = self.dispatch(name, args)
            return (200 if ok else 400), msg
        if path.startswith("/vocaster/"):
            cmd = VOC_CMD.get(path)
            if cmd is None:
                return 404, "not found"
            ok, msg = self.dispatch(cmd, [])
            if not ok and "no vocaster" in msg:
                return 503, "Kein Vocaster angeschlossen"
            return (200, "OK") if ok else (500, msg)
        if path.startswith("/preset/"):
            try:
                nr = int(path.split("/preset/")[1])
            except ValueError:                     # Antworttexte wie PunchBuddy (Stream-Deck-Profile werten sie aus)
                return 400, "Ungueltiger Preset Index"
            ok, msg = self.dispatch("preset", [str(nr)])
            if ok and "loaded" in msg:
                return 200, f"Preset {nr} geladen"
            if ok and "debounced" in msg:
                return 200, f"Preset {nr} entprellt"
            return 400, "Ungueltiger Preset Index"
        return 404, "not found"

    def start(self) -> int:
        """Server starten; belegter Port: 4 Versuche, dann freier Port (nicht gespeichert). Rückgabe: Port."""
        trigger = self

        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True

        class Handler(http.server.BaseHTTPRequestHandler):
            timeout = 30                                     # langsame Clients nicht ewig halten

            def do_GET(self) -> None:  # noqa: N802 – Name aus http.server
                parsed = urlparse(self.path)
                try:
                    code, text = trigger.handle(self.path, dict(self.headers), self.client_address[0])
                except Exception as e:  # noqa: BLE001 – Antwort statt Abbruch
                    log.exception("Webtrigger %s%s", parsed.path, _redact(parsed.query))
                    code, text = 500, f"error: {e}"
                log.info("Webtrigger %s%s → %s %s", parsed.path, _redact(parsed.query), code, text[:80])
                self.send_response(code)
                self.send_header("Content-type", "text/plain; charset=utf-8")
                self.end_headers()
                try:
                    self.wfile.write(text.encode("utf-8"))
                except OSError:
                    pass

            def log_message(self, fmt: str, *args) -> None:   # eigenes Protokoll oben
                pass

        cfg = self.config()
        port = cfg.port
        for attempt in range(4):
            try:
                self.httpd = Server((cfg.bind, port), Handler)
                break
            except OSError:
                if attempt < 3:
                    log.warning("Webtrigger: Port %s belegt (Versuch %s/4)", port, attempt + 1)
                    time.sleep(0.5)
                else:
                    log.warning("Webtrigger: Port %s dauerhaft belegt – freier Port (wird nicht gespeichert)", port)
                    self.httpd = Server((cfg.bind, 0), Handler)
        assert self.httpd is not None
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="tm-webtrigger").start()
        log.info("Webtrigger lauscht auf %s:%s – %s", cfg.bind, self.port,
                 ", ".join(f"{p} ({LABELS.get(p, c)})" for p, c in PATH_CMD.items() if p != "/export_aaf_embedded"))
        return self.port

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


# ── Stream-Deck-Socket (Zeilenprotokoll) ───────────────────────────────────
def socket_alive(path: str) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(path)
        return True
    except OSError:
        return False


def parse_line(line: str) -> tuple[str, list[str]] | None:
    """„<befehl> [arg …]“; `token=…` wird toleriert und ignoriert (Socket ist 0600)."""
    parts = line.split()
    if not parts:
        return None
    return parts[0], [a for a in parts[1:] if not a.startswith("token=")]


class LineSocket:
    def __init__(self, dispatch: Dispatch, path: str = DEFAULT_SOCKET) -> None:
        self.dispatch = dispatch
        self.path = os.path.expanduser(path or DEFAULT_SOCKET)
        self._srv: socket.socket | None = None
        self._inode: int | None = None

    def handle_line(self, line: str) -> str:
        parsed = parse_line(line)
        if parsed is None:
            return "ERR empty\n"
        ok, msg = self.dispatch(*parsed)
        log.info("Stream-Deck-Socket: %r → %s %s", parsed[0], "OK" if ok else "ERR", msg[:80])
        return ("OK " if ok else "ERR ") + msg + "\n"

    def start(self) -> None:
        if os.path.exists(self.path):
            if socket_alive(self.path):
                raise RuntimeError(f"Stream-Deck-Socket {self.path} gehört einem laufenden Programm "
                                   "(läuft PunchBuddy noch?) – nicht übernommen")
            os.unlink(self.path)                             # Rest eines abgestürzten Laufs
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        tmp = f"{self.path}.{os.getpid()}"            # erst lauschen, dann unter dem echten Namen zeigen –
        try:                                           # sonst trifft ein früher Befehl auf „connection refused“
            old = os.umask(0o177)
            try:
                srv.bind(tmp)
            finally:
                os.umask(old)
            os.chmod(tmp, 0o600)
            srv.listen(16)
            os.rename(tmp, self.path)
        except OSError:
            srv.close()
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        self._srv = srv
        self._inode = os.stat(self.path).st_ino

        def handle(conn: socket.socket) -> None:
            try:
                conn.settimeout(5.0)
                data = b""
                while b"\n" not in data and len(data) < 4096:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                if b"\n" not in data and len(data) >= 4096:
                    conn.sendall(b"ERR line too long\n")
                    return
                line = data.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
                conn.sendall(self.handle_line(line).encode("utf-8"))
            except OSError as e:
                log.debug("Stream-Deck-Socket: %s", e)
            finally:
                conn.close()

        def serve() -> None:
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    break                                    # geschlossen beim Beenden
                threading.Thread(target=handle, args=(conn,), daemon=True, name="tm-sd-conn").start()
        threading.Thread(target=serve, daemon=True, name="tm-sd-socket").start()
        log.info("Stream-Deck-Socket aktiv: %s (Test: printf 'list' | nc -U %s)", self.path, self.path)

    def stop(self) -> None:
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
            self._srv = None
        try:                                                # nur die eigene Datei entfernen
            if self._inode is not None and os.stat(self.path).st_ino == self._inode:
                os.unlink(self.path)
        except OSError:
            pass
        self._inode = None


def punchbuddy_running() -> bool:
    """Läuft die PunchBuddy-App noch? (Ottilie ersetzt sie auf dem Platz, Jens 02.10.2026)"""
    try:
        out = subprocess.run(["/bin/ps", "-axww", "-o", "command="], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return is_punchbuddy(out)


def is_punchbuddy(ps_output: str) -> bool:
    """App-Bundle oder aus dem Quelltext gestartet (`-axww`: macOS kürzt sonst die Befehlszeile)."""
    for line in ps_output.splitlines():
        if "PunchBuddy.app/Contents/MacOS/PunchBuddy" in line and "Watchdog" not in line:
            return True
        if "auto_punch_in.py" in line and "grep" not in line:
            return True
    return False
