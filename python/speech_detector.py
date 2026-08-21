# Copyright (C) 2026 RadioSabbelNich
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 (or
# later), as published by the Free Software Foundation. See LICENSE.

"""
speech_detector.py — Sprache-Erkennung via Silero VAD (Voice Activity
Detection), robust auch wenn ein Musikbett unter der Sprache liegt
(typisch für produzierte Werbespots/Jingles).

Warum VAD statt Handbastel-Heuristik: Die reine Signal-Heuristik
(ZCR/Flatness/Bass) versagt bei professionell produzierten Werbespots,
weil die spektral kaum von Musik zu unterscheiden sind (Musikbett,
Soundeffekte). Silero VAD ist ein kleines, spezialisiertes neuronales
Netz, das genau darauf trainiert ist: "ist hier eine menschliche Stimme
zu hören", auch über Hintergrundgeräuschen/Musik.

Silero erwartet 16kHz Mono, feste 512-Sample-Frames (32ms). Unser
Analysepfad läuft intern mit SAMPLE_RATE (aktuell 44100 Hz, siehe
radiosabbelnich.py) — wird hier per einfacher linearer Interpolation
runtergerechnet (reicht für VAD-Zwecke völlig, ist keine Hifi-Anwendung).
"""

import logging
import numpy as np

log = logging.getLogger("speech")

try:
    from silero_vad_lite import SileroVAD
    _SILERO_AVAILABLE = True
    _SILERO_IMPORT_ERROR = None
except Exception as e:  # bewusst breit: auch fehlende Shared Libs (OSError etc.) sollen zum Fallback führen, nicht zum Crash
    _SILERO_AVAILABLE = False
    _SILERO_IMPORT_ERROR = e


class SpeechDetector:
    """Klassifiziert PCM-Fenster als 'speech' oder 'music' via Silero VAD.
    Fällt automatisch auf None zurück (Signal für Heuristik-Fallback),
    falls silero-vad-lite nicht installiert ist."""

    TARGET_SR = 16000

    def __init__(self, source_sr: int,
                 frame_speech_prob_threshold: float = 0.5,
                 window_speech_ratio: float = 0.3):
        self.source_sr = source_sr
        self.frame_threshold = frame_speech_prob_threshold
        self.window_ratio = window_speech_ratio
        self.leftover = np.array([], dtype=np.float32)

        if _SILERO_AVAILABLE:
            try:
                self.vad = SileroVAD(self.TARGET_SR)
                self.frame_size = self.vad.window_size_samples
                self.available = True
            except Exception as e:
                self.vad = None
                self.frame_size = None
                self.available = False
                log.warning("⚠ Silero VAD konnte nicht initialisiert werden (%s) — "
                            "falle auf die Signal-Heuristik zurück.", e)
        else:
            self.vad = None
            self.frame_size = None
            self.available = False
            log.warning("⚠ silero-vad-lite nicht verfügbar (%s) — "
                        "falle auf die Signal-Heuristik zurück.", _SILERO_IMPORT_ERROR)

    def reset(self):
        """Verwirft den Resample-Rest UND baut das VAD-Objekt neu auf --
        beides muss bei jedem echten Streamwechsel (Senderwechsel,
        Rekonnekt nach Verbindungsabbruch, Kandidat-Probing in do_switch())
        passieren, sonst rutscht Audio des VORHERIGEN Streams in die
        Klassifikation des neuen: `self.leftover` sind Resample-Samples,
        die im letzten Fenster übrig blieben; `SileroVAD.process()` hält
        zusätzlich intern einen rekurrenten Zustand über aufeinanderfolgende
        Frames (siehe silero-vad-lite-Doku), den die C-Erweiterung NICHT
        über eine reset()-Methode freigibt -- ein frisches SileroVAD-Objekt
        ist billig genug (kleines ONNX-Modell), das pro Streamwechsel neu
        anzulegen, statt den alten Zustand stillschweigend weiterlaufen zu
        lassen."""
        self.leftover = np.array([], dtype=np.float32)
        if self.available:
            try:
                self.vad = SileroVAD(self.TARGET_SR)
            except Exception as e:
                log.warning("⚠ Silero VAD konnte beim Streamwechsel nicht neu "
                            "initialisiert werden (%s) — Sprache-Erkennung fällt "
                            "bis zum nächsten Prozessneustart auf die Heuristik "
                            "zurück.", e)
                self.vad = None
                self.available = False

    def _resample(self, pcm_int16: np.ndarray) -> np.ndarray:
        samples = pcm_int16.astype(np.float32) / 32768.0
        if self.source_sr == self.TARGET_SR:
            return samples
        duration = len(samples) / self.source_sr
        n_target = max(1, int(round(duration * self.TARGET_SR)))
        x_old = np.linspace(0, duration, num=len(samples), endpoint=False)
        x_new = np.linspace(0, duration, num=n_target, endpoint=False)
        return np.interp(x_new, x_old, samples).astype(np.float32)

    def classify(self, pcm_int16: np.ndarray):
        """Liefert (label, mean_prob) oder (None, 0.0) falls VAD nicht
        verfügbar ist (dann bitte auf Heuristik-Fallback umschalten).
        Die Einzelwerte pro Fenster gehen als DEBUG ins Log."""
        if not self.available or pcm_int16.size == 0:
            return None, 0.0

        resampled = self._resample(pcm_int16)
        buf = np.concatenate([self.leftover, resampled])
        n_frames = len(buf) // self.frame_size
        if n_frames == 0:
            self.leftover = buf
            return "music", 0.0

        probs = np.array([
            self.vad.process(buf[i * self.frame_size:(i + 1) * self.frame_size])
            for i in range(n_frames)
        ])
        self.leftover = buf[n_frames * self.frame_size:]

        speech_ratio = float(np.mean(probs > self.frame_threshold))
        mean_prob = float(np.mean(probs))
        label = "speech" if speech_ratio >= self.window_ratio else "music"

        log.debug("[vad] mean_prob=%.3f speech_ratio=%.2f -> %s",
                  mean_prob, speech_ratio, "SPEECH" if label == "speech" else "music")

        return label, mean_prob
