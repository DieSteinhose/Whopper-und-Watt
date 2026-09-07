# Whopper & Watt als PWA

Dieselbe Frage wie die Android-App, andere Bauform: **Wo steht eine Ladesäule direkt neben
einem Burger King?** Diesmal als installierbare Web-App mit eigenem Server und eigener
Datenbank, statt bei jeder Suche live gegen OpenStreetMap zu fragen.

## Was man auswählen kann

Vier Kategorien, alle einzeln an- und abwählbar, und die Auswahl ist ein Oder:

| Knopf | Was drin ist |
|---|---|
| **Burger King** (Voreinstellung) | `brand:wikidata=Q177054` |
| **Subway** | `brand:wikidata=Q244457` |
| **Vegane Optionen** | `diet:vegan` = `yes`, `limited` oder `only`, **plus alle Burger King und Subway** |
| **Rein vegan** | `diet:vegan=only` |

Ein Lokal kann in mehreren Kategorien liegen, ein Burger King liegt immer in zweien. Deshalb
sind Kette und Ernährungsform getrennte Spalten in der Tabelle `store` und keine Werte
derselben Spalte.

**Warum die Ketten pauschal als vegan zählen.** Weil das OSM-Tag an ihnen unzuverlässig ist,
gemessen am Datenbestand vom 07.09.2026:

| Kette | Filialen | `diet:vegan` gesetzt | davon `no` |
|---|---:|---:|---:|
| Burger King | 952 | 144 (15,1 %) | 32 |
| Subway | 788 | 166 (21,1 %) | 22 |

Beide Ketten führen in Deutschland flächendeckend vegane Produkte. Die 54 Filialen mit
`diet:vegan=no` sind Mapping-Stand von vor der Menü-Umstellung, nicht Realität. Dem Tag hier zu
folgen hieße, Treffer wegen veralteter Kartendaten zu verstecken. Bei unabhängigen Lokalen ist
das Tag dagegen die einzige und beste Quelle, und dort wird es auch genau so ausgewertet.

**Wie dünn die Abdeckung ist**, deutschlandweit gemessen (Overpass, Bounding-Box, 07.09.2026,
durchgehend beschränkt auf `amenity` = `restaurant`, `fast_food` oder `cafe`):

| | Anzahl | Anteil |
|---|---:|---:|
| Gastronomie gesamt | 236.833 | |
| `diet:vegan` überhaupt gesetzt | 20.010 | 8,5 % |
| davon `yes` | 12.845 | |
| davon `only` | 509 | |
| davon `limited` | 350 | |
| davon `no` | 6.290 | |

Nicht einmal jedes zwölfte Lokal trägt das Tag. Das ist ein Grund, die Kategorie ehrlich zu
beschriften, aber keiner, sie wegzulassen: eine externe Quelle wie HappyCow hat keine offene
Schnittstelle und ist lizenzrechtlich mit dem ODbL-Bestand aus OSM nicht mischbar.

Außerhalb der Gastronomie tragen weitere **122** Objekte `diet:vegan=only`, überwiegend Läden
und Bäckereien. Die sind hier bewusst nicht drin: die App fragt nach Essen neben der Ladesäule,
und `amenity`-Beschriftung, Öffnungszeiten-Auswertung und Kartenbeschriftung sind auf Lokale
zugeschnitten. Wer sie will, braucht eine vierte Abfrage im Ingest und eine Beschriftung für
`shop`.

Eine Kette ist im Code eine Datenstruktur (`server/geo.py`, `BRANDS`) mit Wikidata-Objekt und
Namensvarianten, keine fest verdrahtete Bedingung. Eine dritte Kette ist ein Eintrag in dieser
Tabelle, ein Eintrag in `KINDS`, eine Bedingung in `KIND_CONDITIONS` (`server/db.py`) samt
Gegenstück in `KIND_TESTS` (`web/local-search.js`) und ein Knopf im HTML.

Der Anker ist `brand:wikidata`, denn das ist eindeutig und gut gepflegt: in der Stichprobe
trugen alle 49 Subway-Filialen im Ruhrgebiet und 22 von 23 Burger King im Raum Stuttgart
dieses Tag. Ohne Wikidata-Treffer wird ein passender Name nur bei `amenity=fast_food`,
`restaurant` oder `cafe` geglaubt, sonst wäre jeder U-Bahn-Zugang namens "Subway" eine
Filiale.

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

`server/ingest.py` baut sie in zwei Schritten:

1. **Lokale**, vier Abfragen über eine Bounding-Box: eine für die Ketten über `brand:wikidata`
   und Namen, dann je eine pro `amenity` für alles mit einem `diet:vegan`-Tag. Der Ingest holt
   immer alles, ausgewählt wird erst in der App.
2. **Ladesäulen** in einem festen 8×8-Raster über den Suchbereich, Zellen ohne Lokal fallen weg.

Gefiltert wird bei Schritt 1 auf das *Vorhandensein* von `diet:vegan`, nicht auf seine Werte:
das Tag hängt in der Gastronomie an 20.010 Objekten, `amenity=restaurant` allein an 138.261.
Die Instanz greift damit auf den kleinen Index zu. Die 6.290 Lokale mit `diet:vegan=no` kommen
unnötig mit und fliegen in Python raus, das ist billiger als eine Wertabfrage mit Alternativen.

**Warum ein Raster statt Boxen um jedes Lokal.** Schritt 2 lief früher in 1-km-Boxen um jede
einzelne Filiale, in Blöcken zu 20. Das war richtig, solange es um knapp zweitausend Filialen
ging (87 Abfragen). Mit den veganen Lokalen sind es über fünfzehntausend, also wären rund
achthundert Abfragen daraus geworden. Ein Raster über Deutschland sind dagegen 63, und zwar
unabhängig davon, wie viele Lokale noch dazukommen. Gemessener Lauf: **924 Sekunden für 15.189
Lokale, 58.335 Ladesäulen und 237.970 Paare.**

Das Raster holt alle Säulen im Suchbereich, auch die weitab von jedem Lokal. Nach dem
Paarebauen fliegen die wieder raus (58.335 → 25.456), sonst wäre die Datenbank 66 statt 51 MB.
Wer den Abstand später über den `--gap` dieses Laufs hinaus vergrößern will, braucht deshalb
einen frischen Ingest oder von vornherein `--keep-all-chargers`.

**Warum Bounding-Box und nicht Landesfläche:** die naheliegendere Abfrage
`area["ISO3166-1"="DE"]` setzt voraus, dass die Overpass-Instanz eine Area-Datenbank hat.
Ausgerechnet der schnellsten Instanz fehlt sie: `overpass.openstreetmap.fr` antwortet darauf
mit `runtime error: open64: 2 No such file or directory`. Eine Bounding-Box versteht jede
Instanz. Der Preis ist ein Überhang ins Ausland, und ein Burger King fünfzehn Kilometer hinter
der Grenze schadet niemandem. Wer es exakt will, nimmt `--country DE`; scheitert die
Flächensuche, fällt der Ingest automatisch auf die Bounding-Box zurück.

Danach werden die Paare (Lokal ↔ Säule bis 1000 m) einmal ausgerechnet und gespeichert, und
die Lokale kommen in einen R-Tree-Index. Eine Suche ist damit ein Index-Zugriff plus etwas
Arithmetik.

```bash
python3 server/ingest.py --db server/data/whopper.sqlite     # dauert rund 15 Minuten
python3 server/ingest.py --pairs-only                        # nur Paare neu rechnen
python3 server/ingest.py --bbox 46.4,9.5,49.0,17.2           # anderer Suchbereich
python3 server/ingest.py --charger-grid 12                   # feineres Raster, kleinere Abfragen
```

Der Lauf ist fortsetzbar: jede erledigte Rasterzelle wird sofort festgeschrieben, ein Abbruch
kostet nichts. Der Schlüssel enthält die Rastergröße, ein Lauf mit anderem `--charger-grid`
liest die alten Zellen also nicht fälschlich als erledigt. Die Reihenfolge der
Overpass-Endpunkte ist gemessen, nicht geraten: `overpass.openstreetmap.fr` beantwortete einen
Block aus 20 Boxen in 5 Sekunden, während `kumi.systems` und `overpass-api.de` denselben Block
mit HTTP 504 abwiesen.

Eine bestehende Datenbank aus einer älteren Version wird beim Öffnen migriert (`amenity`,
`vegan`, `vegan_only` werden per `ALTER TABLE` ergänzt), sie muss nicht weggeworfen werden.

**Die Datenbank liegt nicht im Repository.** Sie ist rund 50 MB groß und ändert sich bei jedem
Ingest komplett; als Binärdatei in der Git-Historie wäre sie am falschen Platz. Der Workflow
hält sie stattdessen im Actions-Cache, lokal baut sie `server/ingest.py`.

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
| `GET /api/spots?lat&lon&radius_km&gap_m&only_enbw&kinds` | Umkreissuche, `kinds` als Kommaliste (`bk`, `subway`, `vegan`, `vegan_only`) |
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

## Zwei Betriebsarten aus einem Quellstand

|  | mit Server | ohne Server |
|---|---|---|
| Suche | SQLite auf dem Server | im Browser, aus `data/spots*.json` |
| Geocoding und Routing | über den Server, mit Cache | direkt an Nominatim und OSRM |
| Offline | Hülle aus dem Cache | Hülle **und Suche** aus dem Cache |
| Hosting | eigener Rechner | jede Dateiablage, etwa GitHub Pages |

Die App erkennt die Betriebsart selbst: der eigene Server schickt den Header `X-Whopper-Backend`
mit, auf einer reinen Dateiablage fehlt er. Kein Testaufruf ins Leere, der in jeder
Browserkonsole als Fehler stünde.

## GitHub Pages

Das geht, mit einer Einschränkung und einer Klarstellung.

**Die Einschränkung:** Pages liefert nur Dateien aus, dort läuft kein Python. Deshalb der
Betrieb ohne Server: der Datenbestand liegt als JSON daneben und die Suche rechnet der Browser.
Das ist sogar die bessere PWA, denn nach dem ersten Laden funktioniert die Suche auch ohne
Netz. Was ohne Server nicht offline geht, ist Geocoding und Routing, denn dafür braucht es
Nominatim und OSRM.

**In zwei Teilen**, und das ist der springende Punkt seit den veganen Kategorien:

| Datei | Inhalt | roh | gepackt | wann geladen |
|---|---:|---:|---:|---|
| `data/spots.json` | 1.651 Kettenfilialen | 2,8 MB | 445 KB | immer, beim Start |
| `data/spots-vegan.json` | 12.655 vegane Lokale | 35,6 MB | 5,0 MB | erst beim Anhaken |

In einer Datei wären das 5,5 MB gepackt und 38 MB entpackt, die jede Installation beim Start
herunterladen und durch `JSON.parse` schicken müsste, auch für eine Suche nach Burger King.
Der Service Worker installiert deshalb nur den kleinen Teil vorab; der große landet beim ersten
Abruf im Cache und ist ab dann ebenfalls offline da. Gemessen im Browser: eine Suche nach
Burger King rührt `spots-vegan.json` nicht an, die erste vegane Suche kostet einmalig gut eine
Sekunde, danach 8 ms.

**Anzeigegrenzen.** Eine vegane Suche über 100 km um Berlin liefert gemessen 1.934 Treffer.
Die Liste zeigt die ersten 200 und sagt, wie viele es insgesamt sind; die Karte zeichnet
höchstens 250 Lokale mit je ihrer nächstgelegenen Säule. Ungebremst legte Leaflet für jeden
Marker ein DOM-Element an, zehntausende davon, und der Kartenreiter ließ sich nicht mehr
öffnen. Beide Listen sind vorsortiert, nach Entfernung oder nach Strecke ab Start, "die ersten
N" ist also die sinnvolle Auswahl.

**Die Klarstellung zur Adresse:** `whopperundwatt.github.io` gibt es nur, wenn ein
GitHub-Konto oder eine Organisation **genau so heißt** und dort ein Repository namens
`whopperundwatt.github.io` liegt. Unter dem bestehenden Konto lautet die Adresse

```
https://<konto>.github.io/<repository>/
```

Deshalb sind alle Pfade in der App relativ: unter einem Unterverzeichnis würde jeder absolute
Pfad ins Leere zeigen, samt Service Worker und Manifest.

Einmalig nötig: **Settings → Pages → Source: GitHub Actions**. Ohne das schlägt der
Deploy-Schritt fehl.

## Jede Nacht neu

`.github/workflows/nightly.yml` läuft um 03:17 UTC und lässt sich jederzeit von Hand starten:

1. **Tests**, Python und Node.
2. **Datenbestand**: frischer Ingest in eine eigene Datei. Klappt er, ersetzt er den Stand aus
   dem Repository; klappt er nicht, bleibt der alte und der Lauf geht weiter, statt die Seite
   abzuschalten. Danach laufen die Suchtests gegen den frischen Bestand, gefolgt von einer
   Plausibilitätsprüfung: zu wenige Lokale, zu wenige Säulen, zu wenige Kettenfilialen oder
   gar keine veganen Lokale außer den Ketten brechen den Lauf ab. Die letzte Bedingung fängt
   genau den Fall ab, dass die `diet:vegan`-Abfrage still ins Leere läuft und der Bestand
   trotzdem groß genug aussieht.
3. **Pages**: das Verzeichnis `web/` samt frischer `data/spots.json` und `data/spots-vegan.json`
   wird veröffentlicht.

Bei einem gewöhnlichen Commit auf `pwa` wird nicht neu eingesammelt: das dauert Minuten und
belastet fremde Server, und für eine Codeänderung verschieben sich keine Ladesäulen. Der Lauf
nimmt dann den Datenstand aus dem Repository und deployt in Sekunden.

**GitHub-Eigenheit, die hier zählt:** geplante Läufe starten ausschließlich auf dem
**Standard-Branch**. Der Branch `pwa` muss also der Standard sein, sonst feuert der Zeitplan
nicht. Von Hand über "Run workflow" geht es trotzdem.

**Der nächtliche Lauf betrifft nur die PWA**, und das ist kein Versehen. Die Android-App auf
dem Branch `app` fragt OpenStreetMap zur Laufzeit, sie hat gar keinen Datenbestand, der
veralten könnte. Ihr Preis dafür sind die 11 bis 459 Sekunden pro Suche, die überhaupt erst
der Anlass für diese Bauform waren. Eine neue APK entsteht nur, wenn sich ihr Quelltext ändert,
nicht weil in Bochum eine Ladesäule dazugekommen ist.

## Tests

```bash
python3 -m unittest discover -s tests          # Geometrie, Datenbank, Datei-Import
node --test tests/opening-hours.test.mjs       # Öffnungszeiten im Browser-Code
node --test tests/local-search.test.mjs        # Suche im Browser, gegen beide Exportdateien
```

`tests/local-search.test.mjs` überspringt die datenabhängigen Fälle, solange die Exportdateien
fehlen (sie sind nicht eingecheckt). Erst `server/ingest.py` und `server/export_static.py`
laufen lassen, dann laufen sie vollständig, und dann prüfen sie auch, dass eine Suche nach
Burger King den großen Zusatzteil gar nicht erst anfasst.

Beide Testsätze arbeiten mit echten Daten: realen Koordinaten, dem echten Aufbau eines
ABRP-Excel-Exports und allen 54 unterschiedlichen `opening_hours`-Angaben aus der Stichprobe.

## Grenzen

- **Der Bestand ist ein Stichtag.** Was nach dem Ingest in OSM entsteht, fehlt bis zum nächsten
  Lauf. `/api/meta` nennt den Stand, die App zeigt ihn als Tooltip.
- **Paare werden bis 1000 m gerechnet.** Ein größerer Maximalabstand braucht `--pairs-only`
  mit einem größeren `--gap`, und in den statischen Export gehen ohnehin nur Säulen, die zu
  einem Lokal gehören.
- **"Vegane Optionen" ist nur so gut wie das OSM-Tag.** Deutschlandweit trägt es nicht einmal
  jedes zehnte Lokal, und ein fehlendes Tag heißt nicht, dass es dort nichts Veganes gibt. Die
  Kategorie zeigt, was getaggt ist, plus die beiden Ketten. Sie ist keine vollständige Liste,
  und sie kann es mit dieser Datenquelle auch nicht sein.
- **`diet:vegan=no` wird bei den Ketten bewusst ignoriert**, siehe oben. Bei allen anderen
  Lokalen wird es respektiert, dort ist es die beste verfügbare Aussage.
- **"Nur EnBW" heißt: in OSM als EnBW getaggt.** Nicht: dort mit EnBW-Tarif ladbar. Roaming an
  Fremdsäulen ist damit nicht abgedeckt.
- **Routing und Geocoding hängen weiter an öffentlichen Diensten** (OSRM-Demoserver, Nominatim).
  Nur die eigentliche Suche ist davon unabhängig. Für echten Betrieb gehören dort eigene
  Instanzen hin.
- **Kartenkacheln kommen von openstreetmap.org.** Offline zeigt die App ihre Hülle und die
  zuletzt geholten Daten, aber keine Karte.

Daten: © OpenStreetMap-Mitwirkende, ODbL. Routing: OSRM. Ortssuche: Nominatim.
