package com.radiozapper.mvp.playback

import android.content.Context
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import com.radiozapper.mvp.analysis.StreamAnalyzer
import com.radiozapper.mvp.model.Station
import com.radiozapper.mvp.stt.SttSettings
import com.radiozapper.mvp.vosk.VoskModelCache
import com.radiozapper.mvp.vosk.VoskModelManager
import kotlinx.coroutines.CoroutineScope

/**
 * Werbeblock-Vorbuffering fuer die Nachrichten-Pause - Android-Pendant zu
 * `ad_skip_prebuffer.py` im Docker-Projekt (siehe dessen `ARCHITECTURE.md`,
 * Abschnitt "Werbeblock-Vorbuffering"): waehrend die letzten
 * `adPrebufferLeadSeconds` einer laufenden Pause-MP3 ablaufen, schon im
 * Hintergrund einen zweiten, stummen `ExoPlayer` + eine eigene
 * `StreamAnalyzer`-Instanz auf den Sender verbinden, zu dem danach
 * zurueckgeschaltet wird - dasselbe Vorwaermungsmuster, das
 * `PlaybackService` fuer den wahrscheinlichsten naechsten Ringkandidaten
 * ohnehin schon nutzt (`preloadedPlayer`/`refreshPreload()`), nur diesmal
 * fuer den PAUSIERTEN (Resume-)Sender selbst statt fuer den Ring-Nachfolger.
 *
 * Anders als im Docker-Projekt (dort bewusst NUR VAD, siehe Moduldocstring
 * von `ad_skip_prebuffer.py` - eine zweite Vosk/STT-Instanz waere dort zu
 * teuer, RAM-Verdopplung pro Sprache) gibt es auf Android gar kein VAD -
 * Vosk/`StreamAnalyzer` ist hier das EINZIGE verfuegbare
 * Klassifikationswerkzeug (siehe `CLAUDE.md`, Abschnitt "Zwei unabhaengige
 * Dekodierungen"), wird hier also bewusst dafuer wiederverwendet statt eine
 * eigene VAD-Bibliothek einzufuehren. Kostet eine DRITTE parallele
 * Dekodierung, aber nur fuer die kurze `adPrebufferLeadSeconds`-Spanne kurz
 * vor Pause-Ende - passt zur bestehenden "WLAN-Prototyp"-Einordnung der App.
 *
 * `readyForPromotion` (= der GEGLAETTETE Status der Hintergrund-Analyse
 * steht auf MUSIC) nutzt bewusst dieselbe, schon getunte Hysterese wie
 * ueberall sonst im Projekt statt eines eigenen rohen Streak-Zaehlers wie im
 * Docker-Vorbild (dessen `music_confirm_windows`) - fuer eine kurze,
 * einmalige Ja/Nein-Entscheidung am Pause-Ende reicht der ohnehin
 * vorhandene Signalweg.
 *
 * Bewusst OHNE `FingerprintDb`: ein Treffer auf diesem stummen
 * Hintergrund-Stream wuerde ein `FingerprintOutcome`-Ereignis fuer einen
 * Sender feuern, der gerade gar nicht hoerbar ist - der Aufrufer
 * (`PlaybackService`) hat keinen sinnvollen Weg, das einem konkreten
 * Nutzer-Erlebnis zuzuordnen.
 */
class AdSkipPrebuffer(
    private val context: Context,
    private val scope: CoroutineScope,
    val station: Station,
    private val voskModelCache: VoskModelCache,
) {
    private var exoPlayer: ExoPlayer? = null
    private var analyzer: StreamAnalyzer? = null

    /**
     * Verbindet und startet die Hintergrund-Klassifikation. Kein Vosk-Modell
     * fuer die Sprache der Kategorie von `station` heruntergeladen? Dann
     * bleibt diese Instanz dauerhaft "nicht bereit" (siehe `readyForPromotion`)
     * - das Feature deaktiviert sich fuer diese Pause faktisch selbst, kein
     * Fehlerzustand, analog zu "VAD nicht verfuegbar" im Docker-Vorbild.
     */
    fun start() {
        val language = SttSettings.resolveLanguage(context, station.category)
        val cfg = SttSettings.getLanguages(context)[language] ?: return
        val modelPath = VoskModelManager.modelPathOrNull(context, language, cfg.modelUrl) ?: return

        exoPlayer = ExoPlayer.Builder(context).build().apply {
            setWakeMode(C.WAKE_MODE_NETWORK)
            volume = 0f // hoerbar wird das erst bei tatsaechlicher Uebernahme, siehe takePlayer()
            setMediaItem(MediaItem.fromUri(station.url))
            prepare()
            playWhenReady = true // muss wirklich laufen, sonst bekommt die Analyse keine echten Daten
        }
        analyzer = StreamAnalyzer(scope).also {
            it.start(
                station.url, modelPath, language, voskModelCache,
                cfg.ratioToConfirmSpeech, cfg.ratioToConfirmMusic, station.name,
                fingerprintDb = null,
            )
        }
    }

    val readyForPromotion: Boolean
        get() = analyzer?.status?.value == PlaybackStatus.MUSIC

    /**
     * Gibt den vorbereiteten Player zur Uebernahme frei - der Aufrufer
     * uebernimmt Listener/Lautstaerke (siehe `PlaybackService.resumeFromNewsBreak()`),
     * dieselbe Regel wie bei `preloadedPlayer`. Stoppt den Analyzer, der ab
     * hier durch die normale Analyse des Aufrufers ersetzt wird (`refreshAnalyzer()`).
     */
    fun takePlayer(): ExoPlayer? {
        val p = exoPlayer
        exoPlayer = null
        analyzer?.stop()
        analyzer = null
        return p
    }

    /** Verwirft Player + Analyzer komplett (Pause vorbei ohne Uebernahme, Nutzer hat manuell weggeschaltet, o.ae.). */
    fun stop() {
        exoPlayer?.release()
        exoPlayer = null
        analyzer?.stop()
        analyzer = null
    }
}
