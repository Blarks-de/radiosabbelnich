package com.radiozapper.mvp

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.radiozapper.mvp.databinding.ActivityMainBinding
import com.radiozapper.mvp.databinding.ItemStationBinding
import com.radiozapper.mvp.model.Station
import com.radiozapper.mvp.model.Stations
import com.radiozapper.mvp.playback.PlaybackService
import com.radiozapper.mvp.playback.PlaybackStatus
import com.radiozapper.mvp.vosk.ModelState
import com.radiozapper.mvp.vosk.VoskModelManager
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var modelManager: VoskModelManager

    private var playbackService: PlaybackService? = null
    private var serviceBound = false
    private var pendingStation: Station? = null
    private var statusJob: Job? = null
    private var currentStationJob: Job? = null

    private val requestNotificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* Ablehnung: Notification bleibt einfach aus */ }

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val service = (binder as PlaybackService.LocalBinder).service
            playbackService = service
            serviceBound = true

            pendingStation?.let { station ->
                startPlayback(service, station)
                pendingStation = null
            }

            statusJob?.cancel()
            statusJob = lifecycleScope.launch {
                service.status.collect { status -> renderStatus(status) }
            }

            // Beobachtet den Sender direkt vom Service statt ihn nur beim eigenen
            // Play-Klick lokal zu setzen - sonst wuerde die Anzeige beim
            // automatischen Umschalten (siehe PlaybackService) auf dem alten
            // Sendernamen stehenbleiben.
            currentStationJob?.cancel()
            currentStationJob = lifecycleScope.launch {
                service.currentStation.collect { station ->
                    binding.currentStationText.text = station?.name ?: getString(R.string.current_station_none)
                }
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            playbackService = null
            serviceBound = false
            statusJob?.cancel()
            currentStationJob?.cancel()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Zeigt sichtbar, welcher Build installiert ist (siehe BuildConfig.BUILD_TIME-
        // Kommentar in app/build.gradle.kts) - sonst nicht von aussen unterscheidbar,
        // ob z.B. der Auto-Switch-Fix schon installiert ist oder nur noch eine
        // aeltere APK laeuft.
        binding.buildTimeText.text = getString(R.string.build_time, BuildConfig.BUILD_TIME)

        modelManager = VoskModelManager(applicationContext)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestNotificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        setupStationRows()
        observeModelState()

        binding.downloadModelButton.setOnClickListener {
            lifecycleScope.launch { modelManager.downloadAndUnpack() }
        }

        binding.stopButton.setOnClickListener {
            // Sender-/Statusanzeige aktualisiert sich selbst ueber die
            // currentStation-/status-Flows des Service (siehe onServiceConnected).
            playbackService?.stopPlayback()
        }
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
        statusJob?.cancel()
        currentStationJob?.cancel()
    }

    private fun setupStationRows() {
        Stations.ALL.forEach { station ->
            val row = ItemStationBinding.inflate(layoutInflater, binding.stationsContainer, false)
            row.stationNameText.text = station.name
            row.playButton.setOnClickListener { onPlayClicked(station) }
            binding.stationsContainer.addView(row.root)
        }
    }

    private fun onPlayClicked(station: Station) {
        ContextCompat.startForegroundService(this, Intent(this, PlaybackService::class.java))
        val service = playbackService
        if (service != null) {
            startPlayback(service, station)
        } else {
            // Bindung laeuft noch (App-Start) - onServiceConnected holt das nach.
            pendingStation = station
        }
    }

    private fun startPlayback(service: PlaybackService, station: Station) {
        service.play(station, modelManager.modelPathOrNull())
    }

    private fun renderStatus(status: PlaybackStatus) {
        binding.statusText.text = when (status) {
            PlaybackStatus.IDLE -> getString(R.string.status_idle)
            PlaybackStatus.CONNECTING -> getString(R.string.status_connecting)
            PlaybackStatus.SPEECH -> getString(R.string.status_speech)
            PlaybackStatus.MUSIC -> getString(R.string.status_music)
            PlaybackStatus.ERROR -> getString(R.string.status_error)
        }
    }

    private fun observeModelState() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                modelManager.state.collect { state -> renderModelState(state) }
            }
        }
    }

    private fun renderModelState(state: ModelState) {
        when (state) {
            is ModelState.NotReady -> {
                binding.modelStatusText.text = getString(R.string.model_not_ready)
                binding.modelProgressBar.visibility = android.view.View.GONE
                binding.downloadModelButton.visibility = android.view.View.VISIBLE
                binding.downloadModelButton.isEnabled = true
            }

            is ModelState.Downloading -> {
                binding.modelStatusText.text = getString(R.string.model_downloading, state.percent)
                binding.modelProgressBar.visibility = android.view.View.VISIBLE
                binding.modelProgressBar.progress = state.percent
                binding.downloadModelButton.isEnabled = false
            }

            is ModelState.Unpacking -> {
                binding.modelStatusText.text = getString(R.string.model_unpacking)
                binding.downloadModelButton.isEnabled = false
            }

            is ModelState.Ready -> {
                binding.modelStatusText.text = getString(R.string.model_ready)
                binding.modelProgressBar.visibility = android.view.View.GONE
                binding.downloadModelButton.visibility = android.view.View.GONE
            }

            is ModelState.Error -> {
                binding.modelStatusText.text = getString(R.string.model_error, state.message)
                binding.modelProgressBar.visibility = android.view.View.GONE
                binding.downloadModelButton.visibility = android.view.View.VISIBLE
                binding.downloadModelButton.isEnabled = true
            }
        }
    }
}
