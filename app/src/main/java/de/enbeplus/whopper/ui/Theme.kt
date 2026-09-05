package de.enbeplus.whopper.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val Bolt = Color(0xFF1B8A4C)
private val Bun = Color(0xFFD98324)
private val Night = Color(0xFF0F2B46)

private val LightScheme = lightColorScheme(
    primary = Bolt,
    secondary = Bun,
    tertiary = Night,
)

private val DarkScheme = darkColorScheme(
    primary = Color(0xFF6FD79B),
    secondary = Color(0xFFF2B45C),
    tertiary = Color(0xFF9DC3E6),
)

@Composable
fun WhopperTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val context = LocalContext.current
    val colorScheme = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)

        darkTheme -> DarkScheme
        else -> LightScheme
    }
    MaterialTheme(colorScheme = colorScheme, content = content)
}
