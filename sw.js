// Service Worker fürs PWA-Offline-Fallback: cached nur die statische
// Oberflächen-Hülle (HTML/Icons/Banner/QR-Bibliothek), kein Audio und keine
// /api/*-Antworten -- die sind Live-Zustand (aktueller Sender, Hörerzahlen,
// Bullshitometer) und würden aus dem Cache beantwortet einen eingefrorenen
// Stand vorgaukeln. Ohne funktionierendes Backend zeigt die App dann
// bestenfalls "Verbindung zum Server verloren" statt eines leeren Tabs.
const CACHE_NAME = "radiozapper-shell-v1";
const SHELL_URLS = ["/", "/manifest.json", "/icon-192.png", "/icon-512.png", "/qrcode.js", "/radiozapper.webp"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  if (req.method !== "GET") return;
  // Icecast-Stream läuft auf einem eigenen Port/Origin (siehe CLAUDE.md,
  // "IcecastOutput besteht über Senderwechsel hinweg") -- fetch-Events
  // feuern trotzdem dafür, weil die Seite den Service Worker kontrolliert.
  // Ein dauerhaft offener Audio-Stream über respondWith()/cache.put() zu
  // schleifen wäre Speicher- und Latenz-Selbstmord, deshalb hier komplett
  // ignorieren.
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;
  if (req.headers.get("range")) return;

  event.respondWith(
    fetch(req)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
        return response;
      })
      .catch(() => caches.match(req).then((cached) => cached || caches.match("/")))
  );
});
