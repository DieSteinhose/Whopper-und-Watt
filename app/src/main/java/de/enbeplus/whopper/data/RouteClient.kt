package de.enbeplus.whopper.data

import de.enbeplus.whopper.model.RouteGeometry
import de.enbeplus.whopper.model.RoutePoint
import de.enbeplus.whopper.model.decodePolyline
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Routen kommen aus zwei Quellen:
 * - Start und Ziel eintippen, gerechnet wird auf dem oeffentlichen OSRM-Demoserver.
 * - GPX-Datei oeffnen, damit Plaene aus anderen Tools (z. B. der GPX-Export von
 *   A Better Routeplanner) hier weiterverwendet werden koennen.
 *
 * ABRP-Share-Links (plan_uuid) lassen sich bewusst nicht aufloesen: dafuer gibt es
 * keine offene Schnittstelle, und der interne Web-Key von ABRP gehoert nicht in eine
 * fremde App.
 */
class RouteClient {

    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .build()

    suspend fun route(
        startLat: Double,
        startLon: Double,
        destLat: Double,
        destLon: Double,
        label: String,
    ): RouteGeometry = withContext(Dispatchers.IO) {
        val url = "$OSRM_BASE/route/v1/driving/" +
            "$startLon,$startLat;$destLon,$destLat" +
            "?overview=full&geometries=polyline&alternatives=false&steps=false"
        val request = Request.Builder()
            .url(url)
            .header("User-Agent", OverpassClient.USER_AGENT)
            .build()
        client.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IOException("Routing fehlgeschlagen (HTTP ${response.code})")
            }
            val json = JSONObject(body)
            if (json.optString("code") != "Ok") {
                throw IOException("Keine Route gefunden (${json.optString("code")})")
            }
            val geometry = json.optJSONArray("routes")
                ?.optJSONObject(0)
                ?.optString("geometry")
                .orEmpty()
            val points = decodePolyline(geometry)
            if (points.size < 2) throw IOException("Route enthaelt keine Geometrie")
            RouteGeometry(points, label)
        }
    }

    companion object {
        /**
         * Der OSRM-Demoserver ist ausdruecklich fuer Entwicklung und kleine Lasten
         * gedacht. Fuer eine App mit vielen Nutzern gehoert hier ein eigener Router hin.
         */
        private const val OSRM_BASE = "https://router.project-osrm.org"

        /**
         * GPX ist simpel genug, dass die Punktextraktion ohne XML-Parser auskommt:
         * gebraucht werden nur die lat/lon-Attribute der Track-, Routen- und Wegpunkte.
         */
        fun parseGpx(gpx: String, label: String): RouteGeometry? {
            // Trackpunkte schlagen Routenpunkte, Routenpunkte schlagen Wegpunkte:
            // ein GPX kann alles drei enthalten, gemeint ist dann der Track.
            val points = listOf("trkpt", "rtept", "wpt")
                .firstNotNullOfOrNull { tag ->
                    extractPoints(gpx, tag).takeIf { it.size >= 2 }
                }
                ?: return null
            return RouteGeometry(points, label)
        }

        private fun extractPoints(gpx: String, tag: String): List<RoutePoint> {
            val tagPattern = Regex("<$tag\\b[^>]*>", RegexOption.IGNORE_CASE)
            val latPattern = Regex("\\blat\\s*=\\s*\"([-0-9.eE+]+)\"", RegexOption.IGNORE_CASE)
            val lonPattern = Regex("\\blon\\s*=\\s*\"([-0-9.eE+]+)\"", RegexOption.IGNORE_CASE)
            return tagPattern.findAll(gpx).mapNotNull { match ->
                val lat = latPattern.find(match.value)?.groupValues?.get(1)?.toDoubleOrNull()
                val lon = lonPattern.find(match.value)?.groupValues?.get(1)?.toDoubleOrNull()
                if (lat == null || lon == null) null else RoutePoint(lat, lon)
            }.toList()
        }
    }
}
