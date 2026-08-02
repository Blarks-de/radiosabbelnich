#!/usr/bin/env python3
"""
radiozapper.py — Internetradio abspielen, bei Moderation (Sprache) auf
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
    python3 radiozapper.py
    (Sender-Liste in stations.json pflegen, oder über die Config-Seite
    des Web-Interfaces unter /config — siehe stations_store.py)
"""

import collections
import glob
import os
import select
import subprocess
import sys
import threading
import time
import wave
import numpy as np

import fingerprint
import webui
from speech_detector import SpeechDetector

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
CONSECUTIVE_SPEECH_TO_SWITCH = 5   # so viele Sprache-Fenster in Folge -> umschalten (VAD ist zuverlässiger als die Heuristik, daher kürzer als vorher)
COOLDOWN_AFTER_SWITCH = 8.0  # Sekunden Ruhe nach einem Switch, bevor wieder geschaltet wird
STREAM_READ_TIMEOUT = 8.0    # max. Wartezeit pro Analysefenster, bevor eine Quelle als tot gilt
                              # (verhindert, dass ein hängender Sender den Loop für immer blockiert)

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

VERBOSE = False  # wird ggf. per --verbose Kommandozeilenparameter überschrieben

# Fingerprinting: nach so vielen Sekunden Sprache am Stück wird der Clip
# gefingerprintet und gegen die DB bekannter Jingles/Ads geprüft. Muss
# kleiner als CONSECUTIVE_SPEECH_TO_SWITCH sein, sonst hat's keinen Vorteil.
FINGERPRINT_TRIGGER_SECONDS = 3
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
    except OSError as e:
        print(f"⚠ Fingerprint-Debug-Clip konnte nicht geschrieben werden: {e}", file=sys.stderr)


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

    def start(self, url: str):
        self.stop()
        stereo_read_fd, stereo_write_fd = os.pipe()
        os.set_inheritable(stereo_write_fd, True)
        self.proc = subprocess.Popen(
            [
                "ffmpeg", "-loglevel", "error",
                "-i", url,
                "-map", "0:a", "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", str(self.sample_rate), "-ac", "1",
                "pipe:1",
                "-map", "0:a", "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", str(self.sample_rate), "-ac", "2",
                f"pipe:{stereo_write_fd}",
            ],
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

    def __init__(self, sample_rate: int, url: str, buffer_seconds: float = PREBUFFER_SECONDS):
        self.url = url
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
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop_event.is_set():
            mono, stereo = self.source.read_window(WINDOW_SECONDS)
            if mono.size == 0 and stereo.size == 0:
                self.dead = True
                return
            with self._lock:
                self._mono_windows.append(mono)
                self._stereo_windows.append(stereo)

    def _join(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=STREAM_READ_TIMEOUT + 1)
            self._thread = None

    def promote(self):
        """Stoppt den Hintergrund-Thread und gibt (mono, stereo, source)
        zurück: die gepufferten Sekunden als fertige Arrays plus die
        weiterlaufende StreamSource zur Übernahme durch den Aufrufer."""
        self._join()
        with self._lock:
            mono = np.concatenate(self._mono_windows) if self._mono_windows else np.array([], dtype=np.int16)
            stereo = np.concatenate(self._stereo_windows) if self._stereo_windows else np.array([], dtype=np.int16)
        return mono, stereo, self.source

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
            print("⚠ Icecast-Verbindung unterbrochen, versuche neu zu verbinden ...", file=sys.stderr)
            try:
                self.proc.kill()
                self.proc.wait(timeout=3)
            except Exception:
                pass
            try:
                self._start_proc()
            except Exception as e:
                print(f"⚠ Icecast-Reconnect fehlgeschlagen: {e}", file=sys.stderr)

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            self.proc.wait(timeout=5)


def prebuffer_target_ids(current_id: str, active: list) -> list:
    """Liefert die IDs der nächsten PREBUFFER_COUNT Sender in
    Rotationsreihenfolge ab (aber ohne) dem aktuellen — dieselbe
    Reihenfolge, in der do_switch() automatisch durchprobieren würde."""
    ids = [s["id"] for s in active]
    if current_id not in ids or len(active) <= 1:
        return []
    pos = ids.index(current_id)
    n = len(active)
    count = min(PREBUFFER_COUNT, n - 1)
    return [ids[(pos + 1 + i) % n] for i in range(count)]


def sync_prebuffer(prebuffer: dict, current_id: str, active: list, sample_rate: int):
    """Startet/stoppt Hintergrund-Puffer, damit `prebuffer` genau die
    nächsten PREBUFFER_COUNT Sender (in Rotationsreihenfolge ab dem
    aktuellen) enthält — nicht mehr, nicht weniger. Ersetzt außerdem
    Puffer, deren Quelle unterwegs gestorben ist (Netzwerk-Hänger etc.),
    durch einen frischen Versuch."""
    wanted_ids = prebuffer_target_ids(current_id, active)
    wanted_set = set(wanted_ids)

    for sid in list(prebuffer.keys()):
        if sid not in wanted_set:
            prebuffer.pop(sid).stop()
        elif prebuffer[sid].dead:
            prebuffer.pop(sid).stop()

    stations_by_id = {s["id"]: s for s in active}
    for sid in wanted_ids:
        if sid not in prebuffer and sid in stations_by_id:
            pb = PrebufferedSource(sample_rate, stations_by_id[sid]["url"])
            pb.start()
            prebuffer[sid] = pb


# ----------------------------------------------------------------------
# HAUPTLOGIK
# ----------------------------------------------------------------------

def main():
    global VERBOSE

    import argparse
    parser = argparse.ArgumentParser(description="RadioZapper: schaltet bei Moderation um.")
    parser.add_argument("--verbose", action="store_true",
                         help="Feature-Werte (zcr/flat/evar/bass) und Fingerprint-Infos ausgeben")
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
                         help="Icecast-Mountpoint für die Hörer-Abfrage, z.B. /radiozapper.mp3")
    parser.add_argument("--icecast-public-port", default=None,
                         help="Port, unter dem der Browser den Icecast-Stream selbst erreicht "
                              "(für den eingebetteten Player im Web-Interface), z.B. 8000")
    args = parser.parse_args()
    VERBOSE = args.verbose

    if args.icecast_url:
        output = IcecastOutput(SAMPLE_RATE, args.icecast_url, args.bitrate)
        print(f"📡 Restream läuft auf: {args.icecast_url.split('@')[-1]}")
    else:
        output = LocalOutput(SAMPLE_RATE)

    fp_db = None
    if not args.no_fingerprint:
        fp_db = fingerprint.FingerprintDB(args.fingerprint_db)
        print(f"🔎 Fingerprint-DB: {args.fingerprint_db}")

    detector = SpeechDetector(SAMPLE_RATE)
    if detector.available:
        print("🗣  Sprache-Erkennung: Silero VAD")
    else:
        print("🗣  Sprache-Erkennung: Signal-Heuristik (Fallback)")

    def classify(pcm: np.ndarray) -> str:
        label, _ = detector.classify(pcm, verbose=VERBOSE)
        if label is None:
            return classify_window(pcm, SAMPLE_RATE)  # Heuristik-Fallback
        return label

    state = webui.SwitcherState()
    active = state.active_stations
    if not active:
        print("Keine aktivierten Sender in stations.json — bitte über die "
              "Config-Seite (/config) mindestens einen aktivieren.", file=sys.stderr)
        sys.exit(1)

    httpd = None
    if args.webui_port:
        icecast_cfg = {
            "admin_url": args.icecast_admin_url,
            "user": args.icecast_admin_user,
            "password": args.icecast_admin_password,
            "mount": args.icecast_mount,
            "public_port": args.icecast_public_port,
        }
        httpd = webui.start_server(args.webui_port, state, icecast_cfg, args.fingerprint_db)
        print(f"🌐 Web-Interface läuft auf Port {args.webui_port}")

    source = StreamSource(SAMPLE_RATE)
    current = active[0]  # aktuell gespielter Sender (dict: id/name/url/category/enabled)
    source.start(current["url"])
    state.set_current(current["id"])
    print(f"▶ Spiele: {current['name']}")

    # Nächste PREBUFFER_COUNT Sender in Rotationsreihenfolge laufen im
    # Hintergrund bereits mit (siehe PrebufferedSource/sync_prebuffer weiter
    # oben) -> Wechsel dorthin fühlen sich praktisch nahtlos an, statt neu
    # verbinden zu müssen.
    prebuffer = {}
    sync_prebuffer(prebuffer, current["id"], active, SAMPLE_RATE)
    print(f"⏱  Puffere die nächsten {len(prebuffer)} Sender {PREBUFFER_SECONDS:.0f}s im Voraus.")

    def quick_forward(seconds: float = 0.3):
        """Nach einem direkten Sender-Wechsel (manuell oder erzwungen durch
        einen Config-Reload) sofort einen kurzen Schnipsel lesen und an den
        Output weiterreichen, statt bis zum nächsten vollen
        WINDOW_SECONDS-Analysefenster zu warten. Ohne das vergehen nach
        einem Wechsel spürbar mehrere Sekunden, bis überhaupt neue Audio
        bei Icecast/Hörern ankommt — nicht weil die Verbindung zur neuen
        Quelle lange dauert (die steht meist in <1s), sondern weil
        read_window() sonst erst ein volles 1-Sekunden-Fenster sammelt,
        bevor output.write() überhaupt aufgerufen wird."""
        _, stereo = source.read_window(seconds)
        if stereo.size:
            output.write(stereo)

    def switch_to_station(station: dict) -> str:
        """Wechselt auf `station` — nutzt einen laufenden Hintergrund-
        Puffer falls vorhanden (sofortiger Übergang inkl. Burst der
        letzten PREBUFFER_SECONDS Sekunden gepufferten Audios), sonst
        frischer Connect + quick_forward(). Aktualisiert `source`, gibt
        aber KEIN state.set_current()/print() aus — das bleibt Sache der
        Aufrufer, die je nach Situation unterschiedliche Meldungen
        ausgeben. Gibt zurück, ob der Puffer genutzt werden konnte."""
        nonlocal source
        pb = prebuffer.pop(station["id"], None)
        if pb is not None:
            mono, stereo, adopted_source = pb.promote()
            source.stop()
            source = adopted_source
            if stereo.size:
                output.write(stereo)
            return "prebuffered" if mono.size else "prebuffered-empty"
        source.start(station["url"])
        quick_forward()
        return "fresh"

    speech_streak = 0
    last_switch_time = 0.0
    speech_buffer = []       # sammelt PCM-Chunks des aktuellen Sprache-Laufs
    fp_checked_this_run = False

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
        1.5s-Warten + frisches Fenster nötig) — bei "music" sofort mit
        vollem Puffer-Burst übernommen, bei "speech" verworfen und der
        nächste Kandidat probiert."""
        nonlocal current, last_switch_time, source
        print(f"🎙  {reason} auf '{current['name']}' — schalte um ...")
        active = state.active_stations
        if not active:
            print("   ... keine aktivierten Sender konfiguriert, bleibe hier.")
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
                print("   ... manueller Switch angefordert, breche Auto-Suche ab.")
                return
            pos = (pos + 1) % len(active)
            candidate = active[pos]
            skips += 1

            pb = prebuffer.pop(candidate["id"], None)
            if pb is not None:
                buf_mono, buf_stereo, candidate_source = pb.promote()
                tail = buf_mono[-int(SAMPLE_RATE * WINDOW_SECONDS):] if buf_mono.size else buf_mono
                verdict = classify(tail) if tail.size else "music"
                if verdict == "music":
                    current = candidate
                    source.stop()
                    source = candidate_source
                    state.set_current(current["id"])
                    if buf_stereo.size:
                        output.write(buf_stereo)
                    print(f"▶ Spiele: {current['name']} (aus Puffer, nahtlos)")
                    last_switch_time = time.time()
                    break
                candidate_source.stop()
                print(f"   ... auch Sprache (gepuffert), probiere nächsten Sender.")
                continue

            current = candidate
            source.start(current["url"])
            state.set_current(current["id"])
            print(f"▶ Spiele: {current['name']}")
            last_switch_time = time.time()
            time.sleep(1.5)
            probe_mono, probe_stereo = source.read_window(WINDOW_SECONDS)
            if probe_mono.size:
                if probe_stereo.size:
                    output.write(probe_stereo)
                if classify(probe_mono) == "music":
                    break
            print(f"   ... auch Sprache, probiere nächsten Sender.")

    try:
        while True:
            # Puffer für die nächsten PREBUFFER_COUNT Sender ab der aktuellen
            # Position aktuell halten -- billig genug, um einmal pro
            # Schleifendurchlauf zu prüfen (kein Rebuild, falls schon passend).
            sync_prebuffer(prebuffer, current["id"], state.active_stations, SAMPLE_RATE)

            if state.pop_reload_request():
                # Sender wurden über die Config-Seite geändert (hinzugefügt/
                # gelöscht/(de)aktiviert/editiert) -> Rotationsliste neu laden
                state.reload()
                active = state.active_stations
                active_ids = {s["id"] for s in active}
                if not active:
                    print("⚠ Keine aktivierten Sender mehr konfiguriert — "
                          "Wiedergabe pausiert bis wieder einer aktiv ist.", file=sys.stderr)
                elif current["id"] not in active_ids:
                    current = active[0]
                    used_buffer = switch_to_station(current)
                    state.set_current(current["id"])
                    print(f"⚙ Senderliste geändert, aktueller Sender nicht mehr aktiv "
                          f"— schalte auf: {current['name']}"
                          f"{' (aus Puffer)' if used_buffer.startswith('prebuffered') else ''}")
                    last_switch_time = time.time()
                    speech_streak = 0
                    speech_buffer = []
                    fp_checked_this_run = False
                else:
                    print("⚙ Senderliste neu geladen.")
                continue

            manual_id = state.pop_manual_request()
            if manual_id is not None and manual_id != current["id"]:
                station = next((s for s in state.active_stations if s["id"] == manual_id), None)
                if station is None:
                    print(f"⚠ Manueller Switch auf unbekannten/inaktiven Sender ignoriert: "
                          f"{manual_id}", file=sys.stderr)
                    continue
                current = station
                used_buffer = switch_to_station(current)
                state.set_current(current["id"])
                print(f"🎛  Manuell umgeschaltet auf: {current['name']}"
                      f"{' (aus Puffer)' if used_buffer.startswith('prebuffered') else ''}")
                last_switch_time = time.time()
                speech_streak = 0
                speech_buffer = []
                fp_checked_this_run = False
                continue

            if state.pop_skip_request():
                # "Gesabbel!"-Knopf: Nutzer hat selbst Sprache erkannt,
                # auch wenn VAD/Heuristik (noch) nicht angeschlagen haben
                speech_streak = 0
                speech_buffer = []
                fp_checked_this_run = False
                do_switch("Nutzer meldete Gesabbel")
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
                print(f"🔇 Sabbelfilter {'wieder aktiviert' if new_enabled else 'deaktiviert'} "
                      f"(automatisches Umschalten {'läuft weiter' if new_enabled else 'pausiert'}).")
                continue

            pcm, pcm_stereo = source.read_window(WINDOW_SECONDS)
            if pcm.size == 0:
                print(f"⚠ Stream '{current['name']}' liefert nichts mehr, "
                      f"versuche neu zu verbinden ...", file=sys.stderr)
                source.start(current["url"])
                time.sleep(1)
                continue

            if pcm_stereo.size:
                output.write(pcm_stereo)

            if not state.filter_enabled:
                # Sabbelfilter aus: einfach weiterspielen, keine
                # automatische Erkennung/Umschaltung
                continue

            label = classify(pcm)
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
                    match = fp_db.match_or_learn(
                        combined, SAMPLE_RATE, current["name"], verbose=VERBOSE
                    )
                    ts = time.strftime("%Y%m%d-%H%M%S")
                    if match:
                        save_fingerprint_debug_clip(
                            combined, SAMPLE_RATE,
                            f"match_clip{match['clip_id']}_{current['id']}_{ts}.wav",
                        )
                        state.set_last_fingerprint_clip(match["clip_id"], match["label"])
                        print(f"🔁 Bekannter Jingle/Werbespot wiedererkannt "
                              f"(schon {match['times_seen']}x gehört)")
                        speech_streak = 0
                        speech_buffer = []
                        fp_checked_this_run = False
                        do_switch("Bekannte Werbung/Jingle erkannt")
                        continue
                    else:
                        save_fingerprint_debug_clip(
                            combined, SAMPLE_RATE, f"newclip_{current['id']}_{ts}.wav",
                        )
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
        print("\nBeende.")
    finally:
        source.stop()
        for pb in prebuffer.values():
            pb.stop()
        output.close()
        if fp_db:
            fp_db.close()
        if httpd:
            httpd.shutdown()


if __name__ == "__main__":
    main()
    
