package de.enbeplus.whopper

import de.enbeplus.whopper.data.OverpassClient
import de.enbeplus.whopper.data.RouteClient
import de.enbeplus.whopper.model.RouteGeometry
import de.enbeplus.whopper.model.RoutePoint
import de.enbeplus.whopper.model.decodePolyline
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RouteTest {

    @Test
    fun `polyline wird wie bei OSRM dekodiert`() {
        val points = decodePolyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        assertEquals(3, points.size)
        assertEquals(38.5, points[0].lat, 1e-5)
        assertEquals(-120.2, points[0].lon, 1e-5)
        assertEquals(43.252, points[2].lat, 1e-5)
        assertEquals(-126.453, points[2].lon, 1e-5)
    }

    @Test
    fun `abstand zur route und strecke ab start stimmen`() {
        // Gerade Ost-West-Linie auf 48 Grad Nord, rund 7,4 km lang.
        val route = RouteGeometry(
            listOf(RoutePoint(48.0, 9.0), RoutePoint(48.0, 9.1)),
            "Test",
        )
        assertTrue("Laenge war ${route.totalMeters}", route.totalMeters in 7000.0..7900.0)

        val match = route.match(48.001, 9.05)
        // 0,001 Grad Breite sind rund 111 m.
        assertEquals(111.0, match.offsetMeters, 5.0)
        assertEquals(route.totalMeters / 2, match.progressMeters, 50.0)
    }

    @Test
    fun `punkt vor dem start wird auf den start bezogen`() {
        val route = RouteGeometry(
            listOf(RoutePoint(48.0, 9.0), RoutePoint(48.0, 9.1)),
            "Test",
        )
        val match = route.match(48.0, 8.9)
        assertEquals(0.0, match.progressMeters, 1.0)
        assertTrue(match.offsetMeters > 7000)
    }

    @Test
    fun `resample duennt lange routen aus und behaelt die enden`() {
        val points = (0..100).map { RoutePoint(48.0 + it * 0.001, 9.0) }
        val route = RouteGeometry(points, "Test")
        val sampled = route.resample(5000.0)
        assertTrue("waren ${sampled.size}", sampled.size < points.size)
        assertEquals(points.first(), sampled.first())
        assertEquals(points.last(), sampled.last())
    }

    @Test
    fun `gpx trackpunkte werden gelesen`() {
        val gpx = """
            <?xml version="1.0"?>
            <gpx version="1.1">
              <wpt lat="1.0" lon="2.0"><name>Ladestopp</name></wpt>
              <trk><trkseg>
                <trkpt lat="48.7758" lon="9.1829"><ele>245</ele></trkpt>
                <trkpt lon="9.2000" lat="48.8000"/>
                <trkpt lat="48.9000" lon="9.3000"></trkpt>
              </trkseg></trk>
            </gpx>
        """.trimIndent()
        val route = RouteClient.parseGpx(gpx, "Test")
        assertNotNull(route)
        // Trackpunkte haben Vorrang vor dem einzelnen Wegpunkt, auch bei vertauschten Attributen.
        assertEquals(3, route!!.points.size)
        assertEquals(48.8, route.points[1].lat, 1e-6)
        assertEquals(9.2, route.points[1].lon, 1e-6)
    }

    @Test
    fun `gpx ohne verwertbare punkte liefert null`() {
        assertNull(RouteClient.parseGpx("<gpx></gpx>", "Test"))
        assertNull(RouteClient.parseGpx("<gpx><trkpt lat=\"1\" lon=\"2\"/></gpx>", "Test"))
    }

    @Test
    fun `je streckenabschnitt entsteht eine box mit korridor als rand`() {
        val points = listOf(
            RoutePoint(48.0, 9.0),
            RoutePoint(48.1, 9.1),
            RoutePoint(48.2, 9.0),
        )
        val boxes = OverpassClient.routeBoxes(points, 3000)
        assertEquals(2, boxes.size)

        val first = boxes[0]
        // 3 km sind rund 0,027 Grad Breite Rand auf jeder Seite.
        assertEquals(48.0 - 0.027, first.south, 0.002)
        assertEquals(48.1 + 0.027, first.north, 0.002)
        assertTrue(first.west < 9.0 && first.east > 9.1)
        // Auch die absteigende zweite Haelfte muss abgedeckt sein.
        assertTrue(boxes[1].north > 48.2 && boxes[1].south < 48.1)
    }

    @Test
    fun `box um eine filiale ist so gross wie der maximale abstand`() {
        val box = OverpassClient.boxAround(48.0, 9.0, 1000)
        assertEquals(1000 / 111_320.0, box.north - 48.0, 1e-6)
        // Auf 48 Grad Nord sind Laengengrade kuerzer, die Box also breiter in Grad.
        assertTrue(box.east - 9.0 > box.north - 48.0)
        assertEquals("(47.99102,8.98657,48.00898,9.01343)", box.asFilter())
    }

    @Test
    fun `zu kurze routen liefern keine boxen`() {
        assertTrue(OverpassClient.routeBoxes(listOf(RoutePoint(48.0, 9.0)), 1000).isEmpty())
    }
}
