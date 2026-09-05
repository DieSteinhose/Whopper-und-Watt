package de.enbeplus.whopper.data

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.CancellationSignal
import androidx.core.content.ContextCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * Standort ueber den plattformeigenen LocationManager. Bewusst ohne
 * Google Play Services, damit die App auch auf Geraeten ohne GMS laeuft.
 */
object LocationProvider {

    fun hasPermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    suspend fun current(context: Context): Location? {
        if (!hasPermission(context)) return null
        val manager = context.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
            ?: return null

        val providers = listOfNotNull(
            LocationManager.GPS_PROVIDER.takeIf { manager.allProviders.contains(it) },
            LocationManager.NETWORK_PROVIDER.takeIf { manager.allProviders.contains(it) },
            LocationManager.PASSIVE_PROVIDER.takeIf { manager.allProviders.contains(it) },
        )

        val cached = providers
            .mapNotNull { runCatching { manager.getLastKnownLocation(it) }.getOrNull() }
            .maxByOrNull { it.time }
        // Ein Fix, der nicht aelter als zwei Minuten ist, reicht fuer eine Umkreissuche.
        if (cached != null && System.currentTimeMillis() - cached.time < 2 * 60 * 1000L) return cached

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val provider = providers.firstOrNull() ?: return cached
            return suspendCancellableCoroutine { continuation ->
                val signal = CancellationSignal()
                continuation.invokeOnCancellation { signal.cancel() }
                runCatching {
                    manager.getCurrentLocation(
                        provider,
                        signal,
                        context.mainExecutor,
                    ) { location ->
                        if (continuation.isActive) continuation.resume(location ?: cached)
                    }
                }.onFailure {
                    if (continuation.isActive) continuation.resume(cached)
                }
            }
        }
        return cached
    }
}
