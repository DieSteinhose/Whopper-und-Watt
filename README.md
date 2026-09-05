# Whopper & Watt

Android-App, die genau eine Frage beantwortet: **Wo steht eine Ladesäule direkt neben einem Burger King?**

Hintergrund: Es gibt keine App und keine Website, die genau diese Kombination zeigt. Die EnBW
mobility+ App kennt nur Ladesäulen, die Burger King App kennt nur Filialen, und Google Maps
zeigt zwar beides, aber ohne "zeig mir nur die Paare"-Filter. Diese App macht die Verschneidung.

## Was die App macht

1. Standort holen (oder Ort eintippen, Geocoding über Nominatim).
2. Eine Overpass-Abfrage gegen OpenStreetMap: alle Ladesäulen und alle Burger King im gewählten Umkreis.
3. Paare bilden: jede Filiale, zu der eine Ladesäule innerhalb des eingestellten Abstands steht.
4. Anzeige als Liste (sortiert nach Entfernung von dir) und als Karte mit Verbindungslinie
   zwischen Filiale und Säule.
5. Buttons "Zur Säule" / "Zum BK" öffnen die Navigation über einen `geo:`-Intent.

Einstellbar sind Umkreis (10/25/50/100 km), maximaler Abstand zwischen Säule und Filiale
(100/300/500/1000 m) und der Filter "nur EnBW".

## Grenzen, die man kennen sollte

- **Datenquelle ist OpenStreetMap, nicht EnBW.** "Nur EnBW" heißt: in OSM als EnBW getaggt
  (`operator`, `network`, `ref:EnBW`, `operator:wikidata`). Säulen ohne gepflegten Betreiber
  fallen durch den Filter, obwohl sie EnBW sein können. Umgekehrt sagt der Filter nichts darüber,
  ob du dort mit deinem EnBW-Tarif laden kannst, Roaming an Fremdsäulen ist damit nicht abgedeckt.
  Wer alles sehen will, schaltet den Filter aus.
- **Kein Live-Status.** OSM kennt keine Belegung und keine aktuellen Preise.
- **Overpass ist ein Community-Dienst.** Bei 100 km Umkreis dauert eine Abfrage rund eine Minute,
  bei 25 km etwa zehn Sekunden. Die App fragt pro Suche genau einmal an und probiert drei
  Endpunkte durch.
- Filialen werden über `brand:wikidata=Q177054`, `brand="Burger King"` und `name="Burger King"`
  gefunden. Exakte Tag-Vergleiche sind Absicht: die Regex-Variante läuft bei großem Umkreis in
  den Overpass-Timeout.

## Bauen

Voraussetzung: JDK 17+, Android SDK mit Platform 35 und Build-Tools 35.

```bash
./gradlew :app:assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:assembleRelease        # app/build/outputs/apk/release/app-release.apk
./gradlew :app:testDebugUnitTest      # Unit-Tests für Distanz-, Paar- und Tag-Logik
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

Kartendaten und POI-Daten: © OpenStreetMap-Mitwirkende, ODbL.
