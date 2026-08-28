// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.songfingerprint

import android.content.Context

private const val PREFS_NAME = "song_recognition_prefs"
private const val PREF_AUDD_TOKEN = "audd_token"
private const val PREF_ENABLED = "cloud_lookup_enabled"

/**
 * AudD-Cloud-Lookup-Einstellungen (Phase 2) -- gleiches SharedPreferences-
 * Muster wie `update/UpdateManager.kt` (dort die Update-Server-URL). Token
 * bewusst NICHT in `stations.json` oder sonst einer Datei, die versehentlich
 * geteilt werden koennte (siehe README/SESSION.md, "Song-Erkennung").
 *
 * `cloudLookupEnabled` ist ein EIGENER Schalter, unabhaengig davon, ob die
 * lokale Song-Erkennung (Phase 1, laeuft immer mit, sobald ein Sender
 * spielt) aktiv ist -- Cloud-Lookups kosten AudD-Kontingent, das lokale
 * Fingerprinting nicht. Default AUS, gleiche Begruendung wie beim
 * Docker-Pendant (`song_recognition.cloud_lookup_enabled`).
 */
object SongRecognitionSettings {
    private fun prefs(context: Context) = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun getAudDToken(context: Context): String? =
        prefs(context).getString(PREF_AUDD_TOKEN, null)?.trim()?.takeIf { it.isNotEmpty() }

    fun setAudDToken(context: Context, token: String) {
        prefs(context).edit().putString(PREF_AUDD_TOKEN, token.trim()).apply()
    }

    fun isCloudLookupEnabled(context: Context): Boolean =
        prefs(context).getBoolean(PREF_ENABLED, false)

    fun setCloudLookupEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(PREF_ENABLED, enabled).apply()
    }
}
