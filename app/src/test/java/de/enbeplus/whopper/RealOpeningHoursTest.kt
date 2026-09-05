package de.enbeplus.whopper

import de.enbeplus.whopper.model.OpenState
import de.enbeplus.whopper.model.OpeningHours
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime

/**
 * Alle 54 unterschiedlichen opening_hours-Angaben, die in der Stichprobe entlang
 * Dortmund - Stuttgart und im Umkreis Stuttgart tatsaechlich in OSM stehen.
 * Der Test haelt fest, dass keine davon als "unbekannt" durchfaellt, und dass jede
 * ueber eine ganze Woche hinweg mindestens einmal offen und einmal geschlossen ist.
 */
class RealOpeningHoursTest {

    private val real = listOf(
        "00:00-01:00,10:00-24:00",
        "05:30-22:30",
        "08:00-22:00",
        "10:30-00:30; Fr,Sa 09:30-02:30",
        "Mo 00:00-01:00,08:00-01:00; Tu-Th 08:00-01:00; Fr 08:00-24:00; Sa,Su 00:00-24:00",
        "Mo 10:00-01:00, Tu 10:00-01:00, We 10:00-01:00, Th 10:00-01:00, Fr 10:00-02:00, Sa 10:00-02:00, Su 11:00-01:00",
        "Mo,Th-Su 06:00-22:00; Tu-We 08:00-20:00",
        "Mo-Fr 00:00-01:00,10:00-24:00; Sa,Su 00:00-03:00,10:00-24:00",
        "Mo-Fr 06:30-22:00; Sa 06:30-21:00; Su 06:30-22:00; PH 06:30-22:00",
        "Mo-Fr 10:00-22:00, Sa 10:00-22:00, Su 10:00-22:00, PH 10:00-22:00",
        "Mo-Fr 10:30-24:00, Sa,Su 11:00-24:00",
        "Mo-Sa 08:00-24:00",
        "Mo-Sa 10:00-24:00, Su 11:00-24:00",
        "Mo-Sa 12:00-20:00; Su 12:00-22:00",
        "Mo-Su 09:00-24:00",
        "Mo-Su 10:00-24:00",
        "Mo-Su 11:30-00:00",
        "Mo-Su,PH 10:00-16:30",
        "Mo-Su,PH 11:00-22:00",
        "Mo-Th 08:00-01:00, Fr 08:00-03:30, Sa 08:00-04:00, Su 10:00-01:00",
        "Mo-Th 08:00-01:00, Fr 08:00-05:00, Sa 08:00-05:00, Su 09:00-24:00, PH 09:00-24:00",
        "Mo-Th 08:00-01:00, Fr,Sa 08:00-03:00, Su 10:00-01:00",
        "Mo-Th 09:00-01:00, Fr 09:00-02:00, Sa 09:00-02:00, Su 10:00-01:00, PH 10:00-01:00",
        "Mo-Th 09:00-01:00, Fr,Sa 09:00-03:00, Su 10:00-01:00",
        "Mo-Th 09:00-24:00; Fr 00:00-01:00,09:00-24:00; Sa,Su 00:00-03:00,10:00-24:00",
        "Mo-Th 09:00-24:00; Fr 09:00-03:00; Sa 09:00-03:00; Su 10:00-24:00",
        "Mo-Th 09:00-24:00; Tu-Fr 00:00-01:00; Fr,Sa 09:00-24:00; Sa,Su 00:00-03:00; Su 10:00-24:00; Mo 00:00-01:00",
        "Mo-Th 09:30-02:00, Fr,Sa 10:00-04:00, Su 11:00-24:00",
        "Mo-Th 10:00-00:00; Fr,Sa 10:00-02:00; Su 10:00-24:00",
        "Mo-Th 10:00-01:00, Fr 10:00-03:00, Sa 10:00-02:00, Su 10:00-01:00, PH 10:00-01:00",
        "Mo-Th 10:00-01:00, Fr 10:00-03:00, Sa 10:00-03:00, Su 10:00-01:00, PH 10:00-01:00",
        "Mo-Th 10:00-01:00, Fr 10:00-03:00, Sa 10:00-03:00, Su 10:00-24:00, PH 10:00-24:00",
        "Mo-Th 10:00-22:00; Fr 10:00-23:00, Sa 10:00-23:00, Su 10:00-23:00, PH 10:00-23:00",
        "Mo-Th 10:00-22:00; Fr,Sa 11:00-24:00; Su 12:00-22:00",
        "Mo-Th 10:00-23:00; Fr-Sa 10:00-24:00; Su 10:30-23:00",
        "Mo-Th 10:00-24:00, Fr 10:00-01:00, Sa 10:00-01:00, Su 10:00-24:00, PH 10:00-24:00",
        "Mo-Th 10:00-24:00, Fr 10:00-02:00, Sa 10:00-02:00, Su 10:00-24:00, PH 10:00-24:00",
        "Mo-Th 10:00-24:00, Fr 10:00-03:00, Sa 10:00-03:00, Su 10:00-24:00, PH 10:00-24:00",
        "Mo-Th 10:00-24:00, Su 10:30-24:00, PH 10:00-24:00, Fr 10:00-02:00, Sa 10:30-02:00",
        "Mo-Th 11:00-01:00, Fr-Su 11:00-02:00",
        "Mo-Th 11:00-22:00; Fr 11:00-03:00; Sa 11:00-05:00; Su 00:00-24:00",
        "Mo-Th 11:00-23:00, Fr 11:00-24:00, Sa 11:00-24:00, Su 11:00-23:00, PH 11:00-23:00",
        "Mo-Th 11:00-23:00, Fr,Sa 11:00-24:00, Su 11:00-23:00",
        "Mo-Th 11:30-21:30; Fr-Su 11:30-22:00",
        "Mo-Th 12:00-20:00; Fr,Sa 11:00-21:00; Su 11:00-20:00",
        "Mo-Th 12:00-21:00, Fr 12:00-22:00, Sa 12:00-22:00, Su 12:00-21:00, PH 12:00-21:00",
        "PH,Mo-Th 10:00-24:00; Fr,Sa 10:00-03:00",
        "Su 11:00-01:00, Mo-Th 10:00-01:00, Fr,Sa 10:00-02:00",
        "Su-Th 09:00-24:00, Fr,Sa 09:00-01:00",
        "Su-Th 10:00-01:00, Fr,Sa 10:00-03:00",
        "Su-Th 10:30-01:00, Fr 10:30-03:00, Sa 11:00-03:00",
        "Su-Th 11:00-01:00, Fr,Sa 11:00-02:00",
        "Su-Th 11:00-24:00, Fr,Sa 11:00-02:00",
        "Su-We 10:00-01:00, Th 10:00-02:00, Fr,Sa 10:00-04:00",
    )

    @Test
    fun `jede echte angabe ist auswertbar`() {
        real.forEach { spec ->
            val state = OpeningHours.evaluate(spec, LocalDateTime.of(2026, 6, 3, 12, 0))
            assertTrue("nicht auswertbar: $spec -> $state", state !is OpenState.Unknown)
        }
    }

    @Test
    fun `jede echte angabe hat offene und geschlossene zeiten in der woche`() {
        val start = LocalDate.of(2026, 6, 1).atStartOfDay()
        real.forEach { spec ->
            val states = (0 until 7 * 24 * 2).map { half ->
                OpeningHours.evaluate(spec, start.plusMinutes(half * 30L))
            }
            assertTrue("nie offen: $spec", states.any { it is OpenState.Open })
            // 24/7 kommt in der Stichprobe nicht vor, alles andere schliesst irgendwann.
            assertTrue("nie geschlossen: $spec", states.any { it is OpenState.Closed })
        }
    }
}
