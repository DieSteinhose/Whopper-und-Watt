package de.enbeplus.whopper

import de.enbeplus.whopper.data.XlsxPlanReader
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class XlsxPlanReaderTest {

    /** Aufbau wie im echten ABRP-Export: Titel, Plan-URL, Kopfzeile, Wegpunkte, Summenzeile. */
    private val abrpSheet = """
        <worksheet><sheetData>
        <row r="1"><c r="A1" s="1" t="inlineStr"><is><t>ABRP Plan</t></is></c></row>
        <row r="2"><c r="A2" s="2" t="inlineStr"><is><t>https://abetterrouteplanner.com/?plan_uuid=2-abc</t></is></c></row>
        <row r="4"><c r="A4" s="3" t="inlineStr"><is><t>Wegpunkt</t></is></c><c r="B4" s="3" t="inlineStr"><is><t>SoC Ankunft</t></is></c></row>
        <row r="5"><c r="A5" t="inlineStr"><is><t>Punkt auf der Karte</t></is></c><c r="G5" t="inlineStr"><is><t>104 km</t></is></c></row>
        <row r="6"><c r="A6" t="inlineStr"><is><t>Bahnhofstra&#223;e 84, 46145 Oberhausen, Deutschland</t></is></c></row>
        <row r="7"><c r="A7" s="3" t="inlineStr"><is><t>55 Min.</t></is></c><c r="D7" s="3" t="inlineStr"><is><t>0,00 &#8364;</t></is></c></row>
        </sheetData></worksheet>
    """.trimIndent()

    @Test
    fun `aus dem abrp-blatt bleiben nur adressen uebrig`() {
        val plan = XlsxPlanReader.fromXml(abrpSheet, null)
        assertEquals(listOf("Bahnhofstraße 84, 46145 Oberhausen, Deutschland"), plan.addresses)
        assertEquals(1, plan.pointsWithoutAddress)
        assertEquals("https://abetterrouteplanner.com/?plan_uuid=2-abc", plan.planUrl)
    }

    @Test
    fun `mehrere adressen behalten ihre reihenfolge`() {
        val sheet = """
            <worksheet><sheetData>
            <row r="1"><c r="A1" t="inlineStr"><is><t>Wegpunkt</t></is></c></row>
            <row r="2"><c r="A2" t="inlineStr"><is><t>Hauptstr. 1, 44135 Dortmund, Deutschland</t></is></c></row>
            <row r="3"><c r="A3" t="inlineStr"><is><t>Bahnhofstr. 2, 45127 Essen, Deutschland</t></is></c></row>
            <row r="4"><c r="A4" t="inlineStr"><is><t>Marktplatz 3, 47051 Duisburg, Deutschland</t></is></c></row>
            </sheetData></worksheet>
        """.trimIndent()
        val plan = XlsxPlanReader.fromXml(sheet, null)
        assertEquals(3, plan.addresses.size)
        assertTrue(plan.addresses[0].contains("Dortmund"))
        assertTrue(plan.addresses[2].contains("Duisburg"))
        assertEquals(0, plan.pointsWithoutAddress)
    }

    @Test
    fun `gemeinsame zeichenkettentabelle wird aufgeloest`() {
        val sheet = """
            <worksheet><sheetData>
            <row r="1"><c r="A1" t="s"><v>0</v></c></row>
            <row r="2"><c r="A2" t="s"><v>1</v></c></row>
            </sheetData></worksheet>
        """.trimIndent()
        val shared = """
            <sst><si><t>Wegpunkt</t></si><si><t>Hauptstr. 1, 44135 Dortmund</t></si></sst>
        """.trimIndent()
        val plan = XlsxPlanReader.fromXml(sheet, shared)
        assertEquals(listOf("Hauptstr. 1, 44135 Dortmund"), plan.addresses)
    }

    @Test
    fun `einheiten uhrzeiten und ueberschriften sind keine adressen`() {
        listOf("55 Min.", "0,00 €", "104 km", "23:34", "12 kWh", "Wegpunkt", "Notizen", "80 %")
            .forEach { assertFalse(it, XlsxPlanReader.isAddress(it)) }
        listOf(
            "Bahnhofstraße 84, 46145 Oberhausen, Deutschland",
            "Stuttgart, Deutschland",
            "A8 Raststätte Sindelfingen",
        ).forEach { assertTrue(it, XlsxPlanReader.isAddress(it)) }
    }

    @Test
    fun `kartenpunkte werden als solche erkannt`() {
        assertTrue(XlsxPlanReader.isPlaceholder("Punkt auf der Karte"))
        assertTrue(XlsxPlanReader.isPlaceholder("point on map"))
        assertFalse(XlsxPlanReader.isPlaceholder("Marktplatz 3, 47051 Duisburg"))
    }

    @Test
    fun `echte xlsx-datei wird entpackt und gelesen`() {
        val bytes = ByteArrayOutputStream().also { out ->
            ZipOutputStream(out).use { zip ->
                zip.putNextEntry(ZipEntry("xl/worksheets/sheet1.xml"))
                zip.write(abrpSheet.toByteArray())
                zip.closeEntry()
            }
        }.toByteArray()

        assertEquals('P'.code.toByte(), bytes[0])
        val plan = XlsxPlanReader.read(bytes)
        assertEquals(1, plan.addresses.size)
        assertEquals(1, plan.pointsWithoutAddress)
    }
}
