"""Geometrie und OSM-Tag-Auswertung. Bewusst ohne Fremdbibliotheken."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

EARTH_RADIUS_M = 6371008.8
METERS_PER_DEGREE_LAT = 111_320.0


@dataclass(frozen=True)
class Brand:
    """Eine Kette, wie sie in OSM auffindbar ist.

    Der Anker ist brand:wikidata, denn das ist eindeutig und gut gepflegt: in
    der Stichprobe trugen alle 49 Subway-Filialen im Ruhrgebiet und 22 von 23
    Burger King im Raum Stuttgart dieses Tag. Die Namensfelder fangen den Rest.
    """

    key: str
    label: str
    wikidata: str
    names: tuple[str, ...]


BRANDS: dict[str, Brand] = {
    "bk": Brand("bk", "Burger King", "Q177054", ("Burger King",)),
    "subway": Brand("subway", "Subway", "Q244457", ("Subway",)),
}

# Burger King ist die Voreinstellung, alles andere waehlt man dazu.
DEFAULT_BRANDS = ("bk",)

# Ohne Wikidata-Treffer wird der Name nur bei Essensschuppen geglaubt. Sonst
# waere jeder U-Bahn-Zugang namens "Subway" eine Filiale.
FOOD_AMENITIES = frozenset({"fast_food", "restaurant", "cafe"})

# EnBW als Betreiber: die Schreibweisen in OSM sind uneinheitlich.
_ENBW_KEYS = ("operator", "network", "brand", "owner", "name", "operator:short")
_ENBW_WIKIDATA = {"Q321820", "Q1345004"}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def lon_degrees_for_meters(meters: float, lat: float) -> float:
    """Wie viele Laengengrade [meters] auf dieser Breite entsprechen."""
    return meters / (METERS_PER_DEGREE_LAT * max(math.cos(math.radians(lat)), 0.01))


def lat_degrees_for_meters(meters: float) -> float:
    return meters / METERS_PER_DEGREE_LAT


def box_around(lat: float, lon: float, meters: float) -> tuple[float, float, float, float]:
    """(south, west, north, east) um einen Punkt."""
    pad_lat = lat_degrees_for_meters(meters)
    pad_lon = lon_degrees_for_meters(meters, lat)
    return lat - pad_lat, lon - pad_lon, lat + pad_lat, lon + pad_lon


def is_enbw(tags: dict) -> bool:
    if any(key.lower().startswith("ref:enbw") for key in tags):
        return True
    if tags.get("operator:wikidata") in _ENBW_WIKIDATA:
        return True
    if tags.get("network:wikidata") in _ENBW_WIKIDATA:
        return True
    for key in _ENBW_KEYS:
        value = (tags.get(key) or "").lower()
        if "enbw" in value or "en bw" in value:
            return True
    return False


def matches_brand(tags: dict, brand: Brand) -> bool:
    if tags.get("brand:wikidata") == brand.wikidata:
        return True
    if tags.get("amenity") not in FOOD_AMENITIES:
        return False
    haystack = " ".join(
        filter(None, (tags.get("brand"), tags.get("name"), tags.get("operator")))
    ).lower()
    return any(name.lower() in haystack for name in brand.names)


def brand_of(tags: dict) -> Brand | None:
    """Erste passende Marke, oder None."""
    for brand in BRANDS.values():
        if matches_brand(tags, brand):
            return brand
    return None


def parse_brands(raw: str | None) -> list[Brand]:
    """Kommaliste von Markenschluesseln, unbekannte werden ignoriert."""
    keys = [key.strip().lower() for key in (raw or "").split(",") if key.strip()]
    chosen = [BRANDS[key] for key in keys if key in BRANDS]
    return chosen or [BRANDS[key] for key in DEFAULT_BRANDS]


_POWER_VALUE = re.compile(r"[0-9]+(\.[0-9]+)?")


def max_power_kw(tags: dict) -> float | None:
    """Groesste Ladeleistung aus den ueblichen OSM-Tags, in kW."""
    best: float | None = None
    for key, raw in tags.items():
        if key != "maxpower" and key != "charging_station:output" and not key.endswith(":output"):
            continue
        value = _parse_power_kw(raw)
        if value is not None and (best is None or value > best):
            best = value
    return best


def _parse_power_kw(raw: str) -> float | None:
    text = (raw or "").strip().lower().replace(",", ".")
    match = _POWER_VALUE.search(text)
    if not match:
        return None
    number = float(match.group(0))
    if "kw" in text:
        value = number
    elif "mw" in text:
        value = number * 1000
    elif text.endswith("w"):
        value = number / 1000
    else:
        value = number
    return value if 0 < value < 2000 else None


def address_of(tags: dict) -> str | None:
    street = tags.get("addr:street")
    number = tags.get("addr:housenumber")
    city = tags.get("addr:city")
    parts = []
    if street:
        parts.append(" ".join(filter(None, (street, number))))
    if city:
        parts.append(city)
    return ", ".join(parts) or None


def resample(points: Sequence[tuple[float, float]], step_m: float) -> list[tuple[float, float]]:
    """Duennt eine Polylinie auf einen Stuetzpunkt alle [step_m] aus, Enden bleiben."""
    if len(points) <= 2:
        return list(points)
    result = [points[0]]
    carried = 0.0
    for previous, current in zip(points, points[1:]):
        carried += haversine_m(previous[0], previous[1], current[0], current[1])
        if carried >= step_m:
            result.append(current)
            carried = 0.0
    if result[-1] != points[-1]:
        result.append(points[-1])
    return result


def cumulative_distances(points: Sequence[tuple[float, float]]) -> list[float]:
    out = [0.0]
    for previous, current in zip(points, points[1:]):
        out.append(out[-1] + haversine_m(previous[0], previous[1], current[0], current[1]))
    return out


def route_match(
    points: Sequence[tuple[float, float]],
    cumulative: Sequence[float],
    seconds: Sequence[float] | None,
    lat: float,
    lon: float,
) -> tuple[float, float, float]:
    """Kuerzester Abstand zur Route, Strecke ab Start und Fahrzeit ab Start.

    Gerechnet wird in einer lokalen Meter-Ebene um den Suchpunkt. Ueber die Laenge
    eines Streckensegments ist das genau genug und deutlich billiger als Geodaesie.
    """
    lat_scale = METERS_PER_DEGREE_LAT
    lon_scale = METERS_PER_DEGREE_LAT * math.cos(math.radians(lat))
    px, py = lon * lon_scale, lat * lat_scale

    best_distance = float("inf")
    best_progress = 0.0
    best_seconds = 0.0

    for index in range(1, len(points)):
        a_lat, a_lon = points[index - 1]
        b_lat, b_lon = points[index]
        ax, ay = a_lon * lon_scale, a_lat * lat_scale
        bx, by = b_lon * lon_scale, b_lat * lat_scale
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
        distance = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if distance < best_distance:
            best_distance = distance
            best_progress = cumulative[index - 1] + t * (cumulative[index] - cumulative[index - 1])
            if seconds is not None:
                best_seconds = seconds[index - 1] + t * (seconds[index] - seconds[index - 1])
    return best_distance, best_progress, best_seconds


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Google-Encoded-Polyline, wie OSRM sie liefert."""
    factor = 10**precision
    coordinates: list[tuple[float, float]] = []
    index = lat = lon = 0
    while index < len(encoded):
        for is_lat in (True, False):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lat:
                lat += delta
            else:
                lon += delta
        coordinates.append((lat / factor, lon / factor))
    return coordinates


def chunked(items: Sequence, size: int) -> Iterable[Sequence]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
