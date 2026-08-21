// Copyright (C) 2026 RadioSabbelNich
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License version 3 (or
// later), as published by the Free Software Foundation. See LICENSE.

package com.radiozapper.mvp.analysis

/**
 * Simple lineare Interpolation auf 16kHz, kein Anti-Aliasing-Filter - fuer die
 * grobe Sprache/Musik-Unterscheidung dieses MVP ausreichend, aber bewusst kein
 * Ersatz fuer einen "richtigen" Resampler (siehe README, bekannte Grenzen).
 *
 * Haelt eine 1-Sample-"Tail" ueber Aufrufe hinweg, damit die Interpolation an
 * der Chunk-Grenze nicht knackst/aussetzt - ein woertliches Zuruecksetzen der
 * Phase pro process()-Aufruf wuerde an jeder Pufferrenze eine kleine
 * Diskontinuitaet einbauen.
 */
class MonoResampler(private val targetRate: Int) {
    private var tail: ShortArray = ShortArray(0)
    private var phase: Double = 0.0

    fun process(mono: ShortArray, sourceRate: Int): ShortArray {
        if (mono.isEmpty()) return ShortArray(0)
        if (sourceRate == targetRate) return mono

        val combined = if (tail.isEmpty()) mono else tail + mono
        val ratio = sourceRate.toDouble() / targetRate.toDouble()
        val estimatedOutputSize = (combined.size * targetRate / sourceRate) + 2
        val output = ArrayList<Short>(estimatedOutputSize)

        var pos = phase
        while (pos + 1 < combined.size) {
            val idx = pos.toInt()
            val frac = pos - idx
            val s0 = combined[idx]
            val s1 = combined[idx + 1]
            output.add((s0 + (s1 - s0) * frac).toInt().toShort())
            pos += ratio
        }

        tail = shortArrayOf(combined[combined.size - 1])
        phase = pos - (combined.size - 1)

        return output.toShortArray()
    }
}

/** Mischt interleaved PCM16-Samples aller Kanaele auf einen Mono-Kanal ab. */
fun downmixToMono(interleaved: ShortArray, channelCount: Int): ShortArray {
    if (channelCount <= 1) return interleaved
    val frameCount = interleaved.size / channelCount
    val mono = ShortArray(frameCount)
    for (frame in 0 until frameCount) {
        var sum = 0
        val base = frame * channelCount
        for (ch in 0 until channelCount) {
            sum += interleaved[base + ch]
        }
        mono[frame] = (sum / channelCount).toShort()
    }
    return mono
}
