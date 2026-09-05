package de.enbeplus.whopper

import android.Manifest
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import de.enbeplus.whopper.data.LocationProvider
import de.enbeplus.whopper.data.OverpassClient
import de.enbeplus.whopper.ui.MapScreen
import de.enbeplus.whopper.ui.SpotList
import de.enbeplus.whopper.ui.WhopperTheme
import org.osmdroid.config.Configuration

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

    fun searchHere() {
        keyboard?.hide()
        if (place.isNotBlank()) {
            viewModel.searchPlace(place)
        } else if (LocationProvider.hasPermission(context)) {
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
                        val subtitle = if (state.searchedOnce) {
                            "${state.spots.size} Kombis · ${state.originLabel ?: ""}"
                        } else {
                            "Burger King neben der Ladesaeule"
                        }
                        Text(subtitle, style = MaterialTheme.typography.labelSmall)
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
                onPlaceChange = { place = it },
                onSearch = ::searchHere,
                onUseLocation = {
                    place = ""
                    keyboard?.hide()
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
                },
                onRadius = viewModel::setRadius,
                onGap = viewModel::setMaxGap,
                onOnlyEnbw = viewModel::setOnlyEnbw,
            )

            TabRow(selectedTabIndex = tab) {
                Tab(
                    selected = tab == 0,
                    onClick = { tab = 0 },
                    text = { Text("Liste") },
                )
                Tab(
                    selected = tab == 1,
                    onClick = { tab = 1 },
                    text = { Text("Karte") },
                )
            }

            if (tab == 0) {
                SpotList(
                    state = state,
                    onNavigate = ::navigateTo,
                    onShowOnMap = { spot ->
                        viewModel.selectSpot(spot.key())
                        tab = 1
                    },
                    modifier = Modifier.fillMaxSize(),
                )
            } else {
                Column(Modifier.fillMaxSize()) {
                    if (state.selectedSpotKey != null) {
                        TextButton(onClick = { viewModel.selectSpot(null) }) {
                            Text("Alle Treffer zeigen")
                        }
                    }
                    MapScreen(state = state, modifier = Modifier.fillMaxSize())
                }
            }
        }
    }
}

@Composable
private fun SearchControls(
    state: UiState,
    place: String,
    onPlaceChange: (String) -> Unit,
    onSearch: () -> Unit,
    onUseLocation: () -> Unit,
    onRadius: (Int) -> Unit,
    onGap: (Int) -> Unit,
    onOnlyEnbw: (Boolean) -> Unit,
) {
    Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
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

        Spacer(Modifier.height(4.dp))
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Umkreis", style = MaterialTheme.typography.labelMedium)
            listOf(10, 25, 50, 100).forEach { km ->
                FilterChip(
                    selected = state.radiusKm == km,
                    onClick = { onRadius(km) },
                    label = { Text("$km km") },
                )
            }
        }

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Abstand", style = MaterialTheme.typography.labelMedium)
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
            TextButton(onClick = onUseLocation) { Text("Standort") }
        }
    }
}
