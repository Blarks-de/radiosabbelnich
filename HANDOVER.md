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
  (IP/User-Agent/Verbindungsdauer via Icecast-Admin-API), erlaubt manuellen
  Sender-Wechsel, und unter `/config` die volle Sender-Verwaltung (siehe
  unten). Erreichbar auf Port 5000 (host-seitig via `WEBUI_PORT` in `.env`
  konfigurierbar). Details/Design-Entscheidungen: siehe SESSION.md,
  Einträge 2026-08-02.
- `stations_store.py` — alleinige Quelle der Wahrheit für `stations.json`
  (Schema: `{id, name, url, category, enabled}`), CRUD-Funktionen mit
  Datei-Lock, gemeinsam genutzt von `radio_switch.py` (Playback-Rotation
  über `load_active()`) und `webui.py` (Config-Seite über `load_all()` +
  add/update/set_enabled/delete). Migriert alte `stations.json`-Dateien
  ohne diese Felder transparent beim ersten Laden.
- `stations.json` — Senderliste, liegt im Docker-Volume-Mount (einzelne
  Datei gebindmountet — deshalb schreibt `stations_store.py` direkt in
  die Datei statt atomar per temp+rename, siehe SESSION.md)
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
- `POST /api/switch {"id": "..."}` — manueller Sender-Wechsel; Hauptloop
  greift den Request beim nächsten Analysefenster ab und springt sofort
  (ohne erst auf "Musik läuft" zu warten wie beim Auto-Switch)

Hörer-Daten kommen von Icecasts Admin-API
(`GET /admin/listclients?mount=/radiozapper.mp3`, Basic-Auth mit den
ICECAST_ADMIN_*-Credentials). End-to-End getestet inkl. echtem verbundenen
Hörer im Tailnet. Details siehe SESSION.md, Eintrag 2026-08-02.

Seite hat außerdem einen eingebetteten `<audio>`-Player (Quelle wird
clientseitig aus `location.hostname` + servergelieferten `stream_port`/
`stream_mount` gebaut, einmalig gesetzt, nicht bei jedem Poll) — man kann
also auf `:5000/` gleichzeitig hören und umschalten. Der rohe Icecast-Port
8000 bleibt parallel bestehen (VLC im Tailnet etc.).

## Config-Seite: Sender verwalten (erledigt)
`/config` — volle CRUD-Verwaltung der Senderliste, gruppiert nach
Lokal/Regional/National/International/Global/Interstellar (fixe
Reihenfolge, auch leere Kategorien werden angezeigt), pro Sender ein
Haken zum Aktivieren/Deaktivieren, Bearbeiten/Löschen-Buttons, Formular
für neue Sender unten. Alphabetisch sortiert innerhalb jeder Kategorie.

Größter struktureller Umbau bisher: `radio_switch.py` referenziert Sender
jetzt über eine stabile `id` statt über eine Listen-Position — nötig,
damit Hinzufügen/Löschen/(De-)Aktivieren über die Config-Seite die
laufende Wiedergabe nicht durcheinanderbringt. Änderungen wirken sich
**live** aus (kein Neustart nötig): die Config-Seite setzt nach jeder
Änderung ein Reload-Flag, der Hauptloop pollt es einmal pro
Analysefenster und lädt bei Bedarf neu. Wird der *gerade laufende*
Sender deaktiviert/gelöscht, schaltet der Player automatisch auf den
ersten verbleibenden aktiven Sender — getestet und bestätigt (Log:
`⚙ Senderliste geändert, aktueller Sender nicht mehr aktiv — schalte
auf: ...`). Sind gar keine Sender mehr aktiv, pausiert die Rotation
(kein Crash), bis wieder einer aktiviert wird.

Bestehende 7 Sender wurden nachträglich kategorisiert: Lokal (Rock
Antenne Hamburg, Hamburg Zwei), Regional (SWR3, R.SH, ffn), National
(Radio Bob, 1LIVE). International/Global/Interstellar sind noch leer.

Details/Debugging-Verlauf (inkl. zweier gefundener Bugs beim ersten
Deploy — Bind-Mount-Problem bei atomarem Schreiben, veralteter Cache auf
der Config-Seite): SESSION.md, Eintrag 2026-08-02 (Fortsetzung).

## Offene Punkte / bekannte Einschränkungen
- ffn-Stream-URL noch nicht live verifiziert
- Fingerprint-DB (`fingerprints.db`) ist noch jung, braucht ein paar Tage
  Laufzeit um wiederkehrende Jingles/Ads zuverlässig gelernt zu haben
- Silero VAD Diskriminierung (Sprache vs. Musik vs. Werbung-mit-Musikbett)
  wurde nur live auf Dockfish getestet, nicht in einer kontrollierten
  Testumgebung — falls Fehlklassifikationen auftreten, `--verbose` Logs
  sammeln (zeigt `[vad] mean_prob=... speech_ratio=...` Zeilen)
- Web-Interface hat keinerlei Auth — im Tailnet vertretbar, aber falls der
  Port mal breiter exponiert wird, vorher absichern.
- Icecast loggt beim Start `Couldn't find group "icecast2" in groups
  file` / `Cannot open mime types file /etc/mime.types` — harmlose
  Altlasten aus dem icegen-Image-Template, nicht behoben (kein
  funktionaler Effekt).

## Silero VAD Ladefehler + Switch-Zuverlässigkeit (erledigt)
Zwei User-gemeldete Probleme, beide auf konkrete Bugs zurückgeführt:

**"Nachrichten werden nicht erkannt"**: Silero VAD lud nie (`cannot enable
executable stack as shared object requires`) -> lief seit dem allerersten
Rebuild nur auf dem viel unempfindlicheren Heuristik-Fallback. Ursache:
`silero_vad_lite.so` verlangt einen ausführbaren Stack (PT_GNU_STACK/PF_X),
der Kernel auf Dockfish verweigert das beim `dlopen()` — reproduzierbar in
jedem Container auf diesem Host, nicht compose-spezifisch. Fix: neues
`fix_silero_execstack.py` patcht das PF_X-Bit direkt in der ELF-Datei
(reines Python, `execstack`-Paket gibt's in aktuellen Debian-Repos nicht
mehr), läuft als Build-Step im Dockerfile. Verifiziert: VAD lädt jetzt,
erkennt Sprache live korrekt (`speech_ratio=1.00 -> SPEECH`), Fingerprint-
Match + Auto-Switch danach bestätigt funktionierend.

**"Manuelles Zappen dauert ewig"**: drei zusammenhängende Lücken in
`radio_switch.py` gefunden und gefixt —
1. `StreamSource.read_window()` hatte keinen Timeout -> eine hängende
   Zielstation blockierte den kompletten Hauptloop unbegrenzt, inklusive
   aller weiteren manuellen Switch-Versuche. Jetzt: `STREAM_READ_TIMEOUT`
   (8s) über eine Deadline in `select()`.
2. `do_switch()` (automatisches Durchprobieren) ignorierte eingehende
   manuelle Requests bis zu ~66s lang. Prüft jetzt bei jedem Skip auf
   einen pending Request und bricht sofort ab (Request wird zurückgelegt,
   nicht verworfen — Hauptloop übernimmt den Wechsel).
3. `IcecastOutput` hatte keine Reconnect-Logik — nach einem Broken Pipe
   (z.B. Icecast-Container-Neustart) blieb der Broadcast für den Rest der
   Prozess-Laufzeit stumm, auch wenn der Hauptloop weiter brav Sender
   wechselte. Jetzt: automatischer Reconnect-Versuch bei jedem
   Schreibfehler (5s Cooldown gegen Popen-Spam).

Details/Debugging-Verlauf: SESSION.md, Eintrag 2026-08-02 (Fortsetzung).

## Icecast Location/Admin (erledigt)
`<location>` und `<admin>` fehlten im von `icegen` generierten
`/app/icecast.xml` komplett (nicht editierbar über icegen-Flags) ->
Icecast lief auf den Defaults "Earth"/"icemaster@localhost". Fix:
`docker-compose.yml` überschreibt Entrypoint/Command des `icecast`-Service,
patcht `icecast.xml` nach dem Generieren per `sed` (Werte aus
`ICECAST_LOCATION`/`ICECAST_ADMIN_EMAIL` in `.env`, aktuell Hamburg /
blarks@gmail.com). Dabei einen Bug im Image gefunden: `icegen new`
überschreibt eine vorhandene `icecast.xml` nicht, sondern hängt an ->
ohne `rm -f icecast.xml` vor jedem Lauf hätte jeder Container-Neustart zu
ungültigem XML und Absturzschleife geführt. Details: SESSION.md,
Eintrag 2026-08-02.
**Wichtig für künftige Icecast-Änderungen:** Neuerstellen des
`icecast`-Containers kappt die Source-Verbindung von `radio-switch`
(kein Auto-Reconnect) — danach immer auch `radio-switch` neustarten.

## Deploy-Befehl
```bash
cd /opt/docker/RadioZapper
docker compose up -d --build radio-switch
docker compose logs -f radio-switch
```
