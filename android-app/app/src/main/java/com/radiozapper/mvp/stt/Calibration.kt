package com.radiozapper.mvp.stt

// Wie im Docker-Projekt (dessen _THRESHOLD_MARGIN_RATIO=0.7) Richtung
// Sprache-Seite gewichtet: ratioToConfirmSpeech liegt naeher an speechMin
// als ratioToConfirmMusic an musicMax - ein zu frueh angenommenes
// "Sprache" ist stoerender als ein etwas zu spaet erkanntes.
private const val MARGIN_RATIO = 0.7

// Wie viele Samples pro Seite mindestens vorliegen muessen, bevor ueberhaupt
// ein Vorschlag angeboten wird (Review-Befund 4, siehe SESSION.md): der
// Rohwert kennt nur wenige diskrete Stufen, bei 2-3 Samples ist jede Aussage
// ueber die Verteilung Zufall. 20 Samples = ca. 10 Sekunden Sammeln.
private const val MIN_SAMPLES_PER_LEVEL = 20

// Statt min()/max() wird das Perzentil an dieser Stelle genommen: ein
// einziger Uebergangs-Sample (Moderator holt Luft, Jingle laeuft an) hat
// sonst gereicht, um musicMax >= speechMin und damit "Verteilungen
// ueberlappen" zu erzeugen - der Grund, warum der Erfolgspfad des Wizards
// im Live-Test aus Fortsetzung 5 nie zu sehen war.
private const val OUTLIER_PERCENTILE = 0.1

enum class CalibrationLevel { SPEECH, MUSIC }

data class CalibrationSuggestion(
    val ratioToConfirmSpeech: Double,
    val ratioToConfirmMusic: Double,
    /** true, wenn sich Sprache- und Musik-Samples ueberlappen - kein sauberer Vorschlag moeglich, siehe suggestRatios(). */
    val overlapping: Boolean,
)

/**
 * Schlaegt ratioToConfirmSpeech/ratioToConfirmMusic aus gesammelten
 * Kalibrierungs-Samples vor - Pendant zu
 * stt_filter.suggest_confidence_threshold() im Docker-Projekt, hier aber
 * auf ZWEI Schwellen statt einer angewandt: Android hat kein VAD+STT-
 * Konfidenz-Duo, der Wizard kalibriert stattdessen direkt die vorhandene
 * Hysterese-Bandbreite um StreamAnalyzer.speechRatio (siehe dessen
 * Klassen-Doc - dasselbe Rohsignal, das auch das "Bullshitometer" zeigt).
 *
 * `musicHigh`/`speechLow` trennen die beiden Verteilungen; die Luecke
 * dazwischen wird im Verhaeltnis MARGIN_RATIO aufgeteilt - beide
 * Schwellen bleiben innerhalb der Luecke, keine haengt direkt am Rand
 * einer Verteilung. Ueberlappen sich Sprache- und Musik-Samples
 * (musicHigh >= speechLow, z.B. bei mehrdeutigen Samples), ist KEIN
 * sauberer Vorschlag moeglich - overlapping=true signalisiert das der UI,
 * die dann vor dem Uebernehmen warnt statt einen moeglicherweise falschen
 * Vorschlag unkommentiert anzubieten.
 *
 * Seit dem Phase-8-Review (Befund 4, siehe SESSION.md) sind `musicHigh`/
 * `speechLow` NICHT mehr `max()`/`min()`, sondern robuste Perzentile
 * (OUTLIER_PERCENTILE) - und es braucht MIN_SAMPLES_PER_LEVEL Samples pro
 * Seite. Vorher genuegte EIN Uebergangswert, um jeden Vorschlag als
 * "ueberlappend" zu verwerfen.
 */
fun suggestRatios(speechSamples: List<Double>, musicSamples: List<Double>): CalibrationSuggestion? {
    if (speechSamples.size < MIN_SAMPLES_PER_LEVEL || musicSamples.size < MIN_SAMPLES_PER_LEVEL) return null

    val musicHigh = percentile(musicSamples, 1.0 - OUTLIER_PERCENTILE)
    val speechLow = percentile(speechSamples, OUTLIER_PERCENTILE)

    if (musicHigh >= speechLow) {
        return CalibrationSuggestion(
            ratioToConfirmSpeech = speechLow,
            ratioToConfirmMusic = musicHigh,
            overlapping = true,
        )
    }

    val gap = speechLow - musicHigh
    return CalibrationSuggestion(
        ratioToConfirmMusic = musicHigh + gap * (1 - MARGIN_RATIO),
        ratioToConfirmSpeech = musicHigh + gap * MARGIN_RATIO,
        overlapping = false,
    )
}

/** Perzentil per naechstliegendem Rang (kein Interpolieren - die Rohwerte sind ohnehin diskrete Stufen). */
private fun percentile(samples: List<Double>, fraction: Double): Double {
    val sorted = samples.sorted()
    val index = Math.round(fraction * (sorted.size - 1)).toInt().coerceIn(0, sorted.size - 1)
    return sorted[index]
}
