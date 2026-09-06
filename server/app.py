#!/usr/bin/env python3
"""HTTP-Server fuer Whopper & Watt.

Liefert die PWA aus und beantwortet die Suchen aus der lokalen Datenbank.
Bewusst nur Standardbibliothek: kein Build, keine Abhaengigkeiten, laeuft ueberall,
wo Python 3.11 liegt.

    python3 server/app.py --port 8000

Fuer den Betrieb hinter einem Reverse Proxy reicht --host 127.0.0.1 (Standard).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import SpotDatabase  # noqa: E402
import plan  # noqa: E402
import routing  # noqa: E402

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
MAX_BODY_BYTES = 512 * 1024
MAX_WAYPOINTS = 25
MAX_RADIUS_M = 200_000
MAX_CORRIDOR_M = 20_000
MAX_GAP_M = 2_000

# Dateien, die sich nie aendern, duerfen lange im Browser bleiben.
LONG_CACHE = ("/vendor/", "/icons/")


class Handler(BaseHTTPRequestHandler):
    server_version = "WhopperWatt/2.0"
    database: SpotDatabase

    def do_GET(self) -> None:  # noqa: N802 - von BaseHTTPRequestHandler vorgegeben
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)
        started = time.time()
        try:
            if route == "/api/meta":
                self._json(self._meta())
            elif route == "/api/spots":
                self._json(self._spots(query, started))
            elif route == "/api/geocode":
                self._json(self._geocode(query))
            elif route.startswith("/api/"):
                self._error(HTTPStatus.NOT_FOUND, "Unbekannter Endpunkt")
            else:
                self._static(route)
        except ValueError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:  # noqa: BLE001 - der Client soll etwas Lesbares bekommen
            self._error(HTTPStatus.BAD_GATEWAY, f"{type(error).__name__}: {error}")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        started = time.time()
        try:
            if parsed.path == "/api/route-spots":
                self._json(self._route_spots(self._body(), started))
            elif parsed.path == "/api/plan":
                name = (parse_qs(parsed.query).get("name") or [""])[0]
                self._json(plan.read_plan(self._raw_body(), name))
            else:
                self._error(HTTPStatus.NOT_FOUND, "Unbekannter Endpunkt")
        except ValueError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:  # noqa: BLE001
            self._error(HTTPStatus.BAD_GATEWAY, f"{type(error).__name__}: {error}")

    # ---- Endpunkte ------------------------------------------------------

    def _meta(self) -> dict:
        meta = dict(self.database.meta())
        meta["ok"] = True
        return meta

    def _spots(self, query: dict, started: float) -> dict:
        lat = _number(query, "lat", required=True)
        lon = _number(query, "lon", required=True)
        radius_m = min(_number(query, "radius_km", default=25) * 1000, MAX_RADIUS_M)
        gap_m = min(_number(query, "gap_m", default=300), MAX_GAP_M)
        only_enbw = _flag(query, "only_enbw", default=True)

        spots = self.database.spots_near(lat, lon, radius_m, gap_m, only_enbw)
        return {
            "mode": "radius",
            "center": {"lat": lat, "lon": lon},
            "radiusM": radius_m,
            "spots": spots,
            "queryMs": round((time.time() - started) * 1000, 1),
        }

    def _geocode(self, query: dict) -> dict:
        text = (query.get("q") or [""])[0].strip()
        if not text:
            raise ValueError("Parameter q fehlt")
        place = routing.geocode(text)
        if not place:
            raise ValueError(f'Ort "{text}" nicht gefunden')
        return place

    def _route_spots(self, body: dict, started: float) -> dict:
        corridor_m = min(float(body.get("corridorM", 3000)), MAX_CORRIDOR_M)
        gap_m = min(float(body.get("gapM", 300)), MAX_GAP_M)
        only_enbw = bool(body.get("onlyEnbw", True))

        waypoints, label = self._waypoints(body)
        computed = routing.route(waypoints)
        spots = self.database.spots_along_route(
            points=computed["points"],
            seconds=computed["cumulativeSeconds"],
            corridor_m=corridor_m,
            gap_m=gap_m,
            only_enbw=only_enbw,
        )
        return {
            "mode": "route",
            "route": {
                "polyline": computed["polyline"],
                "distanceM": computed["distanceM"],
                "durationS": computed["durationS"],
                "hasMeasuredDurations": computed["cumulativeSeconds"] is not None,
                "label": label,
            },
            "spots": spots,
            "queryMs": round((time.time() - started) * 1000, 1),
        }

    def _waypoints(self, body: dict) -> tuple[list[tuple[float, float]], str]:
        """Wegpunkte kommen entweder als Koordinaten oder als Adresstexte."""
        raw_points = body.get("waypoints")
        if raw_points:
            if len(raw_points) > MAX_WAYPOINTS:
                raise ValueError(f"Hoechstens {MAX_WAYPOINTS} Wegpunkte")
            points = [(float(item[0]), float(item[1])) for item in raw_points]
            if len(points) < 2:
                raise ValueError("Eine Route braucht Start und Ziel")
            return points, body.get("label") or f"{len(points)} Wegpunkte"

        places = []
        for text in body.get("places") or []:
            place = routing.geocode(str(text))
            if not place:
                raise ValueError(f'Ort "{text}" nicht gefunden')
            places.append(place)
        if len(places) < 2:
            raise ValueError("Eine Route braucht Start und Ziel")
        return (
            [(place["lat"], place["lon"]) for place in places],
            f"{places[0]['label']} nach {places[-1]['label']}",
        )

    # ---- Infrastruktur --------------------------------------------------

    def _raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("Leerer Request")
        if length > MAX_BODY_BYTES:
            raise ValueError("Datei zu gross")
        return self.rfile.read(length)

    def _body(self) -> dict:
        try:
            return json.loads(self._raw_body())
        except json.JSONDecodeError as error:
            raise ValueError(f"Kein gueltiges JSON: {error}") from error

    def _static(self, route: str) -> None:
        relative = "index.html" if route in ("", "/") else route.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        # Kein Ausbrechen aus dem Web-Verzeichnis.
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "Nicht gefunden")
            return

        content_type, _ = mimetypes.guess_type(target.name)
        if target.suffix == ".webmanifest":
            content_type = "application/manifest+json"
        payload = target.read_bytes()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        if any(part in route for part in LONG_CACHE):
            self.send_header("Cache-Control", "public, max-age=604800")
        else:
            # Der Service Worker selbst darf nie aus dem Browsercache kommen.
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"ok": False, "error": message}, status)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - Signatur vorgegeben
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def _number(query: dict, name: str, default: float | None = None, required: bool = False) -> float:
    values = query.get(name)
    if not values:
        if required:
            raise ValueError(f"Parameter {name} fehlt")
        return float(default or 0)
    try:
        return float(values[0])
    except ValueError as error:
        raise ValueError(f"Parameter {name} ist keine Zahl") from error


def _flag(query: dict, name: str, default: bool) -> bool:
    values = query.get(name)
    if not values:
        return default
    return values[0].lower() in ("1", "true", "ja", "yes", "on")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(Path(__file__).resolve().parent / "data/whopper.sqlite"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()

    Handler.database = SpotDatabase(Path(arguments.db))
    meta = Handler.database.meta()
    server = ThreadingHTTPServer((arguments.host, arguments.port), Handler)
    print(
        f"Whopper & Watt auf http://{arguments.host}:{arguments.port}  "
        f"({meta.get('burgers', '?')} Filialen, {meta.get('chargers', '?')} Saeulen, "
        f"Stand {meta.get('ingested_at', '?')})",
        flush=True,
    )
    threading.current_thread().name = "http"
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
