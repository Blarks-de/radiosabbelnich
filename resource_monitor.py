#!/usr/bin/env python3
"""
resource_monitor.py — Live-Ressourcenverbrauch von RadioZapper selbst (nicht
des Hosts) für die Config-Seite. Reine Domänenlogik ohne Bezug zu
StreamSource/SwitcherState, analog zu news_break.py/stt_filter.py: kein
Setter/Property auf SwitcherState nötig, da der Webserver-Thread hier nichts
abfragt, was nur der Hauptloop kennt -- psutil liest den eigenen Prozess und
seine Kinder direkt, unabhängig vom Player-Zustand (siehe CLAUDE.md,
host_paths-Abschnitt für dasselbe Muster).

RAM/CPU umfassen bewusst NICHT nur den Python-Hauptprozess, sondern auch alle
ffmpeg-Kindprozesse (laufender Sender + vorgewärmte Prebuffer-Kandidaten,
siehe PrebufferedSource in radiozapper.py) -- die machen den Großteil des
tatsächlichen Fußabdrucks aus, ein Wert ohne sie wäre irreführend niedrig.
"""

import glob
import logging
import os

import psutil

import stt_filter  # nur für WHISPER_DOWNLOAD_ROOT, siehe _dir_size_bytes()

log = logging.getLogger("resources")


class ResourceMonitor:
    """Hält psutil.Process-Handles über mehrere snapshot()-Aufrufe hinweg am
    Leben. Process.cpu_percent(interval=None) misst laut psutil-Doku ein
    Delta seit dem LETZTEN Aufruf auf demselben Objekt -- der jeweils ERSTE
    Aufruf pro Objekt liefert einen bedeutungslosen Wert (0.0), den man
    verwerfen muss. Ein Cache pro PID (statt pro Request neu
    psutil.Process() anzulegen) ist deshalb keine Optimierung, sondern
    notwendig, damit CPU-Werte über mehrere Snapshots hinweg überhaupt
    aussagekräftig sind -- inklusive neu auftauchender ffmpeg-Kinder, die
    im Snapshot ihrer ersten Sichtung mit 0% auftauchen und erst ab dem
    nächsten Poll-Intervall einen echten Wert liefern."""

    def __init__(self, fingerprint_db_path: str, log_file_path: str = None):
        self._fingerprint_db_path = fingerprint_db_path
        self._log_file_path = log_file_path
        self._proc = psutil.Process(os.getpid())
        self._proc.cpu_percent(None)  # Priming-Aufruf, siehe Klassen-Docstring
        self._child_procs = {}  # pid -> psutil.Process, gleiches Priming pro Kind

    def _children_snapshot(self):
        rss = 0
        cpu = 0.0
        count = 0
        try:
            children = self._proc.children(recursive=True)
        except psutil.Error:
            return 0, 0.0, 0

        alive_pids = set()
        for child in children:
            alive_pids.add(child.pid)
            cached = self._child_procs.get(child.pid)
            if cached is None:
                cached = child
                cached.cpu_percent(None)  # Priming, siehe Klassen-Docstring
                self._child_procs[child.pid] = cached
                this_cpu = 0.0  # erster Snapshot dieses Kindes -- s.o.
            else:
                try:
                    this_cpu = cached.cpu_percent(None)
                except psutil.Error:
                    continue
            try:
                rss += cached.memory_info().rss
                cpu += this_cpu
                count += 1
            except psutil.Error:
                continue  # zwischen children() und hier gestorben -- auslassen

        # Gestorbene Kinder aus dem Cache werfen, sonst wächst er unbegrenzt
        # über die Lebensdauer des Prozesses (Sender werden laufend
        # gewechselt, ffmpeg-Prozesse kommen und gehen entsprechend mit).
        for pid in list(self._child_procs):
            if pid not in alive_pids:
                del self._child_procs[pid]

        return rss, cpu, count

    def _log_size_bytes(self):
        if not self._log_file_path:
            return 0
        total = 0
        # RotatingFileHandler hängt .1/.2/... an (siehe logging_setup.py,
        # BACKUP_COUNT) -- die zählen zum tatsächlichen Log-Fußabdruck dazu.
        for path in [self._log_file_path] + glob.glob(self._log_file_path + ".*"):
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        return total

    @staticmethod
    def _file_size_bytes(path):
        if not path:
            return 0
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    @staticmethod
    def _dir_size_bytes(path):
        if not path or not os.path.isdir(path):
            return 0
        total = 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total

    def snapshot(self) -> dict:
        try:
            main_rss = self._proc.memory_info().rss
            main_cpu = self._proc.cpu_percent(None)
        except psutil.Error:
            main_rss, main_cpu = 0, 0.0

        ffmpeg_rss, ffmpeg_cpu, ffmpeg_count = self._children_snapshot()

        return {
            "main_rss_bytes": main_rss,
            "main_cpu_percent": round(main_cpu, 1),
            "ffmpeg_count": ffmpeg_count,
            "ffmpeg_rss_bytes": ffmpeg_rss,
            "ffmpeg_cpu_percent": round(ffmpeg_cpu, 1),
            "total_rss_bytes": main_rss + ffmpeg_rss,
            "total_cpu_percent": round(main_cpu + ffmpeg_cpu, 1),
            "fingerprint_db_bytes": self._file_size_bytes(self._fingerprint_db_path),
            "log_bytes": self._log_size_bytes(),
            "whisper_cache_bytes": self._dir_size_bytes(stt_filter.WHISPER_DOWNLOAD_ROOT),
        }
