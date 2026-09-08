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

## Welche Ladesäule

Ein zweiter Satz Knöpfe, unabhängig von der Auswahl oben. Hier heißt **nichts angehakt: alle
Netze**, anders als bei den Kategorien, wo nichts angehakt nichts gesucht bedeutet. Das steht
deshalb in der Beschriftung der Zeile, statt es erraten zu lassen.

| Knopf | Was drin ist | Säulen im Bestand |
|---|---|---:|
| **EnBW** (Voreinstellung) | `operator:wikidata=Q644304` oder "EnBW" im Betreiber | 1.221 |
| **Lidl** | `operator:wikidata=Q151954` oder "Lidl" im Betreiber | 289 |
| **Kaufland** | `operator:wikidata=Q685967` oder "Kaufland" im Betreiber | 139 |
| **ab 50 kW** | `power_kw >= 50` aus den OSM-Leistungstags | 3.509 |

Die Wikidata-Ids sind am Datenbestand geprüft, nicht aus dem Kopf. Dabei kam heraus, dass der
bisherige EnBW-Anker `Q321820` deutschlandweit **null mal** vorkommt und damit wirkungslos war;
gerettet hat das nur die Namenssuche. Richtig ist `Q644304`, an 919 Säulen.

**Die Leistungsangabe fehlt oft.** Von 25.456 Säulen im Bestand haben 9.188 überhaupt eine
Leistung in OSM, also gut ein Drittel. "ab 50 kW" blendet alles ohne Angabe aus, und das steht
im Tooltip des Knopfs. Eine Säule ohne Tag ist nicht langsam, sie ist unbekannt.

## Ad-hoc-Preise: nicht aus OpenStreetMap

Ein Filter "unter 50 ct" wäre über OSM-Daten nicht ehrlich zu bauen, gemessen am 08.09.2026:

| | Anzahl | Anteil |
|---|---:|---:|
| Ladesäulen in Deutschland | 58.178 | |
| mit `fee` (nur ja/nein) | 28.174 | 48 % |
| **mit `charge` (dem Preisfeld)** | **914** | **1,6 %** |
| mit `socket:*:charge` | 0 | |

Und die 914 Werte sind freier Text in einem Dutzend Formaten. Aus dem eigenen Bestand:
`0.38 EUR/kWh`, `0,55 €/kWh`, `0.23 EUR/kWh + 0.04 EUR/min`, `75ct/kWh Ad-hoc`,
`0,39€/kWh mit Blockiergebühr`, `1€/1H`. Manche sind Zeittarife, manche enthalten
Blockiergebühren, manche sind gar keine Ad-hoc-Preise. Dazu kommt das Grundproblem: Tarife
ändern sich, OSM wird dabei nicht mitgepflegt. Ein Preis von 2023 ist schlechter als kein Preis.

Wer echte Ad-hoc-Preise will, braucht eine Quelle, die genau dafür gebaut ist. Die einzige mit
brauchbarer Abdeckung in Europa ist [Chargeprice](https://www.chargeprice.net/de/charging-intelligence-daten/):
eine [API](https://chargeprice.github.io/chargeprice-api-docs/) mit Ad-hoc, Direktzahlung,
Kreditkarte, Roaming und Tageszeittarifen, mehrmals wöchentlich aktualisiert. Kostenloser
Demo-Zugang auf Anfrage, allerdings mit eingeschränkten Daten und ausdrücklich ohne
kommerzielle Nutzung. Das Bundesnetzagentur-Ladesäulenregister hat Standorte und Leistung, aber
keine Preise; Open Charge Map hat ein Freitextfeld mit ähnlich dünner Abdeckung wie OSM.

Der Lidl- und der Kaufland-Knopf sind der ehrliche Ersatz: statt einen Preis zu behaupten, den
die Daten nicht hergeben, wird nach den Betreibern gefiltert, die günstig sind.

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

1. **Lokale**, zwei Abfragen über eine Bounding-Box: eine für die Ketten über `brand:wikidata`
   und Namen, eine für alles mit einem `diet:vegan`-Tag. Der Ingest holt immer alles,
   ausgewählt wird erst in der App.
2. **Ladesäulen** in einem festen 8×8-Raster über den Suchbereich, Zellen ohne Lokal fallen weg.

Gefiltert wird bei Schritt 1 auf das *Vorhandensein* von `diet:vegan`, nicht auf seine Werte
und nicht auf die Art des Lokals: das Tag hängt in der Gastronomie an 20.010 Objekten,
`amenity=restaurant` allein an 138.261. Die Instanz greift damit auf den kleinen Index zu.
Die 6.290 Lokale mit `diet:vegan=no` und die paar Automaten und Läden kommen unnötig mit und
fliegen in Python raus. Gemessen: drei Abfragen mit `amenity`-Filter zusammen 383 Sekunden,
diese eine 154.

**Warum ein Raster statt Boxen um jedes Lokal.** Schritt 2 lief früher in 1-km-Boxen um jede
einzelne Filiale, in Blöcken zu 20. Das war richtig, solange es um knapp zweitausend Filialen
ging (87 Abfragen). Mit den veganen Lokalen sind es über fünfzehntausend, also wären rund
achthundert Abfragen daraus geworden. Ein Raster über Deutschland sind dagegen 63, und zwar
unabhängig davon, wie viele Lokale noch dazukommen.

Das Raster holt alle Säulen im Suchbereich, auch die weitab von jedem Lokal. Nach dem
Paarebauen fliegen die wieder raus (58.335 → 25.456), sonst wäre die Datenbank 66 statt 51 MB.
Wer den Abstand später über den `--gap` dieses Laufs hinaus vergrößern will, braucht deshalb
einen frischen Ingest oder von vornherein `--keep-all-chargers`.

**Zwei Abfragen gleichzeitig.** Ein großer Teil der Zeit ist Warteschlange, nicht Rechnen.
Gemessen an disjunkten Rasterzellen auf `overpass.openstreetmap.fr`: eine Abfrage nach der
anderen 175 Objekte/s, zwei gleichzeitig 331, drei 521. Bei zwei bleibt es trotzdem, denn das
ist es, was die öffentlichen Instanzen je IP zugestehen; wer eine eigene Instanz betreibt,
dreht `--workers` hoch.

Über *mehrere* Instanzen zu verteilen ist dagegen keine gute Idee, auch gemessen: dieselben
acht Zellen brauchten sequenziell auf `openstreetmap.fr` 31 Sekunden, verteilt auf vier
Instanzen 174, weil drei davon mit HTTP 504 ausstiegen. Die anderen Endpunkte bleiben deshalb
das, was sie sind: Ausweichlager, wenn der erste nicht antwortet.

**Was das zusammen gebracht hat**, derselbe Datenbestand, dieselbe Bounding-Box:

| | vorher | nachher |
|---|---:|---:|
| Lokale abfragen | 640 s (4 Abfragen) | 259 s (2, gleichzeitig) |
| Ladesäulen-Raster | 280 s (63 nacheinander) | 114 s (63, zwei gleichzeitig) |
| Paare, Index, Aufräumen | 4 s | 22 s |
| **Gesamtlauf** | **924 s** | **395 s** |

Ergebnis identisch: 15.189 Lokale, 58.335 gefundene Ladesäulen, 237.970 Paare.

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
python3 server/ingest.py --db server/data/whopper.sqlite     # dauert rund 7 Minuten
python3 server/ingest.py --pairs-only                        # nur Paare neu rechnen
python3 server/ingest.py --bbox 46.4,9.5,49.0,17.2           # anderer Suchbereich
python3 server/ingest.py --charger-grid 12                   # feineres Raster, kleinere Abfragen
python3 server/ingest.py --workers 4                         # nur gegen eine eigene Instanz
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
| `GET /api/spots?lat&lon&radius_km&gap_m&kinds&networks&min_power_kw` | Umkreissuche. `kinds` als Kommaliste (`bk`, `subway`, `vegan`, `vegan_only`), `networks` als Kommaliste (`enbw`, `lidl`, `kaufland`), leer heißt alle |
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
| `data/spots.json` | 1.651 Kettenfilialen | 1,1 MB | 194 KB | immer, beim Start |
| `data/spots-vegan.json` | 12.655 vegane Lokale | 8,7 MB | 1,52 MB | erst beim Anhaken |

In einer ungekürzten Datei wären das 5,5 MB gepackt und 38 MB entpackt, die jede Installation
beim Start herunterladen und durch `JSON.parse` schicken müsste, auch für eine Suche nach
Burger King. Zwei Maßnahmen bringen das auf 1,75 MB:

**Aufteilen.** Der Service Worker installiert nur den kleinen Teil vorab; der große landet beim
ersten Abruf im Cache und ist ab dann ebenfalls offline da. Gemessen im Browser: eine Suche
nach Burger King rührt `spots-vegan.json` nicht an, die erste vegane Suche kostet einmalig
knapp eine Sekunde, danach 17 ms.

**Säulenlisten kürzen.** In der Stadt liegen bei 1000 m im Schnitt 17 Ladesäulen an einem
Lokal, und diese Listen waren der Löwenanteil der Datei. Exportiert werden je Lokal die drei
nächsten EnBW- und die drei nächsten Fremdsäulen: 5,21 → 1,56 MB gepackt.

Das kostet **keine** Treffer, denn die App braucht je Filterstellung nur zwei Dinge, und beide
bleiben exakt: *gibt es hier überhaupt eine passende Säule bis X Meter* und *welche ist die
nächste*. Die nächste Lidl-Säule ist die nächste bei jedem Abstand, der sie einschließt.

Eine Klasse ist ein Paar aus **Netz und Leistungsstufe** (`geo.POWER_STEPS`), denn genau danach
lässt sich filtern. Beim Bauen habe ich zuerst nur nach Netz und "schneller als 50 kW"
klassiert, und eine Prüfung mit Schwelle 150 kW verlor prompt 47 Lokale: deren 150-kW-Säule
fiel raus, weil zwei 50-kW-Säulen näher dran waren. Wer einen Knopf "ab 150 kW" ergänzt, muss
die Stufe in `POWER_STEPS` mitnehmen; der Test `test_capped_export_answers_like_the_full_list`
schlägt sonst fehl.

Gegengeprüft an echten Daten: **48 Kombinationen aus Netzauswahl, Leistungsstufe und Abstand,
null Abweichungen** zwischen gekürzter Datei und voller Datenbank. Und im Browser gegen den
laufenden Server: **60 Kombinationen, null Abweichungen**.

Ungenau wird allein die Zeile "N Standorte in Reichweite". Deshalb reist `chargersCapped` mit,
und die App schreibt dann "3+" statt "3". Mit Server steht dort weiterhin die exakte Zahl, denn
dort kürzt nichts.

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
- **Eine Netzauswahl heißt: in OSM so getaggt.** Nicht: dort mit dem Tarif dieses Anbieters
  ladbar. Roaming an Fremdsäulen ist damit nicht abgedeckt.
- **"ab 50 kW" kennt nur, was eine Leistungsangabe hat**, und das ist gut ein Drittel der
  Säulen. Eine Säule ohne Tag ist nicht langsam, sie ist unbekannt, und sie fällt raus.
- **Preise gibt es hier nicht**, siehe oben: OSM hat sie an 1,6 Prozent der Säulen, als freien
  Text, ohne Pflege bei Tarifwechseln.
- **Routing und Geocoding hängen weiter an öffentlichen Diensten** (OSRM-Demoserver, Nominatim).
  Nur die eigentliche Suche ist davon unabhängig. Für echten Betrieb gehören dort eigene
  Instanzen hin.
- **Kartenkacheln kommen von openstreetmap.org.** Offline zeigt die App ihre Hülle und die
  zuletzt geholten Daten, aber keine Karte.

Daten: © OpenStreetMap-Mitwirkende, ODbL. Routing: OSRM. Ortssuche: Nominatim.
