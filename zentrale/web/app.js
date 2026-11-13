"use strict";
// Web-App der Ottilie-Zentrale (PWA) für Schnitt- und andere Räume.
// Gespeichert im Browser wird nur der Geräteschlüssel (localStorage, nötig für die Kopplung). Chat, Raumstatus und
// Beiträge leben nur im Arbeitsspeicher: nach Neuladen leer (Vorgabe: Verlauf nur bis zum Neustart).

const $ = (id) => document.getElementById(id);
const KEY = "ottilie-zentrale-geraet";
const state = {
  key: null, room: "", ws: null, connected: false, retry: 1000, rooms: [], items: new Map(),
  settings: {}, convs: new Map(), msgs: new Map(), unread: new Map(), current: "", outbox: [], seq: 0,
  tab: "raeume", drafts: {}, urgentFlags: {}, pending: new Map(), error: "", urgentBanner: null, since: null,
};

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function base() { const u = new URL(".", location.href); return u; }
function wsUrl() { const u = base(); u.protocol = u.protocol === "https:" ? "wss:" : "ws:"; u.pathname += "ws"; return u.toString(); }
function rid() { state.seq += 1; return `${Date.now().toString(36)}-${state.seq}-${Math.random().toString(36).slice(2, 8)}`; }

function loadKey() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
function saveKey(k) { try { if (k) localStorage.setItem(KEY, k); else localStorage.removeItem(KEY); } catch (e) { /* privates Fenster */ } }

// ── Kopplung ──────────────────────────────────────────────────────────────────
$("koppel-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("koppel-fehler").textContent = "";
  try {
    const r = await fetch(new URL("api/koppeln", base()), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: $("code").value, geraet: $("geraet").value || "Web-App" }),
    });
    const d = await r.json();
    if (!r.ok) { $("koppel-fehler").textContent = d.fehler || "Kopplung nicht möglich"; return; }
    saveKey(d.schluessel);
    state.key = d.schluessel;
    $("code").value = "";
    start();
    askNotifications();
  } catch (e) { $("koppel-fehler").textContent = "Zentrale nicht erreichbar"; }
});

// ── Verbindung ────────────────────────────────────────────────────────────────
function connect() {
  const ws = new WebSocket(wsUrl());
  state.ws = ws;
  ws.onopen = () => { ws.send(JSON.stringify({ typ: "anmelden", daten: { schluessel: state.key } })); };
  ws.onmessage = (ev) => { let m; try { m = JSON.parse(ev.data); } catch (e) { return; } receive(m); };
  ws.onclose = (ev) => {
    state.connected = false; showConn();
    if (ev.code === 4401) {
      saveKey(null); state.key = null;
      $("koppel-fehler").textContent = "Dieses Gerät wurde in der Zentrale abgemeldet – bitte einen neuen Kopplungscode holen.";
      show("koppeln"); return;
    }
    state.since = state.since || new Date();
    if (state.pending.size) {                          // abgebrochen vor der Bestätigung: Text zurück ins Feld
      for (const p of state.pending.values()) if (!state.drafts[p.conv]) state.drafts[p.conv] = p.text;
      state.pending.clear();
      state.error = "Verbindung unterbrochen – Nachricht nicht sicher angekommen. Text steht wieder im Feld.";
      renderChat();
    }
    renderRooms();
    setTimeout(connect, state.retry);
    state.retry = Math.min(state.retry * 2, 30000);
  };
}

function askNotifications() {
  if (!("Notification" in window) || Notification.permission !== "default") return;
  if (confirm("Benachrichtigungen erlauben, damit dringende Nachrichten auch bei verdecktem Fenster ankommen?")) {
    Notification.requestPermission().then(renderChat);
  }
}

function send(typ, daten, wantId = true) {
  const m = { typ, daten };
  if (wantId) m.anfrage_id = rid();
  if (state.connected && state.ws && state.ws.readyState === 1) state.ws.send(JSON.stringify(m));
  else if (wantId) state.outbox.push(m);                  // Ausgang nur im Speicher
  return m.anfrage_id;
}

function flush() {
  const out = state.outbox.splice(0);
  for (const m of out) state.ws.send(JSON.stringify(m));   // gleiche anfrage_id: die Zentrale verarbeitet nur einmal
}

function receive(m) {
  const d = m.daten || {};
  switch (m.typ) {
    case "willkommen":
      state.connected = true; state.retry = 1000; state.room = d.raum; state.settings = d.einstellungen || {};
      state.rooms = d.raeume || []; state.since = null;
      $("raum").textContent = d.raum;
      showConn(); show(state.tab); renderRooms(); applySettings(); flush();
      send("chat_abholen", {});
      break;
    case "raumstatus": upsertRoom(d); break;
    case "einstellungen":
      state.settings = d; applySettings();
      if (d.chat && d.chat.aktiv && !state.convs.size) send("chat_abholen", {});
      break;
    case "beitrag": upsertItem(d); break;
    case "chat_gespraech": state.convs.set(d.id, d); renderChat(); break;
    case "chat_gespraech_weg": state.convs.delete(d.id); state.msgs.delete(d.id); renderChat(); break;
    case "chat_nachricht": addMsg(d); break;
    case "chat_aus": state.convs.clear(); state.msgs.clear(); state.unread.clear(); renderChat(); break;
    case "abgemeldet": saveKey(null); state.key = null; break;
    case "ok": if (m.anfrage_id && state.pending.has(m.anfrage_id)) { state.pending.delete(m.anfrage_id); renderChat(); } break;
    case "fehler": {
      const p = m.anfrage_id && state.pending.get(m.anfrage_id);
      if (p) {                                          // eigene Nachricht abgelehnt: Text zurück ins Feld
        state.pending.delete(m.anfrage_id);
        if (!state.drafts[p.conv]) state.drafts[p.conv] = p.text;
        state.error = `Nachricht nicht gesendet: ${d.text || "abgelehnt"}`;
        renderChat();
      }
      break;
    }
    case "systemmeldung": if (d.text) { state.urgentBanner = { von: "Ottilie", sys: true, text: d.text, conv: "" }; renderBanner(); } break;
    case "neuer_beitrag": break;
    case "lebenszeichen": if (state.ws) state.ws.send(JSON.stringify({ typ: "lebenszeichen", daten: {} })); break;
    default: break;
  }
}

function showConn() {
  const el = $("verbindung");
  el.textContent = state.connected ? "verbunden" : "nicht verbunden";
  el.className = "pill " + (state.connected ? "on" : "off");
}

// ── Ansichten ─────────────────────────────────────────────────────────────────
function show(tab) {
  state.tab = tab;
  for (const id of ["koppeln", "raeume", "beitraege", "chat"]) $(id).hidden = id !== tab;
  $("tabs").hidden = tab === "koppeln";
  for (const b of document.querySelectorAll("#tabs button")) b.setAttribute("aria-pressed", String(b.dataset.tab === tab));
  if (tab === "chat") markRead(state.current);
}
for (const b of document.querySelectorAll("#tabs button")) b.addEventListener("click", () => show(b.dataset.tab));

const STATE_TEXT = { frei: "frei", uebernommen: "übernommen", importiert: "importiert", mischt: "in Mischung", exportiert: "exportiert" };
function upsertRoom(r) {
  const i = state.rooms.findIndex((x) => x.raum === r.raum);
  if (i >= 0) state.rooms[i] = r; else state.rooms.push(r);
  renderRooms();
}
function renderRooms() {
  const table = $("raum-liste").closest("table");
  table.style.opacity = state.connected ? "" : ".55";
  let note = document.getElementById("raum-stand");
  if (!note) { note = document.createElement("p"); note.id = "raum-stand"; note.className = "warn"; note.setAttribute("role", "status"); table.before(note); }
  note.textContent = state.connected || !state.since ? "" : `Stand von ${state.since.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} – Zentrale nicht erreichbar`;
  $("raum-liste").innerHTML = state.rooms.map((r) => `<tr><td>${esc(r.raum)}</td>
    <td class="${r.verbunden ? "ok" : "mut"}">${r.verbunden ? "verbunden" : "nicht verbunden"}</td>
    <td>${esc(r.betriebsart === "uebergabeordner" ? "Übergabeordner" : r.betriebsart === "helmut_api" ? "Helmut-API" : "")}</td>
    <td>${esc(r.beitrag_titel || "")}</td>
    <td>${esc(STATE_TEXT[r.zustand] || "")}${r.fortschritt ? " " + r.fortschritt + " %" : ""}</td></tr>`).join("")
    || `<tr><td colspan="5" class="mut">Noch keine Räume</td></tr>`;
}

const STEP_TEXT = { uebergeben: "übergeben", belegt: "belegt", in_mischung: "in Mischung", audio_export_fertig: "Audio Export fertig" };
function upsertItem(b) { state.items.set(b.id, b); renderItems(); }
function renderItems() {
  const list = [...state.items.values()].sort((a, b) => (b.zeit || 0) - (a.zeit || 0));
  $("beitraege-leer").hidden = list.length > 0;
  $("beitraege-leer").textContent = "Seit dem Öffnen dieser Seite noch keine Meldungen.";
  $("beitrag-liste").innerHTML = list.map((b) => `<li><b>${esc(b.titel || b.id)}</b>
    <span class="mut">${esc(STEP_TEXT[b.schritt] || b.schritt || "")}${b.mischraum ? " · " + esc(b.mischraum) : ""}</span></li>`).join("");
}

// ── Chat (nur im Speicher) ────────────────────────────────────────────────────
function applySettings() {
  const chatOn = !!(state.settings.chat && state.settings.chat.aktiv);
  $("tab-chat").hidden = !chatOn;
  if (!chatOn) { state.convs.clear(); state.msgs.clear(); state.unread.clear(); if (state.tab === "chat") show("raeume"); }
  renderChat();
}

function addMsg(m) {
  const list = state.msgs.get(m.gespraech) || [];
  if (list.some((x) => x.id === m.id)) return;
  list.push(m);
  if (list.length > 500) list.splice(0, list.length - 500);
  state.msgs.set(m.gespraech, list);
  send("chat_empfangen", { ids: [m.id] }, false);
  if (m.von !== state.room) {
    announce(m);
    if (m.dringend && !(state.tab === "chat" && state.current === m.gespraech)) {
      state.urgentBanner = { von: m.system ? "Ottilie" : m.von, sys: !!m.system, text: m.text, conv: m.gespraech }; renderBanner();
    }
    if (!(state.tab === "chat" && state.current === m.gespraech && document.visibilityState === "visible")) {
      state.unread.set(m.gespraech, (state.unread.get(m.gespraech) || 0) + 1);
      notify(m);
    } else markRead(m.gespraech);
  }
  renderChat();
}

function announce(m) {
  let live = document.getElementById("chat-live");
  if (!live) { live = document.createElement("div"); live.id = "chat-live"; live.className = "sr-only"; live.setAttribute("aria-live", m.dringend ? "assertive" : "polite"); document.body.appendChild(live); }
  live.setAttribute("aria-live", m.dringend ? "assertive" : "polite");
  live.textContent = `${m.dringend ? "Dringend – " : ""}Neue Nachricht von ${m.system ? "Ottilie" : m.von}: ${String(m.text).slice(0, 120)}`;
}

function renderBanner() {
  let el = document.getElementById("dringend-banner");
  if (!el) { el = document.createElement("div"); el.id = "dringend-banner"; el.setAttribute("role", "alert"); document.querySelector("main").prepend(el); }
  const b = state.urgentBanner;
  if (!b) { el.innerHTML = ""; el.className = ""; return; }
  el.className = "notice";
  el.innerHTML = `${b.sys ? '<img src="web/ottilie-192.png" alt="">' : ""}<b>${b.conv ? "Dringend von" : "Meldung:"} ${esc(b.von)}:</b> ${esc(String(b.text).slice(0, 200))}
    ${b.conv ? '<button id="banner-open">Öffnen</button>' : ""} <button id="banner-close">Schließen</button>`;
  const open = $("banner-open"); if (open) open.onclick = () => { state.current = b.conv; state.urgentBanner = null; renderBanner(); show("chat"); renderChat(); };
  $("banner-close").onclick = () => { state.urgentBanner = null; renderBanner(); };
}

function markRead(conv) {
  if (!conv) return;
  state.unread.delete(conv);
  const list = state.msgs.get(conv) || [];
  if (list.length) send("chat_gelesen", { gespraech: conv, bis: list[list.length - 1].id }, false);
  renderBadge();
}

function renderBadge() {
  const n = [...state.unread.values()].reduce((a, b) => a + b, 0);
  $("badge").hidden = !n; $("badge").textContent = n;
  if ("setAppBadge" in navigator) { (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(() => {}); }
}

function notify(m) {
  if (!("Notification" in window) || Notification.permission !== "granted" || document.visibilityState === "visible") return;
  const conv = state.convs.get(m.gespraech);
  const body = m.dringend ? String(m.text).slice(0, 80) : (conv ? conv.titel : "");
  try {
    const n = new Notification(`${m.dringend ? "Dringend – " : ""}${m.system ? "Ottilie" : m.von}`, { body, tag: m.gespraech });
    n.onclick = () => { window.focus(); state.current = m.gespraech; show("chat"); renderChat(); };
  } catch (e) { /* ohne Rechte */ }
}

function renderChat() {
  renderBadge();
  const el = $("chat");
  const chat = state.settings.chat || {};
  if (!chat.aktiv) { el.innerHTML = ""; return; }
  const convs = [...state.convs.values()];
  if (!state.current || !state.convs.has(state.current)) state.current = convs.length ? convs[0].id : "";
  const others = state.rooms.filter((r) => r.raum !== state.room).map((r) => `<option>${esc(r.raum)}</option>`).join("");
  const msgs = state.msgs.get(state.current) || [];
  const notice = esc(chat.hinweis || "");
  const focused = document.activeElement && document.activeElement.id === "text";
  const sel = focused ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
  const pending = [...state.pending.values()].filter((p) => p.conv === state.current);
  el.innerHTML = `<h1 class="brand"><img src="web/ottilie-192.png" alt="" width="32" height="32">Chat</h1><div class="chat"><div class="convs">
      ${convs.map((c) => `<button data-conv="${esc(c.id)}" aria-pressed="${c.id === state.current}">${esc(c.titel)}${state.unread.get(c.id) ? ` <span class="badge">${state.unread.get(c.id)}</span>` : ""}</button>`).join("") || '<p class="mut">Keine Gespräche</p>'}
      ${(chat.arten || {}).raum !== false && others ? `<label>Gespräch mit <select id="neu-raum"><option value="">Raum wählen …</option>${others}</select></label><button id="neu-oeffnen">Öffnen</button>` : ""}
      ${"Notification" in window && Notification.permission === "default" ? '<button id="erlauben">Benachrichtigungen erlauben</button>' : ""}
    </div><div>
      ${state.current ? `<div class="notice" role="note">${notice}</div>
      <div class="msgs" id="msgs" aria-label="Nachrichten">${msgs.map((m) => `<div class="msg${m.von === state.room ? " own" : ""}${m.system ? " sys" : ""}${m.dringend ? " urgent" : ""}">
        <div class="meta">${m.system ? '<img src="web/ottilie-192.png" alt="">Ottilie' : esc(m.von)} · ${new Date(m.zeit * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}${m.dringend ? " · dringend" : ""}</div>${esc(m.text)}</div>`).join("")}
        ${pending.map((p) => `<div class="msg own" style="opacity:.6"><div class="meta">wird gesendet …</div>${esc(p.text)}</div>`).join("")}</div>
      ${state.error ? `<p class="bad" role="alert">${esc(state.error)}</p>` : ""}
      <form class="composer" id="composer"><textarea id="text" maxlength="2000" aria-label="Nachricht" required>${esc(state.drafts[state.current] || "")}</textarea>
        <label style="margin:0"><input type="checkbox" id="dringend" ${state.urgentFlags[state.current] ? "checked" : ""}> dringend</label><button class="primary">Senden</button></form>` : ""}
    </div></div>`;
  for (const b of el.querySelectorAll("[data-conv]")) b.addEventListener("click", () => { state.current = b.dataset.conv; markRead(state.current); renderChat(); });
  const pick = $("neu-raum"), openBtn = $("neu-oeffnen");
  if (openBtn) openBtn.addEventListener("click", () => { if (pick.value) send("chat_oeffnen", { mit: pick.value }); });
  const ta = $("text");
  if (ta) {
    ta.addEventListener("input", () => { state.drafts[state.current] = ta.value; });
    if (sel) { ta.focus(); try { ta.setSelectionRange(sel[0], sel[1]); } catch (e) { /* egal */ } }
  }
  const cbx = $("dringend");
  if (cbx) cbx.addEventListener("change", () => { state.urgentFlags[state.current] = cbx.checked; });
  const allow = $("erlauben");
  if (allow) allow.addEventListener("click", () => Notification.requestPermission().then(renderChat));
  const form = $("composer");
  if (form) form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = $("text").value.trim();
    if (!text) return;
    state.error = "";
    const rid = send("chat_senden", { gespraech: state.current, text, dringend: $("dringend").checked });
    state.pending.set(rid, { conv: state.current, text });            // grau, bis die Zentrale bestätigt
    state.drafts[state.current] = ""; state.urgentFlags[state.current] = false;
    renderChat();
  });
  const box = $("msgs"); if (box) box.scrollTop = box.scrollHeight;
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && state.tab === "chat") markRead(state.current); });

// ── Start ─────────────────────────────────────────────────────────────────────
function start() { show("raeume"); connect(); }
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
state.key = loadKey();
if (state.key) start(); else show("koppeln");
