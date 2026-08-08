package com.radiozapper.mvp.vosk

import android.util.Log
import org.vosk.Model

private const val TAG = "VoskModelCache"

// Wie viele Vosk-Modelle (= Sprachen) gleichzeitig im RAM gehalten werden -
// identischer Default wie MAX_LOADED_VOSK_LANGUAGES im Docker-Projekt
// (stt_filter.py). Bei mehr konfigurierten/genutzten Sprachen als das hier
// wird das am laengsten ungenutzte Modell verdraengt (LRU).
const val MAX_LOADED_VOSK_LANGUAGES = 2

/**
 * Lazy-Load + LRU-Cache fuer geladene Vosk-`Model`-Objekte pro Sprachcode -
 * direktes Pendant zu `SttFilter._get_vosk_engine()` im Docker-Projekt.
 * Instanzgebunden (gehoert `PlaybackService`, analog `FingerprintDb`), NICHT
 * statisch/Singleton - ein `Model` ist zustandsbehaftet und wird beim
 * Verdraengen `close()`d; mehrere unabhaengige Caches sollen sich dabei
 * nicht gegenseitig ins Gehege kommen.
 *
 * Cached sowohl Erfolg (Model-Objekt) als auch Fehlschlag (Fehlertext) fuer
 * denselben Sprachcode - ein kaputter Modellpfad soll nicht bei jedem
 * Sender-Wechsel in diese Sprache erneut das Dateisystem/Modell anfassen,
 * 1:1 wie im Vorbild.
 */
class VoskModelCache {
    private data class Entry(val model: Model?, val error: String?)

    // LinkedHashMap mit accessOrder=true: jeder get()/put() verschiebt den
    // Eintrag ans Ende, das am laengsten ungenutzte steht vorn -
    // removeEldestEntry() verdraengt es, sobald die Kapazitaet ueberschritten ist.
    private val cache = object : LinkedHashMap<String, Entry>(MAX_LOADED_VOSK_LANGUAGES + 1, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Entry>): Boolean {
            if (size <= MAX_LOADED_VOSK_LANGUAGES) return false
            eldest.value.model?.let {
                Log.d(TAG, "Vosk-Modell '${eldest.key}' aus dem Cache verdraengt (LRU, max $MAX_LOADED_VOSK_LANGUAGES gleichzeitig)")
                it.close()
            }
            return true
        }
    }

    /** Geladenes Model fuer `code` (aus dem Cache oder frisch geladen), oder null falls das Laden fehlschlaegt. */
    @Synchronized
    fun get(modelPath: String, code: String): Model? {
        cache[code]?.let { return it.model }

        return try {
            val model = Model(modelPath)
            cache[code] = Entry(model, null)
            Log.i(TAG, "Vosk-Modell fuer Sprache '$code' geladen ($modelPath)")
            model
        } catch (e: Exception) {
            Log.e(TAG, "Vosk-Modell fuer Sprache '$code' nicht ladbar ($modelPath)", e)
            cache[code] = Entry(null, e.message ?: e.toString())
            null
        }
    }

    @Synchronized
    fun clear() {
        cache.values.forEach { it.model?.close() }
        cache.clear()
    }
}
