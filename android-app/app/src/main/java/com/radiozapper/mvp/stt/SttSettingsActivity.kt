// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.stt

import android.content.Intent
import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.AdapterView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.radiozapper.mvp.R
import com.radiozapper.mvp.databinding.ActivitySttSettingsBinding
import com.radiozapper.mvp.databinding.DialogAddSttLanguageBinding
import com.radiozapper.mvp.databinding.ItemSttCategoryBinding
import com.radiozapper.mvp.databinding.ItemSttLanguageBinding
import com.radiozapper.mvp.model.Categories
import com.radiozapper.mvp.vosk.ModelState
import com.radiozapper.mvp.vosk.VoskModelManager
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * Eigener Bildschirm fuer Mehrsprachigkeit beim STT-Sprachfilter (Phase 7,
 * Schritt 1 - siehe stt/SttSettings.kt fuer die Speicherstruktur, deren
 * Klassen-Doc auch die Architektur-Entscheidung "Kategorie -> Sprache, nicht
 * Sender -> Sprache" begruendet). Analog zu StationManagementActivity: kein
 * RecyclerView (kleine Datenmenge), volles removeAllViews()+neu-Aufbauen bei
 * jeder relevanten Aenderung.
 *
 * Zwei unabhaengige Abschnitte, beide muessen bei einer Sprachliste-Aenderung
 * (hinzugefuegt/geloescht) neu gerendert werden - der Kategorie-Abschnitt
 * zeigt schliesslich genau diese Sprachen als Spinner-Optionen.
 */
class SttSettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySttSettingsBinding

    // Ein Sprachstatus-Collector pro sichtbarer Sprachzeile - muss bei jedem
    // Neuaufbau der Zeilen (renderLanguages()) explizit beendet werden, sonst
    // sammeln sich bei wiederholtem Hinzufuegen/Loeschen tote Collector-Jobs
    // auf laengst entfernten Views an.
    private val languageStatusJobs = mutableListOf<Job>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySttSettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.addLanguageButton.setOnClickListener { showAddLanguageDialog() }

        refreshAll()
    }

    private fun refreshAll() {
        renderLanguages()
        renderCategories()
    }

    private fun renderLanguages() {
        languageStatusJobs.forEach { it.cancel() }
        languageStatusJobs.clear()
        binding.languagesContainer.removeAllViews()

        val languages = SttSettings.getLanguages(this)
        languages.forEach { (code, cfg) ->
            val row = ItemSttLanguageBinding.inflate(layoutInflater, binding.languagesContainer, false)
            row.codeText.text = code

            languageStatusJobs += lifecycleScope.launch {
                repeatOnLifecycle(Lifecycle.State.STARTED) {
                    VoskModelManager.state(this@SttSettingsActivity, code, cfg.modelUrl).collect { state ->
                        renderLanguageState(row, state)
                    }
                }
            }

            row.downloadButton.setOnClickListener {
                lifecycleScope.launch { VoskModelManager.downloadAndUnpack(this@SttSettingsActivity, code, cfg.modelUrl) }
            }
            row.deleteButton.setOnClickListener { confirmDeleteLanguage(code) }
            row.calibrateButton.setOnClickListener {
                startActivity(Intent(this, CalibrationActivity::class.java).putExtra(CalibrationActivity.EXTRA_LANGUAGE, code))
            }
            // Mindestens eine Sprache muss konfiguriert bleiben (analog Sender-
            // Loeschsperre) - hier schon im UI gesperrt statt erst nach einem
            // fehlgeschlagenen Versuch mit Toast zu reagieren.
            row.deleteButton.isEnabled = languages.size > 1

            binding.languagesContainer.addView(row.root)
        }
    }

    private fun renderLanguageState(row: ItemSttLanguageBinding, state: ModelState) {
        when (state) {
            is ModelState.NotReady -> {
                row.statusText.text = getString(R.string.model_not_ready)
                row.progressBar.visibility = android.view.View.GONE
                row.downloadButton.visibility = android.view.View.VISIBLE
                row.downloadButton.isEnabled = true
                row.calibrateButton.visibility = android.view.View.GONE
            }

            is ModelState.Downloading -> {
                row.statusText.text = getString(R.string.model_downloading, state.percent)
                row.progressBar.visibility = android.view.View.VISIBLE
                row.progressBar.progress = state.percent
                row.downloadButton.isEnabled = false
                row.calibrateButton.visibility = android.view.View.GONE
            }

            is ModelState.Unpacking -> {
                row.statusText.text = getString(R.string.model_unpacking)
                row.downloadButton.isEnabled = false
                row.calibrateButton.visibility = android.view.View.GONE
            }

            is ModelState.Ready -> {
                row.statusText.text = getString(R.string.model_ready)
                row.progressBar.visibility = android.view.View.GONE
                row.downloadButton.visibility = android.view.View.GONE
                // Kalibrieren braucht ein geladenes Modell fuer echte Samples -
                // ohne heruntergeladenes Modell wuerde play() ohnehin nur
                // sttModelMissing setzen und die Analyse pausiert lassen.
                row.calibrateButton.visibility = android.view.View.VISIBLE
            }

            is ModelState.Error -> {
                row.statusText.text = getString(R.string.model_error, state.message)
                row.progressBar.visibility = android.view.View.GONE
                row.downloadButton.visibility = android.view.View.VISIBLE
                row.downloadButton.isEnabled = true
                row.calibrateButton.visibility = android.view.View.GONE
            }
        }
    }

    /**
     * Ein Spinner pro fester Kategorie (Categories.ALL bleibt unangetastet,
     * siehe SttSettings-Klassen-Doc), vorbelegt mit dem aktuell aufgeloesten
     * Wert (resolveLanguage() - fehlender Eintrag zeigt "de", auch wenn noch
     * nichts explizit gespeichert wurde).
     */
    private fun renderCategories() {
        binding.categoriesContainer.removeAllViews()
        val languageCodes = SttSettings.getLanguages(this).keys.toList()
        if (languageCodes.isEmpty()) return

        Categories.ALL.forEach { category ->
            val row = ItemSttCategoryBinding.inflate(layoutInflater, binding.categoriesContainer, false)
            row.categoryNameText.text = category
            row.languageSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, languageCodes)

            val resolved = SttSettings.resolveLanguage(this, category)
            val initialIndex = languageCodes.indexOf(resolved).coerceAtLeast(0)
            row.languageSpinner.setSelection(initialIndex, false)

            // onItemSelected feuert beim Setzen der initialen Selektion oben
            // NICHT rueckwirkend erneut (Listener wird ja erst danach
            // angehaengt) - kein Schutz-Flag noetig, anders als z.B. bei
            // CheckBox.setOnCheckedChangeListener in StationManagementActivity.
            row.languageSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: AdapterView<*>?, view: android.view.View?, position: Int, id: Long) {
                    SttSettings.setCategoryLanguage(this@SttSettingsActivity, category, languageCodes[position])
                }
                override fun onNothingSelected(parent: AdapterView<*>?) = Unit
            }

            binding.categoriesContainer.addView(row.root)
        }
    }

    private fun showAddLanguageDialog() {
        val dialogBinding = DialogAddSttLanguageBinding.inflate(layoutInflater)

        AlertDialog.Builder(this)
            .setTitle(R.string.dialog_add_language_title)
            .setView(dialogBinding.root)
            .setPositiveButton(R.string.btn_save) { _, _ ->
                val code = dialogBinding.codeInput.text.toString().trim()
                val modelUrl = dialogBinding.modelUrlInput.text.toString().trim()
                if (code.isEmpty() || modelUrl.isEmpty()) {
                    Toast.makeText(this, R.string.language_action_error_empty, Toast.LENGTH_LONG).show()
                    return@setPositiveButton
                }
                // Existiert der Code bereits (das ist zugleich die einzige
                // Moeglichkeit, eine Modell-URL zu aendern), nur die URL
                // ersetzen: ein frisches LanguageConfig wuerde sonst die
                // kalibrierten Schwellen dieser Sprache stillschweigend auf
                // die Defaults zuruecksetzen (Review-Befund 10, siehe
                // SESSION.md).
                val existing = SttSettings.getLanguages(this)[code]
                val cfg = existing?.copy(modelUrl = modelUrl) ?: LanguageConfig(modelUrl = modelUrl)
                SttSettings.addOrUpdateLanguage(this, code, cfg)
                refreshAll()
            }
            .setNegativeButton(R.string.btn_cancel, null)
            .show()
    }

    private fun confirmDeleteLanguage(code: String) {
        AlertDialog.Builder(this)
            .setTitle(R.string.delete_language_confirm_title)
            .setMessage(getString(R.string.delete_language_confirm_message, code))
            .setPositiveButton(R.string.btn_delete_language) { _, _ ->
                val modelUrl = SttSettings.getLanguages(this)[code]?.modelUrl
                try {
                    SttSettings.deleteLanguage(this, code)
                    // Der Modell-Ordner wird aus der URL abgeleitet (siehe
                    // VoskModelManager), zwei Sprachen mit derselben URL teilen
                    // sich also die Dateien - dann darf das Loeschen der einen
                    // der anderen nicht das Modell wegnehmen (beim Aufraeumen
                    // des Phase-8-Testaufbaus aufgefallen).
                    val stillUsed = SttSettings.getLanguages(this).values.any { it.modelUrl == modelUrl }
                    if (modelUrl != null && !stillUsed) VoskModelManager.deleteModel(this, code, modelUrl)
                    refreshAll()
                } catch (e: IllegalStateException) {
                    Toast.makeText(this, e.message, Toast.LENGTH_LONG).show()
                }
            }
            .setNegativeButton(R.string.btn_cancel, null)
            .show()
    }
}
