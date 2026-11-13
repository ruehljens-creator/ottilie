"""Administration der Zentrale (Seiten ohne JavaScript-Rahmenwerk, auf dem Server erzeugt).

Sicherheit: Sitzungs-Cookie HttpOnly, SameSite=Strict, Secure unter https, Pfad nur „…/admin“; jedes Formular trägt
das CSRF-Token der Sitzung (Anmeldeformular: eigenes Doppel-Cookie). Bei Kennwortzwang ist nur die Kennwortseite
erreichbar. Gekoppelte Geräte haben hier keinen Zugang (eigene Anmeldung, kein Geräteschlüssel).
"""
from __future__ import annotations

import csv
import hmac
import html
import io
import re
import secrets
import time
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from app.auth import AuthError, Session
from app.db import ROOM_KINDS
from app.log import DEBUG_SPANS_H, LEVELS

if TYPE_CHECKING:
    from app.main import Context

COOKIE, LOGIN_COOKIE = "zentrale_sitzung", "zentrale_anmeldung"
DEVICE_Q = ("Gerät „{}“ (Raum {}) abmelden? Der Mac verliert sofort die Verbindung und die zentral vorgegebenen "
            "Einstellungen (Ottilie übernimmt das erst, wenn dort nichts mehr in Arbeit ist).")
ROOM_Q = "Raum „{}“ samt {} Geräten entfernen? Diese Macs verlieren sofort die Verbindung."
REJECT_Q = "Anmeldung von „{}“ (Kennung {}) ablehnen? Der Mac muss sich danach neu anmelden."
STATE_TEXT = {"frei": "frei", "uebernommen": "übernommen", "importiert": "importiert", "mischt": "in Mischung",
              "exportiert": "exportiert"}
MODE_TEXT = {"uebergabeordner": "Übergabeordner", "helmut_api": "Helmut-API"}
KIND_TEXT = {"mischraum": "Mischraum", "schnittraum": "Schnittraum", "sonstiger": "sonstiger Raum"}
LEVEL_TEXT = {"fehler": "Fehler", "warnung": "Warnung", "info": "Info", "debug": "Debug"}
SERVER_KEYS = {"server.oeffentliche_adresse": str, "server.zeitzone": str, "server.zugelassene_netze": list,
               "protokoll.aufbewahrung_tage": int, "protokoll.debug_aufbewahrung_tage": int, "admin.sitzung_h": int}
MAX_UPLOAD = 200 << 20

CSS = """
:root{--bg:#f6f7f8;--fg:#1c2024;--mut:#5d6670;--line:#d6dadf;--acc:#0b5cad;--warn:#8a5a00;--bad:#b3261e;--ok:#1e7a3a;
--ctrl:#848d97;--on-acc:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#16181b;--fg:#e8eaed;--mut:#a3abb5;--line:#33383e;--acc:#7cb4ff;
--warn:#f0c060;--bad:#ff8a80;--ok:#7bd88f;--ctrl:#6b7480;--on-acc:#16181b}}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);
color:var(--fg)}header{display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:10px 16px;border-bottom:1px
solid var(--line)}header b{margin-right:8px}.brand{display:flex;align-items:center;gap:8px}
.brand img{display:block}nav a{margin-right:12px;color:var(--acc);text-decoration:none}
main{max-width:1000px;margin:0 auto;padding:16px}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px
solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}input,select,textarea,button{font:inherit;
padding:5px 8px;border:1px solid var(--ctrl);border-radius:6px;background:transparent;color:inherit}
textarea{width:100%;max-width:100%}
button{cursor:pointer}button.primary{background:var(--acc);color:var(--on-acc);border-color:var(--acc)}
.banner.bad{border-color:var(--bad)}.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
white-space:nowrap}
.banner{padding:8px 12px;border-radius:6px;margin:0 0 12px;border:1px solid var(--line)}.warn{color:var(--warn)}
.bad{color:var(--bad)}.ok{color:var(--ok)}.mut{color:var(--mut)}.mono{font-family:ui-monospace,Menlo,monospace}
form.inline{display:inline}fieldset{border:1px solid var(--line);border-radius:8px;margin:0 0 16px}
label{display:block;margin:6px 0}.code{font:600 28px ui-monospace,Menlo,monospace;letter-spacing:4px}
"""


def esc(v) -> str:
    return html.escape(str(v), quote=True)


_SAFE_ROOT = re.compile(r"/[A-Za-z0-9._~/-]*")


def _root(request: Request) -> str:
    r = request.scope.get("root_path", "") or ""
    return esc(r) if _SAFE_ROOT.fullmatch(r) else ""             # nur harmlose Pfade, immer maskiert


def _when(t: float) -> str:
    return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(t)) if t else "–"


def _cell(value) -> str:
    """CSV-Zelle ohne Formel: Gerätenamen kommen ohne Anmeldung ins Protokoll (Sicherheit 13.11.2026) – eine Zelle wie
    „=HYPERLINK(…)“ würde eine Tabellenkalkulation sonst ausführen."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def feedback(note: str, error: bool = False) -> str:
    """Rückmeldung nach dem Absenden: Fehler deutlich anders als Erfolg, mit Fokus für VoiceOver und Tastatur
    (Barrierefreiheit 13.11.2026)."""
    return (f'<p class="banner{" bad" if error else ""}" id="hinweis" tabindex="-1"'
            f'{" role=alert" if error else ""}>{"Fehler: " if error else ""}{esc(note)}</p>'
            '<script>document.getElementById("hinweis").focus()</script>')


def page(request: Request, title: str, body: str, session: Session | None = None, status: int = 200,
         ctx: "Context | None" = None) -> HTMLResponse:
    r = _root(request)
    nav = ""
    banner = ""
    if session and not session.must_change:
        links = [("", "Übersicht"), ("/raeume", "Räume & Geräte"), ("/uebergabeordner", "Übergabeordner"),
                 ("/chat", "Chat"), ("/helmut", "Helmut"), ("/belegungen", "Belegungen"), ("/server", "Server"),
                 ("/protokoll", "Protokoll"), ("/admins", "Administratoren"), ("/betrieb", "Betrieb")]
        nav = "<nav>" + "".join(f'<a href="{r}/admin{p}">{t}</a>' for p, t in links) + "</nav>"
        nav += (f'<form class="inline" method="post" action="{r}/admin/abmelden">'
                f'<input type="hidden" name="csrf" value="{esc(session.csrf)}"><button>Abmelden '
                f'({esc(session.admin)})</button></form>')
    if ctx is not None and session and not session.must_change:
        st = ctx.log.debug_state()
        if st["aktiv"]:
            banner = (f'<div class="banner warn">Debug-Protokoll ist an – noch {st["rest_s"] // 3600} h '
                      f'{st["rest_s"] % 3600 // 60} min, danach schaltet es sich selbst ab.</div>')
    doc = (f'<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" '
           f'content="width=device-width,initial-scale=1"><title>{esc(title)} – Ottilie-Zentrale</title>'
           f'<link rel="icon" href="{r}/web/ottilie-192.png" type="image/png">'
           f'<style>{CSS}</style></head><body><header><span class="brand"><img src="{r}/web/ottilie-192.png" alt="" '
           f'width="32" height="32"><b>Ottilie-Zentrale</b></span>{nav}</header><main>{banner}'
           f'<h1>{esc(title)}</h1>{body}</main></body></html>')
    return HTMLResponse(doc, status_code=status, headers={"Cache-Control": "no-store",
                                                          "X-Frame-Options": "DENY",
                                                          "Referrer-Policy": "no-referrer",
                                                          "Content-Security-Policy":
                                                              "default-src 'self'; style-src 'unsafe-inline'; "
                                                              "script-src 'self' 'unsafe-inline'; "
                                                              "frame-ancestors 'none'"})


async def _form(request: Request) -> dict[str, str]:
    from app.main import MAX_BODY, read_limited
    try:
        body = await read_limited(request, MAX_BODY)
    except ValueError:
        return {}
    return {k: v[-1] for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}


def _csrf(session: Session, form: dict) -> bool:
    return hmac.compare_digest(str(form.get("csrf", "")), session.csrf)


def routes(ctx: "Context") -> list[Route]:
    auth, log, db = ctx.auth, ctx.log, ctx.db

    def guard(request: Request, allow_change: bool = False) -> Session | Response:
        if not ctx.allowed(request.client.host if request.client else ""):
            return Response("Nicht zugelassen", status_code=403)
        s = auth.session(request.cookies.get(COOKIE))
        if s is None:
            return RedirectResponse(f"{_root(request)}/admin/anmelden", status_code=303)
        if s.must_change and not allow_change:
            return RedirectResponse(f"{_root(request)}/admin/kennwort", status_code=303)
        return s

    def secure(request: Request) -> bool:
        return request.url.scheme == "https"

    def back(request: Request, path: str, note: str = "", error: bool = False) -> Response:
        from urllib.parse import quote
        q = f"?hinweis={quote(note)}{'&fehler=1' if error else ''}" if note else ""
        return RedirectResponse(f"{_root(request)}/admin{path}{q}", status_code=303)

    async def posted(request: Request) -> tuple[Session, dict] | Response:
        s = guard(request)
        if not isinstance(s, Session):
            return s
        form = await _form(request)
        if not _csrf(s, form):
            log.warn("admin", f"CSRF-Prüfung gescheitert ({s.admin})")
            return Response("Ungültige Anfrage (CSRF)", status_code=403)
        return s, form

    # ── Anmelden, Abmelden, Kennwort ──────────────────────────────────────────
    async def login(request: Request) -> Response:
        if not ctx.allowed(request.client.host if request.client else ""):
            return Response("Nicht zugelassen", status_code=403)
        error = ""
        if request.method == "POST":
            form = await _form(request)
            if not hmac.compare_digest(str(form.get("csrf", "")), request.cookies.get(LOGIN_COOKIE, "") or "x"):
                error = "Bitte erneut versuchen (Formular abgelaufen)"
            elif not ctx.login_allowed(request.client.host if request.client else ""):
                log.warn("admin", "Zu viele Anmeldeversuche von einer Adresse")
                error = "Zu viele Versuche – bitte später erneut"
            else:
                name = form.get("name", "").strip()[:40]
                try:
                    import asyncio
                    token, s = await asyncio.to_thread(auth.login, name, form.get("kennwort", ""))
                except AuthError as e:
                    known = db.one("SELECT 1 FROM admins WHERE name=?", (name,)) is not None
                    # nie ein unbekannter Name ins Protokoll – oft ist es das versehentlich getippte Kennwort
                    who = f"Konto „{name}“" if known else "unbekanntes Konto"
                    log.warn("admin", f"Anmeldung fehlgeschlagen ({who}): {e}")
                    error = "Anmeldung fehlgeschlagen oder Konto vorübergehend gesperrt"
                else:
                    log.info("admin", f"Angemeldet: {name}")
                    resp = RedirectResponse(f"{_root(request)}/admin{'/kennwort' if s.must_change else ''}",
                                            status_code=303)
                    resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure(request),
                                    path=f"{_root(request)}/admin")
                    resp.delete_cookie(LOGIN_COOKIE, path=f"{_root(request)}/admin")
                    return resp
        nonce = secrets.token_urlsafe(24)
        body = (f'<p class="bad">{esc(error)}</p>' if error else "") + (
            f'<form method="post"><input type="hidden" name="csrf" value="{nonce}">'
            '<label>Name <input name="name" autocomplete="username" required autofocus></label>'
            '<label>Kennwort <input name="kennwort" type="password" autocomplete="current-password" required>'
            '</label><button class="primary">Anmelden</button></form>')
        resp = page(request, "Anmelden", body, status=400 if error else 200)
        resp.set_cookie(LOGIN_COOKIE, nonce, httponly=True, samesite="strict", secure=secure(request),
                        path=f"{_root(request)}/admin", max_age=1800)
        return resp

    async def logout(request: Request) -> Response:
        s = auth.session(request.cookies.get(COOKIE))
        form = await _form(request)
        if s and _csrf(s, form):
            auth.logout(request.cookies.get(COOKIE))
            log.info("admin", f"Abgemeldet: {s.admin}")
        resp = RedirectResponse(f"{_root(request)}/admin/anmelden", status_code=303)
        resp.delete_cookie(COOKIE, path=f"{_root(request)}/admin")
        return resp

    async def password(request: Request) -> Response:
        s = guard(request, allow_change=True)
        if not isinstance(s, Session):
            return s
        error = ""
        if request.method == "POST":
            form = await _form(request)
            if not _csrf(s, form):
                return Response("Ungültige Anfrage (CSRF)", status_code=403)
            if form.get("neu", "") != form.get("neu2", ""):
                error = "Die beiden neuen Kennwörter stimmen nicht überein"
            else:
                try:
                    auth.change_password(s.admin, form.get("alt", ""), form.get("neu", ""),
                                         request.cookies.get(COOKIE))
                except AuthError as e:
                    error = str(e)
                else:
                    log.info("admin", f"Kennwort geändert: {s.admin}")
                    return back(request, "")
        lead = ('<p class="banner warn">Bitte zuerst ein eigenes Kennwort festlegen (mindestens 10 Zeichen). Vorher '
                'ist keine andere Seite erreichbar.</p>' if s.must_change else "")
        body = lead + (f'<p class="bad">{esc(error)}</p>' if error else "") + (
            f'<form method="post"><input type="hidden" name="csrf" value="{esc(s.csrf)}">'
            '<label>Bisheriges Kennwort <input name="alt" type="password" autocomplete="current-password" required>'
            '</label><label>Neues Kennwort <input name="neu" type="password" autocomplete="new-password" '
            'minlength="10" required></label><label>Neues Kennwort wiederholen <input name="neu2" type="password" '
            'autocomplete="new-password" minlength="10" required></label><button class="primary">Speichern</button>'
            '</form>')
        return page(request, "Kennwort ändern", body, s, status=400 if error else 200)

    # ── Übersicht ─────────────────────────────────────────────────────────────
    async def overview(request: Request) -> Response:
        s = guard(request)
        if not isinstance(s, Session):
            return s
        rows = "".join(
            f'<tr><td>{esc(r["raum"])}</td><td>{esc(KIND_TEXT.get(r.get("art"), ""))}</td>'
            f'<td class="{"ok" if r["verbunden"] else "mut"}">{"verbunden" if r["verbunden"] else "nicht verbunden"}'
            f'</td><td>{esc(MODE_TEXT.get(r.get("betriebsart", ""), r.get("betriebsart", "")))}</td>'
            f'<td>{esc(r.get("beitrag_titel", ""))}</td>'
            f'<td>{esc(STATE_TEXT.get(r.get("zustand", ""), ""))}'
            f'{" " + str(r["fortschritt"]) + " %" if r.get("fortschritt") else ""}'
            f'</td></tr>' for r in ctx.hub.overview())
        devices = auth.devices()
        body = (f'<p>{len(ctx.hub.conns)} Geräte verbunden, {sum(1 for d in devices if not d["revoked"])} gekoppelt.'
                '</p><table><tr><th>Raum</th><th>Art</th><th>Verbindung</th><th>Betriebsart</th><th>Beitrag</th>'
                f'<th>Zustand</th></tr>{rows or "<tr><td colspan=6>Noch keine Räume</td></tr>"}</table>')
        return page(request, "Übersicht", body, s, ctx=ctx)

    # ── Räume und Geräte ──────────────────────────────────────────────────────
    async def rooms(request: Request) -> Response:
        s = guard(request)
        if not isinstance(s, Session):
            return s
        hidden = f'<input type="hidden" name="csrf" value="{esc(s.csrf)}">'
        r = _root(request)
        note = request.query_params.get("hinweis", "")
        devs = auth.devices()
        all_rooms = auth.rooms()

        def room_options(skip: str = "") -> str:
            # Pflichtauswahl ohne Vorauswahl (IT-Administrator 13.11.2026: sonst legte ein Klick den Mac in den
            # alphabetisch ersten Raum); Räume mit Gerät und Schnitträume gekennzeichnet
            taken = {d["room"] for d in devs if not d["revoked"]}
            return '<option value="">– Raum wählen –</option>' + "".join(
                f'<option value="{x["id"]}">{esc(x["name"])}'
                f'{" (Schnittraum)" if x.get("kind") == "schnittraum" else ""}'
                f'{" – hat schon ein Gerät" if x["name"] in taken else ""}</option>'
                for x in all_rooms if x["name"] != skip)

        def spelled(code: str) -> str:
            """Code für VoiceOver Zeichen für Zeichen (Barrierefreiheit 13.11.2026)."""
            return f'<span aria-hidden="true">{esc(code)}</span><span class="sr">{esc(" ".join(code))}</span>'
        parts = []
        waiting = auth.pending_list()
        if waiting:
            rows = "".join(
                f'<tr><td>{esc(p.name)}</td><td class="code">{spelled(p.fingerprint)}</td><td>{esc(p.host)}</td>'
                f'<td>{_when(p.created)}</td><td>'
                + (f'<form class="inline" method="post" action="{r}/admin/wartend/{p.id}/zuordnen">{hidden}'
                   f'<input type="hidden" name="kennung" value="{esc(p.fingerprint)}">'
                   f'<select name="raum" required aria-label="Raum für {esc(p.name)}, Code '
                   f'{esc(" ".join(p.fingerprint))}">{room_options()}</select> '
                   f'<button class="primary" aria-label="Zuordnen: {esc(p.name)}">Zuordnen</button></form> '
                   if all_rooms else '<span class="mut">Erst einen Raum anlegen</span> ')
                + f'<form class="inline" method="post" action="{r}/admin/wartend/{p.id}/ablehnen" '
                f'onsubmit="return confirm(this.dataset.frage)" '
                f'data-frage="{esc(REJECT_Q.format(p.name, " ".join(p.fingerprint)))}">{hidden}'
                f'<button aria-label="Ablehnen: {esc(p.name)}">Ablehnen</button>'
                f'</form></td></tr>' for p in waiting)
            parts.append(
                '<fieldset><legend><b>Wartende Geräte</b></legend><p class="mut">Diese Macs haben sich ohne Code '
                'angemeldet und sehen nichts, bis sie einem Raum zugeordnet sind. Vergleichen Sie die Kennung mit '
                'der Anzeige in Ottilie (Einstellungen → System → Zentrale, „Code dieses Macs“). Anfragen verfallen '
                '24 h nach dem letzten Kontakt.</p>'
                '<table><tr><th>Gerät</th><th>Kennung</th><th>Adresse</th><th>Angemeldet</th>'
                '<th><span class="sr">Aktion</span></th></tr>'
                f'{rows}</table></fieldset>')
        for room in all_rooms:
            mine = [d for d in devs if d["room"] == room["name"] and not d["revoked"]]
            others = room_options(room["name"])
            dev_rows = "".join(
                f'<tr><td>{esc(d["name"])}</td><td>{_when(d["created"])}</td><td>{_when(d["last_seen"])}</td>'
                f'<td>' + (f'<form class="inline" method="post" action="{r}/admin/geraete/{d["id"]}/umziehen">'
                           f'{hidden}<select name="raum" required aria-label="Neuer Raum für {esc(d["name"])}">'
                           f'{others}</select> <button aria-label="Umziehen: {esc(d["name"])}">Umziehen</button>'
                           f'</form> ' if others else "")
                + f'<form class="inline" method="post" action="{r}/admin/geraete/{d["id"]}/abmelden" '
                f'onsubmit="return confirm(this.dataset.frage)" '
                f'data-frage="{esc(DEVICE_Q.format(d["name"], room["name"]))}">'
                f'{hidden}<button aria-label="Gerät abmelden: {esc(d["name"])}">Gerät abmelden</button></form>'
                f'</td></tr>' for d in mine)
            parts.append(
                f'<fieldset><legend><b>{esc(room["name"])}</b> · {esc(KIND_TEXT.get(room["kind"], ""))}</legend>'
                f'<table><tr><th>Gerät</th><th>Gekoppelt</th><th>Letzter Kontakt</th>'
                f'<th><span class="sr">Aktion</span></th></tr>'
                f'{dev_rows or "<tr><td colspan=4 class=mut>Kein Gerät gekoppelt</td></tr>"}</table>'
                f'<form method="post" action="{r}/admin/raeume/{room["id"]}/umbenennen">{hidden}'
                f'<label>Neuer Name <input name="name" maxlength="60" required '
                f'aria-label="Neuer Name für {esc(room["name"])}" aria-describedby="umb-{room["id"]}"></label>'
                f'<button aria-label="Umbenennen: {esc(room["name"])}">Umbenennen</button> '
                f'<span class="mut" id="umb-{room["id"]}">nur, wenn im Raum nichts in Arbeit ist</span></form>'
                f'<form method="post" action="{r}/admin/raeume/{room["id"]}/code">{hidden}'
                f'<button class="primary">Kopplungscode erzeugen</button> <span class="mut">gilt 15 min, einmal</span>'
                f'</form><form method="post" action="{r}/admin/raeume/{room["id"]}/schreibweisen">{hidden}'
                f'<label>Schreibweisen aus der Übergabe (kommagetrennt, z. B. „SR4, Schnitt 4“) '
                f'<input name="schreibweisen" size="50" value="{esc(", ".join(room["aliases"]))}"></label>'
                f'<button>Speichern</button></form>'
                f'<form method="post" action="{r}/admin/raeume/{room["id"]}/loeschen" '
                f'onsubmit="return confirm(this.dataset.frage)" '
                f'data-frage="{esc(ROOM_Q.format(room["name"], len(mine)))}">{hidden}<button>Raum entfernen</button>'
                f'</form></fieldset>')
        kinds = "".join(f'<option value="{k}">{KIND_TEXT[k]}</option>' for k in ROOM_KINDS)
        err = request.query_params.get("fehler") == "1"
        body = (feedback(note, err) if note else "") + "".join(parts) + (
            f'<fieldset><legend>Raum anlegen</legend><form method="post" action="{r}/admin/raeume">{hidden}'
            f'<label>Name <input name="name" required maxlength="60"></label><label>Art <select name="art">{kinds}'
            f'</select></label><button class="primary">Anlegen</button></form></fieldset>')
        return page(request, "Räume & Geräte", body, s, ctx=ctx)

    async def room_add(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, form = got
        try:
            auth.add_room(form.get("name", ""), form.get("art", "mischraum"))
        except AuthError as e:
            return back(request, "/raeume", str(e))
        log.info("raeume", f"Raum angelegt: {form.get('name', '')} ({form.get('art', '')}) – {s.admin}")
        return back(request, "/raeume")

    async def room_code(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, _ = got
        room_id = int(request.path_params["rid"])
        try:
            code = auth.pairing_code(room_id)
        except AuthError as e:
            return back(request, "/raeume", str(e))
        name = next((x["name"] for x in auth.rooms() if x["id"] == room_id), "?")
        log.info("kopplung", f"Neuer Code zum Koppeln – {s.admin}", room=name)
        body = (f'<p>Kopplungscode für <b>{esc(name)}</b> – gilt 15 Minuten und nur einmal:</p>'
                f'<p class="code">{esc(code)}</p><p class="mut">In Ottilie: Einstellungen → Zentrale → Koppeln. In der '
                f'Web-App beim ersten Start.</p><p><a href="{_root(request)}/admin/raeume">Zurück</a></p>')
        return page(request, "Kopplungscode", body, s, ctx=ctx)

    async def room_aliases(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, form = got
        room_id = int(request.path_params["rid"])
        old = next((x["aliases"] for x in auth.rooms() if x["id"] == room_id), [])
        new = [a for a in form.get("schreibweisen", "").split(",") if a.strip()]
        auth.set_aliases(room_id, new)
        log.changed("raeume", f"Schreibweisen Raum {room_id}", old, sorted({a.strip().casefold() for a in new}),
                    s.admin)
        return back(request, "/raeume")

    async def room_delete(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, _ = got
        room_id = int(request.path_params["rid"])
        devs = [d["id"] for d in db.all("SELECT id FROM devices WHERE room_id=?", (room_id,))]
        name = next((x["name"] for x in auth.rooms() if x["id"] == room_id), "?")
        auth.remove_room(room_id)
        ctx.chat.remove_room(name)
        for d in devs:
            await ctx.hub.revoke(int(d))
        log.info("raeume", f"Raum entfernt: {name} – {s.admin}")
        return back(request, "/raeume")

    async def pending_assign(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, form = got
        try:
            dev = await ctx.flows.assign_device(int(request.path_params["pid"]), int(form.get("raum") or 0), s.admin,
                                                str(form.get("kennung") or ""))
        except (AuthError, ValueError) as e:
            return back(request, "/raeume", str(e), error=True)
        return back(request, "/raeume", f"„{dev.name}“ gehört jetzt zu {dev.room}.")

    async def pending_reject(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, _ = got
        try:
            await ctx.flows.reject_device(int(request.path_params["pid"]), s.admin)
        except AuthError as e:
            return back(request, "/raeume", str(e), error=True)
        return back(request, "/raeume", "Anmeldung abgelehnt.")

    async def device_move(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, form = got
        dev_id = int(request.path_params["did"])
        if not db.one("SELECT 1 FROM devices WHERE id=? AND revoked=0", (dev_id,)):
            return back(request, "/raeume", "Gerät nicht gefunden", error=True)
        try:
            dev = await ctx.flows.move_device(dev_id, int(form.get("raum") or 0), s.admin)
        except (AuthError, ValueError) as e:
            return back(request, "/raeume", str(e), error=True)
        return back(request, "/raeume", f"„{dev.name}“ gehört jetzt zu {dev.room}.")

    async def room_rename(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, form = got
        try:
            new = await ctx.flows.rename_room(int(request.path_params["rid"]), form.get("name", ""), s.admin)
        except (AuthError, ValueError) as e:
            return back(request, "/raeume", str(e), error=True)
        return back(request, "/raeume", f"Raum heißt jetzt {new}.")

    async def device_revoke(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, _ = got
        dev_id = int(request.path_params["did"])
        dev = auth.device_by_id(dev_id) if db.one("SELECT 1 FROM devices WHERE id=?", (dev_id,)) else None
        if dev is None:
            return back(request, "/raeume", "Gerät nicht gefunden")
        auth.revoke(dev_id)
        await ctx.hub.revoke(dev_id)
        log.info("kopplung", f"Gerät abgemeldet – {s.admin}", room=dev.room, device=dev.name)
        return back(request, "/raeume")

    # ── Server ────────────────────────────────────────────────────────────────
    async def server(request: Request) -> Response:
        s = guard(request)
        if not isinstance(s, Session):
            return s
        if request.method == "POST":
            form = await _form(request)
            if not _csrf(s, form):
                return Response("Ungültige Anfrage (CSRF)", status_code=403)
            errors = []
            for key, kind in SERVER_KEYS.items():
                raw = form.get(key)
                if raw is None:
                    continue
                if kind is list:
                    value = [x.strip() for x in raw.replace(",", "\n").splitlines() if x.strip()]
                    import ipaddress
                    bad = []
                    for n in value:
                        try:
                            ipaddress.ip_network(n, strict=False)
                        except ValueError:
                            bad.append(n)
                    if bad:
                        errors.append(f"Kein Netz: {', '.join(bad)}")
                        continue
                elif kind is int:
                    try:
                        value = int(raw)
                    except ValueError:
                        errors.append(f"{key}: keine Zahl")
                        continue
                    limits = {"protokoll.aufbewahrung_tage": (1, 365), "protokoll.debug_aufbewahrung_tage": (1, 365),
                              "admin.sitzung_h": (1, 24)}[key]
                    value = min(limits[1], max(limits[0], value))
                else:
                    value = raw.strip()[:300]
                    if key == "server.oeffentliche_adresse" and value:
                        from urllib.parse import urlsplit
                        u = urlsplit(value)
                        if u.scheme not in ("http", "https") or not u.hostname or \
                                not _SAFE_ROOT.fullmatch(u.path.rstrip("/") or "/"):
                            errors.append("Öffentliche Adresse: https://Server[:Port][/Pfad] ohne Sonderzeichen")
                            continue
                old = db.setting(key)
                if old != value:
                    db.set_setting(key, value)
                    log.changed("server", key, old, value, s.admin)
            return back(request, "/server", "; ".join(errors) or "Gespeichert")
        get = db.setting
        hidden = f'<input type="hidden" name="csrf" value="{esc(s.csrf)}">'
        note = request.query_params.get("hinweis", "")
        body = (f'<p class="banner">{esc(note)}</p>' if note else "") + (
            f'<form method="post">{hidden}'
            f'<label>Öffentliche Adresse (z. B. https://server:8443 oder …/zentrale; der Pfad gilt ab dem nächsten '
            f'Start) <input name="server.oeffentliche_adresse" size="50" '
            f'value="{esc(get("server.oeffentliche_adresse", "") or "")}"></label>'
            f'<label>Zeitzone <input name="server.zeitzone" value="{esc(get("server.zeitzone", "Europe/Berlin"))}">'
            f'</label><label>Zugelassene Netze (eins je Zeile, leer = alle; z. B. 100.64.0.0/10) '
            f'<textarea name="server.zugelassene_netze" rows="3" cols="40">'
            f'{esc(chr(10).join(get("server.zugelassene_netze", []) or []))}</textarea></label>'
            f'<label>Protokoll aufbewahren (Tage, 1–365) <input name="protokoll.aufbewahrung_tage" type="number" '
            f'min="1" max="365" value="{esc(get("protokoll.aufbewahrung_tage", 30))}"></label>'
            f'<label>Debug-Protokoll aufbewahren (Tage) <input name="protokoll.debug_aufbewahrung_tage" type="number" '
            f'min="1" max="365" value="{esc(get("protokoll.debug_aufbewahrung_tage", 3))}"></label>'
            f'<label>Sitzungsdauer Administration (Stunden) <input name="admin.sitzung_h" type="number" min="1" '
            f'max="24" value="{esc(get("admin.sitzung_h", 8))}"></label>'
            f'<p class="mut">Port (8470) und HTTPS bestimmt der Container bzw. der Proxy davor (tailscale serve, '
            f'nginx).</p><button class="primary">Speichern</button></form>')
        return page(request, "Server", body, s, ctx=ctx)

    # ── Protokoll ─────────────────────────────────────────────────────────────
    def _log_filter(request: Request) -> dict:
        q = request.query_params
        try:
            hours = max(1, min(24 * 365, int(q.get("stunden", "24"))))
        except ValueError:
            hours = 24
        levels = [x for x in q.getlist("stufe") if x in LEVELS]
        return {"since": ctx.clock() - hours * 3600, "levels": levels, "room": q.get("raum", ""),
                "device": q.get("geraet", ""), "area": q.get("bereich", ""), "search": q.get("suche", "")[:100],
                "hours": hours}

    async def protocol(request: Request) -> Response:
        s = guard(request)
        if not isinstance(s, Session):
            return s
        f = _log_filter(request)
        rows = log.query(f["since"], 0, f["levels"], f["room"], f["device"], f["area"], f["search"])
        trs = "".join(f'<tr><td class="mono">{_when(e["t"])}</td><td>{LEVEL_TEXT.get(e["level"], e["level"])}</td>'
                      f'<td>{esc(e["area"])}</td><td>{esc(e["room"])}</td><td>{esc(e["device"])}</td>'
                      f'<td>{esc(e["text"])}</td></tr>' for e in rows)
        checks = "".join(f'<label style="display:inline;margin-right:8px"><input type="checkbox" name="stufe" '
                         f'value="{lv}"{" checked" if lv in f["levels"] else ""}> {LEVEL_TEXT[lv]}</label>'
                         for lv in LEVELS)
        st = log.debug_state()
        hidden = f'<input type="hidden" name="csrf" value="{esc(s.csrf)}">'
        r = _root(request)
        spans = "".join(f'<option value="{h}">{h} h</option>' if h < 72 else f'<option value="{h}">3 Tage</option>'
                        for h in DEBUG_SPANS_H)
        debug = ('<fieldset><legend>Debug-Protokoll</legend>'
                 + (f'<p class="warn">An, noch {st["rest_s"] // 60} min.</p><form method="post" '
                    f'action="{r}/admin/debug">{hidden}<input type="hidden" name="aus" value="1">'
                    f'<button>Ausschalten</button></form>' if st["aktiv"] else
                    f'<form method="post" action="{r}/admin/debug">{hidden}<label>Dauer <select name="stunden">'
                    f'{spans}</select></label><label>Nur Räume (kommagetrennt, leer = alle) <input name="raeume">'
                    f'</label><label>Nur Geräte <input name="geraete"></label><label>Nur Bereiche <input '
                    f'name="bereiche"></label><button>Einschalten</button></form>') + '</fieldset>')
        query = request.url.query
        body = debug + (
            f'<form method="get"><label>Zeitraum (Stunden) <input name="stunden" type="number" min="1" '
            f'value="{f["hours"]}"></label>{checks}<label>Raum <input name="raum" value="{esc(f["room"])}"></label>'
            f'<label>Gerät <input name="geraet" value="{esc(f["device"])}"></label><label>Bereich <input '
            f'name="bereich" value="{esc(f["area"])}"></label><label>Suche <input name="suche" '
            f'value="{esc(f["search"])}"></label><button>Filtern</button> '
            f'<a href="{r}/admin/protokoll.csv?{esc(query)}">'
            f'Herunterladen (CSV)</a></form><table><tr><th>Zeit</th><th>Stufe</th><th>Bereich</th><th>Raum</th>'
            f'<th>Gerät</th><th>Text</th></tr>{trs or "<tr><td colspan=6 class=mut>Keine Einträge</td></tr>"}</table>')
        return page(request, "Protokoll", body, s, ctx=ctx)

    async def protocol_csv(request: Request) -> Response:
        s = guard(request)
        if not isinstance(s, Session):
            return s
        f = _log_filter(request)
        rows = log.query(f["since"], 0, f["levels"], f["room"], f["device"], f["area"], f["search"], limit=100000)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Zeit", "Stufe", "Bereich", "Raum", "Gerät", "Text"])
        for e in rows:
            w.writerow([_cell(x) for x in (_when(e["t"]), e["level"], e["area"], e["room"], e["device"], e["text"])])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="zentrale-protokoll.csv"'})

    async def debug(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, form = got
        if form.get("aus"):
            log.debug_off(s.admin)
        else:
            def split(k: str) -> list[str]:
                return [x.strip() for x in form.get(k, "").split(",") if x.strip()]
            try:
                log.debug_on(float(form.get("stunden", "1")), split("raeume"), split("geraete"), split("bereiche"),
                             by=s.admin)
            except ValueError as e:
                return back(request, "/protokoll", str(e))
        return back(request, "/protokoll")

    # ── Administratoren ───────────────────────────────────────────────────────
    async def admins(request: Request) -> Response:
        s = guard(request)
        if not isinstance(s, Session):
            return s
        hidden = f'<input type="hidden" name="csrf" value="{esc(s.csrf)}">'
        r = _root(request)
        note = request.query_params.get("hinweis", "")
        rows = "".join(
            f'<tr><td>{esc(a["name"])}</td><td>{"muss Kennwort ändern" if a["must_change"] else ""}</td>'
            f'<td>{_when(a["created"])}</td><td>'
            + ("" if a["name"] == s.admin else
               f'<form class="inline" method="post" action="{r}/admin/admins/entfernen">{hidden}'
               f'<input type="hidden" name="name" value="{esc(a["name"])}"><button>Entfernen</button></form>')
            + "</td></tr>" for a in auth.admins())
        body = (f'<p class="banner">{esc(note)}</p>' if note else "") + (
            f'<table><tr><th>Name</th><th></th><th>Angelegt</th><th></th></tr>{rows}</table>'
            f'<p><a href="{r}/admin/kennwort">Eigenes Kennwort ändern</a></p>'
            f'<fieldset><legend>Administrator anlegen</legend><form method="post" action="{r}/admin/admins">{hidden}'
            f'<label>Name <input name="name" required maxlength="40"></label><label>Erstes Kennwort (mind. 10 Zeichen,'
            f' beim ersten Anmelden zu ändern) <input name="kennwort" type="password" minlength="10" required></label>'
            f'<button class="primary">Anlegen</button></form></fieldset>'
            f'<p class="mut">Kennwort vergessen: im Container <span class="mono">python -m app.cli '
            f'kennwort-zuruecksetzen</span>.</p>')
        return page(request, "Administratoren", body, s, ctx=ctx)

    async def admin_add(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, form = got
        try:
            auth.add_admin(form.get("name", ""), form.get("kennwort", ""))
        except AuthError as e:
            return back(request, "/admins", str(e))
        log.info("admin", f"Administrator angelegt: {form.get('name', '')[:40]} – {s.admin}")
        return back(request, "/admins")

    async def admin_remove(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        s, form = got
        name = form.get("name", "")
        if name == s.admin:
            return back(request, "/admins", "Das eigene Konto lässt sich hier nicht entfernen")
        try:
            auth.remove_admin(name)
        except AuthError as e:
            return back(request, "/admins", str(e))
        log.info("admin", f"Administrator entfernt: {name[:40]} – {s.admin}")
        return back(request, "/admins")

    # ── Betrieb ───────────────────────────────────────────────────────────────
    async def operation(request: Request) -> Response:
        s = guard(request)
        if not isinstance(s, Session):
            return s
        hidden = f'<input type="hidden" name="csrf" value="{esc(s.csrf)}">'
        r = _root(request)
        note = request.query_params.get("hinweis", "")
        rows = "".join(f'<tr><td class="mono">{esc(b["name"])}</td><td>{b["groesse"] // 1024} KB</td>'
                       f'<td>{_when(b["zeit"])}</td><td><a href="{r}/admin/sicherungen/{esc(b["name"])}">'
                       f'Herunterladen</a></td></tr>' for b in ctx.backups.list())
        body = (f'<p class="banner">{esc(note)}</p>' if note else "") + (
            f'<h2>Sicherungen</h2><p class="mut">Täglich eine, die letzten 7 bleiben.</p><table><tr><th>Datei</th>'
            f'<th>Größe</th><th>Zeit</th><th></th></tr>{rows}</table><form method="post" '
            f'action="{r}/admin/sicherungen">{hidden}<button>Jetzt sichern</button></form>'
            f'<h2>Wiederherstellen</h2><p class="mut">Die Datei wird geprüft und beim nächsten Start der Zentrale '
            f'eingesetzt; die bisherige Datenbank bleibt als Sicherung liegen.</p>'
            f'<input type="file" id="datei" accept=".db"> <button id="hoch">Vormerken</button> '
            f'<span id="erg" role="status"></span>'
            f'<script>document.getElementById("hoch").onclick=async()=>{{const f=document.getElementById("datei")'
            f'.files[0];if(!f)return;const r=await fetch("{r}/admin/wiederherstellen",{{method:"POST",headers:'
            f'{{"X-CSRF":"{esc(s.csrf)}"}},body:f}});document.getElementById("erg").textContent=await r.text();}}'
            f'</script>')
        return page(request, "Betrieb", body, s, ctx=ctx)

    async def backup_now(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        ctx.backups.create()
        return back(request, "/betrieb", "Sicherung angelegt")

    async def backup_download(request: Request) -> Response:
        s = guard(request)
        if not isinstance(s, Session):
            return s
        p = ctx.backups.path(request.path_params["name"])
        if p is None:
            return Response("Nicht gefunden", status_code=404)
        log.info("betrieb", f"Sicherung heruntergeladen: {request.path_params['name']} – {s.admin}")
        return FileResponse(p, media_type="application/octet-stream", filename=request.path_params["name"])

    async def restore(request: Request) -> Response:
        s = guard(request)
        if not isinstance(s, Session):
            return Response("Nicht angemeldet", status_code=403)
        if not hmac.compare_digest(request.headers.get("X-CSRF", ""), s.csrf):
            return Response("Ungültige Anfrage (CSRF)", status_code=403)
        from app.main import read_limited
        try:
            data = await read_limited(request, MAX_UPLOAD)
        except ValueError:
            data = b""
        if not data:
            return Response("Datei fehlt oder ist zu groß", status_code=400)
        try:
            ctx.backups.stage_restore(data)
        except ValueError as e:
            return Response(str(e), status_code=400)
        log.info("betrieb", f"Wiederherstellen vorgemerkt – {s.admin}")
        return Response("Vorgemerkt – wirkt beim nächsten Start der Zentrale.")

    return [
        Route("/admin", overview), Route("/admin/anmelden", login, methods=["GET", "POST"]),
        Route("/admin/abmelden", logout, methods=["POST"]),
        Route("/admin/kennwort", password, methods=["GET", "POST"]),
        Route("/admin/raeume", rooms), Route("/admin/raeume", room_add, methods=["POST"]),
        Route("/admin/raeume/{rid:int}/code", room_code, methods=["POST"]),
        Route("/admin/raeume/{rid:int}/schreibweisen", room_aliases, methods=["POST"]),
        Route("/admin/raeume/{rid:int}/loeschen", room_delete, methods=["POST"]),
        Route("/admin/raeume/{rid:int}/umbenennen", room_rename, methods=["POST"]),
        Route("/admin/geraete/{did:int}/abmelden", device_revoke, methods=["POST"]),
        Route("/admin/geraete/{did:int}/umziehen", device_move, methods=["POST"]),
        Route("/admin/wartend/{pid:int}/zuordnen", pending_assign, methods=["POST"]),
        Route("/admin/wartend/{pid:int}/ablehnen", pending_reject, methods=["POST"]),
        Route("/admin/server", server, methods=["GET", "POST"]),
        Route("/admin/protokoll", protocol), Route("/admin/protokoll.csv", protocol_csv),
        Route("/admin/debug", debug, methods=["POST"]),
        Route("/admin/admins", admins), Route("/admin/admins", admin_add, methods=["POST"]),
        Route("/admin/admins/entfernen", admin_remove, methods=["POST"]),
        Route("/admin/betrieb", operation), Route("/admin/sicherungen", backup_now, methods=["POST"]),
        Route("/admin/sicherungen/{name}", backup_download),
        Route("/admin/wiederherstellen", restore, methods=["POST"]),
    ]
