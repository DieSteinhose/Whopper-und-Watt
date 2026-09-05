package de.enbeplus.whopper.data

import de.enbeplus.whopper.model.ChargerHit
import de.enbeplus.whopper.model.Poi
import de.enbeplus.whopper.model.Spot
import de.enbeplus.whopper.model.haversineMeters

/**
 * Erkennt EnBW-Ladesaeulen anhand der ueblichen OSM-Tags.
 *
 * Achtung, bewusste Ungenauigkeit: In OSM ist der Betreiber nicht flaechendeckend
 * gepflegt, und EnBW verkauft auch Roaming an fremden Saeulen. "Nur EnBW" heisst
 * hier also "in OSM als EnBW getaggt", nicht "im EnBW-Tarif ladbar".
 */
object EnbwFilter {

    private val keys = listOf(
        "operator", "network", "brand", "owner", "name",
        "operator:short", "charging_station:operator",
    )

    private val wikidataIds = setOf("Q321820", "Q1345004")

    fun isEnbw(poi: Poi): Boolean {
        if (poi.tags.keys.any { it.startsWith("ref:EnBW", ignoreCase = true) }) return true
        if (poi.tags["operator:wikidata"] in wikidataIds) return true
        if (poi.tags["network:wikidata"] in wikidataIds) return true
        return keys.any { key ->
            poi.tags[key]?.lowercase()?.let { value ->
                value.contains("enbw") || value.contains("en bw")
            } == true
        }
    }
}

object Pairing {

    /**
     * Ordnet jeder Burger-King-Filiale die Ladesaeulen zu, die hoechstens
     * [maxGapMeters] entfernt stehen. Filialen ohne Treffer fallen raus.
     */
    fun buildSpots(
        burgers: List<Poi>,
        chargers: List<Poi>,
        maxGapMeters: Int,
        onlyEnbw: Boolean,
        origin: Pair<Double, Double>?,
    ): List<Spot> {
        val usable = if (onlyEnbw) chargers.filter(EnbwFilter::isEnbw) else chargers
        return burgers.mapNotNull { burger ->
            val hits = usable
                .map { charger ->
                    ChargerHit(
                        charger = charger,
                        gapMeters = haversineMeters(burger.lat, burger.lon, charger.lat, charger.lon),
                    )
                }
                .filter { it.gapMeters <= maxGapMeters }
                .sortedBy { it.gapMeters }
            if (hits.isEmpty()) {
                null
            } else {
                Spot(
                    burger = burger,
                    chargers = hits,
                    distanceFromMeMeters = origin?.let { (lat, lon) ->
                        haversineMeters(lat, lon, burger.lat, burger.lon)
                    },
                )
            }
        }.sortedBy { it.distanceFromMeMeters ?: it.nearest.gapMeters }
    }
}
