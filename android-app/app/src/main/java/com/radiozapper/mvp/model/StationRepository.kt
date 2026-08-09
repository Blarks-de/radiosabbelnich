package com.radiozapper.mvp.model

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption

private const val TAG = "StationRepository"
private const val STATIONS_FILE_NAME = "stations.json"

// Dieselben 3 Sender/IDs/URLs wie im bisherigen hartcodierten MVP - dient nur
// als Startbestand, wenn stations.json noch nicht existiert (siehe init()).
// Kein Bestandsnutzer hatte je persistierte Senderdaten (die Liste war eine
// Compile-Time-Konstante), es gibt also nichts "umzuziehen" - dieselbe Rolle
// wie DEFAULT_STATIONS in stations_store.py.
private val DEFAULT_STATIONS = listOf(
    Station(
        id = "dlf",
        name = "Deutschlandfunk (viel Sprache)",
        url = "https://st01.sslstream.dlf.de/dlf/01/128/mp3/stream.mp3",
        category = Categories.DEFAULT,
    ),
    Station(
        id = "1live",
        name = "1LIVE (WDR, viel Musik)",
        url = "https://wdr-1live-live.icecastssl.wdr.de/wdr/1live/live/mp3/128/stream.mp3",
        category = Categories.DEFAULT,
    ),
    Station(
        id = "swr3",
        name = "SWR3 (Musik/Moderation gemischt)",
        url = "https://liveradio.swr.de/sw282p3/swr3/play.mp3",
        category = Categories.DEFAULT,
    ),
)

/**
 * Singleton statt Klasse-pro-Owner (anders als VoskModelManager/UpdateManager,
 * die sich jeder Owner selbst instanziiert) - MainActivity,
 * StationManagementActivity und PlaybackService muessen sich exakt dieselbe
 * Instanz + denselben StateFlow teilen, sonst saehe der laufende Service
 * Aenderungen aus der Verwaltungs-UI nie. Initialisierung ueber
 * RadioSabbelNichApplication.onCreate() (garantiert vor jeder Komponente in
 * diesem Prozess), nicht per "wer zuerst dran ist, ruft init() auf"-Muster
 * in den einzelnen Komponenten - genau das waere die Art Bug, die erst
 * auffaellt, wenn ein neuer Einstiegspunkt ihn vergisst.
 *
 * Persistenz als flache JSON-Datei (org.json, keine neue Dependency, schon
 * anderswo in der App genutzt) statt Room - Vorbild stations_store.py ist
 * selbst ein flacher JSON-Store, keine SQL-Datenbank, und bei einer
 * Datenmenge von Dutzenden Sendern ohne Relationen waere Room (Compiler/KSP,
 * Entity/DAO, Migrations-Versionierung) unverhaeltnismaessig.
 */
object StationRepository {

    private lateinit var stationsFile: File

    private val _stations = MutableStateFlow<List<Station>>(emptyList())
    val stations: StateFlow<List<Station>> = _stations

    fun init(context: Context) {
        stationsFile = File(context.filesDir, STATIONS_FILE_NAME)
        _stations.value = if (stationsFile.exists()) {
            readFromDisk()
        } else {
            writeToDisk(DEFAULT_STATIONS)
            DEFAULT_STATIONS
        }
    }

    /** Aktivierte Sender, alphabetisch nach Name - das ist die Rotationsreihenfolge. */
    fun activeStations(): List<Station> =
        _stations.value.filter { it.enabled }.sortedBy { it.name.lowercase() }

    suspend fun addStation(name: String, url: String, category: String, enabled: Boolean = true): Station {
        val cleanName = name.trim()
        val cleanUrl = url.trim()
        require(cleanName.isNotEmpty() && cleanUrl.isNotEmpty()) { "Name und URL duerfen nicht leer sein." }
        val resolvedCategory = if (category in Categories.ALL) category else Categories.DEFAULT

        return withContext(Dispatchers.IO) {
            val current = _stations.value
            val id = uniqueId(slugify(cleanName), current.map { it.id }.toSet())
            val station = Station(id, cleanName, cleanUrl, resolvedCategory, enabled)
            persist(current + station)
            station
        }
    }

    /**
     * Fuer den M3U-Import (siehe import/StationImporter.kt, Vorbild
     * stations_store.bulk_add): fuegt mehrere Sender in EINEM Schreibvorgang
     * hinzu statt pro Sender einmal addStation() aufzurufen - bei potenziell
     * hunderten Importeintraegen sonst unnoetig viele Disk-Writes. Der Aufrufer
     * hat bereits gegen die bestehende Liste UND innerhalb der eigenen Batch
     * dedupliziert (siehe StationImporter.runImport()), hier also keine
     * Namens-/URL-Kollisionspruefung mehr noetig - nur die id muss weiterhin
     * eindeutig sein.
     */
    suspend fun bulkAdd(entries: List<Pair<String, String>>, category: String, enabled: Boolean): List<Station> {
        if (entries.isEmpty()) return emptyList()
        val resolvedCategory = if (category in Categories.ALL) category else Categories.DEFAULT

        return withContext(Dispatchers.IO) {
            val current = _stations.value
            val usedIds = current.map { it.id }.toMutableSet()
            val added = entries.map { (name, url) ->
                val id = uniqueId(slugify(name.trim()), usedIds)
                usedIds += id
                Station(id, name.trim(), url.trim(), resolvedCategory, enabled)
            }
            persist(current + added)
            added
        }
    }

    /** Aendert Name/URL/Kategorie/Enabled - die id bleibt IMMER gleich (siehe Station.kt-Doc). */
    suspend fun updateStation(id: String, name: String, url: String, category: String, enabled: Boolean): Station {
        val cleanName = name.trim()
        val cleanUrl = url.trim()
        require(cleanName.isNotEmpty() && cleanUrl.isNotEmpty()) { "Name und URL duerfen nicht leer sein." }
        val resolvedCategory = if (category in Categories.ALL) category else Categories.DEFAULT

        return withContext(Dispatchers.IO) {
            val current = _stations.value
            if (current.none { it.id == id }) {
                throw NoSuchElementException("Sender nicht gefunden: $id")
            }
            val updated = Station(id, cleanName, cleanUrl, resolvedCategory, enabled)
            persist(current.map { if (it.id == id) updated else it })
            updated
        }
    }

    suspend fun setEnabled(id: String, enabled: Boolean) {
        withContext(Dispatchers.IO) {
            val current = _stations.value
            val existing = current.find { it.id == id } ?: throw NoSuchElementException("Sender nicht gefunden: $id")
            if (existing.enabled == enabled) return@withContext
            persist(current.map { if (it.id == id) it.copy(enabled = enabled) else it })
        }
    }

    /**
     * Mind. 1 Sender muss bestehen bleiben - exakte Paritaet zu
     * stations_store.delete() (prueft dort nur gegen die UNGEFILTERTE Liste,
     * nicht gegen "mindestens 1 aktivierter"), sonst gaebe es kein
     * Rotationsziel mehr.
     */
    suspend fun deleteStation(id: String) {
        withContext(Dispatchers.IO) {
            val current = _stations.value
            if (current.size <= 1) {
                throw IllegalStateException("Mindestens ein Sender muss konfiguriert bleiben.")
            }
            if (current.none { it.id == id }) {
                throw NoSuchElementException("Sender nicht gefunden: $id")
            }
            persist(current.filterNot { it.id == id })
        }
    }

    private fun persist(list: List<Station>) {
        writeToDisk(list)
        _stations.value = list
    }

    private fun slugify(name: String): String {
        val slug = name.lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-')
        return slug.ifEmpty { "sender" }
    }

    private fun uniqueId(base: String, existingIds: Set<String>): String {
        var candidate = base
        var n = 2
        while (candidate in existingIds) {
            candidate = "$base-$n"
            n++
        }
        return candidate
    }

    private fun readFromDisk(): List<Station> = try {
        val array = JSONArray(stationsFile.readText())
        (0 until array.length()).map { i ->
            val obj = array.getJSONObject(i)
            Station(
                id = obj.getString("id"),
                name = obj.getString("name"),
                url = obj.getString("url"),
                category = obj.optString("category", Categories.DEFAULT),
                enabled = obj.optBoolean("enabled", true),
            )
        }
    } catch (e: Exception) {
        // Kaputte Datei zur Seite legen, BEVOR der naechste Schreibvorgang sie
        // mit dem Startbestand ueberschreibt - vorher war der Verlust der
        // kompletten (u.U. hunderte Sender umfassenden) Liste endgueltig und
        // unbemerkt (Review-Befund 13, siehe SESSION.md).
        val backup = File(stationsFile.parentFile, "$STATIONS_FILE_NAME.corrupt")
        runCatching { stationsFile.copyTo(backup, overwrite = true) }
            .onSuccess { Log.e(TAG, "stations.json unlesbar - Kopie gesichert unter ${backup.name}") }
            .onFailure { Log.e(TAG, "stations.json unlesbar, Sicherungskopie ebenfalls fehlgeschlagen", it) }
        Log.e(TAG, "stations.json konnte nicht gelesen werden, falle auf Startbestand zurueck", e)
        DEFAULT_STATIONS
    }

    /**
     * Temp-Datei im selben Verzeichnis + atomarer Move statt direktem
     * Schreiben - anders als stations_store.py (dort scheitert os.replace()
     * ueber den Docker-Bind-Mount mit "Device or resource busy") ist
     * filesDir ganz normaler lokaler Speicher, ATOMIC_MOVE also tatsaechlich
     * verfuegbar. File.renameTo() bewusst NICHT genutzt - laut eigener
     * Javadoc plattformabhaengig und liefert bei Fehlern nur `false` statt
     * einer Exception, haette also leicht unbemerkt fehlschlagen koennen.
     */
    private fun writeToDisk(list: List<Station>) {
        val array = JSONArray()
        list.forEach { station ->
            array.put(
                JSONObject().apply {
                    put("id", station.id)
                    put("name", station.name)
                    put("url", station.url)
                    put("category", station.category)
                    put("enabled", station.enabled)
                }
            )
        }
        val tempFile = File(stationsFile.parentFile, "$STATIONS_FILE_NAME.tmp")
        tempFile.writeText(array.toString(2))
        Files.move(
            tempFile.toPath(),
            stationsFile.toPath(),
            StandardCopyOption.ATOMIC_MOVE,
            StandardCopyOption.REPLACE_EXISTING,
        )
    }
}
