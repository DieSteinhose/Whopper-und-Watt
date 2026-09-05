package de.enbeplus.whopper

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import de.enbeplus.whopper.data.GeocodeClient
import de.enbeplus.whopper.data.LocationProvider
import de.enbeplus.whopper.data.OverpassClient
import de.enbeplus.whopper.data.Pairing
import de.enbeplus.whopper.model.Poi
import de.enbeplus.whopper.model.Spot
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class UiState(
    val loading: Boolean = false,
    val error: String? = null,
    val spots: List<Spot> = emptyList(),
    val originLat: Double? = null,
    val originLon: Double? = null,
    val originLabel: String? = null,
    val radiusKm: Int = 25,
    val maxGapMeters: Int = 300,
    val onlyEnbw: Boolean = true,
    val searchedOnce: Boolean = false,
    val chargersFound: Int = 0,
    val burgersFound: Int = 0,
    val selectedSpotKey: String? = null,
)

class MainViewModel(app: Application) : AndroidViewModel(app) {

    private val overpass = OverpassClient()
    private val geocoder = GeocodeClient()

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    /** Rohdaten der letzten Abfrage, damit Filteraenderungen keinen neuen Request brauchen. */
    private var lastBurgers: List<Poi> = emptyList()
    private var lastChargers: List<Poi> = emptyList()

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

    fun selectSpot(key: String?) {
        _state.update { it.copy(selectedSpotKey = key) }
    }

    fun searchAroundMe() {
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            val location = LocationProvider.current(getApplication())
            if (location == null) {
                _state.update {
                    it.copy(
                        loading = false,
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
                )
            }
            runSearch(location.latitude, location.longitude)
        }
    }

    fun searchPlace(query: String) {
        if (query.isBlank()) return
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            val place = try {
                geocoder.search(query)
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, error = e.message ?: "Ortssuche fehlgeschlagen") }
                return@launch
            }
            if (place == null) {
                _state.update { it.copy(loading = false, error = "Ort \"$query\" nicht gefunden.") }
                return@launch
            }
            _state.update {
                it.copy(originLat = place.lat, originLon = place.lon, originLabel = place.label)
            }
            runSearch(place.lat, place.lon)
        }
    }

    fun retry() {
        val lat = _state.value.originLat
        val lon = _state.value.originLon
        if (lat != null && lon != null) {
            viewModelScope.launch {
                _state.update { it.copy(loading = true, error = null) }
                runSearch(lat, lon)
            }
        } else {
            searchAroundMe()
        }
    }

    private suspend fun runSearch(lat: Double, lon: Double) {
        val radiusMeters = _state.value.radiusKm * 1000
        try {
            val result = overpass.fetch(lat, lon, radiusMeters)
            lastBurgers = result.burgers
            lastChargers = result.chargers
            _state.update {
                it.copy(
                    loading = false,
                    error = null,
                    searchedOnce = true,
                    burgersFound = result.burgers.size,
                    chargersFound = result.chargers.size,
                )
            }
            recompute()
        } catch (e: Exception) {
            _state.update {
                it.copy(
                    loading = false,
                    error = e.message ?: "Abfrage fehlgeschlagen. Overpass ist gerade nicht erreichbar.",
                )
            }
        }
    }

    /**
     * Bei 100 km Umkreis kommen schnell fuenfstellige Ladesaeulenzahlen zusammen,
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
        )
        _state.update { it.copy(spots = spots) }
    }
}

fun Spot.key(): String = "${burger.type}/${burger.id}"
