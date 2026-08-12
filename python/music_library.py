#!/usr/bin/env python3
"""
music_library.py — Grundgerüst für den Musiksammlung-Modus: liefert die
abspielbaren Dateien eines (nicht rekursiv durchsuchten) Ordners.

Reine Domänenlogik hier drin — KEIN Zugriff auf StreamSource/SwitcherState/
den Hauptloop-Zustand, analog zu news_break.py. Der Hauptloop
(radiosabbelnich.py) entscheidet, wann er list_tracks() aufruft (Play-Klick)
und übernimmt das komplette Audio-Routing selbst, weil dort schon die
gesamte Abspiel-Infrastruktur (StreamSource, write_audio() etc.) existiert.

Kategorisierung (80er/Queen/Oldies/...) ist bewusst NICHT Teil dieses
Moduls — die Kategorie-Buttons auf der Musiksammlung-Seite sind reine
UI-Platzhalter, echtes Mapping kommt erst mit dem ID3/SQLite-Scan (siehe
README "Zukünftige Features", spätere Phase).
"""

import logging
import os

log = logging.getLogger("musiclib")

# Prototyp: nur MP3 (siehe Aufgabenbeschreibung "MP3-Root"). Weitere Formate
# sind eine spätere Erweiterung, kein Architektur-Thema -- AUDIO_EXTENSIONS
# als Tupel statt Einzelwert lässt das ohne Signaturänderung zu.
AUDIO_EXTENSIONS = (".mp3",)


def list_tracks(folder: str) -> list[str]:
    """Alphabetisch sortierte Dateinamen (Basenames, keine Pfade) der
    abspielbaren Dateien direkt in `folder` — NICHT rekursiv, wie in der
    Aufgabenbeschreibung gefordert (ein rekursiver Scan mit Kategorisierung
    ist der spätere DB-Scan-Baustein, nicht dieser Prototyp).

    Leere Liste (mit Log-Warnung, kein Fehler) falls der Ordner nicht
    konfiguriert, nicht lesbar ist oder keine passenden Dateien enthält —
    gleiches Toleranz-Muster wie news_break.pick_random_mp3(): der Aufrufer
    entscheidet, was dann passiert (hier: Play-Klick bleibt wirkungslos,
    siehe radiosabbelnich.py)."""
    if not folder:
        log.warning("⚠ Musiksammlung: kein Ordner konfiguriert — Wiedergabe übersprungen.")
        return []
    try:
        entries = os.listdir(folder)
    except OSError as e:
        log.warning("⚠ Musiksammlung: Ordner %s nicht lesbar (%s) — Wiedergabe übersprungen.",
                    folder, e)
        return []

    files = sorted((f for f in entries if f.lower().endswith(AUDIO_EXTENSIONS)), key=str.lower)
    if not files:
        log.warning("⚠ Musiksammlung: keine abspielbaren Dateien in %s — Wiedergabe übersprungen.",
                    folder)
    return files
