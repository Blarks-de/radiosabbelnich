#!/usr/bin/env python3
# Copyright (C) 2026 RadioSabbelNich
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 (or
# later), as published by the Free Software Foundation. See LICENSE.

"""
audio_tags.py — format-übergreifende Tag-Abstraktion, geteilt zwischen
music_scan.py (DB-Scan) und radiosabbelnich.py (Live-Anzeige während der
Wiedergabe, News-Break + Musik-Player). Reine Domänenlogik, KEIN Zugriff
auf StreamSource/SwitcherState.

open_tags()/extract_year()/RawId3EasyAdapter waren ursprünglich private
Bausteine in music_scan.py (siehe dessen SESSION.md-Historie zur
Format-Erweiterung 2026-08-12) -- hierher verschoben, weil die Live-
Anzeige exakt dieselbe, an echten Testdateien verifizierte Format-Weiche
braucht (v.a. WAV ohne EasyWAVE-Wrapper und rohes ADTS-AAC, das mutagens
Auto-Erkennung fälschlich als MP3 erkennt und crasht -- siehe
Docstrings unten). Cover-Extraktion (_read_cover_bytes()) bleibt bewusst
in music_scan.py: die Live-Anzeige braucht (noch) kein Cover, siehe
CLAUDE.md/Nice-to-have-Notiz zur Tag-Anzeige.
"""

import logging
import os

import mutagen
from mutagen import MutagenError
from mutagen.aac import AAC
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.wave import WAVE

log = logging.getLogger("audiotags")

# easy-Key -> ID3-Frame-ID, für RawId3EasyAdapter unten (WAV/.aac) -- deckt
# exakt die Felder ab, die read_display_tags()/music_scan.py tatsächlich
# auslesen (artist/title/album/genre/date), keine vollständige ID3-Map.
_EASY_KEY_TO_ID3_FRAME = {"artist": "TPE1", "title": "TIT2", "album": "TALB",
                          "genre": "TCON", "date": "TDRC"}


class RawId3EasyAdapter:
    """Bietet dieselbe .get(key) -> list|None -Schnittstelle wie mutagens
    "easy"-Wrapper (EasyID3/EasyMP3/EasyMP4/...), für die zwei Formate, bei
    denen mutagen.File(pfad, easy=True) das NICHT von sich aus liefert:
    WAV (kein EasyWAVE -- ein rohes WAVE(pfad).get("artist") liefert IMMER
    None, auch wenn ID3-Tags unter rohen Frame-IDs wie "TPE1" vorhanden
    sind) und rohes ADTS-AAC (siehe open_tags() unten). Liest die
    Standard-Textframes direkt aus einem mutagen.id3.ID3-Objekt (oder
    liefert für jeden Key None, falls gar keine ID3-Tags vorhanden sind)."""

    def __init__(self, id3_tags, info=None):
        self._tags = id3_tags
        self.info = info

    def get(self, key):
        if self._tags is None:
            return None
        frame = self._tags.get(_EASY_KEY_TO_ID3_FRAME.get(key))
        return list(frame.text) if frame is not None else None


def open_tags(full_path: str, ext: str):
    """Liefert ein Objekt mit derselben .get(key)/.info-Schnittstelle wie
    mutagen.File(pfad, easy=True) -- für die meisten Formate ist das
    tatsächlich mutagen.File() selbst, für .wav und .aac wird bewusst ein
    anderer Lesepfad genommen:

    - .wav: siehe RawId3EasyAdapter oben.
    - .aac (rohes ADTS, NICHT der MP4-Container M4A): mutagen.File()s
      Auto-Erkennung erkennt eine getaggte .aac-Datei fälschlich als MP3
      (wegen des vorhandenen ID3v2-Headers) und crasht beim MPEG-Frame-
      Sync (HeaderNotFoundError, live reproduziert). Tags deshalb direkt
      über ID3(), Stream-Info separat über AAC().info."""
    if ext == ".aac":
        try:
            id3_tags = ID3(full_path)
        except ID3NoHeaderError:
            id3_tags = None
        return RawId3EasyAdapter(id3_tags, AAC(full_path).info)
    if ext == ".wav":
        wav = WAVE(full_path)
        return RawId3EasyAdapter(wav.tags, wav.info)
    return mutagen.File(full_path, easy=True)


def extract_year(easy_tags) -> int | None:
    """easyid3 liefert das Datum (falls vorhanden) als 'YYYY' oder
    'YYYY-MM-DD' im 'date'-Feld -- nur die ersten 4 Ziffern interessieren."""
    values = easy_tags.get("date") if easy_tags else None
    if not values:
        return None
    raw = str(values[0])[:4]
    return int(raw) if raw.isdigit() else None


def read_display_tags(full_path: str) -> dict:
    """Liefert {"title", "artist", "album", "year"} für die Zwei-Zeilen-
    Anzeige (Titel & Interpret / Album & Jahr) beim Abspielen einer
    News-Break-MP3 oder eines Musik-Library-Tracks.

    Fallback-Kaskade:
    - kein Titel-Tag vorhanden -> Dateiname (ohne Endung) als title.
    - artist/album/year fehlen -> None, der Aufrufer entscheidet, wie er
      das darstellt (siehe _build_status() in webui.py: Zeile 2 komplett
      weggelassen, wenn Album UND Jahr fehlen, statt eines Platzhalters).
    - Datei nicht lesbar oder kein unterstütztes Format (MutagenError/
      OSError) -> alles None außer title (Dateiname-Fallback) -- kein
      Fehler, der den Hauptloop stören dürfte, gleiches Toleranz-Muster
      wie news_break.pick_random_mp3()/music_library.list_tracks()."""
    ext = os.path.splitext(full_path)[1].lower()
    fallback_title = os.path.splitext(os.path.basename(full_path))[0]
    try:
        tags = open_tags(full_path, ext)
        if tags is None:
            raise MutagenError(f"kein unterstütztes Audioformat ({full_path})")
        title = (tags.get("title") or [None])[0] or fallback_title
        artist = (tags.get("artist") or [None])[0]
        album = (tags.get("album") or [None])[0]
        year = extract_year(tags)
    except (MutagenError, OSError) as e:
        log.debug("Tag-Anzeige: %s nicht lesbar (%s) -- nur Dateiname.", full_path, e)
        return {"title": fallback_title, "artist": None, "album": None, "year": None}
    return {"title": title, "artist": artist, "album": album, "year": year}


def read_duration_seconds(full_path: str) -> float | None:
    """Spieldauer in Sekunden, oder None falls nicht lesbar/kein
    unterstütztes Format -- für den Werbeblock-Vorbuffering-Trigger einer
    News-Break-MP3 (radiosabbelnich.py): Restzeit = Dauer minus verstrichene
    Zeit seit Start. Gleiches Toleranz-Muster wie read_display_tags() oben,
    der Aufrufer behandelt "unbekannt" einfach wie "noch kein Trigger"."""
    ext = os.path.splitext(full_path)[1].lower()
    try:
        tags = open_tags(full_path, ext)
        if tags is None or tags.info is None:
            return None
        return float(tags.info.length)
    except (MutagenError, OSError) as e:
        log.debug("Dauer nicht lesbar: %s (%s)", full_path, e)
        return None
