// Betrieb mit Server: die Suche kommt aus der SQLite-Datenbank, Geocoding und
// Routing laufen ueber den Server, weil dort der Cache sitzt und das
// Anfragelimit von Nominatim eingehalten wird.

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `Server antwortete mit ${response.status}`);
  }
  return payload;
}

export const serverBackend = {
  mode: 'server',

  meta() {
    return api('api/meta');
  },

  geocode(query) {
    return api(`api/geocode?q=${encodeURIComponent(query)}`);
  },

  async radiusSearch(center, params) {
    const query = new URLSearchParams({
      lat: center.lat,
      lon: center.lon,
      radius_km: params.radiusKm,
      gap_m: params.gapM,
      only_enbw: params.onlyEnbw ? '1' : '0',
    });
    return api(`api/spots?${query}`);
  },

  async routeSearch(request, params) {
    return api('api/route-spots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        places: request.places,
        waypoints: request.waypoints,
        label: request.label,
        corridorM: params.corridorM,
        gapM: params.gapM,
        onlyEnbw: params.onlyEnbw,
      }),
    });
  },

  async readPlan(file) {
    return api(`api/plan?name=${encodeURIComponent(file.name)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: await file.arrayBuffer(),
    });
  },
};
