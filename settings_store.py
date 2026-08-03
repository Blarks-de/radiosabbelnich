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

log = logging.getLogger("settings")

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULTS = {
    "prebuffer_seconds": 10.0,
    "prebuffer_count": 5,
    "import_url": "http://bit.ly/kn-kodi-radio",
    "news_break": {
        "enabled": False,
        # Container-interner Pfad — siehe docker-compose.yml
        # (NEWS_MP3_FOLDER-Bind-Mount) und README. NICHT der Host-Pfad.
        "mp3_folder": "/app/news_mp3",
        "window_minutes": 2.0,
        "enabled_hours": None,  # None = rund um die Uhr, sonst [start, end), z.B. [6, 22]
    },
}

# (min, max) — grobe Leitplanken gegen Tippfehler/Unsinn, nicht als
# strenge Produktentscheidung gedacht.
LIMITS = {
    "prebuffer_seconds": (0.0, 60.0),
    "prebuffer_count": (0, 20),
    "news_break_window_minutes": (0.1, 15.0),
}

_lock = threading.Lock()

# Sentinel für update(news_break_enabled_hours=...): unterscheidet "Feld
# nicht übergeben" (unverändert lassen, wie bei allen anderen Parametern
# durch den None-Default) von explizitem None ("enabled_hours löschen" —
# der gültige Fachwert für "rund um die Uhr"). Siehe update()-Docstring.
UNSET = object()


def _defaults_copy() -> dict:
    """dict(DEFAULTS) reicht nicht — "news_break" ist selbst ein dict,
    ein flacher copy() würde ihn mit dem Modul-weiten DEFAULTS-Objekt
    teilen statt kopieren."""
    return {**DEFAULTS, "news_break": dict(DEFAULTS["news_break"])}


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
           news_break_enabled=None, news_break_mp3_folder=None,
           news_break_window_minutes=None, news_break_enabled_hours=UNSET) -> dict:
    """Aktualisiert nur die übergebenen Felder (None = unverändert lassen),
    validiert. Wirft ValueError bei ungültigen Werten.

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
    passiert erst zur Laufzeit in news_break.pick_random_mp3()."""
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

        _write(data)
        log.info("⚙ Einstellungen gespeichert: %s", data)
        return data
