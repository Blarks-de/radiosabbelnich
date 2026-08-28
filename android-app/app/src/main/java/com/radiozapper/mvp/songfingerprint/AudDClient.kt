// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.songfingerprint

import android.util.Log
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

private const val TAG = "AudDClient"
private const val AUDD_URL = "https://api.audd.io/recognize/"
private const val AUDD_TIMEOUT_MS = 15_000

// Sicherheitsnetz gegen Kontingent-Verbrauch, direktes Pendant zu
// AUDD_MIN_INTERVAL_SECONDS in song_fingerprint.py (Docker-Projekt): der
// lokale Match-Schwellwert (SongFingerprintDb.MIN_HASH_MATCHES) ist ein
// unkalibrierter Platzhalter -- greift er in der Praxis zu locker/streng,
// koennte matchOrLearn() denselben Song wiederholt als "neu" einstufen und
// bei jedem Intervall einen bezahlten AudD-Request ausloesen.
private const val AUDD_MIN_INTERVAL_MS = 60_000L

data class AudDResult(
    val title: String,
    val artist: String,
    val album: String?,
    val year: Int?,
    val durationSeconds: Int?,
)

/**
 * Kotlin-Pendant zu `song_fingerprint.py`s `audd_lookup()` (Docker-Projekt).
 * `HttpURLConnection` statt OkHttp/Retrofit -- siehe `importer/
 * StationImporter.kt` als bestehendes Muster fuer reines HttpURLConnection
 * in diesem Projekt, keine neue Netzwerk-Dependency noetig. Multipart-Body
 * von Hand gebaut (wie beim Docker-Pendant), WAV-Header ebenfalls von Hand
 * (kein `javax.sound.sampled` auf Android verfuegbar, anders als das
 * Docker-Python-Pendant, das dafuer das eingebaute `wave`-Modul nutzt).
 * JSON-Antwort per `org.json` (im Android-SDK eingebaut, keine neue
 * Dependency).
 */
object AudDClient {
    @Volatile
    private var lastCallAtMs = 0L
    private val cooldownLock = Any()

    /**
     * Schickt `pcm` als WAV an AudD und gibt bei einer erfolgreichen
     * Identifikation ein `AudDResult` zurueck, sonst `null` -- sowohl bei
     * "AudD kennt den Song nicht" als auch bei jedem Netzwerk-/Timeout-/
     * Parse-Fehler (gleiches defensives Muster wie das Docker-Pendant: ein
     * Cloud-Lookup darf den Analyse-Thread nie mitreissen). Respektiert
     * `AUDD_MIN_INTERVAL_MS` -- bei aktivem Cooldown wird gar nicht erst
     * eine Verbindung aufgebaut.
     */
    fun recognize(pcm: ShortArray, sampleRate: Int, apiToken: String): AudDResult? {
        synchronized(cooldownLock) {
            val now = System.currentTimeMillis()
            if (now - lastCallAtMs < AUDD_MIN_INTERVAL_MS) {
                Log.i(TAG, "AudD-Cooldown aktiv (< ${AUDD_MIN_INTERVAL_MS / 1000}s seit letztem Call) -- Anfrage übersprungen.")
                return null
            }
            lastCallAtMs = now
        }

        var connection: HttpURLConnection? = null
        return try {
            val wavBytes = pcmToWav(pcm, sampleRate)
            val boundary = UUID.randomUUID().toString()
            val body = multipartBody(boundary, apiToken, wavBytes)

            connection = (URL(AUDD_URL).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                connectTimeout = AUDD_TIMEOUT_MS
                readTimeout = AUDD_TIMEOUT_MS
                setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            }
            connection.outputStream.use { it.write(body) }

            val responseCode = connection.responseCode
            val responseText = (if (responseCode in 200..299) connection.inputStream else connection.errorStream)
                .bufferedReader().use { it.readText() }

            val json = JSONObject(responseText)
            if (json.optString("status") != "success" || json.isNull("result")) {
                return null // AudD hat den Song nicht erkannt -- kein Fehler
            }
            val result = json.getJSONObject("result")
            val title = result.optString("title").takeIf { it.isNotBlank() } ?: return null
            val artist = result.optString("artist").takeIf { it.isNotBlank() } ?: return null
            val album = result.optString("album").takeIf { it.isNotBlank() }
            val year = result.optString("release_date").takeIf { it.length >= 4 }?.substring(0, 4)?.toIntOrNull()
            AudDResult(title, artist, album, year, parseDurationSeconds(result))
        } catch (e: Exception) {
            // Breiter Fang, exakt wie das Docker-Pendant: Netzwerk, Timeout,
            // kaputtes JSON, unerwartete Antwortstruktur duerfen den
            // Analyse-Thread nie mitreissen, egal welcher Fehler genau auftritt.
            Log.w(TAG, "AudD-Lookup fehlgeschlagen: ${e.message}")
            null
        } finally {
            connection?.disconnect()
        }
    }

    /**
     * AudDs Kernantwort liefert KEINE Songlaenge -- nur mit dem
     * `return=apple_music,spotify`-Multipart-Feld (siehe multipartBody())
     * kommen zwei zusaetzliche, verschachtelte Objekte mit je einem
     * Millisekunden-Feld mit (live geprueft, siehe SESSION.md):
     * `result.spotify.duration_ms` bzw.
     * `result.apple_music.durationInMillis` -- beide praktisch identisch
     * (Rundungsdifferenz im Bereich 1ms), Spotify bevorzugt, weil zuerst
     * im Response-JSON. Keins von beiden ist garantiert vorhanden (nicht
     * jeder Song hat einen Spotify-/Apple-Music-Treffer).
     */
    private fun parseDurationSeconds(result: JSONObject): Int? {
        val durationMs = result.optJSONObject("spotify")?.optLong("duration_ms", -1)?.takeIf { it > 0 }
            ?: result.optJSONObject("apple_music")?.optLong("durationInMillis", -1)?.takeIf { it > 0 }
            ?: return null
        return (durationMs / 1000.0).let { Math.round(it) }.toInt()
    }

    /** Roher 16-Bit-Mono-PCM-Schnipsel -> vollstaendige WAV-Datei (44-Byte-Header + Daten). */
    private fun pcmToWav(pcm: ShortArray, sampleRate: Int): ByteArray {
        val dataSize = pcm.size * 2
        val out = ByteArrayOutputStream(44 + dataSize)
        DataOutputStream(out).use { d ->
            fun leInt(v: Int) {
                d.write(v); d.write(v ushr 8); d.write(v ushr 16); d.write(v ushr 24)
            }
            fun leShort(v: Int) {
                d.write(v); d.write(v ushr 8)
            }
            d.writeBytes("RIFF"); leInt(36 + dataSize); d.writeBytes("WAVE")
            d.writeBytes("fmt "); leInt(16); leShort(1); leShort(1) // PCM, 1 Kanal
            leInt(sampleRate); leInt(sampleRate * 2) // Byte-Rate = Samplerate * BlockAlign
            leShort(2); leShort(16) // BlockAlign (2 Byte/Sample), Bits pro Sample
            d.writeBytes("data"); leInt(dataSize)
            for (s in pcm) leShort(s.toInt())
        }
        return out.toByteArray()
    }

    private fun multipartBody(boundary: String, apiToken: String, wavBytes: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        out.write("--$boundary\r\n".toByteArray())
        out.write("Content-Disposition: form-data; name=\"api_token\"\r\n\r\n".toByteArray())
        out.write("$apiToken\r\n".toByteArray())
        // NUR wegen der Songlaenge (siehe parseDurationSeconds()) -- AudDs
        // Kernantwort liefert sie nicht. Kostet keine zusaetzliche Anfrage,
        // nur mehr Felder in derselben Antwort.
        out.write("--$boundary\r\n".toByteArray())
        out.write("Content-Disposition: form-data; name=\"return\"\r\n\r\n".toByteArray())
        out.write("apple_music,spotify\r\n".toByteArray())
        out.write("--$boundary\r\n".toByteArray())
        out.write("Content-Disposition: form-data; name=\"file\"; filename=\"snippet.wav\"\r\n".toByteArray())
        out.write("Content-Type: audio/wav\r\n\r\n".toByteArray())
        out.write(wavBytes)
        out.write("\r\n--$boundary--\r\n".toByteArray())
        return out.toByteArray()
    }
}
