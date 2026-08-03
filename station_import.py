#!/usr/bin/env python3
"""
station_import.py — Sender-Import aus einer M3U-Playlist (z.B. die
Kodinerds-Kodi-Radioliste, Default-URL siehe settings_store.DEFAULTS).
Lädt die Liste, prüft jeden Sender per ffprobe auf Erreichbarkeit
(parallelisiert), und übernimmt nur funktionierende, noch nicht
vorhandene Sender in stations.json (Kategorie stations_store.IMPORT_CATEGORY).

Erreichbarkeits-Check: ffprobe statt HTTP HEAD/GET. Begründung: unsere
eigene Wiedergabe (StreamSource in radiozapper.py) öffnet Sender-URLs
ebenfalls über ffmpeg — ffprobe nutzt denselben Demuxer-/Protokoll-Stack,
prüft also genau das, was zählt ("kann unser Player das tatsächlich
abspielen"), nicht nur "antwortet der Server irgendwie auf HTTP". Viele
Icecast/Shoutcast-Server unterstützen HEAD gar nicht richtig oder liefern
200 ohne echten Audio-Inhalt; manche Playlist-Einträge zeigen selbst
wieder auf verschachtelte M3U/PLS-Dateien, die ein simpler HTTP-Request
als "erreichbar" durchwinken würde, obwohl unser Player (genau wie
ffprobe mit denselben Optionen) sie gar nicht direkt abspielen kann —
live gegen einen Kodinerds-Listeneintrag verifiziert (ffprobe lehnt eine
verschachtelte .m3u korrekt als "Invalid data" ab, ein HTTP-GET hätte
sie fälschlich als erreichbar gemeldet).
"""

import logging
import re
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import stations_store

log = logging.getLogger("import")

CHECK_TIMEOUT = 6.0      # Sekunden pro ffprobe-Check
CHECK_CONCURRENCY = 10   # max. gleichzeitige Checks
FETCH_TIMEOUT = 15.0     # Sekunden zum Laden der M3U-Datei

_EXTINF_RE = re.compile(r"^#EXTINF:[^,]*,(.*)$")


def fetch_m3u(url: str, timeout: float = FETCH_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "RadioZapper/1.0"})
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


def check_reachable(url: str, timeout: float = CHECK_TIMEOUT) -> bool:
    """True, wenn ffprobe unter der URL mindestens einen Audio-Stream
    findet (siehe Modul-Docstring für die Begründung ggü. HTTP HEAD/GET)."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", url],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.debug("[check] %s -> nicht erreichbar (%s)", url, type(e).__name__)
        return False
    ok = result.returncode == 0 and "audio" in result.stdout
    if not ok:
        log.debug("[check] %s -> kein Audio-Stream (rc=%d, stderr=%s)",
                  url, result.returncode, (result.stderr or "").strip()[:120])
    return ok


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
    log.info("📻 Playlist geladen: %d Einträge, prüfe Erreichbarkeit "
             "(max. %d parallel, %.0fs Timeout pro Sender) ...",
             len(entries), CHECK_CONCURRENCY, CHECK_TIMEOUT)

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

    added = stations_store.bulk_add(to_add, category=stations_store.IMPORT_CATEGORY)
    log.info("📻 Import fertig: %d geprüft, %d erreichbar, %d neu in '%s'.",
             len(entries), len(working), len(added), stations_store.IMPORT_CATEGORY)

    return {"checked": len(entries), "working": len(working), "added": len(added)}
