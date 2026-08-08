package com.radiozapper.mvp.vosk

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ConcurrentHashMap
import java.util.zip.ZipInputStream

private const val TAG = "VoskModelManager"

sealed class ModelState {
    data object NotReady : ModelState()
    data class Downloading(val percent: Int) : ModelState()
    data object Unpacking : ModelState()
    data class Ready(val path: String) : ModelState()
    data class Error(val message: String) : ModelState()
}

/**
 * Laedt Vosk-Sprachmodelle bei Bedarf herunter und entpackt sie in filesDir -
 * NICHT im APK gebundlet, weil ein Modell mit ~40-50MB das Debug-APK unnoetig
 * aufblaehen wuerde und Updates sonst einen neuen APK-Build erfordern
 * wuerden. Seit Phase 7 (Mehrsprachigkeit) ein Singleton statt einer
 * Instanz-Klasse - mehrere Activities (MainActivity, die neue
 * SttSettingsActivity) muessen sich denselben Download-Fortschritt/StateFlow
 * teilen, sonst saehe eine Activity einen Download nicht, der gerade in der
 * anderen laeuft (gleiche Begruendung wie bei `StationRepository`s
 * Singleton-Status).
 *
 * Der Modell-Ordnername wird aus dem Dateinamen der Download-URL abgeleitet
 * (ohne `.zip`) statt sprachcode-basiert vergeben - fuer Deutsch mit der
 * unveraenderten Default-URL (siehe stt/SttSettings.kt) ergibt sich dadurch
 * exakt derselbe Pfad wie vor der Mehrsprachigkeit
 * (`vosk-model-small-de-0.15`), bestehende Installationen mit bereits
 * heruntergeladenem Modell brauchen also KEINEN Migrationsschritt.
 */
object VoskModelManager {

    private val states = ConcurrentHashMap<String, MutableStateFlow<ModelState>>()

    private fun modelDirFor(context: Context, modelUrl: String): File {
        val fileName = modelUrl.substringAfterLast('/')
        val dirName = fileName.removeSuffix(".zip")
        return File(context.filesDir, dirName)
    }

    private fun isModelPresent(modelDir: File): Boolean =
        File(modelDir, "am/final.mdl").exists() && File(modelDir, "conf/model.conf").exists()

    /** Ein StateFlow pro Sprachcode, ueber Activity-Instanzen hinweg geteilt (siehe Klassen-Doc). */
    fun state(context: Context, code: String, modelUrl: String): StateFlow<ModelState> =
        states.getOrPut(code) {
            val dir = modelDirFor(context, modelUrl)
            MutableStateFlow(if (isModelPresent(dir)) ModelState.Ready(dir.absolutePath) else ModelState.NotReady)
        }.asStateFlow()

    // 'code' bewusst Teil der Signatur (API-Symmetrie mit state()/downloadAndUnpack()),
    // aber hier ungenutzt - der Pfad haengt ausschliesslich von modelUrl ab (siehe modelDirFor()).
    @Suppress("UNUSED_PARAMETER")
    fun modelPathOrNull(context: Context, code: String, modelUrl: String): String? {
        val dir = modelDirFor(context, modelUrl)
        return if (isModelPresent(dir)) dir.absolutePath else null
    }

    suspend fun downloadAndUnpack(context: Context, code: String, modelUrl: String) {
        val dir = modelDirFor(context, modelUrl)
        val flow = states.getOrPut(code) { MutableStateFlow(ModelState.NotReady) }
        if (isModelPresent(dir)) {
            flow.value = ModelState.Ready(dir.absolutePath)
            return
        }
        withContext(Dispatchers.IO) {
            val zipFile = File(context.cacheDir, "${dir.name}.zip")
            try {
                downloadWithProgress(modelUrl, zipFile, flow)
                flow.value = ModelState.Unpacking
                unzip(zipFile, context.filesDir)
                zipFile.delete()
                if (isModelPresent(dir)) {
                    flow.value = ModelState.Ready(dir.absolutePath)
                } else {
                    flow.value = ModelState.Error("Entpacktes Modell unvollstaendig")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Modell-Download/Entpacken fehlgeschlagen fuer '$code'", e)
                zipFile.delete()
                dir.deleteRecursively()
                flow.value = ModelState.Error(e.message ?: e.toString())
            }
        }
    }

    /** Loescht die heruntergeladenen Modelldateien - fuer SttSettings.deleteLanguage() (Speicherplatz freigeben, keine verwaisten Dateien). */
    fun deleteModel(context: Context, code: String, modelUrl: String) {
        modelDirFor(context, modelUrl).deleteRecursively()
        states[code]?.value = ModelState.NotReady
    }

    private fun downloadWithProgress(modelUrl: String, destination: File, flow: MutableStateFlow<ModelState>) {
        val connection = URL(modelUrl).openConnection() as HttpURLConnection
        connection.connectTimeout = 15_000
        connection.readTimeout = 15_000
        connection.connect()
        val total = connection.contentLength
        var lastPercent = -1
        connection.inputStream.use { input ->
            destination.outputStream().use { output ->
                val buffer = ByteArray(64 * 1024)
                var readTotal = 0L
                while (true) {
                    val read = input.read(buffer)
                    if (read == -1) break
                    output.write(buffer, 0, read)
                    readTotal += read
                    if (total > 0) {
                        val percent = ((readTotal * 100) / total).toInt()
                        if (percent != lastPercent) {
                            lastPercent = percent
                            flow.value = ModelState.Downloading(percent)
                        }
                    }
                }
            }
        }
        connection.disconnect()
    }

    /**
     * Das Zip enthaelt ein Wurzelverzeichnis "<name>/..." - wird 1:1 nach
     * filesDir entpackt, damit der abgeleitete Modell-Ordnername danach
     * direkt stimmt.
     */
    private fun unzip(zipFile: File, targetDir: File) {
        ZipInputStream(zipFile.inputStream().buffered()).use { zis ->
            var entry = zis.nextEntry
            val buffer = ByteArray(64 * 1024)
            while (entry != null) {
                val outFile = File(targetDir, entry.name)
                if (entry.isDirectory) {
                    outFile.mkdirs()
                } else {
                    outFile.parentFile?.mkdirs()
                    outFile.outputStream().use { out ->
                        while (true) {
                            val read = zis.read(buffer)
                            if (read == -1) break
                            out.write(buffer, 0, read)
                        }
                    }
                }
                zis.closeEntry()
                entry = zis.nextEntry
            }
        }
    }
}
