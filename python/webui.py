#!/usr/bin/env python3
# Copyright (C) 2026 RadioSabbelNich
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 (or
# later), as published by the Free Software Foundation. See LICENSE.

"""
webui.py — Eingebettetes Webinterface für RadioSabbelNich.

Läuft als ThreadingHTTPServer in einem Hintergrund-Thread des
Hauptprozesses (radiosabbelnich.py). Zeigt den aktuell laufenden Sender und
verbundene Hörer (IP/User-Agent/Verbindungsdauer, abgefragt über Icecasts
Admin-API) und erlaubt manuelles Umschalten über eine Sender-Liste aus
stations.json.

Kommunikation mit dem Hauptloop läuft über SwitcherState: geteilter,
lock-geschützter In-Memory-Zustand statt Datei-Polling oder IPC — läuft
im selben Prozess, also reicht das.
"""

import base64
import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fingerprint
import folder_browse
import i18n
import music_library
import music_query
import music_scan
import resource_monitor
import settings_store
import station_import
import stations_store
import stt_filter
import update_check

log = logging.getLogger("webui")

# Synthetische Sender-ID für die Nachrichten-Pause (siehe news_break.py) —
# existiert absichtlich NICHT in stations.json, damit Rotation/Watchdog/
# Prebuffering sie nie zu Gesicht bekommen. Nur SwitcherState.current_station()
# kennt sie, um dem Web-Interface während der Pause etwas Sinnvolles zu zeigen.
NEWS_BREAK_STATION_ID = "__news_break__"

# Feste Docker-Mount-Grenzen für die Breadcrumb-Ordner-Browser-Komponente
# (siehe folder_browse.py) -- EIN gemeinsamer Baustein, zwei unabhängig
# gespeicherte Ziele (news_break.mp3_folder bzw. music_library.path,
# siehe settings_store.py). Absichtlich hier als Konstante statt aus
# settings.json abgeleitet: der Mount-Pfad selbst ist über Docker fix
# (docker-compose.yml), nur der jeweils AUSGEWÄHLTE Unterordner darunter
# ist konfigurierbar.
_BROWSE_ROOTS = {
    "news_break": "/app/news_mp3",
    "music_library": "/app/music_library",
}

# Deckel pro Stufe (Sprache/Musik) im STT-Kalibrierungs-Wizard (siehe
# SwitcherState.add_calibration_sample()) -- gegen unbegrenztes Wachstum,
# falls eine Session vergessen im Hintergrund weiterläuft. Bei
# sample_interval_seconds=8s (Default) entspricht das ~13 Minuten
# Sampling pro Stufe, reichlich für eine Kalibrierung.
MAX_CALIBRATION_SAMPLES = 100

# Einmalig beim Modul-Import gelesen (statt bei jedem Request von der
# Platte) — kleines statisches Asset, ändert sich nicht zur Laufzeit.
_BANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "radiosabbelnich.webp")
try:
    with open(_BANNER_PATH, "rb") as _f:
        _BANNER_BYTES = _f.read()
except OSError as _e:
    _BANNER_BYTES = None
    log.warning("⚠ Banner-Bild %s nicht lesbar (%s) — Seite läuft ohne.", _BANNER_PATH, _e)

# Versionsstring aus VERSION (Repo-Root, siehe CLAUDE.md "Versionspflege")
# -- rein informativ, unter dem Banner-Bild angezeigt. Gleiches
# Lade-Muster wie oben: einmalig beim Modul-Import, kein Datei-Zugriff
# pro Request.
_VERSION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
try:
    with open(_VERSION_PATH, "r", encoding="utf-8") as _f:
        _VERSION_STRING = _f.read().strip()
except OSError as _e:
    _VERSION_STRING = ""
    log.warning("⚠ VERSION-Datei %s nicht lesbar (%s) — Seite zeigt keine Versionsnummer.",
                _VERSION_PATH, _e)

# Vendorte QR-Code-Bibliothek (siehe qrcode.js) statt CDN-Einbindung: das
# Web-Interface läuft laut CLAUDE.md nur im eigenen VPN, ein Client dort
# hat nicht zwangsläufig Internetzugriff für ein <script src="cdn...">.
_QRCODE_JS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qrcode.js")
try:
    with open(_QRCODE_JS_PATH, "rb") as _f:
        _QRCODE_JS_BYTES = _f.read()
except OSError as _e:
    _QRCODE_JS_BYTES = None
    log.warning("⚠ %s nicht lesbar (%s) — QR-Code-Button bleibt ohne Wirkung.", _QRCODE_JS_PATH, _e)

# PWA-Assets (Manifest, Service Worker, Icons) -- statische Dateien wie
# oben, gleiches Lade-Muster. Fehlen sie, bleibt die Seite ein normales
# Webinterface: kein "Zum Home-Bildschirm hinzufügen" auf Android, aber
# keine Fehlfunktion.
def _load_static(filename, binary=True):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as f:
            return f.read()
    except OSError as e:
        log.warning("⚠ PWA-Asset %s nicht lesbar (%s) — Installierbarkeit eingeschränkt.", path, e)
        return None


_MANIFEST_JSON_BYTES = _load_static("manifest.json")
_SERVICE_WORKER_JS_BYTES = _load_static("sw.js")
_ICON_192_BYTES = _load_static("icon-192.png")
_ICON_512_BYTES = _load_static("icon-512.png")
# Browser-Tab-Icon -- eine per Center-Crop quadratisch zugeschnittene
# Miniatur von radiosabbelnich.webp (768x768, dann intern von den Browsern auf
# 16/32/48px skaliert), NICHT dieselbe Grafik wie icon-192/512.png (das
# schlichte "Broadcast"-Symbol-Platzhalter fürs Installieren als App).
_FAVICON_ICO_BYTES = _load_static("favicon.ico")


class SwitcherState:
    """Thread-sicherer geteilter Zustand zwischen Hauptloop und Webserver.

    Hält den Live-Rotationszustand (welcher Sender läuft gerade, anstehende
    manuelle Switch-/Reload-Requests) als In-Memory-Cache. stations.json
    (über stations_store) bleibt die eigentliche Quelle der Wahrheit für
    Senderdaten — reload() liest sie neu ein, z.B. nachdem die Config-Seite
    etwas geändert hat."""

    def __init__(self):
        self._lock = threading.Lock()
        # Versionszähler + Condition fürs Long-Polling (siehe wait_for_change()):
        # bumped bei allem, was die Web-UI zeitnah sehen soll (Senderwechsel,
        # News-Break-Start/Ende, Filter-Toggle) -- NICHT bei speech_probability/
        # stt_status, die ändern sich zu oft (jedes Analysefenster) und würden
        # den Long-Poll wieder auf Poll-Tempo runterziehen, ohne echten Nutzen
        # fürs "hinkt hinterher"-Problem (Bullshitometer bleibt Sache des
        # normalen Intervall-Pollings).
        self._version = 0
        self._version_cond = threading.Condition(self._lock)
        self._all_stations = []
        self._active_stations = []
        self._current_id = None
        self._manual_request = None
        self._reload_requested = False
        self._skip_requested = False
        self._news_break_skip_requested = False
        self._last_fingerprint_clip = None  # {"clip_id", "label", "previous_station_id"}
        self._filter_enabled = True
        self._filter_toggle_requested = False
        self._prebuffer_seconds = settings_store.DEFAULTS["prebuffer_seconds"]
        self._prebuffer_count = settings_store.DEFAULTS["prebuffer_count"]
        self._stream_url = settings_store.DEFAULTS["stream_url"]
        self._tls_enabled = settings_store.DEFAULTS["tls_enabled"]
        self._language = settings_store.DEFAULTS["language"]
        self._news_break_cfg = dict(settings_store.DEFAULTS["news_break"])
        self._news_break_active = False
        self._news_break_file = None
        self._news_break_tags = None  # {"title","artist","album","year"}, siehe set_news_break()
        # Radio/Musiksammlung-Modus (siehe radiosabbelnich.py main(),
        # Modus-Fork ganz oben) -- request/pop wie beim manuellen
        # Senderwechsel, weil player-kritisch (nur der Hauptloop darf
        # `source` anfassen, siehe CLAUDE.md). _mode ist der zuletzt vom
        # Hauptloop BESTÄTIGTE Zustand (set_mode(), nach dem echten
        # Übergang), _mode_change_requested der noch unbearbeitete Wunsch.
        self._mode = settings_store.DEFAULTS["current_mode"]
        self._mode_change_requested = None
        self._music_active = False
        self._music_file = None
        self._music_index = -1
        self._music_total = 0
        self._music_label = None  # "Artist – Titel" bei Query-Wiedergabe (Phase 2), sonst None
        self._music_tags = None  # {"title","artist","album","year"}, siehe set_music_status()
        self._music_play_requested = False
        self._music_play_tracks = None  # None=Ordner-Modus, sonst bereits aufgelöste Query-Trackliste
        self._music_stop_requested = False
        self._music_skip_requested = None  # +1/-1 oder None
        self._stt_filter_cfg = dict(settings_store.DEFAULTS["stt_filter"])
        self._song_recognition_cfg = dict(settings_store.DEFAULTS["song_recognition"])
        self._stt_status = {"engine": None, "available": False, "error": None}
        self._stt_language_status = {}  # lang -> Fehlertext|None, siehe set_stt_language_status()
        self._calibration = None  # {"language", "stage", "speech_samples", "music_samples"} oder None
        self._calibration_last_ts = None  # Dedup-Timestamp, siehe add_calibration_sample()
        self._speech_probability = 0.0
        self._audio_levels = []  # siehe set_audio_levels()
        self._stt_probability = None  # siehe set_stt_probability()
        self._stt_language = None  # siehe set_stt_language()
        self._fp_activity = None  # {"status", "label", "ts"} oder None, siehe set_fingerprint_activity()
        # Anders als _fp_activity (blinkt nur FP_ACTIVITY_TTL Sekunden auf)
        # bleiben diese beiden stehen, bis der Prozess neu startet -- Wand-
        # uhrzeit (time.time(), nicht monotonic), fürs "zuletzt gelernt/
        # erkannt: hh:mm:ss Uhr" in der Live-Anzeige (siehe
        # set_fingerprint_activity()). None, solange das jeweilige Ereignis
        # seit Prozessstart noch nie vorkam.
        self._fp_last_learned_ts = None
        self._fp_last_match_ts = None
        self.reload()

    def reload(self):
        all_stations = stations_store.load_all()
        active = sorted(
            (s for s in all_stations if s.get("enabled", True)),
            key=lambda s: s["name"].lower(),
        )
        settings = settings_store.load()
        with self._lock:
            self._all_stations = all_stations
            self._active_stations = active
            if self._current_id is None and active:
                self._current_id = active[0]["id"]
            self._prebuffer_seconds = settings["prebuffer_seconds"]
            self._prebuffer_count = settings["prebuffer_count"]
            self._stream_url = settings["stream_url"]
            self._tls_enabled = settings["tls_enabled"]
            self._language = settings["language"]
            self._news_break_cfg = settings["news_break"]
            self._stt_filter_cfg = settings["stt_filter"]
            self._song_recognition_cfg = settings["song_recognition"]
            # Nur der Startwert -- danach ist der Hauptloop über set_mode()
            # (NACH dem echten Übergang) die alleinige Quelle für _mode zur
            # Laufzeit. reload() läuft ohnehin nur im radio-Zweig
            # (pop_reload_request()), ein Überschreiben hier wäre während
            # einer laufenden Musik-Session sowieso nie erreichbar.
            self._mode = settings["current_mode"]

    @property
    def prebuffer_seconds(self) -> float:
        with self._lock:
            return self._prebuffer_seconds

    @property
    def prebuffer_count(self) -> int:
        with self._lock:
            return self._prebuffer_count

    @property
    def stream_url(self) -> str:
        with self._lock:
            return self._stream_url

    @property
    def tls_enabled(self) -> bool:
        """Nur zum Auslesen BEIM START (siehe radiosabbelnich.py/main()) --
        anders als die anderen Settings hier wirkt eine Änderung nicht
        sofort, weil der ThreadingHTTPServer sein Socket nicht im
        laufenden Betrieb neu in TLS einwickeln kann. Ein späteres
        reload() aktualisiert diesen Wert zwar (z.B. nach einer Änderung
        über /config), das hat aber erst nach einem Neustart des
        Containers eine sichtbare Wirkung."""
        with self._lock:
            return self._tls_enabled

    @property
    def language(self) -> str:
        """Anders als tls_enabled wirkt eine Änderung sofort: do_GET liest
        diesen Wert bei jedem Seitenaufruf frisch und wählt damit nur eine
        von zwei beim Modul-Import bereits fertig gerenderten HTML-Varianten
        aus (siehe _PAGE_HTML_BYTES), kein Server-Neustart nötig."""
        with self._lock:
            return self._language

    @property
    def news_break_cfg(self) -> dict:
        with self._lock:
            return dict(self._news_break_cfg)

    @property
    def stt_filter_cfg(self) -> dict:
        with self._lock:
            return dict(self._stt_filter_cfg)

    @property
    def song_recognition_cfg(self) -> dict:
        with self._lock:
            return dict(self._song_recognition_cfg)

    @property
    def active_stations(self) -> list:
        """Nur aktivierte Sender, alphabetisch — die Rotationsreihenfolge."""
        with self._lock:
            return list(self._active_stations)

    @property
    def all_stations(self) -> list:
        """Alle Sender (aktiv + deaktiviert), für die Config-Seite."""
        with self._lock:
            return list(self._all_stations)

    @property
    def current_id(self):
        with self._lock:
            return self._current_id

    def set_current(self, station_id):
        with self._lock:
            self._current_id = station_id
            self._version += 1
            self._version_cond.notify_all()

    def current_station(self):
        """Aktuell laufender Sender als dict, oder None.

        Während einer Nachrichten-Pause (siehe set_news_break()) liefert
        das eine virtuelle Station statt in stations.json nachzuschlagen
        (dort existiert kein solcher Eintrag) — sonst würde das
        Web-Interface fälschlich "Kein Sender aktiv" zeigen, obwohl aktiv
        eine MP3 läuft."""
        with self._lock:
            if self._news_break_active:
                return {"id": NEWS_BREAK_STATION_ID, "name": "📰 Nachrichten-Pause"}
            cid = self._current_id
            for s in self._active_stations:
                if s["id"] == cid:
                    return s
            for s in self._all_stations:
                if s["id"] == cid:
                    return s
            return None

    def set_news_break(self, active: bool, file_name: str = None, tags: dict = None):
        """Vom Hauptloop aufgerufen, sobald eine Nachrichten-Pause beginnt
        oder endet (siehe news_break.py). `file_name` (Pfad der gerade
        laufenden MP3 relativ zu mp3_folder, seit der Unterordner-
        Rekursion kein reiner Basename mehr) füttert das "Jetzt läuft"-
        Feld im Web-Interface — dieselbe Anzeige, die sonst ICY-Metadaten
        zeigt. `tags` (seit 2026-08-15, siehe audio_tags.read_display_tags())
        füttert die Zwei-Zeilen-Tag-Anzeige (Titel & Interpret / Album &
        Jahr) in _build_status()."""
        with self._lock:
            self._news_break_active = active
            self._news_break_file = file_name
            self._news_break_tags = tags
            if active:
                self._current_id = NEWS_BREAK_STATION_ID
            self._version += 1
            self._version_cond.notify_all()

    @property
    def news_break_active(self) -> bool:
        with self._lock:
            return self._news_break_active

    @property
    def news_break_file(self):
        with self._lock:
            return self._news_break_file

    @property
    def news_break_tags(self):
        with self._lock:
            return self._news_break_tags

    def request_switch(self, station_id):
        with self._lock:
            self._manual_request = station_id

    def pop_manual_request(self):
        """Liefert den anstehenden manuellen Switch-Request (oder None) und
        leert ihn dabei — wird vom Hauptloop einmal pro Fenster abgefragt."""
        with self._lock:
            req = self._manual_request
            self._manual_request = None
            return req

    def request_reload(self):
        """Von der Config-Seite nach jeder Änderung aufgerufen — der
        Hauptloop liest stations.json beim nächsten Durchlauf neu ein."""
        with self._lock:
            self._reload_requested = True

    def pop_reload_request(self) -> bool:
        with self._lock:
            flag = self._reload_requested
            self._reload_requested = False
            return flag

    def request_skip(self):
        """"ZAPPEN!"-Knopf: Nutzer hat selbst erkannt, dass gerade
        geredet wird, auch wenn VAD/Heuristik (noch) nicht angeschlagen
        haben — sofort weg vom aktuellen Sender, wie ein Auto-Switch."""
        with self._lock:
            self._skip_requested = True

    def pop_skip_request(self) -> bool:
        with self._lock:
            flag = self._skip_requested
            self._skip_requested = False
            return flag

    def request_news_break_skip(self):
        """Eigener Skip-Knopf NUR für eine laufende Nachrichten-Pause
        (Nutzer-Wunsch, siehe SESSION.md): ANDERS als request_skip() oben
        (das "ZAPPEN!", beendet die Pause komplett) wählt dieser nur eine
        ANDERE MP3 aus demselben Ordner, die Pause selbst läuft weiter."""
        with self._lock:
            self._news_break_skip_requested = True

    def pop_news_break_skip_request(self) -> bool:
        with self._lock:
            flag = self._news_break_skip_requested
            self._news_break_skip_requested = False
            return flag

    def set_last_fingerprint_clip(self, clip_id: int, label: str, previous_station_id: str):
        """Vom Hauptloop nach jedem Fingerprint-Treffer aufgerufen, damit
        der "Zapping-Fehler"-Knopf weiß, welchen Clip er ggf. aus der DB
        werfen soll — und zu welchem Sender er zurückschalten soll
        (der, der lief, BEVOR der Treffer den Switch ausgelöst hat)."""
        with self._lock:
            self._last_fingerprint_clip = {
                "clip_id": clip_id, "label": label, "previous_station_id": previous_station_id,
            }

    def pop_last_fingerprint_clip(self):
        """Liefert den zuletzt per Fingerprint erkannten Clip (oder None)
        und leert ihn dabei — ein zweiter Klick auf "Zapping-Fehler" ohne
        neuen Treffer dazwischen soll ins Leere laufen, nicht denselben
        (schon gelöschten) Clip nochmal anfassen."""
        with self._lock:
            clip = self._last_fingerprint_clip
            self._last_fingerprint_clip = None
            return clip

    def set_stt_status(self, engine: str, available: bool, error: str = None):
        """Vom Hauptloop nach jedem (Neu-)Laden der STT-Filter-Engine
        aufgerufen (siehe stt_filter.SttFilter.status()), damit die
        Config-Seite den tatsächlichen Zustand zeigen kann ("✅ Vosk
        aktiv" bzw. "⚠ Deaktiviert: <Fehlermeldung>") statt stillschweigend
        nichts zu tun, falls das Modell nicht ladbar war."""
        with self._lock:
            self._stt_status = {"engine": engine, "available": available, "error": error}

    @property
    def stt_status(self) -> dict:
        with self._lock:
            return dict(self._stt_status)

    def set_stt_language_status(self, status: dict):
        """Ladezustand jeder bisher für Vosk versuchten Sprache (siehe
        stt_filter.SttFilter.language_status()) -- für die Sprachen-Tabelle
        auf der Config-Seite (✅/⚠ pro Zeile), analog zu set_stt_status()
        für den Gesamtzustand der Engine."""
        with self._lock:
            self._stt_language_status = dict(status)

    @property
    def stt_language_status(self) -> dict:
        with self._lock:
            return dict(self._stt_language_status)

    # ---- STT-Kalibrierungs-Wizard (Teil 1b, siehe SESSION.md 2026-08-06) ----
    #
    # Bewusst KEIN request/pop wie bei source/current/Streak-Buchhaltung
    # (siehe CLAUDE.md): eine Kalibrierungs-Session berührt keinen der
    # Player-kritischen Zustände, für die dieses Muster da ist -- sie ist
    # ein eigenständiger, isolierter Datentopf, den der Webserver-Thread
    # direkt (lock-geschützt) schreiben und der Hauptloop direkt lesen UND
    # ergänzen darf, ohne dass beide Seiten sich in die Quere kommen
    # können. Der Hauptloop überschreibt _calibration nie komplett, nur
    # add_calibration_sample() hängt an; der Webserver setzt/leert es nie
    # während der Hauptloop mitten in add_calibration_sample() steckt (Lock).

    def start_calibration(self, language: str):
        """Startet (oder startet neu) eine Kalibrierungs-Session für
        `language`, Stufe "speech". Der Hauptloop erzwingt ab dem
        nächsten Tick `language` als STT-Zielsprache (statt der
        kategoriebasierten Auflösung) und pausiert währenddessen die
        automatische Switch-Logik komplett (siehe radiosabbelnich.py) --
        sonst könnte ein durch die erzwungene Sprache verfälschtes
        combine_label()-Ergebnis mitten in der Kalibrierung einen
        Wechsel auslösen."""
        with self._lock:
            self._calibration = {"language": language, "stage": "speech",
                                  "speech_samples": [], "music_samples": []}
            self._calibration_last_ts = None

    def set_calibration_stage(self, stage: str):
        """stage: "speech" oder "music". No-Op, falls gerade keine Session
        läuft (z.B. Doppelklick/veraltete Seite)."""
        with self._lock:
            if self._calibration is not None:
                self._calibration["stage"] = stage
                self._calibration_last_ts = None  # neue Stufe -> nächster Verdict zählt frisch

    def stop_calibration(self):
        with self._lock:
            self._calibration = None
            self._calibration_last_ts = None

    @property
    def calibration_language(self):
        """None, solange keine Session läuft -- vom Hauptloop bei JEDEM
        Tick gelesen, um zu entscheiden, ob die Sprachauflösung über die
        Sender-Kategorie überschrieben werden muss (siehe start_calibration())."""
        with self._lock:
            return self._calibration["language"] if self._calibration else None

    def add_calibration_sample(self, confidence: float, text: str, ts: float):
        """Vom Hauptloop nach jedem STT-Sample aufgerufen (auch wenn
        gerade keine Kalibrierung läuft -- No-Op dann, siehe unten).
        `ts` dedupliziert: last_verdict() liefert über mehrere Haupt-
        loop-Ticks denselben (noch nicht durch ein neues Sample ersetzten)
        Befund zurück, ohne die Dedup-Prüfung würde derselbe Sample-Wert
        mehrfach gezählt. Auf MAX_CALIBRATION_SAMPLES pro Stufe gedeckelt
        -- gegen unbegrenztes Wachstum, falls eine Session vergessen im
        Hintergrund weiterläuft.

        Samples OHNE erkannten Text werden verworfen (live an einer echten
        Kalibrierung entdeckt, siehe SESSION.md 2026-08-06): leerer Text
        bedeutet "STT hat gar kein Wort-Hypothese gebildet" (Pause/Jingle/
        Werbeblock beim Sprache-Test, reine Instrumentalpassage beim
        Musik-Test) -- NICHT "mit niedriger Konfidenz als Sprache erkannt".
        Beides ungefiltert in dieselbe Statistik zu werfen zog speech_min
        in echten Tests künstlich auf 0 herunter (jede Pause zählte als
        "schlechtester Sprache-Sample") und machte suggest_confidence_
        threshold()s Vorschlag unbrauchbar. _VoskEngine.transcribe()/
        _WhisperEngine.transcribe() liefern beide confidence=0.0 GENAU
        dann, wenn auch text leer ist (siehe stt_filter.py) -- der
        Text-Check hier ist daher gleichbedeutend, aber semantisch
        richtiger als ein Vergleich auf confidence==0.0."""
        with self._lock:
            if self._calibration is None or ts == self._calibration_last_ts:
                return
            self._calibration_last_ts = ts
            if not text.strip():
                return
            key = "speech_samples" if self._calibration["stage"] == "speech" else "music_samples"
            samples = self._calibration[key]
            if len(samples) < MAX_CALIBRATION_SAMPLES:
                samples.append({"confidence": confidence, "text": text})

    def calibration_snapshot(self):
        with self._lock:
            return dict(self._calibration) if self._calibration else None

    def set_speech_probability(self, value: float):
        """Vom Hauptloop nach jeder Klassifikation eines Analysefensters
        aufgerufen (VAD-Wahrscheinlichkeit, oder bei Heuristik-Fallback
        die Voting-Quote als grobe Näherung) -- Rohwert VOR der STT-
        Verknüpfung, fürs "Bullshitometer" im Web-Interface. Bleibt
        unverändert, solange die Nachrichten-Pause läuft oder der
        Sabbelfilter aus ist (der Hauptloop ruft classify() dann gar nicht
        auf) -- das Frontend erkennt diesen Zustand an `news_break_active`/
        `filter_enabled` und blendet den Wert entsprechend aus, statt ihn
        auf 0 zu erzwingen."""
        with self._lock:
            self._speech_probability = value

    @property
    def speech_probability(self) -> float:
        with self._lock:
            return self._speech_probability

    def set_audio_levels(self, levels: list):
        """Vom Hauptloop einmal pro Analysefenster aufgerufen (Radio- UND
        Musik-Zweig, siehe radiosabbelnich.py/sub_window_dbfs()) -- mehrere
        dBFS-Pegelwerte statt nur einem, damit das VU-Meter im Web-Interface
        pro Sekunde durch sie animieren kann statt nur 1x/Sekunde zu
        springen. Bumpt bewusst NICHT `_version`, exakt aus demselben Grund
        wie set_speech_probability(): ändert sich jedes Fenster, würde den
        Long-Poll auf Poll-Tempo runterziehen, ohne echten Nutzen -- bleibt
        Sache des normalen Intervall-Pollings."""
        with self._lock:
            self._audio_levels = levels

    @property
    def audio_levels(self) -> list:
        with self._lock:
            return self._audio_levels

    def set_stt_probability(self, value):
        """Vom Hauptloop in derselben classify()-Closure wie
        set_speech_probability() aufgerufen — rohe STT-Konfidenz (0..1)
        oder None (siehe stt_filter.live_confidence(): Filter aus oder
        kein frischer Befund) fürs STT-Live-Anzeige im Web-Interface.
        Gleiches Einfrier-Verhalten wie beim Bullshitometer: bleibt
        unverändert, solange classify() nicht aufgerufen wird (News-Break/
        Sabbelfilter aus), das Frontend blendet über news_break_active/
        filter_enabled aus."""
        with self._lock:
            self._stt_probability = value

    @property
    def stt_probability(self):
        with self._lock:
            return self._stt_probability

    def set_stt_language(self, value):
        """Sprachcode des aktuell frischen STT-Befunds (siehe
        stt_filter.live_language()) oder None -- fürs Sprachkürzel neben
        dem STT-Balken auf der Player-Seite (nur mit Teil 1 aussagekräftig,
        vorher immer der eine konfigurierte Sprachcode)."""
        with self._lock:
            self._stt_language = value

    @property
    def stt_language(self):
        with self._lock:
            return self._stt_language

    def set_fingerprint_activity(self, status: str, label: str = None):
        """status: "match" (bekannter Clip wiedererkannt) oder "learned"
        (neuer Clip gelernt, kein Treffer) — fürs Fingerprint-Live-Icon
        auf der Player-Seite. Bewusst GETRENNT von
        set_last_fingerprint_clip()/pop_last_fingerprint_clip(): die sind
        für den "Zapping-Fehler"-Button reserviert und werden beim Klick
        konsumiert (pop) — ein zweiter Konsument (die Live-Anzeige, die
        bei jedem Poll denselben Wert lesen will) würde sich mit dem
        Button den Wert sonst gegenseitig wegschnappen.

        Aktualisiert nebenbei _fp_last_learned_ts/_fp_last_match_ts (siehe
        __init__) -- die bleiben, anders als _fp_activity, über die
        FP_ACTIVITY_TTL hinaus stehen, damit "zuletzt gelernt/erkannt"
        auch dann noch etwas anzeigt, wenn das letzte Ereignis schon
        länger her ist (Nutzer-Feedback: "Fingerprint zeigt nur Idle und
        gelernt, ich hab erkannt noch nie gesehen" -- ohne die Historie
        war nicht nachvollziehbar, ob "erkannt" je ausgelöst hatte oder
        gar nicht erst funktioniert)."""
        with self._lock:
            self._fp_activity = {"status": status, "label": label, "ts": time.monotonic()}
            if status == "learned":
                self._fp_last_learned_ts = time.time()
            elif status == "match":
                self._fp_last_match_ts = time.time()

    @property
    def fingerprint_activity_raw(self):
        """Roh (ungefiltert nach Alter) — _build_status() wendet die
        FP_ACTIVITY_TTL-Frische-Prüfung an, siehe dort. Getrennter Getter
        statt die Prüfung hier einzubauen, damit _build_status() (das den
        aktuellen Zeitpunkt kennt) die Entscheidung trifft, nicht dieser
        reine Datenzugriff."""
        with self._lock:
            return dict(self._fp_activity) if self._fp_activity else None

    @property
    def fp_last_learned_ts(self):
        with self._lock:
            return self._fp_last_learned_ts

    @property
    def fp_last_match_ts(self):
        with self._lock:
            return self._fp_last_match_ts

    @property
    def filter_enabled(self) -> bool:
        """Ob die automatische Sprache-Erkennung (VAD/Heuristik/
        Fingerprint) gerade aktiv ist. Bei False spielt der Hauptloop
        einfach weiter, ohne auf Sprache zu reagieren — manuelles
        Umschalten, "ZAPPEN!" und "Zapping-Fehler" funktionieren
        trotzdem weiter, das sind explizite Nutzer-Aktionen."""
        with self._lock:
            return self._filter_enabled

    def set_filter_enabled(self, enabled: bool):
        with self._lock:
            self._filter_enabled = enabled
            self._version += 1
            self._version_cond.notify_all()

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def wait_for_change(self, known_version: int, timeout: float) -> int:
        """Blockiert (im aufrufenden Thread -- bei ThreadingHTTPServer ist
        das ein eigener Thread pro Request, blockiert also keine anderen
        Requests), bis sich die Version ändert oder `timeout` Sekunden
        vergangen sind, je nachdem was zuerst eintritt. Gibt die aktuelle
        Version zurück, damit der Aufrufer (siehe /api/status/wait) beim
        nächsten Long-Poll wieder ab hier weiterwarten kann.

        Grundlage für den Fast-Path im Frontend: normales Intervall-Polling
        bleibt als Sicherheitsnetz bestehen (Bullshitometer/Hörerzahlen
        ändern sich auch ohne Versionssprung), aber ein echter Senderwechsel
        oder News-Break-Übergang muss so nicht erst auf den nächsten
        Poll-Tick warten."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while self._version == known_version:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._version_cond.wait(remaining)
            return self._version

    def request_filter_toggle(self):
        """"Sabbelfilter deaktivieren/aktivieren"-Knopf. Läuft über
        denselben Request-Pattern wie Reload/Skip statt filter_enabled
        direkt aus dem Webserver-Thread umzudrehen — der Hauptloop muss
        beim tatsächlichen Umschalten auch seine Streak-Buchhaltung
        zurücksetzen (sonst könnte nach Wieder-Aktivieren ein uralter,
        längst irrelevanter Sprache-Streak sofort einen Switch auslösen)."""
        with self._lock:
            self._filter_toggle_requested = True

    def pop_filter_toggle_request(self) -> bool:
        with self._lock:
            flag = self._filter_toggle_requested
            self._filter_toggle_requested = False
            return flag

    # ---- Radio/Musiksammlung-Modus (siehe CLAUDE.md, Modus-Fork in
    # radiosabbelnich.py main()) ----

    def request_mode_change(self, mode: str):
        """Vom Mode-Toggle im Web-Interface aufgerufen. Request/pop statt
        direktem Umschalten, weil der eigentliche Übergang `source`
        stoppt/neu verbindet -- das darf ausschließlich der Hauptloop
        machen (siehe CLAUDE.md, "Ein Prozess, zwei Akteure")."""
        with self._lock:
            self._mode_change_requested = mode

    def pop_mode_change_request(self):
        with self._lock:
            req = self._mode_change_requested
            self._mode_change_requested = None
            return req

    def set_mode(self, mode: str):
        """Vom Hauptloop aufgerufen, NACHDEM der tatsächliche Übergang
        (Sender/Track stoppen, ggf. neu verbinden) abgeschlossen ist --
        erst ab hier zeigt das Web-Interface den neuen Modus an."""
        with self._lock:
            self._mode = mode
            self._version += 1
            self._version_cond.notify_all()

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    @property
    def music_library_path(self) -> str:
        """Bewusst NICHT über reload()/einen Cache geführt, sondern bei
        jedem Zugriff frisch von der Platte gelesen (wie z.B.
        GET /api/config/stations) -- der Musik-Tick im Hauptloop läuft
        komplett unabhängig vom pop_reload_request()-Zweig (der nur im
        radio-Zweig geprüft wird), ein gecachter Wert würde dort also nie
        aktualisiert, selbst nach einer Änderung über /config."""
        return settings_store.load()["music_library"]["path"]

    def request_music_play(self, tracks: list = None):
        """tracks: None (Default) => Ordner-Modus, der Hauptloop baut die
        Trackliste selbst aus music_library_path (Grundgerüst,
        unverändert). Eine bereits aufgelöste Liste => Query-Ergebnis
        (Phase 2, siehe music_query.py) -- die eigentliche SQLite-Abfrage
        läuft VOR diesem Aufruf synchron im Webserver-Thread
        (_handle_music_play()), nie hier/im Hauptloop. Ein Query-Play
        ERSETZT eine laufende Wiedergabe sofort, anders als der normale
        Play-Klick, der nur bei Idle wirkt (siehe radiosabbelnich.py)."""
        with self._lock:
            self._music_play_requested = True
            self._music_play_tracks = tracks

    def pop_music_play_request(self):
        """Gibt (requested: bool, tracks: list|None) zurück -- tracks nur
        bedeutsam, wenn requested True ist (None danach heißt dann
        "Ordner-Modus", nicht "kein Request")."""
        with self._lock:
            flag = self._music_play_requested
            tracks = self._music_play_tracks
            self._music_play_requested = False
            self._music_play_tracks = None
            return (flag, tracks) if flag else (False, None)

    def request_music_stop(self):
        with self._lock:
            self._music_stop_requested = True

    def pop_music_stop_request(self) -> bool:
        with self._lock:
            flag = self._music_stop_requested
            self._music_stop_requested = False
            return flag

    def request_music_skip(self, direction: int):
        """direction: +1 (Nächster) oder -1 (Zurück)."""
        with self._lock:
            self._music_skip_requested = direction

    def pop_music_skip_request(self):
        with self._lock:
            direction = self._music_skip_requested
            self._music_skip_requested = None
            return direction

    def set_music_status(self, active: bool, file_name: str = None,
                          index: int = -1, total: int = 0, label: str = None,
                          tags: dict = None):
        """Vom Hauptloop nach jedem Track-Start/-Stop aufgerufen -- fürs
        "Jetzt läuft"-Feld und den Play/Stop-Button-Zustand auf der
        Musiksammlung-Seite (und den now_playing-Override in
        _build_status(), analog zum News-Break-Muster). label:
        "Artist – Titel" bei Query-Wiedergabe (Phase 2, aus
        music_query.py) -- None im Ordner-Modus (Grundgerüst), das
        Frontend zeigt dann weiterhin nur den Dateinamen. tags (seit
        2026-08-15, siehe audio_tags.read_display_tags()): füttert die
        Zwei-Zeilen-Tag-Anzeige, unabhängig von label -- wird für BEIDE
        Modi (Ordner UND Query) einheitlich frisch von der Datei gelesen,
        siehe start_music_track() in radiosabbelnich.py."""
        with self._lock:
            self._music_active = active
            self._music_file = file_name
            self._music_index = index
            self._music_total = total
            self._music_label = label
            self._music_tags = tags
            self._version += 1
            self._version_cond.notify_all()

    @property
    def music_status(self) -> dict:
        with self._lock:
            return {
                "active": self._music_active,
                "file": self._music_file,
                "label": self._music_label,
                "index": self._music_index,
                "total": self._music_total,
                "tags": self._music_tags,
            }


class ImportState:
    """Thread-sicherer Fortschritts-/Ergebnis-Tracker für den Sender-
    Import (station_import.py). Läuft komplett unabhängig von
    SwitcherState/dem Hauptloop — der Import schreibt direkt in
    stations.json über stations_store.bulk_add(), genau wie die übrigen
    Config-Seiten-Aktionen (add/update/delete), und stößt danach wie die
    auch nur ein state.request_reload() an. Ein eigenes Objekt statt in
    SwitcherState, weil das hier rein webui-intern ist (Downloaden/Prüfen
    einer Playlist), der Hauptloop muss davon nichts wissen.

    Der Import selbst läuft in einem Hintergrund-Thread (kann bei einer
    langen Playlist mehrere Minuten dauern) — die Config-Seite pollt
    snapshot() für die Fortschrittsanzeige ("X von Y geprüft")."""

    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._phase = "idle"  # idle | downloading | checking | done | error
        self._checked = 0
        self._total = 0
        self._result = None
        self._error = None

    def start(self) -> bool:
        """Markiert einen Import als laufend. Gibt False zurück, wenn
        schon einer läuft (Aufrufer soll das dann nicht nochmal starten)."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._phase = "downloading"
            self._checked = 0
            self._total = 0
            self._result = None
            self._error = None
            return True

    def set_phase(self, phase: str, total: int = None):
        with self._lock:
            self._phase = phase
            if total is not None:
                self._total = total

    def increment_checked(self):
        with self._lock:
            self._checked += 1

    def finish(self, result: dict):
        with self._lock:
            self._running = False
            self._phase = "done"
            self._result = result

    def fail(self, error: str):
        with self._lock:
            self._running = False
            self._phase = "error"
            self._error = error

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "phase": self._phase,
                "checked": self._checked,
                "total": self._total,
                "result": self._result,
                "error": self._error,
            }


class LibraryScanState:
    """Thread-sicherer Fortschritts-/Ergebnis-Tracker für den Musik-
    Library-Scan (music_scan.py) — Kopie des ImportState-Musters oben,
    gleicher Grund: der Scan kann bei einer großen Sammlung mehrere
    Minuten dauern und läuft deshalb in einem Hintergrund-Thread, ein
    Client pollt snapshot() für den Fortschritt. Phasen: idle (noch nie
    gelaufen) -> walking (Verzeichnisbaum wird eingelesen, total noch
    unbekannt) -> scanning (total bekannt, checked zählt hoch) ->
    done/error."""

    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._phase = "idle"  # idle | walking | scanning | done | error
        self._checked = 0
        self._total = 0
        self._result = None
        self._error = None

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._phase = "walking"
            self._checked = 0
            self._total = 0
            self._result = None
            self._error = None
            return True

    def set_phase(self, phase: str, total: int = None):
        with self._lock:
            self._phase = phase
            if total is not None:
                self._total = total

    def increment_checked(self):
        with self._lock:
            self._checked += 1

    def finish(self, result: dict):
        with self._lock:
            self._running = False
            self._phase = "done"
            self._result = result

    def fail(self, error: str):
        with self._lock:
            self._running = False
            self._phase = "error"
            self._error = error

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "phase": self._phase,
                "checked": self._checked,
                "total": self._total,
                "result": self._result,
                "error": self._error,
            }


def _fetch_listeners(admin_url, user, password, mount, timeout=3):
    """Fragt Icecasts Admin-API nach verbundenen Hörern eines Mountpoints ab.
    Gibt None zurück, wenn nicht konfiguriert oder die Abfrage fehlschlägt
    (Icecast down, falsche Credentials, Netzwerkproblem etc.) — der Aufrufer
    zeigt das dann als "nicht verfügbar" statt einer leeren Liste an."""
    if not (admin_url and user and password and mount):
        return None
    url = f"{admin_url.rstrip('/')}/admin/listclients?mount={mount}"
    req = urllib.request.Request(url)
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError):
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    listeners = []
    for listener in root.iter("listener"):
        listeners.append({
            "ip": listener.findtext("IP") or "?",
            "user_agent": listener.findtext("UserAgent") or "",
            "connected_seconds": int(listener.findtext("Connected") or 0),
        })
    return listeners


# Cache für "Jetzt läuft"-Metadaten: eine eigene ICY-Verbindung pro
# Sender-URL kostet eine kurze zusätzliche Verbindung zum jeweiligen
# Radiosender-Server (unabhängig von der laufenden ffmpeg-Wiedergabe) —
# bei mehreren offenen Browser-Tabs, die alle /api/status pollen, soll
# das nicht bei jedem einzelnen Poll erneut passieren.
# Obergrenze für /api/status/wait (Long-Poll, siehe SwitcherState.wait_for_change()).
# ThreadingHTTPServer startet pro Request einen eigenen Thread -- ein
# hängender Long-Poll blockiert also keine anderen Requests, hält aber
# einen Thread offen, deshalb hier begrenzt statt unbegrenzt zu warten
# (u.a. falls ein Client/Proxy dazwischen die Verbindung sonst nie beendet).
_STATUS_WAIT_TIMEOUT = 25.0

_NOW_PLAYING_TTL = 15.0
_now_playing_cache = {}  # url -> (timestamp, titel_oder_None)
_now_playing_lock = threading.Lock()


def _read_exact(resp, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = resp.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def _fetch_icy_title(url: str, timeout: float = 3) -> str | None:
    """Öffnet kurz eine eigene Verbindung zum Stream mit `Icy-MetaData: 1`
    und liest das erste eingebettete Metadaten-Paket (StreamTitle=...) aus.
    Nicht jeder Sender füllt das mit echten Song/Interpret-Daten (manche
    zeigen nur den Sendernamen oder gar nichts) — das ist serverseitig
    entschieden, nicht etwas, das wir beeinflussen können."""
    req = urllib.request.Request(url, headers={"Icy-MetaData": "1", "User-Agent": "RadioSabbelNich/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            metaint = resp.headers.get("icy-metaint")
            if not metaint:
                return None
            metaint = int(metaint)
            _read_exact(resp, metaint)  # Audio-Bytes bis zum Metadaten-Block verwerfen
            length_byte = _read_exact(resp, 1)
            if not length_byte:
                return None
            meta_len = length_byte[0] * 16
            if meta_len == 0:
                return None
            meta = _read_exact(resp, meta_len).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    match = re.search(r"StreamTitle='([^']*)'", meta)
    title = match.group(1).strip() if match else None
    return title or None


# Für die meisten Sender ist ICY-StreamTitle (siehe oben) die einzige
# realistische Quelle. Für einzelne Sender, deren eigene Website eine
# stabile, öffentlich erreichbare JSON-API für "Jetzt läuft" hat, lohnt
# sich ein gezielter Fallback statt ICY-Branding-Text anzuzeigen — kein
# genereller Website-Scraper (die meisten Sender-Homepages rendern das
# clientseitig per JS, die jeweilige API pro Sender zu reverse-engineeren
# wäre pro Sender eigener, fragiler Wartungsaufwand). Bisher recherchiert
# und bestätigt: R.SH läuft über die "loverad.io"-Plattform (Regiocast),
# stream-service.loverad.io/v4/<slug> liefert artist_name/song_title als
# sauberes JSON. Weitere Sender können hier ergänzt werden, sobald jemand
# deren API-Muster gefunden hat.
_LOVERAD_STREAM_SERVICE_SLUGS = {
    "r-sh": "rsh",
}


def _fetch_loverad_now_playing(slug: str, timeout: float = 3) -> str | None:
    url = f"https://stream-service.loverad.io/v4/{slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "RadioSabbelNich/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    channel = data.get("1") if isinstance(data, dict) else None
    if not channel:
        return None
    artist = (channel.get("artist_name") or "").strip()
    title = (channel.get("song_title") or "").strip()
    if artist and title:
        return f"{artist} - {title}"
    return title or artist or None


def _fetch_now_playing(station: dict, timeout: float = 3):
    if not station:
        return None
    cache_key = station["id"]
    now = time.time()
    with _now_playing_lock:
        cached = _now_playing_cache.get(cache_key)
        if cached and now - cached[0] < _NOW_PLAYING_TTL:
            return cached[1]

    slug = _LOVERAD_STREAM_SERVICE_SLUGS.get(station["id"])
    if slug:
        title = _fetch_loverad_now_playing(slug, timeout=timeout)
    else:
        title = _fetch_icy_title(station["url"], timeout=timeout)

    with _now_playing_lock:
        _now_playing_cache[cache_key] = (now, title)
    return title


# Wie lange ein Fingerprint-Ereignis (Treffer/gelernt) in der Live-Anzeige
# sichtbar bleibt, bevor sie auf "idle" zurückfällt -- ohne das würde ein
# einmaliger Treffer für immer als "🔴 Treffer" stehen bleiben (anders als
# der Bullshitometer/STT-Balken, die bei jedem Analysefenster neu gesetzt
# werden, ist ein Fingerprint-Ereignis ein einmaliger Zeitpunkt, kein
# Dauerwert).
FP_ACTIVITY_TTL = 5.0


def _fresh_fingerprint_activity(state: SwitcherState):
    act = state.fingerprint_activity_raw
    if act is None or (time.monotonic() - act["ts"]) > FP_ACTIVITY_TTL:
        return None
    return {"status": act["status"], "label": act["label"]}


def _build_calibration_status(state: SwitcherState) -> dict:
    """Snapshot der laufenden Kalibrierungs-Session (Teil 1b) plus, sobald
    beide Stufen mindestens einen Sample haben, dem berechneten
    Schwellwert-Vorschlag (siehe stt_filter.suggest_confidence_threshold())
    -- serverseitig berechnet statt in JS dupliziert, damit es nur EINE
    Implementierung der Vorschlagsformel gibt."""
    snap = state.calibration_snapshot()
    if snap is None:
        return {"active": False}
    result = {
        "active": True,
        "language": snap["language"],
        "stage": snap["stage"],
        "speech_samples": snap["speech_samples"],
        "music_samples": snap["music_samples"],
        "suggestion": None,
    }
    if snap["speech_samples"] and snap["music_samples"]:
        speech_conf = [s["confidence"] for s in snap["speech_samples"]]
        music_conf = [s["confidence"] for s in snap["music_samples"]]
        threshold, clean = stt_filter.suggest_confidence_threshold(speech_conf, music_conf)
        result["suggestion"] = {"threshold": threshold, "clean_separation": clean}
    return result


def _music_library_host_path(container_path: str, host_root: str) -> str:
    """Übersetzt den (evtl. verschachtelten) Container-Pfad des aktuell
    gewählten Musiksammlung-Unterordners in den entsprechenden Host-Pfad,
    rein zur Anzeige auf `/musik` -- reines String-Mapping anhand des
    festen Mount-Präfixes (`_BROWSE_ROOTS["music_library"]`), kein
    Dateisystemzugriff. `/app/music_library/...` ist zwar technisch
    korrekt, aber für den Nutzer bedeutungslos (siehe SESSION.md-Eintrag
    2026-08-13) -- der echte Host-Root kommt aus MUSIC_LIBRARY_FOLDER
    (.env), durchgereicht über host_paths (siehe make_handler()-Docstring)."""
    if not host_root or not container_path:
        return None
    container_root = _BROWSE_ROOTS["music_library"]
    if container_path == container_root:
        rel = ""
    elif container_path.startswith(container_root + "/"):
        rel = container_path[len(container_root) + 1:]
    else:
        # Sollte nicht vorkommen (music_library.path wird nur über die
        # Breadcrumb-Auswahl unterhalb dieses Roots gesetzt) -- lieber
        # unverändert anzeigen als eine falsche Übersetzung vortäuschen.
        return container_path
    return host_root.rstrip("/") + ("/" + rel if rel else "")


def _build_status(state: SwitcherState, icecast_cfg: dict, host_paths: dict = None) -> dict:
    current = state.current_station()
    active = state.active_stations
    listeners = _fetch_listeners(
        icecast_cfg.get("admin_url"), icecast_cfg.get("user"),
        icecast_cfg.get("password"), icecast_cfg.get("mount"),
    )
    music = state.music_status
    if state.news_break_active:
        # now_playing (Dateiname-Fallback) tritt seit der Tag-Anzeige
        # (2026-08-15) hinter now_playing_tags zurück -- kein Grund mehr,
        # hier ICY-Metadaten vom pausierten Sender abzufragen ODER den
        # reinen Dateinamen separat anzuzeigen, wenn now_playing_tags
        # (mit Dateiname-Fallback in title, siehe audio_tags.py) dieselbe
        # Information bereits abdeckt.
        now_playing = None
        now_playing_tags = state.news_break_tags
    elif state.mode == "music" and music["active"]:
        # Gleiches Muster wie News-Break direkt oben, nur für den
        # persistenten Musiksammlung-Modus statt eines einzelnen
        # pausierten Radiosenders.
        now_playing = None
        now_playing_tags = music["tags"]
    else:
        now_playing = _fetch_now_playing(current) if current else None
        # Song-Erkennung Phase 2 (song_fingerprint.py, AudD-Cloud-Lookup):
        # dritter Fall, in dem now_playing_tags gesetzt wird (Kommentar oben
        # bezog sich nur auf News-Break/Musiksammlung, bevor es diesen Fall
        # gab). Ergänzt den ICY-now_playing-Text oben, ersetzt ihn nicht.
        # Debug-Zwischenzustände (Nutzer-Wunsch, siehe SESSION.md): solange
        # das Feature aus ist, bleibt es bei None wie bisher (kein
        # Anzeige-Rauschen für alle, die Song-Erkennung nie eingeschaltet
        # haben). Ist es AN, aber noch kein Song erkannt, zeigt "pending"/
        # "paused_no_listeners" (siehe applyStatus() unten) einen sichtbaren
        # Platzhalter statt stillschweigend leer zu bleiben -- sonst nicht
        # unterscheidbar, ob das Feature überhaupt läuft.
        song_recognizer = getattr(state, "song_recognizer", None)
        song_recognition_enabled = state.song_recognition_cfg.get("enabled", False)
        if not (song_recognizer and song_recognition_enabled):
            now_playing_tags = None
        else:
            # Pausiert-Status geht VOR einem evtl. erkannten Song: der
            # Hauptloop aktualisiert _current_song nur bei echten Analyse-
            # Läufen, die bei geschlossenem Hörer-Gate gar nicht mehr
            # stattfinden -- ohne diese Reihenfolge würde ein einmal
            # erkannter Titel beliebig lange stehen bleiben, obwohl gerade
            # niemand mehr zuhört und die Erkennung längst pausiert ist
            # (live beim Rollout beobachtet, siehe SESSION.md).
            gate = getattr(state, "song_listener_gate", None)
            paused = bool(gate) and not gate.has_listeners()
            if paused:
                now_playing_tags = {"title": None, "artist": None, "album": None, "year": None,
                                     "pending": True, "paused_no_listeners": True}
            else:
                recognized = song_recognizer.get_current_song()
                if recognized:
                    now_playing_tags = {"title": recognized["title"], "artist": recognized["artist"],
                                         "album": recognized.get("album"), "year": recognized.get("year")}
                else:
                    now_playing_tags = {"title": None, "artist": None, "album": None, "year": None,
                                         "pending": True, "paused_no_listeners": False}
    return {
        "current_id": current["id"] if current else None,
        "current_name": current["name"] if current else None,
        "now_playing": now_playing,
        "now_playing_tags": now_playing_tags,
        "stations": [{"id": s["id"], "name": s["name"]} for s in active],
        "listeners": listeners,
        "stream_port": icecast_cfg.get("public_port"),
        "stream_ssl_port": icecast_cfg.get("public_ssl_port"),
        "stream_mount": icecast_cfg.get("mount"),
        "stream_url": state.stream_url,
        "filter_enabled": state.filter_enabled,
        "news_break_active": state.news_break_active,
        "stt_status": state.stt_status,
        "speech_probability": state.speech_probability,
        "audio_levels_dbfs": state.audio_levels,
        "stt_probability": state.stt_probability,
        "stt_language": state.stt_language,
        "fingerprint_activity": _fresh_fingerprint_activity(state),
        "fingerprint_last_learned_ts": state.fp_last_learned_ts,
        "fingerprint_last_match_ts": state.fp_last_match_ts,
        "mode": state.mode,
        "music": music,
        "music_library_path": state.music_library_path,
        "music_library_host_path": _music_library_host_path(
            state.music_library_path, (host_paths or {}).get("music_library_folder")),
        "version": state.version,
    }


_PAGE_HTML = """<!doctype html>
<html lang="%%LANG%%">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RadioSabbelNich</title>
<link rel="icon" href="/favicon.ico">
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#1abc9c">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-192.png">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="RadioSabbelNich">
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    max-width: 640px; margin: 2rem auto; padding: 0 1rem;
  }
  h1 { font-size: 1.4rem; margin-bottom: 1rem; }
  h2 { font-size: 1.05rem; margin-top: 2rem; }
  #now-playing-box {
    border-radius: .5rem; background: #eee; overflow: hidden; padding-bottom: .6rem;
  }
  #current {
    font-size: 1.1rem; padding: .75rem 1rem 0 1rem;
  }
  #now-playing, #now-playing-title, #now-playing-subtitle {
    font-size: .9rem; padding: .15rem 1rem 0 1rem; color: #555; min-height: 1.1em;
  }
  #now-playing:empty, #now-playing-title:empty, #now-playing-subtitle:empty { display: none; }
  #address-row { display: flex; gap: .6rem; margin-top: .6rem; }
  #address-row button {
    flex: 1; padding: .5rem; font-size: 1.3rem; border-radius: .5rem;
    border: 1px solid #999; background: none; color: inherit; cursor: pointer;
    display: flex; flex-direction: column; align-items: center; gap: .15rem;
  }
  #address-row button[hidden] { display: none; }
  #address-row button:active { opacity: .7; }
  #address-row .icon-label { font-size: .65rem; color: #888; }
  @media (prefers-color-scheme: dark) {
    #now-playing-box { background: #2a2a2a; }
    #now-playing, #now-playing-title, #now-playing-subtitle { color: #aaa; }
  }
  .modal-overlay {
    position: fixed; inset: 0; background: #0009; display: flex;
    align-items: center; justify-content: center; padding: 1rem; z-index: 10;
  }
  .modal-overlay[hidden] { display: none; }
  .modal-box {
    background: #fff; color: #111; border-radius: .6rem; padding: 1.2rem;
    max-width: 320px; width: 100%; text-align: center; position: relative;
  }
  @media (prefers-color-scheme: dark) {
    .modal-box { background: #222; color: #eee; }
  }
  .modal-box h2 { margin: 0 0 .8rem 0; font-size: 1.05rem; }
  .modal-close {
    position: absolute; top: .5rem; right: .6rem; background: none; border: none;
    font-size: 1.1rem; cursor: pointer; color: inherit; opacity: .7;
  }
  .modal-close:hover { opacity: 1; }
  #qr-code-container {
    background: #fff; padding: .75rem; border-radius: .4rem; display: inline-block;
  }
  #qr-code-container svg { width: 100%; height: auto; display: block; max-width: 260px; }
  #qr-modal-url {
    font-size: .8rem; color: #888; margin-top: .8rem; word-break: break-all;
  }
  #qr-modal-copy {
    margin-top: .8rem; padding: .5rem 1rem; font-size: .9rem; border-radius: .4rem;
    border: 1px solid #999; background: none; color: inherit; cursor: pointer;
  }
  #player { width: 100%; margin-top: 1rem; }
  .meter-wrap { margin-top: 1rem; }
  .meter-label {
    display: flex; justify-content: space-between; font-size: .8rem; color: #888;
    margin-bottom: .3rem;
  }
  .meter-track {
    height: 1rem; border-radius: .6rem; background: #8882; overflow: hidden;
    border: 1px solid #8884;
  }
  .meter-fill {
    height: 100%; width: 0%; background: #2ecc71;
    transition: width .5s ease, background-color .5s ease, opacity .3s ease;
  }
  .meter-wrap.paused .meter-fill { opacity: .3; }
  /* VU-Meter: eigener, viel kürzerer Transition als .meter-fill (0.5s) --
     das Frontend setzt die Breite alle ~100ms neu (siehe animateVuMeter()
     im Skript unten), mit der 0.5s-Transition würde der Balken jedem
     Sprung nur träge hinterherziehen statt ihn sichtbar zu treffen. */
  #vu-meter-fill { transition: width .08s linear, background-color .08s linear; }
  #fp-chip {
    display: inline-block; padding: .25rem .7rem; border-radius: 1rem; font-size: .85rem;
    background: #8882; border: 1px solid #8884; color: inherit;
    transition: background-color .3s ease, border-color .3s ease, color .3s ease;
  }
  #fp-chip.state-match { background: #c0392b; border-color: #c0392b; color: #fff; }
  #fp-chip.state-learned { background: #27ae60; border-color: #27ae60; color: #fff; }
  #fp-history { font-size: .75rem; color: #888; margin-top: .35rem; }
  ul#stations { list-style: none; padding: 0; display: grid; gap: .5rem; }
  ul#stations li button {
    width: 100%; text-align: left; padding: .6rem .8rem; font-size: 1rem;
    border-radius: .4rem; border: 1px solid #999; background: none;
    color: inherit; cursor: pointer;
  }
  ul#stations li button.active { border-color: #2a7a4a; background: #2a7a4a33; font-weight: 600; }
  ul#stations li button:disabled { opacity: .5; cursor: default; }
  table { width: 100%; border-collapse: collapse; margin-top: .5rem; font-size: .9rem; }
  td, th { text-align: left; padding: .3rem .5rem; border-bottom: 1px solid #8884; }
  #meta { color: #888; font-size: .8rem; margin-top: 2rem; }
  img.banner { width: 100%; height: auto; display: block; border-radius: .5rem; margin-bottom: 1rem; }
  .version-tag { text-align: center; font-size: .7rem; color: #888; margin: -.6rem 0 1rem; }
  h1.sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0;
  }
  .action-buttons { display: flex; gap: .5rem; margin-top: .75rem; }
  .action-buttons button {
    flex: 1; padding: .6rem; font-size: .95rem; border-radius: .4rem;
    border: 1px solid #999; background: none; color: inherit; cursor: pointer;
  }
  .action-buttons button:active { opacity: .7; }
  /* Große Touch-Ziele fürs mobile/PWA-Umschalten (Android-Empfehlung:
     mind. 48x48dp) -- deutlich größer als die übrigen Buttons hier, weil
     das der primäre Bedienweg "von unterwegs" sein soll, nicht ein
     Sender aus einer langen Liste antippen. */
  .zap-nav { display: flex; gap: .6rem; margin-top: .9rem; }
  .zap-nav button {
    flex: 1; padding: 1rem .5rem; font-size: 1.1rem; font-weight: 600;
    border-radius: .6rem; border: 1px solid #1abc9c; background: #1abc9c1a;
    color: inherit; cursor: pointer; min-height: 3.2rem;
  }
  .zap-nav button:active { background: #1abc9c33; }
  .zap-nav button:disabled { opacity: .5; cursor: default; }
  .filter-toggle-row { text-align: center; margin-top: .5rem; }
  .filter-toggle-row button {
    padding: .4rem 1rem; font-size: .85rem; border-radius: .4rem;
    border: 1px solid #999; background: none; color: inherit; cursor: pointer;
  }
  .filter-toggle-row button.disabled-state { border-color: #c33; color: #c33; }
  #action-msg { font-size: .85rem; margin-top: .4rem; min-height: 1.2em; color: #888; }
  /* Radio/Musiksammlung-Modus-Umschalter -- deutlich sichtbar direkt unter
     dem Banner/der Versionszeile, auf Player- UND Musiksammlung-Seite
     identisch (siehe CLAUDE.md, Modus-Fork im Hauptloop). */
  .mode-toggle { display: flex; gap: .5rem; margin-bottom: 1.2rem; }
  .mode-toggle button {
    flex: 1; padding: .6rem; font-size: .95rem; font-weight: 600;
    border-radius: .5rem; border: 1px solid #999; background: none;
    color: inherit; cursor: pointer; opacity: .55;
  }
  .mode-toggle button.active {
    border-color: #1abc9c; background: #1abc9c1a; opacity: 1;
  }
  .mode-toggle button:disabled { cursor: default; }
  /* Zahnrad oben rechts statt eines Text-Links am Seitenende -- fest
     positioniert (bleibt beim Scrollen sichtbar), immer über dem Banner. */
  a.gear-link {
    position: fixed; top: .75rem; right: .75rem; z-index: 20;
    width: 2.3rem; height: 2.3rem; border-radius: 50%;
    background: #0008; color: #fff; text-decoration: none;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.3rem; box-shadow: 0 1px 4px #0006;
  }
  a.gear-link:active { background: #000c; }
</style>
</head>
<body>
<a class="gear-link" href="/config" title="⚙ Sender verwalten" data-i18n-title="idx_config_link"
   aria-label="Sender verwalten" data-i18n-aria-label="idx_config_link">⚙</a>
<img class="banner" src="/radiosabbelnich.webp" alt="RadioSabbelNich">
<div class="version-tag">%%VERSION%%</div>
<div id="update-banner" style="display:none; background:#2a5a8a; color:#fff; padding:.5rem 1rem; margin:.5rem auto; max-width:32rem; border-radius:6px; font-size:.85rem; text-align:center;">
  <span id="update-banner-text"></span>
  <a id="update-banner-link" href="#" target="_blank" rel="noopener" style="color:#cfe0f5; text-decoration:underline; margin-left:.4rem;" data-i18n="update_banner_changelog_link">Was ist neu?</a>
</div>
<div class="mode-toggle">
  <button id="mode-radio-btn" data-i18n="mode_radio_btn">📻 Radio</button>
  <button id="mode-music-btn" data-i18n="mode_music_btn">🎵 Player</button>
</div>
<h1 class="sr-only">RadioSabbelNich</h1>
<div id="now-playing-box">
  <div id="current" data-i18n="common_loading">Lade …</div>
  <div id="now-playing"></div>
  <!-- Zwei-Zeilen-Tag-Anzeige (News-Break/Musik-Player, seit 2026-08-15,
       siehe audio_tags.py): Titel & Interpret / Album & Jahr. Bleiben
       für Live-Radio (ICY-now_playing-Fall) immer leer -> per :empty
       ausgeblendet, siehe CSS oben. -->
  <div id="now-playing-title"></div>
  <div id="now-playing-subtitle"></div>
</div>
<div class="zap-nav">
  <button id="btn-prev-station" title="Vorheriger Sender" data-i18n="idx_prev_btn" data-i18n-title="idx_prev_title">⏮ Zurück</button>
  <button id="btn-next-station" title="Nächster Sender" data-i18n="idx_next_btn" data-i18n-title="idx_next_title">Weiter ⏭</button>
</div>
<div id="address-row">
  <button id="btn-qr-vlc" hidden title="QR-Code für die Stream-Adresse (VLC & Co.)" data-i18n-title="idx_qr_vlc_title">
    <span>▶️</span><span class="icon-label" data-i18n="idx_qr_vlc_label">VLC Stream</span>
  </button>
  <button id="btn-qr-phone" title="QR-Code für dieses Web-Interface (zum Öffnen auf dem Handy)" data-i18n-title="idx_qr_phone_title">
    <span>📱</span><span class="icon-label" data-i18n="idx_qr_phone_label">Handy Fernsteuerung</span>
  </button>
</div>
<audio id="player" controls preload="none"></audio>

<div id="qr-modal" class="modal-overlay" hidden>
  <div class="modal-box">
    <button id="qr-modal-close" class="modal-close" aria-label="Schließen" data-i18n-aria-label="idx_qr_modal_close_aria">✕</button>
    <h2 id="qr-modal-title" data-i18n="idx_qr_modal_title">Adresse zum Scannen</h2>
    <div id="qr-code-container"></div>
    <div id="qr-modal-url"></div>
    <button id="qr-modal-copy" data-i18n="idx_qr_modal_copy_btn">📋 Adresse kopieren</button>
  </div>
</div>

<div class="action-buttons">
  <button id="btn-zapping-error" title="Letzten fälschlich erkannten Werbe-Clip aus der Datenbank löschen" data-i18n="idx_zapping_error_btn" data-i18n-title="idx_zapping_error_title">🛑 Zapping-Fehler</button>
  <button id="btn-gesabbel" title="Sofort weiterschalten, weil hier gerade geredet wird" data-i18n="idx_gesabbel_btn" data-i18n-title="idx_gesabbel_title">⚡ ZAPPEN!</button>
  <button id="btn-news-break-skip" title="Andere MP3 während der Nachrichten-Pause (Pause bleibt aktiv)" data-i18n="idx_news_break_skip_btn" data-i18n-title="idx_news_break_skip_title" disabled>⏭ Andere Pause-MP3</button>
</div>
<div class="filter-toggle-row">
  <button id="btn-filter-toggle" title="Automatische Sprache-Erkennung komplett pausieren/wieder anschalten" data-i18n="idx_filter_disable_btn" data-i18n-title="idx_filter_toggle_title">Sabbelfilter deaktivieren</button>
</div>
<div id="action-msg"></div>

<div id="vu-meter-wrap" class="meter-wrap">
  <div class="meter-label">
    <span data-i18n="idx_vu_meter_label">🔊 Pegel</span>
    <span id="vu-meter-pct">–</span>
  </div>
  <div class="meter-track">
    <div id="vu-meter-fill" class="meter-fill"></div>
  </div>
</div>

<div id="bs-meter-wrap" class="meter-wrap">
  <div class="meter-label">
    <span data-i18n="idx_bs_meter_label">🤥 Bullshitometer</span>
    <span id="bs-meter-pct">–</span>
  </div>
  <div class="meter-track">
    <div id="bs-meter-fill" class="meter-fill"></div>
  </div>
</div>

<div id="stt-meter-wrap" class="meter-wrap">
  <div class="meter-label">
    <span data-i18n="idx_stt_meter_label">🗣 STT (Speech-to-Text)-Sprachfilter</span>
    <span id="stt-meter-pct">–</span>
  </div>
  <div class="meter-track">
    <div id="stt-meter-fill" class="meter-fill"></div>
  </div>
</div>

<div id="fp-indicator-wrap" class="meter-wrap">
  <div class="meter-label">
    <span data-i18n="idx_fp_indicator_label">🔎 Fingerprint</span>
  </div>
  <span id="fp-chip" data-i18n="idx_fp_state_idle">⚪ Idle</span>
  <div id="fp-history"></div>
</div>

<h2 data-i18n="idx_stations_heading">Sender</h2>
<ul id="stations"></ul>

<h2 data-i18n="idx_listeners_heading">Hörer</h2>
<div id="listeners" data-i18n="common_loading">Lade …</div>

<div id="meta"></div>

<script>
const LANG = "%%LANG%%";
const I18N = %%I18N_JSON%%;
function t(key, vars) {
  let s = (I18N && I18N[key]) || key;
  if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
  return s;
}
function applyStaticI18n() {
  document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.getAttribute('data-i18n')); });
  document.querySelectorAll('[data-i18n-html]').forEach((el) => { el.innerHTML = t(el.getAttribute('data-i18n-html')); });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.getAttribute('data-i18n-title')); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => { el.placeholder = t(el.getAttribute('data-i18n-placeholder')); });
  document.querySelectorAll('[data-i18n-aria-label]').forEach((el) => { el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria-label'))); });
}
applyStaticI18n();
</script>
<script src="/qrcode.js"></script>
<script>
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

let switching = false;
let playerSrcSet = false;
let currentStreamUrl = '';
let lastVersion = 0;
// Letzter vollständiger /api/status-Stand, fürs optimistische UI-Update bei
// Vor/Zurück (siehe switchRelative()) -- die Zielstation muss VOR der
// Server-Antwort bekannt sein, sonst gäbe es dort nichts zu optimieren.
let lastStatus = null;

// VU-Meter-Animation: der Server liefert pro /api/status-Antwort mehrere
// dBFS-Werte für die letzte Sekunde (siehe VU_SLICES_PER_WINDOW/
// sub_window_dbfs() in radiosabbelnich.py), nicht nur einen -- vuQueue
// hält sie, vuTick() (unten per setInterval alle 100ms gestartet) poppt
// einen nach dem anderen und aktualisiert den Balken. Dadurch bewegt sich
// das Meter ~10x/Sekunde, obwohl neue Daten nur 1x/Sekunde vom Server
// kommen ("flüssig" trotz 1Hz-Produktionsrate im Hauptloop, siehe
// ARCHITECTURE.md/Audio-Pfad). Läuft die Queue leer (Netzwerkhänger), hält
// der zuletzt gesetzte Wert, statt auf 0 zu springen.
let vuQueue = [];

function vuLevelToStyle(dbfs) {
  // -50dBFS (deckt sich mit SILENCE_DBFS_THRESHOLD in radiosabbelnich.py)
  // -> 0%, 0dBFS (Vollausschlag) -> 100%, geclampt.
  const pct = Math.max(0, Math.min(100, Math.round((dbfs + 50) / 50 * 100)));
  // Grün (leise) -> Gelb (~-6dBFS) -> Rot (nahe 0dBFS/Clipping) --
  // Standard-VU-Konvention, umgekehrte Richtung zum Bullshitometer unten
  // (das zeigt grün=Musik -> rot=Sprache, nicht leise/laut).
  const hue = Math.max(0, 120 - pct * 1.2);
  return {pct, color: `hsl(${hue}, 70%, 45%)`};
}

function vuTick() {
  const fill = document.getElementById('vu-meter-fill');
  const pctLabel = document.getElementById('vu-meter-pct');
  if (!fill || vuQueue.length === 0) return;
  const dbfs = vuQueue.shift();
  const style = vuLevelToStyle(dbfs);
  fill.style.width = style.pct + '%';
  fill.style.backgroundColor = style.color;
  pctLabel.textContent = Math.round(dbfs) + ' dBFS';
}

function setSwitching(value) {
  switching = value;
  document.getElementById('btn-prev-station').disabled = value;
  document.getElementById('btn-next-station').disabled = value;
}

// navigator.clipboard.writeText() braucht einen "secure context" (HTTPS
// oder localhost) -- dieses Web-Interface läuft typischerweise per
// Tailscale-Hostname über schlichtes HTTP, dort ist navigator.clipboard
// entweder undefined oder verweigert den Zugriff. Fallback über eine
// unsichtbare Textarea + execCommand('copy'), das funktioniert auch dort.
async function copyToClipboard(text) {
  if (window.isSecureContext && navigator.clipboard) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta);
}

async function refresh() {
  let data;
  try {
    const res = await fetch('/api/status');
    data = await res.json();
  } catch (e) {
    document.getElementById('current').textContent = t('idx_connection_lost');
    return;
  }
  applyStatus(data);
}

// Long-Poll-Fast-Path (siehe /api/status/wait in webui.py): hängt am
// Server, bis sich etwas Wesentliches ändert (Senderwechsel, News-Break-
// Start/Ende, Filter-Toggle), statt stur alle paar Sekunden zu fragen --
// dadurch kommen genau diese Übergänge binnen Millisekunden statt erst
// beim nächsten Poll-Tick an. Das normale Intervall-Polling unten bleibt
// zusätzlich bestehen (Sicherheitsnetz + Bullshitometer/Hörerzahlen, die
// sich auch ohne Versionssprung ändern).
async function longPollLoop() {
  for (;;) {
    try {
      const res = await fetch('/api/status/wait?version=' + lastVersion);
      const data = await res.json();
      applyStatus(data);
    } catch (e) {
      // Verbindung weg (Server-Neustart, Netzwerk-Hänger) -- kurz warten
      // statt den Server/die Konsole mit einer engen Fehlerschleife
      // zuzumüllen, dann erneut versuchen.
      await new Promise(r => setTimeout(r, 2000));
    }
  }
}

function applyStatus(data) {
  lastVersion = data.version || 0;
  lastStatus = data;

  const musicMode = data.mode === 'music';
  document.getElementById('mode-radio-btn').classList.toggle('active', !musicMode);
  document.getElementById('mode-music-btn').classList.toggle('active', musicMode);

  document.getElementById('current').textContent = musicMode
    ? t('idx_music_mode_active')
    : (data.current_name ? t('idx_current_playing', {name: data.current_name}) : t('idx_no_station_active'));
  document.getElementById('now-playing').textContent = data.now_playing ? '🎵 ' + data.now_playing : '';

  // Zwei-Zeilen-Tag-Anzeige (News-Break/Musik-Player, seit 2026-08-15;
  // Radio-Song-Erkennung seit Phase 2) -- now_playing_tags ist nur in
  // diesen Fällen gesetzt (siehe _build_status() in webui.py), sonst
  // null -> beide Zeilen bleiben leer
  // und werden per :empty ausgeblendet (siehe CSS). Zeile 1: "Interpret –
  // Titel" (nur Titel, falls kein Interpret-Tag). Zeile 2: "Album (Jahr)",
  // nur Album bzw. nur Jahr falls jeweils das andere fehlt, komplett leer
  // falls beide fehlen -- keine Platzhalter wie "Album: – / Jahr: –".
  // Song-Erkennung ohne (noch) erkannten Titel liefert stattdessen
  // pending/paused_no_listeners (Debug-Zwischenzustände, siehe
  // _build_status()) -- Zeile 1 zeigt dann einen i18n-Platzhalter statt
  // leer zu bleiben, Zeile 2 bleibt in dem Fall leer (kein Album/Jahr).
  const npTags = data.now_playing_tags;
  let npTitleText = '';
  if (npTags && npTags.title) {
    npTitleText = npTags.artist ? `${npTags.artist} – ${npTags.title}` : npTags.title;
  } else if (npTags && npTags.paused_no_listeners) {
    npTitleText = t('idx_song_paused_no_listeners');
  } else if (npTags && npTags.pending) {
    npTitleText = t('idx_song_pending');
  }
  document.getElementById('now-playing-title').textContent = npTitleText;
  document.getElementById('now-playing-subtitle').textContent =
    (npTags && npTags.title) ? (npTags.album && npTags.year ? `${npTags.album} (${npTags.year})` : (npTags.album || (npTags.year ? String(npTags.year) : ''))) : '';

  const filterBtn = document.getElementById('btn-filter-toggle');
  if (data.filter_enabled === false) {
    filterBtn.textContent = t('idx_filter_enable_btn');
    filterBtn.classList.add('disabled-state');
  } else {
    filterBtn.textContent = t('idx_filter_disable_btn');
    filterBtn.classList.remove('disabled-state');
  }

  // Nur während einer laufenden Nachrichten-Pause sinnvoll -- sonst gibt
  // es keine "andere MP3", die dieser Knopf auswählen könnte.
  document.getElementById('btn-news-break-skip').disabled = !data.news_break_active;

  // VU-Meter: legt hier nur die neue Werte-Charge für die nächste Sekunde
  // in vuQueue, animiert wird unabhängig davon im 100ms-Tick (vuTick()
  // oben). Grau/leer nur, wenn wirklich kein Audio läuft (Musik-Modus ohne
  // aktiven Track) -- anders als das Bullshitometer läuft die Pegelmessung
  // auch während News-Break und bei deaktiviertem Sabbelfilter mit (siehe
  // Kommentar an der Aufrufstelle in radiosabbelnich.py).
  const vuWrap = document.getElementById('vu-meter-wrap');
  if (musicMode && !(data.music && data.music.active)) {
    vuWrap.classList.add('paused');
    vuQueue = [];
    document.getElementById('vu-meter-fill').style.width = '0%';
    document.getElementById('vu-meter-pct').textContent = '–';
  } else {
    vuWrap.classList.remove('paused');
    if (Array.isArray(data.audio_levels_dbfs) && data.audio_levels_dbfs.length) {
      vuQueue = data.audio_levels_dbfs.slice();
    }
  }

  // Bullshitometer: Rohwert der VAD/Heuristik-Klassifikation (VOR der
  // STT-Verknüpfung, siehe radiosabbelnich.py/classify()) als Balken grün
  // (Musik) -> rot (Sprache/"Bullshit"). Während Nachrichten-Pause oder
  // deaktiviertem Sabbelfilter klassifiziert der Hauptloop gar nicht erst
  // -- Balken bleibt dann grau/eingefroren statt einen veralteten Wert
  // als aktuell auszugeben.
  const bsWrap = document.getElementById('bs-meter-wrap');
  const bsFill = document.getElementById('bs-meter-fill');
  const bsPct = document.getElementById('bs-meter-pct');
  if (musicMode) {
    bsPct.textContent = t('idx_music_mode_short');
    bsWrap.classList.add('paused');
  } else if (data.news_break_active) {
    bsPct.textContent = t('idx_news_break');
    bsWrap.classList.add('paused');
  } else if (data.filter_enabled === false) {
    bsPct.textContent = t('idx_filter_off');
    bsWrap.classList.add('paused');
  } else {
    const pct = Math.round((data.speech_probability || 0) * 100);
    bsPct.textContent = pct + '%';
    bsFill.style.width = pct + '%';
    // Grün (Hue 120) bei 0% bis Rot (Hue 0) bei 100%, linear.
    const hue = Math.max(0, 120 - pct * 1.2);
    bsFill.style.backgroundColor = `hsl(${hue}, 70%, 45%)`;
    bsWrap.classList.remove('paused');
  }

  // STT-Balken: gleiches Muster wie das Bullshitometer, aber ein
  // zusätzlicher eingefrorener Zustand -- der STT-Filter kann unabhängig
  // vom Sabbelfilter aus sein oder (noch) keinen frischen Befund haben
  // (stt_probability dann null, siehe stt_filter.live_confidence()).
  const sttWrap = document.getElementById('stt-meter-wrap');
  const sttFill = document.getElementById('stt-meter-fill');
  const sttPct = document.getElementById('stt-meter-pct');
  if (musicMode) {
    sttPct.textContent = t('idx_music_mode_short');
    sttWrap.classList.add('paused');
  } else if (data.news_break_active) {
    sttPct.textContent = t('idx_news_break');
    sttWrap.classList.add('paused');
  } else if (data.filter_enabled === false) {
    sttPct.textContent = t('idx_filter_off');
    sttWrap.classList.add('paused');
  } else if (data.stt_probability === null || data.stt_probability === undefined) {
    sttPct.textContent = t('idx_stt_meter_off');
    sttWrap.classList.add('paused');
  } else {
    const pct = Math.round(data.stt_probability * 100);
    // Sprachkürzel neben dem Prozentwert (nur mit Teil 1/mehrsprachiger STT
    // aussagekräftig -- ohne konfigurierte Zusatzsprachen ist es immer "de",
    // dann trotzdem angezeigt statt versteckt, schadet nicht).
    sttPct.textContent = data.stt_language ? `${pct}% (${data.stt_language})` : pct + '%';
    sttFill.style.width = pct + '%';
    const hue = Math.max(0, 120 - pct * 1.2);
    sttFill.style.backgroundColor = `hsl(${hue}, 70%, 45%)`;
    sttWrap.classList.remove('paused');
  }

  // Fingerprint-Chip: kein Dauerwert wie die beiden Balken oben, sondern
  // ein diskretes Ereignis (Treffer/gelernter Clip), das der Server nur
  // FP_ACTIVITY_TTL Sekunden lang meldet (siehe webui.py) -- danach liefert
  // fingerprint_activity von selbst wieder null, die Anzeige fällt auf
  // "idle" zurück, ohne dass das Frontend eine eigene Altersprüfung braucht.
  const fpChip = document.getElementById('fp-chip');
  const fpAct = data.fingerprint_activity;
  fpChip.classList.remove('state-match', 'state-learned');
  if (fpAct && fpAct.status === 'match') {
    fpChip.textContent = t('idx_fp_state_match', {label: fpAct.label || t('common_unknown')});
    fpChip.classList.add('state-match');
  } else if (fpAct && fpAct.status === 'learned') {
    fpChip.textContent = t('idx_fp_state_learned');
    fpChip.classList.add('state-learned');
  } else {
    fpChip.textContent = t('idx_fp_state_idle');
  }

  // Historie unter dem Chip: anders als fpAct oben (blinkt nur kurz auf)
  // bleiben fingerprint_last_learned_ts/fingerprint_last_match_ts stehen,
  // bis der Prozess neu startet -- Nutzer-Feedback: "Fingerprint zeigt nur
  // Idle und gelernt, ich hab erkannt noch nie gesehen", ohne Historie war
  // nicht nachvollziehbar, ob "erkannt" je ausgelöst hatte. Epochen-
  // Sekunden vom Server, lokal per toLocaleTimeString() formatiert (gleiches
  // Muster wie beim "zuletzt aktualisiert"-Zeitstempel unten).
  const fmtTime = (epochSeconds) => {
    if (!epochSeconds) return t('idx_fp_never');
    const s = new Date(epochSeconds * 1000).toLocaleTimeString(LANG === 'de' ? 'de-DE' : 'en-GB');
    // "Uhr"-Suffix ist eine deutsche Zeitangaben-Konvention, gehört an die
    // Zeit selbst (nicht ins Template, sonst stünde "nie Uhr" da, wenn das
    // Ereignis noch nie vorkam -- siehe idx_fp_never).
    return LANG === 'de' ? s + ' Uhr' : s;
  };
  document.getElementById('fp-history').textContent =
    t('idx_fp_last_learned', {time: fmtTime(data.fingerprint_last_learned_ts)}) + ' · ' +
    t('idx_fp_last_match', {time: fmtTime(data.fingerprint_last_match_ts)});

  // Player-Quelle nur einmal setzen, nicht bei jedem Poll -> sonst würde
  // die Wiedergabe alle 5s neu starten/stottern. Die Stream-Adresse ändert
  // sich ebenso wenig -> gleich mit erledigen.
  if (!playerSrcSet && data.stream_port && data.stream_mount) {
    // Eingebetteter Player nutzt IMMER die Adresse, über die der Browser
    // diese Seite selbst erreicht -- die ist garantiert erreichbar. Die
    // konfigurierbare stream_url (siehe /config) ist nur für die Anzeige/
    // zum Kopieren gedacht (z.B. eine andere öffentliche Adresse für VLC)
    // und könnte falsch/nicht erreichbar sein, ohne dass das den Player
    // hier mitreißen soll.
    //
    // Icecast hört HTTP und HTTPS auf ZWEI VERSCHIEDENEN Ports (siehe
    // ICECAST_PORT/ICECAST_SSL_PORT) -- location.protocol einfach mit dem
    // immer gleichen stream_port zu kombinieren ergäbe bei https://-Aufruf
    // dieser Seite ein ungültiges "https://host:8000/...", das niemand
    // beantwortet (Icecasts Port 8000 spricht kein TLS). Deshalb Schema
    // und Port als PAAR wählen: nur wenn die Seite selbst per HTTPS läuft
    // UND ein SSL-Port bekannt ist, beide zusammen nehmen -- sonst bleibt
    // es bei HTTP auf dem normalen Port, auch wenn die Seite selbst https
    // ist (ein http://-Stream in einem https://-eingebetteten Player kann
    // der Browser als "mixed content" blocken, aber ein sicher falscher
    // Port wäre garantiert kaputt, das hier nur möglicherweise).
    const pageIsHttps = location.protocol === 'https:';
    const useSsl = pageIsHttps && data.stream_ssl_port;
    const streamScheme = useSsl ? 'https:' : 'http:';
    const streamPort = useSsl ? data.stream_ssl_port : data.stream_port;
    const autoUrl = streamScheme + '//' + location.hostname + ':' + streamPort + data.stream_mount;
    const player = document.getElementById('player');
    player.src = autoUrl;
    playerSrcSet = true;
    currentStreamUrl = data.stream_url || autoUrl;
    document.getElementById('btn-qr-vlc').hidden = false;
  }

  const list = document.getElementById('stations');
  list.innerHTML = '';
  for (const s of data.stations) {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.textContent = s.name;
    btn.dataset.id = s.id;
    if (s.id === data.current_id) btn.classList.add('active');
    btn.disabled = switching || musicMode;
    btn.addEventListener('click', () => switchStation(s.id));
    li.appendChild(btn);
    list.appendChild(li);
  }
  document.getElementById('btn-prev-station').disabled = switching || musicMode || data.stations.length === 0;
  document.getElementById('btn-next-station').disabled = switching || musicMode || data.stations.length === 0;

  const listenersEl = document.getElementById('listeners');
  if (data.listeners === null) {
    listenersEl.textContent = t('idx_listeners_unavailable');
  } else if (data.listeners.length === 0) {
    listenersEl.textContent = t('idx_listeners_none');
  } else {
    let out = `<table><tr><th>${t('idx_listeners_col_ip')}</th><th>${t('idx_listeners_col_since')}</th><th>${t('idx_listeners_col_client')}</th></tr>`;
    for (const l of data.listeners) {
      const mins = Math.floor(l.connected_seconds / 60);
      const secs = l.connected_seconds % 60;
      out += `<tr><td>${esc(l.ip)}</td><td>${mins}m ${secs}s</td>` +
             `<td>${esc((l.user_agent || '').slice(0, 40))}</td></tr>`;
    }
    out += '</table>';
    listenersEl.innerHTML = out;
  }

  document.getElementById('meta').textContent =
    t('idx_meta_updated', {time: new Date().toLocaleTimeString(LANG === 'de' ? 'de-DE' : 'en-GB')});
}

// Zeigt einen Sender-Wechsel sofort im UI an, ohne auf die Server-Antwort
// oder den nächsten Long-Poll/Intervall-Tick zu warten -- Sekunden-Verzug
// wäre bei "von unterwegs zappen" spürbar unangenehm. Wird per
// state.set_current() im Hauptloop ohnehin bald bestätigt (oder per
// nachfolgendem refresh() unten); trifft die optimistische Annahme mal
// nicht zu (z.B. Sender inzwischen deaktiviert), korrigiert sich das beim
// nächsten Status-Update von selbst.
function applyOptimistic(station) {
  if (!station) return;
  document.getElementById('current').textContent = t('idx_current_playing', {name: station.name});
  document.getElementById('now-playing').textContent = '';
  document.getElementById('now-playing-title').textContent = '';
  document.getElementById('now-playing-subtitle').textContent = '';
  if (lastStatus) lastStatus.current_id = station.id;
  document.querySelectorAll('#stations li button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.id === station.id);
  });
}

async function switchStation(id) {
  if (switching) return;
  setSwitching(true);
  const station = lastStatus && lastStatus.stations ? lastStatus.stations.find((s) => s.id === id) : null;
  applyOptimistic(station);
  try {
    await fetch('/api/switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id}),
    });
  } finally {
    setSwitching(false);
  }
  refresh();
}

// Ermittelt den Nachbar-Sender rein lokal aus dem letzten /api/status-Stand
// -- für die optimistische Anzeige. Die tatsächliche Umschaltung passiert
// serverseitig (/api/switch/next|prev, siehe webui.py), dieselbe Reihenfolge
// (state.active_stations, alphabetisch) wird dort unabhängig neu ermittelt.
function computeNeighbor(direction) {
  if (!lastStatus || !lastStatus.stations || lastStatus.stations.length === 0) return null;
  const ids = lastStatus.stations.map((s) => s.id);
  let idx = ids.indexOf(lastStatus.current_id);
  if (idx === -1) idx = direction > 0 ? -1 : 0;
  const newIdx = ((idx + direction) % ids.length + ids.length) % ids.length;
  return lastStatus.stations[newIdx];
}

async function switchRelative(direction) {
  if (switching) return;
  setSwitching(true);
  applyOptimistic(computeNeighbor(direction));
  try {
    const res = await fetch(direction > 0 ? '/api/switch/next' : '/api/switch/prev', {method: 'POST'});
    const data = await res.json();
    if (!data.ok) setActionMsg(t('common_error', {msg: data.error || t('common_unknown')}));
  } catch (e) {
    setActionMsg(t('common_error', {msg: e.message}));
  } finally {
    setSwitching(false);
  }
  refresh();
}

document.getElementById('btn-prev-station').addEventListener('click', () => switchRelative(-1));
document.getElementById('btn-next-station').addEventListener('click', () => switchRelative(1));

function setActionMsg(text) {
  const el = document.getElementById('action-msg');
  el.textContent = text;
  setTimeout(() => { if (el.textContent === text) el.textContent = ''; }, 5000);
}

// Zwei Icon-Buttons teilen sich denselben Modal-Dialog, kodieren aber
// unterschiedliche Adressen (Stream-URL fürs VLC-Icon, die Adresse dieser
// Seite selbst fürs Handy-Icon) -- qrModalUrl merkt sich, welche der
// beiden gerade angezeigt wird, damit der Kopieren-Knopf im Modal weiß,
// was er kopieren soll (statt hart an currentStreamUrl gebunden zu sein).
let qrModalUrl = '';

function closeQrModal() {
  document.getElementById('qr-modal').hidden = true;
}

function openQrModal(url, title) {
  if (!url || typeof qrcode !== 'function') return;
  qrModalUrl = url;
  const qr = qrcode(0, 'M');
  qr.addData(url);
  qr.make();
  document.getElementById('qr-code-container').innerHTML = qr.createSvgTag({cellSize: 5, margin: 4});
  document.getElementById('qr-modal-title').textContent = title;
  document.getElementById('qr-modal-url').textContent = url;
  document.getElementById('qr-modal').hidden = false;
}

// Nur EIN Icecast-Mount für die gesamte Rotation (siehe CLAUDE.md,
// "IcecastOutput besteht über Senderwechsel hinweg") -- der QR-Code
// kodiert also immer dieselbe currentStreamUrl, unabhängig davon, welcher
// Sender gerade läuft. Kein Bezug zur Senderliste nötig.
document.getElementById('btn-qr-vlc').addEventListener('click', () => {
  openQrModal(currentStreamUrl, t('idx_qr_vlc_modal_title'));
});

// location.origin statt einer fest einprogrammierten Adresse -- dieselbe
// Begründung wie beim eingebetteten Player oben: garantiert die Adresse,
// über die DIESER Browser die Seite gerade selbst erreicht, unabhängig von
// Hostname/Port des jeweiligen Deployments.
document.getElementById('btn-qr-phone').addEventListener('click', () => {
  openQrModal(location.origin + '/', t('idx_qr_phone_modal_title'));
});

document.getElementById('qr-modal-close').addEventListener('click', closeQrModal);
document.getElementById('qr-modal').addEventListener('click', (ev) => {
  if (ev.target.id === 'qr-modal') closeQrModal();
});
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') closeQrModal();
});
document.getElementById('qr-modal-copy').addEventListener('click', async () => {
  try {
    await copyToClipboard(qrModalUrl);
    setActionMsg(t('idx_address_copied'));
  } catch (e) {
    setActionMsg(t('idx_copy_failed', {msg: e.message}));
  }
});

document.getElementById('btn-zapping-error').addEventListener('click', async () => {
  try {
    const res = await fetch('/api/fingerprint/undo', {method: 'POST'});
    const data = await res.json();
    if (data.ok) {
      let msg = t('idx_clip_deleted') + (data.label ? (': ' + data.label) : '');
      if (data.switched_back_to) msg += t('idx_switched_back_to', {name: data.switched_back_to});
      setActionMsg(msg);
      setTimeout(refresh, 1000);
    } else {
      setActionMsg('– ' + data.error);
    }
  } catch (e) {
    setActionMsg(t('common_error', {msg: e.message}));
  }
});

document.getElementById('btn-gesabbel').addEventListener('click', async () => {
  try {
    await fetch('/api/skip', {method: 'POST'});
    setActionMsg(t('idx_zap_switching'));
    setTimeout(refresh, 1500);
  } catch (e) {
    setActionMsg(t('common_error', {msg: e.message}));
  }
});

document.getElementById('btn-news-break-skip').addEventListener('click', async () => {
  try {
    await fetch('/api/news-break/skip', {method: 'POST'});
    setActionMsg(t('idx_news_break_skip_switching'));
    setTimeout(refresh, 1500);
  } catch (e) {
    setActionMsg(t('common_error', {msg: e.message}));
  }
});

document.getElementById('btn-filter-toggle').addEventListener('click', async () => {
  try {
    await fetch('/api/filter/toggle', {method: 'POST'});
    setActionMsg(t('idx_filter_switching'));
    setTimeout(refresh, 1200);
  } catch (e) {
    setActionMsg(t('common_error', {msg: e.message}));
  }
});

// Modus-Umschalter -- der eigentliche Übergang (Sender/Track stoppen, ggf.
// neu verbinden) läuft im Hauptloop und braucht einen Moment; die Buttons
// spiegeln den bestätigten Zustand erst über den nächsten /api/status-
// Poll/Long-Poll wider, kein optimistisches Umschalten hier (anders als
// beim Senderwechsel: ein falsch angenommener Modus wäre hier deutlich
// sichtbarer als ein falscher Sendername).
//
// redirectTo: die Musiksammlung-Steuerung (Play/Stop/Zurück/Nächster) lebt
// nur auf /musik, nicht hier -- ein Wechsel dorthin ohne Weiterleitung würde
// bloß den Radio-Sender pausieren, ohne dass auf dieser Seite irgendwo eine
// Bedienmöglichkeit für den neuen Modus sichtbar wird (wirkte bisher wie
// "Button ohne Funktion", siehe SESSION.md).
async function setMode(mode, redirectTo) {
  try {
    await fetch('/api/mode', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode}),
    });
    if (redirectTo) {
      location.href = redirectTo;
      return;
    }
    setActionMsg(t('idx_mode_switching'));
    setTimeout(refresh, 1200);
  } catch (e) {
    setActionMsg(t('common_error', {msg: e.message}));
  }
}
document.getElementById('mode-radio-btn').addEventListener('click', () => setMode('radio'));
document.getElementById('mode-music-btn').addEventListener('click', () => setMode('music', '/musik'));

refresh();
longPollLoop();
// Einmaliger Abruf reicht -- update_check ändert sich höchstens 1x/Tag
// (siehe update_check.py), kein Polling nötig wie beim übrigen Status.
(async () => {
  const d = await fetch('/api/update_check').then(r => r.json()).catch(() => null);
  if (!d || !d.update_available) return;
  document.getElementById('update-banner-text').textContent =
    t('update_banner_text', {version: d.last_known_remote_version || ''});
  document.getElementById('update-banner-link').href = d.changelog_url;
  document.getElementById('update-banner').style.display = 'block';
})();
// Sicherheitsnetz zusätzlich zum Long-Poll oben: Bullshitometer/Hörerzahlen
// ändern sich auch ohne Versionssprung (kein request/pop-Ereignis), und
// falls der Long-Poll je hängen bleibt (Proxy/Browser-Eigenheiten), holt
// das hier trotzdem regelmäßig den aktuellen Stand. 1000ms statt vormals
// 3000ms -- passt zur tatsächlichen 1Hz-Produktionsrate der Analysewerte
// im Hauptloop (WINDOW_SECONDS), schnelleres Pollen brächte für die Werte
// selbst nichts, macht die Oberfläche insgesamt aber spürbar reaktionsschneller.
setInterval(refresh, 1000);
// VU-Meter-Animation (siehe vuTick()/vuQueue oben), unabhängig vom
// Status-Polling -- läuft die Queue mal leer, tut der Tick einfach nichts.
setInterval(vuTick, 100);

// PWA: Service Worker fürs Offline-Fallback der Oberflächen-Hülle (siehe
// sw.js) -- Registrierung selbst ist Voraussetzung für "Zum Home-Bildschirm
// hinzufügen" unter Chrome/Android, nicht nur fürs Offline-Verhalten.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((e) => {
      console.warn('Service-Worker-Registrierung fehlgeschlagen:', e);
    });
  });
}
</script>
</body>
</html>
"""


_MUSIC_PAGE_HTML = """<!doctype html>
<html lang="%%LANG%%">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RadioSabbelNich — Player</title>
<link rel="icon" href="/favicon.ico">
<meta name="theme-color" content="#1abc9c">
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    max-width: 640px; margin: 2rem auto; padding: 0 1rem;
  }
  img.banner { width: 100%; height: auto; display: block; border-radius: .5rem; margin-bottom: 1rem; }
  .version-tag { text-align: center; font-size: .7rem; color: #888; margin: -.6rem 0 1rem; }
  .mode-toggle { display: flex; gap: .5rem; margin-bottom: 1.2rem; }
  .mode-toggle button {
    flex: 1; padding: .6rem; font-size: .95rem; font-weight: 600;
    border-radius: .5rem; border: 1px solid #999; background: none;
    color: inherit; cursor: pointer; opacity: .55;
  }
  .mode-toggle button.active {
    border-color: #1abc9c; background: #1abc9c1a; opacity: 1;
  }
  h1 {
    font-size: 1.3rem; text-align: center; padding: .8rem; border-radius: .5rem;
    background: #1abc9c1a; border: 1px solid #1abc9c;
  }
  #root-path-box {
    padding: .75rem 1rem; border-radius: .5rem; background: #eee; margin-top: 1rem;
    font-size: .85rem; word-break: break-all;
  }
  #root-path-box a { font-size: .8rem; }
  @media (prefers-color-scheme: dark) { #root-path-box { background: #2a2a2a; } }
  .category-group { margin-top: 1.2rem; }
  .category-group-heading {
    font-size: .75rem; text-transform: uppercase; letter-spacing: .03em;
    color: #888; margin-bottom: .35rem;
  }
  .category-row { display: flex; flex-wrap: wrap; gap: .5rem; }
  /* Seit Phase 3 (music_bpm.py) sind alle sechs Kategorie-/Favoriten-
     Buttons echte Query-Buttons (music_query.py), keine reinen
     Platzhalter mehr -- eine "disabled/ausgegraut"-Variante gibt es in
     dieser Reihe deshalb nicht mehr, gleiche Akzentfarbe wie Play/Stop
     und Zurück/Nächster. */
  .category-row button {
    flex: 1 1 28%; padding: .6rem .4rem; font-size: .85rem; border-radius: .4rem;
    border: 1px solid #1abc9c; background: #1abc9c1a; color: inherit; cursor: pointer;
  }
  .category-row button:active { background: #1abc9c33; }
  #player { display: none; }
  .change-path-btn {
    display: inline-block; margin-top: .5rem; padding: .45rem .9rem; font-size: .85rem;
    border-radius: .4rem; border: 1px solid #1abc9c; background: #1abc9c1a;
    color: inherit; text-decoration: none; cursor: pointer;
  }
  .change-path-btn:active { background: #1abc9c33; }
  .play-stop-row { text-align: center; margin-top: 1.5rem; }
  #btn-play-stop {
    width: 6.5rem; height: 6.5rem; border-radius: 50%; font-size: 2.4rem;
    border: 3px solid #1abc9c; background: #1abc9c1a; color: inherit; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
  }
  #btn-play-stop:active { background: #1abc9c33; }
  #btn-play-stop:disabled { opacity: .4; cursor: default; }
  .zap-nav { display: flex; gap: .6rem; margin-top: 1.2rem; }
  .zap-nav button {
    flex: 1; padding: 1rem .5rem; font-size: 1.1rem; font-weight: 600;
    border-radius: .6rem; border: 1px solid #1abc9c; background: #1abc9c1a;
    color: inherit; cursor: pointer; min-height: 3.2rem;
  }
  .zap-nav button:active { background: #1abc9c33; }
  .zap-nav button:disabled { opacity: .5; cursor: default; }
  #track-status { text-align: center; font-size: .9rem; color: #888; margin-top: 1rem; min-height: 1.2em; }
  #track-title { text-align: center; font-size: 1rem; font-weight: 600; margin-top: .3rem; min-height: 1.2em; }
  #track-title:empty { display: none; }
  #track-subtitle { text-align: center; font-size: .85rem; color: #888; min-height: 1.1em; }
  #track-subtitle:empty { display: none; }
  #action-msg { font-size: .85rem; margin-top: .4rem; min-height: 1.2em; color: #888; text-align: center; }
  a.config-link { display: inline-block; margin-top: 1.5rem; font-size: .9rem; }
  /* Gleiches Bar-Meter-Muster wie auf "/" (dort ausführlich, siehe
     Kommentare dort) -- hier nur das VU-Meter, kein Bullshitometer/STT
     (keine Sprache-Klassifikation im Musik-Modus). */
  .meter-wrap { margin-top: 1.2rem; }
  .meter-label {
    display: flex; justify-content: space-between; font-size: .8rem; color: #888;
    margin-bottom: .3rem;
  }
  .meter-track {
    height: 1rem; border-radius: .6rem; background: #8882; overflow: hidden;
    border: 1px solid #8884;
  }
  .meter-fill {
    height: 100%; width: 0%; background: #2ecc71;
    transition: width .5s ease, background-color .5s ease, opacity .3s ease;
  }
  .meter-wrap.paused .meter-fill { opacity: .3; }
  #vu-meter-fill { transition: width .08s linear, background-color .08s linear; }
</style>
</head>
<body>
<div class="version-tag">%%VERSION%%</div>
<div id="update-banner" style="display:none; background:#2a5a8a; color:#fff; padding:.5rem 1rem; margin:.5rem auto; max-width:32rem; border-radius:6px; font-size:.85rem; text-align:center;">
  <span id="update-banner-text"></span>
  <a id="update-banner-link" href="#" target="_blank" rel="noopener" style="color:#cfe0f5; text-decoration:underline; margin-left:.4rem;" data-i18n="update_banner_changelog_link">Was ist neu?</a>
</div>
<div class="mode-toggle">
  <button id="mode-radio-btn" data-i18n="mode_radio_btn">📻 Radio</button>
  <button id="mode-music-btn" data-i18n="mode_music_btn">🎵 Player</button>
</div>
<h1 data-i18n="music_heading">🎵 Player</h1>

<div id="root-path-box">
  <div data-i18n="music_root_label">Musik-Ordner:</div>
  <strong id="root-path">–</strong><br>
  <a class="change-path-btn" href="/config" data-i18n="music_root_change_link">Pfad ändern</a>
</div>

<!-- Kein <audio controls> hier -- der eine große Play/Stop-Button unten ist
     der einzige sichtbare Play-Knopf (siehe applyStatus(): das Element hier
     folgt automatisch dem musicActive-Status, statt ein zweites, eigenes
     Play/Pause zu zeigen, das dem großen Button in die Quere kommen würde). -->
<audio id="player" preload="none"></audio>

<!-- Kategorie-/Favoriten-Buttons (Phase 2, music_query.py): rock/klassik
     Queen/Pavarotti/rock/klassik lösen eine echte Query gegen
     music_library.db aus (Artist-/Genre-Teilstring-Match, siehe
     music_query.py). schnell/langsam seit Phase 3 (music_bpm.py) über
     die "bpm"-Spalte -- Schwellwerte in music_query.py
     (FAST_BPM_MIN/SLOW_BPM_MAX). -->
<div class="category-group">
  <div class="category-group-heading" data-i18n="music_categories_heading">Kategorien</div>
  <div class="category-row">
    <button class="music-query-btn" data-query-type="tempo" data-query-value="fast" disabled>schnell</button>
    <button class="music-query-btn" data-query-type="tempo" data-query-value="slow" disabled>langsam</button>
    <button class="music-query-btn" data-query-type="genre" data-query-value="rock" disabled>rock</button>
    <button class="music-query-btn" data-query-type="genre" data-query-value="klassik" disabled>klassik</button>
  </div>
</div>
<div class="category-group">
  <div class="category-group-heading" data-i18n="music_favorites_heading">Favoriten</div>
  <div class="category-row">
    <button class="music-query-btn" data-query-type="artist" data-query-value="Queen" disabled>Queen</button>
    <button class="music-query-btn" data-query-type="artist" data-query-value="Pavarotti" disabled>Pavarotti</button>
  </div>
</div>

<div class="play-stop-row">
  <!-- disabled per Default: verhindert einen Klick, bevor der erste
       refresh() (siehe Skript unten) player.src gesetzt hat -- sonst
       wirft player.play() ein NotSupportedError (leere Quelle), live
       reproduziert bei einem schnellen Klick direkt nach der Navigation
       von "/" auf "/musik" (siehe SESSION.md). applyStatus() aktiviert
       den Knopf wieder, NACHDEM player.src gesetzt ist. -->
  <button id="btn-play-stop" title="Play" data-i18n-title="music_play_title" disabled>▶</button>
</div>
<div class="zap-nav">
  <button id="btn-prev-track" title="Vorheriger Track" data-i18n-title="music_prev_title" disabled>⏮</button>
  <button id="btn-next-track" title="Nächster Track" data-i18n-title="music_next_title" disabled>⏭</button>
</div>
<div id="track-status"></div>
<!-- Zwei-Zeilen-Tag-Anzeige (seit 2026-08-15, siehe audio_tags.py):
     Titel & Interpret / Album & Jahr, format-übergreifend via mutagen.
     Bleibt leer -> per :empty ausgeblendet, wenn nichts läuft. -->
<div id="track-title"></div>
<div id="track-subtitle"></div>

<div id="vu-meter-wrap" class="meter-wrap">
  <div class="meter-label">
    <span data-i18n="idx_vu_meter_label">🔊 Pegel</span>
    <span id="vu-meter-pct">–</span>
  </div>
  <div class="meter-track">
    <div id="vu-meter-fill" class="meter-fill"></div>
  </div>
</div>

<div id="action-msg"></div>

<a class="config-link" href="/">← <span data-i18n="music_back_link">zurück zum Radio-Player</span></a>

<script>
const LANG = "%%LANG%%";
const I18N = %%I18N_JSON%%;
function t(key, vars) {
  let s = (I18N && I18N[key]) || key;
  if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
  return s;
}
function applyStaticI18n() {
  document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.getAttribute('data-i18n')); });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.getAttribute('data-i18n-title')); });
}
applyStaticI18n();

let lastVersion = 0;
let musicActive = false;
let playerSrcSet = false;

// VU-Meter-Animation: identisches Muster wie auf "/" (dort ausführlich
// begründet) -- eigene Kopie hier, weil diese Seite ein komplett
// separates Template ist, kein gemeinsamer Code mit "/".
let vuQueue = [];

function vuLevelToStyle(dbfs) {
  const pct = Math.max(0, Math.min(100, Math.round((dbfs + 50) / 50 * 100)));
  const hue = Math.max(0, 120 - pct * 1.2);
  return {pct, color: `hsl(${hue}, 70%, 45%)`};
}

function vuTick() {
  const fill = document.getElementById('vu-meter-fill');
  const pctLabel = document.getElementById('vu-meter-pct');
  if (!fill || vuQueue.length === 0) return;
  const dbfs = vuQueue.shift();
  const style = vuLevelToStyle(dbfs);
  fill.style.width = style.pct + '%';
  fill.style.backgroundColor = style.color;
  pctLabel.textContent = Math.round(dbfs) + ' dBFS';
}

function setActionMsg(text, ms) {
  const el = document.getElementById('action-msg');
  el.textContent = text;
  setTimeout(() => { if (el.textContent === text) el.textContent = ''; }, ms || 5000);
}

// Diagnose für "Anzeige sagt 'läuft', aber kein Ton" (siehe SESSION.md):
// bisher wurden sowohl play()-Ablehnungen als auch Lade-/Netzwerkfehler
// des <audio>-Elements lautlos verschluckt (.catch(() => {})) -- dadurch
// war von außen nicht unterscheidbar, ob der Browser Autoplay blockiert,
// die Stream-URL/das TLS-Zertifikat nicht geladen werden konnte, oder
// etwas anderes schiefging. Beides jetzt sichtbar (Action-Feld + Konsole),
// 15s statt der üblichen 5s, weil das Melden eines Fehlers länger dauert
// als das Lesen eines normalen Kurzhinweises.
document.getElementById('player').addEventListener('error', () => {
  const err = document.getElementById('player').error;
  const msg = 'Audio-Ladefehler' + (err ? ' (Code ' + err.code + ')' : '');
  console.error(msg, err, 'src=', document.getElementById('player').src);
  setActionMsg(msg, 15000);
});

async function refresh() {
  let data;
  try {
    const res = await fetch('/api/status');
    data = await res.json();
  } catch (e) {
    return;
  }
  applyStatus(data);
}

// Gleiches Long-Poll-Fast-Path-Muster wie die Player-Seite (siehe dortige
// Begründung) -- derselbe /api/status/wait-Endpoint, kein eigener nötig.
async function longPollLoop() {
  for (;;) {
    try {
      const res = await fetch('/api/status/wait?version=' + lastVersion);
      const data = await res.json();
      applyStatus(data);
    } catch (e) {
      await new Promise(r => setTimeout(r, 2000));
    }
  }
}

function applyStatus(data) {
  lastVersion = data.version || 0;
  const musicMode = data.mode === 'music';
  document.getElementById('mode-radio-btn').classList.toggle('active', !musicMode);
  document.getElementById('mode-music-btn').classList.toggle('active', musicMode);

  // Gleiche Player-Quelle-Logik wie auf der Radio-Seite (dort ausführlich
  // begründet: nur einmal setzen, Schema/Port als Paar wählen wegen
  // HTTP/HTTPS auf unterschiedlichen Icecast-Ports) -- Musiksammlung-Modus
  // sendet über denselben Icecast-Mount, nur eben andere Quelle.
  if (!playerSrcSet && data.stream_port && data.stream_mount) {
    const pageIsHttps = location.protocol === 'https:';
    const useSsl = pageIsHttps && data.stream_ssl_port;
    const streamScheme = useSsl ? 'https:' : 'http:';
    const streamPort = useSsl ? data.stream_ssl_port : data.stream_port;
    document.getElementById('player').src =
      streamScheme + '//' + location.hostname + ':' + streamPort + data.stream_mount;
    playerSrcSet = true;
  }

  // Host-Pfad (aus .env, siehe host_paths in webui.py) bevorzugt vor dem
  // Container-Pfad -- Letzterer (/app/music_library/...) ist zwar korrekt,
  // aber für den Nutzer bedeutungslos, der kennt nur seinen echten
  // NAS/Host-Pfad. Fallback auf den Container-Pfad nur, falls host_paths
  // beim Start nicht mitgegeben wurde (siehe cfg_host_path_unknown-Fall
  // auf der Config-Seite).
  document.getElementById('root-path').textContent =
    data.music_library_host_path || data.music_library_path || t('common_unknown');

  const m = data.music || {active: false, file: null, label: null, index: -1, total: 0, tags: null};
  musicActive = !!m.active;

  // Einziger sichtbarer Play/Pause-Knopf ist der große Button unten --
  // das <audio>-Element (ohne "controls", siehe HTML oben) folgt dessen
  // Zustand hier automatisch, statt selbst ein zweites, unabhängiges
  // Play/Pause anzubieten. Vorher gab es zwei Play-Knöpfe (großer Button
  // fürs Backend + nativer Player-Button fürs Zuhören), die sich nicht
  // kannten -- Klick auf den einen ließ den anderen unverändert.
  const player = document.getElementById('player');
  if (musicMode && musicActive) {
    // Nur zur Diagnose auf der Konsole geloggt (kein setActionMsg) --
    // dieser Aufruf läuft bei jedem Status-Poll (bis zu alle paar
    // Sekunden), ohne Nutzer-Geste blockiert der Browser ihn evtl.
    // erwartungsgemäß; die Klick-Handler unten sind die Stelle, die dem
    // Nutzer tatsächlich etwas melden soll.
    if (player.paused) player.play().catch((err) => console.warn('applyStatus(): player.play() abgelehnt:', err));
  } else if (!player.paused) {
    player.pause();
  }

  const playBtn = document.getElementById('btn-play-stop');
  playBtn.textContent = musicActive ? '⏹' : '▶';
  playBtn.title = musicActive ? t('music_stop_title') : t('music_play_title');
  playBtn.disabled = !musicMode;

  document.getElementById('btn-prev-track').disabled = !musicMode || !musicActive;
  document.getElementById('btn-next-track').disabled = !musicMode || !musicActive;
  // Query-Buttons (Phase 2) nur im Musiksammlung-Modus klickbar -- gleiches
  // Gating wie der große Play/Stop-Button oben.
  document.querySelectorAll('.music-query-btn').forEach((btn) => { btn.disabled = !musicMode; });

  const statusEl = document.getElementById('track-status');
  if (!musicMode) {
    statusEl.textContent = t('music_switch_hint');
  } else if (musicActive && m.file) {
    // label ("Artist – Titel") hat Vorrang vor dem reinen Dateinamen, wenn
    // aus einer Query-Wiedergabe bekannt (Phase 2) -- sonst wie bisher.
    statusEl.textContent = t('music_now_playing', {file: m.label || m.file, index: m.index + 1, total: m.total});
  } else {
    statusEl.textContent = t('music_idle');
  }

  // Zwei-Zeilen-Tag-Anzeige (seit 2026-08-15) -- m.tags ist nur gesetzt,
  // solange ein Track läuft (siehe set_music_status() in webui.py),
  // sonst null -> beide Zeilen leer, per :empty ausgeblendet. Gleiche
  // Formatierung wie auf "/" (dort ausführlich begründet).
  const tags = musicActive ? m.tags : null;
  document.getElementById('track-title').textContent =
    tags ? (tags.artist ? `${tags.artist} – ${tags.title}` : tags.title) : '';
  document.getElementById('track-subtitle').textContent =
    tags ? (tags.album && tags.year ? `${tags.album} (${tags.year})` : (tags.album || (tags.year ? String(tags.year) : ''))) : '';

  // VU-Meter: siehe vuTick()/vuQueue oben, gleiches Muster wie auf "/".
  // Grau/leer, solange kein Track läuft.
  const vuWrap = document.getElementById('vu-meter-wrap');
  if (!musicActive) {
    vuWrap.classList.add('paused');
    vuQueue = [];
    document.getElementById('vu-meter-fill').style.width = '0%';
    document.getElementById('vu-meter-pct').textContent = '–';
  } else {
    vuWrap.classList.remove('paused');
    if (Array.isArray(data.audio_levels_dbfs) && data.audio_levels_dbfs.length) {
      vuQueue = data.audio_levels_dbfs.slice();
    }
  }
}

// redirectTo: die Radio-Sender-Steuerung lebt nur auf "/", nicht hier --
// symmetrisch zum entsprechenden Wechsel auf der Player-Seite (siehe dort).
async function setMode(mode, redirectTo) {
  try {
    await fetch('/api/mode', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode}),
    });
    if (redirectTo) {
      location.href = redirectTo;
      return;
    }
    setActionMsg(t('idx_mode_switching'));
    setTimeout(refresh, 1200);
  } catch (e) {
    setActionMsg(t('common_error', {msg: e.message}));
  }
}
document.getElementById('mode-radio-btn').addEventListener('click', () => setMode('radio', '/'));
document.getElementById('mode-music-btn').addEventListener('click', () => setMode('music'));

document.getElementById('btn-play-stop').addEventListener('click', async () => {
  // player.play() MUSS synchron hier im Klick-Handler passieren (echte
  // Nutzer-Geste), noch VOR dem await fetch() -- sonst bleibt das spätere
  // programmatische player.play() aus applyStatus() (ausgelöst vom
  // asynchronen Status-Poll, keine Nutzer-Geste mehr) auf einem frischen
  // Origin ohne Autoplay-Freigabe stumm blockiert (.catch() dort
  // verschluckt den Fehler, siehe applyStatus() oben) -- reproduziert:
  // Wiedergabe lief laut Backend/Icecast einwandfrei, aber ohne vorherigen
  // manuellen Play-Klick auf "/" (der das Origin für Audio "freischaltet")
  // blieb der Browser auf /musik stumm.
  if (!musicActive) {
    document.getElementById('player').play().catch((err) => {
      console.error('btn-play-stop: player.play() abgelehnt:', err);
      setActionMsg('Wiedergabe blockiert (' + err.name + ')', 15000);
    });
  }
  try {
    await fetch(musicActive ? '/api/music/stop' : '/api/music/play', {method: 'POST'});
    setTimeout(refresh, 800);
  } catch (e) {
    setActionMsg(t('common_error', {msg: e.message}));
  }
});
document.getElementById('btn-prev-track').addEventListener('click', async () => {
  try {
    await fetch('/api/music/prev', {method: 'POST'});
    setTimeout(refresh, 800);
  } catch (e) {
    setActionMsg(t('common_error', {msg: e.message}));
  }
});
document.getElementById('btn-next-track').addEventListener('click', async () => {
  try {
    await fetch('/api/music/next', {method: 'POST'});
    setTimeout(refresh, 800);
  } catch (e) {
    setActionMsg(t('common_error', {msg: e.message}));
  }
});
// Kategorie-/Favoriten-Buttons (Phase 2, music_query.py) -- derselbe
// /api/music/play-Endpoint wie der große Play-Button oben, nur mit
// zusätzlichem "query"-Body. Der Server antwortet bei 0 Treffern sofort
// mit ok:false (kein Request geht an den Hauptloop, siehe webui.py/
// _handle_music_play()) -- die Fehlermeldung landet 1:1 im Action-Feld.
document.querySelectorAll('.music-query-btn').forEach((btn) => {
  btn.addEventListener('click', async () => {
    // Gleicher Grund wie beim großen Play-Button oben: player.play() muss
    // synchron als Teil dieser Nutzer-Geste passieren, sonst bleibt die
    // Wiedergabe auf einem frischen Origin stumm.
    document.getElementById('player').play().catch((err) => {
      console.error('music-query-btn: player.play() abgelehnt:', err);
      setActionMsg('Wiedergabe blockiert (' + err.name + ')', 15000);
    });
    const query = {type: btn.dataset.queryType, value: btn.dataset.queryValue};
    try {
      const res = await fetch('/api/music/play', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query}),
      });
      const data = await res.json();
      setActionMsg(data.ok ? '' : (data.error || t('music_query_failed')));
      setTimeout(refresh, 800);
    } catch (e) {
      setActionMsg(t('common_error', {msg: e.message}));
    }
  });
});

refresh();
longPollLoop();
(async () => {
  const d = await fetch('/api/update_check').then(r => r.json()).catch(() => null);
  if (!d || !d.update_available) return;
  document.getElementById('update-banner-text').textContent =
    t('update_banner_text', {version: d.last_known_remote_version || ''});
  document.getElementById('update-banner-link').href = d.changelog_url;
  document.getElementById('update-banner').style.display = 'block';
})();
setInterval(refresh, 1000);
setInterval(vuTick, 100);
</script>
</body>
</html>
"""


_CONFIG_PAGE_HTML = """<!doctype html>
<html lang="%%LANG%%">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RadioSabbelNich — Sender verwalten</title>
<link rel="icon" href="/favicon.ico">
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    max-width: 720px; margin: 2rem auto; padding: 0 1rem;
  }
  h1 { font-size: 1.4rem; }
  img.banner { width: 100%; height: auto; display: block; border-radius: .5rem; margin-bottom: 1rem; }
  .version-tag { text-align: center; font-size: .7rem; color: #888; margin: -.6rem 0 1rem; }
  a.back { display: inline-block; margin-bottom: 1rem; }
  h2 {
    font-size: 1.05rem; margin-top: 2rem; border-bottom: 1px solid #8884;
    padding-bottom: .25rem;
  }
  h2.category-header {
    display: flex; align-items: center; justify-content: space-between;
    gap: .5rem; flex-wrap: wrap;
  }
  h2.category-header .disable-all-btn {
    font-size: .75rem; font-weight: normal; padding: .3rem .6rem;
    border-radius: .4rem; border: 1px solid #999; background: none;
    color: inherit; cursor: pointer; flex-shrink: 0;
  }
  details.category-details { margin-top: 2rem; }
  details.category-details summary {
    cursor: pointer; list-style-position: outside;
  }
  details.category-details summary h2.category-header {
    display: inline-flex; margin-top: 0; border-bottom: none;
    padding-bottom: 0; vertical-align: middle;
  }
  ul.stations { list-style: none; padding: 0; margin: .5rem 0; }
  ul.stations li {
    display: flex; align-items: center; gap: .6rem; padding: .5rem 0;
    border-bottom: 1px solid #8882;
  }
  ul.stations li .name { flex: 1; min-width: 0; }
  ul.stations li .name .url {
    display: block; font-size: .75rem; color: #888; word-break: break-all;
  }
  ul.stations li.disabled .name { opacity: .5; }
  ul.stations li button {
    font-size: .85rem; padding: .3rem .6rem; cursor: pointer; flex-shrink: 0;
  }
  .empty { color: #888; font-size: .9rem; font-style: italic; margin: .3rem 0; }
  .edit-row { flex-wrap: wrap; }
  .edit-row .fields { flex: 1 1 100%; display: grid; gap: .3rem; margin-bottom: .4rem; }
  .edit-row input, .edit-row select {
    font-size: .9rem; padding: .3rem; width: 100%; box-sizing: border-box;
  }
  form#add-form {
    margin-top: 1.5rem; padding: 1rem; border: 1px solid #8884; border-radius: .5rem;
    display: grid; gap: .6rem;
  }
  form#add-form input, form#add-form select {
    padding: .5rem; font-size: 1rem; width: 100%; box-sizing: border-box;
  }
  form#add-form button { padding: .6rem; font-size: 1rem; cursor: pointer; }
  form#settings-form {
    margin-top: 1.5rem; padding: 1rem; border: 1px solid #8884; border-radius: .5rem;
    display: grid; gap: .6rem;
  }
  form#settings-form label { display: grid; gap: .25rem; font-size: .9rem; }
  form#settings-form input {
    padding: .5rem; font-size: 1rem; width: 100%; box-sizing: border-box;
  }
  form#settings-form button { padding: .6rem; font-size: 1rem; cursor: pointer; }
  form#settings-form .hint { font-size: .8rem; color: #888; margin: 0; }
  form#news-break-form, form#stt-form, form#music-library-form {
    margin-top: 1.5rem; padding: 1rem; border: 1px solid #8884; border-radius: .5rem;
    display: grid; gap: .6rem;
  }
  form#news-break-form label, form#stt-form label, form#music-library-form label {
    display: grid; gap: .25rem; font-size: .9rem;
  }
  form#news-break-form label.checkbox, form#stt-form label.checkbox {
    display: flex; flex-direction: row; align-items: center; gap: .4rem;
  }
  form#news-break-form input, form#stt-form input, form#stt-form select {
    padding: .5rem; font-size: 1rem; width: 100%; box-sizing: border-box;
  }
  form#news-break-form input[type=checkbox], form#stt-form input[type=checkbox] {
    width: auto; padding: 0;
  }
  form#news-break-form .hours-row { display: flex; gap: .6rem; align-items: flex-end; }
  form#news-break-form .hours-row label { flex: 1; }
  form#news-break-form button, form#stt-form button, form#music-library-form button {
    padding: .6rem; font-size: 1rem; cursor: pointer;
  }
  form#news-break-form .hint, form#stt-form .hint, form#music-library-form .hint {
    font-size: .8rem; color: #888; margin: 0;
  }
  /* Breadcrumb-Ordner-Browser -- gemeinsamer Baustein für News-Break-MP3-
     Pfad UND Musiksammlung-Root (siehe CLAUDE.md/folder_browse.py), zwei
     unabhängige Einbindungen mit demselben Markup/CSS. */
  .folder-browser {
    border: 1px solid #8884; border-radius: .4rem; padding: .5rem; margin-top: -.2rem;
  }
  .folder-browser-breadcrumb { font-size: .85rem; margin-bottom: .4rem; }
  .folder-browser-breadcrumb a { cursor: pointer; text-decoration: underline; }
  .folder-browser-list { display: flex; flex-wrap: wrap; gap: .4rem; max-height: 8rem; overflow-y: auto; }
  .folder-browser-list button {
    font-size: .85rem; padding: .3rem .6rem; border-radius: .3rem; border: 1px solid #999;
    background: none; color: inherit; cursor: pointer;
  }
  #stt-status-line { font-size: .85rem; font-weight: 600; }
  section#stt-lang-section, section#stt-cat-lang-section {
    margin-top: 1.5rem; padding: 1rem; border: 1px solid #8884; border-radius: .5rem;
  }
  table#stt-lang-table, table#stt-cat-lang-table {
    width: 100%; border-collapse: collapse; font-size: .9rem; margin-top: .5rem;
  }
  table#stt-lang-table th, table#stt-lang-table td,
  table#stt-cat-lang-table th, table#stt-cat-lang-table td {
    text-align: left; padding: .3rem .4rem; border-bottom: 1px solid #8884;
  }
  table#stt-lang-table td.status-ok { color: #2a7a4a; }
  table#stt-lang-table td.status-error { color: #d33; }
  table#stt-lang-table button, table#stt-cat-lang-table select {
    font-size: .85rem; padding: .25rem .5rem;
  }
  form#stt-lang-add-form {
    margin-top: .8rem; padding-top: .8rem; border-top: 1px solid #8884;
    display: flex; flex-wrap: wrap; gap: .5rem; align-items: center;
  }
  form#stt-lang-add-form input {
    padding: .4rem; font-size: .9rem; box-sizing: border-box;
  }
  form#stt-lang-add-form #stt-lang-code { width: 5rem; }
  form#stt-lang-add-form #stt-lang-vosk-path { flex: 1; min-width: 10rem; }
  form#stt-lang-add-form #stt-lang-threshold { width: 5rem; }
  form#stt-lang-add-form button { padding: .5rem 1rem; font-size: .9rem; cursor: pointer; }
  section#stt-calib-section {
    margin-top: 1.5rem; padding: 1rem; border: 1px solid #8884; border-radius: .5rem;
  }
  #stt-calib-idle .fields { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
  #stt-calib-idle input {
    width: 6rem; padding: .4rem; font-size: .9rem; box-sizing: border-box;
  }
  #stt-calib-idle button, #stt-calib-active button {
    padding: .5rem 1rem; font-size: .9rem; cursor: pointer;
  }
  #stt-calib-stage-buttons { display: flex; flex-wrap: wrap; gap: .5rem; margin: .6rem 0; }
  #stt-calib-stage-buttons button.active-stage {
    border-color: #2a7a4a; background: #2a7a4a33; font-weight: 600;
  }
  table#stt-calib-summary-table { width: 100%; border-collapse: collapse; font-size: .9rem; margin: .5rem 0; }
  table#stt-calib-summary-table td { padding: .3rem .4rem; border-bottom: 1px solid #8884; }
  #stt-calib-suggestion {
    margin-top: .8rem; padding: .8rem; border: 1px solid #2a7a4a; border-radius: .4rem;
  }
  #stt-calib-suggestion.warn { border-color: #d33; }
  #stt-calib-samples-details { margin-top: .6rem; font-size: .85rem; }
  #stt-calib-samples-list {
    font-size: .8rem; color: #888; max-height: 10rem; overflow-y: auto; padding-left: 1.2rem;
  }
  form#import-form {
    margin-top: 1.5rem; padding: 1rem; border: 1px solid #8884; border-radius: .5rem;
    display: grid; gap: .6rem;
  }
  form#import-form label { display: grid; gap: .25rem; font-size: .9rem; }
  form#import-form input {
    padding: .5rem; font-size: 1rem; width: 100%; box-sizing: border-box;
  }
  form#import-form button { padding: .6rem; font-size: 1rem; cursor: pointer; }
  form#import-form .hint { font-size: .8rem; color: #888; margin: 0; }
  #import-progress { font-size: .9rem; margin-top: .5rem; min-height: 1.2em; }
  form#stream-url-form, form#tls-form {
    margin-top: 1.5rem; padding: 1rem; border: 1px solid #8884; border-radius: .5rem;
    display: grid; gap: .6rem;
  }
  form#stream-url-form label, form#tls-form label { display: grid; gap: .25rem; font-size: .9rem; }
  form#tls-form label.checkbox { display: flex; flex-direction: row; align-items: center; gap: .4rem; }
  form#stream-url-form input {
    padding: .5rem; font-size: 1rem; width: 100%; box-sizing: border-box;
  }
  form#tls-form input[type=checkbox] { width: auto; padding: 0; }
  form#stream-url-form button, form#tls-form button { padding: .6rem; font-size: 1rem; cursor: pointer; }
  form#stream-url-form .hint, form#tls-form .hint { font-size: .8rem; color: #888; margin: 0; }
  section#fingerprint-section {
    margin-top: 1.5rem; padding: 1rem; border: 1px solid #8884; border-radius: .5rem;
  }
  section#fingerprint-section button {
    padding: .6rem 1rem; font-size: 1rem; cursor: pointer; border-radius: .4rem;
    border: 1px solid #c33; color: #c33; background: none;
  }
  section#resource-section {
    margin-top: 1.5rem; padding: 1rem; border: 1px solid #8884; border-radius: .5rem;
  }
  table#resource-table { width: 100%; border-collapse: collapse; font-size: .9rem; margin-top: .5rem; }
  table#resource-table td { padding: .3rem 0; border-bottom: 1px solid #8882; }
  table#resource-table td.label { color: #888; }
  table#resource-table td.value { text-align: right; font-variant-numeric: tabular-nums; }
  #msg { margin-top: 1rem; font-size: .9rem; min-height: 1.2em; }
  #msg.error { color: #d33; }
  #msg.ok { color: #2a7a4a; }
</style>
</head>
<body>
<img class="banner" src="/radiosabbelnich.webp" alt="RadioSabbelNich">
<div class="version-tag">%%VERSION%%</div>
<div id="update-banner" style="display:none; background:#2a5a8a; color:#fff; padding:.5rem 1rem; margin:.5rem auto; max-width:32rem; border-radius:6px; font-size:.85rem; text-align:center;">
  <span id="update-banner-text"></span>
  <a id="update-banner-link" href="#" target="_blank" rel="noopener" style="color:#cfe0f5; text-decoration:underline; margin-left:.4rem;" data-i18n="update_banner_changelog_link">Was ist neu?</a>
</div>
<a class="back" href="/" data-i18n="cfg_back_link">← zurück zum Player</a>
<h1 data-i18n="cfg_heading">⚙ Sender verwalten</h1>

<h2 data-i18n="cfg_news_break_heading">📰 Nachrichten-Pause</h2>
<form id="news-break-form">
  <p class="hint" data-i18n-html="cfg_news_break_hint">Spielt zur vollen/halben Stunde statt eines Radiosenders
    eine zufällige lokale MP3 ab. Der Ordner unten liegt unter einem
    <strong>Container-internen Pfad</strong> — der eigentliche Host-Ordner
    (z.B. ein SMB-Mount) wird über <code>NEWS_MP3_FOLDER</code> in
    <code>.env</code> nach <code>/app/news_mp3</code> gemountet und braucht
    dafür einen Neustart des Containers. Darunter kannst du per Klick den
    gewünschten Unterordner auswählen.</p>
  <label class="checkbox">
    <input type="checkbox" id="nb-enabled"> <span data-i18n="cfg_active_label">aktiv</span>
  </label>
  <label><span data-i18n="cfg_nb_folder_label">MP3-Ordner (Container-Pfad)</span></label>
  <p class="hint" id="nb-folder-selected"></p>
  <div class="folder-browser">
    <div class="folder-browser-breadcrumb" id="nb-folder-breadcrumb"></div>
    <div class="folder-browser-list" id="nb-folder-list"></div>
  </div>
  <input type="hidden" id="nb-folder-value" required>
  <p class="hint" id="nb-mp3-folder-host"></p>
  <label><span data-i18n="cfg_nb_window_label">Zeitfenster (Minuten)</span>
    <input type="number" id="nb-window" min="0.1" max="15" step="0.1" required>
  </label>
  <label class="checkbox">
    <input type="checkbox" id="nb-hours-enabled"> <span data-i18n="cfg_nb_hours_enabled_label">nur zu bestimmten Stunden aktiv</span>
  </label>
  <div class="hours-row">
    <label><span data-i18n="cfg_nb_hour_start_label">von Stunde</span>
      <input type="number" id="nb-hour-start" min="0" max="24" step="1">
    </label>
    <label><span data-i18n="cfg_nb_hour_end_label">bis Stunde</span>
      <input type="number" id="nb-hour-end" min="0" max="24" step="1">
    </label>
  </div>
  <p class="hint" data-i18n="cfg_nb_adskip_hint">Verbindet sich in den letzten Sekunden der Pause-MP3 schon im
    Hintergrund mit dem Sender und hört auf Musik — wird sie rechtzeitig erkannt, steigt die Wiedergabe direkt
    in der Musik ein statt in einem live laufenden Werbeblock. Best-Effort: ein Live-Radiostream lässt sich
    nicht vorspulen, ein längerer Werbeblock läuft dann wie bisher live weiter.</p>
  <label class="checkbox">
    <input type="checkbox" id="nb-adskip-enabled"> <span data-i18n="cfg_nb_adskip_enabled_label">Werbeblock nach der Pause überspringen (experimentell)</span>
  </label>
  <label><span data-i18n="cfg_nb_adskip_lead_label">Vorlaufzeit vor Pause-Ende (Sekunden)</span>
    <input type="number" id="nb-adskip-lead" min="1" max="120" step="1">
  </label>
  <p class="hint" data-i18n="cfg_nb_speechgate_hint">Startet die Pause nur noch, wenn zur Zeitfenster-Grenze
    ZUSÄTZLICH gerade Sprache auf dem Live-Sender erkannt wird — verhindert, dass rein zeitbasiert
    ausgelöst wird, während noch Musik läuft. Ohne erkannte Sprache verstreicht das Zeitfenster einfach,
    die Pause fällt für diesen Termin aus.</p>
  <label class="checkbox">
    <input type="checkbox" id="nb-speechgate-enabled"> <span data-i18n="cfg_nb_speechgate_enabled_label">Pause nur bei erkannter Sprache starten (experimentell)</span>
  </label>
  <label><span data-i18n="cfg_nb_speechgate_window_label">Toleranzfenster um :00/:30 (Minuten)</span>
    <input type="number" id="nb-speechgate-window" min="0.1" max="15" step="0.1">
  </label>
  <label><span data-i18n="cfg_nb_speechgate_streak_label">Nötige Sprache-Fenster am Stück</span>
    <input type="number" id="nb-speechgate-streak" min="1" max="20" step="1">
  </label>
  <button type="submit" data-i18n="common_save">Speichern</button>
</form>

<h2 data-i18n="cfg_music_library_heading">🎵 Player</h2>
<form id="music-library-form">
  <p class="hint" data-i18n="cfg_music_library_hint">Root-Ordner für den Player-Modus (Play/Stop auf der
    /musik-Seite) — Container-interner Pfad, gemountet über MUSIC_LIBRARY_FOLDER in .env.</p>
  <label><span data-i18n="cfg_music_library_folder_label">Musik-Ordner (Container-Pfad)</span></label>
  <p class="hint" id="ml-folder-selected"></p>
  <div class="folder-browser">
    <div class="folder-browser-breadcrumb" id="ml-folder-breadcrumb"></div>
    <div class="folder-browser-list" id="ml-folder-list"></div>
  </div>
  <input type="hidden" id="ml-folder-value" required>
  <p class="hint" id="ml-folder-host"></p>
  <button type="submit" data-i18n="common_save">Speichern</button>
</form>

<div id="categories" data-i18n="common_loading">Lade …</div>

<h2 data-i18n="cfg_new_station_heading">Neuer Sender</h2>
<form id="add-form">
  <input type="text" id="add-name" placeholder="Name" data-i18n-placeholder="cfg_add_name_placeholder" required>
  <input type="url" id="add-url" placeholder="Stream-URL (https://...)" data-i18n-placeholder="cfg_add_url_placeholder" required>
  <select id="add-category"></select>
  <label><input type="checkbox" id="add-enabled" checked> <span data-i18n="cfg_enabled_label">aktiviert</span></label>
  <button type="submit" data-i18n="cfg_add_btn">Hinzufügen</button>
</form>

<h2 data-i18n="cfg_import_heading">📻 Sender-Import</h2>
<form id="import-form">
  <p class="hint" data-i18n-html="cfg_import_hint">Lädt eine M3U-Playlist und hört bei jedem Sender ein
    paar Sekunden mit: übernommen wird nur, wer dabei durchgehend Audio
    liefert (nicht bloß beim Verbinden). Neue Sender landen
    <strong>deaktiviert</strong> in der Kategorie "Unsortiert" — du
    entscheidest per Haken, wer in die Rotation darf. Kann bei einer
    langen Liste einige Minuten dauern.</p>
  <label><span data-i18n="cfg_import_url_label">Playlist-URL</span>
    <input type="url" id="import-url" placeholder="http://...">
  </label>
  <button type="submit" id="btn-import" data-i18n="cfg_import_btn">Sender importieren</button>
</form>
<div id="import-progress"></div>

<h2 data-i18n="cfg_stream_heading">🔗 Streaming-Adresse</h2>
<form id="stream-url-form">
  <p class="hint" data-i18n-html="cfg_stream_hint">Adresse, die auf der Startseite unter "Streaming via VLC"
    angezeigt wird (zum Eintragen in einen externen Player). Leer lassen,
    um sie automatisch aus der Adresse zu bilden, über die die Startseite
    gerade im Browser aufgerufen wird.</p>
  <label><span data-i18n="cfg_stream_url_label">Stream-URL</span>
    <input type="url" id="stream-url-input"
           placeholder="http://dockfish.icefish-ghost.ts.net:8000/radiosabbelnich.mp3">
  </label>
  <button type="submit" data-i18n="common_save">Speichern</button>
</form>

<h2 data-i18n="cfg_tls_heading">🔒 HTTPS</h2>
<form id="tls-form">
  <p class="hint" data-i18n-html="cfg_tls_hint">Verschlüsselt den Zugriff aufs Web-Interface (Player-
    Seite und diese Config-Seite) per TLS. Braucht ein Zertifikat unter
    <code>TLS_CERT_FILE</code>/<code>TLS_KEY_FILE</code> in <code>.env</code>
    (Host-Pfade zu PEM-Dateien, z.B. per <code>tailscale cert</code>
    erzeugt) — ohne die bleibt der Haken hier wirkungslos, das
    Web-Interface läuft dann weiter über HTTP. <strong>Wirkt erst nach
    einem Neustart des Containers</strong> (<code>docker compose up -d
    --build radiosabbelnich</code>), nicht sofort wie die meisten anderen
    Einstellungen hier. Der Icecast-Stream selbst bekommt unabhängig davon
    automatisch einen zusätzlichen HTTPS-Port, sobald dieselben
    Zertifikate in <code>.env</code> eingetragen sind — dafür gibt es
    keinen eigenen Schalter.</p>
  <label class="checkbox">
    <input type="checkbox" id="tls-enabled"> <span data-i18n="cfg_tls_checkbox_label">HTTPS fürs Web-Interface aktiv</span>
  </label>
  <button type="submit" data-i18n="common_save">Speichern</button>
</form>

<h2 data-i18n="cfg_update_check_heading">🔄 Automatische Update-Prüfung</h2>
<form id="update-check-form">
  <p class="hint" data-i18n-html="cfg_update_check_hint">Prüft alle 24h per reinem Lesezugriff
    gegen GitHub, ob der <code>main</code>-Branch weiter ist als diese
    Installation. Es gibt für die Docker-Installation aktuell KEIN
    Image-Registry-Deployment — die einzige Update-Möglichkeit ist
    <code>git pull</code> im Repo-Verzeichnis, danach
    <code>docker compose up -d --build radiosabbelnich</code>. Kein
    Auto-Update, keine automatische Installation — bei Verfügbarkeit
    erscheint nur ein Hinweis oben auf dieser Seite und der Player-Seite.</p>
  <label class="checkbox">
    <input type="checkbox" id="update-check-enabled"> <span data-i18n="cfg_update_check_checkbox">Automatisch nach Updates suchen</span>
  </label>
  <button type="submit" data-i18n="common_save">Speichern</button>
</form>

<h2 data-i18n="cfg_language_heading">🌐 Sprache</h2>
<form id="language-form">
  <p class="hint" data-i18n-html="cfg_language_hint">Sprache der Web-Oberfläche (Player- und Config-Seite). Wirkt
    sofort für neue Seitenaufrufe; diese Seite lädt nach dem Speichern
    automatisch neu. Startwert kommt aus <code>UI_LANGUAGE</code> in
    <code>.env</code>, danach gewinnt immer die hier gespeicherte
    Einstellung.</p>
  <label><span data-i18n="cfg_language_label">Sprache der Oberfläche</span>
    <select id="language-select">
%%LANGUAGE_OPTIONS%%
    </select>
  </label>
  <button type="submit" data-i18n="common_save">Speichern</button>
</form>

<h2 data-i18n="cfg_buffer_heading">⏱ Puffer-Einstellungen</h2>
<form id="settings-form">
  <p class="hint" data-i18n-html="cfg_buffer_hint">Die nächsten Sender in Rotationsreihenfolge laufen im
    Hintergrund mit und halten Audio vor, damit Wechsel flüssig ablaufen.
    Mehr Sekunden/Sender = flüssiger, aber mehr Bandbreite/CPU.</p>
  <label><span data-i18n="cfg_buffer_seconds_label">Sekunden pro gepuffertem Sender</span>
    <input type="number" id="settings-seconds" min="0" max="60" step="0.5" required>
  </label>
  <label><span data-i18n="cfg_buffer_count_label">Anzahl vorausgepufferter Sender</span>
    <input type="number" id="settings-count" min="0" max="20" step="1" required>
  </label>
  <button type="submit" data-i18n="common_save">Speichern</button>
</form>

<h2 data-i18n="cfg_stt_heading">🗣 STT-Sprachfilter</h2>
<form id="stt-form">
  <p class="hint" data-i18n-html="cfg_stt_hint">Zusätzliches Signal per Speech-to-Text: erkennt, ob
    gerade zusammenhängender Text in der jeweils erwarteten Sprache zu hören
    ist (echte Moderation) oder nicht (auch in dieser Sprache gesungene
    Musik zählt dann als "keine Sprache") — ergänzt VAD/Heuristik, die
    reinen Gesang oft fälschlich als Sprache werten. <strong>Vosk</strong>
    ist leichtgewichtig und Pi-tauglich, <strong>Whisper</strong> genauer,
    aber deutlich ressourcenhungriger. Welche Sprache für welchen Sender
    gilt, wird unten über die Sender-Kategorie festgelegt.</p>
  <p id="stt-status-line" class="hint" data-i18n="cfg_stt_status_loading">Lade Status …</p>
  <label class="checkbox">
    <input type="checkbox" id="stt-enabled"> <span data-i18n="cfg_active_label">aktiv</span>
  </label>
  <label><span data-i18n="cfg_stt_engine_label">Engine</span>
    <select id="stt-engine">
      <option value="vosk" data-i18n="cfg_stt_engine_vosk_option">Vosk (leichtgewicht, Pi-tauglich)</option>
      <option value="whisper" data-i18n="cfg_stt_engine_whisper_option">Whisper (genauer, ressourcenhungriger)</option>
    </select>
  </label>
  <label><span data-i18n="cfg_stt_whisper_size_label">Whisper-Modellgröße</span>
    <input type="text" id="stt-whisper-size" placeholder="tiny">
  </label>
  <label><span data-i18n="cfg_stt_interval_label">Sample-Intervall (Sekunden)</span>
    <input type="number" id="stt-interval" min="2" max="60" step="0.5" required>
  </label>
  <label><span data-i18n="cfg_stt_combine_label">Verknüpfung mit VAD/Heuristik</span>
    <select id="stt-combine">
      <option value="and" data-i18n="cfg_stt_combine_and_option">UND — beide müssen "Sprache" sagen (empfohlen)</option>
      <option value="or" data-i18n="cfg_stt_combine_or_option">ODER — eines reicht</option>
    </select>
  </label>
  <button type="submit" data-i18n="common_save">Speichern</button>
</form>

<section id="stt-lang-section">
  <h2 style="margin-top:0" data-i18n="cfg_stt_lang_heading">🌐 STT-Sprachen</h2>
  <p class="hint" data-i18n-html="cfg_stt_lang_hint">Pro Sprache ein Vosk-Modellpfad (nur bei Engine
    "Vosk" relevant, jede Sprache braucht ein eigenes Modell) und eine
    empirisch ermittelte Konfidenz-Schwelle (siehe README). Ein bereits
    vorhandener Sprachcode wird beim erneuten Eintragen aktualisiert statt
    doppelt angelegt.</p>
  <p class="hint" id="stt-lang-vosk-host-hint"></p>
  <table id="stt-lang-table">
    <thead><tr>
      <th data-i18n="cfg_stt_lang_col_code">Sprache</th>
      <th data-i18n="cfg_stt_lang_col_vosk_path">Vosk-Modellpfad</th>
      <th data-i18n="cfg_stt_lang_col_threshold">Schwelle</th>
      <th data-i18n="cfg_stt_lang_col_status">Status</th>
      <th></th>
    </tr></thead>
    <tbody id="stt-lang-tbody"></tbody>
  </table>
  <form id="stt-lang-add-form">
    <div class="fields">
      <input type="text" id="stt-lang-code" data-i18n-placeholder="cfg_stt_lang_code_placeholder" placeholder="z.B. en" required>
      <input type="text" id="stt-lang-vosk-path" placeholder="/app/vosk-model-en">
      <input type="number" id="stt-lang-threshold" min="0" max="1" step="0.05" value="0.6" required>
    </div>
    <button type="submit" data-i18n="cfg_stt_lang_add_btn">+ Sprache hinzufügen/aktualisieren</button>
  </form>
</section>

<section id="stt-cat-lang-section">
  <h2 style="margin-top:0" data-i18n="cfg_stt_cat_lang_heading">🏷 Kategorie-Sprachen</h2>
  <p class="hint" data-i18n="cfg_stt_cat_lang_hint">Legt fest, welche der oben konfigurierten Sprachen für
    Sender welcher Kategorie geprüft wird. Kategorien ohne Auswahl gelten als
    Deutsch.</p>
  <table id="stt-cat-lang-table"><tbody id="stt-cat-lang-tbody"></tbody></table>
</section>

<section id="stt-calib-section">
  <h2 style="margin-top:0" data-i18n="cfg_stt_calib_heading">🧪 Schwellwert-Kalibrierung</h2>
  <p class="hint" data-i18n-html="cfg_stt_calib_hint">Ermittelt einen Vorschlag für
    <code>confidence_threshold</code> einer Sprache, nach derselben Methode wie die
    ursprüngliche Deutsch-Kalibrierung (siehe README): erst ein paar Minuten einen
    Sender mit garantiert echtem Sprachtext dieser Sprache mithören lassen, dann
    einen Musiksender derselben Sprache. Sender dafür manuell auf der
    <a href="/">Player-Seite</a> auswählen — die Kalibrierung selbst schaltet nichts
    um. Voraussetzung: STT-Filter und Sabbelfilter oben sind aktiv. Für Vosk muss die
    Sprache mit Modellpfad bereits in "🌐 STT-Sprachen" angelegt sein (bei Whisper
    nicht nötig).</p>

  <div id="stt-calib-idle">
    <div class="fields">
      <input type="text" id="stt-calib-lang" data-i18n-placeholder="cfg_stt_lang_code_placeholder" placeholder="z.B. en">
      <button type="button" id="btn-stt-calib-start" data-i18n="cfg_stt_calib_start_btn">🧪 Kalibrierung starten</button>
    </div>
  </div>

  <div id="stt-calib-active" hidden>
    <p><span data-i18n="cfg_stt_calib_active_label">Kalibriere:</span>
       <strong id="stt-calib-active-lang"></strong></p>
    <div id="stt-calib-stage-buttons">
      <button type="button" id="btn-stt-calib-stage-speech" data-i18n="cfg_stt_calib_stage_speech_btn">🗣 Sprache-Stufe</button>
      <button type="button" id="btn-stt-calib-stage-music" data-i18n="cfg_stt_calib_stage_music_btn">🎵 Musik-Stufe</button>
      <button type="button" id="btn-stt-calib-stop" data-i18n="cfg_stt_calib_stop_btn">Abbrechen</button>
    </div>
    <table id="stt-calib-summary-table">
      <tr><td data-i18n="cfg_stt_calib_col_speech">🗣 Sprache-Samples</td><td id="stt-calib-speech-summary">–</td></tr>
      <tr><td data-i18n="cfg_stt_calib_col_music">🎵 Musik-Samples</td><td id="stt-calib-music-summary">–</td></tr>
    </table>
    <div id="stt-calib-suggestion" hidden>
      <p id="stt-calib-suggestion-text"></p>
      <button type="button" id="btn-stt-calib-apply" data-i18n="cfg_stt_calib_apply_btn">Übernehmen</button>
    </div>
    <details id="stt-calib-samples-details">
      <summary data-i18n="cfg_stt_calib_samples_summary">Letzte Samples anzeigen</summary>
      <ul id="stt-calib-samples-list"></ul>
    </details>
  </div>
</section>

<section id="fingerprint-section">
  <h2 style="margin-top:0" data-i18n="cfg_fingerprint_heading">🗑 Fingerprint-Datenbank</h2>
  <p class="hint" data-i18n-html="cfg_fingerprint_hint">Löscht alle gelernten Jingle-/Werbespot-Clips (nicht
    die Senderliste). Danach lernt die Erkennung wieder bei Null.</p>
  <button type="button" id="btn-clear-fingerprints" data-i18n="cfg_fingerprint_clear_btn">Clip-DB leeren</button>
</section>

<section id="resource-section">
  <h2 style="margin-top:0" data-i18n="cfg_resources_heading">💾 Ressourcen-Verbrauch</h2>
  <p class="hint" data-i18n="cfg_resources_hint">Aktueller Verbrauch von RadioSabbelNich selbst (nicht des Hosts),
    alle 5 Sekunden aktualisiert.</p>
  <table id="resource-table">
    <tr><td class="label" data-i18n="cfg_resources_ram_total">RAM gesamt</td><td class="value" id="res-ram-total">–</td></tr>
    <tr><td class="label" data-i18n="cfg_resources_ram_breakdown">davon Python / ffmpeg</td><td class="value" id="res-ram-breakdown">–</td></tr>
    <tr><td class="label" data-i18n="cfg_resources_cpu_total">CPU gesamt</td><td class="value" id="res-cpu-total">–</td></tr>
    <tr><td class="label" data-i18n="cfg_resources_ffmpeg_count">Laufende ffmpeg-Prozesse</td><td class="value" id="res-ffmpeg-count">–</td></tr>
    <tr><td class="label" data-i18n="cfg_resources_fingerprint_db">Fingerprint-DB</td><td class="value" id="res-fingerprint-db">–</td></tr>
    <tr><td class="label" data-i18n="cfg_resources_log">Logdatei (inkl. Rotation)</td><td class="value" id="res-log">–</td></tr>
    <tr><td class="label" data-i18n="cfg_resources_whisper_cache">Whisper-Modell-Cache</td><td class="value" id="res-whisper-cache">–</td></tr>
  </table>
</section>

<div id="msg"></div>

<script>
const LANG = "%%LANG%%";
const I18N = %%I18N_JSON%%;
function t(key, vars) {
  let s = (I18N && I18N[key]) || key;
  if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
  return s;
}
function applyStaticI18n() {
  document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.getAttribute('data-i18n')); });
  document.querySelectorAll('[data-i18n-html]').forEach((el) => { el.innerHTML = t(el.getAttribute('data-i18n-html')); });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.getAttribute('data-i18n-title')); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => { el.placeholder = t(el.getAttribute('data-i18n-placeholder')); });
  document.querySelectorAll('[data-i18n-aria-label]').forEach((el) => { el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria-label'))); });
}
applyStaticI18n();
document.getElementById('language-select').value = LANG;

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

let categories = [];
// "Unsortiert" landet nach einem Import oft mit hunderten Sendern (siehe
// CLAUDE.md, "Config-Seite skaliert nicht auf mehrere hundert Sender") --
// standardmäßig eingeklappt hinter einem <details>. loadStations() baut die
// Kategorie-Liste bei praktisch jeder Aktion (Haken setzen, Bearbeiten,
// Löschen, "Alle deaktivieren", ...) komplett neu auf; ohne dieses Merken
// würde <details> dabei jedes Mal wieder zuklappen.
let unsortedExpanded = false;
let editingId = null;
let msgTimer = null;

function showMsg(text, isError) {
  const el = document.getElementById('msg');
  el.textContent = text;
  el.className = isError ? 'error' : 'ok';
  if (msgTimer) clearTimeout(msgTimer);
  msgTimer = setTimeout(() => { el.textContent = ''; el.className = ''; }, 4000);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw new Error(t('cfg_invalid_response'));
  }
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || ('HTTP ' + res.status));
  }
  return data;
}

async function loadStations() {
  let data;
  try {
    data = await api('/api/config/stations');
  } catch (e) {
    showMsg(t('cfg_load_stations_failed', {msg: e.message}), true);
    return;
  }
  categories = data.categories;
  renderSttCategoryLanguages();  // categories jetzt bekannt -- siehe dortiger Docstring

  const addCategorySelect = document.getElementById('add-category');
  const prevAddCategory = addCategorySelect.value;
  addCategorySelect.innerHTML = categories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  if (categories.includes(prevAddCategory)) addCategorySelect.value = prevAddCategory;

  const container = document.getElementById('categories');
  container.innerHTML = '';
  for (const cat of categories) {
    const stations = data.stations
      .filter(s => s.category === cat)
      .sort((a, b) => a.name.localeCompare(b.name, LANG));

    const h2 = document.createElement('h2');
    h2.className = 'category-header';
    const h2Label = document.createElement('span');
    h2Label.textContent = cat;
    h2.appendChild(h2Label);

    // "Unsortiert" ist die einzige Kategorie, die typischerweise durch einen
    // Import mit hunderten Sendern gefüllt wird -- hinter <details> versteckt,
    // alle anderen Kategorien bleiben unverändert immer sichtbar.
    const isUnsorted = cat === 'Unsortiert';
    let parent = container;
    if (isUnsorted) {
      const details = document.createElement('details');
      details.className = 'category-details';
      details.open = unsortedExpanded;
      details.addEventListener('toggle', () => { unsortedExpanded = details.open; });
      const summary = document.createElement('summary');
      summary.appendChild(h2);
      details.appendChild(summary);
      container.appendChild(details);
      parent = details;
    } else {
      container.appendChild(h2);
    }

    if (stations.length === 0) {
      const p = document.createElement('div');
      p.className = 'empty';
      p.textContent = t('cfg_no_stations_in_category');
      parent.appendChild(p);
      continue;
    }

    const enabledCount = stations.filter(s => s.enabled).length;
    if (enabledCount > 0) {
      const disableAllBtn = document.createElement('button');
      disableAllBtn.className = 'disable-all-btn';
      disableAllBtn.textContent = t('cfg_disable_all_btn');
      disableAllBtn.title = t('cfg_disable_all_title', {count: enabledCount, cat});
      disableAllBtn.addEventListener('click', async (ev) => {
        ev.preventDefault(); // sonst schließt der Klick im <summary> zusätzlich das <details>
        if (!confirm(t('cfg_disable_all_confirm', {count: enabledCount, cat}))) return;
        try {
          const data = await api('/api/config/categories/' + encodeURIComponent(cat) + '/disable-all', {method: 'POST'});
          showMsg(t('cfg_disable_all_done', {count: data.changed, cat}), false);
          loadStations();
        } catch (e) {
          showMsg(t('common_error', {msg: e.message}), true);
        }
      });
      h2.appendChild(disableAllBtn);
    }

    const ul = document.createElement('ul');
    ul.className = 'stations';
    for (const s of stations) {
      ul.appendChild(renderStationRow(s));
    }
    parent.appendChild(ul);
  }
}

function renderStationRow(s) {
  const li = document.createElement('li');
  if (!s.enabled) li.classList.add('disabled');

  if (editingId === s.id) {
    li.classList.add('edit-row');

    const fields = document.createElement('div');
    fields.className = 'fields';

    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.value = s.name;
    nameInput.placeholder = t('cfg_add_name_placeholder');

    const urlInput = document.createElement('input');
    urlInput.type = 'url';
    urlInput.value = s.url;
    urlInput.placeholder = t('cfg_field_url_placeholder');

    const catSelect = document.createElement('select');
    catSelect.innerHTML = categories.map(c =>
      `<option value="${esc(c)}"${c === s.category ? ' selected' : ''}>${esc(c)}</option>`).join('');

    fields.appendChild(nameInput);
    fields.appendChild(urlInput);
    fields.appendChild(catSelect);
    li.appendChild(fields);

    const saveBtn = document.createElement('button');
    saveBtn.textContent = t('common_save');
    saveBtn.onclick = async () => {
      try {
        await api('/api/config/stations/' + encodeURIComponent(s.id), {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            name: nameInput.value, url: urlInput.value,
            category: catSelect.value, enabled: s.enabled,
          }),
        });
        editingId = null;
        showMsg(t('cfg_saved'), false);
        loadStations();
      } catch (e) {
        showMsg(t('common_error', {msg: e.message}), true);
      }
    };
    li.appendChild(saveBtn);

    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = t('common_cancel');
    cancelBtn.onclick = () => { editingId = null; loadStations(); };
    li.appendChild(cancelBtn);

    return li;
  }

  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = s.enabled;
  checkbox.title = t('cfg_enabled_label');
  checkbox.onchange = async () => {
    const wanted = checkbox.checked;
    try {
      await api('/api/config/stations/' + encodeURIComponent(s.id) + '/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled: wanted}),
      });
      loadStations();
    } catch (e) {
      showMsg(t('common_error', {msg: e.message}), true);
      checkbox.checked = !wanted;
    }
  };
  li.appendChild(checkbox);

  const nameDiv = document.createElement('div');
  nameDiv.className = 'name';
  nameDiv.innerHTML = `${esc(s.name)}<span class="url">${esc(s.url)}</span>`;
  li.appendChild(nameDiv);

  const editBtn = document.createElement('button');
  editBtn.textContent = t('common_edit');
  editBtn.onclick = () => { editingId = s.id; loadStations(); };
  li.appendChild(editBtn);

  const delBtn = document.createElement('button');
  delBtn.textContent = t('common_delete');
  delBtn.onclick = async () => {
    if (!confirm(t('cfg_delete_confirm', {name: s.name}))) return;
    try {
      await api('/api/config/stations/' + encodeURIComponent(s.id) + '/delete', {method: 'POST'});
      showMsg(t('cfg_deleted'), false);
      loadStations();
    } catch (e) {
      showMsg(t('common_error', {msg: e.message}), true);
    }
  };
  li.appendChild(delBtn);

  return li;
}

document.getElementById('add-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const name = document.getElementById('add-name').value;
  const url = document.getElementById('add-url').value;
  const category = document.getElementById('add-category').value;
  const enabled = document.getElementById('add-enabled').checked;
  try {
    await api('/api/config/stations', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, url, category, enabled}),
    });
    document.getElementById('add-form').reset();
    document.getElementById('add-enabled').checked = true;
    showMsg(t('cfg_added'), false);
    loadStations();
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

// loadStations() (Kategorien) und loadSettings() (STT-Konfiguration) laufen
// beim Seitenaufruf parallel, ohne aufeinander zu warten -- die Kategorie-
// Sprachen-Tabelle braucht aber Daten aus BEIDEN. Statt eine Ladereihenfolge
// zu erzwingen, merkt sich renderSttCategoryLanguages() den zuletzt
// geladenen Stand hier und wird von beiden Ladefunktionen aufgerufen;
// solange eine der beiden Quellen noch fehlt, ist der Aufruf ein No-Op.
let lastSttCfg = null;
let lastSttLangStatus = {};

function renderSttLanguages(hostPaths) {
  const stt = lastSttCfg;
  if (!stt) return;
  document.getElementById('stt-lang-vosk-host-hint').textContent = hostPaths.stt_filter_vosk_model_path
    ? t('cfg_host_path_mounted', {path: hostPaths.stt_filter_vosk_model_path, envVar: 'VOSK_MODEL_FOLDER'})
    : t('cfg_host_path_unknown');

  const tbody = document.getElementById('stt-lang-tbody');
  tbody.innerHTML = '';
  const langs = stt.languages || {};
  for (const code of Object.keys(langs).sort()) {
    const entry = langs[code];
    const err = lastSttLangStatus[code];
    let statusText, statusClass;
    if (err === undefined) { statusText = t('cfg_stt_lang_status_unknown'); statusClass = ''; }
    else if (err === null) { statusText = t('cfg_stt_lang_status_ok'); statusClass = 'status-ok'; }
    else { statusText = t('cfg_stt_lang_status_error', {error: err}); statusClass = 'status-error'; }
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${esc(code)}</td><td>${esc(entry.vosk_model_path || '')}</td>` +
      `<td>${esc(entry.confidence_threshold)}</td><td class="${statusClass}">${esc(statusText)}</td>` +
      `<td><button type="button" data-code="${esc(code)}">${esc(t('common_delete'))}</button></td>`;
    tbody.appendChild(tr);
    tr.querySelector('button').onclick = async () => {
      if (!confirm(t('cfg_stt_lang_delete_confirm', {code}))) return;
      try {
        await api('/api/config/stt-languages/' + encodeURIComponent(code) + '/delete', {method: 'POST'});
        showMsg(t('cfg_stt_lang_deleted'), false);
        loadSettings();
      } catch (e) {
        showMsg(t('common_error', {msg: e.message}), true);
      }
    };
  }
}

function renderSttCategoryLanguages() {
  if (!categories.length || !lastSttCfg) return;  // siehe Kommentar oben
  const tbody = document.getElementById('stt-cat-lang-tbody');
  tbody.innerHTML = '';
  const catLangs = lastSttCfg.category_languages || {};
  const langCodes = Object.keys(lastSttCfg.languages || {}).sort();
  for (const cat of categories) {
    const current = catLangs[cat] || '';
    const options = ['<option value="">' + esc(t('cfg_stt_cat_lang_default')) + '</option>']
      .concat(langCodes.map(code =>
        `<option value="${esc(code)}"${code === current ? ' selected' : ''}>${esc(code)}</option>`));
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${esc(cat)}</td><td><select>${options.join('')}</select></td>`;
    tbody.appendChild(tr);
    tr.querySelector('select').onchange = async (ev) => {
      try {
        await api('/api/config/stt-category-language', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({category: cat, lang_code: ev.target.value}),
        });
        showMsg(t('cfg_stt_cat_lang_saved'), false);
      } catch (e) {
        showMsg(t('common_error', {msg: e.message}), true);
      }
    };
  }
}

document.getElementById('stt-lang-add-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const lang_code = document.getElementById('stt-lang-code').value;
  const vosk_model_path = document.getElementById('stt-lang-vosk-path').value;
  const confidence_threshold = parseFloat(document.getElementById('stt-lang-threshold').value);
  try {
    await api('/api/config/stt-languages', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({lang_code, vosk_model_path, confidence_threshold}),
    });
    document.getElementById('stt-lang-add-form').reset();
    document.getElementById('stt-lang-threshold').value = '0.6';
    showMsg(t('cfg_stt_lang_saved'), false);
    loadSettings();
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

async function loadSettings() {
  try {
    const settings = await api('/api/config/settings');
    document.getElementById('settings-seconds').value = settings.prebuffer_seconds;
    document.getElementById('settings-count').value = settings.prebuffer_count;
    document.getElementById('import-url').value = settings.import_url;
    document.getElementById('stream-url-input').value = settings.stream_url || '';
    document.getElementById('tls-enabled').checked = !!settings.tls_enabled;
    document.getElementById('update-check-enabled').checked =
      !!(settings.update_check && settings.update_check.enabled);
    document.getElementById('language-select').value = settings.language || LANG;

    const hostPaths = settings._host_paths || {};
    function hostPathHint(hostPath, envVar) {
      return hostPath
        ? t('cfg_host_path_mounted', {path: hostPath, envVar})
        : t('cfg_host_path_unknown');
    }

    const nb = settings.news_break || {};
    document.getElementById('nb-enabled').checked = !!nb.enabled;
    document.getElementById('nb-folder-value').value = nb.mp3_folder || '';
    document.getElementById('nb-folder-selected').textContent =
      t('cfg_folder_selected', {path: nb.mp3_folder || t('common_unknown')});
    document.getElementById('nb-mp3-folder-host').textContent =
      hostPathHint(hostPaths.news_break_mp3_folder, 'NEWS_MP3_FOLDER');
    document.getElementById('nb-window').value = nb.window_minutes;
    const hours = nb.enabled_hours;
    document.getElementById('nb-hours-enabled').checked = !!hours;
    document.getElementById('nb-hour-start').value = hours ? hours[0] : '';
    document.getElementById('nb-hour-end').value = hours ? hours[1] : '';
    document.getElementById('nb-adskip-enabled').checked = !!nb.ad_prebuffer_enabled;
    document.getElementById('nb-adskip-lead').value = nb.ad_prebuffer_lead_seconds;
    document.getElementById('nb-speechgate-enabled').checked = !!nb.require_speech_in_window;
    document.getElementById('nb-speechgate-window').value = nb.speech_gate_window_minutes;
    document.getElementById('nb-speechgate-streak').value = nb.speech_gate_streak;

    const ml = settings.music_library || {};
    document.getElementById('ml-folder-value').value = ml.path || '';
    document.getElementById('ml-folder-selected').textContent =
      t('cfg_folder_selected', {path: ml.path || t('common_unknown')});
    document.getElementById('ml-folder-host').textContent =
      hostPathHint(hostPaths.music_library_path, 'MUSIC_LIBRARY_FOLDER');

    const stt = settings.stt_filter || {};
    document.getElementById('stt-enabled').checked = !!stt.enabled;
    document.getElementById('stt-engine').value = stt.engine || 'vosk';
    document.getElementById('stt-whisper-size').value = stt.whisper_model_size || '';
    document.getElementById('stt-interval').value = stt.sample_interval_seconds;
    document.getElementById('stt-combine').value = stt.combine_mode || 'and';

    lastSttCfg = stt;
    lastSttLangStatus = settings._stt_language_status || {};
    renderSttLanguages(hostPaths);
    renderSttCategoryLanguages();  // siehe Docstring dort (categories ggf. noch nicht da)
  } catch (e) {
    showMsg(t('cfg_load_settings_failed', {msg: e.message}), true);
  }

  try {
    const status = await api('/api/status');
    const stt = status.stt_status || {};
    const line = document.getElementById('stt-status-line');
    if (!stt.engine) {
      line.textContent = t('cfg_stt_status_disabled');
    } else if (stt.available) {
      line.textContent = t('cfg_stt_status_active', {engine: stt.engine});
    } else {
      line.textContent = t('cfg_stt_status_error', {error: stt.error || t('cfg_stt_status_model_not_loadable')});
    }
  } catch (e) {
    // Statuszeile ist rein informativ -- ein Fehlschlag hier soll das
    // Laden der übrigen Einstellungen (oben) nicht als Fehler melden.
  }
}

document.getElementById('settings-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const prebuffer_seconds = parseFloat(document.getElementById('settings-seconds').value);
  const prebuffer_count = parseInt(document.getElementById('settings-count').value, 10);
  try {
    await api('/api/config/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prebuffer_seconds, prebuffer_count}),
    });
    showMsg(t('cfg_buffer_saved'), false);
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

document.getElementById('stream-url-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const stream_url = document.getElementById('stream-url-input').value;
  try {
    await api('/api/config/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({stream_url}),
    });
    showMsg(t('cfg_stream_saved'), false);
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

document.getElementById('tls-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const tls_enabled = document.getElementById('tls-enabled').checked;
  try {
    await api('/api/config/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({tls_enabled}),
    });
    showMsg(t('cfg_tls_saved'), false);
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

document.getElementById('update-check-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const update_check_enabled = document.getElementById('update-check-enabled').checked;
  try {
    await api('/api/config/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({update_check_enabled}),
    });
    showMsg(t('cfg_update_check_saved'), false);
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

document.getElementById('language-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const language = document.getElementById('language-select').value;
  try {
    await api('/api/config/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({language}),
    });
    showMsg(t('cfg_language_saved'), false);
    setTimeout(() => location.reload(), 600);
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

document.getElementById('news-break-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const news_break_enabled = document.getElementById('nb-enabled').checked;
  const news_break_mp3_folder = document.getElementById('nb-folder-value').value;
  const news_break_window_minutes = parseFloat(document.getElementById('nb-window').value);
  const hoursEnabled = document.getElementById('nb-hours-enabled').checked;
  let news_break_enabled_hours = null;
  if (hoursEnabled) {
    const start = parseInt(document.getElementById('nb-hour-start').value, 10);
    const end = parseInt(document.getElementById('nb-hour-end').value, 10);
    news_break_enabled_hours = [start, end];
  }
  const news_break_ad_prebuffer_enabled = document.getElementById('nb-adskip-enabled').checked;
  const news_break_ad_prebuffer_lead_seconds = parseFloat(document.getElementById('nb-adskip-lead').value);
  const news_break_require_speech_in_window = document.getElementById('nb-speechgate-enabled').checked;
  const news_break_speech_gate_window_minutes = parseFloat(document.getElementById('nb-speechgate-window').value);
  const news_break_speech_gate_streak = parseInt(document.getElementById('nb-speechgate-streak').value, 10);
  try {
    await api('/api/config/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        news_break_enabled, news_break_mp3_folder, news_break_window_minutes,
        news_break_enabled_hours,
        news_break_ad_prebuffer_enabled, news_break_ad_prebuffer_lead_seconds,
        news_break_require_speech_in_window, news_break_speech_gate_window_minutes,
        news_break_speech_gate_streak,
      }),
    });
    showMsg(t('cfg_news_break_saved'), false);
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

document.getElementById('music-library-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const music_library_path = document.getElementById('ml-folder-value').value;
  try {
    await api('/api/config/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({music_library_path}),
    });
    showMsg(t('cfg_music_library_saved'), false);
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

// Gemeinsamer Baustein für die Breadcrumb-Ordnerauswahl (siehe
// folder_browse.py/CLAUDE.md) -- EIN Satz Funktionen, zwei unabhängige
// Einbindungen (News-Break-MP3-Pfad, Musiksammlung-Root). Welcher der
// beiden hidden inputs (nb-folder-value/ml-folder-value) mit dem Ergebnis
// beschrieben wird, entscheidet nur, welches "els"-Objekt initFolderBrowser()
// für die jeweilige Instanz zusammenstellt -- browseFolder() selbst kennt
// keinen der beiden Config-Keys.
async function browseFolder(target, relPath, els, updateSelection) {
  let data;
  try {
    const res = await fetch('/api/browse-folder?target=' + encodeURIComponent(target) +
                             '&path=' + encodeURIComponent(relPath));
    data = await res.json();
  } catch (e) {
    els.list.textContent = t('common_error', {msg: e.message});
    return;
  }
  if (updateSelection !== false) {
    els.hiddenInput.value = data.absolute_path;
    els.selectedHint.textContent = t('cfg_folder_selected', {path: data.absolute_path});
  }
  els.breadcrumb.innerHTML = '';
  data.breadcrumb.forEach((seg, i) => {
    if (i > 0) els.breadcrumb.appendChild(document.createTextNode(' / '));
    const a = document.createElement('a');
    a.href = '#';
    a.textContent = seg.name;
    a.addEventListener('click', (ev) => {
      ev.preventDefault();
      browseFolder(target, seg.path, els, true);
    });
    els.breadcrumb.appendChild(a);
  });
  els.list.innerHTML = '';
  if (data.error) {
    const p = document.createElement('p');
    p.className = 'hint';
    p.textContent = t('cfg_folder_error', {msg: data.error});
    els.list.appendChild(p);
  } else if (data.folders.length === 0) {
    const p = document.createElement('p');
    p.className = 'hint';
    p.textContent = t('cfg_folder_empty');
    els.list.appendChild(p);
  } else {
    data.folders.forEach((name) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = '📁 ' + name;
      btn.addEventListener('click', () => {
        const newPath = data.path ? data.path + '/' + name : name;
        browseFolder(target, newPath, els, true);
      });
      els.list.appendChild(btn);
    });
  }
}

function initFolderBrowser(target, prefix) {
  const els = {
    breadcrumb: document.getElementById(prefix + '-folder-breadcrumb'),
    list: document.getElementById(prefix + '-folder-list'),
    hiddenInput: document.getElementById(prefix + '-folder-value'),
    selectedHint: document.getElementById(prefix + '-folder-selected'),
  };
  // updateSelection=false: nur zum Navigieren rendern, den per loadSettings()
  // gesetzten gespeicherten Wert NICHT mit dem Root überschreiben -- sonst
  // würde ein Klick auf "Speichern" ohne vorheriges Durchklicken den Ordner
  // stillschweigend auf den Root zurücksetzen.
  browseFolder(target, '', els, false);
}
initFolderBrowser('news_break', 'nb');
initFolderBrowser('music_library', 'ml');

document.getElementById('stt-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const stt_filter_enabled = document.getElementById('stt-enabled').checked;
  const stt_filter_engine = document.getElementById('stt-engine').value;
  const stt_filter_whisper_model_size = document.getElementById('stt-whisper-size').value;
  const stt_filter_sample_interval_seconds = parseFloat(document.getElementById('stt-interval').value);
  const stt_filter_combine_mode = document.getElementById('stt-combine').value;
  try {
    await api('/api/config/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        stt_filter_enabled, stt_filter_engine,
        stt_filter_whisper_model_size, stt_filter_sample_interval_seconds,
        stt_filter_combine_mode,
      }),
    });
    showMsg(t('cfg_stt_saved'), false);
    loadSettings();
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

let importPolling = null;

function setImportProgress(text) {
  document.getElementById('import-progress').textContent = text;
}

async function pollImportStatus() {
  let data;
  try {
    data = await api('/api/config/import/status');
  } catch (e) {
    setImportProgress(t('cfg_import_progress_error', {msg: e.message}));
    return;
  }

  if (data.phase === 'downloading') {
    setImportProgress(t('cfg_import_loading_playlist'));
  } else if (data.phase === 'checking') {
    setImportProgress(t('cfg_import_checking', {checked: data.checked, total: data.total}));
  }

  if (!data.running) {
    clearInterval(importPolling);
    importPolling = null;
    document.getElementById('btn-import').disabled = false;
    if (data.phase === 'error') {
      setImportProgress('');
      showMsg(t('cfg_import_failed', {error: data.error}), true);
    } else if (data.phase === 'done' && data.result) {
      const r = data.result;
      setImportProgress('');
      showMsg(t('cfg_import_result', {checked: r.checked, working: r.working, added: r.added}), false);
      loadStations();
    }
  }
}

document.getElementById('import-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const import_url = document.getElementById('import-url').value;
  const btn = document.getElementById('btn-import');
  try {
    // URL mitspeichern, falls geändert, bevor der Import losläuft
    await api('/api/config/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({import_url}),
    });
    btn.disabled = true;
    setImportProgress(t('cfg_import_starting'));
    await api('/api/config/import/start', {method: 'POST'});
    if (importPolling) clearInterval(importPolling);
    importPolling = setInterval(pollImportStatus, 1000);
    pollImportStatus();
  } catch (e) {
    btn.disabled = false;
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

document.getElementById('btn-clear-fingerprints').addEventListener('click', async () => {
  if (!confirm(t('cfg_fingerprint_clear_confirm'))) {
    return;
  }
  try {
    const data = await api('/api/fingerprint/clear', {method: 'POST'});
    showMsg(t('cfg_fingerprint_cleared', {cleared: data.cleared}), false);
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

function formatBytes(n) {
  if (!n) return '0 MB';
  const mb = n / (1024 * 1024);
  return (mb >= 1000 ? (mb / 1024).toFixed(2) + ' GB' : mb.toFixed(1) + ' MB');
}

async function loadResources() {
  try {
    const r = await api('/api/resources');
    document.getElementById('res-ram-total').textContent = formatBytes(r.total_rss_bytes);
    document.getElementById('res-ram-breakdown').textContent =
      formatBytes(r.main_rss_bytes) + ' / ' + formatBytes(r.ffmpeg_rss_bytes);
    document.getElementById('res-cpu-total').textContent = r.total_cpu_percent.toFixed(1) + ' %';
    document.getElementById('res-ffmpeg-count').textContent = r.ffmpeg_count;
    document.getElementById('res-fingerprint-db').textContent = formatBytes(r.fingerprint_db_bytes);
    document.getElementById('res-log').textContent = formatBytes(r.log_bytes);
    document.getElementById('res-whisper-cache').textContent = formatBytes(r.whisper_cache_bytes);
  } catch (e) {
    // Rein informatives Panel -- ein Fehlschlag hier soll den Rest der
    // Config-Seite nicht als Fehler melden (gleiches Muster wie die
    // STT-Statuszeile in loadSettings()).
  }
}

let lastCalibSnapshot = null;

function formatConfidenceSummary(samples) {
  if (!samples.length) return t('cfg_stt_calib_no_samples');
  const confs = samples.map(s => s.confidence);
  const min = Math.min(...confs), max = Math.max(...confs);
  const mean = confs.reduce((a, b) => a + b, 0) / confs.length;
  return t('cfg_stt_calib_summary', {
    count: samples.length, min: min.toFixed(2), max: max.toFixed(2), mean: mean.toFixed(2),
  });
}

function renderCalibration(s) {
  lastCalibSnapshot = s;
  const idle = document.getElementById('stt-calib-idle');
  const active = document.getElementById('stt-calib-active');
  if (!s.active) {
    idle.hidden = false;
    active.hidden = true;
    return;
  }
  idle.hidden = true;
  active.hidden = false;
  document.getElementById('stt-calib-active-lang').textContent = s.language;

  document.getElementById('btn-stt-calib-stage-speech').classList.toggle('active-stage', s.stage === 'speech');
  document.getElementById('btn-stt-calib-stage-music').classList.toggle('active-stage', s.stage === 'music');

  document.getElementById('stt-calib-speech-summary').textContent = formatConfidenceSummary(s.speech_samples);
  document.getElementById('stt-calib-music-summary').textContent = formatConfidenceSummary(s.music_samples);

  const suggestionBox = document.getElementById('stt-calib-suggestion');
  if (s.suggestion) {
    suggestionBox.hidden = false;
    suggestionBox.classList.toggle('warn', !s.suggestion.clean_separation);
    const key = s.suggestion.clean_separation ? 'cfg_stt_calib_suggestion_clean' : 'cfg_stt_calib_suggestion_warn';
    document.getElementById('stt-calib-suggestion-text').textContent = t(key, {threshold: s.suggestion.threshold});
  } else {
    suggestionBox.hidden = true;
  }

  // Nur die Samples der GERADE aktiven Stufe anzeigen, jüngste zuerst --
  // die andere Stufe bleibt in ihrer eigenen Zusammenfassungszeile oben
  // sichtbar, muss hier nicht doppelt aufgelistet werden.
  const currentSamples = s.stage === 'speech' ? s.speech_samples : s.music_samples;
  document.getElementById('stt-calib-samples-list').innerHTML = currentSamples.slice(-15).reverse()
    .map((sample) => `<li>${sample.confidence.toFixed(2)} — ${esc(sample.text || t('cfg_stt_calib_no_text'))}</li>`)
    .join('');
}

async function pollCalibration() {
  try {
    renderCalibration(await api('/api/config/stt-calibration/status'));
  } catch (e) {
    // Rein informativ -- kein Fehlerhinweis auf der ganzen Config-Seite.
  }
}

document.getElementById('btn-stt-calib-start').addEventListener('click', async () => {
  const language = document.getElementById('stt-calib-lang').value.trim().toLowerCase();
  if (!language) {
    showMsg(t('cfg_stt_calib_lang_required'), true);
    return;
  }
  try {
    await api('/api/config/stt-calibration/start', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({language}),
    });
    pollCalibration();
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

async function setCalibStage(stage) {
  try {
    await api('/api/config/stt-calibration/stage', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({stage}),
    });
    pollCalibration();
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
}
document.getElementById('btn-stt-calib-stage-speech').addEventListener('click', () => setCalibStage('speech'));
document.getElementById('btn-stt-calib-stage-music').addEventListener('click', () => setCalibStage('music'));

document.getElementById('btn-stt-calib-stop').addEventListener('click', async () => {
  try {
    await api('/api/config/stt-calibration/stop', {method: 'POST'});
    pollCalibration();
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

document.getElementById('btn-stt-calib-apply').addEventListener('click', async () => {
  if (!lastCalibSnapshot || !lastCalibSnapshot.suggestion) return;
  try {
    // Bestehender Upsert-Endpoint statt eines eigenen "apply" -- eine
    // Sprache mit neuer Schwelle speichern ist exakt derselbe Vorgang wie
    // manuell in der "🌐 STT-Sprachen"-Tabelle, nur mit vorausgefüllten
    // Werten aus der Kalibrierung.
    await api('/api/config/stt-languages', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        lang_code: lastCalibSnapshot.language,
        confidence_threshold: lastCalibSnapshot.suggestion.threshold,
      }),
    });
    showMsg(t('cfg_stt_calib_applied'), false);
    loadSettings();
  } catch (e) {
    showMsg(t('common_error', {msg: e.message}), true);
  }
});

loadStations();
loadSettings();
loadResources();
pollCalibration();
// Nur relevant, solange die Config-Seite offen ist -- kein Long-Poll nötig,
// das ist kein zeitkritischer Wert (anders als der Bullshitometer auf der
// Player-Seite).
setInterval(loadResources, 5000);
setInterval(pollCalibration, 2000);

// Falls die Seite neu geladen wird, während ein Import noch läuft (z.B.
// nach einem Reload), sofort den Fortschritt abfragen und ggf. weiter pollen.
(async () => {
  const data = await api('/api/config/import/status').catch(() => null);
  if (data && data.running) {
    document.getElementById('btn-import').disabled = true;
    importPolling = setInterval(pollImportStatus, 1000);
    pollImportStatus();
  }
})();

// Einmaliger Abruf reicht -- update_check ändert sich höchstens 1x/Tag
// (siehe update_check.py), kein Polling nötig.
(async () => {
  const d = await api('/api/update_check').catch(() => null);
  if (!d || !d.update_available) return;
  document.getElementById('update-banner-text').textContent =
    t('update_banner_text', {version: d.last_known_remote_version || ''});
  document.getElementById('update-banner-link').href = d.changelog_url;
  document.getElementById('update-banner').style.display = 'block';
})();
</script>
</body>
</html>
"""

_I18N_KEY_RE = re.compile(
    r'data-i18n(?:-[\w-]+)?="([^"]+)"'      # data-i18n/-html/-title/-placeholder/-aria-label="key"
    r"|(?<![A-Za-z0-9_])t\('([^']+)'"       # t('key' ...) in JS -- Lookbehind gegen Fehltreffer
                                             # wie document.createElemen[t('div')] oder spli[t('{')]
)


def _check_i18n_coverage(template: str, template_name: str):
    """Sicherheitsnetz gegen vergessene/vertippte Übersetzungs-Keys: kein
    Test-Framework im Projekt (siehe CLAUDE.md), das übernimmt diese Rolle.
    Läuft einmal beim Modul-Import -- ein fehlender Key wirft sofort beim
    Start, statt als leerer/falscher Text erst zur Laufzeit im Browser
    aufzufallen."""
    keys = {a or b for a, b in _I18N_KEY_RE.findall(template)}
    missing = keys - set(i18n.STRINGS)
    if missing:
        raise AssertionError(
            f"{template_name}: i18n-Keys ohne Eintrag in i18n.STRINGS: {sorted(missing)}"
        )


def _escape_html_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# Sprachauswahl auf der Config-Seite (%%LANGUAGE_OPTIONS%% in _CONFIG_PAGE_HTML)
# kommt seit der .lng-Umstellung aus i18n.LANGUAGE_NAMES statt aus zwei
# hartcodierten <option>-Zeilen -- eine neue Sprache (weitere .lng-Datei)
# taucht dadurch automatisch im Dropdown auf, ohne dieses Template anzufassen.
# Nach Anzeigename sortiert, nicht nach Code, damit die Liste im Dropdown
# alphabetisch nach dem lesbar ist, was der Nutzer tatsächlich sieht.
_LANGUAGE_OPTIONS_HTML = "\n".join(
    f'      <option value="{code}">{_escape_html_text(name)}</option>'
    for code, name in sorted(i18n.LANGUAGE_NAMES.items(), key=lambda kv: kv[1])
)


def _render_i18n_variants(template: str, template_name: str) -> dict:
    _check_i18n_coverage(template, template_name)
    variants = {}
    for lang in i18n.LANGUAGES:
        strings_json = json.dumps({k: v[lang] for k, v in i18n.STRINGS.items()}, ensure_ascii=False)
        html = (template.replace("%%LANG%%", lang).replace("%%I18N_JSON%%", strings_json)
                .replace("%%VERSION%%", _VERSION_STRING)
                .replace("%%LANGUAGE_OPTIONS%%", _LANGUAGE_OPTIONS_HTML))
        variants[lang] = html.encode("utf-8")
    return variants


# Einmal pro Sprache vorgerechnet (wie _MANIFEST_JSON_BYTES etc. oben) --
# do_GET wählt anhand von state.language nur noch per Dict-Lookup aus, keine
# Pro-Request-Stringarbeit. state.language kann sich zur Laufzeit ändern
# (Config-Seite), die vorgerechneten Varianten für BEIDE Sprachen liegen
# aber schon bereit -- kein Neustart nötig, anders als z.B. tls_enabled.
_PAGE_HTML_BYTES = _render_i18n_variants(_PAGE_HTML, "_PAGE_HTML")
_MUSIC_PAGE_HTML_BYTES = _render_i18n_variants(_MUSIC_PAGE_HTML, "_MUSIC_PAGE_HTML")
_CONFIG_PAGE_HTML_BYTES = _render_i18n_variants(_CONFIG_PAGE_HTML, "_CONFIG_PAGE_HTML")


def make_handler(state: SwitcherState, icecast_cfg: dict, fingerprint_db_path: str,
                  host_paths: dict = None, log_file_path: str = None,
                  music_library_db_path: str = music_scan.MUSIC_LIBRARY_DB_FILE,
                  music_library_covers_dir: str = music_scan.MUSIC_LIBRARY_COVERS_DIR):
    """Baut eine BaseHTTPRequestHandler-Subklasse mit state/icecast_cfg im
    Closure — so bleibt der Handler selbst zustandslos und threadsicher.

    host_paths: rein informative Host-Pfade (NEWS_MP3_FOLDER/
    VOSK_MODEL_FOLDER aus .env), NUR fürs Anzeigen auf der Config-Seite --
    der Container selbst kennt nur seine eigenen Pfade (/app/news_mp3 o.ä.),
    dorthin gemountet wird schon vor dem Start in docker-compose.yml. Ohne
    diesen expliziten Durchreich-Weg hätte der laufende Prozess keine
    Möglichkeit, den echten Host-Pfad überhaupt zu kennen (siehe CLAUDE.md)."""
    host_paths = host_paths or {}

    import_state = ImportState()
    library_scan_state = LibraryScanState()
    # Einmalig pro Server-Instanz statt pro Request, analog zu import_state:
    # ResourceMonitor hält psutil-Process-Handles über Requests hinweg am
    # Leben, damit cpu_percent() über die Zeit aussagekräftige Deltas liefert
    # statt bei jedem Request neu bei 0.0 anzufangen (siehe resource_monitor.py).
    res_mon = resource_monitor.ResourceMonitor(fingerprint_db_path, log_file_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Nicht mehr komplett verwerfen: auf DEBUG, damit die Requests in
            # der Logdatei nachvollziehbar sind (wer hat wann was geklickt),
            # ohne die Konsole mit einem Eintrag pro /api/status-Poll alle
            # 5 Sekunden zuzumüllen.
            log.debug("[http] %s %s", self.address_string(), fmt % args)

        def _send(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, obj, status: int = 200):
            self._send(json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", status=status)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                return json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            if self.path in ("/", ""):
                self._send(_PAGE_HTML_BYTES[state.language], "text/html; charset=utf-8")
            elif self.path == "/config":
                self._send(_CONFIG_PAGE_HTML_BYTES[state.language], "text/html; charset=utf-8")
            elif self.path == "/musik":
                self._send(_MUSIC_PAGE_HTML_BYTES[state.language], "text/html; charset=utf-8")
            elif self.path == "/radiosabbelnich.webp":
                if _BANNER_BYTES is None:
                    self.send_error(404)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/webp")
                    self.send_header("Content-Length", str(len(_BANNER_BYTES)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(_BANNER_BYTES)
            elif self.path == "/qrcode.js":
                if _QRCODE_JS_BYTES is None:
                    self.send_error(404)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Content-Length", str(len(_QRCODE_JS_BYTES)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(_QRCODE_JS_BYTES)
            elif self.path == "/manifest.json":
                if _MANIFEST_JSON_BYTES is None:
                    self.send_error(404)
                else:
                    self._send(_MANIFEST_JSON_BYTES, "application/manifest+json; charset=utf-8")
            elif self.path == "/sw.js":
                if _SERVICE_WORKER_JS_BYTES is None:
                    self.send_error(404)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Content-Length", str(len(_SERVICE_WORKER_JS_BYTES)))
                    # Bewusst KEIN langes Caching wie bei /qrcode.js -- ein
                    # veralteter Service Worker im Browser-Cache würde eine
                    # neu ausgerollte sw.js (z.B. geänderte SHELL_URLS) erst
                    # nach bis zu 24h (Chromes eingebautes SW-Update-Limit)
                    # statt beim nächsten Seitenaufruf bemerken.
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(_SERVICE_WORKER_JS_BYTES)
            elif self.path == "/favicon.ico":
                if _FAVICON_ICO_BYTES is None:
                    self.send_error(404)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/x-icon")
                    self.send_header("Content-Length", str(len(_FAVICON_ICO_BYTES)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(_FAVICON_ICO_BYTES)
            elif self.path in ("/icon-192.png", "/icon-512.png"):
                icon_bytes = _ICON_192_BYTES if self.path == "/icon-192.png" else _ICON_512_BYTES
                if icon_bytes is None:
                    self.send_error(404)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(icon_bytes)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(icon_bytes)
            elif self.path == "/api/status":
                self._send_json(_build_status(state, icecast_cfg, host_paths))
            elif self.path.startswith("/api/status/wait"):
                self._handle_status_wait()
            elif self.path == "/api/config/stations":
                # bewusst frisch von der Platte, nicht state.all_stations:
                # das ist nur ein Cache für die Rotation im Hauptloop und
                # wird erst beim nächsten Reload-Poll dort aktualisiert —
                # die Config-Seite muss aber eigene Änderungen sofort sehen
                self._send_json({
                    "stations": stations_store.load_all(),
                    "categories": stations_store.CATEGORIES,
                })
            elif self.path == "/api/config/settings":
                data = settings_store.load()
                # Rein informativ, kein Settings-Feld -- siehe make_handler()-
                # Docstring. Die Config-Seite zeigt das read-only neben den
                # jeweiligen Container-Pfad-Feldern an.
                data["_host_paths"] = {
                    "news_break_mp3_folder": host_paths.get("news_mp3_folder"),
                    "stt_filter_vosk_model_path": host_paths.get("vosk_model_folder"),
                    "music_library_path": host_paths.get("music_library_folder"),
                }
                # Ladezustand pro Sprache (nur für engine="vosk"
                # aussagekräftig, siehe SttFilter.language_status()) -- für
                # die ✅/⚠-Anzeige pro Zeile in der Sprachen-Tabelle.
                data["_stt_language_status"] = state.stt_language_status
                self._send_json(data)
            elif self.path == "/api/config/import/status":
                self._send_json(import_state.snapshot())
            elif self.path == "/api/library/scan/status":
                self._send_json(library_scan_state.snapshot())
            elif self.path == "/api/library/duplicates":
                self._handle_library_duplicates()
            elif self.path == "/api/config/stt-calibration/status":
                self._send_json(_build_calibration_status(state))
            elif self.path == "/api/resources":
                self._send_json(res_mon.snapshot())
            elif self.path == "/api/update_check":
                # Bewusst ein eigener, schlanker Endpoint statt Teil von
                # /api/config/settings: läuft auch auf der Player-Seite
                # (die sonst nie das komplette Settings-Objekt lädt) für
                # die kleine Update-Banner-Anzeige. Liest nur den
                # gecachten State aus settings.json -- KEIN Live-
                # Netzwerk-Request hier, der eigentliche GitHub-Check
                # läuft ausschließlich im Hintergrund-Thread (siehe
                # update_check.UpdateChecker), ein Seitenaufruf wartet
                # also nie auf GitHub.
                data = settings_store.load()["update_check"]
                self._send_json({**data, "changelog_url": update_check.CHANGELOG_URL})
            elif self.path.startswith("/api/browse-folder"):
                self._handle_browse_folder()
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path == "/api/switch":
                self._handle_switch()
            elif self.path == "/api/switch/next":
                self._handle_switch_relative(1)
            elif self.path == "/api/switch/prev":
                self._handle_switch_relative(-1)
            elif self.path == "/api/skip":
                self._handle_skip()
            elif self.path == "/api/news-break/skip":
                self._handle_news_break_skip()
            elif self.path == "/api/filter/toggle":
                self._handle_filter_toggle()
            elif self.path == "/api/fingerprint/undo":
                self._handle_fingerprint_undo()
            elif self.path == "/api/fingerprint/clear":
                self._handle_fingerprint_clear()
            elif self.path == "/api/config/stations":
                self._handle_add_station()
            elif self.path.startswith("/api/config/stations/"):
                self._handle_station_action()
            elif self.path.startswith("/api/config/categories/"):
                self._handle_category_action()
            elif self.path == "/api/config/settings":
                self._handle_update_settings()
            elif self.path == "/api/config/stt-languages":
                self._handle_add_stt_language()
            elif self.path.startswith("/api/config/stt-languages/"):
                self._handle_stt_language_action()
            elif self.path == "/api/config/stt-category-language":
                self._handle_set_category_language()
            elif self.path == "/api/config/stt-calibration/start":
                self._handle_calibration_start()
            elif self.path == "/api/config/stt-calibration/stage":
                self._handle_calibration_stage()
            elif self.path == "/api/config/stt-calibration/stop":
                self._handle_calibration_stop()
            elif self.path == "/api/config/import/start":
                self._handle_import_start()
            elif self.path == "/api/library/scan":
                self._handle_library_scan_start()
            elif self.path == "/api/mode":
                self._handle_mode_change()
            elif self.path == "/api/music/play":
                self._handle_music_play()
            elif self.path == "/api/music/stop":
                state.request_music_stop()
                self._send_json({"ok": True})
            elif self.path == "/api/music/next":
                state.request_music_skip(1)
                self._send_json({"ok": True})
            elif self.path == "/api/music/prev":
                state.request_music_skip(-1)
                self._send_json({"ok": True})
            else:
                self.send_error(404)

        def _handle_category_action(self):
            # Pfadschema: /api/config/categories/<category>/disable-all
            # ("Alle deaktivieren"-Knopf hinter jeder Kategorie-Überschrift
            # auf der Config-Seite — erspart hunderte Einzel-Klicks bei
            # großen Kategorien wie "Unsortiert" nach einem Import.)
            rest = self.path[len("/api/config/categories/"):]
            parts = [p for p in rest.split("/") if p]
            if len(parts) != 2 or parts[1] != "disable-all":
                self.send_error(404)
                return
            category = urllib.parse.unquote(parts[0])
            if category not in stations_store.CATEGORIES:
                self._send_json({"ok": False, "error": "Unbekannte Kategorie."}, status=400)
                return
            changed = stations_store.set_category_enabled(category, False)
            log.info("🎛  Config: Kategorie '%s' komplett deaktiviert (%d Sender).",
                     category, changed)
            state.request_reload()
            self._send_json({"ok": True, "changed": changed})

        def _handle_update_settings(self):
            payload = self._read_json_body()
            try:
                settings = settings_store.update(
                    prebuffer_seconds=payload.get("prebuffer_seconds"),
                    prebuffer_count=payload.get("prebuffer_count"),
                    import_url=payload.get("import_url"),
                    stream_url=payload.get("stream_url"),
                    tls_enabled=payload.get("tls_enabled"),
                    language=payload.get("language"),
                    music_library_path=payload.get("music_library_path"),
                    news_break_enabled=payload.get("news_break_enabled"),
                    news_break_mp3_folder=payload.get("news_break_mp3_folder"),
                    news_break_window_minutes=payload.get("news_break_window_minutes"),
                    # payload.get(...) allein würde "Feld fehlt" und "Feld
                    # ist null" nicht unterscheiden können — beides ergäbe
                    # Python None. settings_store.update() braucht aber
                    # genau diese Unterscheidung (None = aktiv zurücksetzen,
                    # UNSET = unverändert lassen), siehe dortiger Docstring.
                    news_break_enabled_hours=(
                        payload["news_break_enabled_hours"]
                        if "news_break_enabled_hours" in payload
                        else settings_store.UNSET
                    ),
                    news_break_ad_prebuffer_enabled=payload.get("news_break_ad_prebuffer_enabled"),
                    news_break_ad_prebuffer_lead_seconds=payload.get("news_break_ad_prebuffer_lead_seconds"),
                    news_break_require_speech_in_window=payload.get("news_break_require_speech_in_window"),
                    news_break_speech_gate_window_minutes=payload.get("news_break_speech_gate_window_minutes"),
                    news_break_speech_gate_streak=payload.get("news_break_speech_gate_streak"),
                    stt_filter_enabled=payload.get("stt_filter_enabled"),
                    stt_filter_engine=payload.get("stt_filter_engine"),
                    stt_filter_whisper_model_size=payload.get("stt_filter_whisper_model_size"),
                    stt_filter_sample_interval_seconds=payload.get("stt_filter_sample_interval_seconds"),
                    stt_filter_combine_mode=payload.get("stt_filter_combine_mode"),
                    update_check_enabled=payload.get("update_check_enabled"),
                )
                state.request_reload()
                self._send_json({"ok": True, "settings": settings})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)

        def _handle_browse_folder(self):
            # Gemeinsamer Baustein für die Breadcrumb-Ordnerauswahl (siehe
            # folder_browse.py) -- EIN Endpoint, "target" wählt nur die
            # feste Mount-Grenze (_BROWSE_ROOTS), beschreibt aber selbst
            # gar keinen Config-Wert. Welcher der beiden Config-Keys
            # (news_break_mp3_folder/music_library_path) am Ende mit dem
            # zurückgelieferten absolute_path gespeichert wird, entscheidet
            # ausschließlich das Formular im Frontend, das diesen Endpoint
            # aufgerufen hat.
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            target = (qs.get("target") or [""])[0]
            rel_path = (qs.get("path") or [""])[0]
            root = _BROWSE_ROOTS.get(target)
            if root is None:
                self._send_json({"error": f"unbekanntes target: {target!r}"}, status=400)
                return
            self._send_json(folder_browse.list_subfolders(root, rel_path))

        def _handle_mode_change(self):
            payload = self._read_json_body()
            mode = payload.get("mode")
            if mode not in settings_store.CURRENT_MODES:
                self._send_json(
                    {"ok": False, "error": f"mode muss eine von {sorted(settings_store.CURRENT_MODES)} sein."},
                    status=400,
                )
                return
            # Request/pop, kein direktes state.set_mode(): der tatsächliche
            # Übergang (Sender/Track stoppen, ggf. neu verbinden) darf nur
            # der Hauptloop machen (siehe SwitcherState.request_mode_change()-
            # Docstring). Die Antwort bestätigt also nur "angenommen", nicht
            # "schon umgeschaltet" -- das Frontend erkennt den echten
            # Übergang am geänderten "mode"-Feld beim nächsten /api/status.
            state.request_mode_change(mode)
            self._send_json({"ok": True})

        def _handle_add_stt_language(self):
            # set_stt_language() ist ein Upsert (siehe settings_store.py) --
            # ein einziger Endpoint für Anlegen UND Bearbeiten, anders als
            # bei Sendern (dort zwei getrennte Endpoints), weil eine Sprache
            # keine eigene stabile ID neben ihrem Code braucht.
            payload = self._read_json_body()
            try:
                entry = settings_store.set_stt_language(
                    payload.get("lang_code", ""),
                    vosk_model_path=payload.get("vosk_model_path"),
                    confidence_threshold=payload.get("confidence_threshold"),
                )
                state.request_reload()
                self._send_json({"ok": True, "language": entry})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)

        def _handle_stt_language_action(self):
            # Pfadschema: /api/config/stt-languages/<lang_code>/delete
            rest = self.path[len("/api/config/stt-languages/"):]
            parts = [p for p in rest.split("/") if p]
            if len(parts) != 2 or parts[1] != "delete":
                self.send_error(404)
                return
            lang_code = urllib.parse.unquote(parts[0])
            try:
                settings_store.delete_stt_language(lang_code)
                state.request_reload()
                self._send_json({"ok": True})
            except KeyError:
                self._send_json({"ok": False, "error": "Sprache nicht gefunden"}, status=404)
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)

        def _handle_set_category_language(self):
            payload = self._read_json_body()
            category = payload.get("category", "")
            if category not in stations_store.CATEGORIES:
                self._send_json({"ok": False, "error": "Unbekannte Kategorie."}, status=400)
                return
            try:
                settings_store.set_category_language(category, payload.get("lang_code", ""))
                state.request_reload()
                self._send_json({"ok": True})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)

        def _handle_calibration_start(self):
            # Voraussetzungen NICHT automatisch gesetzt (z.B. Sabbelfilter
            # stillschweigend aktivieren) -- der Nutzer soll bewusst
            # entscheiden, den Player kurz für die Kalibrierung zu nutzen,
            # siehe Hinweistext auf der Config-Seite.
            payload = self._read_json_body()
            language = (payload.get("language") or "").strip().lower()
            if not language:
                self._send_json({"ok": False, "error": "Sprachcode darf nicht leer sein."}, status=400)
                return
            if not state.stt_filter_cfg.get("enabled", False):
                self._send_json({"ok": False, "error": "STT-Filter ist deaktiviert — oben zuerst aktivieren."},
                                 status=400)
                return
            if not state.filter_enabled:
                self._send_json({"ok": False, "error": "Sabbelfilter ist aus — STT sampelt sonst nicht."},
                                 status=400)
                return
            state.start_calibration(language)
            log.info("🧪 Kalibrierung gestartet: Sprache '%s', Stufe 'speech'.", language)
            self._send_json({"ok": True})

        def _handle_calibration_stage(self):
            payload = self._read_json_body()
            stage = payload.get("stage")
            if stage not in ("speech", "music"):
                self._send_json({"ok": False, "error": "stage muss 'speech' oder 'music' sein."}, status=400)
                return
            state.set_calibration_stage(stage)
            log.info("🧪 Kalibrierung: Stufe gewechselt zu '%s'.", stage)
            self._send_json({"ok": True})

        def _handle_calibration_stop(self):
            state.stop_calibration()
            log.info("🧪 Kalibrierung beendet.")
            self._send_json({"ok": True})

        def _handle_fingerprint_clear(self):
            # "Clip-DB leeren"-Knopf: löscht ALLE gelernten Fingerprint-
            # Clips (nicht stations.json!). Sicherheitsabfrage passiert im
            # Frontend (confirm()) — hier keine weitere Rückfrage nötig.
            try:
                cleared = fingerprint.clear_all(fingerprint_db_path)
            except Exception as e:
                # z.B. "database is locked", wenn der Hauptloop gerade selbst
                # schreibt — als saubere Fehlermeldung zurückgeben statt als
                # abgebrochener Request ohne Antwort.
                log.exception("⚠ Clip-DB leeren fehlgeschlagen.")
                self._send_json({"ok": False, "error": f"Datenbankfehler: {e}"}, status=500)
                return
            self._send_json({"ok": True, "cleared": cleared})

        def _handle_import_start(self):
            # "Sender importieren"-Knopf: laden+prüfen kann bei einer
            # langen Playlist mehrere Minuten dauern -> läuft in einem
            # Hintergrund-Thread, die Config-Seite pollt
            # /api/config/import/status für den Fortschritt.
            if not import_state.start():
                self._send_json({
                    "ok": False,
                    "error": "Es läuft bereits ein Import.",
                }, status=409)
                return

            settings = settings_store.load()
            import_url = settings["import_url"]

            def worker():
                try:
                    result = station_import.run_import(import_url, progress=import_state)
                    import_state.finish(result)
                    if result["added"]:
                        state.request_reload()
                except Exception as e:
                    log.exception("⚠ Sender-Import fehlgeschlagen (%s).", import_url)
                    import_state.fail(str(e))

            threading.Thread(target=worker, daemon=True, name="import").start()
            self._send_json({"ok": True})

        def _handle_library_scan_start(self):
            # Backend-Trigger für Phase 1 der Musik-Library-Roadmap (siehe
            # README/CLAUDE.md): rekursiver ID3-Scan der aktuell unter
            # music_library.path konfigurierten Musiksammlung in
            # music_library.db. Bewusst OHNE UI-Anschluss in dieser Phase --
            # nur der Endpoint, siehe SESSION.md. Gleiches Hintergrund-
            # Thread-/Progress-Poll-Muster wie _handle_import_start() oben.
            if not library_scan_state.start():
                self._send_json({
                    "ok": False,
                    "error": "Es läuft bereits ein Musik-Scan.",
                }, status=409)
                return

            root = state.music_library_path

            def worker():
                try:
                    result = music_scan.scan_library(
                        root, db_path=music_library_db_path,
                        covers_dir=music_library_covers_dir, progress=library_scan_state)
                    library_scan_state.finish(result)
                except Exception as e:
                    log.exception("⚠ Musik-Scan fehlgeschlagen (%s).", root)
                    library_scan_state.fail(str(e))

            threading.Thread(target=worker, daemon=True, name="libscan").start()
            self._send_json({"ok": True})

        def _handle_library_duplicates(self):
            # Duplikat-Erkennung (siehe music_query.find_duplicates(),
            # README/CLAUDE.md) -- reiner Lese-Endpoint, bewusst OHNE
            # UI-Anschluss in dieser Runde (Nutzerwunsch: erst nur
            # anzeigen/melden, keine Lösch-Aktion). Gleiches Muster wie
            # die anderen Query-Aufrufe in _handle_music_play(): kurzlebige
            # SQLite-Connection direkt im Webserver-Thread.
            duplicates = music_query.find_duplicates(music_library_db_path)
            self._send_json({"ok": True, "count": len(duplicates), "duplicates": duplicates})

        def _handle_music_play(self):
            # Ein Endpoint für beide Fälle (Grundgerüst-Ordner-Play UND
            # Phase-2-Query-Play), Unterscheidung über den optionalen
            # "query"-Body -- kein zweiter Endpoint, siehe CLAUDE.md/
            # SESSION.md zu Phase 2. Die eigentliche SQLite-Abfrage läuft
            # HIER synchron im Webserver-Thread (kurzlebige Connection,
            # gleicher Grund wie bei fingerprint.delete_clip() -- sqlite3-
            # Connections sind nicht thread-übergreifend sicher), NICHT im
            # Hauptloop: der ~1s-Analysetakt darf nie auf eine Query
            # warten. Dadurch kann diese Methode "keine Treffer" auch
            # SOFORT beantworten, ohne überhaupt einen Request an den
            # Hauptloop zu schicken.
            payload = self._read_json_body()
            query = payload.get("query")
            if not query:
                state.request_music_play()
                self._send_json({"ok": True})
                return

            q_type = query.get("type")
            q_value = str(query.get("value") or "")
            if q_type == "artist":
                tracks = music_query.query_by_artist(music_library_db_path, q_value)
            elif q_type == "genre":
                tracks = music_query.query_by_genre(music_library_db_path, q_value)
            elif q_type == "tempo":
                try:
                    tracks = music_query.query_by_tempo(music_library_db_path, q_value)
                except ValueError as e:
                    self._send_json({"ok": False, "error": str(e)}, status=400)
                    return
            else:
                self._send_json({"ok": False, "error": f"Unbekannter Query-Typ: {q_type!r}"}, status=400)
                return

            if not tracks:
                # Bei "tempo" ist q_value "fast"/"slow" (music_query.py-
                # interner Modus-Name) -- fürs Frontend die deutsche
                # Button-Beschriftung zeigen, nicht den rohen Modus-String.
                shown_value = {"fast": "schnell", "slow": "langsam"}.get(q_value, q_value) \
                    if q_type == "tempo" else q_value
                self._send_json({"ok": False, "error": f"Keine Treffer für '{shown_value}'."})
                return
            state.request_music_play(tracks=tracks)
            self._send_json({"ok": True, "track_count": len(tracks)})

        def _handle_status_wait(self):
            """Long-Poll-Fast-Path für die Web-UI: blockiert bis zu
            _STATUS_WAIT_TIMEOUT Sekunden, bis state.version sich ändert
            (Senderwechsel, News-Break-Start/Ende, Filter-Toggle -- siehe
            SwitcherState.wait_for_change()), oder gibt nach Timeout einfach
            den aktuellen Stand zurück (Heartbeat, falls sich in der
            Wartezeit gar nichts tut). Das Frontend hängt sofort den
            nächsten Long-Poll mit der zurückgegebenen Version dran ->
            ein echter Zustandswechsel kommt so binnen Millisekunden an,
            statt frühestens beim nächsten Intervall-Poll-Tick."""
            query = urllib.parse.urlsplit(self.path).query
            params = urllib.parse.parse_qs(query)
            try:
                known_version = int(params.get("version", ["0"])[0])
            except ValueError:
                known_version = 0
            state.wait_for_change(known_version, timeout=_STATUS_WAIT_TIMEOUT)
            self._send_json(_build_status(state, icecast_cfg, host_paths))

        def _handle_skip(self):
            # "ZAPPEN!"-Knopf: Nutzer hat selbst Sprache erkannt, auch
            # wenn VAD/Heuristik (noch) nicht angeschlagen haben -> sofort
            # weiterschalten, ohne auf die automatische Erkennung zu warten.
            log.info("🎛  Web-Interface: '⚡ ZAPPEN!' gedrückt.")
            state.request_skip()
            self._send_json({"ok": True})

        def _handle_news_break_skip(self):
            # Eigener Skip-Knopf NUR für eine laufende Nachrichten-Pause
            # (Nutzer-Wunsch, siehe SESSION.md) -- wählt nur eine ANDERE
            # MP3 aus demselben Ordner, anders als "ZAPPEN!" oben (das die
            # Pause komplett beendet). Kein Guard auf news_break_active
            # hier -- der Hauptloop ignoriert die Anfrage selbst, falls
            # gerade keine Pause läuft (siehe radiosabbelnich.py), und der
            # Button ist im Web-Interface ohnehin nur während einer Pause
            # aktiv.
            log.info("🎛  Web-Interface: Nachrichten-Pause-Skip gedrückt.")
            state.request_news_break_skip()
            self._send_json({"ok": True})

        def _handle_filter_toggle(self):
            # "Sabbelfilter (de)aktivieren"-Knopf: automatische Erkennung
            # komplett pausieren/wieder anschalten. Der Hauptloop wendet
            # den Request an (inkl. Zurücksetzen der Streak-Buchhaltung),
            # der tatsächliche neue Zustand kommt beim nächsten
            # /api/status-Poll an.
            log.info("🎛  Web-Interface: Sabbelfilter-Umschaltung angefordert "
                     "(aktuell %s).", "an" if state.filter_enabled else "aus")
            state.request_filter_toggle()
            self._send_json({"ok": True})

        def _handle_fingerprint_undo(self):
            # "Zapping-Fehler"-Knopf: der letzte automatische Wechsel kam
            # von einem Fingerprint-Treffer, der sich im Nachhinein als
            # falsch/unerwünscht rausstellt -> den zugrundeliegenden Clip
            # aus der DB werfen (damit er nicht weiter fälschlich matcht)
            # UND zurück zu dem Sender schalten, der vor dem Treffer lief.
            log.info("🎛  Web-Interface: '🛑 Zapping-Fehler' gedrückt.")
            clip = state.pop_last_fingerprint_clip()
            if clip is None:
                self._send_json({
                    "ok": False,
                    "error": "Kein kürzlicher Fingerprint-Treffer zum Zurücknehmen.",
                }, status=404)
                return

            try:
                label = fingerprint.delete_clip(fingerprint_db_path, clip["clip_id"])
            except Exception as e:
                log.exception("⚠ Clip #%s löschen fehlgeschlagen.", clip["clip_id"])
                self._send_json({"ok": False, "error": f"Datenbankfehler: {e}"}, status=500)
                return

            switched_back_to = None
            prev_id = clip.get("previous_station_id")
            if prev_id:
                station = next((s for s in state.active_stations if s["id"] == prev_id), None)
                if station is not None:
                    state.request_switch(prev_id)
                    switched_back_to = station["name"]

            if label is None and switched_back_to is None:
                self._send_json({
                    "ok": False,
                    "error": "Clip war schon nicht mehr in der Datenbank, und der "
                             "vorherige Sender ist nicht mehr aktiv.",
                }, status=404)
                return
            self._send_json({"ok": True, "label": label, "switched_back_to": switched_back_to})

        def _handle_switch(self):
            payload = self._read_json_body()
            station_id = payload.get("id")
            active_ids = {s["id"] for s in state.active_stations}
            if isinstance(station_id, str) and station_id in active_ids:
                log.info("🎛  Web-Interface: manueller Switch auf '%s' angefordert.", station_id)
                state.request_switch(station_id)
                self._send_json({"ok": True})
            else:
                self._send_json({"ok": False, "error": "invalid id"}, status=400)

        def _handle_switch_relative(self, direction: int):
            # Für die mobile/PWA-Ansicht: "Vorheriger/Nächster Sender" ohne
            # dass das Frontend die komplette Rotationsreihenfolge kennen
            # muss -- läuft über denselben request_switch()-Pfad wie jeder
            # andere manuelle Wechsel, ermittelt hier nur vorab die Ziel-ID.
            active = state.active_stations
            if not active:
                self._send_json({"ok": False, "error": "keine aktiven Sender"}, status=400)
                return
            ids = [s["id"] for s in active]
            try:
                idx = ids.index(state.current_id)
            except ValueError:
                # state.current_id zeigt während einer Nachrichten-Pause auf
                # die synthetische News-Break-ID (siehe SwitcherState.
                # set_news_break()), die nicht in der Rotation steckt -- als
                # Anker dann so tun, als stünde man "vor" dem ersten bzw.
                # "nach" dem letzten Sender, damit Vor/Zurück trotzdem in
                # der Liste landet statt mit einem Fehler ins Leere zu laufen.
                idx = -1 if direction > 0 else 0
            target = ids[(idx + direction) % len(ids)]
            log.info("🎛  Web-Interface: %s Sender angefordert ('%s').",
                     "nächster" if direction > 0 else "vorheriger", target)
            state.request_switch(target)
            self._send_json({"ok": True, "id": target})

        def _handle_add_station(self):
            payload = self._read_json_body()
            try:
                station = stations_store.add(
                    payload.get("name", ""), payload.get("url", ""),
                    payload.get("category", ""), payload.get("enabled", True),
                )
                state.request_reload()
                self._send_json({"ok": True, "station": station})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)

        def _handle_station_action(self):
            # Pfadschema: /api/config/stations/<id>[/delete|/toggle]
            rest = self.path[len("/api/config/stations/"):]
            parts = [p for p in rest.split("/") if p]
            if not parts:
                self.send_error(404)
                return
            station_id = parts[0]
            action = parts[1] if len(parts) > 1 else None
            payload = self._read_json_body()

            try:
                if action is None:
                    station = stations_store.update(
                        station_id, payload.get("name", ""), payload.get("url", ""),
                        payload.get("category", ""), payload.get("enabled", True),
                    )
                    state.request_reload()
                    self._send_json({"ok": True, "station": station})
                elif action == "toggle":
                    station = stations_store.set_enabled(station_id, bool(payload.get("enabled", True)))
                    state.request_reload()
                    self._send_json({"ok": True, "station": station})
                elif action == "delete":
                    stations_store.delete(station_id)
                    state.request_reload()
                    self._send_json({"ok": True})
                else:
                    self.send_error(404)
            except KeyError:
                self._send_json({"ok": False, "error": "Sender nicht gefunden"}, status=404)
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)

    return Handler


def start_server(port: int, state: SwitcherState, icecast_cfg: dict,
                  fingerprint_db_path: str, tls_cert_file: str = None,
                  tls_key_file: str = None, host_paths: dict = None,
                  log_file_path: str = None,
                  music_library_db_path: str = music_scan.MUSIC_LIBRARY_DB_FILE,
                  music_library_covers_dir: str = music_scan.MUSIC_LIBRARY_COVERS_DIR
                  ) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("0.0.0.0", port),
                                 make_handler(state, icecast_cfg, fingerprint_db_path,
                                              host_paths, log_file_path,
                                              music_library_db_path, music_library_covers_dir))
    # Nur hier gestartet, nicht in make_handler() -- start_server() läuft
    # laut radiosabbelnich.py main() ohnehin nur bei webui_port != 0 (siehe
    # "if args.webui_port:"), ein isolierter Testlauf mit --webui-port 0
    # (siehe CLAUDE.md-Testmuster) bekommt dadurch automatisch auch KEINEN
    # Update-Check-Hintergrund-Thread -- kein ungewollter echter
    # Internet-Request bei einem lokalen Testlauf.
    update_check.UpdateChecker(
        get_settings=lambda: settings_store.load()["update_check"],
        get_local_version=lambda: _VERSION_STRING,
        on_result=settings_store.record_update_check_result,
    ).start()
    scheme = "http"
    if tls_cert_file and tls_key_file:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=tls_cert_file, keyfile=tls_key_file)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            scheme = "https"
        except (ssl.SSLError, OSError) as e:
            # tls_enabled ist an, aber TLS_CERT_FILE/TLS_KEY_FILE sind in
            # .env nicht (oder falsch) gesetzt -- Docker mountet dann
            # /dev/null an die erwarteten Pfade (siehe docker-compose.yml),
            # load_cert_chain() scheitert daran. Lieber mit Klartext-HTTP
            # weiterlaufen als den ganzen Container abstürzen zu lassen.
            log.warning("⚠ TLS-Zertifikat (%s) nicht nutzbar (%s) — "
                        "Web-Interface läuft nur über HTTP.", tls_cert_file, e)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="webui")
    thread.start()
    log.info("🌐 Web-Interface läuft auf Port %d (%s)", port, scheme)
    log.debug("Webserver-Thread gestartet (Port %d, Icecast-Admin: %s)",
              port, icecast_cfg.get("admin_url") or "nicht konfiguriert")
    return httpd
