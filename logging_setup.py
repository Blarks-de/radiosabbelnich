#!/usr/bin/env python3
"""
logging_setup.py — zentrale Logging-Konfiguration für alle RadioSabbelNich-Module.

Ein Aufruf von setup() in radiosabbelnich.main() konfiguriert den Root-Logger;
alle Module holen sich danach einfach ihren eigenen Logger über
logging.getLogger("<name>") und schreiben dorthin. Zwei Ziele:

  - Konsole (stdout): das, was vorher per print() rausging — landet
    unverändert in `docker compose logs -f radiosabbelnich`. Default INFO,
    mit --verbose DEBUG.
  - Logdatei (logs/radiosabbelnich.log, rotierend): IMMER auf DEBUG, egal was
    die Konsole zeigt. Genau dafür ist sie da — wenn nachts etwas schiefgeht,
    will man hinterher die Feature-Werte/VAD-Wahrscheinlichkeiten/HTTP-
    Requests sehen können, ohne den Container vorher zufällig im richtigen
    Modus gestartet zu haben. Rotierend, weil bei ~1 DEBUG-Zeile pro Sekunde
    sonst im Dauerbetrieb unbegrenzt Platz draufgeht.

Zusätzlich wird threading.excepthook umgebogen: Hintergrund-Threads
(PrebufferedSource-Reader, Import-Worker, Webserver) sterben sonst still
mit einem Traceback auf stderr, der in keiner Logdatei auftaucht.
"""

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

DEFAULT_LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "logs", "radiosabbelnich.log"
)
MAX_BYTES = 10 * 1024 * 1024   # pro Datei
BACKUP_COUNT = 5               # -> max. ~60MB Logs insgesamt

CONSOLE_FORMAT = "%(asctime)s %(levelname).1s %(message)s"
CONSOLE_DATEFMT = "%H:%M:%S"
FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)-12s %(threadName)-14s %(message)s"


def setup(log_file: str = DEFAULT_LOG_FILE, verbose: bool = False) -> str | None:
    """Konfiguriert Konsolen- und Datei-Logging. Gibt den tatsächlich
    benutzten Logfile-Pfad zurück, oder None, falls keine Datei geschrieben
    werden konnte (dann läuft alles nur über die Konsole weiter — ein nicht
    beschreibbares Logverzeichnis darf den Player nicht am Starten hindern)."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # Handler filtern, nicht der Root-Logger
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT, datefmt=CONSOLE_DATEFMT))
    root.addHandler(console)

    active_path = None
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            file_handler = RotatingFileHandler(
                log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
            root.addHandler(file_handler)
            active_path = log_file
        except OSError as e:
            root.warning("⚠ Logdatei %s nicht beschreibbar (%s) — logge nur auf die Konsole.",
                         log_file, e)

    _install_thread_excepthook()
    return active_path


def _install_thread_excepthook():
    """Unbehandelte Exceptions in Hintergrund-Threads ins Log schreiben
    statt nur nach stderr (wo sie in der Logdatei fehlen würden)."""
    def hook(args):
        if args.exc_type is SystemExit:
            return
        logging.getLogger("thread").error(
            "💥 Unbehandelte Exception in Thread '%s'",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = hook
