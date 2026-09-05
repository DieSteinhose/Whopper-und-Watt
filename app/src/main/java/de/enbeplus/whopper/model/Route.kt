package de.enbeplus.whopper.model

import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min

data class RoutePoint(val lat: Double, val lon: Double)

/** Wo ein Punkt an der Route liegt: Abstand, gefahrene Strecke und Fahrzeit ab Start. */
data class RouteMatch(
    val offsetMeters: Double,
    val progressMeters: Double,
    val travelSeconds: Double,
)

/**
 * Eine gefahrene Route als Polylinie, inklusive der aufsummierten Distanz je Stuetzpunkt.
 * Reicht fuer beides: Karte zeichnen und "wie weit ab vom Weg liegt dieser Burger King".
 */
class RouteGeometry(
    val points: List<RoutePoint>,
    val label: String,
    /** Fahrzeit je Streckensegment in Sekunden, wie sie OSRM als Annotation liefert. */
    segmentSeconds: List<Double>? = null,
) {
    private val cumulative: DoubleArray = DoubleArray(points.size)
    private val cumulativeSeconds: DoubleArray = DoubleArray(points.size)

    val totalMeters: Double

    /** Falsch, wenn die Fahrzeit nur aus der Strecke geschaetzt ist (etwa bei GPX). */
    val hasMeasuredDurations: Boolean = segmentSeconds != null &&
        segmentSeconds.size == points.size - 1

    init {
        require(points.size >= 2) { "Eine Route braucht mindestens zwei Punkte" }
        var sum = 0.0
        for (i in 1 until points.size) {
            sum += haversineMeters(
                points[i - 1].lat, points[i - 1].lon,
                points[i].lat, points[i].lon,
            )
            cumulative[i] = sum
        }
        totalMeters = sum

        for (i in 1 until points.size) {
            cumulativeSeconds[i] = if (hasMeasuredDurations) {
                cumulativeSeconds[i - 1] + segmentSeconds!![i - 1]
            } else {
                cumulative[i] / ASSUMED_SPEED_METERS_PER_SECOND
            }
        }
    }

    val totalSeconds: Double get() = cumulativeSeconds.last()

    /**
     * Kuerzester Abstand zur Route. Gerechnet wird in einer lokalen Meter-Ebene
     * (aequirektangulaer um den Suchpunkt), das ist ueber die Laenge eines
     * Streckensegments genau genug und deutlich billiger als Geodaesie.
     */
    fun match(lat: Double, lon: Double): RouteMatch {
        val latScale = 111_320.0
        val lonScale = 111_320.0 * cos(Math.toRadians(lat))
        val px = lon * lonScale
        val py = lat * latScale

        var bestDistance = Double.MAX_VALUE
        var bestProgress = 0.0
        var bestSeconds = 0.0

        for (i in 1 until points.size) {
            val a = points[i - 1]
            val b = points[i]
            val ax = a.lon * lonScale
            val ay = a.lat * latScale
            val bx = b.lon * lonScale
            val by = b.lat * latScale
            val dx = bx - ax
            val dy = by - ay
            val lengthSquared = dx * dx + dy * dy
            val t = if (lengthSquared == 0.0) {
                0.0
            } else {
                max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / lengthSquared))
            }
            val cx = ax + t * dx
            val cy = ay + t * dy
            val distance = Math.hypot(px - cx, py - cy)
            if (distance < bestDistance) {
                bestDistance = distance
                bestProgress = cumulative[i - 1] + t * (cumulative[i] - cumulative[i - 1])
                bestSeconds = cumulativeSeconds[i - 1] +
                    t * (cumulativeSeconds[i] - cumulativeSeconds[i - 1])
            }
        }
        return RouteMatch(
            offsetMeters = bestDistance,
            progressMeters = bestProgress,
            travelSeconds = bestSeconds,
        )
    }

    /**
     * Duennt die Route auf einen Stuetzpunkt alle [stepMeters] aus. Overpass bekommt
     * damit eine kurze Polylinie, statt tausend Punkte verarbeiten zu muessen.
     */
    fun resample(stepMeters: Double): List<RoutePoint> {
        if (points.size <= 2) return points
        val result = mutableListOf(points.first())
        var lastDistance = 0.0
        for (i in 1 until points.size - 1) {
            if (cumulative[i] - lastDistance >= stepMeters) {
                result += points[i]
                lastDistance = cumulative[i]
            }
        }
        result += points.last()
        return result
    }
}

/** Ohne Fahrzeiten aus dem Router wird mit dieser Reisegeschwindigkeit gerechnet. */
private const val ASSUMED_SPEED_METERS_PER_SECOND = 80_000.0 / 3600.0

/** Google-Encoded-Polyline, wie sie OSRM standardmaessig liefert. */
fun decodePolyline(encoded: String, precision: Int = 5): List<RoutePoint> {
    val factor = Math.pow(10.0, precision.toDouble())
    val result = mutableListOf<RoutePoint>()
    var index = 0
    var lat = 0
    var lon = 0
    while (index < encoded.length) {
        var shift = 0
        var value = 0
        var byte: Int
        do {
            byte = encoded[index++].code - 63
            value = value or ((byte and 0x1f) shl shift)
            shift += 5
        } while (byte >= 0x20 && index < encoded.length)
        lat += if (value and 1 != 0) (value shr 1).inv() else value shr 1

        shift = 0
        value = 0
        do {
            byte = encoded[index++].code - 63
            value = value or ((byte and 0x1f) shl shift)
            shift += 5
        } while (byte >= 0x20 && index < encoded.length)
        lon += if (value and 1 != 0) (value shr 1).inv() else value shr 1

        result += RoutePoint(lat / factor, lon / factor)
    }
    return result
}
