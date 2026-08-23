# Copyright (C) 2026 RadioSabbelNich
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 (or
# later), as published by the Free Software Foundation. See LICENSE.

"""
song_fingerprint.py — Song-Erkennung Phase 1: lokaler Chromaprint-
Fingerprint-Cache für laufende Musik (siehe ARCHITECTURE.md, Abschnitt
"Song-Erkennung"). Komplett getrennt von fingerprint.py: das dort gebaute
Constellation-Map-Verfahren erkennt WIEDERHOLTE Sprache-Clips (Jingles/
Werbung), hier geht es um MUSIKSTÜCKE -- andere Domäne, eigene DB-Datei
(song_fingerprints.db), eigenes Verfahren (Chromaprint statt Eigenbau).

Warum Chromaprint statt desselben Eigenbaus wie fingerprint.py: Songs
spielen im Radio nicht immer ab Sekunde 0 an -- zwei Aufnahmen desselben
Songs zu unterschiedlichen Zeiten decken fast immer unterschiedliche
Zeitausschnitte ab. Chromaprint ist genau dafür entwickelt (robuste
Ausschnitts-Fingerprints), ein eigenes Constellation-Map-Verfahren dafür
neu zu bauen wäre eine deutlich größere Baustelle als das Kompilat einmal
zu nutzen.

`fpcalc` (Debian-Paket libchromaprint-tools, siehe Dockerfile) liefert nur
die rohe Chromaprint-Integer-Sequenz (`-raw`) -- KEINEN fertigen
Ähnlichkeits-Score. Das eigentliche Matching (Sliding-Offset-Suche +
Hamming-Distanz) ist bewusst eigener, simpler Python-Code statt einer
zusätzlichen pip-Abhängigkeit wie pyacoustid, aus demselben Grund wie in
fingerprint.py: in Python+SQLite komplett selbst verständlich und wartbar.

Warum Sliding-Offset statt direktem Index-Vergleich: die rohen Chromaprint-
Arrays zweier Aufnahmen DESSELBEN Songs sind nur dann Position-für-Position
ähnlich, wenn beide exakt an derselben Stelle im Song anfangen -- das ist
bei zwei zu unterschiedlichen Zeiten mitgeschnittenen Radio-Snippets fast
nie der Fall. similarity() probiert deshalb mehrere Zeitverschiebungen
zwischen den beiden Arrays durch und nimmt die beste -- das dokumentierte
Funktionsprinzip hinter Chromaprint-basiertem Matching.
"""

import json
import logging
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
import wave
from collections import deque
from typing import Optional

import numpy as np

log = logging.getLogger("song_fingerprint")

FPCALC_BIN = "fpcalc"
FPCALC_TIMEOUT = 15  # Sekunden -- ein hängender fpcalc darf den Analyse-Thread nicht für immer blockieren

# Sliding-Offset-Suchbereich (siehe Moduldocstring): bei FAN_VALUE-typischen
# Chromaprint-Raten (~7,8 Werte/Sekunde) deckt +/-40 Positionen bereits gut
# +/-5s Zeitversatz zwischen zwei Snippets ab, mehr würde nur die
# Rechenzeit ohne echten Zusatznutzen erhöhen (Snippets sind ohnehin nur
# wenige Sekunden lang).
MAX_OFFSET = 40
MIN_OVERLAP = 10  # weniger überlappende Werte sind kein verlässliches Urteil


def compute_fingerprint(pcm_int16: np.ndarray, sample_rate: int) -> Optional[list[int]]:
    """PCM-Mono-Clip -> rohes Chromaprint-Integer-Array, oder None bei
    Fehler/leerem Ergebnis. `fpcalc` braucht eine Datei (kein Stdin-Support
    in gängigen Builds) -- deshalb der Umweg über eine kurzlebige
    Temp-WAV-Datei, die in jedem Fall (auch bei Fehlern) wieder gelöscht
    wird."""
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # int16
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_int16.astype(np.int16).tobytes())

        result = subprocess.run(
            [FPCALC_BIN, "-raw", "-json", tmp_path],
            capture_output=True, text=True, timeout=FPCALC_TIMEOUT,
        )
        if result.returncode != 0:
            log.warning("⚠ fpcalc lieferte Exit-Code %d: %s", result.returncode, result.stderr.strip())
            return None

        data = json.loads(result.stdout)
        raw = data.get("fingerprint")
        if not raw:
            return None
        return [int(v) for v in raw]
    except Exception as e:
        # fpcalc-Absturz/Timeout/kaputtes JSON darf nie den Analyse-Thread
        # (und damit nie den Hauptloop) mitreißen -- dieses Snippet wird
        # einfach verworfen, das nächste Intervall versucht es erneut.
        log.warning("⚠ Fingerprint-Berechnung fehlgeschlagen: %s", e)
        return None
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def similarity(fp_a: list[int], fp_b: list[int]) -> float:
    """Ähnlichkeit zweier roher Chromaprint-Arrays, 0.0-1.0 (siehe
    Moduldocstring für die Sliding-Offset-Begründung). Gibt 0.0 zurück,
    wenn keine Überlappung >= MIN_OVERLAP zustande kommt (z.B. weil eines
    der Arrays viel kürzer als MIN_OVERLAP ist)."""
    best = 0.0
    for offset in range(-MAX_OFFSET, MAX_OFFSET + 1):
        if offset >= 0:
            a, b = fp_a[offset:], fp_b[: len(fp_a) - offset]
        else:
            a, b = fp_a[: len(fp_b) + offset], fp_b[-offset:]
        n = min(len(a), len(b))
        if n < MIN_OVERLAP:
            continue
        differing_bits = sum((x ^ y).bit_count() for x, y in zip(a[:n], b[:n]))
        score = 1.0 - differing_bits / (32 * n)
        if score > best:
            best = score
    return best


class SongFingerprintDB:
    """SQLite-gestützter Cache bekannter Songs. ANDERS als FingerprintDB
    (deren Connection exklusiv dem Hauptloop-Thread gehört, weil
    match_or_learn() dort SYNCHRON aufgerufen wird): match_or_learn() hier
    läuft IMMER aus SongRecognizers Hintergrund-Thread (bewusst asynchron,
    siehe dessen Docstring -- ein fpcalc-Subprocess-Call soll den Hauptloop
    nie blockieren). Eine über den Hauptloop-Thread erzeugte, dauerhafte
    Connection wäre damit über eine ANDERE Thread-Identität als die
    erzeugende angesprochen -- sqlite3 wirft dafür hart
    "SQLite objects created in a thread can only be used in that same
    thread" (live beim Testen aufgetreten). Deshalb öffnet JEDE Methode
    hier ihre eigene kurzlebige Connection, exakt das Muster von
    delete_fingerprint()/clear_all() unten -- keine Ausnahme für den
    Hot-Path wie bei FingerprintDB."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS song_fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint_hash TEXT NOT NULL,
                    title TEXT,
                    artist TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    play_count INTEGER DEFAULT 1,
                    station_id TEXT
                )
            """)
            # Kalibrierungs-Logging für similarity_threshold (siehe
            # SESSION.md, Eintrag zu diesem Zwischenschritt): ein Ähnlichkeit-
            # Skalarwert taugt nichts, eine ganze Verteilung von Hit- vs.
            # Miss-Werten aus echtem Betrieb schon -- Zweck rein temporär
            # (Kalibrierung), kein Nutzer-Feature, deshalb keine eigene Datei/
            # kein eigener Bind-Mount, nur eine zweite Tabelle hier.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS song_match_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    station_id TEXT,
                    similarity REAL NOT NULL,
                    threshold REAL NOT NULL,
                    is_hit INTEGER NOT NULL,
                    matched_song_id INTEGER,
                    play_count INTEGER
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def match_or_learn(self, fingerprint: list[int], station_id: str,
                        similarity_threshold: float) -> Optional[dict]:
        """Vergleicht `fingerprint` per Brute-Force gegen alle gecachten
        Songs (siehe ARCHITECTURE.md, "Offene Punkte" zur Skalierungsgrenze
        dieses Ansatzes). `similarity_threshold` wird bei jedem Aufruf frisch
        übergeben (nicht am Objekt fixiert), damit eine Änderung über
        /config wie bei stt_filter.confidence_threshold ohne Neustart wirkt.
        Bei Treffer: play_count/last_seen/station_id aktualisieren,
        Match-Info zurückgeben. Bei keinem Treffer: neuen Eintrag anlegen
        (title/artist NULL -- Phase 2 füllt sie später über den
        Cloud-Lookup), None zurückgeben.

        Protokolliert JEDEN Aufruf zusätzlich in `song_match_log`
        (Kalibrierungs-Zwischenschritt vor der eigentlichen
        Threshold-Bestimmung, siehe SESSION.md) -- voller Similarity-Wert,
        der zum Zeitpunkt dieses Aufrufs geltende Threshold (kann sich über
        /config während der Sammelphase ändern) und das Hit/Miss-Urteil.
        Ändert nichts am Rückgabewert/Verhalten dieser Methode."""
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            rows = c.execute(
                "SELECT id, fingerprint_hash, title, artist, play_count FROM song_fingerprints"
            ).fetchall()

            best_id, best_score, best_row = None, 0.0, None
            for row_id, fp_text, title, artist, play_count in rows:
                candidate = [int(v) for v in fp_text.split(",")] if fp_text else []
                score = similarity(fingerprint, candidate)
                if score > best_score:
                    best_id, best_score, best_row = row_id, score, (title, artist, play_count)

            now = time.strftime("%Y-%m-%d %H:%M:%S")
            if best_id is not None and best_score >= similarity_threshold:
                title, artist, play_count = best_row
                c.execute(
                    "UPDATE song_fingerprints SET play_count = play_count + 1, last_seen = ?, "
                    "station_id = ? WHERE id = ?",
                    (now, station_id, best_id),
                )
                c.execute(
                    "INSERT INTO song_match_log (ts, station_id, similarity, threshold, is_hit, "
                    "matched_song_id, play_count) VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (now, station_id, best_score, similarity_threshold, best_id, play_count + 1),
                )
                conn.commit()
                log.debug("[song_fingerprint] Treffer: Song #%d ('%s' - '%s'), Ähnlichkeit %.2f, "
                          "bereits %dx gehört", best_id, artist, title, best_score, play_count + 1)
                return {"song_id": best_id, "title": title, "artist": artist,
                        "play_count": play_count + 1, "similarity": best_score}

            c.execute(
                "INSERT INTO song_match_log (ts, station_id, similarity, threshold, is_hit, "
                "matched_song_id, play_count) VALUES (?, ?, ?, ?, 0, ?, NULL)",
                (now, station_id, best_score, similarity_threshold, best_id),
            )

            fp_text = ",".join(str(v) for v in fingerprint)
            c.execute(
                "INSERT INTO song_fingerprints (fingerprint_hash, title, artist, first_seen, last_seen, "
                "play_count, station_id) VALUES (?, NULL, NULL, ?, ?, 1, ?)",
                (fp_text, now, now, station_id),
            )
            conn.commit()
            log.debug("[song_fingerprint] neuer Song gelernt (bester Kandidat hatte nur Ähnlichkeit %.2f, "
                      "Schwelle %.2f)", best_score, similarity_threshold)
            return None
        finally:
            conn.close()


def delete_fingerprint(db_path: str, song_id: int) -> bool:
    """Löscht einen Song-Fingerprint anhand der DB-Datei (eigene, kurze
    Connection statt der laufenden SongFingerprintDB-Instanz des
    Hauptprozesses zu teilen, siehe Klassendocstring). Gibt True zurück,
    falls ein Eintrag gelöscht wurde."""
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        deleted = c.execute("DELETE FROM song_fingerprints WHERE id = ?", (song_id,)).rowcount
        conn.commit()
        return deleted > 0
    finally:
        conn.close()


def clear_all(db_path: str) -> int:
    """Löscht ALLE gecachten Song-Fingerprints. Gibt die Anzahl gelöschter
    Einträge zurück (gleiches Muster wie fingerprint.clear_all())."""
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        count = c.execute("SELECT COUNT(*) FROM song_fingerprints").fetchone()[0]
        c.execute("DELETE FROM song_fingerprints")
        conn.commit()
        log.info("🗑 Song-Fingerprint-Cache geleert: %d Eintrag/Einträge gelöscht.", count)
        return count
    finally:
        conn.close()


def on_unknown_fingerprint(pcm_int16: np.ndarray, fingerprint: list[int], station_id: str):
    """Stub für Phase 2 (Cloud-Lookup via AudD bei Cache-Miss, siehe
    SESSION.md/ARCHITECTURE.md) -- loggt hier nur, löst noch KEINEN
    Netzwerk-Call aus."""
    log.info("🎵 Unbekannter Song auf Sender '%s' (%d Fingerprint-Werte) -- "
             "würde hier Cloud-Lookup auslösen (Phase 2, noch nicht implementiert).",
             station_id, len(fingerprint))


class SongRecognizer:
    """Sammelt PCM-Fenster während Musik läuft und stößt alle
    `interval_seconds` eine asynchrone Chromaprint-Analyse an -- Async-
    Muster 1:1 von SttFilter.sample_async() übernommen (Lock + `_busy`-
    Guard, kein Thread-Stapeln falls fpcalc mal länger braucht als das
    Intervall). Läuft nur im Radio-Modus (siehe ARCHITECTURE.md) und nur,
    solange `label == "music"` ist -- der Aufrufer entscheidet das, hier
    wird nur gesammelt/ausgelöst."""

    def __init__(self, db: SongFingerprintDB, sample_rate: int, window_seconds: float,
                 snippet_seconds: float):
        """`snippet_seconds` legt die Ringpuffer-Tiefe fest und ist deshalb
        NUR beim Prozessstart wirksam -- anders als interval_seconds/
        similarity_threshold (siehe maybe_recognize_async()), die bei jedem
        Aufruf frisch aus settings.json kommen und so ohne Neustart wirken.
        Eine Änderung von snippet_seconds über /config greift daher wie
        tls_enabled erst nach einem Container-Neustart."""
        self.db = db
        self.sample_rate = sample_rate
        snippet_windows = max(1, round(snippet_seconds / window_seconds))
        self._ring = deque(maxlen=snippet_windows)
        self._lock = threading.Lock()
        self._busy = False
        self._last_run_at = 0.0
        self._last_fingerprint: Optional[list[int]] = None
        self._last_station_id: Optional[str] = None

    def feed(self, pcm_int16: np.ndarray):
        self._ring.append(pcm_int16)

    def reset(self):
        """An JEDER Stelle aufzurufen, an der auch detector.reset() läuft
        (echter Streamwechsel) -- siehe ARCHITECTURE.md/Modul-Docstring
        von speech_detector.py. Ohne das würde der Ringpuffer Audio zweier
        verschiedener Sender vermischen (Datenmüll-Fingerprint), und
        `_last_fingerprint` würde den neuen Sender fälschlich mit dem
        zuletzt gehörten Song des ALTEN Senders vergleichen."""
        self._ring.clear()
        with self._lock:
            self._last_fingerprint = None
            self._last_station_id = None

    def maybe_recognize_async(self, now: float, station_id: str,
                               interval_seconds: float, similarity_threshold: float):
        """`interval_seconds`/`similarity_threshold` werden bei jedem Tick
        frisch aus state.song_recognition_cfg übergeben (siehe Hauptloop-
        Muster für stt_filter_cfg["sample_interval_seconds"]) -- eine
        Änderung über /config wirkt dadurch ohne Neustart."""
        if len(self._ring) < self._ring.maxlen:
            return  # Ringpuffer noch nicht voll seit dem letzten reset()/Musikbeginn
        if now - self._last_run_at < interval_seconds:
            return
        with self._lock:
            if self._busy:
                return
            self._busy = True
        self._last_run_at = now
        snapshot = np.concatenate(self._ring)

        def _run():
            try:
                fp = compute_fingerprint(snapshot, self.sample_rate)
                if fp is None:
                    return
                with self._lock:
                    same_song = (station_id == self._last_station_id
                                 and self._last_fingerprint is not None
                                 and similarity(fp, self._last_fingerprint) >= similarity_threshold)
                    self._last_fingerprint = fp
                    self._last_station_id = station_id
                if same_song:
                    # Songwechsel-Erkennung: kein Wechsel seit dem letzten
                    # Snippet -- Matching-Logik (DB-Scan) nicht erneut
                    # anstoßen, spart CPU (siehe Vorgabe/ARCHITECTURE.md).
                    return
                match = self.db.match_or_learn(fp, station_id, similarity_threshold)
                if match is None:
                    on_unknown_fingerprint(snapshot, fp, station_id)
            except Exception as e:
                log.warning("⚠ Song-Erkennungs-Sample übersprungen (Fehler: %s)", e)
            finally:
                with self._lock:
                    self._busy = False

        threading.Thread(target=_run, daemon=True, name="song-fp-sample").start()
