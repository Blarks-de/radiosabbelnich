#!/usr/bin/env python3
"""
music_library.py — Grundgerüst für den Musiksammlung-Modus: liefert die
abspielbaren Dateien eines Ordners (seit 2026-08-14 rekursiv bis zu
MAX_SCAN_DEPTH Verzeichnisebenen darunter, Nutzervorgabe).

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

# Seit 2026-08-12 (Format-Erweiterung, siehe README-Roadmap) über MP3
# hinaus: FLAC, OGG (Vorbis), M4A/AAC (MP4-Container UND rohes ADTS),
# WAV, APE (Monkey's Audio). Playback ist dank ffmpeg format-agnostisch
# (keine Änderung hier nötig), die eigentliche Format-Arbeit steckt im
# Metadaten-/Cover-Extraktions-Code in music_scan.py -- siehe dessen
# Moduldoc für die Details (mehrere Formate brauchen dort Sonderfälle,
# "einfach mutagen.File(..., easy=True) aufrufen" reicht NICHT überall).
AUDIO_EXTENSIONS = (".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wav", ".ape")

# Nutzervorgabe 2026-08-14: sowohl der Musik-Player (list_tracks() unten)
# als auch die Nachrichten-Pause (news_break.pick_random_mp3(), nutzt
# iter_files_recursive() unten mit) sollen nicht mehr nur den Wurzelordner
# selbst durchsuchen, sondern bis zu 5 Verzeichnisebenen darunter. Ein
# fester Wert statt unbegrenzter Rekursion, damit ein versehentlich zu
# hoch (z.B. auf den SMB-Mount-Root) konfigurierter Ordner keinen
# unbegrenzt langen os.walk() auslösen kann.
MAX_SCAN_DEPTH = 5


def iter_files_recursive(folder: str, extensions: tuple[str, ...],
                          max_depth: int = MAX_SCAN_DEPTH):
    """Liefert Pfade relativ zu `folder` aller Dateien mit einer der
    `extensions`-Endungen (case-insensitiv), bis zu `max_depth`
    Verzeichnisebenen unter `folder` (0 = nur direkt in `folder`, das
    Verhalten vor der Rekursion-Erweiterung). Bricht den Abstieg gezielt
    per `dirnames[:] = []` ab, sobald die Tiefe erreicht ist, statt
    os.walk() den kompletten (u.U. sehr großen) Baum durchlaufen zu
    lassen und danach zu filtern.

    Geteilt zwischen list_tracks() unten und news_break.pick_random_mp3()
    — beide brauchen exakt dieselbe Tiefenbegrenzung, nur mit
    unterschiedlicher Endungsliste (AUDIO_EXTENSIONS vs. nur ".mp3")."""
    for dirpath, dirnames, filenames in os.walk(folder):
        rel_dir = os.path.relpath(dirpath, folder)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        if depth >= max_depth:
            dirnames[:] = []
        for name in filenames:
            if name.lower().endswith(extensions):
                yield name if rel_dir == "." else os.path.join(rel_dir, name)


def list_tracks(folder: str) -> list[str]:
    """Alphabetisch sortierte Pfade (relativ zu `folder`, inkl. eines
    evtl. Unterordner-Anteils) der abspielbaren Dateien in `folder` —
    seit 2026-08-14 bis zu MAX_SCAN_DEPTH Ebenen rekursiv (vorher nur
    direkt in `folder`, siehe SESSION.md). Bewusst keine reinen
    Basenames mehr: os.path.join(folder, ...) beim Abspielen (siehe
    start_music_track() in radiosabbelnich.py) braucht den vollen
    relativen Anteil, und gleichnamige Dateien in unterschiedlichen
    Unterordnern dürfen sich nicht gegenseitig verdrängen.

    Leere Liste (mit Log-Warnung, kein Fehler) falls der Ordner nicht
    konfiguriert, nicht lesbar ist oder keine passenden Dateien enthält —
    gleiches Toleranz-Muster wie news_break.pick_random_mp3(): der Aufrufer
    entscheidet, was dann passiert (hier: Play-Klick bleibt wirkungslos,
    siehe radiosabbelnich.py)."""
    if not folder:
        log.warning("⚠ Musiksammlung: kein Ordner konfiguriert — Wiedergabe übersprungen.")
        return []
    try:
        os.listdir(folder)  # nur zur Fehlerprüfung, siehe unten
    except OSError as e:
        log.warning("⚠ Musiksammlung: Ordner %s nicht lesbar (%s) — Wiedergabe übersprungen.",
                    folder, e)
        return []

    files = sorted(iter_files_recursive(folder, AUDIO_EXTENSIONS), key=str.lower)
    if not files:
        log.warning("⚠ Musiksammlung: keine abspielbaren Dateien in %s (auch nicht in bis zu "
                    "%d Unterordner-Ebenen) — Wiedergabe übersprungen.",
                    folder, MAX_SCAN_DEPTH)
    return files
