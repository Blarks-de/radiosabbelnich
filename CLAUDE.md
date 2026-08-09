# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

RadioSabbelNich hört mehrere Internetradio-Sender mit, schaltet bei Sprache
(Moderation/Werbung/Jingles) automatisch weiter und strahlt das Ergebnis per
Icecast neu aus. Überblick und Feature-Beschreibung: `README.md`.

## Android-Prototyp (separates Projekt)

`android-app/` ist ein eigenständiges natives Kotlin/Gradle-Projekt (media3 +
Vosk-Android), das dasselbe Grundprinzip lokal auf dem Handy nachbildet — ein
komplett anderer Tech-Stack, eigene Toolchain, eigenes `README.md` dort. Es
läuft unabhängig von der hier beschriebenen Docker-Instanz und ändert nichts
an deren Architektur/Verhalten; die obigen Konventionen (Deutsch, SESSION.md,
VERSION-Pflege) gelten für den Docker-Dienst, nicht 1:1 für den Android-Code.
Seit 2026-08-07 wird es aber mitgepflegt, dafür zwei feste Regeln:

- **Nach jedem Android-Build** die entstandene Debug-APK lokal nach
  `android-app/radiosabbelnich.apk` kopieren (fester, einfach auffindbarer
  Pfad statt des tief verschachtelten
  `app/build/outputs/apk/debug/app-debug.apk` — letzterer ist ohnehin
  gitignored) UND zusätzlich mit Zeitstempel im Dateinamen
  (`radiosabbelnich-YYYYMMDD-HHMMSS.apk`) sowie ein passendes
  `version.json` (`{"buildTime": "...", "apkFile": "..."}`) nach
  `blarks.de/update_radiosabbelnich/` hochladen (siehe
  `android-app/README.md`, Abschnitt "Update-Mechanismus" — sonst hält
  die App den alten Stand weiterhin für aktuell).
- **`android-app/README.md` bei jeder inhaltlichen Änderung an der App
  nachziehen** (analog zur README-Pflicht des Docker-Projekts oben, nur
  eben für die App statt den Dienst).

Der Update-Mechanismus lief bis 2026-08-08 über einen eigenständigen
lokalen Server (`update_server.py` + systemd-Service, nur übers
Tailscale-Netz erreichbar) — seitdem stattdessen über den ganz normalen,
öffentlich erreichbaren Webserver von `blarks.de`
(`/srv/www/blarks.de/update_radiosabbelnich/`, statisches Verzeichnis,
kein eigener Server-Prozess mehr nötig). Bewusste Ausnahme von "Kein Auth,
nur hinter VPN" unten: verteilt nur eine App-Binary ohne Nutzerdaten,
Details und Abwägung in `android-app/SESSION.md`.

## Sprache und Konventionen

- **Alles auf Deutsch**: Kommentare, Docstrings, Log-Meldungen, UI-Texte,
  README, SESSION.md. Neue Beiträge genauso.
- Kommentare erklären **warum**, nicht was — insbesondere bei allem, wo
  ein naheliegender Ansatz nachweislich nicht funktioniert hat (z.B. warum
  `_write()` in `stations_store.py` kein write-temp-then-rename macht, warum
  der Import-Check nicht per ffprobe läuft). Diese Begründungen sind hart
  erarbeitet; nicht wegkürzen.
- **Doku gehört zur Änderung, nicht danach.** Vor jedem Commit beide Dateien
  nachziehen:
  - **`SESSION.md`** ist append-only: pro Arbeitseinheit ein neuer Eintrag am
    Ende (Datum, Auslöser, Umsetzung, "Verifiziert" mit echten Messwerten,
    ggf. "bewusst NICHT gemacht"). Ältere Einträge werden **nicht**
    rückwirkend korrigiert — was sich später als überholt herausstellt, wird
    im neuen Eintrag richtiggestellt. Hier steht das *Wie und Warum*.
  - **`README.md`** beschreibt den *aktuellen Stand* für Nutzer: alles, was
    Verhalten, Setup, Bedienung, Konfigurationswerte oder die Datei-Tabelle
    verändert, muss dort mitgezogen werden (keine Historie, keine
    Doppelung von SESSION.md).
  - **`CLAUDE.md`** (diese Datei) nachziehen, wenn sich Architektur,
    Invarianten oder Arbeitsabläufe ändern — inklusive der Liste offener
    Punkte unten.
- Commit-Messages: die neueren sind Englisch, ältere Deutsch — am jeweils
  letzten Commit orientieren.
- **Versionspflege (seit 2026-08-06)**: `VERSION` am Repo-Root, Format
  `vMAJOR.MINOR.PATCH build YYYY-MM-DD HH:MM Uhr`. Start war `v1.0.0`.
  Jede Änderung, die committet wird, erhöht PATCH um `+0.0.1` und trägt
  Datum/Uhrzeit des Commits nach, bis der Nutzer explizit etwas anderes
  vorgibt (z.B. einen MINOR/MAJOR-Sprung). Vor jedem Commit prüfen/
  nachziehen, wie bei SESSION.md/README.md oben.

## Betrieb und Deployment

Es gibt **kein Test-Framework, keine Linter-Config, keine CI**. Verifikation
läuft über die unten beschriebenen manuellen Muster und wird in SESSION.md
protokolliert.

```bash
docker compose up -d --build radiosabbelnich   # bauen + neustarten (Standard-Zyklus)
docker compose logs -f radiosabbelnich         # Konsole: nur Ereignisse (INFO)
tail -f data/logs/radiosabbelnich.log          # Volles DEBUG-Log, überlebt Neustarts
```

Ein frischer Clone braucht `cp env.example .env` **und `touch data/fingerprints.db`**:
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
python3 radiosabbelnich.py --icecast-url "icecast://source:PASS@localhost:8000/test.mp3" \
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

`radiosabbelnich.main()` fährt den Hauptloop (~1 Analysefenster pro Sekunde);
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

Nicht jeder Datenfluss zwischen Hauptloop und Webserver ist request/pop —
reine Status-/Info-Werte, die nur der Hauptloop kennt und die Web-UI nur
anzeigt (kein Auslösen einer Aktion), laufen als einfache
Setter/Property auf `SwitcherState` in die andere Richtung: `set_stt_status()`
(Fingerprint/VAD-Analogon fehlt hier bewusst, siehe stt_filter.py-Abschnitt)
und `set_speech_probability()` (Rohwert der Klassifikation vor der STT-
Verknüpfung, fürs "Bullshitometer" auf der Startseite — Web-Interface
zeigt ihn nur an, ändert nie etwas daran). `host_paths` (Web-Interface-
Konstruktor-Parameter, NICHT SwitcherState) ist ein dritter, noch
einfacherer Fall: rein statische Werte aus `.env` (`NEWS_MP3_FOLDER_HOST`/
`VOSK_MODEL_FOLDER_HOST`), einmalig beim Start durchgereicht (Env-Var →
CLI-Arg in `radiosabbelnich.py` → `webui.start_server()`), damit die Config-
Seite den echten Host-Pfad neben dem Container-Pfad anzeigen kann — der
Container kennt ihn sonst grundsätzlich nicht, Docker übersetzt Host→
Container-Pfad nur einmalig beim Anlegen des Containers, das ist für den
laufenden Prozess selbst unsichtbar. Deshalb kein Auswahl-/Browse-Dialog
dafür: der könnte ohnehin nur zeigen, was im Container selbst sichtbar
ist (also wieder nur Container-Pfade) — echten Host-Zugriff gäbe es nur
über volle Host-Filesystem- oder Docker-Socket-Freigabe, beides ein
Sicherheitsrückschritt angesichts des Auth-losen Web-Interfaces (siehe
"Kein Auth, nur hinter VPN" unten).

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

### Prebuffering + Playout-Delay: Quellen wandern, nicht Daten

`PrebufferedSource` hält pro Sender eine eigene `StreamSource` plus Reader-
Thread und einen Ringpuffer der letzten `prebuffer_seconds` Sekunden
(Fenster-genau: zwei parallele Deques mit `maxlen`, kein konkateniertes
Array). Beim Wechsel gibt `promote()` sowohl die gepufferte Fenster-Liste
(`[(mono, stereo), ...]`, älteste zuerst) als auch die **weiterlaufende
Quelle** zurück, die der Hauptloop übernimmt.

Seit 2026-08-06 ist dieser Puffer nicht mehr nur ein Vorrat für die
Wechsel-Übergabe, sondern gleichzeitig ein echtes **Playout-Delay** für
den GERADE laufenden Sender: `main()` hält dafür eine eigene Deque
(`playout`, nicht dieselbe wie die der Hintergrund-Kandidaten), die pro
Durchlauf über `push_and_drain()` bedient wird — ein frisch gelesenes
Fenster hinten anhängen, klassifizieren (das passiert dadurch VOR der
Ausgabe, nicht danach), und nur falls die Deque über `prebuffer_seconds`
hinausgewachsen ist, das älteste Fenster abziehen und ausgeben. Ein Push,
höchstens ein Pop pro Durchlauf — dadurch bleibt die Ausgabe im selben
Realzeit-Takt wie vorher, keine Schübe.

Ein Wechsel zu einem vorgewärmten Kandidaten übernimmt dessen komplette
Fenster-Liste **auf einen Schlag** als neue `playout`-Deque
(`adopt_windows()`) — die Deque steht damit sofort auf Zieltiefe, Drain
läuft ab dem nächsten Fenster ohne Lücke weiter, kein Bridge-Timing nötig
(die alte `promote_bridge()`/`stereo_tail()`-Mechanik ist damit
komplett entfallen). Ein Wechsel zu einem NICHT vorgewärmten Sender
(`reset_playout()`) schaltet stattdessen auf reinen Passthrough (kein
Delay, sofortige Ausgabe wie vor 2026-08-06) — ein lückenloser Übergang
von 0 auf volle Verzögerung ist ohne Zeitdehnung nicht möglich (siehe
SESSION.md-Eintrag zur Einführung), deshalb bewusst nicht versucht. Diese
Fälle sind selten (nur außerhalb der nächsten `prebuffer_count` Sender in
der Rotation oder im Notfall, wenn alle Kandidaten-Puffer selbst tot
sind) und bewusst als Grenze akzeptiert, nicht als Bug.

Drei harte Regeln bei `PrebufferedSource` selbst:

1. **Eine Pipe hat genau einen Leser.** `promote()`/`stop()` joinen den
   Reader-Thread; überlebt er den Join, wird die Quelle als `dead` markiert
   und verworfen statt übernommen.
2. **`pb.stop()` blockiert den Hauptloop** (bis ~9 s pro Quelle, weil es auf
   ein laufendes `read_window()` wartet). `sync_prebuffer()` läuft einmal pro
   Durchlauf — dort keine weiteren blockierenden Operationen einbauen.
3. **Audio verlässt den Prozess ausschließlich über `write_audio()`**
   (aufgerufen aus `push_and_drain()` bzw. direkt aus `quick_forward()`
   im Passthrough-Fall) — `output.write()` direkt aufzurufen umgeht die
   Playout-Deque komplett.

`sync_prebuffer()` startet gestorbene Puffer **nicht** selbst neu, sondern gibt
deren IDs zurück; der Hauptloop sperrt sie. Sonst gäbe es bei einer dauerhaft
toten URL einen ffmpeg-Spawn pro Sekunde.

Ändert sich `prebuffer_seconds`/`prebuffer_count` über `/config` während
die `playout`-Deque primed ist, wird sie verworfen (Reset auf
Passthrough) statt auf die neue Zieltiefe umgerechnet — aus demselben
Grund wie oben (kein gapless Übergang zwischen zwei Zieltiefen). Das
Delay baut sich beim nächsten Wechsel zu einem vorgewärmten Sender neu
auf. Die Nachrichten-Pause (`start_news_break_mp3()`) resettet die Deque
ebenfalls explizit: die MP3 ist keine `PrebufferedSource`-Quelle und
während `news_break_active` wird ohnehin nicht klassifiziert (siehe
News-Break-Abschnitt unten) — Passthrough ist dort also nicht nur
technisch nötig, sondern auch inhaltlich richtig. Dadurch verschiebt
sich der hörbare Beginn/Ende eines Nachrichten-Fensters um bis zu
`prebuffer_seconds` gegenüber der tatsächlichen :00/:30, falls der
Sender vorher mit vollem Delay lief — `window_minutes` selbst bleibt
unangetastet.

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

### Nachrichten-Pause (news_break.py)

Reine Domänenlogik (Zeitfenster-Berechnung, MP3-Auswahl) getrennt vom
Hauptloop, der die eigentliche Audio-Umschaltung übernimmt — `news_break.py`
kennt weder `StreamSource` noch `SwitcherState`. Zwei Punkte, an denen sich
das lokale Verhalten von einem Live-Sender unterscheidet und die man beim
Anfassen dieses Codes im Kopf haben muss:

- **`current` bleibt während der Pause bewusst der pausierte Sender**, nicht
  ein synthetisches "News"-Objekt. Dadurch laufen `sync_prebuffer()` und
  Watchdog (falls sie überhaupt aufgerufen werden) mit korrekten Daten
  weiter, und der Resume nutzt einfach `switch_to_station(current)` — dieselbe
  Funktion wie jeder normale Wechsel. Die Web-UI zeigt trotzdem korrekt "📰
  Nachrichten-Pause": `SwitcherState.current_station()` liefert währenddessen
  eine virtuelle Station (`NEWS_BREAK_STATION_ID`), unabhängig vom
  Hauptloop-internen `current`.
- **Lokale Dateien werden von ffmpeg NICHT in Echtzeit gelesen** — anders als
  eine Radio-URL (die durch die Netzwerk-Auslieferung beim Sender selbst
  taktet) dekodiert ffmpeg eine lokale Datei so schnell wie CPU/Disk
  erlauben. `StreamSource.start(path, realtime=True)` (das `-re`-Flag) ist
  deshalb für die MP3 PFLICHT, nicht optional — ohne das landet ein 35s-Clip
  in Sekundenbruchteilen komplett in den Pipes (live gemessen: 0,1s statt
  35s). Für echte Radio-URLs bleibt `realtime` False (Default).

Ein Zeitfenster wird per Slot-ID (`news_break.active_slot()`, siehe
Docstring) höchstens einmal bedient — MP3-Ende, Fensterablauf und manueller
Interrupt markieren den Slot alle gleichermaßen als "schon dran". Zwei
Interaktionen mit bestehendem Code brauchten deshalb einen Zusatz-Guard:
Reload-erzwungener Wechsel (`current["id"] not in active_ids`) darf während
der Pause nicht feuern (`current["id"]` ist ja der PAUSIERTE, nicht der
laufende Sender), und ein manueller Klick auf genau diesen pausierten Sender
braucht `news_break_active or manual_id != current["id"]` statt nur `!=` —
sonst wird der Klick sonst stillschweigend verschluckt.

### STT-Sprachfilter (stt_filter.py)

Zusätzliches Signal per Speech-to-Text, komplett unabhängig von
`fingerprint.py`: wo VAD/Heuristik nur "menschliche Stimme" erkennen
(Gesang zählt da mit), prüft `stt_filter.py` den Inhalt — kommt gerade
zusammenhängender Text in der jeweils erwarteten Sprache? Genau wie
`news_break.py` kennt dieses Modul weder `StreamSource` noch
`SwitcherState`. Die **einzige** Kopplungsstelle mit der bestehenden
Switch-Logik ist `stt_filter.combine_label()`, eingehängt in die
`classify()`-Closure in `radiosabbelnich.py`s `main()` — Streak-Zählung,
Fingerprint-Trigger und `do_switch()` dahinter bleiben dadurch komplett
unverändert, Fingerprint merkt von alldem nichts.

**Mehrsprachigkeit (seit 2026-08-06)**: welche Sprache für den aktuell
laufenden Sender geprüft wird, hängt an dessen **Kategorie**
(`stations_store.CATEGORIES`), nicht am einzelnen Sender —
`settings_store.resolve_stt_language(category, cfg)` löst das über
`cfg["category_languages"]` auf (fehlender Eintrag → `"de"`). Der
Hauptloop ruft das einmal pro Durchlauf auf und reicht dasselbe
`stt_lang` sowohl an `stt.sample_async()` (Sampling-Ziel) als auch an
`classify()` (Verdict-Interpretation) weiter — beide müssen zwingend
dieselbe Sprache sehen, sonst würde z.B. während des ersten Fensters nach
einem Kategoriewechsel mit der falschen Sprache gesampelt.

`stt_filter.py`s `_fresh_verdict(verdict, cfg, expected_language)` prüft
NEBEN dem Alter (siehe unten) auch, ob `verdict`s eigenes Sprach-Tag zu
`expected_language` passt — sonst würde ein noch nicht abgelaufener
Befund einer VORHERIGEN Sprache (z.B. kurz nach einem Sender-/
Kategoriewechsel) fälschlich mit der SCHWELLE der neuen Sprache bewertet.
`combine_label()`/`live_confidence()`/`live_language()` teilen sich
diese eine Prüfung, damit alle drei exakt denselben "kein (passender)
Befund"-Begriff verwenden.

**Engine-Asymmetrie bestimmt die Architektur**: Whisper ist von Haus aus
multilingual (ein geladenes Modell, Sprachcode nur pro
`transcribe()`-Aufruf) — eine zusätzliche Sprache kostet dort kein
zusätzliches RAM. Vosk braucht dagegen ein komplett eigenes Modell PRO
Sprache. `SttFilter._get_vosk_engine(lang, cfg)` lädt Vosk-Modelle
deshalb lazy (erst beim ersten tatsächlichen Sample dieser Sprache) und
hält per `MAX_LOADED_VOSK_LANGUAGES` (Default 2) nur eine begrenzte
Anzahl gleichzeitig im Speicher — ein `OrderedDict` als LRU-Cache
verdrängt bei Bedarf das am längsten ungenutzte Modell. Sowohl Erfolg
ALS AUCH Fehlschlag werden gecacht (Fehlertext statt `_VoskEngine`-
Objekt als Cache-Wert), damit ein kaputter Modellpfad nicht bei jedem
Sample-Tick erneut das Dateisystem anfasst — `SttFilter.language_status()`
legt diesen Zustand für die Config-Seite offen (✅/⚠ pro Sprache in der
"🌐 STT-Sprachen"-Tabelle).

**Kalibrierungs-Wizard (Teil 1b, seit 2026-08-06)**: `SwitcherState`
hält dafür `_calibration` (Sprache/Stufe/beide Sample-Listen) — bewusst
OHNE request/pop, obwohl sonst überall in diesem Bereich üblich (siehe
"Ein Prozess, zwei Akteure" oben): eine Kalibrierungs-Session berührt
keinen der Player-kritischen Zustände (`source`/`current`/Streak-
Buchhaltung), für die request/pop eigentlich da ist. Der Webserver-
Thread schreibt `_calibration` direkt lock-geschützt (Start/Stufenwechsel/
Stop), der Hauptloop liest `calibration_language` bei jedem Tick UND
hängt per `add_calibration_sample()` an dieselbe Session an — beide
Seiten können sich dabei nicht in die Quere kommen, weil der Hauptloop
die Session nie komplett ersetzt, nur ergänzt.

Ist eine Session aktiv, ERZWINGT der Hauptloop ihre Sprache als STT-
Zielsprache (statt `resolve_stt_language()` der aktuellen Kategorie) UND
pausiert die komplette Switch-/Streak-Logik für diesen Tick (`continue`
direkt nach `classify()`) — sonst könnte ein durch die erzwungene
Kalibrierungssprache verfälschtes `combine_label()`-Ergebnis mitten in
der Kalibrierung einen automatischen Wechsel auslösen (der Test-Sender
gehört ja u.U. gar nicht zu der Kategorie, die normalerweise diese
Sprache hätte). Der Wizard schaltet selbst NIEMALS einen Sender um — das
bleibt manuell über die Player-Seite, ganz bewusst kein zusätzlicher
Audio-Pfad oder eine zweite `StreamSource` nur fürs Kalibrieren, sondern
Wiederverwendung der ohnehin laufenden STT-Sampling-Pipeline des gerade
gespielten Senders.

`stt_filter.suggest_confidence_threshold(speech_samples, music_samples)`
ist die einzige, serverseitig einmal implementierte Vorschlagsformel
(Grenze zwischen `max(music_samples)` und `min(speech_samples)`, mit
`_THRESHOLD_MARGIN_RATIO=0.7` Richtung Sprache-Seite gewichtet) — die
Config-Seite pollt `GET /api/config/stt-calibration/status`
(`_build_calibration_status()` in webui.py), das den Vorschlag bei jedem
Poll neu berechnet, statt eine zweite JS-Implementierung der Formel zu
pflegen. "Übernehmen" nutzt bewusst den bestehenden
`/api/config/stt-languages`-Upsert-Endpoint statt eines eigenen "apply"-
Endpoints — eine Sprache mit neuer Schwelle speichern ist derselbe
Vorgang wie manuell in der "🌐 STT-Sprachen"-Tabelle.

**`add_calibration_sample()` verwirft Samples ohne erkannten Text**
(live bei der ersten echten Kalibrierung entdeckt, siehe SESSION.md
2026-08-06): leerer Text bedeutet "STT hat keine Wort-Hypothese
gebildet" (Pause/Jingle/Werbeblock, reine Instrumentalpassage) — NICHT
"mit niedriger Konfidenz erkannt". Beides ungefiltert in dieselbe
Statistik geworfen zieht `speech_min` künstlich auf 0 herunter (jede
Pause zählt sonst als "schlechtester Sprache-Sample") und macht
`suggest_confidence_threshold()`s Vorschlag unbrauchbar. `text.strip()`
leer ⇔ `confidence == 0.0` gilt für beide Engines (siehe deren
`transcribe()`), der Text-Check ist aber semantisch richtiger als ein
Float-Vergleich auf 0.0.

- **Sampling läuft kontinuierlich, unabhängig vom aktuellen VAD-Label**
  (nicht nur während erkannter Sprache) — sonst wäre `combine_mode="or"`
  wirkungslos: der bräuchte STT-Urteile auch für Fenster, die VAD als
  "music" einstuft, um überhaupt etwas beizutragen. Pausiert explizit
  während `news_break_active` (falscher Prüfgegenstand: die MP3, nicht
  der Sender) und wenn `state.filter_enabled == False` (jede Erkennung
  wäre dann ohnehin wirkungslos).
  Läuft in einem Hintergrund-Thread (`SttFilter.sample_async()`) mit
  Busy-Guard statt Thread-Stapelung — Whisper kann pro Clip mehrere
  Sekunden brauchen, das darf den ~1s-Analysetakt des Hauptloops nie
  blockieren.
- **Kein Befund (Feature aus, Ladefehler, noch kein erstes Sample, oder
  letzter Befund älter als `2 × sample_interval_seconds`) → `combine_label()`
  ist ein reines No-Op**, gibt das VAD/Heuristik-Label unverändert zurück.
  Darüber deaktiviert sich das Feature bei einem Modell-Ladefehler
  faktisch selbst, ohne dass der Hauptloop das explizit abfragen muss.
- **Konfidenzwerte sind Best-Effort-Proxys, keine kalibrierten
  Wahrscheinlichkeiten** — Vosk liefert nur bei manchen Modellen echte
  Wort-Konfidenzen (sonst Proxy aus Wortanzahl), Whisper liefert gar keine
  Sprache-Konfidenz, nur `no_speech_prob` pro Segment (`1 - no_speech_prob`
  als Näherung). `confidence_threshold` ist entsprechend Justiersache PRO
  SPRACHE (`cfg["languages"][lang]["confidence_threshold"]`, seit der
  Mehrsprachigkeit kein flaches Feld mehr) und Sender-Mix, nicht aus der
  Theorie ableitbar — dafür die DEBUG-Logs mit Text+Konfidenz pro Sample.
  Der `vosk-model-small-de-0.15`-Default liefert allerdings echte
  Wort-Konfidenzen (kein Proxy nötig) — Messung gegen echte Sender (siehe
  SESSION.md): Deutschlandfunk-Sprache nie unter 0.83, Schlager-Gesang
  (ndr-schlager/radio-paloma/schlagerparadies) im Schnitt 0.38.
  `confidence_threshold=0.75` (Default) liegt mit Marge unter dem
  Sprache-Minimum. **Wichtige Grenze**: klar/langsam gesungener Schlager
  erzeugt gelegentlich kurze, grammatisch plausible Wortfetzen mit hoher
  Konfidenz (~20% der gemessenen Schlager-Clips lagen trotzdem über
  0.75) — `combine_mode="and"` reduziert Fehl-Switches auf gesungene
  Musik deutlich, ist aber KEIN Allheilmittel dagegen. Ein möglicher,
  bisher nicht umgesetzter Ansatz: Wortdichte pro Clip zusätzlich
  einbeziehen (Sprache lag im Test bei 6–9 Wörtern/3s, Gesang meist bei
  1–4) — nicht implementiert, weil die Trennschärfe dafür nur an diesem
  einen kleinen Test (n=40 Clips, 4 Sender) geprüft wurde, keine
  ausreichende Grundlage für eine weitere Schwellwert-Entscheidung.
- **Engine-Reload bei laufendem Sample ist sicher**: `sample_async()`
  kopiert die Whisper-Engine-Referenz (bzw. bei Vosk: `engine_name`, der
  eigentliche Modell-Zugriff läuft über den threadsicheren
  `_get_vosk_engine()`-Cache) lokal in den Thread, bevor `reload()`
  (Config-Änderung, z.B. Vosk→Whisper) sie austauschen kann — ein bereits
  laufender Sample läuft sauber zu Ende, statt ihm das Objekt unter der
  laufenden `transcribe()`-Anfrage wegzuziehen.
- Reload bei Settings-Änderung passiert im bestehenden
  `state.pop_reload_request()`-Zweig in `main()`: nur bei Änderung von
  `enabled`/`engine`/`languages`/`whisper_model_size` wird die Engine
  tatsächlich neu geladen — bei Vosk heißt "neu laden" dank Lazy-Load nur
  "Cache leeren", nicht gleich alle konfigurierten Sprachmodelle neu
  laden. `sample_interval_seconds`/`category_languages`/`combine_mode`
  liest `combine_label()`/`resolve_stt_language()` bei jedem Aufruf
  frisch aus `state.stt_filter_cfg` — dafür ist kein Reload nötig.

### Zweisprachiges Web-Interface (i18n.py)

`i18n.py` ist reine Domänenlogik ohne Bezug zu `StreamSource`/
`SwitcherState`, analog zu `news_break.py`/`stt_filter.py`: ein Dict
`STRINGS` (nach Key gruppiert, `"de"`/`"en"` nebeneinander) plus
`DEFAULT_LANGUAGE` aus `UI_LANGUAGE` in `.env`. Übersetzt wird NUR, was
der Nutzer im Browser sieht (Labels, Buttons, `alert()`/`confirm()`-
Dialoge) — Log-Meldungen, Code-Kommentare und die von
`settings_store.py`/`station_import.py`/`stations_store.py` geworfenen
`ValueError`-Texte bleiben bewusst deutsch (siehe "Sprache und
Konventionen" oben).

`_PAGE_HTML`/`_CONFIG_PAGE_HTML` in `webui.py` bleiben EIN Quelltext
pro Seite, nicht zwei Sprachvarianten: statisches Markup bekommt
`data-i18n`/`data-i18n-html`/`data-i18n-title`/`data-i18n-placeholder`/
`data-i18n-aria-label`-Attribute, JS-seitige Dialoge nutzen `t('key',
vars)`. Ein injiziertes `<script>` (`const I18N = {...}` — das
komplette `i18n.STRINGS`-Dict für die gewählte Sprache als JSON, plus
`t()`) und ein synchron beim Laden ausgeführtes `applyStaticI18n()`
ersetzen die Texte, BEVOR der erste Repaint passiert — kein
sichtbares Umspringen der Sprache, kein zweiter Template-Mechanismus.

Beide Templates werden trotzdem nur EINMAL pro Sprache tatsächlich
gerendert, nicht pro Request: `_render_i18n_variants()` läuft beim
Modul-Import von `webui.py` und ersetzt die zwei Platzhalter
`%%LANG%%`/`%%I18N_JSON%%` für jede Sprache in `i18n.LANGUAGES`,
Ergebnis landet in `_PAGE_HTML_BYTES`/`_CONFIG_PAGE_HTML_BYTES` (dict
`{"de": bytes, "en": bytes}`, analog zu `_MANIFEST_JSON_BYTES` weiter
oben in derselben Datei). `do_GET` wählt daraus nur per
`state.language`-Lookup — kein Pro-Request-Stringersatz.

`_check_i18n_coverage()` läuft beim selben Modul-Import: ein Regex
sammelt alle in den Templates tatsächlich verwendeten
`data-i18n*`/`t('key'`-Keys und gleicht sie gegen `i18n.STRINGS` ab —
ein fehlender/vertippter Key wirft sofort beim Start (kein Test-
Framework im Projekt, siehe oben, das übernimmt hier diese Rolle).
Der Regex für `t('key'` braucht zwingend ein Lookbehind
(`(?<![A-Za-z0-9_])t\('`), sonst matcht er in JEDEM Bezeichner, der
zufällig auf "t(" endet — `document.createElemen`**`t('`**`div')`,
`spli`**`t('`**`{'...` — beides real aufgetreten (siehe SESSION.md).

`language` ist ein normales `settings_store`-Feld, läuft über denselben
request/pop-Zyklus wie `tls_enabled`/`stream_url` (spätestens einen
Hauptloop-Tick nach dem Speichern aktiv) — ANDERS als `tls_enabled`
aber OHNE Neustart des Containers, weil die Auswahl reiner Dict-Lookup
ist statt eines Socket-Rewraps. Die Config-Seite lädt sich nach dem
Speichern trotzdem per `location.reload()` neu, weil pro Sprache schon
fertiges Markup vorliegt — ein Sprachwechsel ohne Reload würde nur die
serverseitig injizierten `I18N`-Strings der aktuell geladenen Seite
treffen, nicht z.B. eine neu aufgerufene Unterseite.

## Docker-Besonderheiten

Host-Layout und Container-Layout sind bewusst entkoppelt: `*.py` liegen am
Repo-Root, alles andere ist auf dem Host in `pics/` (Bilder), `web/`
(vom Webserver ausgelieferte JS/JSON-Assets: `qrcode.js`/`manifest.json`/
`sw.js`) und `data/` (Senderliste/Settings/Fingerprint-DB/Laufzeit-Ordner)
aufgeteilt — im Container landet trotzdem alles flach in `/app/`
(`_load_static()`/`STATIONS_FILE`/`SETTINGS_FILE`/`FINGERPRINT_DB_FILE`/
`FINGERPRINT_CLIPS_DIR`/`DEFAULT_LOG_FILE` sind alle `__file__`-relativ
zum jeweiligen `.py`-Modul, das am Root bleibt). Wer eine neue Datei
hinzufügt: nur der **Host-Pfad** (Dockerfile-`COPY`-Quelle bzw. linke
Seite eines `docker-compose.yml`-Volume-Mounts) folgt der Ordnerstruktur,
das `.py`-seitige/Container-interne Ziel bleibt immer flach in `/app/`.

- `stations.json`, `settings.json` und `fingerprints.db` liegen auf dem
  Host unter `data/` und sind als **einzelne Dateien** gebindmountet.
  Deshalb schreibt `stations_store._write()` direkt statt über
  `os.replace()` — ein Rename über einen Mountpoint scheitert mit
  "Device or resource busy". Nicht auf "atomares Schreiben" umbauen.
- Der Dockerfile kopiert jede `.py`-Datei **einzeln**: neue Module dort
  eintragen, sonst fehlen sie im Image.
- `fix_silero_execstack.py` patcht zur Build-Zeit das PT_GNU_STACK-Bit der
  silero-vad-lite-`.so`. Ohne den Patch verweigert der Kernel dieses Hosts das
  `dlopen()` und die Spracherkennung fällt dauerhaft auf die Heuristik zurück.
- Der Icecast-Service überschreibt den Entrypoint des Basis-Images (`icegen`
  kennt `<location>`/`<admin>` nicht, und ohne `rm -f icecast.xml` hängt es bei
  jedem Neustart eine zweite Kopie an → ungültiges XML, Absturzschleife).
- `news_break.mp3_folder` in `settings.json` ist ein **Container-interner**
  Pfad (Default `/app/news_mp3`), nicht der Host-SMB-Pfad — letzterer kommt
  über `NEWS_MP3_FOLDER` in `.env` rein (Bind-Mount in `docker-compose.yml`).

### TLS/HTTPS (optional, `TLS_CERT_FILE`/`TLS_KEY_FILE` in `.env`)

Beide Dienste bekommen bei Bedarf HTTPS, aber auf grundverschiedene Art —
wer hier etwas ändert, muss beide Hälften verstehen:

- **Web-Interface** (`webui.start_server()`): `settings.json`-Feld
  `tls_enabled` (per `/config` setzbar) entscheidet, ob das Server-Socket
  in `ssl.SSLContext.wrap_socket()` eingewickelt wird. Nur beim Start
  gelesen — ein `ThreadingHTTPServer` kann sein Socket nicht im laufenden
  Betrieb neu einwickeln, die Einstellung wirkt also erst nach einem
  Container-Neustart. Kein Parallelbetrieb: sobald aktiv, läuft nur noch
  HTTPS, Klartext-HTTP auf demselben Port antwortet gar nicht mehr. Fehlt
  das Zertifikat trotz `tls_enabled=true` (leere/ungültige Datei, siehe
  unten), fängt `start_server()` den `ssl.SSLError` ab und bleibt bei
  HTTP statt abzustürzen.
- **Icecast**: kein Python-Code, kein `settings.json`-Bezug — Icecast läuft
  in einem separaten, drittseitigen Container. Steuerung ausschließlich
  über `.env`: sind `TLS_CERT_FILE`/`TLS_KEY_FILE` gesetzt, patcht das
  `command:`-Skript in `docker-compose.yml` einen **zusätzlichen**
  `<listen-socket>` mit `<ssl>1</ssl>` in die generierte `icecast.xml`
  (Port `ICECAST_SSL_PORT`, Default 8443) — der bestehende Klartext-Port
  8000 bleibt unverändert bestehen, Hörer sind nie betroffen. Icecast
  braucht dafür kurz Root, um die 0600-Zertifikatsdatei zu lesen
  (`user: "0:0"` in `docker-compose.yml`), gibt die Rechte danach über
  `<security><changeowner>` in der generierten `icecast.xml` selbst wieder
  ab. **Der icegen-Generator hat dabei einen Bug**, der bis zu diesem
  Feature nie auffiel: er trägt `<group>icecast2</group>` ein, tatsächlich
  heißt im Image die Gruppe `icecast` (nur der User heißt `icecast2`, uid
  und gid sind zufällig beide 101). Ohne den `sed`-Fix dafür verweigert
  Icecast als root generell den Start ("You should not run icecast2 as
  root") — unabhängig davon, ob überhaupt ein Zertifikat konfiguriert ist,
  weil der Container jetzt IMMER als root hochkommt. Zusätzlich reicht
  Icecast eine `<ssl-certificate>` mit getrennten Cert-/Key-Dateien nicht:
  beide werden zu einer PEM-Datei zusammengefügt (`cat cert key >
  icecast-tls.pem`) und explizit auf `icecast2:icecast` gechownt, weil
  Icecast diese Datei nachweislich erst **nach** dem internen
  Privilegien-Drop liest, nicht währenddessen als root (per isoliertem
  Testcontainer verifiziert, siehe SESSION.md) — root-only 0600 hätte dort
  also nicht gereicht.
- Beide Mounts (Web-Interface UND Icecast) fallen ohne gesetzte
  `TLS_CERT_FILE`/`TLS_KEY_FILE` auf `/dev/null` zurück (`${TLS_CERT_FILE:-/dev/null}`)
  statt auf eine Repo-Platzhalterdatei wie bei `NEWS_MP3_FOLDER`/`data/news_mp3`
  — `/dev/null` ist auf jedem Host immer ein gültiges Bind-Mount-Ziel und
  liefert 0 Byte, genau das, was die jeweiligen "ist überhaupt ein
  Zertifikat da?"-Prüfungen (`-s`-Test im Bash-Skript, `ssl.SSLError` im
  Python-Fallback) erwarten.

## Kein Auth, nur hinter VPN

Web-Interface und Config-Seite haben keinerlei Authentifizierung, und der
Restream ist urheberrechtlich nur privat tragbar. Keine Änderungen vorschlagen
oder umsetzen, die auf öffentliche Erreichbarkeit hinauslaufen (Port-
Forwarding, öffentlicher Reverse-Proxy) — siehe Warnung in der README.

## Bekannte offene Punkte

Aktueller Stand am Ende von `SESSION.md` (Abschnitt "bewusst NICHT in diesem
Durchgang"), u.a.: `sync_prebuffer()`/`pb.stop()` können den Hauptloop
blockieren, das Web-Interface zeigt keinen Stream-Health-Status,
`SpeechDetector.leftover` wird beim Senderwechsel nicht zurückgesetzt, die
Fingerprint-DB wächst ohne Pruning, und die Config-Seite skaliert nicht auf
mehrere hundert Sender (keine Suche, kein Bulk-Delete). Außerdem: der
STT-Sprachfilter (`stt_filter.py`) wurde gegen ein kleines, echtes
Sample (Deutschlandfunk + 3 Schlager-Sender, siehe SESSION.md) kalibriert
— `confidence_threshold=0.75` ist dadurch besser fundiert als der
ursprüngliche Startwert, aber klar/langsam gesungener Schlager bleibt
eine bekannte Schwachstelle (~20% falsch-positive Konfidenz trotz
Schwelle). Whisper wurde noch gar nicht gegen echtes Audio getestet.
Die Mehrsprachigkeit (Kategorie → Sprache, siehe STT-Sprachfilter-
Abschnitt oben) inklusive geführtem Kalibrierungs-Wizard ist umgesetzt —
`suggest_confidence_threshold()`s Vorschlagsformel (Marge zwischen dem
höchsten gemessenen Musik-Wert und dem niedrigsten gemessenen
Sprache-Wert) ist bislang nur an den ursprünglichen DE-Messwerten
(Deutschlandfunk/Schlager, siehe oben) plausibilisiert, nicht an einer
zweiten Sprache in echtem Betrieb verifiziert — bei sehr kleinen
Sample-Zahlen (wenige Minuten Kalibrierung) bleibt der Vorschlag
entsprechend grob, die Web-UI weist bei überlappenden Verteilungen
zumindest per Warnung darauf hin.
Das Playout-Delay (siehe Prebuffering-Abschnitt oben) schützt Hörer nur
bei Wechseln zu vorgewärmten Sendern — bei einem frischen Wechsel
(außerhalb der nächsten `prebuffer_count` Sender oder im Notfall) läuft
der Sender bis zum nächsten warmen Wechsel ohne Delay/Vorausschau,
bewusst in Kauf genommen statt eines gapless-Übergangs, der ohne
Zeitdehnung nicht möglich ist.
