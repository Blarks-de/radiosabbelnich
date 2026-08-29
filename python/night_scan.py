#!/usr/bin/env python3
# Copyright (C) 2026 RadioSabbelNich
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 (or
# later), as published by the Free Software Foundation. See LICENSE.

"""
night_scan.py — Kernlogik des nächtlichen Sender-Scans (siehe
ARCHITECTURE.md, Abschnitt "Nächtlicher Sender-Scan", für die volle
Herleitung/Diagramme): pro Sender per ffmpeg verbinden, mit dem
projekteigenen Silero-VAD (speech_detector.py) NUR die tatsächlich
sprachhaltigen Fenster sammeln, und sobald genug davon zusammengekommen
sind, per faster-whisper (`WhisperModel.detect_language()`) die
gesprochene Sprache samt Konfidenz schätzen — im Gegensatz zu
vosk_language_check.py (das jede konfigurierte Sprache EINZELN gegen
Vosk-Modelle testet) braucht das KEIN sprachspezifisches Modell und ist
nicht auf die in stt_filter.languages konfigurierten Sprachen beschränkt.

Reine Domänenlogik ohne WebUI-/SwitcherState-Bezug (analog
station_import.py/vosk_download.py) — webui.py hält den
Hintergrund-Scheduler, den Fortschritts-State und die API/UI-Anbindung.

Drei (plus ein Fehlerfall) mögliche Ausgänge pro Sender, siehe
scan_station() — "music" ist ein NÜTZLICHES Ergebnis, kein Fehlschlag:
  - "detected":    Sprache + Konfidenz ermittelt
  - "music":       capture_timeout_seconds erreicht, Audio kam an, aber
                    nie genug zusammenhängende Sprache gefunden
  - "unreachable": in der gesamten Wartezeit kam gar kein Audio an
  - "error":       Whisper-Aufruf selbst ist fehlgeschlagen (z.B. korrupter
                    Sample-Puffer) -- getrennt von "unreachable", weil hier
                    durchaus Sprache gefunden wurde, nur die Analyse danach
                    scheiterte

Audio-Capture-Technik identisch zu vosk_language_check.capture_pcm()/
station_import.check_reachable() (ffmpeg + select()-Lese-Loop, siehe
dortige Docstrings für die Zeitfenster-Begründung) — hier aber
FENSTERWEISE gelesen (WINDOW_SECONDS-Häppchen) statt am Stück, weil jedes
Fenster erst die VAD-Gate passieren muss, bevor es überhaupt gesammelt
wird. Wie bei vosk_language_check.capture_pcm() (dort per Bug-Fix
nachgezogen) ist jeder Einzel-Read bewusst auf `window_bytes` GEDECKELT
(nicht nur zeitlich begrenzt) -- os.read() liefert nie mehr als angefragt,
ein Burst-Überlesen wie im dortigen Bug ist hier von Anfang an
ausgeschlossen.
"""

import logging
import os
import select
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import speech_detector
import station_import

log = logging.getLogger("night_scan")

try:
    from faster_whisper import WhisperModel
    _WHISPER_AVAILABLE = True
    _WHISPER_IMPORT_ERROR = None
except Exception as e:  # bewusst breit, wie stt_filter.py/speech_detector.py:
                          # fehlendes Paket oder kaputte Installation soll zum
                          # Fallback (Feature bleibt deaktiviert) führen, nicht zum Crash
    _WHISPER_AVAILABLE = False
    _WHISPER_IMPORT_ERROR = e

# Gleicher persistenter, beschreibbarer Cache-Ordner wie beim Live-STT-Filter
# (stt_filter.WHISPER_DOWNLOAD_ROOT) -- ein zweites WhisperModel-Objekt mit
# derselben Modellgröße lädt dadurch keine zweite Kopie von HuggingFace nach.
WHISPER_DOWNLOAD_ROOT = "/app/whisper_cache"

# Analyse-Fenstergröße für die VAD-Gate, wie stt_filter.CLIP_SECONDS.
WINDOW_SECONDS = 3.0
# = speech_detector.SpeechDetector.TARGET_SR -- ffmpeg liefert direkt in
# dieser Rate, kein manueller Resample-Schritt nötig (anders als beim
# Live-Analysepfad, der mit SAMPLE_RATE/44100Hz arbeitet).
SAMPLE_RATE = 16000


def load_whisper_engine(model_size: str) -> "WhisperModel":
    """Lädt ein EIGENES WhisperModel-Objekt, unabhängig von
    stt_filter.SttFilter (dessen "Vosk/Whisper nie gleichzeitig geladen"-
    Regel gilt nur für dessen EINEN Live-Switching-Engine-Slot, siehe
    ARCHITECTURE.md -- dieser Scan läuft komplett separat, genau wie
    vosk_language_check.py eigene Vosk-Engines parallel zum Hauptloop
    lädt). Wirft RuntimeError, wenn faster-whisper nicht installiert ist."""
    if not _WHISPER_AVAILABLE:
        raise RuntimeError(f"faster-whisper nicht installiert ({_WHISPER_IMPORT_ERROR})")
    return WhisperModel(model_size, device="cpu", compute_type="int8",
                         download_root=WHISPER_DOWNLOAD_ROOT)


def _read_window(proc, window_bytes: int, timeout: float) -> bytes:
    """Liest bis zu `window_bytes` Bytes vom ffmpeg-stdout, wartet je
    Einzel-Read höchstens `timeout` Sekunden auf die ersten neuen Daten.
    Kein Gesamt-Timeout hier -- das Gesamtbudget trägt der Aufrufer
    (scan_station()) über capture_timeout_seconds. Gibt weniger als
    window_bytes zurück, wenn der Stream währenddessen endet/stockt."""
    fd = proc.stdout.fileno()
    buf = bytearray()
    deadline = time.monotonic() + timeout
    while len(buf) < window_bytes:
        time_left = deadline - time.monotonic()
        if time_left <= 0:
            break
        ready, _, _ = select.select([fd], [], [], time_left)
        if not ready:
            break
        chunk = os.read(fd, window_bytes - len(buf))
        if not chunk:
            break  # EOF
        buf.extend(chunk)
    return bytes(buf)


def scan_station(url: str, whisper_engine, capture_timeout_seconds: float,
                  min_speech_seconds: float,
                  vad_frame_threshold: float = 0.5,
                  vad_window_ratio: float = 0.3) -> dict:
    """Kernstück des Scans für EINEN Sender (siehe Moduldocstring für die
    vier möglichen `label`-Werte). Öffnet eine eigene ffmpeg-Verbindung
    (unabhängig vom Hauptloop, siehe dortige StreamSource) und sammelt in
    Echtzeit -- ein Sender mit wenig Sprachanteil braucht deshalb
    tatsächlich entsprechend lange Wartezeit, das ist beabsichtigt (siehe
    ARCHITECTURE.md für die empirische Grundlage der Timeout-Wahl).

    Gibt {"label", "language", "confidence", "seconds_captured",
    "speech_seconds"} zurück ("error" zusätzlich mit "error"-Text)."""
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-user_agent", station_import.USER_AGENT,
             "-i", url,
             "-map", "0:a:0", "-f", "s16le", "-acodec", "pcm_s16le",
             "-ar", str(SAMPLE_RATE), "-ac", "1", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        log.debug("[night_scan] %s -> ffmpeg nicht startbar (%s)", url, e)
        return {"label": "unreachable", "language": None, "confidence": None,
                "seconds_captured": 0.0, "speech_seconds": 0.0}

    detector = speech_detector.SpeechDetector(SAMPLE_RATE, vad_frame_threshold, vad_window_ratio)
    if not detector.available:
        proc.kill()
        proc.wait()
        raise RuntimeError("Silero VAD nicht verfügbar (speech_detector.SpeechDetector) -- "
                            "Scan kann ohne VAD nicht sinnvoll zwischen Sprache und Musik "
                            "unterscheiden.")

    window_bytes = int(WINDOW_SECONDS * SAMPLE_RATE * 2)  # s16le = 2 Bytes/Sample
    speech_chunks = []
    speech_seconds = 0.0
    total_seconds = 0.0
    deadline = time.monotonic() + capture_timeout_seconds

    try:
        # ZWEI unabhängige Abbruchbedingungen, nicht nur die Wall-Clock-
        # Deadline über `time_left`: eine Quelle, die schneller als
        # Echtzeit liefert (z.B. ein CDN-Vorrat, siehe der bekannte
        # BBC-Radio-Scotland-Fall in station_import.py, UND per
        # Testserver hier tatsächlich reproduziert -- 60s Audio wurden
        # trotz capture_timeout_seconds=8 verarbeitet, weil viele
        # Loop-Durchläufe fast ohne verstreichende Wall-Clock-Zeit
        # durchliefen), würde sonst beliebig viel Audio verarbeiten,
        # bevor `time_left` überhaupt bei 0 ankommt -- deshalb zusätzlich
        # hart bei `total_seconds >= capture_timeout_seconds` abbrechen.
        while speech_seconds < min_speech_seconds and total_seconds < capture_timeout_seconds:
            time_left = deadline - time.monotonic()
            if time_left <= 0:
                break
            raw = _read_window(proc, window_bytes, time_left)
            if not raw:
                break  # Stream liefert nichts mehr (tot oder sauber zu Ende)
            pcm = np.frombuffer(raw, dtype=np.int16)
            total_seconds += len(pcm) / SAMPLE_RATE
            label, _prob = detector.classify(pcm)
            if label == "speech":
                speech_chunks.append(pcm)
                speech_seconds += len(pcm) / SAMPLE_RATE
    finally:
        proc.kill()
        proc.wait()

    if total_seconds == 0.0:
        return {"label": "unreachable", "language": None, "confidence": None,
                "seconds_captured": 0.0, "speech_seconds": 0.0}

    if speech_seconds < min_speech_seconds:
        return {"label": "music", "language": None, "confidence": None,
                "seconds_captured": round(total_seconds, 1),
                "speech_seconds": round(speech_seconds, 1)}

    speech_pcm = np.concatenate(speech_chunks)
    audio_float = speech_pcm.astype(np.float32) / 32768.0
    try:
        language, confidence, _all_probs = whisper_engine.detect_language(audio_float, vad_filter=False)
    except Exception as e:
        log.warning("⚠ Whisper-Sprach-ID fehlgeschlagen (%s): %s", url, e)
        return {"label": "error", "language": None, "confidence": None,
                "seconds_captured": round(total_seconds, 1),
                "speech_seconds": round(speech_seconds, 1), "error": str(e)}

    return {"label": "detected", "language": language, "confidence": round(float(confidence), 3),
            "seconds_captured": round(total_seconds, 1),
            "speech_seconds": round(speech_seconds, 1)}


def run_scan(stations: list, whisper_engine, cfg: dict, progress=None, should_stop=None) -> dict:
    """Scannt `stations` in Batches von `cfg["concurrency"]` (Default 1,
    siehe ARCHITECTURE.md für die Ressourcen-Abwägung: strikt sequenziell
    ist der sichere Default für einen unbeaufsichtigten Nacht-Job).
    `should_stop` (optional, Callable[[], bool]) wird VOR jedem Batch
    geprüft -- bei Concurrency 1 also vor jedem einzelnen Sender, für den
    Stop-Knopf bzw. das Ende des Nacht-Zeitfensters (siehe
    webui.NightScanScheduler). `progress` (optional): Duck-Typing-Objekt
    mit set_phase(phase, total=None)/set_current(name)/
    record_result(station_id, result)/increment_checked(), siehe
    webui.NightScanState.

    Gibt {"scanned", "total", "stopped_early"} zurück."""
    if progress:
        progress.set_phase("scanning", total=len(stations))
    concurrency = max(1, int(cfg.get("concurrency", 1)))
    scanned = 0
    stopped_early = False

    def _scan_one(station):
        result = scan_station(station["url"], whisper_engine,
                               cfg["capture_timeout_seconds"], cfg["min_speech_seconds"])
        return station, result

    for i in range(0, len(stations), concurrency):
        if should_stop and should_stop():
            stopped_early = True
            break
        batch = stations[i:i + concurrency]
        if progress:
            progress.set_current(", ".join(s["name"] for s in batch))
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            for station, result in pool.map(_scan_one, batch):
                scanned += 1
                if progress:
                    progress.record_result(station["id"], result)
                    progress.increment_checked()

    return {"scanned": scanned, "total": len(stations), "stopped_early": stopped_early}
