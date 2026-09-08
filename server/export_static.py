#!/usr/bin/env python3
"""Schreibt den Datenbestand als JSON-Dateien fuer den Betrieb ohne Server.

GitHub Pages liefert nur statische Dateien aus, dort laeuft kein Python. Also
wandert der Datenbestand in den Browser und die Suche rechnet die PWA selbst.
Enthalten sind nur Lokale mit mindestens einer Ladesaeule in Reichweite; alles
andere beantwortet die Frage dieser App nicht und waere nur Ballast.

Geschrieben wird in zwei Teilen, und das ist der Punkt:

  spots.json        die Ketten, gemessen 1651 Lokale, 445 KB gepackt
  spots-vegan.json  die uebrigen veganen Lokale, 12655 Stueck, 5,0 MB gepackt

In einer Datei waeren das 5,5 MB gepackt und knapp 38 MB entpackt, die jede
Installation herunterladen und beim Start durch JSON.parse schicken muesste,
auch wenn nur nach Burger King gesucht wird. Die Voreinstellung laedt deshalb
nur den kleinen Teil. Der grosse kommt erst, wenn jemand eine vegane Kategorie
anhakt, und liegt danach im Cache.

    python3 server/export_static.py --db server/data/whopper.sqlite --out web/data/spots.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import charger_payload, spot_payload  # noqa: E402
from schema import connect as open_database  # noqa: E402
from geo import POWER_STEPS, power_step  # noqa: E402

# Der Zusatzteil heisst wie die Hauptdatei, nur mit diesem Anhaengsel.
VEGAN_SUFFIX = "-vegan"

# Je Lokal hoechstens so viele Saeulen je Klasse mitgeben.
#
# In der Stadt liegen bei 1000 m im Schnitt 17 Saeulen an einem Lokal, und
# diese Listen sind der Loewenanteil der Datei. Ohne Verlust an Aussagekraft,
# denn die App braucht je Filterstellung nur zwei Dinge, und beide bleiben
# exakt:
#
#   "gibt es hier ueberhaupt eine passende Saeule bis X Meter?"
#   "welche ist die naechste und was steht an ihr?"
#
# Die naechste EnBW-Saeule ist die naechste bei jedem Abstand, der sie
# einschliesst, und bei kleinerem Abstand gibt es gar keine. Deshalb wird je
# Klasse gekuerzt und nicht ueber alle zusammen: sonst koennte der EnBW-Filter
# ein Lokal verlieren, an dem zwar eine EnBW-Saeule liegt, aber vier fremde
# naeher dran sind. Eine Klasse ist ein Paar aus Netz und Leistungsstufe,
# denn genau danach laesst sich in der App filtern.
#
# Die Stufen stehen in geo.POWER_STEPS. Kommt in der App ein Knopf "ab 150 kW"
# dazu, muss die Stufe dort ergaenzt werden, sonst faellt eine 150-kW-Saeule
# aus der Datei, sobald zwei langsamere naeher dran sind. Genau das ist beim
# Bauen passiert und hat 47 Lokale gekostet.
#
# Ungenau wird allein die Zeile "N Standorte in Reichweite". Deshalb reist
# chargersCapped mit, und die App schreibt dann "N+".
CHARGERS_PER_CLASS = 2


def _chargers_by_store(connection: sqlite3.Connection) -> dict[str, list[dict]]:
    chargers: dict[str, list[dict]] = {}
    query = (
        "SELECT p.store_id, p.gap_m, c.id, c.lat, c.lon, c.operator, c.network,"
        " c.power_kw, c.capacity, c.fee"
        " FROM pair p JOIN charger c ON c.id = p.charger_id"
        " WHERE c.is_car = 1"
        " ORDER BY p.store_id, p.gap_m"
    )
    for row in connection.execute(query):
        charger = charger_payload(row)
        charger["lat"] = round(charger["lat"], 6)
        charger["lon"] = round(charger["lon"], 6)
        chargers.setdefault(row["store_id"], []).append(charger)
    return chargers


def charger_class(charger: dict) -> tuple[str | None, int]:
    """Netz und Leistungsstufe. Genau danach filtert die App.

    Fuer eine Abfrage ab Stufe s braucht die App die naechste Saeule mit
    Stufe >= s. Wird je (Netz, Stufe) die naechste behalten, ist das Minimum
    ueber alle Stufen >= s genau diese Saeule. Damit stimmt die gekuerzte
    Datei fuer jede Stufe aus geo.POWER_STEPS, und nur fuer die.
    """
    return charger["network"], power_step(charger["powerKw"])


def cap_chargers(chargers: list[dict], per_class: int = CHARGERS_PER_CLASS) -> tuple[list[dict], bool]:
    """Je Klasse die naechsten [per_class] behalten, aufsteigend nach Abstand.

    Erwartet die Liste bereits nach gapM sortiert, so wie sie aus der Datenbank
    kommt. Zweiter Rueckgabewert sagt, ob etwas weggefallen ist.
    """
    if per_class <= 0:
        return chargers, False
    kept: list[dict] = []
    seen: dict[tuple[str | None, bool], int] = {}
    for charger in chargers:
        klass = charger_class(charger)
        if seen.get(klass, 0) < per_class:
            seen[klass] = seen.get(klass, 0) + 1
            kept.append(charger)
    return kept, len(kept) < len(chargers)


def _write(path: Path, payload: dict) -> tuple[int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(text)
    return len(text), len(gzip.compress(text, 9))


def export(db_path: Path, out_path: Path) -> dict:
    """Schreibt beide Teile und gibt den vollstaendigen Bestand zurueck."""
    # Ueber schema.connect, damit auch dieser Weg eine aeltere Datenbank
    # nachzieht. Genau hier ist der Workflow gescheitert: Datenbank aus dem
    # Cache, Ingest uebersprungen, "no such column: c.network".
    connection = open_database(db_path, create=False)
    chargers = _chargers_by_store(connection)

    parts: dict[str, list[dict]] = {"base": [], "vegan": []}
    for row in connection.execute("SELECT * FROM store ORDER BY id"):
        near = chargers.get(row["id"])
        if not near:
            continue  # Ein Lokal ohne Saeule beantwortet die Frage nicht.
        # Derselbe Bauplan wie im Server. Ein Feld, das nur eine der beiden
        # Betriebsarten mitliefert, faellt sonst genau dort auf die Nase, wo
        # niemand hinschaut. Hier war schon einmal ein Link nach undefined.
        kept, capped = cap_chargers(near)
        spot = spot_payload(row, kept)
        spot["lat"] = round(spot["lat"], 6)
        spot["lon"] = round(spot["lon"], 6)
        if capped:
            spot["chargersCapped"] = True
        # Die Ketten kommen in den Grundteil, alles andere in den Zusatzteil.
        # Ein Lokal steckt in genau einer Datei, doppelt geladen wird nichts.
        parts["base" if row["brand"] else "vegan"].append(spot)

    meta = {key: value for key, value in connection.execute("SELECT key, value FROM meta")}
    meta["spots"] = len(parts["base"]) + len(parts["vegan"])
    meta["baseSpots"] = len(parts["base"])
    meta["veganSpots"] = len(parts["vegan"])

    vegan_path = out_path.with_name(f"{out_path.stem}{VEGAN_SUFFIX}{out_path.suffix}")
    written = {
        out_path: {"meta": meta, "part": "base", "spots": parts["base"]},
        vegan_path: {"meta": meta, "part": "vegan", "spots": parts["vegan"]},
    }
    for path, payload in written.items():
        plain, packed = _write(path, payload)
        print(
            f"{path}: {len(payload['spots'])} Lokale mit "
            f"{sum(len(spot['chargers']) for spot in payload['spots'])} Saeulen, "
            f"{plain / 1024:.0f} KB, gepackt {packed / 1024:.0f} KB",
            flush=True,
        )

    return {"meta": meta, "spots": parts["base"] + parts["vegan"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="server/data/whopper.sqlite")
    parser.add_argument("--out", default="web/data/spots.json")
    arguments = parser.parse_args()
    export(Path(arguments.db), Path(arguments.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
