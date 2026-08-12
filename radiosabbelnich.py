#!/usr/bin/env python3
"""
radiosabbelnich.py — Internetradio abspielen, bei Moderation (Sprache) auf
den nächsten Sender in der Liste umschalten.

Erkennung pro Analysefenster (~1 Sekunde), in dieser Reihenfolge:
  1. Silero VAD (neuronales Netz, siehe speech_detector.py) — der Normalfall.
  2. Fällt VAD aus (Modell nicht ladbar o.ä.), springt die Signal-Heuristik
     weiter unten in dieser Datei ein: Zero-Crossing-Rate, spektrale
     Flachheit, Energie-Modulation, Bass-Veto. Das ist KEINE ML-
     Klassifikation, sondern Schwellwert-Heuristik, und liegt entsprechend
     öfter daneben (A-cappella, Rap, sehr perkussive Musik).
  3. Parallel Audio-Fingerprinting (fingerprint.py) gegen bereits gehörte
     Jingles/Werbespots -> Treffer schaltet sofort um.

Audio läuft intern als Stereo-PCM zum Output (Icecast/Soundkarte); die
Analyse rechnet auf einem parallel abgegriffenen Mono-Downmix.

Robustheit gegen tote Sender: liefert die aktuelle Quelle mehrfach
hintereinander kein Audio mehr, wird sie für STATION_DEAD_COOLDOWN
Sekunden aus der Rotation genommen und automatisch weitergeschaltet
(siehe Watchdog im Hauptloop) — ein einzelner hängender Stream darf den
Loop nicht anhalten.

Abhängigkeiten:
    sudo apt install ffmpeg libportaudio2
    pip install numpy silero-vad-lite sounddevice --break-system-packages
    (sounddevice nur für die lokale Wiedergabe ohne --icecast-url)

Nutzung:
    python3 radiosabbelnich.py
    (Sender-Liste in stations.json pflegen, oder über die Config-Seite
    des Web-Interfaces unter /config — siehe stations_store.py)

Logging: alles läuft über das logging-Modul (Konsole + rotierende
Logdatei, siehe logging_setup.py und --log-file/--verbose).
"""

import collections
import glob
import logging
import os
import select
import subprocess
import sys
import threading
import time
import wave
import numpy as np

import fingerprint
import logging_setup
import music_library
import music_scan
import news_break
import settings_store
import stt_filter
import webui
from speech_detector import SpeechDetector

log = logging.getLogger("radiosabbelnich")

# ----------------------------------------------------------------------
# KONFIGURATION
# ----------------------------------------------------------------------
# Sender-Verwaltung (Laden/Speichern/CRUD) lebt in stations_store.py,
# gemeinsam genutzt mit webui.py (Config-Seite unter /config). Die
# Rotationslogik hier unten referenziert Sender über ihre stabile "id",
# nicht über eine Listen-Position -> Hinzufügen/Löschen/Deaktivieren über
# die Config-Seite kann die laufende Wiedergabe nicht durcheinanderbringen.

SAMPLE_RATE = 44100          # Hz, für Analyse & Wiedergabe
WINDOW_SECONDS = 1.0         # Länge eines Analysefensters
CONSECUTIVE_SPEECH_TO_SWITCH = 3   # so viele Sprache-Fenster in Folge -> umschalten (VAD ist zuverlässiger als die Heuristik, daher kürzer als vorher; von 5 auf 3 gesenkt, um die Reaktionszeit auf Moderation zu verkürzen -- siehe SESSION.md)
COOLDOWN_AFTER_SWITCH = 8.0  # Sekunden Ruhe nach einem Switch, bevor wieder geschaltet wird
STREAM_READ_TIMEOUT = 8.0    # max. Wartezeit pro Analysefenster, bevor eine Quelle als tot gilt
                              # (verhindert, dass ein hängender Sender den Loop für immer blockiert)

# Watchdog gegen tote Sender: liefert die aktuelle Quelle STREAM_FAILURE_LIMIT
# Analysefenster in Folge gar nichts (leerer Read, s.o.), gilt der Sender als
# tot -> er fliegt für STATION_DEAD_COOLDOWN Sekunden aus der Rotation und der
# Player schaltet automatisch weiter.
#
# Warum das nicht optional ist: ohne Watchdog lief der Hauptloop bei einem
# toten Sender endlos "leeres Fenster -> neu verbinden -> 1s warten" im Kreis,
# ohne jemals output.write() aufzurufen. Real beobachtet mit einer aus der
# Kodinerds-Liste importierten DASH-URL (BBC Radio Scotland): 3569 Reconnect-
# Versuche über 8,5 Stunden, in denen der Icecast-Mount komplett weg war
# (Icecast wirft eine Source ohne Daten nach source-timeout=10s raus) — nur
# ein manueller Eingriff im Web-Interface holte den Player da wieder raus.
STREAM_FAILURE_LIMIT = 3
STATION_DEAD_COOLDOWN = 300.0

# Die nächsten PREBUFFER_COUNT Sender in Rotationsreihenfolge (ab dem
# aktuellen) laufen im Hintergrund bereits mit und halten die letzten
# PREBUFFER_SECONDS Sekunden vor -> ein Wechsel dorthin muss nicht erst
# neu verbinden, sondern kann sofort mit bereits vorhandenem Audio
# weitermachen. Kostet zusätzliche Bandbreite/CPU (bis zu PREBUFFER_COUNT
# zusätzliche ffmpeg-Prozesse parallel zum aktuellen), ist aber bei
# haushaltsüblichen Sendermengen unkritisch.
PREBUFFER_SECONDS = 10.0
PREBUFFER_COUNT = 5

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

# Fingerprinting: nach so vielen Sekunden Sprache am Stück wird der Clip
# gefingerprintet und gegen die DB bekannter Jingles/Ads geprüft. Muss
# kleiner als CONSECUTIVE_SPEECH_TO_SWITCH sein, sonst hat's keinen Vorteil.
FINGERPRINT_TRIGGER_SECONDS = 2
FINGERPRINT_DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fingerprints.db")

# Jeder Fingerprint-Check (Treffer oder neu gelernter Clip) wird zusätzlich
# als WAV mitgeschnitten -> falls ein Treffer einen unerwünschten Switch
# auslöst, kann man sich den Clip hinterher tatsächlich anhören statt zu
# raten, ob es wirklich Werbung/Jingle war oder ein Fehlalarm (z.B. ein
# kurzer senderübergreifender Sting, der über ein Musikbett läuft).
FINGERPRINT_CLIPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fingerprint_clips")
FINGERPRINT_CLIPS_KEEP = 100  # älteste Mitschnitte löschen, wenn mehr als das rumliegen

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


def classify_window(pcm_int16: np.ndarray, sr: int) -> tuple:
    """Liefert ('speech'|'music', score) für ein Analysefenster. `score`
    (0..1) ist grob votes/3 -- KEINE kalibrierte Wahrscheinlichkeit wie
    beim VAD, nur zur groben Anzeige gedacht (Web-Interface-
    "Bullshitometer", siehe webui.py). Bei Bass-Veto auf 0 gesetzt, damit
    die Anzeige mit der tatsächlichen Entscheidung übereinstimmt, statt
    trotz "music"-Label einen hohen Wert zu zeigen."""
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

    veto = votes >= VOTES_NEEDED and bass_ratio >= BASS_RATIO_MUSIC_VETO
    is_speech = votes >= VOTES_NEEDED and not veto
    score = 0.0 if veto else votes / 3.0

    if log.isEnabledFor(logging.DEBUG):
        veto_marker = " [BASS-VETO]" if veto else ""
        log.debug("[feat] zcr=%.3f flat=%.3f evar=%.3f bass=%.3f votes=%d/3%s -> %s",
                  zcr, flat, energy_var, bass_ratio, votes, veto_marker,
                  "SPEECH" if is_speech else "music")

    return ("speech" if is_speech else "music"), score


def save_fingerprint_debug_clip(pcm_int16: np.ndarray, sr: int, filename: str):
    """Schreibt einen Fingerprint-Kandidaten-Clip (Mono, s16le) als WAV nach
    FINGERPRINT_CLIPS_DIR, damit man ihn sich im Nachhinein anhören kann.
    Räumt dabei die ältesten Mitschnitte weg, falls mehr als
    FINGERPRINT_CLIPS_KEEP rumliegen (unbeaufsichtigter Dauerbetrieb soll
    nicht unbegrenzt Disk-Speicher fressen)."""
    try:
        os.makedirs(FINGERPRINT_CLIPS_DIR, exist_ok=True)
        path = os.path.join(FINGERPRINT_CLIPS_DIR, filename)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm_int16.tobytes())

        existing = sorted(
            glob.glob(os.path.join(FINGERPRINT_CLIPS_DIR, "*.wav")),
            key=os.path.getmtime,
        )
        for old_path in existing[:-FINGERPRINT_CLIPS_KEEP]:
            os.remove(old_path)
        log.debug("Fingerprint-Debug-Clip geschrieben: %s", filename)
    except OSError as e:
        log.warning("⚠ Fingerprint-Debug-Clip konnte nicht geschrieben werden: %s", e)


# ----------------------------------------------------------------------
# STREAM-HANDLING
# ----------------------------------------------------------------------

class StreamSource:
    """Startet ffmpeg für eine Stream-URL und liefert zwei parallele PCM-Ströme
    aus demselben Prozess: Mono (s16le, pipe:1) für die Analyse-Pipeline
    (VAD/Heuristik/Fingerprint) und Stereo (s16le, zusätzliche Pipe) fürs
    tatsächliche Playback/Icecast-Encoding. Ein Prozess statt zwei, damit der
    Stream nicht doppelt geholt werden muss. Kümmert sich NICHT um
    Ausgabe/Wiedergabe — das macht der Output."""

    def __init__(self, sample_rate: int):
        self.sample_rate = sample_rate
        self.proc = None
        self._stereo_read_fd = None

    def start(self, url: str, realtime: bool = False):
        """`realtime=True` fügt ffmpegs `-re` ein (Input in "nativer"
        Wall-Clock-Geschwindigkeit lesen, statt so schnell wie CPU/Disk
        erlauben). Für echte Radio-URLs unnötig und deshalb NICHT der
        Default: die sind durch die Netzwerk-Auslieferung beim Sender
        selbst schon in Echtzeit getaktet (das ist der Mechanismus, der
        den ganzen Hauptloop auf ~1 Analysefenster/Sekunde hält, ohne dass
        radiosabbelnich.py selbst irgendwo bremst).

        PFLICHT dagegen für lokale Dateien (siehe news_break.py/
        Nachrichten-Pause): ohne -re dekodiert ffmpeg eine lokale Datei so
        schnell wie möglich — ein 35s-Clip landet dann in
        Sekundenbruchteilen komplett in den Ausgabe-Pipes, statt über
        seine echte Spieldauer verteilt zu werden (gemessen: 35s Audio in
        0,1s statt 35s Wall-Clock ohne -re; mit -re exakt 35s)."""
        self.stop()
        stereo_read_fd, stereo_write_fd = os.pipe()
        os.set_inheritable(stereo_write_fd, True)
        cmd = ["ffmpeg", "-loglevel", "error"]
        if realtime:
            cmd += ["-re"]
        cmd += [
            "-i", url,
            "-map", "0:a", "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", str(self.sample_rate), "-ac", "1",
            "pipe:1",
            "-map", "0:a", "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", str(self.sample_rate), "-ac", "2",
            f"pipe:{stereo_write_fd}",
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            pass_fds=(stereo_write_fd,),
        )
        os.close(stereo_write_fd)
        self._stereo_read_fd = stereo_read_fd

    def read_window(self, seconds: float, timeout: float = STREAM_READ_TIMEOUT):
        """Liest ein Analysefenster aus beiden Pipes parallel (via select),
        damit keine Pipe volllaufen und ffmpeg blockieren kann, während wir
        auf die andere warten. Gibt (mono, stereo) als int16-Arrays zurück;
        stereo ist interleaved (L,R,L,R,...).

        Gibt spätestens nach `timeout` Sekunden zurück, auch wenn das
        Fenster noch nicht voll ist (z.B. weil die Quelle nicht antwortet
        oder eine Station nie eine Verbindung zustande bringt) — sonst
        blockiert eine tote Quelle den kompletten Hauptloop für immer,
        inklusive manuellem Umschalten auf einen anderen Sender."""
        n_samples = int(self.sample_rate * seconds)
        mono_fd = self.proc.stdout.fileno()
        stereo_fd = self._stereo_read_fd
        targets = {mono_fd: n_samples * 2, stereo_fd: n_samples * 2 * 2}
        buffers = {mono_fd: b"", stereo_fd: b""}
        remaining = {mono_fd, stereo_fd}

        deadline = time.monotonic() + timeout
        while remaining:
            time_left = deadline - time.monotonic()
            if time_left <= 0:
                break
            ready, _, _ = select.select(list(remaining), [], [], time_left)
            if not ready:
                break  # Timeout, keine Daten mehr angekommen
            for fd in ready:
                need = targets[fd] - len(buffers[fd])
                chunk = os.read(fd, need)
                if not chunk:
                    remaining.discard(fd)
                    continue
                buffers[fd] += chunk
                if len(buffers[fd]) >= targets[fd]:
                    remaining.discard(fd)

        mono = np.frombuffer(buffers[mono_fd], dtype=np.int16)
        stereo = np.frombuffer(buffers[stereo_fd], dtype=np.int16)
        return mono, stereo

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None
        if self._stereo_read_fd is not None:
            try:
                os.close(self._stereo_read_fd)
            except OSError:
                pass
            self._stereo_read_fd = None


class PrebufferedSource:
    """Hält im Hintergrund eine eigene StreamSource am Laufen und sammelt
    fortlaufend die letzten PREBUFFER_SECONDS Sekunden (Mono+Stereo) in
    einem Ringpuffer. Wird eine solche Quelle tatsächlich zur aktuellen
    befördert (promote()), bekommt der Hauptloop sofort einen fertigen
    Audio-Batch zum Weiterschreiben UND die weiterlaufende StreamSource
    zur Übernahme — kein Neuverbinden nötig, der Übergang ist dadurch
    praktisch nahtlos.

    Der Hintergrund-Thread liest in denselben WINDOW_SECONDS-Schnipseln
    wie der Hauptloop; promote()/stop() stoppen ihn per Event und warten
    (join), bis das gerade laufende read_window() fertig ist — die Pipes
    dürfen nie von zwei Seiten gleichzeitig gelesen werden. Im
    schlimmsten Fall wartet man so ~WINDOW_SECONDS auf die Übergabe,
    deutlich weniger als eine frische Neuverbindung gekostet hätte."""

    def __init__(self, sample_rate: int, url: str, buffer_seconds: float = PREBUFFER_SECONDS,
                 name: str = "prebuf"):
        self.url = url
        self.name = name
        self.source = StreamSource(sample_rate)
        self._buffer_windows = max(1, int(round(buffer_seconds / WINDOW_SECONDS)))
        self._mono_windows = collections.deque(maxlen=self._buffer_windows)
        self._stereo_windows = collections.deque(maxlen=self._buffer_windows)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self.dead = False  # True, falls die Quelle unterwegs gestorben ist (EOF/Timeout)

    def start(self):
        self.source.start(self.url)
        self.dead = False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"pb-{self.name}"[:14])
        self._thread.start()
        log.debug("Puffer gestartet: %s (%d Fenster à %.1fs)",
                  self.name, self._buffer_windows, WINDOW_SECONDS)

    def _run(self):
        while not self._stop_event.is_set():
            mono, stereo = self.source.read_window(WINDOW_SECONDS)
            if mono.size == 0 and stereo.size == 0:
                self.dead = True
                log.debug("Puffer-Quelle liefert nichts mehr, markiere als tot: %s", self.url)
                return
            with self._lock:
                self._mono_windows.append(mono)
                self._stereo_windows.append(stereo)

    def _join(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=STREAM_READ_TIMEOUT + 1)
            if self._thread.is_alive():
                # Der Reader hängt noch in read_window(). Die Quelle darf
                # jetzt NICHT übernommen werden — sonst läsen Hauptloop und
                # Reader-Thread gleichzeitig aus derselben Pipe und teilen
                # sich die Bytes auf (zerhacktes Audio). Als tot markieren:
                # der Aufrufer verwirft die Quelle dann, ihr .stop() beendet
                # ffmpeg, und der hängende Read läuft dadurch leer aus.
                self.dead = True
                log.warning("⚠ Puffer-Thread für %s reagiert nicht — Quelle wird verworfen.",
                            self.url)
            self._thread = None

    def promote(self):
        """Stoppt den Hintergrund-Thread und gibt (windows, source) zurück:
        `windows` ist die gepufferte Fenster-Liste als [(mono, stereo), ...]
        in chronologischer Reihenfolge (älteste zuerst -- so, wie die
        Playout-Deque im Hauptloop sie beim Wechsel direkt übernehmen kann,
        siehe adopt_windows()/push_and_drain() in main()), plus die
        weiterlaufende StreamSource zur Übernahme durch den Aufrufer.

        Bewusst keine konkatenierten Arrays mehr (anders als früher): die
        Fenster-Granularität bleibt erhalten, damit ein Wechsel die Deque
        ohne erneutes Zerschneiden (und ohne die stereo_tail()-Frame-
        Grenzen-Problematik erneut einzuführen) übernehmen kann."""
        self._join()
        with self._lock:
            windows = list(zip(self._mono_windows, self._stereo_windows))
        return windows, self.source

    def stop(self):
        """Verwirft die Quelle komplett (z.B. weil sie nicht mehr zu den
        nächsten PREBUFFER_COUNT Sendern gehört)."""
        self._join()
        self.source.stop()


class LocalOutput:
    """Gibt PCM-Chunks über die lokale Soundkarte aus (PortAudio/sounddevice)."""

    def __init__(self, sample_rate: int):
        import sounddevice as sd  # nur hier nötig, nicht headless-tauglich
        self.stream = sd.RawOutputStream(samplerate=sample_rate, channels=2, dtype="int16")
        self.stream.start()

    def write(self, pcm: np.ndarray):
        self.stream.write(pcm.tobytes())

    def close(self):
        self.stream.stop()
        self.stream.close()


class IcecastOutput:
    """Encodiert PCM-Chunks per ffmpeg und pusht sie dauerhaft auf einen
    Icecast-Mountpoint. Bleibt über Sender-Wechsel hinweg bestehen — nur die
    StreamSource wird gewechselt, der Icecast-Client hört nahtlos weiter.

    Baut die Verbindung selbst neu auf, wenn Icecast sie kappt (z.B. Icecast-
    Container-Neustart) — ohne das würde ffmpeg beim ersten Schreibfehler
    dauerhaft tot bleiben und der Broadcast für immer stumm, obwohl der
    Hauptloop munter weiterläuft und Sender wechselt."""

    RECONNECT_COOLDOWN = 5.0  # Sekunden zwischen Reconnect-Versuchen, kein Popen-Spam bei Dauerausfall

    def __init__(self, sample_rate: int, icecast_url: str, bitrate: str = "128k"):
        self.sample_rate = sample_rate
        self.icecast_url = icecast_url
        self.bitrate = bitrate
        self.proc = None
        self._last_reconnect_attempt = 0.0
        self._start_proc()

    def _start_proc(self):
        self.proc = subprocess.Popen(
            [
                "ffmpeg", "-loglevel", "error",
                "-f", "s16le", "-ar", str(self.sample_rate), "-ac", "2",
                "-i", "pipe:0",
                "-acodec", "libmp3lame", "-b:a", self.bitrate,
                "-content_type", "audio/mpeg",
                "-f", "mp3",
                self.icecast_url,
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def write(self, pcm: np.ndarray):
        try:
            self.proc.stdin.write(pcm.tobytes())
        except (BrokenPipeError, OSError):
            now = time.time()
            if now - self._last_reconnect_attempt < self.RECONNECT_COOLDOWN:
                return
            self._last_reconnect_attempt = now
            log.warning("⚠ Icecast-Verbindung unterbrochen, versuche neu zu verbinden ...")
            try:
                self.proc.kill()
                self.proc.wait(timeout=3)
            except Exception:
                pass
            try:
                self._start_proc()
                log.info("📡 Icecast-Verbindung wiederhergestellt.")
            except Exception as e:
                log.error("⚠ Icecast-Reconnect fehlgeschlagen: %s", e)

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            self.proc.wait(timeout=5)




def alive_stations(active: list, dead_until: dict, keep_id: str = None) -> list:
    """Filtert Sender raus, die der Watchdog gerade als tot markiert hat
    (siehe STREAM_FAILURE_LIMIT/STATION_DEAD_COOLDOWN) — Rotationsreihenfolge
    bleibt sonst unverändert.

    `keep_id` (i.d.R. der aktuell laufende Sender) bleibt immer drin, auch
    wenn er selbst gerade als tot markiert ist: sonst würde er aus der Liste
    fallen, an der sich die Puffer-Positionen orientieren, und alle
    Hintergrund-Puffer würden bei jedem Wechsel unnötig neu aufgebaut."""
    now = time.time()
    return [s for s in active
            if dead_until.get(s["id"], 0.0) <= now or s["id"] == keep_id]


def prebuffer_target_ids(current_id: str, active: list, count: int = PREBUFFER_COUNT) -> list:
    """Liefert die IDs der nächsten `count` Sender in Rotationsreihenfolge
    ab (aber ohne) dem aktuellen — dieselbe Reihenfolge, in der
    do_switch() automatisch durchprobieren würde."""
    ids = [s["id"] for s in active]
    if current_id not in ids or len(active) <= 1 or count <= 0:
        return []
    pos = ids.index(current_id)
    n = len(active)
    count = min(count, n - 1)
    return [ids[(pos + 1 + i) % n] for i in range(count)]


def sync_prebuffer(prebuffer: dict, current_id: str, active: list, sample_rate: int,
                    buffer_seconds: float = PREBUFFER_SECONDS, count: int = PREBUFFER_COUNT) -> list:
    """Startet/stoppt Hintergrund-Puffer, damit `prebuffer` genau die
    nächsten `count` Sender (in Rotationsreihenfolge ab dem aktuellen)
    enthält — nicht mehr, nicht weniger.

    Gibt die IDs der Sender zurück, deren Puffer-Quelle unterwegs gestorben
    ist. Der Aufrufer entscheidet, was damit passiert: einfach hier neu
    starten wäre falsch, denn bei einer dauerhaft toten URL bekäme man einen
    frischen ffmpeg-Prozess pro Schleifendurchlauf, also im Sekundentakt
    (live beobachtet). Der Hauptloop setzt sie stattdessen für
    STATION_DEAD_COOLDOWN auf die Sperrliste — danach kriegen sie
    automatisch wieder eine Chance."""
    wanted_ids = prebuffer_target_ids(current_id, active, count)
    wanted_set = set(wanted_ids)

    died = []
    for sid in list(prebuffer.keys()):
        if sid not in wanted_set:
            prebuffer.pop(sid).stop()
        elif prebuffer[sid].dead:
            prebuffer.pop(sid).stop()
            died.append(sid)

    stations_by_id = {s["id"]: s for s in active}
    for sid in wanted_ids:
        if sid not in prebuffer and sid in stations_by_id:
            pb = PrebufferedSource(sample_rate, stations_by_id[sid]["url"], buffer_seconds,
                                   name=sid)
            pb.start()
            prebuffer[sid] = pb

    return died


# ----------------------------------------------------------------------
# HAUPTLOGIK
# ----------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="RadioSabbelNich: schaltet bei Moderation um.")
    parser.add_argument("--verbose", action="store_true",
                         help="Feature-Werte (zcr/flat/evar/bass), VAD-Wahrscheinlichkeiten und "
                              "Fingerprint-Infos auch auf der Konsole ausgeben (in der Logdatei "
                              "stehen sie ohnehin immer)")
    parser.add_argument("--log-file", default=logging_setup.DEFAULT_LOG_FILE,
                         help=f"Pfad der rotierenden Logdatei (leer = keine Datei, "
                              f"default: {logging_setup.DEFAULT_LOG_FILE})")
    parser.add_argument("--icecast-url", default=None,
                         help="Statt lokaler Wiedergabe auf einen Icecast-Mountpoint pushen, "
                              "z.B. icecast://source:PASSWORT@dockfish.icefish-ghost.ts.net:8000/mix.mp3")
    parser.add_argument("--bitrate", default="192k",
                         help="MP3-Bitrate für den Icecast-Restream (default: 128k)")
    parser.add_argument("--no-fingerprint", action="store_true",
                         help="Jingle/Ad-Wiedererkennung per Audio-Fingerprinting deaktivieren")
    parser.add_argument("--fingerprint-db", default=FINGERPRINT_DB_FILE,
                         help=f"Pfad zur Fingerprint-SQLite-DB (default: {FINGERPRINT_DB_FILE})")
    parser.add_argument("--webui-port", type=int, default=0,
                         help="Port für das Web-Interface (0 = deaktiviert)")
    parser.add_argument("--icecast-admin-url", default=None,
                         help="Basis-URL des Icecast-Servers für die Hörer-Abfrage im "
                              "Web-Interface, z.B. http://icecast-radioswitch:8000")
    parser.add_argument("--icecast-admin-user", default=None)
    parser.add_argument("--icecast-admin-password", default=None)
    parser.add_argument("--icecast-mount", default=None,
                         help="Icecast-Mountpoint für die Hörer-Abfrage, z.B. /radiosabbelnich.mp3")
    parser.add_argument("--icecast-public-port", default=None,
                         help="Port, unter dem der Browser den Icecast-Stream selbst erreicht "
                              "(für den eingebetteten Player im Web-Interface), z.B. 8000")
    parser.add_argument("--icecast-public-ssl-port", default=None,
                         help="Port, unter dem der Icecast-Stream per HTTPS erreichbar ist "
                              "(nur relevant, wenn Icecast per TLS_CERT_FILE/TLS_KEY_FILE in "
                              ".env TLS anbietet), z.B. 8443. Webui nutzt das nur, wenn die "
                              "Player-Seite selbst per HTTPS aufgerufen wird -- siehe dortiger "
                              "Kommentar zu autoUrl, sonst Mismatch mit Icecasts Klartext-Port.")
    parser.add_argument("--tls-cert-file", default=None,
                         help="Pfad zum TLS-Zertifikat fürs Web-Interface (PEM). Nur wirksam, "
                              "wenn settings.json 'tls_enabled' gesetzt ist und die Datei "
                              "lesbar ist -- siehe settings_store.py/webui.start_server().")
    parser.add_argument("--tls-key-file", default=None,
                         help="Pfad zum privaten TLS-Schlüssel fürs Web-Interface (PEM).")
    parser.add_argument("--news-mp3-folder-host", default=None,
                         help="Host-Pfad von NEWS_MP3_FOLDER (.env), rein informativ -- der "
                              "Container kennt sonst nur seinen eigenen Mount-Pfad "
                              "(/app/news_mp3), nicht wovon der stammt. Nur fürs Anzeigen auf "
                              "der Config-Seite, siehe webui.py/make_handler().")
    parser.add_argument("--vosk-model-folder-host", default=None,
                         help="Host-Pfad von VOSK_MODEL_FOLDER (.env), analog zu "
                              "--news-mp3-folder-host.")
    parser.add_argument("--music-library-folder-host", default=None,
                         help="Host-Pfad von MUSIC_LIBRARY_FOLDER (.env), analog zu "
                              "--news-mp3-folder-host.")
    parser.add_argument("--music-library-db", default=music_scan.MUSIC_LIBRARY_DB_FILE,
                         help=f"Pfad zur Musik-Library-SQLite-DB für den ID3-Scan "
                              f"(default: {music_scan.MUSIC_LIBRARY_DB_FILE})")
    parser.add_argument("--music-library-covers-dir", default=music_scan.MUSIC_LIBRARY_COVERS_DIR,
                         help=f"Ordner für gecachte Cover-Bilder aus dem Musik-Library-Scan "
                              f"(default: {music_scan.MUSIC_LIBRARY_COVERS_DIR})")
    args = parser.parse_args()

    log_path = logging_setup.setup(args.log_file, verbose=args.verbose)
    log.info("🎬 RadioSabbelNich startet.")
    if log_path:
        log.info("📝 Logdatei: %s (immer DEBUG, rotierend: %d × %d MB)",
                 log_path, logging_setup.BACKUP_COUNT, logging_setup.MAX_BYTES // (1024 * 1024))

    if args.icecast_url:
        output = IcecastOutput(SAMPLE_RATE, args.icecast_url, args.bitrate)
        log.info("📡 Restream läuft auf: %s (%s)", args.icecast_url.split("@")[-1], args.bitrate)
    else:
        output = LocalOutput(SAMPLE_RATE)

    fp_db = None
    if not args.no_fingerprint:
        fp_db = fingerprint.FingerprintDB(args.fingerprint_db)
        log.info("🔎 Fingerprint-DB: %s", args.fingerprint_db)

    detector = SpeechDetector(SAMPLE_RATE)
    if detector.available:
        log.info("🗣  Sprache-Erkennung: Silero VAD")
    else:
        log.info("🗣  Sprache-Erkennung: Signal-Heuristik (Fallback)")

    def classify(pcm: np.ndarray, stt_lang: str) -> str:
        label, prob = detector.classify(pcm)
        if label is None:
            label, prob = classify_window(pcm, SAMPLE_RATE)  # Heuristik-Fallback
        # Rohwert VOR der STT-Verknüpfung fürs Web-Interface-
        # "Bullshitometer" -- bewusst der reine VAD/Heuristik-Wert, nicht
        # das kombinierte Ergebnis: STT sampelt viel seltener (Sekunden
        # statt ~1x/Fenster), ein kombinierter Wert würde sprunghaft
        # wirken statt sich flüssig zu bewegen.
        state.set_speech_probability(prob)
        # Einmal abrufen, für combine_label() (Switch-Logik) UND
        # live_confidence()/live_language() (STT-Balken im Web-Interface) --
        # alle drei sollen exakt denselben Befund sehen, nicht mehrere
        # zeitlich leicht versetzte last_verdict()-Aufrufe. stt_lang kommt
        # vom Aufrufer (siehe unten) -- dieselbe Sprache, mit der auch
        # gerade gesampelt wird (settings_store.resolve_stt_language() der
        # AKTUELLEN Sender-Kategorie).
        verdict = stt.last_verdict()
        state.set_stt_probability(stt_filter.live_confidence(verdict, state.stt_filter_cfg, stt_lang))
        state.set_stt_language(stt_filter.live_language(verdict, state.stt_filter_cfg, stt_lang))
        # Sprachen werden lazy geladen (siehe stt_filter.py), nicht nur bei
        # einem expliziten reload() -- deshalb hier bei jedem Tick mit
        # aktualisieren, günstig (max. MAX_LOADED_VOSK_LANGUAGES Einträge).
        state.set_stt_language_status(stt.language_status())
        # Kalibrierungs-Wizard (Teil 1b): No-Op, solange keine Session
        # läuft (siehe add_calibration_sample()). Nur Befunde IN der
        # gerade erzwungenen Kalibrierungssprache zählen -- verdict kann
        # trotz stt_lang == calibration_language noch einen ALTEN Befund
        # einer anderen Sprache enthalten (kurz nach dem Start), der
        # Sprachabgleich hier verhindert, dass der in die falsche
        # Kalibrierungsstufe einsortiert wird.
        if verdict is not None and verdict[3] == stt_lang:
            state.add_calibration_sample(verdict[0], verdict[1], verdict[2])
        # Einzige Kopplungsstelle mit stt_filter.py -- ohne (frischen,
        # sprachlich passenden) STT-Befund ist das ein No-Op, siehe
        # combine_label()-Docstring.
        return stt_filter.combine_label(label, verdict, state.stt_filter_cfg, stt_lang)

    state = webui.SwitcherState()
    active = state.active_stations
    if not active:
        log.error("Keine aktivierten Sender in stations.json — bitte über die "
                  "Config-Seite (/config) mindestens einen aktivieren.")
        sys.exit(1)

    stt = stt_filter.SttFilter(state.stt_filter_cfg)
    state.set_stt_status(*stt.status())
    state.set_stt_language_status(stt.language_status())
    # STT sammelt in diesem Ringpuffer die letzten CLIP_SECONDS Sekunden
    # Mono-PCM (unabhängig vom aktuellen VAD-Label, siehe CLAUDE.md) und
    # sampelt daraus alle sample_interval_seconds -- siehe Hauptloop unten.
    stt_ring = collections.deque(maxlen=max(1, round(stt_filter.CLIP_SECONDS / WINDOW_SECONDS)))
    last_stt_sample_at = 0.0

    httpd = None
    if args.webui_port:
        icecast_cfg = {
            "admin_url": args.icecast_admin_url,
            "user": args.icecast_admin_user,
            "password": args.icecast_admin_password,
            "mount": args.icecast_mount,
            "public_port": args.icecast_public_port,
            "public_ssl_port": args.icecast_public_ssl_port,
        }
        # tls_enabled ist der explizite Nutzer-Schalter (settings.json,
        # per /config setzbar); die Zertifikatspfade selbst kommen aus
        # .env/docker-compose.yml (TLS_CERT_FILE/TLS_KEY_FILE) -- ohne
        # gesetzten Schalter bleiben sie ungenutzt, auch wenn die Dateien
        # gemountet sind. start_server() fällt bei fehlenden/ungültigen
        # Dateien selbst auf HTTP zurück statt abzustürzen.
        tls_cert = args.tls_cert_file if state.tls_enabled else None
        tls_key = args.tls_key_file if state.tls_enabled else None
        host_paths = {
            "news_mp3_folder": args.news_mp3_folder_host,
            "vosk_model_folder": args.vosk_model_folder_host,
            "music_library_folder": args.music_library_folder_host,
        }
        httpd = webui.start_server(args.webui_port, state, icecast_cfg, args.fingerprint_db,
                                    tls_cert_file=tls_cert, tls_key_file=tls_key,
                                    host_paths=host_paths, log_file_path=log_path,
                                    music_library_db_path=args.music_library_db,
                                    music_library_covers_dir=args.music_library_covers_dir)

    source = StreamSource(SAMPLE_RATE)
    current = active[0]  # aktuell gespielter Sender (dict: id/name/url/category/enabled)
    source.start(current["url"])
    state.set_current(current["id"])
    log.info("▶ Spiele: %s", current["name"])

    # Sender, die der Watchdog gerade als tot markiert hat: id -> Zeitpunkt,
    # ab dem sie wieder mitspielen dürfen (siehe alive_stations()).
    dead_until = {}

    # Nächste Sender in Rotationsreihenfolge laufen im Hintergrund bereits
    # mit (siehe PrebufferedSource/sync_prebuffer weiter oben) -> Wechsel
    # dorthin fühlen sich praktisch nahtlos an, statt neu verbinden zu
    # müssen. Sekunden/Anzahl sind über die Config-Seite einstellbar
    # (settings.json via settings_store.py) und live in state gecacht.
    prebuffer = {}
    sync_prebuffer(prebuffer, current["id"], active, SAMPLE_RATE,
                   state.prebuffer_seconds, state.prebuffer_count)
    log.info("⏱  Puffere die nächsten %d Sender %.0fs im Voraus.",
             len(prebuffer), state.prebuffer_seconds)

    def write_audio(pcm: np.ndarray):
        """Einziger Weg, Audio an den Output zu geben."""
        if pcm.size:
            output.write(pcm)

    # Playout-Verzögerung für den AKTUELL laufenden Sender (Timeshift, damit
    # Erkennung vor der Hörer-Ausgabe passieren kann, nicht danach -- siehe
    # SESSION.md-Eintrag zur Einführung dieses Mechanismus). `playout` ist
    # eine Deque von (mono, stereo)-Fenstern, `playout_primed` sagt, ob sie
    # gerade auf Zieltiefe gehalten wird (True) oder im Passthrough-Modus
    # läuft (False, kein Delay -- siehe push_and_drain()).
    playout = collections.deque()
    playout_primed = False

    def playout_target_windows() -> int:
        return max(1, round(state.prebuffer_seconds / WINDOW_SECONDS))

    def push_and_drain(mono: np.ndarray, stereo: np.ndarray):
        """Einziger Weg, ein frisch gelesenes Analysefenster des aktuellen
        Senders Richtung Hörer zu bringen.

        Unprimed (Passthrough, z.B. nach einem frischen/nicht vorgepufferten
        Wechsel, siehe switch_to_station()/do_switch()): sofort schreiben,
        exakt wie vor Einführung des Delays -- kein Vorlauf, aber auch keine
        Lücke oder doppeltes Audio, siehe CLAUDE.md-Abschnitt zum Playout-
        Delay ("bewusst NICHT gemacht": kein lückenloser Übergang von 0 auf
        volle Verzögerung, das ist ohne Zeitdehnung nicht möglich).

        Primed: `mono`/`stereo` werden nur ans Ende der Deque gehängt (die
        Klassifikation im Hauptloop passiert auf genau diesem frisch
        gepushten Fenster, also VOR der Ausgabe). Erst wenn die Deque über
        die Zieltiefe hinauswächst, wird das jeweils älteste Fenster
        abgezogen und ausgegeben -- ein Push, höchstens ein Pop pro
        Durchlauf, damit die Ausgabe im selben Realzeit-Takt bleibt wie
        heute (kein Schub wie vor dem 2026-08-03-Fix)."""
        nonlocal playout
        if not playout_primed:
            write_audio(stereo)
            return
        playout.append((mono, stereo))
        if len(playout) > playout_target_windows():
            _, old_stereo = playout.popleft()
            write_audio(old_stereo)

    def adopt_windows(windows: list):
        """Übernimmt die Fenster-Liste eines PrebufferedSource.promote()
        komplett als neue Playout-Deque und aktiviert den Delay (primed).
        Der Kandidat wurde bereits `state.prebuffer_seconds` lang im
        Hintergrund gefüllt (siehe sync_prebuffer()/PREBUFFER_SECONDS) --
        die Deque steht also schon auf Zieltiefe, Drain über
        push_and_drain() läuft ab dem nächsten Fenster ohne Lücke weiter."""
        nonlocal playout, playout_primed
        playout = collections.deque(windows)
        playout_primed = True

    def reset_playout():
        """Verwirft die Playout-Deque und schaltet auf Passthrough (kein
        Delay) -- für frische Wechsel ohne vorgewärmten Puffer, siehe
        push_and_drain()."""
        nonlocal playout, playout_primed
        playout = collections.deque()
        playout_primed = False

    def quick_forward(seconds: float = 0.3):
        """Nach einem direkten Sender-Wechsel OHNE vorgewärmten Puffer
        (manuell oder erzwungen durch einen Config-Reload) sofort einen
        kurzen Schnipsel lesen und direkt an den Output weiterreichen,
        statt bis zum nächsten vollen WINDOW_SECONDS-Analysefenster zu
        warten. Ohne das vergehen nach einem Wechsel spürbar mehrere
        Sekunden, bis überhaupt neue Audio bei Icecast/Hörern ankommt —
        nicht weil die Verbindung zur neuen Quelle lange dauert (die steht
        meist in <1s), sondern weil read_window() sonst erst ein volles
        1-Sekunden-Fenster sammelt, bevor etwas geschrieben wird.

        Geht bewusst direkt über write_audio(), nicht über
        push_and_drain(): reset_playout() hat den Passthrough-Modus schon
        aktiviert, dieser Schnipsel wäre ohnehin sofort wieder raus."""
        _, stereo = source.read_window(seconds)
        write_audio(stereo)

    def switch_to_station(station: dict) -> str:
        """Wechselt auf `station` — nutzt einen laufenden Hintergrund-
        Puffer falls vorhanden (sofortiger, nahtloser Übergang MIT vollem
        Playout-Delay: die Fenster-Liste des Kandidaten wird komplett als
        neue Playout-Deque übernommen, siehe adopt_windows()), sonst
        frischer Connect + quick_forward() im Passthrough-Modus (siehe
        reset_playout()/push_and_drain() -- kein Delay für diesen Fall,
        bewusste Grenze, siehe CLAUDE.md). Aktualisiert `source`, gibt aber
        KEIN state.set_current()/Logmeldung aus — das bleibt Sache der
        Aufrufer, die je nach Situation unterschiedliche Meldungen
        ausgeben. Gibt zurück, ob der Puffer genutzt werden konnte.

        Eine Puffer-Quelle, die unterwegs gestorben ist (`pb.dead`), wird
        NICHT übernommen — sonst schaltet man sehenden Auges auf eine
        Quelle, die schon nichts mehr liefert, und der Watchdog im
        Hauptloop müsste den Sender gleich wieder abräumen. Stattdessen
        ganz normal frisch verbinden: vielleicht war es nur ein Hänger."""
        nonlocal source, stream_failures
        stream_failures = 0
        pb = prebuffer.pop(station["id"], None)
        if pb is not None:
            windows, adopted_source = pb.promote()
            if pb.dead:
                adopted_source.stop()
                log.warning("⚠ Puffer von '%s' war tot — verbinde stattdessen frisch.",
                            station["name"])
            else:
                source.stop()
                source = adopted_source
                if windows:
                    adopt_windows(windows)
                    return "prebuffered"
                # Kandidat existierte, hat aber noch kein einziges Fenster
                # gesammelt (Puffer gerade erst gestartet) -- Passthrough
                # statt primed-mit-leerer-Deque: sonst entstünde eine
                # künstliche Lücke, bis sich die Deque erst wieder auf
                # Zieltiefe gefüllt hat. Kein quick_forward() nötig, die
                # Verbindung steht schon -- der nächste reguläre
                # read_window() im Hauptloop liefert von selbst.
                reset_playout()
                return "prebuffered-empty"
        reset_playout()
        source.start(station["url"])
        quick_forward()
        return "fresh"

    speech_streak = 0
    last_switch_time = 0.0
    speech_buffer = []       # sammelt PCM-Chunks des aktuellen Sprache-Laufs
    fp_checked_this_run = False
    stream_failures = 0      # leere Reads in Folge vom aktuellen Sender (Watchdog)

    # Nachrichten-Pause (siehe news_break.py): `current` bleibt während der
    # Pause bewusst UNVERÄNDERT der pausierte Sender -> Watchdog/do_switch/
    # sync_prebuffer laufen (falls sie überhaupt aufgerufen werden) mit
    # korrekten Daten weiter, nur `source` zeigt vorübergehend auf die MP3.
    news_break_active = False
    news_break_resume_id = None   # id des Senders, zu dem danach zurückgeschaltet wird
    # Zuletzt gespielte Dateien (Basename), jüngste zuletzt -> vermeidet
    # nicht nur die direkt vorige, sondern die letzten RECENT_HISTORY_SIZE
    # Dateien bei der Zufallsauswahl (siehe news_break.pick_random_mp3()).
    news_break_recent_files = collections.deque(maxlen=news_break.RECENT_HISTORY_SIZE)
    news_break_served_slot = None  # welcher Slot (siehe news_break.active_slot) schon dran war

    def note_news_break_interrupted():
        """Eine explizite Nutzer-Aktion (manueller Switch, 'ZAPPEN!')
        während einer laufenden Nachrichten-Pause beendet die Pause sofort
        — eigene Entscheidung schlägt Automatik, wie überall sonst auch in
        diesem Programm. Markiert den Slot als bedient, damit die Pause
        nicht im selben Zeitfenster gleich wieder anspringt und den
        gerade erst gewählten Sender sofort wieder verdrängt."""
        nonlocal news_break_active, news_break_served_slot
        if news_break_active:
            state.set_news_break(False)
            news_break_active = False
            news_break_served_slot = news_break.active_slot(state.news_break_cfg)

    def resume_from_news_break(reason: str):
        """Beendet die Nachrichten-Pause und schaltet zurück zum Sender,
        der vorher lief — oder, falls der inzwischen deaktiviert/gelöscht
        wurde, zum ersten aktivierten Sender (wie beim Reload-erzwungenen
        Wechsel). Aufgerufen sowohl bei abgelaufenem Zeitfenster als auch
        bei einer MP3, die kürzer ist als das Fenster."""
        nonlocal current, news_break_active, last_switch_time
        nonlocal speech_streak, speech_buffer, fp_checked_this_run
        state.set_news_break(False)
        news_break_active = False
        active_now = state.active_stations
        resume = next((s for s in active_now if s["id"] == news_break_resume_id), None)
        if resume is None and active_now:
            resume = active_now[0]
            log.warning("📰 Nachrichten-Pause-Ende: ursprünglicher Sender nicht mehr "
                        "aktiv, schalte auf: %s", resume["name"])
        if resume is None:
            log.error("📰 Nachrichten-Pause-Ende: keine aktivierten Sender mehr "
                      "konfiguriert — Wiedergabe pausiert, bis wieder einer aktiv ist.")
            return
        current = resume
        used_buffer = switch_to_station(current)
        state.set_current(current["id"])
        log.info("📰 %s — zurück zu: %s%s", reason, current["name"],
                 " (aus Puffer)" if used_buffer.startswith("prebuffered") else "")
        last_switch_time = time.time()
        speech_streak = 0
        speech_buffer = []
        fp_checked_this_run = False

    def start_news_break_mp3(cfg: dict) -> bool:
        """Startet die nächste MP3 der laufenden Nachrichten-Pause — sowohl
        beim Erstbetreten des Fensters als auch wenn die vorige MP3 zu Ende
        ist, das Fenster (window_minutes) selbst aber noch läuft. Ohne das
        endete die Pause ursprünglich nach genau einer Datei, auch wenn vom
        Zeitfenster noch viel übrig war (siehe SESSION.md). Die letzten
        news_break.RECENT_HISTORY_SIZE Dateien werden bei der Auswahl
        vermieden, sofern der Ordner genug andere MP3s enthält. Gibt False
        zurück, wenn keine MP3 verfügbar ist (Ordner leer/nicht lesbar) —
        der Aufrufer entscheidet dann, was stattdessen passiert."""
        path = news_break.pick_random_mp3(cfg.get("mp3_folder"), recent=news_break_recent_files)
        if path is None:
            return False
        # deque.append() mutiert nur das bestehende Objekt, kein Rebinding
        # des Namens -> kein "nonlocal" nötig (anders als bei der früheren
        # einzelnen news_break_last_file-Variable, die hier reassigned wurde).
        news_break_recent_files.append(os.path.basename(path))
        # Playout-Deque leeren/unprimed: eine evtl. noch gefüllte Deque vom
        # PAUSIERTEN Sender darf nicht mit MP3-Fenstern vermischt werden
        # (siehe push_and_drain()). Während der Pause wird ohnehin nicht
        # klassifiziert (s.u.), ein Delay hätte hier keinen Nutzen --
        # Passthrough ist also auch inhaltlich richtig, nicht nur technisch
        # nötig.
        reset_playout()
        # realtime=True ist hier PFLICHT, nicht optional -- siehe
        # StreamSource.start()-Docstring. StreamSource.start() räumt die
        # alte Verbindung (den pausierten Sender bzw. die vorige MP3)
        # selbst auf.
        source.start(path, realtime=True)
        state.set_news_break(True, news_break_recent_files[-1])
        quick_forward()
        return True

    # Radio/Musiksammlung-Modus (siehe CLAUDE.md, "Modus als Fork ganz oben
    # im Hauptloop"): KEINE Erweiterung der News-Break-Mechanik oben -- die
    # pausiert nur einen einzelnen Sender für eine MP3 und kehrt danach
    # automatisch zurück. Der Musik-Modus ist stattdessen ein persistenter
    # Top-Level-Zustand, in dem sync_prebuffer()/classify()/News-Break-
    # Slot-Prüfung/Watchdog unten schlicht nicht laufen -- deutlich
    # einfacher, als sie durchzuflechten, und die Nutzervorgabe war
    # "STT/VAD im Musik-Modus komplett deaktiviert", nicht nur pausiert.
    current_mode = state.mode
    music_folder = None       # Container-Pfad, beim Play-Klick einmal gelesen
    music_tracks = []         # Dateinamen (Basenames), alphabetisch
    music_index = -1          # -1 = idle, nichts läuft

    if current_mode == "music":
        # current_mode kann direkt beim Start schon "music" sein (aus
        # settings.json persistiert, siehe settings_store.py) -- der
        # Hauptloop ist oben (source.start()/sync_prebuffer()) aber IMMER
        # erst als Radio hochgefahren, unabhängig vom Modus. Das hier
        # macht das einmalig rückgängig, statt die Transition-Logik im
        # Loop unten zu duplizieren oder den Radio-Start oben bedingt zu
        # machen (der bleibt bewusst unverändert/unbedingt, siehe CLAUDE.md
        # "Ein Prozess, zwei Akteure" -- ein einzelner Startpfad ist
        # weniger fehleranfällig als zwei).
        source.stop()
        for pb in prebuffer.values():
            pb.stop()
        prebuffer.clear()
        log.info("🎵 Start im Musiksammlung-Modus (persistiert) — Radio-Verbindung wieder getrennt.")

    def start_music_track(idx: int):
        nonlocal music_index
        path = os.path.join(music_folder, music_tracks[idx])
        source.start(path, realtime=True)  # realtime=True PFLICHT, siehe oben
        music_index = idx
        state.set_music_status(True, music_tracks[idx], idx, len(music_tracks))
        log.info("🎵 Spiele: %s (%d/%d)", music_tracks[idx], idx + 1, len(music_tracks))

    def stop_music_track():
        nonlocal music_index
        if music_index >= 0:
            source.stop()
            music_index = -1
            state.set_music_status(False)
            log.info("🎵 Musiksammlung: Wiedergabe gestoppt.")

    def do_switch(reason: str):
        """Springt reihum zum nächsten (aktivierten) Sender, bis Musik läuft
        (oder alle durch sind). Wird sowohl von der Heuristik als auch bei
        einem Fingerprint-Treffer aufgerufen.

        Bricht sofort ab, sobald ein manueller Switch-Request reinkommt —
        sonst könnte ein Nutzerklick im Web-Interface bis zu
        len(aktive Sender) * (1.5s + STREAM_READ_TIMEOUT) warten müssen,
        falls das automatische Durchprobieren gerade läuft.

        Gepufferte Kandidaten (siehe PrebufferedSource) werden direkt
        anhand ihres bereits vorhandenen Puffers beurteilt (kein
        1.5s-Warten + frisches Fenster nötig) — bei "music" sofort
        übernommen, bei "speech" verworfen und der nächste Kandidat
        probiert.

        Kandidaten, die der Watchdog gerade als tot markiert hat, werden
        übersprungen, ohne sie überhaupt anzufassen."""
        nonlocal current, last_switch_time, source, stream_failures
        log.info("🎙  %s auf '%s' — schalte um ...", reason, current["name"])
        active = state.active_stations
        if not active:
            log.warning("   ... keine aktivierten Sender konfiguriert, bleibe hier.")
            return
        ids = [s["id"] for s in active]
        pos = ids.index(current["id"]) if current["id"] in ids else -1
        skips = 0
        while skips < len(active):
            pending = state.pop_manual_request()
            if pending is not None:
                # nicht einfach verwerfen: Request zurücklegen, der
                # Hauptloop erledigt den eigentlichen Wechsel (inkl.
                # current/state/Streak-Reset) beim nächsten Durchlauf
                state.request_switch(pending)
                log.info("   ... manueller Switch angefordert, breche Auto-Suche ab.")
                return
            pos = (pos + 1) % len(active)
            candidate = active[pos]
            skips += 1

            if dead_until.get(candidate["id"], 0.0) > time.time():
                log.debug("   ... '%s' ist als tot markiert, überspringe.", candidate["name"])
                continue

            pb = prebuffer.pop(candidate["id"], None)
            if pb is not None:
                windows, candidate_source = pb.promote()
                if pb.dead:
                    # Quelle ist im Hintergrund gestorben. Früher wurde ein
                    # leerer Puffer mangels Audio als "music" durchgewunken
                    # und der tote Sender übernommen — genau so landete der
                    # Player auf einer Quelle, die nie wieder etwas lieferte.
                    candidate_source.stop()
                    dead_until[candidate["id"]] = time.time() + STATION_DEAD_COOLDOWN
                    log.warning("   ... '%s' liefert im Puffer nichts mehr — für %.0f Min. "
                                "aus der Rotation.", candidate["name"], STATION_DEAD_COOLDOWN / 60)
                    continue
                tail = windows[-1][0] if windows else np.array([], dtype=np.int16)
                if not tail.size:
                    # Puffer noch leer (gerade erst gestartet) — kein Grund,
                    # den Sender zu verdächtigen, aber auch keine Grundlage
                    # für ein Urteil: normal frisch verbinden statt blind
                    # übernehmen.
                    candidate_source.stop()
                    log.debug("   ... Puffer von '%s' noch leer, probiere frisch.",
                              candidate["name"])
                else:
                    candidate_stt_lang = settings_store.resolve_stt_language(
                        candidate["category"], state.stt_filter_cfg)
                    if classify(tail, candidate_stt_lang) == "music":
                        current = candidate
                        source.stop()
                        source = candidate_source
                        state.set_current(current["id"])
                        stream_failures = 0
                        adopt_windows(windows)
                        log.info("▶ Spiele: %s (aus Puffer, nahtlos, %.0fs Playout-Delay)",
                                 current["name"], state.prebuffer_seconds)
                        last_switch_time = time.time()
                        break
                    candidate_source.stop()
                    log.info("   ... auch Sprache (gepuffert), probiere nächsten Sender.")
                    continue

            reset_playout()
            current = candidate
            source.start(current["url"])
            state.set_current(current["id"])
            stream_failures = 0
            log.info("▶ Spiele: %s (frisch, ohne Playout-Delay)", current["name"])
            last_switch_time = time.time()
            time.sleep(1.5)
            probe_mono, probe_stereo = source.read_window(WINDOW_SECONDS)
            if probe_mono.size:
                write_audio(probe_stereo)
                candidate_stt_lang = settings_store.resolve_stt_language(
                    current["category"], state.stt_filter_cfg)
                if classify(probe_mono, candidate_stt_lang) == "music":
                    break
            else:
                # Gar nichts gekommen: der Sender ist nicht bloß gerade am
                # Reden, er antwortet nicht. Direkt aus der Rotation nehmen,
                # statt ihn beim nächsten Durchlauf gleich wieder anzufassen.
                dead_until[current["id"]] = time.time() + STATION_DEAD_COOLDOWN
                log.warning("   ... '%s' liefert kein Audio — für %.0f Min. aus der Rotation.",
                            current["name"], STATION_DEAD_COOLDOWN / 60)
                continue
            log.info("   ... auch Sprache, probiere nächsten Sender.")

    try:
        while True:
            # Modus-Fork (siehe CLAUDE.md): request/pop wie beim manuellen
            # Senderwechsel, weil der eigentliche Übergang `source` anfasst
            # -- das darf nur der Hauptloop. Erst NACH dem echten Übergang
            # (unten) wird current_mode/state.mode aktualisiert, nicht
            # schon beim bloßen Request.
            requested_mode = state.pop_mode_change_request()
            if requested_mode and requested_mode != current_mode:
                if requested_mode == "music":
                    if news_break_active:
                        # Sauber beenden, kein Resume nötig -- die ganze
                        # Radio-Quelle wird gleich sowieso gestoppt.
                        state.set_news_break(False)
                        news_break_active = False
                        news_break_served_slot = news_break.active_slot(state.news_break_cfg)
                    source.stop()
                    for pb in prebuffer.values():
                        pb.stop()
                    prebuffer.clear()
                    reset_playout()
                    music_tracks = []
                    music_index = -1
                    settings_store.update(current_mode="music")
                    state.set_mode("music")
                    current_mode = "music"
                    log.info("🎵 Wechsel in Musiksammlung-Modus (Radio pausiert).")
                else:
                    stop_music_track()
                    active_now = state.active_stations
                    resume = current if current["id"] in {s["id"] for s in active_now} else (
                        active_now[0] if active_now else None)
                    if resume is not None:
                        current = resume
                        reset_playout()
                        source.start(current["url"])
                        quick_forward()
                        state.set_current(current["id"])
                        last_switch_time = time.time()
                        speech_streak = 0
                        speech_buffer = []
                        fp_checked_this_run = False
                        log.info("📻 Wechsel in Radio-Modus: spiele %s.", current["name"])
                    else:
                        log.error("📻 Wechsel in Radio-Modus: keine aktivierten Sender "
                                  "konfiguriert -- Wiedergabe pausiert, bis wieder einer aktiv ist.")
                    settings_store.update(current_mode="radio")
                    state.set_mode("radio")
                    current_mode = "radio"
                continue

            if current_mode == "music":
                # Kein sync_prebuffer()/classify()/News-Break/Watchdog hier
                # -- siehe Docstring am Kopf des Musik-Modus-Blocks oben
                # (Definition von start_music_track()/stop_music_track()).
                if state.pop_music_stop_request():
                    stop_music_track()
                    continue
                if state.pop_music_play_request():
                    if music_index < 0:
                        music_folder = state.music_library_path
                        tracks = music_library.list_tracks(music_folder)
                        if not tracks:
                            log.warning("🎵 Musiksammlung: keine Wiedergabe gestartet "
                                        "(Ordner leer/nicht lesbar: %s).", music_folder)
                        else:
                            music_tracks = tracks
                            start_music_track(0)
                    continue
                skip_dir = state.pop_music_skip_request()
                if skip_dir and music_index >= 0 and music_tracks:
                    start_music_track((music_index + skip_dir) % len(music_tracks))
                    continue
                if music_index < 0:
                    # Idle -- niemand hat Play gedrückt, kurz schlafen statt
                    # Busy-Loop.
                    time.sleep(0.3)
                    continue
                pcm, pcm_stereo = source.read_window(WINDOW_SECONDS)
                if pcm.size == 0:
                    # Track zu Ende -> nächster, zyklisch (Playlist läuft
                    # endlos weiter, bis Stop kommt).
                    start_music_track((music_index + 1) % len(music_tracks))
                    continue
                # Direkt schreiben, keine Playout-Deque/Klassifikation --
                # im Musik-Modus gibt es nichts zu erkennen/verzögern.
                write_audio(pcm_stereo)
                continue

            # ab hier: radio-Zweig, unverändert.
            #
            # Puffer für die nächsten Sender ab der aktuellen Position
            # aktuell halten -- billig genug, um einmal pro Schleifen-
            # durchlauf zu prüfen (kein Rebuild, falls schon passend).
            # Als tot markierte Sender werden gar nicht erst gepuffert.
            active_now = state.active_stations
            died = sync_prebuffer(prebuffer, current["id"],
                                   alive_stations(active_now, dead_until, current["id"]),
                                   SAMPLE_RATE, state.prebuffer_seconds, state.prebuffer_count)
            for sid in died:
                # Schon im Hintergrund gestorben, bevor überhaupt jemand
                # dorthin schalten wollte -> gar nicht erst als Kandidat
                # anbieten (und vor allem nicht im Sekundentakt neu starten).
                dead_until[sid] = time.time() + STATION_DEAD_COOLDOWN
                name = next((s["name"] for s in active_now if s["id"] == sid), sid)
                log.warning("⚠ Hintergrund-Puffer von '%s' liefert nichts — für %.0f Min. "
                            "aus der Rotation.", name, STATION_DEAD_COOLDOWN / 60)

            if state.pop_reload_request():
                # Sender oder Puffer-Einstellungen wurden über die
                # Config-Seite geändert -> beides neu laden
                prev_prebuffer_settings = (state.prebuffer_seconds, state.prebuffer_count)
                prev_stt_cfg = state.stt_filter_cfg
                state.reload()
                new_stt_cfg = state.stt_filter_cfg
                # Nur bei Änderung an Engine-relevanten Feldern tatsächlich
                # neu laden -- Schwellwert/Intervall/Verknüpfung/
                # category_languages liest combine_label() bzw.
                # resolve_stt_language() ohnehin bei jedem Aufruf frisch aus
                # state.stt_filter_cfg, dafür ist kein Reload nötig.
                # "languages" (Modellpfade PRO Sprache, siehe SESSION.md)
                # ersetzt hier das frühere einzelne "vosk_model_path" -- bei
                # Vosk ist reload() dank Lazy-Load ohnehin billig (räumt nur
                # den Cache, siehe stt_filter.py), lädt also nicht bei jeder
                # Sprachänderung gleich alle Modelle neu.
                if any(prev_stt_cfg.get(f) != new_stt_cfg.get(f)
                       for f in ("enabled", "engine", "languages", "whisper_model_size")):
                    stt.reload(new_stt_cfg)
                    state.set_stt_status(*stt.status())
                    state.set_stt_language_status(stt.language_status())
                if (state.prebuffer_seconds, state.prebuffer_count) != prev_prebuffer_settings:
                    # PrebufferedSource-Instanzen haben ihre Puffergröße
                    # fest einkompiliert (deque(maxlen=...) bei der
                    # Konstruktion) -> bestehende Puffer taugen nicht mehr,
                    # alle verwerfen und beim nächsten sync_prebuffer()
                    # (nächster Schleifendurchlauf) mit den neuen Werten
                    # frisch aufbauen lassen.
                    for pb in prebuffer.values():
                        pb.stop()
                    prebuffer.clear()
                    # Die aktuell laufende Playout-Deque hält auf die ALTE
                    # Zieltiefe ausgelegten Inhalt -- ein gapless Umstellen
                    # auf die neue Tiefe ist ohne Zeitdehnung nicht möglich
                    # (siehe CLAUDE.md, Abschnitt Playout-Delay). Reset auf
                    # Passthrough statt Drift/Fehlverhalten: kurzer,
                    # seltener Vorgang (nur bei tatsächlicher Änderung der
                    # Einstellung über /config), Delay baut sich beim
                    # nächsten warmen Wechsel wieder neu auf.
                    if playout_primed:
                        reset_playout()
                    log.info("⏱  Puffer-/Delay-Einstellungen geändert: %d Sender × %.0fs.",
                             state.prebuffer_count, state.prebuffer_seconds)
                active = state.active_stations
                active_ids = {s["id"] for s in active}
                if not active:
                    log.warning("⚠ Keine aktivierten Sender mehr konfiguriert — "
                                "spiele den aktuellen weiter, bis wieder einer aktiv ist.")
                elif news_break_active:
                    # `current["id"]` zeigt während der Pause weiter auf den
                    # pausierten (also nicht aktiv gespielten) Sender -> er
                    # kann fälschlich als "nicht mehr aktiv" erscheinen, wenn
                    # sich die Senderliste ändert. NICHT deswegen die MP3
                    # kappen und live umschalten -- resume_from_news_break()
                    # prüft ohnehin frisch, ob news_break_resume_id noch
                    # aktiv ist, sobald die Pause tatsächlich endet.
                    log.info("⚙ Senderliste neu geladen (Nachrichten-Pause läuft weiter).")
                elif current["id"] not in active_ids:
                    current = active[0]
                    used_buffer = switch_to_station(current)
                    state.set_current(current["id"])
                    log.info("⚙ Senderliste geändert, aktueller Sender nicht mehr aktiv "
                             "— schalte auf: %s%s", current["name"],
                             " (aus Puffer)" if used_buffer.startswith("prebuffered") else "")
                    last_switch_time = time.time()
                    speech_streak = 0
                    speech_buffer = []
                    fp_checked_this_run = False
                else:
                    log.info("⚙ Senderliste neu geladen (%d aktiv).", len(active))
                continue

            manual_id = state.pop_manual_request()
            # "or news_break_active": während der Pause ist `current["id"]`
            # weiter der PAUSIERTE Sender (siehe oben) -- klickt der Nutzer
            # genau DEN in der Senderliste an, wäre manual_id == current["id"]
            # und der Klick würde ohne diesen Zusatz stillschweigend
            # verschluckt, statt die Pause zu beenden und dorthin zu wechseln.
            if manual_id is not None and (news_break_active or manual_id != current["id"]):
                station = next((s for s in state.active_stations if s["id"] == manual_id), None)
                if station is None:
                    log.warning("⚠ Manueller Switch auf unbekannten/inaktiven Sender "
                                "ignoriert: %s", manual_id)
                    continue
                note_news_break_interrupted()
                current = station
                # Ausdrücklicher Nutzerwunsch: eine alte Tot-Markierung darf
                # dem nicht im Weg stehen (der Sender kann längst wieder da sein).
                dead_until.pop(current["id"], None)
                used_buffer = switch_to_station(current)
                state.set_current(current["id"])
                log.info("🎛  Manuell umgeschaltet auf: %s%s", current["name"],
                         " (aus Puffer)" if used_buffer.startswith("prebuffered") else "")
                last_switch_time = time.time()
                speech_streak = 0
                speech_buffer = []
                fp_checked_this_run = False
                continue

            if state.pop_skip_request():
                # "ZAPPEN!"-Knopf: Nutzer hat selbst Sprache erkannt,
                # auch wenn VAD/Heuristik (noch) nicht angeschlagen haben
                note_news_break_interrupted()
                speech_streak = 0
                speech_buffer = []
                fp_checked_this_run = False
                do_switch("Nutzer meldete Gesabbel (ZAPPEN!-Knopf)")
                continue

            if state.pop_filter_toggle_request():
                # "Sabbelfilter (de)aktivieren"-Knopf: automatische
                # Erkennung komplett pausieren/wieder anschalten. Streak-
                # Buchhaltung zurücksetzen, sonst könnte ein alter,
                # längst irrelevanter Sprache-Streak beim Wieder-
                # Aktivieren sofort einen Switch auslösen.
                new_enabled = not state.filter_enabled
                state.set_filter_enabled(new_enabled)
                speech_streak = 0
                speech_buffer = []
                fp_checked_this_run = False
                log.info("🔇 Sabbelfilter %s (automatisches Umschalten %s).",
                         "wieder aktiviert" if new_enabled else "deaktiviert",
                         "läuft weiter" if new_enabled else "pausiert")
                continue

            # Nachrichten-Pause: Zeitfenster prüfen. Bewusst NACH den
            # Nutzer-Aktionen oben -- ein manueller Wechsel in DIESEM
            # Durchlauf (der news_break_active/news_break_served_slot schon
            # aktualisiert hat) hat Vorrang vor einem Neueintritt hier.
            news_break_cfg = state.news_break_cfg
            slot = news_break.active_slot(news_break_cfg)
            if slot and not news_break_active and slot != news_break_served_slot:
                news_break_served_slot = slot
                news_break_resume_id = current["id"]
                if start_news_break_mp3(news_break_cfg):
                    news_break_active = True
                    log.info("📰 Nachrichten-Pause: spiele '%s' (zurück zu '%s' danach)",
                             news_break_recent_files[-1], current["name"])
                    last_switch_time = time.time()
                    speech_streak = 0
                    speech_buffer = []
                    fp_checked_this_run = False
                continue
            if news_break_active and not slot:
                resume_from_news_break("Nachrichten-Pause-Fenster abgelaufen")
                continue

            pcm, pcm_stereo = source.read_window(WINDOW_SECONDS)
            if pcm.size == 0:
                if news_break_active:
                    # Die MP3 ist zu Ende (kürzer als das Fenster) -- das ist
                    # ein planmäßiges Ereignis, kein toter Sender. VOR dem
                    # Watchdog abgefangen, damit der davon nichts mitbekommt
                    # (siehe Modul-weite Konstanten oben/CLAUDE.md).
                    #
                    # Läuft das Zeitfenster selbst noch (window_minutes noch
                    # nicht um)? Dann nächste zufällige MP3 nachladen statt
                    # sofort zurückzuschalten -- ursprünglicher Bug: pro
                    # Fenster wurde nur genau eine Datei gespielt, danach
                    # sofort zurück zum Sender, egal wie viel vom Fenster
                    # noch übrig war (siehe SESSION.md).
                    if (news_break.active_slot(state.news_break_cfg)
                            and start_news_break_mp3(state.news_break_cfg)):
                        log.info("📰 Nachrichten-Pause: nächste MP3 '%s'", news_break_recent_files[-1])
                        last_switch_time = time.time()
                        speech_streak = 0
                        speech_buffer = []
                        fp_checked_this_run = False
                        continue
                    resume_from_news_break("Nachrichten-Pause-MP3 zu Ende")
                    continue
                # WATCHDOG: erst ein paar Reconnects (ein kurzer Hänger soll
                # keinen Senderwechsel auslösen), dann ist der Sender raus.
                stream_failures += 1
                if stream_failures < STREAM_FAILURE_LIMIT:
                    log.warning("⚠ Stream '%s' liefert nichts mehr (%d/%d), "
                                "versuche neu zu verbinden ...",
                                current["name"], stream_failures, STREAM_FAILURE_LIMIT)
                    source.start(current["url"])
                    time.sleep(1)
                    continue

                log.error("⛔ Stream '%s' liefert nach %d Versuchen immer noch nichts — "
                          "nehme ihn für %.0f Min. aus der Rotation und schalte weiter.",
                          current["name"], stream_failures, STATION_DEAD_COOLDOWN / 60)
                dead_until[current["id"]] = time.time() + STATION_DEAD_COOLDOWN
                stream_failures = 0
                speech_streak = 0
                speech_buffer = []
                fp_checked_this_run = False

                if not alive_stations(state.active_stations, dead_until):
                    # Alle aktivierten Sender sind als tot markiert (z.B.
                    # weil das Netz komplett weg war). Sperren aufheben und
                    # allen nochmal eine Chance geben, statt in einer Runde
                    # aus lauter übersprungenen Kandidaten hängenzubleiben.
                    log.error("⛔ Alle aktivierten Sender sind als tot markiert — "
                              "hebe die Sperren auf und probiere von vorn.")
                    dead_until.clear()

                do_switch("Sender liefert kein Audio")
                continue

            stream_failures = 0

            # push_and_drain() statt write_audio(): normalerweise (primed)
            # geht pcm/pcm_stereo NUR in die Playout-Deque, ausgegeben wird
            # das ältere Fenster vom Deque-Kopf -- die Klassifikation
            # weiter unten läuft dadurch auf Audio, das der Hörer erst in
            # bis zu state.prebuffer_seconds bekommt (siehe adopt_windows()/
            # push_and_drain() oben). Während der Nachrichten-Pause ist
            # playout_primed False (siehe start_news_break_mp3()) -- dann
            # ist das hier ein reines Passthrough wie vor Einführung des
            # Delays, kein Sonderfall nötig.
            push_and_drain(pcm, pcm_stereo)

            if news_break_active:
                # Keine automatische Erkennung während der Nachrichten-
                # Pause -- die MP3 enthält u.U. selbst Sprache/Jingles, das
                # soll VAD/Heuristik/Fingerprint nicht als "Moderation auf
                # dem echten Sender" fehldeuten. Unabhängig vom Sabbelfilter-
                # Zustand des Nutzers (der bleibt unangetastet).
                continue

            if not state.filter_enabled:
                # Sabbelfilter aus: einfach weiterspielen, keine
                # automatische Erkennung/Umschaltung
                continue

            # STT sampelt kontinuierlich (unabhängig vom VAD-Label, siehe
            # CLAUDE.md), aber nur alle sample_interval_seconds und nur
            # solange oben weder Nachrichten-Pause noch Sabbelfilter-Aus
            # gegriffen haben -- sonst wäre es verschwendete Rechenzeit.
            stt_ring.append(pcm)
            now_stt = time.monotonic()
            # Sprache der AKTUELLEN Sender-Kategorie -- einmal pro Durchlauf
            # aufgelöst, gilt sowohl fürs Sampling-Ziel unten als auch für
            # classify() (Verdict-Interpretation), siehe deren Docstrings.
            # Läuft gerade eine Kalibrierungs-Session (Teil 1b, siehe
            # webui.py/SwitcherState), erzwingt sie ihre Zielsprache statt
            # der Kategorie-Auflösung -- der Nutzer schaltet dafür manuell
            # auf einen Test-Sender dieser Sprache.
            calibration_lang = state.calibration_language
            stt_lang = (calibration_lang if calibration_lang is not None
                        else settings_store.resolve_stt_language(current["category"], state.stt_filter_cfg))
            if now_stt - last_stt_sample_at >= state.stt_filter_cfg["sample_interval_seconds"]:
                last_stt_sample_at = now_stt
                stt.sample_async(np.concatenate(stt_ring), SAMPLE_RATE, stt_lang, state.stt_filter_cfg)

            label = classify(pcm, stt_lang)

            if calibration_lang is not None:
                # Kalibrierung läuft: STT sampelt oben normal weiter (dafür
                # ist classify() gerade gelaufen), aber die automatische
                # Switch-Logik bleibt komplett unangetastet -- sonst könnte
                # ein durch die erzwungene Kalibrierungs-Sprache verfälschtes
                # combine_label()-Ergebnis mitten in der Kalibrierung einen
                # Wechsel auslösen (der Test-Sender ist ja evtl. gar nicht in
                # der Kategorie, die normalerweise zu dieser Sprache gehört).
                continue

            now = time.time()

            if now - last_switch_time < COOLDOWN_AFTER_SWITCH:
                # gerade erst geschaltet -> keine hektischen weiteren Switches
                continue

            if label == "speech":
                speech_streak += 1
                speech_buffer.append(pcm)

                # Sobald genug Sprache am Stück da ist: einmal pro Lauf
                # gegen die Fingerprint-DB prüfen. Treffer -> sofort
                # switchen, ohne auf CONSECUTIVE_SPEECH_TO_SWITCH zu warten.
                if fp_db and not fp_checked_this_run and speech_streak >= FINGERPRINT_TRIGGER_SECONDS:
                    fp_checked_this_run = True
                    combined = np.concatenate(speech_buffer)
                    match = fp_db.match_or_learn(combined, SAMPLE_RATE, current["name"])
                    ts = time.strftime("%Y%m%d-%H%M%S")
                    if match:
                        save_fingerprint_debug_clip(
                            combined, SAMPLE_RATE,
                            f"match_clip{match['clip_id']}_{current['id']}_{ts}.wav",
                        )
                        state.set_last_fingerprint_clip(match["clip_id"], match["label"], current["id"])
                        state.set_fingerprint_activity("match", match["label"])
                        log.info("🔁 Bekannter Jingle/Werbespot wiedererkannt: Clip #%d '%s' "
                                 "(schon %dx gehört, Match-Stärke %d)",
                                 match["clip_id"], match["label"], match["times_seen"],
                                 match["match_strength"])
                        speech_streak = 0
                        speech_buffer = []
                        fp_checked_this_run = False
                        do_switch("Bekannte Werbung/Jingle erkannt")
                        continue
                    else:
                        save_fingerprint_debug_clip(
                            combined, SAMPLE_RATE, f"newclip_{current['id']}_{ts}.wav",
                        )
                        state.set_fingerprint_activity("learned")
            else:
                speech_streak = 0
                speech_buffer = []
                fp_checked_this_run = False

            if speech_streak >= CONSECUTIVE_SPEECH_TO_SWITCH:
                speech_streak = 0
                speech_buffer = []
                fp_checked_this_run = False
                do_switch("Moderation erkannt")

    except KeyboardInterrupt:
        log.info("Beende.")
    except Exception:
        # Ohne das stirbt der Prozess mit einem Traceback auf stderr, der in
        # der Logdatei fehlt — genau dann, wenn man ihn braucht.
        log.exception("💥 Unerwarteter Fehler im Hauptloop — beende.")
        raise
    finally:
        source.stop()
        for pb in prebuffer.values():
            pb.stop()
        output.close()
        if fp_db:
            fp_db.close()
        stt.close()
        if httpd:
            httpd.shutdown()


if __name__ == "__main__":
    main()
    
