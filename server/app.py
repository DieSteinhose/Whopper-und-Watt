#!/usr/bin/env python3
"""HTTP-Server fuer Whopper & Watt.

Liefert die PWA aus und beantwortet die Suchen aus der lokalen Datenbank.
Bewusst nur Standardbibliothek: kein Build, keine Abhaengigkeiten, laeuft ueberall,
wo Python 3.11 liegt.

    python3 server/app.py --port 8000                       # nur dieser Rechner
    python3 server/app.py --host 0.0.0.0 --trust-proxy      # im Netz erreichbar

Der Standard ist Absicht: an alle Schnittstellen zu binden gehoert eine bewusste
Entscheidung, kein Vorgabewert. Laeuft der Reverse Proxy auf demselben Rechner,
reicht 127.0.0.1. Steht er woanders, im Docker-Netz oder auf einem anderen Host,
braucht es --host 0.0.0.0, sonst meldet er nur, dass niemand antwortet.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import socket
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

# Endpunkte, die nach draussen telefonieren oder Dateien auspacken, bekommen
# eine Bremse pro IP. Die Suche selbst ist billig und bleibt frei.
EXPENSIVE = ("/api/geocode", "/api/route-spots", "/api/plan")
EXPENSIVE_PER_MINUTE = 20

# Die Seite laedt alles aus dem eigenen Verzeichnis, nur die Kartenkacheln nicht.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' data: https://tile.openstreetmap.org; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)


class RateLimiter:
    """Gleitendes Fenster je Schluessel, ohne Fremdbibliothek."""

    def __init__(self, limit: int, window_s: float = 60.0):
        self._limit = limit
        self._window = window_s
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            hits = [stamp for stamp in self._hits.get(key, ()) if now - stamp < self._window]
            if len(hits) >= self._limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            # Gelegentlich aufraeumen, damit der Speicher nicht mitwaechst.
            if len(self._hits) > 4096:
                self._hits = {
                    other: stamps
                    for other, stamps in self._hits.items()
                    if stamps and now - stamps[-1] < self._window
                }
            return True


class Handler(BaseHTTPRequestHandler):
    server_version = "WhopperWatt/2.0"
    # HTTP/1.1 haelt die Verbindung offen. Jede Antwort setzt Content-Length,
    # sonst wuerde der Browser auf ein Ende warten, das nie kommt.
    protocol_version = "HTTP/1.1"
    database: SpotDatabase
    limiter = RateLimiter(EXPENSIVE_PER_MINUTE)
    trust_proxy = False

    @property
    def client_ip(self) -> str:
        """Hinter einem Reverse Proxy steht die echte Adresse im Header."""
        if self.trust_proxy:
            forwarded = self.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return self.client_address[0]

    def _throttled(self, route: str) -> bool:
        if not route.startswith(EXPENSIVE):
            return False
        if self.limiter.allow(self.client_ip):
            return False
        self._error(
            HTTPStatus.TOO_MANY_REQUESTS,
            f"Zu viele Anfragen. Erlaubt sind {EXPENSIVE_PER_MINUTE} pro Minute.",
        )
        return True

    def do_GET(self) -> None:  # noqa: N802 - von BaseHTTPRequestHandler vorgegeben
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)
        started = time.time()
        if self._throttled(route):
            return
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
        if self._throttled(parsed.path):
            return
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
        self._security_headers()
        if target.suffix in (".html", ".webmanifest"):
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
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
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

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


def _reachable_urls(host: str, port: int) -> list[str]:
    """Bei 0.0.0.0 die tatsaechlichen Adressen zeigen, nicht die Bindeadresse."""
    if host not in ("0.0.0.0", "::"):
        return [f"http://{host}:{port}"]

    addresses = {"127.0.0.1"}
    try:
        # Verbindet nichts, ermittelt nur die Adresse der Standardroute.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))  # reservierter Testbereich
        addresses.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return [f"http://{address}:{port}" for address in sorted(addresses)]


def _flag(query: dict, name: str, default: bool) -> bool:
    values = query.get(name)
    if not values:
        return default
    return values[0].lower() in ("1", "true", "ja", "yes", "on")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(Path(__file__).resolve().parent / "data/whopper.sqlite"))
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="127.0.0.1 nur lokal, 0.0.0.0 auf allen Schnittstellen",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--trust-proxy",
        action="store_true",
        help="X-Forwarded-For auswerten. Nur setzen, wenn wirklich ein Reverse Proxy"
        " davorsteht, sonst kann sich jeder eine fremde IP ausdenken.",
    )
    arguments = parser.parse_args()

    Handler.database = SpotDatabase(Path(arguments.db))
    Handler.trust_proxy = arguments.trust_proxy
    meta = Handler.database.meta()
    server = ThreadingHTTPServer((arguments.host, arguments.port), Handler)

    print(
        f"Whopper & Watt: {meta.get('burgers', '?')} Filialen, "
        f"{meta.get('chargers', '?')} Saeulen, Stand {meta.get('ingested_at', '?')}",
        flush=True,
    )
    for url in _reachable_urls(arguments.host, arguments.port):
        print(f"  {url}", flush=True)
    if arguments.host in ("127.0.0.1", "localhost", "::1"):
        print(
            "  Nur von diesem Rechner erreichbar. Fuer das Netz:"
            " --host 0.0.0.0 (und --trust-proxy hinter einem Reverse Proxy)",
            flush=True,
        )
    else:
        print(
            "  Hinweis: Installation als App, Service Worker und die Standortabfrage"
            " verlangen HTTPS. Ueber eine nackte IP ohne TLS bleibt die Seite eine"
            " normale Webseite.",
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
