#!/usr/bin/env python3
"""
music_scan.py — Phase 1 der Musik-Library-Roadmap (siehe README "Zukünftige
Features"): rekursiver Scan der Musiksammlung, ID3-Metadaten via mutagen in
eine eigene SQLite-DB (music_library.db, NICHT fingerprints.db -- komplett
andere Domäne, gemeinsame DB wäre nur zufällige Kopplung).

Reine Domänenlogik, KEIN Zugriff auf StreamSource/SwitcherState/den
Hauptloop-Zustand, analog zu news_break.py/station_import.py -- der Scan
läuft ausschließlich aus einem Webserver-Thread heraus (siehe webui.py,
POST /api/library/scan), der Hauptloop weiß nichts davon.

Bewusst getrennt von music_library.py: das ist der PLAYER (list_tracks(),
ein Ordner, nicht rekursiv, für den Play/Stop-Button), das hier ist der
SCANNER (ganzer Baum, Metadaten, DB). Beide Module teilen sich nur
AUDIO_EXTENSIONS (Import von dort), damit eine spätere Formaterweiterung
(FLAC/OGG/... , siehe README-Roadmap) an einer einzigen Stelle passiert.

Cover-Bilder werden als Dateien im Dateisystem gecacht (covers_dir), NICHT
als Blob in der DB -- analog zum Bild-Handling an anderer Stelle im Projekt
(z.B. radiosabbelnich.webp/pics/, fingerprint_clips/ für gelernte Audio-
Clips). Der Dateiname ist ein SHA1-Hash des relativen Track-Pfads: dadurch
überschreibt ein Re-Scan das Cover einer bereits bekannten Datei einfach an
derselben Stelle (keine Kollisionen, kein Nachschlagen der DB-ID nötig,
BEVOR die Zeile geschrieben ist).

Inkrementelles Verhalten (mtime+Größe): ein voller Bibliotheks-Scan mit
tausenden Dateien dauert beim ERSTEN Mal zwangsläufig lange (jede Datei
muss einmal gelesen werden) -- das ist nicht vermeidbar. Aber die
allermeisten Dateien ändern sich zwischen zwei Scans nicht, deshalb wird
mutagen (und vor allem das ID3/APIC-Parsing fürs Cover, das teuerste an
einem einzelnen Datei-Read) nur dann aufgerufen, wenn sich mtime ODER
Größe seit der letzten in der DB gespeicherten Zeile unterscheiden -- ein
reiner os.stat()-Vergleich pro unveränderter Datei statt eines vollen
mutagen-Parse. Ein Re-Scan einer unveränderten Bibliothek ist dadurch nur
noch durch die Anzahl der stat()-Aufrufe begrenzt, nicht durch ID3-Parsing.

"Gefunden"-Set schützt vor Fehlbereinigung: eine Datei, die zwar noch auf
der Platte liegt, aber gerade nicht lesbar ist (kaputte MP3, kurzzeitiger
SMB-Hänger), landet TROTZDEM im "gefunden"-Set (nur das mutagen-Parsing
wird übersprungen) -- sonst würde die abschließende Aufräum-Phase
(Zeilen zu nicht mehr gefundenen Pfaden löschen) einen bestehenden,
gültigen DB-Eintrag für eine nur vorübergehend defekte Datei fälschlich
als "Datei wurde gelöscht" werten und entfernen.
"""

import hashlib
import logging
import os
import sqlite3
import time

import mutagen
from mutagen import MutagenError
from mutagen.id3 import ID3, ID3NoHeaderError

from music_library import AUDIO_EXTENSIONS

log = logging.getLogger("musicscan")

MUSIC_LIBRARY_DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music_library.db")
MUSIC_LIBRARY_COVERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music_library_covers")

_COVER_MIME_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png"}


def _init_schema(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT NOT NULL UNIQUE,
            artist TEXT,
            album TEXT,
            title TEXT,
            genre TEXT,
            year INTEGER,
            cover_path TEXT,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            scanned_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_genre ON tracks(genre)")
    conn.commit()


def _extract_year(easy_tags) -> int | None:
    """easyid3 liefert das Datum (falls vorhanden) als 'YYYY' oder
    'YYYY-MM-DD' im 'date'-Feld -- nur die ersten 4 Ziffern interessieren."""
    values = easy_tags.get("date") if easy_tags else None
    if not values:
        return None
    raw = str(values[0])[:4]
    return int(raw) if raw.isdigit() else None


def _extract_cover(full_path: str, rel_path: str, covers_dir: str) -> str | None:
    """Erstes eingebettetes Cover (APIC-Frame) als Datei nach covers_dir
    cachen, relativen Dateinamen zurückgeben (oder None ohne Cover/bei
    einem nicht unterstützten MIME-Typ). Getrennt vom easyid3-Read oben,
    weil APIC-Frames über das "easy"-Interface nicht erreichbar sind."""
    try:
        tags = ID3(full_path)
    except ID3NoHeaderError:
        return None
    apics = tags.getall("APIC")
    if not apics:
        return None
    mime = apics[0].mime
    ext = _COVER_MIME_EXT.get(mime)
    if ext is None:
        log.debug("Cover in %s hat nicht unterstützten MIME-Typ %s -- übersprungen.", rel_path, mime)
        return None
    os.makedirs(covers_dir, exist_ok=True)
    filename = hashlib.sha1(rel_path.encode("utf-8")).hexdigest() + ext
    with open(os.path.join(covers_dir, filename), "wb") as f:
        f.write(apics[0].data)
    return filename


def scan_library(root: str, db_path: str = MUSIC_LIBRARY_DB_FILE,
                  covers_dir: str = MUSIC_LIBRARY_COVERS_DIR, progress=None) -> dict:
    """Scannt `root` rekursiv nach AUDIO_EXTENSIONS-Dateien, aktualisiert
    `db_path`. `progress` (optional): Objekt mit set_phase(phase, total)/
    increment_checked() -- siehe webui.LibraryScanState, gleiches Muster
    wie station_import.run_import(progress=...).

    Gibt eine Zusammenfassung zurück: {"found", "added", "updated",
    "unchanged", "errors", "removed"}."""
    if not root or not os.path.isdir(root):
        raise ValueError(f"Musiksammlung-Root '{root}' nicht lesbar/nicht konfiguriert.")

    if progress:
        progress.set_phase("walking")
    all_files = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(AUDIO_EXTENSIONS):
                full_path = os.path.join(dirpath, name)
                rel_path = os.path.relpath(full_path, root)
                all_files.append((full_path, rel_path))

    conn = sqlite3.connect(db_path)
    _init_schema(conn)
    existing = {
        row[0]: {"mtime": row[1], "size": row[2], "cover_path": row[3]}
        for row in conn.execute("SELECT filepath, mtime, size, cover_path FROM tracks")
    }

    if progress:
        progress.set_phase("scanning", total=len(all_files))

    found = set()
    stats = {"found": len(all_files), "added": 0, "updated": 0, "unchanged": 0, "errors": 0}

    for full_path, rel_path in all_files:
        found.add(rel_path)
        try:
            st = os.stat(full_path)
        except OSError as e:
            log.warning("⚠ Musik-Scan: %s nicht lesbar (%s) — übersprungen.", rel_path, e)
            stats["errors"] += 1
            if progress:
                progress.increment_checked()
            continue

        prev = existing.get(rel_path)
        if prev and prev["mtime"] == st.st_mtime and prev["size"] == st.st_size:
            # Unverändert seit letztem Scan -- teures mutagen/APIC-Parsing
            # überspringen (siehe Modul-Docstring).
            stats["unchanged"] += 1
            if progress:
                progress.increment_checked()
            continue

        try:
            easy = mutagen.File(full_path, easy=True)
            if easy is None:
                raise MutagenError(f"kein unterstütztes Audioformat ({rel_path})")
            artist = (easy.get("artist") or [None])[0]
            album = (easy.get("album") or [None])[0]
            title = (easy.get("title") or [None])[0] or os.path.splitext(os.path.basename(rel_path))[0]
            genre = (easy.get("genre") or [None])[0]
            year = _extract_year(easy)
            cover_path = _extract_cover(full_path, rel_path, covers_dir)
        except (MutagenError, OSError) as e:
            log.warning("⚠ Musik-Scan: Metadaten von %s nicht lesbar (%s) — übersprungen.", rel_path, e)
            stats["errors"] += 1
            if progress:
                progress.increment_checked()
            continue

        is_new = prev is None
        conn.execute("""
            INSERT INTO tracks (filepath, artist, album, title, genre, year,
                                 cover_path, mtime, size, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(filepath) DO UPDATE SET
                artist=excluded.artist, album=excluded.album, title=excluded.title,
                genre=excluded.genre, year=excluded.year, cover_path=excluded.cover_path,
                mtime=excluded.mtime, size=excluded.size, scanned_at=excluded.scanned_at
        """, (rel_path, artist, album, title, genre, year, cover_path,
              st.st_mtime, st.st_size, time.strftime("%Y-%m-%d %H:%M:%S")))
        stats["added" if is_new else "updated"] += 1
        if progress:
            progress.increment_checked()

    # Aufräumen: Zeilen zu Dateien, die beim Walk nicht mehr auftauchten
    # (siehe Modul-Docstring, "gefunden"-Set) -- inkl. verwaister Cover-
    # Dateien, sonst wächst covers_dir dauerhaft weiter.
    stale = [fp for fp in existing if fp not in found]
    for fp in stale:
        cover_path = existing[fp]["cover_path"]
        if cover_path:
            try:
                os.remove(os.path.join(covers_dir, cover_path))
            except OSError:
                pass
    if stale:
        conn.executemany("DELETE FROM tracks WHERE filepath = ?", [(fp,) for fp in stale])
    conn.commit()
    conn.close()

    stats["removed"] = len(stale)
    log.info("🎵 Musik-Scan fertig: %d Datei(en) gefunden, %d neu, %d aktualisiert, "
             "%d unverändert, %d Fehler, %d entfernt.",
             stats["found"], stats["added"], stats["updated"], stats["unchanged"],
             stats["errors"], stats["removed"])
    return stats
