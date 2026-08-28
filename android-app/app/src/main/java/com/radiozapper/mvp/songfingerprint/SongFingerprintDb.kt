// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.songfingerprint

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import android.util.Log
import com.radiozapper.mvp.fingerprint.Fingerprint
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private const val TAG = "SongFingerprintDb"
private const val DB_NAME = "song_fingerprints.db"
private const val DB_VERSION = 1
// Gleicher Grund wie in FingerprintDb.kt: SQLite erlaubt bis zu 999
// Platzhalter pro Statement.
private const val QUERY_CHUNK_SIZE = 500

// Anders als bei FingerprintDb (die dort JEDEN ungematchten Sprache-Clip
// lernt und dadurch dauerhaft waechst): ein Sender-Repertoire an Songs ist
// von Natur aus begrenzt (Dutzende bis wenige Hundert Titel im Loop), die DB
// waechst also nicht unbegrenzt. Obergrenze trotzdem der Vollstaendigkeit
// halber uebernommen (gleiches Muster, viel hoeher angesetzt).
private const val MAX_SONGS = 2000
private const val PRUNE_BATCH = 50

// Skaliert mit der Schnipsel-Laenge -- siehe SongFingerprintDb-Klassendoc
// unten fuer die volle Herleitung/den offenen Kalibrierungs-Punkt.
private const val MIN_HASH_MATCHES = 140

sealed class SongFingerprintOutcome {
    data class Match(
        val songId: Long,
        val title: String?,
        val artist: String?,
        val album: String?,
        val year: Int?,
        val playCount: Int,
        val matchStrength: Int,
    ) : SongFingerprintOutcome()

    /** Neuer, noch unbekannter Song gelernt -- `songId` fuer einen spaeteren
     * `setCloudMetadata()`-Aufruf (Phase 2, AudD-Cloud-Lookup). */
    data class Learned(val songId: Long) : SongFingerprintOutcome()
}

/**
 * SQLite-gestützte Datenbank bekannter Songs -- Kotlin-Pendant zu
 * `SongFingerprintDB` in `song_fingerprint.py` (Docker-Projekt), technisch
 * aber eine Adaption von `fingerprint/FingerprintDb.kt` (siehe deren
 * Klassendoc für die Architekturbegründung: eigener `SQLiteOpenHelper`
 * statt Room). Nutzt `Fingerprint.fingerprintClip()` (dieselbe Funktion wie
 * für Sprache-Fingerprinting) unverändert mit — Algorithmus und
 * Peak-/Hash-Konstanten sind für Musik und Sprache aktuell IDENTISCH
 * (siehe dortige Klassendoc), nur der Match-Schwellwert unten
 * (`MIN_HASH_MATCHES`) unterscheidet sich, weil Song-Schnipsel deutlich
 * länger sind als die 2s-Sprache-Clips, für die der Original-Wert
 * kalibriert wurde (25 * 11.0s/2.0s ≈ 137.5, aufgerundet auf 140).
 *
 * UNKALIBRIERTER PLATZHALTER (siehe README/SESSION.md, offener Punkt): kein
 * Cross-Match-Testkorpus für Musik verfügbar, anders als beim
 * ursprünglichen Sprache-Fingerprinting (dort echter 351-Paar-Test). Sowohl
 * der Match-Schwellwert als auch der rohe Übereinstimmungs-Zähler selbst
 * (statt eines auf die Überlappungslänge normierten Verhältnisses, wie es
 * das Docker-Chromaprint-Pendant nutzt) sind eine bewusste Vereinfachung —
 * siehe README.md, Abschnitt "Song-Erkennung", "Bekannte Grenzen".
 *
 * Alle Methoden sind blockierend (SQLite-I/O) — der Aufrufer (StreamAnalyzer,
 * in dessen `Dispatchers.IO`-Coroutine) ist dafür verantwortlich, sie nicht
 * auf dem Main-Thread aufzurufen, exakt wie bei `FingerprintDb`.
 */
class SongFingerprintDb(context: Context) : SQLiteOpenHelper(context.applicationContext, DB_NAME, null, DB_VERSION) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                artist TEXT,
                album TEXT,
                year INTEGER,
                first_seen TEXT,
                last_seen TEXT,
                play_count INTEGER DEFAULT 1
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE hashes (
                hash TEXT NOT NULL,
                song_id INTEGER NOT NULL,
                offset INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_song_hash ON hashes(hash)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // Reine Zwischenspeicher-DB (lernt im Betrieb neu) -- Neuaufbau
        // statt Migration bei künftigen Schema-Änderungen, exakt dieselbe
        // Begründung wie in FingerprintDb.kt.
        db.execSQL("DROP TABLE IF EXISTS hashes")
        db.execSQL("DROP TABLE IF EXISTS songs")
        onCreate(db)
    }

    /**
     * Prüft den Schnipsel gegen die DB. Bei Treffer: Zähler hochzählen,
     * Match zurückgeben (inkl. Titel/Interpret/Album/Jahr, falls schon per
     * Cloud-Lookup bekannt -- sonst alle vier `null`, analog zum
     * Docker-Pendant). Bei keinem Treffer: neuen Song anlegen (title/
     * artist/album/year NULL -- Phase 2 füllt sie später über
     * `setCloudMetadata()`), `Learned` zurückgeben. `null`, wenn der
     * Schnipsel zu wenige Hashes für ein verlässliches Urteil liefert.
     */
    fun matchOrLearn(pcm: ShortArray, sampleRate: Int): SongFingerprintOutcome? {
        val queryHashes = Fingerprint.fingerprintClip(pcm, sampleRate)
        if (queryHashes.size < MIN_HASH_MATCHES) {
            Log.d(TAG, "nur ${queryHashes.size} Hashes - zu wenig für ein Urteil, ignoriere Schnipsel")
            return null
        }

        val db = writableDatabase
        // Bei mehrfach vorkommenden Hash-Strings gewinnt der letzte Offset,
        // gleiches Verhalten wie FingerprintDb.matchOrLearn().
        val queryOffsets = LinkedHashMap<String, Int>()
        for ((h, off) in queryHashes) queryOffsets[h] = off
        val hashList = queryHashes.map { it.first }

        // Zaehlt pro (song_id, delta), wie oft ein Hash mit KONSISTENTEM
        // Zeitversatz matcht -- exakt dasselbe Voting-Verfahren wie bei
        // FingerprintDb, siehe dortige Kommentare fuer die volle Begruendung.
        val votes = HashMap<Pair<Long, Int>, Int>()
        var i = 0
        while (i < hashList.size) {
            val chunk = hashList.subList(i, minOf(i + QUERY_CHUNK_SIZE, hashList.size))
            val placeholders = chunk.joinToString(",") { "?" }
            db.rawQuery(
                "SELECT hash, song_id, offset FROM hashes WHERE hash IN ($placeholders)",
                chunk.toTypedArray(),
            ).use { cursor ->
                val hashCol = cursor.getColumnIndexOrThrow("hash")
                val songIdCol = cursor.getColumnIndexOrThrow("song_id")
                val offsetCol = cursor.getColumnIndexOrThrow("offset")
                while (cursor.moveToNext()) {
                    val h = cursor.getString(hashCol)
                    val songId = cursor.getLong(songIdCol)
                    val dbOffset = cursor.getInt(offsetCol)
                    val queryOffset = queryOffsets[h] ?: continue
                    val delta = dbOffset - queryOffset
                    val key = songId to delta
                    votes[key] = (votes[key] ?: 0) + 1
                }
            }
            i += QUERY_CHUNK_SIZE
        }

        val best = votes.maxByOrNull { it.value }
        if (best != null) {
            val (bestSongId, _) = best.key
            val bestCount = best.value
            Log.d(
                TAG,
                "bester Kandidat: Song #$bestSongId mit $bestCount konsistenten Hash-Matches " +
                    "(Schwelle $MIN_HASH_MATCHES, Query hat ${queryHashes.size} Hashes)",
            )
            if (bestCount >= MIN_HASH_MATCHES) {
                val now = now()
                db.execSQL(
                    "UPDATE songs SET play_count = play_count + 1, last_seen = ? WHERE id = ?",
                    arrayOf(now, bestSongId),
                )
                db.rawQuery(
                    "SELECT title, artist, album, year, play_count FROM songs WHERE id = ?",
                    arrayOf(bestSongId.toString()),
                ).use { cursor ->
                    if (cursor.moveToFirst()) {
                        val title = cursor.getString(0)
                        val artist = cursor.getString(1)
                        val album = cursor.getString(2)
                        val year = if (cursor.isNull(3)) null else cursor.getInt(3)
                        val playCount = cursor.getInt(4)
                        Log.d(
                            TAG,
                            "Treffer: Song #$bestSongId ('$artist' - '$title'), $bestCount " +
                                "konsistente Hash-Matches, bereits ${playCount}x gehört",
                        )
                        return SongFingerprintOutcome.Match(bestSongId, title, artist, album, year, playCount, bestCount)
                    }
                }
            }
        }

        // Kein Treffer -> als neuen Song lernen. Eine Transaktion fuer
        // Song-Zeile + alle Hash-Zeilen zusammen, exakt wie FingerprintDb.
        val now = now()
        val newSongId: Long
        db.beginTransaction()
        try {
            newSongId = db.insert("songs", null, ContentValues().apply {
                putNull("title")
                putNull("artist")
                putNull("album")
                putNull("year")
                put("first_seen", now)
                put("last_seen", now)
                put("play_count", 1)
            })
            for ((h, off) in queryHashes) {
                db.execSQL("INSERT INTO hashes (hash, song_id, offset) VALUES (?, ?, ?)", arrayOf(h, newSongId, off))
            }
            db.setTransactionSuccessful()
            Log.d(TAG, "neuer Song #$newSongId gelernt (${queryHashes.size} Hashes)")
        } finally {
            db.endTransaction()
        }
        pruneIfNeeded(db)
        return SongFingerprintOutcome.Learned(newSongId)
    }

    /**
     * Trägt Titel/Interpret/Album/Jahr aus einem erfolgreichen AudD-Lookup
     * (Phase 2) in die von `matchOrLearn()` angelegte Zeile nach --
     * `songId` kommt direkt aus deren `Learned`-Rückgabewert (kein Umweg
     * über einen Hash-Text wie beim Docker-Pendant nötig, hier ist die
     * Zeilen-ID schon bekannt).
     */
    fun setCloudMetadata(songId: Long, title: String, artist: String, album: String?, year: Int?) {
        val db = writableDatabase
        db.execSQL(
            "UPDATE songs SET title = ?, artist = ?, album = ?, year = ? WHERE id = ?",
            arrayOf(title, artist, album, year, songId),
        )
        Log.i(TAG, "☁️ Song #$songId per AudD identifiziert: '$artist' - '$title'")
    }

    /**
     * Haelt die DB bei MAX_SONGS (siehe Konstante oben). Gleiches
     * Batch-Loeschmuster wie FingerprintDb.pruneIfNeeded().
     */
    private fun pruneIfNeeded(db: SQLiteDatabase) {
        val count = db.rawQuery("SELECT COUNT(*) FROM songs", null).use { cursor ->
            cursor.moveToFirst()
            cursor.getInt(0)
        }
        if (count <= MAX_SONGS) return

        val toDelete = maxOf(count - MAX_SONGS, PRUNE_BATCH)
        db.beginTransaction()
        try {
            db.execSQL(
                "DELETE FROM hashes WHERE song_id IN (SELECT id FROM songs ORDER BY last_seen ASC LIMIT ?)",
                arrayOf(toDelete),
            )
            db.execSQL("DELETE FROM songs WHERE id IN (SELECT id FROM songs ORDER BY last_seen ASC LIMIT ?)", arrayOf(toDelete))
            db.setTransactionSuccessful()
            Log.i(TAG, "🧹 Song-Fingerprint-DB gekürzt: $toDelete älteste(r) Song(s) entfernt (Obergrenze $MAX_SONGS)")
        } finally {
            db.endTransaction()
        }
    }

    /** Löscht ALLE gelernten Songs + Hashes -- "🗑 Song-DB leeren"-Knopf (Phase 3). Gibt die Anzahl gelöschter Songs zurück. */
    fun clearAll(): Int {
        val db = writableDatabase
        val count = db.rawQuery("SELECT COUNT(*) FROM songs", null).use { cursor ->
            cursor.moveToFirst()
            cursor.getInt(0)
        }
        db.execSQL("DELETE FROM hashes")
        db.execSQL("DELETE FROM songs")
        Log.i(TAG, "🗑 Song-Fingerprint-DB geleert: $count Song(s) gelöscht")
        return count
    }

    private fun now(): String = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.GERMANY).format(Date())
}
