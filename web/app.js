import { evaluateOpeningHours } from './opening-hours.js';

const state = {
  mode: 'radius',
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

function pressed(container, attribute, value) {
  container.querySelectorAll(`[${attribute}]`).forEach((button) => {
    button.setAttribute('aria-pressed', String(button.getAttribute(attribute) === String(value)));
  });
}

// ---- Server ---------------------------------------------------------------

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `Server antwortete mit ${response.status}`);
  }
  return payload;
}

async function searchRadius() {
  const query = view.placeInput.value.trim();
  await withLoading(async () => {
    let center;
    if (query) {
      center = await api(`/api/geocode?q=${encodeURIComponent(query)}`);
    } else {
      const position = await currentPosition();
      center = { lat: position.coords.latitude, lon: position.coords.longitude, label: 'Mein Standort' };
    }
    const result = await api(
      `/api/spots?lat=${center.lat}&lon=${center.lon}` +
      `&radius_km=${state.radiusKm}&gap_m=${state.gapM}&only_enbw=${state.onlyEnbw ? 1 : 0}`,
    );
    state.center = center;
    state.route = null;
    state.spots = result.spots;
    state.queryMs = result.queryMs;
  });
}

async function searchRoute(waypoints, label) {
  await withLoading(async () => {
    const body = { corridorM: state.corridorM, gapM: state.gapM, onlyEnbw: state.onlyEnbw };
    if (waypoints) {
      body.waypoints = waypoints;
      body.label = label;
    } else {
      const start = view.startInput.value.trim();
      const destination = view.destinationInput.value.trim();
      if (!destination) throw new Error('Ziel fehlt.');
      if (start) {
        body.places = [start, destination];
      } else {
        const position = await currentPosition();
        body.waypoints = [[position.coords.latitude, position.coords.longitude]];
        const target = await api(`/api/geocode?q=${encodeURIComponent(destination)}`);
        body.waypoints.push([target.lat, target.lon]);
        body.label = `Mein Standort nach ${target.label}`;
      }
    }
    const result = await api('/api/route-spots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    state.route = result.route;
    state.center = null;
    state.spots = result.spots;
    state.queryMs = result.queryMs;
    if (!result.route.hasMeasuredDurations) {
      setBanner('Für diese Route liegen keine Fahrzeiten vor, die Ankunftszeiten sind geschätzt.');
    }
  });
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
    view.status.textContent = 'Burger King neben der Ladesäule';
    return;
  }
  const where = state.route ? state.route.label : (state.center?.label ?? '');
  const speed = state.queryMs != null ? ` · ${state.queryMs} ms` : '';
  view.status.textContent = `${state.spots.length} Kombis · ${where}${speed}`;
}

function renderList() {
  if (state.loading) {
    view.list.innerHTML = '<div class="empty"><strong>Suche läuft ...</strong>Die Antwort kommt aus der lokalen Datenbank.</div>';
    return;
  }
  if (!state.searched) {
    view.list.innerHTML = '<div class="empty"><strong>Laden und Whopper in einem Stopp</strong>' +
      'Umkreis: Ort eingeben oder Standort freigeben. Route: Start und Ziel eintippen, oder einen Plan laden.</div>';
    return;
  }
  if (state.spots.length === 0) {
    view.list.innerHTML = '<div class="empty"><strong>Keine Kombi gefunden</strong>' +
      'Abstand oder Umkreis erhöhen, oder den EnBW-Filter ausschalten.</div>';
    return;
  }

  const now = new Date();
  view.list.innerHTML = state.spots.map((spot) => card(spot, now)).join('');
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
        <a href="geo:${spot.lat},${spot.lon}?q=${spot.lat},${spot.lon}(${encodeURIComponent(spot.name)})">Zum BK</a>
        ${charger ? `<a href="geo:${charger.lat},${charger.lon}?q=${charger.lat},${charger.lon}(Ladesäule)">Zur Säule</a>` : ''}
        <a href="${spot.osmUrl}" target="_blank" rel="noreferrer">OSM</a>
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
  state.spots.forEach((spot) => {
    const open = evaluateOpeningHours(spot.openingHours, arrivalOf(spot));
    const closed = open.state === 'closed';
    const charger = spot.chargers[0];
    if (charger) {
      L.polyline([[spot.lat, spot.lon], [charger.lat, charger.lon]], {
        color: closed ? '#8e8e93' : '#1b8a4c',
        weight: 4,
        opacity: closed ? 0.4 : 0.9,
      }).addTo(layer);
    }
    // Erst die Saeulen, dann die Filiale: bei kleinem Zoom liegen die Marker
    // uebereinander, und oben liegen soll die Filiale.
    spot.chargers.forEach((item) => {
      L.marker([item.lat, item.lon], { icon: pin('charger', '⚡') })
        .bindPopup(`<strong>${escapeHtml(item.operator || 'Ladesäule')}</strong><br>${formatMeters(item.gapM)} zum Burger King`)
        .addTo(layer);
      bounds.push([item.lat, item.lon]);
    });
    L.marker([spot.lat, spot.lon], { icon: pin(closed ? 'bk-closed' : 'bk', '🍔'), zIndexOffset: 1000 })
      .bindPopup(`<strong>${escapeHtml(spot.name)}</strong><br>${escapeHtml(describeOpen(open, arrivalOf(spot)))}`)
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

// OSRM liefert die Route als Encoded Polyline, das spart gegenueber JSON-Punkten
// bei 400 km rund 90 Prozent Uebertragung.
function decodePolyline(encoded, precision = 5) {
  const factor = 10 ** precision;
  const points = [];
  let index = 0;
  let lat = 0;
  let lon = 0;
  while (index < encoded.length) {
    for (let isLat = 0; isLat < 2; isLat += 1) {
      let shift = 0;
      let result = 0;
      let byte;
      do {
        byte = encoded.charCodeAt(index) - 63;
        index += 1;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20);
      const delta = (result & 1) ? ~(result >> 1) : (result >> 1);
      if (isLat === 0) lat += delta; else lon += delta;
    }
    points.push([lat / factor, lon / factor]);
  }
  return points;
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
  }
});

document.getElementById('enbwChip').addEventListener('click', (event) => {
  state.onlyEnbw = !state.onlyEnbw;
  event.target.setAttribute('aria-pressed', String(state.onlyEnbw));
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
    const form = await file.arrayBuffer();
    const result = await api(`/api/plan?name=${encodeURIComponent(file.name)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: form,
    });
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

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}

updateDepartureLabel();
render();

api('/api/meta')
  .then((meta) => {
    view.status.title =
      `${meta.burgers} Filialen, ${meta.chargers} Ladesäulen, ${meta.pairs} Paare · Stand ${meta.ingested_at}`;
  })
  .catch(() => setBanner('Server nicht erreichbar. Angezeigt wird, was im Cache liegt.'));
