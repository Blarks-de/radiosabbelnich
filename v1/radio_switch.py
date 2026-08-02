#!/usr/bin/env python3
"""
radio_switch.py — Internetradio abspielen, bei Moderation (Sprache) auf
den nächsten Sender in der Liste umschalten.

Erkennung: einfache Signal-Heuristik pro Analysefenster (~1 Sekunde):
  - Zero-Crossing-Rate (ZCR): Sprache hat i.d.R. eine höhere & "unruhigere" ZCR
  - Spektrale Flachheit (Flatness): Musik (v.a. mit Bässen/Harmonien) ist
    tonaler -> niedrigere Flatness. Sprache ist "geräuschhafter" -> höher.
  - Energie-Modulation: Sprache hat die typische Silben-Rhythmik (~3-6 Hz),
    die man grob an der Varianz der Kurzzeit-Energie erkennt.

Das ist KEINE ML-Klassifikation, sondern Schwellwert-Heuristik. Sie wird
also gelegentlich danebenliegen (z.B. bei A-cappella-Gesang, Rap, sehr
perkussiver Musik). Über die Parameter unten lässt sich das Verhalten
tunen.

Abhängigkeiten:
    sudo apt install ffmpeg libportaudio2
    pip install numpy sounddevice --break-system-packages

Nutzung:
    python3 radio_switch.py
    (Sender-Liste unten in STREAMS anpassen)
"""

import json
import os
import subprocess
import sys
import time
import threading
import numpy as np
import sounddevice as sd

# ----------------------------------------------------------------------
# KONFIGURATION
# ----------------------------------------------------------------------

# Sender werden aus dieser Datei geladen (liegt im selben Verzeichnis wie
# das Script). Einfach mit Texteditor pflegen, kein Code-Anfassen nötig.
STATIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stations.json")

DEFAULT_STATIONS = [
    {"name": "Radio Bob",       "url": "https://streams.radiobob.de/bob-live/mp3-192/mediaplayer"},
    {"name": "1LIVE",           "url": "https://wdr-1live-live.icecastssl.wdr.de/wdr/1live/live/mp3/128/stream.mp3"},
    {"name": "SWR3",            "url": "https://liveradio.swr.de/sw282p3/swr3/play.mp3"},
]


def load_stations() -> list:
    """Lädt die Senderliste aus stations.json. Legt eine Beispieldatei an,
    falls noch keine existiert."""
    if not os.path.exists(STATIONS_FILE):
        with open(STATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_STATIONS, f, ensure_ascii=False, indent=2)
        print(f"ℹ Keine {STATIONS_FILE} gefunden, Beispiel-Datei angelegt. "
              f"Bitte nach Belieben anpassen und Script neu starten.", file=sys.stderr)
        return DEFAULT_STATIONS

    with open(STATIONS_FILE, "r", encoding="utf-8") as f:
        stations = json.load(f)

    if not isinstance(stations, list) or not stations:
        raise ValueError(f"{STATIONS_FILE} muss eine nicht-leere Liste von "
                          f"{{'name': ..., 'url': ...}}-Objekten enthalten.")
    for s in stations:
        if "name" not in s or "url" not in s:
            raise ValueError(f"Eintrag ohne 'name'/'url' in {STATIONS_FILE}: {s}")

    return stations


STREAMS = load_stations()

SAMPLE_RATE = 22050          # Hz, für Analyse & Wiedergabe
WINDOW_SECONDS = 1.0         # Länge eines Analysefensters
CONSECUTIVE_SPEECH_TO_SWITCH = 8   # so viele Sprache-Fenster in Folge -> umschalten
COOLDOWN_AFTER_SWITCH = 8.0  # Sekunden Ruhe nach einem Switch, bevor wieder geschaltet wird
MAX_SKIPS_PER_ROUND = len(STREAMS)  # nicht endlos im Kreis rennen, falls überall Sprache läuft

# Heuristik-Schwellwerte (ggf. anpassen/tunen)
ZCR_SPEECH_MIN = 0.11
FLATNESS_SPEECH_MIN = 0.30
ENERGY_VAR_SPEECH_MIN = 0.40
VOTES_NEEDED = 2  # von 3 möglichen — je höher, desto vorsichtiger (Bass-Veto fängt Gesang separat ab)

# Bass-Veto: Musik (auch mit Gesang) hat fast immer nennenswerte
# Tiefton-Energie (Bass/Drums), reine Moderation so gut wie nie.
# Liegt der Anteil der Energie unter BASS_CUTOFF_HZ über diesem Wert,
# wird NIE auf "speech" klassifiziert, egal was die anderen Features sagen.
BASS_CUTOFF_HZ = 300
BASS_RATIO_MUSIC_VETO = 0.22

VERBOSE = False  # wird ggf. per --verbose Kommandozeilenparameter überschrieben

# ----------------------------------------------------------------------
# FEATURE-BERECHNUNG
# ----------------------------------------------------------------------

def zero_crossing_rate(samples: np.ndarray) -> float:
    signs = np.sign(samples)
    signs[signs == 0] = 1
    crossings = np.sum(signs[1:] != signs[:-1])
    return crossings / len(samples)


def spectral_flatness(samples: np.ndarray) -> float:
    windowed = samples * np.hanning(len(samples))
    spectrum = np.abs(np.fft.rfft(windowed)) + 1e-10
    geo_mean = np.exp(np.mean(np.log(spectrum)))
    arith_mean = np.mean(spectrum)
    return float(geo_mean / arith_mean)


def energy_modulation(samples: np.ndarray, sr: int) -> float:
    """Grobe Kennzahl für die Silben-Rhythmik: Varianz der Kurzzeit-Energie
    in ~50ms-Teilfenstern, normalisiert."""
    sub_len = max(1, int(sr * 0.05))
    n_subs = len(samples) // sub_len
    if n_subs < 2:
        return 0.0
    energies = np.array([
        np.mean(samples[i * sub_len:(i + 1) * sub_len].astype(np.float64) ** 2)
        for i in range(n_subs)
    ])
    if np.mean(energies) < 1e-6:
        return 0.0
    return float(np.std(energies) / (np.mean(energies) + 1e-6))


def bass_energy_ratio(samples: np.ndarray, sr: int, cutoff_hz: float) -> float:
    """Anteil der Signalenergie unterhalb von cutoff_hz an der Gesamtenergie."""
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples)))) ** 2
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / sr)
    total = np.sum(spectrum) + 1e-10
    bass = np.sum(spectrum[freqs <= cutoff_hz])
    return float(bass / total)


def classify_window(pcm_int16: np.ndarray, sr: int) -> str:
    """Liefert 'speech' oder 'music' für ein Analysefenster."""
    samples = pcm_int16.astype(np.float64) / 32768.0

    zcr = zero_crossing_rate(samples)
    flat = spectral_flatness(samples)
    energy_var = energy_modulation(samples, sr)
    bass_ratio = bass_energy_ratio(samples, sr, BASS_CUTOFF_HZ)

    votes = 0
    if zcr > ZCR_SPEECH_MIN:
        votes += 1
    if flat > FLATNESS_SPEECH_MIN:
        votes += 1
    if energy_var > ENERGY_VAR_SPEECH_MIN:
        votes += 1

    is_speech = votes >= VOTES_NEEDED and bass_ratio < BASS_RATIO_MUSIC_VETO

    if VERBOSE:
        veto = " [BASS-VETO]" if votes >= VOTES_NEEDED and bass_ratio >= BASS_RATIO_MUSIC_VETO else ""
        print(f"    [feat] zcr={zcr:.3f} flat={flat:.3f} evar={energy_var:.3f} "
              f"bass={bass_ratio:.3f} votes={votes}/3{veto} -> "
              f"{'SPEECH' if is_speech else 'music'}", file=sys.stderr)

    return "speech" if is_speech else "music"


# ----------------------------------------------------------------------
# STREAM-HANDLING
# ----------------------------------------------------------------------

class StreamPlayer:
    """Startet ffmpeg für eine Stream-URL, liefert PCM-Chunks und spielt sie ab."""

    def __init__(self, sample_rate: int):
        self.sample_rate = sample_rate
        self.proc = None
        self.out_stream = None

    def start(self, url: str):
        self.stop()
        self.proc = subprocess.Popen(
            [
                "ffmpeg", "-loglevel", "error",
                "-i", url,
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", str(self.sample_rate), "-ac", "1",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.out_stream = sd.RawOutputStream(
            samplerate=self.sample_rate, channels=1, dtype="int16"
        )
        self.out_stream.start()

    def read_window(self, seconds: float) -> np.ndarray:
        n_samples = int(self.sample_rate * seconds)
        n_bytes = n_samples * 2  # int16
        raw = b""
        while len(raw) < n_bytes:
            chunk = self.proc.stdout.read(n_bytes - len(raw))
            if not chunk:
                break
            raw += chunk
        if not raw:
            return np.array([], dtype=np.int16)
        pcm = np.frombuffer(raw, dtype=np.int16)
        # gleich abspielen
        self.out_stream.write(pcm.tobytes())
        return pcm

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None
        if self.out_stream:
            self.out_stream.stop()
            self.out_stream.close()
            self.out_stream = None


# ----------------------------------------------------------------------
# HAUPTLOGIK
# ----------------------------------------------------------------------

def main():
    global VERBOSE

    import argparse
    parser = argparse.ArgumentParser(description="Radio-Switcher: schaltet bei Moderation um.")
    parser.add_argument("--verbose", action="store_true",
                         help="Feature-Werte (zcr/flat/evar/bass) pro Fenster ausgeben")
    args = parser.parse_args()
    VERBOSE = args.verbose

    if not STREAMS:
        print("Bitte STREAMS-Liste im Script befüllen.", file=sys.stderr)
        sys.exit(1)

    current = 0
    player = StreamPlayer(SAMPLE_RATE)
    player.start(STREAMS[current]["url"])
    print(f"▶ Spiele: {STREAMS[current]['name']}")

    speech_streak = 0
    last_switch_time = 0.0

    try:
        while True:
            pcm = player.read_window(WINDOW_SECONDS)
            if pcm.size == 0:
                print(f"⚠ Stream '{STREAMS[current]['name']}' liefert nichts mehr, "
                      f"versuche neu zu verbinden ...", file=sys.stderr)
                player.start(STREAMS[current]["url"])
                time.sleep(1)
                continue

            label = classify_window(pcm, SAMPLE_RATE)
            now = time.time()

            if now - last_switch_time < COOLDOWN_AFTER_SWITCH:
                # gerade erst geschaltet -> keine hektischen weiteren Switches
                continue

            if label == "speech":
                speech_streak += 1
            else:
                speech_streak = 0

            if speech_streak >= CONSECUTIVE_SPEECH_TO_SWITCH:
                print(f"🎙  Moderation erkannt auf '{STREAMS[current]['name']}' — schalte um ...")
                speech_streak = 0
                skips = 0
                while skips < MAX_SKIPS_PER_ROUND:
                    current = (current + 1) % len(STREAMS)
                    player.start(STREAMS[current]["url"])
                    print(f"▶ Spiele: {STREAMS[current]['name']}")
                    last_switch_time = time.time()
                    skips += 1
                    # kurz reinhören, ob der neue Sender auch gerade Sprache hat
                    time.sleep(1.5)
                    probe = player.read_window(WINDOW_SECONDS)
                    if probe.size and classify_window(probe, SAMPLE_RATE) == "music":
                        break
                    print(f"   ... auch Sprache, probiere nächsten Sender.")

    except KeyboardInterrupt:
        print("\nBeende.")
    finally:
        player.stop()


if __name__ == "__main__":
    main()
