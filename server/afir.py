"""Liest Ladeinfrastruktur-Daten nach dem AFIR-Profil von DATEX II 3.

Seit dem 14.04.2026 muessen Betreiber oeffentlich zugaenglicher Ladepunkte ihre
Daten in diesem Format bereitstellen, kostenfrei und diskriminierungsfrei, und
der Ad-hoc-Preis ist ein Pflichtfeld. Der nationale Zugangspunkt in Deutschland
ist die Mobilithek. Genau das fehlt in OpenStreetMap: dort traegt nur jede
sechzigste Saeule ueberhaupt einen Preis, und dann als freien Text.

Das Modell steckt in zwei Veroeffentlichungen, und man braucht beide:

  EnergyInfrastructureTablePublication   Standorte, Ladepunkte, EVSE-Ids.
                                         Aendert sich selten.
  EnergyInfrastructureStatusPublication  Belegung und Preis. Aendert sich
                                         staendig, laut Profil binnen einer
                                         Minute.

Verbunden sind sie ueber die idG eines Ladepunkts: die Statusmeldung verweist
mit reference.idG auf den refillPoint aus der Tabelle. Erst beides zusammen
ergibt "diese Saeule kostet 0,37 EUR je kWh".

Die beiden amtlichen Beispiele haben unterschiedliche Umschlaege, die Tabelle
liegt direkt unter "payload", der Status unter "messageContainer.payload[]".
read_publication nimmt beide.

Quelle des Datenmodells:
https://github.com/MobilithekDE/AFIR-DATEX-II-Recharging-Profil
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Leistungen stehen im Profil in Watt, unsere Datenbank rechnet in Kilowatt.
WATT_PER_KW = 1000.0

# Der Preis je Kilowattstunde ist der, nach dem gefragt wird. Die anderen
# Bestandteile reisen mit, damit die App nicht "0,37 EUR/kWh" hinschreibt und
# die Standgebuehr verschweigt.
PRICE_PER_KWH = "pricePerKWh"

DEFAULT_CURRENCY = "EUR"

# Koordinaten aus fremden Quellen werden nicht geglaubt, sondern geprueft. Das
# amtliche Beispiel selbst enthaelt eine Station mit latitude == longitude ==
# 50.779594, also einer Laenge weit ausserhalb Europas. Solche Punkte fliegen
# raus, statt spaeter still gegen nichts zu matchen.
PLAUSIBLE_LAT = (35.0, 72.0)
PLAUSIBLE_LON = (-25.0, 45.0)


def plausible(lat: float | None, lon: float | None) -> bool:
    """Liegt der Punkt ueberhaupt in Europa?"""
    if lat is None or lon is None:
        return False
    if not (PLAUSIBLE_LAT[0] <= lat <= PLAUSIBLE_LAT[1]):
        return False
    if not (PLAUSIBLE_LON[0] <= lon <= PLAUSIBLE_LON[1]):
        return False
    # Gleiche Zahl fuer beides ist in der Praxis ein Platzhalter, kein Ort.
    return abs(lat - lon) > 1e-9


@dataclass(frozen=True)
class ChargingPoint:
    """Ein Ladepunkt aus der Tabellen-Veroeffentlichung."""

    ref_id: str                      # idG des refillPoint, der Schluessel zum Status
    evse_id: str | None              # DE*ABC*E123, passt auf ref:EU:EVSE in OSM
    station_id: str | None           # idG der Station
    bnetza_id: str | None            # Ladestations-Id der Bundesnetzagentur
    lat: float | None
    lon: float | None
    operator: str | None
    power_kw: float | None
    rate_ids: tuple[str, ...] = ()   # welche energyRate fuer diesen Punkt gelten


@dataclass(frozen=True)
class Price:
    """Ein Preisstand aus der Status-Veroeffentlichung."""

    ref_id: str
    price_kwh: float | None
    currency: str
    updated_at: str | None
    status: str | None
    components: tuple[tuple[str, float], ...] = ()   # alle Bestandteile, roh


@dataclass
class Publication:
    points: list[ChargingPoint] = field(default_factory=list)
    prices: dict[str, Price] = field(default_factory=dict)
    currencies: dict[str, str] = field(default_factory=dict)   # rate idG -> Waehrung


# ---- Hilfen fuer ein Modell, das alles verschachtelt und alles optional macht ----


def _as_list(value: Any) -> list:
    """DATEX II schreibt Einzelwerte mal als Objekt, mal als Liste mit einem Element."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(node: Any) -> str | None:
    """Holt den Text aus einem MultilingualString, deutsch bevorzugt."""
    if node is None:
        return None
    if isinstance(node, str):
        return node.strip() or None
    values = _as_list(node.get("values") if isinstance(node, dict) else None)
    if values and isinstance(values[0], dict) and "values" in values[0]:
        values = _as_list(values[0]["values"])
    deutsch = [item for item in values if isinstance(item, dict) and item.get("lang") == "de"]
    for item in (deutsch or values):
        if isinstance(item, dict) and (item.get("value") or "").strip():
            return item["value"].strip()
    return None


def _external_id(node: Any, kind: str) -> str | None:
    """Sucht eine externe Id einer bestimmten Art, etwa evseId oder stationIdBNetzA."""
    for entry in _as_list(node):
        if not isinstance(entry, dict):
            continue
        type_of = entry.get("typeOfIdentifier") or {}
        if type_of.get("extendedValueG") == kind or type_of.get("value") == kind:
            identifier = (entry.get("identifier") or "").strip()
            if identifier:
                return identifier
    return None


def _organisation_name(node: Any) -> str | None:
    """Betreibername, egal ob als Objekt oder als Verweis hinterlegt."""
    for entry in _as_list(node):
        if not isinstance(entry, dict):
            continue
        for holder in entry.values():
            if isinstance(holder, dict):
                name = _text(holder.get("name")) or _text(holder.get("legalName"))
                if name:
                    return name
    return None


def _coordinates(node: Any) -> tuple[float | None, float | None]:
    """Koordinaten aus einer locationReference, so tief sie auch liegt."""
    if not isinstance(node, dict):
        return None, None
    if "latitude" in node and "longitude" in node:
        try:
            return float(node["latitude"]), float(node["longitude"])
        except (TypeError, ValueError):
            return None, None
    for value in node.values():
        if isinstance(value, dict):
            lat, lon = _coordinates(value)
            if lat is not None:
                return lat, lon
        elif isinstance(value, list):
            for item in value:
                lat, lon = _coordinates(item)
                if lat is not None:
                    return lat, lon
    return None, None


def _max_power_kw(point: dict) -> float | None:
    """Groesste Leistung eines Ladepunkts, aus Watt in Kilowatt."""
    watts: list[float] = []
    for value in _as_list(point.get("availableChargingPower")):
        try:
            watts.append(float(value))
        except (TypeError, ValueError):
            continue
    for connector in _as_list(point.get("connector")):
        if isinstance(connector, dict) and connector.get("maxPowerAtSocket") is not None:
            try:
                watts.append(float(connector["maxPowerAtSocket"]))
            except (TypeError, ValueError):
                continue
    return max(watts) / WATT_PER_KW if watts else None


def _rate_ids_and_currencies(node: Any, currencies: dict[str, str]) -> tuple[str, ...]:
    """Sammelt die energyRate-Ids und merkt sich deren Waehrung."""
    found: list[str] = []
    for energy in _as_list(node):
        if not isinstance(energy, dict):
            continue
        for rate in _as_list(energy.get("energyRate")):
            if not isinstance(rate, dict):
                continue
            rate_id = rate.get("idG")
            if not rate_id:
                continue
            found.append(rate_id)
            waehrungen = [c for c in _as_list(rate.get("applicableCurrency")) if isinstance(c, str)]
            if waehrungen:
                currencies[rate_id] = waehrungen[0]
    return tuple(found)


# ---- Die beiden Veroeffentlichungen -----------------------------------------


def payloads(document: Any) -> list[dict]:
    """Loest die zwei Umschlaege auf, die im Profil vorkommen."""
    if not isinstance(document, dict):
        return []
    container = document.get("messageContainer")
    if isinstance(container, dict):
        return [item for item in _as_list(container.get("payload")) if isinstance(item, dict)]
    payload = document.get("payload")
    if isinstance(payload, dict):
        return [payload]
    return [item for item in _as_list(payload) if isinstance(item, dict)]


def parse_table(document: Any, into: Publication | None = None) -> Publication:
    """Standorte und Ladepunkte samt EVSE-Id."""
    result = into or Publication()
    for payload in payloads(document):
        publication = payload.get("aegiEnergyInfrastructureTablePublication")
        if not isinstance(publication, dict):
            continue
        for table in _as_list(publication.get("energyInfrastructureTable")):
            for site in _as_list(table.get("energyInfrastructureSite")):
                site_operator = _organisation_name(site.get("operator"))
                for station in _as_list(site.get("energyInfrastructureStation")):
                    _parse_station(station, site_operator, result)
    return result


def _parse_station(station: dict, site_operator: str | None, result: Publication) -> None:
    lat, lon = _coordinates(station.get("locationReference"))
    if not plausible(lat, lon):
        lat, lon = None, None
    operator = _organisation_name(station.get("operator")) or site_operator
    bnetza = _external_id(station.get("externalIdentifier"), "stationIdBNetzA")
    station_rates = _rate_ids_and_currencies(station.get("electricEnergy"), result.currencies)

    for holder in _as_list(station.get("refillPoint")):
        if not isinstance(holder, dict):
            continue
        for point in holder.values():
            if not isinstance(point, dict) or not point.get("idG"):
                continue
            point_rates = _rate_ids_and_currencies(point.get("electricEnergy"), result.currencies)
            result.points.append(
                ChargingPoint(
                    ref_id=point["idG"],
                    evse_id=_external_id(point.get("externalIdentifier"), "evseId"),
                    station_id=station.get("idG"),
                    bnetza_id=bnetza,
                    lat=lat,
                    lon=lon,
                    operator=operator,
                    power_kw=_max_power_kw(point),
                    rate_ids=point_rates or station_rates,
                )
            )


def parse_status(document: Any, into: Publication | None = None) -> Publication:
    """Belegung und Preis je Ladepunkt."""
    result = into or Publication()
    for payload in payloads(document):
        publication = payload.get("aegiEnergyInfrastructureStatusPublication")
        if not isinstance(publication, dict):
            continue
        for site in _as_list(publication.get("energyInfrastructureSiteStatus")):
            for station in _as_list(site.get("energyInfrastructureStationStatus")):
                for holder in _as_list(station.get("refillPointStatus")):
                    if not isinstance(holder, dict):
                        continue
                    for status in holder.values():
                        if isinstance(status, dict):
                            price = _parse_point_status(status, result)
                            if price:
                                result.prices[price.ref_id] = price
    return result


def _parse_point_status(status: dict, result: Publication) -> Price | None:
    reference = status.get("reference") or {}
    ref_id = reference.get("idG")
    if not ref_id:
        return None

    price_kwh: float | None = None
    components: list[tuple[str, float]] = []
    currency = DEFAULT_CURRENCY
    updated = status.get("lastUpdated")

    for update in _as_list(status.get("energyRateUpdate")):
        if not isinstance(update, dict):
            continue
        rate_reference = (update.get("energyRateReference") or {}).get("idG")
        if rate_reference and rate_reference in result.currencies:
            currency = result.currencies[rate_reference]
        updated = update.get("lastUpdated") or updated
        for entry in _as_list(update.get("energyPrice")):
            if not isinstance(entry, dict):
                continue
            art = (entry.get("priceType") or {}).get("value")
            try:
                wert = float(entry["value"])
            except (KeyError, TypeError, ValueError):
                continue
            components.append((art or "unbekannt", wert))
            if art == PRICE_PER_KWH:
                price_kwh = wert

    if price_kwh is None and not components:
        return None
    return Price(
        ref_id=ref_id,
        price_kwh=price_kwh,
        currency=currency,
        updated_at=updated,
        status=(status.get("status") or {}).get("value"),
        components=tuple(components),
    )


def read_publication(sources: Iterable[Path | str | bytes]) -> Publication:
    """Liest beliebig viele Dateien, Tabelle und Status in einem Rutsch.

    Welche Art eine Datei hat, entscheidet ihr Inhalt und nicht ihr Name: beide
    Veroeffentlichungen werden versucht, und nur die passende traegt etwas bei.
    """
    result = Publication()
    for source in sources:
        if isinstance(source, (bytes, bytearray)):
            document = json.loads(source)
        else:
            document = json.loads(Path(source).read_text(encoding="utf-8"))
        parse_table(document, result)
        parse_status(document, result)
    return result


def priced_points(publication: Publication) -> list[tuple[ChargingPoint, Price]]:
    """Nur die Ladepunkte, zu denen auch ein Preis vorliegt."""
    return [
        (point, publication.prices[point.ref_id])
        for point in publication.points
        if point.ref_id in publication.prices
    ]
