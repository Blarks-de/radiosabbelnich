"""
fingerprint.py — Shazam-artiges Audio-Fingerprinting, um WIEDERHOLTE
Audio-Clips (Jingles, Werbespots) zuverlässig wiederzuerkennen.

Prinzip (klassischer "Constellation Map" Ansatz):
  1. Spektrogramm des Clips berechnen (STFT).
  2. Echte lokale Maxima in Zeit UND Frequenz finden ("Sterne am Himmel") —
     nicht einfach die lautesten Bins, sondern Punkte, die in ihrer
     Zeit-Frequenz-Nachbarschaft herausstechen. Siehe "Warum 2D-Peaks"
     unten für den Grund, warum das nicht optional ist.
  3. Peaks paarweise verhashen: hash(f1, f2, delta_t) — robust gegen
     Rauschen/Lautstärke, weil nur relative Peak-Positionen zählen.
  4. Hashes in SQLite ablegen. Neuer Clip -> Hashes gegen DB matchen:
     viele Treffer mit KONSISTENTEM Zeitversatz = derselbe Clip wieder da.
  5. Kein Treffer -> Clip ist neu, wird selbst in die DB aufgenommen
     (die Datenbank lernt so mit der Zeit alle wiederkehrenden Ads/Jingles).

Das ist bewusst simpel gehalten (keine externen Libs wie chromaprint),
dafür in Python+SQLite komplett selbst verständlich und wartbar.

Warum 2D-Peaks (Zeit + Frequenz), nicht nur "lautester Bin pro Frame":
Die ursprüngliche Version wählte pro Zeitframe schlicht die N stärksten
Bins. Das matchte auf echtem Broadcast-Radio fast JEDES Sprache-Fenster
gegen JEDES andere, egal wie unterschiedlich der Inhalt — verifiziert
mit 26 echten, unterschiedlichen 3s-Mitschnitten aus fingerprint_clips/:
351 von 351 möglichen Paaren "matchten" mit bis zu 6455 Hashes/Clip.
Zwei Ursachen gefunden: (a) tieffrequenter Netzbrumm (~50Hz + Oberwellen)
dominierte die lautesten Bins in praktisch jedem Clip unabhängig vom
Inhalt, (b) menschliche Sprache hat generisch ähnliche Formant-Energie
(die immer gleichen Frequenzbereiche sind "laut"), was "lautester Bin"
zu einem Content-unabhängigen Merkmal macht. Die pro Frame lautesten
Bins sind für Sprache einfach nicht diskriminativ genug.
Fix: nur Punkte behalten, die eine ECHTE lokale Spitze in einer
Zeit-Frequenz-Nachbarschaft sind (± ein paar Frames, ± ein paar Bins) —
das filtert dauerhaft anwesende/generische Energie (Brumm, Formanten)
zuverlässig raus und behält nur echte "Landmarken"-Ereignisse (Onsets,
markante Töne). Damit sank die Fehlerquote im selben 26-Clip-Test auf
1 von 351 Paaren (Match-Stärke 14, deutlich unter dem, was ein echter
Treffer erreicht — Tests mit demselben Clip + Rauschen/halber Lautstärke/
0.1s Zeitversatz lagen bei 104–702 Treffern).
"""

import sqlite3
import time
from collections import Counter
from typing import Optional

import numpy as np

# ----------------------------------------------------------------------
# Parameter (Trade-off: mehr/engere Nachbarschaft = mehr Peaks = robuster
# aber mehr Rechenzeit, größere DB, und potenziell weniger diskriminativ)
# ----------------------------------------------------------------------
FRAME_SIZE = 1024
HOP_SIZE = 512
MIN_FREQ_HZ = 200             # alles darunter ausschließen (Netzbrumm/Rumpeln,
                               # nicht content-spezifisch, siehe Modul-Docstring)
PEAK_NEIGHBORHOOD_TIME = 5    # Frames (~58ms bei HOP_SIZE=512/44100Hz)
PEAK_NEIGHBORHOOD_FREQ = 15   # Bins (~630Hz bei FRAME_SIZE=1024/44100Hz)
PEAK_AMP_MIN_FACTOR = 3.0     # Peak muss > mean + FACTOR*std der Log-Magnitude sein
FAN_VALUE = 5                 # wie viele nachfolgende Peaks pro Anker gepaart werden
TARGET_ZONE_FRAMES = 40       # max. Zeitabstand (in Frames) für Peak-Paare
MIN_HASH_MATCHES = 25         # ab so vielen konsistenten Treffern gilt ein Clip als
                               # "wiedererkannt" — mit Sicherheitsabstand zum stärksten
                               # beobachteten Fehltreffer (14) im 26-Clip-Test oben


def _spectrogram(pcm_int16: np.ndarray, sr: int) -> np.ndarray:
    """STFT-Magnitudenspektrum, ein Frame pro Zeile."""
    samples = pcm_int16.astype(np.float64) / 32768.0
    n_frames = 1 + (len(samples) - FRAME_SIZE) // HOP_SIZE
    window = np.hanning(FRAME_SIZE)
    spec = np.zeros((n_frames, FRAME_SIZE // 2 + 1))
    for i in range(n_frames):
        start = i * HOP_SIZE
        frame = samples[start:start + FRAME_SIZE] * window
        spec[i] = np.abs(np.fft.rfft(frame))
    return spec


def _local_max_mask(arr: np.ndarray, t_win: int, f_win: int) -> np.ndarray:
    """True an Stellen, die das Maximum ihrer (t_win x f_win)-Nachbarschaft
    sind — reines-numpy-Ersatz für scipy.ndimage.maximum_filter (keine
    zusätzliche Abhängigkeit für eine simple 2D-Sliding-Window-Operation)."""
    pad_t, pad_f = t_win // 2, f_win // 2
    padded = np.pad(arr, ((pad_t, pad_t), (pad_f, pad_f)), mode="constant", constant_values=-np.inf)
    maxed = np.full_like(arr, -np.inf)
    for dt in range(t_win):
        for df in range(f_win):
            shifted = padded[dt:dt + arr.shape[0], df:df + arr.shape[1]]
            maxed = np.maximum(maxed, shifted)
    return arr >= maxed


def _spectrogram_peaks(pcm_int16: np.ndarray, sr: int) -> list[tuple[int, int]]:
    """Liefert Liste von (frame_idx, freq_bin) für echte 2D-Landmarken
    (lokale Maxima in Zeit UND Frequenz, siehe Modul-Docstring)."""
    spec = _spectrogram(pcm_int16, sr)
    if spec.shape[0] < 2:
        return []

    min_freq_bin = int(MIN_FREQ_HZ / (sr / FRAME_SIZE))
    region = spec[:, min_freq_bin:]
    if region.size == 0 or region.max() < 1e-6:
        return []

    log_spec = np.log1p(region)
    mask_max = _local_max_mask(log_spec, PEAK_NEIGHBORHOOD_TIME, PEAK_NEIGHBORHOOD_FREQ)
    threshold = log_spec.mean() + PEAK_AMP_MIN_FACTOR * log_spec.std()
    mask = mask_max & (log_spec > threshold)

    ts, fs = np.nonzero(mask)
    return list(zip(ts.tolist(), (fs + min_freq_bin).tolist()))


def _generate_hashes(peaks: list[tuple[int, int]]) -> list[tuple[str, int]]:
    """Paart Peaks zu Hashes: (hash_string, anchor_offset_frame)."""
    hashes = []
    peaks_sorted = sorted(peaks, key=lambda p: p[0])
    n = len(peaks_sorted)
    for i in range(n):
        t1, f1 = peaks_sorted[i]
        fanned = 0
        for j in range(i + 1, n):
            t2, f2 = peaks_sorted[j]
            dt = t2 - t1
            if dt <= 0:
                continue
            if dt > TARGET_ZONE_FRAMES:
                break
            h = f"{f1}-{f2}-{dt}"
            hashes.append((h, t1))
            fanned += 1
            if fanned >= FAN_VALUE:
                break
    return hashes


def fingerprint_clip(pcm_int16: np.ndarray, sr: int) -> list[tuple[str, int]]:
    """Öffentliche Funktion: PCM-Clip -> Liste von (hash, offset_frame)."""
    peaks = _spectrogram_peaks(pcm_int16, sr)
    return _generate_hashes(peaks)


class FingerprintDB:
    """SQLite-gestützte Datenbank bekannter Audio-Clips (Jingles/Ads)."""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self):
        c = self.conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT,
                first_seen TEXT,
                last_seen TEXT,
                times_seen INTEGER DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS hashes (
                hash TEXT NOT NULL,
                clip_id INTEGER NOT NULL,
                offset INTEGER NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_hash ON hashes(hash)")
        self.conn.commit()

    def match_or_learn(self, pcm_int16: np.ndarray, sr: int, label: str,
                        verbose: bool = False) -> Optional[dict]:
        """Prüft den Clip gegen die DB. Bei Treffer: Zähler hochzählen,
        Match-Info zurückgeben. Bei keinem Treffer: Clip neu anlegen,
        None zurückgeben."""
        query_hashes = fingerprint_clip(pcm_int16, sr)
        if len(query_hashes) < MIN_HASH_MATCHES:
            return None  # Clip zu kurz/leise für verlässliches Fingerprinting

        c = self.conn.cursor()
        # Zähle pro (clip_id, delta) wie oft ein Hash mit konsistentem
        # Zeitversatz matcht -> das filtert Zufallstreffer raus.
        votes = Counter()
        hash_list = [h for h, _ in query_hashes]
        query_offsets = {h: off for h, off in query_hashes}

        # Batch-Lookup in Chunks, um SQLite-Parameterlimits nicht zu sprengen
        CHUNK = 500
        for i in range(0, len(hash_list), CHUNK):
            chunk = hash_list[i:i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = c.execute(
                f"SELECT hash, clip_id, offset FROM hashes WHERE hash IN ({placeholders})",
                chunk,
            ).fetchall()
            for h, clip_id, db_offset in rows:
                delta = db_offset - query_offsets[h]
                votes[(clip_id, delta)] += 1

        if votes:
            (best_clip_id, _), best_count = votes.most_common(1)[0]
            if best_count >= MIN_HASH_MATCHES:
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                c.execute(
                    "UPDATE clips SET times_seen = times_seen + 1, last_seen = ? WHERE id = ?",
                    (now, best_clip_id),
                )
                self.conn.commit()
                row = c.execute(
                    "SELECT label, times_seen FROM clips WHERE id = ?", (best_clip_id,)
                ).fetchone()
                if verbose:
                    print(f"    [fingerprint] Treffer: Clip #{best_clip_id} "
                          f"('{row[0]}'), {best_count} konsistente Hash-Matches, "
                          f"bereits {row[1]}x gesehen")
                return {"clip_id": best_clip_id, "label": row[0],
                        "times_seen": row[1], "match_strength": best_count}

        # kein Treffer -> als neuen Clip lernen
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "INSERT INTO clips (label, first_seen, last_seen, times_seen) VALUES (?, ?, ?, 1)",
            (label, now, now),
        )
        clip_id = c.lastrowid
        c.executemany(
            "INSERT INTO hashes (hash, clip_id, offset) VALUES (?, ?, ?)",
            [(h, clip_id, off) for h, off in query_hashes],
        )
        self.conn.commit()
        if verbose:
            print(f"    [fingerprint] neuer Clip #{clip_id} gelernt "
                  f"({len(query_hashes)} Hashes)")
        return None

    def close(self):
        self.conn.close()


def delete_clip(db_path: str, clip_id: int) -> Optional[str]:
    """Löscht einen Clip + seine Hashes anhand der DB-Datei (eigene, kurze
    Connection statt der laufenden FingerprintDB-Instanz des Hauptprozesses
    zu teilen — sqlite3-Connection-Objekte sind nicht thread-übergreifend
    sicher, und webui.py ruft das aus einem anderen Thread auf).

    Für den "Zapping-Fehler"-Knopf im Web-Interface: falls ein Fingerprint-
    Treffer fälschlich einen Sender-Wechsel ausgelöst hat, kann der Nutzer
    den zugrundeliegenden Clip damit aus der DB werfen, statt dass er
    dauerhaft weiter fehlklassifiziert.

    Gibt das Label des gelöschten Clips zurück, oder None falls die ID
    nicht (mehr) existiert."""
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        row = c.execute("SELECT label FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            return None
        c.execute("DELETE FROM hashes WHERE clip_id = ?", (clip_id,))
        c.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        conn.commit()
        return row[0]
    finally:
        conn.close()
