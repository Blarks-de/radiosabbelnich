FROM python:3.12-slim

# build-essential NUR wegen aubio: das PyPI-Paket liefert kein Wheel, pip
# baut es aus dem Source-Tarball (setup.py, braucht gcc) -- bleibt danach
# im Image (bewusster Größen-Tradeoff, siehe CLAUDE.md/SESSION.md zu
# Phase 3 der Musik-Library-Roadmap: aubio selbst ist zur LAUFZEIT sehr
# leichtgewichtig, ~0,25s pro Track inkl. Decode, deutlich schlanker als
# die Alternative librosa mit ihrem numba/scipy/scikit-learn-Rattenschwanz).
# curl NUR fürs Herunterladen des aubio-Source-Tarballs unten (bewusst
# NICHT "pip download" dafür -- das hängt sich in diesem Image an einer
# isolierten Build-Umgebung auf, siehe SESSION.md).
# libchromaprint-tools liefert `fpcalc` (Song-Erkennung Phase 1, siehe
# python/song_fingerprint.py) -- nur die Rohdaten-Extraktion kommt von dort,
# das eigentliche Matching ist eigener Python-Code (siehe ARCHITECTURE.md).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 build-essential curl \
    libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir numpy silero-vad-lite vosk faster-whisper psutil mutagen

# aubio 0.4.9 (letztes PyPI-Release, 2019) fürs BPM-Tempo (Phase 3 der
# Musik-Library, siehe python/music_bpm.py) -- kein Wheel auf PyPI, UND
# baut nicht sauber gegen aktuelles numpy: PyUFuncGenericFunction
# erwartet seit numpy>=1.22 "const npy_intp*" statt "npy_intp*" in den
# ufunc-Callback-Signaturen, aubios gebündeltes python/ext/ufuncs.c
# wurde seit 2019 nicht angepasst -> "incompatible pointer type"-
# Compile-Fehler (live hier aufgetreten, siehe SESSION.md). Patch
# analog zu fix_silero_execstack.py (Workaround für eine Toolchain-/
# Library-Versionsinkompatibilität in einer Fremdbibliothek, nicht in
# diesem Projekt) -- zwei minimal-invasive sed-Ersetzungen auf den
# direkt von PyPI geladenen Source-Tarball. --no-build-isolation bei
# der Installation: nutzt das schon installierte numpy von oben direkt
# statt eine zweite, isolierte Build-Umgebung samt eigenem numpy-Download
# aufzusetzen (genau DAS hing sich mit "pip download"/Standard-Isolation
# hier auf, siehe SESSION.md).
RUN curl -fsSL -o /tmp/aubio.tar.gz \
       https://files.pythonhosted.org/packages/cd/80/302d89240603e5347c7f8026c8b02c59f8dfaec66c91a743d82de7c86006/aubio-0.4.9.tar.gz \
    && mkdir -p /tmp/aubio-src && tar xzf /tmp/aubio.tar.gz -C /tmp/aubio-src \
    && sed -i \
       -e 's/static void aubio_PyUFunc_d_d(char \*\*args, npy_intp \*dimensions,/static void aubio_PyUFunc_d_d(char **args, npy_intp const *dimensions,/' \
       -e 's/static void aubio_PyUFunc_f_f_As_d_d(char \*\*args, npy_intp \*dimensions,/static void aubio_PyUFunc_f_f_As_d_d(char **args, npy_intp const *dimensions,/' \
       -e 's/^                            npy_intp\* steps, void\* data)$/                            npy_intp const * steps, void* data)/' \
       /tmp/aubio-src/aubio-0.4.9/python/ext/ufuncs.c \
    && pip install --no-cache-dir --no-build-isolation /tmp/aubio-src/aubio-0.4.9 \
    && rm -rf /tmp/aubio.tar.gz /tmp/aubio-src

# silero-vad-lite's .so verlangt einen ausführbaren Stack, den der Kernel
# auf diesem Host beim dlopen() verweigert -> ohne Patch fällt die
# Spracherkennung dauerhaft auf die Heuristik zurück. Details/Reproduktion:
# siehe python/fix_silero_execstack.py.
COPY python/fix_silero_execstack.py .
RUN python3 fix_silero_execstack.py && rm fix_silero_execstack.py

ENV PYTHONUNBUFFERED=1

WORKDIR /app
# Alle .py-Module liegen auf dem Host unter python/ (siehe CLAUDE.md,
# "Host-Layout und Container-Layout") -- Container-intern bleibt trotzdem
# alles flach in /app/, nur die COPY-Quelle (links) folgt der Host-Struktur.
COPY python/radiosabbelnich.py .
COPY python/stream_source.py .
COPY python/fingerprint.py .
COPY python/song_fingerprint.py .
COPY python/speech_detector.py .
COPY python/ad_skip_prebuffer.py .
COPY python/webui.py .
COPY python/stations_store.py .
COPY python/settings_store.py .
COPY python/station_import.py .
COPY python/logging_setup.py .
COPY python/news_break.py .
COPY python/audio_tags.py .
COPY python/music_library.py .
COPY python/music_bpm.py .
COPY python/music_query.py .
COPY python/music_scan.py .
COPY python/folder_browse.py .
COPY python/stt_filter.py .
COPY python/i18n.py .
COPY python/resource_monitor.py .
COPY python/update_check.py .
COPY VERSION .
COPY data/stations.json .
COPY pics/radiosabbelnich.webp .
COPY web/qrcode.js .
COPY web/manifest.json .
COPY web/sw.js .
COPY pics/icon-192.png .
COPY pics/icon-512.png .
COPY pics/favicon.ico .
# Einzige Ausnahme von "jede Datei einzeln per COPY" oben: language/ wird
# als ganzer Ordner kopiert, weil i18n.py ihn zur Laufzeit per glob nach
# *.lng-Dateien durchsucht (siehe dortiges LANGUAGE_DIR) -- eine neue
# Sprachdatei soll durch bloßes Ablegen + Rebuild wirken, ohne zusätzlich
# eine eigene COPY-Zeile hier zu brauchen (leicht zu vergessen, und ohne
# sie fehlt die Sprache im Image lautlos statt mit einem Fehler beim Start).
COPY language/ language/

# ICECAST_URL wird beim Start via docker-compose environment gesetzt,
# z.B. icecast://source:PASSWORT@icecast-radiosabbelnich:8000/mix.mp3
# (Container-Name statt Hostname reicht, wenn beide im selben Compose-Netz sind)
# ICECAST_ADMIN_URL/-USER/-PASSWORD/-MOUNT versorgen das eingebettete
# Web-Interface (webui.py) mit Hörer-Daten aus Icecasts Admin-API.
# ICECAST_PUBLIC_PORT ist der Port, unter dem der Browser den Stream selbst
# erreicht (fürs eingebettete <audio>-Element) — kann vom containerinternen
# Icecast-Port abweichen. Der Webserver selbst lauscht containerintern fest
# auf 5000 (siehe docker-compose.yml für die host-seitige Portwahl über
# WEBUI_PORT).
#
# TLS_CERT_PATH/TLS_KEY_PATH sind Container-interne, feste Pfade (siehe
# docker-compose.yml: dorthin gemountet aus den Host-Pfaden TLS_CERT_FILE/
# TLS_KEY_FILE in .env, per Default /dev/null -- webui.start_server()
# erkennt eine leere/ungültige Datei selbst und fällt auf HTTP zurück).
# Der eigentliche Ein/Aus-Schalter ist "tls_enabled" in settings.json (per
# /config setzbar), nicht diese Pfade selbst -- siehe radiosabbelnich.py/main().
#
# Bewusst OHNE --verbose: die Logdatei unter logs/ schreibt ohnehin immer
# auf DEBUG-Niveau (VAD-Werte, Fingerprint-Details, HTTP-Requests), da muss
# `docker compose logs` nicht dieselbe Flut nochmal zeigen. Wer sie doch auf
# der Konsole haben will, hängt --verbose hier an.
# NEWS_MP3_FOLDER_HOST/VOSK_MODEL_FOLDER_HOST/MUSIC_LIBRARY_FOLDER_HOST sind
# rein informativ (siehe
# webui.py/make_handler()) -- dieselben .env-Werte, die docker-compose.yml
# schon für die eigentlichen Bind-Mounts nutzt, hier zusätzlich als
# Klartext-Umgebungsvariable durchgereicht, damit die Config-Seite den
# echten Host-Pfad anzeigen kann. Der Container kann ihn sonst grundsätzlich
# nicht kennen -- Docker übersetzt Host->Container-Pfad einmalig beim
# Start, danach ist das für den laufenden Prozess unsichtbar.
ENTRYPOINT ["sh", "-c", "python3 -u radiosabbelnich.py --icecast-url \"$ICECAST_URL\" --webui-port 5000 --icecast-admin-url \"$ICECAST_ADMIN_URL\" --icecast-admin-user \"$ICECAST_ADMIN_USER\" --icecast-admin-password \"$ICECAST_ADMIN_PASSWORD\" --icecast-mount \"${ICECAST_MOUNT:-/radiosabbelnich.mp3}\" --icecast-public-port \"${ICECAST_PUBLIC_PORT:-8000}\" --icecast-public-ssl-port \"${ICECAST_PUBLIC_SSL_PORT:-}\" --tls-cert-file \"${TLS_CERT_PATH:-}\" --tls-key-file \"${TLS_KEY_PATH:-}\" --news-mp3-folder-host \"${NEWS_MP3_FOLDER_HOST:-}\" --vosk-model-folder-host \"${VOSK_MODEL_FOLDER_HOST:-}\" --music-library-folder-host \"${MUSIC_LIBRARY_FOLDER_HOST:-}\""]
