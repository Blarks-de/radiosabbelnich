// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.station

import android.graphics.Typeface
import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.radiozapper.mvp.R
import com.radiozapper.mvp.databinding.ActivityStationManagementBinding
import com.radiozapper.mvp.databinding.DialogEditStationBinding
import com.radiozapper.mvp.databinding.ItemStationManageBinding
import com.radiozapper.mvp.importer.CheckState
import com.radiozapper.mvp.importer.ImportState
import com.radiozapper.mvp.importer.StationImporter
import com.radiozapper.mvp.importer.StationReachabilityChecker
import com.radiozapper.mvp.model.Categories
import com.radiozapper.mvp.model.Station
import com.radiozapper.mvp.model.StationRepository
import kotlinx.coroutines.launch

/**
 * Eigener Bildschirm fuer CRUD auf der persistenten Senderliste (siehe
 * model/StationRepository.kt) - der Hauptschirm bleibt bewusst eine flache
 * Play-Liste, das Gruppieren nach Kategorie ist ausschliesslich hier.
 * Kein RecyclerView (Datenmenge klein, siehe README) - volles
 * removeAllViews()+neu-Aufbauen bei jeder Aenderung, analog zu
 * MainActivity.renderStationRows().
 */
class StationManagementActivity : AppCompatActivity() {

    private lateinit var binding: ActivityStationManagementBinding
    private var unreachableIds: Set<String> = emptySet()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityStationManagementBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.addStationButton.setOnClickListener { showEditDialog(existing = null) }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                StationRepository.stations.collect { renderStations(it) }
            }
        }

        setupImport()
        setupReachabilityCheck()
    }

    /** Vorbelegt mit der gespeicherten/Default-URL (siehe StationImporter.getImportUrl()). */
    private fun setupImport() {
        binding.importUrlInput.setText(StationImporter.getImportUrl(this))

        binding.importButton.setOnClickListener {
            StationImporter.setImportUrl(this, binding.importUrlInput.text.toString())
            binding.importUrlInput.setText(StationImporter.getImportUrl(this))
            lifecycleScope.launch { StationImporter.runImport(applicationContext) }
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                StationImporter.state.collect { state -> renderImportState(state) }
            }
        }
    }

    private fun renderImportState(state: ImportState) {
        binding.importButton.isEnabled = state !is ImportState.Loading
        binding.importStatusText.text = when (state) {
            is ImportState.Idle -> ""
            is ImportState.Loading -> getString(R.string.import_loading)
            is ImportState.Done -> getString(R.string.import_done, state.checked, state.added)
            is ImportState.Error -> getString(R.string.import_error, state.message)
        }
    }

    /** Beschraenkt auf Categories.IMPORTED ("Unsortiert") - siehe StationReachabilityChecker-Klassen-Doc. */
    private fun setupReachabilityCheck() {
        binding.checkUnsortedButton.setOnClickListener {
            lifecycleScope.launch { StationReachabilityChecker.checkCategory(Categories.IMPORTED) }
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                StationReachabilityChecker.state.collect { state -> renderCheckState(state) }
            }
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                StationReachabilityChecker.unreachableIds.collect { ids ->
                    unreachableIds = ids
                    renderStations(StationRepository.stations.value)
                }
            }
        }
    }

    private fun renderCheckState(state: CheckState) {
        binding.checkUnsortedButton.isEnabled = state !is CheckState.Checking
        binding.checkStatusText.text = when (state) {
            is CheckState.Idle -> ""
            is CheckState.Checking -> getString(R.string.check_running, state.checked, state.total)
            is CheckState.Done -> getString(R.string.check_done, state.checked, state.unreachable)
        }
    }

    /**
     * Zeigt auch leere Kategorien (feste Reihenfolge ueber Categories.ALL),
     * damit klar ist, wohin ein neuer Sender einsortiert werden kann - keine
     * Sonderfall-Logik fuer die Anzeige-Reihenfolge noetig, analog zum
     * Vorbild (Docker-Projekt iteriert dort auch einfach ueber CATEGORIES).
     */
    private fun renderStations(stations: List<Station>) {
        binding.categoriesContainer.removeAllViews()
        val byCategory = stations.groupBy { it.category }

        Categories.ALL.forEach { category ->
            val header = TextView(this).apply {
                text = category
                textSize = 18f
                setTypeface(typeface, Typeface.BOLD)
                val padTop = (16 * resources.displayMetrics.density).toInt()
                val padBottom = (8 * resources.displayMetrics.density).toInt()
                setPadding(0, padTop, 0, padBottom)
            }
            binding.categoriesContainer.addView(header)

            val stationsInCategory = (byCategory[category] ?: emptyList()).sortedBy { it.name.lowercase() }
            stationsInCategory.forEach { station ->
                val row = ItemStationManageBinding.inflate(layoutInflater, binding.categoriesContainer, false)
                row.nameText.text = station.name
                // Reihenfolge wichtig: isChecked ZUERST setzen, DANN den
                // Listener anhaengen - sonst wuerde jeder Render-Durchlauf
                // durch das programmatische Setzen selbst einen ungewollten
                // setEnabled()-Aufruf ausloesen.
                row.enabledCheckbox.isChecked = station.enabled
                row.enabledCheckbox.setOnCheckedChangeListener { _, checked ->
                    lifecycleScope.launch { runCatchingToToast { StationRepository.setEnabled(station.id, checked) } }
                }
                row.unreachableBadge.visibility =
                    if (station.id in unreachableIds) android.view.View.VISIBLE else android.view.View.GONE
                row.editButton.setOnClickListener { showEditDialog(existing = station) }
                row.deleteButton.setOnClickListener { confirmDelete(station) }
                binding.categoriesContainer.addView(row.root)
            }
        }
    }

    private fun showEditDialog(existing: Station?) {
        val dialogBinding = DialogEditStationBinding.inflate(layoutInflater)
        dialogBinding.nameInput.setText(existing?.name ?: "")
        dialogBinding.urlInput.setText(existing?.url ?: "")
        dialogBinding.enabledCheckbox.isChecked = existing?.enabled ?: true

        dialogBinding.categorySpinner.adapter =
            ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, Categories.ALL)
        val initialIndex = Categories.ALL.indexOf(existing?.category ?: Categories.DEFAULT).coerceAtLeast(0)
        dialogBinding.categorySpinner.setSelection(initialIndex)

        AlertDialog.Builder(this)
            .setTitle(if (existing == null) R.string.dialog_add_station_title else R.string.dialog_edit_station_title)
            .setView(dialogBinding.root)
            .setPositiveButton(R.string.btn_save) { _, _ ->
                val name = dialogBinding.nameInput.text.toString()
                val url = dialogBinding.urlInput.text.toString()
                val category = Categories.ALL[dialogBinding.categorySpinner.selectedItemPosition]
                val enabled = dialogBinding.enabledCheckbox.isChecked
                lifecycleScope.launch {
                    runCatchingToToast {
                        if (existing == null) {
                            StationRepository.addStation(name, url, category, enabled)
                        } else {
                            StationRepository.updateStation(existing.id, name, url, category, enabled)
                        }
                    }
                }
            }
            .setNegativeButton(R.string.btn_cancel, null)
            .show()
    }

    private fun confirmDelete(station: Station) {
        AlertDialog.Builder(this)
            .setTitle(R.string.delete_station_confirm_title)
            .setMessage(getString(R.string.delete_station_confirm_message, station.name))
            .setPositiveButton(R.string.btn_delete_station) { _, _ ->
                lifecycleScope.launch { runCatchingToToast { StationRepository.deleteStation(station.id) } }
            }
            .setNegativeButton(R.string.btn_cancel, null)
            .show()
    }

    /** Repository-Exceptions (Validierung, Loeschsperre, ...) sind bereits deutsche Klartexte. */
    private suspend fun runCatchingToToast(block: suspend () -> Unit) {
        try {
            block()
        } catch (e: Exception) {
            Toast.makeText(this, getString(R.string.station_action_error, e.message ?: e.toString()), Toast.LENGTH_LONG).show()
        }
    }
}
