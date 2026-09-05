package de.enbeplus.whopper

import de.enbeplus.whopper.data.EnbwFilter
import de.enbeplus.whopper.data.Pairing
import de.enbeplus.whopper.model.Poi
import de.enbeplus.whopper.model.haversineMeters
import de.enbeplus.whopper.model.maxPowerKw
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingTest {

    private fun poi(id: Long, lat: Double, lon: Double, tags: Map<String, String>) =
        Poi(id = id, type = "node", lat = lat, lon = lon, tags = tags)

    private val bkStuttgart = poi(
        1, 48.7758, 9.1829,
        mapOf("amenity" to "fast_food", "brand" to "Burger King", "name" to "Burger King"),
    )

    @Test
    fun `haversine liefert plausible Distanzen`() {
        // Stuttgart Hbf -> Karlsruhe Hbf, real rund 64 km Luftlinie.
        val meters = haversineMeters(48.7838, 9.1817, 48.9937, 8.4017)
        assertTrue("erwartet ~64 km, war $meters m", meters in 60_000.0..68_000.0)
    }

    @Test
    fun `nur Ladesaeulen im Abstandsfenster zaehlen`() {
        val near = poi(
            2, 48.7760, 9.1835,
            mapOf("amenity" to "charging_station", "operator" to "EnBW"),
        )
        val far = poi(
            3, 48.7900, 9.2000,
            mapOf("amenity" to "charging_station", "operator" to "EnBW"),
        )
        val spots = Pairing.buildSpots(
            burgers = listOf(bkStuttgart),
            chargers = listOf(near, far),
            maxGapMeters = 300,
            onlyEnbw = true,
            origin = 48.7758 to 9.1829,
        )
        assertEquals(1, spots.size)
        assertEquals(1, spots.first().chargers.size)
        assertEquals(2L, spots.first().nearest.charger.id)
        assertTrue(spots.first().nearest.gapMeters < 300)
    }

    @Test
    fun `ohne Treffer faellt die Filiale raus`() {
        val far = poi(
            4, 48.8000, 9.2000,
            mapOf("amenity" to "charging_station", "operator" to "EnBW"),
        )
        val spots = Pairing.buildSpots(
            burgers = listOf(bkStuttgart),
            chargers = listOf(far),
            maxGapMeters = 300,
            onlyEnbw = true,
            origin = null,
        )
        assertTrue(spots.isEmpty())
    }

    @Test
    fun `EnBW-Filter greift nur bei passenden Tags`() {
        val enbw = poi(5, 0.0, 0.0, mapOf("operator" to "EnBW mobility+"))
        val network = poi(6, 0.0, 0.0, mapOf("network" to "enbw"))
        val ref = poi(7, 0.0, 0.0, mapOf("ref:EnBW" to "DE*ENB*1234"))
        val ionity = poi(8, 0.0, 0.0, mapOf("operator" to "IONITY"))
        assertTrue(EnbwFilter.isEnbw(enbw))
        assertTrue(EnbwFilter.isEnbw(network))
        assertTrue(EnbwFilter.isEnbw(ref))
        assertFalse(EnbwFilter.isEnbw(ionity))
    }

    @Test
    fun `Ladeleistung wird aus den ueblichen Tags gelesen`() {
        assertEquals(150.0, maxPowerKw(mapOf("charging_station:output" to "150 kW"))!!, 0.01)
        assertEquals(22.0, maxPowerKw(mapOf("socket:type2:output" to "22kW"))!!, 0.01)
        assertEquals(3.7, maxPowerKw(mapOf("socket:schuko:output" to "3.7 kW"))!!, 0.01)
        assertEquals(50.0, maxPowerKw(mapOf("maxpower" to "50000 W"))!!, 0.01)
        assertEquals(300.0, maxPowerKw(mapOf("a:output" to "150 kW", "b:output" to "300 kW"))!!, 0.01)
        assertEquals(null, maxPowerKw(mapOf("amenity" to "charging_station")))
    }
}
