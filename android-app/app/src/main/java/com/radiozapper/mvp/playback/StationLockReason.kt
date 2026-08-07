package com.radiozapper.mvp.playback

/**
 * Zwei bewusst getrennte Sperrgruende (siehe PlaybackService) - eigene Maps,
 * eigene Konstanten, aber eine gemeinsame Auswahl-Logik fuer "naechster
 * verfuegbarer Sender" (siehe nextAvailableStation()).
 */
enum class StationLockReason {
    SPEECH_COOLDOWN,
    DEAD,
}
