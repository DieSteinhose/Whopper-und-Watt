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
python3 server/app.py --port 8000                    # nur dieser Rechner
python3 server/app.py --host 0.0.0.0 --trust-proxy    # im Netz erreichbar
```

Keine Abhängigkeiten, kein Build. Python 3.11 genügt.

Der Standard `127.0.0.1` ist Absicht: an alle Schnittstellen zu binden gehört eine bewusste
Entscheidung, kein Vorgabewert. Beim Start werden die tatsächlich erreichbaren Adressen
ausgegeben.

| Endpunkt | Zweck |
|---|---|
| `GET /api/meta` | Bestand und Stand der Datenbank |
| `GET /api/spots?lat&lon&radius_km&gap_m&only_enbw` | Umkreissuche |
| `POST /api/route-spots` | Route aus Adressen oder Wegpunkten, plus Treffer im Korridor |
| `GET /api/geocode?q=` | Ortssuche über Nominatim, mit Cache und 1 Anfrage pro Sekunde |
| `POST /api/plan?name=` | GPX oder ABRP-Excel hochladen, ergibt Wegpunkte |

Geocoding und Routing laufen bewusst über den Server: dort sitzt der Cache, dort wird das
Anfragelimit von Nominatim eingehalten, und der Client braucht nur einen Aufruf pro Suche.

## Betrieb im Netz, etwa hinter Zoraxy

Meldet der Reverse Proxy **Error 521 (Web server is down)**, hat er den Server nicht erreicht.
Der häufigste Grund ist die Bindeadresse: `127.0.0.1` nimmt nur Verbindungen von demselben
Rechner an. Läuft der Proxy in einem Container, einer VM oder auf einem anderen Host, ist das
für ihn ein anderer Rechner.

1. Server mit `--host 0.0.0.0 --trust-proxy` starten.
2. Im Proxy als Ziel die **IP des Rechners** eintragen, nicht `127.0.0.1`, außer der Proxy läuft
   wirklich daneben.
3. Port in der Firewall freigeben.
4. **TLS im Proxy einschalten.** Ohne HTTPS ist die Seite kein Secure Context: der Service
   Worker registriert sich nicht, die App ist nicht installierbar, und der Browser rückt
   keinen Standort heraus. Über eine nackte IP bleibt sie eine gewöhnliche Webseite.

`--trust-proxy` lässt den Server `X-Forwarded-For` auswerten, damit die Bremse pro Client
greift und nicht pro Proxy. Nur setzen, wenn wirklich ein Proxy davorsteht, sonst kann sich
jeder eine fremde Adresse ausdenken.

Als Dienst:

```ini
[Unit]
Description=Whopper und Watt
After=network-online.target

[Service]
WorkingDirectory=/opt/enbw-plus-whopper
ExecStart=/usr/bin/python3 server/app.py --host 0.0.0.0 --port 8000 --trust-proxy
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/enbw-plus-whopper/server/data

[Install]
WantedBy=multi-user.target
```

Was der Server von sich aus mitbringt, sobald er offen steht: eine Bremse von 20 Anfragen pro
Minute und IP auf die Endpunkte, die nach draußen telefonieren oder Dateien auspacken
(`/api/geocode`, `/api/route-spots`, `/api/plan`); die eigentliche Suche bleibt frei, weil sie
aus der lokalen Datenbank kommt. Dazu eine Content-Security-Policy, `nosniff`,
`Referrer-Policy: no-referrer`, eine Obergrenze von 512 KB je Upload und eine Deckelung beim
Entpacken, damit eine kleine Zip-Datei den Speicher nicht sprengt.

Was er nicht mitbringt: TLS, Authentifizierung und Logrotation. Das ist Sache des Proxys.

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
