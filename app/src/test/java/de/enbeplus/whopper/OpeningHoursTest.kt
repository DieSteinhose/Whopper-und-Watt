package de.enbeplus.whopper

import de.enbeplus.whopper.model.OpenState
import de.enbeplus.whopper.model.OpeningHours
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.temporal.TemporalAdjusters

/**
 * Alle Zeitangaben in diesen Tests stammen wortwoertlich aus OSM, aus der Stichprobe
 * entlang der Strecke Dortmund - Stuttgart.
 */
class OpeningHoursTest {

    private fun at(day: DayOfWeek, hour: Int, minute: Int = 0): LocalDateTime =
        LocalDate.of(2026, 6, 1)
            .with(TemporalAdjusters.nextOrSame(day))
            .atTime(hour, minute)

    private fun open(spec: String, moment: LocalDateTime): OpenState.Open {
        val state = OpeningHours.evaluate(spec, moment)
        assertTrue("erwartet offen, war $state", state is OpenState.Open)
        return state as OpenState.Open
    }

    private fun closed(spec: String, moment: LocalDateTime): OpenState.Closed {
        val state = OpeningHours.evaluate(spec, moment)
        assertTrue("erwartet geschlossen, war $state", state is OpenState.Closed)
        return state as OpenState.Closed
    }

    @Test
    fun `24 durch 7 ist immer offen`() {
        val state = open("24/7", at(DayOfWeek.SUNDAY, 3, 30))
        assertNull(state.until)
    }

    @Test
    fun `fenster ueber mitternacht reicht in den folgetag`() {
        val spec = "Mo-Th 09:00-01:00, Fr,Sa 09:00-03:00, Su 10:00-01:00"

        // Mittwoch 23 Uhr: offen, und zwar bis Donnerstag 01:00.
        val wednesday = open(spec, at(DayOfWeek.WEDNESDAY, 23))
        assertEquals(DayOfWeek.THURSDAY, wednesday.until!!.dayOfWeek)
        assertEquals(LocalTime.of(1, 0), wednesday.until!!.toLocalTime())

        // Samstag 02 Uhr faellt noch in das Freitagsfenster bis 03:00.
        val saturdayNight = open(spec, at(DayOfWeek.SATURDAY, 2))
        assertEquals(LocalTime.of(3, 0), saturdayNight.until!!.toLocalTime())

        // Donnerstag 02 Uhr ist zu, geoeffnet wird erst wieder um 09:00.
        val thursdayEarly = closed(spec, at(DayOfWeek.THURSDAY, 2))
        assertEquals(DayOfWeek.THURSDAY, thursdayEarly.nextOpen!!.dayOfWeek)
        assertEquals(LocalTime.of(9, 0), thursdayEarly.nextOpen!!.toLocalTime())
    }

    @Test
    fun `zwei fenster am tag werden ueber mitternacht zusammengefasst`() {
        val spec = "Mo-Fr 00:00-01:00,10:00-24:00; Sa,Su 00:00-03:00,10:00-24:00"

        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.MONDAY, 0, 30)) is OpenState.Open)
        val gap = closed(spec, at(DayOfWeek.MONDAY, 9))
        assertEquals(LocalTime.of(10, 0), gap.nextOpen!!.toLocalTime())

        // Montag 23 Uhr laeuft nahtlos in das Dienstagsfenster bis 01:00 weiter.
        val evening = open(spec, at(DayOfWeek.MONDAY, 23))
        assertEquals(DayOfWeek.TUESDAY, evening.until!!.dayOfWeek)
        assertEquals(LocalTime.of(1, 0), evening.until!!.toLocalTime())
    }

    @Test
    fun `wochentagsbereich ueber den sonntag hinweg`() {
        val spec = "Su-Th 11:00-01:00, Fr,Sa 11:00-02:00"
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.SUNDAY, 12)) is OpenState.Open)
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.TUESDAY, 12)) is OpenState.Open)
        val saturday = open(spec, at(DayOfWeek.SATURDAY, 23))
        assertEquals(LocalTime.of(2, 0), saturday.until!!.toLocalTime())
    }

    @Test
    fun `regel ohne wochentag gilt jeden tag`() {
        val spec = "05:30-22:30"
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.SUNDAY, 6)) is OpenState.Open)
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.WEDNESDAY, 23)) is OpenState.Closed)
    }

    @Test
    fun `feiertage werden ignoriert und das ergebnis markiert`() {
        val state = open("Mo-Su,PH 11:00-22:00", at(DayOfWeek.SUNDAY, 12))
        assertTrue(state.holidaysIgnored)
        assertFalse(open("Mo-Su 11:00-22:00", at(DayOfWeek.SUNDAY, 12)).holidaysIgnored)
    }

    @Test
    fun `semikolon und komma als regeltrenner gemischt`() {
        val spec = "Mo-Th 10:00-22:00; Fr 10:00-23:00, Sa 10:00-23:00, Su 10:00-23:00, PH 10:00-23:00"
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.MONDAY, 22, 30)) is OpenState.Closed)
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.FRIDAY, 22, 30)) is OpenState.Open)
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.SUNDAY, 22, 30)) is OpenState.Open)
    }

    @Test
    fun `off-regel schliesst einen einzelnen tag`() {
        val spec = "Mo-Su 10:00-20:00; We off"
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.TUESDAY, 12)) is OpenState.Open)
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.WEDNESDAY, 12)) is OpenState.Closed)
    }

    @Test
    fun `zerstueckelte echte angabe bleibt auswertbar`() {
        // So steht es tatsaechlich in OSM, inklusive der doppelten Nennung von Mo.
        val spec = "Mo 00:00-01:00,08:00-01:00; Tu-Th 08:00-01:00; Fr 08:00-24:00; Sa,Su 00:00-24:00"
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.SATURDAY, 4)) is OpenState.Open)
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.TUESDAY, 7)) is OpenState.Closed)
        assertTrue(OpeningHours.evaluate(spec, at(DayOfWeek.TUESDAY, 9)) is OpenState.Open)
    }

    @Test
    fun `was nicht sicher lesbar ist wird als unbekannt gemeldet statt geraten`() {
        listOf(
            null,
            "",
            "Mar-Oct 10:00-20:00",
            "sunrise-sunset",
            "Mo[1] 10:00-20:00",
            "Dec 24 10:00-14:00",
        ).forEach { spec ->
            val state = OpeningHours.evaluate(spec, at(DayOfWeek.MONDAY, 12))
            assertTrue("$spec ergab $state", state is OpenState.Unknown)
        }
    }
}
