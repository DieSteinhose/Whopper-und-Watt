package de.enbeplus.whopper.ui

import android.graphics.Color
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
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

    LaunchedEffect(state.spots, state.selectedSpotKey, state.originLat, state.originLon) {
        renderOverlays(mapView, state)
    }

    AndroidView(factory = { mapView }, modifier = modifier)
}

private fun renderOverlays(map: MapView, state: UiState) {
    map.overlays.clear()

    state.spots.forEach { spot ->
        val burgerPoint = GeoPoint(spot.burger.lat, spot.burger.lon)
        val chargerPoint = GeoPoint(spot.nearest.charger.lat, spot.nearest.charger.lon)

        map.overlays.add(
            Polyline(map).apply {
                setPoints(listOf(burgerPoint, chargerPoint))
                outlinePaint.color = Color.rgb(0x1B, 0x8A, 0x4C)
                outlinePaint.strokeWidth = 6f
            },
        )

        map.overlays.add(
            Marker(map).apply {
                position = burgerPoint
                setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_BOTTOM)
                title = spot.burger.name ?: "Burger King"
                snippet = "${formatMeters(spot.nearest.gapMeters)} zur naechsten Ladesaeule"
            },
        )

        spot.chargers.forEach { hit ->
            map.overlays.add(
                Marker(map).apply {
                    position = GeoPoint(hit.charger.lat, hit.charger.lon)
                    setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_CENTER)
                    setTextIcon("Laden")
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

    val originLat = state.originLat
    val originLon = state.originLon
    if (originLat != null && originLon != null) {
        map.overlays.add(
            Marker(map).apply {
                position = GeoPoint(originLat, originLon)
                setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_CENTER)
                setTextIcon("Start")
                title = state.originLabel ?: "Suchmittelpunkt"
            },
        )
    }

    map.invalidate()
    moveCamera(map, state)
}

private fun moveCamera(map: MapView, state: UiState) {
    val selected: Spot? = state.selectedSpotKey?.let { key ->
        state.spots.firstOrNull { it.key() == key }
    }

    if (selected != null) {
        map.post {
            map.controller.setZoom(17.0)
            map.controller.setCenter(
                GeoPoint(
                    (selected.burger.lat + selected.nearest.charger.lat) / 2,
                    (selected.burger.lon + selected.nearest.charger.lon) / 2,
                ),
            )
        }
        return
    }

    val originLat = state.originLat
    val originLon = state.originLon
    val points = buildList {
        state.spots.forEach {
            add(GeoPoint(it.burger.lat, it.burger.lon))
            add(GeoPoint(it.nearest.charger.lat, it.nearest.charger.lon))
        }
        if (originLat != null && originLon != null) {
            add(GeoPoint(originLat, originLon))
        }
    }
    if (points.isEmpty()) return

    map.post {
        if (points.size == 1) {
            map.controller.setZoom(14.0)
            map.controller.setCenter(points.first())
        } else {
            val box = BoundingBox.fromGeoPointsSafe(points).increaseByScale(1.2f)
            runCatching { map.zoomToBoundingBox(box, false, 64) }
        }
    }
}
