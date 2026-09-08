"""Aufbau der Datenbank und das Nachziehen aelterer Bestaende.

Eigene Datei, weil das Schema drei Nutzer hat und nicht nur einen: der Ingest
schreibt, der Server liest, der statische Export liest. Solange die Migration
nur im Ingest lag, konnte genau das passieren, was es dann auch tat: der
Workflow holte eine Datenbank aus dem Actions-Cache, uebersprang den Ingest,
weil sich am Datenbestand nichts geaendert hatte, und der Export lief in

    sqlite3.OperationalError: no such column: c.network

Wer hier eine Spalte ergaenzt, traegt sie in SCHEMA fuer neue Datenbanken ein
UND in ADDED_*_COLUMNS fuer bestehende. Beides, sonst kippt eine der beiden
Seiten. Der Test test_old_database_is_migrated_on_every_path faengt das ab.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from geo import network_of

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS store (
    id TEXT PRIMARY KEY,
    brand TEXT,
    amenity TEXT,
    vegan INTEGER NOT NULL DEFAULT 0,
    vegan_only INTEGER NOT NULL DEFAULT 0,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    name TEXT,
    address TEXT,
    opening_hours TEXT,
    tags TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS store_by_brand ON store(brand);

CREATE TABLE IF NOT EXISTS charger (
    id TEXT PRIMARY KEY,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    operator TEXT,
    is_enbw INTEGER NOT NULL,
    network TEXT,
    power_kw REAL,
    capacity TEXT,
    fee TEXT,
    tags TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pair (
    store_id TEXT NOT NULL,
    charger_id TEXT NOT NULL,
    gap_m REAL NOT NULL,
    PRIMARY KEY (store_id, charger_id)
);
CREATE INDEX IF NOT EXISTS pair_by_store ON pair(store_id, gap_m);

-- Fortschritt des Ladesaeulen-Schritts, damit ein Abbruch nichts kostet.
-- Der Schluessel enthaelt die Rastergroesse, damit ein Lauf mit anderem Raster
-- nicht faelschlich die alten Zellen als erledigt liest.
CREATE TABLE IF NOT EXISTS ingest_cell (cell TEXT PRIMARY KEY, done_at TEXT NOT NULL);

CREATE VIRTUAL TABLE IF NOT EXISTS store_rtree USING rtree(rowid, min_lat, max_lat, min_lon, max_lon);
"""

# Spalten, die spaeter dazugekommen sind. Eine bestehende Datenbank soll nicht
# weggeworfen werden muessen, nur weil eine Kategorie dazugekommen ist.
ADDED_STORE_COLUMNS = {
    "amenity": "TEXT",
    "vegan": "INTEGER NOT NULL DEFAULT 0",
    "vegan_only": "INTEGER NOT NULL DEFAULT 0",
}
ADDED_CHARGER_COLUMNS = {"network": "TEXT"}

# Erst nach der Migration, denn ein Index auf einer Spalte, die es in einer
# aelteren Datenbank noch nicht gibt, laesst sich nicht anlegen.
SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS store_by_vegan ON store(vegan);
CREATE INDEX IF NOT EXISTS store_by_vegan_only ON store(vegan_only);
CREATE INDEX IF NOT EXISTS charger_by_network ON charger(network);
CREATE INDEX IF NOT EXISTS charger_by_power ON charger(power_kw);
"""


def connect(path: Path, create: bool = True, **kwargs) -> sqlite3.Connection:
    """Oeffnet die Datenbank und bringt sie auf den aktuellen Stand.

    Jeder Weg in die Datenbank geht hier durch, auch die lesenden. Eine
    Datenbank aus dem Cache ist sonst genau so alt wie der Lauf, der sie
    gebaut hat.
    """
    path = Path(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.exists():
        raise FileNotFoundError(f"{path} fehlt. Erst 'python3 server/ingest.py' laufen lassen.")

    connection = sqlite3.connect(path, **kwargs)
    connection.row_factory = sqlite3.Row
    if create:
        connection.executescript(SCHEMA)
    migrate(connection)
    connection.executescript(SCHEMA_INDEXES)
    return connection


def migrate(connection: sqlite3.Connection) -> list[str]:
    """Fehlende Spalten nachziehen. Gibt zurueck, was ergaenzt wurde."""
    added = []
    for table, columns in (("store", ADDED_STORE_COLUMNS), ("charger", ADDED_CHARGER_COLUMNS)):
        present = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if not present:
            continue  # Tabelle gibt es noch gar nicht, SCHEMA legt sie vollstaendig an.
        for column, definition in columns.items():
            if column not in present:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                added.append(f"{table}.{column}")
    if added:
        connection.commit()
    if "charger.network" in added:
        backfill_networks(connection)
    return added


def backfill_networks(connection: sqlite3.Connection) -> int:
    """Netz aus den gespeicherten Tags nachtragen, ohne neu abzufragen.

    Die Tags liegen ohnehin in der Datenbank. Eine bestehende Datenbank muss
    also nicht wegen einer neuen Spalte fuenf Minuten Overpass kosten.
    """
    rows = [
        (network_of(json.loads(row["tags"] or "{}")), row["id"])
        for row in connection.execute("SELECT id, tags FROM charger")
    ]
    connection.executemany("UPDATE charger SET network = ? WHERE id = ?", rows)
    connection.commit()
    filled = sum(1 for network, _ in rows if network)
    print(f"  Netz fuer {filled} von {len(rows)} Saeulen aus den Tags nachgetragen", flush=True)
    return filled
