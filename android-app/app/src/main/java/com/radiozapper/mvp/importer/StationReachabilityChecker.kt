// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.importer

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.util.Log
import com.radiozapper.mvp.model.StationRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

private const val TAG = "StationReachabilityChecker"

// Vorbild: station_import.check_reachable() im Docker-Projekt (siehe dessen
// CLAUDE.md, Abschnitt "Sender-Import") - CHECK_WINDOW Sekunden lang wirklich
// mitlesen und verlangen, dass in den letzten CHECK_TAIL Sekunden noch Audio
// ankam. Ein Stream, der nur einen Fragment-Vorrat ausschuettet und danach
// verstummt (der urspruengliche BBC-Radio-Scotland-Fall), besteht einen
// reinen "kommt ueberhaupt was"-Check, faellt hier aber durch.
private const val CHECK_WINDOW_MS = 8_000L
private const val CHECK_TAIL_MS = 3_000L
private const val CHECK_MIN_SECONDS = 3.0
private const val CODEC_TIMEOUT_US = 20_000L

// Deutlich weniger parallel als die 10 im Python-Vorbild: MediaCodec-Dekodierung
// ist auf dem Handy teurer als ein ffmpeg-Prozess auf dem Server, und nebenbei
// laeuft im selben Prozess meist noch Wiedergabe + Vosk-Analyse des aktuellen
// Senders.
private const val CHECK_CONCURRENCY = 3

sealed class CheckState {
    data object Idle : CheckState()
    data class Checking(val checked: Int, val total: Int) : CheckState()
    data class Done(val checked: Int, val unreachable: Int) : CheckState()
}

/**
 * Separater, manuell ausgeloester Erreichbarkeits-Check fuer importierte
 * Sender (siehe StationImporter) - NICHT Teil des Imports selbst, siehe
 * dessen Klassen-Doc fuer die Begruendung. Ergebnis ist rein informativ
 * (unreachableIds), es wird nichts automatisch geloescht - das bleibt eine
 * bewusste Nutzer-Entscheidung ueber den bestehenden Loeschen-Button in der
 * Verwaltungs-Activity.
 */
object StationReachabilityChecker {
    private val _state = MutableStateFlow<CheckState>(CheckState.Idle)
    val state: StateFlow<CheckState> = _state

    private val _unreachableIds = MutableStateFlow<Set<String>>(emptySet())
    val unreachableIds: StateFlow<Set<String>> = _unreachableIds

    private val lock = Any()

    // Ein zweiter, parallel gestarteter Lauf (Doppelklick auf den Knopf, oder
    // Neustart der Activity waehrend ein Lauf noch laeuft) wuerde sich
    // dieselben Flows teilen und dem ersten die Ergebnisse unter den Fuessen
    // wegziehen (Review-Befund 15, siehe SESSION.md).
    @Volatile
    private var running = false

    suspend fun checkCategory(category: String) {
        synchronized(lock) {
            if (running) {
                Log.d(TAG, "Erreichbarkeits-Check laeuft bereits - zweiter Start ignoriert")
                return
            }
            running = true
        }
        try {
            runCheck(category)
        } finally {
            running = false
        }
    }

    private suspend fun runCheck(category: String) {
        val targets = StationRepository.stations.value.filter { it.category == category }
        _unreachableIds.value = emptySet()

        if (targets.isEmpty()) {
            _state.value = CheckState.Done(checked = 0, unreachable = 0)
            return
        }
        _state.value = CheckState.Checking(checked = 0, total = targets.size)

        var checkedCount = 0
        val unreachable = mutableSetOf<String>()
        val semaphore = Semaphore(CHECK_CONCURRENCY)

        coroutineScope {
            targets.map { station ->
                async(Dispatchers.IO) {
                    semaphore.withPermit {
                        val reachable = checkReachable(station.url)
                        synchronized(lock) {
                            checkedCount++
                            if (!reachable) unreachable += station.id
                            _unreachableIds.value = unreachable.toSet()
                            _state.value = CheckState.Checking(checked = checkedCount, total = targets.size)
                        }
                    }
                }
            }.awaitAll()
        }

        _state.value = CheckState.Done(checked = targets.size, unreachable = unreachable.size)
    }

    /**
     * True, wenn `url` ueber ein CHECK_WINDOW_MS langes Zeitfenster dauerhaft
     * Audio liefert, inklusive der letzten CHECK_TAIL_MS. Wall-Clock-begrenzt
     * (nicht ueber die dekodierte Audio-Laenge) - siehe Klassen-Doc.
     *
     * withTimeoutOrNull() als Sicherheitsnetz: MediaExtractor.setDataSource()
     * kann bei einer nicht routbaren Adresse laenger blockieren als das
     * eigentliche Pruepffenster (OS-Verbindungstimeout statt CHECK_WINDOW_MS) -
     * ohne die Grenze wuerde ein einziger toter Kandidat den gesamten
     * Check-Lauf verzoegern.
     */
    private suspend fun checkReachable(url: String): Boolean =
        withTimeoutOrNull(CHECK_WINDOW_MS + 5_000) {
            withContext(Dispatchers.IO) { decodeCheck(url) }
        } ?: false

    private fun decodeCheck(url: String): Boolean {
        var extractor: MediaExtractor? = null
        var codec: MediaCodec? = null
        try {
            extractor = MediaExtractor()
            extractor.setDataSource(url)

            val trackIndex = (0 until extractor.trackCount).firstOrNull { index ->
                extractor.getTrackFormat(index).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
            } ?: return false

            val format = extractor.getTrackFormat(trackIndex)
            extractor.selectTrack(trackIndex)
            val mime = format.getString(MediaFormat.KEY_MIME)!!

            codec = MediaCodec.createDecoderByType(mime)
            codec.configure(format, null, null, 0)
            codec.start()

            val bufferInfo = MediaCodec.BufferInfo()
            var sawInputEos = false
            var sawOutputEos = false
            var firstPtsUs = -1L
            var lastPtsUs = -1L
            var lastDataAtMs = 0L
            val deadlineMs = System.currentTimeMillis() + CHECK_WINDOW_MS

            while (System.currentTimeMillis() < deadlineMs && !sawOutputEos) {
                if (!sawInputEos) {
                    val inputIndex = codec.dequeueInputBuffer(CODEC_TIMEOUT_US)
                    if (inputIndex >= 0) {
                        val inputBuffer = codec.getInputBuffer(inputIndex)!!
                        val sampleSize = extractor.readSampleData(inputBuffer, 0)
                        if (sampleSize < 0) {
                            codec.queueInputBuffer(inputIndex, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            sawInputEos = true
                        } else {
                            codec.queueInputBuffer(inputIndex, 0, sampleSize, extractor.sampleTime, 0)
                            extractor.advance()
                        }
                    }
                }

                val outputIndex = codec.dequeueOutputBuffer(bufferInfo, CODEC_TIMEOUT_US)
                if (outputIndex >= 0) {
                    if (bufferInfo.size > 0) {
                        if (firstPtsUs < 0) firstPtsUs = bufferInfo.presentationTimeUs
                        lastPtsUs = bufferInfo.presentationTimeUs
                        lastDataAtMs = System.currentTimeMillis()
                    }
                    codec.releaseOutputBuffer(outputIndex, false)
                    if (bufferInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) {
                        sawOutputEos = true
                    }
                }
            }

            if (firstPtsUs < 0) return false // ueberhaupt kein Audio dekodiert
            val decodedSeconds = (lastPtsUs - firstPtsUs) / 1_000_000.0
            if (decodedSeconds < CHECK_MIN_SECONDS) return false

            val silentForMs = deadlineMs - lastDataAtMs
            return silentForMs <= CHECK_TAIL_MS
        } catch (e: Exception) {
            Log.d(TAG, "Check fehlgeschlagen fuer $url: ${e.message}")
            return false
        } finally {
            runCatching { codec?.stop() }
            runCatching { codec?.release() }
            runCatching { extractor?.release() }
        }
    }
}
