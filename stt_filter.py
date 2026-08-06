"""
stt_filter.py — Zusätzliches Sprache-Signal per Speech-to-Text (STT),
unabhängig von Silero VAD/der Signal-Heuristik (speech_detector.py) und
von fingerprint.py.

Wo VAD/Heuristik nur beurteilen "ist hier eine menschliche Stimme" (auch
Gesang zählt da mit), prüft dieses Modul den INHALT: kommt beim aktuellen
Sender gerade zusammenhängender deutscher Text raus? Genau das
unterscheidet echte Moderation von deutsch gesungener Musik, die VAD
regelmäßig als Sprache fehlklassifiziert (ähnliche Vokal-/Formant-
Struktur). Die einzige Stelle, an der dieses Modul mit der bestehenden
Switch-Logik in radiozapper.py in Berührung kommt, ist combine_label()
ganz unten — Streak-Zählung und Fingerprint-Trigger dort bleiben
unverändert, dieses Modul kennt weder StreamSource noch SwitcherState
(exakt dieselbe Trennung wie news_break.py, siehe CLAUDE.md).

Zwei austauschbare Engines, NIE gleichzeitig geladen (spart Speicher auf
schwacher Hardware, z.B. Raspberry Pi):
  - Vosk: kleines, schnelles deutsches Kaldi-Modell, pi-tauglich.
  - faster-whisper: genauer, aber deutlich schwerer (auch als "tiny"-
    Modell noch spürbar CPU-hungriger als Vosk).

Beide erwarten 16kHz Mono -- unser Analysepfad läuft mit SAMPLE_RATE
(44100Hz, siehe radiozapper.py), daher dieselbe simple lineare
Interpolation wie in speech_detector.py (für STT-Zwecke ausreichend
genau, keine Hifi-Anwendung; hier separat gehalten statt importiert,
damit dieses Modul keine Abhängigkeit auf speech_detector.py bekommt).
"""

import json
import logging
import threading
import time

import numpy as np

log = logging.getLogger("stt")

TARGET_SR = 16000

# Länge eines Analyse-Clips (Sekunden) -- radiozapper.py sammelt genau so
# viel Mono-PCM in einem Ringpuffer, bevor sample_async() aufgerufen wird.
CLIP_SECONDS = 3.0

# faster-whisper lädt Modelle beim ersten Gebrauch selbst von HuggingFace
# nach -- fester Container-Pfad, damit der Download nicht bei jedem
# Neustart wiederholt wird (siehe docker-compose.yml: dorthin gemountetes
# beschreibbares Volume).
WHISPER_DOWNLOAD_ROOT = "/app/whisper_cache"

# Ein STT-Befund gilt nur für das FRESHNESS_FACTOR-fache von
# sample_interval_seconds als "frisch" -- Toleranz dafür, dass ein
# einzelner Whisper-Durchlauf durchaus länger als das Sample-Intervall
# dauern kann. Älter als das -> combine_label() behandelt es als "kein
# Befund vorhanden" (neutral), siehe dortiger Docstring.
FRESHNESS_FACTOR = 2.0

try:
    from vosk import Model as _VoskModel, KaldiRecognizer
    _VOSK_AVAILABLE = True
    _VOSK_IMPORT_ERROR = None
except Exception as e:  # bewusst breit, wie speech_detector.py: fehlendes Paket
                          # oder eine kaputte Installation sollen zum Fallback
                          # führen (Feature deaktiviert sich selbst), nicht zum Crash
    _VOSK_AVAILABLE = False
    _VOSK_IMPORT_ERROR = e

try:
    from faster_whisper import WhisperModel
    _WHISPER_AVAILABLE = True
    _WHISPER_IMPORT_ERROR = None
except Exception as e:
    _WHISPER_AVAILABLE = False
    _WHISPER_IMPORT_ERROR = e


def _resample(pcm_int16: np.ndarray, source_sr: int, target_sr: int = TARGET_SR) -> np.ndarray:
    """Lineare Interpolation auf target_sr, Rückgabe als float32 in [-1, 1]."""
    samples = pcm_int16.astype(np.float32) / 32768.0
    if source_sr == target_sr or samples.size == 0:
        return samples
    duration = len(samples) / source_sr
    n_target = max(1, int(round(duration * target_sr)))
    x_old = np.linspace(0, duration, num=len(samples), endpoint=False)
    x_new = np.linspace(0, duration, num=n_target, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


class _VoskEngine:
    """Dünner Wrapper um vosk.KaldiRecognizer. Ein frischer Recognizer pro
    transcribe()-Aufruf statt eines langlebigen: unsere Clips sind
    unabhängige Stichproben, kein fortlaufender Stream -- ein neuer
    Recognizer ist einfacher als AcceptWaveform()-Zustand zwischen
    unzusammenhängenden Clips manuell zurückzusetzen."""

    def __init__(self, model_path: str):
        if not _VOSK_AVAILABLE:
            raise RuntimeError(f"vosk nicht installiert ({_VOSK_IMPORT_ERROR})")
        self.model = _VoskModel(model_path)  # wirft bei ungültigem/fehlendem Pfad

    def transcribe(self, pcm_int16: np.ndarray, source_sr: int):
        samples = _resample(pcm_int16, source_sr)
        pcm16 = (samples * 32767.0).astype(np.int16)
        rec = KaldiRecognizer(self.model, TARGET_SR)
        rec.SetWords(True)
        rec.AcceptWaveform(pcm16.tobytes())
        result = json.loads(rec.FinalResult())
        text = result.get("text", "").strip()
        words = result.get("result", [])
        if words:
            # Manche Vosk-Modelle liefern echte Wort-Konfidenzen -- wenn
            # vorhanden, ist das Mittel die beste verfügbare Schätzung.
            confidence = float(np.mean([w.get("conf", 1.0) for w in words]))
        elif text:
            # Kein "result"-Array (manche kleineren Modelle liefern das
            # nicht), aber Text erkannt -- Proxy statt "keine Ahnung":
            # mehr erkannte Wörter am Stück = wahrscheinlicher echter
            # Treffer statt Rauschen/Fehlerkennung. Bewusst grob, keine
            # kalibrierte Wahrscheinlichkeit (siehe Modul-Docstring).
            confidence = min(1.0, len(text.split()) / 3.0)
        else:
            confidence = 0.0
        return text, confidence


class _WhisperEngine:
    """Dünner Wrapper um faster_whisper.WhisperModel."""

    def __init__(self, model_size: str, download_root: str = WHISPER_DOWNLOAD_ROOT):
        if not _WHISPER_AVAILABLE:
            raise RuntimeError(f"faster-whisper nicht installiert ({_WHISPER_IMPORT_ERROR})")
        # int8 statt float16: läuft im Container ohne GPU auf der CPU,
        # spart spürbar Rechenzeit bei überschaubarem Genauigkeitsverlust.
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8",
                                   download_root=download_root)

    def transcribe(self, pcm_int16: np.ndarray, source_sr: int):
        samples = _resample(pcm_int16, source_sr)
        # language="de" erzwingen statt Autodetect: spart die
        # Detection-Zeit UND vermeidet Fehlklassifikation der Sprache
        # bei kurzen/verrauschten Clips (genau unser Anwendungsfall).
        segments, _info = self.model.transcribe(samples, language="de", vad_filter=False)
        segments = list(segments)
        if not segments:
            return "", 0.0
        text = " ".join(s.text.strip() for s in segments).strip()
        # no_speech_prob ist KEINE kalibrierte Sprache-Wahrscheinlichkeit,
        # aber der einzige von faster-whisper gelieferte Anhaltspunkt in
        # diese Richtung -- Proxy wie beim Vosk-Fallback oben.
        avg_no_speech = float(np.mean([s.no_speech_prob for s in segments]))
        confidence = max(0.0, 1.0 - avg_no_speech)
        return text, confidence


class SttFilter:
    """Hält die aktuell aktive STT-Engine (genau eine, siehe Modul-
    Docstring) und den zuletzt erhaltenen Sample-Befund. sample_async()
    läuft im Hintergrund-Thread -- Whisper kann mehrere Sekunden pro Clip
    brauchen, das darf den Hauptloop (Analysefenster ~1x/Sekunde) niemals
    blockieren."""

    def __init__(self, cfg: dict):
        self._lock = threading.Lock()
        self.engine = None
        self.available = False
        self.last_error = None
        self._engine_name = None
        self._busy = False
        self._verdict = None  # (confidence, text, timestamp) oder None
        self.reload(cfg)

    def reload(self, cfg: dict):
        """Lädt die durch cfg["engine"] gewählte Engine neu (oder entlädt
        sie, falls cfg["enabled"] False ist). Ein zum Zeitpunkt des
        Aufrufs laufender sample_async()-Thread hält seine eigene lokale
        Kopie der ALTEN Engine-Referenz (siehe sample_async()) und läuft
        damit unbeeinflusst zu Ende -- kein Warten auf "busy" nötig, bevor
        self.engine hier ausgetauscht wird."""
        if not cfg.get("enabled", False):
            with self._lock:
                self.engine = None
                self.available = False
                self.last_error = None
                self._engine_name = None
                self._verdict = None
            log.info("🗣 STT-Filter deaktiviert.")
            return

        engine_name = cfg.get("engine", "vosk")
        try:
            if engine_name == "vosk":
                engine = _VoskEngine(cfg.get("vosk_model_path", ""))
            elif engine_name == "whisper":
                engine = _WhisperEngine(cfg.get("whisper_model_size", "tiny"))
            else:
                raise ValueError(f"unbekannte STT-Engine: {engine_name!r}")
        except Exception as e:
            # Modell nicht gefunden, Paket fehlt, Ladefehler o.ä. --
            # Feature deaktiviert sich selbst, RadioZapper läuft normal
            # ohne STT-Filter weiter (siehe combine_label(): kein Befund
            # -> No-Op).
            with self._lock:
                self.engine = None
                self.available = False
                self.last_error = str(e)
                self._engine_name = engine_name
                self._verdict = None
            log.error("⚠ STT-Filter: Engine '%s' konnte nicht geladen werden (%s) — "
                      "Feature bleibt deaktiviert.", engine_name, e)
            return

        with self._lock:
            self.engine = engine
            self.available = True
            self.last_error = None
            self._engine_name = engine_name
            self._verdict = None
        log.info("🗣 STT-Filter: Engine '%s' geladen.", engine_name)

    def status(self):
        """(engine_name, available, last_error) für die Web-UI (siehe
        webui.py SwitcherState.set_stt_status())."""
        with self._lock:
            return self._engine_name, self.available, self.last_error

    def sample_async(self, pcm_int16: np.ndarray, sample_rate: int):
        """Startet einen Hintergrund-Sample, falls die Engine verfügbar
        ist und gerade kein anderer Sample läuft -- kein Thread-Stapeln,
        falls die Engine (v.a. Whisper) langsamer ist als
        sample_interval_seconds."""
        with self._lock:
            if not self.available or self._busy:
                return
            self._busy = True
            engine = self.engine  # lokale Kopie, siehe reload()-Docstring

        def _run():
            try:
                text, confidence = engine.transcribe(pcm_int16, sample_rate)
                log.debug("[stt] text=%r confidence=%.2f", text, confidence)
                with self._lock:
                    self._verdict = (confidence, text, time.monotonic())
            except Exception as e:
                # Ein Absturz der Engine bei einem einzelnen Clip darf nie
                # den Hauptprozess mitreißen -- dieser Sample wird
                # einfach verworfen, der nächste Tick versucht es erneut.
                log.warning("⚠ STT-Sample übersprungen (Engine-Fehler: %s)", e)
            finally:
                with self._lock:
                    self._busy = False

        threading.Thread(target=_run, daemon=True, name="stt-sample").start()

    def last_verdict(self):
        """(confidence, text, timestamp) des letzten Samples, oder None,
        falls noch keiner gelaufen ist."""
        with self._lock:
            return self._verdict

    def close(self):
        with self._lock:
            self.engine = None
            self.available = False


def _fresh_confidence(verdict, cfg):
    """Extrahiert die Konfidenz aus verdict, falls vorhanden UND noch
    frisch genug (siehe FRESHNESS_FACTOR) -- sonst None. Gemeinsame Basis
    für combine_label() (Switch-Logik) und live_confidence() (STT-Anzeige
    im Web-Interface, siehe SESSION.md 2026-08-06) -- beide sollen exakt
    denselben "kein Befund"-Begriff verwenden, nicht zwei leicht
    unterschiedliche Altersschwellen."""
    if verdict is None:
        return None
    confidence, _text, ts = verdict
    max_age = cfg.get("sample_interval_seconds", 8.0) * FRESHNESS_FACTOR
    if time.monotonic() - ts > max_age:
        return None  # Befund zu alt -> wie "kein Befund"
    return confidence


def combine_label(label: str, verdict, cfg: dict) -> str:
    """Kombiniert das VAD/Heuristik-Ergebnis (`label`, "speech"/"music")
    mit dem letzten STT-Befund (siehe SttFilter.last_verdict()). Reine
    Funktion ohne Zugriff auf StreamSource/SwitcherState -- einzige
    Kopplungsstelle zwischen stt_filter.py und der bestehenden Switch-
    Logik in radiozapper.py.

    Kein (frischer) STT-Befund -> `label` unverändert durchgereicht. Das
    ist der Mechanismus, über den sich das Feature bei deaktiviertem
    Filter, Ladefehler oder noch fehlendem ersten Sample von selbst
    neutral verhält, statt RadioZapper zu beeinflussen, bevor überhaupt
    ein Ergebnis vorliegt.

    combine_mode:
      - "and" (Default): beide Signale müssen "speech" sagen. Das ist der
        Mechanismus gegen deutsch gesungene Musik, die VAD/Heuristik oft
        fälschlich als Sprache einordnen -- STT erkennt beim Gesang meist
        keinen zusammenhängenden Text (niedrige Konfidenz), das Fenster
        gilt dann trotz VAD-Treffer als Musik.
      - "or": eines der beiden Signale reicht -- fängt mehr echte
        Moderation (auch was VAD verpasst), aber wieder anfälliger für
        den Gesangs-Fall, den "and" gerade lösen soll."""
    if not cfg.get("enabled", False):
        return label

    confidence = _fresh_confidence(verdict, cfg)
    if confidence is None:
        return label

    stt_label = "speech" if confidence >= cfg.get("confidence_threshold", 0.6) else "music"
    mode = cfg.get("combine_mode", "and")
    if mode == "or":
        return "speech" if (label == "speech" or stt_label == "speech") else "music"
    return "speech" if (label == "speech" and stt_label == "speech") else "music"


def live_confidence(verdict, cfg: dict):
    """Rohe STT-Konfidenz (0..1) für die Live-Anzeige im Web-Interface,
    oder None, falls der Filter deaktiviert ist oder kein frischer Befund
    vorliegt -- das Web-Interface friert die Anzeige dann grau ein (siehe
    webui.py). Separate Funktion statt kombiniert mit combine_label():
    letztere braucht denselben "kein Befund"-Fall für die Switch-Logik,
    beide teilen sich deshalb _fresh_confidence() statt die Altersprüfung
    zweimal leicht unterschiedlich zu implementieren."""
    if not cfg.get("enabled", False):
        return None
    return _fresh_confidence(verdict, cfg)
