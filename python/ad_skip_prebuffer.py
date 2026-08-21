#!/usr/bin/env python3
"""
ad_skip_prebuffer.py — Hintergrund-Baustein für das geplante
"Werbeblock-Vorbuffering nach der Nachrichtenpause" (siehe SESSION.md,
Eintrag 2026-08-21, Phase 1). Noch NICHT in radiosabbelnich.py verdrahtet
-- dieses Modul ist bewusst eigenständig testbar, bevor es an den
News-Break-Lebenszyklus des Hauptloops angebunden wird.

Grundidee: während die News-Break-MP3 noch läuft, im Hintergrund schon
den Live-Stream des pausierten Senders öffnen und Fenster für Fenster
klassifizieren (Sprache vs. Musik) -- OHNE das Audio abzuspielen oder
zwischenzuspeichern. Läuft der Werbeblock auf dem Live-Sender kürzer als
die verbleibende Pause-Zeit, ist er dadurch schon vorbei, wenn die
Pause-MP3 endet: der Hauptloop kann dann direkt in die Musik einsteigen,
statt den Werbeblock hörbar abzuspielen. Ein Live-Stream lässt sich nicht
schneller als Echtzeit lesen -- der Trick ist NICHT "vorspulen", sondern
die ohnehin verstreichende Pause-Zeit doppelt zu nutzen.

`AdSkipPrebuffer` ist bewusst KEINE Erweiterung von
`PrebufferedSource` (radiosabbelnich.py): die sammelt nur rohes Audio für
eine spätere Übernahme, hier geht es um fortlaufende Klassifikation OHNE
Audio-Aufbewahrung -- andere Aufgabe, anderer Lebenszyklus. Eigene
`StreamSource` (aus stream_source.py, nicht aus radiosabbelnich.py
importiert -- siehe dessen Docstring, Zirkelimport-Vermeidung) und
EIGENE `SpeechDetector`-Instanz: siehe deren `reset()`-Docstring bzw.
ARCHITECTURE.md-Abschnitt "Sprache-Erkennung (VAD)" -- zwei parallel
laufende Audioströme dürfen sich niemals eine Detector-Instanz teilen,
sonst vermischen sich Leftover/Modellzustand beider Ströme.

Bewusst NUR VAD, kein STT/Fingerprint (Stand Phase 1): STT-Engines
(`stt_filter.SttFilter`) laden pro Instanz eigene Sprachmodelle -- eine
zweite Instanz würde RAM verdoppeln, eine geteilte Instanz konkurriert
um deren `_busy`-Guard mit dem Hauptloop-Sampling. `FingerprintDB`-
Connections sind laut ARCHITECTURE.md exklusiv dem Hauptloop-Thread
vorbehalten (sqlite3 ist nicht thread-übergreifend sicher). Beides wäre
für einen echten Nutzen lösbar, aber kein Blocker fürs Kernfeature --
reine VAD-Klassifikation reicht, um "ist das noch Sprache" zu
beantworten. Bewusst auch KEIN Heuristik-Fallback (`classify_window()`
aus radiosabbelnich.py): der ist deutlich unzuverlässiger als VAD und
würde denselben Zirkelimport-Konflikt heraufbeschwören, den
stream_source.py gerade vermeidet. Ist VAD nicht verfügbar, bleibt
dieser Hintergrund-Detector einfach dauerhaft "nicht bereit" -- das
Feature deaktiviert sich dadurch faktisch selbst (gleiches Muster wie
combine_label() in stt_filter.py bei fehlendem STT-Befund), der
Hauptloop-Fallback bleibt der heutige, unveränderte Pfad.
"""

import logging
import threading

from speech_detector import SpeechDetector
from stream_source import STREAM_READ_TIMEOUT, StreamSource

log = logging.getLogger("adskip")

# So viele MUSIK-Fenster am Stück müssen erkannt werden, bevor is_ready()
# True liefert -- ein einzelnes musikalisches Jingle/Sting MITTEN im
# Werbeblock soll nicht sofort als "fertig, Werbung vorbei" durchgehen.
CONSECUTIVE_MUSIC_TO_CONFIRM = 2


class AdSkipPrebuffer:
    """Liest im Hintergrund-Thread fortlaufend Analysefenster von `url` und
    klassifiziert sie (VAD, siehe Moduldocstring). Audio wird NIRGENDS
    zwischengespeichert -- jedes Fenster wird direkt nach der
    Klassifikation verworfen, das ist hier Absicht, kein Weglassen einer
    Pufferung."""

    def __init__(self, sample_rate: int, url: str,
                 window_seconds: float = 1.0,
                 music_confirm_windows: int = CONSECUTIVE_MUSIC_TO_CONFIRM,
                 name: str = "adskip"):
        self.url = url
        self.name = name
        self.sample_rate = sample_rate
        self.window_seconds = window_seconds
        self.music_confirm_windows = music_confirm_windows
        self.source = StreamSource(sample_rate)
        self.detector = SpeechDetector(sample_rate)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self.dead = False  # True, falls die Quelle unterwegs gestorben ist (EOF/Timeout)
        self._music_streak = 0
        self._ready = False
        self._windows_seen = 0  # rein fürs Debug-Log/Tests, keine Steuerlogik daran

    def start(self):
        """Verbindet und startet den Hintergrund-Thread. `detector.reset()`
        VOR dem ersten Fenster ist hier eigentlich redundant (die Instanz
        ist gerade erst konstruiert, also schon frisch) -- steht trotzdem
        explizit hier, falls `start()` je auf einer wiederverwendeten
        Instanz aufgerufen wird (z.B. Retry nach `dead`): dann darf kein
        Leftover/Modellzustand vom vorigen Verbindungsversuch überleben."""
        self.source.start(self.url)
        self.detector.reset()
        self.dead = False
        self._music_streak = 0
        self._ready = False
        self._windows_seen = 0
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"adskip-{self.name}"[:15])
        self._thread.start()
        log.debug("Ad-Skip-Hintergrund-Detector gestartet: %s", self.name)

    def _run(self):
        while not self._stop_event.is_set():
            mono, _stereo = self.source.read_window(self.window_seconds)
            if mono.size == 0:
                self.dead = True
                log.debug("Ad-Skip-Hintergrund-Quelle liefert nichts mehr, "
                          "markiere als tot: %s", self.url)
                return

            label, prob = self.detector.classify(mono)
            if label is None:
                # Kein VAD verfügbar -- siehe Moduldocstring, bewusst kein
                # Heuristik-Fallback. Fenster verstreicht ungenutzt, "ready"
                # bleibt auf Dauer False.
                continue

            with self._lock:
                self._windows_seen += 1
                if label == "music":
                    self._music_streak += 1
                else:
                    self._music_streak = 0
                self._ready = self._music_streak >= self.music_confirm_windows

            log.debug("[adskip:%s] label=%s prob=%.3f streak=%d ready=%s",
                      self.name, label, prob, self._music_streak, self._ready)
            # mono wird hier NICHT aufbewahrt -- siehe Klassendocstring.

    def is_ready(self) -> bool:
        """True, sobald `music_confirm_windows` Musik-Fenster am Stück
        erkannt wurden -- Signal an den Aufrufer, dass ein Umstieg in
        diesen Hintergrund-Stream jetzt in der Musik landen würde."""
        with self._lock:
            return self._ready

    def _join(self):
        """Gleiche Regel wie bei `PrebufferedSource._join()`
        (radiosabbelnich.py): eine Pipe hat genau einen Leser. Reagiert der
        Thread nicht innerhalb der Frist, wird die Quelle als tot markiert
        statt übernommen -- der Aufrufer darf sie dann nicht mehr anfassen,
        `stop()` räumt sie trotzdem auf (ffmpeg beenden)."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=STREAM_READ_TIMEOUT + 1)
            if self._thread.is_alive():
                self.dead = True
                log.warning("⚠ Ad-Skip-Hintergrund-Thread für %s reagiert nicht "
                            "— Quelle wird verworfen.", self.url)
            self._thread = None

    def promote(self):
        """Stoppt den Hintergrund-Thread, gibt aber die weiterlaufende
        StreamSource zur Übernahme zurück (KEIN self.source.stop() -- der
        Aufrufer übernimmt sie als neue Playback-Quelle, gleiches Prinzip
        wie `PrebufferedSource.promote()`). `self.dead` danach prüfen, bevor
        die zurückgegebene Quelle tatsächlich verwendet wird -- ein
        hängender Reader-Thread wird erst beim Join (also während dieses
        Aufrufs) als tot erkannt, nicht schon vorher."""
        self._join()
        return self.source

    def stop(self):
        """Verwirft die Hintergrundquelle komplett (Pause vorbei, Nutzer
        hat manuell weggeschaltet, Feature deaktiviert o.ä.)."""
        self._join()
        self.source.stop()
