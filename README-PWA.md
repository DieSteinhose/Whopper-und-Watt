# Whopper & Watt als PWA

Dieselbe Frage wie die Android-App, andere Bauform: **Wo steht eine Ladesäule direkt neben
einem Burger King?** Diesmal als installierbare Web-App mit eigenem Server und eigener
Datenbank, statt bei jeder Suche live gegen OpenStreetMap zu fragen.

## Warum überhaupt ein Server

Die Android-App fragt Overpass zur Laufzeit. Das war der Flaschenhals, gemessen über mehrere
Tage: eine Umkreissuche dauerte 11 Sekunden im guten Fall, eine Routensuche über 419 km einmal
459 Sekunden, und die öffentlichen Instanzen wiesen Abfragen regelmäßig mit HTTP 429 oder 504 ab.

Hier passiert die teure Arbeit einmal beim Ingest. Die Abfrage liest danach nur noch aus SQLite.

```
Browser (PWA)          Server (Python, nur Standardbibliothek)      einmalig
  Suche ──────────────► /api/spots ──► SQLite (Filialen, Säulen,       Overpass
  Karte ◄────────────── JSON          fertige Paare, R-Tree)     ◄──── OSRM/Nominatim
  Öffnungszeiten lokal                 Routing/Geocoding nur bei Bedarf
```

## Datenbank

`server/ingest.py` baut sie in zwei Schritten, weil Burger King selten und Ladesäulen häufig sind:

1. Alle Filialen im Suchbereich, eine Abfrage über eine Bounding-Box.
2. Ladesäulen in 1-km-Boxen um genau diese Filialen, in Blöcken zu 20.

**Warum Bounding-Box und nicht Landesfläche:** die naheliegendere Abfrage
`area["ISO3166-1"="DE"]` setzt voraus, dass die Overpass-Instanz eine Area-Datenbank hat.
Ausgerechnet der schnellsten Instanz fehlt sie: `overpass.openstreetmap.fr` antwortet darauf
mit `runtime error: open64: 2 No such file or directory`. Eine Bounding-Box versteht jede
Instanz. Der Preis ist ein Überhang ins Ausland, und ein Burger King fünfzehn Kilometer hinter
der Grenze schadet niemandem. Wer es exakt will, nimmt `--country DE`; scheitert die
Flächensuche, fällt der Ingest automatisch auf die Bounding-Box zurück.

Danach werden die Paare (Filiale ↔ Säule bis 1000 m) einmal ausgerechnet und gespeichert, und
die Filialen kommen in einen R-Tree-Index. Eine Suche ist damit ein Index-Zugriff plus etwas
Arithmetik.

```bash
python3 server/ingest.py --db server/data/whopper.sqlite     # dauert wenige Minuten
python3 server/ingest.py --pairs-only                        # nur Paare neu rechnen
python3 server/ingest.py --bbox 46.4,9.5,49.0,17.2           # anderer Suchbereich
```

Der Lauf ist fortsetzbar: jeder erledigte Block wird sofort festgeschrieben, ein Abbruch kostet
nichts. Die Reihenfolge der Overpass-Endpunkte ist gemessen, nicht geraten: `overpass.openstreetmap.fr`
beantwortete einen Block aus 20 Boxen in 5 Sekunden, während `kumi.systems` und `overpass-api.de`
denselben Block mit HTTP 504 abwiesen.

## Server

```bash
python3 server/app.py --port 8000        # http://127.0.0.1:8000
```

Keine Abhängigkeiten, kein Build. Python 3.11 genügt.

| Endpunkt | Zweck |
|---|---|
| `GET /api/meta` | Bestand und Stand der Datenbank |
| `GET /api/spots?lat&lon&radius_km&gap_m&only_enbw` | Umkreissuche |
| `POST /api/route-spots` | Route aus Adressen oder Wegpunkten, plus Treffer im Korridor |
| `GET /api/geocode?q=` | Ortssuche über Nominatim, mit Cache und 1 Anfrage pro Sekunde |
| `POST /api/plan?name=` | GPX oder ABRP-Excel hochladen, ergibt Wegpunkte |

Geocoding und Routing laufen bewusst über den Server: dort sitzt der Cache, dort wird das
Anfragelimit von Nominatim eingehalten, und der Client braucht nur einen Aufruf pro Suche.

## PWA

`web/` ist die komplette App: kein Bundler, keine npm-Abhängigkeiten, Leaflet liegt lokal unter
`web/vendor/`. Installierbar über das Manifest, offline nutzbar über den Service Worker
(App-Hülle aus dem Cache, `/api` aus dem Netz mit Cache als Notnagel).

Die Öffnungszeiten werden **im Browser** ausgewertet, nicht auf dem Server: das Ändern der
Abfahrtszeit ist damit sofort sichtbar und kostet keinen Roundtrip. `web/opening-hours.js` ist
die Portierung des Auswerters aus der Android-App, mit denselben Grenzen und denselben Tests.

## Tests

```bash
python3 -m unittest discover -s tests          # Geometrie, Datenbank, Datei-Import
node --test tests/opening-hours.test.mjs       # Öffnungszeiten im Browser-Code
```

Beide Testsätze arbeiten mit echten Daten: realen Koordinaten, dem echten Aufbau eines
ABRP-Excel-Exports und allen 54 unterschiedlichen `opening_hours`-Angaben aus der Stichprobe.

## Grenzen

- **Der Bestand ist ein Stichtag.** Was nach dem Ingest in OSM entsteht, fehlt bis zum nächsten
  Lauf. `/api/meta` nennt den Stand, die App zeigt ihn als Tooltip.
- **Gespeichert werden nur Säulen im Umkreis von 1 km um eine Filiale.** Alles andere ist für
  diese Frage nutzlos und würde die Datenbank um zwei Größenordnungen aufblähen. Ein größerer
  Maximalabstand als 1000 m braucht deshalb einen neuen Ingest.
- **"Nur EnBW" heißt: in OSM als EnBW getaggt.** Nicht: dort mit EnBW-Tarif ladbar. Roaming an
  Fremdsäulen ist damit nicht abgedeckt.
- **Routing und Geocoding hängen weiter an öffentlichen Diensten** (OSRM-Demoserver, Nominatim).
  Nur die eigentliche Suche ist davon unabhängig. Für echten Betrieb gehören dort eigene
  Instanzen hin.
- **Kartenkacheln kommen von openstreetmap.org.** Offline zeigt die App ihre Hülle und die
  zuletzt geholten Daten, aber keine Karte.

Daten: © OpenStreetMap-Mitwirkende, ODbL. Routing: OSRM. Ortssuche: Nominatim.
