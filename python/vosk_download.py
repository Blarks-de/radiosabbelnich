#!/usr/bin/env python3
# Copyright (C) 2026 RadioSabbelNich
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 (or
# later), as published by the Free Software Foundation. See LICENSE.

"""
vosk_download.py — lädt ein Vosk-Modell aus vosk_catalog.CATALOG herunter
und installiert es in einen beschreibbaren Ordner, komplett ohne
Konsolenzugriff (siehe SESSION.md). Reine Domänenlogik ohne WebUI-Bezug,
analog zu station_import.py: `progress` ist ein optionales Duck-Typing-
Objekt (set_phase()/set_downloaded()), das echte Fortschritts-Tracking
sitzt in webui.VoskDownloadState.

dest_root MUSS der neue beschreibbare Sammel-Mount sein (VOSK_MODELS_FOLDER,
siehe docker-compose.yml) -- NICHT einer der alten, einzeln UND read-only
gemounteten Sprachordner (vosk-model-de/-en): dort kann der Container gar
nicht schreiben, siehe ARCHITECTURE.md.

Rollback-Strategie: der komplette Ablauf (Download, Entpacken) läuft unter
Temp-Namen (`.download-<code>.zip`, `.extract-<code>/`) INNERHALB von
dest_root -- das ist derselbe gemountete Ordner, also ist der finale
Installationsschritt ein einziges atomares os.rename() (kein Rename-über-
Mountpoint-Problem wie bei stations_store._write(), das betrifft nur
einzeln gebindmountete DATEIEN, hier ist der ganze Ordner gemountet). Der
Zielordner dest_root/<code>/ entsteht AUSSCHLIESSLICH durch diesen einen
rename() -- bricht irgendwas vorher ab (Netzwerk, korruptes Zip,
Plattenfehler), bleibt er unberührt. Ein `finally`-Block räumt Temp-Reste
in JEDEM Fall auf (Erfolg wie Fehlschlag), damit nie ein halb entpackter
Ordner unter einem Namen liegen bleibt, den ein künftiger Aufruf
fälschlich als eigenen Rest interpretieren könnte.
"""

import logging
import os
import shutil
import urllib.request
import zipfile

import vosk_catalog

log = logging.getLogger("vosk_download")

CHUNK_SIZE = 1024 * 1024  # 1 MB pro Read -- groß genug für wenig Overhead,
                           # klein genug für feingranulare Fortschrittsupdates
DOWNLOAD_TIMEOUT = 30.0   # Sekunden PRO read()/connect(), nicht Gesamtdauer --
                           # ein Mehrhundert-MB-Download braucht auf normaler
                           # Leitung ohnehin mehrere Minuten (viele einzelne
                           # Reads), das ist beabsichtigt kein Gesamt-Timeout
USER_AGENT = "RadioSabbelNich/1.0"

# Zip-Datei und entpackte Kopie liegen kurzzeitig GLEICHZEITIG auf der
# Platte -- 2.5x der (ohnehin nur grob geschätzten, siehe vosk_catalog.py)
# Katalog-Größe als Sicherheitsmarge statt exaktem 2x, weil entpackte
# Kaldi-Modelle typischerweise etwas größer als das Zip sind.
DISK_SPACE_SAFETY_FACTOR = 2.5


class InsufficientDiskSpaceError(Exception):
    pass


def check_disk_space(dest_root: str, size_mb: int):
    os.makedirs(dest_root, exist_ok=True)
    free_bytes = shutil.disk_usage(dest_root).free
    needed_bytes = size_mb * 1024 * 1024 * DISK_SPACE_SAFETY_FACTOR
    if free_bytes < needed_bytes:
        raise InsufficientDiskSpaceError(
            f"Nur noch {free_bytes / (1024 * 1024):.0f} MB frei, benötigt werden "
            f"ca. {needed_bytes / (1024 * 1024):.0f} MB (Modell ~{size_mb} MB, "
            f"Sicherheitsmarge für Download+Entpacken gleichzeitig)."
        )


def _download(url: str, dest_path: str, progress=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        if progress:
            progress.set_phase("downloading", total=total)
        downloaded = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress.set_downloaded(downloaded)


def _find_model_root(extract_dir: str) -> str:
    """Vosk-Zips enthalten praktisch immer EINEN Wurzelordner
    (z.B. 'vosk-model-de-0.21/') statt die Modelldateien direkt im
    Zip-Root -- den finden und zurückgeben, damit der finale Zielordner
    direkt das Modellverzeichnis selbst ist (Vosk erwartet 'am/'/'conf/'
    etc. UNMITTELBAR unter vosk_model_path, kein zusätzliches
    Verschachtelungslevel)."""
    entries = [e for e in os.listdir(extract_dir) if not e.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
        return os.path.join(extract_dir, entries[0])
    return extract_dir  # Fallback: Zip hatte doch keinen Wurzelordner


def download_and_install(catalog_key: str, dest_root: str, progress=None) -> dict:
    """Lädt vosk_catalog.CATALOG_BY_KEY[catalog_key] herunter und
    installiert es atomar unter dest_root/<code>/. Gibt {"code", "path"}
    zurück. Wirft ValueError (unbekannter Katalog-Key, korruptes Zip),
    InsufficientDiskSpaceError, oder eine urllib-Exception bei
    Netzwerkfehlern -- der Aufrufer (webui.VoskDownloadState-Worker)
    fängt das breit ab und zeigt die Fehlermeldung in der WebUI."""
    entry = vosk_catalog.CATALOG_BY_KEY.get(catalog_key)
    if entry is None:
        raise ValueError(f"Unbekannter Katalog-Eintrag: {catalog_key!r}")

    code = entry["code"]
    check_disk_space(dest_root, entry["size_mb"])

    final_dir = os.path.join(dest_root, code)
    tmp_zip = os.path.join(dest_root, f".download-{code}.zip")
    tmp_extract = os.path.join(dest_root, f".extract-{code}")

    def cleanup_temp():
        if os.path.exists(tmp_zip):
            os.remove(tmp_zip)
        if os.path.isdir(tmp_extract):
            shutil.rmtree(tmp_extract, ignore_errors=True)

    cleanup_temp()  # Reste eines vorherigen fehlgeschlagenen Versuchs zuerst wegräumen
    try:
        _download(entry["url"], tmp_zip, progress)

        if progress:
            progress.set_phase("extracting")
        if not zipfile.is_zipfile(tmp_zip):
            raise ValueError(
                "Heruntergeladene Datei ist kein gültiges ZIP-Archiv "
                "(Download vermutlich unvollständig oder korrupt)."
            )
        os.makedirs(tmp_extract, exist_ok=True)
        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(tmp_extract)

        if progress:
            progress.set_phase("installing")
        model_root = _find_model_root(tmp_extract)
        if os.path.isdir(final_dir):
            shutil.rmtree(final_dir)  # erneuter Download derselben Sprache ersetzt sauber
        os.rename(model_root, final_dir)
        log.info("🌐 Vosk-Modell '%s' (%s) installiert unter %s.", code, catalog_key, final_dir)
        return {"code": code, "path": final_dir}
    finally:
        cleanup_temp()
