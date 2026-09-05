package de.enbeplus.whopper.ui

import de.enbeplus.whopper.model.OpenState
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale

private val CLOCK = DateTimeFormatter.ofPattern("HH:mm")

/** Uhrzeit, mit Wochentag davor, sobald es nicht mehr derselbe Tag ist. */
fun formatMoment(moment: LocalDateTime, reference: LocalDateTime): String {
    val time = moment.format(CLOCK)
    return if (moment.toLocalDate() == reference.toLocalDate()) {
        time
    } else {
        val day = moment.dayOfWeek.getDisplayName(TextStyle.SHORT, Locale.GERMAN)
        "$day $time"
    }
}

/**
 * Auf der Route ist es eine geschaetzte Ankunft, im Umkreismodus schlicht der
 * gewaehlte Zeitpunkt, weil dort keine Fahrzeit dazwischen liegt.
 */
fun describeArrival(arrival: LocalDateTime, reference: LocalDateTime, onRoute: Boolean): String =
    if (onRoute) {
        "Ankunft ca. ${formatMoment(arrival, reference)}"
    } else {
        "Zeitpunkt ${formatMoment(arrival, reference)}"
    }

fun describeOpenState(state: OpenState, arrival: LocalDateTime): String = when (state) {
    is OpenState.Open -> buildString {
        append(if (state.until == null) "durchgehend offen" else "offen bis ${formatMoment(state.until, arrival)}")
        if (state.holidaysIgnored) append(" (ohne Feiertage)")
    }

    is OpenState.Closed -> buildString {
        append("geschlossen")
        state.nextOpen?.let { append(", oeffnet ${formatMoment(it, arrival)}") }
        if (state.holidaysIgnored) append(" (ohne Feiertage)")
    }

    is OpenState.Unknown -> state.reason
}
