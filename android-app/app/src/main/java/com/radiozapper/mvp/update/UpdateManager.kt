// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

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

// Absichtlich NICHT hartcodiert: der Wert liegt in SharedPreferences (siehe
// getBaseUrl()/setBaseUrl() unten), per Textfeld in der UI aenderbar, ohne
// Rebuild - nur der DEFAULT ist fest im Code. Seit 2026-08-08 der oeffentliche
// Server unter blarks.de (vorher: private Tailscale-Adresse eines einzelnen
// Hosts, siehe SESSION.md) - kein VPN mehr noetig, damit koennte die APK
// jetzt auch tatsaechlich an andere weitergegeben werden, ohne dass die den
// Default erst umstellen muessten. Seit 2026-08-12 unter dem neuen Unterordner
// https://blarks.de/radio/update (identischer Inhalt wie zuvor unter
// /update_radiosabbelnich, siehe SESSION.md) - Geraete mit bereits
// gespeichertem Wert im Textfeld bleiben davon unberuehrt, nur der Default
// fuer neue/unveraenderte Installationen wechselt.
private const val DEFAULT_UPDATE_BASE_URL = "https://blarks.de/radio/update"
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

    // Von checkForUpdate() befuellt, von downloadUpdate() gelesen: seit dem
    // Umzug auf blarks.de bekommt jede hochgeladene APK einen eigenen,
    // zeitgestempelten Dateinamen (radiosabbelnich-YYYYMMDD-HHMMSS.apk,
    // siehe README "Update-Mechanismus") statt eines fest ueberschriebenen
    // "radiosabbelnich.apk" - der tatsaechliche Name steht deshalb nur noch
    // in version.json, nicht mehr fest im Code.
    private var remoteApkFileName: String? = null

    fun getBaseUrl(): String = prefs.getString(PREF_BASE_URL, null) ?: DEFAULT_UPDATE_BASE_URL

    fun setBaseUrl(url: String) {
        prefs.edit().putString(PREF_BASE_URL, url.trim().removeSuffix("/")).apply()
    }

    suspend fun checkForUpdate() {
        _state.value = UpdateState.Checking
        withContext(Dispatchers.IO) {
            try {
                val remote = fetchRemoteVersion()
                remoteApkFileName = remote.apkFile
                _state.value = if (remote.buildTime == BuildConfig.BUILD_TIME) {
                    UpdateState.UpToDate
                } else {
                    UpdateState.Available(remote.buildTime)
                }
            } catch (e: Exception) {
                _state.value = UpdateState.Error(e.message ?: e.toString())
            }
        }
    }

    private data class RemoteVersion(val buildTime: String, val apkFile: String)

    private fun fetchRemoteVersion(): RemoteVersion {
        val connection = URL("${getBaseUrl()}/version.json").openConnection() as HttpURLConnection
        connection.connectTimeout = 10_000
        connection.readTimeout = 10_000
        val json = connection.inputStream.bufferedReader().use { it.readText() }
        connection.disconnect()
        val obj = JSONObject(json)
        return RemoteVersion(obj.getString("buildTime"), obj.getString("apkFile"))
    }

    suspend fun downloadUpdate() {
        _state.value = UpdateState.Downloading
        withContext(Dispatchers.IO) {
            try {
                // Erfordert einen vorherigen checkForUpdate()-Aufruf (fuellt
                // remoteApkFileName) - fuer den bestehenden UI-Ablauf immer
                // der Fall (Button-Reihenfolge: erst Check, dann Download).
                val fileName = remoteApkFileName
                    ?: throw IllegalStateException("Kein Update-Dateiname bekannt - erst nach Update suchen.")
                val updatesDir = File(context.cacheDir, "updates").apply { mkdirs() }
                val apkFile = File(updatesDir, "radiosabbelnich.apk")
                val connection = URL("${getBaseUrl()}/$fileName").openConnection() as HttpURLConnection
                connection.connectTimeout = 15_000
                connection.readTimeout = 15_000
                // Ohne diese Pruefung landete z.B. eine 404-Fehlerseite als
                // "radiosabbelnich.apk" im Cache und scheiterte erst kommentarlos
                // im System-Installer (Review-Befund 14, siehe SESSION.md).
                if (connection.responseCode !in 200..299) {
                    throw IllegalStateException("Server antwortete mit HTTP ${connection.responseCode}")
                }
                // Der Statuscode allein reicht nicht: ein falscher/veralteter
                // Pfad auf einem Webserver mit Catch-All-Route (z.B. eine SPA-
                // Startseite fuer unbekannte URLs) antwortet ebenfalls mit 200,
                // nur eben mit HTML statt der APK - live so auf blarks.de
                // aufgetreten (aeltere, bereits installierte App-Version fragte
                // noch den alten fest verdrahteten Pfad ab, siehe SESSION.md).
                val contentType = connection.contentType
                if (contentType == null || !contentType.startsWith("application/vnd.android.package-archive")) {
                    throw IllegalStateException(
                        "Server lieferte kein APK zurueck (Content-Type: $contentType) - " +
                            "falsche Update-Server-Adresse oder falscher Dateiname?"
                    )
                }
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
