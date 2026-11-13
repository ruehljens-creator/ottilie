"use strict";
// Service Worker: nur die App-Hülle (HTML, CSS, JS, Symbol) zwischenspeichern – nie Chat, Raumstatus oder Antworten
// der Zentrale (Vorgabe: kein Chat auf Platte). API und WebSocket gehen immer ans Netz.
const CACHE = "ottilie-zentrale-huelle-2";
const SHELL = ["./", "web/app.css", "web/app.js", "web/ottilie-192.png", "web/manifest.webmanifest"];

self.addEventListener("install", (ev) => {
  ev.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (ev) => {
  ev.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (ev) => {
  const url = new URL(ev.request.url);
  const scope = new URL(self.registration.scope);
  const rel = url.pathname.slice(scope.pathname.length);
  const shell = ev.request.method === "GET" && url.origin === scope.origin && (rel === "" || SHELL.includes(rel));
  if (!shell) return;                                   // alles andere: nur Netz, nichts speichern
  ev.respondWith(fetch(ev.request).then((r) => {
    const copy = r.clone();
    if (r.ok) caches.open(CACHE).then((c) => c.put(ev.request, copy));
    return r;
  }).catch(() => caches.match(ev.request)));
});
