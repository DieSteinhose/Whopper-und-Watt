#!/usr/bin/env python3
"""Ordnet AFIR-Ladepunkte unseren OSM-Saeulen zu und schreibt die Preise weg.

    python3 server/prices.py tabelle.json status.json
    python3 server/prices.py --report        # nur zeigen, was zugeordnet ist

Die Daten kommen von der Mobilithek, dem nationalen Zugangspunkt nach Artikel
20 AFIR. Zugang: dort registrieren, Datenangebote abonnieren, kostenfrei. Diese
Datei nimmt die heruntergeladenen Veroeffentlichungen entgegen, sie holt sie
nicht selbst; welche Abonnements jemand hat, weiss nur er.

Das eigentliche Problem ist nicht das Lesen, sondern das Zuordnen. Unsere
Saeulen sind OSM-Knoten, die AFIR-Daten haben eigene Ids, und einen gemeinsamen
Schluessel gibt es nur manchmal. Jede Verbindung merkt sich, welche Regel
gegriffen hat:

  evse      EVSE-Id stimmt ueberein. Exakt, kein Abstand noetig.
            An 2213 unserer 24558 Saeulen steht ref:EU:EVSE, also 9 Prozent.
  operator  Paar unter 25 m, dessen Betreibername passt.
  naehe     Paar unter 25 m ohne Betreibertreffer. Mit --nur-mit-betreiber aus.
  (keine)   Alles andere bleibt ohne Preis, mit Grund.

Wie gut das ist, gemessen an einem simulierten Feed aus unseren eigenen Saeulen
(60 Prozent Abdeckung, bis 15 m Koordinatenversatz, zufaellige Rechtsform am
Betreibernamen), bei dem zu jedem Punkt bekannt ist, aus welcher Saeule er kam:

  14683 Verbindungen, davon 89,5 Prozent auf die richtige Saeule
  aber 99,8 Prozent mit dem richtigen Preis

Der Unterschied ist der ganze Witz. Die 10,5 Prozent Fehlgriffe sind fast
ausnahmslos Vertauschungen innerhalb eines Ladeparks: zwei Saeulen desselben
Betreibers, sieben Meter auseinander, und die Zuordnung nimmt die falsche.
Sichtbar wird davon nichts, denn der Ad-hoc-Preis haengt am Tarif des
Betreibers, nicht am einzelnen Stecker. Uebrig bleiben 0,2 Prozent echte
Preisfehler, alle innerhalb von 18 m. --nur-mit-betreiber hebt die
Verbindungsguete auf 90,2 Prozent und kostet dafuer 16 Prozent aller Punkte;
das ist kein guter Tausch, deshalb ist es nicht die Voreinstellung.

Warum das ueberhaupt eine Tabelle braucht und nicht bei jedem Lauf neu gerechnet
wird: der Abgleich selbst kostet gemessen 1,1 Sekunden, Geschwindigkeit ist
also kein Argument. Stabilitaet ist eins. 33 Prozent unserer Saeulen haben eine
weitere im Umkreis von 25 m, im Schnitt 2,6 davon. Ohne feste Zuordnung
entscheidet bei jedem Lauf ein Meter Koordinatenunterschied, welcher Punkt
gewinnt, und mit ihm der angezeigte Preis. Deshalb gilt: eine einmal gesetzte
Verbindung wird bestaetigt, nicht neu gesucht, solange sie plausibel bleibt.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import afir  # noqa: E402
from geo import haversine_m, lat_degrees_for_meters, lon_degrees_for_meters  # noqa: E402
from schema import connect  # noqa: E402

# Wie weit ein AFIR-Ladepunkt von unserer Saeule entfernt sein darf. 25 m ist
# grosszuegig genug fuer die uebliche Ungenauigkeit beider Quellen und eng
# genug, dass nicht der Ladepark gegenueber gewinnt.
MATCH_RADIUS_M = 25.0

# Eine Verbindung, die so lange nicht mehr bestaetigt wurde, ist tot: der
# Betreiber hat die Id geaendert oder den Ladepunkt abgebaut.
STALE_AFTER_DAYS = 14

# Rechtsformen und Zusaetze, die zwei Schreibweisen desselben Betreibers
# unterscheiden, ohne etwas zu bedeuten.
_NOISE = (
    "gmbh", "ag", "kg", "se", "co", "kgaa", "eg", "e.g", "mbh", "und", "&",
    "stiftung", "energie", "energy", "mobility", "gruppe", "group", "the",
)


def normalise_operator(name: str | None) -> str:
    """Betreibername auf das reduzieren, was ihn ausmacht."""
    if not name:
        return ""
    text = name.lower().replace(".", " ").replace("-", " ").replace("+", " ")
    woerter = [wort for wort in text.split() if wort and wort not in _NOISE]
    return " ".join(woerter)


def operators_match(links: str | None, rechts: str | None) -> bool:
    """Passen zwei Betreibernamen aus verschiedenen Quellen zusammen?

    Bewusst grosszuegig: "EnBW Energie Baden-Wuerttemberg AG" und "EnBW
    mobility+" sind derselbe Betreiber, "EnBW" und "EWE Go" nicht. Ein
    gemeinsames Wort reicht, denn die Auswahl ist ohnehin schon auf 25 m
    eingegrenzt.
    """
    a, b = normalise_operator(links), normalise_operator(rechts)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return bool(set(a.split()) & set(b.split()))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- Zuordnung ---------------------------------------------------------------


class Matcher:
    """Ordnet OSM-Saeulen und AFIR-Ladepunkte einander zu, wechselseitig.

    Wechselseitig ist der Punkt. Der erste Entwurf liess jede Saeule
    unabhaengig nach dem naechsten Punkt greifen, und das ging schief: bei
    einem simulierten Feed ueber 14688 Punkte entstanden 16241 Verbindungen,
    also mindestens 1553 Saeulen, die sich den Punkt des Nachbarn geschnappt
    hatten. Gegen die Wahrheit geprueft waren 19,4 Prozent aller Verbindungen
    falsch. Ladeparks stehen eben zusammen: ein Drittel unserer Saeulen hat
    eine weitere im Umkreis von 25 m.

    Jetzt darf jeder Ladepunkt hoechstens einmal vergeben werden, und das
    naechste Paar gewinnt. Das ist ein Zuordnungsproblem, gierig geloest:
    alle in Frage kommenden Paare nach Guete sortieren, von oben durchgehen,
    nehmen was noch frei ist. Damit sind 89,5 Prozent der Verbindungen
    richtig, und weil die uebrigen Vertauschungen im selben Ladepark beim
    selben Betreiber passieren, 99,8 Prozent der Preise.

    Gierig und nicht optimal, mit Absicht. Eine echte Loesung des
    Zuordnungsproblems (Ungarische Methode) wuerde die Vertauschungen im
    Ladepark aufloesen, an denen der Preis ohnehin gleich ist, und dafuer
    kubisch teuer werden. Der gierige Weg braucht fuer 14688 Punkte gegen
    24558 Saeulen 1,1 Sekunden.
    """

    def __init__(self, points: Sequence[afir.ChargingPoint], radius_m: float = MATCH_RADIUS_M):
        self.radius_m = radius_m
        self.points = list(points)
        self.by_evse: dict[str, afir.ChargingPoint] = {}
        doppelt: set[str] = set()
        self._grid: dict[tuple[int, int], list[afir.ChargingPoint]] = {}
        self._cell = 0.0005  # rund 50 m
        for point in points:
            if point.evse_id:
                key = point.evse_id.upper()
                if key in self.by_evse:
                    doppelt.add(key)
                self.by_evse[key] = point
            if point.lat is not None and point.lon is not None:
                cell = (int(point.lat / self._cell), int(point.lon / self._cell))
                self._grid.setdefault(cell, []).append(point)
        # Eine EVSE-Id, die zweimal vorkommt, ist als Schluessel wertlos.
        for key in doppelt:
            self.by_evse.pop(key, None)

    def nearby(self, lat: float, lon: float) -> list[tuple[float, afir.ChargingPoint]]:
        base = (int(lat / self._cell), int(lon / self._cell))
        found = []
        for d_lat in (-1, 0, 1):
            for d_lon in (-1, 0, 1):
                for point in self._grid.get((base[0] + d_lat, base[1] + d_lon), ()):
                    distance = haversine_m(lat, lon, point.lat, point.lon)
                    if distance <= self.radius_m:
                        found.append((distance, point))
        found.sort(key=lambda item: item[0])
        return found

    def assign(
        self,
        chargers: Sequence[sqlite3.Row],
        reserved: Iterable[str] = (),
        allow_without_operator: bool = True,
    ) -> dict[str, tuple[afir.ChargingPoint, str, float | None, bool]]:
        """Wechselseitige Zuordnung fuer einen ganzen Schwung Saeulen.

        reserved sind Ladepunkte, die schon durch bestaetigte Verbindungen
        belegt sind und deshalb nicht neu vergeben werden duerfen.
        """
        vergeben: set[str] = set(reserved)
        ergebnis: dict[str, tuple[afir.ChargingPoint, str, float | None, bool]] = {}

        # Erste Runde: die EVSE-Id ist exakt und schlaegt jede Naehe.
        rest = []
        for charger in chargers:
            tags = json.loads(charger["tags"] or "{}")
            evse = _osm_evse(tags)
            point = self.by_evse.get(evse.upper()) if evse else None
            if point is not None and point.ref_id not in vergeben:
                vergeben.add(point.ref_id)
                ergebnis[charger["id"]] = (point, "evse", None, True)
            else:
                rest.append((charger, charger["operator"] or _osm_operator(tags)))

        # Zweite Runde: alle Paare in Reichweite, sortiert nach Betreibertreffer
        # und dann nach Abstand. Wer zuerst kommt, bekommt den Punkt.
        paare = []
        for charger, operator in rest:
            for distance, point in self.nearby(charger["lat"], charger["lon"]):
                if point.ref_id in vergeben:
                    continue
                passt = operators_match(operator, point.operator)
                if not passt and not allow_without_operator:
                    continue
                paare.append((0 if passt else 1, distance, charger["id"], point, passt))
        paare.sort(key=lambda item: (item[0], item[1]))

        for rang, distance, charger_id, point, passt in paare:
            if charger_id in ergebnis or point.ref_id in vergeben:
                continue
            vergeben.add(point.ref_id)
            ergebnis[charger_id] = (point, "operator" if passt else "naehe", distance, passt)
        return ergebnis


def _osm_evse(tags: dict) -> str | None:
    for key, value in tags.items():
        if key.lower() in ("ref:eu:evse", "ref:evse") and (value or "").strip():
            return value.strip()
    return None


def _osm_operator(tags: dict) -> str | None:
    for key in ("operator", "network", "brand", "owner"):
        value = (tags.get(key) or "").strip()
        if value:
            return value
    return None


# ---- Schreiben ----------------------------------------------------------------


def update(
    connection: sqlite3.Connection,
    publication: afir.Publication,
    source: str,
    radius_m: float = MATCH_RADIUS_M,
    allow_without_operator: bool = True,
) -> dict[str, int]:
    """Verbindungen bestaetigen oder neu zuordnen, dann Preise schreiben."""
    zeitpunkt = now()
    matcher = Matcher(publication.points, radius_m)
    von_id = {point.ref_id: point for point in publication.points}
    chargers = list(connection.execute("SELECT * FROM charger WHERE is_car = 1"))

    bestand = {
        row["charger_id"]: row
        for row in connection.execute("SELECT * FROM charger_link WHERE source = ?", (source,))
    }

    zaehler = {"bestaetigt": 0, "neu": 0, "gelost": 0, "ohne": 0}
    methoden: dict[str, int] = {}
    belegt: set[str] = set()
    offen = []

    # Bestehende Verbindungen zuerst: sie behalten ihren Ladepunkt, damit der
    # Preis nicht ueber Nacht auf einen anderen Punkt springt.
    for charger in chargers:
        vorhanden = bestand.get(charger["id"])
        if vorhanden and (vorhanden["pinned"] or _still_valid(vorhanden, von_id, radius_m)):
            connection.execute(
                "UPDATE charger_link SET last_confirmed = ? WHERE charger_id = ?",
                (zeitpunkt, charger["id"]),
            )
            belegt.add(vorhanden["external_id"])
            zaehler["bestaetigt"] += 1
            methoden[vorhanden["method"]] = methoden.get(vorhanden["method"], 0) + 1
        else:
            offen.append(charger)

    zuordnung = matcher.assign(offen, belegt, allow_without_operator)
    for charger in offen:
        treffer = zuordnung.get(charger["id"])
        if treffer is None:
            zaehler["ohne"] += 1
            continue
        point, methode, distance, operator_ok = treffer
        connection.execute(
            "INSERT OR REPLACE INTO charger_link"
            " (charger_id, external_id, source, method, distance_m, operator_ok,"
            "  matched_lat, matched_lon, first_seen, last_confirmed, pinned)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT first_seen FROM charger_link"
            "  WHERE charger_id = ?), ?), ?, 0)",
            (charger["id"], point.ref_id, source, methode, distance, 1 if operator_ok else 0,
             point.lat, point.lon, charger["id"], zeitpunkt, zeitpunkt),
        )
        zaehler["gelost" if charger["id"] in bestand else "neu"] += 1
        methoden[methode] = methoden.get(methode, 0) + 1

    connection.commit()
    zaehler["preise"] = _write_prices(connection, publication, source, zeitpunkt)
    zaehler["verwaist"] = _drop_stale(connection, source, zeitpunkt)
    zaehler.update({f"regel_{name}": anzahl for name, anzahl in methoden.items()})
    return zaehler


def _still_valid(link: sqlite3.Row, von_id: dict, radius_m: float) -> bool:
    """Zeigt die gespeicherte Verbindung noch auf etwas Plausibles?"""
    point = von_id.get(link["external_id"])
    if point is None:
        return False
    if link["method"] == "evse":
        return True
    if point.lat is None or link["matched_lat"] is None:
        return False
    return haversine_m(link["matched_lat"], link["matched_lon"], point.lat, point.lon) <= radius_m


def _write_prices(
    connection: sqlite3.Connection, publication: afir.Publication, source: str, zeitpunkt: str
) -> int:
    rows = []
    for link in connection.execute("SELECT charger_id, external_id FROM charger_link WHERE source = ?", (source,)):
        price = publication.prices.get(link["external_id"])
        if price is None:
            continue
        rows.append(
            (
                link["charger_id"],
                price.price_kwh,
                price.currency,
                json.dumps(dict(price.components), ensure_ascii=False) if price.components else None,
                price.updated_at,
                zeitpunkt,
            )
        )
    connection.executemany(
        "INSERT OR REPLACE INTO charger_price"
        " (charger_id, price_kwh, currency, components, price_updated_at, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    return len(rows)


def _drop_stale(connection: sqlite3.Connection, source: str, zeitpunkt: str) -> int:
    """Verbindungen wegwerfen, die lange nicht mehr bestaetigt wurden."""
    grenze = datetime.fromisoformat(zeitpunkt).timestamp() - STALE_AFTER_DAYS * 86400
    tot = []
    for row in connection.execute(
        "SELECT charger_id, last_confirmed FROM charger_link WHERE source = ? AND pinned = 0",
        (source,),
    ):
        try:
            if datetime.fromisoformat(row["last_confirmed"]).timestamp() < grenze:
                tot.append(row["charger_id"])
        except ValueError:
            tot.append(row["charger_id"])
    if tot:
        connection.executemany("DELETE FROM charger_link WHERE charger_id = ?", [(i,) for i in tot])
        connection.executemany("DELETE FROM charger_price WHERE charger_id = ?", [(i,) for i in tot])
        connection.commit()
    return len(tot)


def carry_over(old_path: Path, connection: sqlite3.Connection) -> dict[str, int]:
    """Zuordnungen und Preise aus der vorigen Datenbank uebernehmen.

    Der naechtliche Lauf baut nach fresh.sqlite, also in eine leere Datenbank.
    Ohne diesen Schritt waere die gespeicherte Zuordnung jeden Morgen weg, und
    damit genau die Stabilitaet, wegen der sie ueberhaupt gespeichert wird: der
    Preis spraenge jede Nacht auf eine andere Saeule im selben Ladepark.

    Uebernommen wird nur, was noch eine Saeule hat. Was aus OSM verschwunden
    ist, verschwindet auch hier.
    """
    if not Path(old_path).exists():
        return {"verbindungen": 0, "preise": 0}
    connection.execute("ATTACH DATABASE ? AS alt", (str(old_path),))
    try:
        gezaehlt = {}
        for tabelle in ("charger_link", "charger_price"):
            # Die alte Datenbank kann aelter sein als diese Tabellen.
            vorhanden = connection.execute(
                "SELECT 1 FROM alt.sqlite_master WHERE type = 'table' AND name = ?", (tabelle,)
            ).fetchone()
            if not vorhanden:
                gezaehlt[tabelle] = 0
                continue
            cursor = connection.execute(
                f"INSERT OR REPLACE INTO {tabelle} SELECT a.* FROM alt.{tabelle} a"
                f" WHERE a.charger_id IN (SELECT id FROM charger)"
            )
            gezaehlt[tabelle] = cursor.rowcount
        connection.commit()
        return {"verbindungen": gezaehlt["charger_link"], "preise": gezaehlt["charger_price"]}
    finally:
        connection.execute("DETACH DATABASE alt")


def report(connection: sqlite3.Connection) -> str:
    zeilen = ["Zuordnung je Regel:"]
    for row in connection.execute(
        "SELECT method, COUNT(*) n, AVG(distance_m) d FROM charger_link GROUP BY method ORDER BY n DESC"
    ):
        abstand = f", im Schnitt {row['d']:.1f} m" if row["d"] is not None else ""
        zeilen.append(f"  {row['method']:10s} {row['n']:6d}{abstand}")
    gesamt = connection.execute("SELECT COUNT(*) n FROM charger WHERE is_car = 1").fetchone()["n"]
    verbunden = connection.execute("SELECT COUNT(*) n FROM charger_link").fetchone()["n"]
    mit_preis = connection.execute(
        "SELECT COUNT(*) n FROM charger_price WHERE price_kwh IS NOT NULL"
    ).fetchone()["n"]
    anteil = f" ({100 * verbunden / gesamt:.1f} %)" if gesamt else ""
    zeilen.append(f"\n{verbunden} von {gesamt} Saeulen verbunden{anteil},"
                  f" davon {mit_preis} mit Preis je kWh")
    return "\n".join(zeilen)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dateien", nargs="*", type=Path, help="AFIR-Veroeffentlichungen als JSON")
    parser.add_argument("--db", default="server/data/whopper.sqlite")
    parser.add_argument("--source", default="mobilithek", help="Name des Feeds, fuer die Herkunft")
    parser.add_argument("--radius", type=float, default=MATCH_RADIUS_M)
    parser.add_argument(
        "--nur-mit-betreiber",
        action="store_true",
        help="Nur zuordnen, wenn auch der Betreibername passt. Weniger Treffer, dafuer verlaesslichere.",
    )
    parser.add_argument(
        "--von",
        type=Path,
        help="Zuordnungen und Preise aus dieser aelteren Datenbank uebernehmen,"
             " bevor der neue Stand eingerechnet wird",
    )
    parser.add_argument("--report", action="store_true", help="nur den Stand zeigen")
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    connection = connect(Path(arguments.db), create=False)
    if arguments.von:
        uebernommen = carry_over(arguments.von, connection)
        print(f"Aus {arguments.von} uebernommen: {uebernommen['verbindungen']} Verbindungen,"
              f" {uebernommen['preise']} Preise")
    if arguments.report or not arguments.dateien:
        print(report(connection))
        return 0

    publication = afir.read_publication(arguments.dateien)
    print(f"{len(publication.points)} Ladepunkte, {len(publication.prices)} Preisstaende gelesen")
    zaehler = update(connection, publication, arguments.source, arguments.radius,
                     allow_without_operator=not arguments.nur_mit_betreiber)
    for name, anzahl in zaehler.items():
        print(f"  {name:22s} {anzahl}")
    print()
    print(report(connection))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
