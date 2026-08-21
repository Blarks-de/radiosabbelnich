package com.radiozapper.mvp.newsbreak

import java.time.LocalDateTime
import java.time.temporal.ChronoUnit
import kotlin.math.abs

/**
 * `requireSpeechInWindow`/`speechGateWindowMinutes`/`speechGateStreakSeconds`
 * und `adPrebufferEnabled`/`adPrebufferLeadSeconds` sind die Android-Pendants
 * zu `require_speech_in_window`/`speech_gate_window_minutes`/
 * `speech_gate_streak` bzw. `ad_prebuffer_enabled`/`ad_prebuffer_lead_seconds`
 * im Docker-Projekt (siehe dessen `ARCHITECTURE.md`, Abschnitte
 * "Sprache-Gate für den Pause-Start" und "Werbeblock-Vorbuffering") - Details
 * zur Verdrahtung in `PlaybackService.kt`. `speechGateStreakSeconds` heißt
 * bewusst nicht wie im Vorbild nach einer Fenster-ANZAHL, weil Android dafür
 * `StreamAnalyzer.rawSpeechStreakSeconds` (kontinuierliche Sekunden, siehe
 * dort) statt eines diskreten 1s-Fenster-Zählers liefert - beide Defaults
 * (3) meinen inhaltlich dasselbe, nur in robusterer Einheit.
 */
data class NewsBreakConfig(
    val enabled: Boolean,
    val windowMinutes: Double,
    val requireSpeechInWindow: Boolean = false,
    val speechGateWindowMinutes: Double = 2.0,
    val speechGateStreakSeconds: Double = 3.0,
    val adPrebufferEnabled: Boolean = false,
    val adPrebufferLeadSeconds: Double = 20.0,
)

/**
 * Reine Domänenlogik ("Nachrichten-Pause": zur vollen/halben Stunde statt des
 * Radiosenders eine zufällige MP3 aus einem lokalen Ordner) - direktes Pendant
 * zu news_break.py im Docker-Projekt, KEIN Zugriff auf PlaybackService/SAF.
 * Die eigentliche Audio-Umschaltung (Sender pausieren, MP3 abspielen,
 * zurückschalten) bleibt bewusst in PlaybackService.kt, weil dort schon die
 * gesamte Switch-Infrastruktur existiert, die hier nicht dupliziert werden
 * soll - exakt dieselbe Begründung wie im Vorbild.
 *
 * enabled_hours (Ruhezeiten) des Docker-Vorbilds bewusst NICHT übernommen
 * (Nutzerentscheidung) - kleinere UI, passt zum bisherigen "bewusst minimal"-
 * Ansatz der Android-App, bei Bedarf später ergänzbar.
 */
object NewsBreak {
    // Wie viele zuletzt gespielte Dateien pickRandom() bei der Zufallsauswahl
    // ausschließt - dieselbe Begründung wie im Docker-Projekt: klein genug,
    // dass auch kleinere Ordner (4-5 Dateien) noch eine echte Auswahl behalten,
    // groß genug, dass eine Datei nicht schon nach 1-2 Wechseln wieder drankommt.
    const val RECENT_HISTORY_SIZE = 3

    /** Die näher gelegene der drei Kandidaten-Grenzen: die volle Stunde davor, :30 dieser Stunde, oder die volle Stunde danach. */
    private fun nearestHalfHour(now: LocalDateTime): LocalDateTime {
        val base = now.withMinute(0).withSecond(0).withNano(0)
        val candidates = listOf(base, base.plusMinutes(30), base.plusHours(1))
        return candidates.minBy { abs(ChronoUnit.SECONDS.between(now, it)) }
    }

    /**
     * Liefert eine stabile Kennung ("Slot-ID") für das aktuell laufende
     * Nachrichten-Pause-Fenster, oder null, falls gerade keins aktiv ist.
     *
     * Die Slot-ID ist dieselbe über die gesamte Fensterdauer (die
     * nächstgelegene :00/:30 als String) - der Aufrufer merkt sich pro Slot,
     * ob er schon bedient wurde (MP3 zu Ende, Fenster abgelaufen, Nutzer hat
     * manuell weggeschaltet: in jedem Fall wird derselbe Slot nicht ein
     * zweites Mal bedient, siehe PlaybackService.newsBreakServedSlot).
     */
    fun activeSlot(cfg: NewsBreakConfig, now: LocalDateTime = LocalDateTime.now()): String? {
        if (!cfg.enabled) return null
        if (cfg.windowMinutes <= 0) return null

        val slot = nearestHalfHour(now)
        val diffSeconds = abs(ChronoUnit.SECONDS.between(now, slot))
        return if (diffSeconds <= cfg.windowMinutes * 60) slot.toString() else null
    }

    /**
     * Liefert eine zufällige Datei aus `files`, oder null falls `files` leer
     * ist (Ordner-Zugriff selbst - leer/nicht lesbar/kein Ordner gewählt -
     * bleibt bei NewsBreakSettings, siehe dessen Doc).
     *
     * `recent` (die zuletzt gespielten Schlüssel, siehe RECENT_HISTORY_SIZE)
     * wird bei der Auswahl vermieden, sofern danach noch mindestens eine Datei
     * übrig bleibt. Enthält `files` insgesamt nicht mehr Einträge als `recent`
     * (z.B. nur 1-2 MP3s insgesamt), lässt der Ausschluss nichts mehr übrig -
     * dann lieber eine Wiederholung als eine fehlschlagende Nachrichten-Pause.
     */
    fun <T> pickRandom(files: List<T>, recent: List<String>, keyOf: (T) -> String): T? {
        if (files.isEmpty()) return null
        val candidates = files.filter { keyOf(it) !in recent }.ifEmpty { files }
        return candidates.random()
    }
}
