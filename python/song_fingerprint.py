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
import json
import logging
import os
import sqlite3
import statistics
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
import xml.etree.ElementTree as ET
from collections import deque
from typing import Callable, Optional

import numpy as np

log = logging.getLogger("song_fingerprint")

FPCALC_BIN = "fpcalc"
FPCALC_TIMEOUT = 15  # Sekunden -- ein hängender fpcalc darf den Analyse-Thread nicht für immer blockieren

# Phase 2: AcoustID-Cloud-Lookup bei Cache-Miss (siehe
# on_unknown_fingerprint() unten). Ersetzt seit 2026-09 das bisherige AudD
# (Kontingent verbraucht, kein Abo mehr) -- AcoustID ist ein dauerhaft
# kostenloses Pendant auf derselben Chromaprint-Basis, siehe
# acoustid_lookup() unten. Key kommt bewusst aus der Umgebung, nicht aus
# settings.json -- das Web-Interface hat keine Auth (siehe CLAUDE.md, "Kein
# Auth, nur hinter VPN"), ein API-Key gehört da nicht rein. Gleiches Muster
# wie ICECAST_SOURCE_PASSWORD: .env -> docker-compose.yml-Passthrough ->
# hier nur gelesen. Einmal beim Modul-Import gelesen (wie TLS_CERT_FILE);
# ein geänderter Key braucht wie andere Umgebungsvariablen einen
# Container-Neustart.
ACOUSTID_API_KEY = os.environ.get("ACOUSTID_API_KEY", "").strip() or None
ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"
ACOUSTID_TIMEOUT = 15  # Sekunden, gleiche Größenordnung wie FPCALC_TIMEOUT

# Cooldown gegen wiederholte Cloud-Requests: ein zu locker/streng
# kalibrierter similarity_threshold (siehe ARCHITECTURE.md/README, noch
# unkalibrierter Platzhalter) könnte match_or_learn() denselben Song
# wiederholt als "neu" einstufen und bei JEDEM Intervall einen Request
# auslösen. AcoustID ist zwar kostenlos, dokumentiert aber ein
# Fair-Use-Limit von 3 Requests/Sekunde (siehe acoustid.org/webservice) --
# unnötige Last auf einen kostenlosen, spendenfinanzierten Dienst bleibt
# trotzdem vermeidenswert. Fester Mindestabstand statt Nutzer-Einstellung,
# gleiche Kategorie wie MAX_OFFSET/MIN_OVERLAP unten -- interne Leitplanke,
# keine Fachentscheidung.
ACOUSTID_MIN_INTERVAL_SECONDS = 60.0
_acoustid_lock = threading.Lock()
_acoustid_last_call_at = 0.0

# AcoustID dokumentiert (Stand acoustid.org/webservice) KEINE Fehlercode-
# Liste wie AudDs #900/#901/#902 -- jede AcoustID-eigene Fehlerantwort
# landet deshalb als generischer "acoustid_error" mit sichtbarem Code/
# Message statt geraten zu werden. Die einzige dokumentierte Einschränkung
# (das Rate-Limit oben) wird separat über den HTTP-Status erkannt, siehe
# acoustid_lookup().
_ACOUSTID_RATE_LIMIT_HTTP_STATUS = 429

# Letzter AcoustID-Aufrufstatus für die Live-Anzeige (webui.py,
# now_playing_tags im "pending"-Fall) -- siehe
# get_acoustid_status()/_record_acoustid_status(). Reines In-Memory-Live-
# Signal wie _acoustid_last_call_at, bewusst NICHT in settings.json
# persistiert: ein Neustart braucht dafür keine besondere Behandlung, der
# nächste tatsächliche AcoustID-Aufruf (spätestens nach
# ACOUSTID_MIN_INTERVAL_SECONDS) setzt den Status ohnehin sofort neu.
_acoustid_last_status: dict = {"state": None, "error_code": None, "error_message": None, "checked_at": None}


def _record_acoustid_status(state: str, error_code: Optional[int] = None, error_message: Optional[str] = None):
    with _acoustid_lock:
        _acoustid_last_status.update({
            "state": state, "error_code": error_code,
            "error_message": error_message, "checked_at": time.time(),
        })


def get_acoustid_status() -> Optional[dict]:
    """Letzter AcoustID-Aufrufstatus ({"state", "error_code", "error_message",
    "checked_at"}) -- state ist "ok"/"rate_limited"/"network_error"/
    "acoustid_error", oder None, solange noch kein AcoustID-Aufruf versucht
    wurde (z.B. Cloud-Lookup deaktiviert, oder seit dem Start noch kein
    Cache-Miss). Wird NUR von acoustid_lookup() gesetzt, nicht vom
    "acoustid_lookup_enabled=false"-Fall in on_unknown_fingerprint() --
    sonst würde die Live-Anzeige für jeden, der Cloud-Lookup bewusst aus
    gelassen hat, dauerhaft einen "nicht konfiguriert"-Hinweis zeigen statt
    des neutralen Pending-Texts."""
    with _acoustid_lock:
        return dict(_acoustid_last_status) if _acoustid_last_status["state"] is not None else None


# Hörer-Gate (Nutzer-Wunsch, siehe SESSION.md): Song-Erkennung -- lokales
# Fingerprinting UND Cloud-Lookup -- kostet CPU bzw. unnötige AcoustID-
# Anfragen, ist aber wertlos, solange niemand den Restream hört (die Live-Anzeige, für die
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
    potenziell ein sinnloser/falscher Fingerprint samt unnötigem AcoustID-Call.
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


def _compute_acoustid_submission(pcm_int16: np.ndarray, sample_rate: int) -> Optional[tuple]:
    """Wie compute_fingerprint(), aber OHNE `-raw` -- AcoustID erwartet den
    komprimierten Base64-Fingerprint (fpcalcs Standardausgabe), nicht die
    rohen Integer-Werte, die der lokale Sliding-Offset-Vergleich in
    similarity() braucht (siehe Moduldocstring/ARCHITECTURE.md, Abschnitt
    "Song-Erkennung": zwei unterschiedliche Verwendungszwecke desselben
    Ausgangssignals, keine Möglichkeit, das eine aus dem anderen ohne die
    Chromaprint-Kompressionslogik selbst zurückzurechnen). Deshalb ein
    zweiter, separater fpcalc-Aufruf auf einer eigenen Temp-WAV -- nur im
    Cache-Miss-Pfad (on_unknown_fingerprint()), kein Einfluss auf den
    Hot-Path (jedes Fenster während laufender Musik). Gibt
    (fingerprint_base64, duration_seconds) oder None zurück."""
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
            [FPCALC_BIN, "-json", tmp_path],
            capture_output=True, text=True, timeout=FPCALC_TIMEOUT,
        )
        if result.returncode != 0:
            log.warning("⚠ fpcalc (AcoustID-Format) lieferte Exit-Code %d: %s",
                        result.returncode, result.stderr.strip())
            return None
        data = json.loads(result.stdout)
        fp, duration = data.get("fingerprint"), data.get("duration")
        if not fp or not duration:
            return None
        return fp, int(round(duration))
    except Exception as e:
        # Gleiches breites Fangen wie compute_fingerprint() -- ein
        # fpcalc-Absturz/Timeout/kaputtes JSON darf nie den Analyse-Thread
        # mitreißen.
        log.warning("⚠ AcoustID-Fingerprint-Berechnung fehlgeschlagen: %s", e)
        return None
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _parse_acoustid_result(result: dict) -> Optional[dict]:
    """Ein Element aus AcoustID-`results[]` (bereits nach höchstem `score`
    ausgewählt, siehe acoustid_lookup()) -> {"title","artist","album","year",
    "duration_seconds"}, oder None, falls nicht mal Titel+Interpret
    ermittelbar sind. Struktur laut AcoustID-Doku bei
    `meta=recordings+releasegroups+releases`: `result["recordings"][0]`
    enthält title/duration/artists[], darunter
    releasegroups[0].releases[0].date fürs Jahr -- alles best-effort mit
    .get()/try, ein von der Doku abweichender Verschachtelungsgrad darf hier
    nie eine Exception werfen (gleiches defensives Muster wie der Rest
    dieses Moduls), sondern liefert nur weniger Metadaten."""
    recordings = result.get("recordings") or []
    if not recordings:
        return None
    recording = recordings[0]
    title = recording.get("title")
    artists = recording.get("artists") or []
    artist = artists[0].get("name") if artists else None
    if not title or not artist:
        return None

    album, year = None, None
    try:
        releasegroups = recording.get("releasegroups") or []
        if releasegroups:
            album = releasegroups[0].get("title")
            releases = releasegroups[0].get("releases") or []
            if releases:
                year = (releases[0].get("date") or {}).get("year")
    except (AttributeError, IndexError, TypeError):
        pass  # unerwartete Antwortstruktur -- Album/Jahr bleiben None, kein Absturz

    duration = recording.get("duration")
    return {
        "title": title, "artist": artist, "album": album, "year": year,
        "duration_seconds": int(duration) if duration else None,
    }


def acoustid_lookup(pcm_int16: np.ndarray, sample_rate: int, api_key: str,
                     station_id: str = None,
                     log_request: Optional[Callable[[str, str, str], None]] = None) -> Optional[dict]:
    """Identifiziert `pcm_int16` per AcoustID (https://acoustid.org) und
    gibt bei Erfolg {"title", "artist", "album", "year", "duration_seconds"}
    zurück (album/year können None sein, falls AcoustID sie nicht
    mitliefert), sonst None -- sowohl bei "AcoustID kennt den Song nicht"
    als auch bei jedem Netzwerk-/Timeout-/Parse-Fehler (gleiches defensives
    Muster wie compute_fingerprint(): ein Cloud-Lookup darf den
    Analyse-Thread nie mitreißen). Respektiert ACOUSTID_MIN_INTERVAL_SECONDS
    als Cooldown (siehe Modul-Kommentar oben) -- bei aktivem Cooldown wird
    gar nicht erst eine Verbindung aufgebaut (und get_acoustid_status()
    unverändert gelassen, siehe dort).

    Anders als das frühere AudD (Datei-Upload) schickt AcoustID nur den
    bereits lokal berechneten Chromaprint-Fingerprint als Formularfeld --
    braucht dafür aber die komprimierte Base64-Variante statt der rohen
    Integer-Sequenz aus compute_fingerprint(), siehe
    _compute_acoustid_submission().

    Setzt bei jedem tatsächlich versuchten Aufruf den Live-Status für
    get_acoustid_status(): "ok" bei einer normalen Antwort (auch wenn der
    Song darin nicht erkannt wurde -- das ist kein AcoustID-Fehler),
    "rate_limited" bei HTTP 429 (siehe ACOUSTID_MIN_INTERVAL_SECONDS-
    Kommentar oben), "acoustid_error" bei jeder anderen AcoustID-eigenen
    Fehlerantwort (Code sichtbar), "network_error" bei sonstigen
    Verbindungs-/Timeout-/Parse-Problemen (inkl. gescheiterter lokaler
    Fingerprint-Berechnung für die Submission).

    `log_request(station_id, source, outcome)` (optional, Callback statt
    direktem SongFingerprintDB-Import -- gleiches Muster wie
    update_check.UpdateChecker.on_result, hält diese Funktion isoliert
    testbar) wird bei GENAU demselben tatsächlich versuchten Aufruf
    zusätzlich zu get_acoustid_status() aufgerufen, mit `source="acoustid"`
    fest (siehe SongFingerprintDB.log_cloud_request() -- die Spalte
    unterscheidet das von historischen, vor der Umstellung geschriebenen
    "audd_legacy"-Zeilen) und feinerem `outcome`: "hit"/"no_match" statt
    beide unter "ok". Ein Cooldown-Skip ruft `log_request` NICHT auf -- das
    war kein tatsächlicher AcoustID-Request, würde die Request-Zählung
    sonst künstlich aufblähen."""
    global _acoustid_last_call_at
    with _acoustid_lock:
        now = time.time()
        if now - _acoustid_last_call_at < ACOUSTID_MIN_INTERVAL_SECONDS:
            log.info("🎵 AcoustID-Cooldown aktiv (< %.0fs seit letztem Call) -- "
                      "Anfrage übersprungen.", ACOUSTID_MIN_INTERVAL_SECONDS)
            return None
        _acoustid_last_call_at = now

    submission = _compute_acoustid_submission(pcm_int16, sample_rate)
    if submission is None:
        _record_acoustid_status("network_error", error_message="Fingerprint-Berechnung für AcoustID fehlgeschlagen")
        if log_request:
            log_request(station_id, "acoustid", "network_error")
        return None
    fingerprint_b64, duration = submission

    body = urllib.parse.urlencode({
        "client": api_key, "fingerprint": fingerprint_b64, "duration": duration,
        "meta": "recordings+releasegroups+releases", "format": "json",
    }).encode()
    req = urllib.request.Request(ACOUSTID_URL, data=body, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=ACOUSTID_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == _ACOUSTID_RATE_LIMIT_HTTP_STATUS:
            log.warning("⚠ AcoustID-Rate-Limit erreicht (HTTP %d).", e.code)
            _record_acoustid_status("rate_limited")
            if log_request:
                log_request(station_id, "acoustid", "rate_limited")
        else:
            log.warning("⚠ AcoustID-Lookup fehlgeschlagen (HTTP %s): %s", e.code, e)
            _record_acoustid_status("network_error", error_message=str(e))
            if log_request:
                log_request(station_id, "acoustid", "network_error")
        return None
    except Exception as e:
        # Breiter Fang wie bei compute_fingerprint() -- ein Cloud-Lookup
        # (Netzwerk, Timeout, kaputtes JSON, unerwartete Antwortstruktur)
        # darf den Analyse-Thread nie mitreißen, egal welcher Fehler genau
        # auftritt.
        log.warning("⚠ AcoustID-Lookup fehlgeschlagen (Netzwerk/Zeitüberschreitung/Parsing): %s", e)
        _record_acoustid_status("network_error", error_message=str(e))
        if log_request:
            log_request(station_id, "acoustid", "network_error")
        return None

    if data.get("status") != "ok":
        # AcoustID dokumentiert kein festes Fehlercode-Schema (anders als
        # AudDs #900/#901) -- Code/Message landen deshalb ungefiltert als
        # generischer "acoustid_error" in get_acoustid_status(), siehe
        # Modul-Kommentar oben zu _ACOUSTID_RATE_LIMIT_HTTP_STATUS.
        err = data.get("error") or {}
        code, msg = err.get("code"), err.get("message")
        log.warning("⚠ AcoustID meldet einen API-Fehler (Code %s): %s", code, msg)
        _record_acoustid_status("acoustid_error", error_code=code, error_message=msg)
        if log_request:
            log_request(station_id, "acoustid", "acoustid_error")
        return None

    _record_acoustid_status("ok")
    results = sorted(data.get("results") or [], key=lambda r: r.get("score") or 0, reverse=True)
    parsed = None
    for r in results:
        parsed = _parse_acoustid_result(r)
        if parsed:
            break
    if parsed is None:
        if log_request:
            log_request(station_id, "acoustid", "no_match")
        return None  # AcoustID hat den Song nicht erkannt -- kein Fehler
    if log_request:
        log_request(station_id, "acoustid", "hit")
    return parsed


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


# Kuratierte Erstbefüllung für is_german_language (siehe README.md/
# ARCHITECTURE.md, "Deutschsprachige Musik ausblenden") -- bewusst eine
# kurze Handliste bekannter deutschsprachiger Interpreten statt einer
# externen Datenquelle (kein zusätzlicher Netzwerk-Call/Cache-Risiko,
# gleiches Muster wie die Genre-Teilstring-Filter in music_query.py).
# Klein gehalten und NICHT als Anspruch auf Vollständigkeit gedacht -- die
# eigentliche, verlässliche Klassifizierung passiert über den Zeitverlauf
# per manuellem "Deutsch!"-Button (set_language()), das hier ist nur ein
# Kaltstart-Vorteil, damit der Skip-Filter nicht bei leerer DB komplett
# wirkungslos ist. Klein geschrieben, Substring-Vergleich gegen den
# (klein geschriebenen) Interpreten-Namen.
_GERMAN_ARTIST_HINTS = frozenset({
    "herbert grönemeyer", "grönemeyer", "peter fox", "seeed", "wir sind helden",
    "die ärzte", "die toten hosen", "tote hosen", "silbermond", "juli",
    "ich + ich", "ich und ich", "andreas bourani", "mark forster", "revolverheld",
    "rosenstolz", "unheilig", "in extremo", "rammstein", "oomph!",
    "eisbrecher", "megaherz", "andrea berg", "helene fischer", "roland kaiser",
    "howard carpendale", "matthias reim", "wolfgang petry", "udo lindenberg",
    "peter maffay", "cro", "sido", "bushido", "kollegah",
    "capital bra", "apache 207", "shirin david", "casper", "clueso",
    "max herre", "kraftklub", "beginner", "fettes brot", "deichkind",
    "fanta 4", "die fantastischen vier", "nena", "extrabreit", "trio",
    "spider murphy gang", "ideal", "element of crime", "sportfreunde stiller",
    "the bosshoss", "philipp poisel", "max giesinger", "namika", "lea",
    "loredana", "sarah connor", "xavier naidoo", "adel tawil", "glasperlenspiel",
    "madsen", "kettcar", "tocotronic", "einstürzende neubauten", "nina hagen",
    "falco", "dj ötzi", "voxxclub", "vanessa mai", "beatrice egli",
})


def guess_is_german(artist: Optional[str]) -> Optional[bool]:
    """Substring-Abgleich gegen _GERMAN_ARTIST_HINTS. Liefert NUR True
    (positive Bestätigung) oder None (unbekannt) -- absichtlich NIE False:
    ein fehlender Listentreffer heißt nicht "garantiert nicht deutsch",
    das würde bei der (kurzen, nicht vollständigen) Liste sonst
    reihenweise falsch-negative is_german_language=0 erzeugen. Eine echte
    Verneinung bleibt der manuellen Klassifizierung (set_language()) oder
    einem künftigen Genre-Signal aus der Cloud-Anbindung vorbehalten."""
    if not artist:
        return None
    lowered = artist.lower()
    return True if any(hint in lowered for hint in _GERMAN_ARTIST_HINTS) else None


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
            # "Deutschsprachige Musik ausblenden" (siehe README.md/
            # ARCHITECTURE.md): NULL = unklassifiziert (Default für jede
            # bestehende Zeile nach dieser Migration), 0/1 = bewusst
            # klassifiziert -- entweder per guess_is_german() (kuratierte
            # Interpreten-Liste, siehe unten) oder manuell per "Deutsch!"-
            # Button (set_language()). Bewusst KEIN BOOLEAN-Typ -- SQLite
            # kennt den ohnehin nicht, INTEGER mit expliziter NULL-Semantik
            # ist hier zusätzlich das einzige, was "unklassifiziert" von
            # "geprüft und nicht deutsch" unterscheiden kann.
            if "is_german_language" not in columns:
                conn.execute("ALTER TABLE song_fingerprints ADD COLUMN is_german_language INTEGER")
                log.info("🎵 Song-Fingerprint-DB-Schema migriert: Spalte 'is_german_language' ergänzt.")
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
            # Cloud-Request-Log (Nutzer-Wunsch, Statistik-Sektion Config-
            # Seite, siehe SESSION.md): EIN Zeile pro tatsächlich
            # versuchtem Cloud-Lookup-Aufruf (Cooldown-Skips zählen NICHT
            # mit, siehe dort) -- ohne das gab es für "Requests heute/diese
            # Woche/gesamt" und eine echte Erfolgsquote keine Datenquelle,
            # nur unzuverlässige, schnell rotierende Logfile-Zeilen
            # (DEBUG-Level, ~1-2 Tage Retention). Gleiches Muster wie
            # song_match_log oben: rein additiv, kein Einfluss auf
            # Matching/Cloud-Lookup selbst.
            #
            # Migration von der AudD-Ära (Tabelle hieß damals
            # audd_request_log, ausschließlich AudD-Requests) auf AcoustID:
            # bestehende Zeilen bleiben erhalten (keine Historie löschen),
            # bekommen aber rückwirkend eine neue Spalte `source =
            # 'audd_legacy'` -- ab jetzt schreibt nur noch acoustid_lookup()
            # mit source='acoustid' hier hinein. Prüfung per sqlite_master
            # (SQLite kennt kein "ALTER TABLE ... RENAME TO ... IF NOT
            # EXISTS"), Migration läuft dadurch nur einmal.
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "audd_request_log" in tables and "song_cloud_request_log" not in tables:
                conn.execute("ALTER TABLE audd_request_log RENAME TO song_cloud_request_log")
                conn.execute("ALTER TABLE song_cloud_request_log ADD COLUMN source TEXT")
                conn.execute("UPDATE song_cloud_request_log SET source = 'audd_legacy' WHERE source IS NULL")
                log.info("🎵 Song-Fingerprint-DB-Schema migriert: audd_request_log -> "
                         "song_cloud_request_log (bestehende Zeilen als 'audd_legacy' markiert).")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS song_cloud_request_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    station_id TEXT,
                    source TEXT NOT NULL DEFAULT 'acoustid',
                    outcome TEXT NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def match_or_learn(self, fingerprint: list[int], station_id: str,
                        similarity_threshold: float) -> dict:
        """Vergleicht `fingerprint` per Brute-Force gegen alle gecachten
        Songs (siehe ARCHITECTURE.md, "Offene Punkte" zur Skalierungsgrenze
        dieses Ansatzes). `similarity_threshold` wird bei jedem Aufruf frisch
        übergeben (nicht am Objekt fixiert), damit eine Änderung über
        /config wie bei stt_filter.confidence_threshold ohne Neustart wirkt.
        Bei Treffer: play_count/last_seen/station_id aktualisieren,
        Match-Info zurückgeben (`new`: False). Bei keinem Treffer: neuen
        Eintrag anlegen (title/artist NULL -- Phase 2 füllt sie später über
        den Cloud-Lookup) und dessen frisch vergebene `song_id` zurückgeben
        (`new`: True) -- seit der "Deutsch!"-Button-Erweiterung (siehe
        README.md) IMMER ein dict statt None bei Cache-Miss, damit der
        Aufrufer auch einen noch unidentifizierten Song schon anhand seiner
        `song_id` manuell klassifizieren kann, bevor Titel/Interpret
        überhaupt bekannt sind.

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
                "SELECT id, fingerprint_hash, title, artist, play_count, album, year, "
                "duration_seconds, is_german_language FROM song_fingerprints"
            ).fetchall()

            best_id, best_score, best_row = None, 0.0, None
            for row_id, fp_text, title, artist, play_count, album, year, duration_seconds, is_german_language in rows:
                candidate = [int(v) for v in fp_text.split(",")] if fp_text else []
                score = similarity(fingerprint, candidate)
                if score > best_score:
                    best_id, best_score, best_row = row_id, score, (
                        title, artist, play_count, album, year, duration_seconds, is_german_language)

            now = time.strftime("%Y-%m-%d %H:%M:%S")
            if best_id is not None and best_score >= similarity_threshold:
                title, artist, play_count, album, year, duration_seconds, is_german_language = best_row
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
                        "play_count": play_count + 1, "similarity": best_score,
                        "is_german": bool(is_german_language) if is_german_language is not None else None,
                        "new": False}

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
            new_id = c.lastrowid
            conn.commit()
            log.debug("[song_fingerprint] neuer Song gelernt (Song #%d, bester Kandidat hatte nur "
                      "Ähnlichkeit %.2f, Schwelle %.2f)", new_id, best_score, similarity_threshold)
            return {"song_id": new_id, "title": None, "artist": None, "album": None, "year": None,
                    "duration_seconds": None, "play_count": 1, "similarity": best_score,
                    "is_german": None, "new": True}
        finally:
            conn.close()

    def set_cloud_metadata(self, fingerprint_hash: str, title: str, artist: str,
                            album: Optional[str] = None, year: Optional[int] = None,
                            duration_seconds: Optional[int] = None):
        """Trägt Titel/Interpret (+ optional Album/Jahr/Länge, siehe
        acoustid_lookup()) aus einem erfolgreichen Cloud-Lookup (Phase 2,
        aktuell AcoustID, siehe on_unknown_fingerprint() unten) in die Zeile
        nach, die
        match_or_learn() beim Cache-Miss mit title/artist=NULL angelegt hat
        -- Zuordnung über denselben fingerprint_hash-Text, den
        match_or_learn() dafür schreibt. Eigene kurzlebige Connection,
        gleiches Muster wie delete_fingerprint()/clear_all() unten -- diese
        Methode läuft wie set_cloud_metadata()s Aufrufer im Hintergrund-
        Thread von SongRecognizer, nicht im Hauptloop.

        Wendet zusätzlich guess_is_german() auf den jetzt bekannten
        `artist` an (kuratierte Erstbefüllung, siehe README.md/
        ARCHITECTURE.md, "Deutschsprachige Musik ausblenden") -- per
        COALESCE aber NUR, falls is_german_language noch NULL ist. Ein
        manueller "Deutsch!"-Klick (set_language()) kann diese Zeile
        theoretisch schon VOR der AcoustID-Antwort erreicht haben (Nutzer
        reagiert schneller als der Cloud-Lookup) und darf dadurch nicht
        wieder überschrieben werden."""
        is_german_guess = guess_is_german(artist)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE song_fingerprints SET title = ?, artist = ?, album = ?, year = ?, "
                "duration_seconds = ?, "
                "is_german_language = COALESCE(is_german_language, ?) "
                "WHERE fingerprint_hash = ?",
                (title, artist, album, year, duration_seconds,
                 1 if is_german_guess else None,
                 fingerprint_hash),
            )
            conn.commit()
        finally:
            conn.close()

    def set_language(self, song_id: int, is_german: bool) -> bool:
        """Manuelles Anlernen per "Deutsch!"-Button (bzw. dessen Gegenstück
        auf der Config-Seite, siehe README.md) -- gewinnt immer gegen
        guess_is_german(), da diese Methode direkt (nicht per COALESCE)
        schreibt. Eigene kurzlebige Connection, gleiches Muster wie
        delete_fingerprint()/clear_all() unten. Gibt zurück, ob eine Zeile
        mit dieser `song_id` existierte."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "UPDATE song_fingerprints SET is_german_language = ? WHERE id = ?",
                (1 if is_german else 0, song_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def log_cloud_request(self, station_id: str, source: str, outcome: str):
        """Protokolliert EINEN tatsächlich versuchten Cloud-Lookup-Aufruf
        (aktuell nur acoustid_lookup(), das immer `source="acoustid"`
        übergibt) in `song_cloud_request_log` -- `outcome` ist eines von
        "hit"/"no_match"/"rate_limited"/"acoustid_error"/"network_error"
        (siehe acoustid_lookup()). Wird von dort per `log_request`-Callback
        aufgerufen, nicht direkt importiert -- gleiches Callback-Muster wie
        update_check.UpdateChecker.on_result, damit acoustid_lookup() selbst
        von SongFingerprintDB/SQLite unwissend und leicht isoliert testbar
        bleibt. Historische, vor der Umstellung von AudD auf AcoustID
        geschriebene Zeilen tragen `source='audd_legacy'` (einmalige
        Migration, siehe _init_schema()) und werden hier nie mehr erzeugt."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO song_cloud_request_log (ts, station_id, source, outcome) VALUES (?, ?, ?, ?)",
                (time.strftime("%Y-%m-%d %H:%M:%S"), station_id, source, outcome),
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


def build_recognition_stats(db_path: str, current_threshold: float) -> dict:
    """Aggregiert Kennzahlen aus song_fingerprints/song_match_log/
    song_cloud_request_log für die Config-Seite ("🎵 Song-Erkennung –
    Statistik", Nutzer-Wunsch, siehe SESSION.md) -- reiner Lesezugriff,
    alles per SQL on-demand berechnet, kein Caching nötig (bei den hier
    üblichen Zeilenzahlen -- Größenordnung tausend -- für SQLite trivial
    schnell). Portiert die Percentil-/Histogramm-/Lücken-Logik aus
    check_song_calibration.py (siehe _percentiles()/_histogram()/
    _separation_gap() oben).

    Die AcoustID-"Requests"-Werte zählen NUR Zeilen mit `source='acoustid'`
    (siehe song_cloud_request_log-Migration in _init_schema()) -- die
    historischen `source='audd_legacy'`-Zeilen aus der AudD-Ära fließen
    NICHT in Erfolgsquote/Requests-Zählung ein (anderer Anbieter, andere
    Fehlersemantik), tauchen aber separat als reiner Transparenz-Zähler auf.
    Anders als bei AudD gibt es hier kein Kostenmodell -- AcoustID ist
    dauerhaft kostenlos, nur durch ein Fair-Use-Rate-Limit begrenzt (siehe
    song_fingerprint.py-Modulkommentar)."""
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
            "SELECT id, title, artist, play_count, station_id, is_german_language "
            "FROM song_fingerprints WHERE title IS NOT NULL ORDER BY play_count DESC LIMIT 10"
        ).fetchall()

        match_rows = c.execute(
            "SELECT similarity, is_hit, ts FROM song_match_log ORDER BY ts"
        ).fetchall()

        today_start = time.strftime("%Y-%m-%d 00:00:00")
        week_start = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7 * 86400))
        acoustid_total = c.execute(
            "SELECT COUNT(*) FROM song_cloud_request_log WHERE source = 'acoustid'"
        ).fetchone()[0]
        acoustid_today = c.execute(
            "SELECT COUNT(*) FROM song_cloud_request_log WHERE source = 'acoustid' AND ts >= ?", (today_start,)
        ).fetchone()[0]
        acoustid_week = c.execute(
            "SELECT COUNT(*) FROM song_cloud_request_log WHERE source = 'acoustid' AND ts >= ?", (week_start,)
        ).fetchone()[0]
        outcome_counts = dict(c.execute(
            "SELECT outcome, COUNT(*) FROM song_cloud_request_log WHERE source = 'acoustid' GROUP BY outcome"
        ).fetchall())
        acoustid_first_ts = c.execute(
            "SELECT MIN(ts) FROM song_cloud_request_log WHERE source = 'acoustid'"
        ).fetchone()[0]
        # Rein informativ (siehe Docstring oben): wie viele Zeilen noch aus
        # der AudD-Ära stammen -- fließt in keine der Zahlen oben ein.
        legacy_audd_total = c.execute(
            "SELECT COUNT(*) FROM song_cloud_request_log WHERE source = 'audd_legacy'"
        ).fetchone()[0]
    finally:
        conn.close()

    hits = [r[0] for r in match_rows if r[1]]
    misses = [r[0] for r in match_rows if not r[1]]

    hit_count = outcome_counts.get("hit", 0)
    no_match_count = outcome_counts.get("no_match", 0)
    rate_limited_count = outcome_counts.get("rate_limited", 0)
    acoustid_error_count = outcome_counts.get("acoustid_error", 0)
    network_error_count = outcome_counts.get("network_error", 0)
    # Erfolgsquote NUR über tatsächlich abgeschlossene Erkennungsversuche
    # (hit/no_match) -- Requests, die an einem AcoustID-Fehler scheiterten,
    # sagen nichts über "erkennt AcoustID den Song" aus, würden die Quote
    # sonst künstlich verwässern.
    completed = hit_count + no_match_count
    success_rate = (hit_count / completed) if completed else None

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
                {"song_id": i, "title": t, "artist": a, "play_count": pc, "station_id": s,
                 "is_german": bool(g) if g is not None else None}
                for i, t, a, pc, s, g in top_songs
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
        "acoustid": {
            "key_configured": ACOUSTID_API_KEY is not None,
            "last_status": get_acoustid_status(),
            "requests_today": acoustid_today,
            "requests_last_7_days": acoustid_week,
            "requests_total": acoustid_total,
            "counting_since": acoustid_first_ts,
            "outcomes": {
                "hit": hit_count, "no_match": no_match_count, "rate_limited": rate_limited_count,
                "acoustid_error": acoustid_error_count, "network_error": network_error_count,
            },
            "success_rate": success_rate,
            "legacy_audd_requests_total": legacy_audd_total,
        },
    }


def on_unknown_fingerprint(db: "SongFingerprintDB", pcm_int16: np.ndarray, fingerprint: list[int],
                            sample_rate: int, station_id: str, cloud_lookup_enabled: bool) -> Optional[dict]:
    """Bei Cache-Miss (Phase 1): identifiziert den Song per AcoustID, wenn
    sowohl `cloud_lookup_enabled` (song_recognition.acoustid_lookup_enabled)
    ALS AUCH ACOUSTID_API_KEY gesetzt sind -- fehlt eine der beiden
    Voraussetzungen, unverändertes Phase-1-Verhalten (reines Logging, kein
    Netzwerk-Call). Schreibt Titel/Interpret/Album/Jahr/Länge bei Erfolg
    über SongFingerprintDB.set_cloud_metadata() in die von match_or_learn()
    gerade angelegte Zeile zurück und gibt {"title","artist","album","year",
    "duration_seconds"} zurück (für SongRecognizers aktuellen "jetzt
    läuft"-Zustand, siehe dort) -- sonst None."""
    if not cloud_lookup_enabled or not ACOUSTID_API_KEY:
        log.info("🎵 Unbekannter Song auf Sender '%s' (%d Fingerprint-Werte) -- Cloud-Lookup %s.",
                 station_id, len(fingerprint),
                 "deaktiviert (song_recognition.acoustid_lookup_enabled=false)" if not cloud_lookup_enabled
                 else "kein ACOUSTID_API_KEY gesetzt (.env)")
        return None

    result = acoustid_lookup(pcm_int16, sample_rate, ACOUSTID_API_KEY,
                              station_id=station_id, log_request=db.log_cloud_request)
    if result is None:
        log.info("🎵 AcoustID kennt den Song auf Sender '%s' nicht (oder Anfrage fehlgeschlagen/im Cooldown).",
                 station_id)
        return None

    fingerprint_hash = ",".join(str(v) for v in fingerprint)
    db.set_cloud_metadata(fingerprint_hash, result["title"], result["artist"],
                           result.get("album"), result.get("year"), result.get("duration_seconds"))
    log.info("🎵 AcoustID-Identifikation auf Sender '%s': '%s' – '%s' (Album: %s, Jahr: %s, Länge: %s)",
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
        # bekannter `song_id` (seit der "Deutsch!"-Button-Erweiterung --
        # vorher nur bei bekanntem Titel, siehe SESSION.md: der Button
        # muss aber schon VOR einer AcoustID-Identifikation bedienbar sein).
        self._current_song: Optional[dict] = None

    def feed(self, pcm_int16: np.ndarray):
        self._ring.append(pcm_int16)

    def get_current_song(self) -> Optional[dict]:
        with self._lock:
            return dict(self._current_song) if self._current_song else None

    def _set_current_song(self, song_id: Optional[int], title: Optional[str],
                           artist: Optional[str], album: Optional[str] = None,
                           year: Optional[int] = None, duration_seconds: Optional[int] = None,
                           is_german: Optional[bool] = None):
        with self._lock:
            self._current_song = (
                {"song_id": song_id, "title": title, "artist": artist, "album": album,
                 "year": year, "duration_seconds": duration_seconds, "is_german": is_german}
                if song_id is not None else None
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
                if not match["new"]:
                    self._set_current_song(match["song_id"], match.get("title"), match.get("artist"),
                                            match.get("album"), match.get("year"),
                                            match.get("duration_seconds"), match.get("is_german"))
                else:
                    result = on_unknown_fingerprint(
                        self.db, snapshot, fp, self.sample_rate, station_id, cloud_lookup_enabled
                    )
                    # guess_is_german() lief bei einem AcoustID-Treffer schon in
                    # set_cloud_metadata() gegen die DB-Zeile -- hier
                    # zusätzlich direkt auf `result` angewendet, damit der
                    # laufende "jetzt läuft"-Zustand (_current_song) nicht
                    # erst auf den nächsten match_or_learn()-Durchlauf
                    # warten muss, um is_german zu zeigen/zu skippen.
                    self._set_current_song(
                        match["song_id"],
                        result.get("title") if result else None,
                        result.get("artist") if result else None,
                        result.get("album") if result else None,
                        result.get("year") if result else None,
                        result.get("duration_seconds") if result else None,
                        guess_is_german(result.get("artist")) if result else None,
                    )
            except Exception as e:
                log.warning("⚠ Song-Erkennungs-Sample übersprungen (Fehler: %s)", e)
            finally:
                with self._lock:
                    self._busy = False

        threading.Thread(target=_run, daemon=True, name="song-fp-sample").start()
