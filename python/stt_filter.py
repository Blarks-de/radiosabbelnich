"""
stt_filter.py — Zusätzliches Sprache-Signal per Speech-to-Text (STT),
unabhängig von Silero VAD/der Signal-Heuristik (speech_detector.py) und
von fingerprint.py.

Wo VAD/Heuristik nur beurteilen "ist hier eine menschliche Stimme" (auch
Gesang zählt da mit), prüft dieses Modul den INHALT: kommt beim aktuellen
Sender gerade zusammenhängender Text in der jeweils erwarteten Sprache
raus? Genau das unterscheidet echte Moderation von in dieser Sprache
gesungener Musik, die VAD regelmäßig als Sprache fehlklassifiziert
(ähnliche Vokal-/Formant-Struktur). Die einzige Stelle, an der dieses
Modul mit der bestehenden Switch-Logik in radiosabbelnich.py in Berührung
kommt, ist combine_label() weiter unten — Streak-Zählung und
Fingerprint-Trigger dort bleiben unverändert, dieses Modul kennt weder
StreamSource noch SwitcherState (exakt dieselbe Trennung wie
news_break.py, siehe CLAUDE.md).

Zwei austauschbare Engines, NIE gleichzeitig geladen (spart Speicher auf
schwacher Hardware, z.B. Raspberry Pi):
  - Vosk: kleines, schnelles Kaldi-Modell -- aber EIN Modell pro Sprache,
    Mehrsprachigkeit heißt hier also mehrere geladene Modelle.
  - faster-whisper: genauer, aber deutlich schwerer (auch als "tiny"-
    Modell noch spürbar CPU-hungriger als Vosk) -- dafür von Haus aus
    multilingual: EIN geladenes Modell deckt beliebig viele Sprachen ab,
    der Sprachcode wird nur pro transcribe()-Aufruf mitgegeben (siehe
    _WhisperEngine.transcribe()). Mehrsprachigkeit kostet bei Whisper
    also kein zusätzliches RAM/keine zusätzliche Ladezeit, bei Vosk schon
    -- deshalb der Lazy-Load+LRU-Cache in SttFilter._get_vosk_engine()
    unten, der bei Vosk nur MAX_LOADED_VOSK_LANGUAGES Modelle gleichzeitig
    im Speicher hält.

Beide erwarten 16kHz Mono -- unser Analysepfad läuft mit SAMPLE_RATE
(44100Hz, siehe radiosabbelnich.py), daher dieselbe simple lineare
Interpolation wie in speech_detector.py (für STT-Zwecke ausreichend
genau, keine Hifi-Anwendung; hier separat gehalten statt importiert,
damit dieses Modul keine Abhängigkeit auf speech_detector.py bekommt).
"""

import json
import logging
import threading
import time
from collections import OrderedDict

import numpy as np

log = logging.getLogger("stt")

TARGET_SR = 16000

# Länge eines Analyse-Clips (Sekunden) -- radiosabbelnich.py sammelt genau so
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

# Wie viele Vosk-Modelle (= Sprachen) gleichzeitig im RAM gehalten werden --
# nur relevant für engine="vosk", siehe Moduldocstring. Bei mehr konfigurierten
# Sprachen als das hier wird das am längsten ungenutzte Modell verdrängt
# (LRU, siehe SttFilter._get_vosk_engine()) statt alle gleichzeitig zu laden.
MAX_LOADED_VOSK_LANGUAGES = 2

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

    def transcribe(self, pcm_int16: np.ndarray, source_sr: int, language: str):
        samples = _resample(pcm_int16, source_sr)
        # language erzwingen statt Autodetect: spart die Detection-Zeit UND
        # vermeidet Fehlklassifikation der Sprache bei kurzen/verrauschten
        # Clips (genau unser Anwendungsfall) -- der Aufrufer kennt die
        # erwartete Sprache ohnehin über die Sender-Kategorie
        # (siehe settings_store.resolve_stt_language()).
        segments, _info = self.model.transcribe(samples, language=language, vad_filter=False)
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
    """Hält die aktuell aktive STT-Engine und den zuletzt erhaltenen
    Sample-Befund. sample_async() läuft im Hintergrund-Thread -- Whisper
    kann mehrere Sekunden pro Clip brauchen, das darf den Hauptloop
    (Analysefenster ~1x/Sekunde) niemals blockieren.

    Bei engine="whisper" ist "die Engine" ein einzelnes, sprachunabhängiges
    _WhisperEngine-Objekt (self._whisper_engine). Bei engine="vosk" gibt es
    KEIN einzelnes Engine-Objekt mehr, sondern einen Lazy-Load+LRU-Cache
    pro Sprache (self._vosk_engines, siehe _get_vosk_engine()) -- Grund
    siehe Moduldocstring."""

    def __init__(self, cfg: dict):
        self._lock = threading.Lock()
        self.available = False
        self.last_error = None
        self._engine_name = None
        self._busy = False
        self._verdict = None  # (confidence, text, timestamp, language) oder None
        self._whisper_engine = None
        # lang -> _VoskEngine (erfolgreich geladen) ODER str (Fehlertext,
        # damit ein kaputter Pfad nicht bei jedem sample_async()-Tick erneut
        # das Dateisystem/Modell anfasst). OrderedDict für LRU-Verdrängung,
        # siehe _get_vosk_engine().
        self._vosk_engines = OrderedDict()
        self.reload(cfg)

    def reload(self, cfg: dict):
        """Lädt die durch cfg["engine"] gewählte Engine neu (oder entlädt
        alles, falls cfg["enabled"] False ist). Ein zum Zeitpunkt des
        Aufrufs laufender sample_async()-Thread hält seine eigenen lokalen
        Kopien der ALTEN Engine-Referenzen (siehe sample_async()) und läuft
        damit unbeeinflusst zu Ende -- kein Warten auf "busy" nötig, bevor
        hier ausgetauscht wird.

        Bei Vosk wird hier NICHT eifrig jedes konfigurierte Sprachmodell
        geladen -- nur der Cache geleert (alte Modellpfade könnten sich
        geändert haben), das eigentliche Laden passiert lazy beim ersten
        sample_async() für diese Sprache (siehe _get_vosk_engine()).
        "available" heißt bei Vosk deshalb nur "Filter ist scharf geschaltet",
        NICHT "aktuelle Sprache erfolgreich geladen" -- das zeigt
        language_status() pro Sprache separat."""
        with self._lock:
            self._whisper_engine = None
            self._vosk_engines.clear()

        if not cfg.get("enabled", False):
            with self._lock:
                self.available = False
                self.last_error = None
                self._engine_name = None
                self._verdict = None
            log.info("🗣 STT-Filter deaktiviert.")
            return

        engine_name = cfg.get("engine", "vosk")
        if engine_name == "vosk":
            if not cfg.get("languages"):
                with self._lock:
                    self.available = False
                    self.last_error = "keine Sprache konfiguriert"
                    self._engine_name = engine_name
                    self._verdict = None
                log.error("⚠ STT-Filter: keine Sprache konfiguriert — Feature bleibt deaktiviert.")
                return
            with self._lock:
                self.available = True
                self.last_error = None
                self._engine_name = engine_name
                self._verdict = None
            log.info("🗣 STT-Filter: Engine 'vosk' scharf geschaltet (%d Sprache(n) konfiguriert, "
                     "Laden erfolgt pro Sprache beim ersten Sample).", len(cfg["languages"]))
            return

        if engine_name == "whisper":
            try:
                whisper_engine = _WhisperEngine(cfg.get("whisper_model_size", "tiny"))
            except Exception as e:
                # Modell nicht ladbar, Paket fehlt o.ä. -- Feature
                # deaktiviert sich selbst, RadioSabbelNich läuft normal ohne
                # STT-Filter weiter (siehe combine_label(): kein Befund
                # -> No-Op).
                with self._lock:
                    self.available = False
                    self.last_error = str(e)
                    self._engine_name = engine_name
                    self._verdict = None
                log.error("⚠ STT-Filter: Engine 'whisper' konnte nicht geladen werden (%s) — "
                          "Feature bleibt deaktiviert.", e)
                return
            with self._lock:
                self._whisper_engine = whisper_engine
                self.available = True
                self.last_error = None
                self._engine_name = engine_name
                self._verdict = None
            log.info("🗣 STT-Filter: Engine 'whisper' geladen (multilingual, ein Modell für "
                     "alle konfigurierten Sprachen).")
            return

        with self._lock:
            self.available = False
            self.last_error = f"unbekannte STT-Engine: {engine_name!r}"
            self._engine_name = engine_name
            self._verdict = None
        log.error("⚠ STT-Filter: unbekannte Engine %r — Feature bleibt deaktiviert.", engine_name)

    def status(self):
        """(engine_name, available, last_error) für die Web-UI (siehe
        webui.py SwitcherState.set_stt_status()). Bei Vosk beschreibt das
        nur den Gesamtzustand ("scharf geschaltet"), nicht den Ladezustand
        einzelner Sprachen -- dafür siehe language_status()."""
        with self._lock:
            return self._engine_name, self.available, self.last_error

    def language_status(self) -> dict:
        """Ladezustand jeder bisher für Vosk versuchten Sprache: {lang:
        None} bei Erfolg, {lang: Fehlertext} bei Misserfolg. Nur für Vosk
        aussagekräftig (Whisper braucht kein Pro-Sprache-Modell, siehe
        Moduldocstring) -- leer, solange noch kein Sample für eine Sprache
        versucht wurde (Lazy-Load, siehe _get_vosk_engine())."""
        with self._lock:
            return {lang: (None if isinstance(v, _VoskEngine) else v)
                    for lang, v in self._vosk_engines.items()}

    def _get_vosk_engine(self, lang: str, cfg: dict):
        """Lazy-Load + LRU-Cache für Vosk-Modelle (siehe Moduldocstring).
        Gibt die geladene _VoskEngine zurück, oder None, falls für `lang`
        kein Modellpfad konfiguriert ist oder das Laden fehlschlug -- das
        Ergebnis (Erfolg UND Fehler) wird gecacht, damit ein kaputter Pfad
        nicht bei jedem Sample-Tick erneut Dateisystem/Modell anfasst."""
        with self._lock:
            cached = self._vosk_engines.get(lang)
            if cached is not None:
                self._vosk_engines.move_to_end(lang)  # LRU: zuletzt genutzt
                return cached if isinstance(cached, _VoskEngine) else None

        lang_cfg = cfg.get("languages", {}).get(lang)
        model_path = lang_cfg.get("vosk_model_path") if lang_cfg else None
        if not model_path:
            result = f"keine Sprache/kein Modellpfad für '{lang}' konfiguriert"
            log.error("⚠ STT-Filter: %s.", result)
        else:
            try:
                result = _VoskEngine(model_path)
                log.info("🗣 STT-Filter: Vosk-Modell für Sprache '%s' geladen (%s).", lang, model_path)
            except Exception as e:
                result = str(e)
                log.error("⚠ STT-Filter: Vosk-Modell für Sprache '%s' nicht ladbar (%s).", lang, e)

        with self._lock:
            self._vosk_engines[lang] = result
            self._vosk_engines.move_to_end(lang)
            while len(self._vosk_engines) > MAX_LOADED_VOSK_LANGUAGES:
                oldest_lang, oldest = next(iter(self._vosk_engines.items()))
                del self._vosk_engines[oldest_lang]
                if isinstance(oldest, _VoskEngine):
                    log.debug("🗑 STT-Filter: Vosk-Modell '%s' aus dem Cache verdrängt "
                              "(LRU, max %d gleichzeitig).", oldest_lang, MAX_LOADED_VOSK_LANGUAGES)

        return result if isinstance(result, _VoskEngine) else None

    def sample_async(self, pcm_int16: np.ndarray, sample_rate: int, language: str, cfg: dict):
        """Startet einen Hintergrund-Sample für `language`, falls der
        Filter verfügbar ist und gerade kein anderer Sample läuft -- kein
        Thread-Stapeln, falls die Engine (v.a. Whisper) langsamer ist als
        sample_interval_seconds. `cfg` wird nur für den Vosk-Zweig
        gebraucht (Modellpfad-Lookup, siehe _get_vosk_engine())."""
        with self._lock:
            if not self.available or self._busy:
                return
            self._busy = True
            engine_name = self._engine_name
            whisper_engine = self._whisper_engine  # lokale Kopie, siehe reload()-Docstring

        def _run():
            try:
                if engine_name == "whisper":
                    if whisper_engine is None:
                        return
                    text, confidence = whisper_engine.transcribe(pcm_int16, sample_rate, language)
                else:
                    vosk_engine = self._get_vosk_engine(language, cfg)
                    if vosk_engine is None:
                        return  # Modell für diese Sprache nicht ladbar -- kein Sample möglich
                    text, confidence = vosk_engine.transcribe(pcm_int16, sample_rate)
                log.debug("[stt/%s] text=%r confidence=%.2f", language, text, confidence)
                with self._lock:
                    self._verdict = (confidence, text, time.monotonic(), language)
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
        """(confidence, text, timestamp, language) des letzten Samples,
        oder None, falls noch keiner gelaufen ist."""
        with self._lock:
            return self._verdict

    def close(self):
        with self._lock:
            self._whisper_engine = None
            self._vosk_engines.clear()
            self.available = False


def _fresh_verdict(verdict, cfg: dict, expected_language: str):
    """Gibt (confidence, text) zurück, falls verdict vorhanden, noch frisch
    genug ist (siehe FRESHNESS_FACTOR) UND zur AKTUELL erwarteten Sprache
    passt -- sonst None. Der Sprachabgleich ist nötig, weil last_verdict()
    das Ergebnis des letzten Samples liefert, unabhängig davon, ob der
    Sender/die Kategorie seitdem gewechselt hat (siehe resolve_stt_language()
    in radiosabbelnich.py) -- ohne diese Prüfung könnte kurz nach einem Wechsel
    von einer De-Kategorie auf eine En-Kategorie noch der alte deutsche
    Befund (mit seiner FALSCHEN Konfidenz-Schwelle) nachwirken, bis er von
    selbst zu alt wird. Gemeinsame Basis für combine_label() (Switch-Logik),
    live_confidence() und live_language() (Web-UI, siehe SESSION.md
    2026-08-06) -- alle drei sollen exakt denselben "kein (passender)
    Befund"-Begriff verwenden."""
    if verdict is None:
        return None
    confidence, text, ts, language = verdict
    if language != expected_language:
        return None
    max_age = cfg.get("sample_interval_seconds", 8.0) * FRESHNESS_FACTOR
    if time.monotonic() - ts > max_age:
        return None  # Befund zu alt -> wie "kein Befund"
    return confidence, text


def combine_label(label: str, verdict, cfg: dict, expected_language: str) -> str:
    """Kombiniert das VAD/Heuristik-Ergebnis (`label`, "speech"/"music")
    mit dem letzten STT-Befund (siehe SttFilter.last_verdict()). Reine
    Funktion ohne Zugriff auf StreamSource/SwitcherState -- einzige
    Kopplungsstelle zwischen stt_filter.py und der bestehenden Switch-
    Logik in radiosabbelnich.py.

    `expected_language` kommt aus resolve_stt_language() für die Kategorie
    des AKTUELL laufenden Senders (siehe radiosabbelnich.py/classify()) -- nur
    ein Befund für exakt diese Sprache zählt, siehe _fresh_verdict().

    Kein (frischer, passender) STT-Befund -> `label` unverändert
    durchgereicht. Das ist der Mechanismus, über den sich das Feature bei
    deaktiviertem Filter, Ladefehler, noch fehlendem ersten Sample oder
    frischem Sprachwechsel von selbst neutral verhält, statt RadioSabbelNich
    mit einem nicht (mehr) passenden Befund zu beeinflussen.

    combine_mode:
      - "and" (Default): beide Signale müssen "speech" sagen. Das ist der
        Mechanismus gegen in dieser Sprache gesungene Musik, die
        VAD/Heuristik oft fälschlich als Sprache einordnen -- STT erkennt
        beim Gesang meist keinen zusammenhängenden Text (niedrige
        Konfidenz), das Fenster gilt dann trotz VAD-Treffer als Musik.
      - "or": eines der beiden Signale reicht -- fängt mehr echte
        Moderation (auch was VAD verpasst), aber wieder anfälliger für
        den Gesangs-Fall, den "and" gerade lösen soll."""
    if not cfg.get("enabled", False):
        return label

    fresh = _fresh_verdict(verdict, cfg, expected_language)
    if fresh is None:
        return label
    confidence, _text = fresh

    lang_cfg = cfg.get("languages", {}).get(expected_language, {})
    stt_label = "speech" if confidence >= lang_cfg.get("confidence_threshold", 0.6) else "music"
    mode = cfg.get("combine_mode", "and")
    if mode == "or":
        return "speech" if (label == "speech" or stt_label == "speech") else "music"
    return "speech" if (label == "speech" and stt_label == "speech") else "music"


def live_confidence(verdict, cfg: dict, expected_language: str):
    """Rohe STT-Konfidenz (0..1) für die Live-Anzeige im Web-Interface,
    oder None, falls der Filter deaktiviert ist oder kein frischer,
    zur aktuellen Sprache passender Befund vorliegt -- das Web-Interface
    friert die Anzeige dann grau ein (siehe webui.py). Separate Funktion
    statt kombiniert mit combine_label(): letztere braucht denselben
    "kein Befund"-Fall für die Switch-Logik, beide teilen sich deshalb
    _fresh_verdict() statt die Prüfung zweimal leicht unterschiedlich zu
    implementieren."""
    if not cfg.get("enabled", False):
        return None
    fresh = _fresh_verdict(verdict, cfg, expected_language)
    return fresh[0] if fresh else None


def live_language(verdict, cfg: dict, expected_language: str):
    """Sprachcode des aktuell frischen STT-Befunds für die Live-Anzeige,
    oder None (Filter aus, kein frischer Befund, oder ein Sender-
    /Kategoriewechsel hat die erwartete Sprache seit dem letzten Sample
    geändert -- siehe _fresh_verdict()). Da _fresh_verdict() ohnehin nur
    Treffer FÜR expected_language akzeptiert, ist der Rückgabewert bei
    Erfolg immer gleich expected_language -- die Funktion existiert
    trotzdem separat, damit die Web-UI nicht selbst wissen muss, wann sie
    ein Sprachkürzel statt gar nichts anzeigen soll."""
    if not cfg.get("enabled", False):
        return None
    fresh = _fresh_verdict(verdict, cfg, expected_language)
    return expected_language if fresh else None


# Anteil der Lücke zwischen music_max und speech_min, den der Vorschlag von
# music_max aus Richtung speech_min einnimmt -- 0.7 heißt: 70% des Wegs
# Richtung Sprache-Minimum, 30% Sicherheitsabstand nach unten Richtung
# Musik-Maximum. Kein magischer Wert aus der Theorie, sondern grob dem
# nachempfunden, wie der ursprüngliche DE-Default (0.75) relativ zu den
# gemessenen 0.83 (Sprache-Minimum) und 0.38 (Musik-Schnitt) lag (siehe
# README/SESSION.md) -- näher an der Sprache-Seite, weil combine_mode="and"
# (Default) ohnehin zusätzlich gegen Musik-Fehlalarme absichert, ein zu
# niedriger Schwellwert aber echte Moderation verpassen würde.
_THRESHOLD_MARGIN_RATIO = 0.7


def suggest_confidence_threshold(speech_samples, music_samples):
    """Schlägt aus zwei Listen gemessener STT-Konfidenzwerte (Sprache-Test
    bzw. Musik-Test derselben Sprache, siehe Kalibrierungs-Wizard in
    webui.py) einen confidence_threshold vor -- reine Funktion, dieselbe
    Methode, mit der der ursprüngliche DE-Default (0.75) von Hand
    hergeleitet wurde (siehe README): eine Schwelle irgendwo zwischen dem
    höchsten gemessenen Musik-Wert und dem niedrigsten gemessenen
    Sprache-Wert trennt die beiden Verteilungen im gemessenen Sample
    perfekt.

    Gibt (threshold, clean_separation) zurück. clean_separation ist False,
    wenn sich die beiden Verteilungen im gemessenen Sample ÜBERLAPPEN
    (kein Wert trennt beide sauber) -- dann ist der Vorschlag nur der
    Mittelwert beider Mittelwerte, ein Kompromiss ohne Garantie, und die
    Web-UI zeigt dafür eine Warnung statt den Vorschlag unkommentiert zu
    übernehmen."""
    speech_min = min(speech_samples)
    music_max = max(music_samples)
    if speech_min > music_max:
        gap = speech_min - music_max
        threshold = music_max + _THRESHOLD_MARGIN_RATIO * gap
        clean = True
    else:
        threshold = (sum(speech_samples) / len(speech_samples)
                     + sum(music_samples) / len(music_samples)) / 2
        clean = False
    return round(max(0.0, min(1.0, threshold)), 2), clean
