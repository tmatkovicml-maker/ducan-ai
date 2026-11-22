// Minimal service worker: caches static assets only. Pages always come from the network,
// so approval data is never served stale.
const CACHE = "ducan-static-v1";
const ASSETS = ["/static/app.css", "/static/htmx.min.js"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS))));
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method === "GET" && url.pathname.startsWith("/static/")) {
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
  }
});
