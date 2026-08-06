FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir numpy silero-vad-lite vosk faster-whisper psutil

# silero-vad-lite's .so verlangt einen ausführbaren Stack, den der Kernel
# auf diesem Host beim dlopen() verweigert -> ohne Patch fällt die
# Spracherkennung dauerhaft auf die Heuristik zurück. Details/Reproduktion:
# siehe fix_silero_execstack.py.
COPY fix_silero_execstack.py .
RUN python3 fix_silero_execstack.py && rm fix_silero_execstack.py

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY radiozapper.py .
COPY fingerprint.py .
COPY speech_detector.py .
COPY webui.py .
COPY stations_store.py .
COPY settings_store.py .
COPY station_import.py .
COPY logging_setup.py .
COPY news_break.py .
COPY stt_filter.py .
COPY i18n.py .
COPY resource_monitor.py .
COPY VERSION .
COPY data/stations.json .
COPY pics/radiozapper.webp .
COPY web/qrcode.js .
COPY web/manifest.json .
COPY web/sw.js .
COPY pics/icon-192.png .
COPY pics/icon-512.png .
COPY pics/favicon.ico .

# ICECAST_URL wird beim Start via docker-compose environment gesetzt,
# z.B. icecast://source:PASSWORT@icecast-radiozapper:8000/mix.mp3
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
# /config setzbar), nicht diese Pfade selbst -- siehe radiozapper.py/main().
#
# Bewusst OHNE --verbose: die Logdatei unter logs/ schreibt ohnehin immer
# auf DEBUG-Niveau (VAD-Werte, Fingerprint-Details, HTTP-Requests), da muss
# `docker compose logs` nicht dieselbe Flut nochmal zeigen. Wer sie doch auf
# der Konsole haben will, hängt --verbose hier an.
# NEWS_MP3_FOLDER_HOST/VOSK_MODEL_FOLDER_HOST sind rein informativ (siehe
# webui.py/make_handler()) -- dieselben .env-Werte, die docker-compose.yml
# schon für die eigentlichen Bind-Mounts nutzt, hier zusätzlich als
# Klartext-Umgebungsvariable durchgereicht, damit die Config-Seite den
# echten Host-Pfad anzeigen kann. Der Container kann ihn sonst grundsätzlich
# nicht kennen -- Docker übersetzt Host->Container-Pfad einmalig beim
# Start, danach ist das für den laufenden Prozess unsichtbar.
ENTRYPOINT ["sh", "-c", "python3 -u radiozapper.py --icecast-url \"$ICECAST_URL\" --webui-port 5000 --icecast-admin-url \"$ICECAST_ADMIN_URL\" --icecast-admin-user \"$ICECAST_ADMIN_USER\" --icecast-admin-password \"$ICECAST_ADMIN_PASSWORD\" --icecast-mount \"${ICECAST_MOUNT:-/radiozapper.mp3}\" --icecast-public-port \"${ICECAST_PUBLIC_PORT:-8000}\" --icecast-public-ssl-port \"${ICECAST_PUBLIC_SSL_PORT:-}\" --tls-cert-file \"${TLS_CERT_PATH:-}\" --tls-key-file \"${TLS_KEY_PATH:-}\" --news-mp3-folder-host \"${NEWS_MP3_FOLDER_HOST:-}\" --vosk-model-folder-host \"${VOSK_MODEL_FOLDER_HOST:-}\""]
