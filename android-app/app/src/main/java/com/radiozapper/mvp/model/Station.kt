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
