# RadioZapper — Übergabe an Claude Code

## Was das Projekt macht
Python-Script (`radio_switch.py`) spielt Internetradio-Streams ab, erkennt
Moderation/Werbung/Jingles und schaltet automatisch auf den nächsten Sender
in `stations.json`. Läuft als Docker-Compose-Stack auf Dockfish (Debian,
Docker-Host), streamt via Icecast (`icecast-radioswitch`-Container) neu raus,
sodass man's per VLC im ganzen Tailnet hören kann.

## Architektur
- `radio_switch.py` — Hauptscript, Orchestrierung
- `speech_detector.py` — Sprache-Erkennung via Silero VAD (silero-vad-lite,
  16kHz intern, resampled automatisch von SAMPLE_RATE), Fallback auf
  Signal-Heuristik (`classify_window()` in radio_switch.py: ZCR/Flatness/
  Bass-Veto) falls VAD-Lib mal nicht lädt
- `fingerprint.py` — Shazam-artiges Audio-Fingerprinting (Constellation-Map
  Hashing) in SQLite, erkennt wiederkehrende Jingles/Werbespots und schaltet
  sofort, ohne auf die volle Sprache-Konsens-Zeit zu warten
- `webui.py` — eingebettetes Web-Interface (ThreadingHTTPServer im
  Hintergrund-Thread desselben Prozesses): zeigt aktuellen Sender + Hörer
  (IP/User-Agent/Verbindungsdauer via Icecast-Admin-API) und erlaubt
  manuellen Sender-Wechsel aus der stations.json-Liste heraus. Erreichbar
  auf Port 5000 (host-seitig via `WEBUI_PORT` in `.env` konfigurierbar).
  Details/Design-Entscheidungen: siehe SESSION.md, Eintrag 2026-08-02.
- `stations.json` — Senderliste, wird zur Laufzeit geladen (kein Code-Anfassen
  nötig), liegt im Docker-Volume-Mount
- Icecast + Radio-Switch laufen als zwei Services in `docker-compose.yml`,
  Credentials über `.env` (nicht `.env.example` committen als echte Werte!)

## Laufende Session-Dokumentation
Ab sofort (2026-08-02) wird die Detailarbeit chronologisch in `SESSION.md`
protokolliert — dort steht das *Wie und Warum* einzelner Schritte.
`HANDOVER.md` bleibt der High-Level-Architektur-/Status-Überblick.

## Aktueller Stand (funktioniert)
- VAD + Fingerprinting laufen sauber, Jingle-Erkennung bestätigt live getestet
- SAMPLE_RATE gerade von 22050 auf 44100 Hz angehoben (mehr Fidelity)
- 7 Sender in stations.json: Radio Bob, 1LIVE, SWR3, R.SH, Rock Antenne
  Hamburg, Hamburg Zwei, ffn (ffn-URL nicht 100% verifiziert, ggf. nochmal
  gegenchecken: https://stream.ffn.de/ffn/mp3-192 vs. http://player.ffn.de/ffn.mp3)

## Mono → Stereo (erledigt)
`StreamSource` startet jetzt einen ffmpeg-Prozess mit zwei `-map`-Outputs aus
derselben Quelle: Mono (s16le) auf `pipe:1` für die Analyse-Pipeline
(classify/VAD/Fingerprint) und Stereo (s16le) auf einer zweiten, per
`os.pipe()` + `pass_fds` durchgereichten Pipe fürs tatsächliche
Playback/Icecast-Encoding. `read_window()` liest beide Pipes parallel via
`select.select()` (verhindert Deadlocks, falls eine Pipe vollläuft während
auf die andere gewartet wird) und gibt `(mono, stereo)` zurück.
`IcecastOutput` und `LocalOutput` laufen jetzt mit `-ac 2` / `channels=2`.
Analyse (VAD/Heuristik/Fingerprint) bleibt bewusst auf Mono, spart
Rechenzeit ohne Erkennungsqualität zu verlieren.

Smoke-getestet: synthetische ffmpeg-Quelle (kein Deadlock, korrekte
Sample-Anzahl) sowie Live-Test gegen den SWR3-Stream — beide liefern
korrekt getrennte Mono-/Stereo-Fenster.

## Web-Interface (erledigt)
Neues Modul `webui.py`, läuft als `ThreadingHTTPServer` im selben Prozess
wie `radio_switch.py` (Hintergrund-Thread, geteilter Lock-geschützter
Zustand `SwitcherState` — kein separater Service, kein IPC nötig). Auf
Port 5000 (host-seitig via `WEBUI_PORT`):
- `GET /` — HTML/JS-Seite, pollt alle 5s
- `GET /api/status` — aktueller Sender, alle Sender aus stations.json,
  Hörer-Liste (IP/User-Agent/Verbindungsdauer)
- `POST /api/switch {"index": N}` — manueller Sender-Wechsel; Hauptloop
  greift den Request beim nächsten Analysefenster ab und springt sofort
  (ohne erst auf "Musik läuft" zu warten wie beim Auto-Switch)

Hörer-Daten kommen von Icecasts Admin-API
(`GET /admin/listclients?mount=/radiozapper.mp3`, Basic-Auth mit den
ICECAST_ADMIN_*-Credentials). End-to-End getestet inkl. echtem verbundenen
Hörer im Tailnet. Details siehe SESSION.md, Eintrag 2026-08-02.

## Offene Punkte / bekannte Einschränkungen
- ffn-Stream-URL noch nicht live verifiziert
- Fingerprint-DB (`fingerprints.db`) ist noch jung, braucht ein paar Tage
  Laufzeit um wiederkehrende Jingles/Ads zuverlässig gelernt zu haben
- Silero VAD Diskriminierung (Sprache vs. Musik vs. Werbung-mit-Musikbett)
  wurde nur live auf Dockfish getestet, nicht in einer kontrollierten
  Testumgebung — falls Fehlklassifikationen auftreten, `--verbose` Logs
  sammeln (zeigt `[vad] mean_prob=... speech_ratio=...` Zeilen)
- Silero VAD lädt aktuell im Container gar nicht (`cannot enable
  executable stack as shared object requires`), läuft seit dem letzten
  Rebuild nur noch auf dem Heuristik-Fallback. Noch nicht untersucht —
  vermutlich Kernel-Hardening auf dem Docker-Host oder eine geänderte
  `silero-vad-lite`-Wheel. Sollte als nächstes angeschaut werden, sonst
  läuft die Spracherkennung dauerhaft auf der schwächeren Heuristik.
- Web-Interface hat keinerlei Auth — im Tailnet vertretbar, aber falls der
  Port mal breiter exponiert wird, vorher absichern.

## Deploy-Befehl
```bash
cd /opt/docker/RadioZapper
docker compose up -d --build radio-switch
docker compose logs -f radio-switch
```
