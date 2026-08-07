package com.radiozapper.mvp.playback

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import com.radiozapper.mvp.R
import com.radiozapper.mvp.analysis.StreamAnalyzer
import com.radiozapper.mvp.model.Station
import com.radiozapper.mvp.model.StationRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

private const val TAG = "PlaybackService"
private const val NOTIFICATION_CHANNEL_ID = "playback"
private const val NOTIFICATION_ID = 1

// Automatisches Umschalten: nach einem vollen Durchlauf durch die Senderliste
// (jeder Sender einmal probiert, jeder davon Sprache) kurze Pause statt endlos
// weiterzuspringen - siehe Klassen-Doc unten fuer die Begruendung.
private const val AUTO_SWITCH_PAUSE_SECONDS = 20L

// Cooldown pro Sender: ein Sender, der gerade wegen Sprache verlassen wurde,
// wird fuer diese Zeit beim Weiterspringen uebersprungen - sonst kommt er beim
// naechsten Ringdurchlauf sofort wieder dran, obwohl die Moderation/der
// Gesang (siehe README, "Bekannte Grenzen") vermutlich noch nicht vorbei ist.
private const val STATION_COOLDOWN_SECONDS = 60L

/**
 * Foreground Service: haelt den ExoPlayer fuer die eigentliche Wiedergabe UND
 * den StreamAnalyzer fuer die parallele Vosk-Analyse (siehe StreamAnalyzer-
 * Kommentar: zweite, unabhaengige Dekodierung desselben Streams). Beides muss
 * im selben Service leben, sonst stoppt Android die Analyse, sobald die
 * Activity nicht mehr im Vordergrund ist.
 *
 * Kommunikation mit der Activity laeuft ueber einen simplen Binder + die
 * StateFlows von StreamAnalyzer/PlaybackService - kein request/pop-Muster wie
 * im Docker-Projekt, weil es hier nur einen einzigen Akteur gibt, der den
 * Player anfasst (die Activity ruft direkt play()/stopPlayback() auf dem
 * Binder auf, das automatische Umschalten unten laeuft im selben Service).
 *
 * Automatisches Umschalten (seit dieser Version aktiv): wie im Docker-Projekt
 * (schaltet WEG von Sprache, siehe dessen README/CLAUDE.md - "schaltet bei
 * Sprache automatisch weiter") schaltet dieses MVP WEG von Sprache (Moderation/
 * Werbung/Jingles) - wer auf "Sprache" steht, ist per Definition (noch) kein
 * Treffer. Die Ringlogik liest die Senderliste bei jedem Versuch frisch aus
 * `StationRepository.activeStations()` (persistent, per Verwaltungs-Activity
 * editierbar, siehe model/StationRepository.kt) statt einer hartcodierten
 * Liste - kein Request/Pop-Mechanismus wie im Docker-Projekt noetig, weil
 * hier alles im selben Prozess laeuft und der aktuelle StateFlow-Wert nie
 * veraltet sein kann. Kein Watchdog/Ban-System (kommt erst spaeter) - nur
 * die einfache Ringlogik, ein Cooldown pro Sender (`STATION_COOLDOWN_SECONDS`,
 * siehe `nextStationOffCooldown()` - ein wegen Sprache verlassener Sender
 * kommt fuer diese Zeit beim Ringdurchlauf nicht sofort wieder an die Reihe)
 * und eine Obergrenze gegen die Endlosschleife, falls zufaellig ALLE Sender
 * gerade Sprache spielen oder im Cooldown sind.
 */
class PlaybackService : LifecycleService() {

    inner class LocalBinder : Binder() {
        val service: PlaybackService get() = this@PlaybackService
    }

    private val binder = LocalBinder()

    private var player: ExoPlayer? = null
    private lateinit var analyzer: StreamAnalyzer

    private val _currentStation = MutableStateFlow<Station?>(null)
    val currentStation: StateFlow<Station?> = _currentStation

    private var activeModelPath: String? = null
    private var autoSwitchAttempts = 0
    private var autoSwitchPausedUntil = 0L
    private val stationCooldownUntil = mutableMapOf<String, Long>()

    val status get() = analyzer.status

    override fun onCreate() {
        super.onCreate()
        analyzer = StreamAnalyzer(lifecycleScope)
        player = ExoPlayer.Builder(this).build()
        createNotificationChannel()

        lifecycleScope.launch {
            analyzer.status.collect { status -> handleStatusForAutoSwitch(status) }
        }

        // Reagiert auf Aenderungen der persistenten Senderliste (z.B. aus der
        // Verwaltungs-Activity) - siehe handleStationListChanged().
        lifecycleScope.launch {
            StationRepository.stations.collect { stations -> handleStationListChanged(stations) }
        }
    }

    override fun onBind(intent: Intent): IBinder {
        super.onBind(intent)
        return binder
    }

    fun play(station: Station, modelPath: String?) {
        _currentStation.value = station
        activeModelPath = modelPath

        player?.apply {
            stop()
            setMediaItem(MediaItem.fromUri(station.url))
            prepare()
            playWhenReady = true
        }

        startForegroundNotification(station.name)

        if (modelPath != null) {
            analyzer.start(station.url, modelPath)
        } else {
            analyzer.stop()
        }
    }

    fun stopPlayback() {
        _currentStation.value = null
        activeModelPath = null
        autoSwitchAttempts = 0
        autoSwitchPausedUntil = 0L
        stationCooldownUntil.clear()
        player?.stop()
        analyzer.stop()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /**
     * Reagiert auf den GEGLAETTETEN Status aus StreamAnalyzer (siehe dessen
     * Klassen-Doc) - nicht auf jeden Roh-Frame, sonst wuerde hier genauso
     * geflackert werden wie vorher in der Anzeige.
     */
    private fun handleStatusForAutoSwitch(status: PlaybackStatus) {
        when (status) {
            PlaybackStatus.MUSIC -> {
                autoSwitchAttempts = 0 // Treffer - Zaehler fuer die naechste Sprache-Serie zuruecksetzen
                // Kein Grund, einen Sender laenger im Cooldown zu halten, der
                // sich inzwischen selbst als Musik bestaetigt hat.
                _currentStation.value?.let { stationCooldownUntil.remove(it.id) }
            }
            PlaybackStatus.SPEECH -> attemptAutoSwitch()
            else -> Unit
        }
    }

    private fun attemptAutoSwitch() {
        val now = SystemClock.elapsedRealtime()
        if (now < autoSwitchPausedUntil) {
            Log.d(TAG, "Auto-Umschalten pausiert noch ${(autoSwitchPausedUntil - now) / 1000}s")
            return
        }

        val station = _currentStation.value ?: return
        val stations = StationRepository.activeStations()
        val currentIndex = stations.indexOfFirst { it.id == station.id }
        if (currentIndex < 0) return

        stationCooldownUntil[station.id] = now + STATION_COOLDOWN_SECONDS * 1000

        autoSwitchAttempts++
        if (autoSwitchAttempts > stations.size) {
            Log.i(
                TAG,
                "Alle ${stations.size} Sender einmal durchprobiert, ueberall Sprache - " +
                    "Pause fuer ${AUTO_SWITCH_PAUSE_SECONDS}s statt weiter im Kreis zu springen"
            )
            autoSwitchAttempts = 0
            autoSwitchPausedUntil = now + AUTO_SWITCH_PAUSE_SECONDS * 1000
            return
        }

        val next = nextStationOffCooldown(stations, currentIndex, now)
        if (next == null) {
            // Alle anderen Sender noch im Cooldown - dieselbe Sackgasse wie
            // "alle Sender Sprache", also dieselbe Behandlung: kurze Pause.
            Log.i(TAG, "Alle anderen Sender noch im Cooldown - Pause fuer ${AUTO_SWITCH_PAUSE_SECONDS}s")
            autoSwitchAttempts = 0
            autoSwitchPausedUntil = now + AUTO_SWITCH_PAUSE_SECONDS * 1000
            return
        }

        Log.i(TAG, "Sprache erkannt auf '${station.name}' - schalte weiter zu '${next.name}'")
        play(next, activeModelPath)
    }

    /**
     * Prueft bei jeder Aenderung der persistenten Senderliste, ob der GERADE
     * LAUFENDE Sender noch vorhanden UND aktiviert ist - z.B. weil er gerade
     * aus der Verwaltungs-Activity heraus deaktiviert/geloescht wurde.
     * Fruehes Return bei null: sonst wuerde jede Bearbeitung eines voellig
     * UNBETEILIGTEN Senders faelschlich die Wiedergabe neu starten, waehrend
     * gerade eigentlich gestoppt ist (siehe stopPlayback()).
     */
    private fun handleStationListChanged(stations: List<Station>) {
        val current = _currentStation.value ?: return
        val stillActive = stations.any { it.id == current.id && it.enabled }
        if (stillActive) return

        val fallback = StationRepository.activeStations().firstOrNull()
        if (fallback != null) {
            Log.i(TAG, "Senderliste geaendert, '${current.name}' nicht mehr aktiv - schalte auf '${fallback.name}'")
            play(fallback, activeModelPath)
        } else {
            Log.i(TAG, "Senderliste geaendert, keine aktiven Sender mehr - stoppe Wiedergabe")
            stopPlayback()
        }
    }

    /** Naechster Sender im Ring ab currentIndex (exklusiv), der aktuell nicht im Cooldown ist. */
    private fun nextStationOffCooldown(stations: List<Station>, currentIndex: Int, now: Long): Station? {
        for (offset in 1..stations.size) {
            val candidate = stations[(currentIndex + offset) % stations.size]
            val cooldownUntil = stationCooldownUntil[candidate.id] ?: 0L
            if (now >= cooldownUntil) return candidate
        }
        return null
    }

    private fun startForegroundNotification(stationName: String) {
        val notification = NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.notification_playing, stationName))
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setOngoing(true)
            .build()

        // ServiceCompat.startForeground kapselt die Versionsunterschiede selbst
        // (foregroundServiceType wird vor API 29 intern ignoriert).
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification,
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
        )
    }

    private fun createNotificationChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            NOTIFICATION_CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW
        )
        manager.createNotificationChannel(channel)
    }

    override fun onDestroy() {
        analyzer.stop()
        player?.release()
        player = null
        super.onDestroy()
    }
}
