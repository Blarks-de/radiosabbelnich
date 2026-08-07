package com.radiozapper.mvp

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.provider.Settings
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.radiozapper.mvp.databinding.ActivityMainBinding
import com.radiozapper.mvp.databinding.ItemStationBinding
import com.radiozapper.mvp.model.Station
import com.radiozapper.mvp.model.StationRepository
import com.radiozapper.mvp.playback.PlaybackService
import com.radiozapper.mvp.playback.PlaybackStatus
import com.radiozapper.mvp.playback.StationLockReason
import com.radiozapper.mvp.station.StationManagementActivity
import com.radiozapper.mvp.update.UpdateManager
import com.radiozapper.mvp.update.UpdateState
import com.radiozapper.mvp.vosk.ModelState
import com.radiozapper.mvp.vosk.VoskModelManager
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var modelManager: VoskModelManager
    private lateinit var updateManager: UpdateManager

    private var playbackService: PlaybackService? = null
    private var serviceBound = false
    private var pendingStation: Station? = null
    private var statusJob: Job? = null
    private var currentStationJob: Job? = null
    private var lockedStationsJob: Job? = null
    private var lockedStations: Map<String, StationLockReason> = emptyMap()

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

            // Reiner Anstoss fuer einen Re-Render der bestehenden Play-Liste
            // (siehe renderStationRows()) - kein eigener Aufbau hier, sonst
            // gaebe es zwei Stellen, die dieselben Zeilen erzeugen.
            lockedStationsJob?.cancel()
            lockedStationsJob = lifecycleScope.launch {
                service.lockedStations.collect { locked ->
                    lockedStations = locked
                    renderStationRows(StationRepository.stations.value)
                }
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            playbackService = null
            serviceBound = false
            statusJob?.cancel()
            currentStationJob?.cancel()
            lockedStationsJob?.cancel()
            lockedStations = emptyMap()
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
        updateManager = UpdateManager(applicationContext)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestNotificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        observeStations()
        observeModelState()
        observeUpdateState()

        binding.manageStationsButton.setOnClickListener {
            startActivity(Intent(this, StationManagementActivity::class.java))
        }

        // Update-Server-Adresse bewusst NICHT hartcodiert (siehe UpdateManager.kt) -
        // wer die APK weitergibt, hat vermutlich nicht denselben Tailscale-Zugriff
        // wie der Entwickler. Vorbelegt mit dem aktuellen Default/gespeicherten Wert,
        // per Textfeld jederzeit ohne Rebuild aenderbar.
        binding.updateServerUrlInput.setText(updateManager.getBaseUrl())
        binding.saveUpdateServerButton.setOnClickListener {
            updateManager.setBaseUrl(binding.updateServerUrlInput.text.toString())
            binding.updateServerUrlInput.setText(updateManager.getBaseUrl())
            binding.updateStatusText.text = getString(R.string.update_server_saved)
        }

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
        lockedStationsJob?.cancel()
    }

    /**
     * Ersetzt das frueher einmalige setupStationRows() - die Senderliste ist
     * jetzt persistent und aus der Verwaltungs-Activity heraus aenderbar,
     * die Zeilen muessen sich also bei jeder Aenderung neu aufbauen. Volles
     * removeAllViews()+neu-Aufbauen statt RecyclerView/DiffUtil, weil die
     * Datenmenge klein bleibt (siehe android-app/README.md) - kein Grund fuer
     * eine neue Dependency. Zeigt nur aktivierte Sender (deaktivierte gehoeren
     * per Definition nicht in eine Play-Liste), Kategorien werden hier NICHT
     * gruppiert - das ist Aufgabe der Verwaltungs-Activity.
     */
    private fun observeStations() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                StationRepository.stations.collect { renderStationRows(it) }
            }
        }
    }

    private fun renderStationRows(stations: List<Station>) {
        binding.stationsContainer.removeAllViews()
        stations.filter { it.enabled }.sortedBy { it.name.lowercase() }.forEach { station ->
            val row = ItemStationBinding.inflate(layoutInflater, binding.stationsContainer, false)
            row.stationNameText.text = station.name
            when (lockedStations[station.id]) {
                StationLockReason.SPEECH_COOLDOWN -> {
                    row.lockReasonText.text = getString(R.string.lock_reason_speech)
                    row.lockReasonText.visibility = android.view.View.VISIBLE
                }
                StationLockReason.DEAD -> {
                    row.lockReasonText.text = getString(R.string.lock_reason_dead)
                    row.lockReasonText.visibility = android.view.View.VISIBLE
                }
                null -> row.lockReasonText.visibility = android.view.View.GONE
            }
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
        // manualPlay() statt play(): explizite Nutzerwahl hebt eine eventuelle
        // Sperre (Sprache-Cooldown ODER Watchdog) fuer diesen Sender auf, siehe
        // PlaybackService-Klassen-Doc.
        service.manualPlay(station, modelManager.modelPathOrNull())
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

    /**
     * Ein einziger Button, dessen Beschriftung/Aktion sich mit dem
     * UpdateState mitdreht (suchen -> laden -> installieren) statt drei
     * getrennter Buttons - bewusst minimal, siehe restlicher UI-Ansatz.
     */
    private fun observeUpdateState() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                updateManager.state.collect { state -> renderUpdateState(state) }
            }
        }
    }

    private fun renderUpdateState(state: UpdateState) {
        when (state) {
            is UpdateState.Idle -> {
                binding.updateStatusText.text = ""
                binding.updateButton.text = getString(R.string.btn_check_update)
                binding.updateButton.isEnabled = true
                binding.updateButton.setOnClickListener { lifecycleScope.launch { updateManager.checkForUpdate() } }
            }

            is UpdateState.Checking -> {
                binding.updateStatusText.text = getString(R.string.update_checking)
                binding.updateButton.isEnabled = false
            }

            is UpdateState.UpToDate -> {
                binding.updateStatusText.text = getString(R.string.update_up_to_date)
                binding.updateButton.text = getString(R.string.btn_check_update)
                binding.updateButton.isEnabled = true
                binding.updateButton.setOnClickListener { lifecycleScope.launch { updateManager.checkForUpdate() } }
            }

            is UpdateState.Available -> {
                binding.updateStatusText.text = getString(R.string.update_available, state.buildTime)
                binding.updateButton.text = getString(R.string.btn_download_update)
                binding.updateButton.isEnabled = true
                binding.updateButton.setOnClickListener { lifecycleScope.launch { updateManager.downloadUpdate() } }
            }

            is UpdateState.Downloading -> {
                binding.updateStatusText.text = getString(R.string.update_downloading)
                binding.updateButton.isEnabled = false
            }

            is UpdateState.ReadyToInstall -> {
                binding.updateStatusText.text = getString(R.string.update_ready)
                binding.updateButton.text = getString(R.string.btn_install_update)
                binding.updateButton.isEnabled = true
                binding.updateButton.setOnClickListener { installUpdate(state.apkFile) }
            }

            is UpdateState.Error -> {
                binding.updateStatusText.text = getString(R.string.update_error, state.message)
                binding.updateButton.text = getString(R.string.btn_check_update)
                binding.updateButton.isEnabled = true
                binding.updateButton.setOnClickListener { lifecycleScope.launch { updateManager.checkForUpdate() } }
            }
        }
    }

    /**
     * Vor dem eigentlichen Install-Intent pruefen, ob diese App ueberhaupt
     * unbekannte Apps installieren darf (Android 8+, REQUEST_INSTALL_PACKAGES
     * allein reicht nicht) - sonst wuerde der Installer stillschweigend
     * nichts tun statt den Nutzer zur Freigabe zu leiten.
     */
    private fun installUpdate(apkFile: java.io.File) {
        if (!packageManager.canRequestPackageInstalls()) {
            startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName"))
            )
            return
        }
        startActivity(updateManager.installIntentFor(apkFile))
    }
}
