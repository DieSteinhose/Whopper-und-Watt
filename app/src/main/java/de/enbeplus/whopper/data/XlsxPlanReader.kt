package de.enbeplus.whopper.data

import java.io.ByteArrayInputStream
import java.util.zip.ZipInputStream

/**
 * Liest den Excel-Export von A Better Routeplanner.
 *
 * Wichtige Einschraenkung, die aus dem Dateiformat kommt und nicht aus dieser App:
 * der Export enthaelt **keine Koordinaten**, nur die Adresstexte der Wegpunkte.
 * Wegpunkte, die in ABRP per Klick auf die Karte gesetzt wurden, stehen dort als
 * "Punkt auf der Karte" und tragen ueberhaupt keine Ortsinformation. Aus den
 * Adressen laesst sich per Geocoding und Routing eine Strecke rekonstruieren,
 * aus den Kartenpunkten nicht.
 */
object XlsxPlanReader {

    data class Plan(
        val addresses: List<String>,
        /** Wegpunkte ohne Adresse, also in ABRP auf der Karte angetippt. */
        val pointsWithoutAddress: Int,
        val planUrl: String?,
    )

    fun read(bytes: ByteArray): Plan {
        var sheet: String? = null
        var sharedStrings: String? = null
        ZipInputStream(ByteArrayInputStream(bytes)).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                when {
                    entry.name.endsWith("xl/worksheets/sheet1.xml") -> sheet = zip.readBytes().decodeToString()
                    entry.name.endsWith("xl/sharedStrings.xml") -> sharedStrings = zip.readBytes().decodeToString()
                }
            }
        }
        val sheetXml = sheet ?: throw IllegalStateException(
            "Die Datei sieht nicht nach einer Excel-Tabelle mit einem Tabellenblatt aus.",
        )
        return fromXml(sheetXml, sharedStrings)
    }

    fun fromXml(sheetXml: String, sharedStringsXml: String?): Plan {
        val shared = sharedStringsXml?.let(::parseSharedStrings) ?: emptyList()
        val column = parseFirstColumn(sheetXml, shared)
        return Plan(
            addresses = column.filter(::isAddress),
            pointsWithoutAddress = column.count(::isPlaceholder),
            planUrl = column.firstOrNull { it.startsWith("http", ignoreCase = true) },
        )
    }

    private fun parseSharedStrings(xml: String): List<String> =
        Regex("<si>(.*?)</si>", RegexOption.DOT_MATCHES_ALL).findAll(xml)
            .map { item ->
                Regex("<t[^>]*>(.*?)</t>", RegexOption.DOT_MATCHES_ALL)
                    .findAll(item.groupValues[1])
                    .joinToString("") { unescape(it.groupValues[1]) }
            }
            .toList()

    /** Alle Zellen der Spalte A in Zeilenreihenfolge. Dort stehen bei ABRP die Wegpunkte. */
    private fun parseFirstColumn(sheetXml: String, shared: List<String>): List<String> =
        Regex("""<c\b[^>]*?r="A\d+"[^>]*?(?:/>|>(.*?)</c>)""", RegexOption.DOT_MATCHES_ALL)
            .findAll(sheetXml)
            .map { match -> cellText(match.value, match.groupValues[1], shared) }
            .filter { it.isNotBlank() }
            .toList()

    private fun cellText(cellTag: String, body: String, shared: List<String>): String {
        if (body.isBlank()) return ""
        val inline = Regex("<t[^>]*>(.*?)</t>", RegexOption.DOT_MATCHES_ALL).find(body)
        if (inline != null) return unescape(inline.groupValues[1]).trim()
        val value = Regex("<v>(.*?)</v>", RegexOption.DOT_MATCHES_ALL).find(body)
            ?: return ""
        val raw = unescape(value.groupValues[1]).trim()
        // t="s" heisst: die Zahl ist ein Index in die gemeinsame Zeichenkettentabelle.
        return if (cellTag.contains("t=\"s\"")) shared.getOrNull(raw.toIntOrNull() ?: -1).orEmpty() else raw
    }

    private val NUMERIC_ENTITY = Regex("&#(x?)([0-9a-fA-F]+);")

    /**
     * Excel-Exporte schreiben Umlaute mal direkt als UTF-8, mal als Zahlenreferenz
     * (&#223; fuer ss). Beides muss hier ankommen, sonst sucht Nominatim spaeter
     * nach einer Adresse mit kaputtem Namen.
     */
    private fun unescape(text: String): String = NUMERIC_ENTITY
        .replace(text) { match ->
            val radix = if (match.groupValues[1].isEmpty()) 10 else 16
            match.groupValues[2].toIntOrNull(radix)
                ?.let { String(Character.toChars(it)) }
                ?: match.value
        }
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
        .replace("&amp;", "&")

    private val UNIT = Regex(
        """^\s*\d+([.,]\d+)?\s*(km|m|min\.?|std\.?|h|kwh|%|€)\s*$""",
        RegexOption.IGNORE_CASE,
    )
    private val CLOCK = Regex("""^\s*\d{1,2}:\d{2}\s*$""")

    private val PLACEHOLDERS = listOf("punkt auf der karte", "point on map", "map point")

    fun isPlaceholder(value: String): Boolean =
        PLACEHOLDERS.any { value.trim().equals(it, ignoreCase = true) }

    /**
     * Grobfilter fuer die Spalte: uebrig bleiben soll, was Nominatim ueberhaupt
     * finden kann. Ueberschriften, Summenzeilen, Zeitangaben und Kartenpunkte
     * haben entweder kein Komma und keine Ziffer oder sind reine Einheiten.
     */
    fun isAddress(value: String): Boolean {
        val text = value.trim()
        if (text.length < 5) return false
        if (text.startsWith("http", ignoreCase = true)) return false
        if (isPlaceholder(text)) return false
        if (UNIT.matches(text) || CLOCK.matches(text)) return false
        if (!text.any { it.isLetter() }) return false
        return text.contains(',') || text.any { it.isDigit() }
    }
}
