package com.radiozapper.mvp.stt

// Wie im Docker-Projekt (dessen _THRESHOLD_MARGIN_RATIO=0.7) Richtung
// Sprache-Seite gewichtet: ratioToConfirmSpeech liegt naeher an speechMin
// als ratioToConfirmMusic an musicMax - ein zu frueh angenommenes
// "Sprache" ist stoerender als ein etwas zu spaet erkanntes.
private const val MARGIN_RATIO = 0.7

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
 * musicMax/speechMin trennen die beiden Verteilungen; die Luecke
 * dazwischen wird im Verhaeltnis MARGIN_RATIO aufgeteilt - beide
 * Schwellen bleiben innerhalb der Luecke, keine haengt direkt am Rand
 * einer Verteilung. Ueberlappen sich Sprache- und Musik-Samples
 * (musicMax >= speechMin, z.B. bei zu wenigen oder mehrdeutigen Samples),
 * ist KEIN sauberer Vorschlag moeglich - overlapping=true signalisiert
 * das der UI, die dann vor dem Uebernehmen warnt statt einen
 * moeglicherweise falschen Vorschlag unkommentiert anzubieten.
 */
fun suggestRatios(speechSamples: List<Double>, musicSamples: List<Double>): CalibrationSuggestion? {
    if (speechSamples.isEmpty() || musicSamples.isEmpty()) return null

    val musicMax = musicSamples.max()
    val speechMin = speechSamples.min()

    if (musicMax >= speechMin) {
        return CalibrationSuggestion(
            ratioToConfirmSpeech = speechMin,
            ratioToConfirmMusic = musicMax,
            overlapping = true,
        )
    }

    val gap = speechMin - musicMax
    return CalibrationSuggestion(
        ratioToConfirmMusic = musicMax + gap * (1 - MARGIN_RATIO),
        ratioToConfirmSpeech = musicMax + gap * MARGIN_RATIO,
        overlapping = false,
    )
}
