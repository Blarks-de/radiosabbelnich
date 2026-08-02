FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir numpy silero-vad-lite

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
COPY stations.json .
COPY radiozapper.webp .

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
ENTRYPOINT ["sh", "-c", "python3 -u radiozapper.py --icecast-url \"$ICECAST_URL\" --webui-port 5000 --icecast-admin-url \"$ICECAST_ADMIN_URL\" --icecast-admin-user \"$ICECAST_ADMIN_USER\" --icecast-admin-password \"$ICECAST_ADMIN_PASSWORD\" --icecast-mount \"${ICECAST_MOUNT:-/radiozapper.mp3}\" --icecast-public-port \"${ICECAST_PUBLIC_PORT:-8000}\" --verbose"]
