"""Routen aus hochgeladenen Dateien: GPX und der Excel-Export von ABRP.

Der Unterschied ist wichtig und liegt am Format, nicht an dieser Software:
GPX enthaelt die Strecke punktgenau, der ABRP-Export nur die Adresstexte der
Wegpunkte. Wegpunkte, die in ABRP per Klick auf die Karte gesetzt wurden, stehen
dort als "Punkt auf der Karte" und tragen ueberhaupt keine Ortsangabe.
"""

from __future__ import annotations

import io
import re
import zipfile
from html import unescape

import routing

_GPX_TAGS = ("trkpt", "rtept", "wpt")
_LAT = re.compile(r'\blat\s*=\s*"([-0-9.eE+]+)"', re.IGNORECASE)
_LON = re.compile(r'\blon\s*=\s*"([-0-9.eE+]+)"', re.IGNORECASE)

_CELL = re.compile(r'<c\b[^>]*?r="A\d+"[^>]*?(?:/>|>(.*?)</c>)', re.DOTALL)
_INLINE = re.compile(r"<t[^>]*>(.*?)</t>", re.DOTALL)
_VALUE = re.compile(r"<v>(.*?)</v>", re.DOTALL)
_SHARED_ITEM = re.compile(r"<si>(.*?)</si>", re.DOTALL)

_UNIT = re.compile(r"^\s*\d+([.,]\d+)?\s*(km|m|min\.?|std\.?|h|kwh|%|€)\s*$", re.IGNORECASE)
_CLOCK = re.compile(r"^\s*\d{1,2}:\d{2}\s*$")
_PLACEHOLDERS = ("punkt auf der karte", "point on map", "map point")


def parse_gpx(text: str) -> list[tuple[float, float]]:
    """Trackpunkte schlagen Routenpunkte, Routenpunkte schlagen Wegpunkte."""
    for tag in _GPX_TAGS:
        points = []
        for match in re.finditer(rf"<{tag}\b[^>]*>", text, re.IGNORECASE):
            lat = _LAT.search(match.group(0))
            lon = _LON.search(match.group(0))
            if lat and lon:
                points.append((float(lat.group(1)), float(lon.group(1))))
        if len(points) >= 2:
            return points
    return []


def is_placeholder(value: str) -> bool:
    return value.strip().lower() in _PLACEHOLDERS


def is_address(value: str) -> bool:
    text = value.strip()
    if len(text) < 5 or text.lower().startswith("http"):
        return False
    if is_placeholder(text) or _UNIT.match(text) or _CLOCK.match(text):
        return False
    if not any(character.isalpha() for character in text):
        return False
    return "," in text or any(character.isdigit() for character in text)


def parse_xlsx(data: bytes) -> tuple[list[str], int]:
    """Adressen aus Spalte A und die Zahl der Wegpunkte ohne Ortsangabe."""
    sheet = None
    shared_xml = None
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if name.endswith("xl/worksheets/sheet1.xml"):
                sheet = archive.read(name).decode("utf-8")
            elif name.endswith("xl/sharedStrings.xml"):
                shared_xml = archive.read(name).decode("utf-8")
    if sheet is None:
        raise ValueError("Die Datei enthaelt kein Tabellenblatt.")

    shared = [
        "".join(unescape(text) for text in _INLINE.findall(item))
        for item in _SHARED_ITEM.findall(shared_xml or "")
    ]

    column = []
    for match in _CELL.finditer(sheet):
        body = match.group(1) or ""
        inline = _INLINE.search(body)
        if inline:
            column.append(unescape(inline.group(1)).strip())
            continue
        value = _VALUE.search(body)
        if not value:
            continue
        raw = unescape(value.group(1)).strip()
        if 't="s"' in match.group(0):
            index = int(raw) if raw.isdigit() else -1
            column.append(shared[index] if 0 <= index < len(shared) else "")
        else:
            column.append(raw)

    column = [value for value in column if value]
    return [value for value in column if is_address(value)], sum(map(is_placeholder, column))


def read_plan(data: bytes, filename: str) -> dict:
    """Macht aus einer hochgeladenen Datei Wegpunkte fuer die Routensuche."""
    if data[:2] == b"PK":
        addresses, placeholders = parse_xlsx(data)
        notes = []
        if placeholders:
            notes.append(
                f"{placeholders} Wegpunkt(e) im Export sind \"Punkt auf der Karte\" "
                "und enthalten keine Ortsangabe."
            )
        if len(addresses) < 2:
            notes.append(
                "Der ABRP-Excel-Export enthält keine Koordinaten, nur Adresstexte. "
                f"Verwertbar ist hier {len(addresses)}. In ABRP die Wegpunkte über die "
                "Adresssuche setzen statt per Klick auf die Karte."
            )
            return {"waypoints": [], "addresses": addresses, "note": " ".join(notes)}

        waypoints = []
        failed = []
        for address in addresses:
            place = routing.geocode(address)
            if place:
                waypoints.append([place["lat"], place["lon"]])
            else:
                failed.append(address)
        if failed:
            notes.append("Nicht gefunden: " + "; ".join(failed) + ".")
        return {
            "waypoints": waypoints,
            "addresses": addresses,
            "label": f"ABRP-Plan ({len(waypoints)} Wegpunkte)",
            "note": " ".join(notes),
        }

    points = parse_gpx(data.decode("utf-8", errors="replace"))
    if len(points) < 2:
        raise ValueError("Keine Streckenpunkte in der Datei gefunden.")
    # Der Router nimmt nur eine Handvoll Stuetzpunkte, die Form der Strecke
    # entsteht wieder beim Routen dazwischen.
    return {
        "waypoints": [[lat, lon] for lat, lon in _thin(points, 20)],
        "label": filename or "GPX-Route",
        "note": "",
    }


def _thin(points: list[tuple[float, float]], limit: int) -> list[tuple[float, float]]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    thinned = [points[round(index * step)] for index in range(limit)]
    thinned[-1] = points[-1]
    return thinned
