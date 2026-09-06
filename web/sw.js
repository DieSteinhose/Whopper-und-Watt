// Service Worker: App-Huelle offline halten, Daten immer frisch holen.
//
// Bewusst zwei Strategien. Die Huelle (HTML, CSS, JS, Leaflet, Icons) aendert sich
// nur beim Deploy und kommt aus dem Cache. Antworten unter /api sind zeitabhaengig
// und kommen aus dem Netz, mit dem Cache nur als Notnagel, wenn es offline ist.

const VERSION = 'whopper-watt-v1';
const SHELL = [
  '/',
  '/index.html',
  '/styles.css',
  '/app.js',
  '/opening-hours.js',
  '/manifest.webmanifest',
  '/vendor/leaflet.js',
  '/vendor/leaflet.css',
  '/vendor/images/marker-icon.png',
  '/vendor/images/marker-icon-2x.png',
  '/vendor/images/marker-shadow.png',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== VERSION).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // Kartenkacheln regelt der Browser

  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || offlineAnswer())),
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request)),
  );
});

function offlineAnswer() {
  return new Response(
    JSON.stringify({ ok: false, error: 'Offline und nichts im Cache für diese Abfrage.' }),
    { status: 503, headers: { 'Content-Type': 'application/json' } },
  );
}
