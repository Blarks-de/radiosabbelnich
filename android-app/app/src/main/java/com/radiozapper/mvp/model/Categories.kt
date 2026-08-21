// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.model

/**
 * Reduziertes Set ggue. dem Docker-Projekt (dort 7 Kategorien inkl.
 * "Interstellar") - auf dem Handy werden vermutlich eine Handvoll Sender
 * verwaltet, nicht Hunderte nach einem M3U-Import. "Unsortiert" bleibt wie
 * im Vorbild bewusst der letzte Eintrag (Catch-all, keine Sonderfall-Logik
 * fuer die Anzeige-Reihenfolge noetig - die Verwaltungs-Activity iteriert
 * einfach ueber ALL in dieser Reihenfolge).
 */
object Categories {
    val ALL = listOf("Lokal", "National", "International", "Unsortiert")
    const val DEFAULT = "National"

    /** Ziel-Kategorie fuer importierte Sender (siehe importer/StationImporter.kt), analog stations_store.IMPORT_CATEGORY. */
    const val IMPORTED = "Unsortiert"
}
