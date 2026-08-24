#!/usr/bin/env python3
"""Wertet song_match_log aus data/song_fingerprints.db aus (siehe SESSION.md,
2026-08-23/24 -- "Nächster Schritt"): zeigt die Similarity-Verteilung für
Hit- und Miss-Zeilen getrennt, damit sich similarity_threshold (aktuell
Platzhalter 0.65, settings.json) nach ein paar Tagen Sammelzeit empirisch
statt geraten bestimmen lässt.

Aufruf: python3 check_song_calibration.py [Pfad zur DB, default data/song_fingerprints.db]
"""
import sqlite3
import statistics
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "data/song_fingerprints.db"


def percentiles(values, ps=(10, 25, 50, 75, 90)):
    if len(values) < 2:  # statistics.quantiles() braucht mind. 2 Werte
        return {}
    q = statistics.quantiles(values, n=100, method="inclusive")
    return {p: q[p - 1] for p in ps}


def print_stats(label, values):
    print(f"\n{label} (n={len(values)})")
    if not values:
        print("  keine Daten")
        return
    print(f"  min={min(values):.4f}  max={max(values):.4f}  "
          f"mean={statistics.mean(values):.4f}  median={statistics.median(values):.4f}")
    p = percentiles(values)
    if p:
        print("  Perzentile: " + "  ".join(f"p{k}={v:.4f}" for k, v in p.items()))


def histogram(hits, misses, bucket_size=0.05):
    print(f"\nHistogramm (Bucket-Breite {bucket_size}, H=Hit-Zeilen, M=Miss-Zeilen):")
    n_buckets = int(round(1.0 / bucket_size))
    for i in range(n_buckets):
        lo, hi = i * bucket_size, (i + 1) * bucket_size
        h = sum(1 for v in hits if lo <= v < hi)
        m = sum(1 for v in misses if lo <= v < hi)
        if h == 0 and m == 0:
            continue
        print(f"  [{lo:.2f}-{hi:.2f}) H:{'█' * h}{h:>3}   M:{'░' * m}{m:>3}")


def main():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT similarity, threshold, is_hit, ts FROM song_match_log ORDER BY ts"
    ).fetchall()
    con.close()

    if not rows:
        print(f"{DB_PATH}: song_match_log ist leer -- noch keine Vergleiche protokolliert. "
              "song_recognition.enabled in settings.json prüfen (siehe SESSION.md, 2026-08-24).")
        return

    hits = [r[0] for r in rows if r[2]]
    misses = [r[0] for r in rows if not r[2]]
    current_threshold = rows[-1][1]

    print(f"{DB_PATH}: {len(rows)} Zeilen, Zeitraum {rows[0][3]} bis {rows[-1][3]}")
    print(f"Aktuell konfigurierter threshold (letzte Zeile): {current_threshold}")
    print_stats("HITS", hits)
    print_stats("MISSES", misses)
    histogram(hits, misses)

    if hits and misses:
        gap_lo, gap_hi = max(misses), min(hits)
        if gap_lo < gap_hi:
            print(f"\n-> Saubere Lücke zwischen Miss-Maximum ({gap_lo:.4f}) und "
                  f"Hit-Minimum ({gap_hi:.4f}) -- ein threshold irgendwo dazwischen "
                  f"(z.B. {(gap_lo + gap_hi) / 2:.4f}) trennt die bisherigen Daten "
                  f"vollständig.")
        else:
            print(f"\n-> Keine saubere Lücke: Miss-Maximum ({gap_lo:.4f}) liegt ÜBER "
                  f"Hit-Minimum ({gap_hi:.4f}) -- Überlappungsbereich beachten, "
                  f"Perzentile/Histogramm oben für eine Abwägung nutzen statt eines "
                  f"einzelnen Schwellwerts mit perfekter Trennung.")
    else:
        print("\n-> Noch zu wenig Daten für eine Aussage (Hits UND Misses nötig) -- "
              "weiter sammeln lassen.")


if __name__ == "__main__":
    main()
