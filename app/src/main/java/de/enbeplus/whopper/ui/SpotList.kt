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
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import de.enbeplus.whopper.SearchMode
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
        state.loading -> CenteredMessage(
            modifier = modifier,
            title = state.progress ?: "Suche laeuft ...",
            body = if (state.mode == SearchMode.ROUTE) {
                "Lange Routen brauchen mehrere Overpass-Abfragen, das dauert ein paar Minuten."
            } else {
                "Die OSM-Abfrage dauert je nach Umkreis einige Sekunden."
            },
            showSpinner = true,
        )

        error != null -> CenteredMessage(
            modifier = modifier,
            title = "Das hat nicht geklappt",
            body = error,
            isError = true,
        )

        !state.searchedOnce -> CenteredMessage(
            modifier = modifier,
            title = "Laden und Whopper in einem Stopp",
            body = "Umkreis: Ort eingeben oder Standort freigeben. " +
                "Route: Start und Ziel eintippen, oder eine GPX-Datei laden.",
        )

        state.spots.isEmpty() -> CenteredMessage(
            modifier = modifier,
            title = "Keine Kombi gefunden",
            body = buildString {
                append("Gefunden wurden ${state.burgersFound} Filialen und ")
                append("${state.chargersFound} Ladesaeulen, aber keine davon im Abstand ")
                append("von ${state.maxGapMeters} m zueinander")
                append(if (state.onlyEnbw) " und mit EnBW-Tag. " else ". ")
                append(
                    if (state.mode == SearchMode.ROUTE) {
                        "Korridor oder Abstand erhoehen, oder den EnBW-Filter ausschalten."
                    } else {
                        "Umkreis oder Abstand erhoehen, oder den EnBW-Filter ausschalten."
                    },
                )
            },
        )

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
fun MessageBanner(text: String, onDismiss: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.secondaryContainer,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(start = 16.dp, top = 8.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = text,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onDismiss) { Text("OK") }
        }
    }
}

@Composable
private fun CenteredMessage(
    title: String,
    body: String,
    modifier: Modifier = Modifier,
    isError: Boolean = false,
    showSpinner: Boolean = false,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        if (showSpinner) {
            CircularProgressIndicator()
            Spacer(Modifier.height(16.dp))
        }
        Text(
            text = title,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            color = if (isError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
        )
        Spacer(Modifier.height(8.dp))
        Text(text = body, style = MaterialTheme.typography.bodyMedium)
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
                )
                val progress = spot.routeProgressMeters
                val distance = spot.distanceFromMeMeters
                if (progress != null) {
                    AssistChip(
                        onClick = {},
                        enabled = false,
                        label = { Text("km ${(progress / 1000).toInt()} ab Start") },
                    )
                } else if (distance != null) {
                    AssistChip(
                        onClick = {},
                        enabled = false,
                        label = { Text("${formatMeters(distance)} entfernt") },
                    )
                }
            }
            spot.routeOffsetMeters?.let {
                Spacer(Modifier.height(4.dp))
                Text(
                    text = "${formatMeters(it)} neben der Route",
                    style = MaterialTheme.typography.bodySmall,
                )
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
