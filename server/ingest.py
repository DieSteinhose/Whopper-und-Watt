#!/usr/bin/env python3
"""Baut die lokale Datenbank aus OpenStreetMap.

Der Sinn der Uebung: die Live-Abfragen gegen Overpass dauerten je nach Serverlast
zwischen 10 Sekunden und mehreren Minuten und wurden regelmaessig mit HTTP 429
oder 504 abgewiesen. Einmal einsammeln, danach lokal beantworten.

Zwei Schritte:
  1. Lokale: die Ketten ueber brand:wikidata, dazu alles mit einem diet:vegan-Tag.
  2. Ladesaeulen in einem festen Raster ueber den Suchbereich.

Schritt 2 lief frueher in kleinen Boxen um jede einzelne Filiale. Das war richtig,
solange es um knapp zweitausend Filialen ging. Mit den veganen Lokalen sind es
ueber fuenfzehntausend, und daraus waeren rund achthundert Abfragen geworden. Ein
Raster ueber Deutschland sind dagegen 63, unabhaengig davon, wie viele Lokale
dazukommen. Gemessen: 58335 Ladesaeulen liegen in der Bounding-Box, von denen
25456 in Reichweite eines Lokals liegen und gespeichert bleiben.

Abgefragt werden zwei Sachen gleichzeitig, siehe run_queries: ein grosser Teil der
Zeit ist Warteschlange, nicht Rechnen. Zusammen mit der einen statt drei Abfragen
fuer diet:vegan bringt das den Lauf von 924 auf 395 Sekunden, bei identischem
Ergebnis.

Die oeffentlichen Instanzen weisen Abfragen zwischendurch ab, deshalb ist der Lauf
fortsetzbar: jede erledigte Rasterzelle wird sofort festgeschrieben, ein Neustart
macht dort weiter.

    python3 server/ingest.py --db server/data/whopper.sqlite
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from geo import (  # noqa: E402
    BRANDS,
    FOOD_AMENITIES,
    Brand,
    address_of,
    chunked,
    has_vegan_options,
    haversine_m,
    is_car_charger,
    is_enbw,
    is_vegan_only,
    network_of,
    lat_degrees_for_meters,
    lon_degrees_for_meters,
    matches_brand,
    max_power_kw,
)
from schema import connect, migrate  # noqa: E402,F401  (connect wird hier benutzt)

# Reihenfolge ist gemessen: die franzoesische Instanz beantwortete einen Block aus
# 20 Boxen in 5 Sekunden, waehrend kumi und overpass-api.de denselben Block mit
# HTTP 504 abwiesen. Die anderen bleiben als Ausweichlager stehen.
ENDPOINTS = (
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
USER_AGENT = "WhopperUndWatt/2.0 (PWA-Ingest; OpenStreetMap-Daten via Overpass)"

CHARGER_SEARCH_M = 1000
# Zellen je Achse. Acht mal acht sind 64 Zellen ueber Deutschland, von denen die
# ueber Nord- und Ostsee wegfallen. Pro Zelle rund tausend Saeulen, das beantwortet
# jede Instanz ohne Zeitueberschreitung.
CHARGER_GRID = 8

# Gleichzeitige Overpass-Abfragen. Zwei ist das, was die oeffentlichen Instanzen
# je IP zugestehen; gemessen bringt das gegenueber einer nach der anderen fast
# den doppelten Durchsatz. Wer eine eigene Instanz hat, dreht --workers hoch.
OVERPASS_WORKERS = 2

# Deutschland mit etwas Rand. Bewusst grosszuegig: eine Filiale kurz hinter der
# Grenze ist fuer die Frage genauso brauchbar wie eine kurz davor.
GERMANY_BBOX = "47.20,5.80,55.10,15.10"

def overpass(query: str, timeout: int = 600, attempts: int = 4) -> dict:
    """Fragt Overpass ab und wechselt bei Fehlern den Endpunkt."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        for endpoint in ENDPOINTS:
            started = time.time()
            try:
                request = urllib.request.Request(
                    endpoint,
                    data=urllib.parse.urlencode({"data": query}).encode(),
                    headers={"User-Agent": USER_AGENT},
                )
                payload = json.loads(urllib.request.urlopen(request, timeout=timeout).read())
                remark = payload.get("remark")
                if remark:
                    # Diese Meldung kommt, wenn die Instanz keine Area-Datenbank hat.
                    # Ohne Uebersetzung ist sie kaum zu deuten.
                    if "open64" in remark:
                        raise RuntimeError("diese Instanz kennt keine Flaechen (area)")
                    raise RuntimeError(f"Overpass: {remark[:80]}")
                print(
                    f"    {endpoint.split('//')[1][:22]} {time.time() - started:.1f}s "
                    f"{len(payload.get('elements', []))} Objekte",
                    flush=True,
                )
                return payload
            except Exception as error:  # noqa: BLE001 - jeder Fehler heisst: naechster Server
                last_error = error
                print(
                    f"    {endpoint.split('//')[1][:22]} {time.time() - started:.1f}s "
                    f"{type(error).__name__}: {str(error)[:60]}",
                    flush=True,
                )
        pause = 60 * (attempt + 1)
        print(f"    alle Endpunkte belegt, {pause}s Pause", flush=True)
        time.sleep(pause)
    raise RuntimeError(f"Overpass nicht erreichbar: {last_error}")


def element_point(element: dict) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return element["lat"], element["lon"]
    center = element.get("center")
    if center:
        return center["lat"], center["lon"]
    return None


def chain_query(prelude: str, selector: str, brands: Sequence[Brand]) -> str:
    clauses = []
    for brand in brands:
        clauses.append(f'  nwr["brand:wikidata"="{brand.wikidata}"]{selector};')
        for name in brand.names:
            clauses.append(f'  nwr["brand"="{name}"]{selector};')
            clauses.append(f'  nwr["name"="{name}"]{selector};')
    joined = "\n".join(clauses)
    return f"""[out:json][timeout:300];
{prelude}
(
{joined}
);
out center tags;"""


def vegan_query(prelude: str, selector: str) -> str:
    """Alles mit einem diet:vegan-Tag, gefiltert wird danach in Python.

    Zwei Entscheidungen stecken hier drin, beide gemessen:

    Erstens wird auf das *Vorhandensein* des Tags gefiltert, nicht auf seine
    Werte. diet:vegan haengt in der deutschen Gastronomie an 20010 Objekten,
    amenity=restaurant allein an 138261. Die Instanz greift damit auf den
    kleinen Index zu.

    Zweitens ist die Art des Lokals gar nicht mehr Teil der Abfrage. Vorher
    lief je eine Abfrage fuer restaurant, fast_food und cafe, zusammen 383
    Sekunden. Eine einzige Abfrage nur ueber den Tag-Index liefert 21946
    Objekte in 154 Sekunden, und die paar Automaten und Laeden darin wirft
    load_stores ueber FOOD_AMENITIES weg.
    """
    return (
        f"[out:json][timeout:600];\n{prelude}\n"
        f'nwr["diet:vegan"]{selector};\n'
        "out center tags;"
    )


def run_queries(queries: Sequence[str], workers: int) -> list[dict]:
    """Fuehrt mehrere Overpass-Abfragen gleichzeitig aus, Reihenfolge bleibt.

    Gemessen an disjunkten Rasterzellen auf overpass.openstreetmap.fr: eine
    Abfrage nach der anderen 175 Objekte/s, zwei gleichzeitig 331, drei 521.
    Ein grosser Teil der Zeit ist Warteschlange, nicht Rechnen.

    Bei zwei bleibt es trotzdem, denn das ist es, was die oeffentlichen
    Instanzen je IP zugestehen. Wer eine eigene Instanz betreibt, dreht
    --workers hoch.
    """
    if workers <= 1 or len(queries) == 1:
        return [overpass(query) for query in queries]
    with ThreadPoolExecutor(max_workers=min(workers, len(queries))) as pool:
        return list(pool.map(overpass, queries))


def _fetch_stores(
    bbox: str,
    country: str | None,
    seed: Path | None,
    brands: Sequence[Brand],
    workers: int = OVERPASS_WORKERS,
) -> list[dict]:
    """Holt die Rohdaten, standardmaessig ueber eine Bounding-Box.

    Die naheliegendere Variante ueber die Landesflaeche (area["ISO3166-1"="DE"])
    setzt voraus, dass die Overpass-Instanz eine Area-Datenbank hat. Genau die
    fehlt der schnellsten Instanz: overpass.openstreetmap.fr antwortet darauf mit
    "runtime error: open64: 2 No such file or directory". Eine Bounding-Box
    versteht dagegen jede Instanz. Der Preis ist ein Ueberhang ins Ausland, und
    ein Burger King fuenfzehn Kilometer hinter der Grenze schadet niemandem.

    Ketten und vegane Lokale werden getrennt abgefragt. Zusammengefasst waere es
    eine einzige, sehr grosse Antwort, und genau die hat mir eine Instanz schon
    mitten im Senden abgebrochen. Zwei kleinere Abfragen darf der Endpunktwechsel
    einzeln wiederholen, und nebenbei laufen sie gleichzeitig.
    """
    if seed and seed.exists():
        print(f"Lokale aus {seed}", flush=True)
        return json.loads(seed.read_text()).get("elements", [])

    selector = f"({bbox})"
    prelude = ""
    elements: list[dict] = []
    if country:
        area = f'area["ISO3166-1"="{country}"][admin_level=2]->.searched;'
        print(f"Ketten in {country} abfragen (braucht eine Instanz mit Area-Daten)", flush=True)
        try:
            # Nur ein Durchgang durch die Endpunkte: fehlende Area-Daten sind kein
            # Ausfall, den Warten heilt, sondern ein Grund fuer die Bounding-Box.
            elements = list(
                overpass(chain_query(area, "(area.searched)", brands), attempts=1).get("elements", [])
            )
            prelude, selector = area, "(area.searched)"
        except RuntimeError as error:
            print(f"  Flaechensuche gescheitert ({error}), weiche auf die Bounding-Box aus", flush=True)
            elements = list(overpass(chain_query("", selector, brands)).get("elements", []))
        # Nacheinander, nicht gleichzeitig: ob die Flaechensuche geht, entscheidet
        # sich erst an der ersten Abfrage, und danach richtet sich diese hier.
        print("Lokale mit diet:vegan abfragen", flush=True)
        elements.extend(overpass(vegan_query(prelude, selector)).get("elements", []))
        return elements

    print(
        f"Ketten und vegane Lokale im Bereich {bbox} abfragen"
        f" ({', '.join(brand.label for brand in brands)}, zwei Abfragen gleichzeitig)",
        flush=True,
    )
    for payload in run_queries(
        [chain_query("", selector, brands), vegan_query("", selector)], workers
    ):
        elements.extend(payload.get("elements", []))
    return elements


def load_stores(
    connection: sqlite3.Connection,
    bbox: str,
    country: str | None,
    seed: Path | None,
    brands: Sequence[Brand],
    workers: int = OVERPASS_WORKERS,
) -> int:
    """Schreibt Lokale in die Datenbank, mit den Merkmalen fuer die Auswahl.

    Ein Lokal kann in mehreren Kategorien liegen, deshalb sind vegan und
    vegan_only eigene Spalten und keine Werte von brand. Burger King und Subway
    gelten immer als "vegane Optionen": beide Ketten fuehren die entsprechenden
    Produkte bundesweit, waehrend das OSM-Tag an ihren Filialen nur zu 15 bis 21
    Prozent gesetzt ist und stellenweise noch auf dem Stand vor der
    Menueumstellung steht. 54 Filialen tragen diet:vegan=no. Dem Tag hier zu
    folgen hiesse, Treffer wegen veralteter Kartendaten zu verstecken.
    """
    elements = _fetch_stores(bbox, country, seed, brands, workers)

    rows: dict[str, tuple] = {}
    labels: dict[str, str] = {}
    for element in elements:
        point = element_point(element)
        tags = element.get("tags") or {}
        if not point:
            continue
        brand = next((item for item in brands if matches_brand(tags, item)), None)
        vegan = has_vegan_options(tags)
        vegan_only = is_vegan_only(tags)
        if brand is not None:
            vegan = True  # Ketten zaehlen immer, siehe Docstring.
        elif not vegan:
            continue  # Weder Kette noch vegan: beantwortet die Frage nicht.
        elif tags.get("amenity") not in FOOD_AMENITIES:
            continue  # Ein diet:vegan-Tag an einem Kiosk ist kein Lokal.

        # Die Ketten- und die diet:vegan-Abfrage koennen dasselbe Lokal liefern.
        # Gezaehlt wird deshalb erst zum Schluss, ueber die eindeutigen Zeilen.
        identifier = f"{element.get('type', 'node')}/{element.get('id')}"
        labels[identifier] = (
            brand.label if brand else ("Rein vegan" if vegan_only else "Vegane Optionen")
        )
        rows[identifier] = (
            identifier,
            brand.key if brand else None,
            tags.get("amenity"),
            1 if vegan else 0,
            1 if vegan_only else 0,
            point[0],
            point[1],
            tags.get("name") or tags.get("brand"),
            address_of(tags),
            tags.get("opening_hours"),
            json.dumps(tags, ensure_ascii=False),
        )

    connection.execute("DELETE FROM store")
    connection.executemany(
        "INSERT OR REPLACE INTO store"
        " (id, brand, amenity, vegan, vegan_only, lat, lon, name, address, opening_hours, tags)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows.values(),
    )
    connection.commit()
    counts: dict[str, int] = {}
    for label in labels.values():
        counts[label] = counts.get(label, 0) + 1
    summary = ", ".join(f"{count} {label}" for label, count in sorted(counts.items()))
    print(f"  {len(rows)} Lokale gespeichert ({summary})", flush=True)
    return len(rows)


def grid_cells(bbox: str, grid: int) -> list[tuple[float, float, float, float]]:
    """Zerlegt die Bounding-Box in grid mal grid Zellen, (south, west, north, east)."""
    south, west, north, east = (float(part) for part in bbox.split(","))
    step_lat = (north - south) / grid
    step_lon = (east - west) / grid
    return [
        (
            south + row * step_lat,
            west + column * step_lon,
            south + (row + 1) * step_lat,
            west + (column + 1) * step_lon,
        )
        for row in range(grid)
        for column in range(grid)
    ]


def cells_with_stores(
    cells: Sequence[tuple[float, float, float, float]],
    stores: Sequence[sqlite3.Row],
    padding_m: float,
) -> list[int]:
    """Indizes der Zellen, in denen ueberhaupt ein Lokal liegt.

    Spart die Abfragen ueber Nordsee, Ostsee und die Alpen. Der Rand kommt dazu,
    damit ein Lokal dicht an der Zellgrenze seine Saeulen jenseits davon behaelt.
    """
    pad_lat = lat_degrees_for_meters(padding_m)
    wanted = []
    for index, (south, west, north, east) in enumerate(cells):
        pad_lon = lon_degrees_for_meters(padding_m, (south + north) / 2)
        if any(
            south - pad_lat <= store["lat"] <= north + pad_lat
            and west - pad_lon <= store["lon"] <= east + pad_lon
            for store in stores
        ):
            wanted.append(index)
    return wanted


def charger_query(cell: tuple[float, float, float, float]) -> str:
    """Alle Ladesaeulen in einer Rasterzelle, mit Rand.

    Der Rand sorgt dafuer, dass eine Saeule knapp jenseits der Zellgrenze zu
    einem Lokal knapp diesseits gefunden wird. Die Ueberlappung liefert Saeulen
    doppelt, das faengt INSERT OR REPLACE ab.
    """
    south, west, north, east = cell
    pad_lat = lat_degrees_for_meters(CHARGER_SEARCH_M)
    pad_lon = lon_degrees_for_meters(CHARGER_SEARCH_M, (south + north) / 2)
    return (
        "[out:json][timeout:600];\n"
        f'nwr["amenity"="charging_station"]({south - pad_lat:.5f},{west - pad_lon:.5f},'
        f"{north + pad_lat:.5f},{east + pad_lon:.5f});\n"
        "out center tags;"
    )


def load_chargers(
    connection: sqlite3.Connection,
    bbox: str,
    grid: int,
    resume: bool,
    workers: int = OVERPASS_WORKERS,
) -> int:
    stores = connection.execute("SELECT id, lat, lon FROM store").fetchall()
    cells = grid_cells(bbox, grid)
    wanted = cells_with_stores(cells, stores, CHARGER_SEARCH_M)

    done = (
        {row["cell"] for row in connection.execute("SELECT cell FROM ingest_cell")}
        if resume
        else set()
    )
    if not resume:
        connection.execute("DELETE FROM ingest_cell")
        connection.commit()

    todo = [index for index in wanted if f"{grid}:{index}" not in done]
    print(
        f"Ladesaeulen im Raster {grid}x{grid}: {len(wanted)} von {len(cells)} Zellen"
        f" enthalten eines der {len(stores)} Lokale, {len(todo)} noch offen",
        flush=True,
    )

    # Geholt wird gleichzeitig, geschrieben nur hier im Hauptfaden: die
    # SQLite-Verbindung gehoert einem Faden, und jede fertige Zelle soll sofort
    # festgeschrieben sein, damit ein Abbruch nichts kostet.
    for position, batch in enumerate(chunked(todo, max(1, workers))):
        first = position * max(1, workers) + 1
        print(f"  Zellen {first}-{min(first + len(batch) - 1, len(todo))}/{len(todo)}", flush=True)
        payloads = run_queries([charger_query(cells[index]) for index in batch], workers)
        for index, payload in zip(batch, payloads):
            store_chargers(connection, payload)
            connection.execute(
                "INSERT OR REPLACE INTO ingest_cell (cell, done_at) VALUES (?, ?)",
                (f"{grid}:{index}", datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            connection.commit()

    total = connection.execute("SELECT COUNT(*) AS n FROM charger").fetchone()["n"]
    print(f"  {total} Ladesaeulen gespeichert", flush=True)
    return total


def store_chargers(connection: sqlite3.Connection, payload: dict) -> int:
    """Schreibt die Ladesaeulen einer Overpass-Antwort weg."""
    rows = []
    for element in payload.get("elements", []):
        point = element_point(element)
        tags = element.get("tags") or {}
        if not point:
            continue
        rows.append(
            (
                f"{element.get('type', 'node')}/{element.get('id')}",
                point[0],
                point[1],
                tags.get("operator") or tags.get("network") or tags.get("name"),
                1 if is_enbw(tags) else 0,
                network_of(tags),
                1 if is_car_charger(tags) else 0,
                max_power_kw(tags),
                tags.get("capacity"),
                tags.get("fee"),
                json.dumps(tags, ensure_ascii=False),
            )
        )
    connection.executemany(
        "INSERT OR REPLACE INTO charger"
        " (id, lat, lon, operator, is_enbw, network, is_car, power_kw, capacity, fee, tags)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def build_pairs(connection: sqlite3.Connection, gap_m: float) -> int:
    """Paare einmal ausrechnen, damit die Abfrage spaeter nur noch lesen muss."""
    connection.execute("DELETE FROM pair")
    stores = connection.execute("SELECT id, lat, lon FROM store").fetchall()
    chargers = connection.execute("SELECT id, lat, lon FROM charger").fetchall()

    # Raster ueber die Ladesaeulen, damit nicht jede Filiale gegen alle geprueft wird.
    cell = 0.02  # rund 2 km
    grid: dict[tuple[int, int], list[sqlite3.Row]] = {}
    for charger in chargers:
        key = (int(charger["lat"] / cell), int(charger["lon"] / cell))
        grid.setdefault(key, []).append(charger)

    rows = []
    for store in stores:
        base_lat = int(store["lat"] / cell)
        base_lon = int(store["lon"] / cell)
        for d_lat in (-1, 0, 1):
            for d_lon in (-1, 0, 1):
                for charger in grid.get((base_lat + d_lat, base_lon + d_lon), ()):
                    gap = haversine_m(store["lat"], store["lon"], charger["lat"], charger["lon"])
                    if gap <= gap_m:
                        rows.append((store["id"], charger["id"], gap))

    connection.executemany(
        "INSERT OR REPLACE INTO pair (store_id, charger_id, gap_m) VALUES (?, ?, ?)", rows
    )
    connection.commit()
    print(f"  {len(rows)} Paare bis {gap_m:.0f} m", flush=True)
    return len(rows)


def prune_chargers(connection: sqlite3.Connection) -> int:
    """Wirft Ladesaeulen weg, die zu keinem Lokal gehoeren.

    Das Raster holt alle Saeulen im Suchbereich, gemessen 58335 in Deutschland.
    Zu einem Lokal gehoeren davon 25456. Der Rest beantwortet die Frage dieser
    App nicht und kostet nur Plattenplatz, in Zahlen 66 statt 51 MB.

    Der Preis: ein spaeteres --pairs-only mit groesserem --gap findet die
    weggeworfenen Saeulen nicht mehr. Wer den Abstand ueber den Wert dieses
    Laufs hinaus vergroessern will, braucht einen frischen Ingest.
    """
    before = connection.execute("SELECT COUNT(*) AS n FROM charger").fetchone()["n"]
    connection.execute("DELETE FROM charger WHERE id NOT IN (SELECT charger_id FROM pair)")
    connection.commit()
    connection.execute("VACUUM")
    after = connection.execute("SELECT COUNT(*) AS n FROM charger").fetchone()["n"]
    print(f"  {before - after} Saeulen ohne Lokal verworfen, {after} bleiben", flush=True)
    return after


def build_index(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM store_rtree")
    connection.execute(
        "INSERT INTO store_rtree (rowid, min_lat, max_lat, min_lon, max_lon)"
        " SELECT s.rowid, s.lat, s.lat, s.lon, s.lon FROM store s"
    )
    connection.commit()


def set_meta(connection: sqlite3.Connection, **values: object) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        [(key, str(value)) for key, value in values.items()],
    )
    connection.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="server/data/whopper.sqlite")
    parser.add_argument(
        "--bbox",
        default=GERMANY_BBOX,
        help="Suchbereich als south,west,north,east (Standard: Deutschland mit Rand)",
    )
    parser.add_argument(
        "--country",
        default=None,
        help="ISO-3166-1-Code statt Bounding-Box. Braucht eine Instanz mit Area-Daten,"
        " sonst wird automatisch auf die Bounding-Box zurueckgefallen.",
    )
    parser.add_argument("--stores-json", type=Path, default=None, help="Overpass-Antwort wiederverwenden")
    parser.add_argument(
        "--brands",
        default=",".join(BRANDS),
        help="Kommaliste der Ketten. Der Ingest holt immer alle, ausgewaehlt wird spaeter"
        f" in der App. Moeglich: {', '.join(BRANDS)}",
    )
    parser.add_argument("--gap", type=float, default=1000.0, help="Maximaler Abstand fuer Paare in Metern")
    parser.add_argument(
        "--charger-grid",
        type=int,
        default=CHARGER_GRID,
        help="Zellen je Achse fuer den Ladesaeulen-Schritt. Groesser heisst mehr,"
        " dafuer kleinere Abfragen.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=OVERPASS_WORKERS,
        help="Gleichzeitige Overpass-Abfragen. Zwei ist das, was oeffentliche Instanzen"
        " je IP zugestehen; mehr nur gegen eine eigene Instanz.",
    )
    parser.add_argument("--no-resume", action="store_true", help="Ladesaeulen komplett neu holen")
    parser.add_argument("--pairs-only", action="store_true", help="Nur Paare und Index neu bauen")
    parser.add_argument(
        "--keep-all-chargers",
        action="store_true",
        help="Auch Saeulen behalten, die zu keinem Lokal gehoeren. Kostet Plattenplatz,"
        " erlaubt dafuer spaeter ein --pairs-only mit groesserem --gap.",
    )
    arguments = parser.parse_args()

    brands = [BRANDS[key.strip()] for key in arguments.brands.split(",") if key.strip() in BRANDS]
    if not brands:
        parser.error(f"Keine bekannte Kette in --brands. Moeglich: {', '.join(BRANDS)}")
    if arguments.charger_grid < 1:
        parser.error("--charger-grid braucht mindestens 1")
    if arguments.workers < 1:
        parser.error("--workers braucht mindestens 1")

    connection = connect(Path(arguments.db))
    started = time.time()

    if not arguments.pairs_only:
        load_stores(
            connection,
            arguments.bbox,
            arguments.country,
            arguments.stores_json,
            brands,
            arguments.workers,
        )
        load_chargers(
            connection,
            arguments.bbox,
            arguments.charger_grid,
            resume=not arguments.no_resume,
            workers=arguments.workers,
        )

    print("Paare berechnen", flush=True)
    pairs = build_pairs(connection, arguments.gap)
    if not arguments.keep_all_chargers:
        prune_chargers(connection)
    build_index(connection)

    counts = {
        name: connection.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
        for name in ("store", "charger")
    }
    # Zaehlt je Kategorie, nicht je Zeile: ein Burger King steckt in zweien.
    per_kind = {
        "bk": _count(connection, "brand = 'bk'"),
        "subway": _count(connection, "brand = 'subway'"),
        "vegan": _count(connection, "vegan = 1"),
        "vegan_only": _count(connection, "vegan_only = 1"),
    }
    set_meta(
        connection,
        ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        area=arguments.country or f"bbox {arguments.bbox}",
        brands=",".join(brand.key for brand in brands),
        stores=counts["store"],
        stores_per_kind=json.dumps(per_kind),
        chargers=counts["charger"],
        pairs=pairs,
        max_gap_m=arguments.gap,
        charger_search_m=CHARGER_SEARCH_M,
        charger_grid=arguments.charger_grid,
        source="OpenStreetMap via Overpass, ODbL",
    )
    print(
        f"Fertig in {time.time() - started:.0f}s: "
        f"{counts['store']} Lokale, {counts['charger']} Saeulen, {pairs} Paare",
        flush=True,
    )
    return 0


def _count(connection: sqlite3.Connection, condition: str) -> int:
    return connection.execute(f"SELECT COUNT(*) AS n FROM store WHERE {condition}").fetchone()["n"]


if __name__ == "__main__":
    raise SystemExit(main())
