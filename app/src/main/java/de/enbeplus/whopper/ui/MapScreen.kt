package de.enbeplus.whopper.ui

import android.graphics.Color
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import de.enbeplus.whopper.R
import de.enbeplus.whopper.UiState
import de.enbeplus.whopper.key
import de.enbeplus.whopper.model.Spot
import de.enbeplus.whopper.model.formatMeters
import org.osmdroid.tileprovider.tilesource.TileSourceFactory
import org.osmdroid.util.BoundingBox
import org.osmdroid.util.GeoPoint
import org.osmdroid.views.MapView
import org.osmdroid.views.overlay.Marker
import org.osmdroid.views.overlay.Polyline

@Composable
fun MapScreen(state: UiState, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val mapView = remember {
        MapView(context).apply {
            setTileSource(TileSourceFactory.MAPNIK)
            setMultiTouchControls(true)
            isTilesScaledToDpi = true
            zoomController.setVisibility(
                org.osmdroid.views.CustomZoomButtonsController.Visibility.SHOW_AND_FADEOUT,
            )
            controller.setZoom(11.0)
        }
    }

    DisposableEffect(Unit) {
        mapView.onResume()
        onDispose {
            mapView.onPause()
            mapView.onDetach()
        }
    }

    LaunchedEffect(state.spots, state.selectedSpotKey, state.route, state.originLat, state.originLon) {
        renderOverlays(mapView, state)
    }

    AndroidView(factory = { mapView }, modifier = modifier)
}

private fun renderOverlays(map: MapView, state: UiState) {
    map.overlays.clear()

    val burgerIcon = ContextCompat.getDrawable(map.context, R.drawable.ic_marker_burger)
    val closedIcon = ContextCompat.getDrawable(map.context, R.drawable.ic_marker_burger_closed)
    val chargerIcon = ContextCompat.getDrawable(map.context, R.drawable.ic_marker_charger)

    state.route?.let { route ->
        map.overlays.add(
            Polyline(map).apply {
                setPoints(route.points.map { GeoPoint(it.lat, it.lon) })
                outlinePaint.color = Color.argb(180, 0x1E, 0x6F, 0xC4)
                outlinePaint.strokeWidth = 8f
            },
        )
    }

    state.spots.forEach { spot ->
        val burgerPoint = GeoPoint(spot.burger.lat, spot.burger.lon)
        val chargerPoint = GeoPoint(spot.nearest.charger.lat, spot.nearest.charger.lon)

        val closed = spot.isClosedOnArrival

        map.overlays.add(
            Polyline(map).apply {
                setPoints(listOf(burgerPoint, chargerPoint))
                // Geschlossene Filialen bleiben auf der Karte, nur eben blass.
                outlinePaint.color = if (closed) {
                    Color.argb(110, 0x8E, 0x8E, 0x93)
                } else {
                    Color.rgb(0x1B, 0x8A, 0x4C)
                }
                outlinePaint.strokeWidth = 6f
            },
        )

        map.overlays.add(
            Marker(map).apply {
                position = burgerPoint
                setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_CENTER)
                (if (closed) closedIcon else burgerIcon)?.let { icon = it }
                title = spot.burger.name ?: "Burger King"
                snippet = listOfNotNull(
                    "${formatMeters(spot.nearest.gapMeters)} zur naechsten Ladesaeule",
                    spot.arrival?.let { arrival ->
                        spot.openState?.let { describeOpenState(it, arrival) }
                    },
                ).joinToString(" · ")
            },
        )

        spot.chargers.forEach { hit ->
            map.overlays.add(
                Marker(map).apply {
                    position = GeoPoint(hit.charger.lat, hit.charger.lon)
                    setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_CENTER)
                    chargerIcon?.let { icon = it }
                    title = hit.charger.tags["operator"]
                        ?: hit.charger.tags["network"]
                        ?: "Ladesaeule"
                    snippet = listOfNotNull(
                        hit.charger.tags["capacity"]?.let { "$it Ladepunkte" },
                        "${formatMeters(hit.gapMeters)} zum Burger King",
                    ).joinToString(" · ")
                },
            )
        }
    }

    map.invalidate()
    moveCamera(map, state)
}

private fun moveCamera(map: MapView, state: UiState) {
    val selected: Spot? = state.selectedSpotKey?.let { key ->
        state.spots.firstOrNull { it.key() == key }
    }

    val move: () -> Unit = {
        if (selected != null) {
            map.controller.setZoom(17.0)
            map.controller.setCenter(
                GeoPoint(
                    (selected.burger.lat + selected.nearest.charger.lat) / 2,
                    (selected.burger.lon + selected.nearest.charger.lon) / 2,
                ),
            )
        } else {
            val points = collectPoints(state)
            when {
                points.isEmpty() -> Unit
                points.size == 1 -> {
                    map.controller.setZoom(14.0)
                    map.controller.setCenter(points.first())
                }

                else -> runCatching {
                    map.zoomToBoundingBox(
                        BoundingBox.fromGeoPointsSafe(points).increaseByScale(1.15f),
                        false,
                        48,
                    )
                }
            }
        }
    }

    // zoomToBoundingBox braucht eine bereits vermessene View, sonst passiert nichts.
    if (map.width > 0 && map.height > 0) {
        map.post(move)
    } else {
        map.addOnFirstLayoutListener { _, _, _, _, _ -> map.post(move) }
    }
}

private fun collectPoints(state: UiState): List<GeoPoint> {
    val route = state.route
    if (route != null) return route.points.map { GeoPoint(it.lat, it.lon) }

    val originLat = state.originLat
    val originLon = state.originLon
    return buildList {
        state.spots.forEach {
            add(GeoPoint(it.burger.lat, it.burger.lon))
            add(GeoPoint(it.nearest.charger.lat, it.nearest.charger.lon))
        }
        if (originLat != null && originLon != null) {
            add(GeoPoint(originLat, originLon))
        }
    }
}
