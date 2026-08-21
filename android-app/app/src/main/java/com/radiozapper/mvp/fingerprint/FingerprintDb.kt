// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.fingerprint

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import android.util.Log
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private const val TAG = "FingerprintDb"
private const val DB_NAME = "fingerprints.db"
private const val DB_VERSION = 1
// SQLite erlaubt bis zu 999 Platzhalter pro Statement - in Chunks abfragen,
// um dieses Limit bei vielen Query-Hashes nicht zu sprengen (analog
// fingerprint.py, das aus demselben Grund CHUNK=500 verwendet).
private const val QUERY_CHUNK_SIZE = 500

// Obergrenze fuer gelernte Clips. matchOrLearn() lernt JEDEN ungematchten
// 2s-Sprachclip mit mehreren hundert Hash-Zeilen - auf dem Handy waechst die
// DB dadurch dauerhaft weiter (im Docker-Projekt als offener Punkt bekannt,
// hier vorher gar nicht bedacht - Review-Befund 16, siehe SESSION.md).
// Verdraengt wird nach last_seen: ein Clip, der seit langem nicht mehr
// wiedererkannt wurde, war offensichtlich kein wiederkehrender Jingle.
private const val MAX_CLIPS = 500
private const val PRUNE_BATCH = 50

sealed class FingerprintOutcome {
    data class Match(val clipId: Long, val label: String, val timesSeen: Int, val matchStrength: Int) : FingerprintOutcome()
    data object Learned : FingerprintOutcome()
}

/**
 * SQLite-gestützte Datenbank bekannter Audio-Clips (Jingles/Ads) - direktes
 * Kotlin-Pendant zu `FingerprintDB` in fingerprint.py, auf Androids
 * eingebautem `SQLiteOpenHelper` statt Room (siehe README, Abschnitt
 * "Audio-Fingerprinting" für die Begründung - derselbe Grund wie die
 * JSON-Datei-statt-Room-Entscheidung bei der Senderliste, nur in die andere
 * Richtung).
 *
 * Alle Methoden sind blockierend (SQLite-I/O) - der Aufrufer ist dafür
 * verantwortlich, sie nicht auf dem Main-Thread aufzurufen (StreamAnalyzer
 * ruft matchOrLearn() bereits aus seiner Dispatchers.IO-Coroutine auf,
 * PlaybackService.undoLastFingerprintMatch() wrapped deleteClip()
 * entsprechend).
 */
class FingerprintDb(context: Context) : SQLiteOpenHelper(context.applicationContext, DB_NAME, null, DB_VERSION) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT,
                first_seen TEXT,
                last_seen TEXT,
                times_seen INTEGER DEFAULT 1
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE hashes (
                hash TEXT NOT NULL,
                clip_id INTEGER NOT NULL,
                offset INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_hash ON hashes(hash)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // Reine Zwischenspeicher-DB (lernt im Betrieb neu) - bei einer
        // künftigen Schema-Änderung ist Neuaufbau statt Migration
        // angemessen, analog dazu, dass ein Algorithmus-Wechsel im
        // Docker-Projekt ohnehin ein Leeren der DB nötig macht (siehe
        // clear_all()-Doc in fingerprint.py).
        db.execSQL("DROP TABLE IF EXISTS hashes")
        db.execSQL("DROP TABLE IF EXISTS clips")
        onCreate(db)
    }

    /**
     * Prüft den Clip gegen die DB. Bei Treffer: Zähler hochzählen, Match
     * zurückgeben. Bei keinem Treffer: Clip neu anlegen, `Learned`
     * zurückgeben. `null`, wenn der Clip zu wenige Hashes für ein
     * verlässliches Urteil liefert (zu kurz/leise) - wird dann komplett
     * ignoriert, nicht gelernt.
     */
    fun matchOrLearn(pcm: ShortArray, sampleRate: Int, label: String): FingerprintOutcome? {
        val queryHashes = Fingerprint.fingerprintClip(pcm, sampleRate)
        if (queryHashes.size < Fingerprint.MIN_HASH_MATCHES) {
            Log.d(TAG, "nur ${queryHashes.size} Hashes - zu wenig für ein Urteil, ignoriere Clip")
            return null
        }

        val db = writableDatabase
        // Bei mehrfach vorkommenden Hash-Strings gewinnt der letzte Offset -
        // exakt dasselbe Verhalten wie Pythons dict-Komprehension
        // {h: off for h, off in query_hashes}.
        val queryOffsets = LinkedHashMap<String, Int>()
        for ((h, off) in queryHashes) queryOffsets[h] = off
        val hashList = queryHashes.map { it.first }

        // Zaehlt pro (clip_id, delta), wie oft ein Hash mit KONSISTENTEM
        // Zeitversatz matcht - filtert Zufallstreffer raus.
        val votes = HashMap<Pair<Long, Int>, Int>()
        var i = 0
        while (i < hashList.size) {
            val chunk = hashList.subList(i, minOf(i + QUERY_CHUNK_SIZE, hashList.size))
            val placeholders = chunk.joinToString(",") { "?" }
            db.rawQuery(
                "SELECT hash, clip_id, offset FROM hashes WHERE hash IN ($placeholders)",
                chunk.toTypedArray(),
            ).use { cursor ->
                val hashCol = cursor.getColumnIndexOrThrow("hash")
                val clipIdCol = cursor.getColumnIndexOrThrow("clip_id")
                val offsetCol = cursor.getColumnIndexOrThrow("offset")
                while (cursor.moveToNext()) {
                    val h = cursor.getString(hashCol)
                    val clipId = cursor.getLong(clipIdCol)
                    val dbOffset = cursor.getInt(offsetCol)
                    val queryOffset = queryOffsets[h] ?: continue
                    val delta = dbOffset - queryOffset
                    val key = clipId to delta
                    votes[key] = (votes[key] ?: 0) + 1
                }
            }
            i += QUERY_CHUNK_SIZE
        }

        val best = votes.maxByOrNull { it.value }
        if (best != null) {
            val (bestClipId, _) = best.key
            val bestCount = best.value
            Log.d(
                TAG,
                "bester Kandidat: Clip #$bestClipId mit $bestCount konsistenten Hash-Matches " +
                    "(Schwelle ${Fingerprint.MIN_HASH_MATCHES}, Query hat ${queryHashes.size} Hashes)",
            )
            if (bestCount >= Fingerprint.MIN_HASH_MATCHES) {
                val now = now()
                db.execSQL(
                    "UPDATE clips SET times_seen = times_seen + 1, last_seen = ? WHERE id = ?",
                    arrayOf(now, bestClipId),
                )
                db.rawQuery("SELECT label, times_seen FROM clips WHERE id = ?", arrayOf(bestClipId.toString())).use { cursor ->
                    if (cursor.moveToFirst()) {
                        val matchLabel = cursor.getString(0)
                        val timesSeen = cursor.getInt(1)
                        Log.d(TAG, "Treffer: Clip #$bestClipId ('$matchLabel'), $bestCount konsistente Hash-Matches, bereits ${timesSeen}x gesehen")
                        return FingerprintOutcome.Match(bestClipId, matchLabel, timesSeen, bestCount)
                    }
                }
            }
        }

        // Kein Treffer -> als neuen Clip lernen. Eine Transaktion für
        // Clip-Zeile + alle Hash-Zeilen zusammen (analog Pythons einzelnem
        // conn.commit() am Ende beider Statements) - sonst könnte bei einem
        // Fehler mitten in den Hash-Inserts ein Clip ohne jede Hash-Zeile
        // übrig bleiben.
        val now = now()
        db.beginTransaction()
        try {
            val clipId = db.insert("clips", null, ContentValues().apply {
                put("label", label)
                put("first_seen", now)
                put("last_seen", now)
                put("times_seen", 1)
            })
            for ((h, off) in queryHashes) {
                db.execSQL("INSERT INTO hashes (hash, clip_id, offset) VALUES (?, ?, ?)", arrayOf(h, clipId, off))
            }
            db.setTransactionSuccessful()
            Log.d(TAG, "neuer Clip #$clipId gelernt (${queryHashes.size} Hashes, Label '$label')")
        } finally {
            db.endTransaction()
        }
        pruneIfNeeded(db)
        return FingerprintOutcome.Learned
    }

    /**
     * Haelt die DB bei MAX_CLIPS (siehe Konstante oben). Geloescht wird in
     * Batches (PRUNE_BATCH), damit nicht bei JEDEM gelernten Clip ein
     * Loeschvorgang laeuft - die aeltesten nach `last_seen` zuerst.
     */
    private fun pruneIfNeeded(db: SQLiteDatabase) {
        val count = db.rawQuery("SELECT COUNT(*) FROM clips", null).use { cursor ->
            cursor.moveToFirst()
            cursor.getInt(0)
        }
        if (count <= MAX_CLIPS) return

        val toDelete = maxOf(count - MAX_CLIPS, PRUNE_BATCH)
        db.beginTransaction()
        try {
            db.execSQL(
                "DELETE FROM hashes WHERE clip_id IN (SELECT id FROM clips ORDER BY last_seen ASC LIMIT ?)",
                arrayOf(toDelete),
            )
            db.execSQL("DELETE FROM clips WHERE id IN (SELECT id FROM clips ORDER BY last_seen ASC LIMIT ?)", arrayOf(toDelete))
            db.setTransactionSuccessful()
            Log.i(TAG, "🧹 Fingerprint-DB gekuerzt: $toDelete aelteste Clip(s) entfernt (Obergrenze $MAX_CLIPS)")
        } finally {
            db.endTransaction()
        }
    }

    /**
     * Löscht einen Clip + seine Hashes - für den "🛑 Zapping-Fehler"-Knopf:
     * falls ein Fingerprint-Treffer fälschlich einen Wechsel ausgelöst hat,
     * kann der Nutzer den zugrundeliegenden Clip damit aus der DB werfen,
     * statt dass er dauerhaft weiter fehlklassifiziert. Gibt das Label des
     * gelöschten Clips zurück, oder null falls die ID nicht (mehr)
     * existiert.
     */
    fun deleteClip(clipId: Long): String? {
        val db = writableDatabase
        val label = db.rawQuery("SELECT label FROM clips WHERE id = ?", arrayOf(clipId.toString())).use { cursor ->
            if (cursor.moveToFirst()) cursor.getString(0) else null
        } ?: return null
        db.execSQL("DELETE FROM hashes WHERE clip_id = ?", arrayOf(clipId))
        db.execSQL("DELETE FROM clips WHERE id = ?", arrayOf(clipId))
        Log.i(TAG, "🗑 Fingerprint-Clip #$clipId ('$label') gelöscht")
        return label
    }

    /** Löscht ALLE gelernten Clips + Hashes. Gibt die Anzahl gelöschter Clips zurück. */
    fun clearAll(): Int {
        val db = writableDatabase
        val count = db.rawQuery("SELECT COUNT(*) FROM clips", null).use { cursor ->
            cursor.moveToFirst()
            cursor.getInt(0)
        }
        db.execSQL("DELETE FROM hashes")
        db.execSQL("DELETE FROM clips")
        Log.i(TAG, "🗑 Fingerprint-DB geleert: $count Clip(s) gelöscht")
        return count
    }

    private fun now(): String = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.GERMANY).format(Date())
}
