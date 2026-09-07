// Service Worker: App-Huelle offline halten, Daten je nach Betriebsart behandeln.
//
// Zwei Strategien. Die Huelle (HTML, CSS, JS, Leaflet, Icons, Datenbestand)
// aendert sich nur beim Deploy und kommt aus dem Cache. Antworten unter api/
// sind zeitabhaengig und kommen aus dem Netz, mit dem Cache als Notnagel.
//
// Alle Pfade relativ zum Scope: auf GitHub Pages liegt die App unter einem
// Unterverzeichnis, absolute Pfade zeigten dort ins Leere.

const VERSION = 'whopper-watt-v2';
const SHELL = [
  './',
  'index.html',
  'styles.css',
  'app.js',
  'geo.js',
  'opening-hours.js',
  'server-backend.js',
  'local-search.js',
  'manifest.webmanifest',
  'vendor/leaflet.js',
  'vendor/leaflet.css',
  'vendor/images/marker-icon.png',
  'vendor/images/marker-icon-2x.png',
  'vendor/images/marker-shadow.png',
  'icons/icon-192.png',
  'icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then(async (cache) => {
        await cache.addAll(SHELL);
        // Nur im Betrieb ohne Server vorhanden. Fehlt die Datei, ist das kein Grund,
        // die ganze Installation scheitern zu lassen.
        await cache.add('data/spots.json').catch(() => {});
      })
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
  if (url.origin !== self.location.origin) return; // Kacheln, Nominatim und OSRM regelt der Browser

  if (url.pathname.includes('/api/')) {
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
