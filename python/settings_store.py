#!/usr/bin/env python3
# Copyright (C) 2026 RadioSabbelNich
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 (or
# later), as published by the Free Software Foundation. See LICENSE.

"""
settings_store.py — Laufzeit-Einstellungen, persistiert in settings.json.

Prebuffer-Parameter (wie viele Sekunden/Sender im Voraus gepuffert
werden, siehe PrebufferedSource in radiosabbelnich.py), die Import-URL für
den Sender-Import (station_import.py) und die Nachrichten-Pause-
Einstellungen (news_break.py) — alles flach validiert bis auf
"news_break", das als eigener verschachtelter Block gespeichert wird.
Analog zu stations_store.py: eigener Lock, direktes Schreiben statt
write-temp-then-rename (settings.json ist wie stations.json einzeln
gebindmountet, os.replace() schlägt darüber mit "Device or resource
busy" fehl).
"""

import copy
import json
import logging
import os
import threading

import i18n

log = logging.getLogger("settings")

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULTS = {
    "prebuffer_seconds": 10.0,
    "prebuffer_count": 5,
    "import_url": "http://bit.ly/kn-kodi-radio",
    # Leer = automatisch aus der Adresse gebildet, über die der Browser das
    # Web-Interface gerade aufruft (siehe webui.py/_PAGE_HTML). Nur setzen,
    # wenn die tatsächliche öffentliche Adresse davon abweicht.
    "stream_url": "",
    # Ob webui.py sein Server-Socket in TLS einwickelt (siehe
    # webui.start_server()). Wirkungslos ohne TLS_CERT_FILE/TLS_KEY_FILE in
    # .env (siehe docker-compose.yml) UND wirkt erst nach einem Neustart
    # des Containers -- anders als die meisten anderen Einstellungen hier
    # kann ein laufender ThreadingHTTPServer sein Socket nicht im laufenden
    # Betrieb neu einwickeln.
    "tls_enabled": False,
    # Sprache des Web-Interfaces ("de"/"en", siehe i18n.py). Startwert kommt
    # aus UI_LANGUAGE in .env (i18n.DEFAULT_LANGUAGE) -- gilt nur für eine
    # Neuinstallation ohne bestehende settings.json (siehe _read_raw()).
    # Einmal über /config geändert, gewinnt danach immer der hier
    # gespeicherte Wert, exakt wie bei allen anderen Feldern hier.
    "language": i18n.DEFAULT_LANGUAGE,
    # Radio (Stream-Switching, STT/VAD aktiv) vs. Musiksammlung (lokale
    # Dateien, STT/VAD komplett deaktiviert) -- siehe radiosabbelnich.py
    # main()-Loop, Modus-Fork ganz oben. Persistiert, damit der Modus einen
    # Container-Neustart übersteht (Nutzer-Vorgabe).
    "current_mode": "radio",
    "music_library": {
        # Container-interner Pfad, gleiches Muster wie news_break.mp3_folder
        # unten -- siehe docker-compose.yml (MUSIC_LIBRARY_FOLDER-Bind-Mount)
        # und README. NICHT der Host-Pfad. Default = Mount-Root selbst.
        "path": "/app/music_library",
    },
    "news_break": {
        "enabled": False,
        # Container-interner Pfad — siehe docker-compose.yml
        # (NEWS_MP3_FOLDER-Bind-Mount) und README. NICHT der Host-Pfad.
        "mp3_folder": "/app/news_mp3",
        "window_minutes": 2.0,
        "enabled_hours": None,  # None = rund um die Uhr, sonst [start, end), z.B. [6, 22]
        # Werbeblock-Vorbuffering (siehe ARCHITECTURE.md, Abschnitt
        # "Nachrichten-Pause"/SESSION.md 2026-08-21 Phase 2): hört den
        # pausierten Sender in den letzten ad_prebuffer_lead_seconds der
        # laufenden Pause-MP3 schon im Hintergrund mit, um beim Pause-Ende
        # direkt in die Musik einzusteigen statt in einen Werbeblock.
        # Default AUS -- reine Zusatzoptimierung, kein Kernfeature.
        "ad_prebuffer_enabled": False,
        "ad_prebuffer_lead_seconds": 20.0,
        # Sprache-Gate (siehe ARCHITECTURE.md/SESSION.md 2026-08-21):
        # verhindert, dass die Pause rein zeitbasiert startet, während der
        # Live-Sender noch erkennbar Musik spielt. Bei enabled=True muss
        # ZUSÄTZLICH zum Zeitfenster (window_minutes) auch ein enges,
        # eigenes Toleranzfenster (speech_gate_window_minutes) UND
        # speech_gate_streak aufeinanderfolgende Sprache-Analysefenster
        # (analog CONSECUTIVE_MUSIC_TO_CONFIRM beim Ad-Prebuffer) erfüllt
        # sein. Default AUS -- bestehendes rein zeitbasiertes Verhalten
        # bricht dadurch nicht überraschend.
        "require_speech_in_window": False,
        "speech_gate_window_minutes": 2.0,
        "speech_gate_streak": 3,
    },
    "stt_filter": {
        "enabled": False,
        "engine": "vosk",              # "vosk" | "whisper" -- GLOBAL, gilt für alle Sprachen
                                         # gleichzeitig (siehe stt_filter.py-Moduldocstring: nie
                                         # beide Engines gleichzeitig geladen, RAM-Gründe)
        "whisper_model_size": "tiny",  # "tiny" | "base" (o.ä., siehe faster-whisper) -- ebenfalls
                                         # GLOBAL: faster-whisper ist von Haus aus multilingual,
                                         # ein Sprachwechsel braucht bei dieser Engine KEIN neues
                                         # Modell, nur einen anderen language=-Parameter pro
                                         # transcribe()-Aufruf (siehe stt_filter.py)
        "sample_interval_seconds": 8.0,
        "combine_mode": "and",          # "and" | "or" — siehe stt_filter.combine_label()
        # Pro Sprache: vosk_model_path (Container-interner Pfad, NUR für
        # engine="vosk" relevant -- siehe docker-compose.yml/README für den
        # Bind-Mount) + confidence_threshold (siehe stt_filter.py, empirisch
        # pro Sprache zu ermitteln, nicht aus der Theorie ableitbar -- der
        # de-Default 0.75 ist so hergeleitet, siehe SESSION.md: Deutschlandfunk-
        # Sprache lag in 10 Clips nie unter 0.83, Schlager-Gesang im Schnitt
        # bei 0.38, 0.75 liegt mit Marge dazwischen).
        "languages": {
            "de": {"vosk_model_path": "/app/vosk-model-de", "confidence_threshold": 0.75},
        },
        # Sender-Kategorie (siehe stations_store.CATEGORIES) -> Sprachcode
        # (Schlüssel in "languages" oben). Fehlt eine Kategorie hier, gilt
        # DEFAULT_STT_LANGUAGE -- ein frischer Server ohne jede Zuordnung
        # verhält sich dadurch exakt wie vor diesem Feature (alles Deutsch).
        "category_languages": {},
    },
    # Song-Erkennung (siehe ARCHITECTURE.md, Abschnitt "Song-Erkennung",
    # und python/song_fingerprint.py): lokaler Chromaprint-Fingerprint-
    # Cache (Phase 1) + optionaler AudD-Cloud-Lookup bei Cache-Miss
    # (Phase 2). Default AUS wie jedes neue Feature.
    "song_recognition": {
        "enabled": False,
        "interval_seconds": 45.0,
        "snippet_seconds": 11.0,  # < AudDs 12s-Limit
        # Eigener Schalter, UNABHÄNGIG von "enabled" oben: lokales
        # Fingerprinting kostet nichts, ein Cloud-Lookup schon (AudD-
        # Kontingent) -- wer Phase 1 schon nutzt, soll nicht überraschend
        # anfangen, für jeden neuen Song Cloud-Requests abzusetzen. Greift
        # nur, wenn zusätzlich AUDD_API_TOKEN gesetzt ist (siehe .env).
        "cloud_lookup_enabled": False,
        # Platzhalter, NICHT empirisch kalibriert (siehe SESSION.md-Muster
        # bei stt_filter.confidence_threshold 0.75) -- vor produktivem
        # Einsatz gegen echtes Stream-Audio nachjustieren.
        "similarity_threshold": 0.65,
    },
    # Automatische Update-Prüfung für die Docker-Installation (siehe
    # ARCHITECTURE.md, Abschnitt "Automatische Update-Prüfung", und
    # python/update_check.py): reiner Lesezugriff gegen
    # raw.githubusercontent.com, kein Auto-Update -- es gibt kein
    # Image-Registry-Deployment, nur `git pull`. Default AN, ANDERS als
    # jedes sonstige neue Feature hier (vgl. song_recognition oben):
    # kostet im Gegensatz zu STT/Fingerprinting/Song-Erkennung keine
    # laufende CPU/RAM, nur alle 24h ein einzelner HTTP-GET -- bewusste
    # Ausnahme, siehe README.md/ARCHITECTURE.md.
    "update_check": {
        "enabled": True,
        "last_checked_at": None,          # Unix-Timestamp, None = noch nie geprüft
        "last_known_remote_version": None,
        "update_available": False,
    },
}

# Fallback-Sprache für Kategorien ohne explizite Zuordnung in
# category_languages -- siehe resolve_stt_language().
DEFAULT_STT_LANGUAGE = "de"

# (min, max) — grobe Leitplanken gegen Tippfehler/Unsinn, nicht als
# strenge Produktentscheidung gedacht.
LIMITS = {
    "prebuffer_seconds": (0.0, 60.0),
    "prebuffer_count": (0, 20),
    "news_break_window_minutes": (0.1, 15.0),
    "news_break_ad_prebuffer_lead_seconds": (1.0, 120.0),
    "news_break_speech_gate_window_minutes": (0.1, 15.0),
    "news_break_speech_gate_streak": (1, 20),
    "stt_sample_interval_seconds": (2.0, 60.0),
    "stt_confidence_threshold": (0.0, 1.0),
    "song_recognition_interval_seconds": (5.0, 300.0),
    "song_recognition_snippet_seconds": (3.0, 30.0),
    "song_recognition_similarity_threshold": (0.0, 1.0),
}

STT_ENGINES = {"vosk", "whisper"}
STT_COMBINE_MODES = {"and", "or"}
CURRENT_MODES = {"radio", "music"}

_lock = threading.Lock()

# Sentinel für update(news_break_enabled_hours=...): unterscheidet "Feld
# nicht übergeben" (unverändert lassen, wie bei allen anderen Parametern
# durch den None-Default) von explizitem None ("enabled_hours löschen" —
# der gültige Fachwert für "rund um die Uhr"). Siehe update()-Docstring.
UNSET = object()


def _defaults_copy() -> dict:
    """dict(DEFAULTS) reicht nicht — "news_break"/"stt_filter" sind selbst
    dicts, ein flacher copy() würde sie mit dem Modul-weiten DEFAULTS-
    Objekt teilen statt kopieren. stt_filter.languages/category_languages
    sind seit der Mehrsprachigkeit (siehe SESSION.md) selbst wieder
    verschachtelte dicts -- deshalb hier deepcopy() statt eines weiteren
    Handkopie-Levels."""
    return {**DEFAULTS, "news_break": dict(DEFAULTS["news_break"]),
            "music_library": dict(DEFAULTS["music_library"]),
            "stt_filter": copy.deepcopy(DEFAULTS["stt_filter"]),
            "song_recognition": dict(DEFAULTS["song_recognition"]),
            "update_check": dict(DEFAULTS["update_check"])}


def _migrate_stt_filter(v: dict) -> dict:
    """Alte settings.json-Dateien (vor der Mehrsprachigkeit, siehe
    SESSION.md) hatten vosk_model_path/confidence_threshold flach in
    stt_filter statt pro Sprache in stt_filter.languages -- reine
    In-Memory-Migration beim Laden (wie das übrige Reconcile hier auch:
    kein Zurückschreiben nötig, der nächste echte set_stt_language()-
    Aufruf persistiert die neue Form ohnehin). Nur relevant, solange
    "languages" noch NICHT im geladenen JSON steht -- danach ist
    "languages" die alleinige Quelle der Wahrheit, alte Flachfelder
    werden ignoriert, damit ein Nutzer sie nicht versehentlich durch
    Zurückbearbeiten einer alten settings.json wiederbeleben kann."""
    if "languages" in v:
        return v
    old_path = v.get("vosk_model_path")
    old_threshold = v.get("confidence_threshold")
    if old_path is None and old_threshold is None:
        return v
    default_de = DEFAULTS["stt_filter"]["languages"]["de"]
    v = dict(v)
    v["languages"] = {
        "de": {
            "vosk_model_path": old_path if old_path is not None else default_de["vosk_model_path"],
            "confidence_threshold": old_threshold if old_threshold is not None else default_de["confidence_threshold"],
        }
    }
    log.info("⚙ settings.json: alte STT-Konfiguration (vosk_model_path=%r, "
             "confidence_threshold=%r) zu Sprache 'de' migriert.", old_path, old_threshold)
    return v


def _read_raw() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        _write(DEFAULTS)
        return _defaults_copy()
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    merged = _defaults_copy()
    for k, v in data.items():
        if k not in DEFAULTS:
            continue
        if k == "news_break" and isinstance(v, dict):
            # Nur bekannte Unterfelder übernehmen, Rest bei den Defaults
            # belassen -> ein settings.json von vor diesem Feature (ohne
            # "news_break") oder mit nur teilweise gesetzten Unterfeldern
            # funktioniert unverändert weiter.
            merged["news_break"].update(
                {kk: vv for kk, vv in v.items() if kk in DEFAULTS["news_break"]}
            )
        elif k == "music_library" and isinstance(v, dict):
            # Gleiches Muster wie "news_break" direkt oben: ein
            # settings.json von vor diesem Feature funktioniert dadurch
            # unverändert weiter (fehlt der Block komplett, bleibt der
            # Default-Root aus _defaults_copy() stehen).
            merged["music_library"].update(
                {kk: vv for kk, vv in v.items() if kk in DEFAULTS["music_library"]}
            )
        elif k == "stt_filter" and isinstance(v, dict):
            # Gleiches Muster wie "news_break" direkt oben: ein
            # settings.json von vor diesem Feature funktioniert dadurch
            # unverändert weiter. _migrate_stt_filter() zusätzlich davor,
            # weil "languages"/"category_languages" ERST mit der
            # Mehrsprachigkeit dazukamen (siehe dortiger Docstring).
            merged["stt_filter"].update(
                {kk: vv for kk, vv in _migrate_stt_filter(v).items() if kk in DEFAULTS["stt_filter"]}
            )
        elif k == "song_recognition" and isinstance(v, dict):
            # Gleiches Muster wie "news_break"/"music_library" oben: ein
            # settings.json von vor diesem Feature funktioniert dadurch
            # unverändert weiter.
            merged["song_recognition"].update(
                {kk: vv for kk, vv in v.items() if kk in DEFAULTS["song_recognition"]}
            )
        elif k == "update_check" and isinstance(v, dict):
            # Gleiches Muster wie "song_recognition" direkt oben: ein
            # settings.json von vor diesem Feature funktioniert dadurch
            # unverändert weiter (Default "enabled": True greift dann).
            merged["update_check"].update(
                {kk: vv for kk, vv in v.items() if kk in DEFAULTS["update_check"]}
            )
        else:
            merged[k] = v
    return merged


def _write(data: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load() -> dict:
    with _lock:
        return _read_raw()


def update(prebuffer_seconds=None, prebuffer_count=None, import_url=None,
           stream_url=None, tls_enabled=None, language=None,
           current_mode=None, music_library_path=None,
           news_break_enabled=None, news_break_mp3_folder=None,
           news_break_window_minutes=None, news_break_enabled_hours=UNSET,
           news_break_ad_prebuffer_enabled=None,
           news_break_ad_prebuffer_lead_seconds=None,
           news_break_require_speech_in_window=None,
           news_break_speech_gate_window_minutes=None,
           news_break_speech_gate_streak=None,
           stt_filter_enabled=None, stt_filter_engine=None,
           stt_filter_whisper_model_size=None,
           stt_filter_sample_interval_seconds=None,
           stt_filter_combine_mode=None,
           song_recognition_enabled=None,
           song_recognition_interval_seconds=None,
           song_recognition_snippet_seconds=None,
           song_recognition_similarity_threshold=None,
           song_recognition_cloud_lookup_enabled=None,
           update_check_enabled=None) -> dict:
    """Aktualisiert nur die übergebenen Felder (None = unverändert lassen),
    validiert. Wirft ValueError bei ungültigen Werten.

    stream_url folgt NICHT der None-Konvention der anderen String-Felder:
    ein leerer String ist hier ein gültiger Fachwert ("automatisch
    ermitteln", siehe DEFAULTS) und wird deshalb, anders als bei
    import_url, akzeptiert statt verworfen — nur None (Feld weggelassen)
    lässt den gespeicherten Wert unangetastet.

    news_break_enabled_hours ist eine Ausnahme von der None-Konvention:
    hier bedeutet None explizit "auf 'rund um die Uhr' zurücksetzen" (der
    gültige Fachwert), UNSET (Default) bedeutet "nicht übergeben,
    unverändert lassen". Ruft man diese Funktion mit dem üblichen
    News_break_enabled_hours=None, wird also aktiv zurückgesetzt, nicht
    ignoriert — Aufrufer, die das Feld unangetastet lassen wollen, lassen
    das Argument einfach weg.

    news_break_mp3_folder wird bewusst NICHT auf Existenz/Lesbarkeit
    geprüft — das ist typischerweise ein SMB-Mount, der beim Speichern der
    Einstellung noch nicht verfügbar sein kann. Die eigentliche Prüfung
    passiert erst zur Laufzeit in news_break.pick_random_mp3().

    music_library_path aus demselben Grund ebenfalls ungeprüft -- die
    eigentliche Prüfung passiert in music_library.list_tracks() beim
    nächsten Play-Klick. current_mode wird vom Hauptloop selbst gesetzt,
    NACHDEM der tatsächliche Übergang (Sender/Track stoppen, neu
    verbinden) abgeschlossen ist -- siehe radiosabbelnich.py main(), nicht
    direkt vom Toggle-Klick im Webserver-Thread (player-kritischer
    Zustand, gleiches request/pop-Prinzip wie beim manuellen Senderwechsel,
    siehe CLAUDE.md).

    stt_filter_whisper_model_size wird aus demselben Grund NICHT auf
    Existenz geprüft — die eigentliche Prüfung (Modell ladbar?) passiert
    erst beim Laden in stt_filter.py, das sich bei einem ungültigen Wert
    selbst deaktiviert statt hier schon einen Fehler zu werfen. Gleiches
    gilt für vosk_model_path PRO SPRACHE — das ist seit der
    Mehrsprachigkeit (siehe SESSION.md) kein Feld dieser Funktion mehr,
    sondern läuft über set_stt_language() weiter unten."""
    with _lock:
        data = _read_raw()
        if prebuffer_seconds is not None:
            lo, hi = LIMITS["prebuffer_seconds"]
            try:
                prebuffer_seconds = float(prebuffer_seconds)
            except (TypeError, ValueError):
                raise ValueError("prebuffer_seconds muss eine Zahl sein.")
            if not (lo <= prebuffer_seconds <= hi):
                raise ValueError(f"prebuffer_seconds muss zwischen {lo} und {hi} liegen.")
            data["prebuffer_seconds"] = prebuffer_seconds
        if prebuffer_count is not None:
            lo, hi = LIMITS["prebuffer_count"]
            try:
                prebuffer_count = int(prebuffer_count)
            except (TypeError, ValueError):
                raise ValueError("prebuffer_count muss eine Ganzzahl sein.")
            if not (lo <= prebuffer_count <= hi):
                raise ValueError(f"prebuffer_count muss zwischen {lo} und {hi} liegen.")
            data["prebuffer_count"] = prebuffer_count
        if import_url is not None:
            import_url = str(import_url).strip()
            if not import_url:
                raise ValueError("import_url darf nicht leer sein.")
            if not (import_url.startswith("http://") or import_url.startswith("https://")):
                raise ValueError("import_url muss mit http:// oder https:// beginnen.")
            data["import_url"] = import_url
        if stream_url is not None:
            stream_url = str(stream_url).strip()
            if stream_url and not (stream_url.startswith("http://") or stream_url.startswith("https://")):
                raise ValueError("stream_url muss leer sein (automatisch) oder mit "
                                  "http:// bzw. https:// beginnen.")
            data["stream_url"] = stream_url
        if tls_enabled is not None:
            data["tls_enabled"] = bool(tls_enabled)
        if language is not None:
            language = str(language).strip().lower()
            if language not in i18n.LANGUAGES:
                raise ValueError(f"language muss eine von {sorted(i18n.LANGUAGES)} sein.")
            data["language"] = language
        if current_mode is not None:
            current_mode = str(current_mode).strip().lower()
            if current_mode not in CURRENT_MODES:
                raise ValueError(f"current_mode muss eine von {sorted(CURRENT_MODES)} sein.")
            data["current_mode"] = current_mode
        if music_library_path is not None:
            data["music_library"]["path"] = str(music_library_path).strip()

        nb = data["news_break"]
        if news_break_enabled is not None:
            nb["enabled"] = bool(news_break_enabled)
        if news_break_mp3_folder is not None:
            nb["mp3_folder"] = str(news_break_mp3_folder).strip()
        if news_break_window_minutes is not None:
            lo, hi = LIMITS["news_break_window_minutes"]
            try:
                news_break_window_minutes = float(news_break_window_minutes)
            except (TypeError, ValueError):
                raise ValueError("news_break_window_minutes muss eine Zahl sein.")
            if not (lo <= news_break_window_minutes <= hi):
                raise ValueError(f"news_break_window_minutes muss zwischen {lo} und {hi} liegen.")
            nb["window_minutes"] = news_break_window_minutes
        if news_break_enabled_hours is not UNSET:
            if news_break_enabled_hours in (None, "", []):
                nb["enabled_hours"] = None
            else:
                try:
                    start, end = news_break_enabled_hours
                    start, end = int(start), int(end)
                except (TypeError, ValueError):
                    raise ValueError("news_break_enabled_hours muss [start, end] sein, z.B. [6, 22].")
                if not (0 <= start < end <= 24):
                    raise ValueError("news_break_enabled_hours muss 0 <= start < end <= 24 erfüllen "
                                      "(Übernacht-Fenster wie 22-6 werden nicht unterstützt).")
                nb["enabled_hours"] = [start, end]
        if news_break_ad_prebuffer_enabled is not None:
            nb["ad_prebuffer_enabled"] = bool(news_break_ad_prebuffer_enabled)
        if news_break_ad_prebuffer_lead_seconds is not None:
            lo, hi = LIMITS["news_break_ad_prebuffer_lead_seconds"]
            try:
                news_break_ad_prebuffer_lead_seconds = float(news_break_ad_prebuffer_lead_seconds)
            except (TypeError, ValueError):
                raise ValueError("news_break_ad_prebuffer_lead_seconds muss eine Zahl sein.")
            if not (lo <= news_break_ad_prebuffer_lead_seconds <= hi):
                raise ValueError(f"news_break_ad_prebuffer_lead_seconds muss zwischen {lo} und {hi} liegen.")
            nb["ad_prebuffer_lead_seconds"] = news_break_ad_prebuffer_lead_seconds
        if news_break_require_speech_in_window is not None:
            nb["require_speech_in_window"] = bool(news_break_require_speech_in_window)
        if news_break_speech_gate_window_minutes is not None:
            lo, hi = LIMITS["news_break_speech_gate_window_minutes"]
            try:
                news_break_speech_gate_window_minutes = float(news_break_speech_gate_window_minutes)
            except (TypeError, ValueError):
                raise ValueError("news_break_speech_gate_window_minutes muss eine Zahl sein.")
            if not (lo <= news_break_speech_gate_window_minutes <= hi):
                raise ValueError(f"news_break_speech_gate_window_minutes muss zwischen {lo} und {hi} liegen.")
            nb["speech_gate_window_minutes"] = news_break_speech_gate_window_minutes
        if news_break_speech_gate_streak is not None:
            lo, hi = LIMITS["news_break_speech_gate_streak"]
            try:
                news_break_speech_gate_streak = int(news_break_speech_gate_streak)
            except (TypeError, ValueError):
                raise ValueError("news_break_speech_gate_streak muss eine Ganzzahl sein.")
            if not (lo <= news_break_speech_gate_streak <= hi):
                raise ValueError(f"news_break_speech_gate_streak muss zwischen {lo} und {hi} liegen.")
            nb["speech_gate_streak"] = news_break_speech_gate_streak

        stt = data["stt_filter"]
        if stt_filter_enabled is not None:
            stt["enabled"] = bool(stt_filter_enabled)
        if stt_filter_engine is not None:
            stt_filter_engine = str(stt_filter_engine).strip()
            if stt_filter_engine not in STT_ENGINES:
                raise ValueError(f"stt_filter_engine muss eine von {sorted(STT_ENGINES)} sein.")
            stt["engine"] = stt_filter_engine
        if stt_filter_whisper_model_size is not None:
            stt["whisper_model_size"] = str(stt_filter_whisper_model_size).strip()
        if stt_filter_sample_interval_seconds is not None:
            lo, hi = LIMITS["stt_sample_interval_seconds"]
            try:
                stt_filter_sample_interval_seconds = float(stt_filter_sample_interval_seconds)
            except (TypeError, ValueError):
                raise ValueError("stt_filter_sample_interval_seconds muss eine Zahl sein.")
            if not (lo <= stt_filter_sample_interval_seconds <= hi):
                raise ValueError(f"stt_filter_sample_interval_seconds muss zwischen {lo} und {hi} liegen.")
            stt["sample_interval_seconds"] = stt_filter_sample_interval_seconds
        if stt_filter_combine_mode is not None:
            stt_filter_combine_mode = str(stt_filter_combine_mode).strip()
            if stt_filter_combine_mode not in STT_COMBINE_MODES:
                raise ValueError(f"stt_filter_combine_mode muss eine von {sorted(STT_COMBINE_MODES)} sein.")
            stt["combine_mode"] = stt_filter_combine_mode

        sr = data["song_recognition"]
        if song_recognition_enabled is not None:
            sr["enabled"] = bool(song_recognition_enabled)
        if song_recognition_interval_seconds is not None:
            lo, hi = LIMITS["song_recognition_interval_seconds"]
            try:
                song_recognition_interval_seconds = float(song_recognition_interval_seconds)
            except (TypeError, ValueError):
                raise ValueError("song_recognition_interval_seconds muss eine Zahl sein.")
            if not (lo <= song_recognition_interval_seconds <= hi):
                raise ValueError(f"song_recognition_interval_seconds muss zwischen {lo} und {hi} liegen.")
            sr["interval_seconds"] = song_recognition_interval_seconds
        if song_recognition_snippet_seconds is not None:
            lo, hi = LIMITS["song_recognition_snippet_seconds"]
            try:
                song_recognition_snippet_seconds = float(song_recognition_snippet_seconds)
            except (TypeError, ValueError):
                raise ValueError("song_recognition_snippet_seconds muss eine Zahl sein.")
            if not (lo <= song_recognition_snippet_seconds <= hi):
                raise ValueError(f"song_recognition_snippet_seconds muss zwischen {lo} und {hi} liegen.")
            sr["snippet_seconds"] = song_recognition_snippet_seconds
        if song_recognition_similarity_threshold is not None:
            lo, hi = LIMITS["song_recognition_similarity_threshold"]
            try:
                song_recognition_similarity_threshold = float(song_recognition_similarity_threshold)
            except (TypeError, ValueError):
                raise ValueError("song_recognition_similarity_threshold muss eine Zahl sein.")
            if not (lo <= song_recognition_similarity_threshold <= hi):
                raise ValueError(f"song_recognition_similarity_threshold muss zwischen {lo} und {hi} liegen.")
            sr["similarity_threshold"] = song_recognition_similarity_threshold
        if song_recognition_cloud_lookup_enabled is not None:
            sr["cloud_lookup_enabled"] = bool(song_recognition_cloud_lookup_enabled)

        if update_check_enabled is not None:
            data["update_check"]["enabled"] = bool(update_check_enabled)

        _write(data)
        log.info("⚙ Einstellungen gespeichert: %s", data)
        return data


def set_stt_language(lang_code: str, vosk_model_path: str = None,
                      confidence_threshold: float = None) -> dict:
    """Legt eine STT-Sprache an oder aktualisiert sie (gleiche None-
    Konvention wie update(): nur übergebene Felder ändern sich, ein
    bestehender Eintrag wird nicht überschrieben). Sprachcode wird NICHT
    gegen eine feste Liste geprüft -- Freitext statt fest programmierter
    Auswahl, weil Vosk-Modelle ohnehin selbst besorgt/gemountet werden
    müssen (siehe SESSION.md) und Whisper praktisch jeden Sprachcode
    akzeptiert, den faster-whisper kennt.

    vosk_model_path wird wie beim übrigen STT-Filter NICHT auf Existenz
    geprüft — die eigentliche Prüfung passiert lazy beim ersten Sample
    dieser Sprache in stt_filter.py, das sich bei einem ungültigen Pfad
    für genau diese Sprache selbst deaktiviert statt hier schon einen
    Fehler zu werfen."""
    lang_code = (lang_code or "").strip().lower()
    if not lang_code:
        raise ValueError("Sprachcode darf nicht leer sein.")
    with _lock:
        data = _read_raw()
        langs = data["stt_filter"]["languages"]
        entry = dict(langs.get(lang_code, {"vosk_model_path": "", "confidence_threshold": 0.6}))
        if vosk_model_path is not None:
            entry["vosk_model_path"] = str(vosk_model_path).strip()
        if confidence_threshold is not None:
            lo, hi = LIMITS["stt_confidence_threshold"]
            try:
                confidence_threshold = float(confidence_threshold)
            except (TypeError, ValueError):
                raise ValueError("confidence_threshold muss eine Zahl sein.")
            if not (lo <= confidence_threshold <= hi):
                raise ValueError(f"confidence_threshold muss zwischen {lo} und {hi} liegen.")
            entry["confidence_threshold"] = confidence_threshold
        langs[lang_code] = entry
        _write(data)
        log.info("🌐 STT-Sprache gespeichert: '%s' (%s)", lang_code, entry)
        return entry


def delete_stt_language(lang_code: str) -> None:
    """Mindestens eine Sprache muss konfiguriert bleiben (sonst hat
    engine="vosk" gar kein Modell mehr, das es laden könnte). Kategorien,
    die auf die gelöschte Sprache zeigten, werden aus category_languages
    entfernt statt als verwaiste Zuordnung stehen zu bleiben -- sie fallen
    danach auf DEFAULT_STT_LANGUAGE zurück, siehe resolve_stt_language()."""
    with _lock:
        data = _read_raw()
        langs = data["stt_filter"]["languages"]
        if lang_code not in langs:
            raise KeyError(lang_code)
        if len(langs) <= 1:
            raise ValueError("Mindestens eine Sprache muss konfiguriert bleiben.")
        del langs[lang_code]
        cat_langs = data["stt_filter"]["category_languages"]
        for cat in [c for c, lang in cat_langs.items() if lang == lang_code]:
            del cat_langs[cat]
        _write(data)
        log.info("🗑 STT-Sprache gelöscht: '%s'", lang_code)


def set_category_language(category: str, lang_code: str) -> None:
    """lang_code leer/None löscht die Zuordnung -- die Kategorie fällt
    dann auf DEFAULT_STT_LANGUAGE zurück (siehe resolve_stt_language()),
    statt einen ungültigen leeren Sprachcode zu speichern."""
    category = (category or "").strip()
    if not category:
        raise ValueError("category darf nicht leer sein.")
    lang_code = (lang_code or "").strip().lower()
    with _lock:
        data = _read_raw()
        cat_langs = data["stt_filter"]["category_languages"]
        if not lang_code:
            cat_langs.pop(category, None)
        else:
            cat_langs[category] = lang_code
        _write(data)
        log.info("🌐 Kategorie-Sprache gespeichert: '%s' -> '%s'", category, lang_code or "(Standard)")


def resolve_stt_language(category: str, cfg: dict) -> str:
    """Löst die für eine Sender-Kategorie zuständige STT-Sprache auf --
    fehlt die Kategorie in cfg["category_languages"], gilt
    DEFAULT_STT_LANGUAGE. cfg ist typischerweise state.stt_filter_cfg im
    Hauptloop (siehe radiosabbelnich.py), damit nicht bei jedem Aufruf frisch
    von der Platte gelesen wird."""
    return cfg.get("category_languages", {}).get(category, DEFAULT_STT_LANGUAGE)


def record_update_check_result(remote_version: str, update_available: bool,
                                checked_at: float) -> None:
    """Schreibt NUR die drei State-Felder von 'update_check' (siehe
    update_check.py, UpdateChecker) -- bewusst ohne die
    Validierungskette von update(), weil dieser Aufruf aus dem
    Hintergrund-Thread kommt, nicht aus einem validierten Nutzer-
    Request. 'enabled' selbst wird hier nie angefasst -- das bleibt
    ausschließlich über update(update_check_enabled=...) (Config-Seite)
    steuerbar."""
    with _lock:
        data = _read_raw()
        uc = data["update_check"]
        uc["last_checked_at"] = checked_at
        uc["last_known_remote_version"] = remote_version
        uc["update_available"] = bool(update_available)
        _write(data)
        log.info("🔄 Update-Check-Ergebnis gespeichert: Remote-Version %s, verfügbar: %s",
                 remote_version, update_available)
