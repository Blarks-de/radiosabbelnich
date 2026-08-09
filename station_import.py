#!/usr/bin/env python3
"""
station_import.py — Sender-Import aus einer M3U-Playlist (z.B. die
Kodinerds-Kodi-Radioliste, Default-URL siehe settings_store.DEFAULTS).
Lädt die Liste, prüft jeden Sender auf dauerhaften Audiofluss
(parallelisiert, siehe unten), und übernimmt nur funktionierende, noch
nicht vorhandene Sender in stations.json (Kategorie
stations_store.IMPORT_CATEGORY) — dort DEAKTIVIERT, damit ein Import
nicht ungefragt hunderte fremde Sender in die laufende Rotation kippt.

Erreichbarkeits-Check: ffmpeg dekodiert den Stream über ein Zeitfenster
und es wird geprüft, ob am ENDE dieses Fensters noch Audio ankommt —
nicht bloß, ob überhaupt welches kam.

Warum so aufwendig, warum kein HTTP HEAD/GET und kein ffprobe:

- HTTP HEAD/GET sagt gar nichts. Viele Icecast/Shoutcast-Server können
  HEAD nicht richtig oder liefern 200 ohne echten Audio-Inhalt; manche
  Playlist-Einträge zeigen wieder auf verschachtelte M3U/PLS-Dateien,
  die unser Player gar nicht direkt abspielen kann.
- ffprobe (die frühere Lösung) prüft nur, ob am Anfang ein Audio-Stream
  erkennbar ist. Das reicht nachweislich nicht: die BBC-Radio-Scotland-
  DASH-URL aus der Kodinerds-Liste wurde so als "erreichbar" übernommen,
  landete in der Rotation und legte den Player für 8,5 Stunden lahm.
  ffmpeg holt dort beim Verbinden den vorhandenen Fragment-Pool auf
  einen Schlag (gemessen: 12s Audio in 0,4s Wall-Clock, insgesamt ~38s)
  und bekommt danach auf JEDES weitere Fragment ein HTTP 404
  ("Failed to open fragment of playlist") — der Prozess lebt weiter,
  liefert aber nie wieder ein Sample. Auch ein "dekodiere mal 3
  Sekunden"-Check läuft da glatt durch, weil die ersten Sekunden ja
  tadellos kommen.

Deshalb: CHECK_WINDOW Sekunden lang echt mitlesen (Wall-Clock, nicht
Audio-Zeit) und verlangen, dass in den letzten CHECK_TAIL Sekunden noch
Daten eintrudeln. Ein gesunder Live-Stream liefert nach dem anfänglichen
Burst dauerhaft ungefähr in Echtzeit weiter (gemessen SWR3: 12s Audio in
7,4s) und besteht das mühelos; eine Quelle, die nur einen Vorrat
ausschüttet und dann verstummt, fällt durch. Das ist exakt die Bedingung,
an der auch der Hauptloop hängt (StreamSource.read_window in
radiosabbelnich.py liest fortlaufend genau solche Fenster).
"""

import logging
import os
import re
import select
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import stations_store

log = logging.getLogger("import")

CHECK_WINDOW = 8.0       # Sekunden Wall-Clock, die pro Sender mitgelesen werden
CHECK_TAIL = 3.0         # in den letzten so vielen Sekunden muss noch Audio kommen
CHECK_MIN_SECONDS = 3.0  # so viel Audio muss insgesamt mindestens rauskommen
CHECK_SAMPLE_RATE = 8000 # Prüf-Samplerate (Mono); nur zum Mitzählen, keine Hifi-Frage
CHECK_CONCURRENCY = 10   # max. gleichzeitige Checks
FETCH_TIMEOUT = 15.0     # Sekunden zum Laden der M3U-Datei

USER_AGENT = "RadioSabbelNich/1.0"

_EXTINF_RE = re.compile(r"^#EXTINF:[^,]*,(.*)$")


def fetch_m3u(url: str, timeout: float = FETCH_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "RadioSabbelNich/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def parse_m3u(text: str) -> list:
    """Parst #EXTINF-Zeilen (Sendername nach dem letzten Komma) plus die
    folgende Stream-URL-Zeile. Ignoriert sonstige Kommentare/Attribute
    und Leerzeilen dazwischen. Erwartet Extended-M3U
    (#EXTM3U/#EXTINF:-1 attr="...",Name), das Standardformat der
    Kodinerds-Liste und der meisten IPTV/Radio-Playlists."""
    entries = []
    pending_name = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            m = _EXTINF_RE.match(line)
            pending_name = m.group(1).strip() if m else None
        elif line.startswith("#"):
            continue
        else:
            if pending_name:
                entries.append({"name": pending_name, "url": line})
            pending_name = None
    return entries


def check_reachable(url: str, window: float = CHECK_WINDOW) -> bool:
    """True, wenn der Sender über ein `window` Sekunden langes Zeitfenster
    dauerhaft Audio liefert — inklusive der letzten CHECK_TAIL Sekunden
    (siehe Modul-Docstring: eine Quelle, die einmalig einen Vorrat
    ausschüttet und dann verstummt, ist für uns unbrauchbar).

    Läuft bewusst über einen eigenen Lese-Loop statt subprocess.run():
    gebraucht wird nicht "wie viel kam insgesamt", sondern "WANN kam das
    letzte Byte"."""
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-user_agent", USER_AGENT,
             "-i", url,
             "-map", "0:a:0", "-f", "s16le", "-acodec", "pcm_s16le",
             "-ar", str(CHECK_SAMPLE_RATE), "-ac", "1", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        log.debug("[check] %s -> ffmpeg nicht startbar (%s)", url, e)
        return False

    total_bytes = 0
    last_data_at = None
    deadline = time.monotonic() + window
    try:
        fd = proc.stdout.fileno()
        while True:
            time_left = deadline - time.monotonic()
            if time_left <= 0:
                break
            ready, _, _ = select.select([fd], [], [], time_left)
            if not ready:
                break  # Stream steht still, wir sind fertig mit ihm
            chunk = os.read(fd, 65536)
            if not chunk:
                break  # EOF: Quelle hat abgeschlossen, liefert nichts mehr
            total_bytes += len(chunk)
            last_data_at = time.monotonic()
    finally:
        proc.kill()
        proc.wait()

    seconds_of_audio = total_bytes / (CHECK_SAMPLE_RATE * 2)
    if seconds_of_audio < CHECK_MIN_SECONDS:
        log.debug("[check] %s -> nur %.1fs Audio (min. %.1fs)",
                  url, seconds_of_audio, CHECK_MIN_SECONDS)
        return False

    silent_for = deadline - last_data_at
    if silent_for > CHECK_TAIL:
        log.debug("[check] %s -> %.1fs Audio, aber die letzten %.1fs kam nichts mehr "
                  "(nur ein Vorrat, kein laufender Stream)", url, seconds_of_audio, silent_for)
        return False

    log.debug("[check] %s -> ok (%.1fs Audio, zuletzt vor %.1fs)",
              url, seconds_of_audio, silent_for)
    return True


def run_import(url: str, progress=None) -> dict:
    """Kompletter Import-Ablauf: laden, parsen, parallel prüfen
    (max. CHECK_CONCURRENCY gleichzeitig), Duplikate raus, funktionierende
    neue Sender in stations.json übernehmen.

    `progress` (optional): Objekt mit set_phase(phase, total=None) und
    increment_checked() für eine Fortschrittsanzeige im Web-Interface.
    Phasen: "downloading" -> "checking" -> (Aufrufer setzt "done"/"error").

    Gibt {"checked": int, "working": int, "added": int} zurück. Wirft bei
    Netzwerk-/Parse-Fehlern beim Laden der Playlist selbst (nicht bei
    einzelnen nicht erreichbaren Sendern — die werden einfach übersprungen)."""
    log.info("📻 Sender-Import gestartet: %s", url)
    if progress:
        progress.set_phase("downloading")
    text = fetch_m3u(url)
    entries = parse_m3u(text)
    log.info("📻 Playlist geladen: %d Einträge, prüfe je %.0fs auf dauerhaften "
             "Audiofluss (max. %d parallel, grob %.0f Min.) ...",
             len(entries), CHECK_WINDOW, CHECK_CONCURRENCY,
             len(entries) * CHECK_WINDOW / CHECK_CONCURRENCY / 60)

    if progress:
        progress.set_phase("checking", total=len(entries))

    working = []
    if entries:
        with ThreadPoolExecutor(max_workers=CHECK_CONCURRENCY) as pool:
            futures = {pool.submit(check_reachable, e["url"]): e for e in entries}
            for future in as_completed(futures):
                entry = futures[future]
                try:
                    ok = future.result()
                except Exception:
                    ok = False
                if ok:
                    working.append(entry)
                if progress:
                    progress.increment_checked()

    existing = stations_store.load_all()
    existing_names = {s["name"].strip().lower() for s in existing}
    existing_urls = {s["url"].strip() for s in existing}

    to_add = []
    seen_names, seen_urls = set(), set()
    for e in working:
        name_key = e["name"].strip().lower()
        url_key = e["url"].strip()
        if name_key in existing_names or url_key in existing_urls:
            continue
        if name_key in seen_names or url_key in seen_urls:
            continue  # Duplikate innerhalb der importierten Liste selbst
        seen_names.add(name_key)
        seen_urls.add(url_key)
        to_add.append(e)

    # enabled=False: importierte Sender landen deaktiviert in der Liste und
    # werden erst durch eine bewusste Entscheidung des Nutzers Teil der
    # Rotation (Haken auf der Config-Seite).
    added = stations_store.bulk_add(to_add, category=stations_store.IMPORT_CATEGORY,
                                    enabled=False)
    log.info("📻 Import fertig: %d geprüft, %d dauerhaft spielbar, %d neu in '%s' "
             "(deaktiviert).",
             len(entries), len(working), len(added), stations_store.IMPORT_CATEGORY)

    return {"checked": len(entries), "working": len(working), "added": len(added)}
