# Whopper & Watt

Android-App, die genau eine Frage beantwortet: **Wo steht eine Ladesäule direkt neben einem Burger King?**

Hintergrund: Es gibt keine App und keine Website, die genau diese Kombination zeigt. Die EnBW
mobility+ App kennt nur Ladesäulen, die Burger King App kennt nur Filialen, und Google Maps
zeigt zwar beides, aber ohne "zeig mir nur die Paare"-Filter. Diese App macht die Verschneidung.

## Zwei Suchmodi

**Umkreis**: Standort holen oder Ort eintippen, dann alles im gewählten Radius (10 bis 100 km).

**Route**: Start und Ziel eintippen, oder eine GPX-Datei laden. Die Treffer sind nach gefahrenen
Kilometern ab Start sortiert und zeigen, wie weit sie neben der Route liegen. Der Korridor ist
auf 1, 3 oder 5 km einstellbar.

In beiden Fällen gilt: einstellbarer Maximalabstand zwischen Säule und Filiale (100 bis 1000 m),
Filter auf EnBW, Liste und Karte mit Verbindungslinie, Navigation per `geo:`-Intent.

## Pläne aus A Better Routeplanner

**Share-Links gehen nicht.** ABRP hat keine offene Schnittstelle, über die sich ein Link
(`?plan_uuid=...`) von außen auflösen ließe. Getestet: `api.iternio.com` antwortet ohne API-Key
mit 404, im Web-Bundle steht der Endpunkt nicht, und der interne Web-Key von ABRP gehört nicht
in eine fremde App. Wer so einen Link ins Suchfeld klebt, bekommt genau diesen Hinweis.

**Dateien gehen**, über "Plan öffnen". Zwei Formate, mit einem wichtigen Unterschied:

- **GPX**: enthält die gefahrene Strecke punktgenau. Wird direkt übernommen.
- **Excel (.xlsx)**: enthält **keine Koordinaten**, nur die Adresstexte der Wegpunkte in
  Spalte A. Die App filtert Kopf-, Summen- und Einheitenzeilen heraus, geocodiert die
  übrigen Adressen über Nominatim (eine Anfrage pro Sekunde, wie es die Nutzungsregeln
  verlangen) und lässt OSRM eine Route durch diese Punkte legen.

Die Grenze des Excel-Exports: Wegpunkte, die in ABRP per Klick auf die Karte gesetzt wurden,
stehen dort als "Punkt auf der Karte" und tragen überhaupt keine Ortsangabe. Aus so einem
Punkt lässt sich nichts rekonstruieren. Bleibt dadurch nur eine einzige Adresse übrig, trägt
die App sie ins Zielfeld ein und sagt, dass der Start fehlt. Wer den Export brauchbar haben
will, setzt die Wegpunkte in ABRP über die Adresssuche statt per Kartenklick.

## Warum die Abfragen so aussehen, wie sie aussehen

Die Abfrageform ist gemessen, nicht geraten. Alle Zahlen von echten Läufen gegen öffentliche
Overpass-Instanzen:

| Variante | Ergebnis |
|---|---|
| Regex `~"burger king",i`, 25 km Umkreis | Timeout |
| Exakte Tags (`brand:wikidata=Q177054`), 25 km Umkreis | 11 s, 23 Filialen, 1369 Säulen |
| Route 419 km, `around:` entlang der Polylinie | 88 s, teils Timeout |
| Route 419 km, Bounding-Boxen je Abschnitt | **21 s, 55 Filialen** |
| Säulen an 20 Filialen per `around:` | 143 s |
| Säulen an 20 Filialen per kleiner Box | 28 s |

Ein kompletter Durchlauf Dortmund nach Oberhausen (52 km, Korridor 3 km) über beide Schritte:
13 Filialen in 13 s, dazu 73 Säulen, daraus 4 EnBW-Kombis und 8 über alle Betreiber.

Daraus folgt: Umkreissuche als eine einzige `around:`-Abfrage, Routensuche über
Bounding-Boxen entlang der Strecke, danach die Säulen in kleinen Boxen um die gefundenen
Filialen. Was zu weit von der Route weg liegt, fliegt erst auf dem Gerät raus, weil das
nichts kostet. Zwischenstände werden während der Suche schon angezeigt.

## Grenzen, die man kennen sollte

- **Datenquelle ist OpenStreetMap, nicht EnBW.** "Nur EnBW" heißt: in OSM als EnBW getaggt
  (`operator`, `network`, `ref:EnBW`, `operator:wikidata`). Säulen ohne gepflegten Betreiber
  fallen durch den Filter, obwohl sie EnBW sein können. Umgekehrt sagt der Filter nichts darüber,
  ob du dort mit deinem EnBW-Tarif laden kannst, Roaming an Fremdsäulen ist damit nicht abgedeckt.
  Wer alles sehen will, schaltet den Filter aus.
- **Kein Live-Status.** OSM kennt keine Belegung und keine aktuellen Preise.
- **Overpass ist ein Community-Dienst mit Rate-Limits.** Mehrere schwere Abfragen kurz
  hintereinander werden mit HTTP 429 oder 504 abgewiesen. Die App probiert drei Endpunkte durch
  und sagt es dann klar an, statt eine leere Liste zu zeigen.
- **Routing läuft über den OSRM-Demoserver**, der ausdrücklich für kleine Lasten gedacht ist.
  Für eine App mit vielen Nutzern gehört dort ein eigener Router hin.
- Filialen werden über `brand:wikidata=Q177054`, `brand="Burger King"` und `name="Burger King"`
  gefunden. In der Stichprobe Stuttgart hingen 22 von 23 Filialen an `brand:wikidata`, eine
  wurde nur über den Namen gefunden.

## Bauen

Voraussetzung: JDK 17+, Android SDK mit Platform 35 und Build-Tools 35.

```bash
./gradlew :app:assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:assembleRelease        # app/build/outputs/apk/release/app-release.apk
./gradlew :app:testDebugUnitTest      # Unit-Tests für Distanz-, Routen-, Box- und Tag-Logik
```

Der Release-Build ist mit R8 verkleinert und mit dem Debug-Key signiert, damit er ohne eigenen
Keystore installierbar ist. Für eine Veröffentlichung im Play Store gehört dort ein echter
Release-Key hin.

Die APK ist nicht über den Play Store signiert, zum Installieren also "Installation aus
unbekannten Quellen" für die installierende App erlauben.

## Technik

Kotlin, Jetpack Compose (Material 3), osmdroid für die Karte, OkHttp für die HTTP-Aufrufe,
Android-`LocationManager` statt Play Services (läuft damit auch auf Geräten ohne Google-Dienste).
minSdk 24, targetSdk 35.

Kartendaten und POI-Daten: © OpenStreetMap-Mitwirkende, ODbL. Routing: OSRM. Ortssuche: Nominatim.
