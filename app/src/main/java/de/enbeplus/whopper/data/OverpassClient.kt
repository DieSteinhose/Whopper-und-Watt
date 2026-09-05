package de.enbeplus.whopper.data

import de.enbeplus.whopper.model.Poi
import de.enbeplus.whopper.model.RouteGeometry
import de.enbeplus.whopper.model.RoutePoint
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.IOException
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min

/**
 * Holt die Rohdaten aus OpenStreetMap.
 *
 * Die Abfrageform ist nicht Geschmackssache, sondern gemessen:
 * - Umkreissuche: ein einziges around: um den Mittelpunkt, rund 10 Sekunden fuer 25 km.
 * - Route: around: entlang einer Polylinie ist viel zu teuer (25 km Route mit 3 km
 *   Korridor brauchten 44 Sekunden, eine ganze Route lief in den Timeout). Deshalb
 *   werden aus der Route Bounding-Boxen gebaut, denn die sind in Overpass indiziert.
 *   Zuerst nur die seltenen Filialen suchen, dann Saeulen in kleinen Boxen um die
 *   gefundenen Filialen. Was zu weit von der Route weg liegt, faellt danach lokal raus.
 */
class OverpassClient {

    data class Result(
        val burgers: List<Poi>,
        val chargers: List<Poi>,
    )

    data class Box(
        val south: Double,
        val west: Double,
        val north: Double,
        val east: Double,
    ) {
        fun asFilter(): String =
            "(%.5f,%.5f,%.5f,%.5f)".format(Locale.US, south, west, north, east)
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(240, TimeUnit.SECONDS)
        .build()

    /** Index des zuletzt erfolgreichen Endpunkts, damit tote Server nicht jedes Mal bremsen. */
    private var preferredEndpoint = 0

    suspend fun fetchAround(
        lat: Double,
        lon: Double,
        radiusMeters: Int,
        onProgress: (String) -> Unit = {},
    ): Result = withContext(Dispatchers.IO) {
        onProgress("Filialen und Ladesaeulen im Umkreis ...")
        val filter = "(around:$radiusMeters,${format(lat)},${format(lon)})"
        val query = "[out:json][timeout:180];\n(\n" +
            """  nwr["amenity"="charging_station"]$filter;""" + "\n" +
            burgerClauses(filter) + "\n);\nout center tags;"
        parse(post(query))
    }

    suspend fun fetchAlongRoute(
        route: RouteGeometry,
        corridorMeters: Int,
        onProgress: (String) -> Unit = {},
        onPartial: (Result) -> Unit = {},
    ): Result = withContext(Dispatchers.IO) {
        val boxes = routeBoxes(route.resample(SAMPLE_STEP_METERS), corridorMeters)
        val burgers = LinkedHashMap<String, Poi>()
        val boxBlocks = boxes.chunked(BOXES_PER_QUERY)
        boxBlocks.forEachIndexed { index, block ->
            onProgress("Filialen entlang der Route (${index + 1}/${boxBlocks.size}) ...")
            val clauses = block.joinToString("\n") { burgerClauses(it.asFilter()) }
            parse(post("[out:json][timeout:180];\n(\n$clauses\n);\nout center tags;")).burgers
                .forEach { burgers["${it.type}/${it.id}"] = it }
        }

        val found = burgers.values.toList()
        val chargers = LinkedHashMap<String, Poi>()
        val burgerBlocks = found.chunked(BURGERS_PER_QUERY)
        burgerBlocks.forEachIndexed { index, block ->
            onProgress(
                "Ladesaeulen an ${found.size} Filialen (${index + 1}/${burgerBlocks.size}) ...",
            )
            val clauses = block.joinToString("\n") { burger ->
                val box = boxAround(burger.lat, burger.lon, MAX_GAP_METERS)
                """  nwr["amenity"="charging_station"]${box.asFilter()};"""
            }
            parse(post("[out:json][timeout:180];\n(\n$clauses\n);\nout center tags;")).chargers
                .forEach { chargers["${it.type}/${it.id}"] = it }
            // Zwischenstand melden, damit die Liste waehrend der Suche schon fuellt.
            onPartial(Result(found, chargers.values.toList()))
        }
        Result(burgers = found, chargers = chargers.values.toList())
    }

    /**
     * Bewusst nur exakte Tag-Vergleiche: dafuer hat Overpass einen Index.
     * Die Regex-Variante (~"burger king",i) laeuft schon bei 25 km Umkreis in den Timeout.
     * Rund 96 Prozent der Filialen haengen an brand:wikidata, der Rest an brand oder name.
     */
    private fun burgerClauses(filter: String): String = listOf(
        """  nwr["brand:wikidata"="$BURGER_KING_WIKIDATA"]$filter;""",
        """  nwr["brand"="Burger King"]$filter;""",
        """  nwr["name"="Burger King"]$filter;""",
    ).joinToString("\n")

    private fun post(query: String): String {
        var lastError: IOException? = null
        for (offset in ENDPOINTS.indices) {
            val index = (preferredEndpoint + offset) % ENDPOINTS.size
            try {
                val body = execute(ENDPOINTS[index], query)
                preferredEndpoint = index
                return body
            } catch (e: IOException) {
                lastError = e
            }
        }
        throw lastError ?: IOException("Keine Overpass-Instanz erreichbar")
    }

    private fun execute(endpoint: String, query: String): String {
        val request = Request.Builder()
            .url(endpoint)
            .header("User-Agent", USER_AGENT)
            .post(FormBody.Builder().add("data", query).build())
            .build()
        client.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IOException(
                    when (response.code) {
                        429, 504 -> "Overpass ist ausgelastet (HTTP ${response.code}). " +
                            "Kurz warten und neu suchen."

                        else -> "Overpass antwortete mit HTTP ${response.code}"
                    },
                )
            }
            // Overpass meldet Timeouts auch mit HTTP 200 und einem remark im JSON.
            val remark = runCatching { JSONObject(body).optString("remark") }.getOrNull()
            if (!remark.isNullOrBlank()) throw IOException("Overpass: $remark")
            return body
        }
    }

    private fun parse(json: String): Result {
        val elements = JSONObject(json).optJSONArray("elements")
            ?: return Result(emptyList(), emptyList())
        val burgers = mutableListOf<Poi>()
        val chargers = mutableListOf<Poi>()
        for (i in 0 until elements.length()) {
            val element = elements.optJSONObject(i) ?: continue
            val center = element.optJSONObject("center")
            val lat = if (element.has("lat")) element.optDouble("lat") else center?.optDouble("lat")
            val lon = if (element.has("lon")) element.optDouble("lon") else center?.optDouble("lon")
            if (lat == null || lon == null || lat.isNaN() || lon.isNaN()) continue

            val tagsJson = element.optJSONObject("tags") ?: JSONObject()
            val tags = buildMap {
                val keys = tagsJson.keys()
                while (keys.hasNext()) {
                    val key = keys.next()
                    put(key, tagsJson.optString(key))
                }
            }
            val poi = Poi(
                id = element.optLong("id"),
                type = element.optString("type", "node"),
                lat = lat,
                lon = lon,
                tags = tags,
            )
            when {
                tags["amenity"] == "charging_station" -> chargers += poi
                isBurgerKing(tags) -> burgers += poi
            }
        }
        return Result(burgers = burgers, chargers = chargers)
    }

    private fun isBurgerKing(tags: Map<String, String>): Boolean {
        if (tags["brand:wikidata"] == BURGER_KING_WIKIDATA) return true
        val haystack = listOfNotNull(tags["brand"], tags["name"], tags["operator"])
            .joinToString(" ")
            .lowercase()
        return haystack.contains("burger king")
    }

    private fun format(value: Double): String = "%.5f".format(Locale.US, value)

    companion object {
        private val ENDPOINTS = listOf(
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
        )

        const val USER_AGENT = "WhopperUndWatt/1.1 (Android; OpenStreetMap-Daten via Overpass)"

        /** Wikidata-Objekt der Marke Burger King, in OSM als brand:wikidata gepflegt. */
        private const val BURGER_KING_WIKIDATA = "Q177054"

        /** Groesster in der App einstellbarer Abstand zwischen Filiale und Saeule. */
        const val MAX_GAP_METERS = 1000

        private const val SAMPLE_STEP_METERS = 5000.0
        private const val BOXES_PER_QUERY = 20
        private const val BURGERS_PER_QUERY = 20
        private const val METERS_PER_DEGREE_LAT = 111_320.0

        /** Quadrat um einen Punkt, Kantenlaenge zwei mal [meters]. */
        fun boxAround(lat: Double, lon: Double, meters: Int): Box {
            val padLat = meters / METERS_PER_DEGREE_LAT
            val padLon = meters / (METERS_PER_DEGREE_LAT * cos(Math.toRadians(lat)).coerceAtLeast(0.01))
            return Box(lat - padLat, lon - padLon, lat + padLat, lon + padLon)
        }

        /**
         * Eine Box je Streckenabschnitt, um den Korridor aufgeweitet. Boxen sind
         * groesser als der Korridor, das gleicht der lokale Abstandsfilter wieder aus.
         */
        fun routeBoxes(points: List<RoutePoint>, corridorMeters: Int): List<Box> {
            if (points.size < 2) return emptyList()
            val padLat = corridorMeters / METERS_PER_DEGREE_LAT
            return (1 until points.size).map { i ->
                val a = points[i - 1]
                val b = points[i]
                val middleLat = (a.lat + b.lat) / 2
                val padLon = corridorMeters /
                    (METERS_PER_DEGREE_LAT * cos(Math.toRadians(middleLat)).coerceAtLeast(0.01))
                Box(
                    south = min(a.lat, b.lat) - padLat,
                    west = min(a.lon, b.lon) - padLon,
                    north = max(a.lat, b.lat) + padLat,
                    east = max(a.lon, b.lon) + padLon,
                )
            }
        }
    }
}
