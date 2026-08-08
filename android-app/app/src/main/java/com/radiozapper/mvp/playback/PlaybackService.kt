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
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import com.radiozapper.mvp.R
import com.radiozapper.mvp.analysis.StreamAnalyzer
import com.radiozapper.mvp.model.Station
import com.radiozapper.mvp.model.StationRepository
import com.radiozapper.mvp.newsbreak.NewsBreak
import com.radiozapper.mvp.newsbreak.NewsBreakSettings
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
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

// Watchdog gegen tote/nicht antwortende Sender (analog zum Docker-Projekt,
// dessen dead_until/alive_stations() - siehe dessen CLAUDE.md). Eigene Map,
// eigene Konstante, bewusst getrennt von stationCooldownUntil (der ist fuer
// "gerade als Sprache erkannt", hier geht's um "technisch kaputt") - beide
// Gruende sollen unterscheidbar bleiben, siehe StationLockReason.
private const val STATION_DEAD_LOCK_SECONDS = 300L

// Wie lange Player.STATE_BUFFERING ununterbrochen anhalten darf, bevor der
// Sender als tot gilt - deckt sowohl "verbindet nie" (Anfangsphase) als
// auch "haengt mitten im Stream fest" ab, kein Sonderfall fuer die
// Anfangsverbindung noetig, weil beides einfach "seit wann buffert es schon".
private const val BUFFERING_TIMEOUT_SECONDS = 15L

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
 * veraltet sein kann. Ein Cooldown pro Sender (`STATION_COOLDOWN_SECONDS`,
 * siehe `nextAvailableStation()` - ein wegen Sprache verlassener Sender
 * kommt fuer diese Zeit beim Ringdurchlauf nicht sofort wieder an die Reihe)
 * und eine Obergrenze gegen die Endlosschleife, falls zufaellig ALLE Sender
 * gerade Sprache spielen oder gesperrt sind.
 *
 * Watchdog gegen tote/nicht antwortende Sender (analog zum Docker-Projekt,
 * dessen `dead_until`/`alive_stations()`): ein `Player.Listener` an ExoPlayer
 * erkennt zwei Faelle - `onPlayerError()` (haretes Signal) und ein Sender,
 * der laenger als `BUFFERING_TIMEOUT_SECONDS` ununterbrochen buffert (deckt
 * "verbindet, liefert aber nie Daten" ab, das nie einen Error ausloest -
 * siehe `handlePlaybackFailure()`). Eigene Sperr-Map (`deadUntil`), bewusst
 * getrennt von `stationCooldownUntil` (siehe `StationLockReason`), aber
 * dieselbe Auswahl-Logik (`nextAvailableStation()`/`findNextOrEscalate()`)
 * behandelt beide Sperrgruende einheitlich - inkl. der "alle Sender
 * gesperrt"-Eskalation (beide Maps leeren statt haengenzubleiben, wie
 * `dead_until.clear()` im Docker-Projekt). Manuelle Sender-Wahl
 * (`manualPlay()`) hebt beide Sperren fuer den gewaehlten Sender auf -
 * expliziter Nutzerwunsch schlaegt Automatik.
 *
 * Vorwaermung fuer luecken lose Wechsel (Phase 3 aus dem Fahrplan): ein
 * zweiter, paralleler ExoPlayer (`preloadedPlayer`) haelt immer GENAU den
 * Sender vorbereitet, den `nextAvailableStation()` gerade als naechsten
 * waehlen wuerde (`refreshPreload()`, aufgerufen bei jeder Aenderung von
 * aktuellem Sender/Sperren/Senderliste - alle drei Einfluesse auf das
 * Ergebnis). Da `attemptAutoSwitch()` (automatisch UND der ZAPPEN!-Button)
 * sowie `handlePlaybackFailure()` (Watchdog) exakt dieselbe Auswahl-Logik
 * verwenden, trifft `play()` in der ueberwiegenden Mehrheit der Faelle genau
 * diesen vorgewaermten Kandidaten und uebernimmt ihn statt eines Kaltstarts.
 * Bewusst nur EIN Kandidat, kein Pool wie `PrebufferedSource`/
 * `prebuffer_count` im Docker-Projekt (siehe dessen `CLAUDE.md`) - der
 * Ressourcenverbrauch mehrerer gleichzeitiger Dauerstreams stuende fuer die
 * auf dem Handy erwartete Sendermenge in keinem Verhaeltnis zum
 * Zusatznutzen. `manualPlay()` (Sender in der Liste antippen) profitiert nur
 * zufaellig, wenn der angetippte Sender der vorhergesagte ist.
 *
 * Nachrichten-Pause (Phase 6, siehe newsbreak/NewsBreak.kt fuer die reine
 * Zeitfenster-/Auswahl-Logik): `_currentStation` bleibt waehrend einer Pause
 * bewusst UNVERAENDERT der pausierte Sender (analog `current` im
 * Docker-Projekt) - Ring-Berechnung, Cooldown/Dead-Maps und die Vorwaermung
 * oben laufen dadurch mit korrekten Daten weiter, nur `player` zeigt
 * voruebergehend auf eine lokale MP3. Ein periodischer Tick
 * (`startNewsBreakTicker()`, alle 15s waehrend der Wiedergabe) prueft
 * `NewsBreak.activeSlot()` und steuert Eintritt/Fortsetzen/Ende. Der
 * `playerListener` muss dabei zwischen "MP3 hat ein Problem" (naechste Datei
 * laden bzw. Pause beenden) und "Sender hat ein Problem" (regulaerer
 * Watchdog oben) unterscheiden - sonst wuerde ein MP3-Fehler faelschlich den
 * gesunden, nur pausierten Sender als tot markieren.
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
    private val deadUntil = mutableMapOf<String, Long>()
    private var bufferingTimeoutJob: Job? = null

    private val _lockedStations = MutableStateFlow<Map<String, StationLockReason>>(emptyMap())
    /** Momentaufnahme, neu berechnet bei jeder Aenderung einer der beiden Sperr-Maps - siehe refreshLockedStationsSnapshot(). */
    val lockedStations: StateFlow<Map<String, StationLockReason>> = _lockedStations

    // Vorwaermung (Phase 3 aus dem Fahrplan, siehe Klassen-Doc oben): ein
    // zweiter ExoPlayer haelt den laut Ringlogik wahrscheinlichsten naechsten
    // Sender bereits vorbereitet, damit ein Wechsel dorthin ohne Kaltstart
    // ablaeuft.
    private var preloadedPlayer: ExoPlayer? = null
    private var preloadedStationId: String? = null

    // Nachrichten-Pause (siehe Klassen-Doc oben + newsbreak/NewsBreak.kt).
    private val _newsBreakActive = MutableStateFlow(false)
    val newsBreakActive: StateFlow<Boolean> = _newsBreakActive
    private val _newsBreakFileName = MutableStateFlow<String?>(null)
    val newsBreakFileName: StateFlow<String?> = _newsBreakFileName
    private val newsBreakRecentFiles = ArrayDeque<String>() // maxsize NewsBreak.RECENT_HISTORY_SIZE, aeltestes zuerst
    private var newsBreakServedSlot: String? = null
    private var newsBreakTickerJob: Job? = null

    private val playerListener = object : Player.Listener {
        override fun onPlayerError(error: PlaybackException) {
            if (_newsBreakActive.value) {
                Log.w(TAG, "Nachrichten-Pause-Datei '${_newsBreakFileName.value}' fehlgeschlagen", error)
                advanceNewsBreak()
            } else {
                Log.w(TAG, "ExoPlayer-Fehler bei '${_currentStation.value?.name}'", error)
                handlePlaybackFailure()
            }
        }

        override fun onPlaybackStateChanged(playbackState: Int) {
            when (playbackState) {
                Player.STATE_BUFFERING -> armBufferingWatchdog()
                Player.STATE_ENDED -> {
                    // Radiostreams enden nie - dieser Fall tritt nur bei einer
                    // zu Ende gespielten Nachrichten-Pause-MP3 auf.
                    bufferingTimeoutJob?.cancel()
                    bufferingTimeoutJob = null
                    if (_newsBreakActive.value) advanceNewsBreak()
                }
                Player.STATE_READY -> {
                    bufferingTimeoutJob?.cancel()
                    bufferingTimeoutJob = null
                    if (!_newsBreakActive.value) {
                        // Laeuft wieder - kein Grund, eine eventuelle Tot-Markierung
                        // laenger zu halten als noetig (analog "jeder erfolgreiche
                        // Read setzt den Zaehler zurueck" im Docker-Projekt). Waehrend
                        // einer Pause zeigt _currentStation auf den PAUSIERTEN Sender -
                        // dessen Sperre hat mit der MP3-Bereitschaft nichts zu tun.
                        _currentStation.value?.let { deadUntil.remove(it.id) }
                        refreshLockedStationsSnapshot()
                    }
                }
                else -> {
                    bufferingTimeoutJob?.cancel()
                    bufferingTimeoutJob = null
                }
            }
        }
    }

    /**
     * Verwirft einen fehlgeschlagenen Vorwaerm-Kandidaten wieder - bewusst OHNE
     * den deadUntil/Watchdog-Mechanismus auszuloesen: der Sender ist noch nicht
     * `current`, ein Fehler hier bedeutet nur "war kein guter Kandidat", nicht
     * zwingend "ist tot" (siehe Klassen-Doc oben). Wird er tatsaechlich
     * ausgewaehlt, faengt ihn der normale Watchdog ganz regulaer ueber den
     * Kaltstart-Pfad ab.
     */
    private val preloadFailureListener = object : Player.Listener {
        override fun onPlayerError(error: PlaybackException) {
            Log.d(TAG, "Vorgewaermter Kandidat '$preloadedStationId' fehlgeschlagen - verwerfe")
            clearPreload()
        }
    }

    val status get() = analyzer.status
    val speechRatio get() = analyzer.speechRatio

    override fun onCreate() {
        super.onCreate()
        analyzer = StreamAnalyzer(lifecycleScope)
        player = ExoPlayer.Builder(this).build().apply { addListener(playerListener) }
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

    /**
     * Fuer manuelle Sender-Wahl (Activity-Klick): explizite Nutzerwahl schlaegt
     * jede Automatik, deshalb werden BEIDE Sperr-Gruende fuer diesen Sender
     * aufgehoben, bevor ueberhaupt versucht wird - der Sender kann laengst
     * wieder da sein. Automatische Aufrufe (attemptAutoSwitch/
     * handlePlaybackFailure) rufen weiterhin direkt play() auf.
     */
    fun manualPlay(station: Station, modelPath: String?) {
        // Expliziter Nutzerwunsch beendet eine laufende Nachrichten-Pause sofort -
        // eigene Entscheidung schlaegt Automatik, wie ueberall sonst in dieser
        // Klasse auch (siehe Klassen-Doc, Abschnitt Nachrichten-Pause).
        interruptNewsBreak()
        stationCooldownUntil.remove(station.id)
        deadUntil.remove(station.id)
        refreshLockedStationsSnapshot()
        play(station, modelPath)
    }

    /**
     * Fuer den "⚡ ZAPPEN!"-Button (analog zum Web-Interface): sofort weiterschalten,
     * weil hier gerade geredet wird - inhaltlich exakt dieselbe Aktion wie ein
     * automatisch erkannter Sprache-Treffer, nur manuell ausgeloest statt ueber
     * handleStatusForAutoSwitch(). Reine Weiterleitung, kein Duplikat der Ring-Logik.
     */
    fun manualSkip() {
        interruptNewsBreak()
        attemptAutoSwitch()
    }

    /**
     * Uebernimmt den vorgewaermten Player, falls `station` genau der laut
     * `refreshPreload()` vorbereitete Kandidat ist (der Normalfall bei
     * automatischem Umschalten, ZAPPEN! und dem Watchdog - alle drei waehlen
     * ueber dieselbe `nextAvailableStation()`-Logik) - praktisch luecken loser
     * Wechsel statt Kaltstart. Trifft `station` NICHT den vorgewaermten
     * Kandidaten (z.B. `manualPlay()` auf einen beliebigen Sender in der
     * Liste), bleibt es beim bisherigen Kaltstart.
     */
    fun play(station: Station, modelPath: String?) {
        _currentStation.value = station
        activeModelPath = modelPath

        val warm = preloadedPlayer
        if (warm != null && preloadedStationId == station.id) {
            Log.d(TAG, "Uebernehme vorgewaermten Kandidaten '${station.name}' - kein Kaltstart")
            player?.apply {
                removeListener(playerListener)
                release()
            }
            player = warm.apply {
                removeListener(preloadFailureListener)
                addListener(playerListener)
                playWhenReady = true
            }
            preloadedPlayer = null
            preloadedStationId = null
            // Randfall siehe armBufferingWatchdog()-Doc: war der Kandidat beim
            // Uebernehmen noch nicht STATE_READY, muss der Timer manuell
            // angestossen werden.
            if (warm.playbackState == Player.STATE_BUFFERING) armBufferingWatchdog()
        } else {
            clearPreload() // falscher Kandidat stand bereit - verworfen statt uebernommen
            player?.apply {
                stop()
                setMediaItem(MediaItem.fromUri(station.url))
                prepare()
                playWhenReady = true
            }
        }

        startForegroundNotification(station.name)

        if (modelPath != null) {
            analyzer.start(station.url, modelPath)
        } else {
            analyzer.stop()
        }

        refreshPreload()
        startNewsBreakTicker()
    }

    /**
     * Bereitet den laut Ringlogik naechsten Sender im Hintergrund vor (siehe
     * Klassen-Doc oben, Abschnitt Vorwaermung) - aufgerufen an jeder Stelle, an der
     * sich einer der drei Einflussfaktoren aendert: aktueller Sender (Ende von
     * `play()`), Sperren (Ende von `refreshLockedStationsSnapshot()`) oder
     * Senderliste (Ende von `handleStationListChanged()`). Idempotent: steht
     * der richtige Kandidat schon bereit, passiert nichts.
     */
    private fun refreshPreload() {
        val current = _currentStation.value ?: run { clearPreload(); return }
        val stations = StationRepository.activeStations()
        val currentIndex = stations.indexOfFirst { it.id == current.id }
        val candidate = if (currentIndex >= 0) {
            nextAvailableStation(stations, currentIndex, SystemClock.elapsedRealtime())
        } else {
            null
        }

        if (candidate == null || candidate.id == current.id) {
            // Kein sinnvoller Kandidat (alles gesperrt) oder nur der aktuelle
            // Sender selbst (z.B. genau 1 aktiver Sender) - nichts vorwaermen.
            clearPreload()
            return
        }
        if (candidate.id == preloadedStationId) return // schon der richtige

        clearPreload()
        preloadedStationId = candidate.id
        Log.d(TAG, "Waerme '${candidate.name}' als naechsten Kandidaten vor")
        preloadedPlayer = ExoPlayer.Builder(this).build().apply {
            addListener(preloadFailureListener)
            setMediaItem(MediaItem.fromUri(candidate.url))
            prepare()
            playWhenReady = false // puffert trotzdem im Hintergrund, spielt nur nicht hoerbar
        }
    }

    private fun clearPreload() {
        preloadedPlayer?.release()
        preloadedPlayer = null
        preloadedStationId = null
    }

    /** Alle 15s waehrend der Wiedergabe - die Fenstergranularitaet ist Minuten, das reicht. Laeuft ueber play()/resumeFromNewsBreak() hinweg durch, bis stopPlayback()/onDestroy(). */
    private fun startNewsBreakTicker() {
        if (newsBreakTickerJob?.isActive == true) return
        newsBreakTickerJob = lifecycleScope.launch {
            while (isActive) {
                checkNewsBreak()
                delay(15_000)
            }
        }
    }

    /**
     * Prueft, ob gerade ein Nachrichten-Pause-Fenster laeuft (siehe
     * NewsBreak.activeSlot()) und steuert Eintritt/Ende. `newsBreakServedSlot`
     * wird SOFORT beim Eintreten gesetzt, nicht erst bei Erfolg - sonst wuerde
     * ein leerer/ungueltiger Ordner bei jedem Tick erneut versucht, bis das
     * Fenster von selbst vorbei ist (analog zum Docker-Projekt).
     */
    private fun checkNewsBreak() {
        val slot = NewsBreak.activeSlot(NewsBreakSettings.getConfig(this))
        if (slot != null && !_newsBreakActive.value && slot != newsBreakServedSlot) {
            newsBreakServedSlot = slot
            enterNewsBreak()
        } else if (_newsBreakActive.value && slot == null) {
            resumeFromNewsBreak("Nachrichten-Pause-Fenster abgelaufen")
        }
    }

    /**
     * Waehlt eine zufaellige, zuletzt nicht gespielte MP3 (siehe
     * NewsBreak.pickRandom()/RECENT_HISTORY_SIZE) und startet sie auf dem
     * bestehenden `player` - dieselbe Instanz, die sonst Radiosender abspielt
     * (kein eigener MP3-Player noetig). True bei Erfolg, false wenn der
     * Ordner leer/ungueltig ist (der Aufrufer entscheidet dann, was
     * stattdessen passiert - Eintritt: gar nichts, laufender Sender bleibt
     * unangetastet; laufende Pause: beenden).
     */
    private fun playNextNewsBreakFile(): Boolean {
        val files = NewsBreakSettings.listMp3s(this)
        val chosen = NewsBreak.pickRandom(files, newsBreakRecentFiles.toList()) { it.name ?: "" } ?: return false
        val name = chosen.name ?: return false

        if (newsBreakRecentFiles.size >= NewsBreak.RECENT_HISTORY_SIZE) newsBreakRecentFiles.removeFirst()
        newsBreakRecentFiles.addLast(name)

        player?.apply {
            stop()
            setMediaItem(MediaItem.fromUri(chosen.uri))
            prepare()
            playWhenReady = true
        }
        _newsBreakFileName.value = name
        return true
    }

    private fun enterNewsBreak() {
        if (!playNextNewsBreakFile()) {
            Log.w(TAG, "📰 Nachrichten-Pause-Fenster erreicht, aber keine MP3 verfuegbar - uebersprungen")
            return
        }
        // Keine Klassifikation waehrend der Pause noetig - die MP3 enthaelt
        // u.U. selbst Sprache, das soll nicht als "Moderation auf dem echten
        // Sender" fehlgedeutet werden (siehe handleStatusForAutoSwitch()).
        analyzer.stop()
        _newsBreakActive.value = true
        Log.i(
            TAG,
            "📰 Nachrichten-Pause: spiele '${_newsBreakFileName.value}' " +
                "(zurueck zu '${_currentStation.value?.name}' danach)"
        )
    }

    /** MP3 zu Ende ODER fehlgeschlagen: laeuft das Fenster noch, naechste Datei laden - sonst Pause beenden. Ohne das endete die Pause nach genau einer Datei (siehe Klassen-Doc/SESSION.md). */
    private fun advanceNewsBreak() {
        val stillActive = NewsBreak.activeSlot(NewsBreakSettings.getConfig(this)) != null
        if (stillActive && playNextNewsBreakFile()) {
            Log.i(TAG, "📰 Nachrichten-Pause: naechste Datei '${_newsBreakFileName.value}'")
            return
        }
        resumeFromNewsBreak("Nachrichten-Pause-MP3 zu Ende")
    }

    /** Beendet die Pause und schaltet zurueck zum vorher laufenden Sender - dieselbe Funktion (play()) wie jeder normale Wechsel. */
    private fun resumeFromNewsBreak(reason: String) {
        _newsBreakActive.value = false
        _newsBreakFileName.value = null
        val station = _currentStation.value ?: return
        Log.i(TAG, "📰 $reason - zurueck zu '${station.name}'")
        play(station, activeModelPath)
    }

    /**
     * Eine explizite Nutzer-Aktion (manueller Switch, ZAPPEN!) waehrend einer
     * laufenden Nachrichten-Pause beendet die Pause sofort. Markiert den Slot
     * als bedient, damit die Pause nicht im selben Zeitfenster gleich wieder
     * anspringt und den gerade erst gewaehlten Sender sofort wieder verdraengt.
     * Loest selbst KEINEN Wechsel aus - das macht der Aufrufer (play() folgt
     * unmittelbar in manualPlay()/attemptAutoSwitch()).
     */
    private fun interruptNewsBreak() {
        if (!_newsBreakActive.value) return
        newsBreakServedSlot = NewsBreak.activeSlot(NewsBreakSettings.getConfig(this))
        _newsBreakActive.value = false
        _newsBreakFileName.value = null
    }

    fun stopPlayback() {
        _currentStation.value = null
        activeModelPath = null
        autoSwitchAttempts = 0
        autoSwitchPausedUntil = 0L
        stationCooldownUntil.clear()
        deadUntil.clear()
        refreshLockedStationsSnapshot()
        bufferingTimeoutJob?.cancel()
        bufferingTimeoutJob = null
        newsBreakTickerJob?.cancel()
        newsBreakTickerJob = null
        _newsBreakActive.value = false
        _newsBreakFileName.value = null
        newsBreakRecentFiles.clear()
        newsBreakServedSlot = null
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
        if (_newsBreakActive.value) return // keine automatische Umschaltung, ausgeloest von einer MP3
        when (status) {
            PlaybackStatus.MUSIC -> {
                autoSwitchAttempts = 0 // Treffer - Zaehler fuer die naechste Sprache-Serie zuruecksetzen
                // Kein Grund, einen Sender laenger gesperrt zu halten, der sich
                // inzwischen selbst als Musik bestaetigt hat - egal aus welchem
                // der beiden Gruende.
                _currentStation.value?.let {
                    stationCooldownUntil.remove(it.id)
                    deadUntil.remove(it.id)
                }
                refreshLockedStationsSnapshot()
            }
            PlaybackStatus.SPEECH -> attemptAutoSwitch()
            // StreamAnalyzers eigene, unabhaengige Dekodierung (siehe dessen
            // Klassen-Doc) ist eine zweite, praktisch kostenlose Bestaetigung
            // "diese URL ist kaputt" - haengt an denselben Sperr-Mechanismus
            // wie ein ExoPlayer-Fehler.
            PlaybackStatus.ERROR -> handlePlaybackFailure()
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
        refreshLockedStationsSnapshot()

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

        val next = findNextOrEscalate(stations, currentIndex, now)
        if (next == null) {
            // Alle anderen Sender noch gesperrt (Cooldown oder tot) - dieselbe
            // Sackgasse wie "alle Sender Sprache", also dieselbe Behandlung:
            // kurze Pause. findNextOrEscalate() hat die "alle gesperrt"-
            // Eskalation (beide Maps leeren) bereits selbst einmal versucht.
            Log.i(TAG, "Alle anderen Sender noch gesperrt - Pause fuer ${AUTO_SWITCH_PAUSE_SECONDS}s")
            autoSwitchAttempts = 0
            autoSwitchPausedUntil = now + AUTO_SWITCH_PAUSE_SECONDS * 1000
            return
        }

        Log.i(TAG, "Sprache erkannt auf '${station.name}' - schalte weiter zu '${next.name}'")
        play(next, activeModelPath)
    }

    /**
     * Startet den Buffering-Timeout-Timer (neu). Aufgerufen sowohl vom
     * `playerListener` bei jedem Eintritt in `STATE_BUFFERING`, als auch
     * explizit direkt nach einer Vorwaerm-Uebernahme in `play()`, falls der
     * uebernommene Player zu dem Zeitpunkt noch buffert - ein `Player.Listener`
     * feuert `onPlaybackStateChanged` nur bei KUENFTIGEN Zustandswechseln, ein
     * bereits bestehender Zustand zum Zeitpunkt des `addListener()`-Aufrufs
     * loest ihn nicht rueckwirkend aus (siehe `play()`-Kommentar).
     */
    private fun armBufferingWatchdog() {
        // Timer neu starten statt nur einmal zu pruefen - jeder erneute
        // Eintritt in BUFFERING (auch ein Wiederanlauf mitten im Stream)
        // bekommt seine volle Frist, kein Zusammenzaehlen ueber mehrere
        // kurze Aussetzer hinweg.
        bufferingTimeoutJob?.cancel()
        bufferingTimeoutJob = lifecycleScope.launch {
            delay(BUFFERING_TIMEOUT_SECONDS * 1000)
            if (_newsBreakActive.value) {
                Log.w(TAG, "Kein Fortschritt seit ${BUFFERING_TIMEOUT_SECONDS}s - Nachrichten-Pause-Datei antwortet nicht")
                advanceNewsBreak()
            } else {
                Log.w(
                    TAG,
                    "Kein Fortschritt seit ${BUFFERING_TIMEOUT_SECONDS}s - " +
                        "'${_currentStation.value?.name}' antwortet nicht"
                )
                handlePlaybackFailure()
            }
        }
    }

    /**
     * Reagiert auf einen ExoPlayer-Fehler, einen Buffering-Timeout ODER einen
     * StreamAnalyzer-Fehler (siehe handleStatusForAutoSwitch) - der Sender
     * antwortet technisch nicht, unabhaengig vom Sprache/Musik-Inhalt. Sperrt
     * ihn fuer STATION_DEAD_LOCK_SECONDS und schaltet weiter, analog zu
     * attemptAutoSwitch(), aber mit dem eigenen Sperrgrund.
     */
    private fun handlePlaybackFailure() {
        val station = _currentStation.value ?: return
        val now = SystemClock.elapsedRealtime()

        deadUntil[station.id] = now + STATION_DEAD_LOCK_SECONDS * 1000
        refreshLockedStationsSnapshot()
        Log.w(TAG, "Sender '${station.name}' antwortet nicht - fuer ${STATION_DEAD_LOCK_SECONDS / 60} Min. aus der Rotation")

        val stations = StationRepository.activeStations()
        val currentIndex = stations.indexOfFirst { it.id == station.id }
        if (currentIndex < 0) {
            // Sender ist zwischenzeitlich deaktiviert/geloescht worden -
            // handleStationListChanged() kuemmert sich bereits darum.
            return
        }

        val next = findNextOrEscalate(stations, currentIndex, now)
        if (next != null) {
            Log.i(TAG, "Schalte weiter zu '${next.name}'")
            play(next, activeModelPath)
        } else {
            Log.i(TAG, "Keine verfuegbaren Sender mehr - stoppe Wiedergabe")
            stopPlayback()
        }
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
        if (stillActive) {
            // Der laufende Sender bleibt zwar aktiv, aber Reihenfolge/
            // Mitgliedschaft der Liste kann sich trotzdem geaendert haben -
            // der vorgewaermte Kandidat muss dann neu berechnet werden (play()/
            // stopPlayback() unten decken diesen Fall sonst nicht ab).
            refreshPreload()
            return
        }

        val fallback = StationRepository.activeStations().firstOrNull()
        if (fallback != null) {
            Log.i(TAG, "Senderliste geaendert, '${current.name}' nicht mehr aktiv - schalte auf '${fallback.name}'")
            play(fallback, activeModelPath)
        } else {
            Log.i(TAG, "Senderliste geaendert, keine aktiven Sender mehr - stoppe Wiedergabe")
            stopPlayback()
        }
    }

    private fun isLocked(stationId: String, now: Long): Boolean {
        val cooldownUntil = stationCooldownUntil[stationId] ?: 0L
        val deadLockUntil = deadUntil[stationId] ?: 0L
        return now < cooldownUntil || now < deadLockUntil
    }

    /** Naechster Sender im Ring ab currentIndex (exklusiv), der aktuell durch KEINEN der beiden Gruende gesperrt ist. */
    private fun nextAvailableStation(stations: List<Station>, currentIndex: Int, now: Long): Station? {
        for (offset in 1..stations.size) {
            val candidate = stations[(currentIndex + offset) % stations.size]
            if (!isLocked(candidate.id, now)) return candidate
        }
        return null
    }

    /**
     * Wie nextAvailableStation(), aber mit der "alle gesperrt"-Eskalation:
     * findet sich kein verfuegbarer Sender, WEIL wirklich jeder aktive Sender
     * gerade durch Cooldown ODER Tot-Sperre ausgeschlossen ist, werden BEIDE
     * Sperrlisten geleert und einmal neu versucht - wie dead_until.clear()
     * im Docker-Projekt, statt in einer Runde aus lauter uebersprungenen
     * Kandidaten haengenzubleiben.
     */
    private fun findNextOrEscalate(stations: List<Station>, currentIndex: Int, now: Long): Station? {
        nextAvailableStation(stations, currentIndex, now)?.let { return it }

        val allLocked = stations.isNotEmpty() && stations.all { isLocked(it.id, now) }
        if (!allLocked) return null

        Log.w(TAG, "Alle ${stations.size} aktiven Sender gesperrt - hebe beide Sperrlisten auf und probiere erneut")
        stationCooldownUntil.clear()
        deadUntil.clear()
        refreshLockedStationsSnapshot()
        return nextAvailableStation(stations, currentIndex, now)
    }

    private fun refreshLockedStationsSnapshot() {
        val now = SystemClock.elapsedRealtime()
        val snapshot = mutableMapOf<String, StationLockReason>()
        // DEAD zuerst eingetragen, damit es im (seltenen) Fall einer
        // Doppel-Sperre denselben Sender-Eintrag gewinnt - dringlicher als
        // ein Sprache-Cooldown.
        deadUntil.forEach { (id, until) -> if (now < until) snapshot[id] = StationLockReason.DEAD }
        stationCooldownUntil.forEach { (id, until) ->
            if (now < until) snapshot.putIfAbsent(id, StationLockReason.SPEECH_COOLDOWN)
        }
        _lockedStations.value = snapshot
        // Deckt ALLE Aufrufer dieser Funktion ab (manualPlay, attemptAutoSwitch,
        // handlePlaybackFailure, die MUSIC-Freigabe, die Eskalation oben, das
        // Aufraeumen in stopPlayback()) - der vorgewaermte Kandidat haengt von
        // genau diesen Sperren ab, siehe refreshPreload().
        refreshPreload()
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
        bufferingTimeoutJob?.cancel()
        newsBreakTickerJob?.cancel()
        analyzer.stop()
        player?.release()
        player = null
        clearPreload()
        super.onDestroy()
    }
}
