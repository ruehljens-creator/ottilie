"""Mock-Server für Helmut (Entwicklung und Tests, nur Standardbibliothek).

Bildet die Endpunkte, die Ottilie benutzt, im Format der offiziellen Helmut4-OpenAPI ab:
Projektstatus als Objekt, Metadaten mit Set-Verweis
(`parent`), Assets mit `filepath`/`mimeType`/`customMetadata`, Token im Header.
Falsch geformte Anfragen lehnt er mit 400 ab, damit Formatfehler in Tests auffallen.
Fehler lassen sich einspielen: `unavailable` (503), `fail_next` (Statuscodes),
`late_next` (Sekunden: Anfrage wird ausgeführt, die Antwort kommt verspätet), `expire_tokens()` (401 → Neuanmeldung).

Einzeln starten:  python -m tonmischung.helmut.mock --port 8765   (Anmeldung test / test)
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from tonmischung.helmut.mapping import SET_MIX

SET_MIX_ID = "set-mischung"
ASSET_MIME = {"VIDEO", "AUDIO", "IMAGE", "DATA", "PRO_TOOLS_TRACK", "MULTITRACK_SESSION"}


class MockHelmut:
    def __init__(self, users: dict[str, str] | None = None) -> None:
        self.users = users or {"test": "test"}
        self.oauth = {"tm-client": "tm-secret"}
        self.projects: dict[str, dict] = {}
        self.assets: dict[str, list[dict]] = {}
        self.notifications: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, str]] = []
        self.tokens: set[str] = set()
        self.unavailable = False
        self.fail_next: list[int] = []
        self.late_next: list[float] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    # ── Daten ───────────────────────────────────────────────────────────────
    def add_project(self, project_id: str, name: str, group: str, state: str = "bereit",
                    assets: list[str] | None = None, path: str = "", created: str = "", **mix: str) -> dict:
        values = {"zustand": state, **mix}
        meta = [self.meta_entry(k, v) for k, v in values.items()]
        project = {"id": project_id, "name": name, "group": group, "category": "", "template": "",
                   "status": None, "metadata": meta, "path": path,
                   "createDate": created or f"2026-10-03T{6 + len(self.projects):02d}:00:00+00:00"}
        self.projects[project_id] = project
        self.assets[project_id] = [
            {"id": uuid.uuid4().hex, "projectId": project_id, "name": p.rsplit("/", 1)[-1],
             "filepath": p, "mimeType": "DATA" if p.endswith(".aaf") else "VIDEO",
             "lastModified": f"2026-10-02T08:00:{i:02d}Z"}
            for i, p in enumerate(assets or [])]
        return project

    @staticmethod
    def meta_entry(name: str, value: str, set_name: str = SET_MIX, set_id: str = SET_MIX_ID) -> dict:
        """Metadata-Eintrag wie in Helmut: Wert als Text, Set als `parent`."""
        return {"id": uuid.uuid4().hex, "name": name, "value": value, "type": "STRING",
                "parent": [{"id": set_id, "name": set_name, "tags": [], "order": 0}]}

    def mix_value(self, project_id: str, name: str) -> str | None:
        for m in self.projects[project_id]["metadata"]:
            if any(p.get("name") == SET_MIX for p in m.get("parent") or []) and m.get("name") == name:
                return m.get("value")
        return None

    def status(self, project_id: str) -> str | None:
        """Projektstatus als Text (LOCKED, UNLOCKED) oder None."""
        st = self.projects[project_id].get("status")
        return st.get("status") if isinstance(st, dict) else st

    def set_status(self, project_id: str, status: str | None) -> None:
        self.projects[project_id]["status"] = {"status": status, "active": status == "LOCKED"} if status else None

    def expire_tokens(self) -> None:
        self.tokens.clear()

    # ── Server ──────────────────────────────────────────────────────────────
    def start(self, port: int = 0) -> str:
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):   # keine Konsolenausgabe
                pass

            def _reply(self, status: int, body=None, headers=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass                     # Client wartete nicht mehr (Zeitlimit im Test)

            def _handle(self, method: str):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
                    body = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                else:
                    body = json.loads(raw) if raw else None
                url = urlparse(self.path)
                path = unquote(url.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                with mock._lock:
                    mock.requests.append((method, path))
                    if mock.unavailable:
                        return self._reply(503, {"error": "Wartung"})
                    if mock.fail_next:
                        return self._reply(mock.fail_next.pop(0), {"error": "eingespielt"})
                    status, result, headers = mock.route(method, path, body,
                                                         self.headers.get("Authorization", ""), query)
                    late = mock.late_next.pop(0) if mock.late_next else 0.0
                if late:
                    time.sleep(late)
                self._reply(status, result, headers)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PUT(self):
                self._handle("PUT")

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # ── Endpunkte ───────────────────────────────────────────────────────────
    def _login(self, ok: bool, query: dict):
        if not ok:
            return 401, {"error": "Anmeldung fehlgeschlagen"}, None
        token = uuid.uuid4().hex
        self.tokens.add(token)
        body = {"token": token} if query.get("responseWithToken") == "true" else {}
        return 200, body, {"Authorization": f"Bearer {token}"}

    def route(self, method: str, path: str, body, auth: str, query: dict | None = None):
        query = query or {}
        if method == "POST" and path == "/v1/members/auth/login/body":
            b = body or {}
            return self._login(b.get("username") in self.users and self.users[b["username"]] == b.get("password"),
                               query)
        if method == "POST" and path == "/v1/members/auth/oauth":
            b = body or {}
            ok = (b.get("grant_type") == "authorization" and b.get("scope") == "platform"
                  and self.oauth.get(b.get("client_id")) == b.get("client_secret"))
            return self._login(ok, query)
        if not auth.startswith("Bearer ") or auth[7:] not in self.tokens:
            return 401, {"error": "nicht angemeldet"}, None

        if method == "POST" and path == "/v1/fx/projects/search":
            if (body or {}).get("type") != "FX" or not isinstance((body or {}).get("filter"), list):
                return 400, {"error": "SearchFilter erwartet (type FX, filter [])"}, None
            return 200, [copy.deepcopy(p) for p in self.projects.values()], None
        m = re.fullmatch(r"/v1/fx/projects/metadata/([^/]+)", path)
        if method == "PUT" and m:
            p = self.projects.get(m.group(1))
            if not p:
                return 404, {"error": "unbekannt"}, None
            meta = (body or {}).get("metadata")
            if not isinstance(meta, list) or any("parent" not in x or "value" not in x for x in meta):
                return 400, {"error": "ProjectPatchMetadata erwartet"}, None
            p["metadata"] = copy.deepcopy(meta)
            return 200, copy.deepcopy(p), None
        if method == "POST" and path == "/v1/fx/projects/duplicate":
            src = self.projects.get((body or {}).get("id"))
            if not src or not (body or {}).get("name"):
                return 400, {"error": "Project mit id und name erwartet"}, None
            # wie vermutlich in Helmut: Projekt mit Metadaten, ohne Assets (Plan M4-Rest A2)
            new = copy.deepcopy(src)
            new["id"] = f"{src['id']}-dup{sum(1 for k in self.projects if k.startswith(src['id'] + '-dup')) + 1}"
            new["name"] = body["name"]
            self.projects[new["id"]] = new
            self.assets[new["id"]] = []
            return 200, copy.deepcopy(new), None
        m = re.fullmatch(r"/v1/fx/projects/([^/]+)", path)
        if method == "GET" and m:
            p = self.projects.get(m.group(1))
            return (200, copy.deepcopy(p), None) if p else (404, {"error": "unbekannt"}, None)
        if method == "PUT" and path == "/v1/fx/projects":
            pid = (body or {}).get("id")
            if pid not in self.projects:
                return 404, {"error": "unbekannt"}, None
            self.projects[pid] = copy.deepcopy(body)
            return 200, copy.deepcopy(body), None
        m = re.fullmatch(r"/v1/fx/projects/status/([^/]+)", path)
        if method == "PUT" and m:
            p = self.projects.get(m.group(1))
            if not p:
                return 404, {"error": "unbekannt"}, None
            if not isinstance(body, dict) or "status" not in body:
                return 400, {"error": "ProjectStatus erwartet"}, None
            self.set_status(p["id"], body["status"])
            return 200, copy.deepcopy(p), None
        m = re.fullmatch(r"/v1/co/assets/([^/]+)", path)
        if method == "GET" and m:
            return 200, copy.deepcopy(self.assets.get(m.group(1), [])), None
        if method == "POST" and path == "/v1/co/assets":
            if not (body or {}).get("filepath") or (body or {}).get("mimeType") not in ASSET_MIME:
                return 400, {"error": "Asset braucht filepath und mimeType"}, None
            asset = {**(body or {}), "id": uuid.uuid4().hex,
                     "name": body["filepath"].rsplit("/", 1)[-1]}
            self.assets.setdefault(asset.get("projectId", ""), []).append(asset)
            return 200, copy.deepcopy(asset), None
        m = re.fullmatch(r"/v1/members/users/notification/([^/]+)", path)
        if method == "POST" and m:
            if not isinstance((body or {}).get("msg"), str):
                return 400, {"error": "Message erwartet (msg)"}, None
            self.notifications.append((m.group(1), body))
            return 200, {"username": m.group(1)}, None
        return 404, {"error": f"{method} {path} nicht im Mock"}, None


def sample() -> MockHelmut:
    """Beispieldaten wie im Prototyp."""
    mock = MockHelmut()
    base = "/Volumes/Helmut/Heimatjournal"
    mock.add_project("p1", "Bahnstreik: Pendler warten", "Heimatjournal",
                     assets=[f"{base}/Bahnstreik/Uebergabe/Bahnstreik.aaf",
                             f"{base}/Bahnstreik/Uebergabe/Bahnstreik_ref.mxf"])
    mock.add_project("p2", "Apfelweinfest in Sachsenhausen", "Heimatjournal",
                     assets=[f"{base}/Apfelweinfest/Uebergabe/Apfelweinfest.aaf",
                             f"{base}/Apfelweinfest/Uebergabe/Apfelweinfest_ref.mxf"])
    mock.add_project("p3", "Kita-Streik", "Heimatjournal", state="in_mischung",
                     mischer="A. Wolf", raum="Ton 2")
    return mock


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Helmut-Mock für die Entwicklung")
    ap.add_argument("--port", type=int, default=8765)
    port = ap.parse_args().port
    url = sample().start(port)
    print(f"Helmut-Mock läuft auf {url} (Anmeldung test / test). Beenden mit Strg+C.")
    threading.Event().wait()
