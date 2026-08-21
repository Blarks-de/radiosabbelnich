// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

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
