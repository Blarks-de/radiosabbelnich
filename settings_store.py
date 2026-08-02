#!/usr/bin/env python3
"""
settings_store.py — Laufzeit-Einstellungen, persistiert in settings.json.

Prebuffer-Parameter (wie viele Sekunden/Sender im Voraus gepuffert
werden, siehe PrebufferedSource in radiozapper.py) und die Import-URL
für den Sender-Import (station_import.py). Analog zu stations_store.py:
eigener Lock, direktes Schreiben statt write-temp-then-rename
(settings.json ist wie stations.json einzeln gebindmountet, os.replace()
schlägt darüber mit "Device or resource busy" fehl).
"""

import json
import os
import threading

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULTS = {
    "prebuffer_seconds": 10.0,
    "prebuffer_count": 5,
    "import_url": "http://bit.ly/kn-kodi-radio",
}

# (min, max) — grobe Leitplanken gegen Tippfehler/Unsinn, nicht als
# strenge Produktentscheidung gedacht.
LIMITS = {
    "prebuffer_seconds": (0.0, 60.0),
    "prebuffer_count": (0, 20),
}

_lock = threading.Lock()


def _read_raw() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        _write(DEFAULTS)
        return dict(DEFAULTS)
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    return merged


def _write(data: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load() -> dict:
    with _lock:
        return _read_raw()


def update(prebuffer_seconds=None, prebuffer_count=None, import_url=None) -> dict:
    """Aktualisiert nur die übergebenen Felder (None = unverändert lassen),
    validiert. Wirft ValueError bei ungültigen Werten."""
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
        _write(data)
        return data
