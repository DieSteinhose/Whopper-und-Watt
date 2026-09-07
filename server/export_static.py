#!/usr/bin/env python3
"""Schreibt den Datenbestand als eine JSON-Datei fuer den Betrieb ohne Server.

GitHub Pages liefert nur statische Dateien aus, dort laeuft kein Python. Der
Datenbestand ist aber klein genug, um ihn komplett in den Browser zu legen:
rund tausend Filialen mit ihren Ladesaeulen. Die Suche rechnet die PWA dann
selbst, und zwar auch offline.

Enthalten sind nur Filialen mit mindestens einer Ladesaeule in Reichweite.
Alles andere beantwortet die Frage dieser App nicht und waere nur Ballast.

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

from geo import BRANDS  # noqa: E402


def export(db_path: Path, out_path: Path) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row

    chargers: dict[str, list[dict]] = {}
    query = (
        "SELECT p.store_id, p.gap_m, c.id, c.lat, c.lon, c.operator, c.is_enbw,"
        " c.power_kw, c.capacity, c.fee"
        " FROM pair p JOIN charger c ON c.id = p.charger_id"
        " ORDER BY p.store_id, p.gap_m"
    )
    for row in connection.execute(query):
        chargers.setdefault(row["store_id"], []).append(
            {
                "id": row["id"],
                "lat": round(row["lat"], 6),
                "lon": round(row["lon"], 6),
                "operator": row["operator"],
                "isEnbw": bool(row["is_enbw"]),
                "powerKw": row["power_kw"],
                "capacity": row["capacity"],
                "fee": row["fee"],
                "gapM": round(row["gap_m"]),
            }
        )

    spots = []
    for row in connection.execute("SELECT * FROM store ORDER BY id"):
        near = chargers.get(row["id"])
        if not near:
            continue  # Filiale ohne Saeule beantwortet die Frage nicht.
        brand = BRANDS.get(row["brand"])
        spots.append(
            {
                "id": row["id"],
                "brand": row["brand"],
                "brandLabel": brand.label if brand else row["brand"],
                "lat": round(row["lat"], 6),
                "lon": round(row["lon"], 6),
                "name": row["name"] or (brand.label if brand else "Filiale"),
                "address": row["address"],
                "openingHours": row["opening_hours"],
                "chargers": near,
            }
        )

    meta = {key: value for key, value in connection.execute("SELECT key, value FROM meta")}
    meta["spots"] = len(spots)
    payload = {"meta": meta, "spots": spots}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    out_path.write_text(text, encoding="utf-8")

    plain = len(text.encode("utf-8"))
    packed = len(gzip.compress(text.encode("utf-8"), 9))
    print(
        f"{out_path}: {len(spots)} Filialen mit "
        f"{sum(len(spot['chargers']) for spot in spots)} Saeulen, "
        f"{plain / 1024:.0f} KB, gepackt {packed / 1024:.0f} KB",
        flush=True,
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="server/data/whopper.sqlite")
    parser.add_argument("--out", default="web/data/spots.json")
    arguments = parser.parse_args()
    export(Path(arguments.db), Path(arguments.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
