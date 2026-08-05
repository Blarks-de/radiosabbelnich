#!/usr/bin/env python3
"""
settings_store.py — Laufzeit-Einstellungen, persistiert in settings.json.

Prebuffer-Parameter (wie viele Sekunden/Sender im Voraus gepuffert
werden, siehe PrebufferedSource in radiozapper.py), die Import-URL für
den Sender-Import (station_import.py) und die Nachrichten-Pause-
Einstellungen (news_break.py) — alles flach validiert bis auf
"news_break", das als eigener verschachtelter Block gespeichert wird.
Analog zu stations_store.py: eigener Lock, direktes Schreiben statt
write-temp-then-rename (settings.json ist wie stations.json einzeln
gebindmountet, os.replace() schlägt darüber mit "Device or resource
busy" fehl).
"""

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
    "news_break": {
        "enabled": False,
        # Container-interner Pfad — siehe docker-compose.yml
        # (NEWS_MP3_FOLDER-Bind-Mount) und README. NICHT der Host-Pfad.
        "mp3_folder": "/app/news_mp3",
        "window_minutes": 2.0,
        "enabled_hours": None,  # None = rund um die Uhr, sonst [start, end), z.B. [6, 22]
    },
    "stt_filter": {
        "enabled": False,
        "engine": "vosk",              # "vosk" | "whisper"
        # Container-interner Pfad — siehe docker-compose.yml
        # (VOSK_MODEL_FOLDER-Bind-Mount) und README. NICHT der Host-Pfad.
        "vosk_model_path": "/app/vosk-model-de",
        "whisper_model_size": "tiny",  # "tiny" | "base" (o.ä., siehe faster-whisper)
        "sample_interval_seconds": 8.0,
        # Empirisch aus echten Sendern hergeleitet (siehe SESSION.md):
        # Deutschlandfunk-Sprache lag in 10 Clips nie unter 0.83, Schlager-
        # Gesang (ndr-schlager/radio-paloma/schlagerparadies) im Schnitt
        # bei 0.38 -- 0.75 liegt mit Marge unter dem Sprache-Minimum, damit
        # reale Moderation sicher erkannt wird, filtert aber den Großteil
        # des gesungenen Schlagers heraus (bei 0.6 wären es nur ~60%
        # gewesen statt ~80% bei 0.75).
        "confidence_threshold": 0.75,
        "combine_mode": "and",          # "and" | "or" — siehe stt_filter.combine_label()
    },
}

# (min, max) — grobe Leitplanken gegen Tippfehler/Unsinn, nicht als
# strenge Produktentscheidung gedacht.
LIMITS = {
    "prebuffer_seconds": (0.0, 60.0),
    "prebuffer_count": (0, 20),
    "news_break_window_minutes": (0.1, 15.0),
    "stt_sample_interval_seconds": (2.0, 60.0),
    "stt_confidence_threshold": (0.0, 1.0),
}

STT_ENGINES = {"vosk", "whisper"}
STT_COMBINE_MODES = {"and", "or"}

_lock = threading.Lock()

# Sentinel für update(news_break_enabled_hours=...): unterscheidet "Feld
# nicht übergeben" (unverändert lassen, wie bei allen anderen Parametern
# durch den None-Default) von explizitem None ("enabled_hours löschen" —
# der gültige Fachwert für "rund um die Uhr"). Siehe update()-Docstring.
UNSET = object()


def _defaults_copy() -> dict:
    """dict(DEFAULTS) reicht nicht — "news_break"/"stt_filter" sind selbst
    dicts, ein flacher copy() würde sie mit dem Modul-weiten DEFAULTS-
    Objekt teilen statt kopieren."""
    return {**DEFAULTS, "news_break": dict(DEFAULTS["news_break"]),
            "stt_filter": dict(DEFAULTS["stt_filter"])}


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
        elif k == "stt_filter" and isinstance(v, dict):
            # Gleiches Muster wie "news_break" direkt oben: ein
            # settings.json von vor diesem Feature funktioniert dadurch
            # unverändert weiter.
            merged["stt_filter"].update(
                {kk: vv for kk, vv in v.items() if kk in DEFAULTS["stt_filter"]}
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
           news_break_enabled=None, news_break_mp3_folder=None,
           news_break_window_minutes=None, news_break_enabled_hours=UNSET,
           stt_filter_enabled=None, stt_filter_engine=None,
           stt_filter_vosk_model_path=None, stt_filter_whisper_model_size=None,
           stt_filter_sample_interval_seconds=None, stt_filter_confidence_threshold=None,
           stt_filter_combine_mode=None) -> dict:
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

    stt_filter_vosk_model_path/stt_filter_whisper_model_size werden aus
    demselben Grund NICHT auf Existenz geprüft — die eigentliche Prüfung
    (Modell ladbar?) passiert erst beim Laden in stt_filter.py, das sich
    bei einem ungültigen Pfad selbst deaktiviert statt hier schon einen
    Fehler zu werfen."""
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

        stt = data["stt_filter"]
        if stt_filter_enabled is not None:
            stt["enabled"] = bool(stt_filter_enabled)
        if stt_filter_engine is not None:
            stt_filter_engine = str(stt_filter_engine).strip()
            if stt_filter_engine not in STT_ENGINES:
                raise ValueError(f"stt_filter_engine muss eine von {sorted(STT_ENGINES)} sein.")
            stt["engine"] = stt_filter_engine
        if stt_filter_vosk_model_path is not None:
            stt["vosk_model_path"] = str(stt_filter_vosk_model_path).strip()
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
        if stt_filter_confidence_threshold is not None:
            lo, hi = LIMITS["stt_confidence_threshold"]
            try:
                stt_filter_confidence_threshold = float(stt_filter_confidence_threshold)
            except (TypeError, ValueError):
                raise ValueError("stt_filter_confidence_threshold muss eine Zahl sein.")
            if not (lo <= stt_filter_confidence_threshold <= hi):
                raise ValueError(f"stt_filter_confidence_threshold muss zwischen {lo} und {hi} liegen.")
            stt["confidence_threshold"] = stt_filter_confidence_threshold
        if stt_filter_combine_mode is not None:
            stt_filter_combine_mode = str(stt_filter_combine_mode).strip()
            if stt_filter_combine_mode not in STT_COMBINE_MODES:
                raise ValueError(f"stt_filter_combine_mode muss eine von {sorted(STT_COMBINE_MODES)} sein.")
            stt["combine_mode"] = stt_filter_combine_mode

        _write(data)
        log.info("⚙ Einstellungen gespeichert: %s", data)
        return data
