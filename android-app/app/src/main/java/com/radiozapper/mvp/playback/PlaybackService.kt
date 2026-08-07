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
import com.radiozapper.mvp.model.Stations
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

private const val TAG = "PlaybackService"
private const val NOTIFICATION_CHANNEL_ID = "playback"
private const val NOTIFICATION_ID = 1

// Automatisches Umschalten: nach einem vollen Durchlauf durch die Senderliste
// (jeder Sender einmal probiert, jeder davon Musik) kurze Pause statt endlos
// weiterzuspringen - siehe Klassen-Doc unten fuer die Begruendung.
private const val AUTO_SWITCH_PAUSE_SECONDS = 20L

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
 * Treffer. Mit nur 3 hartcodierten Sendern laesst sich das so klar beobachten:
 * die App wandert bis zum ersten ueberwiegend musikalischen Sender (i.d.R.
 * 1LIVE oder SWR3) und bleibt dort stehen. Kein Watchdog/Ban-System (kommt
 * erst spaeter) - nur die einfache Ringlogik plus eine Obergrenze gegen die
 * Endlosschleife, falls zufaellig ALLE Sender gerade Sprache spielen.
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

    val status get() = analyzer.status

    override fun onCreate() {
        super.onCreate()
        analyzer = StreamAnalyzer(lifecycleScope)
        player = ExoPlayer.Builder(this).build()
        createNotificationChannel()

        lifecycleScope.launch {
            analyzer.status.collect { status -> handleStatusForAutoSwitch(status) }
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
            PlaybackStatus.MUSIC -> autoSwitchAttempts = 0 // Treffer - Zaehler fuer die naechste Sprache-Serie zuruecksetzen
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
        val stations = Stations.ALL
        val currentIndex = stations.indexOfFirst { it.id == station.id }
        if (currentIndex < 0) return

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

        val next = stations[(currentIndex + 1) % stations.size]
        Log.i(TAG, "Sprache erkannt auf '${station.name}' - schalte weiter zu '${next.name}'")
        play(next, activeModelPath)
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
