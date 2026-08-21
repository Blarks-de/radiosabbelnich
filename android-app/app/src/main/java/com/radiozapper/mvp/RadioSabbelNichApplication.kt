// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp

import android.app.Application
import com.radiozapper.mvp.model.StationRepository

/**
 * Einziger Zweck: StationRepository.init() garantiert VOR jeder Komponente
 * in diesem Prozess aufrufen (Application.onCreate() laeuft immer zuerst,
 * unabhaengig vom Einstiegspunkt - Hauptschirm, Verwaltungs-Activity,
 * Service-Autostart, ...). Ohne diese Klasse muesste sich jeder Einstiegspunkt
 * selbst um eine defensive Initialisierung kuemmern - genau die Art Bug, die
 * erst auffaellt, wenn ein neuer Einstiegspunkt sie vergisst.
 */
class RadioSabbelNichApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        StationRepository.init(this)
    }
}
