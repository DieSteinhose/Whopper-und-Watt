package de.enbeplus.whopper

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import de.enbeplus.whopper.data.GeocodeClient
import de.enbeplus.whopper.data.LocationProvider
import de.enbeplus.whopper.data.OverpassClient
import de.enbeplus.whopper.data.Pairing
import de.enbeplus.whopper.data.RouteClient
import de.enbeplus.whopper.model.Poi
import de.enbeplus.whopper.model.RouteGeometry
import de.enbeplus.whopper.model.Spot
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class SearchMode { RADIUS, ROUTE }

data class UiState(
    val mode: SearchMode = SearchMode.RADIUS,
    val loading: Boolean = false,
    val progress: String? = null,
    val error: String? = null,
    val hint: String? = null,
    val spots: List<Spot> = emptyList(),
    val originLat: Double? = null,
    val originLon: Double? = null,
    val originLabel: String? = null,
    val radiusKm: Int = 25,
    val maxGapMeters: Int = 300,
    val onlyEnbw: Boolean = true,
    val corridorMeters: Int = 3000,
    val route: RouteGeometry? = null,
    val searchedOnce: Boolean = false,
    val chargersFound: Int = 0,
    val burgersFound: Int = 0,
    val selectedSpotKey: String? = null,
)

class MainViewModel(app: Application) : AndroidViewModel(app) {

    private val overpass = OverpassClient()
    private val geocoder = GeocodeClient()
    private val router = RouteClient()

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    /** Rohdaten der letzten Abfrage, damit Filteraenderungen keinen neuen Request brauchen. */
    private var lastBurgers: List<Poi> = emptyList()
    private var lastChargers: List<Poi> = emptyList()

    /** Mit welchem Korridor die Rohdaten geholt wurden. Enger filtern geht lokal. */
    private var fetchedCorridorMeters = 0

    fun setMode(mode: SearchMode) {
        _state.update { it.copy(mode = mode, error = null, hint = null) }
    }

    fun setRadius(km: Int) {
        _state.update { it.copy(radiusKm = km) }
    }

    fun setMaxGap(meters: Int) {
        _state.update { it.copy(maxGapMeters = meters) }
        recompute()
    }

    fun setOnlyEnbw(value: Boolean) {
        _state.update { it.copy(onlyEnbw = value) }
        recompute()
    }

    fun setCorridor(meters: Int) {
        _state.update { it.copy(corridorMeters = meters) }
        val route = _state.value.route
        when {
            route == null -> Unit
            meters <= fetchedCorridorMeters -> recompute()
            else -> searchAlongRoute(route)
        }
    }

    fun selectSpot(key: String?) {
        _state.update { it.copy(selectedSpotKey = key) }
    }

    fun dismissMessages() {
        _state.update { it.copy(error = null, hint = null) }
    }

    // ---- Umkreissuche -------------------------------------------------------

    fun searchAroundMe() {
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null, hint = null) }
            val location = LocationProvider.current(getApplication())
            if (location == null) {
                _state.update {
                    it.copy(
                        loading = false,
                        progress = null,
                        error = "Kein Standort verfuegbar. Standortfreigabe pruefen oder oben einen Ort eingeben.",
                    )
                }
                return@launch
            }
            _state.update {
                it.copy(
                    originLat = location.latitude,
                    originLon = location.longitude,
                    originLabel = "Mein Standort",
                    route = null,
                )
            }
            runRadiusSearch(location.latitude, location.longitude)
        }
    }

    fun searchPlace(query: String) {
        if (query.isBlank()) return
        if (looksLikeAbrpLink(query)) {
            _state.update { it.copy(hint = ABRP_HINT) }
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null, hint = null, progress = "Ort suchen ...") }
            val place = try {
                geocoder.search(query)
            } catch (e: Exception) {
                fail(e.message ?: "Ortssuche fehlgeschlagen")
                return@launch
            }
            if (place == null) {
                fail("Ort \"$query\" nicht gefunden.")
                return@launch
            }
            _state.update {
                it.copy(
                    originLat = place.lat,
                    originLon = place.lon,
                    originLabel = place.label,
                    route = null,
                )
            }
            runRadiusSearch(place.lat, place.lon)
        }
    }

    private suspend fun runRadiusSearch(lat: Double, lon: Double) {
        val radiusMeters = _state.value.radiusKm * 1000
        try {
            val result = overpass.fetchAround(lat, lon, radiusMeters) { progress ->
                _state.update { it.copy(progress = progress) }
            }
            applyResult(result)
        } catch (e: Exception) {
            fail(e.message ?: "Abfrage fehlgeschlagen. Overpass ist gerade nicht erreichbar.")
        }
    }

    // ---- Routensuche --------------------------------------------------------

    fun searchRoute(start: String, destination: String) {
        if (looksLikeAbrpLink(start) || looksLikeAbrpLink(destination)) {
            _state.update { it.copy(hint = ABRP_HINT) }
            return
        }
        if (destination.isBlank()) {
            _state.update { it.copy(error = "Ziel fehlt.") }
            return
        }
        viewModelScope.launch {
            _state.update {
                it.copy(loading = true, error = null, hint = null, progress = "Start und Ziel suchen ...")
            }
            try {
                val from = if (start.isBlank()) {
                    val location = LocationProvider.current(getApplication())
                        ?: throw IllegalStateException(
                            "Kein Standort verfuegbar. Start bitte eintippen.",
                        )
                    GeocodeClient.Place("Mein Standort", location.latitude, location.longitude)
                } else {
                    geocoder.search(start) ?: throw IllegalStateException("Start \"$start\" nicht gefunden.")
                }
                val to = geocoder.search(destination)
                    ?: throw IllegalStateException("Ziel \"$destination\" nicht gefunden.")

                _state.update { it.copy(progress = "Route berechnen ...") }
                val route = router.route(
                    startLat = from.lat,
                    startLon = from.lon,
                    destLat = to.lat,
                    destLon = to.lon,
                    label = "${from.label} nach ${to.label}",
                )
                _state.update {
                    it.copy(
                        route = route,
                        originLat = from.lat,
                        originLon = from.lon,
                        originLabel = route.label,
                    )
                }
                runRouteSearch(route)
            } catch (e: Exception) {
                fail(e.message ?: "Route konnte nicht berechnet werden.")
            }
        }
    }

    fun loadGpx(uri: Uri) {
        viewModelScope.launch {
            _state.update {
                it.copy(loading = true, error = null, hint = null, progress = "GPX lesen ...", mode = SearchMode.ROUTE)
            }
            val route = try {
                withContext(Dispatchers.IO) {
                    val text = getApplication<Application>().contentResolver
                        .openInputStream(uri)
                        ?.bufferedReader()
                        ?.use { it.readText() }
                        ?: throw IllegalStateException("Datei nicht lesbar.")
                    RouteClient.parseGpx(text, "GPX-Route")
                        ?: throw IllegalStateException("Keine Streckenpunkte im GPX gefunden.")
                }
            } catch (e: Exception) {
                fail(e.message ?: "GPX konnte nicht gelesen werden.")
                return@launch
            }
            _state.update {
                it.copy(
                    route = route,
                    originLat = route.points.first().lat,
                    originLon = route.points.first().lon,
                    originLabel = route.label,
                )
            }
            runRouteSearch(route)
        }
    }

    private fun searchAlongRoute(route: RouteGeometry) {
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null, hint = null) }
            runRouteSearch(route)
        }
    }

    private suspend fun runRouteSearch(route: RouteGeometry) {
        val corridor = _state.value.corridorMeters
        try {
            val result = overpass.fetchAlongRoute(
                route = route,
                corridorMeters = corridor,
                onProgress = { progress -> _state.update { it.copy(progress = progress) } },
                // Zwischenstaende sofort anzeigen: bei langen Routen dauert die
                // Gesamtabfrage Minuten, die ersten Treffer stehen aber viel frueher fest.
                onPartial = { partial ->
                    lastBurgers = partial.burgers
                    lastChargers = partial.chargers
                    _state.update {
                        it.copy(
                            searchedOnce = true,
                            burgersFound = partial.burgers.size,
                            chargersFound = partial.chargers.size,
                        )
                    }
                    recompute()
                },
            )
            fetchedCorridorMeters = corridor
            applyResult(result)
        } catch (e: Exception) {
            fail(e.message ?: "Abfrage fehlgeschlagen. Overpass ist gerade nicht erreichbar.")
        }
    }

    // ---- Gemeinsames --------------------------------------------------------

    fun retry() {
        val current = _state.value
        val route = current.route
        when {
            current.mode == SearchMode.ROUTE && route != null -> searchAlongRoute(route)
            current.originLat != null && current.originLon != null -> viewModelScope.launch {
                _state.update { it.copy(loading = true, error = null) }
                runRadiusSearch(current.originLat, current.originLon)
            }

            else -> searchAroundMe()
        }
    }

    private fun applyResult(result: OverpassClient.Result) {
        lastBurgers = result.burgers
        lastChargers = result.chargers
        _state.update {
            it.copy(
                loading = false,
                progress = null,
                error = null,
                searchedOnce = true,
                burgersFound = result.burgers.size,
                chargersFound = result.chargers.size,
            )
        }
        recompute()
    }

    private fun fail(message: String) {
        _state.update { it.copy(loading = false, progress = null, error = message) }
    }

    /**
     * Bei langen Routen kommen einige hundert Filialen und Saeulen zusammen,
     * deshalb laeuft das Paaren nicht auf dem Main-Thread.
     */
    private fun recompute() {
        viewModelScope.launch { recomputeNow() }
    }

    private suspend fun recomputeNow() = withContext(Dispatchers.Default) {
        val current = _state.value
        val spots = Pairing.buildSpots(
            burgers = lastBurgers,
            chargers = lastChargers,
            maxGapMeters = current.maxGapMeters,
            onlyEnbw = current.onlyEnbw,
            origin = if (current.originLat != null && current.originLon != null) {
                current.originLat to current.originLon
            } else {
                null
            },
            route = current.route.takeIf { current.mode == SearchMode.ROUTE },
            maxRouteOffsetMeters = current.corridorMeters,
        )
        _state.update { it.copy(spots = spots) }
    }

    private fun looksLikeAbrpLink(text: String): Boolean =
        text.contains("abetterrouteplanner", ignoreCase = true) ||
            text.contains("plan_uuid", ignoreCase = true)

    companion object {
        const val ABRP_HINT =
            "ABRP-Links lassen sich von aussen nicht auslesen, dafuer gibt es keine offene " +
                "Schnittstelle. In ABRP den Plan als GPX exportieren und hier ueber " +
                "\"GPX oeffnen\" laden, dann wird genau diese Route durchsucht."
    }
}

fun Spot.key(): String = "${burger.type}/${burger.id}"
