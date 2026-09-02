# Changelog

Alle nennenswerten Änderungen an RadioSabbelNich, neueste zuerst. Deckt
sowohl den Docker-Dienst als auch die native Android-App (`android-app/`,
eigenständiges Projekt, siehe `CLAUDE.md`) ab — Android-Einträge sind mit
**Android:** markiert. Ausführliche Begründungen, Design-Entscheidungen
und Messwerte hinter den einzelnen Punkten stehen in `SESSION.md`
(Docker) bzw. `android-app/SESSION.md` (Android); dies hier ist die
verdichtete Übersicht.

**Namenshistorie**: das Projekt hieß ursprünglich *RadioZapper*, wurde am
2026-08-08 zu *KeinSabbelRadio* und am 2026-08-09 zu *RadioSabbelNich*
umbenannt. Ältere Commit-Nachrichten/Dateinamen (z.B. `radio_switch`,
`check-radiozapper.sh`) spiegeln den jeweils zur Zeit gültigen Namen
wider — hier einheitlich mit dem aktuellen Namen beschrieben, sofern
nicht der Umbenennungsvorgang selbst der Inhalt eines Eintrags ist.

## Aktueller Stand

**Zuletzt umgesetzt** (siehe Datumsabschnitte unten für Details):
- Nächtlicher Sender-Scan (Whisper-Sprach-ID, WebUI-integriert, Vorschlag statt Automatik)
- Automatische Sender-Sprach-Erkennung (`vosk_language_check.py`)
- Vosk-Sprachmodelle per WebUI herunterladen (kein Konsolenzugriff mehr nötig)
- STT-Sprache pro Sender überschreibbar (statt nur pro Kategorie)
- Song-Erkennung Phase 2: AudD-Cloud-Lookup für unbekannte Songs
  (`song_recognition.cloud_lookup_enabled` + `AUDD_API_TOKEN`), Live-Anzeige
  von Titel/Interpret im Radio-Modus
- Automatische Update-Prüfung für die Docker-Installation
  (`update_check.py`, git-pull-Hinweis im Web-Interface, Default AN)
- Song-Erkennung Phase 1 (lokaler Chromaprint-Cache) + Kalibrierungs-
  Logging/-Skript (`check_song_calibration.py`)
- Werbeblock-Vorbuffering + Sprache-Gate für die Nachrichten-Pause
- GPLv3-Lizenzierung, Inhaltsverzeichnisse für README.md/ARCHITECTURE.md

**Aktuell offen/geplant** (Details: README.md, "Zukünftige Features"):
- Song-Erkennung: Vorbefüllung der Referenz-DB aus der eigenen
  Musik-Library, automatisierte Threshold-Kalibrierung anhand der
  AudD-Identifikationen statt des bisherigen (tautologischen)
  `song_match_log`
- Deutschsprachige Musik ausblenden (Skip-Filter + manuelles Anlernen
  per "Deutsch!"-Button)
- Musik-Library: Enrichment (Cover/Lyrics), Energy-Erkennung/Browse-UI
- iOS-App (Idee, kein Zeitplan)

## 2026-09-02

- Fix: Docker-Build brach bei aubio (Schritt 4/43) mit
  `BackendUnavailable: Cannot import 'setuptools.build_meta'` ab, weil
  `--no-build-isolation` das System-Environment nutzt und Python 3.12
  dort kein setuptools mehr automatisch mitbringt — `setuptools`/`wheel`
  jetzt vorab im ersten `pip install` ergänzt.
- Neu: README.md (DE + EN) um Sektion "Verwendete Open-Source-Software" /
  "Third-Party Open Source Components" ergänzt — Tabelle mit allen
  eingebundenen Fremdkomponenten (Backend + Android) und ihren verifizierten
  Lizenzen, AudD explizit als kommerzieller externer Dienst abgegrenzt.
- **Android:** Pendant-Sektion "Verwendete Open-Source-Software" in
  `android-app/README.md` ergänzt — alle sechs Gradle-Abhängigkeitsgruppen
  einzeln mit verifizierter Lizenz (durchweg Apache-2.0).

## 2026-08-29

- Neu: **Nächtlicher Sender-Scan** ("🌙"-Sektion auf der Config-Seite,
  `night_scan.py`) — erkennt automatisch per Whisper (kein
  sprachspezifisches Modell nötig) die gesprochene Sprache je Sender via
  VAD-gesteuertem Sammeln von Sprach-Fenstern, schlägt sie als
  Ergänzung zur manuellen Sprach-Zuordnung vor (Übernehmen/Verwerfen-
  Tabelle) — NIE automatisch scharf geschaltet. Konfigurierbares
  Nacht-Zeitfenster (Default 02–05 Uhr, max. 1 automatischer Lauf/Tag)
  + jederzeit nutzbarer manueller "Jetzt scannen"-Knopf. Default AUS.
  Deckt seit demselben Tag die komplette Senderliste ab (nicht nur
  aktive Sender) — aktive Sender werden dabei immer zuerst geprüft, erst
  danach deaktivierte; eine Fortschrittszeile zeigt den Stand getrennt
  nach aktiv/deaktiviert, ein erster Durchlauf über alle 350+ Sender darf
  sich über mehrere Nächte ziehen.
- Neu: `vosk_language_check.py` (eigenständiges CLI-Skript, `docker exec`)
  erkennt automatisch die gesprochene Sprache je Sender per Vosk-STT und
  taggt `stations.json` entsprechend — Alternative zum manuellen Setzen
  in der Senderliste. Standardmäßig nur ein Report, `--apply` übernimmt
  eindeutige Treffer und stößt danach automatisch einen Reload im
  laufenden Hauptloop an, `--include-disabled` bezieht auch deaktivierte
  Sender mit ein. Reagiert bewusst konservativ (lieber "unklar" als ein
  falscher Tag).
- Neu: Vosk-Sprachmodelle lassen sich jetzt direkt über die "🌐
  STT-Sprachen"-Tabelle **herunterladen** (Dropdown mit kuratiertem
  Modell-Katalog + Fortschrittsbalken) — kein manuelles Herunterladen/
  Entpacken/Mounten über die Konsole mehr nötig. Neuer beschreibbarer
  Sammel-Mount `VOSK_MODELS_FOLDER` in `docker-compose.yml` (einmalig
  einzurichten, danach für jede weitere Sprache ausreichend). Löschen
  bietet zusätzlich "+ Dateien löschen", um Plattenplatz wirklich
  freizugeben (nur für so heruntergeladene Sprachen, alte manuell
  gemountete Pfade bleiben unangetastet).
- Neu: STT-Sprache jetzt zusätzlich **pro Sender** überschreibbar
  (Sprache-Dropdown/Badge in der Sender-Liste) — greift vor der
  bisherigen Kategorie-Zuordnung, die als Fallback für Sender ohne
  eigenen Override erhalten bleibt.
- Neu: Statistik-Sektion auf der Config-Seite ("🎵 Song-Erkennung –
  Statistik") — DB-Größe, Sammelzeitraum, Hit-Rate, Similarity-Perzentile/
  Histogramm (Hits vs. Misses), Trennschärfe-Analyse, Top-Sender und
  meistgespielte erkannte Songs für das lokale Fingerprinting; für den
  AudD-Cloud-Fallback zusätzlich Requests heute/letzte 7 Tage/gesamt,
  Erfolgsquote und eine grobe Kostenschätzung. Neue Tabelle
  `audd_request_log` protokolliert dafür jeden tatsächlich versuchten
  AudD-Aufruf.
- Fix: Update-Prüfung zeigte nach einem Rebuild kurz nach einem gefundenen
  Update bis zu 24h lang fälschlich "Update verfügbar" für eine bereits
  installierte Version — der gecachte Zustand korrigiert sich jetzt sofort
  selbst, sobald die laufende Version aufgeholt hat.
- Neu: Live-Statusanzeige für AudD-Cloud-Lookup — die "Jetzt
  läuft"-Anzeige zeigt jetzt "⚠️ AudD-Kontingent aufgebraucht"/"⚠️ AudD
  nicht erreichbar"/"⚠️ AudD-Fehler (Code X)" anstelle des neutralen "🔍
  noch nicht erkannt", wenn Cloud-Lookup gerade nicht funktioniert. AudDs
  Fehlercodes #900/#901/#902 gelten dabei als Kontingent-Fall.

## 2026-08-28

- Neu: Song-Erkennung liefert jetzt auch die Songlänge (Album/Jahr waren
  schon da), wenn AudD sie mitliefert — erscheint in der "Jetzt
  läuft"-Anzeige als "Album (Jahr) · m:ss". Kommt aus zusätzlich
  angeforderten Spotify-/Apple-Music-Daten (`return=apple_music,spotify`),
  da AudDs Kernantwort keine Länge liefert. Neue Spalte `duration_seconds`
  in `song_fingerprints.db` (Migration für bestehende DBs).
- Neu: "⏭ Andere Pause-MP3"-Knopf auf der Player-Seite — wählt während
  einer laufenden Nachrichten-Pause eine andere zufällige MP3 aus
  demselben Ordner, ohne die Pause (anders als "⚡ ZAPPEN!") komplett zu
  beenden. Neuer Endpoint `/api/news-break/skip`, nur klickbar während
  einer aktiven Pause.
- Neu: Song-Erkennung liefert jetzt auch Album/Erscheinungsjahr (nicht nur
  Titel/Interpret), wenn AudD sie mitliefert — erscheinen in der zweiten
  Zeile der "Jetzt läuft"-Anzeige. Neue Spalten `album`/`year` in
  `song_fingerprints.db` (Migration für bestehende DBs, gleiches Muster wie
  die `bpm`-Spalte in `music_scan.py`); ein einmal per Cloud identifizierter
  Song zeigt Album/Jahr danach auch bei jeder lokalen Wiedererkennung, ohne
  erneuten AudD-Call.
- Neu: Hörer-Gate für Song-Erkennung (`song_fingerprint.ListenerGate`) —
  stoppt lokales Fingerprinting UND AudD-Cloud-Lookup automatisch, sobald
  niemand mehr den Restream hört (60s-Polling gegen Icecasts Admin-API,
  fail-open bei Fehlern/fehlender Konfiguration, 15s Start-Verzögerung
  gegen einen Timing-Fehler beim allerersten Check). Bewusst ein Stop, kein
  Pause: der `SongRecognizer`-Ringpuffer wird beim Verschwinden der letzten
  Hörer per `reset()` geleert statt eingefroren, sonst würde er nach der
  Rückkehr eines Hörers eine Weile veraltetes Vor-Pause-Audio enthalten.
- Neu: Live-Debug-Anzeige für Song-Erkennung — "🔍 noch nicht erkannt"/
  "⏸ Song-Erkennung pausiert (keine Hörer)" statt stillschweigend leerer
  "Jetzt läuft"-Zeile, solange das Feature aktiv ist, aber (noch) kein
  Song identifiziert wurde. Zwei neue i18n-Keys
  (`idx_song_pending`/`idx_song_paused_no_listeners`).
- Neu: Song-Erkennung Phase 2 — AudD-Cloud-Lookup (`song_fingerprint.py`,
  `audd_lookup()`) identifiziert einen bei Phase 1 unbekannten Song, wenn
  `song_recognition.cloud_lookup_enabled` UND `AUDD_API_TOKEN` (`.env`)
  gesetzt sind. Kein neuer pip-Dependency (nur `urllib`/`wave`, Multipart-
  Upload von Hand gebaut). Fester 60s-Mindestabstand zwischen Cloud-Calls
  (`AUDD_MIN_INTERVAL_SECONDS`) als Sicherheitsnetz gegen Kontingent-
  Verbrauch bei noch unkalibriertem `similarity_threshold`.
- Titel/Interpret erscheinen bei Erfolg live in der "Jetzt läuft"-Anzeige
  im Radio-Modus (`webui.py` `/api/status`, bestehender
  `now_playing_tags`-Mechanismus, keine neuen Templates/JS).
- `SongFingerprintDB.set_cloud_metadata()` neu: schreibt Titel/Interpret
  nachträglich in die von `match_or_learn()` angelegte Zeile (kein
  Schema-Wechsel, Spalten existierten bereits).
- Vorab geklärt: das bisherige `song_match_log`-Kalibrierungs-Logging
  (2026-08-23) ist für eine echte `similarity_threshold`-Kalibrierung
  ungeeignet (Hit/Miss wird tautologisch aus dem Vergleich mit dem
  aktuellen Threshold selbst abgeleitet) — AudD-Identifikationen sind die
  bessere Referenz, siehe ARCHITECTURE.md/README.md.

## 2026-08-24

- Fix: `update_check.py` fehlte im Dockerfile (`COPY`-pro-Datei-Muster
  übersehen) — Container landete beim ersten Rebuild in einer
  Neustartschleife, live beim vom Nutzer angeforderten Rebuild gefunden
  und sofort behoben.
- Neu (Default AN, Ausnahme von "Default AUS"): automatische
  Update-Prüfung für die Docker-Installation (`update_check.py`) — prüft
  alle 24h per Lesezugriff gegen die `VERSION`-Datei im GitHub-
  `main`-Branch, zeigt bei Rückstand einen Hinweis-Banner auf Player-
  UND Config-Seite mit Anleitung zum manuellen `git pull` (kein
  Image-Registry-Deployment, kein Auto-Update). Neuer `update_check`-
  Block in `settings.json`, neue Config-Sektion "🔄 Automatische
  Update-Prüfung".
- README.md: neue Roadmap-Unterabschnitte "Automatische Song-Erkennung:
  Cloud-Erweiterung (geplant)" und "Deutschsprachige Musik ausblenden
  (geplant)" unter "Zukünftige Features".
- CHANGELOG.md: neuer Abschnitt "Aktueller Stand" ganz oben (kompakte
  Zusammenfassung zuletzt umgesetzter + offener Punkte).
- ARCHITECTURE.md: dokumentiert, dass ein neuer `settings_store`-
  Default-Key (wie `song_recognition`) nie automatisch in eine
  bestehende `settings.json` nachgetragen wird — betrifft künftig jeden
  neuen Config-Unterblock, nicht nur diesen Fall.
- README.md: Song-Erkennung prominent in der Einleitung erwähnt (bisher
  nur im eigenen Abschnitt), Datei-Tabelle und Song-Erkennung-Abschnitt
  um `check_song_calibration.py` ergänzt.
- Song-Erkennung: fehlende Startup-Log-Zeile ergänzt ("aktiv"/"inaktiv"),
  Root Cause für leere `song_match_log` war kein Bug — `enabled=false`
  per Default, kein `/config`-Schalter zum Umschalten. In `data/
  settings.json` jetzt scharfgestellt, damit die Kalibrierungs-
  Sammelphase Daten sammelt.

## 2026-08-23

- Song-Erkennung: Kalibrierungs-Logging für `similarity_threshold`
  (neue Tabelle `song_match_log` in `song_fingerprints.db`, protokolliert
  jeden Cache-Vergleich mit vollem Similarity-Wert) — Vorbereitung für
  eine spätere empirische Threshold-Bestimmung, ändert kein Verhalten.
- Song-Erkennung Phase 1: lokaler Chromaprint-Fingerprint-Cache
  (`python/song_fingerprint.py`, `fpcalc` im Docker-Image) erkennt
  Songwiederholungen im laufenden Musik-Betrieb, noch ohne Cloud-Lookup
  (Stub `on_unknown_fingerprint()`). Default deaktiviert
  (`song_recognition.enabled`).

## 2026-08-21

- Doku: Feature-Beschreibungen in README.md von historischen
  Datums-/Changelog-Details befreit, beschreiben jetzt durchgängig den
  aktuellen Ist-Zustand (deutscher + englischer Teil).
- Doku: Inhaltsverzeichnis für README.md ergänzt (analog zu
  ARCHITECTURE.md), Anker per `github-slugger` erzeugt.
- Doku: Inhaltsverzeichnis für ARCHITECTURE.md ergänzt.
- Doku: GitHub- und Forgejo-Repo-Umbenennung radiozapper →
  radiosabbelnich nachgezogen (lokaler `github`-Remote sowie
  `origin`/Forgejo-Remote-URL).
- Lizenz: Projekt unter GPLv3 lizenziert — `LICENSE` (voller Originaltext)
  im Repo-Root, Lizenz-Badge/-Abschnitt in README.md, GPLv3-Kurzheader
  in allen `python/`-Modulen sowie **Android:** allen Kotlin-Klassen
  unter `mvp/`. Abhängigkeiten auf Kompatibilität geprüft, keine
  problematische gefunden.
- Doku: erste echte Bestätigung des Sprache-Gates im Produktivbetrieb
  (echtes :00/:30-Fenster, kein synthetischer Test) — Details in
  SESSION.md.
- Doku: dasselbe Pause-Ende real verfolgt — Werbeblock-Vorbuffering blieb
  über zwei MP3-Fortsetzungen und 4:37 Min. korrekt am Leben und wurde
  sauber übernommen, keine Fehler.
- Fix: `SpeechDetector` setzt Resample-Rest UND internen VAD-Modellzustand
  jetzt bei jedem echten Streamwechsel zurück (`reset()`, neu) — vorher
  konnte Audio des vorigen Senders/Kandidaten die Klassifikation des
  neuen leicht verfälschen.
- Intern: `StreamSource` aus `radiosabbelnich.py` nach `stream_source.py`
  extrahiert, neues (noch nicht verdrahtetes) `ad_skip_prebuffer.py` für
  das geplante Werbeblock-Vorbuffering nach der Nachrichtenpause — kein
  Verhaltensunterschied für Nutzer in diesem Schritt.
- Neu (experimentell, standardmäßig AUS): Werbeblock-Vorbuffering nach
  der Nachrichten-Pause — hört den pausierten Sender in den letzten
  Sekunden der Pause-MP3 schon im Hintergrund mit und steigt beim
  Pause-Ende direkt in die Musik ein, falls die Werbung dort rechtzeitig
  vorbei ist. Einstellbar auf der Config-Seite unter "📰
  Nachrichten-Pause" (`ad_prebuffer_enabled`/`ad_prebuffer_lead_seconds`).
- Fix: Dockerfile kopierte die beiden neuen Module `stream_source.py`/
  `ad_skip_prebuffer.py` nicht ins Image — verursachte kurzzeitig eine
  Crash-Loop nach dem Deploy, bevor die fehlenden `COPY`-Zeilen ergänzt
  wurden.
- Doku: Werbeblock-Vorbuffering-Abschnitt in `ARCHITECTURE.md` um real
  gemessene Ressourcenwerte ergänzt (~40MB RSS/~1 Prozentpunkt CPU
  zusätzlich, nur während der Vorlaufzeit einer aktiven Pause).
- Intern: neue Settings `news_break.require_speech_in_window`/
  `speech_gate_window_minutes`/`speech_gate_streak` (alle noch
  wirkungslos, Trigger-Verdrahtung folgt) — Vorbereitung für "Pause nur
  bei erkannter Sprache starten".
- Neu (experimentell, standardmäßig AUS): News-Break startet optional
  nur noch, wenn zusätzlich zum Zeitfenster gerade Sprache auf dem
  Live-Sender erkannt wird — verhindert rein zeitbasiertes Starten
  während noch Musik läuft. Einstellbar unter "📰 Nachrichten-Pause"
  (`require_speech_in_window`/`speech_gate_window_minutes`/
  `speech_gate_streak`).
- Fix: die bestehende "Moderation erkannt"/Fingerprint-Match-Skip-Logik
  hätte den neuen Sprache-Gate-Zähler fast immer vor dessen eigener
  Prüfung zurückgesetzt (beide nutzen denselben Zähler) — während des
  engen Sprache-Gate-Fensters jetzt ausgesetzt.
- Doku: README-Konfigurationstabelle (DE/EN) um die drei neuen
  Sprache-Gate-Felder ergänzt.

## 2026-08-20

- Web-Interface: "STT"-Meter-Label ausgeschrieben (🗣 STT
  (Speech-to-Text)-Sprachfilter) statt der bloßen Abkürzung.
- Web-Interface: Fingerprint-Anzeige zeigt jetzt dauerhaft "Zuletzt
  gelernt"/"Zuletzt erkannt" mit Uhrzeit unter dem blinkenden Chip, statt
  nur den 5s lang sichtbaren Event.
- Neu: 🔊 VU-Meter im Web-Interface (`/` und `/musik`) — zeigt den
  aktuellen Lautstärkepegel von Radio- und Musik-Wiedergabe, 10
  Pegelwerte/Sekunde vom Server, lokal im Browser animiert für einen
  flüssigen statt ruckeligen Balken.
- Web-Interface: Intervall-Polling von 3s auf 1s verkürzt (passt zur
  tatsächlichen 1Hz-Update-Rate des Hauptloops) — Bullshitometer/
  STT-Balken/Hörerzahlen/Senderstatus fühlen sich insgesamt
  reaktionsschneller an.
- Neu: Totluft-Watchdog — ein Sender, der weiter technisch einwandfrei
  Daten liefert, aber 30s am Stück nur noch Stille/Rauschen sendet
  (senderseitiges Problem, real beobachtet), wird jetzt genau wie ein
  hart toter Stream automatisch aus der Rotation genommen und
  weitergeschaltet (`SILENCE_DBFS_THRESHOLD`/`SILENCE_DURATION_LIMIT` in
  `radiosabbelnich.py`). Vorher blieb der Player stumm auf so einem
  Sender hängen, weil der bestehende Watchdog nur leere Reads erkennt.

## 2026-08-16

- Doku: Architekturwissen konsolidiert — `ARCHITECTURE.md` ist jetzt die
  alleinige, vollständige Quelle (Diagramme + Begründungen), `CLAUDE.md`s
  vorherige "Architektur"/"Docker-Besonderheiten"/"Bekannte offene
  Punkte"-Abschnitte sind auf Kurzverweise dorthin geschrumpft (1194 →
  174 Zeilen), keine Doppelpflege mehr nötig.
- Neu: `ARCHITECTURE.md` — grafische Architektur-Gesamtübersicht mit
  Mermaid-Diagrammen, verlinkt aus `CLAUDE.md`/`README.md`.

## 2026-08-15

- Neu: Tag-Anzeige (Titel & Interpret / Album & Jahr) beim Abspielen
  einer News-Break-MP3 oder eines Musik-Player-Tracks, format-
  übergreifend via mutagen (MP3, FLAC, OGG, M4A/AAC, WAV, APE) —
  gemeinsamer Baustein `audio_tags.py`, kein Titel-Tag → Dateiname als
  Fallback, fehlendes Album/Jahr → Zeile entfällt statt Platzhalter.
- Fix: Player-Modus startete nach einem Container-Neustart mit
  gespeichertem Modus (bzw. nach einem manuellen Wechsel Radio→Player)
  keine Wiedergabe automatisch — Modus war zwar korrekt gemerkt, aber es
  kam kein Ton, bis manuell auf ▶ getippt wurde. Startet jetzt
  automatisch den ersten Track des konfigurierten Ordners.

## 2026-08-14

- Fix: `/musik`-Play-Knopf warf `NotSupportedError`, wenn er geklickt
  wurde, bevor der erste Status vom Server geladen war (Wettlauf mit
  `player.src`) — alle Wiedergabe-Buttons sind jetzt bis dahin
  deaktiviert.
- `/musik`: bisher stillschweigend verschluckte Audio-Wiedergabefehler
  (Ladefehler, abgelehntes `play()`) werden jetzt im Action-Feld und in
  der Browser-Konsole angezeigt statt lautlos zu verschwinden — Fix aus
  dem vorigen Punkt reichte laut Nutzer-Test allein nicht, Ursache noch
  offen.
- Fix: `/musik` blieb auf einem frischen Browser-Origin stumm — der
  Play-Knopf startet `player.play()` jetzt synchron als Teil der
  Klick-Geste statt erst später aus dem Status-Poll (Autoplay-Policy).
- Nachrichten-Pause-MP3-Ordner und Musik-Player durchsuchen jetzt auch
  Unterordner, bis zu 5 Ebenen tief (vorher nur der Ordner selbst).

## 2026-08-13

- `/musik` aufgeräumt: nur noch ein Play-Knopf (der native Browser-
  Play-Knopf kam vorher dem großen Play/Stop-Button in die Quere), die
  Funktion heißt jetzt schlicht "Player" statt "Musiksammlung", kein
  Banner-Bild mehr auf der Seite, angezeigter Musik-Ordner ist jetzt der
  echte Host-Pfad aus `.env` statt des Container-Pfads, neuer
  "Pfad ändern"-Knopf statt Text-Link.
- Musiksammlung-Seite (`/musik`) bekommt einen eigenen eingebetteten
  Audio-Player — man muss zum Zuhören nicht mehr zur Player-Seite
  zurückspringen.

## 2026-08-12

- Musik-Library: Duplikat-Erkennung (`music_query.find_duplicates()`,
  normalisiertes Artist+Titel-Metadaten-Match) über neuen
  `GET /api/library/duplicates`-Endpoint, bewusst ohne UI-Anschluss.
- Musik-Library: Format-Unterstützung über MP3 hinaus auf FLAC, OGG,
  M4A, AAC, WAV und APE erweitert (Scan + Playback).
- Web-Interface: Basissprache auf Englisch umgestellt, Deutsch als
  externes Sprachpaket (`language/*.lng`, Windows-Sprachpaket-Analogie)
  ausgelagert.
- **Android:** UI-Basissprache ebenfalls auf Englisch umgestellt
  (`values/` = Englisch, `values-de/` = Deutsch, nativer Android-
  Ressourcenmechanismus); Update-Server-Pfad auf
  `blarks.de/radio/update` verschoben.
- Die drei Betriebs-Skripte auf eines reduziert: `check-radiosabbelnich.sh`
  und `run_radiosabbelnich.sh` als neue Subcommands `check`/`start` in
  `radiosabbelnich.sh` integriert, beide alten Dateien gelöscht.
- Musik-Library Phase 3: BPM-Schätzung (`music_bpm.py`, aubio) im
  Scan-Pass ergänzt, `schnell`/`langsam`-Buttons auf `/musik` aktiviert.
- Bugfix Nachrichten-Pause: eine laufende MP3 wird nicht mehr hart
  abgebrochen, wenn `window_minutes` mittendrin abläuft — sie spielt
  jetzt immer bis zum Ende, der Rückweg zum Sender passiert danach.
- Musik-Library Phase 2: Query-Layer (`music_query.py`, Artist-/
  Genre-Teilstring-Filter) an die `rock`/`klassik`/Queen/Pavarotti-
  Buttons angebunden; "Jetzt läuft" zeigt bei Query-Wiedergabe
  Artist – Titel statt nur den Dateinamen.
- `radiosabbelnich.sh status`: warnt jetzt rot bei nicht erreichbarem
  konfigurierten Hostnamen oder fehlendem Internet/DNS (Ping gegen
  `hamburg.de`), zeigt den konfigurierten `ICECAST_HOSTNAME` und den
  MP3-Ordner-Status der Nachrichten-Pause.
- Alle `.py`-Module von Repo-Root nach `python/` verschoben (reines
  Aufräumen); Web-Interface-Button-Labels präzisiert ("VLC" → "VLC
  Stream", "Handy" → "Handy Fernsteuerung"); QR-Code zum Download der
  Android-APK im README ergänzt (fester Alias `radiosabbelnich-latest.apk`
  auf blarks.de, überlebt künftige Android-Builds).
- Musik-Library Phase 1: rekursiver ID3-Scan (`music_scan.py`, mutagen)
  der Musiksammlung in eine eigene SQLite-DB (`music_library.db`,
  inkrementell per mtime/Größe), Kategorie-/Favoriten-Buttons auf
  `/musik` von einer Reihe in zwei benannte Gruppen aufgeteilt.

## 2026-08-11

- Musiksammlung-Modus, Grundgerüst: Umschalter Radio ⇄ Musiksammlung
  (STT/VAD im Musik-Modus komplett aus), minimaler Player (Play/Stop/
  Zurück/Nächster über einen konfigurierbaren, nicht-rekursiven Ordner),
  neue Seite `/musik`, gemeinsame Breadcrumb-Ordnerauswahl
  (`folder_browse.py`) für Nachrichten-Pause- und Musiksammlung-Pfad.
  Dazu `radiosabbelnich.sh` als erster Betriebs-Wrapper (`start`/`stop`/
  `restart`/`status`).
- README-Serverpfade aktualisiert.
- Musik-Library-Feature als Idee in die Roadmap aufgenommen (reine
  Doku, noch ohne Code).

## 2026-08-09

- Preflight-Checks lesen `NEWS_MP3_FOLDER` jetzt über `docker compose
  config` statt `.env`/Shell selbst zu parsen — behebt einen Bug, bei
  dem ein wörtlicher Backslash im Pfad (z.B. `80\'s`) von der Shell
  "korrigiert" wurde, aber nicht von Docker Compose, wodurch der Check
  fälschlich grün war und der eigentliche Mount trotzdem scheiterte.
- **Android:** Brücken-Build- und Landingpage-Update dokumentiert; neuer
  Server-Update-Pfad (blarks.de) dokumentiert.
- Projekt umbenannt: *KeinSabbelRadio* → *RadioSabbelNich*.

## 2026-08-08

- Namensänderung dokumentiert (README-Hinweis, VERSION-Bump).
- **Android:** Update-Server von einem separaten, nur per Tailscale
  erreichbaren Dienst auf den ganz normalen, öffentlich erreichbaren
  Webserver von blarks.de umgezogen (bewusste Ausnahme von "kein
  öffentlicher Betrieb" — verteilt nur eine App-Binary ohne
  Nutzerdaten, siehe `android-app/CLAUDE.md`) + Bugfixes.
- Projekt umbenannt: *RadioZapper* → *KeinSabbelRadio* (neues Banner-
  Bild passend zum neuen Namen).
- **Android:** Feature-Parität mit dem Docker-Dienst als fertig
  dokumentiert (README-Abschnitt ergänzt).
- **Android:** Review-Befunde aus Phase 8 (Code-Review) umgesetzt.
- **Android:** Mehrsprachiges STT samt geführtem Kalibrierungs-Wizard
  (Phase 7, in zwei Schritten: Grundgerüst, dann Wizard).
- **Android:** Audio-Fingerprinting gegen Jingles/Werbung (Phase 4).
- **Android:** Nachrichten-Pause / News-Break (Phase 6).
- **Android:** Prebuffering für lückenlosere Senderwechsel (Phase 3).
- **Android:** Optik-Angleichung (Phase 5) + Kodi-/M3U-Sender-Import.

## 2026-08-07

- **Android:** Watchdog für tote/nicht erreichbare Sender ergänzt.
- **Android:** 8-Phasen-Fahrplan für Feature-Parität mit dem
  Docker-Dienst aufgestellt.
- **Android:** feste Senderliste durch persistente Senderverwaltung
  ersetzt.
- **Android:** Cooldown pro Sender nach einem Wechsel + konfigurierbarer
  OTA-Update-Mechanismus.
- **Android:** Build-Zeitstempel in der App-UI angezeigt.
- **Android:** komplett neues, natives Kotlin/Android-Prototyp-Projekt
  gestartet (`android-app/`, media3 + Vosk-Android) — bildet dasselbe
  Grundprinzip (mehrere Sender, Sprache/Musik-Erkennung, Auto-Switch)
  lokal auf dem Handy nach; MVP mit geglätteter Sprache/Musik-Erkennung
  und Auto-Switch, danach als eigenständiges Projekt neben dem
  Docker-Dienst weitergepflegt (siehe `CLAUDE.md`).

## 2026-08-06

- Nachrichten-Pause: wiederholt zuletzt gespielte MP3s vermeiden statt
  rein zufällig neu zu wählen.
- Zwei Bugs aus der Englisch-Kalibrierung behoben, zusätzlicher
  englischer Vosk-Modell-Mount.
- Mehrsprachiges STT: Grundgerüst (Kategorie → Sprache statt einer
  einzigen globalen Sprache) und geführter Kalibrierungs-Wizard zur
  Ermittlung der Konfidenz-Schwelle pro Sprache.
- Live-Statusanzeigen (Bullshitometer, STT-Balken, Fingerprint-Chip)
  und Versionsanzeige im Web-Interface.
- Ressourcen-Verbrauch (RAM/CPU/DB-Größe) auf der Config-Seite.
- Playout-Delay: echte Vorausschau für die Sprache-Erkennung (Audio
  wird verzögert ausgegeben, damit die Klassifikation VOR der Ausgabe
  passiert, nicht danach) — vorgewärmte Sender wechseln dadurch ohne
  hörbaren Ruck.

## 2026-08-05

- Repo aufgeräumt: Nicht-`.py`-Dateien nach `pics/`, `web/`, `data/`
  sortiert statt alles am Root.
- Zweisprachiges Web-Interface (Deutsch/Englisch), "Unsortiert"-
  Kategorie auf der Config-Seite einklappbar.
- Nachrichten-Pause: lädt innerhalb eines Zeitfensters so lange weitere
  MP3s nach, bis `window_minutes` abgelaufen ist, statt nach der ersten
  Datei sofort zurückzuschalten.

## 2026-08-04

- `install_radiozapper.sh` zu `check-radiozapper.sh` umbenannt, volle
  Preflight-Diagnose ergänzt (Vorläufer der heutigen
  `radiosabbelnich.sh check`).
- PWA-Unterstützung: Web-Interface als installierbare App
  (Zurück/Weiter-Steuerung ergänzt).

## 2026-08-03

- QR-Code für die Stream-Adresse, STT-Sprachfilter (erste Version),
  TLS-Unterstützung fürs Web-Interface, diverse UI-Verbesserungen.
- Nachrichten-Pause-Feature: zur vollen/halben Stunde eine lokale MP3
  statt eines Senders.
- `CLAUDE.md` mit Architektur-Notizen und Konventionen angelegt.
- Sender-Import: neue Sender werden deaktiviert übernommen, Prüfung auf
  dauerhaften (nicht nur anfänglichen) Audiofluss.
- Watchdog gegen tote Sender, Logging überarbeitet.

## 2026-08-02

- "Alle deaktivieren"-Knopf pro Sender-Kategorie.
- Sender-Import aus M3U-Playlist (Kodinerds-Kodi-Radioliste), Knopf zum
  Leeren der Fingerprint-Clip-DB.
- Fingerprint-Algorithmus überarbeitet: echte 2D-Landmarken
  (Constellation-Map) statt des schlicht lautesten Frequenz-Bins pro
  Frame — deutlich weniger Fehltreffer auf echtem Broadcast-Radio.
- Puffer-Einstellungen konfigurierbar, "Zapping-Fehler" schaltet direkt
  zurück, README-Warnung (privater Betrieb, Urheberrecht).
- Vorausschauendes Puffern der nächsten 5 Sender für flüssigere
  Wechsel.
- "Sabbelfilter deaktivieren"-Knopf: automatische Erkennung komplett
  pausierbar.
- "Zapping-Fehler"/"Gesabbel"-Knöpfe, Hero-Bild, README ersetzt die
  bisherige HANDOVER-Datei.
- Fingerprint-Fehlalarm behoben: "Zapping-Fehler"-Knopf zum Löschen
  eines falsch gelernten Clips, Audio-Mitschnitt für künftige Treffer.
- Now-Playing-Fallback über Sender-eigene Webseiten (z.B. R.SH), wenn
  keine ICY-Metadaten kommen.
- Umschalt-Latenz reduziert, Now-Playing-Anzeige über ICY-Metadaten.
- Config-Seite für Sender-Verwaltung (aktivieren/deaktivieren/CRUD,
  Kategorien).
- Player ins Web-Interface eingebettet (alles auf einer Seite).
- Silero-VAD-Ladefehler behoben, Umschalt-Zuverlässigkeit verbessert.
- Icecast-Konfiguration gefixt (Location/Admin-Kontakt, Neustart-
  Absturzschleife im Basis-Image behoben).
- Initial Commit: Umstellung auf Stereo-Ausgabe, Web-Interface, erste
  Session-Doku.
