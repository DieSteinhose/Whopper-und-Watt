#!/usr/bin/env python3
"""Baut die lokale Datenbank aus OpenStreetMap.

Der Sinn der Uebung: die Live-Abfragen gegen Overpass dauerten je nach Serverlast
zwischen 10 Sekunden und mehreren Minuten und wurden regelmaessig mit HTTP 429
oder 504 abgewiesen. Einmal einsammeln, danach lokal beantworten.

Zwei Schritte, weil Burger King selten und Ladesaeulen haeufig sind:
  1. Alle Filialen im Suchbereich (eine Abfrage ueber eine Bounding-Box).
  2. Ladesaeulen in kleinen Boxen um genau diese Filialen, in Bloecken zu 20.

Schritt 2 sind rund fuenfzig Abfragen. Auf einer gut gelaunten Instanz dauert das
wenige Minuten, auf einer ausgelasteten deutlich laenger, und zwischendurch weisen
die oeffentlichen Instanzen Abfragen ab. Deshalb ist der Lauf fortsetzbar: jeder
erledigte Block wird sofort festgeschrieben, ein Neustart macht dort weiter.

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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from geo import (  # noqa: E402
    BURGER_KING_WIKIDATA,
    address_of,
    box_around,
    chunked,
    haversine_m,
    is_burger_king,
    is_enbw,
    max_power_kw,
)

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

BURGERS_PER_BLOCK = 20
CHARGER_SEARCH_M = 1000

# Deutschland mit etwas Rand. Bewusst grosszuegig: eine Filiale kurz hinter der
# Grenze ist fuer die Frage genauso brauchbar wie eine kurz davor.
GERMANY_BBOX = "47.20,5.80,55.10,15.10"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS burger (
    id TEXT PRIMARY KEY,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    name TEXT,
    address TEXT,
    opening_hours TEXT,
    tags TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS charger (
    id TEXT PRIMARY KEY,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    operator TEXT,
    is_enbw INTEGER NOT NULL,
    power_kw REAL,
    capacity TEXT,
    fee TEXT,
    tags TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pair (
    burger_id TEXT NOT NULL,
    charger_id TEXT NOT NULL,
    gap_m REAL NOT NULL,
    PRIMARY KEY (burger_id, charger_id)
);
CREATE INDEX IF NOT EXISTS pair_by_burger ON pair(burger_id, gap_m);

-- Fortschritt des Ladesaeulen-Schritts, damit ein Abbruch nichts kostet.
CREATE TABLE IF NOT EXISTS ingest_block (block INTEGER PRIMARY KEY, done_at TEXT NOT NULL);

CREATE VIRTUAL TABLE IF NOT EXISTS burger_rtree USING rtree(rowid, min_lat, max_lat, min_lon, max_lon);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def overpass(query: str, timeout: int = 300, attempts: int = 4) -> dict:
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


def burger_query(prelude: str, selector: str) -> str:
    return f"""[out:json][timeout:300];
{prelude}
(
  nwr["brand:wikidata"="{BURGER_KING_WIKIDATA}"]{selector};
  nwr["brand"="Burger King"]{selector};
  nwr["name"="Burger King"]{selector};
);
out center tags;"""


def load_burgers(
    connection: sqlite3.Connection,
    bbox: str,
    country: str | None,
    seed: Path | None,
) -> int:
    """Holt die Filialen, standardmaessig ueber eine Bounding-Box.

    Die naheliegendere Variante ueber die Landesflaeche (area["ISO3166-1"="DE"])
    setzt voraus, dass die Overpass-Instanz eine Area-Datenbank hat. Genau die
    fehlt der schnellsten Instanz: overpass.openstreetmap.fr antwortet darauf mit
    "runtime error: open64: 2 No such file or directory". Eine Bounding-Box
    versteht dagegen jede Instanz. Der Preis ist ein Ueberhang ins Ausland, und
    ein Burger King fuenfzehn Kilometer hinter der Grenze schadet niemandem.
    """
    if seed and seed.exists():
        print(f"Filialen aus {seed}", flush=True)
        payload = json.loads(seed.read_text())
    elif country:
        print(f"Filialen in {country} abfragen (braucht eine Instanz mit Area-Daten)", flush=True)
        try:
            # Nur ein Durchgang durch die Endpunkte: fehlende Area-Daten sind kein
            # Ausfall, den Warten heilt, sondern ein Grund fuer die Bounding-Box.
            payload = overpass(
                burger_query(
                    f'area["ISO3166-1"="{country}"][admin_level=2]->.searched;', "(area.searched)"
                ),
                attempts=1,
            )
        except RuntimeError as error:
            print(f"  Flaechensuche gescheitert ({error}), weiche auf die Bounding-Box aus", flush=True)
            payload = overpass(burger_query("", f"({bbox})"))
    else:
        print(f"Filialen im Bereich {bbox} abfragen (dauert einige Minuten)", flush=True)
        payload = overpass(burger_query("", f"({bbox})"))

    rows = []
    for element in payload.get("elements", []):
        point = element_point(element)
        tags = element.get("tags") or {}
        if not point or not is_burger_king(tags):
            continue
        rows.append(
            (
                f"{element.get('type', 'node')}/{element.get('id')}",
                point[0],
                point[1],
                tags.get("name") or tags.get("brand"),
                address_of(tags),
                tags.get("opening_hours"),
                json.dumps(tags, ensure_ascii=False),
            )
        )

    connection.executemany(
        "INSERT OR REPLACE INTO burger (id, lat, lon, name, address, opening_hours, tags)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    print(f"  {len(rows)} Filialen gespeichert", flush=True)
    return len(rows)


def load_chargers(connection: sqlite3.Connection, resume: bool) -> int:
    burgers = connection.execute("SELECT id, lat, lon FROM burger ORDER BY id").fetchall()
    blocks = list(chunked(burgers, BURGERS_PER_BLOCK))
    done = (
        {row["block"] for row in connection.execute("SELECT block FROM ingest_block")}
        if resume
        else set()
    )
    if not resume:
        connection.execute("DELETE FROM ingest_block")
        connection.commit()

    print(f"Ladesaeulen um {len(burgers)} Filialen, {len(blocks)} Bloecke", flush=True)
    for index, block in enumerate(blocks):
        if index in done:
            continue
        clauses = []
        for row in block:
            south, west, north, east = box_around(row["lat"], row["lon"], CHARGER_SEARCH_M)
            clauses.append(
                f'  nwr["amenity"="charging_station"]'
                f"({south:.5f},{west:.5f},{north:.5f},{east:.5f});"
            )
        query = "[out:json][timeout:180];\n(\n" + "\n".join(clauses) + "\n);\nout center tags;"
        print(f"  Block {index + 1}/{len(blocks)}", flush=True)
        payload = overpass(query)

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
                    max_power_kw(tags),
                    tags.get("capacity"),
                    tags.get("fee"),
                    json.dumps(tags, ensure_ascii=False),
                )
            )
        connection.executemany(
            "INSERT OR REPLACE INTO charger"
            " (id, lat, lon, operator, is_enbw, power_kw, capacity, fee, tags)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.execute(
            "INSERT OR REPLACE INTO ingest_block (block, done_at) VALUES (?, ?)",
            (index, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        connection.commit()

    total = connection.execute("SELECT COUNT(*) AS n FROM charger").fetchone()["n"]
    print(f"  {total} Ladesaeulen gespeichert", flush=True)
    return total


def build_pairs(connection: sqlite3.Connection, gap_m: float) -> int:
    """Paare einmal ausrechnen, damit die Abfrage spaeter nur noch lesen muss."""
    connection.execute("DELETE FROM pair")
    burgers = connection.execute("SELECT id, lat, lon FROM burger").fetchall()
    chargers = connection.execute("SELECT id, lat, lon FROM charger").fetchall()

    # Raster ueber die Ladesaeulen, damit nicht jede Filiale gegen alle geprueft wird.
    cell = 0.02  # rund 2 km
    grid: dict[tuple[int, int], list[sqlite3.Row]] = {}
    for charger in chargers:
        key = (int(charger["lat"] / cell), int(charger["lon"] / cell))
        grid.setdefault(key, []).append(charger)

    rows = []
    for burger in burgers:
        base_lat = int(burger["lat"] / cell)
        base_lon = int(burger["lon"] / cell)
        for d_lat in (-1, 0, 1):
            for d_lon in (-1, 0, 1):
                for charger in grid.get((base_lat + d_lat, base_lon + d_lon), ()):
                    gap = haversine_m(burger["lat"], burger["lon"], charger["lat"], charger["lon"])
                    if gap <= gap_m:
                        rows.append((burger["id"], charger["id"], gap))

    connection.executemany(
        "INSERT OR REPLACE INTO pair (burger_id, charger_id, gap_m) VALUES (?, ?, ?)", rows
    )
    connection.commit()
    print(f"  {len(rows)} Paare bis {gap_m:.0f} m", flush=True)
    return len(rows)


def build_index(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM burger_rtree")
    connection.execute(
        "INSERT INTO burger_rtree (rowid, min_lat, max_lat, min_lon, max_lon)"
        " SELECT b.rowid, b.lat, b.lat, b.lon, b.lon FROM burger b"
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
    parser.add_argument("--burgers-json", type=Path, default=None, help="Overpass-Antwort wiederverwenden")
    parser.add_argument("--gap", type=float, default=1000.0, help="Maximaler Abstand fuer Paare in Metern")
    parser.add_argument("--no-resume", action="store_true", help="Ladesaeulen komplett neu holen")
    parser.add_argument("--pairs-only", action="store_true", help="Nur Paare und Index neu bauen")
    arguments = parser.parse_args()

    connection = connect(Path(arguments.db))
    started = time.time()

    if not arguments.pairs_only:
        load_burgers(connection, arguments.bbox, arguments.country, arguments.burgers_json)
        load_chargers(connection, resume=not arguments.no_resume)

    print("Paare berechnen", flush=True)
    pairs = build_pairs(connection, arguments.gap)
    build_index(connection)

    counts = {
        name: connection.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
        for name in ("burger", "charger")
    }
    set_meta(
        connection,
        ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        area=arguments.country or f"bbox {arguments.bbox}",
        burgers=counts["burger"],
        chargers=counts["charger"],
        pairs=pairs,
        max_gap_m=arguments.gap,
        charger_search_m=CHARGER_SEARCH_M,
        source="OpenStreetMap via Overpass, ODbL",
    )
    print(
        f"Fertig in {time.time() - started:.0f}s: "
        f"{counts['burger']} Filialen, {counts['charger']} Saeulen, {pairs} Paare",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
