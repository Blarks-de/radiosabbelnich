// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.model

/**
 * `id` ist stabil (einmal beim Anlegen vergeben, siehe StationRepository) und
 * ueberlebt Umbenennungen/URL-Aenderungen - Rotation, Cooldown
 * (PlaybackService.stationCooldownUntil) und die aktuelle Wiedergabe
 * referenzieren Sender ausschliesslich ueber diese id, nie ueber eine
 * Listenposition, analog zum Docker-Projekt (siehe dessen CLAUDE.md).
 */
data class Station(
    val id: String,
    val name: String,
    val url: String,
    val category: String,
    val enabled: Boolean = true,
)
