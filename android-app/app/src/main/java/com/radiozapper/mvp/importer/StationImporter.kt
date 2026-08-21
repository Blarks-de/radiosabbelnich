// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.importer

import android.content.Context
import com.radiozapper.mvp.model.Categories
import com.radiozapper.mvp.model.StationRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL

// Dieselbe Default-Playlist wie im Docker-Projekt (settings_store.DEFAULTS,
// import_url) - die Kodinerds-Kodi-Radioliste. Bewusst NICHT hartcodiert
// belassen, sondern wie DEFAULT_UPDATE_BASE_URL in UpdateManager.kt nur der
// Startwert, aendbar per Textfeld ohne Rebuild.
private const val DEFAULT_IMPORT_URL = "http://bit.ly/kn-kodi-radio"
private const val PREFS_NAME = "import_prefs"
private const val PREF_IMPORT_URL = "import_url"
private const val FETCH_TIMEOUT_MS = 15_000
private const val USER_AGENT = "RadioSabbelNich-Android"

private val EXTINF_RE = Regex("""^#EXTINF:[^,]*,(.*)$""")

sealed class ImportState {
    data object Idle : ImportState()
    data object Loading : ImportState()
    data class Done(val checked: Int, val added: Int) : ImportState()
    data class Error(val message: String) : ImportState()
}

/**
 * Android-Pendant zu station_import.py (Docker-Projekt) - laedt eine
 * M3U-Playlist, parst sie und uebernimmt neue, noch nicht vorhandene Sender
 * deaktiviert in die Kategorie "Unsortiert" (Categories.ALL.last()).
 *
 * Bewusst OHNE den dortigen 8s-Audiofluss-Check pro Sender beim Import selbst
 * (siehe android-app/CLAUDE.md/README.md, Abschnitt Sender-Import): bei einer
 * Playlist mit hunderten Eintraegen waere das auf dem Handy zu langsam und
 * akkuintensiv, UND unnoetig - importierte Sender landen ohnehin deaktiviert,
 * eine kaputte URL darin ist harmlos bis zur bewussten Aktivierung durch den
 * Nutzer. Die echte Erreichbarkeitspruefung gibt es trotzdem, aber als
 * separater, manuell ausgeloester Schritt - siehe StationReachabilityChecker.
 */
object StationImporter {
    private val _state = MutableStateFlow<ImportState>(ImportState.Idle)
    val state: StateFlow<ImportState> = _state

    fun getImportUrl(context: Context): String =
        prefs(context).getString(PREF_IMPORT_URL, null) ?: DEFAULT_IMPORT_URL

    fun setImportUrl(context: Context, url: String) {
        prefs(context).edit().putString(PREF_IMPORT_URL, url.trim()).apply()
    }

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    suspend fun runImport(context: Context) {
        _state.value = ImportState.Loading
        withContext(Dispatchers.IO) {
            try {
                val text = fetchM3u(getImportUrl(context))
                val entries = parseM3u(text)

                val existing = StationRepository.stations.value
                val existingNames = existing.map { it.name.trim().lowercase() }.toHashSet()
                val existingUrls = existing.map { it.url.trim() }.toHashSet()

                val toAdd = mutableListOf<Pair<String, String>>()
                val seenNames = HashSet<String>()
                val seenUrls = HashSet<String>()
                for (entry in entries) {
                    val nameKey = entry.first.trim().lowercase()
                    val urlKey = entry.second.trim()
                    if (nameKey in existingNames || urlKey in existingUrls) continue
                    if (nameKey in seenNames || urlKey in seenUrls) continue // Duplikat innerhalb der Playlist selbst
                    seenNames += nameKey
                    seenUrls += urlKey
                    toAdd += entry
                }

                val added = StationRepository.bulkAdd(toAdd, category = Categories.IMPORTED, enabled = false)
                _state.value = ImportState.Done(checked = entries.size, added = added.size)
            } catch (e: Exception) {
                _state.value = ImportState.Error(e.message ?: e.toString())
            }
        }
    }

    /**
     * HttpURLConnection folgt Redirects NUR innerhalb desselben Protokolls
     * automatisch - ein http://-Kurzlink (der Default hier: bit.ly/kn-kodi-radio),
     * der auf eine https://-URL umbiegt, wuerde sonst stillschweigend nur die
     * Redirect-Antwortseite selbst liefern statt der eigentlichen Playlist (live
     * beobachtet: 301 http -> https, ohne manuelles Folgen "0 geprueft, 0 neu").
     * Deshalb hier von Hand ueber die Location-Kopfzeile folgen, protokoll-
     * uebergreifend inklusive.
     */
    private fun fetchM3u(url: String, redirectsLeft: Int = 5): String {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.connectTimeout = FETCH_TIMEOUT_MS
        connection.readTimeout = FETCH_TIMEOUT_MS
        connection.instanceFollowRedirects = false
        connection.setRequestProperty("User-Agent", USER_AGENT)

        val responseCode = connection.responseCode
        if (responseCode in 300..399) {
            val location = connection.getHeaderField("Location")
            connection.disconnect()
            require(redirectsLeft > 0 && !location.isNullOrBlank()) { "Zu viele Weiterleitungen beim Laden der Playlist." }
            return fetchM3u(URL(URL(url), location).toString(), redirectsLeft - 1)
        }

        val text = connection.inputStream.bufferedReader().use { it.readText() }
        connection.disconnect()
        return text
    }

    /**
     * Parst #EXTINF-Zeilen (Sendername nach dem letzten Komma) plus die folgende
     * Stream-URL-Zeile - 1:1 dieselbe Logik wie station_import.parse_m3u im
     * Docker-Projekt (Extended-M3U, das Format der Kodinerds-Liste).
     */
    internal fun parseM3u(text: String): List<Pair<String, String>> {
        val entries = mutableListOf<Pair<String, String>>()
        var pendingName: String? = null
        for (rawLine in text.lineSequence()) {
            val line = rawLine.trim()
            if (line.isEmpty()) continue
            if (line.startsWith("#EXTINF")) {
                pendingName = EXTINF_RE.matchEntire(line)?.groupValues?.get(1)?.trim()
            } else if (line.startsWith("#")) {
                continue
            } else {
                if (!pendingName.isNullOrEmpty()) {
                    entries += pendingName to line
                }
                pendingName = null
            }
        }
        return entries
    }
}
