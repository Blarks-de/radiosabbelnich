package com.radiozapper.mvp.analysis

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.util.Log
import com.radiozapper.mvp.playback.PlaybackStatus
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.coroutines.coroutineContext

private const val TAG = "StreamAnalyzer"

private const val TARGET_SAMPLE_RATE = 16_000
private const val CHUNK_SAMPLES = TARGET_SAMPLE_RATE / 2 // 0.5s je Analyse-Haeppchen
private const val CODEC_TIMEOUT_US = 20_000L

// Glaettung des Roh-Signals (siehe Klassen-Doc weiter unten fuer die Begruendung):
// gleitendes Fenster ueber die letzten SMOOTHING_WINDOW_CHUNKS Haeppchen (je
// CHUNK_SAMPLES/TARGET_SAMPLE_RATE Sekunden), Mehrheitsvotum mit Hysterese statt
// harter "N Sekunden ohne jede Unterbrechung"-Serie. 4.0s liegt in der vom
// Nutzer vorgeschlagenen Spanne von 3-5s; ueber PARAMETER anpassbar zum Tunen.
private const val SMOOTHING_WINDOW_SECONDS = 4.0
private val SMOOTHING_WINDOW_CHUNKS = (SMOOTHING_WINDOW_SECONDS * TARGET_SAMPLE_RATE / CHUNK_SAMPLES).toInt() // 8

// Hysterese (Schmitt-Trigger): unterschiedliche Schwellen fuer "wird zu Sprache"
// vs. "wird zu Musik", damit ein Anteil nahe 50% nicht bei jedem Chunk umkippt.
// RATIO_TO_CONFIRM_SPEECH > RATIO_TO_CONFIRM_MUSIC ist Pflicht (Hysterese-Band).
private const val RATIO_TO_CONFIRM_SPEECH = 0.65
private const val RATIO_TO_CONFIRM_MUSIC = 0.30

/**
 * Dekodiert denselben Stream ein zweites Mal ausschliesslich fuer die
 * Spracherkennung (eigene MediaExtractor/MediaCodec-Instanz, unabhaengig vom
 * ExoPlayer der eigentlichen Wiedergabe in PlaybackService).
 *
 * Bewusste Vereinfachung ggue. dem Docker-Projekt (dort: EIN ffmpeg-Prozess
 * mit zwei Pipes, siehe dessen CLAUDE.md) - dort ist die geteilte
 * ffmpeg-Pipe eine Optimierung, die hier fuer ein "bewusst minimales" MVP
 * nicht nachgebaut wird. Preis: der Stream wird effektiv zweimal ueber das
 * Netz geladen (Play zusaetzlich zu Analyse).
 *
 * Glaettung: das Docker-Projekt zaehlt in radiozapper.py eine einfache Serie
 * ("N Sekunden Sprache IN FOLGE", CONSECUTIVE_SPEECH_TO_SWITCH=3, siehe dessen
 * CLAUDE.md/stt_filter.py) - dort reicht das, weil ein Analysefenster dort 1s
 * lang ist und die Serie nur den einmaligen SWITCH-Trigger ausloest, nicht
 * einen laufend angezeigten Status. Hier wuerde dieselbe Serie bei 0.5s-
 * Haeppchen zu haeufig durch ganz normale kurze Sprechpausen (Vosk liefert
 * dann kurz ein leeres Partial-Result) auf 0 zurueckgesetzt und dadurch der
 * ANGEZEIGTE Status flackern (live beobachtet). Deshalb hier bewusst ein
 * gleitendes Mehrheitsvotum mit Hysterese statt einer harten Serie - toleriert
 * einzelne kurze Aussetzer in beide Richtungen, siehe Konstanten oben.
 */
class StreamAnalyzer(private val scope: CoroutineScope) {

    private val _status = MutableStateFlow(PlaybackStatus.IDLE)
    val status: StateFlow<PlaybackStatus> = _status

    private var job: Job? = null

    fun start(url: String, modelPath: String) {
        stop()
        _status.value = PlaybackStatus.CONNECTING
        job = scope.launch(Dispatchers.IO) {
            runAnalysis(url, modelPath)
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        _status.value = PlaybackStatus.IDLE
    }

    private suspend fun runAnalysis(url: String, modelPath: String) {
        var extractor: MediaExtractor? = null
        var codec: MediaCodec? = null
        var model: Model? = null
        var recognizer: Recognizer? = null

        try {
            model = Model(modelPath)
            recognizer = Recognizer(model, TARGET_SAMPLE_RATE.toFloat())

            extractor = MediaExtractor()
            extractor.setDataSource(url)

            val trackIndex = (0 until extractor.trackCount).firstOrNull { index ->
                extractor.getTrackFormat(index).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
            } ?: throw IllegalStateException("Kein Audio-Track im Stream gefunden")

            val format = extractor.getTrackFormat(trackIndex)
            extractor.selectTrack(trackIndex)
            val mime = format.getString(MediaFormat.KEY_MIME)!!

            codec = MediaCodec.createDecoderByType(mime)
            codec.configure(format, null, null, 0)
            codec.start()

            var sourceRate = format.getInteger(MediaFormat.KEY_SAMPLE_RATE)
            var channelCount = format.getInteger(MediaFormat.KEY_CHANNEL_COUNT)

            val resampler = MonoResampler(TARGET_SAMPLE_RATE)
            val chunkBuffer = ArrayDeque<Short>()

            // Gleitendes Fenster der letzten Chunk-Verdikte (true = Text erkannt)
            // fuer das Mehrheitsvotum, siehe Klassen-Doc oben.
            val recentChunks = ArrayDeque<Boolean>()
            var confirmedSpeech = false
            var hasConfirmedOnce = false

            val bufferInfo = MediaCodec.BufferInfo()
            var sawInputEos = false
            var sawOutputEos = false

            while (coroutineContext.isActive && !sawOutputEos) {
                if (!sawInputEos) {
                    val inputIndex = codec.dequeueInputBuffer(CODEC_TIMEOUT_US)
                    if (inputIndex >= 0) {
                        val inputBuffer = codec.getInputBuffer(inputIndex)!!
                        val sampleSize = extractor.readSampleData(inputBuffer, 0)
                        if (sampleSize < 0) {
                            codec.queueInputBuffer(inputIndex, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            sawInputEos = true
                        } else {
                            val presentationTimeUs = extractor.sampleTime
                            codec.queueInputBuffer(inputIndex, 0, sampleSize, presentationTimeUs, 0)
                            extractor.advance()
                        }
                    }
                }

                val outputIndex = codec.dequeueOutputBuffer(bufferInfo, CODEC_TIMEOUT_US)
                when {
                    outputIndex >= 0 -> {
                        val outputBuffer = codec.getOutputBuffer(outputIndex)
                        if (outputBuffer != null && bufferInfo.size > 0) {
                            outputBuffer.order(ByteOrder.LITTLE_ENDIAN)
                            outputBuffer.position(bufferInfo.offset)
                            outputBuffer.limit(bufferInfo.offset + bufferInfo.size)
                            val shorts = ShortArray(bufferInfo.size / 2)
                            outputBuffer.asShortBuffer().get(shorts)

                            val mono = downmixToMono(shorts, channelCount)
                            val resampled = resampler.process(mono, sourceRate)
                            for (s in resampled) chunkBuffer.addLast(s)

                            while (chunkBuffer.size >= CHUNK_SAMPLES) {
                                val chunk = ShortArray(CHUNK_SAMPLES) { chunkBuffer.removeFirst() }

                                val endOfUtterance = recognizer.acceptWaveForm(chunk, chunk.size)
                                val text = extractText(
                                    if (endOfUtterance) recognizer.getResult() else recognizer.getPartialResult()
                                )

                                recentChunks.addLast(text.isNotBlank())
                                if (recentChunks.size > SMOOTHING_WINDOW_CHUNKS) {
                                    recentChunks.removeFirst()
                                }

                                if (recentChunks.size >= SMOOTHING_WINDOW_CHUNKS) {
                                    val speechRatio = recentChunks.count { it }.toDouble() / recentChunks.size
                                    val shouldBeSpeech = when {
                                        !hasConfirmedOnce -> speechRatio >= 0.5
                                        confirmedSpeech -> speechRatio > RATIO_TO_CONFIRM_MUSIC
                                        else -> speechRatio >= RATIO_TO_CONFIRM_SPEECH
                                    }
                                    if (!hasConfirmedOnce || shouldBeSpeech != confirmedSpeech) {
                                        confirmedSpeech = shouldBeSpeech
                                        hasConfirmedOnce = true
                                        _status.value = if (shouldBeSpeech) PlaybackStatus.SPEECH else PlaybackStatus.MUSIC
                                    }
                                }
                            }
                        }
                        codec.releaseOutputBuffer(outputIndex, false)
                        if (bufferInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) {
                            sawOutputEos = true
                        }
                    }

                    outputIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                        val newFormat = codec.outputFormat
                        sourceRate = newFormat.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                        channelCount = newFormat.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                    }

                    outputIndex == MediaCodec.INFO_TRY_AGAIN_LATER -> {
                        // Noch keine Daten - naechste Iteration erneut versuchen.
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Analyse abgebrochen fuer $url", e)
            _status.value = PlaybackStatus.ERROR
        } finally {
            runCatching { codec?.stop() }
            runCatching { codec?.release() }
            runCatching { extractor?.release() }
            runCatching { recognizer?.close() }
            runCatching { model?.close() }
        }
    }

    private fun extractText(json: String): String = try {
        val obj = JSONObject(json)
        obj.optString("partial", obj.optString("text", ""))
    } catch (e: Exception) {
        ""
    }
}
