"""Abfragen gegen die vorgebaute Datenbank.

Die teure Arbeit ist beim Ingest passiert: Paare aus Filiale und Ladesaeule sind
fertig ausgerechnet, die Filialen haengen in einem R-Tree. Eine Abfrage ist damit
ein Index-Zugriff plus ein bisschen Rechnen, statt eines Overpass-Requests.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Sequence

from geo import (
    AMENITY_LABELS,
    BRANDS,
    DEFAULT_KINDS,
    box_around,
    cumulative_distances,
    haversine_m,
    lat_degrees_for_meters,
    lon_degrees_for_meters,
    resample,
    route_match,
)

ROUTE_SAMPLE_STEP_M = 2000.0

# Jede anwaehlbare Kategorie ist eine Bedingung auf der Tabelle store. Die
# Auswahl ist ein Oder: wer Subway und rein vegan anhakt, will beides sehen.
# Ketten und Ernaehrungsform sind bewusst getrennte Spalten, denn ein Lokal
# kann in mehreren Kategorien liegen. Ein Burger King liegt immer in zweien.
KIND_CONDITIONS = {
    "bk": "s.brand = 'bk'",
    "subway": "s.brand = 'subway'",
    "vegan": "s.vegan = 1",
    "vegan_only": "s.vegan_only = 1",
}


def kind_filter(kinds: Sequence[str]) -> str:
    """SQL-Bedingung fuer die gewaehlten Kategorien.

    Ohne bekannte Kategorie faellt das auf 0 zurueck, also auf keine Treffer.
    Das ist die ehrliche Antwort auf eine leere Auswahl: nichts angehakt heisst
    nichts gesucht, nicht heimlich alles.
    """
    conditions = [KIND_CONDITIONS[key] for key in dict.fromkeys(kinds) if key in KIND_CONDITIONS]
    return " OR ".join(conditions) if conditions else "0"


class SpotDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(
                f"{self.path} fehlt. Erst 'python3 server/ingest.py' laufen lassen."
            )
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row

    def meta(self) -> dict:
        rows = self._connection.execute("SELECT key, value FROM meta").fetchall()
        return {row["key"]: row["value"] for row in rows}

    # ---- Umkreissuche ---------------------------------------------------

    def spots_near(
        self,
        lat: float,
        lon: float,
        radius_m: float,
        gap_m: float,
        only_enbw: bool,
        kinds: Sequence[str] = DEFAULT_KINDS,
    ) -> list[dict]:
        south, west, north, east = box_around(lat, lon, radius_m)
        stores = self._stores_in_box(south, west, north, east, kinds)

        spots = []
        for store in stores:
            distance = haversine_m(lat, lon, store["lat"], store["lon"])
            if distance > radius_m:
                continue
            chargers = self._chargers_for(store["id"], gap_m, only_enbw)
            if not chargers:
                continue
            spot = self._spot(store, chargers)
            spot["distanceM"] = round(distance)
            spots.append(spot)

        spots.sort(key=lambda item: item["distanceM"])
        return spots

    # ---- Routensuche ----------------------------------------------------

    def spots_along_route(
        self,
        points: Sequence[tuple[float, float]],
        seconds: Sequence[float] | None,
        corridor_m: float,
        gap_m: float,
        only_enbw: bool,
        kinds: Sequence[str] = DEFAULT_KINDS,
    ) -> list[dict]:
        if len(points) < 2:
            return []

        # Fuer den Abstand reicht eine ausgeduennte Linie, das spart pro Filiale
        # tausende Segmentvergleiche.
        sampled, sampled_seconds = _resample_with_seconds(points, seconds, ROUTE_SAMPLE_STEP_M)
        cumulative = cumulative_distances(sampled)

        candidates: dict[str, sqlite3.Row] = {}
        for first, second in zip(sampled, sampled[1:]):
            pad_lat = lat_degrees_for_meters(corridor_m)
            pad_lon = lon_degrees_for_meters(corridor_m, (first[0] + second[0]) / 2)
            for store in self._stores_in_box(
                min(first[0], second[0]) - pad_lat,
                min(first[1], second[1]) - pad_lon,
                max(first[0], second[0]) + pad_lat,
                max(first[1], second[1]) + pad_lon,
                kinds,
            ):
                candidates[store["id"]] = store

        spots = []
        for store in candidates.values():
            offset, progress, travel = route_match(
                sampled, cumulative, sampled_seconds, store["lat"], store["lon"]
            )
            if offset > corridor_m:
                continue
            chargers = self._chargers_for(store["id"], gap_m, only_enbw)
            if not chargers:
                continue
            spot = self._spot(store, chargers)
            spot["routeOffsetM"] = round(offset)
            spot["routeProgressM"] = round(progress)
            spot["routeSeconds"] = round(travel)
            spots.append(spot)

        spots.sort(key=lambda item: item["routeProgressM"])
        return spots

    # ---- Innereien ------------------------------------------------------

    def _stores_in_box(
        self,
        south: float,
        west: float,
        north: float,
        east: float,
        kinds: Sequence[str],
    ):
        return self._connection.execute(
            "SELECT s.* FROM store s JOIN store_rtree r ON r.rowid = s.rowid"
            " WHERE r.max_lat >= ? AND r.min_lat <= ? AND r.max_lon >= ? AND r.min_lon <= ?"
            f" AND ({kind_filter(kinds)})",
            (south, north, west, east),
        ).fetchall()

    def _chargers_for(self, store_id: str, gap_m: float, only_enbw: bool) -> list[dict]:
        query = (
            "SELECT c.*, p.gap_m FROM pair p JOIN charger c ON c.id = p.charger_id"
            " WHERE p.store_id = ? AND p.gap_m <= ?"
        )
        parameters: list = [store_id, gap_m]
        if only_enbw:
            query += " AND c.is_enbw = 1"
        query += " ORDER BY p.gap_m"
        return [
            {
                "id": row["id"],
                "lat": row["lat"],
                "lon": row["lon"],
                "operator": row["operator"],
                "isEnbw": bool(row["is_enbw"]),
                "powerKw": row["power_kw"],
                "capacity": row["capacity"],
                "fee": row["fee"],
                "gapM": round(row["gap_m"]),
            }
            for row in self._connection.execute(query, parameters).fetchall()
        ]

    @staticmethod
    def _spot(store: sqlite3.Row, chargers: list[dict]) -> dict:
        return spot_payload(store, chargers)


def spot_payload(store, chargers: list[dict]) -> dict:
    """Ein Lokal, wie es an die App geht.

    Bewusst ohne osmUrl: die laesst sich aus der id ableiten, und ein Feld, das
    nur der Server mitliefert und der statische Export nicht, war schon einmal
    ein Fehler. Kategoriezugehoerigkeit reist als Flags mit, damit die App einen
    Burger King mit veganer Option auch als solchen kennzeichnen kann.
    """
    brand = BRANDS.get(store["brand"])
    label = brand.label if brand else AMENITY_LABELS.get(store["amenity"], "Lokal")
    return {
        "id": store["id"],
        "brand": store["brand"],
        "label": label,
        "vegan": bool(store["vegan"]),
        "veganOnly": bool(store["vegan_only"]),
        "name": store["name"] or label,
        "address": store["address"],
        "lat": store["lat"],
        "lon": store["lon"],
        "openingHours": store["opening_hours"],
        "chargers": chargers,
    }


def _resample_with_seconds(
    points: Sequence[tuple[float, float]],
    seconds: Sequence[float] | None,
    step_m: float,
) -> tuple[list[tuple[float, float]], list[float] | None]:
    """Duennt die Linie aus und nimmt die kumulierten Fahrzeiten mit."""
    if seconds is None or len(seconds) != len(points):
        return resample(points, step_m), None

    kept_points = [points[0]]
    kept_seconds = [seconds[0]]
    carried = 0.0
    for index in range(1, len(points)):
        carried += haversine_m(
            points[index - 1][0], points[index - 1][1], points[index][0], points[index][1]
        )
        if carried >= step_m or index == len(points) - 1:
            kept_points.append(points[index])
            kept_seconds.append(seconds[index])
            carried = 0.0
    return kept_points, kept_seconds


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text())
