// Geometrie fuer die Suche im Browser. Gleiche Rechnung wie server/geo.py,
// damit beide Betriebsarten dieselben Ergebnisse liefern.

const EARTH_RADIUS_M = 6371008.8;
export const METERS_PER_DEGREE_LAT = 111320;

export function haversineM(lat1, lon1, lat2, lon2) {
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function lonDegreesForMeters(meters, lat) {
  return meters / (METERS_PER_DEGREE_LAT * Math.max(Math.cos((lat * Math.PI) / 180), 0.01));
}

export function latDegreesForMeters(meters) {
  return meters / METERS_PER_DEGREE_LAT;
}

/** Duennt eine Polylinie aus und nimmt die kumulierten Fahrzeiten mit. */
export function resample(points, seconds, stepM) {
  if (points.length <= 2) return { points: [...points], seconds: seconds ? [...seconds] : null };
  const keptPoints = [points[0]];
  const keptSeconds = seconds ? [seconds[0]] : null;
  let carried = 0;
  for (let index = 1; index < points.length; index += 1) {
    carried += haversineM(points[index - 1][0], points[index - 1][1], points[index][0], points[index][1]);
    if (carried >= stepM || index === points.length - 1) {
      keptPoints.push(points[index]);
      if (keptSeconds && seconds[index] !== undefined) keptSeconds.push(seconds[index]);
      carried = 0;
    }
  }
  return { points: keptPoints, seconds: keptSeconds };
}

export function cumulativeDistances(points) {
  const out = [0];
  for (let index = 1; index < points.length; index += 1) {
    out.push(out[index - 1] +
      haversineM(points[index - 1][0], points[index - 1][1], points[index][0], points[index][1]));
  }
  return out;
}

/**
 * Kuerzester Abstand zur Route, Strecke ab Start und Fahrzeit ab Start.
 * Gerechnet in einer lokalen Meter-Ebene um den Suchpunkt: ueber die Laenge
 * eines Segments genau genug und deutlich billiger als Geodaesie.
 */
export function routeMatch(points, cumulative, seconds, lat, lon) {
  const latScale = METERS_PER_DEGREE_LAT;
  const lonScale = METERS_PER_DEGREE_LAT * Math.cos((lat * Math.PI) / 180);
  const px = lon * lonScale;
  const py = lat * latScale;

  let bestDistance = Infinity;
  let bestProgress = 0;
  let bestSeconds = 0;

  for (let index = 1; index < points.length; index += 1) {
    const ax = points[index - 1][1] * lonScale;
    const ay = points[index - 1][0] * latScale;
    const bx = points[index][1] * lonScale;
    const by = points[index][0] * latScale;
    const dx = bx - ax;
    const dy = by - ay;
    const lengthSq = dx * dx + dy * dy;
    const t = lengthSq === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengthSq));
    const distance = Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
    if (distance < bestDistance) {
      bestDistance = distance;
      bestProgress = cumulative[index - 1] + t * (cumulative[index] - cumulative[index - 1]);
      if (seconds) bestSeconds = seconds[index - 1] + t * (seconds[index] - seconds[index - 1]);
    }
  }
  return { offsetM: bestDistance, progressM: bestProgress, seconds: bestSeconds };
}

/** Google-Encoded-Polyline, wie OSRM sie liefert. */
export function decodePolyline(encoded, precision = 5) {
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
