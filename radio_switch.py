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
import select
import subprocess
import sys
import time
import numpy as np

import fingerprint
import webui
from speech_detector import SpeechDetector

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

SAMPLE_RATE = 44100          # Hz, für Analyse & Wiedergabe
WINDOW_SECONDS = 1.0         # Länge eines Analysefensters
CONSECUTIVE_SPEECH_TO_SWITCH = 5   # so viele Sprache-Fenster in Folge -> umschalten (VAD ist zuverlässiger als die Heuristik, daher kürzer als vorher)
COOLDOWN_AFTER_SWITCH = 8.0  # Sekunden Ruhe nach einem Switch, bevor wieder geschaltet wird
MAX_SKIPS_PER_ROUND = len(STREAMS)  # nicht endlos im Kreis rennen, falls überall Sprache läuft
STREAM_READ_TIMEOUT = 8.0    # max. Wartezeit pro Analysefenster, bevor eine Quelle als tot gilt
                              # (verhindert, dass ein hängender Sender den Loop für immer blockiert)

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


# ----------------------------------------------------------------------
# HAUPTLOGIK
# ----------------------------------------------------------------------

def main():
    global VERBOSE

    import argparse
    parser = argparse.ArgumentParser(description="Radio-Switcher: schaltet bei Moderation um.")
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

    if not STREAMS:
        print("Bitte STREAMS-Liste im Script befüllen.", file=sys.stderr)
        sys.exit(1)

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

    state = webui.SwitcherState(STREAMS)
    httpd = None
    if args.webui_port:
        icecast_cfg = {
            "admin_url": args.icecast_admin_url,
            "user": args.icecast_admin_user,
            "password": args.icecast_admin_password,
            "mount": args.icecast_mount,
            "public_port": args.icecast_public_port,
        }
        httpd = webui.start_server(args.webui_port, state, icecast_cfg)
        print(f"🌐 Web-Interface läuft auf Port {args.webui_port}")

    current = 0
    source = StreamSource(SAMPLE_RATE)
    source.start(STREAMS[current]["url"])
    state.set_current(current)
    print(f"▶ Spiele: {STREAMS[current]['name']}")

    speech_streak = 0
    last_switch_time = 0.0
    speech_buffer = []       # sammelt PCM-Chunks des aktuellen Sprache-Laufs
    fp_checked_this_run = False

    def do_switch(reason: str):
        """Springt reihum zum nächsten Sender, bis Musik läuft (oder alle
        durch sind). Wird sowohl von der Heuristik als auch bei einem
        Fingerprint-Treffer aufgerufen.

        Bricht sofort ab, sobald ein manueller Switch-Request reinkommt —
        sonst könnte ein Nutzerklick im Web-Interface bis zu
        MAX_SKIPS_PER_ROUND * (1.5s + STREAM_READ_TIMEOUT) warten müssen,
        falls das automatische Durchprobieren gerade läuft."""
        nonlocal current, last_switch_time
        print(f"🎙  {reason} auf '{STREAMS[current]['name']}' — schalte um ...")
        skips = 0
        while skips < MAX_SKIPS_PER_ROUND:
            pending = state.pop_manual_request()
            if pending is not None:
                # nicht einfach verwerfen: Request zurücklegen, der
                # Hauptloop erledigt den eigentlichen Wechsel (inkl.
                # current/state/Streak-Reset) beim nächsten Durchlauf
                state.request_switch(pending)
                print("   ... manueller Switch angefordert, breche Auto-Suche ab.")
                break
            current = (current + 1) % len(STREAMS)
            source.start(STREAMS[current]["url"])
            state.set_current(current)
            print(f"▶ Spiele: {STREAMS[current]['name']}")
            last_switch_time = time.time()
            skips += 1
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
            manual_idx = state.pop_manual_request()
            if manual_idx is not None and manual_idx != current:
                current = manual_idx
                source.start(STREAMS[current]["url"])
                state.set_current(current)
                print(f"🎛  Manuell umgeschaltet auf: {STREAMS[current]['name']}")
                last_switch_time = time.time()
                speech_streak = 0
                speech_buffer = []
                fp_checked_this_run = False
                continue

            pcm, pcm_stereo = source.read_window(WINDOW_SECONDS)
            if pcm.size == 0:
                print(f"⚠ Stream '{STREAMS[current]['name']}' liefert nichts mehr, "
                      f"versuche neu zu verbinden ...", file=sys.stderr)
                source.start(STREAMS[current]["url"])
                time.sleep(1)
                continue

            if pcm_stereo.size:
                output.write(pcm_stereo)
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
                        combined, SAMPLE_RATE, STREAMS[current]["name"], verbose=VERBOSE
                    )
                    if match:
                        print(f"🔁 Bekannter Jingle/Werbespot wiedererkannt "
                              f"(schon {match['times_seen']}x gehört)")
                        speech_streak = 0
                        speech_buffer = []
                        fp_checked_this_run = False
                        do_switch("Bekannte Werbung/Jingle erkannt")
                        continue
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
        output.close()
        if fp_db:
            fp_db.close()
        if httpd:
            httpd.shutdown()


if __name__ == "__main__":
    main()
    
