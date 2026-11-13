"""Starlette-Anwendung der Zentrale: Web-App, Kopplung, WebSocket, Administration, Gesundheitsprüfung.

Start im Container: `python -m app.run` (uvicorn, Port 8470, ohne TLS). Davor ein Proxy mit TLS (tailscale serve,
nginx). `X-Forwarded-For` gilt nur von den vertrauenswürdigen Proxy-Adressen (uvicorn `forwarded_allow_ips`).
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import os
import time
from collections import deque
from collections.abc import Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from app import VERSION
from app import admin as admin_pages
from app import admin2
from app.auth import Auth, AuthError
from app.backup import Backups
from app.chat import ChatStore
from app.flows import Flows, chat_settings
from app.db import Database
from app.hub import LOGIN_TIMEOUT_S, MAX_MESSAGE, Conn, Hub, Waiting
from app.log import Log

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
PAIR_LIMIT = (10, 600.0)                    # höchstens 10 Kopplungsversuche je Adresse in 10 min
PAIR_LIMIT_ALL = 100                        # und höchstens 100 insgesamt (gefälschte Adressen, Sicherheit)
WAITING_PER_MIN = 20                        # wartende Verbindung: Nachrichten je Minute (sonst getrennt)
MAX_BODY = 64 * 1024


async def read_limited(request: Request, limit: int) -> bytes:
    """Anfragekörper lesen, aber höchstens `limit` Bytes (sonst ValueError) – nichts Großes in den Speicher."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ValueError("zu groß")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise ValueError("zu groß")
    return bytes(data)


SECURITY_HEADERS = {"X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                                               "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                                               "frame-ancestors 'none'; base-uri 'none'"}


class WebHeaders:
    """Sicherheits-Kopfzeilen für die Web-App-Dateien unter /web/ (CSP, kein Einbetten)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or "/web/" not in scope.get("path", ""):
            await self.app(scope, receive, send)
            return

        async def with_headers(message) -> None:
            if message["type"] == "http.response.start":
                have = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()
                         if k.lower().encode() not in have]
                message = {**message, "headers": list(message.get("headers", [])) + extra}
            await send(message)
        await self.app(scope, receive, with_headers)


class Context:
    def __init__(self, data_dir: str, clock: Callable[[], float] = time.time) -> None:
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.db = Database(os.path.join(data_dir, "zentrale.db"))
        self.log = Log(self.db, clock)
        self.auth = Auth(self.db, clock)
        self.hub = Hub(self.auth, self.log, clock)
        self.backups = Backups(self.db, os.path.join(data_dir, "sicherungen"), self.log, clock)
        self.chat = ChatStore(os.path.join(data_dir, "chat.db"), clock)     # nicht in den Sicherungen
        from app.work import Work
        self.work = Work(self.db, clock)
        self.flows = Flows(self.hub, self.chat, self.work)
        self.clock = clock
        self._pair_tries: dict[str, dict[str, deque]] = {}
        self._login_tries: dict[str, deque] = {}

    def allowed(self, host: str | None) -> bool:
        """Zugelassene Netze (Administration → Server); leer = alle."""
        nets = self.db.setting("server.zugelassene_netze", []) or []
        if not nets:
            return True
        try:
            ip = ipaddress.ip_address(host or "")
        except ValueError:
            return False
        for n in nets:
            try:
                if ip in ipaddress.ip_network(str(n), strict=False):
                    return True
            except ValueError:
                continue
        return False

    def login_allowed(self, host: str) -> bool:
        """Anmeldungen der Administration: höchstens 20 je Adresse in 10 min (bremst Durchprobieren und
        Aussperren durch Fremde; scrypt kostet Rechenzeit)."""
        t = self.clock()
        q = self._login_tries.setdefault(host, deque())
        while q and q[0] < t - 600:
            q.popleft()
        if len(self._login_tries) > 10000:
            self._login_tries = {host: q}
        if len(q) >= 20:
            return False
        q.append(t)
        return True

    def pair_allowed(self, host: str, bucket: str = "koppeln") -> bool:
        """Bremse je Adresse und gesamt – getrennt für Koppeln mit Code und Anmelden ohne Code (Sicherheit 13.11.2026:
        sonst sperrte eine Flut von Anmeldungen auch das Koppeln mit Code für alle)."""
        t = self.clock()
        tries = self._pair_tries.setdefault(bucket, {})
        for h in list(tries):                         # Alte weg – die Tabelle wächst nicht ohne Grenze
            q = tries[h]
            while q and q[0] < t - PAIR_LIMIT[1]:
                q.popleft()
            if not q:
                del tries[h]
        if sum(len(q) for q in tries.values()) >= PAIR_LIMIT_ALL:
            return False
        q = tries.setdefault(host, deque())
        if len(q) >= PAIR_LIMIT[0]:
            return False
        q.append(t)
        return True


def create_app(data_dir: str | None = None, clock: Callable[[], float] = time.time,
               background: bool = True) -> Starlette:
    ctx = Context(data_dir or os.environ.get("ZENTRALE_DATEN", "/data"), clock)

    async def health(request: Request) -> Response:
        try:
            ctx.db.one("SELECT 1")
            return JSONResponse({"ok": True, "version": VERSION, "geraete_verbunden": len(ctx.hub.conns)})
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": False}, status_code=503)

    async def pair(request: Request) -> Response:
        host = request.client.host if request.client else ""
        if not ctx.allowed(host):
            return JSONResponse({"fehler": "Nicht zugelassen"}, status_code=403)
        if not ctx.pair_allowed(host):
            ctx.log.warn_limited(f"pair-limit:{host}", "kopplung", f"Zu viele Kopplungsversuche von {host}")
            return JSONResponse({"fehler": "Zu viele Versuche – bitte später erneut"}, status_code=429)
        try:
            body = json.loads(await read_limited(request, MAX_BODY))
        except (ValueError, json.JSONDecodeError):
            return JSONResponse({"fehler": "Kein JSON oder zu groß"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"fehler": "Kein JSON-Objekt"}, status_code=400)
        try:
            key, dev = await asyncio.to_thread(ctx.auth.pair, str(body.get("code") or ""),
                                               str(body.get("geraet") or ""))
        except AuthError as e:
            ctx.log.warn_limited(f"pair:{host}", "kopplung", f"Kopplung abgelehnt ({host}): {e}")
            return JSONResponse({"fehler": str(e)}, status_code=400)
        ctx.log.info("kopplung", f"Gerät gekoppelt ({host})", room=dev.room, device=dev.name)
        return JSONResponse({"schluessel": key, "raum": dev.room, "geraet": dev.name, "geraet_id": dev.id})

    async def register(request: Request) -> Response:
        """Anmelden ohne Code (Jens 13.11.2026): Schlüssel für ein wartendes Gerät. Es sieht nichts, bis die
        Administration es einem Raum zuordnet. Gleiche Bremse wie beim Koppeln, jede Anfrage mit Adresse im
        Protokoll."""
        host = request.client.host if request.client else ""
        if not ctx.allowed(host):
            return JSONResponse({"fehler": "Nicht zugelassen"}, status_code=403)
        if not ctx.pair_allowed(host, "anmelden"):
            ctx.log.warn_limited(f"pair-limit:{host}", "kopplung", f"Zu viele Anmeldeversuche von {host}")
            return JSONResponse({"fehler": "Zu viele Versuche – bitte später erneut"}, status_code=429)
        try:
            body = json.loads(await read_limited(request, MAX_BODY))
        except (ValueError, json.JSONDecodeError):
            return JSONResponse({"fehler": "Kein JSON oder zu groß"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"fehler": "Kein JSON-Objekt"}, status_code=400)
        try:
            key, pend = await asyncio.to_thread(ctx.auth.register, str(body.get("geraet") or ""), host)
        except AuthError as e:
            ctx.log.warn_limited(f"register:{host}", "kopplung", f"Anmeldung ohne Code abgelehnt ({host}): {e}")
            return JSONResponse({"fehler": str(e)}, status_code=429)
        ctx.log.info("kopplung", f"Gerät wartet auf Zuordnung ({host}, Kennung {pend.fingerprint})",
                     device=pend.name)
        return JSONResponse({"schluessel": key, "kennung": pend.fingerprint, "geraet": pend.name, "wartet": True})

    async def waiting(websocket: WebSocket, pend, host: str) -> None:
        """Wartendes Gerät: nur „wartet“, alles andere wird verworfen – kein Raumstatus, kein Chat, kein Belegen."""
        lock = asyncio.Lock()

        async def send(m: dict) -> None:
            async with lock:
                await websocket.send_json(m)

        async def close(code: int) -> None:
            await websocket.close(code=code)
        w = Waiting(pend, send, close, host=host)
        recent: deque = deque()
        try:
            await ctx.hub.attach_waiting(w)
            while True:
                raw = await websocket.receive_text()
                t = ctx.clock()
                recent.append(t)
                while recent and recent[0] < t - 60:
                    recent.popleft()
                if len(recent) > WAITING_PER_MIN:           # Ottilie schickt zwei Lebenszeichen je Minute
                    ctx.log.warn_limited(f"wait-flood:{host}", "verbindung",
                                         f"Wartendes Gerät sendet zu viel ({host}) – getrennt", device=pend.name)
                    break
                if len(raw) <= MAX_MESSAGE:
                    ctx.auth.seen_pending(pend.id)              # nur Lebenszeichen zählen – nichts wird verarbeitet
        except (WebSocketDisconnect, ValueError):
            pass
        except Exception as e:  # noqa: BLE001
            ctx.log.error("verbindung", f"Wartende Verbindung abgebrochen: {type(e).__name__}", device=pend.name)
        finally:
            ctx.hub.detach_waiting(w)
            with contextlib.suppress(Exception):
                await websocket.close(code=1000)

    async def ws(websocket: WebSocket) -> None:
        host = websocket.client.host if websocket.client else ""
        if not ctx.allowed(host):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), LOGIN_TIMEOUT_S)
            msg = json.loads(raw) if len(raw) <= MAX_MESSAGE else {}
        except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
            with contextlib.suppress(Exception):
                await websocket.close(code=4401)
            return
        key = (msg.get("daten") or {}).get("schluessel") if isinstance(msg, dict) else None
        dev = ctx.auth.device(key) if isinstance(msg, dict) and msg.get("typ") == "anmelden" else None
        pend = ctx.auth.pending(key) if dev is None and isinstance(msg, dict) and msg.get("typ") == "anmelden" else None
        if pend is not None:
            await waiting(websocket, pend, host)
            return
        if dev is None:
            ctx.log.warn_limited(f"ws:{host}", "verbindung", f"Anmeldung am WebSocket abgelehnt ({host})")
            with contextlib.suppress(Exception):
                await websocket.send_json({"typ": "abgemeldet", "daten": {"text": "Gerät nicht gekoppelt"}})
                await websocket.close(code=4401)
            return
        lock = asyncio.Lock()

        async def send(m: dict) -> None:
            async with lock:
                await websocket.send_json(m)

        async def close(code: int) -> None:
            await websocket.close(code=code)
        conn = Conn(dev, send, close, host=host)
        try:
            await ctx.hub.attach(conn)
            while True:
                raw = await websocket.receive_text()
                await ctx.hub.handle(conn, raw)
        except WebSocketDisconnect:
            pass
        except Exception as e:  # noqa: BLE001
            ctx.log.error("verbindung", f"Verbindung abgebrochen: {type(e).__name__}", room=dev.room,
                          device=dev.name)
        finally:
            await ctx.hub.detach(conn)

    async def helmut_event(request: Request) -> Response:
        """Stufe 3 B: Helmut meldet „Beitrag übergeben“ (eigener Schlüssel, nur als Hash gespeichert)."""
        import hmac as _hmac

        from app.auth import sha
        host = request.client.host if request.client else ""
        stored = ctx.db.setting("helmut.ereignis_schluessel_hash", "") or ""
        auth_header = request.headers.get("authorization", "")
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
        if not ctx.allowed(host):
            return JSONResponse({"fehler": "Nicht zugelassen"}, status_code=403)
        if not stored or not token or not _hmac.compare_digest(sha(token), stored):
            ctx.log.warn_limited(f"helmut:{host}", "helmut",
                                 f"Ereignis abgelehnt (Schlüssel fehlt oder falsch, {host})")
            return JSONResponse({"fehler": "Nicht berechtigt"}, status_code=401)
        try:
            body = json.loads(await read_limited(request, MAX_BODY))
        except (ValueError, json.JSONDecodeError):
            return JSONResponse({"fehler": "Kein JSON oder zu groß"}, status_code=400)
        if not isinstance(body, dict) or body.get("ereignis") != "beitrag_uebergeben":
            return JSONResponse({"fehler": "Unbekanntes Ereignis"}, status_code=400)
        beitrag = str(body.get("ordner") or body.get("beitrag_id") or "").strip()[:200]
        if not beitrag:
            return JSONResponse({"fehler": "beitrag_id oder ordner fehlt"}, status_code=400)
        await ctx.flows.handed_over(beitrag, str(body.get("titel") or "")[:200],
                                    str(body.get("schnittraum") or "")[:100], "Helmut")
        return JSONResponse({"ok": True})

    async def index(request: Request) -> Response:
        return FileResponse(os.path.join(WEB, "index.html"),
                            headers={"Cache-Control": "no-cache", **SECURITY_HEADERS})

    async def service_worker(request: Request) -> Response:
        return FileResponse(os.path.join(WEB, "sw.js"), media_type="text/javascript",
                            headers={"Cache-Control": "no-cache"})

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        tasks = []
        if background:
            tasks = [asyncio.create_task(ctx.hub.heartbeat()), asyncio.create_task(_daily(ctx))]
            from app import watch
            roots = watch.roots_from(os.environ.get("ZENTRALE_UEBERGABE", "/mnt/uebergabe"),
                                     os.environ.get("ZENTRALE_UEBERGABE_WEITERE", ""))
            ctx.watch = watch.start_all(ctx, asyncio.get_running_loop(), roots)
            ctx.flows.watching = lambda: ctx.watch is not None and ctx.watch.state == "beobachtet"
            ctx.flows.watched_keys = lambda: ctx.watch.keys() if ctx.watch is not None else []
        ctx.log.info("server", f"Zentrale {VERSION} gestartet")
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            if getattr(ctx, "watch", None) is not None:
                ctx.watch.stop()

    routes = [Route("/", index), Route("/sw.js", service_worker), Route("/gesund", health),
              Route("/api/koppeln", pair, methods=["POST"]), Route("/api/anmelden", register, methods=["POST"]),
              WebSocketRoute("/ws", ws),
              Route("/api/helmut/ereignis", helmut_event, methods=["POST"]),
              *admin_pages.routes(ctx), *admin2.routes(ctx), Mount("/web", StaticFiles(directory=WEB), name="web")]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(WebHeaders)
    app.state.ctx = ctx
    return app


async def _daily(ctx: Context) -> None:
    """Täglich: Sicherung (7 behalten), Protokoll aufräumen; jede Minute: Debug-Frist prüfen."""
    last_day = ""
    while True:
        ctx.log.debug_state()                         # schaltet Debug nach Ablauf selbst ab
        try:
            n = ctx.chat.expire(chat_settings(ctx.db)["haltezeit_h"])
            if n:
                ctx.log.info("chat", f"{n} Nachrichten nach der Haltezeit gelöscht (nicht allen zugestellt)")
        except Exception as e:  # noqa: BLE001
            ctx.log.error("chat", f"Aufräumen gescheitert: {type(e).__name__}")
        day = time.strftime("%Y%m%d", time.localtime(ctx.clock()))
        if day != last_day:
            last_day = day
            try:
                ctx.backups.daily()
                ctx.log.cleanup()
                ctx.work.items_of("")                     # alte „Meine Beiträge“ (7 Tage) aufräumen
            except Exception as e:  # noqa: BLE001
                ctx.log.error("betrieb", f"Tägliche Aufgabe gescheitert: {type(e).__name__}: {e}")
        await asyncio.sleep(60)
