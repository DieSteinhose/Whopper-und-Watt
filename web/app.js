import { evaluateOpeningHours } from './opening-hours.js';
import { decodePolyline } from './geo.js';
import { serverBackend } from './server-backend.js';
import { localBackend } from './local-search.js';

// Zwei Betriebsarten aus einem Quellstand: mit Server (SQLite, Cache, eigene
// Instanz) oder ohne (GitHub Pages, alles im Browser). Erkannt wird es daran,
// ob es den Server ueberhaupt gibt.
let backend = serverBackend;

async function pickBackend() {
  // Der eigene Server schickt einen Kennungs-Header mit. Fehlt er, liegt die App
  // auf einer reinen Dateiablage wie GitHub Pages und rechnet selbst.
  let hasServer = false;
  try {
    const probe = await fetch('.', { method: 'HEAD' });
    hasServer = probe.headers.get('X-Whopper-Backend') === 'server';
  } catch {
    hasServer = false;
  }
  backend = hasServer ? serverBackend : localBackend;
  try {
    return await backend.meta();
  } catch (error) {
    // Server da, aber Datenbank kaputt: lieber lokal weitermachen als gar nicht.
    if (backend === serverBackend) {
      backend = localBackend;
      return localBackend.meta();
    }
    throw error;
  }
}

const state = {
  mode: 'radius',
  // Burger King ist voreingestellt, alles andere waehlt man dazu. Alles ist
  // abwaehlbar, auch Burger King.
  kinds: ['bk'],
  radiusKm: 25,
  corridorM: 3000,
  gapM: 300,
  onlyEnbw: true,
  departure: null, // null bedeutet "jetzt"
  spots: [],
  route: null,
  center: null,
  loading: false,
  searched: false,
  tab: 'list',
  queryMs: null,
  rerun: null,
};

const view = {
  status: document.getElementById('status'),
  banner: document.getElementById('banner'),
  list: document.getElementById('list'),
  map: document.getElementById('map'),
  options: document.getElementById('options'),
  optionsToggle: document.getElementById('optionsToggle'),
  radiusForm: document.getElementById('radiusForm'),
  routeForm: document.getElementById('routeForm'),
  kindChips: document.getElementById('kindChips'),
  radiusChips: document.getElementById('radiusChips'),
  corridorChips: document.getElementById('corridorChips'),
  departureLabel: document.getElementById('departureLabel'),
  departureTime: document.getElementById('departureTime'),
  placeInput: document.getElementById('placeInput'),
  startInput: document.getElementById('startInput'),
  destinationInput: document.getElementById('destinationInput'),
  planInput: document.getElementById('planInput'),
  installButton: document.getElementById('installButton'),
};

let map = null;
let layer = null;

// ---- Hilfen ---------------------------------------------------------------

const clock = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' });
const weekday = new Intl.DateTimeFormat('de-DE', { weekday: 'short' });

function formatMoment(moment, reference) {
  const time = clock.format(moment);
  return moment.toDateString() === reference.toDateString()
    ? time
    : `${weekday.format(moment)} ${time}`;
}

function formatMeters(meters) {
  if (meters < 1000) return `${Math.round(meters)} m`;
  if (meters < 10000) return `${(meters / 1000).toFixed(1).replace('.', ',')} km`;
  return `${Math.round(meters / 1000)} km`;
}

function departureTime() {
  return state.departure ? new Date(state.departure) : new Date();
}

function arrivalOf(spot) {
  const start = departureTime();
  if (spot.routeSeconds == null) return start;
  return new Date(start.getTime() + spot.routeSeconds * 1000);
}

function setBanner(message) {
  view.banner.textContent = message || '';
  view.banner.hidden = !message;
}

const KIND_LABELS = {
  bk: 'Burger King',
  subway: 'Subway',
  vegan: 'Vegane Optionen',
  vegan_only: 'Rein vegan',
};

// Obergrenzen fuer die Anzeige. Mit den veganen Kategorien liefert eine Suche
// ueber 100 km um Berlin gemessen 1934 Treffer. Ungebremst waren das rund 20000
// Karten in der Liste und, weil Leaflet je Marker ein DOM-Element anlegt,
// zehntausende Marker auf der Karte: der Kartenreiter liess sich danach nicht
// mehr oeffnen. Beide Listen sind bereits sortiert, nach Entfernung oder nach
// Strecke ab Start, "die ersten N" ist hier also die sinnvolle Auswahl.
const LIST_LIMIT = 200;
const MAP_LIMIT = 250;

/**
 * Zeichen fuer die Karte. Die Kette geht vor, denn sie sagt mehr: ein Burger
 * King zaehlt zwar auch als vegane Option, aber als Burger King erkennt man ihn.
 */
function glyphFor(spot) {
  if (spot.brand === 'bk') return '🍔';
  if (spot.brand === 'subway') return '🥪';
  return spot.veganOnly ? '🌱' : '🌿';
}

function pinKind(spot) {
  if (spot.brand) return spot.brand;
  return spot.veganOnly ? 'vegan-only' : 'vegan';
}

/**
 * "15189 Lokale, davon 952 Burger King, 788 Subway, 509 rein vegan".
 *
 * Die Kategorien ueberschneiden sich, ein Burger King steckt in zweien. Die
 * Zahl fuer "vegane Optionen" bleibt deshalb weg: sie ist per Konstruktion die
 * Gesamtzahl, denn im Bestand landet nur, was entweder Kette oder vegan ist.
 * Nebeneinandergestellt sahe sie aus wie ein Rechenfehler.
 */
function describeStock(meta) {
  const total = `${meta.stores ?? '?'} Lokale`;
  try {
    const perKind = JSON.parse(meta.stores_per_kind ?? '{}');
    const parts = ['bk', 'subway', 'vegan_only']
      .filter((key) => perKind[key])
      .map((key) => `${perKind[key]} ${KIND_LABELS[key]}`);
    if (parts.length) return `${total}, davon ${parts.join(', ')}`;
  } catch {
    // Ein kaputter Zaehler ist kein Grund, den Tooltip zu verlieren.
  }
  return total;
}

function kindLabels() {
  const chosen = state.kinds.map((key) => KIND_LABELS[key] ?? key);
  if (chosen.length === 0) return 'Nichts ausgewählt';
  return chosen.join(' und ');
}

// Ohne Auswahl gibt es nichts zu suchen. Das ist ein erlaubter Zustand, denn
// alle Kategorien sollen abwaehlbar sein, aber er braucht eine Ansage.
function requireKind() {
  if (state.kinds.length === 0) {
    throw new Error('Nichts ausgewählt. Mindestens eine Kategorie antippen.');
  }
}

function pressed(container, attribute, value) {
  container.querySelectorAll(`[${attribute}]`).forEach((button) => {
    button.setAttribute('aria-pressed', String(button.getAttribute(attribute) === String(value)));
  });
}

// ---- Server ---------------------------------------------------------------

function searchParams() {
  return {
    kinds: state.kinds,
    radiusKm: state.radiusKm,
    corridorM: state.corridorM,
    gapM: state.gapM,
    onlyEnbw: state.onlyEnbw,
  };
}

async function searchRadius() {
  const query = view.placeInput.value.trim();
  await withLoading(async () => {
    requireKind();
    let center;
    if (query) {
      center = await backend.geocode(query);
    } else {
      const position = await currentPosition();
      center = { lat: position.coords.latitude, lon: position.coords.longitude, label: 'Mein Standort' };
    }
    await runRadius(center);
  });
}

// Getrennt von der Eingabe, damit ein Filterwechsel die Suche wiederholen kann,
// ohne Ort und Route erneut aufzuloesen. Bei einer geladenen GPX-Route gibt es
// gar keine Eingabefelder mehr, aus denen sich das rekonstruieren liesse.
async function runRadius(center) {
  const result = await backend.radiusSearch(center, searchParams());
  state.center = center;
  state.route = null;
  state.spots = result.spots;
  state.queryMs = result.queryMs;
  state.rerun = () => withLoading(async () => {
    requireKind();
    await runRadius(center);
  });
}

async function searchRoute(waypoints, label) {
  await withLoading(async () => {
    requireKind();
    const request = {};
    if (waypoints) {
      request.waypoints = waypoints;
      request.label = label;
    } else {
      const start = view.startInput.value.trim();
      const destination = view.destinationInput.value.trim();
      if (!destination) throw new Error('Ziel fehlt.');
      if (start) {
        request.places = [start, destination];
      } else {
        const position = await currentPosition();
        const target = await backend.geocode(destination);
        request.waypoints = [[position.coords.latitude, position.coords.longitude], [target.lat, target.lon]];
        request.label = `Mein Standort nach ${target.label}`;
      }
    }
    await runRoute(request);
  });
}

async function runRoute(request) {
  const result = await backend.routeSearch(request, searchParams());
  state.route = result.route;
  state.center = null;
  state.spots = result.spots;
  state.queryMs = result.queryMs;
  state.rerun = () => withLoading(async () => {
    requireKind();
    await runRoute(request);
  });
  if (!result.route.hasMeasuredDurations) {
    setBanner('Für diese Route liegen keine Fahrzeiten vor, die Ankunftszeiten sind geschätzt.');
  }
}

/** Filterwechsel nach einer Suche: dieselbe Suche noch einmal, mit neuen Werten. */
function rerunIfSearched() {
  if (state.searched && state.rerun) state.rerun();
  else renderStatus();
}

async function withLoading(task) {
  state.loading = true;
  setBanner('');
  render();
  try {
    await task();
    state.searched = true;
  } catch (error) {
    state.spots = [];
    setBanner(error.message);
  } finally {
    state.loading = false;
    render();
  }
}

function currentPosition() {
  return new Promise((resolve, reject) => {
    // Ohne HTTPS gibt der Browser gar keinen Standort heraus, egal was der Nutzer erlaubt.
    if (!window.isSecureContext) {
      reject(new Error('Standort gibt es nur über HTTPS. Ort bitte eintippen.'));
      return;
    }
    if (!navigator.geolocation) {
      reject(new Error('Dieses Gerät liefert keinen Standort. Ort bitte eintippen.'));
      return;
    }
    navigator.geolocation.getCurrentPosition(resolve, () => {
      reject(new Error('Kein Standort verfügbar. Freigabe prüfen oder Ort eintippen.'));
    }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 120000 });
  });
}

// ---- Darstellung ----------------------------------------------------------

function render() {
  renderStatus();
  renderList();
  renderMap();
}

function renderStatus() {
  if (state.loading) {
    view.status.textContent = 'Suche läuft ...';
    return;
  }
  if (!state.searched) {
    view.status.textContent = `${kindLabels()} neben der Ladesäule`;
    return;
  }
  const where = state.route ? state.route.label : (state.center?.label ?? '');
  const speed = state.queryMs != null ? ` · ${state.queryMs} ms` : '';
  const capped = state.spots.length > LIST_LIMIT ? ` (${LIST_LIMIT} gezeigt)` : '';
  view.status.textContent = `${state.spots.length} Kombis${capped} · ${where}${speed}`;
}

function renderList() {
  if (state.loading) {
    view.list.innerHTML = '<div class="empty"><strong>Suche läuft ...</strong>Die Antwort kommt aus der lokalen Datenbank.</div>';
    return;
  }
  if (!state.searched) {
    view.list.innerHTML = '<div class="empty"><strong>Laden und Essen in einem Stopp</strong>' +
      'Umkreis: Ort eingeben oder Standort freigeben. Route: Start und Ziel eintippen, oder einen Plan laden.</div>';
    return;
  }
  if (state.spots.length === 0) {
    view.list.innerHTML = '<div class="empty"><strong>Keine Kombi gefunden</strong>' +
      'Abstand oder Umkreis erhöhen, oder den EnBW-Filter ausschalten.</div>';
    return;
  }

  const now = new Date();
  const shown = state.spots.slice(0, LIST_LIMIT);
  const rest = state.spots.length - shown.length;
  view.list.innerHTML = shown.map((spot) => card(spot, now)).join('') + (rest > 0
    ? `<div class="empty"><strong>${rest} weitere Treffer</strong>` +
      'Die Liste ist auf die nächstgelegenen begrenzt. Umkreis verkleinern, ' +
      'Abstand verringern oder eine Kategorie abwählen.</div>'
    : '');
}

function card(spot, now) {
  const arrival = arrivalOf(spot);
  const open = evaluateOpeningHours(spot.openingHours, arrival);
  const charger = spot.chargers[0];
  const badges = [];
  if (charger) badges.push(`${formatMeters(charger.gapM)} zur Säule`);
  if (spot.routeProgressM != null) badges.push(`km ${Math.round(spot.routeProgressM / 1000)} ab Start`);
  else if (spot.distanceM != null) badges.push(`${formatMeters(spot.distanceM)} entfernt`);
  if (spot.routeOffsetM != null) badges.push(`${formatMeters(spot.routeOffsetM)} neben der Route`);
  // Bei den Ketten waere das Abzeichen an jeder Karte und damit wertlos: dort
  // gilt vegan pauschal, weil die Kette das Produkt fuehrt. Nur wo es aus dem
  // OSM-Tag des einzelnen Lokals kommt, sagt es etwas aus.
  if (spot.veganOnly) badges.push('rein vegan');
  else if (spot.vegan && !spot.brand) badges.push('vegane Optionen');

  const details = [];
  if (charger?.powerKw) details.push(`bis ${trimNumber(charger.powerKw)} kW`);
  if (charger?.capacity) details.push(`${charger.capacity} Ladepunkte`);
  if (charger?.fee) details.push(charger.fee === 'no' ? 'kostenlos' : 'kostenpflichtig');
  if (spot.chargers.length > 1) details.push(`${spot.chargers.length} Standorte in Reichweite`);

  const label = spot.routeSeconds != null
    ? `Ankunft ca. ${formatMoment(arrival, now)}`
    : `Zeitpunkt ${formatMoment(arrival, now)}`;

  return `
    <article class="card ${open.state === 'closed' ? 'closed' : ''}">
      <h2>${escapeHtml(spot.name)}</h2>
      ${spot.address ? `<p class="address">${escapeHtml(spot.address)}</p>` : ''}
      <p class="arrival">${label}</p>
      <p class="${openClass(open)}">${escapeHtml(describeOpen(open, arrival))}</p>
      <div class="badges">${badges.map((text) => `<span class="badge">${escapeHtml(text)}</span>`).join('')}</div>
      <div class="charger">
        <strong>${escapeHtml(charger?.operator || 'Ladesäule')}</strong>
        ${details.length ? `<p class="details">${escapeHtml(details.join(' · '))}</p>` : ''}
      </div>
      <div class="actions">
        <a href="geo:${spot.lat},${spot.lon}?q=${spot.lat},${spot.lon}(${encodeURIComponent(spot.name)})">Zum Lokal</a>
        ${charger ? `<a href="geo:${charger.lat},${charger.lon}?q=${charger.lat},${charger.lon}(Ladesäule)">Zur Säule</a>` : ''}
        <a href="${osmLink(spot)}" target="_blank" rel="noreferrer">OSM</a>
      </div>
    </article>`;
}

function openClass(open) {
  if (open.state === 'open') return 'open';
  if (open.state === 'closed') return 'shut';
  return 'vague';
}

function describeOpen(open, arrival) {
  if (open.state === 'unknown') return open.reason;
  const suffix = open.holidaysIgnored ? ' (ohne Feiertage)' : '';
  if (open.state === 'open') {
    return (open.until ? `offen bis ${formatMoment(open.until, arrival)}` : 'durchgehend offen') + suffix;
  }
  return (open.nextOpen ? `geschlossen, öffnet ${formatMoment(open.nextOpen, arrival)}` : 'geschlossen') + suffix;
}

// Die ID ist "node/123" oder "way/456", daraus baut sich die Adresse von selbst.
// Frueher lieferte der Server dafuer ein eigenes Feld, der statische Export aber
// nicht: ohne Server zeigte der Knopf auf "undefined".
function osmLink(spot) {
  return `https://www.openstreetmap.org/${spot.id}`;
}

function trimNumber(value) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace('.', ',');
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (char) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]
  ));
}

function ensureMap() {
  if (map) return map;
  map = L.map('map', { zoomControl: true }).setView([51.2, 9.5], 6);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap-Mitwirkende',
  }).addTo(map);
  layer = L.layerGroup().addTo(map);
  return map;
}

function renderMap() {
  if (state.tab !== 'map') return;
  ensureMap();
  layer.clearLayers();
  const bounds = [];

  if (state.route?.polyline) {
    const points = decodePolyline(state.route.polyline);
    L.polyline(points, { color: '#1e6fc4', weight: 5, opacity: 0.7 }).addTo(layer);
    points.forEach((point) => bounds.push(point));
  }

  const now = new Date();
  // Nur die nächstgelegene Säule je Lokal, und nur die ersten MAP_LIMIT Lokale.
  // Alle Säulen zu zeichnen hiess bei dichter Ladeinfrastruktur rund fünfzehn
  // Marker pro Lokal; die Karte stand dann. Wie viele Standorte in Reichweite
  // liegen, steht ohnehin auf der Karte in der Liste.
  state.spots.slice(0, MAP_LIMIT).forEach((spot) => {
    const open = evaluateOpeningHours(spot.openingHours, arrivalOf(spot));
    const closed = open.state === 'closed';
    const charger = spot.chargers[0];
    if (charger) {
      L.polyline([[spot.lat, spot.lon], [charger.lat, charger.lon]], {
        color: closed ? '#8e8e93' : '#1b8a4c',
        weight: 4,
        opacity: closed ? 0.4 : 0.9,
      }).addTo(layer);
      // Erst die Saeule, dann das Lokal: bei kleinem Zoom liegen die Marker
      // uebereinander, und oben liegen soll das Lokal.
      L.marker([charger.lat, charger.lon], { icon: pin('charger', '⚡') })
        .bindPopup(
          `<strong>${escapeHtml(charger.operator || 'Ladesäule')}</strong>` +
          `<br>${formatMeters(charger.gapM)} zu ${escapeHtml(spot.name)}` +
          (spot.chargers.length > 1 ? `<br>${spot.chargers.length} Standorte in Reichweite` : ''),
        )
        .addTo(layer);
      bounds.push([charger.lat, charger.lon]);
    }
    L.marker([spot.lat, spot.lon], {
      icon: pin(closed ? 'store-closed' : `store ${pinKind(spot)}`, glyphFor(spot)),
      zIndexOffset: 1000,
    })
      .bindPopup(
        `<strong>${escapeHtml(spot.name)}</strong>` +
        `<br>${escapeHtml(describeOpen(open, arrivalOf(spot)))}`,
      )
      .addTo(layer);
    bounds.push([spot.lat, spot.lon]);
  });

  if (state.center) {
    L.circleMarker([state.center.lat, state.center.lon], { radius: 7, color: '#0f2b46' }).addTo(layer);
    bounds.push([state.center.lat, state.center.lon]);
  }

  map.invalidateSize();
  if (bounds.length) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
}

function pin(kind, glyph) {
  return L.divIcon({
    className: '',
    html: `<div class="pin ${kind}">${glyph}</div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
  });
}

// ---- Bedienung ------------------------------------------------------------

document.querySelectorAll('[data-mode]').forEach((button) => {
  button.addEventListener('click', () => {
    state.mode = button.dataset.mode;
    pressed(document.querySelector('.modes'), 'data-mode', state.mode);
    const route = state.mode === 'route';
    view.radiusForm.hidden = route;
    view.routeForm.hidden = !route;
    view.radiusChips.hidden = route;
    view.corridorChips.hidden = !route;
  });
});

view.radiusForm.addEventListener('submit', (event) => {
  event.preventDefault();
  searchRadius();
});

view.routeForm.addEventListener('submit', (event) => {
  event.preventDefault();
  searchRoute();
});

document.getElementById('locateButton').addEventListener('click', () => {
  view.placeInput.value = '';
  searchRadius();
});

view.radiusChips.addEventListener('click', (event) => {
  const value = event.target.dataset?.radius;
  if (!value) return;
  state.radiusKm = Number(value);
  pressed(view.radiusChips, 'data-radius', value);
});

view.corridorChips.addEventListener('click', (event) => {
  const value = event.target.dataset?.corridor;
  if (!value) return;
  state.corridorM = Number(value);
  pressed(view.corridorChips, 'data-corridor', value);
});

document.getElementById('gapChips').addEventListener('click', (event) => {
  const value = event.target.dataset?.gap;
  if (value) {
    state.gapM = Number(value);
    pressed(document.getElementById('gapChips'), 'data-gap', value);
    rerunIfSearched();
  }
});

view.kindChips.addEventListener('click', (event) => {
  const key = event.target.dataset?.kind;
  if (!key) return;
  state.kinds = state.kinds.includes(key)
    ? state.kinds.filter((item) => item !== key)
    : [...state.kinds, key];
  event.target.setAttribute('aria-pressed', String(state.kinds.includes(key)));
  rerunIfSearched();
});

document.getElementById('enbwChip').addEventListener('click', (event) => {
  state.onlyEnbw = !state.onlyEnbw;
  event.target.setAttribute('aria-pressed', String(state.onlyEnbw));
  rerunIfSearched();
});

document.getElementById('departureChips').addEventListener('click', (event) => {
  const value = event.target.dataset?.departure;
  if (!value) return;
  state.departure = value === 'now' ? null : new Date(Date.now() + Number(value) * 3600_000);
  view.departureTime.value = '';
  updateDepartureLabel();
  render();
});

view.departureTime.addEventListener('change', () => {
  const [hour, minute] = view.departureTime.value.split(':').map(Number);
  if (Number.isNaN(hour)) return;
  const chosen = new Date();
  chosen.setHours(hour, minute, 0, 0);
  // Eine Uhrzeit in der Vergangenheit meint morgen.
  if (chosen < new Date()) chosen.setDate(chosen.getDate() + 1);
  state.departure = chosen;
  updateDepartureLabel();
  render();
});

function updateDepartureLabel() {
  const chips = document.getElementById('departureChips');
  chips.querySelectorAll('[data-departure]').forEach((button) => {
    button.setAttribute('aria-pressed', String(!state.departure && button.dataset.departure === 'now'));
  });
  view.departureLabel.textContent = state.departure
    ? `Abfahrt ${formatMoment(state.departure, new Date())}`
    : 'Abfahrt jetzt';
}

view.optionsToggle.addEventListener('click', () => {
  const hidden = !view.options.hidden;
  view.options.hidden = hidden;
  view.optionsToggle.textContent = hidden ? 'Optionen' : 'Optionen aus';
});

document.querySelectorAll('[data-tab]').forEach((button) => {
  button.addEventListener('click', () => {
    state.tab = button.dataset.tab;
    pressed(document.querySelector('.tabs'), 'data-tab', state.tab);
    view.list.hidden = state.tab !== 'list';
    view.map.hidden = state.tab !== 'map';
    renderMap();
  });
});

view.planInput.addEventListener('change', async () => {
  const file = view.planInput.files?.[0];
  if (!file) return;
  await withLoading(async () => {
    const result = await backend.readPlan(file);
    if (result.note) setBanner(result.note);
    if (result.waypoints.length < 2) {
      throw new Error(result.note || 'Der Plan enthält keine zwei verwertbaren Punkte.');
    }
    state.mode = 'route';
    pressed(document.querySelector('.modes'), 'data-mode', 'route');
    view.radiusForm.hidden = true;
    view.routeForm.hidden = false;
    view.radiusChips.hidden = true;
    view.corridorChips.hidden = false;
    await searchRoute(result.waypoints, result.label);
  });
  view.planInput.value = '';
});

let installPrompt = null;
window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  installPrompt = event;
  view.installButton.hidden = false;
});
view.installButton.addEventListener('click', async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  installPrompt = null;
  view.installButton.hidden = true;
});

// Relative Pfade ueberall: auf GitHub Pages liegt die App unter einem
// Unterverzeichnis, absolute Pfade wuerden dort ins Leere zeigen.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}

updateDepartureLabel();
render();

pickBackend()
  .then((meta) => {
    const source = backend.mode === 'server' ? 'Server' : 'im Browser';
    view.status.title =
      `${describeStock(meta)}, ${meta.chargers} Ladesäulen, ${meta.pairs} Paare` +
      `\nBereich ${meta.area ?? '?'} · Stand ${meta.ingested_at} · Suche ${source}`;
  })
  .catch(() => setBanner('Kein Datenbestand erreichbar. Angezeigt wird, was im Cache liegt.'));
