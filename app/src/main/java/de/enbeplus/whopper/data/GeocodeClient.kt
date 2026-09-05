package de.enbeplus.whopper.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Ortssuche ueber Nominatim, damit man auch ohne Standortfreigabe suchen kann. */
class GeocodeClient {

    data class Place(val label: String, val lat: Double, val lon: Double)

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    suspend fun search(query: String): Place? = withContext(Dispatchers.IO) {
        val url = "https://nominatim.openstreetmap.org/search".toHttpUrl().newBuilder()
            .addQueryParameter("q", query)
            .addQueryParameter("format", "jsonv2")
            .addQueryParameter("limit", "1")
            .build()
        val request = Request.Builder()
            .url(url)
            .header("User-Agent", OverpassClient.USER_AGENT)
            .header("Accept-Language", "de")
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw IOException("Ortssuche fehlgeschlagen (HTTP ${response.code})")
            val array = JSONArray(response.body?.string().orEmpty())
            val first = array.optJSONObject(0) ?: return@use null
            val lat = first.optString("lat").toDoubleOrNull() ?: return@use null
            val lon = first.optString("lon").toDoubleOrNull() ?: return@use null
            Place(
                label = first.optString("display_name").split(",").take(2)
                    .joinToString(",").trim().ifBlank { query },
                lat = lat,
                lon = lon,
            )
        }
    }
}
