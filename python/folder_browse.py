#!/usr/bin/env python3
"""
folder_browse.py — Gemeinsamer Baustein für die Breadcrumb-Ordnerauswahl
auf der Config-Seite: wird sowohl für den News-Break-MP3-Pfad als auch für
den Musiksammlung-Root verwendet (siehe CLAUDE.md/webui.py _BROWSE_ROOTS).

Reine Domänenlogik hier drin — kein Bezug zu settings_store/StreamSource/
SwitcherState, analog zu news_break.py/music_library.py. Welcher
Config-Key mit dem Ergebnis (absolute_path) am Ende beschrieben wird,
entscheidet ausschließlich der Aufrufer in webui.py — diese Funktion kennt
nur einen festen Root (eine Docker-Mount-Grenze) und einen relativen
Unterpfad darunter.
"""

import logging
import os

log = logging.getLogger("folderbrowse")


def list_subfolders(root: str, rel_path: str = "") -> dict:
    """Listet die UNMITTELBAREN Unterordner von `root`/`rel_path` (nicht
    rekursiv).

    `rel_path` wird per realpath() + Prefix-Check gegen Verzeichnis-
    Traversal (z.B. "../../etc") abgesichert — `root` ist eine feste
    Docker-Mount-Grenze, ein Client darf sie über diesen Endpoint nicht
    verlassen können, auch hinter VPN nicht (siehe CLAUDE.md "Kein Auth,
    nur hinter VPN": kein Auth heißt nicht "beliebiger Dateisystemzugriff
    ist ok"). Ein Traversal-Versuch wird geloggt und fällt still auf den
    Root zurück, statt einen Fehler zu werfen — die Breadcrumb-Komponente
    im Frontend soll dadurch nie in einen Fehlerzustand geraten, nur nie
    weiter als bis zum Root zurückspringen.

    Gibt IMMER ein dict zurück, nie eine Exception (Toleranz-Muster wie
    news_break.pick_random_mp3()/music_library.list_tracks()):
    {"path": <normalisierter rel_path, "/"-getrennt>,
     "breadcrumb": [{"name": ..., "path": ...}, ...] (erster Eintrag = Root),
     "folders": [...Namen, alphabetisch...],
     "absolute_path": <str, tatsächlicher Container-Pfad>,
     "error": None oder Fehlertext (z.B. Ordner nicht lesbar)}."""
    root_real = os.path.realpath(root)
    rel_path = (rel_path or "").strip("/")
    candidate = os.path.realpath(os.path.join(root_real, rel_path)) if rel_path else root_real

    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        log.warning("⚠ Pfad-Traversal-Versuch abgewiesen: root=%s rel_path=%r", root, rel_path)
        candidate = root_real

    norm_rel = os.path.relpath(candidate, root_real)
    norm_rel = "" if norm_rel == "." else norm_rel.replace(os.sep, "/")

    breadcrumb = [{"name": os.path.basename(root_real) or root_real, "path": ""}]
    acc = ""
    if norm_rel:
        for part in norm_rel.split("/"):
            acc = f"{acc}/{part}" if acc else part
            breadcrumb.append({"name": part, "path": acc})

    try:
        with os.scandir(candidate) as it:
            folders = sorted((e.name for e in it if e.is_dir()), key=str.lower)
        error = None
    except OSError as e:
        folders = []
        error = str(e)
        log.warning("⚠ Ordner-Browser: %s nicht lesbar (%s).", candidate, e)

    return {
        "path": norm_rel,
        "breadcrumb": breadcrumb,
        "folders": folders,
        "absolute_path": candidate,
        "error": error,
    }
