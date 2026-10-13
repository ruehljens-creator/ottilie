// Ottilie – Oberfläche. Dünn: zeichnet den Zustand, den Python liefert, und schickt Befehle.
// Barrierefreiheit (WCAG 2.2 AA): echte Bedienelemente, Tastatur vollständig, Fokus bleibt beim
// Neuzeichnen erhalten, Ansagen über feste Live-Regionen, nie nur Farbe.
"use strict";

const TM = (() => {
  let nextId = 1;
  const pending = new Map();
  const listeners = {};
  const native = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.tm;
  function call(cmd, args) {
    return new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject });
      const text = JSON.stringify({ id, cmd, args: args || {} });
      if (native) native.postMessage(text); else window.Demo.handle(text);
    });
  }
  function receive(m) {
    if (m.type === "reply") {
      const p = pending.get(m.id);
      if (!p) return;
      pending.delete(m.id);
      if (m.ok) p.resolve(m.result); else p.reject(new Error(m.error));
    } else if (m.type === "event") {
      (listeners[m.name] || []).forEach((f) => f(m.data));
    }
  }
  function on(name, f) { (listeners[name] = listeners[name] || []).push(f); }
  return { call, receive, on };
})();
window.TM = TM;   // Python ruft window.TM.receive – „const“ allein hängt nicht an window

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ── Ansagen für Screenreader ────────────────────────────────────────────────
const Live = {
  lastStep: null, lastPct: -1,
  say(text, urgent) {
    const el = document.getElementById(urgent ? "live-urgent" : "live-polite");
    el.textContent = "";
    setTimeout(() => { el.textContent = text; }, 40);
  },
  job(j) {   // Schrittwechsel sofort, sonst alle 25 % – nicht jede Sekunde
    const pct = Math.floor(j.fraction * 4) * 25;
    if (j.state === "fertig") return this.say(`${j.title}: fertig`);
    if (j.state === "fehler" || j.state === "abgebrochen") return this.say(`${j.title}: ${j.error}`, true);
    if (j.step !== this.lastStep) { this.lastStep = j.step; this.lastPct = pct; return this.say(`${j.step || ""}`); }
    if (pct !== this.lastPct) { this.lastPct = pct; this.say(`${pct} Prozent`); }
  },
};

const STEP_TEXT = { done: "erledigt", run: "läuft", wait: "offen", error: "Fehler" };
const STEP_ICON = { done: "✓", run: "●", wait: "○", error: "✕" };

function savedOrder() {
  try { return localStorage.getItem("tm-order") === "sendung" ? "sendung" : "zeit"; } catch (e) { return "zeit"; }
}

const state = {
  status: null, error: null, view: "list", query: "", order: savedOrder(), list: null, selected: null, detail: null,
  dialog: null, toast: null, flow: null, job: null, templates: null, returnFocus: null, preview: null,
  settings: null, rooms: null, history: null, users: null, theme: null, templatesMain: null, tplPick: null,
  tplPickFor: null, pendingTriage: null, lastHvPath: null, dialogShown: null,
};

function toast(text, err) {
  state.toast = { text, err: !!err };
  Live.say(text, !!err);
  render(true);
  setTimeout(() => { state.toast = null; render(true); }, err ? 6000 : 3000);
}

// ── Titelleiste und Seitenleiste ────────────────────────────────────────────
function jobChip() {
  const j = state.job;
  if (!j || !["laeuft", "frage"].includes(j.state)) return "";
  if (state.flow && ["import", "triage", "job"].includes(state.flow.step)) return "";
  const pct = Math.round(j.fraction * 100);
  return `<button class="chip job-chip" data-k="job-chip" data-act="open-job" aria-label="Laufender Auftrag: ${esc(j.title)}, ${pct} Prozent${j.state === "frage" ? ", wartet auf Antwort" : ""}">
    <span>${esc(j.title)}${j.state === "frage" ? " · Frage" : ""}</span><span class="bar" aria-hidden="true"><span style="width:${pct}%"></span></span></button>`;
}

function topbar(st) {
  const pt = !st ? "Pro Tools: …" : (st.pro_tools.ok
    ? "Pro Tools: " + (st.pro_tools.session || "keine Session offen") : "Pro Tools nicht erreichbar");
  // Interner Fehler von Pro Tools (Assertion): bis zum Neustart gehen keine Befehle hin
  const ptHalt = st && st.pro_tools && st.pro_tools.halted
    ? `<span class="chip warn" data-alert title="${esc(st.pro_tools.error || "")}">Pro Tools: interner Fehler – neu starten</span>` : "";
  const h = st && st.helmut;
  const hText = !h ? "Helmut …" : (h.ok ? (h.mock ? "Helmut-Mock verbunden" : "Helmut verbunden") : "Helmut nicht erreichbar");
  const hCls = !h ? "" : (h.ok ? (h.mock ? "mock" : "ok") : "bad");
  const n = st && st.nachtrag;
  const nChip = n && (n.offen || n.konflikte.length)
    ? `<span class="chip ${n.konflikte.length ? "warn" : ""}" role="status">Nachtrag: ${n.offen} offen${n.konflikte.length ? ` · ${n.konflikte.length} Konflikt${n.konflikte.length > 1 ? "e" : ""}` : ""}</span>` : "";
  const fr = st && st.fremd;
  const fChip = fr && (fr.neu || fr.fehlt)
    ? `<button class="chip warn" data-k="chip-fremd" data-act="archive" title="${esc(fr.text || "")}">${fr.neu ? `${fr.neu} Datei${fr.neu > 1 ? "en" : ""} außerhalb des Projekts` : ""}${fr.neu && fr.fehlt ? " · " : ""}${fr.fehlt ? `${fr.fehlt} ${fr.fehlt > 1 ? "fehlen" : "fehlt"}` : ""}</button>` : "";
  const sp = st && st.spiegel;
  const sChip = sp && (sp.zeit || sp.fehler)
    ? `<span class="chip ${sp.fehler ? "warn" : ""}" role="status" title="${esc(sp.ziel || "")}">${sp.fehler ? "Spiegeln: Fehler" : `gespiegelt ${esc(sp.zeit)}`}</span>` : "";
  return `<header class="topbar">
    <div class="brand"><img src="ottilie.png" alt="" width="44" height="44"><span class="title">Ottilie</span></div><div class="grow"></div>
    ${jobChip()}${nChip}${fChip}${sChip}
    ${ptHalt || `<span class="hint mono" style="font-size:12px">${esc(pt)}</span>`}
    <div class="state"><span class="dot ${hCls}" aria-hidden="true"></span>${esc(hText)}</div>
    <button class="chip" data-k="chip-user" data-act="login-open" aria-label="${st && st.user ? `Angemeldet: ${esc(st.user.name)} – Nutzer wechseln` : "Nicht angemeldet – Nutzer wählen"}, Raum ${esc(st ? st.room : "")}">${st && st.user ? esc(st.user.name) : "Nicht angemeldet"} · <strong style="color:var(--text)">${esc(st ? st.room : "")}</strong></button>
  </header>`;
}

function nav() {
  const c = (state.list && state.list.counts) || {};
  const item = (id, label) => {
    const on = !state.flow && !state.settings && state.view === id;
    return `<button class="side-item ${on ? "on" : ""}" data-k="nav-${id}" data-view="${id}" ${on ? 'aria-current="page"' : ""}>
      <span>${label}</span>${c[id] == null ? "" : `<span class="count" aria-label="${c[id]} Beiträge">${c[id]}</span>`}</button>`;
  };
  return `<nav class="nav" aria-label="Ansichten">
    <div class="nav-head" aria-hidden="true">PROJEKTE</div>
    ${item("list", "Zur Mischung")}${item("rooms", "Räume")}${item("history", "Gemachte Mischungen")}
    <div class="grow"></div>
    <button class="side-item" data-k="nav-havarie" data-act="havarie"><span>Paket manuell öffnen …</span></button>
    <button class="side-item" data-k="nav-hvdel" data-act="hvdel"><span>Havarie: Sendefile ausliefern …</span></button>
    <button class="side-item" data-k="nav-archive" data-act="archive"><span>Archiv und fremde Dateien …</span></button>
    <button class="side-item ${state.settings ? "on" : ""}" data-k="nav-settings" data-act="settings" ${state.settings ? 'aria-current="page"' : ""}><span>Einstellungen</span></button>
  </nav>`;
}

// ── Liste und Detail ────────────────────────────────────────────────────────
function catchUpBanner() {
  const n = state.status && state.status.nachtrag;
  const hv = (n && n.ohne_beitrag) || [];
  const hvBox = hv.length ? `<div class="banner warn" role="note" style="margin:8px 16px;flex-direction:column;align-items:flex-start">
    <div>Havarie-Sessions ohne Beitrag – bitte einem Helmut-Beitrag zuordnen, damit Übernahme und Zustand nachgetragen werden:</div>
    <ul class="warnlist">${hv.map((h) => `<li>${esc(h.titel)} <button class="btn ghost sm" data-k="hv-as-${esc(h.id)}" data-hvassign="${esc(h.id)}" data-hvtitle="${esc(h.titel)}">Beitrag zuordnen …</button>
      <button class="btn ghost sm" data-k="hv-drop-${esc(h.id)}" data-ntdrophv="${esc(h.id)}" data-hvtitle="${esc(h.titel)}">Verwerfen …</button></li>`).join("")}</ul>
  </div>` : "";
  if (!n || (!n.offen && !n.konflikte.length)) return hvBox;
  const conf = n.konflikte.map((c) => `<li>${esc(c.text)} – ${esc(c.grund)}
      <button class="btn ghost sm" data-k="nt-drop-${esc(c.id)}" data-ntdrop="${esc(c.id)}">Verwerfen …</button></li>`).join("");
  // Jens 09.10.2026: auch noch offene Änderungen einzeln oder alle verwerfen können
  const open = (n.eintraege || []).map((e) => `<li>${esc(e.text)}
      <button class="btn ghost sm" data-k="nt-drop-${esc(e.id)}" data-ntdrop="${esc(e.id)}">Verwerfen …</button></li>`).join("");
  return `<div class="banner ${n.konflikte.length ? "warn" : "info"}" role="note" style="margin:8px 16px;flex-direction:column;align-items:flex-start">
    <div>${n.offen ? `${n.offen} Änderung${n.offen > 1 ? "en warten" : " wartet"} auf Helmut und ${n.offen > 1 ? "werden" : "wird"} nachgetragen.` : ""}
      ${n.konflikte.length ? " Nicht nachgetragen, weil der Beitrag inzwischen anders zugewiesen ist:" : ""}</div>
    ${open ? `<ul class="warnlist">${open}</ul>` : ""}
    ${conf ? `<ul class="warnlist">${conf}</ul>` : ""}
    <div style="display:flex;gap:8px"><button class="btn sm" data-k="nt-now" data-act="catchup">Jetzt nachtragen</button>
      <button class="btn ghost sm" data-k="nt-drop-all" data-act="nt-drop-all">Alle verwerfen …</button></div>
  </div>` + hvBox;
}

function listView() {
  const rows = state.list ? state.list.rows : null;
  const title = "Zur Mischung anstehend";
  const full = rows && rows.some((p) => p.due !== "–" || p.length !== "–" || p.cutter !== "–");
  const bySend = state.order === "sendung";
  const sub = (p) => [bySend ? "" : esc(p.show), p.air && p.air !== "–" ? esc(p.air) : "", p.handed ? `übergeben ${esc(p.handed)}` : ""]
    .filter(Boolean).join(" · ");
  const counts = {};
  (rows || []).forEach((p) => { counts[p.group] = (counts[p.group] || 0) + 1; });
  let lastGroup = null;
  const head1 = (p) => {
    if (!bySend || p.group === lastGroup) return "";
    lastGroup = p.group;
    return `<div class="row-group" role="heading" aria-level="2">${esc(p.group)} <span class="muted">${counts[p.group]}</span></div>`;
  };
  const body = !rows ? `<div class="empty" role="status">Lade Beiträge …</div>`
    : (!rows.length ? `<div class="empty" role="status">${state.query ? "Keine Treffer." : "Keine Beiträge."}</div>` : rows.map((p) => `${head1(p)}
      <button class="row ${full ? "" : "compact"} ${state.selected === p.id ? "sel" : ""}" data-k="row-${esc(p.id)}" data-pick="${esc(p.id)}"
        aria-current="${state.selected === p.id ? "true" : "false"}" aria-label="${esc(p.title)}, ${esc(p.show)}, ${esc(p.state)}${p.urgent ? ", dringend" : ""}${(p.tags || []).map((g) => ", " + esc(g)).join("")}">
        <span class="dot ${p.urgent ? "urgent" : "normal"}" aria-hidden="true"></span>
        <span class="t1"><span class="name">${esc(p.title)}${(p.tags || []).map((g) => ` <span class="tag">${esc(g)}</span>`).join("")}</span><span class="muted">${sub(p)}</span></span>
        ${full ? `<span class="mono">${esc(p.due)}</span><span class="mono">${esc(p.length)}</span><span>${esc(p.cutter)}</span>` : ""}
        <span><span class="pill ${p.pill}">${esc(p.state)}</span></span><span></span>
      </button>`).join(""));
  const head = full ? `<span></span><span>Beitrag / Sendung</span><span>Sendung in</span><span>Länge</span><span>Cutter</span><span>Status</span><span></span>`
    : `<span></span><span>Beitrag / Sendung</span><span>Status</span><span></span>`;
  return `<main class="main" aria-labelledby="list-title">
    <div class="list-head">
      <div class="title"><h1 id="list-title" tabindex="-1" data-k="view-title">${title}</h1><span class="muted">${rows ? rows.length : ""} Beiträge</span></div>
      ${roomStrip()}
      <label class="sr-only" for="q">Suchen in Titel, Sendung, Cutter, Mischer, Raum (⌘F)</label>
      <input class="inp" id="q" data-k="q" type="search" placeholder="Suchen: Titel, Sendung, Cutter, Mischer, Raum" value="${esc(state.query)}">
      <div class="seg-group" role="radiogroup" aria-label="Sortierung">
        ${[["zeit", "Nach Zeit"], ["sendung", "Nach Sendung"]].map(([v, l]) => `<button class="seg ${state.order === v ? "on" : ""}" role="radio" aria-checked="${state.order === v}" data-k="order-${v}" data-order="${v}">${l}</button>`).join("")}
      </div>
    </div>
    ${catchUpBanner()}
    <div class="row-head ${full ? "" : "compact"}" aria-hidden="true">${head}</div>
    <div class="rows" role="list" aria-label="Beiträge">${body}</div>
  </main>`;
}

// Raumübersicht im Hauptfenster (Jens 04.10.2026: „direkt im Hauptfenster über alle Räume und was dort gerade
// geschieht“), Aussehen wie im Prototyp (roomStrip): Chip mit Punkt und Raumname; dazu (Jens
// 04.10.2026) wer dort ist und welcher Beitrag bearbeitet wird – nur Beiträge, nach dem Zurückliefern „frei“.
// Klick öffnet die Ansicht „Räume“ mit allen Angaben und „Beitrag zurückgeben …“
// Farbe und (bei hohem Kontrast / rot-grün-sicher) Form des Punkts kommen aus themes.css (.dot.st-…)
const ROOM_STATES = ["frei", "mischt", "importiert", "exportiert", "offline"];
function roomClass(c) {                // nicht erreichbar: grau, auch wenn Helmut den Raum frei meldet
  return "st-" + (c.online === false || !ROOM_STATES.includes(c.st) ? "offline" : c.st);
}
function roomWork(c) {
  const offline = c.st === "offline" || c.online === false;
  const step = c.st === "importiert" ? `Import ${c.pct || 0} %` : (c.st === "exportiert" ? `Export ${c.pct || 0} %` : "");
  const what = offline ? "" : (c.title || "");
  return [offline ? "offline" : step, what].filter(Boolean).join(" · ") || "frei";
}
function roomTip(c) {
  const who = c.user || c.mixer;
  return [`${c.name}${c.here ? " (dieser Mac)" : ""} · ${c.label}`, who ? `Mischer: ${who}` : "niemand angemeldet",
    c.title ? `Beitrag: ${c.title}` : "kein Beitrag in Arbeit"].join("\n");
}
function roomStrip() {
  const r = state.rooms;
  if (!r || !r.cards || !r.cards.length) return "";
  const chips = r.cards.map((c, i) => {
    const who = c.st === "offline" || c.online === false ? "" : (c.user || c.mixer || "");
    return `<button class="chip room-chip" data-k="rs-${i}" data-view="rooms" title="${esc(roomTip(c))}"
      aria-label="${esc(roomTip(c).replace(/\n/g, ", "))}">
      <span class="dot ${roomClass(c)}" aria-hidden="true"></span><strong>${esc(c.name)}</strong>${who ? `<span>${esc(who)}</span>` : ""}<span class="muted ellip">${esc(roomWork(c))}</span></button>`;
  }).join("");
  return `<nav class="room-strip" aria-label="Räume – Klick öffnet die Übersicht">${chips}</nav>`;
}

async function loadRooms() {
  try { state.rooms = await TM.call("raeume", {}); } catch (e) { /* Raumstatus nicht erreichbar: Leiste bleibt leer */ }
}

function roomsView() {
  const r = state.rooms;
  const cards = !r ? `<div class="empty" role="status">Lade Räume …</div>` : r.cards.map((c, i) => `
    <section class="room-card" aria-label="${esc(c.name)}: ${esc(c.label)}${c.mixer ? ", " + esc(c.mixer) : ""}">
      <div class="room-head"><span class="dot ${roomClass(c)}" aria-hidden="true"></span>
        <strong>${esc(c.name)}</strong>${c.here ? ' <span class="tag">hier</span>' : ""}<span class="grow"></span><span class="pill ${c.st === "frei" && c.online !== false ? "ready" : "busy"}">${esc(c.label)}</span></div>
      <div>${c.user || c.mixer ? esc(c.user || c.mixer) : '<span class="muted">niemand angemeldet</span>'}</div>
      <div class="muted">${c.title ? esc(c.title) : "kein Beitrag in Arbeit"}${c.busy_count > 1 ? ` (+${c.busy_count - 1} weitere)` : ""}</div>
      ${c.pct ? `<div class="bar" role="progressbar" aria-label="Fortschritt ${esc(c.name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${c.pct}"><span style="width:${c.pct}%"></span></div>` : ""}
      ${c.can_release ? `<button class="btn sm" data-k="room-rel-${i}" data-roomrel="${esc(c.id)}">Beitrag zurückgeben …</button>` : ""}
      ${c.source ? `<div class="hint">${esc(c.source)}${c.online === true ? " · App läuft" : ""}</div>` : ""}
    </section>`).join("");
  return `<main class="main" aria-labelledby="rooms-title">
    <div class="list-head"><div class="title"><h1 id="rooms-title" tabindex="-1" data-k="view-title">Räume</h1><span class="muted">${r ? `${r.busy} belegt` : ""}</span></div></div>
    <div class="room-grid">${cards}</div>
    <p class="hint" style="margin:8px 16px">${r ? esc(r.source) : ""}</p>
  </main>`;
}

// Verzeichnis der auf diesem Mac gemachten Mischungen (Jens 09.10.2026); „Liste leeren“ lässt Helmut, Sessions und
// Dateien unverändert
function historyView() {
  const h = state.history;
  const rows = !h ? `<tr><td colspan="6" role="status">Lade …</td></tr>`
    : (!h.eintraege.length ? `<tr><td colspan="6" class="muted">Noch keine Mischungen auf diesem Mac.</td></tr>`
      : h.eintraege.map((e) => `<tr><td class="mono">${esc(e.zeit)}</td>
        <td><strong>${esc(e.titel)}</strong>${e.sendung ? `<div class="muted">${esc(e.sendung)}</div>` : ""}</td>
        <td>${esc(e.art)}</td><td>${esc([e.mischer, e.raum].filter(Boolean).join(" · ") || "–")}</td>
        <td title="${esc(e.ordner || "")}">${esc((e.dateien || []).join(", ") || "–")}</td>
        <td class="mono" title="${esc(e.session || "")}">${esc((e.session || "–").split("/").pop())}</td></tr>`).join(""));
  return `<main class="main" aria-labelledby="hist-title">
    <div class="list-head"><div class="title"><h1 id="hist-title" tabindex="-1" data-k="view-title">Gemachte Mischungen</h1>
      <span class="muted">${h ? `${h.anzahl} auf diesem Mac` : ""}</span><span class="grow"></span>
      <button class="btn sm" data-k="hist-clear" data-act="hist-clear" ${h && h.anzahl ? "" : 'disabled aria-disabled="true"'}>Liste leeren …</button></div>
      <span class="hint">Zurückgelieferte Beiträge und Havarie-Sendefiles dieses Macs, neueste zuerst. „Liste leeren“ leert nur dieses Verzeichnis – Helmut, Sessions und Dateien bleiben, wie sie sind.</span></div>
    <div class="rows" style="padding:0 16px"><table class="clips"><caption class="sr-only">Gemachte Mischungen</caption>
      <thead><tr><th scope="col">Zeit</th><th scope="col">Beitrag</th><th scope="col">Art</th><th scope="col">Mischer · Raum</th><th scope="col">Dateien</th><th scope="col">Session</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
  </main>`;
}

function detailView() {
  const d = state.detail;
  if (!d) return `<aside class="detail" aria-label="Details"><div class="detail-body"><span class="muted">Beitrag wählen</span></div></aside>`;
  const a = d.actions;
  const stale = d.id !== state.selected;          // neuer Beitrag gewählt, Details noch unterwegs: nichts auslösbar
  return `<aside class="detail${stale ? " stale" : ""}" aria-labelledby="detail-title"${stale ? ' inert aria-busy="true"' : ""}>
    <div class="detail-body">
      <div class="kicker">${esc(d.show)}${d.air && d.air !== "–" ? " · " + esc(d.air) : ""}</div>
      <h2 id="detail-title">${esc(d.title)}</h2>
      ${d.banners.map((b) => `<div class="banner ${b.cls}" role="note">${esc(b.text)}</div>`).join("")}
      <dl class="kv">${d.kv.map((x) => `<dt>${esc(x.k)}</dt><dd>${esc(x.v)}</dd>`).join("")}</dl>
      ${a.backups || a.finder ? `<div style="display:flex;gap:8px;flex-wrap:wrap">
        ${a.finder ? `<button class="btn ghost sm" data-k="act-finder" data-act="finder">Im Finder zeigen</button>` : ""}
        ${a.backups ? `<button class="btn ghost sm" data-k="act-backups" data-act="backups">Sicherungen …</button>` : ""}</div>` : ""}
    </div>
    <div class="detail-foot">
      ${a.duplicate ? `<button class="btn" data-k="act-dup" data-act="dup">Beitrag duplizieren …</button>` : ""}
      ${a.release ? `<button class="btn" data-k="act-release" data-act="release">Freigeben …</button>` : ""}
      ${a.deliver ? `<button class="btn" data-k="act-deliver" data-act="deliver">Zurückliefern …</button>` : ""}
      ${a.fetch ? mainTemplatePicker(d) : ""}
      <button class="btn pri" data-k="act-fetch" data-act="fetch" ${a.fetch ? "" : 'disabled aria-disabled="true"'}>${esc(a.fetch_label)}</button>
    </div>
  </aside>`;
}

// Template-Auswahl im Hauptfenster (Jens 04.10.2026: „unten rechts, Standard-Template immer vorgewählt“)
const tplKey = (x) => x ? `${x.group}\u0001${x.name}` : "";
function mainTemplatePicker(d) {
  const t = state.templatesMain;
  if (!t) { loadMainTemplates(); return ""; }
  if (state.tplPickFor !== d.id) { state.tplPickFor = d.id; state.tplPick = tplKey(t.standard || t.default); }
  const opt = (x) => `<option value="${esc(tplKey(x))}" ${tplKey(x) === state.tplPick ? "selected" : ""}>${esc(x.label)}${t.standard && tplKey(x) === tplKey(t.standard) ? " (Standard)" : ""}</option>`;
  const groups = {};
  t.found.forEach((x) => { (groups[x.group] = groups[x.group] || []).push(x); });
  return `<select class="inp" style="max-width:260px" data-k="main-tpl" data-maintpl="1" aria-label="Template für das Holen">
    ${state.tplPick ? "" : `<option value="" selected>– Template wählen –</option>`}
    ${t.mine.length ? `<optgroup label="Meine Templates">${t.mine.map(opt).join("")}</optgroup>` : ""}
    ${Object.keys(groups).map((g) => `<optgroup label="${esc(g)}">${groups[g].map(opt).join("")}</optgroup>`).join("")}</select>`;
}

let mainTplLoading = false;
async function loadMainTemplates() {
  if (mainTplLoading) return;
  mainTplLoading = true;
  try { state.templatesMain = await TM.call("templates", {}); state.tplPickFor = null; } catch (e) { /* ohne Auswahl */ }
  mainTplLoading = false;
  render();
}

// ── Ablauf „Holen“ ──────────────────────────────────────────────────────────
function flowSteps(active, defs) {
  defs = defs || [["template", "Template & Raum"], ["triage", "AAF-Triage"], ["import", "Import"]];
  const idx = defs.findIndex((d) => d[0] === active);
  return `<ol aria-label="Schritte">${defs.map((d, i) => {
    const cls = i < idx ? "done" : (i === idx ? "on" : "");
    const sr = i < idx ? " (erledigt)" : (i === idx ? " (aktuell)" : "");
    return `<li class="step ${cls}" ${i === idx ? 'aria-current="step"' : ""}><span class="n" aria-hidden="true">${i < idx ? "✓" : i + 1}</span>${d[1]}<span class="sr-only">${sr}</span></li>`;
  }).join("")}</ol>`;
}

function templateStep() {
  const f = state.flow;
  const t = state.templates;
  if (!t) return `<div class="flow-body"><div role="status">Lade Templates …</div></div>`;
  const pre = t.default;
  const opt = (x) => {
    const on = f.choice && f.choice.group === x.group && f.choice.name === x.name;
    const isPre = pre && pre.group === x.group && pre.name === x.name && t.hint;
    return `<button class="opt ${on ? "on" : ""}" role="radio" aria-checked="${on}" data-k="tpl-${esc(x.group)}/${esc(x.name)}"
      data-tpl="${esc(x.group)}\u0001${esc(x.name)}"><span class="radio" aria-hidden="true"></span>
      <span><strong>${esc(x.label)}</strong><span class="sub">${esc(x.group)}${x.info && !x.info.startsWith("Pro Tools") ? " · " + esc(x.info) : ""}${isPre ? " · " + esc(t.hint) : ""}</span></span></button>`;
  };
  const groups = {};
  t.found.forEach((x) => { (groups[x.group] = groups[x.group] || []).push(x); });
  const mine = t.mine.length ? `<div class="grp" aria-hidden="true">MEINE TEMPLATES</div>${t.mine.map(opt).join("")}` : "";
  const found = Object.keys(groups).map((g) => `<div class="grp" aria-hidden="true">${esc(g.toUpperCase())}</div>${groups[g].map(opt).join("")}`).join("");
  const room = state.status ? state.status.room : "";
  const o = f.open;
  const openMode = f.mode === "offen" && o && o.offer;
  const modes = o && o.offer ? `<div role="radiogroup" aria-label="Wohin importieren" style="display:flex;flex-direction:column;gap:8px;margin-bottom:12px">
      ${[["neu", "Neue Session aus Template", "Die offene Session wird gespeichert und geschlossen, dann wird eine neue Session angelegt."],
         ["offen", `In offene Session „${o.name}“ importieren`, "Das AAF wird in die geöffnete Session geladen. Vorher wird sie gespeichert; wo sie danach liegt, bestimmt die Auswahl darunter."]]
        .map(([v, l, h]) => `<button class="opt ${f.mode === v ? "on" : ""}" role="radio" aria-checked="${f.mode === v}" data-k="mode-${v}" data-mode="${v}"><span class="radio" aria-hidden="true"></span>
          <span><strong>${esc(l)}</strong><span class="sub">${esc(h)}</span></span></button>`).join("")}</div>` : "";
  const sel = (k, label) => `<label class="field"><span>${esc(label)}</span><select class="inp" data-k="open-${k}" data-openopt="${k}" aria-label="${esc(label)}">
      ${o.choices[k].map((c) => `<option value="${esc(c.value)}" ${f.openOpts[k] === c.value ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select></label>`;
  const left = openMode ? `<section aria-labelledby="h-tpl">
      <h2 id="h-tpl">Import in die offene Session</h2>
      ${modes}
      <div style="display:flex;flex-direction:column;gap:10px">
        ${sel("umzug", "Wohin mit der offenen Session")}
        <span class="hint">${esc(o.hints[f.openOpts.umzug] || "")}</span>
        ${sel("clips", "Clips")}${sel("position", "Position")}${sel("bisheriger_beitrag", "Bisheriger Beitrag in Helmut")}
        <span class="hint">Ohne Triage-Verteilung: Clips kommen auf die gleichnamigen Spuren der offenen Session, fehlende Spuren werden erfragt.</span>
      </div>
    </section>` : `<section aria-labelledby="h-tpl">
      ${modes}
      <h2 id="h-tpl">In welches Template importieren?</h2>
      <span class="hint">Neue Session aus dem Template. ${pre && t.hint ? `Vorgewählt: ${esc(t.hint)}.` : "Kein Standard festgelegt – bitte wählen."}</span>
      <div class="tpl-list" role="radiogroup" aria-labelledby="h-tpl">${mine}${found || '<span class="hint">Keine Templates gefunden.</span>'}</div>
    </section>`;
  return `<div class="flow-body"><div class="cols">
    ${left}
    <section aria-labelledby="h-room">
      <h2 id="h-room">Wo soll gemischt werden?</h2>
      <div role="radiogroup" aria-labelledby="h-room" style="display:flex;flex-direction:column;gap:8px">
        <button class="opt on" role="radio" aria-checked="true" data-k="room-here"><span class="radio" aria-hidden="true"></span>
          <span><strong>Hier öffnen · ${esc(room)}</strong><span class="sub">Triage und Import laufen jetzt an diesem Mac</span></span></button>
        <button class="opt" role="radio" aria-checked="false" aria-disabled="true" disabled><span class="radio" aria-hidden="true"></span>
          <span><strong>An anderen Raum weitergeben</strong><span class="sub">folgt später</span></span></button>
      </div>
      <h2 style="margin-top:12px">Import</h2>
      ${openMode ? "" : `<label class="chk"><input type="checkbox" data-k="opt-triage" data-opt="triage" ${f.triage ? "checked" : ""}>
        <span>AAF-Triage <span class="hint" style="display:block">Clips als O-Ton, Atmo, Musik auf die Zielspuren verteilen</span></span></label>`}
      <label class="chk"><input type="checkbox" data-k="opt-video" data-opt="video" ${f.video ? "checked" : ""}>
        <span>Referenzvideo importieren</span></label>
    </section>
  </div></div>
  <div class="flow-foot">
    <button class="btn" data-k="flow-cancel" data-act="flow-cancel">Abbrechen</button>
    <button class="btn pri" data-k="flow-start" data-act="flow-start" ${f.choice || openMode ? "" : 'disabled aria-disabled="true"'}>${!openMode && f.triage && t.preview !== "nie" ? "Weiter zur Triage" : "Import starten"}</button>
  </div>`;
}

function importStep() {
  const j = state.job;
  if (!j) return `<div class="flow-body"><div role="status">Starte …</div></div>`;
  const pct = Math.round(j.fraction * 100);
  const steps = j.steps.map((s) => `<li class="prog ${s.state}"><span class="st" aria-hidden="true">${STEP_ICON[s.state]}</span>
      <span>${esc(s.name)}</span><span class="sr-only">${STEP_TEXT[s.state]}</span></li>`).join("");
  let end = "";
  if (j.state === "fertig" && j.result && j.kind === "zurueckliefern") {
    const r = j.result;
    end = `<div class="banner ok" role="status">${r.wavs.length} WAVs an Helmut übergeben. Zustand in Helmut: ausgeliefert, Beitrag freigegeben.</div>
      <dl class="kv"><dt>Ordner</dt><dd>${esc(r.folder)}</dd></dl>
      <ul class="warnlist" aria-label="Übergebene WAVs">${r.wavs.map((w) => `<li class="mono">${esc(w)}</li>`).join("")}</ul>
      ${r.warnings.length ? `<h3 style="margin:0;font-size:15px">Hinweise</h3><ul class="warnlist">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}`;
  } else if (j.state === "fertig" && j.result && j.kind === "havarie_ausliefern") {
    const r = j.result;
    const sent = r.uebertragen.filter((u) => u.ok).map((u) => u.ziel);
    const failed = r.uebertragen.filter((u) => !u.ok);
    end = `<div class="banner ${r.gestoppt || failed.length ? "warn" : "ok"}" role="status">${r.gestoppt ? "Sendefile erstellt, aber nicht übertragen – die Prüfung fand Fehler." : sent.length ? `Sendefile an ${esc(sent.join(", "))} übertragen.` : "Sendefile erstellt (kein Ziel gewählt)."}</div>
      <dl class="kv"><dt>Sendefile</dt><dd class="mono">${esc(r.sendefile)}</dd><dt>Prüfung</dt><dd>${esc(r.pruefung)}</dd>
        <dt>Bericht</dt><dd class="mono">${esc(r.bericht)}</dd></dl>
      ${failed.length ? `<ul class="warnlist">${failed.map((u) => `<li>${esc(u.ziel)}: ${esc(u.fehler)}</li>`).join("")}</ul>` : ""}
      ${r.warnings.length ? `<h3 style="margin:0;font-size:15px">Hinweise</h3><ul class="warnlist">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}`;
  } else if (j.state === "fertig" && j.result && j.kind === "wiederoeffnen") {
    const r = j.result;
    end = `<div class="banner ok" role="status">Kopie ist in Pro Tools geöffnet. Zustand in Helmut: importiert.</div>
      <dl class="kv"><dt>Session</dt><dd class="mono">${esc(r.session)}</dd><dt>Spuren</dt><dd>${r.tracks}</dd></dl>
      ${r.warnings.length ? `<h3 style="margin:0;font-size:15px">Hinweise</h3><ul class="warnlist">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}`;
  } else if (j.state === "fertig" && j.result) {
    const r = j.result;
    end = `<div class="banner ok" role="status">Session ist in Pro Tools geöffnet. Zustand in Helmut: importiert.</div>
      <dl class="kv">
        ${r.open_import ? `<dt>Import</dt><dd>${esc(r.how)}</dd>${r.previous ? `<dt>Bisheriger Beitrag</dt><dd>„${esc(r.previous)}“ ${esc(r.previous_action)}</dd>` : ""}` : ""}
        <dt>Clips</dt><dd>${r.clips_ok} von ${r.clips} exakt an ihrer Stelle</dd>
        ${r.triage ? `<dt>Triage</dt><dd>${r.moves} Bereiche verschoben</dd>` : ""}
        <dt>Session</dt><dd>${esc(r.session)}</dd>
      </dl>
      ${r.warnings.length ? `<h3 style="margin:0;font-size:15px">Hinweise</h3><ul class="warnlist">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}`;
  } else if (j.state === "fehler" || j.state === "abgebrochen") {
    end = `<div class="banner warn">${esc(j.error)}</div>`;             // angesagt von Live.job
  }
  const finished = ["fertig", "fehler", "abgebrochen"].includes(j.state);
  const offerRelease = finished && j.state !== "fertig" && (j.kind === "holen" || j.kind === "wiederoeffnen") && state.detail && state.detail.id === j.contribution && state.detail.actions.release;
  return `<div class="flow-body"><div class="progress-box">
    <h2>${esc(j.title)}</h2>
    <div class="progress-line"><div class="bar" role="progressbar" aria-label="Fortschritt" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><span style="width:${pct}%"></span></div>
      <span class="mono" aria-hidden="true">${pct} %</span></div>
    <div class="hint">${esc(j.text)}</div>
    ${j.state === "frage" && j.question ? `<div class="banner warn" role="status">Wartet auf deine Antwort: ${esc(j.question.title || "Frage")}
      <button class="btn sm" data-k="flow-show-q" data-act="show-question">Zur Frage</button></div>` : ""}
    <ol style="list-style:none;margin:0;padding:0" aria-label="Schritte">${steps}</ol>
    ${end}
  </div></div>
  <div class="flow-foot">
    ${offerRelease ? `<button class="btn" data-k="flow-release" data-act="release">Übernahme aufheben (Freigeben) …</button>` : ""}
    ${finished && j.state === "fertig" && j.kind === "havarie" ? `<button class="btn" data-k="flow-hvdel" data-act="hvdel">Später: Sendefile ausliefern …</button>` : ""}
    ${!finished && j.cancellable ? `<button class="btn" data-k="flow-job-cancel" data-act="job-cancel" ${j.cancel_requested ? 'disabled aria-disabled="true"' : ""}>${j.cancel_requested ? "Wird abgebrochen …" : "Abbrechen"}</button>` : ""}
    <button class="btn ${finished ? "pri" : ""}" data-k="flow-back" data-act="flow-back">${finished ? "Zur Liste" : "Im Hintergrund weiterlaufen lassen"}</button>
  </div>`;
}

// ── Triage-Vorschau ─────────────────────────────────────────────────────────
function clipLabel(c) {
  return `${c.name}, ${c.tc_in} bis ${c.tc_out}, ${c.label}${c.unsure ? ", unsicher" : ""}${c.moved ? ", wird verschoben" : ""}, Zielspur ${c.targets}`;
}

function timelineView(v, sel) {
  const ticks = v.ticks.map((t) => `<span style="left:${t.left}%">${esc(t.tc)}</span>`).join("");
  const lanes = v.lanes.map((l) => `<div class="lane" role="group" aria-label="Zielspur ${esc(l.id)}, ${esc(l.label)}, ${l.clips.length} Clips">
      <span class="lane-lbl"><span class="sw ${l.cat}" aria-hidden="true"></span><span class="mono">${esc(l.id)}</span></span>
      <div class="lane-track">${l.clips.map((c) => `<button class="clip ${c.cat} ${c.unsure ? "unsure" : ""} ${c.moved ? "moved" : ""} ${sel === c.key ? "sel" : ""}"
        style="left:${c.left}%;width:${c.width}%" data-k="clip-${esc(l.id)}-${esc(c.key)}" data-clip="${esc(c.key)}" data-lane="${esc(l.id)}"
        aria-pressed="${sel === c.key}" aria-label="${esc(clipLabel(c))}">${c.unsure ? "? " : ""}${esc(c.name)}</button>`).join("")}</div>
    </div>`).join("");
  return `<div class="timeline"><div class="tl-head" aria-hidden="true"><span style="padding:6px 12px">Zielspur</span><span class="ticks">${ticks}</span></div>${lanes}</div>
    <span class="hint">Tasten: ← → nächster Clip, ↑ ↓ andere Spur, 1–4 verschieben. Die Liste zeigt dasselbe als Tabelle.</span>`;
}

function listTable(v, sel, filter) {
  const rows = filter ? v.rows.filter((r) => r.unsure || r.moved || r.reason === "Korrektur in der Vorschau") : v.rows;
  const opts = (r) => v.categories.map((c) => `<option value="${c.value}" ${c.value === r.category ? "selected" : ""}>${esc(c.label)}</option>`).join("");
  return `<label class="chk"><input type="checkbox" data-k="tri-filter" data-trifilter ${filter ? "checked" : ""}><span>Nur unsichere, verschobene und korrigierte Clips (${rows.length})</span></label>
    <table class="clips"><caption class="sr-only">Clips der AAF mit Kategorie und Zielspur</caption>
    <thead><tr><th scope="col">Zeit</th><th scope="col">Quellspur</th><th scope="col">Quelle</th><th scope="col">Kategorie</th><th scope="col">Sicherheit</th><th scope="col">Zielspur</th></tr></thead>
    <tbody>${rows.map((r) => `<tr class="${sel === r.key ? "sel" : ""}">
      <td class="mono">${esc(r.tc_in)}–${esc(r.tc_out)}</td><td>${esc(r.track)}</td>
      <td><button class="btn ghost sm" data-k="trow-${esc(r.key)}" data-clip="${esc(r.key)}" aria-label="Clip ${esc(r.name)} auswählen">${esc(r.name)}</button></td>
      <td>${r.category === "sprecher" ? "Sprecher" : `<select class="inp" data-k="tcat-${esc(r.key)}" data-cat-for="${esc(r.key)}" aria-label="Kategorie für ${esc(r.name)} ${esc(r.tc_in)}">${opts(r)}</select>`}
        ${r.unsure ? '<span class="badge warn">unsicher</span>' : ""}${r.moved ? '<span class="badge">verschoben</span>' : ""}</td>
      <td>${r.confidence == null ? "–" : r.confidence + " %"}</td><td class="mono">${esc(r.targets)}</td></tr>`).join("")}</tbody></table>`;
}

function inspectorView(v, sel) {
  const c = v.rows.find((r) => r.key === sel);
  const foot = `<div class="hint">${v.clips} Clips auf ${v.tracks_used} Spuren · Namens-Matching mit Template „${esc(v.template)}“</div>`;
  if (!c) return `<aside class="inspector" aria-label="Clip-Inspektor"><div class="muted">Clip wählen, um ihn zu prüfen oder zu verschieben.</div>${foot}</aside>`;
  const mv = v.categories.map((k, i) => `<button class="mv" data-k="mv-${k.value}" data-move="${k.value}" aria-pressed="${k.value === c.category}"
      ${c.category === "sprecher" ? 'disabled aria-disabled="true"' : ""}><span class="sw ${k.cat}" aria-hidden="true"></span>${esc(k.label)}<span class="hint" aria-hidden="true">${i + 1}</span></button>`).join("");
  return `<aside class="inspector" aria-labelledby="insp-title">
    <div class="muted" style="font-size:12px;font-weight:600;letter-spacing:.06em">AUSGEWÄHLTER CLIP</div>
    <h3 id="insp-title">${esc(c.name)}</h3>
    <dl class="kv"><dt>Zeit</dt><dd class="mono">${esc(c.tc_in)}–${esc(c.tc_out)}</dd><dt>Quellspur</dt><dd>${esc(c.track)}</dd>
      <dt>Regel</dt><dd>${esc(c.reason)}</dd><dt>Sicherheit</dt><dd>${c.confidence == null ? "–" : c.confidence + " %"}</dd>
      <dt>Kategorie</dt><dd>${esc(c.label)}${c.unsure ? " (unsicher)" : ""}</dd><dt>Zielspur</dt><dd class="mono">${esc(c.targets)}</dd></dl>
    <div class="lbl" id="mv-label">Verschieben nach</div>
    <div class="mv-grid" role="group" aria-labelledby="mv-label">${mv}</div>
    <span class="hint">Gilt für beide Hälften eines Paares.</span>
    ${foot}
  </aside>`;
}

function triageStep() {
  const p = state.preview;
  const v = p.view;
  const banners = [];
  if (v.unsure) banners.push(`<div class="banner warn" role="note">${v.unsure} Clips ohne sichere Zuordnung – sie kommen ab A11.</div>`);
  if (v.moves) banners.push(`<div class="banner info" role="note">${v.moves} Bereiche werden nach dem Import auf ihre Zielspur verschoben.</div>`);
  v.notes.forEach((n) => banners.push(`<div class="banner info" role="note">${esc(n)}</div>`));
  const legend = v.categories.map((c) => `<span><span class="sw ${c.cat}" aria-hidden="true"></span>${esc(c.label)}</span>`).join("")
    + `<span><span class="sw unsure-sw" aria-hidden="true"></span>unsicher (?)</span><span>⇄ wird verschoben</span>`;
  const tab = (id, label) => `<button class="tab ${p.mode === id ? "on" : ""}" role="tab" aria-selected="${p.mode === id}" data-k="tab-${id}" data-tab="${id}">${label}</button>`;
  return `<div class="triage">
    <div class="triage-main">
      ${banners.join("")}
      <div class="legend" aria-label="Legende">${legend}</div>
      <div class="tabs" role="tablist" aria-label="Ansicht">${tab("zeit", "Zeitleiste")}${tab("liste", "Liste")}</div>
      <div role="tabpanel">${p.mode === "zeit" ? timelineView(v, p.sel) : listTable(v, p.sel, p.filter)}</div>
    </div>
    ${inspectorView(v, p.sel)}
  </div>
  <div class="flow-foot">
    <button class="btn" data-k="tri-cancel" data-act="tri-cancel">Abbrechen</button>
    <button class="btn pri" data-k="tri-ok" data-act="tri-ok">Import starten</button>
  </div>`;
}

function deliverOverview() {
  const f = state.flow;
  const i = f.info;
  if (!i) return `<div class="flow-body"><div role="status">Lade …</div></div>`;
  const all = i.session_tracks.concat(i.tracks.filter((t) => !i.session_tracks.includes(t)));
  const listed = all.filter((t) => f.sel[t]);
  const missing = i.session_open ? listed.filter((t) => !i.session_tracks.includes(t)) : [];
  return `<div class="flow-body"><div class="cols">
    <section aria-labelledby="h-del">
      <h2 id="h-del">An Helmut zurückliefern</h2>
      <span class="hint">Konsolidieren über die Videolänge, Lautheit nach EBU R 128, je Spur eine WAV (24 bit / 48 kHz) in den Übergabeordner.</span>
      <dl class="kv">
        <dt>Session</dt><dd>${esc(i.session || "–")}${i.session ? (i.session_open ? " (offen)" : " (wird geöffnet)") : ""}</dd>
        <dt>Übergabeordner</dt><dd>${esc(i.target)}</dd>
        <dt>Lautheit</dt><dd>${i.loudness_on ? `${esc(i.lufs)} LUFS, True Peak höchstens ${esc(i.truepeak)} dBTP für ${esc(i.loudness.join(", ") || "–")}` : "aus"}</dd>
      </dl>
    </section>
    <section aria-labelledby="h-tracks">
      <h2 id="h-tracks">Exportspuren</h2>
      <fieldset class="tracklist" aria-describedby="del-hint${missing.length ? " del-miss" : ""}"><legend class="sr-only">Exportspuren</legend>
        ${all.map((t, k) => `<label class="chk"><input type="checkbox" id="${k ? `del-t-${k}` : "del-tracks"}" data-k="del-t-${k}" data-del-track="${esc(t)}" ${f.sel[t] ? "checked" : ""} aria-label="${esc(t)} exportieren">
          <span>${esc(t)}${i.session_open && !i.session_tracks.includes(t) ? ' <span class="hint">(nicht in der Session)</span>' : ""}</span></label>`).join("")}</fieldset>
      <span class="hint" id="del-hint">${i.session_open ? "Spuren der offenen Session, vorbelegt" : "Session nicht offen – Spuren"} aus der Spurtabelle bzw. dem Spurprofil. Leere Spuren (AD, Dolby E) gehen als digitale Null mit.</span>
      ${missing.length ? `<div class="banner warn" id="del-miss" data-alert>In der offenen Session fehlt: ${esc(missing.join(", "))}</div>` : ""}
    </section>
  </div></div>
  <div class="flow-foot">
    <button class="btn" data-k="flow-cancel" data-act="flow-cancel">Abbrechen</button>
    <button class="btn pri" data-k="del-start" data-act="del-start" ${listed.length ? "" : 'disabled aria-disabled="true"'}>Zurückliefern starten</button>
  </div>`;
}

function reopenChoice() {
  const r = state.flow.reopen;
  return `<div class="flow-body"><section aria-labelledby="h-reopen" style="max-width:640px">
      <h2 id="h-reopen">${r.kind === "datei" ? "Letzten gespeicherten Stand öffnen" : "Mischstand des Originals übernehmen"}</h2>
      <p class="hint">${esc(r.text)}</p>
      <dl class="kv"><dt>Gespeicherte Session</dt><dd class="mono">${esc(r.source)}</dd></dl>
    </section></div>
  <div class="flow-foot">
    <button class="btn" data-k="flow-cancel" data-act="flow-cancel">Abbrechen</button>
    <button class="btn" data-k="reopen-new" data-act="reopen-new">Stattdessen neu importieren …</button>
    <button class="btn pri" data-k="reopen-start" data-act="reopen-start">${r.kind === "datei" ? "Stand als Kopie öffnen" : "Kopieren und öffnen"}</button>
  </div>`;
}

function havarieStep() {
  const f = state.flow;
  const t = state.templates;
  const i = f.info;
  const opts = !t ? "" : [...t.mine, ...t.found].map((x) => {
    const v = `${x.group}\u0001${x.name}`;
    const on = f.choice && f.choice.group === x.group && f.choice.name === x.name;
    return `<option value="${esc(v)}" ${on ? "selected" : ""}>${esc(x.label)} (${esc(x.group)})</option>`;
  }).join("");
  return `<div class="flow-body"><section aria-labelledby="h-hv" style="max-width:720px;display:flex;flex-direction:column;gap:10px">
      <h2 id="h-hv">Übergabepaket ohne Helmut öffnen</h2>
      <span class="hint">Für den Ausfall von Helmut: Das AAF (und das Referenzvideo) werden direkt aus dem Paketordner importiert. Übernahme und Zustand trägt die App in Helmut nach, sobald Helmut wieder da ist – nur wenn der Beitrag dann noch frei ist.</span>
      <label class="field"><span>Paketordner</span><input class="inp" id="hv-path" data-k="hv-path" aria-label="Paketordner" value="${esc(f.path || "")}" placeholder="/Volumes/Helmut/…/Uebergabe"></label>
      <div style="display:flex;gap:8px"><button class="btn sm" data-k="hv-choose" data-act="hv-choose">Ordner wählen …</button>
        <button class="btn sm" data-k="hv-check" data-act="hv-check">Paket prüfen</button></div>
      ${i ? `<dl class="kv"><dt>AAF</dt><dd class="mono">${esc(i.aaf)}</dd><dt>Referenzvideo</dt><dd class="mono">${esc(i.video || "–")}</dd></dl>
        ${i.hint ? `<div class="banner warn" role="note">${esc(i.hint)}</div>` : ""}
        <label class="field"><span>Titel</span><input class="inp" id="hv-title" data-k="hv-title" aria-label="Titel" value="${esc(f.title || "")}"></label>
        <label class="field"><span>Helmut-Projekt-ID (falls bekannt)</span><input class="inp" id="hv-id" data-k="hv-id" aria-label="Helmut-Projekt-ID" value="${esc(f.pid || "")}">
          <span class="hint">Leer lassen, wenn unbekannt – dann ordnest du die Session später einem Beitrag zu.</span></label>
        <label class="field"><span>Template</span><select class="inp" id="hv-tpl" data-k="hv-tpl" data-hvtpl="1" aria-label="Template">${opts}</select></label>
        <label class="chk"><input type="checkbox" data-k="opt-triage" data-opt="triage" ${f.triage ? "checked" : ""}><span>AAF-Triage</span></label>
        <label class="chk"><input type="checkbox" data-k="opt-video" data-opt="video" ${f.video ? "checked" : ""} ${i.video ? "" : "disabled"}><span>Referenzvideo importieren</span></label>` : ""}
    </section></div>
  <div class="flow-foot">
    <button class="btn" data-k="flow-cancel" data-act="flow-cancel">Abbrechen</button>
    <button class="btn pri" data-k="hv-start" data-act="hv-start" ${i && f.choice ? "" : 'disabled aria-disabled="true"'}>Import starten</button>
  </div>`;
}

function hvdelStep() {
  const f = state.flow;
  const i = f.info;
  const sel = (v, cur) => (v === cur ? "selected" : "");
  let body = "";
  if (i) {
    const pics = i.pictures.length > 1
      ? `<label class="field"><span>Bild (mehrere neben dem AAF)</span><select class="inp" data-k="hvdel-bild" data-hvdel-bild="1" aria-label="Bild für das Sendefile">
          <option value="">– wählen –</option>${i.pictures.map((p) => `<option value="${esc(p)}" ${sel(p, f.bild)}>${esc(p.split("/").pop())}</option>`).join("")}</select></label>`
      : `<dl class="kv"><dt>Bild</dt><dd class="mono">${esc(i.pictures[0] || "kein Bild neben dem AAF")}</dd></dl>`;
    const hv = i.havarie_ordner ? `<label class="chk"><input type="checkbox" data-k="hvdel-havarie" data-hvdel-havarie="1" aria-label="Havarie-Ordner als Ziel" ${f.havarie ? "checked" : ""}>
        <span>Havarie-Ordner <span class="hint mono">${esc(i.havarie_ordner)}</span></span></label>` : "";
    const desk = hv + `<label class="chk"><input type="checkbox" data-k="hvdel-desktop" data-hvdel-desktop="1" aria-label="Schreibtisch als Ziel" ${f.desktop ? "checked" : ""}>
        <span>Schreibtisch <span class="hint mono">${esc(i.schreibtisch || "")}</span></span></label>`;
    const usb = i.usb.length ? i.usb.map((u, k) => `<label class="chk"><input type="checkbox" data-k="hvdel-usb-${k}" data-hvdel-usb="${esc(u.mount)}" data-name="${esc(u.name)}" aria-label="${u.kind === "platte" ? "Externe Platte" : "USB-Stick"} ${esc(u.name)} als Ziel" ${f.usb[u.mount] ? "checked" : ""} ${u.writable ? "" : "disabled"}>
        <span>${u.kind === "platte" ? `Externe Platte „${esc(u.name)}“${u.bus ? ` (${esc(u.bus)})` : ""}` : `USB-Stick „${esc(u.name)}“`}${u.writable ? "" : " (schreibgeschützt)"}${u.fs === "msdos" ? " – FAT32: höchstens 4 GB je Datei" : ""}</span></label>`).join("") : `<span class="hint">Kein USB-Stick und keine externe Platte angeschlossen.</span>`;
    const folders = f.ordner.map((p, k) => `<li class="mono">${esc(p)} <button class="btn ghost sm" data-k="hvdel-rm-${k}" data-act="hvdel-rm" data-idx="${k}" aria-label="${esc(p)} entfernen">Entfernen</button></li>`).join("");
    body = `${pics}
      <label class="field"><span>Titel</span><input class="inp" data-k="hvdel-title" data-hvdel-field="title" value="${esc(f.title)}" aria-label="Titel"></label>
      <label class="field"><span>Sendung (für den Dateinamen)</span><input class="inp" data-k="hvdel-show" data-hvdel-field="sendung" value="${esc(f.sendung)}" aria-label="Sendung"></label>
      <label class="field"><span>Preset</span><select class="inp" data-k="hvdel-preset" data-hvdel-field="preset" aria-label="Preset">${i.presets.map((p) => `<option value="${esc(p.id)}" ${sel(p.id, f.preset)}>${esc(p.name)} – ${esc(p.profil)}</option>`).join("")}</select></label>
      <h3 style="margin:8px 0 0;font-size:15px">Kanäle im Sendefile${i.profile ? ` (Spurprofil ${esc(i.profile)})` : ""}</h3>
      <table class="settab"><thead><tr><th scope="col">Kanäle</th><th scope="col">Spur</th><th scope="col">Exportname</th><th scope="col">Inhalt</th></tr></thead>
        <tbody>${i.channels.map((c) => `<tr><td>${esc(c.kanaele)}</td><td>${esc(c.spur || "Stille")}</td><td>${esc(c.exportname)}</td><td>${esc(c.inhalt)}</td></tr>`).join("")}</tbody></table>
      <h3 style="margin:8px 0 0;font-size:15px">Ziele</h3>${desk}${usb}
      <ul class="warnlist" aria-label="Zielordner">${folders}</ul>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn sm" data-k="hvdel-usbscan" data-act="hvdel-check">USB-Sticks neu suchen</button>
        <button class="btn sm" data-k="hvdel-setfolder" data-act="hvdel-setfolder">${i.havarie_ordner ? "Havarie-Ordner ändern …" : "Havarie-Ordner festlegen …"}</button>
        <button class="btn sm" data-k="hvdel-addfolder" data-act="hvdel-addfolder">Anderer Ordner (nur jetzt) …</button></div>
      ${i.session ? `<span class="hint">Session: „${esc(i.session.name)}“ – Pro-Tools-Teil wie beim Zurückliefern (R 128, WAVs), danach Sendefile, ${i.pruefung ? "Prüfung, " : ""}Bericht und Transfer.</span>` : `<div class="banner warn" data-alert>Keine Session offen – bitte die Havarie-Session in Pro Tools öffnen.</div>`}
      ${i.missing_tools.length ? `<div class="banner warn" data-alert>Es fehlt: ${esc(i.missing_tools.join(", "))}</div>` : ""}
      ${i.bmx ? "" : `<span class="hint">bmx fehlt – das MXF entsteht dann nur mit ffmpeg (Prüfung meldet es).</span>`}`;
  }
  const ready = i && i.session && !i.missing_tools.length && (i.pictures.length === 1 || f.bild);
  return `<div class="flow-body"><section aria-labelledby="h-hvdel" style="max-width:820px;display:flex;flex-direction:column;gap:10px">
      <h2 id="h-hvdel">Havarie: Sendefile erstellen und ausliefern</h2>
      <span class="hint">Nur wenn Helmut ausfällt. Im Normalbetrieb gehen die WAVs an Helmut zurück („Zurückliefern“).</span>
      <label class="field"><span>Paketordner (AAF und Bild)</span><input class="inp" data-k="hvdel-path" data-hvdel-field="path" value="${esc(f.path || "")}" aria-label="Paketordner"></label>
      <div style="display:flex;gap:8px"><button class="btn sm" data-k="hvdel-choose" data-act="hvdel-choose">Ordner wählen …</button>
        <button class="btn sm" data-k="hvdel-check" data-act="hvdel-check">Paket prüfen</button></div>
      ${body}
    </section></div>
  <div class="flow-foot">
    <button class="btn" data-k="flow-cancel" data-act="flow-cancel">Abbrechen</button>
    <button class="btn pri" data-k="hvdel-start" data-act="hvdel-start" ${ready ? "" : 'disabled aria-disabled="true"'}>Sendefile erstellen und ausliefern</button>
  </div>`;
}

async function hvdelCheck(auto) {
  const f = state.flow;
  try {
    const r = await TM.call("havarie_ausliefern_info", { path: f.path, auto: !!auto });
    if (auto && !r.path) return render();                 // kein Paket zur offenen Session bekannt: selbst wählen
    f.info = r;
    f.path = r.path || f.path;
    // Vorauswahl (Jens 04.10.2026): Havarie-Ordner, sonst Schreibtisch; beschreibbare USB-Sticks automatisch
    if (f.desktop === undefined || f.havarie === undefined) { f.havarie = !!r.havarie_ordner; f.desktop = !r.havarie_ordner; }
    for (const u of r.usb || []) if (f.usb[u.mount] === undefined) f.usb[u.mount] = !!u.auto;
    f.title = f.title || f.info.title;
    if (f.info.pictures.length === 1) f.bild = f.info.pictures[0];
    f.preset = f.preset || f.info.preset;
    state.lastHvPath = f.path;
  } catch (e) { f.info = null; toast(e.message, true); }
  render();
}

// Rahmen jeder Ablaufseite: „← Liste“, Titel, Schrittanzeige, Inhalt (vorher fünfmal ausgeschrieben)
function flowFrame(title, steps, body) {
  return `<main class="flow" aria-labelledby="flow-title">
    <div class="flow-head">
      <button class="btn ghost sm" data-k="flow-list" data-act="flow-back">← Liste</button>
      <h1 class="ftitle" id="flow-title" style="margin:0;font-size:16px">${esc(title)}</h1>
      ${steps}
    </div>${body}</main>`;
}

function flowView() {
  const f = state.flow;
  if (f.kind === "hvdel") {
    return flowFrame("Havarie: Sendefile ausliefern",
      flowSteps(f.step === "auswahl" ? "auswahl" : "job", [["auswahl", "Paket & Ziele"], ["job", "Ausliefern"]]),
      f.step === "auswahl" ? hvdelStep() : importStep());
  }
  if (f.kind === "havarie") {
    // Triage-Vorschau auch im Havarie-Ablauf (live 04.10.2026: die Frage kam, die Ansicht fehlte – Auftrag wartete)
    const tri = f.step === "triage" && state.preview;
    return flowFrame("Paket manuell öffnen",
      flowSteps(f.step === "paket" ? "paket" : (tri ? "triage" : "job"),
        [["paket", "Paket & Template"], ["triage", "Triage"], ["job", "Import"]]),
      f.step === "paket" ? havarieStep() : (tri ? triageStep() : importStep()));
  }
  if (f.kind === "reopen") {
    return flowFrame(f.title, flowSteps(f.step === "choice" ? "choice" : "job", [["choice", "Stand"], ["job", "Öffnen"]]),
      f.step === "choice" ? reopenChoice() : importStep());
  }
  if (f.kind === "deliver") {
    return flowFrame(f.title,
      flowSteps(f.step === "overview" ? "overview" : "job", [["overview", "Übersicht"], ["job", "Zurückliefern"]]),
      f.step === "overview" ? deliverOverview() : importStep());
  }
  const body = f.step === "template" ? templateStep() : (f.step === "triage" && state.preview ? triageStep() : importStep());
  const active = f.step === "template" ? "template"
    : (f.step === "triage" || (state.job && state.job.step === "AAF-Triage") ? "triage" : "import");
  return flowFrame(f.title, flowSteps(active), body);
}

// ── Dialoge ─────────────────────────────────────────────────────────────────
// Rahmen jedes Dialogs: modal, Titel „dlg-title“, Beschreibung „dlg-text“ (vorher achtmal ausgeschrieben)
function dialogFrame(inner, { wide = false, alert = false } = {}) {
  return `<div class="overlay"><div class="dialog${wide ? " wide" : ""}" role="${alert ? "alertdialog" : "dialog"}" aria-modal="true" aria-labelledby="dlg-title" aria-describedby="dlg-text">${inner}</div></div>`;
}

function dialogView() {
  const g = state.dialog;
  if (!g) return "";
  if (g.kind === "confirm") {                     // Rückfrage vor endgültigen Aktionen; vorgewählt ist „Abbrechen“
    return dialogFrame(`
      <h2 id="dlg-title">${esc(g.title)}</h2>
      <p id="dlg-text" style="margin:0">${esc(g.text)}</p>
      <div class="buttons"><button class="btn" data-k="dlg-cancel" data-act="close" data-default-focus>Abbrechen</button>
        <button class="btn pri" data-k="dlg-ok" data-act="confirm-ok">${esc(g.ok)}</button></div>
    `, { alert: true });
  }
  if (g.kind === "login") {                       // Nutzer wählen, ohne Passwort (Jens 04.10.2026)
    const us = (state.users && state.users.users) || [];
    return dialogFrame(`
      <h2 id="dlg-title">Wer arbeitet hier?</h2>
      <span class="hint" id="dlg-text">Einstellungen und Templates hängen am Nutzer. Ohne Anmeldung laufen Aufnahme, Stream Deck und Webtrigger; Holen, Zurückliefern und Havarie brauchen einen Nutzer.</span>
      <label class="field"><span>Nutzer</span><select class="inp" data-k="login-user" data-login="user" aria-label="Nutzer">
        ${us.map((u) => `<option value="${esc(u.id)}" ${u.id === g.sel ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></label>
      <label class="chk"><input type="checkbox" data-k="login-std" data-login="std" ${g.std ? "checked" : ""}><span>Auf diesem Mac automatisch anmelden (Standard-Nutzer)</span></label>
      <div style="display:flex;gap:8px;align-items:center"><input class="inp" data-k="login-new" data-login="new" value="${esc(g.newName || "")}" placeholder="Name" aria-label="Name für einen neuen Nutzer">
        <button class="btn sm" data-k="login-create" data-act="login-create">Neuer Nutzer</button></div>
      ${g.message ? `<span class="hint" role="status">${esc(g.message)}</span>` : ""}
      <div class="buttons"><button class="btn" data-k="dlg-cancel" data-act="close">Später</button>
        <button class="btn pri" data-k="dlg-ok" data-act="login-ok" ${g.sel ? "" : 'disabled aria-disabled="true"'}>Anmelden</button></div>
    `);
  }
  if (g.kind === "release") {
    const i = g.info || { keep: [], keep_hint: "", warn: "", notify: [] };
    const radio = (on, k, data, label) => `<button class="opt ${on ? "on" : ""}" role="radio" aria-checked="${on}" data-k="${k}" ${data}><span class="radio" aria-hidden="true"></span>${esc(label)}</button>`;
    return dialogFrame(`
      <h2 id="dlg-title">Übernahme aufheben</h2>
      <span class="hint" id="dlg-text">Der Beitrag wird in Helmut freigegeben und entsperrt. Er steht danach wieder unter „Zur Mischung“; die Session bleibt liegen.</span>
      ${i.warn ? `<div class="banner warn" role="note">${esc(i.warn)}</div>` : ""}
      <h3 id="rel-grund">Grund</h3>
      <div role="radiogroup" aria-labelledby="rel-grund" style="display:flex;flex-direction:column;gap:8px">
      ${g.reasons.map((r, n) => radio(g.reason === r, `reason-${n}`, `data-reason="${esc(r)}"`, r)).join("")}
      </div>
      <h3 id="rel-keep">Beim nächsten Öffnen</h3>
      <div role="radiogroup" aria-labelledby="rel-keep" style="display:flex;flex-direction:column;gap:8px">
      ${i.keep.map((k) => radio(g.keep === k.value, `keep-${k.value}`, `data-keep="${esc(k.value)}"`, k.label)).join("")}
      </div>
      <span class="hint">${esc(i.keep_hint)}</span>
      ${i.notify.length ? `<label class="chk"><input type="checkbox" data-k="rel-notify" data-relnotify="1" ${g.notify ? "checked" : ""}>
        <span>Benachrichtigen: ${esc(i.notify.join(", "))}</span></label>` : ""}
      <div class="buttons"><button class="btn" data-k="dlg-cancel" data-act="close">Abbrechen</button>
        <button class="btn pri" data-k="dlg-ok" data-act="release-ok" ${g.reason ? "" : 'disabled aria-disabled="true"'}>Freigeben</button></div>
    `);
  }
  if (g.kind === "hvassign") {
    const rows = (state.list && state.list.rows) || [];
    return dialogFrame(`
      <h2 id="dlg-title">Havarie-Session zuordnen</h2>
      <span class="hint" id="dlg-text">„${esc(g.title)}“ wurde ohne Helmut geöffnet. Zu welchem Beitrag gehört sie? Danach trägt die App Übernahme und Zustand nach – nur wenn der Beitrag noch frei ist.</span>
      <label class="field"><span>Beitrag</span><select class="inp" id="hv-target" data-k="hv-target" data-hvtarget="1" aria-label="Beitrag">
        <option value="">– wählen –</option>${rows.map((r) => `<option value="${esc(r.id)}" ${g.target === r.id ? "selected" : ""}>${esc(r.title)} · ${esc(r.state)}</option>`).join("")}</select></label>
      <div class="buttons"><button class="btn" data-k="dlg-cancel" data-act="close">Abbrechen</button>
        <button class="btn pri" data-k="dlg-ok" data-act="hv-assign-ok" ${g.target ? "" : 'disabled aria-disabled="true"'}>Zuordnen und nachtragen</button></div>
    `);
  }
  if (g.kind === "archive") {
    const a = g.data;
    const fRows = !a ? "" : (a.foreign.length ? a.foreign.map((f) => `<tr><td>${esc(f.name)}</td><td>${esc(f.state)}</td><td class="mono" style="overflow-wrap:anywhere">${esc(f.path)}</td></tr>`).join("")
      : `<tr><td colspan="3">Alle Audiodateien liegen im Projekt.</td></tr>`);
    const aRows = !a ? "" : (a.archive.length ? a.archive.map((f, i) => `<tr><td><input type="checkbox" data-k="arch-${i}" data-arch="${i}" aria-label="${esc(f.name)} auswählen" ${g.sel[i] ? "checked" : ""}></td>
        <td>${esc(f.name)}</td><td class="mono" style="overflow-wrap:anywhere">${esc(f.folder)}</td><td class="mono">${esc(f.time)}</td></tr>`).join("")
      : `<tr><td colspan="4">Keine neuen Audiodateien in den Archiv-Ordnern.</td></tr>`);
    const n = Object.values(g.sel).filter(Boolean).length;
    return dialogFrame(`
      <h2 id="dlg-title">Archiv und fremde Dateien</h2>
      <span class="hint" id="dlg-text">${!a ? "Lade …" : (a.session ? `Offene Session: ${esc(a.session)}` : "In Pro Tools ist keine Session offen.")}</span>
      <h3>Dateien außerhalb des Projekts</h3>
      <table class="clips"><thead><tr><th>Datei</th><th>Zustand</th><th>Ort</th></tr></thead><tbody>${fRows}</tbody></table>
      <span class="hint">Übernommene Dateien liegen als Kopie in „Audio Files/Extern“. Die Session verweist weiter auf den Originalpfad; an einem anderen Platz findet Pro Tools die Kopie über „Automatisch suchen und neu verknüpfen“.</span>
      <h3>Neu in den Archiv-Ordnern${a ? ` (letzte ${a.days} Tage)` : ""}</h3>
      <table class="clips"><thead><tr><th><span class="sr-only">Auswahl</span></th><th>Datei</th><th>Ordner</th><th>Geändert</th></tr></thead><tbody>${aRows}</tbody></table>
      <span class="hint">Import mit Kopie in den Session-Ordner – eine spätere Übernahme ist dann nicht nötig.</span>
      <div class="buttons"><button class="btn" data-k="dlg-cancel" data-act="close">Schließen</button>
        <button class="btn" data-k="arch-clips" data-act="arch-import" data-ziel="clipliste" ${n && a && a.session ? "" : 'disabled aria-disabled="true"'}>In die Clip-Liste</button>
        <button class="btn pri" data-k="arch-insert" data-act="arch-import" data-ziel="einfuegemarke" ${n && a && a.session ? "" : 'disabled aria-disabled="true"'}>An der Einfügemarke importieren${n ? ` (${n})` : ""}</button></div>
    `, { wide: true });
  }
  if (g.kind === "backups") {
    const b = g.data;
    const rows = !b ? `<tr><td colspan="5" role="status">Lade Stände …</td></tr>` : b.rows.map((r, i) => `<tr>
        <td class="mono">${esc(r.time)}</td><td>${esc(r.kind)}</td><td class="mono" style="overflow-wrap:anywhere">${esc(r.name)}</td><td class="mono">${esc(r.size)}</td>
        <td style="white-space:nowrap"><button class="btn sm" data-k="bak-open-${i}" data-bakopen="${esc(r.path)}" ${b.can_open ? "" : 'disabled aria-disabled="true"'}>${r.current ? "Öffnen" : "Diesen Stand öffnen"}</button>
          <button class="btn ghost sm" data-k="bak-reveal-${i}" data-bakreveal="${esc(r.path)}" aria-label="${esc(r.name)} im Finder zeigen">Im Finder zeigen</button></td></tr>`).join("");
    return dialogFrame(`
      <h2 id="dlg-title">Sicherungen · „${esc(g.title)}“</h2>
      <span class="hint" id="dlg-text">${b ? esc(b.availability) : ""}</span>
      <table class="clips"><thead><tr><th>Zeit</th><th>Art</th><th>Datei</th><th>Größe</th><th><span class="sr-only">Aktionen</span></th></tr></thead><tbody>${rows}</tbody></table>
      <span class="hint">${b ? esc(b.hint) : ""}</span>
      <div class="buttons"><button class="btn" data-k="bak-folder" data-act="bak-folder" ${b && b.reachable ? "" : 'disabled aria-disabled="true"'}>Ordner im Finder öffnen</button>
        <button class="btn pri" data-k="dlg-cancel" data-act="close">Schließen</button></div>
    `, { wide: true });
  }
  if (g.kind === "dup") {
    const i = g.info;
    return dialogFrame(`
      <h2 id="dlg-title">Duplikat anlegen</h2>
      <span class="hint" id="dlg-text">Neuer Beitrag in Helmut mit derselben Übergabe (AAF, Referenzvideo). Das Original bleibt unverändert.</span>
      <h3 id="dup-zweck">Zweck</h3>
      <div role="radiogroup" aria-labelledby="dup-zweck" style="display:flex;flex-wrap:wrap;gap:8px">
      ${i.purposes.map((pp, n) => `<button class="opt ${g.purpose === pp ? "on" : ""}" role="radio" aria-checked="${g.purpose === pp}" data-k="dup-p${n}" data-purpose="${esc(pp)}"><span class="radio" aria-hidden="true"></span>${esc(pp)}</button>`).join("")}
      </div>
      <h3><label for="dup-title">Titel</label></h3>
      <input class="inp" id="dup-title" data-k="dup-title" value="${esc(g.title)}">
      ${i.can_keep ? `<label class="chk"><input type="checkbox" data-k="dup-keep" data-dupopt="keep" ${g.keep ? "checked" : ""}>
        <span>Mischstand übernehmen <span class="hint" style="display:block">Beim Übernehmen startet das Duplikat mit einer Kopie der zuletzt gespeicherten Session (samt Audiodateien) statt mit einem neuen Import.</span></span></label>` : ""}
      <label class="chk"><input type="checkbox" data-k="dup-take" data-dupopt="take" ${g.take ? "checked" : ""}><span>Sofort übernehmen</span></label>
      <div class="buttons"><button class="btn" data-k="dlg-cancel" data-act="close">Abbrechen</button>
        <button class="btn pri" data-k="dlg-ok" data-act="dup-ok" ${g.title.trim() ? "" : 'disabled aria-disabled="true"'}>Duplikat anlegen</button></div>
    `);
  }
  if (g.kind === "question") {
    const q = g.question;
    if (q.kind === "fehlende_spuren") {
      const opts = (t) => [["ab A11", "ab A11 (nächste freie Spur)"], ["weglassen", "weglassen"]]
        .concat(q.available.map((a) => [a, "auf " + a])).map(([v, l]) =>
          `<option value="${esc(v)}" ${g.answers[t] === v ? "selected" : ""}>${esc(l)}</option>`).join("");
      return dialogFrame(`
        <h2 id="dlg-title">${esc(q.title)}</h2><span class="hint" id="dlg-text">${esc(q.text)}</span>
        ${q.missing.map((t, i) => `<div class="qrow"><label for="miss-${i}">${esc(t)}</label>
          <select class="inp" id="miss-${i}" data-k="miss-${i}" data-miss="${esc(t)}">${opts(t)}</select></div>`).join("")}
        <div class="buttons"><button class="btn" data-k="dlg-cancel" data-answer="cancel">Abbrechen</button>
          <button class="btn pri" data-k="dlg-ok" data-answer="missing">Import fortsetzen</button></div>
      `, { wide: true, alert: true });
    }
    return dialogFrame(`
      <h2 id="dlg-title">${esc(q.title)}</h2>
      <p id="dlg-text" style="margin:0">${esc(q.text)}</p>
      <div class="buttons">${(q.options || []).map((o, i) => `<button class="btn ${o.primary ? "pri" : ""}" data-k="dlg-o${i}" data-answer="${i}"${o.primary ? " data-default-focus" : ""}>${esc(o.label)}</button>`).join("")}</div>
    `, { alert: true });
  }
  return "";
}

// ── Einstellungen (M6b) ─────────────────────────────────────────────────────
const fid = (path) => "f-" + path.replace(/[^a-zA-Z0-9_-]/g, "-");

function fieldInput(f) {
  const id = fid(f.path);
  const dis = f.locked ? 'disabled aria-disabled="true"' : "";
  const desc = `aria-describedby="${id}-meta${f.errors.length ? " " + id + "-err" : ""}"`;
  const inv = f.errors.length ? 'aria-invalid="true"' : "";
  if (f.kind === "bool") {
    return `<label class="chk"><input type="checkbox" id="${id}" data-k="${id}" data-set="${esc(f.path)}" data-kind="bool" ${f.value ? "checked" : ""} ${dis} ${desc}><span>${esc(f.label)}</span></label>`;
  }
  const label = `<label class="lbl" for="${id}">${esc(f.label)}</label>`;
  if (f.kind === "enum") {
    const labels = f.option_labels || f.options;
    return label + `<select class="inp" id="${id}" data-k="${id}" data-set="${esc(f.path)}" data-kind="enum" ${dis} ${desc} ${inv}>
      ${f.options.map((o, i) => `<option value="${esc(o)}" ${o === f.value ? "selected" : ""}>${esc(labels[i] === "" ? "–" : labels[i])}</option>`).join("")}</select>`;
  }
  if (f.kind === "number") {
    return label + `<input class="inp" type="number" id="${id}" data-k="${id}" data-set="${esc(f.path)}" data-kind="number" value="${esc(f.value)}"
      ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""} step="${f.step || (f.integer ? 1 : "any")}" ${dis} ${desc} ${inv}>`;
  }
  if (f.kind === "text") {
    return label + `<input class="inp" type="text" id="${id}" data-k="${id}" data-set="${esc(f.path)}" data-kind="text" value="${esc(f.value)}" ${dis} ${desc} ${inv}>`;
  }
  if (f.kind === "user") {                         // Auswahl aus den angelegten Nutzern
    const us = (state.users && state.users.users) || [];
    return label + `<select class="inp" id="${id}" data-k="${id}" data-set="${esc(f.path)}" data-kind="text" ${dis} ${desc} ${inv}>
      <option value="" ${f.value ? "" : "selected"}>– keiner: beim Start fragen –</option>
      ${us.map((u) => `<option value="${esc(u.id)}" ${u.id === f.value ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select>`;
  }
  if (f.kind === "folder") {                       // Ordner wählen statt tippen
    return label + `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span class="mono" id="${id}" ${desc}>${esc(f.value || "– nicht festgelegt –")}</span>
      <button class="btn sm" data-k="${id}-pick" data-act="set-folder" data-path="${esc(f.path)}" ${dis}>Wählen …</button>
      ${f.value ? `<button class="btn ghost sm" data-k="${id}-clear" data-act="clear-folder" data-path="${esc(f.path)}" ${dis}>Leeren</button>` : ""}</div>`;
  }
  if (f.kind === "list") {
    return label + `<textarea class="inp" id="${id}" data-k="${id}" data-set="${esc(f.path)}" data-kind="list" rows="${Math.max(3, (f.value || []).length + 1)}" ${dis} ${desc} ${inv}>${esc((f.value || []).join("\n"))}</textarea>`;
  }
  if (f.kind === "table") {
    const rows = f.value || [];
    const cell = (r, c, i) => {
      const k = `${id}-${i}-${c.key}`;
      const lab = `aria-label="${esc(c.label)}, Zeile ${i + 1}"`;
      const common = `data-k="${k}" data-cell="${esc(f.path)}" data-row="${i}" data-col="${esc(c.key)}" data-ckind="${c.kind}" ${dis} ${lab}`;
      const v = r[c.key];
      if (c.kind === "bool") return `<td class="c-bool"><input type="checkbox" ${common} ${v ? "checked" : ""}></td>`;
      if (c.kind === "enum") return `<td><select class="inp" ${common}>${[""].concat(c.options).map((o) => `<option value="${esc(o)}" ${o === v ? "selected" : ""}>${esc(o || "–")}</option>`).join("")}</select></td>`;
      if (c.kind === "number") return `<td><input class="inp" type="number" ${common} value="${esc(v == null ? "" : v)}"></td>`;
      if (c.kind === "text") return `<td><input class="inp" type="text" ${common} value="${esc(v == null ? "" : v)}"></td>`;
      return `<td><input class="inp" type="text" ${common} data-ckind="json" value="${esc(v == null ? "" : JSON.stringify(v))}"></td>`;
    };
    return `<span class="lbl" id="${id}-lbl">${esc(f.label)}</span>
      <table class="settab" aria-labelledby="${id}-lbl" ${desc}><thead><tr>${f.columns.map((c) => `<th scope="col">${esc(c.label)}${c.required ? " *" : ""}</th>`).join("")}<th><span class="sr-only">Zeile</span></th></tr></thead>
      <tbody>${rows.map((r, i) => `<tr>${f.columns.map((c) => cell(r, c, i)).join("")}<td><button class="btn ghost sm" data-k="${id}-del-${i}" data-del-row="${esc(f.path)}" data-row="${i}" ${dis} aria-label="Zeile ${i + 1} entfernen">Entfernen</button></td></tr>`).join("")}</tbody></table>
      <div><button class="btn sm" data-k="${id}-add" data-add-row="${esc(f.path)}" ${dis}>Zeile hinzufügen</button></div>`;
  }
  return label + `<textarea class="inp" id="${id}" data-k="${id}" data-set="${esc(f.path)}" data-kind="json" rows="4" ${dis} ${desc} ${inv}>${esc(JSON.stringify(f.value, null, 1))}</textarea>`;
}

function fieldRow(f) {
  const id = fid(f.path);
  return `<div class="fieldrow">${fieldInput(f)}
    <div class="meta" id="${id}-meta"><span class="${f.locked ? "locked-note" : ""}">Herkunft: ${esc(f.layer_text)}</span>
      ${f.hints.map((h) => `<span>${esc(h)}</span>`).join("")}
      ${f.resettable ? `<button class="btn ghost sm" data-k="${id}-reset" data-reset="${esc(f.path)}">Zurücksetzen</button>` : ""}</div>
    ${f.errors.length ? `<div class="err" id="${id}-err" data-alert>${f.errors.map(esc).join("<br>")}</div>` : ""}
  </div>`;
}

// Spurraster und Presets (PunchBuddy-Übernahme): eigene Blöcke über bzw. statt der Listenfelder
function gridBlock() {
  const g = state.settings.grid;
  if (!g) return `<div role="status">Spuren werden gelesen …</div>`;
  const head = g.columns.map((c) => `<th scope="col">${esc(c.label)}</th>`).join("");
  const rows = g.rows.map((r, i) => `<tr><th scope="row">${esc(r.spur)}${r.in_session || !g.verbunden ? "" : ' <span class="hint">(nicht in der Session)</span>'}</th>${g.columns.map((c) => {
    const v = r[c.key];
    if (v === null) return `<td class="c-bool"><span aria-hidden="true">–</span><span class="sr-only">${esc(c.label)} für ${esc(r.spur)}: nicht in der Spurtabelle</span></td>`;
    return `<td class="c-bool"><input type="checkbox" data-k="grid-${i}-${esc(c.key)}" data-grid-col="${esc(c.key)}" data-grid-track="${esc(r.spur)}" ${v ? "checked" : ""} aria-label="${esc(c.label)} für ${esc(r.spur)}"></td>`;
  }).join("")}</tr>`).join("");
  const quelle = g.meldung || (g.verbunden ? "Spuren aus Pro Tools" : "Pro Tools nicht verbunden – Spuren aus den Einstellungen");
  return `<fieldset><legend>Spurraster</legend>
    <span class="hint">${esc(quelle)}. Export und Lautheit sind die Haken der Spurtabelle (Export → Spuren &amp; Lautheit); „–“ heißt: Spur dort keinem Kanalpaar zugeordnet.</span>
    <div><button class="btn sm" data-k="grid-read" data-grid-read="1">Spuren aus Pro Tools lesen</button></div>
    <table class="settab grid" aria-label="Spurraster"><thead><tr><th scope="col">Spur</th>${head}</tr></thead><tbody>${rows}</tbody></table></fieldset>`;
}

// Audio verschieben wie in PunchBuddy (Jens 09.10.2026): je Zeile ein Spurpaar Quelle → Ziel aus den Spuren der
// Session, „−“ entfernt das Paar, „+ Spurpaar“ fügt eins an. Gespeichert werden nur vollständige Paare; eine halb
// gewählte Zeile bleibt im Fenster stehen, bis auch die andere Seite gewählt ist.
function setMove(view, keepPairs) {
  const pairs = keepPairs && state.settings.move ? state.settings.move.paare : view.paare.map((p) => [...p]);
  state.settings.move = { ...view, paare: pairs.length ? pairs : [["", ""]] };
}

function moveBlock() {
  const m = state.settings.move;
  if (!m) return `<div role="status">Spuren werden gelesen …</div>`;
  const dis = m.gesperrt ? " disabled" : "";
  const options = (cur) => ["", ...m.spuren].map((name) => `<option value="${esc(name)}" ${name === cur ? "selected" : ""}>${name ? esc(name) + (m.fehlen.includes(name) ? " (nicht in der Session)" : "") : "– Spur wählen –"}</option>`).join("");
  const rows = m.paare.map((p, i) => `<tr>
    <td><select class="inp" data-k="mv-${i}-0" data-mv-row="${i}" data-mv-side="0" aria-label="Quelle, Paar ${i + 1}"${dis}>${options(p[0])}</select></td>
    <td aria-hidden="true" style="text-align:center">→</td>
    <td><select class="inp" data-k="mv-${i}-1" data-mv-row="${i}" data-mv-side="1" aria-label="Ziel, Paar ${i + 1}"${dis}>${options(p[1])}</select></td>
    <td class="acts"><button class="btn ghost sm" data-k="mv-del-${i}" data-mv-del="${i}" aria-label="Paar ${i + 1} entfernen"${dis}>−</button></td></tr>`).join("");
  const quelle = m.meldung || (m.verbunden ? "Spuren aus Pro Tools" : "Pro Tools nicht verbunden – nur die gespeicherten Spuren");
  return `<fieldset><legend>Spurpaare</legend>
    <span class="hint">Verschiebt das gesamte Material der Quell- auf die Zielspuren (gleiche Zeitposition), Paar für Paar. Menüleiste, Stream Deck „Audio verschieben“, Webtrigger /move_audio. Gespeichert werden nur vollständige Paare.</span>
    <span class="hint">${esc(quelle)}${m.gesperrt ? " · von der Geräteverwaltung festgelegt, nicht änderbar" : ""}</span>
    <div><button class="btn sm" data-k="mv-read" data-mv-read="1">Spuren aus Pro Tools lesen</button></div>
    <table class="settab" aria-label="Spurpaare für Audio verschieben"><thead><tr><th scope="col">Quelle</th><th scope="col"><span class="sr-only">nach</span></th><th scope="col">Ziel</th><th scope="col"><span class="sr-only">Entfernen</span></th></tr></thead>
    <tbody>${rows}</tbody></table>
    <div><button class="btn sm" data-k="mv-add" data-mv-add="1"${dis}>+ Spurpaar</button></div></fieldset>`;
}

async function saveMove(say) {
  try {
    const view = await TM.call("audio_verschieben_setzen", { paare: state.settings.move.paare });
    setMove(view, true);
    if (say) Live.say(say);
  } catch (e) { toast(e.message, true); }
  render();
}

function presetsBlock() {
  const p = state.settings.presets;
  if (!p) return `<div role="status">Presets werden geladen …</div>`;
  const rows = p.map((x) => `<tr><td>${x.nr}</td>
    <td><input class="inp" type="text" data-k="preset-name-${x.nr}" data-preset-name="${x.nr}" value="${esc(x.name)}" aria-label="Name von Preset ${x.nr}"></td>
    <td>${esc(x.inhalt)}</td>
    <td class="acts"><button class="btn sm" data-k="preset-load-${x.nr}" data-preset-act="laden" data-nr="${x.nr}" aria-label="Preset ${x.nr} laden">Laden</button>
      <button class="btn ghost sm" data-k="preset-save-${x.nr}" data-preset-act="ueberschreiben" data-nr="${x.nr}" aria-label="Aktuellen Stand in Preset ${x.nr} speichern">Aktuellen Stand speichern</button>
      <button class="btn ghost sm" data-k="preset-del-${x.nr}" data-preset-act="loeschen" data-nr="${x.nr}" aria-label="Preset ${x.nr} löschen">Löschen</button></td></tr>`).join("");
  return `<fieldset><legend>Spur-Presets</legend>
    <span class="hint">Die Nummer ist die Position: Befehl „preset 1“, Webtrigger /preset/1 usw. Ein Preset merkt sich Rec A/B, Play Input sowie Export und Lautheit.</span>
    <table class="settab"><thead><tr><th scope="col">Nr.</th><th scope="col">Name</th><th scope="col">Inhalt</th><th scope="col"><span class="sr-only">Aktionen</span></th></tr></thead>
    <tbody>${rows || '<tr><td colspan="4">Noch keine Presets.</td></tr>'}</tbody></table>
    <div class="fieldrow"><label class="lbl" for="preset-new">Neues Preset aus dem aktuellen Stand</label>
      <div><input class="inp" id="preset-new" data-k="preset-new" type="text" value="${esc(state.settings.newPreset || "")}" placeholder="Name">
      <button class="btn sm" data-k="preset-add" data-preset-act="neu">Anlegen</button></div></div></fieldset>`;
}

function webtriggerBlock() {
  const w = state.settings.wt;
  if (!w) return `<div role="status">Adressen werden erstellt …</div>`;
  const roomSel = w.rooms.length ? `<div class="fieldrow"><label class="lbl" for="wt-room">Platz</label>
    <select class="inp" id="wt-room" data-k="wt-room" data-wt-room="1">${w.rooms.map((r) => `<option value="${esc(r)}" ${r === w.room ? "selected" : ""}>${esc(r)}</option>`).join("")}</select></div>` : "";
  const idRow = w.rooms.length ? `<div class="fieldrow"><span class="lbl">ID-Code für ${esc(w.room)}: ${w.id_code ? `<code>${esc(w.id_code)}</code>` : "keiner"}</span>
    <div><button class="btn sm" data-k="wt-id-new" data-wt-id="neu">ID-Code erzeugen</button>
    ${w.id_code ? '<button class="btn ghost sm" data-k="wt-id-del" data-wt-id="weg">ID-Code entfernen</button>' : ""}</div></div>` : "";
  const rows = w.rows.map((r, i) => `<tr><td>${esc(r.label)}</td><td><code>${esc(r.url)}</code></td>
    <td><button class="btn ghost sm" data-k="wt-copy-${i}" data-copy="${esc(r.url)}" aria-label="Adresse ${esc(r.label)} kopieren">Kopieren</button></td></tr>`).join("");
  return `<fieldset><legend>Adressen zum Kopieren</legend>
    <span class="hint">Für Stream Deck, Bitfocus Companion oder Skripte. Mit ID-Code hängt jede Adresse ?id=… an; die App führt den Befehl nur aus, wenn der Code zum Platz passt. ${esc(w.count_text)}.</span>
    ${roomSel}${idRow}
    <div><button class="btn sm" data-k="wt-copy-all" data-copy-all="1">Alle kopieren</button>
      <button class="btn ghost sm" data-k="wt-launcher" data-act="launcher">Launcher für das Stream Deck erzeugen</button></div>
    <span class="hint">Launcher sind kleine Apps je Befehl (im Stream Deck: System → Öffnen). Sie brauchen kein Netzwerk und laufen auch auf gesperrten Rechnern.</span>
    <table class="settab"><thead><tr><th scope="col">Aufruf</th><th scope="col">Adresse</th><th scope="col"><span class="sr-only">Kopieren</span></th></tr></thead><tbody>${rows}</tbody></table></fieldset>`;
}

function vocasterBlock() {
  const v = state.settings.voc;
  if (!v) return `<div role="status">Vocaster wird abgefragt …</div>`;
  const dev = v.geraet ? `${esc(v.geraet)} (${v.kanaele} ${v.kanaele === 1 ? "Eingang" : "Eingänge"})` : "kein Vocaster angeschlossen (Erkennung beim Start der App)";
  const routing = v.routing ? `gesichert am ${esc(v.routing.saved_at)} (${esc(v.routing.model)})` : "noch nicht gesichert";
  return `<fieldset><legend>Gerät</legend>
    <dl class="kv"><dt>Gerät</dt><dd>${dev}</dd><dt>Routing</dt><dd>${routing}</dd></dl>
    <div><button class="btn sm" data-k="voc-save" data-act="voc-save" ${v.geraet ? "" : "disabled"}>Aktuelles Routing sichern</button></div>
    <span class="hint">Der Zugriff über USB beendet „Vocaster Hub“ und kann den Audio-Ausgang kurz unterbrechen. Befehle für Stream Deck und Webtrigger: vocaster_autogain_host, vocaster_autogain_guest, vocaster_phantom_on, vocaster_phantom_off.</span></fieldset>`;
}

// Spurprofile je Template (Plan Spurprofile): Spuren aus Pro Tools lesen, Häkchen je Spur
const CHANNELS = [0, 1, 3, 5, 7, 9, 11, 13, 15];
function profileBlock() {
  const p = state.settings.prof;
  if (!p) return `<div role="status">Spurprofile werden geladen …</div>`;
  const tplOpts = p.templates.map((t) => `<option value="${esc(t.group + "\u0001" + t.name)}" ${t.group === p.chosen.group && t.name === p.chosen.name ? "selected" : ""}>${esc(t.group + " / " + t.name)}${t.has_profile ? "" : " – noch kein Profil"}</option>`).join("");
  const sel = p.selected && p.selected.group === p.chosen.group && p.selected.name === p.chosen.name ? p.selected : null;
  const session = p.session ? `Offene Session: „${esc(p.session)}“` : "Keine Session offen – bitte eine Session aus dem Template öffnen";
  const head = `<div class="fieldrow"><label class="lbl" for="prof-tpl">Template</label>
      <select class="inp" id="prof-tpl" data-k="prof-tpl" data-prof-tpl="1">${p.chosen.name ? "" : '<option value="">– Template wählen –</option>'}${tplOpts}</select></div>
    <div><button class="btn sm" data-k="prof-read" data-prof-act="lesen" ${p.chosen.name ? "" : "disabled"}>Spuren aus der offenen Session lesen</button>
      <span class="hint">${session}</span></div>
    ${p.message ? `<div role="status" class="hint">${esc(p.message)}</div>` : ""}
    <div class="buttons-row"><button class="btn ghost sm" data-k="prof-exp" data-prof-act="exportieren" ${p.selected ? "" : "disabled"}>Profil exportieren …</button>
      <button class="btn ghost sm" data-k="prof-exp-all" data-prof-act="alle_exportieren" ${p.templates.some((t) => t.has_profile) ? "" : "disabled"}>Alle Profile exportieren …</button>
      <button class="btn ghost sm" data-k="prof-imp" data-prof-act="importieren">Profile importieren …</button></div>
    ${profileImportBox()}`;
  if (!sel) {
    return `<fieldset><legend>Spurprofil</legend>${head}
      <span class="hint">Für dieses Template gibt es noch kein Profil. Eine Session aus dem Template öffnen und „Spuren aus der offenen Session lesen“ wählen – die Häkchen werden nach den bisherigen Einstellungen vorbelegt. Ohne Profil gelten Zielspuren, Spurtabelle und Aufnahmespuren wie bisher.</span></fieldset>`;
  }
  const groups = [];
  for (const c of p.columns) { const g = groups[groups.length - 1]; if (g && g.name === c.group) g.n++; else groups.push({ name: c.group, n: 1 }); }
  const cell = (r, c) => {
    const k = `prof-${r.name}-${c.key}`;
    const lab = `aria-label="${esc(c.label)} für ${esc(r.name)}"`;
    const common = `data-k="${esc(k)}" data-prof-cell="${esc(c.key)}" data-prof-track="${esc(r.name)}" ${r.audio ? "" : 'disabled aria-disabled="true"'} ${lab}`;
    if (c.kind === "channel") return `<td><select class="inp sm" ${common}>${CHANNELS.map((n) => `<option value="${n}" ${n === r.channel ? "selected" : ""}>${n ? `${n}/${n + 1}` : "–"}</option>`).join("")}</select></td>`;
    if (c.kind === "text") return `<td><input class="inp sm" type="text" ${common} value="${esc(r.content || "")}"></td>`;
    if (c.kind === "exportname") return `<td><select class="inp sm" ${common}>${[""].concat(p.export_names || []).concat(r.export_name && !(p.export_names || []).includes(r.export_name) ? [r.export_name] : []).map((n) => `<option value="${esc(n)}" ${n === (r.export_name || "") ? "selected" : ""}>${esc(n || "–")}</option>`).join("")}</select></td>`;
    return `<td class="c-bool"><input type="checkbox" ${common} ${r[c.key] ? "checked" : ""}></td>`;
  };
  const rows = sel.rows.map((r) => `<tr class="${r.missing ? "missing" : ""}"><th scope="row">${esc(r.name)}
      <span class="hint">${esc([r.kind, r.format, r.folder ? "in " + r.folder : ""].filter(Boolean).join(" · "))}${r.missing ? " · fehlt in der Session" : ""}</span>
      ${r.missing ? `<button class="btn ghost sm" data-k="prof-del-${esc(r.name)}" data-prof-del="${esc(r.name)}" aria-label="${esc(r.name)} aus dem Profil entfernen">Entfernen</button>` : ""}</th>
      ${p.columns.map((c) => cell(r, c)).join("")}</tr>`).join("");
  const sw = sel.switches;
  const L = p.switch_labels;
  const switches = `<label class="chk"><input type="checkbox" data-k="prof-sw-clips" data-prof-switch="show_clips" ${sw.show_clips ? "checked" : ""}><span>${esc(L.show_clips)}</span></label>
    <label class="chk"><input type="checkbox" data-k="prof-sw-hide" data-prof-switch="hide_empty" ${sw.hide_empty ? "checked" : ""}><span>${esc(L.hide_empty)}</span></label>
    <div class="fieldrow"><label class="lbl" for="prof-full">${esc(L.when_full)}</label>
      <select class="inp" id="prof-full" data-k="prof-full" data-prof-switch="when_full">${Object.entries(p.when_full_labels).map(([v, l]) => `<option value="${esc(v)}" ${v === sw.when_full ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>
    <div class="fieldrow"><label class="lbl" for="prof-preset">${esc(L.track_data_preset)}</label>
      <select class="inp" id="prof-preset" data-k="prof-preset" data-prof-switch="track_data_preset">${[""].concat(p.track_data_presets || []).concat(sw.track_data_preset && !(p.track_data_presets || []).includes(sw.track_data_preset) ? [sw.track_data_preset] : []).map((n) => `<option value="${esc(n)}" ${n === (sw.track_data_preset || "") ? "selected" : ""}>${esc(n || "– wie Einstellung Import-Vorschau –")}</option>`).join("")}</select></div>`;
  const info = sel.read_from ? `Gelesen aus „${esc(sel.read_from)}“${sel.read_at ? ", " + esc(sel.read_at) : ""}.` : "";
  const confirm = state.settings.profConfirm
    ? `<button class="btn sm" data-k="prof-del-yes" data-prof-act="entfernen">Profil wirklich entfernen</button> <button class="btn ghost sm" data-k="prof-del-no" data-prof-act="behalten">Behalten</button>`
    : `<button class="btn ghost sm" data-k="prof-del" data-prof-act="fragen">Profil entfernen</button>`;
  return `<fieldset><legend>Spurprofil ${esc(sel.group + " / " + sel.name)}</legend>${head}
    <span class="hint">${info} Je Spur höchstens ein Triage-Ziel. „Ausblenden, wenn leer“ blendet die Spur nach dem Import aus, wenn sie keine Clips hat; Export- und Aufnahmespuren bleiben immer sichtbar. Exportname = Name der exportierten Datei; die Wahl setzt Export, Kanäle und Inhalt nach Haus-Standard. Kanäle = Platz im Sendefile (1/2 Sendeton …); „–“ = nach der Reihenfolge.</span>
    ${sel.problems.length ? `<div class="err" data-alert>${sel.problems.map(esc).join("<br>")}</div>` : ""}
    <div class="tablewrap"><table class="settab grid prof" aria-label="Spurprofil ${esc(sel.name)}"><thead>
      <tr><td></td>${groups.map((g) => `<th scope="colgroup" colspan="${g.n}">${esc(g.name)}</th>`).join("")}</tr>
      <tr><th scope="col">Spur</th>${p.columns.map((c) => `<th scope="col">${esc(c.label)}</th>`).join("")}</tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${switches}
    <div>${confirm}</div></fieldset>`;
}

function profileImportBox() {
  const im = state.settings.profImport;
  if (!im || !im.items.length) return "";
  const label = { neu: "neu", ersetzt: "ersetzt das vorhandene Profil", gleich: "unverändert – schon so vorhanden" };
  return `<div class="banner info prof-imp" role="group" aria-labelledby="prof-imp-title"><strong id="prof-imp-title">Aus „${esc(im.path.split("/").pop())}“ importieren</strong>
    ${im.items.map((it, i) => `<label class="chk"><input type="checkbox" data-k="prof-imp-${i}" data-prof-imp="${esc(it.key)}" ${im.sel[it.key] ? "checked" : ""}>
      <span>${esc(it.group + " / " + it.name)} – ${it.tracks} Spuren, ${esc(label[it.status] || it.status)}</span></label>`).join("")}
    ${im.new_names.length ? `<span class="hint">Exportnamen, die ergänzt werden: ${esc(im.new_names.join(", "))}</span>` : ""}
    <div><button class="btn sm" data-k="prof-imp-ok" data-prof-act="import_ok">Importieren</button>
      <button class="btn ghost sm" data-k="prof-imp-no" data-prof-act="import_ab">Abbrechen</button></div></div>`;
}

async function profileCall(cmd, args) {
  const p = state.settings.prof;
  try {
    state.settings.prof = await TM.call(cmd, { gruppe: p.chosen.group, name: p.chosen.name, ...args });
    if (state.settings.prof.message) Live.say(state.settings.prof.message);
  } catch (e) { toast(e.message, true); }
  render();
}

// Alle Einstellungen exportieren und importieren (System › Einstellungen übertragen)
function aboutBlock() {                          // Software mit Lizenz, Urheber, Projekt (Jens 04.10.2026)
  const a = state.settings.about;
  if (!a) return `<fieldset><legend>Über Ottilie</legend><div role="status">Lade …</div></fieldset>`;
  const groups = a.groups.map((g) => {
    const rows = a.components.filter((c) => c.group === g).map((c, i) => `<tr>
        <td><strong>${esc(c.name)}</strong>${c.note ? `<div class="hint">${esc(c.note)}</div>` : ""}</td>
        <td class="mono">${esc(c.version || "–")}</td><td>${esc(c.license)}</td><td>${esc(c.author)}</td>
        <td>${c.url ? `<button class="btn ghost sm" data-k="lnk-${esc(g)}-${i}" data-act="open-link" data-url="${esc(c.url)}" aria-label="Projektseite ${esc(c.name)} öffnen">${esc(c.url.replace(/^https?:\/\//, ""))}</button>` : "–"}
          ${c.source ? `<button class="btn ghost sm" data-k="src-${esc(g)}-${i}" data-act="open-link" data-url="${esc(c.source)}" aria-label="Quelltext ${esc(c.name)} ${esc(c.version)} öffnen">Quelltext</button>` : ""}</td>
        <td class="hint">${esc(c.use)}</td></tr>`).join("");
    return rows ? `<h3 style="margin:12px 0 4px;font-size:15px">${esc(g)}</h3>
      <table class="settab"><thead><tr><th scope="col">Software</th><th scope="col">Version</th><th scope="col">Lizenz</th><th scope="col">Urheber</th><th scope="col">Projekt</th><th scope="col">Wofür</th></tr></thead><tbody>${rows}</tbody></table>` : "";
  }).join("");
  return `<fieldset><legend>Über Ottilie</legend>
    <p style="margin:0">Ottilie ${esc(a.version)} – Tonmischung mit Helmut und Pro Tools. Die App bringt diese Software mit; die Lizenztexte liegen im Paket bei den jeweiligen Bestandteilen.</p>
    ${groups}</fieldset>`;
}

function templatesBlock() {                       // Meine Templates (Plan Nutzer & Räume, Schritt 3)
  const t = state.settings.tpl;
  if (!t) return `<fieldset><legend>Meine Templates</legend><div role="status">Lade …</div></fieldset>`;
  const rows = t.mine.map((x, i) => `<li style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <strong>${esc(x.label)}</strong><span class="hint">${esc(x.group)}${x.uploaded ? " · hochgeladen" : ""}</span>
      ${x.standard ? `<span class="chip" aria-label="Standard-Template">Standard</span>` : `<button class="btn ghost sm" data-k="tpl-std-${i}" data-act="tpl-std" data-id="${esc(x.id)}">Als Standard</button>`}
      ${state.settings.tplDel === x.id
        ? `<span>${x.uploaded ? "Ins Archiv verschieben?" : "Aus der Liste nehmen?"}</span> <button class="btn sm" data-k="tpl-del-ok-${i}" data-act="tpl-del-ok" data-id="${esc(x.id)}">Ja</button>
           <button class="btn ghost sm" data-k="tpl-del-no-${i}" data-act="tpl-del-no">Nein</button>`
        : `<button class="btn ghost sm" data-k="tpl-del-${i}" data-act="tpl-del" data-id="${esc(x.id)}">${x.uploaded ? "Löschen" : "Entfernen"}</button>`}</li>`).join("");
  const found = (t.found || []).map((x, i) => `<option value="${i}">${esc(x.group)} · ${esc(x.label)}</option>`).join("");
  return `<fieldset><legend>Meine Templates</legend>
    <span class="hint">Hochgeladene Templates wandern mit dem Nutzer; die App stellt sie vor dem Anlegen in Pro Tools bereit (Gruppe „Ottilie &lt;Nutzer&gt;“). Löschen verschiebt sie ins Archiv.</span>
    <ul class="warnlist" aria-label="Meine Templates">${rows || `<li class="hint">Noch keine Templates.</li>`}</ul>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <button class="btn sm" data-k="tpl-up" data-act="tpl-up" ${t.can_upload ? "" : 'disabled aria-disabled="true" title="Erst anmelden"'}>Template hochladen …</button>
      ${found ? `<select class="inp" data-k="tpl-found" data-tplfound="1" aria-label="Template aus Pro Tools">${found}</select>
        <button class="btn sm" data-k="tpl-adopt" data-act="tpl-adopt">Aus Pro Tools übernehmen</button>` : ""}</div>
    ${t.message ? `<div role="status" class="hint">${esc(t.message)}</div>` : ""}</fieldset>`;
}

async function tplCall(cmd, args) {
  try { state.settings.tpl = await TM.call(cmd, args || {}); state.templatesMain = null; state.tplPickFor = null; }
  catch (e) { toast(e.message, true); if (state.settings.tpl) state.settings.tpl.message = e.message; }
  state.settings.tplDel = null; render();
}

function roomsBlock() {                           // Räume über Mac-Namen (Plan Nutzer & Räume, Schritt 4)
  const r = state.settings.rooms;
  if (!r) return `<fieldset><legend>Räume</legend><div role="status">Lade …</div></fieldset>`;
  const macOpt = (cur) => `<option value="" ${cur ? "" : "selected"}>– Mac wählen –</option>` +
    r.macs.map((m) => `<option value="${esc(m)}" ${m === cur ? "selected" : ""}>${esc(m)}${m === r.this_mac ? " (dieser Mac)" : ""}</option>`).join("");
  const rows = r.rows.map((x, i) => `<tr>
      <td><input class="inp sm" data-k="room-name-${i}" data-room="${i}" data-rkey="name" value="${esc(x.name || "")}" aria-label="Raumname, Zeile ${i + 1}"></td>
      <td><select class="inp sm" data-k="room-mac-${i}" data-room="${i}" data-rkey="rechnername" aria-label="Mac, Zeile ${i + 1}">${macOpt(x.rechnername || "")}</select></td>
      <td><input class="inp sm" data-k="room-code-${i}" data-room="${i}" data-rkey="id_code" value="${esc(x.id_code || "")}" aria-label="ID-Code, Zeile ${i + 1}"></td>
      <td><button class="btn ghost sm" data-k="room-del-${i}" data-act="room-del" data-idx="${i}" aria-label="Zeile ${i + 1} entfernen">Entfernen</button></td></tr>`).join("");
  return `<fieldset><legend>Räume</legend>
    <span class="hint">Ein Raum ist ein Mac. Dieser Mac: <strong>${esc(r.this_mac)}</strong> – Raum „${esc(r.this_room)}“. Zur Auswahl stehen dieser Mac und die Macs, auf denen Ottilie gerade läuft. Die Liste liegt in der Ablage der Nutzer (${esc(r.ablage)}) – im gemeinsamen Ordner gilt sie für alle Räume.</span>
    <table class="settab"><thead><tr><th scope="col">Raum</th><th scope="col">Mac</th><th scope="col">ID-Code (Webtrigger, optional)</th><th scope="col"><span class="sr-only">Aktion</span></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="4" class="hint">Noch keine Räume.</td></tr>`}</tbody></table>
    <div style="display:flex;gap:8px"><button class="btn sm" data-k="room-add" data-act="room-add">Raum hinzufügen</button>
      <button class="btn sm pri" data-k="room-save" data-act="room-save">Räume speichern</button></div>
    ${r.errors.length ? `<div class="banner warn" data-alert>${r.errors.map(esc).join("<br>")}</div>` : ""}
    ${r.message ? `<div role="status" class="hint">${esc(r.message)}</div>` : ""}</fieldset>`;
}

function usersBlock() {
  const u = state.users || { users: [] };
  const ed = state.settings.usrEdit || {};
  const rows = u.users.map((x, i) => {
    const tags = [u.current && u.current.id === x.id ? "angemeldet" : "", u.standard === x.id ? "Standard auf diesem Mac" : ""].filter(Boolean).join(" · ");
    let tail = `<button class="btn ghost sm" data-k="usr-ren-${i}" data-act="usr-edit" data-usrmode="umbenennen" data-id="${esc(x.id)}">Umbenennen</button>
      <button class="btn ghost sm" data-k="usr-dup-${i}" data-act="usr-edit" data-usrmode="duplizieren" data-id="${esc(x.id)}">Duplizieren</button>
      <button class="btn ghost sm" data-k="usr-del-${i}" data-act="usr-edit" data-usrmode="loeschen" data-id="${esc(x.id)}">Löschen</button>`;
    if (ed.id === x.id && ed.mode === "loeschen") {
      tail = `<span>„${esc(x.name)}“ ins Archiv verschieben?</span> <button class="btn sm" data-k="usr-ok-${i}" data-act="usr-do">Ja</button>
        <button class="btn ghost sm" data-k="usr-no-${i}" data-act="usr-cancel">Nein</button>`;
    } else if (ed.id === x.id) {
      tail = `<input class="inp sm" data-k="usr-name-${i}" data-usrname="1" value="${esc(ed.name)}" aria-label="${ed.mode === "duplizieren" ? "Name der Kopie" : "Neuer Name"}">
        <button class="btn sm" data-k="usr-ok-${i}" data-act="usr-do">${ed.mode === "duplizieren" ? "Kopie anlegen" : "Umbenennen"}</button>
        <button class="btn ghost sm" data-k="usr-no-${i}" data-act="usr-cancel">Abbrechen</button>`;
    }
    return `<li style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><strong>${esc(x.name)}</strong>${tags ? `<span class="hint">${esc(tags)}</span>` : ""}${tail}</li>`;
  }).join("");
  return `<fieldset><legend>Nutzer</legend>
    <span class="hint">Ohne Passwort. Löschen verschiebt den Nutzer mit Einstellungen und Templates ins Archiv der Ablage (${esc(u.ablage || "")}).</span>
    <ul class="warnlist" aria-label="Nutzer">${rows}</ul>
    <div style="display:flex;gap:8px;align-items:center"><input class="inp" data-k="usr-new" data-usrnew="1" value="${esc(state.settings.usrNew || "")}" placeholder="Name" aria-label="Name für einen neuen Nutzer">
      <button class="btn sm" data-k="usr-create" data-act="usr-create">Neuer Nutzer</button></div>
    ${state.settings.usrMsg ? `<div role="status" class="hint">${esc(state.settings.usrMsg)}</div>` : ""}</fieldset>`;
}

async function usersCall(cmd, args) {
  try {
    const r = await TM.call(cmd, args);
    state.users = r; state.settings.usrMsg = r.message || ""; state.settings.usrEdit = null;
    await refreshStatus();
  } catch (e) { state.settings.usrMsg = e.message; toast(e.message, true); }
  render();
}

async function openLogin(message) {
  try { state.users = await TM.call("nutzer"); } catch (e) { toast(e.message, true); return; }
  const u = state.users;
  state.dialog = { kind: "login", sel: (u.current && u.current.id) || u.standard || (u.users[0] && u.users[0].id) || "",
                   std: false, newName: "", message: message || "" };
  state.dialog.std = !!state.dialog.sel && u.standard === state.dialog.sel;
  render();
}

function transferBlock() {
  const t = state.settings.transfer || {};
  const im = t.import;
  let pick = "";
  if (im && im.choices.length) {
    const all = im.choices.every((c) => im.sel[c.id]);
    const status = { neu: "neu", ersetzt: "ersetzt das vorhandene Profil", gleich: "unverändert" };
    const rows = im.choices.map((c, i) => `<label class="chk"><input type="checkbox" data-k="tr-${i}" data-tr-pick="${esc(c.id)}" ${im.sel[c.id] ? "checked" : ""}>
      <span><strong>${esc(c.area)}</strong> · ${esc(c.title)} – ${c.status ? esc(status[c.status] || c.status) : `${c.count} Werte, ${c.changed} anders als hier`}${c.locked ? `, ${c.locked} gesperrt (Geräteverwaltung)` : ""}</span></label>`).join("");
    pick = `<div class="banner info prof-imp" role="group" aria-labelledby="tr-title"><strong id="tr-title">Aus „${esc(im.path.split("/").pop())}“ importieren – was soll übernommen werden?</strong>
      <label class="chk"><input type="checkbox" data-k="tr-all" data-tr-all="1" ${all ? "checked" : ""}><span>Alles</span></label>
      ${rows}
      ${im.unknown && im.unknown.length ? `<span class="hint">${im.unknown.length} Werte kennt diese Version nicht – sie werden übergangen.</span>` : ""}
      <span class="hint">„Nur für einen Mac“ (Ordner, Aufnahmespuren, Stream Deck, Raum) ist nicht vorgewählt.</span>
      <div><button class="btn sm" data-k="tr-ok" data-tr-act="ok">Gewähltes übernehmen</button>
        <button class="btn ghost sm" data-k="tr-no" data-tr-act="ab">Abbrechen</button></div></div>`;
  }
  return `<fieldset><legend>Einstellungen übertragen</legend>
    <span class="hint">Exportiert alles, was auf diesem Mac eingestellt ist – auch Spurprofile und Exportnamen –, als Datei; Zugangsdaten liegen nur im Schlüsselbund und sind nicht dabei. Beim Import wählen Sie, was übernommen wird.</span>
    <div class="buttons-row"><button class="btn sm" data-k="tr-exp" data-tr-act="exportieren">Alle Einstellungen exportieren …</button>
      <button class="btn sm" data-k="tr-imp" data-tr-act="importieren">Einstellungen importieren …</button></div>
    ${t.message ? `<div role="status" class="hint">${esc(t.message)}</div>` : ""}
    ${pick}</fieldset>`;
}

async function transferAction(act) {
  const t = state.settings.transfer = state.settings.transfer || {};
  try {
    if (act === "exportieren") { t.message = (await TM.call("einstellungen_exportieren", {})).message; t.import = null; }
    else if (act === "importieren") {
      const im = await TM.call("einstellungen_import_pruefen", {});
      im.sel = Object.fromEntries(im.choices.map((c) => [c.id, c.default]));
      t.import = im.choices.length ? im : null;
      t.message = im.message || "";
    } else if (act === "ab") { t.import = null; t.message = "Import abgebrochen"; }
    else if (act === "ok") {
      const im = t.import;
      t.message = (await TM.call("einstellungen_importieren", { pfad: im.path, auswahl: Object.keys(im.sel).filter((k) => im.sel[k]) })).message;
      t.import = null;
    }
    if (t.message) Live.say(t.message);
  } catch (e) { toast(e.message, true); }
  render();
}

const CUSTOM_FIELDS = new Set(["aufnahme.presets", "import.spurprofile", "aufnahme.audio_verschieben.quellspuren",
  "aufnahme.audio_verschieben.zielspuren"]);

function settingsView() {
  const st = state.settings;
  const nav = (st.areas || []).map((a) => `<h2 id="area-${esc(a.id)}">${esc(a.title)}</h2>
    <div role="tablist" aria-labelledby="area-${esc(a.id)}" aria-orientation="vertical">${a.sections.map((x) => `<button class="tab ${st.sectionId === x.id ? "on" : ""}" role="tab"
      aria-selected="${st.sectionId === x.id}" data-k="sec-${esc(x.id)}" data-sec="${esc(x.id)}">${esc(x.title)}</button>`).join("")}</div>`).join("");
  const sec = st.section;
  const form = !sec ? `<div role="status">Lade …</div>` : `<h1 id="set-title">${esc(sec.area)} · ${esc(sec.title)}</h1>
    <span class="hint">Änderungen gelten sofort und werden auf diesem Mac gespeichert. Von der Geräteverwaltung festgelegte Werte sind gesperrt.</span>
    ${sec.id === "aufnahme.spuren" ? gridBlock() : ""}${sec.id === "aufnahme.audio_verschieben" ? moveBlock() : ""}${sec.id === "aufnahme" ? presetsBlock() : ""}${sec.id === "system.streamdeck_webtrigger" ? webtriggerBlock() : ""}${sec.id === "system.vocaster" ? vocasterBlock() : ""}${sec.id === "import.spurprofile" ? profileBlock() : ""}${sec.id === "system.einstellungen_uebertragen" ? transferBlock() : ""}${sec.id === "system.raeume_nutzer" ? roomsBlock() + usersBlock() : ""}${sec.id === "persoenlich" ? templatesBlock() : ""}${sec.id === "system.ueber" ? aboutBlock() : ""}
    ${sec.groups.map((g) => { const fields = g.fields.filter((f) => !CUSTOM_FIELDS.has(f.path)); return g.title ? `<fieldset><legend>${esc(g.title)}</legend>${fields.map(fieldRow).join("")}</fieldset>` : fields.map(fieldRow).join(""); }).join("")}`;
  return `<main class="settings" aria-label="Einstellungen"><nav class="set-nav" aria-label="Bereiche der Einstellungen">${nav}</nav>
    <div class="set-form" role="tabpanel" aria-labelledby="set-title">${form}</div></main>`;
}

async function openSettings(sectionId) {
  state.flow = null;
  state.settings = state.settings || { areas: null, sectionId: null, section: null };
  render();
  try {
    if (!state.settings.areas) state.settings.areas = (await TM.call("einstellungen")).areas;
    const id = sectionId || state.settings.sectionId || state.settings.areas[0].sections[0].id;
    state.settings.sectionId = id;
    state.settings.section = await TM.call("einstellungen_abschnitt", { id });
    if (id === "aufnahme.spuren") { state.settings.grid = null; render(); state.settings.grid = await TM.call("spurraster", {}); }
    if (id === "aufnahme.audio_verschieben") { state.settings.move = null; render(); setMove(await TM.call("audio_verschieben", {})); }
    if (id === "aufnahme") state.settings.presets = (await TM.call("presets", {})).presets;
    if (id === "system.streamdeck_webtrigger") state.settings.wt = await TM.call("webtrigger_adressen", {});
    if (id === "system.vocaster") state.settings.voc = await TM.call("vocaster_info", {});
    if (id === "import.spurprofile") { state.settings.prof = null; state.settings.profConfirm = false; render(); state.settings.prof = await TM.call("spurprofile", {}); }
    if (id === "persoenlich") state.settings.tpl = await TM.call("vorlagen", {});
    if (id === "system.ueber") state.settings.about = await TM.call("ueber", {});
    if (id === "system.raeume_nutzer") { const r = await TM.call("raumliste", {}); state.settings.rooms = { ...r, rows: r.rooms.map((x) => ({ ...x })) }; }
  } catch (e) { toast(e.message, true); }
  render();
}

async function reloadSection() {
  const id = state.settings.sectionId;
  state.settings.section = await TM.call("einstellungen_abschnitt", { id });
}

async function presetAction(act, nr) {
  try {
    if (act === "laden") {
      const r = await TM.call("punch_preset", { nr });
      Live.say(`Preset ${nr} geladen`);
      void r;
    } else if (act === "ueberschreiben") {
      const name = (state.settings.presets.find((x) => x.nr === nr) || {}).name || `Preset ${nr}`;
      state.settings.presets = (await TM.call("preset_speichern", { nr, name })).presets;
      Live.say(`Aktueller Stand in Preset ${nr} gespeichert`);
    } else if (act === "loeschen") {
      state.settings.presets = (await TM.call("preset_loeschen", { nr })).presets;
      Live.say(`Preset ${nr} gelöscht`);
    } else if (act === "neu") {
      state.settings.presets = (await TM.call("preset_speichern", { name: state.settings.newPreset || "" })).presets;
      state.settings.newPreset = "";
      Live.say("Preset angelegt");
    }
    state.settings.presets = (await TM.call("presets", {})).presets;
    await reloadSection();
  } catch (e) { toast(e.message, true); }
  render();
}

function settingField(path) {
  for (const g of state.settings.section.groups) { const f = g.fields.find((x) => x.path === path); if (f) return f; }
  return null;
}

async function saveSetting(path, value) {
  try {
    const view = await TM.call("einstellung_setzen", { path, value });
    for (const g of state.settings.section.groups) { const i = g.fields.findIndex((x) => x.path === path); if (i >= 0) g.fields[i] = view; }
    Live.say(view.errors.length ? `Nicht gespeichert: ${view.errors.join(", ")}` : `${view.label} gespeichert`, !!view.errors.length);
    if (path.startsWith("persoenlich.templates")) state.templates = null;
    if (path === "persoenlich.farbschema" && !view.errors.length) state.theme = view.value;      // sofort umschalten
    if (path.startsWith("system.raeume_nutzer.")) { try { state.users = await TM.call("nutzer"); } catch (e) { /* bleibt */ } }
    if (path === "export.spuren_lautheit.exportnamen" && state.settings.prof) {      // Auswahl im Profil auffrischen
      const c = state.settings.prof.chosen;
      state.settings.prof = await TM.call("spurprofile", { gruppe: c.group, name: c.name });
    }
  } catch (e) { toast(e.message, true); }
  render();
}

function readInput(el) {
  const kind = el.dataset.kind || el.dataset.ckind;
  if (kind === "bool") return el.checked;
  if (kind === "number") return el.value === "" ? null : Number(el.value);
  if (kind === "list") return el.value.split("\n").map((x) => x.trim()).filter(Boolean);
  if (kind === "json") { try { return el.value === "" ? null : JSON.parse(el.value); } catch (e) { return { __fehler: "JSON nicht lesbar: " + e.message }; } }
  return el.value;
}

// ── Zeichnen: Fokus und Scrollstand erhalten ────────────────────────────────
// Jens 06.10.2026: „Die App springt beim Scrollen oder kurz danach zurück zum Ausgangspunkt“ (v. a. Einstellungen).
// Jedes Neuzeichnen (Status alle 15 s, Hinweis ausblenden nach 3 s, Auftragsfortschritt) ersetzte die Scrollbereiche
// – Stand 0 – und holte den Fokus samt Scrollen zum zuletzt benutzten Feld zurück. Jetzt: vier Bereiche (Titelleiste,
// Hauptteil, Dialog, Hinweis) werden einzeln gesetzt; Hintergrund-Aktualisierungen (`render(true)`) setzen nur, was
// sich geändert hat – Scrollen und halb getippte Eingaben bleiben unberührt. Wird ein Bereich neu gesetzt, bleiben
// seine Scrollstände, solange dieselbe Ansicht zu sehen ist, und der Fokus kehrt ohne Scrollen zurück.
const REGIONS = ["top", "body", "dlg", "toast"];
const lastHtml = {};
function regionHost(root, name) {
  let el = root.querySelector(`:scope > [data-region="${name}"]`);
  if (!el) {
    el = document.createElement("div");
    el.dataset.region = name;
    el.style.display = "contents";        // Kinder bleiben Teil des Flex-Layouts von .app
    root.appendChild(el);
  }
  return el;
}
// Welche Ansicht ein Scrollbereich zeigt – gemerkt beim Zeichnen, denn beim nächsten Zeichnen ist der Zustand
// schon umgeschaltet (anderer Abschnitt, anderer Beitrag: dort oben anfangen).
function viewSnap() {
  const f = state.flow, d = state.dialog || {};
  return {
    main: state.error ? "err" : (f ? `flow:${f.kind || "holen"}:${f.id || ""}:${f.step}`
      : (state.settings ? `set:${state.settings.sectionId}` : `view:${state.view}`)),
    selected: String(state.selected || ""), dlg: `${d.kind || ""}:${d.question ? d.question.id : ""}`,
  };
}
let shownSnap = viewSnap();
function scrollContext(el, snap) {
  if (el.closest(".dialog")) return "dlg:" + snap.dlg;
  return el.classList.contains("detail-body") ? `${snap.main}:${snap.selected}` : snap.main;
}
function scrollSelector(el) {
  return el.classList.length ? "." + CSS.escape(el.classList[0]) : el.tagName.toLowerCase();
}
function saveScroll(root, snap) {
  const out = [];
  for (const el of root.querySelectorAll("*")) {
    if (!el.scrollTop && !el.scrollLeft) continue;
    const sel = scrollSelector(el);
    out.push({ sel, i: [...root.querySelectorAll(sel)].indexOf(el), top: el.scrollTop, left: el.scrollLeft, ctx: scrollContext(el, snap) });
  }
  return out;
}
function restoreScroll(root, saved, snap) {
  for (const s of saved) {
    const el = root.querySelectorAll(s.sel)[s.i];
    if (el && scrollContext(el, snap) === s.ctx) { el.scrollTop = s.top; el.scrollLeft = s.left; }
  }
}

function applyTheme() {
  // Farbschema des angemeldeten Nutzers (Jens 08.10.2026; Standard Ottilie) – nur Farben und Formen, Bedienung gleich
  const theme = state.theme || (state.status && state.status.theme) || "ottilie";
  if (document.documentElement.dataset.theme !== theme) document.documentElement.dataset.theme = theme;
}

// quiet: Aktualisierung aus dem Hintergrund (Takt, Auftrag, Hinweis) – nur geänderte Bereiche neu setzen.
// Sonst (nach einer Bedienung) alle Bereiche, damit das Bild sicher dem Stand entspricht.
function render(quiet = false) {
  applyTheme();
  const active = document.activeElement;
  const key = active && active.dataset ? active.dataset.k : null;
  const sel = active && active.id === "q" ? [active.selectionStart, active.selectionEnd] : null;
  const st = state.status;
  const center = state.error ? `<main class="main"><div class="placeholder error" data-alert>${esc(state.error)}</div></main>`
    : (state.flow ? flowView() : (state.settings ? settingsView() : (state.view === "rooms" ? roomsView() : (state.view === "history" ? historyView() : listView() + detailView()))));
  const parts = {
    top: topbar(st), body: `<div class="body">${nav()}${center}</div>`, dlg: dialogView(),
    toast: state.toast ? `<div class="toast ${state.toast.err ? "err" : ""}" aria-hidden="true">${esc(state.toast.text)}</div>` : "",
  };
  const root = document.getElementById("root");
  const snap = viewSnap();
  for (const name of REGIONS) {
    const host = regionHost(root, name);
    if (quiet && parts[name] === lastHtml[name]) continue;
    // Takt-Neuzeichnen nicht mitten in eine Eingabe: ein noch nicht übernommener Wert ginge verloren (Review B32)
    const typing = document.activeElement;
    if (quiet && host.contains(typing) && typing.matches("input, textarea") && typing.id !== "q"
        && typing.value !== typing.defaultValue) continue;
    lastHtml[name] = parts[name];
    const scroll = saveScroll(host, shownSnap);
    host.innerHTML = parts[name];
    restoreScroll(host, scroll, snap);
  }
  shownSnap = snap;
  const dlg = document.querySelector(".dialog");
  if (dlg && !state.dialogShown) {
    state.dialogShown = true;             // Dialog neu: Fokus einmal hinein
    focusFirst(dlg);
  } else if (key && !(active && active.isConnected)) {
    const el = document.querySelector(`[data-k="${CSS.escape(key)}"]`);
    if (el && !el.disabled) { el.focus({ preventScroll: true }); if (sel && el.setSelectionRange) el.setSelectionRange(sel[0], sel[1]); }
    else if (!dlg) {                                    // Knopf jetzt gesperrt (z. B. Liste leer): zur Überschrift
      const h = document.querySelector('[data-k="view-title"]');
      if (h) h.focus({ preventScroll: true });
    }
  }
  if (!dlg && state.dialogShown) {
    // Dialog zu (auf welchem Weg auch immer): Fokus zurück an den Knopf, der ihn geöffnet hat – vorher landete er
    // nach „Freigeben“, „Duplizieren“ usw. im Nichts (Review B31)
    const now = document.activeElement;
    const lost = !now || now === document.body || !now.isConnected;
    const el = lost && state.returnFocus && document.querySelector(`[data-k="${CSS.escape(state.returnFocus)}"]`);
    if (el && !el.disabled) el.focus({ preventScroll: true });
    else if (lost) {                                    // Auslöser weg oder gesperrt: zur Überschrift der Ansicht
      const h = document.querySelector('[data-k="view-title"]');
      if (h) h.focus({ preventScroll: true });
    }
  }
  if (!dlg) state.dialogShown = false;
  announceAlerts(root);
}

// Warnungen (`data-alert`) einmal ansagen, wenn sie erscheinen. Mit role="alert" las VoiceOver sie bei jedem
// Neuzeichnen erneut vor – z. B. alle 15 s den Pro-Tools-Hinweis in der Titelleiste (Review B31).
const announced = new Set();
function announceAlerts(root) {
  const shown = [...root.querySelectorAll("[data-alert]")].map((e) => e.textContent.trim()).filter(Boolean);
  const fresh = shown.filter((t) => !announced.has(t));
  if (fresh.length) Live.say(fresh.join(". "), true);
  announced.clear();
  shown.forEach((t) => announced.add(t));
}

function focusables(root) {
  return [...root.querySelectorAll("button:not([disabled]), input, select, [tabindex='0']")];
}
function focusFirst(root) {
  const items = focusables(root);
  const first = root.querySelector("[data-default-focus]") || root.querySelector("[aria-checked='true']")
    || items[0];
  if (first) first.focus();
}

// ── Daten ───────────────────────────────────────────────────────────────────
async function loadList(quiet) {
  quiet = quiet === true;                 // auch als Ereignis-Handler benutzt
  if (state.view === "rooms") {
    try { state.rooms = await TM.call("raeume", {}); } catch (e) { toast(e.message, true); }
    return render(quiet);
  }
  if (state.view === "history") {
    try { state.history = await TM.call("mischungen", {}); } catch (e) { toast(e.message, true); }
    return render(quiet);
  }
  try {
    state.list = await TM.call("list", { query: state.query, order: state.order });
    const ids = state.list.rows.map((r) => r.id);
    if (!ids.includes(state.selected)) state.selected = ids[0] || null;
    await loadDetail();
  } catch (e) { toast(e.message, true); }
  render(quiet);
}

async function loadDetail() {
  const id = state.selected;
  const d = id ? await TM.call("detail", { id }) : null;
  if (state.selected === id) state.detail = d;    // inzwischen anderer Beitrag gewählt: Antwort verwerfen (Review A14)
}

async function select(id, focusRow) {
  state.selected = id;
  TM.call("auswahl", { id }).catch(() => {});          // für den Webtrigger /import
  render();
  try { await loadDetail(); } catch (e) { toast(e.message, true); }
  render();
  if (focusRow) { const el = document.querySelector(`[data-pick="${CSS.escape(id)}"]`); if (el) el.focus(); }
}

async function openFlow(fresh) {
  const d = state.detail;
  state.returnFocus = "act-fetch";
  if (d.reopen && !fresh) {               // freigegeben mit „Letzter Stand“ bzw. Duplikat mit Mischstand
    state.flow = { kind: "reopen", id: d.id, title: d.title, step: "choice", reopen: d.reopen };
    render();
    return focusFirst(document.querySelector(".flow-body"));
  }
  state.flow = { id: d.id, title: d.title, step: "template", choice: null, triage: true, video: true };
  state.templates = null;
  render();
  try {
    state.templates = await TM.call("templates", { show: d.show || "" });   // Vorauswahl hängt an der Sendung
    if (state.templates.default) state.flow.choice = state.templates.default;
    if (state.tplPick) {                      // im Hauptfenster gewählt (Vorgabe: Standard-Template)
      const [g, n] = state.tplPick.split("\u0001");
      state.flow.choice = { group: g, name: n };
    }
    state.flow.triage = state.templates.triage !== false;
  } catch (e) { toast(e.message, true); }
  try {
    const o = await TM.call("offene_session_info", { id: d.id });
    if (state.flow && state.flow.id === d.id && o.offer) {
      state.flow.open = o; state.flow.mode = "neu"; state.flow.openOpts = { ...o.defaults };
    }
  } catch (e) { /* ohne Angebot: immer neue Session */ }
  render();
  focusFirst(document.querySelector(".flow-body"));
}

async function openDeliver() {
  const d = state.detail;
  state.flow = { kind: "deliver", id: d.id, title: d.title, step: "overview", info: null, sel: {} };
  render();
  try {
    const info = await TM.call("zurueckliefern_info", { id: d.id });
    state.flow.info = info;
    state.flow.sel = Object.fromEntries(info.tracks.map((t) => [t, true]));
  } catch (e) { toast(e.message, true); }
  render();
  const el = document.getElementById("del-tracks"); if (el) el.focus();
}

async function startDeliver() {
  const f = state.flow;
  try {
    const i = f.info;
    const order = i.session_tracks.concat(i.tracks.filter((t) => !i.session_tracks.includes(t)));
    state.job = await TM.call("zurueckliefern", { id: f.id, tracks: order.filter((t) => f.sel[t]) });
    f.step = "job";
    render();
    const h = document.querySelector(".progress-box h2");
    if (h) { h.tabIndex = -1; h.focus(); }
  } catch (e) { toast(e.message, true); }
}

async function hvCheck() {
  const f = state.flow;
  try {
    f.info = await TM.call("havarie_info", { path: f.path });
    f.title = f.title || f.info.title; f.pid = f.pid || f.info.project_id; f.video = !!f.info.video;
  } catch (e) { f.info = null; toast(e.message, true); }
  render();
}

async function startReopen() {
  const f = state.flow;
  try {
    state.job = await TM.call("wiederoeffnen", { id: f.id });
    f.step = "job";
    f.jobId = state.job.id;
    render();
    const h = document.querySelector(".progress-box h2");
    if (h) { h.tabIndex = -1; h.focus(); }
  } catch (e) { toast(e.message, true); }
}

async function startFetch() {
  const f = state.flow;
  try {
    state.job = f.mode === "offen" && f.open && f.open.offer
      ? await TM.call("holen_offen", { id: f.id, video: f.video, ...f.openOpts })
      : await TM.call("holen", { id: f.id, group: f.choice.group, name: f.choice.name, triage: f.triage, video: f.video });
    f.step = "import";
    f.jobId = state.job.id;
    render();
    const h = document.querySelector(".progress-box h2");
    if (h) { h.tabIndex = -1; h.focus(); }
  } catch (e) { toast(e.message, true); }
}

function closeFlow() {
  state.flow = null;
  render();
  loadList().then(() => {
    const el = document.querySelector(`[data-pick="${CSS.escape(state.selected || "")}"]`);
    if (el) el.focus();
  });
}

// ── Aufträge und Fragen ─────────────────────────────────────────────────────
TM.on("job", (j) => onJob(j));
function onJob(j) {
  state.job = j;
  Live.job(j);
  if (j.state === "frage" && j.question && j.question.kind === "triage_vorschau") {
    // Nur den Ablauf dieses Auftrags auf die Vorschau stellen – vorher bekam jeder offene Ablauf (Zurückliefern,
    // Havarie-Versand …) den Schritt „triage“, die Frage blieb unsichtbar und „Zur Frage“ führte im Kreis (Review B33)
    if (state.flow && state.flow.jobId !== j.id) {
      if (state.pendingTriage !== j.question.id) {
        state.pendingTriage = j.question.id;
        toast("Die Vorschau der AAF-Triage wartet – oben auf den laufenden Auftrag klicken");
      }
      return render(true);
    }
    if (!state.preview || state.preview.qid !== j.question.id) {
      state.preview = { qid: j.question.id, view: j.question.view, mode: "zeit", sel: null, filter: false };
      if (!state.flow) state.flow = flowForJob(j);
      state.flow.step = "triage";
      const v = j.question.view;
      Live.say(`Vorschau der AAF-Triage: ${v.clips} Clips, ${v.unsure} unsicher, ${v.moves} Bereiche werden verschoben. Import starten oder Abbrechen.`, true);
      render();
      const h = document.getElementById("flow-title"); if (h) { h.tabIndex = -1; h.focus(); }
    }
    return render();
  }
  if (state.flow && state.flow.step === "triage" && j.state !== "frage") { state.flow.step = "import"; state.preview = null; }
  if (j.state === "frage" && j.question && !(state.dialog && state.dialog.question && state.dialog.question.id === j.question.id)) {
    state.returnFocus = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.k : null;
    const answers = {};
    (j.question.missing || []).forEach((t) => { answers[t] = "ab A11"; });
    state.dialog = { kind: "question", question: j.question, answers };
    Live.say(`Frage: ${j.question.title}`, true);
  }
  if (j.state !== "frage" && state.dialog && state.dialog.kind === "question") state.dialog = null;
  if (["fertig", "fehler", "abgebrochen"].includes(j.state) && state.selected) {
    loadDetail().then(() => render(true)).catch(() => {});
  }
  render(true);
}

// Ablauf, der zu einem Auftrag passt (Titelleiste „laufender Auftrag“, Triage-Frage): Art aus dem Auftrag, Beitrag
// aus `job.contribution` – vorher wurde jeder Auftrag ein „Holen“ des gerade gewählten Beitrags (Review B33).
function flowForJob(j) {
  const base = { id: j.contribution || "", title: j.title, jobId: j.id };
  if (j.kind === "zurueckliefern") return { ...base, kind: "deliver", step: "job" };
  if (j.kind === "havarie_ausliefern") return { ...base, kind: "hvdel", step: "job" };
  if (j.kind === "havarie") return { ...base, kind: "havarie", step: "job" };
  if (j.kind === "wiederoeffnen") return { ...base, kind: "reopen", step: "job" };
  return { ...base, title: j.title.replace(/^Holen: /, ""), step: "import" };
}

async function answer(value) {
  const q = state.dialog.question;
  state.dialog = null;
  render();
  try { await TM.call("antworten", { id: q.id, value }); } catch (e) { toast(e.message, true); }
}

// ── Bedienung ───────────────────────────────────────────────────────────────
// Doppelklick löst eine Aktion nicht zweimal aus (Review A15: Duplizieren legte zwei Duplikate an): solange die
// Aktion eines Knopfs (`data-k`) noch läuft, bleiben weitere Klicks auf ihn ohne Wirkung.
const CLICKABLE = "[data-view],[data-pick],[data-act],[data-reason],[data-keep],[data-order],[data-purpose],[data-mode],[data-ntdrop],[data-ntdrophv],[data-bakopen],[data-bakreveal],[data-roomrel],[data-hvassign],[data-tpl],[data-answer],[data-clip],[data-tab],[data-move],[data-sec],[data-reset],[data-add-row],[data-del-row],[data-grid-read],[data-mv-read],[data-mv-add],[data-mv-del],[data-prof-act],[data-prof-del],[data-tr-act],[data-preset-act],[data-copy],[data-copy-all],[data-wt-id]";
const runningClicks = new Set();
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest(CLICKABLE);
  if (!t || t.disabled) return;
  const key = t.dataset.k;
  if (key && runningClicks.has(key)) return;
  if (key) runningClicks.add(key);
  try { await onClick(ev, t); } finally { if (key) runningClicks.delete(key); }
});

async function onClick(ev, t) {
  if (t.dataset.copy || t.dataset.copyAll) {
    const w = state.settings.wt;
    const text = t.dataset.copy || w.rows.map((r) => r.label + "\t" + r.url).join("\n");
    try { await TM.call("kopieren", { text }); Live.say(t.dataset.copy ? "Adresse kopiert" : `${w.rows.length} Adressen für ${w.room} kopiert`); }
    catch (e) { toast(e.message, true); }
    return;
  }
  if (t.dataset.wtId) {
    try { state.settings.wt = await TM.call(t.dataset.wtId === "neu" ? "id_code_erzeugen" : "id_code_entfernen", { raum: state.settings.wt.room });
      Live.say(state.settings.wt.id_code ? `ID-Code ${state.settings.wt.id_code} gesetzt` : "ID-Code entfernt"); }
    catch (e) { toast(e.message, true); }
    return render();
  }
  if (t.dataset.profAct) {
    const act = t.dataset.profAct;
    if (act === "fragen" || act === "behalten") { state.settings.profConfirm = act === "fragen"; return render(); }
    state.settings.profConfirm = false;
    if (act === "exportieren" || act === "alle_exportieren") return profileCall("spurprofile_exportieren", { alle: act === "alle_exportieren" });
    if (act === "importieren") {
      try {
        const im = await TM.call("spurprofile_import_pruefen", {});
        im.sel = Object.fromEntries(im.items.map((it) => [it.key, it.status !== "gleich"]));
        state.settings.profImport = im;
        Live.say(im.message || `${im.items.length} Profil(e) in der Datei – bitte wählen`);
      } catch (e) { toast(e.message, true); }
      return render();
    }
    if (act === "import_ab") { state.settings.profImport = null; return render(); }
    if (act === "import_ok") {
      const im = state.settings.profImport;
      state.settings.profImport = null;
      return profileCall("spurprofile_importieren", { pfad: im.path, auswahl: Object.keys(im.sel).filter((k) => im.sel[k]) });
    }
    return profileCall(act === "lesen" ? "spurprofil_lesen" : "spurprofil_entfernen", {});
  }
  if (t.dataset.trAct) return transferAction(t.dataset.trAct);
  if (t.dataset.profDel) return profileCall("spurprofil_spur_entfernen", { spur: t.dataset.profDel });
  if (t.dataset.mvRead) {
    try { setMove(await TM.call("audio_verschieben", { lesen: true }), true); Live.say(state.settings.move.meldung); }
    catch (e) { toast(e.message, true); }
    return render();
  }
  if (t.dataset.mvAdd) {
    state.settings.move.paare.push(["", ""]);
    render();
    const el = document.querySelector(`[data-k="mv-${state.settings.move.paare.length - 1}-0"]`);
    if (el) el.focus();
    return Live.say(`Paar ${state.settings.move.paare.length} angefügt`);
  }
  if (t.dataset.mvDel) {
    const i = Number(t.dataset.mvDel);
    state.settings.move.paare.splice(i, 1);
    if (!state.settings.move.paare.length) state.settings.move.paare.push(["", ""]);
    return saveMove(`Paar ${i + 1} entfernt`);
  }
  if (t.dataset.gridRead) {
    try { state.settings.grid = await TM.call("spurraster", { lesen: true }); Live.say(state.settings.grid.meldung); }
    catch (e) { toast(e.message, true); }
    return render();
  }
  if (t.dataset.presetAct) return presetAction(t.dataset.presetAct, Number(t.dataset.nr || 0));
  if (t.dataset.view) { state.flow = null; state.settings = null; state.view = t.dataset.view; state.list = null; render(); return loadList(); }
  if (t.dataset.sec) return openSettings(t.dataset.sec);
  if (t.dataset.reset) {
    try {
      const view = await TM.call("einstellung_zuruecksetzen", { path: t.dataset.reset });
      for (const g of state.settings.section.groups) { const i = g.fields.findIndex((x) => x.path === view.path); if (i >= 0) g.fields[i] = view; }
      Live.say(`${view.label}: Hausstandard`);
    } catch (e) { toast(e.message, true); }
    return render();
  }
  if (t.dataset.addRow) {
    const f = settingField(t.dataset.addRow);
    const row = {}; f.columns.forEach((c) => { row[c.key] = c.kind === "bool" ? false : (c.kind === "number" ? null : ""); });
    return saveSetting(f.path, (f.value || []).concat([row]));
  }
  if (t.dataset.delRow) {
    const f = settingField(t.dataset.delRow);
    return saveSetting(f.path, (f.value || []).filter((_, i) => i !== Number(t.dataset.row)));
  }
  if (t.dataset.pick) return select(t.dataset.pick, false);
  if (t.dataset.reason) { state.dialog.reason = t.dataset.reason; return render(); }
  if (t.dataset.hvassign) {
    state.returnFocus = t.dataset.k;
    state.dialog = { kind: "hvassign", havarie: t.dataset.hvassign, title: t.dataset.hvtitle, target: "" };
    return render();
  }
  if (t.dataset.roomrel) {
    state.selected = t.dataset.roomrel;
    try { await loadDetail(); } catch (e) { return toast(e.message, true); }
    if (!state.detail || state.detail.id !== t.dataset.roomrel) return;
    const info = state.detail.release_info;
    state.returnFocus = t.dataset.k;
    state.dialog = { kind: "release", id: state.detail.id, reasons: state.detail.release_reasons, reason: null, info,
                     keep: info ? info.keep_default : null, notify: true };
    return render();
  }
  if (t.dataset.bakreveal) {
    try { await TM.call("im_finder", { id: state.dialog.id, path: t.dataset.bakreveal }); } catch (e) { toast(e.message, true); }
    return;
  }
  if (t.dataset.bakopen) {
    const d = state.detail;
    state.dialog = null;
    state.flow = { kind: "reopen", id: d.id, title: d.title, step: "job" };
    state.job = null;
    render();
    try { state.job = await TM.call("sicherung_oeffnen", { id: d.id, path: t.dataset.bakopen }); state.flow.jobId = state.job.id; }
    catch (e) { toast(e.message, true); closeFlow(); }
    return render();
  }
  if (t.dataset.ntdrop || t.dataset.ntdrophv) {
    const n = (state.status && state.status.nachtrag) || {};
    const item = [...(n.eintraege || []), ...(n.konflikte || [])].find((e) => e.id === t.dataset.ntdrop);
    const what = t.dataset.ntdrophv ? `Alle Änderungen der Havarie-Session „${t.dataset.hvtitle}“` : `„${item ? item.text : "Diese Änderung"}“`;
    return askConfirm(t, "Nachtrag verwerfen", `${what} – wird nicht in Helmut nachgetragen. Helmut bleibt, wie es ist.`,
      "Verwerfen", ["nachtrag_verwerfen", t.dataset.ntdrophv ? { havarie: t.dataset.ntdrophv } : { id: t.dataset.ntdrop }]);
  }
  if (t.dataset.order) {
    state.order = t.dataset.order;
    try { localStorage.setItem("tm-order", state.order); } catch (e) { /* ohne Speicher: nur für jetzt */ }
    return loadList();
  }
  if (t.dataset.keep) { state.dialog.keep = t.dataset.keep; return render(); }
  if (t.dataset.mode && state.flow) { state.flow.mode = t.dataset.mode; return render(); }
  if (t.dataset.purpose) {
    const g = state.dialog;
    if (g.title === g.info.titles[g.purpose]) g.title = g.info.titles[t.dataset.purpose];   // Vorschlag folgt dem Zweck
    g.purpose = t.dataset.purpose; return render();
  }
  if (t.dataset.tpl) { const [group, name] = t.dataset.tpl.split("\u0001"); state.flow.choice = { group, name }; return render(); }
  if (t.dataset.answer !== undefined) {
    const q = state.dialog.question;
    if (t.dataset.answer === "cancel") return answer(null);
    if (t.dataset.answer === "missing") return answer({ ...state.dialog.answers });
    return answer(q.options[Number(t.dataset.answer)].value);
  }
  if (t.dataset.clip) { state.preview.sel = t.dataset.clip; return render(); }
  if (t.dataset.tab) { state.preview.mode = t.dataset.tab; return render(); }
  if (t.dataset.move) { return correct(state.preview.sel, t.dataset.move); }
  const act = t.dataset.act;
  if (act === "tri-ok" || act === "tri-cancel") {
    const qid = state.preview.qid;
    state.flow.step = "import"; state.preview = null; render();
    try { await TM.call("antworten", { id: qid, value: act === "tri-ok" }); } catch (e) { toast(e.message, true); }
    return;
  }
  if (act === "close") { closeDialog(); }
  else if (act === "release") {
    const info = state.detail.release_info;
    state.returnFocus = t.dataset.k;
    state.dialog = { kind: "release", id: state.detail.id, reasons: state.detail.release_reasons, reason: null, info,
                     keep: info ? info.keep_default : null, notify: true };
    render();
  }
  else if (act === "hv-assign-ok") {
    const g = state.dialog;
    try { const r = await TM.call("havarie_zuordnen", { havarie: g.havarie, id: g.target }); state.dialog = null; toast(r.text, r.konflikte.length > 0); }
    catch (e) { toast(e.message, true); }
    return refreshStatus();
  }
  else if (act === "havarie") {
    state.flow = { kind: "havarie", step: "paket", path: "", info: null, title: "", pid: "", choice: null, triage: true, video: true };
    render();
    try {
      state.templates = await TM.call("templates", {});
      if (state.templates.default) state.flow.choice = state.templates.default;
      else { const all = [...state.templates.mine, ...state.templates.found]; if (all[0]) state.flow.choice = { group: all[0].group, name: all[0].name }; }
      state.flow.triage = state.templates.triage !== false;
    } catch (e) { toast(e.message, true); }
    render();
  }
  else if (act === "hv-choose") {
    try { const r = await TM.call("ordner_waehlen", { prompt: "Übergabepaket wählen" }); if (r.path) { state.flow.path = r.path; await hvCheck(); } }
    catch (e) { toast(e.message, true); }
  }
  else if (act === "hv-check") { await hvCheck(); }
  else if (act === "hvdel") {
    const from = state.flow && state.flow.kind === "havarie" ? state.flow.path : "";
    state.flow = { kind: "hvdel", step: "auswahl", path: from || state.lastHvPath || "", info: null, bild: "", preset: "",
      usb: {}, ordner: [], title: "", sendung: "" };
    render();
    await hvdelCheck(!state.flow.path);                   // ohne Pfad: Paket der offenen Session vorschlagen
  }
  else if (act === "hvdel-choose") {
    try { const r = await TM.call("ordner_waehlen", { prompt: "Havarie-Paket wählen (AAF und Bild)" }); if (r.path) { state.flow.path = r.path; state.flow.info = null; await hvdelCheck(); } }
    catch (e) { toast(e.message, true); }
  }
  else if (act === "hvdel-check") { await hvdelCheck(); }
  else if (act === "hvdel-addfolder") {
    try { const r = await TM.call("ordner_waehlen", { prompt: "Zielordner für das Sendefile wählen" }); if (r.path && !state.flow.ordner.includes(r.path)) state.flow.ordner.push(r.path); }
    catch (e) { toast(e.message, true); }
    render();
  }
  else if (act === "login-open") { await openLogin(); }
  else if (act === "login-ok") {
    const g = state.dialog;
    try {
      state.users = await TM.call("nutzer_anmelden", { id: g.sel, standard: g.std });
      state.templatesMain = null; state.tplPickFor = null;      // Templates des neuen Nutzers
      closeDialog(); await refreshStatus(); await loadList();
      if (state.settings) { state.settings.areas = null; await openSettings(state.settings.sectionId); }   // Werte des neuen Nutzers (B32)
      Live.say(`Angemeldet: ${state.users.current ? state.users.current.name : ""}`);
    } catch (e) { g.message = e.message; render(); }
  }
  else if (act === "login-create") {
    const g = state.dialog;
    try {
      const r = await TM.call("nutzer_anlegen", { name: g.newName || "" });
      state.users = r; const neu = r.users.find((x) => x.name.toLowerCase() === (g.newName || "").trim().toLowerCase());
      if (neu) g.sel = neu.id; g.newName = ""; g.message = r.message || "";
    } catch (e) { g.message = e.message; }
    render();
  }
  else if (act === "job-cancel") {
    try { state.job = await TM.call("auftrag_abbrechen", { id: state.job && state.job.id }); Live.say("Wird abgebrochen"); } catch (e) { toast(e.message, true); }
    render();
  }
  else if (act === "show-question") { state.dialog = null; state.preview = null; onJob(state.job); }
  else if (act === "room-add") { state.settings.rooms.rows.push({ name: "", rechnername: "", id_code: "" }); render(); }
  else if (act === "room-del") { state.settings.rooms.rows.splice(Number(t.dataset.idx), 1); render(); }
  else if (act === "room-save") {
    try {
      const r = await TM.call("raumliste_speichern", { rooms: state.settings.rooms.rows });
      state.settings.rooms = { ...r, rows: r.errors.length ? state.settings.rooms.rows : r.rooms.map((x) => ({ ...x })) };
      if (!r.errors.length) await refreshStatus();
      Live.say(r.errors.length ? r.errors.join(" ") : r.message, !!r.errors.length);
    } catch (e) { toast(e.message, true); }
    render();
  }
  else if (act === "open-link") { try { await TM.call("link_oeffnen", { url: t.dataset.url }); } catch (e) { toast(e.message, true); } }
  else if (act === "tpl-up") { await tplCall("vorlage_hochladen"); }
  else if (act === "tpl-std") { await tplCall("vorlage_standard", { id: t.dataset.id }); }
  else if (act === "tpl-del") { state.settings.tplDel = t.dataset.id; render(); }
  else if (act === "tpl-del-no") { state.settings.tplDel = null; render(); }
  else if (act === "tpl-del-ok") { await tplCall("vorlage_loeschen", { id: t.dataset.id }); }
  else if (act === "tpl-adopt") {
    const sel = document.querySelector('[data-tplfound]');
    const x = state.settings.tpl.found[Number(sel ? sel.value : 0)];
    if (x) await tplCall("vorlage_uebernehmen", { group: x.group, name: x.name });
  }
  else if (act === "usr-create") { await usersCall("nutzer_anlegen", { name: state.settings.usrNew || "" }); state.settings.usrNew = ""; render(); }
  else if (act === "usr-edit") {
    const x = (state.users.users || []).find((u) => u.id === t.dataset.id);
    state.settings.usrEdit = { id: t.dataset.id, mode: t.dataset.usrmode, name: t.dataset.usrmode === "duplizieren" ? `${x.name} (Kopie)` : x.name };
    render();
  }
  else if (act === "usr-cancel") { state.settings.usrEdit = null; render(); }
  else if (act === "usr-do") {
    const ed = state.settings.usrEdit;
    const cmd = { umbenennen: "nutzer_umbenennen", duplizieren: "nutzer_duplizieren", loeschen: "nutzer_loeschen" }[ed.mode];
    await usersCall(cmd, { id: ed.id, name: ed.name });
  }
  else if (act === "set-folder") {
    try { const r = await TM.call("ordner_waehlen", { prompt: "Ordner wählen" }); if (r.path) await saveSetting(t.dataset.path, r.path); }
    catch (e) { toast(e.message, true); }
  }
  else if (act === "clear-folder") { await saveSetting(t.dataset.path, ""); }
  else if (act === "hvdel-setfolder") {
    try {
      const r = await TM.call("ordner_waehlen", { prompt: "Havarie-Ordner festlegen (bleibt gespeichert)" });
      if (r.path) { await TM.call("einstellung_setzen", { path: "export.havarie_zielordner", value: r.path }); state.flow.havarie = true; await hvdelCheck(); }
    } catch (e) { toast(e.message, true); }
  }
  else if (act === "hvdel-rm") { state.flow.ordner.splice(Number(t.dataset.idx), 1); render(); }
  else if (act === "hvdel-start") {
    const f = state.flow;
    const ziele = (f.havarie && f.info.havarie_ordner ? [{ art: "havarieordner" }] : [])
      .concat(f.desktop ? [{ art: "schreibtisch" }] : [])
      .concat(Object.entries(f.usb).filter(([mount, v]) => v && f.info.usb.some((u) => u.mount === mount)).map(([mount]) => ({ art: "usb", mount, name: (f.info.usb.find((u) => u.mount === mount) || {}).name })))
      .concat(f.ordner.map((p) => ({ art: "ordner", pfad: p })));
    try {
      state.job = await TM.call("havarie_ausliefern", { path: f.path, bild: f.bild, preset: f.preset, title: f.title, sendung: f.sendung, ziele });
      f.step = "job"; f.jobId = state.job.id; render();
    } catch (e) { toast(e.message, true); }
  }
  else if (act === "hv-start") {
    const f = state.flow;
    try {
      state.job = await TM.call("havarie_holen", { path: f.path, title: f.title, project_id: f.pid, group: f.choice.group,
        name: f.choice.name, triage: f.triage, video: f.video });
      f.step = "job"; f.jobId = state.job.id; render();
    } catch (e) { toast(e.message, true); }
  }
  else if (act === "archive") {
    state.returnFocus = t.dataset.k;
    state.dialog = { kind: "archive", data: null, sel: {} };
    render();
    try { state.dialog.data = await TM.call("archiv", {}); } catch (e) { state.dialog = null; toast(e.message, true); }
    render();
  }
  else if (act === "arch-import") {
    const g = state.dialog;
    const paths = g.data.archive.filter((_, i) => g.sel[i]).map((f) => f.path);
    try {
      await TM.call("archiv_import", { paths, ziel: t.dataset.ziel });
      state.dialog = null;
      toast(`Import gestartet: ${paths.length} Datei${paths.length > 1 ? "en" : ""}`);
    } catch (e) { toast(e.message, true); }
  }
  else if (act === "finder") {
    try { await TM.call("im_finder", { id: state.detail.id }); } catch (e) { toast(e.message, true); }
  }
  else if (act === "backups") {
    state.returnFocus = t.dataset.k;
    state.dialog = { kind: "backups", id: state.detail.id, title: state.detail.title, data: null };
    render();
    try { state.dialog.data = await TM.call("sicherungen", { id: state.dialog.id }); } catch (e) { state.dialog = null; toast(e.message, true); }
    render();
  }
  else if (act === "bak-folder") {
    try { await TM.call("im_finder", { id: state.dialog.id }); } catch (e) { toast(e.message, true); }
  }
  else if (act === "hist-clear") {
    const n = state.history ? state.history.anzahl : 0;
    return askConfirm(t, "Liste leeren", `${n} Einträge werden aus dem Verzeichnis dieses Macs entfernt. Helmut, Sessions und Dateien bleiben, wie sie sind.`,
      "Liste leeren", ["mischungen_leeren", {}], async () => { state.history = await TM.call("mischungen", {}); });
  }
  else if (act === "nt-drop-all") {
    const n = (state.status && state.status.nachtrag) || {};
    const count = (n.offen || 0) + (n.konflikte || []).length;
    return askConfirm(t, "Alle Nachträge verwerfen", `${count} Änderung${count === 1 ? "" : "en"} (offen und Konflikte, dazu Havarie-Sessions ohne Beitrag) werden nicht in Helmut nachgetragen. Helmut bleibt, wie es ist.`,
      "Alle verwerfen", ["nachtrag_verwerfen", { alle: true }]);
  }
  else if (act === "confirm-ok") {
    const g = state.dialog;
    state.dialog = null;
    try { const r = await TM.call(g.call[0], g.call[1]); toast(r.text); } catch (e) { toast(e.message, true); }
    await refreshStatus();
    if (g.after) await g.after();
    return render();
  }
  else if (act === "catchup") {
    try { const r = await TM.call("nachtragen", {}); toast(r.text, r.konflikte.length > 0); } catch (e) { toast(e.message, true); }
    return refreshStatus();
  }
  else if (act === "dup") {
    const info = state.detail.duplicate_info;
    state.returnFocus = t.dataset.k;
    state.dialog = { kind: "dup", id: state.detail.id, info, purpose: info.purposes[0], title: info.titles[info.purposes[0]], keep: info.keep_default, take: false };
    render();
  }
  else if (act === "dup-ok") {
    const g = state.dialog;
    try {
      const d = await TM.call("duplizieren", { id: g.id, title: g.title.trim(), keep: g.keep && g.info.can_keep, take: g.take });
      state.dialog = null; state.selected = d.id; state.detail = d;
      toast(`Duplikat angelegt: „${d.title}“`);
      loadList();
    } catch (e) { toast(e.message, true); }
  }
  else if (act === "release-ok") {
    try {
      const g = state.dialog;
      state.detail = await TM.call("release", { id: g.id, reason: g.reason, keep: g.keep, notify: g.notify });
      state.dialog = null;
      const h = state.detail.hinweise || [];
      toast(h.length ? "Freigegeben. " + h.join(" ") : "Freigegeben", h.length > 0);
      if (state.flow) closeFlow(); else loadList();
    } catch (e) { state.dialog = null; toast(e.message, true); }
  }
  else if (act === "fetch") { openFlow(); }
  else if (act === "settings") { openSettings(); }
  else if (act === "voc-save") {
    try { state.settings.voc = await TM.call("vocaster_routing_sichern", {}); Live.say(state.settings.voc.meldung || "Routing gesichert"); }
    catch (e) { toast(e.message, true); }
    render();
  }
  else if (act === "launcher") {
    try {
      const r = await TM.call("launcher_erzeugen", {});
      const old = r.alte ? ` ${r.alte} ältere „TM …“-Starter liegen noch daneben – sie funktionieren weiter, bis die Stream-Deck-Tasten auf „Ottilie …“ umgestellt sind.` : "";
      toast(`${r.anzahl} Launcher erzeugt: ${r.ordner}.${old}`);
    }
    catch (e) { toast(e.message, true); }
  }
  else if (act === "deliver") { openDeliver(); }
  else if (act === "del-start") { startDeliver(); }
  else if (act === "flow-cancel" || act === "flow-back") { closeFlow(); }
  else if (act === "flow-start") { startFetch(); }
  else if (act === "reopen-new") { openFlow(true); }
  else if (act === "reopen-start") { startReopen(); }
  else if (act === "open-job") {
    state.flow = flowForJob(state.job);
    render();
    if (state.job.state === "frage") onJob(state.job);            // offene Frage gleich zeigen
  }
}

async function correct(key, category) {
  if (!key) return;
  const [track, index] = [key.slice(0, key.lastIndexOf("#")), Number(key.slice(key.lastIndexOf("#") + 1))];
  try {
    state.preview.view = await TM.call("triage_korrigieren", { track, index, category });
    const c = state.preview.view.rows.find((r) => r.key === key);
    Live.say(`Verschoben nach ${c ? c.label : category}, Zielspur ${c ? c.targets : ""}`);
  } catch (e) { toast(e.message, true); }
  render();
}

document.addEventListener("change", async (ev) => {
  const t = ev.target;
  if (t.dataset.hvdelField) { state.flow[t.dataset.hvdelField] = t.value; if (t.dataset.hvdelField === "path") state.flow.info = null; return render(); }
  if (t.dataset.hvdelBild !== undefined) { state.flow.bild = t.value; return render(); }
  if (t.dataset.hvdelUsb) { state.flow.usb[t.dataset.hvdelUsb] = t.checked; return render(); }
  if (t.dataset.hvdelDesktop) { state.flow.desktop = t.checked; return render(); }
  if (t.dataset.hvdelHavarie) { state.flow.havarie = t.checked; return render(); }
  if (t.dataset.maintpl) { state.tplPick = t.value; return; }
  if (t.dataset.room !== undefined && t.tagName === "SELECT") { state.settings.rooms.rows[Number(t.dataset.room)][t.dataset.rkey] = t.value; return; }
  if (t.dataset.login === "user") { state.dialog.sel = t.value; state.dialog.std = state.users.standard === t.value; return render(); }
  if (t.dataset.login === "std") { state.dialog.std = t.checked; return render(); }
  if (t.dataset.profTpl) {
    const [g, n] = t.value.split("\u0001");
    try { state.settings.prof = await TM.call("spurprofile", { gruppe: g || "", name: n || "" }); } catch (e) { toast(e.message, true); }
    state.settings.profConfirm = false;
    return render();
  }
  if (t.dataset.profCell) {
    const v = t.type === "checkbox" ? t.checked : t.value;
    await profileCall("spurprofil_setzen", { spur: t.dataset.profTrack, spalte: t.dataset.profCell, wert: v });
    return Live.say(`${t.getAttribute("aria-label")}: ${t.type === "checkbox" ? (v ? "an" : "aus") : v}`);
  }
  if (t.dataset.trPick) { state.settings.transfer.import.sel[t.dataset.trPick] = t.checked; return render(); }
  if (t.dataset.trAll) { const im = state.settings.transfer.import; im.choices.forEach((c) => { im.sel[c.id] = t.checked; }); return render(); }
  if (t.dataset.profImp) { state.settings.profImport.sel[t.dataset.profImp] = t.checked; return render(); }
  if (t.dataset.profSwitch) return profileCall("spurprofil_schalter", { schalter: t.dataset.profSwitch, wert: t.type === "checkbox" ? t.checked : t.value });
  if (t.dataset.mvRow !== undefined) {
    const pair = state.settings.move.paare[Number(t.dataset.mvRow)];
    pair[Number(t.dataset.mvSide)] = t.value;
    return saveMove(pair[0] && pair[1] ? `${pair[0]} nach ${pair[1]} gespeichert` : "");
  }
  if (t.dataset.gridCol) {
    try {
      state.settings.grid = await TM.call("spurraster_setzen", { spalte: t.dataset.gridCol, spur: t.dataset.gridTrack, an: t.checked });
      Live.say(`${t.getAttribute("aria-label")}: ${t.checked ? "an" : "aus"}`);
      await reloadSection();
    } catch (e) { t.checked = !t.checked; toast(e.message, true); }
    return render();
  }
  if (t.dataset.wtRoom) {
    try { state.settings.wt = await TM.call("webtrigger_adressen", { raum: t.value }); } catch (e) { toast(e.message, true); }
    return render();
  }
  if (t.dataset.presetName) {
    try { state.settings.presets = (await TM.call("preset_umbenennen", { nr: Number(t.dataset.presetName), name: t.value })).presets; Live.say("Preset umbenannt"); }
    catch (e) { toast(e.message, true); }
    return render();
  }
  if (t.dataset.set) {
    const v = readInput(t);
    if (v && v.__fehler) { const f = settingField(t.dataset.set); f.errors = [v.__fehler]; return render(); }
    return saveSetting(t.dataset.set, v);
  }
  if (t.dataset.cell) {
    const f = settingField(t.dataset.cell);
    const rows = (f.value || []).map((r) => ({ ...r }));
    const v = readInput(t);
    if (v && v.__fehler) { f.errors = [v.__fehler]; return render(); }
    rows[Number(t.dataset.row)][t.dataset.col] = v;
    return saveSetting(f.path, rows);
  }
  if (t.dataset.catFor) { state.preview.sel = t.dataset.catFor; return correct(t.dataset.catFor, t.value); }
  if (t.dataset.trifilter !== undefined) { state.preview.filter = t.checked; return render(); }
  if (t.dataset.opt) { state.flow[t.dataset.opt] = t.checked; render(); }
  if (t.dataset.relnotify) { state.dialog.notify = t.checked; }
  if (t.dataset.dupopt) { state.dialog[t.dataset.dupopt] = t.checked; }
  if (t.dataset.arch !== undefined && state.dialog && state.dialog.kind === "archive") { state.dialog.sel[t.dataset.arch] = t.checked; render(); }
  if (t.dataset.openopt) { state.flow.openOpts[t.dataset.openopt] = t.value; render(); }
  if (t.dataset.hvtarget) { state.dialog.target = t.value; render(); }
  if (t.dataset.hvtpl) { const [g, n] = t.value.split("\u0001"); state.flow.choice = { group: g, name: n }; render(); }
  if (t.dataset.miss) { state.dialog.answers[t.dataset.miss] = t.value; }
});

let searchTimer = null;
document.addEventListener("input", (ev) => {
  if (ev.target.dataset && ev.target.dataset.login === "new") { state.dialog.newName = ev.target.value; return; }
  if (ev.target.dataset && ev.target.dataset.usrnew) { state.settings.usrNew = ev.target.value; return; }
  if (ev.target.dataset && ev.target.dataset.room !== undefined && ev.target.tagName === "INPUT") { state.settings.rooms.rows[Number(ev.target.dataset.room)][ev.target.dataset.rkey] = ev.target.value; return; }
  if (ev.target.dataset && ev.target.dataset.usrname) { state.settings.usrEdit.name = ev.target.value; return; }
  if (ev.target.dataset && ev.target.dataset.delTrack) { state.flow.sel[ev.target.dataset.delTrack] = ev.target.checked; return render(); }
  if (ev.target.id === "hv-path") { state.flow.path = ev.target.value; return; }
  if (ev.target.id === "hv-title") { state.flow.title = ev.target.value; return; }
  if (ev.target.id === "hv-id") { state.flow.pid = ev.target.value; return; }
  if (ev.target.id === "dup-title") {
    state.dialog.title = ev.target.value;
    const ok = document.querySelector('[data-act="dup-ok"]');
    if (ok) { ok.disabled = !ev.target.value.trim(); ok.setAttribute("aria-disabled", String(ok.disabled)); }
    return;
  }
  if (ev.target.id === "preset-new") { state.settings.newPreset = ev.target.value; return; }
  if (ev.target.id !== "q") return;
  state.query = ev.target.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadList, 250);
});

// Rückfrage vor einer endgültigen Aktion; „OK“ ruft `call` = [Befehl, Argumente] auf, danach optional `after()`
function askConfirm(t, title, text, ok, call, after) {
  state.returnFocus = t.dataset.k;
  state.dialog = { kind: "confirm", title, text, ok, call, after };
  render();
}

function closeDialog() {
  const back = state.returnFocus;
  state.dialog = null;
  render();
  const el = back && document.querySelector(`[data-k="${CSS.escape(back)}"]`);
  if (el) el.focus();
}

document.addEventListener("keydown", (ev) => {
  const a = document.activeElement;
  const dlg = document.querySelector(".dialog");
  if (dlg) {
    if (ev.key === "Escape") {
      ev.preventDefault();
      if (state.dialog.kind === "question") {
        // Escape heißt „nein / abbrechen“ – nie die riskante Option (Review 09.10.2026: vorher „Trotzdem einfügen“)
        const opts = state.dialog.question.options || [];
        const cancel = opts.findIndex((o) => o.value === false);
        if (cancel >= 0) return answer(opts[cancel].value);
        if (!opts.length) return answer(null);        // Fragen ohne Optionen (fehlende Spuren): null = nicht importieren
        return;                                       // keine Abbrechen-Option: Dialog bleibt, Mischer entscheidet
      }
      return closeDialog();
    }
    if (ev.key === "Tab") {                                 // Fokus bleibt im Dialog
      const items = focusables(dlg);
      if (!items.length) return;
      const i = items.indexOf(a);
      if (ev.shiftKey && (i <= 0)) { ev.preventDefault(); items[items.length - 1].focus(); }
      else if (!ev.shiftKey && i === items.length - 1) { ev.preventDefault(); items[0].focus(); }
    }
  }
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "f" && !state.flow) {
    ev.preventDefault();
    const q = document.getElementById("q"); if (q) q.focus();
    return;
  }
  if (a && a.dataset && a.dataset.pick && (ev.key === "ArrowDown" || ev.key === "ArrowUp")) {
    ev.preventDefault();
    const rows = [...document.querySelectorAll("[data-pick]")];
    const i = rows.indexOf(a) + (ev.key === "ArrowDown" ? 1 : -1);
    if (rows[i]) select(rows[i].dataset.pick, true);
  }
  if (a && a.dataset && a.dataset.pick && ev.key === "Enter") {
    ev.preventDefault();
    const b = document.querySelector("[data-act='fetch']:not([disabled])") || document.querySelector(".detail-foot button:not([disabled])");
    if (b) b.focus();
  }
  if (state.preview && state.flow && state.flow.step === "triage" && !dlg) {
    if (/^[1-4]$/.test(ev.key) && state.preview.sel && !(a && a.tagName === "SELECT")) {
      ev.preventDefault();
      return correct(state.preview.sel, state.preview.view.categories[Number(ev.key) - 1].value);
    }
    if (a && a.dataset && a.dataset.lane && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(ev.key)) {
      ev.preventDefault();
      const lane = [...document.querySelectorAll(`[data-lane="${CSS.escape(a.dataset.lane)}"]`)];
      let next = null;
      if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") next = lane[lane.indexOf(a) + (ev.key === "ArrowRight" ? 1 : -1)];
      else {
        const lanes = [...document.querySelectorAll(".lane")].filter((l) => l.querySelector("[data-clip]"));
        const cur = lanes.indexOf(a.closest(".lane"));
        const target = lanes[cur + (ev.key === "ArrowDown" ? 1 : -1)];
        if (target) {
          const x = a.getBoundingClientRect().left;
          next = [...target.querySelectorAll("[data-clip]")].reduce((best, b) =>
            !best || Math.abs(b.getBoundingClientRect().left - x) < Math.abs(best.getBoundingClientRect().left - x) ? b : best, null);
        }
      }
      if (next) { next.focus(); next.click(); }
      return;
    }
  }
  if (a && a.dataset && a.dataset.sec && ["ArrowDown", "ArrowUp"].includes(ev.key)) {
    ev.preventDefault();
    const tabs = [...document.querySelectorAll("[data-sec]")];
    const n = tabs[tabs.indexOf(a) + (ev.key === "ArrowDown" ? 1 : -1)];
    if (n) { n.focus(); n.click(); }
    return;
  }
  if (a && a.getAttribute && a.getAttribute("role") === "radio" && ["ArrowDown", "ArrowUp"].includes(ev.key)) {
    ev.preventDefault();
    const group = a.closest("[role='radiogroup']");
    const items = [...group.querySelectorAll("[role='radio']:not([disabled])")];
    const n = items[items.indexOf(a) + (ev.key === "ArrowDown" ? 1 : -1)];
    if (n) { n.focus(); n.click(); }
  }
});

// ── Autopilot nur für den Selbsttest (index.html#auto=holen:<id>) ─────────────
// Klickt den Ablauf so durch wie ein Mischer; ohne diesen Schalter ohne Wirkung.
async function autopilot(spec) {
  const [kind, id] = spec.split(":");
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (cond, ms) => { const t0 = Date.now(); while (!cond()) { if (Date.now() - t0 > ms) throw new Error("Zeit abgelaufen"); await wait(100); } };
  try {
    if (kind !== "holen") throw new Error("Autopilot kennt nur holen");
    await select(id, true);
    document.querySelector('[data-act="fetch"]').click();
    await until(() => state.flow && state.templates, 10000);
    const go = document.querySelector('[data-act="flow-start"]');
    if (go.disabled) throw new Error("kein Template vorgewählt");
    go.click();
    while (true) {
      await until(() => state.job && (["fertig", "fehler", "abgebrochen"].includes(state.job.state)
        || (state.job.state === "frage" && state.job.question)), 600000);
      if (state.job.state !== "frage") break;
      const q = state.job.question;
      await wait(300);
      if (q.kind === "triage_vorschau") document.querySelector('[data-act="tri-ok"]').click();
      else if (q.kind === "fehlende_spuren") document.querySelector('[data-answer="missing"]').click();
      else document.querySelector('.dialog .btn.pri').click();
      await until(() => !state.job || state.job.state !== "frage" || state.job.question.id !== q.id, 10000);
    }
    TM.call("ready", { auto: state.job });
  } catch (e) {
    TM.call("ready", { auto: { state: "fehler", error: "Autopilot: " + e.message } });
  }
}

// ── Start ───────────────────────────────────────────────────────────────────
async function refreshStatus(quiet) {
  try { state.status = await TM.call("status"); state.error = null; state.theme = null; } catch (e) { state.error = e.message; }
  render(quiet);
}

async function start() {
  render();
  try { await TM.call("ping"); } catch (e) { state.error = e.message; render(); return; }
  await refreshStatus();
  await loadList();
  try { state.job = await TM.call("auftrag"); } catch (e) { /* ohne Auftrag */ }
  try { state.users = await TM.call("nutzer"); } catch (e) { /* Nutzer nicht lesbar – dann ohne Anmeldefrage */ }
  await loadRooms();
  render();
  const auto0 = location.hash.startsWith("#auto=");
  if (state.users && state.users.needs_login && !auto0) await openLogin();   // beim Start fragen (Jens 04.10.2026)
  const auto = location.hash.startsWith("#auto=") ? decodeURIComponent(location.hash.slice(6)) : null;
  if (auto) autopilot(auto); else TM.call("ready").catch(() => {});
  let ticking = false;                     // Takt nicht stapeln: hängt Helmut, läuft die vorige Abfrage noch (Review A13)
  setInterval(async () => {
    if (ticking) return;
    ticking = true;
    const calls = [refreshStatus(true)];
    // Liste und Räume nur, wenn sie zu sehen sind – nicht hinter den Einstellungen (Review B32)
    if (!state.dialog && !state.flow && !state.settings) { calls.push(loadList(true)); if (state.view !== "rooms") calls.push(loadRooms()); }
    try { await Promise.allSettled(calls); } finally { ticking = false; }
  }, 15000);
}
start();
