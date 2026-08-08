#!/usr/bin/env python3
"""
stations_store.py — Laden/Speichern/Verwalten der Senderliste (stations.json).

Gemeinsam genutzt von keinsabbelradio.py (Playback-Rotation) und webui.py
(Config-Seite) — beide arbeiten auf derselben Datei/demselben Schema, damit
Änderungen über die Config-Seite ohne Sonderweg im laufenden Player
ankommen (keinsabbelradio.py pollt state.pop_reload_request() und liest bei
Bedarf per load_all()/load_active() neu ein).

Schema pro Sender: {"id": str, "name": str, "url": str, "category": str,
"enabled": bool}. "id" ist stabil (wird einmal beim Anlegen/Migrieren
vergeben) und überlebt Umbenennungen — Playback-Rotation und manuelles
Umschalten referenzieren Sender über "id", nicht über Listen-Position,
damit Hinzufügen/Löschen/Deaktivieren die laufende Wiedergabe nicht
durcheinanderbringt.
"""

import json
import logging
import os
import re
import threading

log = logging.getLogger("stations")

STATIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stations.json")

CATEGORIES = ["Lokal", "Regional", "National", "International", "Global", "Interstellar", "Unsortiert"]
DEFAULT_CATEGORY = "National"

# Kategorie für importierte Sender (station_import.py) — muss in
# CATEGORIES enthalten sein, absichtlich als letztes Element dort, damit
# sie auf der Config-Seite immer nach allen "richtigen" Kategorien
# erscheint (Config-Seite iteriert einfach in CATEGORIES-Reihenfolge).
IMPORT_CATEGORY = "Unsortiert"

DEFAULT_STATIONS = [
    {"name": "Radio Bob", "url": "https://streams.radiobob.de/bob-live/mp3-192/mediaplayer",
     "category": "National", "enabled": True},
    {"name": "1LIVE", "url": "https://wdr-1live-live.icecastssl.wdr.de/wdr/1live/live/mp3/128/stream.mp3",
     "category": "National", "enabled": True},
    {"name": "SWR3", "url": "https://liveradio.swr.de/sw282p3/swr3/play.mp3",
     "category": "National", "enabled": True},
]

# Schützt Lesen+Schreiben von stations.json vor Races zwischen der
# Config-Seite (mehrere Requests) und dem Reload im Hauptloop.
_lock = threading.Lock()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "sender"


def _unique_id(base: str, existing_ids: set) -> str:
    candidate = base
    n = 2
    while candidate in existing_ids:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _ensure_ids_and_defaults(stations: list) -> bool:
    """Füllt fehlende id/category/enabled auf (Migration alter
    stations.json-Dateien ohne diese Felder). Gibt True zurück, wenn etwas
    geändert wurde -> Datei muss neu geschrieben werden."""
    changed = False
    seen_ids = set()
    for s in stations:
        if "enabled" not in s:
            s["enabled"] = True
            changed = True
        if s.get("category") not in CATEGORIES:
            s["category"] = DEFAULT_CATEGORY
            changed = True
        if not s.get("id"):
            s["id"] = _unique_id(_slugify(s.get("name", "sender")), seen_ids)
            changed = True
        seen_ids.add(s["id"])
    return changed


def _read_raw() -> list:
    """Liest stations.json (ohne Lock — nur von gelockten Funktionen unten
    aufrufen). Legt eine Beispieldatei an, falls noch keine existiert."""
    if not os.path.exists(STATIONS_FILE):
        stations = [dict(s) for s in DEFAULT_STATIONS]
        _ensure_ids_and_defaults(stations)
        _write(stations)
        log.warning("ℹ Keine %s gefunden, Beispiel-Datei angelegt.", STATIONS_FILE)
        return stations

    with open(STATIONS_FILE, "r", encoding="utf-8") as f:
        stations = json.load(f)

    if not isinstance(stations, list) or not stations:
        raise ValueError(f"{STATIONS_FILE} muss eine nicht-leere Liste von "
                          f"{{'name': ..., 'url': ...}}-Objekten enthalten.")
    for s in stations:
        if "name" not in s or "url" not in s:
            raise ValueError(f"Eintrag ohne 'name'/'url' in {STATIONS_FILE}: {s}")

    if _ensure_ids_and_defaults(stations):
        _write(stations)

    return stations


def _write(stations: list):
    """Schreibt direkt in stations.json (kein write-temp-then-rename):
    die Datei ist in docker-compose.yml einzeln als Bind-Mount eingehängt,
    darüber schlägt os.replace() mit 'Device or resource busy' fehl, weil
    man eine neue Inode nicht über einen Mountpoint renamen kann."""
    with open(STATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(stations, f, ensure_ascii=False, indent=2)


def load_all() -> list:
    """Alle Sender (aktiv + deaktiviert), so wie in stations.json."""
    with _lock:
        return _read_raw()


def load_active() -> list:
    """Nur aktivierte Sender, alphabetisch nach Name sortiert — das ist die
    Rotationsreihenfolge für automatisches/manuelles Umschalten."""
    stations = load_all()
    active = [s for s in stations if s.get("enabled", True)]
    active.sort(key=lambda s: s["name"].lower())
    return active


def add(name: str, url: str, category: str, enabled: bool = True) -> dict:
    name = (name or "").strip()
    url = (url or "").strip()
    if not name or not url:
        raise ValueError("Name und URL dürfen nicht leer sein.")
    if category not in CATEGORIES:
        category = DEFAULT_CATEGORY
    with _lock:
        stations = _read_raw()
        existing_ids = {s["id"] for s in stations}
        station = {
            "id": _unique_id(_slugify(name), existing_ids),
            "name": name,
            "url": url,
            "category": category,
            "enabled": bool(enabled),
        }
        stations.append(station)
        _write(stations)
        log.info("➕ Sender angelegt: '%s' (%s, %s, %s)", station["name"], station["id"],
                 station["category"], "aktiv" if station["enabled"] else "inaktiv")
        return station


def bulk_add(entries: list, category: str = DEFAULT_CATEGORY, enabled: bool = True) -> list:
    """Fügt mehrere Sender in einem Rutsch hinzu — ein Read+Write statt
    einem pro Sender (für station_import.py, wo sonst bei einer großen
    Playlist hunderte einzelne Lock-Zyklen anfallen würden). Jeder Eintrag
    in `entries`: {"name": str, "url": str}. Einträge ohne Name/URL werden
    stillschweigend übersprungen (Aufrufer hat i.d.R. schon vorgefiltert).
    Gibt die tatsächlich hinzugefügten Sender-dicts zurück.

    `enabled` gilt für alle Einträge des Aufrufs. Der Import übergibt hier
    bewusst False: hunderte fremde Sender ungefragt in die laufende
    Rotation zu kippen ist keine Entscheidung, die ein Import-Knopf für
    den Nutzer treffen sollte — und ein einziger davon, der sich später
    als unspielbar herausstellt, hat den Player schon mal für 8,5 Stunden
    lahmgelegt (siehe Watchdog in keinsabbelradio.py)."""
    if category not in CATEGORIES:
        category = DEFAULT_CATEGORY
    with _lock:
        stations = _read_raw()
        existing_ids = {s["id"] for s in stations}
        added = []
        for e in entries:
            name = (e.get("name") or "").strip()
            url = (e.get("url") or "").strip()
            if not name or not url:
                continue
            station = {
                "id": _unique_id(_slugify(name), existing_ids),
                "name": name,
                "url": url,
                "category": category,
                "enabled": bool(enabled),
            }
            existing_ids.add(station["id"])
            stations.append(station)
            added.append(station)
        if added:
            _write(stations)
        log.info("➕ %d Sender per Bulk-Add in '%s' übernommen (%s).", len(added), category,
                 "aktiviert" if enabled else "deaktiviert")
        return added


def update(station_id: str, name: str, url: str, category: str, enabled: bool) -> dict:
    name = (name or "").strip()
    url = (url or "").strip()
    if not name or not url:
        raise ValueError("Name und URL dürfen nicht leer sein.")
    if category not in CATEGORIES:
        category = DEFAULT_CATEGORY
    with _lock:
        stations = _read_raw()
        for s in stations:
            if s["id"] == station_id:
                s["name"] = name
                s["url"] = url
                s["category"] = category
                s["enabled"] = bool(enabled)
                _write(stations)
                log.info("✏ Sender geändert: '%s' (%s, %s, %s)", s["name"], s["id"],
                         s["category"], "aktiv" if s["enabled"] else "inaktiv")
                return s
        raise KeyError(station_id)


def set_enabled(station_id: str, enabled: bool) -> dict:
    with _lock:
        stations = _read_raw()
        for s in stations:
            if s["id"] == station_id:
                s["enabled"] = bool(enabled)
                _write(stations)
                log.info("%s Sender '%s' %s.", "☑" if s["enabled"] else "☐", s["name"],
                         "aktiviert" if s["enabled"] else "deaktiviert")
                return s
        raise KeyError(station_id)


def set_category_enabled(category: str, enabled: bool) -> int:
    """Setzt enabled für ALLE Sender einer Kategorie auf einmal — ein
    Read+Write statt einem Klick+Request pro Sender (für den
    "Alle deaktivieren"-Knopf auf der Config-Seite; bei einer großen
    importierten Kategorie wie "Unsortiert" mit hunderten Sendern macht
    das den Unterschied zwischen einem Klick und hunderten). Gibt die
    Anzahl tatsächlich geänderter Sender zurück (Sender, die schon den
    gewünschten Zustand hatten, zählen nicht mit)."""
    with _lock:
        stations = _read_raw()
        changed = 0
        for s in stations:
            if s.get("category") == category and s.get("enabled", True) != enabled:
                s["enabled"] = enabled
                changed += 1
        if changed:
            _write(stations)
        log.info("%s %d Sender der Kategorie '%s' %s.", "☑" if enabled else "☐", changed,
                 category, "aktiviert" if enabled else "deaktiviert")
        return changed


def delete(station_id: str) -> None:
    with _lock:
        stations = _read_raw()
        remaining = [s for s in stations if s["id"] != station_id]
        if len(remaining) == len(stations):
            raise KeyError(station_id)
        if not remaining:
            raise ValueError("Mindestens ein Sender muss konfiguriert bleiben.")
        _write(remaining)
        log.info("🗑 Sender gelöscht: %s", station_id)
