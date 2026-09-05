package de.enbeplus.whopper.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import de.enbeplus.whopper.UiState
import de.enbeplus.whopper.key
import de.enbeplus.whopper.model.Spot
import de.enbeplus.whopper.model.formatMeters
import de.enbeplus.whopper.model.maxPowerKw

@Composable
fun SpotList(
    state: UiState,
    onNavigate: (Double, Double, String) -> Unit,
    onShowOnMap: (Spot) -> Unit,
    modifier: Modifier = Modifier,
) {
    val error = state.error
    when {
        state.loading -> Column(
            modifier = modifier.fillMaxSize(),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            CircularProgressIndicator()
            Spacer(Modifier.height(16.dp))
            Text("Suche laeuft ...")
            Spacer(Modifier.height(4.dp))
            Text(
                text = "Die OSM-Abfrage dauert bei ${state.radiusKm} km Umkreis " +
                    if (state.radiusKm >= 100) "gut eine Minute." else "einige Sekunden.",
                style = MaterialTheme.typography.bodySmall,
            )
        }

        error != null -> Column(
            modifier = modifier
                .fillMaxSize()
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = error,
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.error,
            )
        }

        !state.searchedOnce -> Column(
            modifier = modifier
                .fillMaxSize()
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = "Laden und Whopper in einem Stopp",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                text = "Standort freigeben oder oben einen Ort eingeben, dann auf Suchen tippen.",
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        state.spots.isEmpty() -> Column(
            modifier = modifier
                .fillMaxSize()
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = "Keine Kombi gefunden.",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                text = "Im Umkreis von ${state.radiusKm} km: ${state.burgersFound} Burger King, " +
                    "${state.chargersFound} Ladesaeulen, aber keine im Abstand von " +
                    "${state.maxGapMeters} m zueinander. Radius oder Abstand erhoehen, " +
                    "oder den EnBW-Filter ausschalten.",
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        else -> LazyColumn(
            modifier = modifier.fillMaxSize(),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            items(state.spots, key = { it.key() }) { spot ->
                SpotCard(spot = spot, onNavigate = onNavigate, onShowOnMap = onShowOnMap)
            }
        }
    }
}

@Composable
private fun SpotCard(
    spot: Spot,
    onNavigate: (Double, Double, String) -> Unit,
    onShowOnMap: (Spot) -> Unit,
) {
    val charger = spot.nearest.charger
    val power = maxPowerKw(charger.tags)
    Card(
        modifier = Modifier.fillMaxWidth(),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(
                text = spot.burger.name ?: "Burger King",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
            spot.burger.address?.let {
                Text(text = it, style = MaterialTheme.typography.bodySmall)
            }

            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AssistChip(
                    onClick = {},
                    enabled = false,
                    label = { Text("${formatMeters(spot.nearest.gapMeters)} zur Saeule") },
                    colors = AssistChipDefaults.assistChipColors(),
                )
                spot.distanceFromMeMeters?.let {
                    AssistChip(
                        onClick = {},
                        enabled = false,
                        label = { Text("${formatMeters(it)} entfernt") },
                    )
                }
            }

            Spacer(Modifier.height(10.dp))
            HorizontalDivider()
            Spacer(Modifier.height(10.dp))

            Text(
                text = charger.tags["operator"]
                    ?: charger.tags["network"]
                    ?: charger.tags["name"]
                    ?: "Ladesaeule",
                style = MaterialTheme.typography.bodyLarge,
            )
            val details = buildList {
                power?.let { add("bis ${trimNumber(it)} kW") }
                charger.tags["capacity"]?.let { add("$it Ladepunkte") }
                charger.tags["socket:type2_combo"]?.let { add("CCS") }
                charger.tags["fee"]?.let { fee ->
                    add(if (fee == "no") "kostenlos" else "kostenpflichtig")
                }
                if (spot.chargers.size > 1) add("${spot.chargers.size} Standorte in Reichweite")
            }
            if (details.isNotEmpty()) {
                Text(
                    text = details.joinToString(" · "),
                    style = MaterialTheme.typography.bodySmall,
                )
            }

            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { onShowOnMap(spot) }) { Text("Karte") }
                OutlinedButton(
                    onClick = {
                        onNavigate(
                            charger.lat,
                            charger.lon,
                            charger.tags["operator"] ?: "Ladesaeule",
                        )
                    },
                ) { Text("Zur Saeule") }
                OutlinedButton(
                    onClick = {
                        onNavigate(
                            spot.burger.lat,
                            spot.burger.lon,
                            spot.burger.name ?: "Burger King",
                        )
                    },
                ) { Text("Zum BK") }
            }
        }
    }
}

private fun trimNumber(value: Double): String =
    if (value % 1.0 == 0.0) value.toInt().toString() else String.format("%.1f", value)
