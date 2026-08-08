# RadioZapper MVP (Android)

> ⚠️ **Aktiver Prototyp im Bau, kein fertiges Produkt.** Wird laufend
> weiterentwickelt (siehe `../SESSION.md` fuer den aktuellen Verlauf) -
> Verhalten, Konstanten und sogar die Architektur einzelner Teile koennen
> sich zwischen zwei Sessions noch aendern.

Eigenstaendiger Android-Prototyp, der dasselbe Grundprinzip wie das
RadioZapper-Docker-Projekt (`../`) auf dem Handy abbildet: Radiostream
abspielen und per Vosk (Speech-to-Text) grob erkennen, ob gerade Sprache
oder Musik laeuft, und bei Sprache automatisch weiterschalten. **Kein
Web-Wrapper, keine Abhaengigkeit von der Docker-Instanz** - reines
natives Kotlin/Android.

Ursprünglich bewusst minimal gestartet (kein Watchdog/Ban-System, kein
News-Break, keine Settings-UI), mittlerweile aber entlang des
Feature-Parität-Fahrplans (`RadioZapper_Android_Fahrplan.md`) gewachsen -
Watchdog, Vorwärmung, M3U-Import und Nachrichten-Pause sind inzwischen
umgesetzt, siehe Feature-Liste unten. Weiterhin bewusst kein Ban-System,
das eine Sperre über einen App-Neustart hinweg merkt (siehe "Bekannte
Grenzen").

## Was funktioniert (live im Emulator getestet, siehe unten)

- **Persistente Senderverwaltung** (`model/StationRepository.kt`, Vorbild
  `stations_store.py` im Docker-Projekt) - Sender als `{id, name, url,
  category, enabled}`, gespeichert als JSON-Datei (`filesDir/
  stations.json`), nicht mehr im Code hartcodiert. Eigener Bildschirm
  "⚙️ Sender verwalten" (`station/StationManagementActivity.kt`): Liste
  gruppiert nach Kategorie (`Lokal`/`National`/`International`/
  `Unsortiert`, feste Reihenfolge, auch leere Kategorien sichtbar),
  Enabled-Checkbox pro Sender, Bearbeiten/Loeschen-Buttons, ein
  wiederverwendeter Add/Edit-Dialog. Mindestens 1 Sender muss bestehen
  bleiben (Loeschsperre, exakte Paritaet zum Docker-Vorbild). Rotation =
  aktivierte Sender alphabetisch nach Name, wie im Docker-Projekt.
  Beim allerersten Start (keine `stations.json` vorhanden) werden die
  bisherigen 3 Sender (Deutschlandfunk/1LIVE/SWR3) als Startbestand
  geschrieben statt geloescht - fuer Bestandsnutzer aendert sich beim
  Update auf diese Version sichtbar nichts.
- Wiedergabe ueber ExoPlayer (media3 1.4.1)
- Vosk-Modell-Download: deutsches Kleinmodell `vosk-model-small-de-0.15`
  (~45MB, dasselbe Modell wie im Docker-Projekt) wird beim ersten Start
  per Button-Klick heruntergeladen und in den App-internen Speicher
  entpackt - NICHT im APK gebundlet
- Parallele Analyse: eine zweite, unabhaengige Dekodierung desselben
  Streams (MediaExtractor/MediaCodec) wird auf 16kHz-Mono resampelt und
  laufend in Vosk (`Recognizer.acceptWaveForm`) gefuettert; erkannter
  Text (nicht-leer) = Sprache-Signal fuer den jeweiligen 0.5s-Chunk
- **Geglaetteter Status** (`StreamAnalyzer.kt`): gleitendes
  Mehrheitsvotum ueber die letzten `SMOOTHING_WINDOW_SECONDS=4.0`
  Sekunden statt Einzel-Chunk-Anzeige, mit Hysterese
  (`RATIO_TO_CONFIRM_SPEECH=0.65` / `RATIO_TO_CONFIRM_MUSIC=0.30`) gegen
  Flackern nahe der 50%-Grenze. Ersetzt eine fruehere strikte
  "N Sekunden ohne Unterbrechung"-Serie, die bei ganz normalen kurzen
  Sprechpausen zu haeufig zurueckgesetzt wurde (live beobachtet).
- **Automatisches Umschalten** (`PlaybackService.kt`): bei bestaetigter
  Sprache springt die App zum naechsten Sender im Ring - wie das
  Docker-Projekt schaltet sie WEG von Sprache/Moderation, HIN zu Musik.
  Der Ring liest `StationRepository.activeStations()` bei jedem Versuch
  frisch (kein Request/Pop-Mechanismus wie im Docker-Projekt noetig, siehe
  Architektur-Abschnitt unten). **Cooldown pro Sender**
  (`STATION_COOLDOWN_SECONDS=60`): ein wegen Sprache verlassener Sender
  wird beim Ringdurchlauf fuer diese Zeit uebersprungen, statt sofort
  wieder dran zu sein (Moderation/Gesang ist ja vermutlich noch nicht
  vorbei) - endet automatisch mit der Zeit ODER sobald der Sender selbst
  wieder Musik bestaetigt. Obergrenze gegen Endlosschleife: sind entweder
  alle Sender einmal ohne Treffer probiert ODER alle uebrigen gerade im
  Cooldown, folgt eine kurze Pause (`AUTO_SWITCH_PAUSE_SECONDS=20`) statt
  endlos weiterzuspringen. **Reagiert auch auf Aenderungen aus der
  Verwaltungs-Activity**: wird der GERADE LAUFENDE Sender dort deaktiviert
  oder geloescht, schaltet der Service automatisch auf den ersten
  aktivierten Sender weiter (oder stoppt sauber, falls keiner mehr aktiv
  ist) - live getestet, siehe unten.
- **Watchdog gegen tote/nicht antwortende Sender** (`PlaybackService.kt`,
  Vorbild `dead_until`/`alive_stations()` im Docker-Projekt) - ein
  `Player.Listener` an ExoPlayer erkennt zwei Faelle: `onPlayerError()`
  (hartes Signal, sofortige Sperre) und ununterbrochenes
  `Player.STATE_BUFFERING` laenger als `BUFFERING_TIMEOUT_SECONDS=15`
  (deckt "verbindet, liefert aber nie Daten" ab, das nie einen Error
  ausloest). Eigene Sperr-Map (`deadUntil`, `STATION_DEAD_LOCK_SECONDS=
  300` = 5 Min., wie im Docker-Projekt), bewusst getrennt vom
  Sprache-Cooldown oben (`StationLockReason.SPEECH_COOLDOWN` vs. `.DEAD`) -
  beide Sperrgruende sind in der Play-Liste unterscheidbar ("⏸ Pause wegen
  Sprache" / "⚠ Antwortet nicht"). Sind ALLE aktiven Sender durch
  irgendeinen der beiden Gruende gesperrt, werden beide Sperrlisten
  geleert und einmal neu versucht, statt haengenzubleiben (wie
  `dead_until.clear()` im Docker-Projekt). Manuelle Sender-Wahl
  (`manualPlay()`) hebt beide Sperren fuer den gewaehlten Sender auf -
  expliziter Nutzerwunsch schlaegt Automatik.
- Foreground Service mit Notification, damit Wiedergabe+Analyse
  weiterlaufen, wenn die App im Hintergrund ist; UI zeigt den aktuellen
  Sender live mit, auch wenn der automatische Wechsel ihn geaendert hat
- Einfache UI: Senderliste mit Play-Buttons (nur aktivierte Sender, flach,
  kein Kategorie-Gruppieren - das ist Aufgabe der Verwaltungs-Activity),
  Stop-Button, Statusanzeige, Modell-Download-Fortschritt, Button
  "Sender verwalten"
- **Optik an das Web-Interface angeglichen** (`MainActivity.kt`): Banner-Bild
  (dieselbe Datei wie im Web-Interface) und Türkis-Akzentfarbe (`#1ABC9C`).
  Live-Balken "🤥 Bullshitometer" unter den Steuerbuttons zeigt die rohe
  Sprache-Wahrscheinlichkeit (`StreamAnalyzer.speechRatio`, VOR der
  Hysterese) mit demselben grün→rot-Farbverlauf wie im Web. Button "⚡
  ZAPPEN!" neben "■ Stopp" fuer den manuellen Sofort-Wechsel (ruft
  dieselbe Ring-Logik wie ein automatisch erkannter Sprache-Treffer auf).
  Kein separater STT-Meter (Android hat nur einen Detektor, kein VAD+STT-
  Kombi wie das Docker-Projekt) und kein Fingerprint-Chip (Fingerprinting
  ist noch nicht umgesetzt, siehe Fahrplan Phase 4).
- **Vorgewärmter Kandidat für lückenlosere Wechsel** (siehe eigener
  Architektur-Abschnitt unten) - ein zweiter, paralleler ExoPlayer hält
  immer den laut Ringlogik wahrscheinlichsten nächsten Sender bereits
  vorbereitet. Trifft ein Wechsel (automatisch, ZAPPEN! oder Watchdog)
  diesen Kandidaten - der Regelfall - läuft er praktisch ohne
  Verbindungslücke statt mit dem bisherigen Kaltstart.
- **Sender-Import aus einer M3U-Playlist** (`importer/StationImporter.kt`,
  Vorbild `station_import.py`) - Textfeld "Sender-Import" in der
  Verwaltungs-Activity, vorbelegt mit derselben Default-Playlist wie im
  Docker-Projekt (Kodinerds-Kodi-Radioliste, `http://bit.ly/kn-kodi-radio`,
  frei aenderbar). Laedt die Playlist, parst `#EXTINF`-Eintraege, filtert
  gegen bereits vorhandene Sender (Name/URL) UND Duplikate innerhalb der
  Playlist selbst, uebernimmt den Rest deaktiviert in die Kategorie
  "Unsortiert" - genau wie beim Docker-Projekt landet ein Import nie
  ungefragt in der laufenden Rotation. Bewusst OHNE Erreichbarkeitspruefung
  beim Import selbst (siehe "Architektur in Kuerze" unten fuer die
  Begruendung). Dafuer ein separater Button "🔍 Unsortierte Sender pruefen"
  (`importer/StationReachabilityChecker.kt`), der genau diese Pruefung fuer
  alle Sender in "Unsortiert" nachtraegt und nicht erreichbare Sender mit
  einem Badge "⚠ nicht erreichbar" markiert - rein informativ, es wird
  nichts automatisch geloescht.
- **Nachrichten-Pause / News-Break** (`newsbreak/NewsBreak.kt`,
  `newsbreak/NewsBreakSettings.kt`, siehe eigener Architektur-Abschnitt
  unten) - zur vollen/halben Stunde spielt statt des Radiosenders fuer ein
  konfigurierbares Zeitfenster (Default 2 Minuten, wie im Docker-Projekt)
  eine zufaellige MP3 aus einem selbst gewaehlten Ordner (Storage Access
  Framework). Neue Sektion "📰 Nachrichten-Pause" auf der Startseite:
  Aktiviert-Schalter, "📁 Ordner waehlen", Fensterlaenge in Minuten. Mehrere
  Dateien pro Fenster werden nachgeladen, bis das Fenster um ist (nicht nur
  eine, siehe Docker-Projekt-Historie), dieselbe Datei kommt nicht direkt
  zweimal hintereinander. Danach automatische Rueckkehr zum vorher
  laufenden Sender - ein manueller Sendertipp oder "⚡ ZAPPEN!" waehrend der
  Pause beendet sie sofort.
- **Build-Zeitstempel in der UI** (`Build: YYYY-MM-DD HH:MM` direkt unter
  dem App-Titel, `BuildConfig.BUILD_TIME`) - entsteht automatisch bei
  jedem Build. Zweck: von aussen erkennbar, ob eine gerade installierte
  APK noch ein aelterer Stand ist (Anlass: Auto-Switch schien auf einem
  Test-Handy "nicht zu funktionieren" - war eine veraltete Installation).
- **Update-Mechanismus mit konfigurierbarer Adresse** (`update/
  UpdateManager.kt`, siehe eigener Abschnitt unten) - Textfeld "Update-
  Server:" auf der Startseite (Default aktuell: Tailscale-Adresse dieses
  Hosts, spaeter voraussichtlich `https://blarks.de`). Button "Nach
  Update suchen" prueft den dort eingetragenen Server, laedt bei Bedarf
  die neue APK und stoesst den System-Installer an. Kein Play Store,
  keine Signatur-Pruefung ueber die Debug-Signierung hinaus.

### Live-Testergebnis (Android-Emulator auf diesem Host, API 34 x86_64)

Deutschlandfunk (Sprache) gestartet → nach ~15s bestaetigt "Sprache"
erkannt → automatisch zu 1LIVE gewechselt → dort ueber 1 Minute stabil
"🎵 Musik", kein Nachflackern, kein weiteres Springen. Keine Abstuerze/
Exceptions im Logcat. Details und weitere Durchlaeufe siehe
`../SESSION.md`, Eintrag "2026-08-07 — Android RadioZapper MVP".

Zweiter Durchlauf (nach Ergaenzung des Build-Zeitstempels, gleicher
Emulator): Deutschlandfunk → 1LIVE → SWR3 → Deutschlandfunk → 1LIVE →
SWR3 im Kreis, jeweils nach "Sprache erkannt" - alle drei Sender lieferten
also irgendwann einen bestaetigten Sprache-Treffer (bei 1LIVE/SWR3
vermutlich Gesang, siehe "Bekannte Grenzen" unten), der Zaehler wurde
aber zwischendurch durch Musik-Phasen immer wieder auf 0 zurueckgesetzt,
bevor die Obergrenze (`AUTO_SWITCH_PAUSE_SECONDS`) je griff. Zeigt: die
Umschalt-Logik selbst funktioniert zuverlaessig, das haeufige Springen in
diesem Fall kam von echtem Radioinhalt (Musik+Moderation gemischt), nicht
von einem Bug.

**Dritter Durchlauf (Senderverwaltung, frischer Install)**: die 3
geseedeten Sender erscheinen identisch zu vorher unter "National" in
beiden Bildschirmen. Neuer Sender ("SWR3 Test", Kategorie "International")
per Dialog angelegt → erscheint sofort in der flachen Play-Liste UND in
der Verwaltungs-Activity, `adb shell run-as com.radiozapper.mvp cat
files/stations.json` zeigt korrektes, menschenlesbares JSON mit
generierter id `swr3-test`. 1LIVE gestartet, waehrend es lief in der
Verwaltungs-Activity deaktiviert → Logcat: "Senderliste geaendert, '1LIVE
...' nicht mehr aktiv - schalte auf 'Deutschlandfunk ...'" - automatischer
Wechsel funktioniert. Sender geloescht bis auf einen einzigen uebrig →
Loeschversuch auf dem letzten liefert korrekt "Mindestens ein Sender muss
konfiguriert bleiben." statt die Liste zu leeren. App per `force-stop`
beendet und neu gestartet → aller Stand (inkl. des angelegten Testsenders)
persistiert korrekt. Keine Abstuerze in der gesamten Sequenz (Crash-Log-
Buffer nach jedem Schritt leer geprueft).

**Vierter Durchlauf (Watchdog)**: Sender mit nicht routbarer IP ueber die
Verwaltungs-Activity angelegt, abgespielt → nach exakt
`BUFFERING_TIMEOUT_SECONDS` (15s) Logcat "Kein Fortschritt seit 15s -
'TOT-Test' antwortet nicht" → automatischer Wechsel zum naechsten Sender,
Play-Liste zeigt "⚠ Antwortet nicht" unter dem toten Sender. Manuelles
erneutes Antippen hebt die Sperre sofort auf (Badge verschwindet,
"Verbinde…" startet neu), nach weiteren 15s erneut korrekt gesperrt.
Eskalationstest: BEIDE aktiven Sender auf tote URLs gesetzt → Logcat
"Alle 2 aktiven Sender gesperrt - hebe beide Sperrlisten auf und probiere
erneut" statt Haengenbleiben - danach stabiler ~15s-Zyklus zwischen beiden
(weiterhin toten) Sendern, kein Absturz (erwartetes Verhalten, wenn
tatsaechlich alles tot ist). Regressionscheck: URL zurueckgesetzt, 25s
stabil "🎵 Musik", kein Fehlalarm auf einem gesunden Sender.

## Was NICHT funktioniert / nicht im Scope

- Kein Ban-System, das eine Sperre ueber einen App-Neustart hinweg merkt
  (der Watchdog unten ist reines In-Memory-Timing, siehe "Bekannte
  Grenzen")
- Kein Play-Store-taugliches Icon (einfaches Vektor-Icon)
- Keine Fehlerbehandlung fuer jeden Edge Case (z.B. Stream ohne
  erkennbaren Audio-Track, Wechsel des Mobilfunknetzes mitten im Stream)

## Installation

Fertige Debug-APK liegt nach jedem Build zusaetzlich an einem festen,
leicht auffindbaren Pfad (siehe `../CLAUDE.md`, Abschnitt
"Android-Prototyp"):

```
android-app/radiozapper.apk
```

(daneben weiterhin auch unter dem von Gradle erzeugten
`app/build/outputs/apk/debug/app-debug.apk` - identischer Inhalt, aber
tief verschachtelt und gitignored).

Per USB (ADB, falls Entwickleroptionen aktiv sind):

```bash
adb install -r android-app/radiozapper.apk
```

Alternativ die APK-Datei per Kabel/Cloud aufs Handy kopieren und dort
oeffnen - Android fragt dann nach der Berechtigung "Installation aus
unbekannten Quellen erlauben" fuer die jeweilige App (Dateimanager/
Browser), aus der heraus installiert wird.

Beim ersten Start fragt die App nach der Notification-Berechtigung
(Android 13+, sonst erscheint keine Notification und der Foreground
Service kann vom System eher beendet werden) und zeigt den Button zum
Modell-Download an - erst danach liefert die Sprache/Musik-Erkennung
etwas (ohne Modell laeuft nur die Wiedergabe, Status bleibt "Gestoppt").

## Bauen und Testen

```bash
cd android-app
./gradlew assembleDebug
cp app/build/outputs/apk/debug/app-debug.apk radiozapper.apk
echo "{\"buildTime\": \"$(date '+%Y-%m-%d %H:%M')\"}" > version.json
```

Der letzte Schritt versorgt den Update-Mechanismus (siehe eigener
Abschnitt unten) mit einem aktuellen Versions-Stempel - ohne ihn denkt
die App weiterhin, der alte Stand sei der neueste.

Benoetigt Android SDK (`local.properties` mit `sdk.dir=...` oder
`ANDROID_HOME`/`ANDROID_SDK_ROOT` gesetzt) sowie Internetzugriff beim
ersten Build (Gradle-Wrapper-Distribution + Abhaengigkeiten aus
`google()`/`mavenCentral()`).

Fuer echte Laufzeit-Tests (nicht nur Kompilieren) steht auf diesem Host
seit 2026-08-07 ein Android-Emulator zur Verfuegung (`~/Android/Sdk`,
AVD `test_device`, API 34 google_apis x86_64, headless/ressourcenschonend
konfiguriert). Kurzform:

```bash
emulator -avd test_device -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect &
adb wait-for-device
adb install -r radiozapper.apk
adb logcat -s PlaybackService:*   # zeigt jeden Sprache/Musik-Wechsel
```

## Architektur in Kuerze

- `RadioZapperApplication.kt` - einziger Zweck: `StationRepository.init()`
  garantiert VOR jeder Komponente aufrufen (`Application.onCreate()` laeuft
  immer zuerst) - kein "wer zuerst dran ist, ruft init() auf"-Muster in
  den einzelnen Komponenten, das ein neuer Einstiegspunkt leicht vergessen
  koennte.
- `model/Station.kt` - `data class Station(id, name, url, category,
  enabled)`. `id` ist stabil (einmal vergeben, ueberlebt Umbenennungen),
  Rotation/Cooldown referenzieren ausschliesslich darueber.
- `model/Categories.kt` - feste Kategorienliste (`Lokal`/`National`/
  `International`/`Unsortiert`, reduziertes Set ggue. den 7 Kategorien
  des Docker-Projekts), selbst weiterhin nur im Code aenderbar.
- `model/StationRepository.kt` - Singleton, haelt die Senderliste als
  `StateFlow<List<Station>>`, persistiert als JSON-Datei (`filesDir/
  stations.json`, atomarer Schreibvorgang via `Files.move(...,
  ATOMIC_MOVE)`). CRUD (`addStation`/`updateStation`/`setEnabled`/
  `deleteStation`), ID-Vergabe per Slugify+Kollisionssuffix (analog
  `stations_store.py`s `_slugify`/`_unique_id`), Startbestand nur wenn die
  Datei noch nicht existiert.
- `station/StationManagementActivity.kt` - eigener Bildschirm fuer CRUD,
  kategorie-gruppierte Liste (volles Neu-Aufbauen bei jeder Aenderung,
  kein RecyclerView - Datenmenge klein), Add/Edit-`AlertDialog`, plus die
  Import-/Check-Sektion (siehe eigener Abschnitt unten).
- `importer/StationImporter.kt` - laedt/parst eine M3U-Playlist und
  uebernimmt neue Sender per `StationRepository.bulkAdd()` (siehe eigener
  Abschnitt unten fuer Details).
- `importer/StationReachabilityChecker.kt` - separater, manuell
  ausgeloester Erreichbarkeits-Check fuer Sender der Kategorie "Unsortiert"
  (siehe eigener Abschnitt unten).
- `vosk/VoskModelManager.kt` - Download+Entpacken des Vosk-Modells nach
  `filesDir`, Fortschritt als `StateFlow<ModelState>`
- `playback/StationLockReason.kt` - Enum `{SPEECH_COOLDOWN, DEAD}` fuer
  die beiden unterscheidbaren Sperrgruende (siehe `PlaybackService`).
- `playback/PlaybackService.kt` - Foreground Service, haelt ExoPlayer
  (Wiedergabe) UND `StreamAnalyzer` (Analyse); reagiert auf den
  geglaetteten Status mit automatischem Umschalten, auf Aenderungen aus
  `StationRepository.stations` (siehe oben) UND auf ExoPlayer-Fehler/
  Buffering-Timeouts (Watchdog, siehe eigener Abschnitt unten); exponiert
  `currentStation`/`status`/`speechRatio`/`lockedStations` als StateFlows
  fuer die UI; `manualSkip()` fuer den "⚡ ZAPPEN!"-Button (ruft intern
  dieselbe `attemptAutoSwitch()`-Logik wie ein automatisch erkannter
  Sprache-Treffer auf); Notification via `NotificationCompat`/
  `ServiceCompat.startForeground`
- `analysis/StreamAnalyzer.kt` - **eigene** MediaExtractor/MediaCodec-
  Dekodierung desselben Stream-URLs nur fuer die Analyse (unabhaengig
  vom ExoPlayer der Wiedergabe), Downmix+Resample auf 16kHz-Mono
  (`MonoResampler.kt`), Fuetterung von Vosk, gleitendes Mehrheitsvotum
  mit Hysterese fuer den geglaetteten Sprache/Musik-Status. Der rohe
  Anteil VOR der Hysterese (`speechRatio`) wird zusaetzlich als eigener
  `StateFlow<Double?>` exponiert - Basis fuers "🤥 Bullshitometer" in der UI
  (`null` = idle/noch kein volles Glaettungsfenster).
- `MainActivity.kt` - bindet an den Service, zeigt Senderliste (reaktiv
  aus `StationRepository`), Status, Bullshitometer und Modell-Fortschritt
  live an, Buttons zur Verwaltungs-Activity und fuer ZAPPEN!/Stopp

### Bewusste Vereinfachung ggue. dem Docker-Projekt: zwei Dekodierungen

Das Docker-Projekt (`../CLAUDE.md`, Abschnitt "Audio-Pfad") nutzt einen
einzigen ffmpeg-Prozess mit zwei Ausgabe-Pipes (Wiedergabe + Analyse aus
derselben Dekodierung). Dieses MVP macht das bewusst NICHT nach: ExoPlayer
und `StreamAnalyzer` oeffnen unabhaengig voneinander je eine eigene
Verbindung zum selben Stream-URL und dekodieren beide komplett selbst.
Vorteil: kein Eingriff in ExoPlayers internen Audio-Pfad noetig (kein
custom `AudioProcessor`/`RenderersFactory`), deutlich weniger
Fehlerflaeche. Preis: der Stream laeuft effektiv doppelt ueber das Netz
(Mobilfunk-Datenvolumen!). Falls das MVP weitergefuehrt wird, waere das
Anzapfen des ExoPlayer-Audio-Pfads via custom `AudioProcessor` der
naheliegende naechste Schritt.

### Persistenz: JSON-Datei statt Room

`StationRepository` speichert als flache JSON-Datei (`org.json`, schon
anderswo in der App genutzt, keine neue Dependency), bewusst nicht als
Room-Datenbank. Begruendung: das Vorbild `stations_store.py` ist selbst
ein flacher JSON-Store, keine SQL-Datenbank - Konsistenz zum Vorbild und
Angemessenheit an die Datenmenge (Dutzende Sender, keine Relationen,
keine komplexen Queries) zeigen in dieselbe Richtung. Room brächte einen
Compiler/KSP-Dependency, Entity/DAO-Boilerplate und Migrations-
Versionierung - fuer diese Groessenordnung unverhaeltnismaessig. Anders
als `stations_store.py` (muss auf direktes Schreiben ausweichen, weil
`stations.json` dort einzeln in Docker gebindmountet ist und
`os.replace()` ueber einen Mountpoint mit "Device or resource busy"
scheitert) kann die Android-Variante echt atomar schreiben: Temp-Datei
im selben Verzeichnis + `Files.move(..., ATOMIC_MOVE, REPLACE_EXISTING)`
- `filesDir` ist normaler lokaler Speicher ohne diese Einschraenkung,
eine bewusste Verbesserung gegenueber dem Original, keine blinde Kopie.

### Watchdog gegen tote/nicht antwortende Sender

Vorbild: `dead_until`/`alive_stations()` im Docker-Projekt (siehe dessen
`CLAUDE.md`, "Watchdog gegen tote Sender"), technisch aber an ExoPlayer
angepasst statt 1:1 uebersetzt. Zwei Erkennungswege, beide in
`PlaybackService`s `Player.Listener`:

- `onPlayerError()` - hartes Signal, sperrt sofort. Kein eigener
  Reconnect-Zaehler wie `STREAM_FAILURE_LIMIT` im Docker-Projekt: ExoPlayer
  versucht bei transienten Fehlern bereits intern zu reconnecten, bevor
  der Callback ueberhaupt feuert.
- Ununterbrochenes `Player.STATE_BUFFERING` laenger als
  `BUFFERING_TIMEOUT_SECONDS=15` - deckt "verbindet, liefert aber nie
  Daten" ab, das nie einen Error ausloest (der eigentliche BBC-Radio-
  Scotland-Fall aus dem Docker-Projekt: DASH-Manifest technisch gueltig,
  aber dauerhaft leer). Timer startet bei Eintritt in `STATE_BUFFERING`,
  bricht bei jedem anderen Zustand ab - deckt automatisch auch die
  anfaengliche "Verbinde…"-Phase mit ab.
- Bonus, praktisch kostenlos: `StreamAnalyzer`s eigenes, unabhaengiges
  `PlaybackStatus.ERROR` (vorher komplett ignoriert) haengt jetzt ebenfalls
  am selben Mechanismus.

Eigene Sperr-Map `deadUntil` (`STATION_DEAD_LOCK_SECONDS=300`, 5 Min. wie
im Docker-Projekt), bewusst getrennt von `stationCooldownUntil` (Sprache-
Cooldown) - `StationLockReason` haelt beide Gruende unterscheidbar,
sowohl fuer die Sperr-Logik als auch fuer die UI. Eine gemeinsame Auswahl-
Funktion (`nextAvailableStation()`/`findNextOrEscalate()`) behandelt
beide Sperrgruende einheitlich, inkl. der "alle gesperrt"-Eskalation
(beide Maps leeren statt haengenzubleiben, wie `dead_until.clear()`).
Manuelle Sender-Wahl (`manualPlay()`, von `MainActivity` statt `play()`
aufgerufen) hebt beide Sperren fuer den gewaehlten Sender auf.

### Vorwärmung: ein vorgewärmter Kandidat für lückenlosere Wechsel

Phase 3 aus dem Fahrplan. Bis dahin machte jeder Wechsel in `play()` einen
kompletten Kaltstart (`player.stop()` → neue `MediaItem` → `prepare()` →
Buffering), spürbare Lücke von 1-3s. Bewusst KEIN Pool mehrerer
vorgewärmter Kandidaten wie `PrebufferedSource`/`prebuffer_count` im
Docker-Projekt (siehe dessen `CLAUDE.md`) - der Ressourcenverbrauch
mehrerer gleichzeitiger Dauerstreams stünde für die auf dem Handy erwartete
Sendermenge in keinem Verhältnis zum Zusatznutzen gegenüber der
einfacheren Variante unten.

`refreshPreload()` bereitet über die bereits bestehende
`nextAvailableStation()`-Funktion (siehe Watchdog-Abschnitt oben) immer
GENAU den Sender vor, den die Ringlogik gerade als nächsten wählen würde -
ein zweiter, paralleler `ExoPlayer` (`preloadedPlayer`) puffert im
Hintergrund (`playWhenReady=false`). Rein reaktiv aufgerufen (kein
Polling-Timer wie `sync_prebuffer()` im Docker-Projekt), an den drei
Stellen, an denen sich einer der Einflussfaktoren ändert: aktueller Sender
(Ende von `play()`), Sperren (Ende von `refreshLockedStationsSnapshot()` -
deckt dadurch alle deren Aufrufer ab) und Senderliste (Ende von
`handleStationListChanged()`).

Da `attemptAutoSwitch()` (automatisch UND der ZAPPEN!-Button) sowie
`handlePlaybackFailure()` (Watchdog) exakt dieselbe Auswahl-Logik
verwenden, trifft `play()` in der überwiegenden Mehrheit der Fälle genau
diesen vorgewärmten Kandidaten und übernimmt ihn (Player-Instanzen tauschen,
alter Player wird released) statt eines Kaltstarts. `manualPlay()` (Sender
in der Liste antippen) profitiert nur zufällig, wenn der angetippte Sender
zufällig der vorhergesagte ist.

**Randfall, beim Testen live aufgetreten**: ein `Player.Listener` feuert
`onPlaybackStateChanged` nur bei KÜNFTIGEN Zustandswechseln, nicht
rückwirkend für den Zustand zum Zeitpunkt von `addListener()`. War der
übernommene Kandidat beim Wechsel noch nicht `STATE_READY`, würde der
`BUFFERING_TIMEOUT_SECONDS`-Watchdog nie zu laufen anfangen - die
Timer-Start-Logik ist deshalb in eine eigene `armBufferingWatchdog()`
extrahiert, die `play()` nach einer Übernahme explizit erneut aufruft,
falls der übernommene Player noch buffert. Ein eigener, minimaler
`preloadFailureListener` verwirft einen fehlgeschlagenen Kandidaten NUR aus
der Vorwärmung (`clearPreload()`) - löst bewusst NICHT den
`deadUntil`/Watchdog-Mechanismus aus, solange der Sender noch nicht
`current` ist.

### M3U-/Kodi-Sender-Import

Vorbild: `station_import.py` im Docker-Projekt (siehe dessen `CLAUDE.md`,
Abschnitt "Sender-Import"). `StationImporter.runImport()` laedt die
Playlist-URL (Default identisch zum Docker-Projekt: die Kodinerds-Kodi-
Radioliste, editierbar per Textfeld, in `SharedPreferences` gespeichert wie
bei `UpdateManager`), parst `#EXTINF:...,Name` + folgende URL-Zeile 1:1
nach demselben Muster wie `station_import.parse_m3u`, filtert Duplikate
(gegen die bestehende Senderliste UND innerhalb der Playlist selbst, je
nach Name UND URL) und uebernimmt den Rest per neuem
`StationRepository.bulkAdd()` (ein einziger Schreibvorgang fuer die ganze
Batch statt einem pro Sender) deaktiviert in die Kategorie "Unsortiert".

`HttpURLConnection` folgt Redirects nur INNERHALB desselben Protokolls
automatisch - die Default-URL (`bit.ly/...`) leitet per 301 von `http://`
auf `https://` um, was beim ersten echten Test kommentarlos die
Redirect-Antwortseite statt der Playlist lieferte (siehe `SESSION.md`,
2026-08-08). `fetchM3u()` folgt `Location`-Headern deshalb von Hand,
protokolluebergreifend inklusive.

**Bewusst OHNE** den 8s-Audiofluss-Check des Docker-Vorbilds
(`check_reachable()`) waehrend des Imports selbst - bei einer Playlist mit
hunderten Eintraegen waere das auf dem Handy zu langsam/akkuintensiv, UND
unnoetig: importierte Sender landen ohnehin deaktiviert in "Unsortiert",
eine kaputte URL darin ist harmlos bis zur bewussten Aktivierung durch den
Nutzer (anders als im Docker-Projekt, wo der Check verhindert, dass ein
toter Sender in die LAUFENDE Rotation gelangt). Stattdessen ein separater,
manuell ausgeloester Button "🔍 Unsortierte Sender pruefen"
(`StationReachabilityChecker.checkCategory()`, beschraenkt auf Kategorie
"Unsortiert") mit einer eigenstaendigen MediaExtractor/MediaCodec-
Dekodierschleife (kein Vosk noetig) - konzeptionell identisch zu
`check_reachable()`: Wall-Clock-begrenztes Zeitfenster
(`CHECK_WINDOW_MS=8000`), verlangt wird Audio bis in die letzten
`CHECK_TAIL_MS=3000` hinein, nicht nur "kam ueberhaupt was". Begrenzte
Parallelitaet per Kotlin-`Semaphore` (`CHECK_CONCURRENCY=3`, deutlich
weniger als die 10 im Python-Vorbild - MediaCodec-Dekodierung ist auf dem
Handy teurer, und nebenbei laeuft meist noch Wiedergabe + Vosk-Analyse im
selben Prozess). Ergebnis ist rein informativ (`unreachableIds`, nur
In-Memory fuer die aktuelle Sitzung) - ein Badge "⚠ nicht erreichbar" in
der Verwaltungs-Activity, **kein automatisches Loeschen**: das bleibt eine
bewusste Entscheidung ueber den bestehenden Loeschen-Button (ein
falsch-negativer Check, z.B. durch eine kurze Netzwerkstoerung, wuerde
sonst einen echten Sender unwiderruflich entfernen).

### Nachrichten-Pause / News-Break

Vorbild: `news_break.py` + dessen Einbindung im Docker-Projekt (siehe
dessen `CLAUDE.md`, Abschnitt "Nachrichten-Pause"). `newsbreak/NewsBreak.kt`
ist reine Domaenenlogik (`activeSlot()`: naechstgelegene :00/:30-Grenze,
aktiv wenn innerhalb der Fensterlaenge; `pickRandom()`: Zufallsauswahl mit
`RECENT_HISTORY_SIZE=3`-Ausschluss) - kennt weder Android/SAF noch
`PlaybackService`, exakt dieselbe Trennung wie im Vorbild und aus
demselben Grund: die Audio-Umschaltung (Sender pausieren, MP3 abspielen,
zurueckschalten) gehoert in `PlaybackService`, wo die gesamte
Switch-Infrastruktur schon existiert.

`newsbreak/NewsBreakSettings.kt` haelt den SAF-Ordnerzugriff
(`ACTION_OPEN_DOCUMENT_TREE`, `takePersistableUriPermission()` fuer
Persistenz ueber App-Neustarts hinweg - neue Dependency
`androidx.documentfile`) und die Einstellungen (`enabled`/`windowMinutes`,
Default `false`/`2.0` wie im Docker-Projekt). Bewusst OHNE die dortige
`enabled_hours`-Ruhezeiten-Option (Nutzerentscheidung - kleinere UI, passt
zum bisherigen "bewusst minimal"-Ansatz, spaeter ergaenzbar).

`PlaybackService.kt` haelt `_currentStation` waehrend einer Pause bewusst
unveraendert auf dem pausierten Sender (wie `current` im Docker-Projekt) -
Ringberechnung, Cooldown/Dead-Maps und die Vorwaermung (siehe oben) laufen
dadurch mit korrekten Daten weiter, ohne einen einzigen Sonderfall dafuer
zu brauchen. Ein periodischer Tick (`startNewsBreakTicker()`, alle 15s
waehrend der Wiedergabe) ersetzt die "jeder Hauptloop-Durchlauf"-Pruefung
des Docker-Projekts - Android hat keinen vergleichbaren Dauer-Takt.
Mehrere Dateien pro Fenster werden nachgeladen (`advanceNewsBreak()`), bis
das Fenster um ist - der historische Docker-Bug "News-Break spielte nur
eine MP3" ist damit von vornherein ausgeschlossen (live im Emulator
verifiziert, siehe `SESSION.md`). `manualPlay()`/`manualSkip()`
(ZAPPEN!) rufen `interruptNewsBreak()` zuerst auf und beenden eine
laufende Pause damit sofort - explizite Nutzerentscheidung schlaegt
Automatik, wie ueberall sonst in dieser Klasse.

Der `playerListener` unterscheidet dabei zwischen "MP3 hat ein Problem"
(`advanceNewsBreak()`: naechste Datei laden bzw. Pause beenden) und
"Sender hat ein Problem" (regulaerer Watchdog oben) - sonst wuerde ein
MP3-Fehler faelschlich den pausierten, aber gesunden Sender als tot
markieren. Kein `-re`/realtime-Sonderfall noetig (anders als im
Docker-Projekt, dessen ffmpeg-Pipe eine lokale Datei sonst in
Sekundenbruchteilen durchreicht) - ExoPlayer spielt eine lokale Datei
ohnehin in ihrem eigenen Tempo ab, unabhaengig von der Quelle.

## Update-Mechanismus (Tailscale, kein Play Store)

Damit eine neue APK nicht jedes Mal per USB/Datei-Transfer aufs Handy
muss: `update_server.py` ist ein eigenstaendiger, minimaler HTTP-Server
(Python-Stdlib, keine Abhaengigkeiten), der ausschliesslich zwei Dateien
aus diesem Verzeichnis ausliefert - `radiozapper.apk` und `version.json`
(`{"buildTime": "..."}`, siehe Bauen-Abschnitt oben). Kein Auth, genau
wie der Rest des Projekts (siehe `../CLAUDE.md`, "Kein Auth, nur hinter
VPN") - nur ueber Tailscale erreichbar, keine oeffentliche Portfreigabe.

Laeuft als systemd-User-Service (`~/.config/systemd/user/
radiozapper-android-update.service`, Linger aktiv, ueberlebt also auch
ohne aktive Login-Session):

```bash
systemctl --user status radiozapper-android-update.service
journalctl --user -u radiozapper-android-update.service -f
```

Port `8098`. **Die Server-Adresse ist bewusst NICHT hartcodiert** - sobald
diese App an andere weitergegeben wird, hat niemand sonst Zugriff auf
dieses Tailscale-Netz bzw. will den eigenen PC als Update-Server
betreiben. `UpdateManager.kt` haelt sie in `SharedPreferences`
(`getBaseUrl()`/`setBaseUrl()`), mit einem Textfeld direkt auf der
Startseite ("Update-Server:") jederzeit ohne Rebuild aenderbar.
`DEFAULT_UPDATE_BASE_URL` im Code ist nur der Startwert, falls noch
nichts gespeichert ist - aktuell
`http://dockfish.icefish-ghost.ts.net:8098` (Tailscale-MagicDNS-Name statt
IP, damit ein IP-Wechsel des Hosts nichts bricht), weil das der einzige
tatsaechlich existierende Server ist. **Geplant**: sobald ein oeffentlicher
Server unter `https://blarks.de` steht, wird DAS der neue Default - bis
dahin kann jeder, der die APK bekommt, einfach seine eigene Adresse ins
Textfeld eintragen (oder leer/unveraendert lassen, wenn er ohnehin keinen
eigenen Server hat und nur manuell aktualisieren will).

Button "Nach Update suchen": laedt `version.json` von der aktuell
eingestellten Adresse, vergleicht den `buildTime`-String 1:1 gegen
`BuildConfig.BUILD_TIME` der laufenden App (reiner String-Vergleich, keine
Datums-Ordnung - ein Rollback auf einen aelteren Server-Stand wuerde also
ebenfalls als "Update verfuegbar" gemeldet). Bei Unterschied: Button laedt
die APK in den Cache und stoesst danach ueber `FileProvider` +
`Intent.ACTION_VIEW` (`android.permission.REQUEST_INSTALL_PACKAGES`) den
normalen System-Installer an - keine stille Auto-Installation (dafuer
waere die App Device-Owner/MDM, weit ausserhalb des Scopes). Vor dem
allerersten Update fragt Android einmalig, ob diese App unbekannte Apps
installieren darf (`Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES`) - das
faengt `installUpdate()` in `MainActivity.kt` ab und leitet dorthin
weiter, statt dass der Install-Intent kommentarlos ins Leere laeuft.

**Emulator-Einschraenkung, kein App-Bug**: der Android-Emulator auf
diesem Host loest Tailscale-MagicDNS-Namen nicht auf (eigenes NAT-Netz,
nutzt nicht Tailscales DNS) - `adb shell ping dockfish.icefish-ghost.ts.net`
schlaegt dort fehl, `ping 100.92.3.18` (dieselbe Tailscale-IP direkt)
funktioniert. Deshalb im Emulator ueber das Textfeld auf
`http://10.0.2.2:8098` umgestellt (Emulator-Alias fuer den Host) - damit
den KOMPLETTEN Ablauf (Check → Download → System-Installer-Dialog "Do you
want to update this app?") verifiziert, siehe SESSION.md. Auf einem
echten, im Tailnet angemeldeten Handy mit aktiviertem "Use Tailscale DNS"
sollte der Default-Hostname normal aufloesen; falls nicht, greift genau
dafuer das Textfeld - einfach die Tailscale-IP `100.92.3.18` eintragen.

## Bekannte Grenzen / offene Punkte

- **Doppelter (seit der Vorwärmung eher dreifacher) Netzwerkverbrauch**
  durch die zwei unabhaengigen Dekodierungen (siehe oben) PLUS den
  vorgewaermten Kandidaten (siehe "Vorwärmung" oben) - fuer echten
  Mobilfunk-Betrieb ungeeignet, nur fuer WLAN/Prototyp gedacht. Durch
  automatisches Umschalten jetzt noch relevanter (haeufigere neue
  Verbindungen).
- **Vorwärmung deckt nur EINEN Kandidaten ab, kein Pool** - bei einer
  Sperren-Kette (mehrere aufeinanderfolgende Sender gerade gesperrt) oder
  bei `manualPlay()` auf einen nicht vorhergesagten Sender bleibt es beim
  bisherigen Kaltstart (siehe "Vorwärmung" oben, bewusste
  Aufwand/Nutzen-Entscheidung gegen einen Pool wie im Docker-Projekt).
- **Nachrichten-Pause ohne Ruhezeiten-Option** (`enabled_hours` des
  Docker-Vorbilds, siehe eigener Abschnitt oben) - Nutzerentscheidung,
  spaeter ergaenzbar. Der Fenster-Check laeuft alle 15s statt bei jedem
  Hauptloop-Durchlauf wie im Docker-Projekt - fuer die minutengranulare
  Fensterlaenge unerheblich, aber ein Fenster-Ein-/Austritt kann dadurch
  bis zu 15s spaeter bemerkt werden als der exakte Zeitpunkt. Kein
  Ban-System-artiges Gedaechtnis fuer den zuletzt bedienten Slot ueber
  einen App-Neustart hinweg (`newsBreakServedSlot` ist reines
  In-Memory-Feld) - nach einem Neustart mitten in einem bereits bedienten
  Fenster koennte die Pause theoretisch ein zweites Mal fuer denselben Slot
  anspringen.
- **Vosk-`Model` wird bei jedem Play-/Auto-Switch-Klick neu geladen**
  (kein Wiederverwenden ueber Sender-Wechsel hinweg) - kostet jedes Mal
  ca. 1-2 Sekunden zusaetzliche Verzoegerung, nicht optimiert.
- **Resampler ist reine lineare Interpolation ohne Anti-Aliasing-Filter**
  (`MonoResampler.kt`) - fuer die grobe Sprache/Musik-Unterscheidung
  ausreichend (siehe Live-Test), aber keine hochwertige Audiobearbeitung.
- **Glaettungs-/Hysterese-Schwellen sind nicht an echten Sendern
  kalibriert** - anders als im Docker-Projekt (dort gegen echte Sender
  gemessen, siehe dessen `CLAUDE.md`) sind `RATIO_TO_CONFIRM_SPEECH`/
  `RATIO_TO_CONFIRM_MUSIC` plausible Startwerte, keine Messwerte. Bei
  Gesang koennte das haeufiger falsch-positiv auf "Sprache" kippen.
- **Beide Sperr-Mechanismen (Sprache-Cooldown UND Watchdog) sind reines
  In-Memory-Timing** - sie verfallen einfach nach ihrer jeweiligen
  Konstante, es gibt keine Eskalation (z.B. laenger werdende Sperren bei
  wiederholten Treffern) und nichts ueberlebt einen Neustart der App
  (ein Sender, der beim Beenden gerade gesperrt war, ist nach dem
  naechsten Start wieder unbefangen im Rennen).
- **Keine Mindest-Verweildauer** vor dem naechsten Auto-Switch-Versuch -
  der Cooldown wirkt nur auf den VERLASSENEN Sender, nicht auf den NEUEN
  (der koennte theoretisch sofort wieder als Sprache gelten).
- **Watchdog hat keinen Reconnect-Zaehler vor dem Sperren** (anders als
  `STREAM_FAILURE_LIMIT` im Docker-Projekt) - `onPlayerError()` sperrt
  sofort, in der Annahme, dass ExoPlayers eigene interne Retries fuer
  transiente Fehler bereits vorher gegriffen haben. Bei bestimmten
  Netzwerk-Situationen (z.B. sehr kurze, wiederholte Aussetzer, die
  ExoPlayer selbst gerade NICHT als Retry-wuerdigen Fehler einstuft)
  koennte das aggressiver sperren als noetig - noch nicht gegen echte
  Problemsender verifiziert, nur gegen eine garantiert tote IP.
- **Schmale Race beim Watchdog**: ein spaet eintreffender
  `onPlayerError()`-Callback der ALTEN Quelle nach einem bereits erfolgten
  `play()`-Wechsel auf eine neue koennte theoretisch die NEUE (gerade erst
  gestartete) Quelle faelschlich sperren - bewusst nicht durch einen
  Generation-Counter o.ae. abgesichert, Zeitfenster sehr schmal.
- **Kein Sperr-Anzeige in der Verwaltungs-Activity** (nur in der
  Play-Liste auf dem Hauptschirm).
- **Kategorienliste selbst ist weiterhin nur im Code aenderbar**
  (`model/Categories.kt`) - anders als die Sender ist sie keine
  Nutzerdateneinstellung. Keine Drag-Sortierung innerhalb einer Kategorie,
  keine Sender-Suche - bei der auf dem Handy erwarteten Senderzahl
  (Dutzende, nicht Hunderte je Kategorie) bisher nicht als notwendig
  eingeschaetzt, auch wenn ein M3U-Import mittlerweile hunderte Sender auf
  einen Schlag in "Unsortiert" ablegen kann (siehe "M3U-/Kodi-Sender-
  Import" oben) - genau dafuer gibt es dort den separaten "🔍 Unsortierte
  Sender pruefen"-Knopf statt eines automatischen Checks beim Import.
- **Ein manuell einzeln angelegter Sender (Add-Dialog) hat weiterhin
  keinen Reachability-Check** (anders als `station_import.py`s
  `check_reachable()` im Docker-Projekt, das dort JEDEN neuen Sender prueft)
  - eine kaputte URL faellt erst beim tatsaechlichen Abspielversuch auf.
  Fuer Sender aus dem M3U-Import gibt es den Check jetzt (siehe oben), fuer
  einzeln hinzugefuegte bewusst nicht dupliziert - der bestehende Watchdog
  (siehe oben) faengt das beim ersten Abspielversuch ohnehin zuverlaessig ab.
- **Update-Mechanismus braucht denselben Debug-Signierschluessel** ueber
  Installationen hinweg - Android verweigert ein Update, wenn der neue
  APK-Signer vom installierten abweicht. Solange `~/.android/
  debug.keystore` auf diesem Host nicht geloescht/neu erzeugt wird, ist
  das kein Problem (Gradle nutzt automatisch immer dieselbe Datei); falls
  doch, muss die App auf dem Handy einmal deinstalliert werden, bevor die
  naechste Update-Installation wieder klappt.
- **Kein automatischer Update-Check, keine Benachrichtigung** - der
  Nutzer muss selbst auf "Nach Update suchen" tippen. Kein Hintergrund-
  Polling (bewusst - haette einen weiteren Dauer-Netzwerkzugriff bedeutet).
- **"🔍 Unsortierte Sender pruefen" ist bei einer grossen Importliste
  langsam** (live gemessen: ~8 Sender/45s bei `CHECK_CONCURRENCY=3`, bei
  361 importierten Sendern also grob 15-25 Minuten) und verbraucht dabei
  spuerbar Akku/Datenvolumen - bewusst in Kauf genommen statt eines
  automatischen Checks beim Import selbst (siehe "M3U-/Kodi-Sender-Import"
  oben fuer die Begruendung). Ergebnisse (`unreachableIds`) ueberleben
  ausserdem keinen App-Neustart - rein informativ fuer die aktuelle
  Sitzung, kein Persistenz-Feld in `stations.json`.
