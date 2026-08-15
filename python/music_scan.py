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

BPM-Schätzung (Phase 3, siehe music_bpm.py) läuft im selben Durchlauf wie
das ID3-Parsing, profitiert also von derselben mtime/Größe-Skip-Logik --
unveränderte Tracks werden nicht neu analysiert. Ein fehlgeschlagener/
unmöglicher BPM-Wert (music_bpm.estimate_bpm() gibt dann None zurück)
verwirft NICHT die ganze Zeile, nur das bpm-Feld bleibt NULL.

Format-Erweiterung (seit 2026-08-12, siehe README-Roadmap): über MP3
hinaus jetzt auch FLAC, OGG (Vorbis), M4A (MP4-Container), rohes ADTS-
AAC, WAV und APE (Monkey's Audio) -- AUDIO_EXTENSIONS in music_library.py.
"Einfach mutagen.File(pfad, easy=True) aufrufen" reicht dafür NICHT
durchgängig, an echten (per ffmpeg erzeugten + per mutagen getaggten)
Testdateien verifiziert, nicht nur aus der mutagen-Doku übernommen:

- FLAC/OGG/MP4 liefern die normalisierten "easy"-Keys (artist/title/
  album/genre/date) korrekt -- keine Sonderbehandlung nötig, nur die
  Cover-Extraktion ist pro Format eigener Code (siehe _read_cover_bytes()
  unten): FLACs `.pictures`, OGGs Base64-kodiertes
  `metadata_block_picture`, MP4s `covr`-Atom sind drei komplett
  unterschiedliche Ablageorte, keiner davon über "easy" erreichbar.
- WAV wird von mutagen NICHT easy-gewrappt (kein EasyWAVE) -- ein rohes
  `WAVE(pfad).get("artist")` liefert IMMER None, auch wenn ID3-Tags
  vorhanden sind (die Datei trägt sie unter rohen Frame-IDs wie "TPE1").
  `audio_tags.RawId3EasyAdapter` liest deshalb die Frames direkt.
- Rohes ADTS-AAC (NICHT der MP4-Container M4A) ist der überraschendste
  Fund: mutagen.File()s Auto-Erkennung erkennt eine getaggte .aac-Datei
  fälschlich als MP3 (weil ein ID3v2-Header vorhanden ist) und crasht
  beim MPEG-Frame-Sync (`HeaderNotFoundError`, live reproduziert) --
  die Exception ist zwar eine MutagenError-Unterklasse und wird
  dadurch nicht zum Absturz des gesamten Scans, aber OHNE Sonderbehandlung
  würde JEDE getaggte .aac-Datei bei JEDEM Scan als "Fehler" markiert
  und nie eingelesen. `audio_tags.open_tags()` umgeht deshalb für .aac
  bewusst die Auto-Erkennung: Tags direkt über `ID3()`, Stream-Info
  (für den BPM-Duration-Hint) separat über `AAC().info`. Seit 2026-08-15
  liegt diese Format-Weiche (WAV/.aac-Sonderfälle, `extract_year()`) in
  `audio_tags.py` -- geteilt mit der Live-Tag-Anzeige während der
  Wiedergabe (News-Break/Musik-Player, siehe dortiges Moduldoc und
  CLAUDE.md), statt hier dupliziert zu sein.
- APE (Monkey's Audio) hat KEIN standardisiertes Cover-Art-Feld (nur
  eine informelle, nicht durchgängig unterstützte Tool-Konvention ohne
  mutagen-API dafür) -- Cover-Extraktion wird für APE bewusst NICHT
  versucht (siehe _read_cover_bytes()), Text-Tags (Artist/Titel/...)
  laufen über den normalen "easy"-Pfad. Konnte NICHT gegen eine echte
  .ape-Datei verifiziert werden -- das im Image enthaltene ffmpeg hat
  zwar einen Monkey's-Audio-DECODER (Playback funktioniert also), aber
  keinen Encoder, um eine Testdatei zu erzeugen (siehe SESSION.md).
"""

import base64
import hashlib
import logging
import os
import sqlite3
import time

from mutagen import MutagenError
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

import music_bpm
from audio_tags import extract_year, open_tags
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
            bpm REAL,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            scanned_at TEXT NOT NULL
        )
    """)
    # Migration für DBs aus Phase 1/2 (vor der bpm-Spalte, siehe SESSION.md
    # Phase 3): das CREATE TABLE IF NOT EXISTS oben greift bei einer schon
    # bestehenden Tabelle nicht mehr -- SQLite kennt kein "ADD COLUMN IF
    # NOT EXISTS", deshalb erst per PRAGMA prüfen, ob die Spalte fehlt.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    if "bpm" not in columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN bpm REAL")
        log.info("🎵 Musik-DB-Schema migriert: Spalte 'bpm' ergänzt.")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_genre ON tracks(genre)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm)")
    conn.commit()


def _read_cover_bytes(full_path: str, ext: str):
    """Liefert (mime, data) des ersten eingebetteten Covers, oder
    (None, None) ohne Cover bzw. für ein Format ohne Cover-Unterstützung
    (.aac, .ape -- siehe Moduldoc). Ein Format pro Zweig, weil Cover-Daten
    über KEIN Format hinweg einheitlich abgelegt sind (ID3-APIC-Frames bei
    MP3/WAV, FLAC-Picture-Blöcke, Base64-kodiertes metadata_block_picture
    bei OGG, covr-Atom bei MP4) -- anders als die Text-Tags oben gibt es
    dafür keine gemeinsame "easy"-Abstraktion in mutagen."""
    if ext in (".mp3", ".wav"):
        if ext == ".mp3":
            try:
                tags = ID3(full_path)
            except ID3NoHeaderError:
                return None, None
        else:
            tags = WAVE(full_path).tags
            if tags is None:
                return None, None
        apics = tags.getall("APIC")
        return (apics[0].mime, apics[0].data) if apics else (None, None)
    if ext == ".flac":
        pics = FLAC(full_path).pictures
        return (pics[0].mime, pics[0].data) if pics else (None, None)
    if ext == ".ogg":
        raw = OggVorbis(full_path).get("metadata_block_picture")
        if not raw:
            return None, None
        pic = Picture(base64.b64decode(raw[0]))
        return pic.mime, pic.data
    if ext == ".m4a":
        covr = MP4(full_path).get("covr")
        if not covr:
            return None, None
        cover = covr[0]
        mime = "image/jpeg" if cover.imageformat == MP4Cover.FORMAT_JPEG else "image/png"
        return mime, bytes(cover)
    return None, None


def _extract_cover(full_path: str, rel_path: str, covers_dir: str, ext: str) -> str | None:
    """Cover aus _read_cover_bytes() als Datei nach covers_dir cachen,
    relativen Dateinamen zurückgeben (oder None ohne Cover/bei nicht
    unterstütztem MIME-Typ/Format)."""
    mime, data = _read_cover_bytes(full_path, ext)
    if mime is None:
        return None
    cover_ext = _COVER_MIME_EXT.get(mime)
    if cover_ext is None:
        log.debug("Cover in %s hat nicht unterstützten MIME-Typ %s -- übersprungen.", rel_path, mime)
        return None
    os.makedirs(covers_dir, exist_ok=True)
    filename = hashlib.sha1(rel_path.encode("utf-8")).hexdigest() + cover_ext
    with open(os.path.join(covers_dir, filename), "wb") as f:
        f.write(data)
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
        row[0]: {"mtime": row[1], "size": row[2], "cover_path": row[3], "bpm": row[4]}
        for row in conn.execute("SELECT filepath, mtime, size, cover_path, bpm FROM tracks")
    }

    if progress:
        progress.set_phase("scanning", total=len(all_files))

    found = set()
    stats = {"found": len(all_files), "added": 0, "updated": 0, "unchanged": 0,
              "bpm_backfilled": 0, "errors": 0}

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
        unchanged = prev is not None and prev["mtime"] == st.st_mtime and prev["size"] == st.st_size
        if unchanged and prev["bpm"] is not None:
            # Unverändert seit letztem Scan UND schon ein BPM-Wert
            # vorhanden -- teures mutagen/APIC-Parsing UND BPM-Decode
            # überspringen (siehe Modul-Docstring).
            stats["unchanged"] += 1
            if progress:
                progress.increment_checked()
            continue
        if unchanged:
            # Unverändert, aber bpm ist NULL -- Zeile stammt aus einer
            # Phase-1/2-DB von VOR der bpm-Spalte (siehe _init_schema()-
            # Migration). ID3-Tags/Cover unverändert, deshalb KEIN voller
            # mutagen-Reparse nötig -- nur die BPM-Lücke nachtragen, ohne
            # den Rest der Zeile anzufassen.
            bpm = music_bpm.estimate_bpm(full_path)
            conn.execute("UPDATE tracks SET bpm = ? WHERE filepath = ?", (bpm, rel_path))
            stats["bpm_backfilled"] += 1
            if progress:
                progress.increment_checked()
            continue

        ext = os.path.splitext(rel_path)[1].lower()
        try:
            easy = open_tags(full_path, ext)
            if easy is None:
                raise MutagenError(f"kein unterstütztes Audioformat ({rel_path})")
            artist = (easy.get("artist") or [None])[0]
            album = (easy.get("album") or [None])[0]
            title = (easy.get("title") or [None])[0] or os.path.splitext(os.path.basename(rel_path))[0]
            genre = (easy.get("genre") or [None])[0]
            year = extract_year(easy)
            cover_path = _extract_cover(full_path, rel_path, covers_dir, ext)
        except (MutagenError, OSError) as e:
            log.warning("⚠ Musik-Scan: Metadaten von %s nicht lesbar (%s) — übersprungen.", rel_path, e)
            stats["errors"] += 1
            if progress:
                progress.increment_checked()
            continue

        # BPM-Schätzung (Phase 3) -- eigener Decode/Analyse-Schritt, aber
        # ein Fehlschlag dort (None) verwirft NICHT die schon erfolgreich
        # gelesenen ID3-Daten, siehe Modul-Docstring.
        duration_hint = getattr(getattr(easy, "info", None), "length", None)
        bpm = music_bpm.estimate_bpm(full_path, duration_hint=duration_hint)

        is_new = prev is None
        conn.execute("""
            INSERT INTO tracks (filepath, artist, album, title, genre, year,
                                 cover_path, bpm, mtime, size, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(filepath) DO UPDATE SET
                artist=excluded.artist, album=excluded.album, title=excluded.title,
                genre=excluded.genre, year=excluded.year, cover_path=excluded.cover_path,
                bpm=excluded.bpm, mtime=excluded.mtime, size=excluded.size, scanned_at=excluded.scanned_at
        """, (rel_path, artist, album, title, genre, year, cover_path, bpm,
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
             "%d unverändert, %d BPM nachgetragen, %d Fehler, %d entfernt.",
             stats["found"], stats["added"], stats["updated"], stats["unchanged"],
             stats["bpm_backfilled"], stats["errors"], stats["removed"])
    return stats
