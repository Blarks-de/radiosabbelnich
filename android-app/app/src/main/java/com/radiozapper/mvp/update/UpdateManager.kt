package com.radiozapper.mvp.update

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import com.radiozapper.mvp.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

// Absichtlich NICHT hartcodiert: solange nur der Entwickler selbst testet,
// ist die eigene Tailscale-Adresse ein sinnvoller Default - sobald die App
// aber an andere weitergegeben wird, hat niemand sonst Zugriff auf dieses
// Tailscale-Netz bzw. will nicht den eigenen PC als Update-Server betreiben.
// Der Wert liegt deshalb in SharedPreferences (siehe getBaseUrl()/
// setBaseUrl() unten), per Textfeld in der UI aenderbar, ohne Rebuild - nur
// der DEFAULT (aktueller Entwicklungsstand: Tailscale) ist fest im Code.
// Geplant: sobald der oeffentliche Server unter https://blarks.de steht,
// wird DAS der neue Default (siehe SESSION.md) - bis dahin bewusst noch
// Tailscale, weil das der einzige tatsaechlich existierende Server ist.
private const val DEFAULT_UPDATE_BASE_URL = "http://dockfish.icefish-ghost.ts.net:8098"
private const val PREFS_NAME = "update_prefs"
private const val PREF_BASE_URL = "base_url"

sealed class UpdateState {
    data object Idle : UpdateState()
    data object Checking : UpdateState()
    data object UpToDate : UpdateState()
    data class Available(val buildTime: String) : UpdateState()
    data object Downloading : UpdateState()
    data class ReadyToInstall(val apkFile: File) : UpdateState()
    data class Error(val message: String) : UpdateState()
}

/**
 * Vergleicht nur den Build-Zeitstempel als simplen String (kein Datums-
 * Vergleich, keine Versionsnummer) - bewusst minimal: unterscheidet
 * zuverlaessig "anderer Stand" von "identisch", mehr wird hier nicht
 * gebraucht. Ein Rollback auf einen aelteren Server-Build wuerde ebenfalls
 * als "Update verfuegbar" angezeigt - fuer den Eigenbedarf akzeptabel.
 */
class UpdateManager(private val context: Context) {
    private val _state = MutableStateFlow<UpdateState>(UpdateState.Idle)
    val state: StateFlow<UpdateState> = _state

    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun getBaseUrl(): String = prefs.getString(PREF_BASE_URL, null) ?: DEFAULT_UPDATE_BASE_URL

    fun setBaseUrl(url: String) {
        prefs.edit().putString(PREF_BASE_URL, url.trim().removeSuffix("/")).apply()
    }

    suspend fun checkForUpdate() {
        _state.value = UpdateState.Checking
        withContext(Dispatchers.IO) {
            try {
                val remoteBuildTime = fetchRemoteBuildTime()
                _state.value = if (remoteBuildTime == BuildConfig.BUILD_TIME) {
                    UpdateState.UpToDate
                } else {
                    UpdateState.Available(remoteBuildTime)
                }
            } catch (e: Exception) {
                _state.value = UpdateState.Error(e.message ?: e.toString())
            }
        }
    }

    private fun fetchRemoteBuildTime(): String {
        val connection = URL("${getBaseUrl()}/version.json").openConnection() as HttpURLConnection
        connection.connectTimeout = 10_000
        connection.readTimeout = 10_000
        val json = connection.inputStream.bufferedReader().use { it.readText() }
        connection.disconnect()
        return JSONObject(json).getString("buildTime")
    }

    suspend fun downloadUpdate() {
        _state.value = UpdateState.Downloading
        withContext(Dispatchers.IO) {
            try {
                val updatesDir = File(context.cacheDir, "updates").apply { mkdirs() }
                val apkFile = File(updatesDir, "radiozapper.apk")
                val connection = URL("${getBaseUrl()}/radiozapper.apk").openConnection() as HttpURLConnection
                connection.connectTimeout = 15_000
                connection.readTimeout = 15_000
                connection.inputStream.use { input ->
                    apkFile.outputStream().use { output -> input.copyTo(output) }
                }
                connection.disconnect()
                _state.value = UpdateState.ReadyToInstall(apkFile)
            } catch (e: Exception) {
                _state.value = UpdateState.Error(e.message ?: e.toString())
            }
        }
    }

    fun installIntentFor(apkFile: File): Intent {
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", apkFile)
        return Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
    }
}
