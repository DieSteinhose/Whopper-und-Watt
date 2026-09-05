package de.enbeplus.whopper.model

import java.time.DayOfWeek
import java.time.LocalDateTime

sealed interface OpenState {
    /** Offen, [until] ist das Ende des laufenden Zeitfensters. */
    data class Open(val until: LocalDateTime?, val holidaysIgnored: Boolean) : OpenState

    /** Geschlossen, [nextOpen] ist die naechste Oeffnung innerhalb einer Woche. */
    data class Closed(val nextOpen: LocalDateTime?, val holidaysIgnored: Boolean) : OpenState

    /** Keine oder keine auswertbare Angabe in OSM. */
    data class Unknown(val reason: String) : OpenState
}

/**
 * Auswertung des OSM-Tags `opening_hours` fuer einen konkreten Zeitpunkt.
 *
 * Bewusst nur eine Teilmenge der Spezifikation, dafuer ehrlich: was nicht sicher
 * ausgewertet werden kann, kommt als [OpenState.Unknown] zurueck statt als Rateergebnis.
 *
 * Unterstuetzt wird, was in den Daten tatsaechlich vorkommt (Stichprobe: 67 Filialen):
 * - `24/7`
 * - Wochentage einzeln, als Liste und als Bereich, auch ueber den Sonntag hinweg (`Su-Th`)
 * - mehrere Zeitfenster je Regel (`00:00-01:00,10:00-24:00`)
 * - Fenster ueber Mitternacht (`Mo-Th 09:00-01:00`)
 * - Regeln ohne Wochentag (`08:00-22:00` gilt taeglich)
 * - `off` und `closed` als Ausnahme
 * - Regeltrenner `;` und `,`, die in echten Daten gemischt auftreten
 *
 * Nicht unterstuetzt und deshalb Unknown: Monats- und Datumsangaben, Kalenderwochen,
 * Sonnenauf- und -untergang, Wochentagszaehler wie `Mo[1]`.
 *
 * Feiertage (`PH`) werden ignoriert, weil dafuer ein Feiertagskalender noetig waere.
 * Enthaelt der Tag eine PH-Regel, wird das Ergebnis entsprechend markiert.
 */
object OpeningHours {

    private const val MINUTES_PER_DAY = 24 * 60

    private val DAY_NAMES = mapOf(
        "mo" to DayOfWeek.MONDAY,
        "tu" to DayOfWeek.TUESDAY,
        "we" to DayOfWeek.WEDNESDAY,
        "th" to DayOfWeek.THURSDAY,
        "fr" to DayOfWeek.FRIDAY,
        "sa" to DayOfWeek.SATURDAY,
        "su" to DayOfWeek.SUNDAY,
    )

    private val TIME_SPAN = Regex("""(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})""")
    private val STARTS_WITH_DAY = Regex("""^(mo|tu|we|th|fr|sa|su|ph)\b""", RegexOption.IGNORE_CASE)
    private val UNSUPPORTED = Regex(
        """\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|week|sunrise|sunset|dawn|dusk|easter)\b|\[|\|\|""",
        RegexOption.IGNORE_CASE,
    )

    private data class Rule(
        val days: Set<DayOfWeek>,
        /** Minuten ab Tagesbeginn; ein Ende groesser als 1440 laeuft in den Folgetag. */
        val spans: List<Pair<Int, Int>>,
        val closed: Boolean,
    )

    private data class Interval(val start: LocalDateTime, val end: LocalDateTime)

    fun evaluate(spec: String?, at: LocalDateTime): OpenState {
        val text = spec?.trim().orEmpty()
        if (text.isEmpty()) return OpenState.Unknown("keine Oeffnungszeiten in OSM")
        if (text == "24/7") return OpenState.Open(until = null, holidaysIgnored = false)
        if (UNSUPPORTED.containsMatchIn(text)) {
            return OpenState.Unknown("Zeitangabe zu komplex: $text")
        }

        val holidays = Regex("""\bPH\b""").containsMatchIn(text)
        val rules = splitRules(text).mapNotNull(::parseRule)
        if (rules.isEmpty()) return OpenState.Unknown("Zeitangabe nicht lesbar: $text")

        val open = merge(expand(rules.filterNot { it.closed }, at))
        val closed = merge(expand(rules.filter { it.closed }, at))

        val current = open.firstOrNull { !at.isBefore(it.start) && at.isBefore(it.end) }
        if (current != null && closed.none { !at.isBefore(it.start) && at.isBefore(it.end) }) {
            // Eine spaetere Ausnahme kann das laufende Fenster vorzeitig beenden.
            val cut = closed.filter { it.start.isAfter(at) && it.start.isBefore(current.end) }
                .minByOrNull { it.start }
            return OpenState.Open(cut?.start ?: current.end, holidays)
        }

        val next = open.filter { it.start.isAfter(at) }.minByOrNull { it.start }
        return OpenState.Closed(next?.start, holidays)
    }

    /**
     * Trennt Regeln. In echten Daten steht sowohl `;` als auch `,` zwischen Regeln,
     * und `,` trennt gleichzeitig Wochentags- und Zeitlisten innerhalb einer Regel.
     * Eine neue Regel beginnt deshalb erst, wenn ein Wochentag auftaucht und die
     * bisherige Regel schon ein Zeitfenster hat.
     */
    private fun splitRules(text: String): List<String> {
        val rules = mutableListOf<String>()
        text.split(';').forEach { chunk ->
            var current = mutableListOf<String>()
            chunk.split(',').map { it.trim() }.filter { it.isNotEmpty() }.forEach { part ->
                val startsNewRule = STARTS_WITH_DAY.containsMatchIn(part) &&
                    current.any { TIME_SPAN.containsMatchIn(it) || isClosedKeyword(it) }
                if (startsNewRule && current.isNotEmpty()) {
                    rules += current.joinToString(",")
                    current = mutableListOf()
                }
                current += part
            }
            if (current.isNotEmpty()) rules += current.joinToString(",")
        }
        return rules
    }

    private fun isClosedKeyword(text: String): Boolean =
        text.contains("off", ignoreCase = true) || text.contains("closed", ignoreCase = true)

    private fun parseRule(rule: String): Rule? {
        val text = rule.trim()
        if (text.isEmpty()) return null
        val closed = isClosedKeyword(text)
        val spans = TIME_SPAN.findAll(text).map { match ->
            val start = match.groupValues[1].toInt() * 60 + match.groupValues[2].toInt()
            var end = match.groupValues[3].toInt() * 60 + match.groupValues[4].toInt()
            // 10:00-01:00 meint den Folgetag, 00:00-24:00 den ganzen Tag.
            if (end <= start) end += MINUTES_PER_DAY
            start to end
        }.toList()
        if (spans.isEmpty() && !closed) return null

        // Der Wochentagsteil steht vor der ersten Zeitangabe.
        val firstTime = TIME_SPAN.find(text)?.range?.first ?: text.length
        val dayText = text.substring(0, firstTime).replace(Regex("""\b(off|closed)\b""", RegexOption.IGNORE_CASE), "")
        val days = if (dayText.isBlank()) {
            // Regel ohne Wochentag (etwa "08:00-22:00") gilt an jedem Tag.
            DayOfWeek.values().toSet()
        } else {
            // Bleibt nichts uebrig, war es eine reine Feiertagsregel. Die wird
            // verworfen, sonst gaelte sie faelschlich an jedem Wochentag.
            parseDays(dayText).ifEmpty { return null }
        }
        return Rule(
            days = days,
            spans = spans.ifEmpty { listOf(0 to MINUTES_PER_DAY) },
            closed = closed,
        )
    }

    private fun parseDays(text: String): Set<DayOfWeek> {
        val tokens = text.split(',').map { it.trim().lowercase() }.filter { it.isNotEmpty() }
        val days = mutableSetOf<DayOfWeek>()
        tokens.forEach { token ->
            if (token == "ph") return@forEach
            val range = token.split('-')
            if (range.size == 2) {
                val from = DAY_NAMES[range[0].take(2)]
                val to = DAY_NAMES[range[1].take(2)]
                if (from != null && to != null) {
                    var day: DayOfWeek = from
                    while (true) {
                        days += day
                        if (day == to) break
                        day = day.plus(1)
                    }
                }
            } else {
                DAY_NAMES[token.take(2)]?.let { days += it }
            }
        }
        return days
    }

    /** Rechnet die Regeln in konkrete Zeitfenster um, von gestern bis in acht Tage. */
    private fun expand(rules: List<Rule>, at: LocalDateTime): List<Interval> {
        val result = mutableListOf<Interval>()
        for (offset in -1..8) {
            val date = at.toLocalDate().plusDays(offset.toLong())
            val midnight = date.atStartOfDay()
            rules.filter { date.dayOfWeek in it.days }.forEach { rule ->
                rule.spans.forEach { (start, end) ->
                    result += Interval(
                        start = midnight.plusMinutes(start.toLong()),
                        end = midnight.plusMinutes(end.toLong()),
                    )
                }
            }
        }
        return result.sortedBy { it.start }
    }

    /** Fasst aneinandergrenzende Fenster zusammen, damit "offen bis" ueber Mitternacht stimmt. */
    private fun merge(intervals: List<Interval>): List<Interval> {
        val merged = mutableListOf<Interval>()
        intervals.forEach { interval ->
            val last = merged.lastOrNull()
            if (last != null && !interval.start.isAfter(last.end)) {
                if (interval.end.isAfter(last.end)) {
                    merged[merged.lastIndex] = last.copy(end = interval.end)
                }
            } else {
                merged += interval
            }
        }
        return merged
    }
}
