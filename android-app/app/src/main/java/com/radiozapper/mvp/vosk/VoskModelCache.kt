// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.vosk

import android.os.SystemClock
import android.util.Log
import org.vosk.Model

private const val TAG = "VoskModelCache"

// Wie viele Vosk-Modelle (= Sprachen) gleichzeitig im RAM gehalten werden -
// identischer Default wie MAX_LOADED_VOSK_LANGUAGES im Docker-Projekt
// (stt_filter.py). Bei mehr konfigurierten/genutzten Sprachen als das hier
// wird das am laengsten ungenutzte Modell verdraengt (LRU).
const val MAX_LOADED_VOSK_LANGUAGES = 2

// Wie lange ein fehlgeschlagener Ladeversuch gecacht bleibt, bevor er erneut
// probiert wird. Ohne Ablauf blieb eine Sprache fuer die gesamte
// Service-Lebensdauer kaputt - auch nachdem der Nutzer das Modell laengst
// erneut heruntergeladen hatte (Review-Befund 11, siehe SESSION.md).
private const val FAILURE_RETRY_MS = 60_000L

/**
 * Lazy-Load + LRU-Cache fuer geladene Vosk-`Model`-Objekte - direktes Pendant
 * zu `SttFilter._get_vosk_engine()` im Docker-Projekt. Instanzgebunden
 * (gehoert `PlaybackService`, analog `FingerprintDb`), NICHT statisch/
 * Singleton - ein `Model` ist zustandsbehaftet und wird beim Verdraengen
 * `close()`d; mehrere unabhaengige Caches sollen sich dabei nicht gegenseitig
 * ins Gehege kommen.
 *
 * Zwei Eigenschaften stammen aus dem Phase-8-Review (siehe SESSION.md):
 *
 * - **Belegzaehler statt blindem `close()` (Befund 3)**: `Model.close()` gibt
 *   nativen Speicher frei, auf den ein laufender `Recognizer` noch zeigt.
 *   `StreamAnalyzer` haelt ein Modell deshalb per `acquire()` fuer die Dauer
 *   seines Laufs fest und gibt es erst im `finally` (nach
 *   `recognizer.close()`) per `release()` frei. Verdraengt/geschlossen wird
 *   nur, was gerade NIEMAND benutzt; ein noch belegtes Modell wird
 *   vorgemerkt (`pendingClose`) und beim `release()` geschlossen. Ohne das
 *   konnte die LRU-Verdraengung (ab der dritten genutzten Sprache) oder
 *   `clear()` in `PlaybackService.onDestroy()` einen nativen Absturz
 *   ausloesen - ohne Java-Stacktrace, entsprechend schwer zu finden.
 * - **Schluessel = Sprachcode UND Modellpfad (Befund 11)**: aendert sich die
 *   Modell-URL einer Sprache (oder wird das Modell geloescht und neu
 *   geladen), zeigt der Pfad woanders hin - ein rein sprachcode-basierter
 *   Schluessel haette weiter das alte Modell geliefert.
 *
 * Cached weiterhin auch Fehlschlaege (Fehlertext statt Model), damit ein
 * kaputter Modellpfad nicht bei jedem Sample erneut das Dateisystem anfasst -
 * jetzt aber mit Ablauf, siehe FAILURE_RETRY_MS.
 */
class VoskModelCache {
    private class Entry(val model: Model?, val error: String?, val createdAtMs: Long) {
        var refs = 0
        var pendingClose = false
    }

    // accessOrder=true: jeder get()/put() verschiebt den Eintrag ans Ende,
    // das am laengsten ungenutzte steht vorn. Verdraengt wird von Hand
    // (evictIfNeeded()) statt per removeEldestEntry(), weil belegte
    // Eintraege uebersprungen werden muessen.
    private val cache = LinkedHashMap<String, Entry>(MAX_LOADED_VOSK_LANGUAGES + 1, 0.75f, true)

    private fun keyOf(modelPath: String, code: String) = "$code|$modelPath"

    /**
     * Geladenes Model (aus dem Cache oder frisch geladen), oder null falls das
     * Laden fehlschlaegt. Der Aufrufer MUSS bei einem Ergebnis != null
     * spaeter `release()` mit denselben Argumenten aufrufen.
     */
    @Synchronized
    fun acquire(modelPath: String, code: String): Model? {
        val key = keyOf(modelPath, code)
        val existing = cache[key]
        if (existing != null) {
            val staleFailure = existing.model == null &&
                SystemClock.elapsedRealtime() - existing.createdAtMs > FAILURE_RETRY_MS
            if (!staleFailure) {
                if (existing.model != null) existing.refs++
                return existing.model
            }
            cache.remove(key) // Fehlschlag abgelaufen - erneut probieren
        }

        return try {
            val model = Model(modelPath)
            val entry = Entry(model, null, SystemClock.elapsedRealtime())
            entry.refs = 1
            cache[key] = entry
            Log.i(TAG, "Vosk-Modell fuer Sprache '$code' geladen ($modelPath)")
            evictIfNeeded()
            model
        } catch (e: Exception) {
            Log.e(TAG, "Vosk-Modell fuer Sprache '$code' nicht ladbar ($modelPath)", e)
            cache[key] = Entry(null, e.message ?: e.toString(), SystemClock.elapsedRealtime())
            null
        }
    }

    /** Gegenstueck zu `acquire()` - gibt das Modell zur Verdraengung frei und schliesst es, falls es waehrend der Benutzung vorgemerkt wurde. */
    @Synchronized
    fun release(modelPath: String, code: String) {
        val key = keyOf(modelPath, code)
        val entry = cache[key] ?: return
        if (entry.refs > 0) entry.refs--
        if (entry.refs == 0 && entry.pendingClose) {
            Log.d(TAG, "Vosk-Modell '$code' war noch belegt - jetzt freigegeben und geschlossen")
            entry.model?.close()
            cache.remove(key)
        } else if (entry.refs == 0) {
            evictIfNeeded()
        }
    }

    /**
     * Schliesst alle nicht belegten Modelle sofort; noch belegte werden
     * vorgemerkt und vom jeweiligen `release()` geschlossen. Wird aus
     * `PlaybackService.onDestroy()` aufgerufen, wo ein Analyse-Lauf noch
     * auslaufen kann (Cancel ist nur kooperativ).
     */
    @Synchronized
    fun clear() {
        val iterator = cache.entries.iterator()
        while (iterator.hasNext()) {
            val entry = iterator.next().value
            if (entry.refs > 0) {
                entry.pendingClose = true
            } else {
                entry.model?.close()
                iterator.remove()
            }
        }
    }

    /** LRU-Verdraengung ueber alle geladenen (nicht fehlgeschlagenen) Eintraege - belegte werden uebersprungen, nicht geschlossen. */
    private fun evictIfNeeded() {
        while (cache.count { it.value.model != null } > MAX_LOADED_VOSK_LANGUAGES) {
            val victim = cache.entries.firstOrNull { it.value.model != null && it.value.refs == 0 } ?: return
            Log.d(TAG, "Vosk-Modell '${victim.key}' aus dem Cache verdraengt (LRU, max $MAX_LOADED_VOSK_LANGUAGES gleichzeitig)")
            victim.value.model?.close()
            cache.remove(victim.key)
        }
    }
}
