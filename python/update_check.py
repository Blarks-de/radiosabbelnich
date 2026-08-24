#!/usr/bin/env python3
# Copyright (C) 2026 RadioSabbelNich
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 (or
# later), as published by the Free Software Foundation. See LICENSE.

"""
update_check.py — reiner Lesezugriff: prüft alle 24h gegen
raw.githubusercontent.com, ob die dortige VERSION-Datei (main-Branch)
weiter ist als die im Container gebackene lokale VERSION (siehe
webui._VERSION_STRING). Es gibt für die Docker-Installation aktuell
KEIN Image-Registry-Deployment -- die einzige Update-Möglichkeit ist
`git pull` im Repo-Verzeichnis + `docker compose up -d --build`, siehe
README.md. Dieses Modul löst dementsprechend selbst NICHTS aus (kein
Auto-Update, kein `docker pull`), es liefert nur den Hinweis fürs
Web-Interface.

Reine Domänenlogik ohne Zugriff auf StreamSource/SwitcherState, analog
zu resource_monitor.py/news_break.py -- der Radiobetrieb bekommt von
diesem Modul nichts mit, ein Fehler hier (kein Internet, GitHub down,
Rate-Limit, kaputtes VERSION-Format) bleibt lokal und wird nur leise
geloggt (siehe UpdateChecker._run()).
"""

import logging
import re
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("updatecheck")

REMOTE_VERSION_URL = "https://raw.githubusercontent.com/Blarks-de/radiosabbelnich/main/VERSION"
# Fest verlinkt statt aus einer Remote-JSON gelesen -- es gibt bewusst
# keine zweite Versionsdatei neben VERSION (siehe ARCHITECTURE.md), ein
# "was ist neu"-Link auf den CHANGELOG reicht für den Zweck hier völlig.
CHANGELOG_URL = "https://github.com/Blarks-de/radiosabbelnich/blob/main/CHANGELOG.md"

CHECK_INTERVAL_SECONDS = 24 * 3600
# Wie oft der Hintergrund-Thread aufwacht, um enabled/last_checked_at neu
# zu bewerten -- deutlich kürzer als CHECK_INTERVAL_SECONDS, damit ein
# Deaktivieren über die Config-Seite zeitnah wirkt (siehe UpdateChecker),
# statt bis zu 24h nachzuwirken.
_POLL_INTERVAL_SECONDS = 300
_HTTP_TIMEOUT_SECONDS = 10

_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str):
    """Extrahiert (major, minor, patch) aus dem SemVer-Präfix einer
    VERSION-Datei (Format "vMAJOR.MINOR.PATCH build ..."). None bei
    Nichttreffer statt Exception -- ein kaputtes/leeres Remote-Ergebnis
    ist ein Fehlerfall für den Aufrufer, kein Programmierfehler hier."""
    if not text:
        return None
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def check_now(local_version_string: str) -> dict:
    """Ein einzelner synchroner Check. Wirft bei Netzwerk-/Parse-Fehlern
    eine Exception -- der Aufrufer (UpdateChecker._run()) entscheidet,
    wie fehlertolerant damit umgegangen wird, diese Funktion selbst
    bleibt darüber bewusst unwissend (leichter isoliert testbar, siehe
    SESSION.md-Verifikation)."""
    req = urllib.request.Request(
        REMOTE_VERSION_URL, headers={"User-Agent": "RadioSabbelNich-UpdateCheck/1.0"}
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
        remote_text = resp.read().decode("utf-8", errors="replace")

    remote_version = parse_version(remote_text)
    if remote_version is None:
        raise ValueError(f"VERSION-Antwort nicht im erwarteten Format: {remote_text[:80]!r}")

    local_version = parse_version(local_version_string) or (0, 0, 0)
    update_available = remote_version > local_version

    return {
        "remote_version": remote_text.strip().splitlines()[0] if remote_text.strip() else "",
        "update_available": update_available,
        "checked_at": time.time(),
    }


class UpdateChecker:
    """Hintergrund-Thread, der check_now() alle CHECK_INTERVAL_SECONDS
    aufruft. get_settings/on_result sind Callbacks statt eines direkten
    settings_store-Imports, damit dieses Modul (wie news_break.py/
    resource_monitor.py) reine Domänenlogik ohne Kenntnis des konkreten
    Persistenz-Formats bleibt."""

    def __init__(self, get_settings, get_local_version, on_result):
        """get_settings() -> dict (der 'update_check'-Block aus
        settings.json, für 'enabled' + 'last_checked_at'),
        get_local_version() -> str, on_result(remote_version,
        update_available, checked_at) persistiert das Ergebnis."""
        self._get_settings = get_settings
        self._get_local_version = get_local_version
        self._on_result = on_result
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="UpdateChecker")
        self._thread.start()

    def _run(self):
        while True:
            cfg = self._get_settings()
            if cfg.get("enabled", True):
                last_checked_at = cfg.get("last_checked_at")
                due = last_checked_at is None or (
                    time.time() - last_checked_at >= CHECK_INTERVAL_SECONDS
                )
                if due:
                    self._check_once()
            time.sleep(_POLL_INTERVAL_SECONDS)

    def _check_once(self):
        try:
            result = check_now(self._get_local_version())
        except (OSError, urllib.error.URLError, ValueError) as e:
            # Kein Internet, GitHub down, Rate-Limit, kaputtes Format --
            # bewusst leise (debug statt warning/error, siehe
            # Moduldocstring): das ist ein reiner Komfort-Hinweis, kein
            # Fehler, der den Betrieb betrifft. Kein Retry-Spam: der
            # nächste Versuch kommt erst wieder beim nächsten fälligen
            # Poll-Tick, last_checked_at bleibt dabei bewusst
            # UNVERÄNDERT, damit ein dauerhafter Ausfall nicht alle 5 Min.
            # neu versucht wird, sondern regulär alle 24h.
            log.debug("🔄 Update-Prüfung fehlgeschlagen (bleibt folgenlos): %s", e)
            return
        log.info(
            "🔄 Update-Prüfung: Remote-Version %s, Update verfügbar: %s",
            result["remote_version"], result["update_available"],
        )
        self._on_result(result["remote_version"], result["update_available"], result["checked_at"])
