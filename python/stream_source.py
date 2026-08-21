#!/usr/bin/env python3
"""
stream_source.py — ffmpeg-Wrapper, der aus einer Stream-URL oder lokalen
Datei zwei parallele PCM-Ströme aus demselben Prozess liefert: Mono
(s16le, pipe:1) für die Analyse-Pipeline (VAD/Heuristik/Fingerprint/STT)
und Stereo (s16le, zusätzliche Pipe) fürs tatsächliche Playback/Icecast-
Encoding. Ein Prozess statt zwei, damit der Stream nicht doppelt geholt
werden muss.

Eigenständiges Modul (seit 2026-08-21, vorher Teil von
radiosabbelnich.py): sowohl der Hauptloop als auch der Hintergrund-
Detector für das Werbeblock-Vorbuffering (siehe ad_skip_prebuffer.py)
brauchen dieselbe Klasse, ohne dass ad_skip_prebuffer.py radiosabbelnich.py
importieren müsste (das gäbe einen Zirkelimport, sobald der Hauptloop
seinerseits ad_skip_prebuffer.py einbindet). Reine Stream-Mechanik hier
drin — kein Zugriff auf SwitcherState/Hauptloop-Zustand, kein
Ausgabe-/Wiedergabe-Handling (das macht der Output, siehe
radiosabbelnich.py)."""

import os
import select
import subprocess
import time

import numpy as np

STREAM_READ_TIMEOUT = 8.0    # max. Wartezeit pro Analysefenster, bevor eine Quelle als tot gilt
                              # (verhindert, dass ein hängender Sender den Loop für immer blockiert)


class StreamSource:
    """Startet ffmpeg für eine Stream-URL und liefert zwei parallele PCM-Ströme
    aus demselben Prozess: Mono (s16le, pipe:1) für die Analyse-Pipeline
    (VAD/Heuristik/Fingerprint) und Stereo (s16le, zusätzliche Pipe) fürs
    tatsächliche Playback/Icecast-Encoding. Ein Prozess statt zwei, damit der
    Stream nicht doppelt geholt werden muss. Kümmert sich NICHT um
    Ausgabe/Wiedergabe — das macht der Output."""

    def __init__(self, sample_rate: int):
        self.sample_rate = sample_rate
        self.proc = None
        self._stereo_read_fd = None

    def start(self, url: str, realtime: bool = False):
        """`realtime=True` fügt ffmpegs `-re` ein (Input in "nativer"
        Wall-Clock-Geschwindigkeit lesen, statt so schnell wie CPU/Disk
        erlauben). Für echte Radio-URLs unnötig und deshalb NICHT der
        Default: die sind durch die Netzwerk-Auslieferung beim Sender
        selbst schon in Echtzeit getaktet (das ist der Mechanismus, der
        den ganzen Hauptloop auf ~1 Analysefenster/Sekunde hält, ohne dass
        radiosabbelnich.py selbst irgendwo bremst).

        PFLICHT dagegen für lokale Dateien (siehe news_break.py/
        Nachrichten-Pause): ohne -re dekodiert ffmpeg eine lokale Datei so
        schnell wie möglich — ein 35s-Clip landet dann in
        Sekundenbruchteilen komplett in den Ausgabe-Pipes, statt über
        seine echte Spieldauer verteilt zu werden (gemessen: 35s Audio in
        0,1s statt 35s Wall-Clock ohne -re; mit -re exakt 35s)."""
        self.stop()
        stereo_read_fd, stereo_write_fd = os.pipe()
        os.set_inheritable(stereo_write_fd, True)
        cmd = ["ffmpeg", "-loglevel", "error"]
        if realtime:
            cmd += ["-re"]
        cmd += [
            "-i", url,
            "-map", "0:a", "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", str(self.sample_rate), "-ac", "1",
            "pipe:1",
            "-map", "0:a", "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", str(self.sample_rate), "-ac", "2",
            f"pipe:{stereo_write_fd}",
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            pass_fds=(stereo_write_fd,),
        )
        os.close(stereo_write_fd)
        self._stereo_read_fd = stereo_read_fd

    def read_window(self, seconds: float, timeout: float = STREAM_READ_TIMEOUT):
        """Liest ein Analysefenster aus beiden Pipes parallel (via select),
        damit keine Pipe volllaufen und ffmpeg blockieren kann, während wir
        auf die andere warten. Gibt (mono, stereo) als int16-Arrays zurück;
        stereo ist interleaved (L,R,L,R,...).

        Gibt spätestens nach `timeout` Sekunden zurück, auch wenn das
        Fenster noch nicht voll ist (z.B. weil die Quelle nicht antwortet
        oder eine Station nie eine Verbindung zustande bringt) — sonst
        blockiert eine tote Quelle den kompletten Hauptloop für immer,
        inklusive manuellem Umschalten auf einen anderen Sender."""
        n_samples = int(self.sample_rate * seconds)
        mono_fd = self.proc.stdout.fileno()
        stereo_fd = self._stereo_read_fd
        targets = {mono_fd: n_samples * 2, stereo_fd: n_samples * 2 * 2}
        buffers = {mono_fd: b"", stereo_fd: b""}
        remaining = {mono_fd, stereo_fd}

        deadline = time.monotonic() + timeout
        while remaining:
            time_left = deadline - time.monotonic()
            if time_left <= 0:
                break
            ready, _, _ = select.select(list(remaining), [], [], time_left)
            if not ready:
                break  # Timeout, keine Daten mehr angekommen
            for fd in ready:
                need = targets[fd] - len(buffers[fd])
                chunk = os.read(fd, need)
                if not chunk:
                    remaining.discard(fd)
                    continue
                buffers[fd] += chunk
                if len(buffers[fd]) >= targets[fd]:
                    remaining.discard(fd)

        mono = np.frombuffer(buffers[mono_fd], dtype=np.int16)
        stereo = np.frombuffer(buffers[stereo_fd], dtype=np.int16)
        return mono, stereo

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None
        if self._stereo_read_fd is not None:
            try:
                os.close(self._stereo_read_fd)
            except OSError:
                pass
            self._stereo_read_fd = None
