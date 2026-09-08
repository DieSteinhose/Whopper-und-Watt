// Service Worker: App-Huelle offline halten, Daten je nach Betriebsart behandeln.
//
// Zwei Strategien. Die Huelle (HTML, CSS, JS, Leaflet, Icons, Datenbestand)
// aendert sich nur beim Deploy und kommt aus dem Cache. Antworten unter api/
// sind zeitabhaengig und kommen aus dem Netz, mit dem Cache als Notnagel.
//
// Alle Pfade relativ zum Scope: auf GitHub Pages liegt die App unter einem
// Unterverzeichnis, absolute Pfade zeigten dort ins Leere.

// Muss sich bei jeder Aenderung an der Huelle aendern. Der Browser holt einen
// Service Worker nur neu, wenn sich dessen eigene Bytes unterscheiden; bleibt
// die Zeile stehen, liefert eine bestehende Installation ewig das alte app.js
// aus dem Cache aus, egal was deployt wurde.
const VERSION = 'whopper-watt-v4';
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
        //
        // data/spots-vegan.json wird bewusst NICHT mitinstalliert: gepackt gut
        // 5 MB, und wer nur nach Burger King sucht, braucht sie nie. Sie landet
        // beim ersten Abruf im Cache (siehe fetch-Handler) und ist ab dann
        // ebenfalls offline da.
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

  // Der Datenbestand wird beim Abruf mit in den Cache gelegt. Das betrifft vor
  // allem spots-vegan.json, die zu gross fuer die Vorab-Installation ist: nach
  // der ersten veganen Suche liegt sie da und die App bleibt offline benutzbar.
  if (url.pathname.includes('/data/')) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put(request, copy));
        }
        return response;
      })),
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
