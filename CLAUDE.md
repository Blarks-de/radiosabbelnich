# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

RadioZapper hört mehrere Internetradio-Sender mit, schaltet bei Sprache
(Moderation/Werbung/Jingles) automatisch weiter und strahlt das Ergebnis per
Icecast neu aus. Überblick und Feature-Beschreibung: `README.md`.

## Sprache und Konventionen

- **Alles auf Deutsch**: Kommentare, Docstrings, Log-Meldungen, UI-Texte,
  README, SESSION.md. Neue Beiträge genauso.
- Kommentare erklären **warum**, nicht was — insbesondere bei allem, wo
  ein naheliegender Ansatz nachweislich nicht funktioniert hat (z.B. warum
  `_write()` in `stations_store.py` kein write-temp-then-rename macht, warum
  der Import-Check nicht per ffprobe läuft). Diese Begründungen sind hart
  erarbeitet; nicht wegkürzen.
- **`SESSION.md` ist append-only**: pro Arbeitseinheit ein neuer Eintrag am
  Ende (Datum, Auslöser, Umsetzung, "Verifiziert", ggf. "bewusst NICHT
  gemacht"). Ältere Einträge werden nicht rückwirkend korrigiert. Dort steht
  das Wie und Warum, in der README das Was.
- Commit-Messages: die neueren sind Englisch, ältere Deutsch — am jeweils
  letzten Commit orientieren.

## Betrieb und Deployment

Es gibt **kein Test-Framework, keine Linter-Config, keine CI**. Verifikation
läuft über die unten beschriebenen manuellen Muster und wird in SESSION.md
protokolliert.

```bash
docker compose up -d --build radiozapper   # bauen + neustarten (Standard-Zyklus)
docker compose logs -f radiozapper         # Konsole: nur Ereignisse (INFO)
tail -f logs/radiozapper.log               # Volles DEBUG-Log, überlebt Neustarts
```

Ein frischer Clone braucht `cp env.example .env` **und `touch fingerprints.db`**:
die DB ist als einzelne Datei gebindmountet und gitignored — fehlt sie, legt
Docker ein Verzeichnis an und SQLite scheitert in einer Neustartschleife.

Das Web-Interface läuft auf Port 5000, Icecast auf 8000 (siehe `.env`).
Änderungen an `stations.json`/`settings.json` wirken **ohne Neustart** (der
Hauptloop lädt neu), Code-Änderungen brauchen einen Rebuild.

### Testen ohne das laufende Deployment anzufassen

Bewährtes Muster (in SESSION.md mehrfach dokumentiert): `*.py` in ein
Temp-Verzeichnis kopieren, dort eine eigene `stations.json`/`settings.json`
anlegen und gegen einen **separaten** Icecast-Mount streamen — der Hauptloop
schreibt sonst in die echte Senderliste und den echten Mount.

```bash
python3 radiozapper.py --icecast-url "icecast://source:PASS@localhost:8000/test.mp3" \
    --no-fingerprint --webui-port 0 --log-file logs/test.log
```

Auf dem Host ist `numpy` vorhanden, `silero-vad-lite` **nicht** — lokal läuft
also immer die Signal-Heuristik statt VAD. Wer VAD testen will, muss in den
Container.

Für Live-Tests am echten Deployment gibt es die API (`/api/config/stations`,
`/api/switch`, `/api/config/import/start`, …). Dabei angelegte Test-Sender
hinterher wieder löschen und geänderte Settings zurücksetzen — die
Senderliste ist Produktivzustand des Nutzers.

## Architektur: das Bild über mehrere Dateien hinweg

### Ein Prozess, zwei Akteure, geteilter Zustand

`radiozapper.main()` fährt den Hauptloop (~1 Analysefenster pro Sekunde);
`webui.start_server()` hängt einen `ThreadingHTTPServer` als Daemon-Thread
daneben. Kommunikation läuft **ausschließlich** über `webui.SwitcherState`:
lock-geschützter In-Memory-Zustand, kein IPC, kein Datei-Polling.

Das Muster ist durchgehend **request/pop**: der Webserver setzt ein Flag
(`request_switch`, `request_reload`, `request_skip`, `request_filter_toggle`),
der Hauptloop holt es einmal pro Durchlauf ab (`pop_*`) und führt die Aktion
aus. Grund: nur der Hauptloop darf `source`/`current` und die Streak-
Buchhaltung anfassen — würde der Webserver-Thread direkt umschalten, wären
Puffer-Übergabe und Sprach-Streak inkonsistent. Neue UI-Aktionen, die den
Player betreffen, gehören in dasselbe Muster.

`stations.json` (via `stations_store`) und `settings.json` (via
`settings_store`) sind die Quelle der Wahrheit; `SwitcherState` ist nur ein
Cache für die Rotation. Deshalb liest die Config-Seite bewusst frisch von der
Platte (`GET /api/config/stations` → `stations_store.load_all()`), während der
Hauptloop erst beim nächsten `pop_reload_request()` nachzieht.

Sender werden **immer über ihre stabile `id`** referenziert, nie über eine
Listenposition. Rotationsreihenfolge = aktivierte Sender alphabetisch nach
Name. Hinzufügen/Löschen/Deaktivieren darf die laufende Wiedergabe nicht
durcheinanderbringen.

### Audio-Pfad: ein ffmpeg, zwei Pipes

`StreamSource.start()` startet **einen** ffmpeg-Prozess mit zwei Ausgängen:
Mono nach `pipe:1` für die Analyse (VAD/Heuristik/Fingerprint) und Stereo über
eine zusätzliche Pipe fürs Playback. `read_window()` liest beide per `select`
parallel — läuft eine Pipe voll, blockiert ffmpeg und die andere bekommt auch
nichts mehr. Der Timeout dort sorgt dafür, dass eine tote Quelle nicht den
einzelnen Read blockiert (gegen die *Endlosschleife* drumherum hilft der
Watchdog, siehe unten).

`IcecastOutput` besteht über Senderwechsel hinweg — nur die `StreamSource`
wird getauscht, der Hörer merkt keinen Verbindungsabbruch. Analyse ist Mono,
Ausstrahlung Stereo; wer am Audio-Pfad arbeitet, muss beide Seiten bedienen.

### Prebuffering: Quellen wandern, nicht Daten

`PrebufferedSource` hält pro Sender eine eigene `StreamSource` plus Reader-
Thread und einen Ringpuffer der letzten Sekunden. Beim Wechsel gibt
`promote()` sowohl die gepufferten Samples als auch die **weiterlaufende
Quelle** zurück, die der Hauptloop übernimmt.

Zwei harte Regeln:

1. **Eine Pipe hat genau einen Leser.** `promote()`/`stop()` joinen den
   Reader-Thread; überlebt er den Join, wird die Quelle als `dead` markiert
   und verworfen statt übernommen.
2. **`pb.stop()` blockiert den Hauptloop** (bis ~9 s pro Quelle, weil es auf
   ein laufendes `read_window()` wartet). `sync_prebuffer()` läuft einmal pro
   Durchlauf — dort keine weiteren blockierenden Operationen einbauen.

`sync_prebuffer()` startet gestorbene Puffer **nicht** selbst neu, sondern gibt
deren IDs zurück; der Hauptloop sperrt sie. Sonst gäbe es bei einer dauerhaft
toten URL einen ffmpeg-Spawn pro Sekunde.

### Watchdog gegen tote Sender

`dead_until` (id → Ablaufzeitpunkt) im Hauptloop, gespeist aus drei Quellen:
`STREAM_FAILURE_LIMIT` leere Reads des aktuellen Senders, ein im Hintergrund
gestorbener Puffer, ein Kandidat der beim Durchprobieren nichts liefert.
`alive_stations()` filtert gesperrte Sender aus Rotation und Pufferzielen;
`keep_id` hält den laufenden Sender drin, damit nicht alle Pufferpositionen
verrutschen. Manuelles Umschalten hebt die Sperre auf, und sind *alle* Sender
gesperrt, werden die Sperren verworfen statt hängenzubleiben.

Hintergrund: ohne das legte ein einziger toter Sender den Player für 8,5
Stunden still (Details in SESSION.md). Wer an Switch-Logik oder Pufferung
arbeitet, muss diese Pfade mitdenken.

### Fingerprinting

`fingerprint.py` lernt jeden Sprach-Clip, der keinen Treffer erzeugt — die DB
wächst also im Betrieb. Matching per Constellation-Map mit Delta-Konsistenz;
`MIN_HASH_MATCHES` trennt echte Treffer (hunderte) von Zufallstreffern
(gemessen ≤7). Die `FingerprintDB`-Connection gehört dem Hauptloop;
`delete_clip()`/`clear_all()` öffnen aus dem Webserver-Thread bewusst eigene
kurze Connections (sqlite3-Connections sind nicht thread-übergreifend sicher).

### Logging

`logging_setup.setup()` wird **in `main()`** aufgerufen, also nach dem
Modul-Import. Log-Aufrufe auf Modulebene (z.B. beim Banner-Laden in
`webui.py`) landen deshalb noch beim `lastResort`-Handler und nicht in der
Datei — beim Hinzufügen von Modul-Level-Logging beachten.

Konsole = INFO (mit `--verbose` DEBUG), Datei = **immer** DEBUG, rotierend.
Neue Diagnose-Ausgaben gehören auf DEBUG: die Datei ist dafür da, dass ein
Vorfall im Nachhinein rekonstruierbar ist, ohne den Container vorher zufällig
im richtigen Modus gestartet zu haben. Loggen mit `%s`-Platzhaltern, nicht mit
f-Strings.

### Sender-Import

`station_import.check_reachable()` prüft nicht "kommt Audio", sondern "kommt am
**Ende** eines Zeitfensters noch Audio" — DASH/HLS-Quellen schütten sonst einen
Fragment-Vorrat aus, bestehen jeden kurzen Check und verstummen danach für
immer. Importierte Sender landen **deaktiviert** in "Unsortiert".

## Docker-Besonderheiten

- `stations.json`, `settings.json` und `fingerprints.db` sind als **einzelne
  Dateien** gebindmountet. Deshalb schreibt `stations_store._write()` direkt
  statt über `os.replace()` — ein Rename über einen Mountpoint scheitert mit
  "Device or resource busy". Nicht auf "atomares Schreiben" umbauen.
- Der Dockerfile kopiert jede `.py`-Datei **einzeln**: neue Module dort
  eintragen, sonst fehlen sie im Image.
- `fix_silero_execstack.py` patcht zur Build-Zeit das PT_GNU_STACK-Bit der
  silero-vad-lite-`.so`. Ohne den Patch verweigert der Kernel dieses Hosts das
  `dlopen()` und die Spracherkennung fällt dauerhaft auf die Heuristik zurück.
- Der Icecast-Service überschreibt den Entrypoint des Basis-Images (`icegen`
  kennt `<location>`/`<admin>` nicht, und ohne `rm -f icecast.xml` hängt es bei
  jedem Neustart eine zweite Kopie an → ungültiges XML, Absturzschleife).

## Kein Auth, nur hinter VPN

Web-Interface und Config-Seite haben keinerlei Authentifizierung, und der
Restream ist urheberrechtlich nur privat tragbar. Keine Änderungen vorschlagen
oder umsetzen, die auf öffentliche Erreichbarkeit hinauslaufen (Port-
Forwarding, öffentlicher Reverse-Proxy) — siehe Warnung in der README.

## Bekannte offene Punkte

Aktueller Stand am Ende von `SESSION.md` (Abschnitt "bewusst NICHT in diesem
Durchgang"), u.a.: der Puffer-Burst beim Wechsel schiebt bis zu
`prebuffer_seconds` Audio auf einen Schlag in den Encoder (Hörer rutschen pro
Wechsel hinter Live), `sync_prebuffer()`/`pb.stop()` können den Hauptloop
blockieren, das Web-Interface zeigt keinen Stream-Health-Status, und
`SpeechDetector.leftover` wird beim Senderwechsel nicht zurückgesetzt.
