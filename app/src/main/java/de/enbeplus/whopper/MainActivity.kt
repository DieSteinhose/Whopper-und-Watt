package de.enbeplus.whopper

import android.Manifest
import android.app.TimePickerDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import de.enbeplus.whopper.data.LocationProvider
import de.enbeplus.whopper.data.OverpassClient
import de.enbeplus.whopper.ui.MapScreen
import de.enbeplus.whopper.ui.MessageBanner
import de.enbeplus.whopper.ui.SpotList
import de.enbeplus.whopper.ui.WhopperTheme
import de.enbeplus.whopper.ui.formatMoment
import org.osmdroid.config.Configuration
import java.time.LocalDateTime

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // osmdroid verlangt einen eigenen User-Agent, sonst blockt der Tile-Server.
        Configuration.getInstance().apply {
            load(applicationContext, getSharedPreferences("osmdroid", MODE_PRIVATE))
            userAgentValue = OverpassClient.USER_AGENT
        }
        enableEdgeToEdge()
        setContent {
            WhopperTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    AppScreen()
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalComposeUiApi::class)
@Composable
private fun AppScreen(viewModel: MainViewModel = viewModel()) {
    val state by viewModel.state.collectAsState()
    val context = LocalContext.current
    val keyboard = LocalSoftwareKeyboardController.current

    var tab by remember { mutableIntStateOf(0) }
    var place by remember { mutableStateOf("") }
    var routeStart by remember { mutableStateOf("") }
    var routeDestination by remember { mutableStateOf("") }
    var showOptions by remember { mutableStateOf(true) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { granted ->
        if (granted.values.any { it }) {
            viewModel.searchAroundMe()
        } else {
            Toast.makeText(
                context,
                "Ohne Standort: einfach oben einen Ort eintippen.",
                Toast.LENGTH_LONG,
            ).show()
        }
    }

    val planLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri: Uri? -> uri?.let(viewModel::loadPlan) }

    // Bringt ein ABRP-Export nur eine brauchbare Adresse mit, landet sie im Zielfeld.
    LaunchedEffect(state.destinationSuggestion) {
        state.destinationSuggestion?.let { suggestion ->
            routeDestination = suggestion
            viewModel.consumeDestinationSuggestion()
        }
    }

    fun requestLocationSearch() {
        if (LocationProvider.hasPermission(context)) {
            viewModel.searchAroundMe()
        } else {
            permissionLauncher.launch(
                arrayOf(
                    Manifest.permission.ACCESS_COARSE_LOCATION,
                    Manifest.permission.ACCESS_FINE_LOCATION,
                ),
            )
        }
    }

    fun startSearch() {
        keyboard?.hide()
        // Auf der Karte nimmt der eingeklappte Kopfbereich weniger Platz weg.
        if (tab == 1) showOptions = false
        when (state.mode) {
            SearchMode.ROUTE -> viewModel.searchRoute(routeStart, routeDestination)
            SearchMode.RADIUS -> if (place.isNotBlank()) {
                viewModel.searchPlace(place)
            } else {
                requestLocationSearch()
            }
        }
    }

    // Uhrzeit waehlen ueber den Systemdialog: liegt sie vor jetzt, ist morgen gemeint.
    fun pickDepartureTime() {
        val now = LocalDateTime.now()
        TimePickerDialog(
            context,
            { _, hour, minute ->
                val chosen = now.withHour(hour).withMinute(minute).withSecond(0).withNano(0)
                viewModel.setDeparture(if (chosen.isBefore(now)) chosen.plusDays(1) else chosen)
            },
            now.hour,
            now.minute,
            true,
        ).show()
    }

    fun navigateTo(lat: Double, lon: Double, label: String) {
        val uri = Uri.parse("geo:$lat,$lon?q=$lat,$lon(${Uri.encode(label)})")
        try {
            context.startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (_: ActivityNotFoundException) {
            Toast.makeText(context, "Keine Karten-App gefunden.", Toast.LENGTH_SHORT).show()
        }
    }

    LaunchedEffect(state.selectedSpotKey) {
        if (state.selectedSpotKey != null) tab = 1
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Whopper & Watt")
                        Text(
                            text = when {
                                state.loading -> state.progress ?: "Suche laeuft ..."
                                state.searchedOnce ->
                                    "${state.spots.size} Kombis · ${state.originLabel.orEmpty()}"

                                else -> "Burger King neben der Ladesaeule"
                            },
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            SearchControls(
                state = state,
                place = place,
                routeStart = routeStart,
                routeDestination = routeDestination,
                showOptions = showOptions,
                onPlaceChange = { place = it },
                onRouteStartChange = { routeStart = it },
                onRouteDestinationChange = { routeDestination = it },
                onToggleOptions = { showOptions = !showOptions },
                onMode = viewModel::setMode,
                onSearch = ::startSearch,
                onUseLocation = {
                    place = ""
                    keyboard?.hide()
                    requestLocationSearch()
                },
                onOpenPlan = { planLauncher.launch(arrayOf("*/*")) },
                onRadius = viewModel::setRadius,
                onCorridor = viewModel::setCorridor,
                onGap = viewModel::setMaxGap,
                onOnlyEnbw = viewModel::setOnlyEnbw,
                onDepartureNow = { viewModel.setDeparture(null) },
                onDepartureIn = viewModel::setDepartureInHours,
                onPickTime = ::pickDepartureTime,
            )

            if (state.loading) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            }

            state.hint?.let { hint ->
                MessageBanner(text = hint, onDismiss = viewModel::dismissMessages)
            }

            TabRow(selectedTabIndex = tab) {
                Tab(selected = tab == 0, onClick = { tab = 0 }, text = { Text("Liste") })
                Tab(
                    selected = tab == 1,
                    onClick = {
                        tab = 1
                        showOptions = false
                    },
                    text = { Text("Karte") },
                )
            }

            // weight(1f) statt fillMaxSize: der Inhalt bekommt exakt den Rest der Hoehe,
            // sonst schiebt die Karte die Bedienelemente aus dem Bild. clipToBounds
            // sorgt zusaetzlich dafuer, dass die Karten-View nichts ausserhalb malt.
            Box(
                modifier = Modifier
                    .weight(1f)
                    .clipToBounds(),
            ) {
                if (tab == 0) {
                    SpotList(
                        state = state,
                        onNavigate = ::navigateTo,
                        onShowOnMap = { spot ->
                            viewModel.selectSpot(spot.key())
                            showOptions = false
                            tab = 1
                        },
                        modifier = Modifier.fillMaxSize(),
                    )
                } else {
                    MapScreen(state = state, modifier = Modifier.fillMaxSize())
                }
            }

            if (tab == 1 && state.selectedSpotKey != null) {
                TextButton(
                    onClick = { viewModel.selectSpot(null) },
                    modifier = Modifier.padding(horizontal = 8.dp),
                ) { Text("Alle Treffer auf der Karte zeigen") }
            }
        }
    }
}

@Composable
private fun SearchControls(
    state: UiState,
    place: String,
    routeStart: String,
    routeDestination: String,
    showOptions: Boolean,
    onPlaceChange: (String) -> Unit,
    onRouteStartChange: (String) -> Unit,
    onRouteDestinationChange: (String) -> Unit,
    onToggleOptions: () -> Unit,
    onMode: (SearchMode) -> Unit,
    onSearch: () -> Unit,
    onUseLocation: () -> Unit,
    onOpenPlan: () -> Unit,
    onRadius: (Int) -> Unit,
    onCorridor: (Int) -> Unit,
    onGap: (Int) -> Unit,
    onOnlyEnbw: (Boolean) -> Unit,
    onDepartureNow: () -> Unit,
    onDepartureIn: (Long) -> Unit,
    onPickTime: () -> Unit,
) {
    Column(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            FilterChip(
                selected = state.mode == SearchMode.RADIUS,
                onClick = { onMode(SearchMode.RADIUS) },
                label = { Text("Umkreis") },
            )
            Spacer(Modifier.width(8.dp))
            FilterChip(
                selected = state.mode == SearchMode.ROUTE,
                onClick = { onMode(SearchMode.ROUTE) },
                label = { Text("Route") },
            )
            Spacer(Modifier.weight(1f))
            TextButton(onClick = onToggleOptions) {
                Text(if (showOptions) "Optionen aus" else "Optionen")
            }
        }

        if (state.mode == SearchMode.RADIUS) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = place,
                    onValueChange = onPlaceChange,
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                    label = { Text("Ort (leer = mein Standort)") },
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = { onSearch() }),
                )
                Spacer(Modifier.width(8.dp))
                Button(onClick = onSearch, enabled = !state.loading) { Text("Suchen") }
            }
        } else {
            OutlinedTextField(
                value = routeStart,
                onValueChange = onRouteStartChange,
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                label = { Text("Start (leer = mein Standort)") },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
            )
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = routeDestination,
                    onValueChange = onRouteDestinationChange,
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                    label = { Text("Ziel") },
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = { onSearch() }),
                )
                Spacer(Modifier.width(8.dp))
                Button(onClick = onSearch, enabled = !state.loading) { Text("Suchen") }
            }
        }

        if (showOptions) {
            Spacer(Modifier.height(4.dp))
            if (state.mode == SearchMode.RADIUS) {
                ChipRow(label = "Umkreis") {
                    listOf(10, 25, 50, 100).forEach { km ->
                        FilterChip(
                            selected = state.radiusKm == km,
                            onClick = { onRadius(km) },
                            label = { Text("$km km") },
                        )
                    }
                    TextButton(onClick = onUseLocation) { Text("Standort") }
                }
            } else {
                ChipRow(label = "Korridor") {
                    listOf(1000, 3000, 5000).forEach { meters ->
                        FilterChip(
                            selected = state.corridorMeters == meters,
                            onClick = { onCorridor(meters) },
                            label = { Text("${meters / 1000} km") },
                        )
                    }
                    TextButton(onClick = onOpenPlan) { Text("Plan oeffnen") }
                }
            }

            ChipRow(
                label = state.departureAt?.let {
                    "Abfahrt ${formatMoment(it, LocalDateTime.now())}"
                } ?: "Abfahrt jetzt",
            ) {
                FilterChip(
                    selected = state.departureAt == null,
                    onClick = onDepartureNow,
                    label = { Text("jetzt") },
                )
                listOf(1L, 2L, 4L).forEach { hours ->
                    FilterChip(
                        selected = false,
                        onClick = { onDepartureIn(hours) },
                        label = { Text("+$hours h") },
                    )
                }
                TextButton(onClick = onPickTime) { Text("Uhrzeit") }
            }

            ChipRow(label = "Abstand") {
                listOf(100, 300, 500, 1000).forEach { meters ->
                    FilterChip(
                        selected = state.maxGapMeters == meters,
                        onClick = { onGap(meters) },
                        label = { Text("$meters m") },
                    )
                }
                FilterChip(
                    selected = state.onlyEnbw,
                    onClick = { onOnlyEnbw(!state.onlyEnbw) },
                    label = { Text("nur EnBW") },
                )
            }
        }
    }
}

@Composable
private fun ChipRow(label: String, content: @Composable () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.labelMedium)
        content()
    }
}
