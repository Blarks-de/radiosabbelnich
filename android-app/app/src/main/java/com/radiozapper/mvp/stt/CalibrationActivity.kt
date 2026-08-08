package com.radiozapper.mvp.stt

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Bundle
import android.os.IBinder
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.radiozapper.mvp.R
import com.radiozapper.mvp.databinding.ActivityCalibrationBinding
import com.radiozapper.mvp.playback.PlaybackService
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * Kalibrierungs-Wizard fuer ratioToConfirmSpeech/ratioToConfirmMusic einer
 * einzelnen Sprache (Phase 7, Schritt 2 - siehe stt/Calibration.kt fuer die
 * Vorschlagsformel, PlaybackService-Klassen-Doc fuer die Session-Steuerung).
 * Bindet sich wie MainActivity an PlaybackService, erzwingt darueber aber
 * zusaetzlich die zu kalibrierende Sprache fuer den gerade laufenden Sender
 * (`startCalibration()`) - Sender koennen waehrenddessen ganz normal ueber
 * die Startseite gewechselt werden, jeder trifft automatisch dieselbe
 * erzwungene Sprache.
 *
 * `activity:screenOrientation="portrait"` im Manifest ist hier bewusst
 * gesetzt: ein Rotations-bedingtes Neuerstellen der Activity wuerde sonst
 * `onDestroy()` ausloesen und damit `stopCalibration()` - ohne den
 * Guard `activeCalibrationLanguage != language` in `onServiceConnected()`
 * kaeme das echten Datenverlust gleich (schon gesammelte Samples verworfen,
 * `startCalibration()` leert die Listen explizit). Mit Portrait-Sperre
 * bleibt `onDestroy()` fuer den Fall reserviert, in dem der Nutzer den
 * Bildschirm tatsaechlich verlassen will (Zurueck, "Fertig"-Button).
 */
class CalibrationActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_LANGUAGE = "language"
    }

    private lateinit var binding: ActivityCalibrationBinding
    private lateinit var language: String

    private var playbackService: PlaybackService? = null
    private var serviceBound = false
    private var currentStationJob: Job? = null
    private var speechRatioJob: Job? = null
    private var levelJob: Job? = null
    private var sampleCountsJob: Job? = null

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val service = (binder as PlaybackService.LocalBinder).service
            playbackService = service
            serviceBound = true
            if (service.activeCalibrationLanguage != language) {
                service.startCalibration(language)
            }

            currentStationJob?.cancel()
            currentStationJob = lifecycleScope.launch {
                service.currentStation.collect { station ->
                    binding.currentStationText.text = station?.name ?: getString(R.string.current_station_none)
                }
            }

            speechRatioJob?.cancel()
            speechRatioJob = lifecycleScope.launch {
                service.speechRatio.collect { ratio -> renderRatio(ratio) }
            }

            levelJob?.cancel()
            levelJob = lifecycleScope.launch {
                service.calibrationLevel.collect { level -> renderLevel(level) }
            }

            sampleCountsJob?.cancel()
            sampleCountsJob = lifecycleScope.launch {
                service.calibrationSampleCounts.collect { counts ->
                    renderSampleCounts(counts)
                    // Wird bei jeder Sample-Aenderung frisch berechnet statt
                    // zwischengespeichert - analog zum Docker-Wizard, der den
                    // Vorschlag bei jedem Status-Poll neu berechnet.
                    renderSuggestion(service.calibrationSuggestion())
                }
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            playbackService = null
            serviceBound = false
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val extraLanguage = intent.getStringExtra(EXTRA_LANGUAGE)
        if (extraLanguage == null) {
            finish()
            return
        }
        language = extraLanguage
        binding = ActivityCalibrationBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.titleText.text = getString(R.string.calibration_title, language)

        binding.speechLevelButton.setOnClickListener { toggleLevel(CalibrationLevel.SPEECH) }
        binding.musicLevelButton.setOnClickListener { toggleLevel(CalibrationLevel.MUSIC) }
        binding.applyButton.setOnClickListener { applySuggestion() }
        binding.doneButton.setOnClickListener { finish() }
    }

    override fun onStart() {
        super.onStart()
        bindService(Intent(this, PlaybackService::class.java), connection, Context.BIND_AUTO_CREATE)
    }

    override fun onStop() {
        super.onStop()
        if (serviceBound) {
            unbindService(connection)
            serviceBound = false
        }
        currentStationJob?.cancel()
        speechRatioJob?.cancel()
        levelJob?.cancel()
        sampleCountsJob?.cancel()
    }

    override fun onDestroy() {
        // Endgueltiges Verlassen des Bildschirms (siehe Klassen-Doc) - erst
        // hier die erzwungene Sprache wieder freigeben, der laufende Sender
        // faellt zurueck auf die normale Kategorie-Aufloesung.
        playbackService?.stopCalibration()
        super.onDestroy()
    }

    private fun toggleLevel(level: CalibrationLevel) {
        val service = playbackService ?: return
        val current = service.calibrationLevel.value
        service.setCalibrationLevel(if (current == level) null else level)
    }

    private fun renderRatio(ratio: Double?) {
        if (ratio == null) {
            binding.ratioBar.progress = 0
            binding.ratioPercentText.text = getString(R.string.bs_meter_off)
            return
        }
        val pct = (ratio * 100).toInt().coerceIn(0, 100)
        binding.ratioBar.progress = pct
        binding.ratioPercentText.text = getString(R.string.bs_meter_pct, pct)
    }

    private fun renderLevel(level: CalibrationLevel?) {
        binding.levelStatusText.text = when (level) {
            CalibrationLevel.SPEECH -> getString(R.string.calibration_level_speech)
            CalibrationLevel.MUSIC -> getString(R.string.calibration_level_music)
            null -> getString(R.string.calibration_level_none)
        }
    }

    private fun renderSampleCounts(counts: Pair<Int, Int>) {
        binding.sampleCountsText.text = getString(R.string.calibration_sample_counts, counts.first, counts.second)
    }

    private fun renderSuggestion(suggestion: CalibrationSuggestion?) {
        when {
            suggestion == null -> {
                binding.suggestionText.text = getString(R.string.calibration_suggestion_none)
                binding.applyButton.isEnabled = false
            }
            suggestion.overlapping -> {
                binding.suggestionText.text = getString(R.string.calibration_suggestion_overlapping)
                binding.applyButton.isEnabled = false
            }
            else -> {
                binding.suggestionText.text = getString(
                    R.string.calibration_suggestion,
                    suggestion.ratioToConfirmSpeech,
                    suggestion.ratioToConfirmMusic,
                )
                binding.applyButton.isEnabled = true
            }
        }
    }

    private fun applySuggestion() {
        val applied = playbackService?.applyCalibrationSuggestion() ?: false
        Toast.makeText(
            this,
            if (applied) R.string.calibration_applied else R.string.calibration_apply_failed,
            Toast.LENGTH_SHORT,
        ).show()
    }
}
