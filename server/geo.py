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

# Ohne Wikidata-Treffer wird der Name nur bei Essensschuppen geglaubt. Sonst
# waere jeder U-Bahn-Zugang namens "Subway" eine Filiale.
FOOD_AMENITIES = frozenset({"fast_food", "restaurant", "cafe"})

AMENITY_LABELS = {"restaurant": "Restaurant", "fast_food": "Imbiss", "cafe": "Café"}

# diet:vegan ist das etablierte Tag dafuer, ob es vegan etwas zu essen gibt.
# "only" heisst rein vegan, "limited" heisst eine Handvoll Gerichte.
VEGAN_OPTION_VALUES = frozenset({"yes", "only", "limited"})
VEGAN_ONLY_VALUE = "only"


@dataclass(frozen=True)
class Kind:
    """Eine Kategorie, die in der App an- und abwaehlbar ist.

    Ketten und Ernaehrungsform stehen bewusst nebeneinander in einer Liste,
    denn aus Sicht der Benutzung sind sie dasselbe: ein Knopf, der Treffer
    dazuholt. Ein Lokal kann in mehreren Kategorien liegen, ein Burger King
    ist beides.
    """

    key: str
    label: str


KINDS: dict[str, Kind] = {
    "bk": Kind("bk", "Burger King"),
    "subway": Kind("subway", "Subway"),
    "vegan": Kind("vegan", "Vegane Optionen"),
    "vegan_only": Kind("vegan_only", "Rein vegan"),
}

# Burger King ist die Voreinstellung, alles andere waehlt man dazu.
DEFAULT_KINDS = ("bk",)

# Wo an einer Ladesaeule der Betreiber stehen kann. Die Schreibweisen in OSM
# sind uneinheitlich: "EnBW", "EnBW mobility+ AG und Co.KG", "Lidl Stiftung
# GmbH & Co. KG". Deshalb Wikidata als Anker und Textsuche als Auffangnetz.
_OPERATOR_KEYS = ("operator", "network", "brand", "owner", "name", "operator:short")
_WIKIDATA_KEYS = ("operator:wikidata", "network:wikidata", "brand:wikidata")


@dataclass(frozen=True)
class Network:
    """Ein Ladenetz, wie es in OSM auffindbar ist."""

    key: str
    label: str
    wikidata: frozenset[str]
    needles: tuple[str, ...]


# Die Wikidata-Ids sind am Datenbestand geprueft, nicht aus dem Kopf: Q644304
# steht an 919 Saeulen, Q151954 an 225, Q685967 an 102. Der vorherige
# EnBW-Anker Q321820 kam null mal vor und war damit wirkungslos; gerettet hat
# das nur die Namenssuche.
NETWORKS: dict[str, Network] = {
    "enbw": Network("enbw", "EnBW", frozenset({"Q644304"}), ("enbw", "en bw")),
    "lidl": Network("lidl", "Lidl", frozenset({"Q151954"}), ("lidl",)),
    "kaufland": Network("kaufland", "Kaufland", frozenset({"Q685967"}), ("kaufland",)),
}

# Ab hier gilt eine Saeule als Schnelllader. 50 kW ist die uebliche Grenze
# zwischen AC-Laden und Gleichstrom-Schnellladen.
FAST_CHARGER_KW = 50.0

# Die Leistungsstufen, nach denen die App filtern kann: keine Grenze oder
# Schnelllader. Diese Liste ist nicht bloss Dekoration, an ihr haengt die
# Kuerzung der Saeulenlisten im statischen Export (siehe export_static). Wer
# hier eine Stufe ergaenzt, muss sie dort mitnehmen, sonst faengt der Export
# an, Lokale zu verlieren. Der Test dazu heisst
# test_capped_export_answers_like_the_full_list.
POWER_STEPS: tuple[float, ...] = (0.0, FAST_CHARGER_KW)


def power_step(power_kw: float | None) -> int:
    """Hoechste Leistungsstufe, die diese Saeule erfuellt, als Index."""
    value = power_kw or 0.0
    return max(index for index, step in enumerate(POWER_STEPS) if value >= step)


# Ab hier gilt ein Ad-hoc-Preis als guenstig. 0,50 EUR/kWh liegt ungefaehr am
# unteren Rand dessen, was die grossen Netze ohne Vertrag am Schnelllader
# nehmen; Lidl und Kaufland liegen darunter, deshalb der Wunsch nach dem Knopf.
CHEAP_PRICE_EUR = 0.50

# Wie POWER_STEPS eine Filterstufe, und aus demselben Grund hier und nicht im
# Export: die Kuerzung der Saeulenlisten muss je Stufe die naechste behalten,
# sonst verschwindet die guenstige Saeule hinter zwei teuren.
PRICE_STEPS: tuple[float, ...] = (CHEAP_PRICE_EUR,)


def price_step(price_kwh: float | None) -> int:
    """Guenstigste erfuellte Preisstufe als Index, 0 wenn kein Preis bekannt.

    Anders herum als bei der Leistung: teuer ist die Voreinstellung, denn ein
    fehlender Preis darf nie als guenstig durchgehen. Index 0 heisst "keine
    Aussage", Index 1 heisst "unter CHEAP_PRICE_EUR".
    """
    if price_kwh is None:
        return 0
    return max(
        (index + 1 for index, grenze in enumerate(PRICE_STEPS) if price_kwh < grenze),
        default=0,
    )


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


# amenity=charging_station steht in OSM auch an Fahrrad-Ladestationen. Gemessen
# waren 898 von 25456 Saeulen im Bestand keine Autoladesaeulen, gut drei
# Prozent, und sie steckten in 4811 Paaren: die App zeigte E-Bike-Ladepunkte
# als "Ladesaeule" neben dem Burger King an.
_BICYCLE_HINT = re.compile(r"e-?bike|fahrrad|bike-energy|pedelec", re.I)


def is_car_charger(tags: dict) -> bool:
    """Laedt hier ein Auto?

    Zwei Merkmale reichen und sind belastbar: ein ausdrueckliches motorcar=no,
    und ein Name oder Betreiber, der vom Fahrrad spricht. Ueber bicycle=yes zu
    gehen waere verlockend, aber falsch: von den Saeulen, die nur so auffallen,
    tragen 269 trotzdem Autosteckertypen. Und motorcar=yes zu verlangen ginge
    gar nicht, das Tag fehlt an 14652 von 25456 Saeulen.
    """
    if (tags.get("motorcar") or "").strip().lower() == "no":
        return False
    haystack = " ".join(filter(None, (tags.get("name"), tags.get("operator"), tags.get("brand"))))
    return not _BICYCLE_HINT.search(haystack)


def matches_network(tags: dict, network: Network) -> bool:
    if any(key.lower().startswith(f"ref:{network.key}") for key in tags):
        return True
    if any(tags.get(key) in network.wikidata for key in _WIKIDATA_KEYS):
        return True
    haystack = " ".join(filter(None, (tags.get(key) for key in _OPERATOR_KEYS))).lower()
    return any(needle in haystack for needle in network.needles)


def network_of(tags: dict) -> str | None:
    """Schluessel des ersten passenden Netzes, oder None fuer alle anderen."""
    for network in NETWORKS.values():
        if matches_network(tags, network):
            return network.key
    return None


def is_enbw(tags: dict) -> bool:
    return matches_network(tags, NETWORKS["enbw"])


def parse_networks(raw: str | None) -> list[Network]:
    """Kommaliste von Netzschluesseln. Leer heisst: alle Netze, kein Filter."""
    keys = [key.strip().lower() for key in (raw or "").split(",") if key.strip()]
    chosen: list[Network] = []
    for key in keys:
        if key in NETWORKS and NETWORKS[key] not in chosen:
            chosen.append(NETWORKS[key])
    return chosen


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


def _diet_vegan(tags: dict) -> str:
    return (tags.get("diet:vegan") or "").strip().lower()


def has_vegan_options(tags: dict) -> bool:
    """Gibt es hier laut OSM vegan etwas zu essen?

    Bewusst nur die drei positiven Werte. "no" und ein fehlendes Tag sind
    beide ein Nein, aber ein sehr unterschiedlich starkes: bei knapp einem
    Zehntel der deutschen Gastronomie ist das Tag ueberhaupt gesetzt.
    """
    return _diet_vegan(tags) in VEGAN_OPTION_VALUES


def is_vegan_only(tags: dict) -> bool:
    """Rein vegan, also kein Tier auf der Karte. Genau das meint diet:vegan=only."""
    return _diet_vegan(tags) == VEGAN_ONLY_VALUE


def parse_kinds(raw: str | None) -> list[Kind]:
    """Kommaliste von Kategorieschluesseln, unbekannte werden ignoriert."""
    keys = [key.strip().lower() for key in (raw or "").split(",") if key.strip()]
    chosen: list[Kind] = []
    for key in keys:
        if key in KINDS and KINDS[key] not in chosen:
            chosen.append(KINDS[key])
    return chosen or [KINDS[key] for key in DEFAULT_KINDS]


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
