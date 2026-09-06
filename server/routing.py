"""Ortssuche und Routing, gebuendelt auf dem Server.

Der Client stellt eine Frage ("von hier nach da"), der Server erledigt Geocoding,
Routing und die Suche in einem Rutsch. Antworten werden zwischengespeichert, damit
die oeffentlichen Dienste nicht fuer jeden Tastendruck angefasst werden.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

from geo import decode_polyline

USER_AGENT = "WhopperUndWatt/2.0 (PWA; OpenStreetMap-Daten)"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM = "https://router.project-osrm.org"

GEOCODE_TTL_S = 24 * 3600
ROUTE_TTL_S = 6 * 3600


class _Cache:
    def __init__(self, ttl_s: float, limit: int = 512):
        self._ttl = ttl_s
        self._limit = limit
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            stored_at, value = item
            if time.time() - stored_at > self._ttl:
                del self._items[key]
                return None
            return value

    def put(self, key: str, value: object) -> None:
        with self._lock:
            if len(self._items) >= self._limit:
                oldest = min(self._items, key=lambda k: self._items[k][0])
                del self._items[oldest]
            self._items[key] = (time.time(), value)


_geocode_cache = _Cache(GEOCODE_TTL_S)
_route_cache = _Cache(ROUTE_TTL_S)

# Nominatim erlaubt eine Anfrage pro Sekunde. Der Server haelt sich global daran.
_nominatim_lock = threading.Lock()
_last_nominatim_call = 0.0


def _fetch(url: str, timeout: int = 60) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "de"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def geocode(query: str) -> dict | None:
    key = query.strip().lower()
    if not key:
        return None
    cached = _geocode_cache.get(key)
    if cached is not None:
        return cached or None

    global _last_nominatim_call
    with _nominatim_lock:
        wait = 1.1 - (time.time() - _last_nominatim_call)
        if wait > 0:
            time.sleep(wait)
        _last_nominatim_call = time.time()
        url = NOMINATIM + "?" + urllib.parse.urlencode(
            {"q": query, "format": "jsonv2", "limit": 1}
        )
        results = _fetch(url, timeout=30)

    if not results:
        _geocode_cache.put(key, {})
        return None
    first = results[0]
    place = {
        "label": ", ".join(first.get("display_name", query).split(",")[:2]).strip(),
        "lat": float(first["lat"]),
        "lon": float(first["lon"]),
    }
    _geocode_cache.put(key, place)
    return place


def route(waypoints: list[tuple[float, float]]) -> dict:
    """Fahrt ueber alle Wegpunkte, mit Fahrzeit je Segment."""
    if len(waypoints) < 2:
        raise ValueError("Eine Route braucht Start und Ziel")

    key = ";".join(f"{lat:.5f},{lon:.5f}" for lat, lon in waypoints)
    cached = _route_cache.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    coordinates = ";".join(f"{lon},{lat}" for lat, lon in waypoints)
    url = (
        f"{OSRM}/route/v1/driving/{coordinates}"
        "?overview=full&geometries=polyline&alternatives=false&steps=false"
        "&annotations=duration"
    )
    payload = _fetch(url, timeout=90)
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ValueError(f"Keine Route gefunden ({payload.get('code')})")

    best = payload["routes"][0]
    points = decode_polyline(best["geometry"])
    seconds = _cumulative_seconds(best, len(points))
    result = {
        "polyline": best["geometry"],
        "points": points,
        "cumulativeSeconds": seconds,
        "distanceM": best.get("distance"),
        "durationS": best.get("duration"),
    }
    _route_cache.put(key, result)
    return result


def _cumulative_seconds(route_json: dict, point_count: int) -> list[float] | None:
    """Fahrzeit ab Start je Stuetzpunkt.

    Die Summe der Segment-Annotationen weicht leicht von der Gesamtdauer ab
    (Abbiegekosten), deshalb wird linear auf die ausgewiesene Dauer skaliert.
    """
    durations: list[float] = []
    for leg in route_json.get("legs", []):
        annotation = leg.get("annotation") or {}
        durations.extend(annotation.get("duration") or [])
    if len(durations) != point_count - 1:
        return None

    total = route_json.get("duration") or 0.0
    summed = sum(durations)
    factor = (total / summed) if summed > 0 and total > 0 else 1.0

    cumulative = [0.0]
    for value in durations:
        cumulative.append(cumulative[-1] + value * factor)
    return cumulative
