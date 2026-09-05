package de.enbeplus.whopper.model

import java.time.LocalDateTime
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/** Ein Punkt aus OpenStreetMap (Node, Way oder Relation, jeweils auf einen Punkt reduziert). */
data class Poi(
    val id: Long,
    val type: String,
    val lat: Double,
    val lon: Double,
    val tags: Map<String, String>,
) {
    val name: String? get() = tags["name"] ?: tags["brand"] ?: tags["operator"]

    val osmUrl: String get() = "https://www.openstreetmap.org/$type/$id"

    /** Strasse + Hausnummer, sofern in OSM hinterlegt. */
    val address: String?
        get() {
            val street = tags["addr:street"]
            val number = tags["addr:housenumber"]
            val city = tags["addr:city"]
            val line = listOfNotNull(
                listOfNotNull(street, number).joinToString(" ").ifBlank { null },
                city,
            ).joinToString(", ")
            return line.ifBlank { null }
        }
}

/** Ein Burger King mit den Ladesaeulen, die nah genug dran stehen. */
data class Spot(
    val burger: Poi,
    val chargers: List<ChargerHit>,
    val distanceFromMeMeters: Double?,
    /** Nur im Routenmodus: Luftlinie von der Route zur Filiale. */
    val routeOffsetMeters: Double? = null,
    /** Nur im Routenmodus: gefahrene Strecke ab Start bis zur Abzweigung. */
    val routeProgressMeters: Double? = null,
    /** Voraussichtliche Ankunft, aus Abfahrtszeit und Fahrzeit bis hierher. */
    val arrival: LocalDateTime? = null,
    /** Ob die Filiale zur Ankunftszeit geoeffnet hat. */
    val openState: OpenState? = null,
) {
    val isClosedOnArrival: Boolean get() = openState is OpenState.Closed

    val nearest: ChargerHit get() = chargers.first()
}

data class ChargerHit(
    val charger: Poi,
    val gapMeters: Double,
)

/** Entfernung in Metern (Haversine, fuer diese Distanzen mehr als genau genug). */
fun haversineMeters(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
    val r = 6371008.8
    val dLat = Math.toRadians(lat2 - lat1)
    val dLon = Math.toRadians(lon2 - lon1)
    val a = sin(dLat / 2) * sin(dLat / 2) +
        cos(Math.toRadians(lat1)) * cos(Math.toRadians(lat2)) * sin(dLon / 2) * sin(dLon / 2)
    return 2 * r * asin(min(1.0, sqrt(a)))
}

fun formatMeters(meters: Double): String = when {
    meters < 1000 -> "${meters.toInt()} m"
    meters < 10_000 -> String.format("%.1f km", meters / 1000).replace('.', ',')
    else -> "${(meters / 1000).toInt()} km"
}

/**
 * Liest die Ladeleistung aus den ueblichen OSM-Tags heraus.
 * Rueckgabe in kW oder null, wenn nichts Verwertbares getaggt ist.
 */
fun maxPowerKw(tags: Map<String, String>): Double? {
    val candidates = tags.entries
        .filter { (key, _) ->
            key == "charging_station:output" ||
                key == "maxpower" ||
                key.endsWith(":output")
        }
        .mapNotNull { (_, value) -> parseKw(value) }
    return candidates.maxOrNull()
}

private fun parseKw(raw: String): Double? {
    // Werte wie "150 kW", "22kW", "3.7 kW", "50000 W", "22"
    val cleaned = raw.trim().lowercase().replace(',', '.')
    val number = Regex("[0-9]+(\\.[0-9]+)?").find(cleaned)?.value?.toDoubleOrNull() ?: return null
    return when {
        cleaned.contains("kw") -> number
        cleaned.contains("mw") -> number * 1000
        cleaned.endsWith("w") -> number / 1000
        else -> number
    }.takeIf { it > 0 && it < 2000 }
}
