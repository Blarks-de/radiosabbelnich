#!/usr/bin/env python3
# Copyright (C) 2026 RadioSabbelNich
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 (or
# later), as published by the Free Software Foundation. See LICENSE.

"""
music_bpm.py — Phase 3 der Musik-Library-Roadmap (siehe README "Zukünftige
Features"): BPM-Schätzung für die "schnell"/"langsam"-Kategorie-Buttons
auf /musik. Reine Domänenlogik, kein Zugriff auf StreamSource/
SwitcherState -- wird ausschließlich aus music_scan.py heraus aufgerufen
(webserver-seitig, wie der gesamte Scan, siehe dortiger Modul-Docstring),
NIE aus dem Hauptloop.

Bibliothekswahl aubio statt librosa: librosa zieht einen sehr schweren
Dependency-Baum (numba+llvmlite allein >100 MB, dazu scipy/scikit-learn/
soundfile/audioread) -- passt schlecht zum bisher schlanken
Abhängigkeitsstil dieses Projekts (mutagen war die einzige Ergänzung vor
aubio). aubio ist zur LAUFZEIT sehr leicht (reine C-Bibliothek mit
dünnem Python-Binding), hat auf PyPI aber kein Wheel -- das Dockerfile
braucht deshalb einmalig einen C-Compiler zum Bauen (siehe dortiger
Kommentar), danach ist aubio selbst genügsam.

Nur ein Schnipsel wird dekodiert (Default 60s ab Sekunde 20, oder ab 0
bei kürzeren Tracks), nicht der komplette Track -- an echten Dateien
gemessen (siehe SESSION.md): ~0,25s pro Track inkl. ffmpeg-Decode.
Reicht für eine Tempo-Schätzung locker aus und hält den Scan trotz
BPM-Analyse praktikabel schnell, gerade weil die Größenordnung real
gemessen statt nur geschätzt ist.

Bekannte Grenze: Oktavfehler (halbe/doppelte Geschwindigkeit fälschlich
erkannt) sind ein generisches Problem jeder Beat-Tracking-Methode, kein
aubio-spezifischer Bug -- akzeptierte Ungenauigkeit, analog zum
Genre-Teilstring-Match in music_query.py."""

import logging
import subprocess

import aubio
import numpy as np

log = logging.getLogger("musicbpm")

SAMPLE_RATE = 44100
SNIPPET_SECONDS = 60
SNIPPET_START_SECONDS = 20  # übersprungen, um Intros/Stille zu meiden
WIN_SIZE = 1024
HOP_SIZE = 512


def _decode_snippet(path: str, duration_hint: float = None) -> np.ndarray:
    """Dekodiert einen Mono-Float32-Schnipsel per ffmpeg. Kürzere Tracks
    (laut duration_hint, z.B. aus mutagen) starten bei 0 statt bei
    SNIPPET_START_SECONDS, damit nicht versehentlich hinter das Trackende
    gesprungen wird und ffmpeg leer ausgibt."""
    start = SNIPPET_START_SECONDS
    if duration_hint is not None and duration_hint <= SNIPPET_START_SECONDS + 5:
        start = 0
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", str(start), "-t", str(SNIPPET_SECONDS),
         "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
        capture_output=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32)


def estimate_bpm(path: str, duration_hint: float = None) -> float | None:
    """Gibt die geschätzte BPM zurück, oder None bei zu kurzem/leerem
    Schnipsel (z.B. defekte Datei, reine Stille) -- kein Fehler, der
    Aufrufer (music_scan.py) speichert dann einfach NULL statt die ganze
    Zeile zu verwerfen."""
    try:
        pcm = _decode_snippet(path, duration_hint)
    except OSError as e:
        log.warning("⚠ BPM-Schätzung: ffmpeg-Aufruf für %s fehlgeschlagen (%s).", path, e)
        return None
    if len(pcm) < SAMPLE_RATE:  # < 1s Audio -- zu wenig für eine Aussage
        return None

    tempo_o = aubio.tempo("default", WIN_SIZE, HOP_SIZE, SAMPLE_RATE)
    for i in range(0, len(pcm) - HOP_SIZE, HOP_SIZE):
        tempo_o(pcm[i:i + HOP_SIZE])
    bpm = tempo_o.get_bpm()
    return round(bpm, 1) if bpm > 0 else None
