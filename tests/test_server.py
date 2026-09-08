"""Tests fuer Geometrie, Datenbankabfragen und den Datei-Import.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import app  # noqa: E402
import db as db_module  # noqa: E402
import geo  # noqa: E402
import ingest  # noqa: E402
import plan  # noqa: E402
from db import SpotDatabase  # noqa: E402

# Echte Koordinaten, damit die Zahlen ueberpruefbar bleiben.
BK_ECHTERDINGEN = (48.6919, 9.1946)
STUTTGART_HBF = (48.7838, 9.1817)
KARLSRUHE_HBF = (48.9937, 8.4017)


class GeoTest(unittest.TestCase):
    def test_haversine_matches_reality(self):
        # Stuttgart Hbf nach Karlsruhe Hbf, real rund 64 km Luftlinie.
        meters = geo.haversine_m(*STUTTGART_HBF, *KARLSRUHE_HBF)
        self.assertTrue(60_000 < meters < 68_000, meters)

    def test_box_around_is_wider_than_tall(self):
        south, west, north, east = geo.box_around(48.0, 9.0, 1000)
        self.assertAlmostEqual(north - 48.0, 1000 / 111_320, places=6)
        # Auf 48 Grad Nord sind Laengengrade kuerzer, die Box also breiter in Grad.
        self.assertGreater(east - 9.0, north - 48.0)
        self.assertAlmostEqual(48.0 - south, north - 48.0, places=9)
        self.assertAlmostEqual(9.0 - west, east - 9.0, places=9)

    def test_brand_detection(self):
        subway = geo.BRANDS["subway"]
        burger_king = geo.BRANDS["bk"]
        self.assertTrue(geo.matches_brand({"brand:wikidata": "Q244457"}, subway))
        self.assertTrue(geo.matches_brand({"amenity": "fast_food", "name": "Subway"}, subway))
        self.assertFalse(geo.matches_brand({"amenity": "fast_food", "name": "Subway"}, burger_king))
        # Ohne Wikidata-Treffer zaehlt der Name nur bei Essensschuppen: ein
        # U-Bahn-Zugang namens "Subway" ist keine Filiale.
        self.assertFalse(geo.matches_brand({"railway": "subway_entrance", "name": "Subway"}, subway))
        self.assertEqual(geo.brand_of({"brand:wikidata": "Q177054"}), burger_king)
        self.assertIsNone(geo.brand_of({"amenity": "fast_food", "name": "Nordsee"}))

    def test_kind_parsing_falls_back_to_the_default(self):
        self.assertEqual([k.key for k in geo.parse_kinds("subway")], ["subway"])
        self.assertEqual([k.key for k in geo.parse_kinds("bk,vegan_only")], ["bk", "vegan_only"])
        self.assertEqual([k.key for k in geo.parse_kinds("bk,bk")], ["bk"])
        self.assertEqual([k.key for k in geo.parse_kinds("")], list(geo.DEFAULT_KINDS))
        self.assertEqual([k.key for k in geo.parse_kinds("mcdonalds")], list(geo.DEFAULT_KINDS))

    def test_vegan_tag_reading(self):
        self.assertTrue(geo.has_vegan_options({"diet:vegan": "yes"}))
        self.assertTrue(geo.has_vegan_options({"diet:vegan": "limited"}))
        self.assertTrue(geo.has_vegan_options({"diet:vegan": "only"}))
        self.assertFalse(geo.has_vegan_options({"diet:vegan": "no"}))
        self.assertFalse(geo.has_vegan_options({"diet:vegetarian": "yes"}))
        self.assertFalse(geo.has_vegan_options({}))
        # Nur "only" heisst rein vegan. Ein rein vegetarisches Lokal mit veganen
        # Gerichten ist nicht dasselbe und faellt bewusst nicht darunter.
        self.assertTrue(geo.is_vegan_only({"diet:vegan": "only"}))
        self.assertFalse(geo.is_vegan_only({"diet:vegan": "yes"}))
        self.assertFalse(geo.is_vegan_only({"diet:vegetarian": "only", "diet:vegan": "yes"}))
        # OSM-Werte kommen auch mal mit Grossbuchstaben oder Leerzeichen.
        self.assertTrue(geo.has_vegan_options({"diet:vegan": " Yes "}))

    def test_kind_filter_only_accepts_known_keys(self):
        self.assertEqual(db_module.kind_filter([]), "0")
        self.assertEqual(db_module.kind_filter(["quatsch"]), "0")
        # Kein Schluessel aus der Anfrage landet je im SQL, nur die feste Bedingung.
        self.assertEqual(db_module.kind_filter(["'; DROP TABLE store; --", "vegan"]), "s.vegan = 1")
        self.assertEqual(db_module.kind_filter(["bk", "bk"]), "s.brand = 'bk'")

    def test_network_detection(self):
        self.assertTrue(geo.is_enbw({"operator": "EnBW mobility+"}))
        self.assertTrue(geo.is_enbw({"network": "enbw"}))
        self.assertTrue(geo.is_enbw({"ref:EnBW": "DE*ENB*1234"}))
        # Q644304, am Datenbestand geprueft: 919 Treffer. Hier stand vorher
        # Q321820, und das kam in ganz Deutschland null mal vor. Der Anker war
        # damit wirkungslos, gerettet hat es nur die Namenssuche.
        self.assertTrue(geo.is_enbw({"operator:wikidata": "Q644304"}))
        self.assertFalse(geo.is_enbw({"operator": "IONITY"}))

        self.assertEqual(geo.network_of({"operator:wikidata": "Q151954"}), "lidl")
        self.assertEqual(geo.network_of({"operator": "Lidl Stiftung GmbH & Co. KG"}), "lidl")
        self.assertEqual(geo.network_of({"operator:wikidata": "Q685967"}), "kaufland")
        self.assertEqual(geo.network_of({"brand": "Kaufland"}), "kaufland")
        self.assertIsNone(geo.network_of({"operator": "IONITY"}))
        self.assertIsNone(geo.network_of({}))

    def test_network_parsing_means_all_when_empty(self):
        """Anders als bei den Kategorien heisst leer hier: kein Filter."""
        self.assertEqual([n.key for n in geo.parse_networks("lidl,kaufland")], ["lidl", "kaufland"])
        self.assertEqual([n.key for n in geo.parse_networks("lidl,lidl")], ["lidl"])
        self.assertEqual(geo.parse_networks(""), [])
        self.assertEqual(geo.parse_networks("aral"), [])

    def test_power_parsing(self):
        self.assertEqual(geo.max_power_kw({"charging_station:output": "150 kW"}), 150)
        self.assertEqual(geo.max_power_kw({"maxpower": "50000 W"}), 50)
        self.assertEqual(geo.max_power_kw({"socket:type2:output": "22kW"}), 22)
        self.assertEqual(geo.max_power_kw({"a:output": "150 kW", "b:output": "300 kW"}), 300)
        self.assertIsNone(geo.max_power_kw({"amenity": "charging_station"}))

    def test_route_match_gives_offset_progress_and_time(self):
        points = [(48.0, 9.0), (48.0, 9.1)]
        cumulative = geo.cumulative_distances(points)
        seconds = [0.0, 600.0]
        offset, progress, travel = geo.route_match(points, cumulative, seconds, 48.001, 9.05)
        self.assertAlmostEqual(offset, 111, delta=5)
        self.assertAlmostEqual(progress, cumulative[-1] / 2, delta=50)
        self.assertAlmostEqual(travel, 300, delta=10)

    def test_decode_polyline_matches_osrm_example(self):
        points = geo.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        self.assertEqual(len(points), 3)
        self.assertAlmostEqual(points[0][0], 38.5, places=5)
        self.assertAlmostEqual(points[2][1], -126.453, places=5)

    def test_resample_keeps_both_ends(self):
        points = [(48.0 + index * 0.001, 9.0) for index in range(101)]
        thinned = geo.resample(points, 5000)
        self.assertLess(len(thinned), len(points))
        self.assertEqual(thinned[0], points[0])
        self.assertEqual(thinned[-1], points[-1])


class DatabaseTest(unittest.TestCase):
    """Baut eine kleine Datenbank mit demselben Schema wie der Ingest."""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        path = Path(cls._directory.name) / "test.sqlite"
        connection = ingest.connect(path)

        # Die Spalten vegan und vegan_only sind so gesetzt, wie der Ingest sie
        # setzen wuerde: bei den Ketten pauschal, sonst aus dem OSM-Tag.
        stores = [
            ("node/1", "bk", "fast_food", 1, 0, *BK_ECHTERDINGEN,
             "Burger King", "Echterdinger Straße", "24/7"),
            ("node/2", "bk", "fast_food", 1, 0, 48.7758, 9.1829,
             "Burger King", "Stuttgart Mitte", "Mo-Su 10:00-22:00"),
            ("node/3", "bk", "fast_food", 1, 0, 52.5200, 13.4050, "Burger King", "Berlin", None),
            # Subway direkt neben Lokal 1, an derselben Ladesaeule.
            ("node/4", "subway", "fast_food", 1, 0, 48.69195, 9.1946,
             "Subway", "Echterdingen", "Mo-Su 09:00-21:00"),
            # Rein veganes Lokal, ebenfalls an derselben Saeule.
            ("node/5", None, "restaurant", 1, 1, 48.6920, 9.1947,
             "Grünzeug", "Echterdingen", "Mo-Sa 11:00-22:00"),
            # Restaurant mit veganen Optionen, keine Kette, nicht rein vegan.
            ("node/6", None, "restaurant", 1, 0, 48.69205, 9.19455,
             "Zum Ochsen", "Echterdingen", "Mo-Su 11:00-23:00"),
        ]
        connection.executemany(
            "INSERT INTO store"
            " (id, brand, amenity, vegan, vegan_only, lat, lon, name, address, opening_hours, tags)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')",
            stores,
        )
        # Saeule 1: EnBW, 50 m neben Filiale 1. Saeule 2: fremd, 60 m neben Filiale 2.
        connection.executemany(
            "INSERT INTO charger"
            " (id, lat, lon, operator, is_enbw, network, power_kw, capacity, fee, tags)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')",
            [
                ("node/10", 48.69235, 9.1946, "EnBW", 1, "enbw", 150.0, "4", "yes"),
                ("node/11", 48.77634, 9.1829, "IONITY", 0, None, 350.0, "6", "yes"),
            ],
        )
        connection.commit()
        ingest.build_pairs(connection, 1000)
        ingest.build_index(connection)
        connection.close()
        cls.database = SpotDatabase(path)

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_radius_search_finds_only_nearby(self):
        spots = self.database.spots_near(
            *BK_ECHTERDINGEN, radius_m=25_000, gap_m=300, networks=["enbw"], kinds=["bk"]
        )
        self.assertEqual([spot["id"] for spot in spots], ["node/1"])
        self.assertEqual(spots[0]["chargers"][0]["gapM"], 50)
        self.assertEqual(spots[0]["distanceM"], 0)

    def test_network_filter_hides_foreign_operators(self):
        near_stuttgart = (48.7758, 9.1829)
        nur_enbw = self.database.spots_near(
            *near_stuttgart, radius_m=5_000, gap_m=300, networks=["enbw"], kinds=["bk"]
        )
        everyone = self.database.spots_near(
            *near_stuttgart, radius_m=5_000, gap_m=300, networks=(), kinds=["bk"]
        )
        self.assertEqual(nur_enbw, [])
        self.assertEqual([spot["id"] for spot in everyone], ["node/2"])

    def test_network_and_power_filter(self):
        """Netzauswahl und Mindestleistung, beides auf der Saeulenseite."""
        def ids(**kwargs):
            return sorted(
                spot["id"]
                for spot in self.database.spots_near(
                    *BK_ECHTERDINGEN, radius_m=25_000, gap_m=300,
                    kinds=["bk", "subway", "vegan"], **kwargs
                )
            )

        # Ohne Netzangabe zaehlen alle Netze, das ist bewusst anders als bei
        # den Kategorien: dort heisst leer "nichts".
        self.assertTrue(ids(networks=()))
        self.assertEqual(ids(networks=["enbw"]), ids(networks=["enbw", "lidl"]))
        self.assertEqual(ids(networks=["lidl"]), [])

        # Die EnBW-Saeule an Lokal 1 hat 150 kW, die IONITY bei Stuttgart 350.
        # Ab 200 kW bleibt deshalb nur noch Stuttgart uebrig.
        self.assertEqual(ids(networks=(), min_power_kw=50), ids(networks=()))
        self.assertEqual(ids(networks=(), min_power_kw=200), ["node/2"])
        self.assertEqual(ids(networks=(), min_power_kw=400), [])
        # Netz und Leistung wirken zusammen, nicht wahlweise.
        self.assertEqual(ids(networks=["enbw"], min_power_kw=200), [])

        # Eine Saeule ohne Leistungsangabe faellt bei einer Mindestleistung raus.
        connection = sqlite3.connect(self.database.path)
        connection.execute("UPDATE charger SET power_kw = NULL WHERE id = 'node/10'")
        connection.commit()
        connection.close()
        try:
            self.assertEqual(ids(networks=["enbw"], min_power_kw=1), [])
            self.assertTrue(ids(networks=["enbw"]))
        finally:
            connection = sqlite3.connect(self.database.path)
            connection.execute("UPDATE charger SET power_kw = 150.0 WHERE id = 'node/10'")
            connection.commit()
            connection.close()

    def test_gap_is_compared_on_whole_metres(self):
        """Der Abstand wird gerundet ausgeliefert, also auch gerundet gefiltert.

        Sonst stand an einer Saeule "300 m zur Saeule", und die 300-m-Suche
        warf sie trotzdem raus, weil sie in Wahrheit bei 300,39 m lag. Genau
        das trennte den Betrieb mit Server von dem ohne.
        """
        connection = sqlite3.connect(self.database.path)
        connection.execute(
            "INSERT OR REPLACE INTO pair (store_id, charger_id, gap_m)"
            " VALUES ('node/1', 'node/11', 300.4)"
        )
        connection.commit()
        connection.close()
        try:
            spot = self.database.spots_near(
                *BK_ECHTERDINGEN, radius_m=1_000, gap_m=300, networks=(), kinds=["bk"]
            )[0]
            gaps = [charger["gapM"] for charger in spot["chargers"]]
            self.assertIn(300, gaps, "eine als 300 m ausgewiesene Saeule fehlt in der 300-m-Suche")
            # Bei 299 m darf sie dagegen nicht mehr dabei sein.
            spot = self.database.spots_near(
                *BK_ECHTERDINGEN, radius_m=1_000, gap_m=299, networks=(), kinds=["bk"]
            )[0]
            self.assertNotIn(300, [charger["gapM"] for charger in spot["chargers"]])
        finally:
            connection = sqlite3.connect(self.database.path)
            connection.execute(
                "DELETE FROM pair WHERE store_id='node/1' AND charger_id='node/11'"
            )
            connection.commit()
            connection.close()

    def test_gap_filter(self):
        spots = self.database.spots_near(
            *BK_ECHTERDINGEN, radius_m=25_000, gap_m=20, networks=["enbw"], kinds=["bk"]
        )
        self.assertEqual(spots, [])

    def test_kind_selection(self):
        """Burger King ist Standard, alles andere waehlbar, alles kombinierbar."""
        def ids(kinds):
            return sorted(
                spot["id"]
                for spot in self.database.spots_near(
                    *BK_ECHTERDINGEN, radius_m=1_000, gap_m=300, networks=["enbw"], kinds=kinds
                )
            )

        self.assertEqual(ids(["bk"]), ["node/1"])
        self.assertEqual(ids(["subway"]), ["node/4"])
        self.assertEqual(ids(["bk", "subway"]), ["node/1", "node/4"])
        self.assertEqual(ids(["vegan_only"]), ["node/5"])
        # Vegane Optionen holen die Ketten mit: beide fuehren die Produkte
        # bundesweit, unabhaengig davon, was am einzelnen Laden getaggt ist.
        self.assertEqual(ids(["vegan"]), ["node/1", "node/4", "node/5", "node/6"])
        # Ueberschneidende Kategorien liefern jedes Lokal genau einmal.
        self.assertEqual(ids(["bk", "vegan"]), ["node/1", "node/4", "node/5", "node/6"])
        # Ohne Auswahl gibt es nichts, und das darf kein Fehler sein.
        self.assertEqual(ids([]), [])
        self.assertEqual(ids(["unbekannt"]), [])

    def test_static_export_has_the_same_fields_as_the_server(self):
        """Der Betrieb ohne Server muss dieselben Felder liefern wie der mit.

        Genau daran ist der OSM-Knopf gescheitert: der Server lieferte ein Feld
        osmUrl, der statische Export nicht, und im Browser stand dann
        href="undefined".
        """
        import export_static

        with tempfile.TemporaryDirectory() as directory:
            payload = export_static.export(self.database.path, Path(directory) / "spots.json")
        exported = {spot["id"]: spot for spot in payload["spots"]}

        served = self.database.spots_near(
            *BK_ECHTERDINGEN, radius_m=25_000, gap_m=1000, networks=(),
            kinds=["bk", "subway", "vegan", "vegan_only"],
        )
        self.assertTrue(served)
        for spot in served:
            counterpart = exported[spot["id"]]
            # Was erst die Abfrage ausrechnet, steht naturgemaess nicht im Export.
            per_query = {"distanceM", "routeOffsetM", "routeProgressM", "routeSeconds"}
            # Umgekehrt darf der Export Felder mitgeben, die es nur ohne Server
            # gibt. Gefaehrlich ist nur die andere Richtung, an der ist der
            # OSM-Knopf gescheitert: ein Feld, das nur der Server liefert.
            export_only = {"chargersCapped"}
            self.assertEqual(set(spot) - per_query, set(counterpart) - export_only, spot["id"])
            self.assertEqual(set(spot["chargers"][0]), set(counterpart["chargers"][0]))

    def test_charger_cap_keeps_the_nearest_of_each_class(self):
        """Der Export kuerzt je Klasse, damit der EnBW-Filter exakt bleibt."""
        import export_static

        chargers = [
            {"id": f"node/{index}", "network": "enbw" if index == 9 else None,
             "powerKw": 22.0, "gapM": index * 10}
            for index in range(10)
        ]
        kept, capped = export_static.cap_chargers(chargers, per_class=3)
        self.assertTrue(capped)
        # Drei naechste fremde plus die eine EnBW-Saeule, die am weitesten weg ist.
        self.assertEqual([item["id"] for item in kept], ["node/0", "node/1", "node/2", "node/9"])

        # Schnelle und langsame Saeulen sind eigene Klassen, sonst koennte der
        # Schnelllader-Filter ein Lokal verlieren, an dem eine schnelle Saeule
        # liegt, die nur weiter weg ist als drei langsame.
        mixed = [
            {"id": "node/a", "network": None, "powerKw": 11.0, "gapM": 10},
            {"id": "node/b", "network": None, "powerKw": 11.0, "gapM": 20},
            {"id": "node/c", "network": None, "powerKw": 11.0, "gapM": 30},
            {"id": "node/d", "network": None, "powerKw": 150.0, "gapM": 900},
        ]
        kept, capped = export_static.cap_chargers(mixed, per_class=2)
        self.assertIn("node/d", [item["id"] for item in kept])
        self.assertTrue(capped)

        # Ohne Kuerzung bleibt alles, und dann ist nichts weggefallen.
        kept, capped = export_static.cap_chargers(chargers[:2], per_class=3)
        self.assertEqual(len(kept), 2)
        self.assertFalse(capped)

    def test_capped_export_answers_like_the_full_list(self):
        """Die Kuerzung darf keine einzige Filterstellung der App veraendern.

        Geprueft wird jede Kombination aus Netzauswahl, Leistungsstufe und
        Abstand gegen die ungekuerzte Liste: gibt es einen Treffer, und welche
        ist die naechste passende Saeule.

        Der Test haengt an geo.POWER_STEPS. Genau daran ist es beim Bauen
        schiefgegangen: die Klassen trennten nur bei 50 kW, und eine 150-kW-
        Abfrage verlor deshalb 47 Lokale, weil je zwei langsamere Saeulen
        naeher dran waren.
        """
        import itertools

        import export_static

        # Ein bewusst gemeiner Standort: die schnellen Saeulen sind weiter weg
        # als die langsamen, und die Ketten-Saeulen weiter als die fremden.
        chargers = []
        for index, (network, power) in enumerate([
            (None, 11.0), (None, 22.0), (None, None), (None, 22.0),
            ("enbw", 11.0), ("enbw", 22.0), (None, 50.0), (None, 150.0),
            ("enbw", 150.0), ("lidl", 22.0), ("lidl", 300.0), ("kaufland", 11.0),
            ("kaufland", 22.0), ("kaufland", 250.0), (None, 350.0),
        ]):
            chargers.append({"id": f"node/{index}", "network": network,
                             "powerKw": power, "gapM": (index + 1) * 40})

        kept, capped = export_static.cap_chargers(chargers)
        self.assertTrue(capped, "der Testfall kuerzt gar nichts, er prueft also nichts")
        self.assertLess(len(kept), len(chargers))

        def nearest(source, networks, min_kw, gap):
            passend = [
                item for item in source
                if item["gapM"] <= gap
                and (not networks or item["network"] in networks)
                and (min_kw <= 0 or (item["powerKw"] or 0) >= min_kw)
            ]
            return passend[0]["id"] if passend else None

        keys = [None, "enbw", "lidl", "kaufland"]
        combinations = 0
        for size in range(len(keys) + 1):
            for networks in itertools.combinations([k for k in keys if k], size):
                for min_kw in geo.POWER_STEPS:
                    for gap in (100, 300, 500, 1000):
                        combinations += 1
                        self.assertEqual(
                            nearest(kept, networks, min_kw, gap),
                            nearest(chargers, networks, min_kw, gap),
                            f"networks={networks} min_kw={min_kw} gap={gap}",
                        )
        self.assertGreater(combinations, 50)

    def test_labels_and_flags_travel_with_the_spot(self):
        def one(kinds):
            return self.database.spots_near(
                *BK_ECHTERDINGEN, radius_m=1_000, gap_m=300, networks=["enbw"], kinds=kinds
            )[0]

        subway = one(["subway"])
        self.assertEqual(subway["brand"], "subway")
        self.assertEqual(subway["label"], "Subway")
        self.assertTrue(subway["vegan"])
        self.assertFalse(subway["veganOnly"])

        # Ohne Kette benennt die Art des Lokals die Karte, nicht ein leeres Feld.
        vegan = one(["vegan_only"])
        self.assertIsNone(vegan["brand"])
        self.assertEqual(vegan["label"], "Restaurant")
        self.assertEqual(vegan["name"], "Grünzeug")
        self.assertTrue(vegan["veganOnly"])

    def test_route_search_sorts_by_progress_and_skips_far_away(self):
        # Strecke Echterdingen -> Stuttgart Mitte, Berlin liegt weit daneben.
        points = [BK_ECHTERDINGEN, (48.7758, 9.1829)]
        seconds = [0.0, 900.0]
        spots = self.database.spots_along_route(
            points=points, seconds=seconds, corridor_m=2000, gap_m=300, networks=(), kinds=["bk"]
        )
        self.assertEqual([spot["id"] for spot in spots], ["node/1", "node/2"])
        self.assertLess(spots[0]["routeProgressM"], spots[1]["routeProgressM"])
        self.assertEqual(spots[0]["routeSeconds"], 0)
        self.assertAlmostEqual(spots[1]["routeSeconds"], 900, delta=5)

    def test_route_corridor_excludes_detours(self):
        points = [(48.60, 9.19), (48.66, 9.19)]  # endet vor Echterdingen
        spots = self.database.spots_along_route(
            points=points, seconds=None, corridor_m=1000, gap_m=300, networks=(), kinds=["bk"]
        )
        self.assertEqual(spots, [])


class PlanTest(unittest.TestCase):
    def test_gpx_track_wins_over_waypoint(self):
        gpx = """<gpx>
          <wpt lat="1.0" lon="2.0"><name>Ladestopp</name></wpt>
          <trk><trkseg>
            <trkpt lat="48.7758" lon="9.1829"/>
            <trkpt lon="9.2000" lat="48.8000"/>
          </trkseg></trk>
        </gpx>"""
        points = plan.parse_gpx(gpx)
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[1][0], 48.8)
        self.assertAlmostEqual(points[1][1], 9.2)

    def test_abrp_excel_yields_addresses_and_counts_map_points(self):
        sheet = (
            "<worksheet><sheetData>"
            '<row r="1"><c r="A1" t="inlineStr"><is><t>ABRP Plan</t></is></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>https://abetterrouteplanner.com/?plan_uuid=2-abc</t></is></c></row>'
            '<row r="4"><c r="A4" t="inlineStr"><is><t>Wegpunkt</t></is></c></row>'
            '<row r="5"><c r="A5" t="inlineStr"><is><t>Punkt auf der Karte</t></is></c></row>'
            '<row r="6"><c r="A6" t="inlineStr"><is><t>Bahnhofstra&#223;e 84, 46145 Oberhausen, Deutschland</t></is></c></row>'
            '<row r="7"><c r="A7" t="inlineStr"><is><t>55 Min.</t></is></c></row>'
            "</sheetData></worksheet>"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        addresses, placeholders = plan.parse_xlsx(buffer.getvalue())
        self.assertEqual(addresses, ["Bahnhofstraße 84, 46145 Oberhausen, Deutschland"])
        self.assertEqual(placeholders, 1)

    def test_address_filter(self):
        for value in ("55 Min.", "0,00 €", "104 km", "23:34", "12 kWh", "Wegpunkt", "80 %"):
            self.assertFalse(plan.is_address(value), value)
        for value in (
            "Bahnhofstraße 84, 46145 Oberhausen, Deutschland",
            "Stuttgart, Deutschland",
            "A8 Raststätte Sindelfingen",
        ):
            self.assertTrue(plan.is_address(value), value)

    def test_thin_keeps_ends_and_limit(self):
        points = [(48.0 + index * 0.01, 9.0) for index in range(100)]
        thinned = plan._thin(points, 20)
        self.assertEqual(len(thinned), 20)
        self.assertEqual(thinned[0], points[0])
        self.assertEqual(thinned[-1], points[-1])


class NetworkExposureTest(unittest.TestCase):
    """Was zaehlt, sobald der Server nicht mehr nur auf localhost lauscht."""

    def test_rate_limiter_blocks_after_limit_and_separates_clients(self):
        limiter = app.RateLimiter(limit=3, window_s=60)
        self.assertTrue(all(limiter.allow("1.2.3.4") for _ in range(3)))
        self.assertFalse(limiter.allow("1.2.3.4"))
        # Eine andere Adresse hat ihr eigenes Kontingent.
        self.assertTrue(limiter.allow("5.6.7.8"))

    def test_rate_limiter_window_expires(self):
        limiter = app.RateLimiter(limit=1, window_s=0.05)
        self.assertTrue(limiter.allow("1.2.3.4"))
        self.assertFalse(limiter.allow("1.2.3.4"))
        time.sleep(0.06)
        self.assertTrue(limiter.allow("1.2.3.4"))

    def test_only_expensive_endpoints_are_throttled(self):
        self.assertTrue("/api/geocode".startswith(app.EXPENSIVE))
        self.assertTrue("/api/route-spots".startswith(app.EXPENSIVE))
        self.assertTrue("/api/plan".startswith(app.EXPENSIVE))
        # Die eigentliche Suche kommt aus der lokalen Datenbank und bleibt frei.
        self.assertFalse("/api/spots".startswith(app.EXPENSIVE))
        self.assertFalse("/index.html".startswith(app.EXPENSIVE))

    def test_bound_to_all_interfaces_shows_real_addresses(self):
        self.assertEqual(app._reachable_urls("127.0.0.1", 8000), ["http://127.0.0.1:8000"])
        urls = app._reachable_urls("0.0.0.0", 8000)
        self.assertIn("http://127.0.0.1:8000", urls)
        self.assertTrue(all(url.endswith(":8000") for url in urls))

    def test_oversized_sheet_is_refused(self):
        """Eine kleine Zip-Datei darf sich beim Auspacken nicht aufblasen duerfen."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xl/worksheets/sheet1.xml", "A" * (plan.MAX_ENTRY_BYTES + 10))
        self.assertLess(len(buffer.getvalue()), 100_000)  # gepackt winzig
        with self.assertRaises(ValueError):
            plan.parse_xlsx(buffer.getvalue())


class IngestTest(unittest.TestCase):
    def test_grid_covers_the_bounding_box_without_gaps(self):
        cells = ingest.grid_cells("47.20,5.80,55.10,15.10", 4)
        self.assertEqual(len(cells), 16)
        # Luecken oder Ueberlappungen im Raster hiessen fehlende Ladesaeulen.
        self.assertAlmostEqual(min(cell[0] for cell in cells), 47.20)
        self.assertAlmostEqual(min(cell[1] for cell in cells), 5.80)
        self.assertAlmostEqual(max(cell[2] for cell in cells), 55.10)
        self.assertAlmostEqual(max(cell[3] for cell in cells), 15.10)
        covered = sum((cell[2] - cell[0]) * (cell[3] - cell[1]) for cell in cells)
        self.assertAlmostEqual(covered, (55.10 - 47.20) * (15.10 - 5.80), places=6)

    def test_cells_without_stores_are_skipped(self):
        cells = ingest.grid_cells("47.00,5.00,55.00,15.00", 2)
        stores = [{"lat": 48.0, "lon": 6.0}]  # nur die Zelle unten links
        wanted = ingest.cells_with_stores(cells, stores, padding_m=1000)
        self.assertEqual(wanted, [0])

        # Ein Lokal knapp jenseits der Zellgrenze zieht die Nachbarzelle mit,
        # sonst fehlten ihm die Saeulen auf der anderen Seite der Grenze.
        edge = [{"lat": 50.9999, "lon": 6.0}]
        self.assertEqual(ingest.cells_with_stores(cells, edge, padding_m=1000), [0, 2])

    def test_pairs_are_precomputed_within_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = ingest.connect(Path(directory) / "pairs.sqlite")
            connection.execute(
                "INSERT INTO store (id, brand, lat, lon, name, address, opening_hours, tags)"
                " VALUES ('node/1', 'bk', 48.0, 9.0, 'BK', NULL, NULL, '{}')"
            )
            connection.executemany(
                "INSERT INTO charger (id, lat, lon, operator, is_enbw, power_kw, capacity, fee, tags)"
                " VALUES (?, ?, ?, 'EnBW', 1, NULL, NULL, NULL, '{}')",
                [
                    ("node/near", 48.0009, 9.0),   # rund 100 m
                    ("node/far", 48.02, 9.0),      # rund 2,2 km
                ],
            )
            connection.commit()
            self.assertEqual(ingest.build_pairs(connection, 1000), 1)
            row = connection.execute("SELECT charger_id, gap_m FROM pair").fetchone()
            self.assertEqual(row["charger_id"], "node/near")
            self.assertAlmostEqual(row["gap_m"], 100, delta=5)

    def test_area_query_falls_back_to_bounding_box(self):
        """Instanzen ohne Area-Datenbank duerfen den Lauf nicht abbrechen."""
        queries = []

        def fake_overpass(query, timeout=300, attempts=4):
            queries.append(query)
            if "area.searched" in query:
                raise RuntimeError("diese Instanz kennt keine Flaechen (area)")
            return {
                "elements": [
                    {
                        "type": "node",
                        "id": 7,
                        "lat": 48.0,
                        "lon": 9.0,
                        "tags": {"brand:wikidata": "Q177054"},
                    }
                ]
            }

        original = ingest.overpass
        ingest.overpass = fake_overpass
        try:
            with tempfile.TemporaryDirectory() as directory:
                connection = ingest.connect(Path(directory) / "fallback.sqlite")
                count = ingest.load_stores(
                    connection, "47.2,5.8,55.1,15.1", "DE", None, [geo.BRANDS["bk"]]
                )
        finally:
            ingest.overpass = original

        self.assertEqual(count, 1)
        # Erst die Flaechensuche, dann dieselbe Frage per Bounding-Box, dann die
        # eine Abfrage fuer alles mit einem diet:vegan-Tag.
        self.assertEqual(len(queries), 3)
        self.assertIn("area.searched", queries[0])
        # Nach dem Ausweichen darf keine einzige Abfrage mehr auf die Flaeche
        # zeigen, sonst laufen die veganen Lokale in denselben Fehler.
        for query in queries[1:]:
            self.assertNotIn("area.searched", query)
            self.assertIn("47.2,5.8,55.1,15.1", query)
        self.assertIn('"diet:vegan"', queries[2])

    def test_vegan_query_uses_only_the_tag_index(self):
        """Eine Abfrage ueber den seltenen Tag, nicht drei ueber die Lokalarten.

        Gemessen: drei Abfragen mit amenity-Filter zusammen 383 Sekunden, diese
        eine 154. Die Lokalart filtert danach load_stores ueber FOOD_AMENITIES.
        """
        query = ingest.vegan_query("", "(47.2,5.8,55.1,15.1)")
        self.assertIn('nwr["diet:vegan"](47.2,5.8,55.1,15.1)', query)
        for amenity in geo.FOOD_AMENITIES:
            self.assertNotIn(f'"amenity"="{amenity}"', query)

    def test_non_food_with_a_vegan_tag_is_dropped(self):
        """Ein Verkaufsautomat mit diet:vegan ist kein Lokal."""
        with tempfile.TemporaryDirectory() as directory:
            payload = {
                "elements": [
                    {"type": "node", "id": 1, "lat": 48.0, "lon": 9.0,
                     "tags": {"amenity": "restaurant", "diet:vegan": "only", "name": "Grünzeug"}},
                    {"type": "node", "id": 2, "lat": 48.1, "lon": 9.1,
                     "tags": {"amenity": "vending_machine", "diet:vegan": "only"}},
                    {"type": "node", "id": 3, "lat": 48.2, "lon": 9.2,
                     "tags": {"shop": "supermarket", "diet:vegan": "only", "name": "Veganladen"}},
                ]
            }
            seed = Path(directory) / "seed.json"
            seed.write_text(json.dumps(payload))
            connection = ingest.connect(Path(directory) / "food.sqlite")
            count = ingest.load_stores(
                connection, ingest.GERMANY_BBOX, None, seed, [geo.BRANDS["bk"]]
            )
            self.assertEqual(count, 1)
            row = connection.execute("SELECT id, amenity, vegan_only FROM store").fetchone()
            self.assertEqual(row["id"], "node/1")
            self.assertEqual(row["amenity"], "restaurant")
            self.assertEqual(row["vegan_only"], 1)

    def test_burger_rows_survive_a_second_ingest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "twice.sqlite"
            payload = {
                "elements": [
                    {
                        "type": "node",
                        "id": 1,
                        "lat": 48.0,
                        "lon": 9.0,
                        "tags": {"brand:wikidata": "Q177054", "name": "Burger King"},
                    }
                ]
            }
            seed = Path(directory) / "seed.json"
            seed.write_text(json.dumps(payload))
            for _ in range(2):
                connection = ingest.connect(path)
                ingest.load_stores(connection, ingest.GERMANY_BBOX, None, seed, [geo.BRANDS["bk"]])
                connection.close()
            connection = sqlite3.connect(path)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM store").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
