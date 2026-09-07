import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// Die Suche im Browser laedt ihren Datenbestand per fetch. Fuer den Test kommt
// er vom Dateisystem, sonst ist es dieselbe Codebasis wie auf GitHub Pages.
const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
// Zwei Teile wie im echten Betrieb: die Ketten immer, die uebrigen veganen
// Lokale nur, wenn die Suche sie anfordert.
const DATA = ['web/data/spots.json', 'web/data/spots-vegan.json'];

let dataAvailable = true;
for (const relative of DATA) {
  try {
    await readFile(path.join(ROOT, relative));
  } catch {
    dataAvailable = false;
  }
}

const fetched = [];
globalThis.fetch = async (url) => {
  const relative = DATA.find((item) => String(url).endsWith(path.basename(item)));
  if (relative) {
    fetched.push(path.basename(relative));
    const text = await readFile(path.join(ROOT, relative), 'utf8');
    return { ok: true, json: async () => JSON.parse(text) };
  }
  throw new Error(`Im Test nicht erlaubt: ${url}`);
};

const { localBackend, parseGpx, isAddress, isPlaceholder, columnAFromSheet, thin } =
  await import('../web/local-search.js');

const DORTMUND = { lat: 51.5142, lon: 7.4653 };
const STUTTGART = { lat: 48.7758, lon: 9.1829 };
const params = { kinds: ['bk'], radiusKm: 25, corridorM: 3000, gapM: 300, onlyEnbw: true };

// Eine gerade Linie reicht: geprueft wird die Auswahllogik, nicht der Router.
function straightRoute(from, to, steps = 200) {
  const points = [];
  const seconds = [];
  for (let index = 0; index <= steps; index += 1) {
    const share = index / steps;
    points.push([
      from.lat + (to.lat - from.lat) * share,
      from.lon + (to.lon - from.lon) * share,
    ]);
    seconds.push(share * 14400); // vier Stunden ueber die ganze Strecke
  }
  return { points, cumulativeSeconds: seconds };
}

test('Umkreissuche liefert nur Treffer im Radius, sortiert nach Entfernung', { skip: !dataAvailable }, async () => {
  const result = await localBackend.radiusSearch(DORTMUND, params);
  assert.ok(result.spots.length > 0, 'keine Treffer um Dortmund');
  for (const spot of result.spots) {
    assert.ok(spot.distanceM <= 25000, `${spot.name} liegt ${spot.distanceM} m entfernt`);
    assert.ok(spot.chargers.length > 0);
    for (const charger of spot.chargers) {
      assert.ok(charger.gapM <= params.gapM);
      assert.equal(charger.isEnbw, true);
    }
  }
  const distances = result.spots.map((spot) => spot.distanceM);
  assert.deepEqual(distances, [...distances].sort((a, b) => a - b));

  // Eine Suche nach Burger King darf den grossen Zusatzteil nicht anfassen.
  // Genau das ist der Grund fuer die Aufteilung: gepackt 445 KB statt 5,5 MB.
  assert.ok(fetched.includes('spots.json'));
  assert.ok(!fetched.includes('spots-vegan.json'), 'Zusatzteil ohne Not geladen');
});

test('Der EnBW-Filter kann nur weniger finden, nie mehr', { skip: !dataAvailable }, async () => {
  const strict = await localBackend.radiusSearch(DORTMUND, { ...params, onlyEnbw: true });
  const loose = await localBackend.radiusSearch(DORTMUND, { ...params, onlyEnbw: false });
  const looseIds = new Set(loose.spots.map((spot) => spot.id));
  assert.ok(strict.spots.length <= loose.spots.length);
  for (const spot of strict.spots) assert.ok(looseIds.has(spot.id), spot.id);
});

test('Ein engerer Abstand liefert eine Teilmenge', { skip: !dataAvailable }, async () => {
  const near = await localBackend.radiusSearch(DORTMUND, { ...params, gapM: 100 });
  const far = await localBackend.radiusSearch(DORTMUND, { ...params, gapM: 1000 });
  const farIds = new Set(far.spots.map((spot) => spot.id));
  for (const spot of near.spots) assert.ok(farIds.has(spot.id), spot.id);
});

test('Routensuche haelt den Korridor ein und sortiert nach Fahrtrichtung', { skip: !dataAvailable }, async () => {
  const route = straightRoute(DORTMUND, STUTTGART);
  const spots = await localBackend.spotsAlongRoute(route, params);
  assert.ok(spots.length > 0, 'keine Treffer entlang der Strecke');

  let previous = -1;
  for (const spot of spots) {
    assert.ok(spot.routeOffsetM <= params.corridorM, `${spot.name}: ${spot.routeOffsetM} m daneben`);
    assert.ok(spot.routeProgressM >= previous, 'nicht nach Strecke sortiert');
    previous = spot.routeProgressM;
    // Ankunftszeit muss innerhalb der Fahrzeit liegen.
    assert.ok(spot.routeSeconds >= 0 && spot.routeSeconds <= 14400);
  }
});

test('Ein weiterer Korridor enthaelt den engeren', { skip: !dataAvailable }, async () => {
  const route = straightRoute(DORTMUND, STUTTGART);
  const narrow = await localBackend.spotsAlongRoute(route, { ...params, corridorM: 1000 });
  const wide = await localBackend.spotsAlongRoute(route, { ...params, corridorM: 5000 });
  const wideIds = new Set(wide.map((spot) => spot.id));
  assert.ok(narrow.length <= wide.length);
  for (const spot of narrow) assert.ok(wideIds.has(spot.id), spot.id);
});

test('GPX: Trackpunkte schlagen Wegpunkte, Attributreihenfolge egal', () => {
  const points = parseGpx(`<gpx>
    <wpt lat="1.0" lon="2.0"><name>Ladestopp</name></wpt>
    <trk><trkseg>
      <trkpt lat="48.7758" lon="9.1829"/>
      <trkpt lon="9.2000" lat="48.8000"/>
    </trkseg></trk>
  </gpx>`);
  assert.equal(points.length, 2);
  assert.deepEqual(points[1], [48.8, 9.2]);
});

test('Excel: Spalte A wird gefiltert wie auf dem Server', () => {
  const sheet =
    '<worksheet><sheetData>' +
    '<row r="1"><c r="A1" t="inlineStr"><is><t>ABRP Plan</t></is></c></row>' +
    '<row r="5"><c r="A5" t="inlineStr"><is><t>Punkt auf der Karte</t></is></c></row>' +
    '<row r="6"><c r="A6" t="inlineStr"><is><t>Bahnhofstra&#223;e 84, 46145 Oberhausen, Deutschland</t></is></c></row>' +
    '<row r="7"><c r="A7" t="inlineStr"><is><t>55 Min.</t></is></c></row>' +
    '</sheetData></worksheet>';
  const column = columnAFromSheet(sheet, '');
  assert.deepEqual(column.filter(isAddress), ['Bahnhofstraße 84, 46145 Oberhausen, Deutschland']);
  assert.equal(column.filter(isPlaceholder).length, 1);
});

test('Gemeinsame Zeichenkettentabelle wird aufgeloest', () => {
  const sheet =
    '<worksheet><sheetData>' +
    '<row r="1"><c r="A1" t="s"><v>0</v></c></row>' +
    '<row r="2"><c r="A2" t="s"><v>1</v></c></row>' +
    '</sheetData></worksheet>';
  const shared = '<sst><si><t>Wegpunkt</t></si><si><t>Hauptstr. 1, 44135 Dortmund</t></si></sst>';
  assert.deepEqual(columnAFromSheet(sheet, shared).filter(isAddress), ['Hauptstr. 1, 44135 Dortmund']);
});

test('Ausduennen behaelt Anfang und Ende', () => {
  const points = Array.from({ length: 100 }, (_, index) => [48 + index * 0.01, 9]);
  const thinned = thin(points, 20);
  assert.equal(thinned.length, 20);
  assert.deepEqual(thinned[0], points[0]);
  assert.deepEqual(thinned[19], points[99]);
});

test('Ketten sind einzeln waehlbar und kombinierbar', { skip: !dataAvailable }, async () => {
  const wide = { ...params, radiusKm: 100, onlyEnbw: false };
  const burgerKing = await localBackend.radiusSearch(DORTMUND, { ...wide, kinds: ['bk'] });
  const subway = await localBackend.radiusSearch(DORTMUND, { ...wide, kinds: ['subway'] });
  const both = await localBackend.radiusSearch(DORTMUND, { ...wide, kinds: ['bk', 'subway'] });

  assert.ok(burgerKing.spots.length > 0, 'kein Burger King im 100-km-Umkreis');
  assert.ok(subway.spots.length > 0, 'kein Subway im 100-km-Umkreis');
  assert.ok(burgerKing.spots.every((spot) => spot.brand === 'bk'));
  assert.ok(subway.spots.every((spot) => spot.brand === 'subway'));
  // Zusammen ist genau die Vereinigung, ohne Doppelte.
  assert.equal(both.spots.length, burgerKing.spots.length + subway.spots.length);
  assert.equal(new Set(both.spots.map((spot) => spot.id)).size, both.spots.length);
});

test('Burger King ist abwaehlbar, ohne Auswahl gibt es nichts', { skip: !dataAvailable }, async () => {
  const none = await localBackend.radiusSearch(DORTMUND, { ...params, kinds: [] });
  assert.equal(none.spots.length, 0);
  const unknown = await localBackend.radiusSearch(DORTMUND, { ...params, kinds: ['mcdonalds'] });
  assert.equal(unknown.spots.length, 0);
});

test('Auch entlang der Route wirkt die Auswahl', { skip: !dataAvailable }, async () => {
  const route = straightRoute(DORTMUND, STUTTGART);
  const subway = await localBackend.spotsAlongRoute(route, { ...params, kinds: ['subway'], onlyEnbw: false });
  assert.ok(subway.every((spot) => spot.brand === 'subway'));
});

test('Vegane Kategorien schliessen die Ketten ein, rein vegan ist eine Teilmenge',
  { skip: !dataAvailable }, async () => {
    const wide = { ...params, radiusKm: 100, onlyEnbw: false };
    const vegan = await localBackend.radiusSearch(DORTMUND, { ...wide, kinds: ['vegan'] });
    const veganOnly = await localBackend.radiusSearch(DORTMUND, { ...wide, kinds: ['vegan_only'] });
    const chains = await localBackend.radiusSearch(DORTMUND, { ...wide, kinds: ['bk', 'subway'] });

    assert.ok(vegan.spots.length > 0, 'nichts mit veganen Optionen im 100-km-Umkreis');
    assert.ok(vegan.spots.every((spot) => spot.vegan === true));
    assert.ok(veganOnly.spots.every((spot) => spot.veganOnly === true));

    // Rein vegan ist immer auch vegan, und die Ketten zaehlen immer mit,
    // unabhaengig davon, was am einzelnen Laden getaggt ist.
    const veganIds = new Set(vegan.spots.map((spot) => spot.id));
    for (const spot of veganOnly.spots) assert.ok(veganIds.has(spot.id), spot.id);
    for (const spot of chains.spots) assert.ok(veganIds.has(spot.id), spot.id);
  });

test('Kombinierte Auswahl liefert jedes Lokal genau einmal', { skip: !dataAvailable }, async () => {
  const wide = { ...params, radiusKm: 100, onlyEnbw: false, kinds: ['bk', 'vegan', 'vegan_only'] };
  const result = await localBackend.radiusSearch(DORTMUND, wide);
  assert.equal(new Set(result.spots.map((spot) => spot.id)).size, result.spots.length);
});

test('Der Zusatzteil wird genau einmal geladen und dann wiederverwendet',
  { skip: !dataAvailable }, async () => {
    const wide = { ...params, radiusKm: 50, onlyEnbw: false, kinds: ['vegan'] };
    await localBackend.radiusSearch(DORTMUND, wide);
    await localBackend.radiusSearch(STUTTGART, wide);
    const count = (name) => fetched.filter((item) => item === name).length;
    assert.equal(count('spots.json'), 1);
    assert.equal(count('spots-vegan.json'), 1);
  });
