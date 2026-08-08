package com.radiozapper.mvp.fingerprint

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Shazam-artiges Audio-Fingerprinting, um WIEDERHOLTE Audio-Clips (Jingles,
 * Werbespots) zuverlässig wiederzuerkennen - direktes Kotlin-Pendant zu
 * fingerprint.py im Docker-Projekt (siehe dessen Moduldoc für die volle
 * Herleitung), auf 16kHz statt 44.1kHz umgerechnet (siehe Konstanten unten).
 *
 * Prinzip ("Constellation Map"): Spektrogramm -> echte 2D-lokale-Maxima in
 * Zeit UND Frequenz finden ("Sterne am Himmel") -> Peaks paarweise
 * verhashen (robust gegen Rauschen/Lautstärke, weil nur relative
 * Peak-Positionen zählen) -> Hashes gegen eine DB bekannter Clips matchen
 * (siehe FingerprintDb.kt).
 *
 * WICHTIG, warum 2D-Peaks (Zeit + Frequenz) und nicht nur "lautester Bin pro
 * Frame": Die ursprüngliche Python-Version wählte pro Zeitframe schlicht die
 * stärksten Bins - das matchte auf echtem Broadcast-Radio praktisch JEDES
 * Sprache-Fenster gegen JEDES andere (verifiziert: 351 von 351 möglichen
 * Paaren aus 26 echten, unterschiedlichen Clips "matchten"). Ursache:
 * menschliche Sprache hat generisch ähnliche Formant-Energie (die immer
 * gleichen Frequenzbereiche sind "laut"), das macht "lautester Bin" zu einem
 * content-UNABHÄNGIGEN Merkmal. Fix: nur Punkte behalten, die eine ECHTE
 * lokale Spitze in einer Zeit-Frequenz-Nachbarschaft sind - das filtert
 * dauerhaft anwesende/generische Energie zuverlässig raus und behält nur
 * echte "Landmarken"-Ereignisse (Onsets, markante Töne). Damit sank die
 * Fehlerquote im selben Test auf 1 von 351 Paaren. Dieser Fix ist
 * PFLICHT-Ausgangspunkt für diese Kotlin-Umsetzung, keine Kür - siehe
 * `KeinSabbelRadio_Android_Fahrplan.md` Phase 4 und `../SESSION.md`,
 * "Fingerprint-Algorithmus überarbeitet".
 */
object Fingerprint {
    // Alle Werte proportional zu fingerprint.py neu hergeleitet (16kHz statt
    // 44.1kHz) - siehe android-app/README.md, Abschnitt "Audio-Fingerprinting"
    // für die vollständige Umrechnungstabelle. FRAME_SIZE muss eine
    // Zweierpotenz bleiben (eigene Radix-2-FFT, siehe fft() unten).
    const val FRAME_SIZE = 512
    const val HOP_SIZE = 256
    const val MIN_FREQ_HZ = 200
    const val PEAK_NEIGHBORHOOD_TIME = 5
    const val PEAK_NEIGHBORHOOD_FREQ = 21
    const val PEAK_AMP_MIN_FACTOR = 3.0
    const val FAN_VALUE = 5
    const val TARGET_ZONE_FRAMES = 30
    // Startwert, identisch zum Python-Wert - NICHT automatisch gültig für
    // die andere Samplerate/Frame-Größe hier, siehe README "Bekannte
    // Grenzen". Das Docker-Projekt kam auf 25 durch einen echten
    // 351-Paar-Cross-Match-Test, den es für diese Portierung (noch) nicht
    // gibt.
    const val MIN_HASH_MATCHES = 25

    /** Öffentliche Funktion: PCM-Clip -> Liste von (hash, ankerOffsetFrame). */
    fun fingerprintClip(pcm: ShortArray, sampleRate: Int): List<Pair<String, Int>> {
        val peaks = spectrogramPeaks(pcm, sampleRate)
        return generateHashes(peaks)
    }

    /** STFT-Magnitudenspektrum, ein Frame pro Zeile (Frame x Bin). FRAME_SIZE/HOP_SIZE sind feste Konstanten, die Samplerate geht nur in spectrogramPeaks() (minFreqBin-Umrechnung) ein. */
    private fun spectrogram(pcm: ShortArray): Array<DoubleArray> {
        if (pcm.size < FRAME_SIZE) return emptyArray()
        val samples = DoubleArray(pcm.size) { pcm[it] / 32768.0 }
        val nFrames = 1 + (samples.size - FRAME_SIZE) / HOP_SIZE
        val window = hanningWindow(FRAME_SIZE)
        val numBins = FRAME_SIZE / 2 + 1
        return Array(nFrames) { i ->
            val start = i * HOP_SIZE
            val re = DoubleArray(FRAME_SIZE) { j -> samples[start + j] * window[j] }
            val im = DoubleArray(FRAME_SIZE)
            fft(re, im)
            DoubleArray(numBins) { b -> hypot(re[b], im[b]) }
        }
    }

    private fun hanningWindow(size: Int): DoubleArray =
        DoubleArray(size) { i -> 0.5 - 0.5 * cos(2.0 * PI * i / (size - 1)) }

    /**
     * In-place iterative Radix-2-Cooley-Tukey-FFT (Standardalgorithmus,
     * bewusst keine externe Bibliothek wie JTransforms - siehe Klassen-Doc
     * oben, "Eigene FFT statt neuer Dependency"). `re.size` MUSS eine
     * Zweierpotenz sein (hier immer FRAME_SIZE=512).
     */
    private fun fft(re: DoubleArray, im: DoubleArray) {
        val n = re.size
        var j = 0
        for (i in 1 until n) {
            var bit = n shr 1
            while (j and bit != 0) {
                j = j xor bit
                bit = bit shr 1
            }
            j = j or bit
            if (i < j) {
                val tRe = re[i]; re[i] = re[j]; re[j] = tRe
                val tIm = im[i]; im[i] = im[j]; im[j] = tIm
            }
        }

        var len = 2
        while (len <= n) {
            val ang = -2.0 * PI / len
            val wRe = cos(ang)
            val wIm = sin(ang)
            var i = 0
            while (i < n) {
                var curRe = 1.0
                var curIm = 0.0
                for (k in 0 until len / 2) {
                    val uRe = re[i + k]
                    val uIm = im[i + k]
                    val vRe = re[i + k + len / 2] * curRe - im[i + k + len / 2] * curIm
                    val vIm = re[i + k + len / 2] * curIm + im[i + k + len / 2] * curRe
                    re[i + k] = uRe + vRe
                    im[i + k] = uIm + vIm
                    re[i + k + len / 2] = uRe - vRe
                    im[i + k + len / 2] = uIm - vIm
                    val nextCurRe = curRe * wRe - curIm * wIm
                    val nextCurIm = curRe * wIm + curIm * wRe
                    curRe = nextCurRe
                    curIm = nextCurIm
                }
                i += len
            }
            len = len shl 1
        }
    }

    /**
     * Liefert Liste von (frameIdx, freqBin) für echte 2D-Landmarken (lokale
     * Maxima in Zeit UND Frequenz, siehe Klassen-Doc oben).
     */
    private fun spectrogramPeaks(pcm: ShortArray, sampleRate: Int): List<Pair<Int, Int>> {
        val spec = spectrogram(pcm)
        if (spec.size < 2) return emptyList()

        val numBins = spec[0].size
        val minFreqBin = (MIN_FREQ_HZ / (sampleRate.toDouble() / FRAME_SIZE)).toInt()
        if (minFreqBin >= numBins) return emptyList()
        val regionWidth = numBins - minFreqBin

        var maxVal = 0.0
        val logSpec = Array(spec.size) { t ->
            DoubleArray(regionWidth) { f ->
                val raw = spec[t][f + minFreqBin]
                if (raw > maxVal) maxVal = raw
                Math.log1p(raw)
            }
        }
        if (maxVal < 1e-6) return emptyList()

        val maskMax = localMaxMask(logSpec, PEAK_NEIGHBORHOOD_TIME, PEAK_NEIGHBORHOOD_FREQ)

        var sum = 0.0
        var count = 0
        for (row in logSpec) for (v in row) { sum += v; count++ }
        val mean = if (count > 0) sum / count else 0.0
        var varSum = 0.0
        for (row in logSpec) for (v in row) varSum += (v - mean) * (v - mean)
        val std = if (count > 0) sqrt(varSum / count) else 0.0
        val threshold = mean + PEAK_AMP_MIN_FACTOR * std

        val peaks = mutableListOf<Pair<Int, Int>>()
        for (t in logSpec.indices) {
            val row = logSpec[t]
            val maskRow = maskMax[t]
            for (f in row.indices) {
                if (maskRow[f] && row[f] > threshold) {
                    peaks.add(t to (f + minFreqBin))
                }
            }
        }
        return peaks
    }

    /**
     * True an Stellen, die das Maximum ihrer (tWin x fWin)-Nachbarschaft
     * sind - reine Kotlin-Sliding-Window-Operation, Pendant zu
     * scipy.ndimage.maximum_filter (das die Python-Version aus demselben
     * "keine neue Dependency für eine simple Operation"-Grund selbst schon
     * vermeidet, siehe fingerprint.py).
     */
    private fun localMaxMask(arr: Array<DoubleArray>, tWin: Int, fWin: Int): Array<BooleanArray> {
        val nT = arr.size
        val nF = if (nT > 0) arr[0].size else 0
        val padT = tWin / 2
        val padF = fWin / 2

        val padded = Array(nT + 2 * padT) { DoubleArray(nF + 2 * padF) { Double.NEGATIVE_INFINITY } }
        for (t in 0 until nT) for (f in 0 until nF) padded[t + padT][f + padF] = arr[t][f]

        val maxed = Array(nT) { DoubleArray(nF) { Double.NEGATIVE_INFINITY } }
        for (dt in 0 until tWin) {
            for (t in 0 until nT) {
                val row = padded[t + dt]
                val maxRow = maxed[t]
                for (df in 0 until fWin) {
                    for (f in 0 until nF) {
                        val v = row[f + df]
                        if (v > maxRow[f]) maxRow[f] = v
                    }
                }
            }
        }
        return Array(nT) { t -> BooleanArray(nF) { f -> arr[t][f] >= maxed[t][f] } }
    }

    /** Paart Peaks zu Hashes: (hashString, ankerOffsetFrame). */
    private fun generateHashes(peaks: List<Pair<Int, Int>>): List<Pair<String, Int>> {
        val sorted = peaks.sortedBy { it.first }
        val hashes = mutableListOf<Pair<String, Int>>()
        for (i in sorted.indices) {
            val (t1, f1) = sorted[i]
            var fanned = 0
            for (j in i + 1 until sorted.size) {
                val (t2, f2) = sorted[j]
                val dt = t2 - t1
                if (dt <= 0) continue
                if (dt > TARGET_ZONE_FRAMES) break
                hashes.add("$f1-$f2-$dt" to t1)
                fanned++
                if (fanned >= FAN_VALUE) break
            }
        }
        return hashes
    }
}
