"""Administration, Stufe 2/3: Übergabeordner (verteilte Einstellungen), Chat, Helmut-Eingang, Belegungen."""
from __future__ import annotations

import hmac
import secrets
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from app.admin import COOKIE, _form, _root, _when, esc, page
from app.auth import Session, sha
from app.chat import DEFAULT_NOTICE, KINDS, SYSTEM_KINDS
from app.flows import chat_settings
from app.work import FOLDER_KEYS, folder_keys, folder_lines, parse_value

if TYPE_CHECKING:
    from app.main import Context

KIND_TEXT = {"alle": "Alle Räume", "raum": "Raum an Raum", "beitrag": "Beitragsgespräch"}
HOLD_H = (1, 4, 8, 24, 48, 72, 168)


def routes(ctx: "Context") -> list[Route]:
    auth, log, db = ctx.auth, ctx.log, ctx.db

    def guard(request: Request) -> Session | Response:
        if not ctx.allowed(request.client.host if request.client else ""):
            return Response("Nicht zugelassen", status_code=403)
        s = auth.session(request.cookies.get(COOKIE))
        if s is None:
            return RedirectResponse(f"{_root(request)}/admin/anmelden", status_code=303)
        if s.must_change:
            return RedirectResponse(f"{_root(request)}/admin/kennwort", status_code=303)
        return s

    async def posted(request: Request):
        s = guard(request)
        if not isinstance(s, Session):
            return s
        form = await _form(request)
        if not hmac.compare_digest(str(form.get("csrf", "")), s.csrf):
            log.warn("admin", f"CSRF-Prüfung gescheitert ({s.admin})")
            return Response("Ungültige Anfrage (CSRF)", status_code=403)
        return s, form

    def back(request: Request, path: str, note: str = "") -> Response:
        from urllib.parse import quote
        return RedirectResponse(f"{_root(request)}/admin{path}" + (f"?hinweis={quote(note)}" if note else ""),
                                status_code=303)

    def note_of(request: Request) -> str:
        n = request.query_params.get("hinweis", "")
        return f'<p class="banner">{esc(n)}</p>' if n else ""

    # ── Übergabeordner ────────────────────────────────────────────────────────
    async def folder(request: Request) -> Response:
        if request.method == "POST":
            got = await posted(request)
            if isinstance(got, Response):
                return got
            s, form = got
            old = ctx.work.folder_values()
            new, errors = {}, {}
            for key, (label, kind, extra) in FOLDER_KEYS.items():
                raw = form.get(key, "")
                if kind == "bool":
                    raw = {"": "", "ja": "ja", "nein": "nein"}.get(raw, "")
                try:
                    v = parse_value(kind, extra, raw) if not (kind == "bool" and raw == "nein") else False
                except ValueError as e:
                    errors[key] = str(e)
                    continue
                if key == "system.uebergabeordner.netzadresse" and v and not str(v).lower().startswith("smb://"):
                    errors[key] = "nur smb://"
                    continue
                if v is not None:
                    new[key] = v
            more = "system.uebergabeordner.weitere_ordner"
            gone = folder_keys(old.get(more)) - folder_keys(new.get(more))
            busy = [c["title"] or c["beitrag"] for c in ctx.work.claims()
                    if "/" in c["beitrag"] and c["beitrag"].split("/", 1)[0] in gone]
            if busy and more not in errors:
                # Die Kennung eines Ordners entsteht aus seinem Namen: Umbenennen oder Entfernen ließe Filme in Arbeit
                # ins Leere laufen (Ausfall, Programmierer 13.11.2026)
                errors[more] = (f"Umbenennen oder Entfernen erst, wenn dort nichts mehr in Arbeit ist – belegt: "
                                f"{', '.join(busy[:3])}{' …' if len(busy) > 3 else ''}")
            if errors:
                # Eingaben bleiben stehen, der Fehler steht am Feld (Barrierefreiheit 13.11.2026)
                return form_page(request, s, {k: form.get(k, "") for k in FOLDER_KEYS}, errors, status=400)
            for key in sorted(set(old) | set(new)):
                if old.get(key) != new.get(key):
                    log.changed("uebergabeordner", key, old.get(key), new.get(key), s.admin)
            ctx.work.set_folder_values(new)
            await ctx.flows.push_settings()
            return back(request, "/uebergabeordner", "Gespeichert und an alle verbundenen Ottilies verteilt")
        s = guard(request)
        if not isinstance(s, Session):
            return s
        return form_page(request, s, None, {})

    def form_page(request: Request, s: Session, posted_raw: dict | None, errors: dict,
                  status: int = 200) -> Response:
        vals = ctx.work.folder_values()
        rows = []
        for key, (label, kind, extra) in FOLDER_KEYS.items():
            v = vals.get(key)
            raw = None if posted_raw is None else str(posted_raw.get(key, ""))
            fid = "f-" + key.rsplit(".", 1)[-1]
            err = errors.get(key, "")
            described = f' aria-describedby="{fid}-hilfe{f" {fid}-fehler" if err else ""}"'
            invalid = ' aria-invalid="true"' if err else ""
            if kind in ("enum", "bool"):
                choices = [(x, x) for x in extra] if kind == "enum" else [("ja", "ja"), ("nein", "nein")]
                opts = [("", "– nicht vorgegeben –")] + choices
                cur = raw if raw is not None else (("ja" if v else "nein") if kind == "bool" and v is not None
                                                   else (v or ""))
                ctrl = (f'<select id="{fid}" name="{key}"{described}{invalid}>' + "".join(
                    f'<option value="{esc(o)}"{" selected" if o == cur else ""}>{esc(t)}</option>' for o, t in opts)
                    + "</select>")
            elif kind == "folders":
                text = raw if raw is not None else folder_lines(v)
                ctrl = (f'<textarea id="{fid}" name="{key}" rows="4"{described}{invalid}>{esc(text)}</textarea>')
            else:
                shown = raw if raw is not None else (", ".join(v) if isinstance(v, list) else ("" if v is None else v))
                ctrl = f'<input id="{fid}" name="{key}" size="50" value="{esc(shown)}"{described}{invalid}>'
            hint = esc(key)
            if kind == "folders":
                hint = ("Je Zeile: Name | smb://Server/Freigabe | gast oder kerberos | Pfad auf dem Mac. Der Name ist "
                        "zugleich die Kennung der Filme (z. B. „Schnitt Nord“ → schnitt-nord/Film); in "
                        "ZENTRALE_UEBERGABE_WEITERE gleich schreiben. Umbenennen oder Entfernen nur, wenn dort "
                        "nichts in Arbeit ist.")
            rows.append(f'<p><label for="{fid}">{esc(label)}</label><br>{ctrl}<br>'
                        f'<span class="mut" id="{fid}-hilfe">{hint}</span>'
                        + (f'<br><span class="bad" id="{fid}-fehler">Fehler: {esc(err)}</span>' if err else "")
                        + "</p>")
        top = ""
        if errors:
            top = ('<p class="banner bad" id="hinweis" tabindex="-1" role="alert">Nicht gespeichert – bitte die '
                   'markierten Felder prüfen.</p><script>document.getElementById("hinweis").focus()</script>')
        body = (top or note_of(request)) + (
            '<p class="mut">Leer = nicht zentral vorgegeben (dann gilt in Ottilie der lokale Wert; die Betriebsart ist '
            'dann „helmut_api“). Ein Wert aus dem Konfigurationsprofil (MDM) geht immer vor. Ottilie zeigt zentral '
            'vorgegebene Werte an; ohne Zentrale gilt der zuletzt empfangene Stand. Kennwörter gehören nie hierher – '
            'nur der Name des Schlüsselbund-Eintrags.</p>'
            '<form method="post" onsubmit="return confirm(\'Wirkt sofort in allen verbundenen Räumen. Betriebsart '
            'und Übergabeordner wechseln dort erst, wenn nichts mehr in Arbeit ist. Jetzt verteilen?\')">'
            f'<input type="hidden" name="csrf" value="{esc(s.csrf)}">{"".join(rows)}'
            '<button class="primary">Speichern und verteilen</button></form>'
            '<p class="mut">Pfade im Container (Ordnerbeobachtung): <span class="mono">/mnt/uebergabe</span>, weitere '
            'Übergabeordner über <span class="mono">ZENTRALE_UEBERGABE_WEITERE</span> („Name=/pfad;…“) – '
            'festgelegt in compose.yaml.</p>')
        return page(request, "Übergabeordner", body, s, status=status, ctx=ctx)

    # ── Chat ──────────────────────────────────────────────────────────────────
    async def chat(request: Request) -> Response:
        if request.method == "POST":
            got = await posted(request)
            if isinstance(got, Response):
                return got
            s, form = got
            old = chat_settings(db)
            if form.get("loeschen"):
                n = ctx.chat.wipe()
                log.info("chat", f"Wartende Nachrichten gelöscht: {n} ({s.admin})")
                return back(request, "/chat", f"{n} wartende Nachrichten gelöscht")
            on = form.get("aktiv") == "1"
            arten = {k: form.get(f"art_{k}") == "1" for k in KINDS}
            system = {k: form.get(f"sys_{k}") == "1" for k in SYSTEM_KINDS}
            notice = " ".join(form.get("hinweis", "").split())[:600] or DEFAULT_NOTICE
            try:
                hold = float(form.get("haltezeit_h", "24"))
            except ValueError:
                hold = 24.0
            hold = min(168.0, max(1.0, hold))
            changes = {"chat.aktiv": on, "chat.arten": arten, "chat.systemmeldungen": system,
                       "chat.status_bei_aus": form.get("status_bei_aus") == "1", "chat.hinweis": notice,
                       "chat.haltezeit_h": hold, "chat.dringend_ton": form.get("dringend_ton") == "1"}
            for k, v in changes.items():
                before = db.setting(k)
                if before != v:
                    db.set_setting(k, v)
                    log.changed("chat", k, before, v, s.admin)
            if old["aktiv"] and not on:
                n = await ctx.flows.chat_switched_off()
                log.info("chat", f"Chat ausgeschaltet – {n} wartende Nachrichten gelöscht ({s.admin})")
            else:
                for kind, on_now in arten.items():           # abgeschaltete Gesprächsart: nur diese Gespräche weg
                    if old["arten"].get(kind, True) and not on_now:
                        for cid in ctx.chat.convs_of_kind(kind):
                            await ctx.hub.broadcast({"typ": "chat_gespraech_weg", "daten": {"id": cid}})
                await ctx.flows.push_settings()
            return back(request, "/chat", "Gespeichert")
        s = guard(request)
        if not isinstance(s, Session):
            return s
        cs = chat_settings(db)

        def box(name: str, on: bool, label: str) -> str:
            return (f'<label><input type="checkbox" name="{name}" value="1"{" checked" if on else ""}> '
                    f'{esc(label)}</label>')
        holds = "".join(f'<option value="{h}"{" selected" if h == cs["haltezeit_h"] else ""}>'
                        f'{h} h{" (Standard)" if h == 24 else ""}</option>' for h in HOLD_H)
        body = note_of(request) + (
            f'<form method="post"><input type="hidden" name="csrf" value="{esc(s.csrf)}">'
            '<fieldset><legend>Chat</legend>'
            + box("aktiv", cs["aktiv"], "Chat an (aus: Chatbereich überall weg, wartende Nachrichten werden sofort "
                                        "gelöscht; Status und Belegung laufen weiter)")
            + "".join(box(f"art_{k}", cs["arten"][k], KIND_TEXT[k]) for k in KINDS)
            + '</fieldset><fieldset><legend>Systemmeldungen</legend>'
            + "".join(box(f"sys_{k}", cs["systemmeldungen"][k], t) for k, t in SYSTEM_KINDS.items())
            + box("status_bei_aus", cs["status_bei_aus"], "auch bei ausgeschaltetem Chat als Statusanzeige")
            + '</fieldset><fieldset><legend>Hinweis über jedem Gespräch</legend><p class="mut">Nicht abschaltbar; '
            'leer = Standardtext.</p>'
            f'<textarea name="hinweis" rows="3" cols="80">{esc(cs["hinweis"])}</textarea></fieldset>'
            f'<label>Haltezeit nicht zugestellter Nachrichten <select name="haltezeit_h">{holds}</select></label>'
            + box("dringend_ton", cs["dringend_ton"], "Ton bei „dringend“ (in Ottilie nie während Aufnahme oder "
                                                      "Wiedergabe)")
            + '<button class="primary">Speichern</button></form>'
            f'<h2>Wartende Nachrichten</h2><p>{ctx.chat.waiting()} Nachrichten warten auf Zustellung (Inhalte sind '
            f'hier nicht einsehbar).</p><form method="post" onsubmit="return confirm(\'Alle wartenden Nachrichten '
            f'löschen?\')"><input type="hidden" name="csrf" value="{esc(s.csrf)}"><input type="hidden" '
            f'name="loeschen" value="1"><button>Wartende löschen</button></form>')
        return page(request, "Chat", body, s, ctx=ctx)

    # ── Helmut (Ereignis-Eingang, Stufe 3 B) ──────────────────────────────────
    async def helmut(request: Request) -> Response:
        if request.method == "POST":
            got = await posted(request)
            if isinstance(got, Response):
                return got
            s, form = got
            if form.get("neuer_schluessel"):
                key = secrets.token_urlsafe(32)
                db.set_setting("helmut.ereignis_schluessel_hash", sha(key))
                log.info("helmut", f"Schlüssel für den Ereignis-Eingang neu erzeugt ({s.admin})")
                body = (f'<p>Neuer Schlüssel für <span class="mono">POST {_root(request)}/api/helmut/ereignis</span> – '
                        f'wird nur jetzt angezeigt:</p><p class="mono" style="word-break:break-all">{esc(key)}</p>'
                        f'<p class="mut">Kopfzeile: <span class="mono">Authorization: Bearer &lt;Schlüssel&gt;</span>'
                        f'</p><p><a href="{_root(request)}/admin/helmut">Zurück</a></p>')
                return page(request, "Helmut", body, s, ctx=ctx)
            if form.get("aus"):
                db.set_setting("helmut.ereignis_schluessel_hash", "")
                log.info("helmut", f"Ereignis-Eingang abgeschaltet ({s.admin})")
                return back(request, "/helmut", "Ereignis-Eingang aus")
            old = db.setting("helmut.api_adresse", "")
            new = form.get("api_adresse", "").strip()[:300]
            if new != old:
                db.set_setting("helmut.api_adresse", new)
                log.changed("helmut", "helmut.api_adresse", old, new, s.admin)
            return back(request, "/helmut", "Gespeichert")
        s = guard(request)
        if not isinstance(s, Session):
            return s
        has = bool(db.setting("helmut.ereignis_schluessel_hash", ""))
        hidden = f'<input type="hidden" name="csrf" value="{esc(s.csrf)}">'
        body = note_of(request) + (
            f'<h2>Ereignis-Eingang</h2><p>Helmut meldet „Beitrag übergeben“ an <span class="mono">POST '
            f'{_root(request)}/api/helmut/ereignis</span> – gleiche Wirkung wie die Ordnerbeobachtung. Stand: '
            f'<b>{"eingerichtet" if has else "aus (kein Schlüssel)"}</b>. Der Schlüssel ist nur beim Erzeugen '
            f'sichtbar.</p><form method="post" class="inline">{hidden}<input type="hidden" name="neuer_schluessel" '
            f'value="1"><button class="primary">{"Neuen Schlüssel erzeugen" if has else "Schlüssel erzeugen"}'
            f'</button></form> ' + (f'<form method="post" class="inline">{hidden}<input type="hidden" name="aus" '
                                    f'value="1"><button>Abschalten</button></form>' if has else "")
            + '<pre class="mono">{"ereignis": "beitrag_uebergeben", "beitrag_id": "…", "titel": "…",\n'
              ' "ordner": "Filmordner-Name", "schnittraum": "SR4"}</pre>'
            f'<h2>Helmut-API</h2><form method="post">{hidden}<label>Adresse (nur zur Information für die Räume) '
            f'<input name="api_adresse" size="50" value="{esc(db.setting("helmut.api_adresse", "") or "")}"></label>'
            '<button>Speichern</button></form><p class="mut">Die Fertigmeldung (Adresse, Anmeldung) wird an jedem Mac '
            'eingestellt oder per Konfigurationsprofil verteilt – nicht über die Zentrale; ihre Geheimnisse liegen nur '
            'im Schlüsselbund der Macs.</p>')
        return page(request, "Helmut", body, s, ctx=ctx)

    # ── Belegungen (Betrieb) ──────────────────────────────────────────────────
    async def claims(request: Request) -> Response:
        if request.method == "POST":
            got = await posted(request)
            if isinstance(got, Response):
                return got
            s, form = got
            reason = " ".join(form.get("grund", "").split())[:200]
            if not reason:
                return back(request, "/belegungen", "Bitte einen Grund angeben")
            had = await ctx.flows.admin_release(form.get("beitrag", ""), reason, s.admin)
            return back(request, "/belegungen", f"Belegung von {had} gelöst" if had else "War nicht belegt")
        s = guard(request)
        if not isinstance(s, Session):
            return s
        hidden = f'<input type="hidden" name="csrf" value="{esc(s.csrf)}">'
        rows = "".join(
            f'<tr><td>{esc(c["title"] or c["beitrag"])}<br><span class="mut mono">{esc(c["beitrag"])}</span></td>'
            f'<td>{esc(c["room"])}</td><td>{_when(c["t"])}</td><td><form method="post">{hidden}'
            f'<input type="hidden" name="beitrag" value="{esc(c["beitrag"])}"><input name="grund" placeholder="Grund" '
            f'required maxlength="200"> <button>Lösen</button></form></td></tr>' for c in ctx.work.claims())
        conf = "".join(f'<tr><td>{esc(c["title"] or c["beitrag"])}</td><td>{esc(c["room"])}</td>'
                       f'<td>{esc(c["holder"])}</td><td>{_when(c["t"])}</td><td><form method="post">{hidden}'
                       f'<input type="hidden" name="beitrag" value="{esc(c["beitrag"])}"><input name="grund" '
                       f'placeholder="Grund" required maxlength="200"> <button>Belegung lösen …</button>'
                       f'<br><span class="mut">Danach hat ihn niemand – der Raum, der weitermischt, belegt neu.</span>'
                       f'</form></td></tr>' for c in ctx.work.conflicts())
        body = note_of(request) + (
            '<p class="mut">Belegt wird über die Zentrale (wer zuerst fragt, bekommt den Film); die Sperrdatei im '
            'Filmordner schreibt Ottilie weiter. Eine verwaiste Belegung (Mac aus, Raum verlassen) hier mit Grund '
            'lösen.</p><table><tr><th>Beitrag</th><th>Raum</th><th>Seit</th><th></th></tr>'
            f'{rows or "<tr><td colspan=4 class=mut>Nichts belegt</td></tr>"}</table>'
            '<h2>Konflikte</h2><p class="mut">Nach einem Ausfall meldete ein Raum einen Film, den laut Zentrale ein '
            'anderer Raum hat. Die Zentrale entscheidet nicht selbst – bitte mit den Räumen klären und dann lösen.</p>'
            '<table><tr><th>Beitrag</th><th>gemeldet von</th><th>belegt von</th><th>Zeit</th><th></th></tr>'
            f'{conf or "<tr><td colspan=5 class=mut>Keine</td></tr>"}</table>')
        return page(request, "Belegungen", body, s, ctx=ctx)

    return [Route("/admin/uebergabeordner", folder, methods=["GET", "POST"]),
            Route("/admin/chat", chat, methods=["GET", "POST"]),
            Route("/admin/helmut", helmut, methods=["GET", "POST"]),
            Route("/admin/belegungen", claims, methods=["GET", "POST"])]
