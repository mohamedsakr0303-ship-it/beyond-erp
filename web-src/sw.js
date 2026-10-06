/* Beyond ERP service worker: lets the installed app (phone / desktop "Install app") open without a connection.
   Network first — so every deploy is picked up at once — and the last copy of each file is kept for offline use.
   Only this site's own files are handled; the workspace (Supabase) is never cached. */
const CACHE = "beyond-erp";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  const r = e.request, url = new URL(r.url);
  if (r.method !== "GET" || url.origin !== self.location.origin || url.pathname.includes("/downloads/")) return;
  e.respondWith(fetch(r).then(res => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(r, copy)); }
    return res;
  }).catch(() => caches.match(r, { ignoreSearch: true }).then(hit => hit || (r.mode === "navigate" ? caches.match("./") : Response.error()))));
});
// chat notifications: tapping one opens (or focuses) the app on that conversation
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const href = (e.notification.data && e.notification.data.href) || "#/chat";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
    const win = list.find(c => c.url.startsWith(self.registration.scope));
    if (win) { win.postMessage({ href }); return win.focus(); }
    return self.clients.openWindow(self.registration.scope + href);
  }));
});
