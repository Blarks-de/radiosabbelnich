#!/usr/bin/env python3
"""
music_query.py — Phase 2 der Musik-Library-Roadmap (siehe README
"Zukünftige Features"): einfache Lesezugriffe auf die von music_scan.py
gefüllte music_library.db. Angelehnt an Beets' Query-Syntax als
Inspiration, aber bewusst OHNE echten Parser — nur die Handvoll fester
Filterarten, die die Kategorie-/Favoriten-Buttons auf `/musik` brauchen.
Ein generischer Query-Parser wäre für vier feste Buttons over-engineered;
kommt erst, wenn es tatsächlich freie Nutzereingaben gibt.

Reine Domänenlogik, KEIN Zugriff auf StreamSource/SwitcherState — wird
ausschließlich aus dem WEBSERVER-Thread aufgerufen (siehe webui.py,
_handle_music_play()), NIEMALS aus dem Hauptloop: der ~1s-Analysetakt
darf nie auf eine SQLite-Query warten. Der Hauptloop bekommt nur die
bereits fertig aufgelöste Track-Liste durchgereicht (request/pop, wie
jede andere Aktion, die `source` anfasst) — exakt das gleiche Prinzip
wie beim Scan selbst (music_scan.py läuft ebenfalls nur webserver-seitig).

Matching per SQL LIKE (Teilstring, NICHT case-sensitiv für ASCII — das
ist SQLites Standardverhalten für LIKE, kein LOWER()/COLLATE nötig) statt
exakter Gleichheit: ID3-Genre-Tags sind Freitext ohne feste Taxonomie
("Rock" vs. "Classic Rock" vs. "rock'n'roll"), ein Teilstring-Match ist
die einzige praktikable Annäherung ohne eigene Genre-Normalisierung
(spätere Phase, nicht Teil hiervon). Bewusste Grenze: "klassik" matcht
NICHT automatisch auch englisch getaggte "Classical"-Dateien — keine
Synonym-Liste, um den Scope klein zu halten."""

import logging
import os
import sqlite3

log = logging.getLogger("musicquery")

_SELECT = "SELECT id, filepath, artist, title, album, cover_path, bpm FROM tracks"
_ORDER = " ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE, filepath COLLATE NOCASE"

# Phase 3 (music_bpm.py): Standardkonvention statt Musikwissenschaft --
# < 90 BPM gilt als "langsam", >= 120 BPM als "schnell", der Bereich
# dazwischen fällt bei BEIDEN Buttons raus. Gleiche bewusste Unschärfe
# wie beim Genre-Teilstring-Match, siehe Moduldocstring.
SLOW_BPM_MAX = 90
FAST_BPM_MIN = 120


def _rows_to_tracks(rows) -> list[dict]:
    tracks = []
    for track_id, filepath, artist, title, album, cover_path, bpm in rows:
        display_title = title or os.path.splitext(os.path.basename(filepath))[0]
        label = f"{artist} – {display_title}" if artist else display_title
        tracks.append({
            "id": track_id, "filepath": filepath, "artist": artist,
            "title": display_title, "album": album, "cover_path": cover_path,
            "bpm": bpm, "label": label,
        })
    return tracks


def _query(db_path: str, where_sql: str = "", params: tuple = ()) -> list[dict]:
    """Eigene, kurze Connection pro Aufruf (Webserver-Thread, sqlite3-
    Connections sind nicht thread-übergreifend sicher, gleiches Muster wie
    fingerprint.delete_clip()). Fehlt die tracks-Tabelle (DB noch nie
    gescannt) oder ist die DB kurzzeitig durch einen parallel laufenden
    Scan gesperrt: leere Liste statt Exception — der Aufrufer (webui.py)
    zeigt das dann als "keine Treffer" an, kein Serverfehler."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(f"{_SELECT} {where_sql}{_ORDER}", params).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        log.debug("Musik-Query fehlgeschlagen (%s) — vermutlich noch nicht gescannt.", e)
        return []
    return _rows_to_tracks(rows)


def query_all(db_path: str) -> list[dict]:
    return _query(db_path)


def query_by_artist(db_path: str, artist_substring: str) -> list[dict]:
    return _query(db_path, "WHERE artist LIKE ?", (f"%{artist_substring}%",))


def query_by_genre(db_path: str, genre_substring: str) -> list[dict]:
    return _query(db_path, "WHERE genre LIKE ?", (f"%{genre_substring}%",))


def query_by_tempo(db_path: str, mode: str) -> list[dict]:
    """mode: "fast" (>= FAST_BPM_MIN) oder "slow" (> 0 und <= SLOW_BPM_MAX).
    Tracks ohne BPM-Wert (noch nicht/nicht erfolgreich analysiert, siehe
    music_bpm.py) tauchen in KEINEM der beiden Modi auf -- kein Rätselraten
    mit einem Platzhalterwert."""
    if mode == "fast":
        return _query(db_path, "WHERE bpm >= ?", (FAST_BPM_MIN,))
    if mode == "slow":
        return _query(db_path, "WHERE bpm > 0 AND bpm <= ?", (SLOW_BPM_MAX,))
    raise ValueError(f"Unbekannter Tempo-Modus: {mode!r} (erwartet 'fast' oder 'slow')")
