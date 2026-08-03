#!/usr/bin/env python3
"""
news_break.py — "Nachrichten-Pause": zur vollen und halben Stunde
verlesen praktisch alle Radiosender Nachrichten. Statt dessen spielt
RadioZapper für ein kurzes, konfigurierbares Zeitfenster eine zufällige
MP3 aus einem lokalen Ordner (z.B. ein SMB-Mount mit eigenen Jingles/
Musikstücken) statt eines Radiosenders.

Reine Domänenlogik hier drin — KEIN Zugriff auf StreamSource/SwitcherState/
den Hauptloop-Zustand. Der Hauptloop (radiozapper.py) entscheidet anhand
von active_slot(), ob gerade ein Fenster läuft, und ruft bei Bedarf
pick_random_mp3() auf; alles Audio-Routing (Sender pausieren, MP3
abspielen, zurückschalten) bleibt bewusst dort, weil dort schon die
gesamte Switch-Infrastruktur (switch_to_station() etc.) existiert, die
hier nicht dupliziert werden soll.
"""

import logging
import os
import random
from datetime import datetime, timedelta

log = logging.getLogger("newsbreak")


def _nearest_half_hour(now: datetime) -> datetime:
    """Die näher gelegene der drei Kandidaten-Grenzen: die volle Stunde
    davor, :30 dieser Stunde, oder die volle Stunde danach."""
    base = now.replace(minute=0, second=0, microsecond=0)
    candidates = (base, base + timedelta(minutes=30), base + timedelta(hours=1))
    return min(candidates, key=lambda c: abs((c - now).total_seconds()))


def active_slot(cfg: dict, now: datetime = None) -> str | None:
    """Liefert eine stabile Kennung ("Slot-ID") für das aktuell laufende
    Nachrichten-Pause-Fenster, oder None, falls gerade keins aktiv ist.

    Die Slot-ID ist dieselbe über die gesamte Fensterdauer (die
    nächstgelegene :00/:30 als ISO-Zeitstempel) — der Hauptloop merkt
    sich pro Slot, ob er schon bedient wurde (MP3 zu Ende, Fenster
    abgelaufen, Nutzer hat manuell weggeschaltet: in jedem Fall wird
    derselbe Slot nicht ein zweites Mal bedient, siehe
    news_break_served_slot in radiozapper.py)."""
    if not cfg.get("enabled"):
        return None
    now = now or datetime.now()

    hours = cfg.get("enabled_hours")
    if hours and not (hours[0] <= now.hour < hours[1]):
        return None

    window = float(cfg.get("window_minutes") or 0)
    if window <= 0:
        return None

    slot = _nearest_half_hour(now)
    if abs((slot - now).total_seconds()) <= window * 60:
        return slot.isoformat()
    return None


def pick_random_mp3(folder: str, exclude: str = None) -> str | None:
    """Liefert einen zufälligen Pfad zu einer .mp3-Datei aus `folder`.

    None (mit Log-Warnung, nicht mehr) falls der Ordner nicht konfiguriert,
    nicht lesbar ist oder keine .mp3-Dateien enthält — Anforderung ist
    "Feature still überspringen, ins Log schreiben, normal weiterlaufen",
    kein Fehler, der den Hauptloop stören dürfte.

    `exclude` (der zuletzt gespielte Dateiname) wird vermieden, sofern noch
    eine andere Wahl übrig bleibt — bei nur einer Datei im Ordner bleibt
    nichts anderes übrig, dann wird sie trotzdem gespielt."""
    if not folder:
        log.warning("⚠ Nachrichten-Pause aktiv, aber kein mp3_folder konfiguriert — übersprungen.")
        return None
    try:
        entries = os.listdir(folder)
    except OSError as e:
        log.warning("⚠ Nachrichten-Pause: Ordner %s nicht lesbar (%s) — übersprungen.", folder, e)
        return None

    files = [f for f in entries if f.lower().endswith(".mp3")]
    if not files:
        log.warning("⚠ Nachrichten-Pause: keine .mp3-Dateien in %s — übersprungen.", folder)
        return None

    candidates = [f for f in files if f != exclude] or files
    choice = random.choice(candidates)
    return os.path.join(folder, choice)
