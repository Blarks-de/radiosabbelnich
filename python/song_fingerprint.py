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

import base64
import io
import json
import logging
import os
import sqlite3
import statistics
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
import wave
import xml.etree.ElementTree as ET
from collections import deque
from typing import Callable, Optional

import numpy as np

log = logging.getLogger("song_fingerprint")

FPCALC_BIN = "fpcalc"
FPCALC_TIMEOUT = 15  # Sekunden -- ein hängender fpcalc darf den Analyse-Thread nicht für immer blockieren

# Phase 2: AudD-Cloud-Lookup bei Cache-Miss (siehe on_unknown_fingerprint()
# unten). Token kommt bewusst aus der Umgebung, nicht aus settings.json --
# das Web-Interface hat keine Auth (siehe CLAUDE.md, "Kein Auth, nur hinter
# VPN"), ein API-Token gehört da nicht rein. Gleiches Muster wie
# ICECAST_SOURCE_PASSWORD: .env -> docker-compose.yml-Passthrough -> hier
# nur gelesen. Einmal beim Modul-Import gelesen (wie TLS_CERT_FILE); ein
# geänderter Token braucht wie andere Umgebungsvariablen einen Container-
# Neustart.
AUDD_API_TOKEN = os.environ.get("AUDD_API_TOKEN", "").strip() or None
AUDD_URL = "https://api.audd.io/recognize/"
AUDD_TIMEOUT = 15  # Sekunden, gleiche Größenordnung wie FPCALC_TIMEOUT

# Sicherheitsnetz gegen Kontingent-Verbrauch: similarity_threshold ist
# laut ARCHITECTURE.md/README noch ein unkalibrierter Platzhalter -- greift
# er in der Praxis zu locker/streng, könnte match_or_learn() denselben Song
# wiederholt als "neu" einstufen und bei JEDEM Intervall einen bezahlten
# AudD-Request auslösen. Fester Mindestabstand statt Nutzer-Einstellung,
# gleiche Kategorie wie MAX_OFFSET/MIN_OVERLAP unten -- interne Leitplanke,
# keine Fachentscheidung.
AUDD_MIN_INTERVAL_SECONDS = 60.0
_audd_lock = threading.Lock()
_audd_last_call_at = 0.0

# Nur ~7 von AudDs ~40 Fehlercodes sind öffentlich dokumentiert (siehe
# docs.audd.io) -- #900/#901 sind die einzigen zwei DOKUMENTIERTEN, die auf
# ein Token-/Kontingent-Problem hindeuten ("invalid token" bzw. "no
# api_token passed, and the limit was reached"). #902 ist NICHT
# dokumentiert, aber live beobachtet (siehe SESSION.md) genau in dem
# Moment, als das reale Kontingent mit einem GÜLTIGEN Token aufgebraucht
# war -- passt exakt ins Namensschema von #900/#901 (900=Token ungültig,
# 901=Limit ohne Token, 902=Limit MIT Token) und wird deshalb ebenfalls als
# Kontingent-Fall behandelt. Alles andere landet als generischer
# "audd_error" (Code sichtbar in get_audd_status()) statt geraten zu
# werden.
_AUDD_QUOTA_ERROR_CODES = {900, 901, 902}

# Letzter AudD-Aufrufstatus für die Live-Anzeige (webui.py, now_playing_tags
# im "pending"-Fall) -- siehe get_audd_status()/_record_audd_status(). Reines
# In-Memory-Live-Signal wie _audd_last_call_at, bewusst NICHT in
# settings.json persistiert: ein Neustart braucht dafür keine besondere
# Behandlung, der nächste tatsächliche AudD-Aufruf (spätestens nach
# AUDD_MIN_INTERVAL_SECONDS) setzt den Status ohnehin sofort neu.
_audd_last_status: dict = {"state": None, "error_code": None, "error_message": None, "checked_at": None}


def _record_audd_status(state: str, error_code: Optional[int] = None, error_message: Optional[str] = None):
    with _audd_lock:
        _audd_last_status.update({
            "state": state, "error_code": error_code,
            "error_message": error_message, "checked_at": time.time(),
        })


def get_audd_status() -> Optional[dict]:
    """Letzter AudD-Aufrufstatus ({"state", "error_code", "error_message",
    "checked_at"}) -- state ist "ok"/"quota"/"network_error"/"audd_error",
    oder None, solange noch kein AudD-Aufruf versucht wurde (z.B. Cloud-
    Lookup deaktiviert, oder seit dem Start noch kein Cache-Miss). Wird
    NUR von audd_lookup() gesetzt, nicht vom "cloud_lookup_enabled=false"-
    Fall in on_unknown_fingerprint() -- sonst würde die Live-Anzeige für
    jeden, der Cloud-Lookup bewusst aus gelassen hat, dauerhaft einen
    "nicht konfiguriert"-Hinweis zeigen statt des neutralen Pending-Texts."""
    with _audd_lock:
        return dict(_audd_last_status) if _audd_last_status["state"] is not None else None


# Hörer-Gate (Nutzer-Wunsch, siehe SESSION.md): Song-Erkennung -- lokales
# Fingerprinting UND Cloud-Lookup -- kostet CPU/AudD-Kontingent, ist aber
# wertlos, solange niemand den Restream hört (die Live-Anzeige, für die
# identifiziert wird, hat dann kein Publikum). Gepollt statt live geprüft:
# eine Icecast-Admin-Abfrage im Hauptloop-Thread könnte bis zu mehreren
# Sekunden blockieren (Netzwerk-I/O) -- exakt das, was der ganze
# Async-Aufbau in diesem Modul vermeiden soll. Läuft deshalb in einem
# eigenen Hintergrund-Thread mit langem Intervall, der Hauptloop liest nur
# den zwischengespeicherten Bool (siehe ListenerGate.has_listeners()).
LISTENER_CHECK_INTERVAL_SECONDS = 60.0
LISTENER_CHECK_TIMEOUT = 5.0
# Verzögert den ERSTEN Check nach Prozessstart: der Icecast-Mount
# existiert erst, sobald der Hauptloop tatsächlich als Source verbunden
# hat (Sender wählen, ggf. prebuffern) -- ein Check direkt beim
# ListenerGate-Konstruktor-Aufruf (der noch vor dieser Verbindung läuft)
# fragt sonst einen noch nicht existierenden Mount ab. Live beim ersten
# Rollout beobachtet: Icecast antwortet dafür mit "400 Bad Request" statt
# einer leeren Hörerliste -- kein Bug (Fail-Open griff korrekt, Song-
# Erkennung lief unbeeinflusst weiter), aber unnötige Warnung bei jedem
# Start.
LISTENER_CHECK_STARTUP_DELAY_SECONDS = 15.0


class ListenerGate:
    """Pollt Icecasts Admin-API (dieselbe `/admin/listclients`-Route wie
    webui.py._fetch_listeners(), hier bewusst separat nachgebaut statt
    importiert -- song_fingerprint.py ist ein reines Audio-/Matching-Modul
    ohne Abhängigkeit auf den HTTP-Server, das soll so bleiben) in einem
    eigenen Hintergrund-Thread und hält einen zwischengespeicherten
    "gibt es gerade Hörer?"-Zustand vor.

    Fail-open bei Fehlern (Icecast down, falsche Credentials, Timeout,
    Admin-API nicht konfiguriert) -- ein Admin-API-Problem soll die Song-
    Erkennung nicht stillschweigend lahmlegen, lieber gelegentlich unnötig
    analysieren als bei echtem Publikum fälschlich zu pausieren.

    `on_change` (optional) wird bei JEDEM tatsächlichen Wechsel des
    Hörer-Zustands aufgerufen, mit dem neuen Bool. Grund: "pausieren"
    heißt hier nicht einfach "kurz nichts tun" -- der Aufrufer (siehe
    radiosabbelnich.py) nutzt das, um bei "keine Hörer mehr" den
    SongRecognizer-Ringpuffer per reset() zu LEEREN statt ihn einfach
    einfrieren zu lassen. Ohne das würde der Puffer beim nächsten Hörer
    fast ausschließlich veraltetes Vor-Pause-Audio enthalten (nur ein
    einzelnes frisches Fenster kommt pro feed()-Aufruf dazu) -- die erste
    Analyse nach der Rückkehr liefe dann auf einem Frankenstein-Schnipsel,
    potenziell ein sinnloser/falscher Fingerprint samt unnötigem AudD-Call.
    "Stop" statt "Pause", wie vom Nutzer gewünscht."""

    def __init__(self, admin_url: Optional[str], user: Optional[str],
                 password: Optional[str], mount: Optional[str],
                 on_change: Optional[Callable[[bool], None]] = None):
        self._admin_url = admin_url
        self._user = user
        self._password = password
        self._mount = mount
        self._on_change = on_change
        configured = bool(admin_url and user and password and mount)
        self._lock = threading.Lock()
        self._has_listeners = True  # fail-open, bis der erste echte Check durch ist
        if configured:
            threading.Thread(target=self._poll_loop, daemon=True, name="listener-gate").start()
        else:
            log.info("🎧 Listener-Gate inaktiv (ICECAST_ADMIN_URL/-USER/-PASSWORD/-MOUNT "
                     "unvollständig) -- Song-Erkennung läuft unabhängig von der Hörerzahl.")

    def has_listeners(self) -> bool:
        with self._lock:
            return self._has_listeners

    def _poll_loop(self):
        time.sleep(LISTENER_CHECK_STARTUP_DELAY_SECONDS)
        while True:
            self._check_once()
            time.sleep(LISTENER_CHECK_INTERVAL_SECONDS)

    def _check_once(self):
        count = self._fetch_listener_count()
        if count is None:
            return  # Fehler -- letzter bekannter/fail-open-Wert bleibt stehen
        now_has_listeners = count > 0
        with self._lock:
            was = self._has_listeners
            self._has_listeners = now_has_listeners
        if was == now_has_listeners:
            return
        log.info("🎧 Listener-Gate: %s (%d Hörer) -- Song-Erkennung %s.",
                 "Hörer da" if now_has_listeners else "keine Hörer mehr",
                 count, "läuft weiter" if now_has_listeners else "gestoppt (Ringpuffer wird geleert)")
        if self._on_change:
            try:
                self._on_change(now_has_listeners)
            except Exception as e:
                log.warning("⚠ Listener-Gate: on_change-Callback fehlgeschlagen: %s", e)

    def _fetch_listener_count(self) -> Optional[int]:
        url = f"{self._admin_url.rstrip('/')}/admin/listclients?mount={self._mount}"
        req = urllib.request.Request(url)
        creds = base64.b64encode(f"{self._user}:{self._password}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
        try:
            with urllib.request.urlopen(req, timeout=LISTENER_CHECK_TIMEOUT) as resp:
                data = resp.read()
            root = ET.fromstring(data)
            return sum(1 for _ in root.iter("listener"))
        except Exception as e:
            log.warning("⚠ Listener-Gate: Icecast-Admin-Abfrage fehlgeschlagen (%s) -- "
                        "Song-Erkennung läuft unverändert weiter (fail-open).", e)
            return None


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


def _multipart_encode(fields: dict, file_field: str, filename: str, file_bytes: bytes):
    """Baut einen `multipart/form-data`-Request-Body von Hand (KEINE
    zusätzliche pip-Abhängigkeit wie `requests` -- gleiche Begründung wie
    beim Rest des Projekts, siehe update_check.py/station_import.py, die
    beide nur urllib.request nutzen). Gibt (body_bytes, content_type)
    zurück."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
        f"filename=\"{filename}\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
        + file_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _parse_release_year(release_date) -> Optional[int]:
    """AudDs `release_date` ist ein Datumsstring wie "1983-04-01" (oder
    fehlt/ist leer) -- nur das Jahr interessiert hier, gleiches Format
    (`int | None`) wie audio_tags.extract_year() für die Musiksammlung."""
    if not release_date:
        return None
    try:
        return int(str(release_date)[:4])
    except ValueError:
        return None


def _parse_duration_seconds(result: dict) -> Optional[int]:
    """AudDs Kernantwort liefert KEINE Songlänge -- nur mit `return=
    apple_music,spotify` (siehe audd_lookup()) kommen zwei zusätzliche,
    verschachtelte Objekte mit je einem Millisekunden-Feld mit
    (live geprüft, siehe SESSION.md): `result["spotify"]["duration_ms"]`
    bzw. `result["apple_music"]["durationInMillis"]` -- beide praktisch
    identisch (Rundungsdifferenz im Bereich 1ms), Spotify bevorzugt, weil
    zuerst im Response-JSON. Keins von beiden ist garantiert vorhanden
    (nicht jeder Song hat einen Spotify-/Apple-Music-Treffer)."""
    spotify = result.get("spotify") or {}
    apple_music = result.get("apple_music") or {}
    duration_ms = spotify.get("duration_ms") or apple_music.get("durationInMillis")
    if not duration_ms:
        return None
    try:
        return round(int(duration_ms) / 1000)
    except (TypeError, ValueError):
        return None


def audd_lookup(pcm_int16: np.ndarray, sample_rate: int, api_token: str,
                 station_id: str = None,
                 log_request: Optional[Callable[[str, str], None]] = None) -> Optional[dict]:
    """Schickt `pcm_int16` als WAV an AudD (https://audd.io) und gibt bei
    einer erfolgreichen Identifikation {"title", "artist", "album", "year",
    "duration_seconds"} zurück (alle außer title/artist können None sein,
    falls AudD sie nicht mitliefert), sonst None -- sowohl bei "AudD kennt
    den Song nicht" als auch bei jedem
    Netzwerk-/Timeout-/Parse-Fehler (gleiches defensives Muster wie
    compute_fingerprint(): ein Cloud-Lookup darf den Analyse-Thread nie
    mitreißen). Respektiert AUDD_MIN_INTERVAL_SECONDS als Sicherheitsnetz
    gegen Kontingent-Verbrauch (siehe Modul-Kommentar oben) -- bei aktivem
    Cooldown wird gar nicht erst eine Verbindung aufgebaut (und
    get_audd_status() unverändert gelassen, siehe dort).

    Setzt bei jedem tatsächlich versuchten Aufruf den Live-Status für
    get_audd_status(): "ok" bei einer normalen AudD-Antwort (auch wenn der
    Song darin nicht erkannt wurde -- das ist kein AudD-Fehler), "quota"/
    "audd_error" bei einer AudD-eigenen Fehlerantwort (HTTP bleibt dabei
    laut AudD-Doku immer 200, der Fehler steckt im JSON-Body als
    {"status":"error","error":{"error_code":...}}), "network_error" bei
    Verbindungs-/Timeout-/Parse-Problemen.

    `log_request(station_id, outcome)` (optional, Callback statt direktem
    SongFingerprintDB-Import -- gleiches Muster wie
    update_check.UpdateChecker.on_result, hält diese Funktion isoliert
    testbar) wird bei GENAU demselben tatsächlich versuchten Aufruf
    zusätzlich zu get_audd_status() aufgerufen, aber mit feinerem
    `outcome`: "hit"/"no_match" statt beide unter "ok" (Statistik-Sektion
    Config-Seite braucht die Erfolgsquote, get_audd_status() fürs Live-
    Banner nur "läuft/läuft nicht"). Ein Cooldown-Skip ruft `log_request`
    NICHT auf -- das war kein tatsächlicher AudD-Request, würde die
    Request-Zählung sonst künstlich aufblähen.

    WAV wird in-memory gebaut (io.BytesIO), kein Temp-File wie bei
    compute_fingerprint() -- fpcalc braucht zwingend einen Dateipfad,
    urllib dagegen nimmt die Bytes direkt."""
    global _audd_last_call_at
    with _audd_lock:
        now = time.time()
        if now - _audd_last_call_at < AUDD_MIN_INTERVAL_SECONDS:
            log.info("🎵 AudD-Cooldown aktiv (< %.0fs seit letztem Call) -- "
                      "Anfrage übersprungen.", AUDD_MIN_INTERVAL_SECONDS)
            return None
        _audd_last_call_at = now

    try:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # int16
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_int16.astype(np.int16).tobytes())

        body, content_type = _multipart_encode(
            # "return=apple_music,spotify" NUR wegen der Songlänge (siehe
            # _parse_duration_seconds()) -- AudDs Kernantwort liefert sie
            # nicht. Kostet keine zusätzliche Anfrage, nur mehr Felder in
            # derselben Antwort.
            {"api_token": api_token, "return": "apple_music,spotify"},
            "file", "snippet.wav", buf.getvalue()
        )
        req = urllib.request.Request(
            AUDD_URL, data=body, method="POST",
            headers={"Content-Type": content_type},
        )
        with urllib.request.urlopen(req, timeout=AUDD_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        # Breiter Fang wie bei compute_fingerprint() -- ein Cloud-Lookup
        # (Netzwerk, Timeout, kaputtes JSON, unerwartete Antwortstruktur)
        # darf den Analyse-Thread nie mitreißen, egal welcher Fehler genau
        # auftritt.
        log.warning("⚠ AudD-Lookup fehlgeschlagen (Netzwerk/Zeitüberschreitung/Parsing): %s", e)
        _record_audd_status("network_error", error_message=str(e))
        if log_request:
            log_request(station_id, "network_error")
        return None

    if data.get("status") == "error":
        # AudD meldet den Fehler im JSON-Body, nicht per HTTP-Status (siehe
        # Docstring) -- #900/#901 sind die einzigen zwei dokumentierten
        # Codes für ein Token-/Kontingent-Problem, alles andere bleibt ein
        # generischer "audd_error" mit sichtbarem Code statt geraten zu
        # werden (siehe _AUDD_QUOTA_ERROR_CODES oben).
        err = data.get("error") or {}
        code, msg = err.get("error_code"), err.get("error_message")
        log.warning("⚠ AudD meldet einen API-Fehler (Code %s): %s", code, msg)
        outcome = "quota" if code in _AUDD_QUOTA_ERROR_CODES else "audd_error"
        _record_audd_status(outcome, error_code=code, error_message=msg)
        if log_request:
            log_request(station_id, outcome)
        return None

    _record_audd_status("ok")
    if not data.get("result"):
        if log_request:
            log_request(station_id, "no_match")
        return None  # AudD hat den Song nicht erkannt -- kein Fehler
    result = data["result"]
    title, artist = result.get("title"), result.get("artist")
    if not title or not artist:
        if log_request:
            log_request(station_id, "no_match")
        return None
    if log_request:
        log_request(station_id, "hit")
    return {
        "title": title, "artist": artist,
        "album": result.get("album") or None,
        "year": _parse_release_year(result.get("release_date")),
        "duration_seconds": _parse_duration_seconds(result),
    }


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
            # Migration für DBs von vor der Album/Jahr-Ergänzung (siehe
            # SESSION.md) -- CREATE TABLE IF NOT EXISTS oben greift bei
            # einer schon bestehenden Tabelle nicht mehr, SQLite kennt kein
            # "ADD COLUMN IF NOT EXISTS", deshalb erst per PRAGMA prüfen
            # (identisches Muster wie music_scan.py bei der bpm-Spalte).
            columns = {row[1] for row in conn.execute("PRAGMA table_info(song_fingerprints)")}
            if "album" not in columns:
                conn.execute("ALTER TABLE song_fingerprints ADD COLUMN album TEXT")
                log.info("🎵 Song-Fingerprint-DB-Schema migriert: Spalte 'album' ergänzt.")
            if "year" not in columns:
                conn.execute("ALTER TABLE song_fingerprints ADD COLUMN year INTEGER")
                log.info("🎵 Song-Fingerprint-DB-Schema migriert: Spalte 'year' ergänzt.")
            if "duration_seconds" not in columns:
                conn.execute("ALTER TABLE song_fingerprints ADD COLUMN duration_seconds INTEGER")
                log.info("🎵 Song-Fingerprint-DB-Schema migriert: Spalte 'duration_seconds' ergänzt.")
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
            # AudD-Request-Log (Nutzer-Wunsch, Statistik-Sektion Config-
            # Seite, siehe SESSION.md): EIN Zeile pro tatsächlich
            # versuchtem audd_lookup()-Aufruf (Cooldown-Skips zählen NICHT
            # mit, siehe dort) -- ohne das gab es für "Requests heute/diese
            # Woche/gesamt" und eine echte Erfolgsquote keine Datenquelle,
            # nur unzuverlässige, schnell rotierende Logfile-Zeilen
            # (DEBUG-Level, ~1-2 Tage Retention). Gleiches Muster wie
            # song_match_log oben: rein additiv, kein Einfluss auf
            # Matching/Cloud-Lookup selbst.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audd_request_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    station_id TEXT,
                    outcome TEXT NOT NULL
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
                "SELECT id, fingerprint_hash, title, artist, play_count, album, year, duration_seconds "
                "FROM song_fingerprints"
            ).fetchall()

            best_id, best_score, best_row = None, 0.0, None
            for row_id, fp_text, title, artist, play_count, album, year, duration_seconds in rows:
                candidate = [int(v) for v in fp_text.split(",")] if fp_text else []
                score = similarity(fingerprint, candidate)
                if score > best_score:
                    best_id, best_score, best_row = row_id, score, (title, artist, play_count, album, year, duration_seconds)

            now = time.strftime("%Y-%m-%d %H:%M:%S")
            if best_id is not None and best_score >= similarity_threshold:
                title, artist, play_count, album, year, duration_seconds = best_row
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
                        "album": album, "year": year, "duration_seconds": duration_seconds,
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

    def set_cloud_metadata(self, fingerprint_hash: str, title: str, artist: str,
                            album: Optional[str] = None, year: Optional[int] = None,
                            duration_seconds: Optional[int] = None):
        """Trägt Titel/Interpret (+ optional Album/Jahr/Länge, siehe
        audd_lookup()) aus einem erfolgreichen AudD-Lookup (Phase 2, siehe
        on_unknown_fingerprint() unten) in die Zeile nach, die
        match_or_learn() beim Cache-Miss mit title/artist=NULL angelegt hat
        -- Zuordnung über denselben fingerprint_hash-Text, den
        match_or_learn() dafür schreibt. Eigene kurzlebige Connection,
        gleiches Muster wie delete_fingerprint()/clear_all() unten -- diese
        Methode läuft wie set_cloud_metadata()s Aufrufer im Hintergrund-
        Thread von SongRecognizer, nicht im Hauptloop."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE song_fingerprints SET title = ?, artist = ?, album = ?, year = ?, "
                "duration_seconds = ? WHERE fingerprint_hash = ?",
                (title, artist, album, year, duration_seconds, fingerprint_hash),
            )
            conn.commit()
        finally:
            conn.close()

    def log_audd_request(self, station_id: str, outcome: str):
        """Protokolliert EINEN tatsächlich versuchten `audd_lookup()`-Aufruf
        in `audd_request_log` -- `outcome` ist eines von "hit"/"no_match"/
        "quota"/"audd_error"/"network_error" (siehe audd_lookup()). Wird von
        dort per `log_request`-Callback aufgerufen, nicht direkt importiert
        -- gleiches Callback-Muster wie update_check.UpdateChecker.on_result,
        damit audd_lookup() selbst von SongFingerprintDB/SQLite unwissend
        und leicht isoliert testbar bleibt (siehe dortige Tests)."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO audd_request_log (ts, station_id, outcome) VALUES (?, ?, ?)",
                (time.strftime("%Y-%m-%d %H:%M:%S"), station_id, outcome),
            )
            conn.commit()
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


def _percentiles(values: list[float], ps=(10, 25, 50, 75, 90)) -> dict:
    """Identische Perzentil-Logik wie check_song_calibration.py -- absichtlich
    hier dupliziert statt von dort importiert (jenes Skript ist ein
    eigenständiges CLI-Tool außerhalb von python/, siehe CLAUDE.md-
    Dateitabelle), damit ein Skript-Refactoring diese Funktion hier nicht
    versehentlich mitreißt. `statistics.quantiles()` braucht mindestens 2
    Werte, sonst leeres Dict statt Exception."""
    if len(values) < 2:
        return {}
    q = statistics.quantiles(values, n=100, method="inclusive")
    return {p: q[p - 1] for p in ps}


def _histogram(hits: list[float], misses: list[float], bucket_size: float = 0.05) -> list[dict]:
    """20 Buckets à 0.05 über den vollen [0,1)-Wertebereich, ALLE
    (auch leere) statt wie check_song_calibration.py nur die belegten --
    eine feste Bucket-Zahl ergibt in der WebUI ein gleichbleibendes
    Balkendiagramm-Layout statt eines pro Aufruf unterschiedlich langen."""
    n_buckets = int(round(1.0 / bucket_size))
    buckets = []
    for i in range(n_buckets):
        lo, hi = round(i * bucket_size, 2), round((i + 1) * bucket_size, 2)
        h = sum(1 for v in hits if lo <= v < hi)
        m = sum(1 for v in misses if lo <= v < hi)
        buckets.append({"lo": lo, "hi": hi, "hits": h, "misses": m})
    return buckets


def _separation_gap(hits: list[float], misses: list[float]) -> Optional[dict]:
    """Identische Lücken-Analyse wie check_song_calibration.py: gibt es
    einen Schwellwert-Bereich, der ALLE bisherigen Hits von ALLEN Misses
    trennt? None, falls einer der beiden Sätze leer ist (keine Aussage
    möglich)."""
    if not hits or not misses:
        return None
    gap_lo, gap_hi = max(misses), min(hits)
    return {
        "clean_gap": gap_lo < gap_hi,
        "miss_max": gap_lo,
        "hit_min": gap_hi,
        "suggested_threshold": (gap_lo + gap_hi) / 2 if gap_lo < gap_hi else None,
    }


# AudDs kostenloses Startkontingent -- siehe README/AudD-Doku. Rein
# informativ für die Kostenschätzung unten, KEINE echte Abrechnungsgrenze,
# die dieses Programm irgendwo durchsetzt.
_AUDD_FREE_QUOTA = 300
_AUDD_COST_PER_1000_USD = 5.0


def build_recognition_stats(db_path: str, current_threshold: float) -> dict:
    """Aggregiert Kennzahlen aus song_fingerprints/song_match_log/
    audd_request_log für die Config-Seite ("🎵 Song-Erkennung –
    Statistik", Nutzer-Wunsch, siehe SESSION.md) -- reiner Lesezugriff,
    alles per SQL on-demand berechnet, kein Caching nötig (bei den hier
    üblichen Zeilenzahlen -- Größenordnung tausend -- für SQLite trivial
    schnell). Portiert die Percentil-/Histogramm-/Lücken-Logik aus
    check_song_calibration.py (siehe _percentiles()/_histogram()/
    _separation_gap() oben).

    Die AudD-"Requests"/"Kosten"-Werte zählen AUSSCHLIESSLICH ab Einführung
    von audd_request_log (siehe dort) -- sie sind NICHT der tatsächliche
    AudD-Kontostand: falls das Kontingent schon VOR diesem Logging
    (teilweise) verbraucht war, wissen wir das nicht, AudD liefert dafür
    weder ein Antwort-Feld noch einen Abfrage-Endpoint (siehe SESSION.md/
    ARCHITECTURE.md). Der Aufrufer (webui.py) muss diese Einschränkung in
    der Anzeige transparent machen, nicht nur die Zahl zeigen."""
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        total = c.execute("SELECT COUNT(*) FROM song_fingerprints").fetchone()[0]
        with_title = c.execute(
            "SELECT COUNT(*) FROM song_fingerprints WHERE title IS NOT NULL"
        ).fetchone()[0]

        top_stations = c.execute(
            "SELECT station_id, COUNT(*), SUM(title IS NOT NULL), SUM(play_count) "
            "FROM song_fingerprints GROUP BY station_id ORDER BY 2 DESC LIMIT 10"
        ).fetchall()
        top_songs = c.execute(
            "SELECT title, artist, play_count, station_id FROM song_fingerprints "
            "WHERE title IS NOT NULL ORDER BY play_count DESC LIMIT 10"
        ).fetchall()

        match_rows = c.execute(
            "SELECT similarity, is_hit, ts FROM song_match_log ORDER BY ts"
        ).fetchall()

        today_start = time.strftime("%Y-%m-%d 00:00:00")
        week_start = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7 * 86400))
        audd_total = c.execute("SELECT COUNT(*) FROM audd_request_log").fetchone()[0]
        audd_today = c.execute(
            "SELECT COUNT(*) FROM audd_request_log WHERE ts >= ?", (today_start,)
        ).fetchone()[0]
        audd_week = c.execute(
            "SELECT COUNT(*) FROM audd_request_log WHERE ts >= ?", (week_start,)
        ).fetchone()[0]
        outcome_counts = dict(c.execute(
            "SELECT outcome, COUNT(*) FROM audd_request_log GROUP BY outcome"
        ).fetchall())
        audd_first_ts = c.execute("SELECT MIN(ts) FROM audd_request_log").fetchone()[0]
    finally:
        conn.close()

    hits = [r[0] for r in match_rows if r[1]]
    misses = [r[0] for r in match_rows if not r[1]]

    hit_count = outcome_counts.get("hit", 0)
    no_match_count = outcome_counts.get("no_match", 0)
    quota_count = outcome_counts.get("quota", 0)
    audd_error_count = outcome_counts.get("audd_error", 0)
    network_error_count = outcome_counts.get("network_error", 0)
    # Erfolgsquote NUR über tatsächlich abgeschlossene Erkennungsversuche
    # (hit/no_match) -- Requests, die an einem AudD-Fehler scheiterten,
    # sagen nichts über "erkennt AudD den Song" aus, würden die Quote sonst
    # künstlich verwässern.
    completed = hit_count + no_match_count
    success_rate = (hit_count / completed) if completed else None
    # Für die Kostenschätzung zählen alle Requests, die AudD tatsächlich
    # erreicht haben (auch ein abgelehnter Kontingent-/Fehler-Request lief
    # über die Leitung) -- NUR network_error ausgenommen, da dort nie eine
    # AudD-Antwort ankam, sich also nicht sagen lässt, ob AudD ihn überhaupt
    # verarbeitet/gezählt hat.
    billable_requests = hit_count + no_match_count + quota_count + audd_error_count
    billable_overage = max(0, billable_requests - _AUDD_FREE_QUOTA)
    estimated_cost_usd = round(billable_overage / 1000 * _AUDD_COST_PER_1000_USD, 2)

    return {
        "local": {
            "total_entries": total,
            "with_title": with_title,
            "without_title": total - with_title,
            "top_stations": [
                {"station_id": s, "entries": n, "with_title": wt or 0, "plays": p or 0}
                for s, n, wt, p in top_stations
            ],
            "top_songs": [
                {"title": t, "artist": a, "play_count": pc, "station_id": s}
                for t, a, pc, s in top_songs
            ],
            "match_log": {
                "total": len(match_rows),
                "first_ts": match_rows[0][2] if match_rows else None,
                "last_ts": match_rows[-1][2] if match_rows else None,
                "hits": len(hits),
                "misses": len(misses),
                "hit_rate": (len(hits) / len(match_rows)) if match_rows else None,
                "current_threshold": current_threshold,
                "hit_percentiles": _percentiles(hits),
                "miss_percentiles": _percentiles(misses),
                "histogram": _histogram(hits, misses),
                "separation": _separation_gap(hits, misses),
            },
        },
        "audd": {
            "token_configured": AUDD_API_TOKEN is not None,
            "last_status": get_audd_status(),
            "requests_today": audd_today,
            "requests_last_7_days": audd_week,
            "requests_total": audd_total,
            "counting_since": audd_first_ts,
            "outcomes": {
                "hit": hit_count, "no_match": no_match_count, "quota": quota_count,
                "audd_error": audd_error_count, "network_error": network_error_count,
            },
            "success_rate": success_rate,
            "estimated_cost_usd": estimated_cost_usd,
            "free_quota": _AUDD_FREE_QUOTA,
        },
    }


def on_unknown_fingerprint(db: "SongFingerprintDB", pcm_int16: np.ndarray, fingerprint: list[int],
                            sample_rate: int, station_id: str, cloud_lookup_enabled: bool) -> Optional[dict]:
    """Bei Cache-Miss (Phase 1): identifiziert den Song per AudD, wenn
    sowohl `cloud_lookup_enabled` (song_recognition.cloud_lookup_enabled)
    ALS AUCH AUDD_API_TOKEN gesetzt sind -- fehlt eine der beiden
    Voraussetzungen, unverändertes Phase-1-Verhalten (reines Logging, kein
    Netzwerk-Call). Schreibt Titel/Interpret/Album/Jahr/Länge bei Erfolg
    über SongFingerprintDB.set_cloud_metadata() in die von match_or_learn()
    gerade angelegte Zeile zurück und gibt {"title","artist","album","year",
    "duration_seconds"} zurück (für SongRecognizers aktuellen "jetzt
    läuft"-Zustand, siehe dort) -- sonst None."""
    if not cloud_lookup_enabled or not AUDD_API_TOKEN:
        log.info("🎵 Unbekannter Song auf Sender '%s' (%d Fingerprint-Werte) -- Cloud-Lookup %s.",
                 station_id, len(fingerprint),
                 "deaktiviert (song_recognition.cloud_lookup_enabled=false)" if not cloud_lookup_enabled
                 else "kein AUDD_API_TOKEN gesetzt (.env)")
        return None

    result = audd_lookup(pcm_int16, sample_rate, AUDD_API_TOKEN,
                          station_id=station_id, log_request=db.log_audd_request)
    if result is None:
        log.info("🎵 AudD kennt den Song auf Sender '%s' nicht (oder Anfrage fehlgeschlagen/im Cooldown).",
                 station_id)
        return None

    fingerprint_hash = ",".join(str(v) for v in fingerprint)
    db.set_cloud_metadata(fingerprint_hash, result["title"], result["artist"],
                           result.get("album"), result.get("year"), result.get("duration_seconds"))
    log.info("🎵 AudD-Identifikation auf Sender '%s': '%s' – '%s' (Album: %s, Jahr: %s, Länge: %s)",
             station_id, result["artist"], result["title"],
             result.get("album") or "unbekannt", result.get("year") or "unbekannt",
             result.get("duration_seconds") or "unbekannt")
    return result


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
        # Aktuell erkannter Song für die Live-Anzeige (webui.py /api/status,
        # now_playing_tags im Radio-Zweig) -- {"title","artist"} oder None,
        # solange nichts (lokal oder per Cloud) identifiziert ist. Getrennt
        # von _last_fingerprint: der bleibt auch bei title/artist=NULL
        # gesetzt (reine Songwechsel-Erkennung), _current_song nur bei
        # BEKANNTEM Titel.
        self._current_song: Optional[dict] = None

    def feed(self, pcm_int16: np.ndarray):
        self._ring.append(pcm_int16)

    def get_current_song(self) -> Optional[dict]:
        with self._lock:
            return dict(self._current_song) if self._current_song else None

    def _set_current_song(self, title: Optional[str], artist: Optional[str],
                           album: Optional[str] = None, year: Optional[int] = None,
                           duration_seconds: Optional[int] = None):
        with self._lock:
            self._current_song = (
                {"title": title, "artist": artist, "album": album, "year": year,
                 "duration_seconds": duration_seconds} if title else None
            )

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
            self._current_song = None

    def maybe_recognize_async(self, now: float, station_id: str,
                               interval_seconds: float, similarity_threshold: float,
                               cloud_lookup_enabled: bool = False):
        """`interval_seconds`/`similarity_threshold`/`cloud_lookup_enabled`
        werden bei jedem Tick frisch aus state.song_recognition_cfg
        übergeben (siehe Hauptloop-Muster für
        stt_filter_cfg["sample_interval_seconds"]) -- eine Änderung über
        /config wirkt dadurch ohne Neustart."""
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
                if match is not None:
                    self._set_current_song(match.get("title"), match.get("artist"),
                                            match.get("album"), match.get("year"),
                                            match.get("duration_seconds"))
                else:
                    result = on_unknown_fingerprint(
                        self.db, snapshot, fp, self.sample_rate, station_id, cloud_lookup_enabled
                    )
                    self._set_current_song(
                        result.get("title") if result else None,
                        result.get("artist") if result else None,
                        result.get("album") if result else None,
                        result.get("year") if result else None,
                        result.get("duration_seconds") if result else None,
                    )
            except Exception as e:
                log.warning("⚠ Song-Erkennungs-Sample übersprungen (Fehler: %s)", e)
            finally:
                with self._lock:
                    self._busy = False

        threading.Thread(target=_run, daemon=True, name="song-fp-sample").start()
