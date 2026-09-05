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
import de.enbeplus.whopper.data.XlsxPlanReader
import de.enbeplus.whopper.model.Poi
import de.enbeplus.whopper.model.RouteGeometry
import de.enbeplus.whopper.model.RoutePoint
import de.enbeplus.whopper.model.Spot
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.LocalDateTime

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
    /** Abfahrtszeit; null bedeutet "jetzt" und wird bei jeder Auswertung neu gelesen. */
    val departureAt: LocalDateTime? = null,
    val route: RouteGeometry? = null,
    /** Aus einem ABRP-Export uebernommene Adresse, die die UI ins Zielfeld schreibt. */
    val destinationSuggestion: String? = null,
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

    /** null setzt auf "jetzt" zurueck. */
    fun setDeparture(at: LocalDateTime?) {
        _state.update { it.copy(departureAt = at) }
        recompute()
    }

    fun setDepartureInHours(hours: Long) {
        setDeparture(LocalDateTime.now().plusHours(hours))
    }

    fun selectSpot(key: String?) {
        _state.update { it.copy(selectedSpotKey = key) }
    }

    fun dismissMessages() {
        _state.update { it.copy(error = null, hint = null) }
    }

    /** Die UI hat den Adressvorschlag aus dem Export ins Zielfeld uebernommen. */
    fun consumeDestinationSuggestion() {
        _state.update { it.copy(destinationSuggestion = null) }
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
                    waypoints = listOf(
                        RoutePoint(from.lat, from.lon),
                        RoutePoint(to.lat, to.lon),
                    ),
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

    /** Nimmt GPX-Dateien und den Excel-Export von A Better Routeplanner. */
    fun loadPlan(uri: Uri) {
        viewModelScope.launch {
            _state.update {
                it.copy(
                    loading = true,
                    error = null,
                    hint = null,
                    progress = "Datei lesen ...",
                    mode = SearchMode.ROUTE,
                )
            }
            val route = try {
                val bytes = withContext(Dispatchers.IO) {
                    getApplication<Application>().contentResolver
                        .openInputStream(uri)
                        ?.use { it.readBytes() }
                        ?: throw IllegalStateException("Datei nicht lesbar.")
                }
                // xlsx ist ein ZIP-Archiv und faengt mit PK an, GPX ist Text.
                if (bytes.size > 2 && bytes[0] == 'P'.code.toByte() && bytes[1] == 'K'.code.toByte()) {
                    routeFromExcelPlan(bytes)
                } else {
                    RouteClient.parseGpx(bytes.decodeToString(), "GPX-Route")
                        ?: throw IllegalStateException("Keine Streckenpunkte im GPX gefunden.")
                }
            } catch (e: Exception) {
                fail(e.message ?: "Die Datei konnte nicht gelesen werden.")
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

    /**
     * Baut aus dem ABRP-Excel eine Route. Der Export hat keine Koordinaten, also
     * werden die Adresstexte geocodiert und anschliessend geroutet. Wegpunkte, die
     * in ABRP auf der Karte angetippt wurden, heissen dort "Punkt auf der Karte"
     * und enthalten nichts, woraus sich ein Ort ableiten liesse.
     */
    private suspend fun routeFromExcelPlan(bytes: ByteArray): RouteGeometry {
        val plan = withContext(Dispatchers.Default) { XlsxPlanReader.read(bytes) }

        if (plan.addresses.size < 2) {
            _state.update {
                it.copy(destinationSuggestion = plan.addresses.firstOrNull())
            }
            throw IllegalStateException(explainThinPlan(plan))
        }

        val points = mutableListOf<RoutePoint>()
        val failed = mutableListOf<String>()
        plan.addresses.forEachIndexed { index, address ->
            _state.update {
                it.copy(progress = "Adresse ${index + 1}/${plan.addresses.size} suchen ...")
            }
            val place = runCatching { geocoder.search(address) }.getOrNull()
            if (place == null) failed += address else points += RoutePoint(place.lat, place.lon)
            // Nominatim erlaubt eine Anfrage pro Sekunde.
            if (index < plan.addresses.lastIndex) delay(1100)
        }
        if (points.size < 2) {
            throw IllegalStateException(
                "Von ${plan.addresses.size} Adressen im Plan war keine ausreichende Zahl " +
                    "auffindbar: ${failed.joinToString("; ")}",
            )
        }

        _state.update { it.copy(progress = "Route berechnen ...") }
        val route = router.route(points, "ABRP-Plan (${points.size} Wegpunkte)")
        if (failed.isNotEmpty() || plan.pointsWithoutAddress > 0) {
            _state.update {
                it.copy(
                    hint = buildString {
                        if (plan.pointsWithoutAddress > 0) {
                            append(
                                "${plan.pointsWithoutAddress} Wegpunkt(e) im Export sind " +
                                    "\"Punkt auf der Karte\" und enthalten keine Ortsangabe. ",
                            )
                        }
                        if (failed.isNotEmpty()) {
                            append("Nicht gefunden: ${failed.joinToString("; ")}. ")
                        }
                        append("Die Route wurde aus den uebrigen Adressen gebaut.")
                    },
                )
            }
        }
        return route
    }

    private fun explainThinPlan(plan: XlsxPlanReader.Plan): String = buildString {
        append("Der ABRP-Excel-Export enthaelt keine Koordinaten, nur Adresstexte. ")
        append("In diesem Plan hat ${plan.addresses.size} Wegpunkt eine Adresse")
        if (plan.pointsWithoutAddress > 0) {
            append(", ${plan.pointsWithoutAddress} weitere stehen als \"Punkt auf der Karte\" ")
            append("ohne jede Ortsangabe")
        }
        append(". ")
        if (plan.addresses.size == 1) {
            append("Die gefundene Adresse steht jetzt im Zielfeld, Start bitte eintippen. ")
        }
        append("Dauerhafte Loesung: in ABRP die Wegpunkte ueber die Adresssuche setzen ")
        append("statt per Klick auf die Karte, dann stehen sie auch im Export.")
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
            departure = current.departureAt ?: LocalDateTime.now(),
        )
        _state.update { it.copy(spots = spots) }
    }

    private fun looksLikeAbrpLink(text: String): Boolean =
        text.contains("abetterrouteplanner", ignoreCase = true) ||
            text.contains("plan_uuid", ignoreCase = true)

    companion object {
        const val ABRP_HINT =
            "ABRP-Links lassen sich von aussen nicht auslesen, dafuer gibt es keine offene " +
                "Schnittstelle. Stattdessen den Plan in ABRP exportieren und hier ueber " +
                "\"Plan oeffnen\" laden: GPX bringt die Strecke punktgenau mit, der " +
                "Excel-Export nur die Adressen der Wegpunkte, die dann geocodiert werden."
    }
}

fun Spot.key(): String = "${burger.type}/${burger.id}"
