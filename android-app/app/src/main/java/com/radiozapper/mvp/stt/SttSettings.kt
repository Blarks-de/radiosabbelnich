// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.stt

import android.content.Context
import org.json.JSONObject

private const val PREFS_NAME = "stt_settings_prefs"
private const val PREF_JSON = "settings_json"

// Gleiches Modell wie im Docker-Projekt (vosk-model-small-de-0.15) - dort
// bereits gegen echte Sender kalibriert (siehe ../../../../../CLAUDE.md des
// Docker-Projekts). Bleibt der Default fuer "de", damit bestehende
// Installationen (Modell schon heruntergeladen) ohne Zutun weiterlaufen -
// VoskModelManager.kt leitet den Modell-Ordnernamen aus dieser URL ab, der
// bleibt fuer "de" dadurch unveraendert (kein Migrationsschritt noetig).
const val DEFAULT_DE_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip"
private const val DEFAULT_RATIO_SPEECH = 0.65
private const val DEFAULT_RATIO_MUSIC = 0.30
private const val DEFAULT_LANGUAGE = "de"

data class LanguageConfig(
    val modelUrl: String,
    val ratioToConfirmSpeech: Double = DEFAULT_RATIO_SPEECH,
    val ratioToConfirmMusic: Double = DEFAULT_RATIO_MUSIC,
)

/**
 * Mehrsprachigkeit fuers STT (Phase 7, Schritt 1) - Kategorie -> Sprache,
 * nicht Sender -> Sprache (Vorbild stt_filter.py/settings_store.py im
 * Docker-Projekt, Kernentscheidung siehe dessen SESSION.md "Mehrsprachige
 * STT-Erkennung (Teil 1a)"). Freitext-Sprachliste statt fester Auswahl -
 * Nutzer traegt Sprachcode + Vosk-Modell-Download-URL selbst ein, analog zur
 * dortigen Freitext-Entscheidung (dort Modellpfad statt -URL, weil der
 * Docker-Container auf einen Host-Mount angewiesen ist - Android laedt
 * dagegen selbst herunter, siehe VoskModelManager.kt).
 *
 * `ratioToConfirmSpeech`/`ratioToConfirmMusic` sind die bisher in
 * StreamAnalyzer.kt global hartkodierten Hysterese-Schwellen, jetzt pro
 * Sprache konfigurierbar - Speicherstruktur fuer den in Phase 7 Schritt 2
 * geplanten Kalibrierungs-Wizard, der genau diese beiden Werte pro Sprache
 * vorschlagen wird (siehe Fahrplan). Defaults entsprechen exakt den
 * bisherigen globalen Konstanten, bis eine Sprache kalibriert wurde.
 */
object SttSettings {
    private fun prefs(context: Context) = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    private fun defaultState(): JSONObject = JSONObject().apply {
        put(
            "languages",
            JSONObject().apply {
                put(
                    DEFAULT_LANGUAGE,
                    JSONObject().apply {
                        put("modelUrl", DEFAULT_DE_MODEL_URL)
                        put("ratioToConfirmSpeech", DEFAULT_RATIO_SPEECH)
                        put("ratioToConfirmMusic", DEFAULT_RATIO_MUSIC)
                    },
                )
            },
        )
        put("categoryLanguages", JSONObject())
    }

    private fun load(context: Context): JSONObject {
        val raw = prefs(context).getString(PREF_JSON, null) ?: return defaultState()
        return try {
            JSONObject(raw)
        } catch (e: Exception) {
            defaultState()
        }
    }

    private fun save(context: Context, root: JSONObject) {
        prefs(context).edit().putString(PREF_JSON, root.toString()).apply()
    }

    fun getLanguages(context: Context): Map<String, LanguageConfig> {
        val languagesJson = load(context).optJSONObject("languages") ?: JSONObject()
        val result = LinkedHashMap<String, LanguageConfig>()
        for (code in languagesJson.keys()) {
            val obj = languagesJson.getJSONObject(code)
            result[code] = LanguageConfig(
                modelUrl = obj.getString("modelUrl"),
                ratioToConfirmSpeech = obj.optDouble("ratioToConfirmSpeech", DEFAULT_RATIO_SPEECH),
                ratioToConfirmMusic = obj.optDouble("ratioToConfirmMusic", DEFAULT_RATIO_MUSIC),
            )
        }
        return result
    }

    fun addOrUpdateLanguage(context: Context, code: String, cfg: LanguageConfig) {
        val root = load(context)
        val languagesJson = root.optJSONObject("languages") ?: JSONObject().also { root.put("languages", it) }
        languagesJson.put(
            code,
            JSONObject().apply {
                put("modelUrl", cfg.modelUrl)
                put("ratioToConfirmSpeech", cfg.ratioToConfirmSpeech)
                put("ratioToConfirmMusic", cfg.ratioToConfirmMusic)
            },
        )
        save(context, root)
    }

    /**
     * Wirft `IllegalStateException`, falls `code` die letzte verbleibende
     * Sprache ist - analog zur Loeschsperre bei Sendern (mindestens einer
     * muss bleiben, siehe `StationRepository.deleteStation()`). Raeumt
     * verwaiste `categoryLanguages`-Eintraege mit auf (Pendant zu
     * `delete_stt_language()` im Docker-Projekt).
     */
    fun deleteLanguage(context: Context, code: String) {
        val root = load(context)
        val languagesJson = root.optJSONObject("languages") ?: JSONObject()
        if (languagesJson.length() <= 1) {
            throw IllegalStateException("Mindestens eine Sprache muss konfiguriert bleiben.")
        }
        languagesJson.remove(code)

        val categoryLanguagesJson = root.optJSONObject("categoryLanguages") ?: JSONObject()
        val orphaned = categoryLanguagesJson.keys().asSequence()
            .filter { categoryLanguagesJson.getString(it) == code }
            .toList()
        orphaned.forEach { categoryLanguagesJson.remove(it) }

        save(context, root)
    }

    fun getCategoryLanguages(context: Context): Map<String, String> {
        val categoryLanguagesJson = load(context).optJSONObject("categoryLanguages") ?: JSONObject()
        val result = LinkedHashMap<String, String>()
        for (category in categoryLanguagesJson.keys()) {
            result[category] = categoryLanguagesJson.getString(category)
        }
        return result
    }

    fun setCategoryLanguage(context: Context, category: String, code: String) {
        val root = load(context)
        val categoryLanguagesJson = root.optJSONObject("categoryLanguages")
            ?: JSONObject().also { root.put("categoryLanguages", it) }
        categoryLanguagesJson.put(category, code)
        save(context, root)
    }

    /**
     * Kategorie -> Sprachcode (analog `resolve_stt_language()` im
     * Docker-Projekt). Fehlender Eintrag -> `"de"`, aber nur solange "de"
     * ueberhaupt noch konfiguriert ist: sonst die erste konfigurierte Sprache.
     * Vorher fuehrte das Loeschen von "de" dazu, dass jede nicht explizit
     * zugeordnete Kategorie dauerhaft "kein Modell" meldete und die Analyse
     * still ausblieb, obwohl Sprachen vorhanden waren (Review-Befund 12,
     * siehe SESSION.md).
     */
    fun resolveLanguage(context: Context, category: String): String {
        val configured = getLanguages(context)
        getCategoryLanguages(context)[category]?.let { if (it in configured) return it }
        if (DEFAULT_LANGUAGE in configured) return DEFAULT_LANGUAGE
        return configured.keys.firstOrNull() ?: DEFAULT_LANGUAGE
    }
}
