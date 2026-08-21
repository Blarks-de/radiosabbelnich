package com.radiozapper.mvp.newsbreak

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.documentfile.provider.DocumentFile

private const val TAG = "NewsBreakSettings"
private const val PREFS_NAME = "news_break_prefs"
private const val PREF_ENABLED = "enabled"
private const val PREF_WINDOW_MINUTES = "window_minutes"
private const val PREF_FOLDER_URI = "folder_uri"
private const val PREF_REQUIRE_SPEECH_IN_WINDOW = "require_speech_in_window"
private const val PREF_SPEECH_GATE_WINDOW_MINUTES = "speech_gate_window_minutes"
private const val PREF_SPEECH_GATE_STREAK_SECONDS = "speech_gate_streak_seconds"
private const val PREF_AD_PREBUFFER_ENABLED = "ad_prebuffer_enabled"
private const val PREF_AD_PREBUFFER_LEAD_SECONDS = "ad_prebuffer_lead_seconds"

// Identisch zum Docker-Projekt-Default (settings_store.DEFAULTS["news_break"]).
private const val DEFAULT_WINDOW_MINUTES = 2.0
private const val DEFAULT_SPEECH_GATE_WINDOW_MINUTES = 2.0
private const val DEFAULT_SPEECH_GATE_STREAK_SECONDS = 3.0
private const val DEFAULT_AD_PREBUFFER_LEAD_SECONDS = 20.0

/**
 * Android-spezifischer Teil der Nachrichten-Pause (SAF-Ordnerzugriff +
 * Einstellungen) - bewusst getrennt von der reinen Logik in NewsBreak.kt,
 * analog zur Trennung stations_store.py/settings_store.py vom eigentlichen
 * Hauptloop im Docker-Projekt.
 *
 * SAF (ACTION_OPEN_DOCUMENT_TREE, vom Aufrufer/MainActivity gestartet) statt
 * MediaStore: einzige robuste Möglichkeit unter Scoped Storage (minSdk 26),
 * einen vom Nutzer gewählten Ordner über App-Neustarts hinweg zugänglich zu
 * halten (takePersistableUriPermission()).
 */
object NewsBreakSettings {

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun getConfig(context: Context): NewsBreakConfig {
        val p = prefs(context)
        return NewsBreakConfig(
            enabled = p.getBoolean(PREF_ENABLED, false),
            windowMinutes = p.getFloat(PREF_WINDOW_MINUTES, DEFAULT_WINDOW_MINUTES.toFloat()).toDouble(),
            requireSpeechInWindow = p.getBoolean(PREF_REQUIRE_SPEECH_IN_WINDOW, false),
            speechGateWindowMinutes = p.getFloat(
                PREF_SPEECH_GATE_WINDOW_MINUTES, DEFAULT_SPEECH_GATE_WINDOW_MINUTES.toFloat()
            ).toDouble(),
            speechGateStreakSeconds = p.getFloat(
                PREF_SPEECH_GATE_STREAK_SECONDS, DEFAULT_SPEECH_GATE_STREAK_SECONDS.toFloat()
            ).toDouble(),
            adPrebufferEnabled = p.getBoolean(PREF_AD_PREBUFFER_ENABLED, false),
            adPrebufferLeadSeconds = p.getFloat(
                PREF_AD_PREBUFFER_LEAD_SECONDS, DEFAULT_AD_PREBUFFER_LEAD_SECONDS.toFloat()
            ).toDouble(),
        )
    }

    fun setEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(PREF_ENABLED, enabled).apply()
    }

    fun setWindowMinutes(context: Context, minutes: Double) {
        prefs(context).edit().putFloat(PREF_WINDOW_MINUTES, minutes.toFloat()).apply()
    }

    fun setRequireSpeechInWindow(context: Context, required: Boolean) {
        prefs(context).edit().putBoolean(PREF_REQUIRE_SPEECH_IN_WINDOW, required).apply()
    }

    fun setSpeechGateWindowMinutes(context: Context, minutes: Double) {
        prefs(context).edit().putFloat(PREF_SPEECH_GATE_WINDOW_MINUTES, minutes.toFloat()).apply()
    }

    fun setSpeechGateStreakSeconds(context: Context, seconds: Double) {
        prefs(context).edit().putFloat(PREF_SPEECH_GATE_STREAK_SECONDS, seconds.toFloat()).apply()
    }

    fun setAdPrebufferEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(PREF_AD_PREBUFFER_ENABLED, enabled).apply()
    }

    fun setAdPrebufferLeadSeconds(context: Context, seconds: Double) {
        prefs(context).edit().putFloat(PREF_AD_PREBUFFER_LEAD_SECONDS, seconds.toFloat()).apply()
    }

    fun getFolderUri(context: Context): Uri? =
        prefs(context).getString(PREF_FOLDER_URI, null)?.let { Uri.parse(it) }

    /** Sichert den Zugriff dauerhaft (Persisted URI Permission) - ohne das wäre er nach einem Geräte-Neustart weg. */
    fun setFolderUri(context: Context, uri: Uri) {
        context.contentResolver.takePersistableUriPermission(
            uri,
            Intent.FLAG_GRANT_READ_URI_PERMISSION,
        )
        prefs(context).edit().putString(PREF_FOLDER_URI, uri.toString()).apply()
    }

    /**
     * Alle .mp3-Dateien im gewählten Ordner, oder eine leere Liste (mit
     * Log-Warnung) falls kein Ordner gewählt oder nicht mehr lesbar ist -
     * Pendant zu pick_random_mp3()s Fehlerbehandlung im Docker-Projekt
     * ("Feature still überspringen, ins Log schreiben, normal weiterlaufen").
     */
    fun listMp3s(context: Context): List<DocumentFile> {
        val treeUri = getFolderUri(context) ?: run {
            Log.w(TAG, "Nachrichten-Pause aktiv, aber kein Ordner gewählt - übersprungen.")
            return emptyList()
        }
        val root = DocumentFile.fromTreeUri(context, treeUri)
        if (root == null || !root.isDirectory) {
            Log.w(TAG, "Nachrichten-Pause: Ordner nicht (mehr) lesbar - übersprungen.")
            return emptyList()
        }
        val files = root.listFiles().filter { it.isFile && it.name?.lowercase()?.endsWith(".mp3") == true }
        if (files.isEmpty()) {
            Log.w(TAG, "Nachrichten-Pause: keine .mp3-Dateien im gewählten Ordner - übersprungen.")
        }
        return files
    }
}
