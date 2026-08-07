package com.radiozapper.mvp.vosk

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.zip.ZipInputStream

private const val TAG = "VoskModelManager"

// Gleiches Modell wie im Docker-Projekt (vosk-model-small-de-0.15) - Konsistenzgrund:
// dort bereits gegen echte Sender kalibriert (siehe CLAUDE.md des Docker-Projekts).
private const val MODEL_NAME = "vosk-model-small-de-0.15"
private const val MODEL_URL = "https://alphacephei.com/vosk/models/$MODEL_NAME.zip"

sealed class ModelState {
    data object NotReady : ModelState()
    data class Downloading(val percent: Int) : ModelState()
    data object Unpacking : ModelState()
    data class Ready(val path: String) : ModelState()
    data class Error(val message: String) : ModelState()
}

/**
 * Laedt das deutsche Vosk-Modell beim ersten Start herunter und entpackt es in
 * filesDir - NICHT im APK gebundlet (Vorgabe), weil das Modell mit ~45MB das
 * Debug-APK unnoetig aufblaehen wuerde und Updates des Modells sonst einen
 * neuen APK-Build erfordern wuerden.
 */
class VoskModelManager(private val context: Context) {

    private val modelDir = File(context.filesDir, MODEL_NAME)

    private val _state = MutableStateFlow<ModelState>(
        if (isModelPresent()) ModelState.Ready(modelDir.absolutePath) else ModelState.NotReady
    )
    val state: StateFlow<ModelState> = _state

    fun isModelPresent(): Boolean =
        File(modelDir, "am/final.mdl").exists() && File(modelDir, "conf/model.conf").exists()

    fun modelPathOrNull(): String? = if (isModelPresent()) modelDir.absolutePath else null

    suspend fun downloadAndUnpack() {
        if (isModelPresent()) {
            _state.value = ModelState.Ready(modelDir.absolutePath)
            return
        }
        withContext(Dispatchers.IO) {
            val zipFile = File(context.cacheDir, "$MODEL_NAME.zip")
            try {
                downloadWithProgress(zipFile)
                _state.value = ModelState.Unpacking
                unzip(zipFile, context.filesDir)
                zipFile.delete()
                if (isModelPresent()) {
                    _state.value = ModelState.Ready(modelDir.absolutePath)
                } else {
                    _state.value = ModelState.Error("Entpacktes Modell unvollstaendig")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Modell-Download/Entpacken fehlgeschlagen", e)
                zipFile.delete()
                modelDir.deleteRecursively()
                _state.value = ModelState.Error(e.message ?: e.toString())
            }
        }
    }

    private fun downloadWithProgress(destination: File) {
        val connection = URL(MODEL_URL).openConnection() as HttpURLConnection
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
                            _state.value = ModelState.Downloading(percent)
                        }
                    }
                }
            }
        }
        connection.disconnect()
    }

    /**
     * Das Zip enthaelt ein Wurzelverzeichnis "$MODEL_NAME/..." - wird 1:1 nach
     * filesDir entpackt, damit modelDir danach direkt stimmt.
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
