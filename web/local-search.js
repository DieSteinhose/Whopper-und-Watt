// Betrieb ohne Server: alles rechnet der Browser.
//
// Auf GitHub Pages laeuft kein Python. Der Datenbestand ist aber klein genug
// (rund tausend Filialen, gepackt gut 200 KB), um ihn einmal zu laden und die
// Suche lokal zu machen. Das ist nebenbei die ehrlichere PWA: nach dem ersten
// Laden funktioniert die Suche auch offline.
//
// Geocoding und Routing gehen dann direkt an Nominatim und OSRM. Beide erlauben
// das per CORS. Ohne Server gibt es keinen gemeinsamen Cache mehr, dafuer trifft
// das Anfragelimit auch nur den einzelnen Nutzer.

import {
  cumulativeDistances,
  decodePolyline,
  haversineM,
  resample,
  routeMatch,
} from './geo.js';

const DATA_URL = 'data/spots.json';
const NOMINATIM = 'https://nominatim.openstreetmap.org/search';
const OSRM = 'https://router.project-osrm.org';
const ROUTE_SAMPLE_STEP_M = 2000;
const MAX_UNPACKED_BYTES = 8 * 1024 * 1024;

let dataPromise = null;

function load() {
  if (!dataPromise) {
    dataPromise = fetch(DATA_URL).then((response) => {
      if (!response.ok) throw new Error(`Datenbestand nicht ladbar (${response.status})`);
      return response.json();
    });
  }
  return dataPromise;
}

function usableChargers(spot, gapM, onlyEnbw) {
  return spot.chargers.filter(
    (charger) => charger.gapM <= gapM && (!onlyEnbw || charger.isEnbw),
  );
}

// Ohne Angabe bleibt es bei der Voreinstellung des Datenbestands, sonst waere
// eine alte gespeicherte Auswahl schlimmer als gar keine.
function wantedBrand(spot, brands) {
  if (!brands || brands.length === 0) return false;
  return brands.includes(spot.brand ?? 'bk');
}

export const localBackend = {
  mode: 'local',

  async meta() {
    const data = await load();
    return { ...data.meta, ok: true };
  },

  async radiusSearch(center, params) {
    const started = performance.now();
    const data = await load();
    const radiusM = params.radiusKm * 1000;
    const spots = [];
    for (const spot of data.spots) {
      if (!wantedBrand(spot, params.brands)) continue;
      const distance = haversineM(center.lat, center.lon, spot.lat, spot.lon);
      if (distance > radiusM) continue;
      const chargers = usableChargers(spot, params.gapM, params.onlyEnbw);
      if (!chargers.length) continue;
      spots.push({ ...spot, chargers, distanceM: Math.round(distance) });
    }
    spots.sort((a, b) => a.distanceM - b.distanceM);
    return { spots, queryMs: Math.round((performance.now() - started) * 10) / 10 };
  },

  async routeSearch(request, params) {
    const started = performance.now();
    let waypoints = request.waypoints;
    let label = request.label;
    if (!waypoints) {
      const places = [];
      for (const text of request.places) places.push(await this.geocode(text));
      waypoints = places.map((place) => [place.lat, place.lon]);
      label = `${places[0].label} nach ${places[places.length - 1].label}`;
    }
    const computed = await this.route(waypoints);
    const spots = await this.spotsAlongRoute(computed, params);
    return {
      route: {
        polyline: computed.polyline,
        distanceM: computed.distanceM,
        durationS: computed.durationS,
        hasMeasuredDurations: computed.cumulativeSeconds !== null,
        label,
      },
      spots,
      queryMs: Math.round((performance.now() - started) * 10) / 10,
    };
  },

  async spotsAlongRoute(computed, params) {
    const data = await load();
    // Fuer den Abstand reicht eine ausgeduennte Linie, das spart je Filiale
    // tausende Segmentvergleiche.
    const thin = resample(computed.points, computed.cumulativeSeconds, ROUTE_SAMPLE_STEP_M);
    const cumulative = cumulativeDistances(thin.points);

    const spots = [];
    for (const spot of data.spots) {
      if (!wantedBrand(spot, params.brands)) continue;
      const match = routeMatch(thin.points, cumulative, thin.seconds, spot.lat, spot.lon);
      if (match.offsetM > params.corridorM) continue;
      const chargers = usableChargers(spot, params.gapM, params.onlyEnbw);
      if (!chargers.length) continue;
      spots.push({
        ...spot,
        chargers,
        routeOffsetM: Math.round(match.offsetM),
        routeProgressM: Math.round(match.progressM),
        routeSeconds: thin.seconds ? Math.round(match.seconds) : null,
      });
    }
    spots.sort((a, b) => a.routeProgressM - b.routeProgressM);
    return spots;
  },

  async geocode(query) {
    const url = `${NOMINATIM}?${new URLSearchParams({ q: query, format: 'jsonv2', limit: '1' })}`;
    const response = await fetch(url, { headers: { 'Accept-Language': 'de' } });
    if (!response.ok) throw new Error(`Ortssuche fehlgeschlagen (${response.status})`);
    const results = await response.json();
    if (!results.length) throw new Error(`Ort "${query}" nicht gefunden`);
    return {
      label: results[0].display_name.split(',').slice(0, 2).join(',').trim(),
      lat: Number(results[0].lat),
      lon: Number(results[0].lon),
    };
  },

  async route(waypoints) {
    const coordinates = waypoints.map(([lat, lon]) => `${lon},${lat}`).join(';');
    const url = `${OSRM}/route/v1/driving/${coordinates}` +
      '?overview=full&geometries=polyline&alternatives=false&steps=false&annotations=duration';
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Routing fehlgeschlagen (${response.status})`);
    const payload = await response.json();
    if (payload.code !== 'Ok' || !payload.routes?.length) {
      throw new Error(`Keine Route gefunden (${payload.code})`);
    }
    const best = payload.routes[0];
    const points = decodePolyline(best.geometry);
    return {
      polyline: best.geometry,
      points,
      cumulativeSeconds: cumulativeSeconds(best, points.length),
      distanceM: best.distance,
      durationS: best.duration,
    };
  },

  async readPlan(file) {
    const buffer = await file.arrayBuffer();
    const head = new Uint8Array(buffer, 0, Math.min(2, buffer.byteLength));
    if (head[0] === 0x50 && head[1] === 0x4b) return readExcelPlan(buffer, this);
    const points = parseGpx(new TextDecoder().decode(buffer));
    if (points.length < 2) throw new Error('Keine Streckenpunkte in der Datei gefunden.');
    return { waypoints: thin(points, 20), label: file.name || 'GPX-Route', note: '' };
  },
};

/**
 * Fahrzeit ab Start je Stuetzpunkt. Die Summe der Segment-Annotationen weicht
 * leicht von der Gesamtdauer ab (Abbiegekosten), deshalb wird linear skaliert.
 */
function cumulativeSeconds(route, pointCount) {
  const durations = [];
  for (const leg of route.legs ?? []) {
    for (const value of leg.annotation?.duration ?? []) durations.push(value);
  }
  if (durations.length !== pointCount - 1) return null;

  const summed = durations.reduce((total, value) => total + value, 0);
  const factor = summed > 0 && route.duration > 0 ? route.duration / summed : 1;
  const cumulative = [0];
  for (const value of durations) cumulative.push(cumulative[cumulative.length - 1] + value * factor);
  return cumulative;
}

// ---- Dateien --------------------------------------------------------------

const GPX_TAGS = ['trkpt', 'rtept', 'wpt'];

export function parseGpx(text) {
  // Trackpunkte schlagen Routenpunkte, Routenpunkte schlagen Wegpunkte.
  for (const tag of GPX_TAGS) {
    const points = [];
    for (const match of text.matchAll(new RegExp(`<${tag}\\b[^>]*>`, 'gi'))) {
      const lat = /\blat\s*=\s*"([-0-9.eE+]+)"/i.exec(match[0]);
      const lon = /\blon\s*=\s*"([-0-9.eE+]+)"/i.exec(match[0]);
      if (lat && lon) points.push([Number(lat[1]), Number(lon[1])]);
    }
    if (points.length >= 2) return points;
  }
  return [];
}

const UNIT = /^\s*\d+([.,]\d+)?\s*(km|m|min\.?|std\.?|h|kwh|%|€)\s*$/i;
const CLOCK = /^\s*\d{1,2}:\d{2}\s*$/;
const PLACEHOLDERS = ['punkt auf der karte', 'point on map', 'map point'];

export function isPlaceholder(value) {
  return PLACEHOLDERS.includes(value.trim().toLowerCase());
}

export function isAddress(value) {
  const text = value.trim();
  if (text.length < 5 || text.toLowerCase().startsWith('http')) return false;
  if (isPlaceholder(text) || UNIT.test(text) || CLOCK.test(text)) return false;
  if (!/[a-zA-ZäöüßÄÖÜ]/.test(text)) return false;
  return text.includes(',') || /\d/.test(text);
}

export function columnAFromSheet(sheetXml, sharedXml) {
  const shared = [...(sharedXml || '').matchAll(/<si>([\s\S]*?)<\/si>/g)].map((item) =>
    [...item[1].matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((part) => unescapeXml(part[1])).join(''),
  );
  const values = [];
  for (const cell of sheetXml.matchAll(/<c\b[^>]*?r="A\d+"[^>]*?(?:\/>|>([\s\S]*?)<\/c>)/g)) {
    const body = cell[1] || '';
    const inline = /<t[^>]*>([\s\S]*?)<\/t>/.exec(body);
    if (inline) {
      values.push(unescapeXml(inline[1]).trim());
      continue;
    }
    const value = /<v>([\s\S]*?)<\/v>/.exec(body);
    if (!value) continue;
    const raw = unescapeXml(value[1]).trim();
    values.push(cell[0].includes('t="s"') ? (shared[Number(raw)] ?? '') : raw);
  }
  return values.filter(Boolean);
}

function unescapeXml(text) {
  return text
    .replace(/&#(x?)([0-9a-fA-F]+);/g, (_, hex, code) =>
      String.fromCodePoint(parseInt(code, hex ? 16 : 10)))
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, '&');
}

async function readExcelPlan(buffer, backend) {
  const files = await unzip(buffer, (name) =>
    name.endsWith('xl/worksheets/sheet1.xml') || name.endsWith('xl/sharedStrings.xml'));
  const sheetName = Object.keys(files).find((name) => name.includes('sheet1.xml'));
  if (!sheetName) throw new Error('Die Datei enthält kein Tabellenblatt.');

  const decoder = new TextDecoder();
  const sharedName = Object.keys(files).find((name) => name.includes('sharedStrings.xml'));
  const column = columnAFromSheet(
    decoder.decode(files[sheetName]),
    sharedName ? decoder.decode(files[sharedName]) : '',
  );
  const addresses = column.filter(isAddress);
  const placeholders = column.filter(isPlaceholder).length;

  const notes = [];
  if (placeholders) {
    notes.push(`${placeholders} Wegpunkt(e) im Export sind "Punkt auf der Karte" und enthalten keine Ortsangabe.`);
  }
  if (addresses.length < 2) {
    notes.push(
      'Der ABRP-Excel-Export enthält keine Koordinaten, nur Adresstexte. ' +
      `Verwertbar ist hier ${addresses.length}. In ABRP die Wegpunkte über die Adresssuche ` +
      'setzen statt per Klick auf die Karte.',
    );
    return { waypoints: [], addresses, note: notes.join(' ') };
  }

  const waypoints = [];
  const failed = [];
  for (const address of addresses) {
    try {
      const place = await backend.geocode(address);
      waypoints.push([place.lat, place.lon]);
    } catch {
      failed.push(address);
    }
    // Nominatim erlaubt eine Anfrage pro Sekunde.
    await new Promise((resolve) => setTimeout(resolve, 1100));
  }
  if (failed.length) notes.push(`Nicht gefunden: ${failed.join('; ')}.`);
  return { waypoints, addresses, label: `ABRP-Plan (${waypoints.length} Wegpunkte)`, note: notes.join(' ') };
}

/** Minimaler ZIP-Leser. xlsx ist ein ZIP, und der Browser kann deflate selbst. */
async function unzip(buffer, wanted) {
  const view = new DataView(buffer);
  let endOfDirectory = -1;
  for (let offset = buffer.byteLength - 22; offset >= 0; offset -= 1) {
    if (view.getUint32(offset, true) === 0x06054b50) {
      endOfDirectory = offset;
      break;
    }
  }
  if (endOfDirectory < 0) throw new Error('Das ist keine lesbare ZIP-Datei.');

  const entries = view.getUint16(endOfDirectory + 10, true);
  let offset = view.getUint32(endOfDirectory + 16, true);
  const files = {};
  for (let index = 0; index < entries; index += 1) {
    if (view.getUint32(offset, true) !== 0x02014b50) break;
    const method = view.getUint16(offset + 10, true);
    const compressedSize = view.getUint32(offset + 20, true);
    const nameLength = view.getUint16(offset + 28, true);
    const extraLength = view.getUint16(offset + 30, true);
    const commentLength = view.getUint16(offset + 32, true);
    const localOffset = view.getUint32(offset + 42, true);
    const name = new TextDecoder().decode(new Uint8Array(buffer, offset + 46, nameLength));
    offset += 46 + nameLength + extraLength + commentLength;
    if (!wanted(name)) continue;

    const localNameLength = view.getUint16(localOffset + 26, true);
    const localExtraLength = view.getUint16(localOffset + 28, true);
    const start = localOffset + 30 + localNameLength + localExtraLength;
    const raw = new Uint8Array(buffer, start, compressedSize);
    const unpacked = method === 0 ? raw : await inflateRaw(raw);
    if (unpacked.byteLength > MAX_UNPACKED_BYTES) {
      throw new Error('Der Inhalt der Datei ist unplausibel groß.');
    }
    files[name] = unpacked;
  }
  return files;
}

async function inflateRaw(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export function thin(points, limit) {
  if (points.length <= limit) return points;
  const step = (points.length - 1) / (limit - 1);
  const result = [];
  for (let index = 0; index < limit; index += 1) result.push(points[Math.round(index * step)]);
  result[result.length - 1] = points[points.length - 1];
  return result;
}
