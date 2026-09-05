package de.enbeplus.whopper.data

import de.enbeplus.whopper.model.Poi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Holt Burger-King-Filialen und Ladesaeulen in einem einzigen Overpass-Request.
 * Overpass ist ein kostenloser Community-Dienst, deshalb: ein Request pro Suche,
 * grosszuegige Timeouts und ein aussagekraeftiger User-Agent.
 */
class OverpassClient {

    data class Result(
        val burgers: List<Poi>,
        val chargers: List<Poi>,
    )

    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    suspend fun fetch(lat: Double, lon: Double, radiusMeters: Int): Result =
        withContext(Dispatchers.IO) {
            var lastError: IOException? = null
            for (endpoint in ENDPOINTS) {
                try {
                    return@withContext parse(post(endpoint, query(lat, lon, radiusMeters)))
                } catch (e: IOException) {
                    lastError = e
                }
            }
            throw lastError ?: IOException("Keine Overpass-Instanz erreichbar")
        }

    /**
     * Bewusst nur exakte Tag-Vergleiche: Overpass hat dafuer einen Index.
     * Eine Regex-Variante (~"burger king",i) laeuft bei 25 km Umkreis in den
     * Timeout, weil sie jedes Objekt einzeln anfassen muss.
     */
    private fun query(lat: Double, lon: Double, radius: Int): String {
        val around = "around:$radius,$lat,$lon"
        return """
            [out:json][timeout:90];
            (
              nwr["amenity"="charging_station"]($around);
              nwr["brand:wikidata"="$BURGER_KING_WIKIDATA"]($around);
              nwr["brand"="Burger King"]($around);
              nwr["name"="Burger King"]($around);
            );
            out center tags;
        """.trimIndent()
    }

    private fun post(endpoint: String, query: String): String {
        val request = Request.Builder()
            .url(endpoint)
            .header("User-Agent", USER_AGENT)
            .post(FormBody.Builder().add("data", query).build())
            .build()
        client.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IOException("Overpass antwortete mit HTTP ${response.code}")
            }
            return body
        }
    }

    private fun parse(json: String): Result {
        val elements = JSONObject(json).optJSONArray("elements") ?: return Result(emptyList(), emptyList())
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
            when (tags["amenity"]) {
                "charging_station" -> chargers += poi
                else -> if (isBurgerKing(tags)) burgers += poi
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

    companion object {
        private val ENDPOINTS = listOf(
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass-api.de/api/interpreter",
            "https://overpass.osm.ch/api/interpreter",
        )

        /** Wikidata-Objekt der Marke Burger King, in OSM als brand:wikidata gepflegt. */
        private const val BURGER_KING_WIKIDATA = "Q177054"
        const val USER_AGENT = "WhopperUndWatt/1.0 (Android; OpenStreetMap-Daten via Overpass)"
    }
}
