# RadioSabbelNich (Android)

> ✅ **Fertig im Sinne des Fahrplans**: alle acht Phasen aus
> `RadioSabbelNich_Android_Fahrplan.md` sind umgesetzt und live getestet,
> zuletzt Phase 8 (Review + Feinschliff) am 2026-08-08. Es sind keine
> geplanten Ausbaustufen mehr offen - die App wird im Alltag benutzt, nicht
> mehr Stück für Stück aufgebaut. Was sie bewusst NICHT kann, steht
> vollständig unter "Bekannte Grenzen / offene Punkte" am Ende (u.a.
> doppelter Netzwerkverbrauch, kein HLS/DASH, keine Play-Store-Reife) -
> daran ändert der Fertig-Status nichts.
>
> Verlauf/Begründungen: `SESSION.md` in diesem Verzeichnis, ältere Einträge
> bis 2026-08-07 in `../SESSION.md`.

Eigenstaendige Android-App, die dasselbe Grundprinzip wie das
RadioSabbelNich-Docker-Projekt (`../`) auf dem Handy abbildet: Radiostream
abspielen und per Vosk (Speech-to-Text) grob erkennen, ob gerade Sprache
oder Musik laeuft, und bei Sprache automatisch weiterschalten. **Kein
Web-Wrapper, keine Abhaengigkeit von der Docker-Instanz** - reines
natives Kotlin/Android.

Ursprünglich bewusst minimal gestartet (drei hartcodierte Sender, kein
Watchdog/Ban-System, kein News-Break, keine Settings-UI) und dann entlang
des Feature-Parität-Fahrplans (`RadioSabbelNich_Android_Fahrplan.md`) auf den
heutigen Stand gewachsen: Senderverwaltung, Watchdog, Vorwärmung,
M3U-Import, Nachrichten-Pause, Audio-Fingerprinting und mehrsprachiges STT
inklusive Kalibrierungs-Wizard - siehe Feature-Liste unten. Weiterhin
bewusst kein Ban-System, das eine Sperre über einen App-Neustart hinweg
merkt (siehe "Bekannte Grenzen").

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
- **Mehrsprachiges STT** (`stt/SttSettings.kt`, `vosk/VoskModelManager.kt`,
  `vosk/VoskModelCache.kt`, siehe eigener Architektur-Abschnitt unten) -
  eigener Bildschirm "🌐 STT-Sprachen verwalten": beliebig viele Sprachen
  als Freitext (Code + Vosk-Modell-Download-URL, kein festes Dropdown),
  jede einzeln herunterladbar/loeschbar (mindestens eine muss bleiben).
  Zuordnung **Kategorie → Sprache** (nicht Sender → Sprache, wie im
  Docker-Projekt) ueber eine zweite Sektion "🏷 Kategorie-Sprachen" (ein
  Dropdown pro fester Kategorie). Vorbelegt mit "de" und derselben
  Modell-URL wie bisher (`vosk-model-small-de-0.15`, ~45MB) - bestehende
  Installationen mit bereits heruntergeladenem Modell brauchen keinen
  Migrationsschritt. Fehlt fuer die aktuelle Kategorie ein heruntergeladenes
  Modell, zeigt die Startseite einen Hinweis ("⚠ Kein Modell für „…" –
  Analyse pausiert.") statt automatisch zu wechseln. **Kalibrierungs-Wizard**
  (`stt/CalibrationActivity.kt`, "Kalibrieren"-Button pro heruntergeladener
  Sprache): erzwingt die gewaehlte Sprache fuer den gerade laufenden Sender,
  sammelt beim Antippen von "🗣 Das ist Sprache"/"🎵 Das ist Musik" den
  Live-Rohwert (`StreamAnalyzer.speechRatioSamples`, ein Wert pro 0.5s-
  Haeppchen) in zwei Listen und schlaegt
  daraus `ratioToConfirmSpeech`/`ratioToConfirmMusic` fuer diese Sprache vor
  - live neu berechnet bei jedem neuen Sample, mit Warnung statt
  Vorschlag, falls sich Sprache-/Musik-Samples noch ueberlappen. Automatisches
  Umschalten bleibt waehrend einer Session aus, Sender koennen aber jederzeit
  ueber die Startseite gewechselt werden. Die Session endet erst mit
  "Fertig"/Zurueck (nicht schon beim Wegwischen des Bildschirms) - solange
  weist die Startseite ausdruecklich darauf hin, dass das automatische Zappen
  gerade aus ist.
- Parallele Analyse: eine zweite, unabhaengige Dekodierung desselben
  Streams (MediaExtractor/MediaCodec) wird auf 16kHz-Mono resampelt und
  laufend in Vosk (`Recognizer.acceptWaveForm`) gefuettert; erkannter
  Text (nicht-leer) = Sprache-Signal fuer den jeweiligen 0.5s-Chunk
- **Geglaetteter Status** (`StreamAnalyzer.kt`): gleitendes
  Mehrheitsvotum ueber die letzten `SMOOTHING_WINDOW_SECONDS=4.0`
  Sekunden statt Einzel-Chunk-Anzeige, mit Hysterese
  (`ratioToConfirmSpeech`/`ratioToConfirmMusic`, Defaults 0.65/0.30 -
  seit Phase 7 pro Sprache konfigurierbar und ueber den
  Kalibrierungs-Wizard messbar, siehe unten) gegen
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
  Stop-Button, Statusanzeige, Buttons "Sender verwalten"/"STT-Sprachen
  verwalten"
- **Optik an das Web-Interface angeglichen** (`MainActivity.kt`): Banner-Bild
  (dieselbe Datei wie im Web-Interface) und Türkis-Akzentfarbe (`#1ABC9C`).
  Live-Balken "🤥 Bullshitometer" unter den Steuerbuttons zeigt die rohe
  Sprache-Wahrscheinlichkeit (`StreamAnalyzer.speechRatio`, VOR der
  Hysterese) mit demselben grün→rot-Farbverlauf wie im Web. Button "⚡
  ZAPPEN!" neben "■ Stopp" fuer den manuellen Sofort-Wechsel (ruft
  dieselbe Ring-Logik wie ein automatisch erkannter Sprache-Treffer auf).
  Kein separater STT-Meter (Android hat nur einen Detektor, kein VAD+STT-
  Kombi wie das Docker-Projekt). Chip "🔎 Fingerprint" zeigt das letzte
  Fingerprint-Ereignis (siehe unten).
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
- **Audio-Fingerprinting** (`fingerprint/Fingerprint.kt`,
  `fingerprint/FingerprintDb.kt`, Vorbild `fingerprint.py` - siehe eigener
  Architektur-Abschnitt unten fuer die volle Herleitung) - erkennt
  wiederkehrende Jingles/Werbespots per Constellation-Map-Verfahren
  (echte 2D-Landmarken in Zeit UND Frequenz, NICHT "lautester Bin pro
  Frame" - siehe Docker-Projekt-Historie fuer den Grund) auf dem
  ohnehin laufenden 16kHz-Analysestrom, kein zweiter Decode-Pfad. Bei
  Wiedererkennung sofortiger Wechsel (dieselbe Ring-Logik wie ein
  Sprache-Treffer), sonst wird der Clip in einer lokalen SQLite-DB
  gelernt. Chip "🔎 Fingerprint" zeigt das letzte Ereignis, Button "🛑
  Zapping-Fehler" nimmt einen fälschlichen Treffer zurück (Clip aus der
  DB werfen, kein automatisches Umschalten rückgängig machen).
- **Build-Zeitstempel in der UI** (`Build: YYYY-MM-DD HH:MM` direkt unter
  dem App-Titel, `BuildConfig.BUILD_TIME`) - entsteht automatisch bei
  jedem Build. Zweck: von aussen erkennbar, ob eine gerade installierte
  APK noch ein aelterer Stand ist (Anlass: Auto-Switch schien auf einem
  Test-Handy "nicht zu funktionieren" - war eine veraltete Installation).
- **Update-Mechanismus mit konfigurierbarer Adresse** (`update/
  UpdateManager.kt`, siehe eigener Abschnitt unten) - Textfeld "Update-
  Server:" auf der Startseite (Default `https://blarks.de/
  update_radiosabbelnich`, oeffentlich erreichbar, kein VPN noetig).
  Button "Nach Update suchen" prueft den dort eingetragenen Server, laedt
  bei Bedarf die neue APK und stoesst den System-Installer an. Kein Play
  Store, keine Signatur-Pruefung ueber die Debug-Signierung hinaus.

### Live-Testergebnis (Android-Emulator auf diesem Host, API 34 x86_64)

Deutschlandfunk (Sprache) gestartet → nach ~15s bestaetigt "Sprache"
erkannt → automatisch zu 1LIVE gewechselt → dort ueber 1 Minute stabil
"🎵 Musik", kein Nachflackern, kein weiteres Springen. Keine Abstuerze/
Exceptions im Logcat. Details und weitere Durchlaeufe siehe
`../SESSION.md`, Eintrag "2026-08-07 — Android RadioSabbelNich MVP".

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
android-app/radiosabbelnich.apk
```

(daneben weiterhin auch unter dem von Gradle erzeugten
`app/build/outputs/apk/debug/app-debug.apk` - identischer Inhalt, aber
tief verschachtelt und gitignored).

Per USB (ADB, falls Entwickleroptionen aktiv sind):

```bash
adb install -r android-app/radiosabbelnich.apk
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

**Fehlermeldung "Es liegt ein Problem mit der App-Datei vor" beim
Installieren, obwohl der Download vorher erfolgreich war**: fast immer
ein Signatur-Konflikt mit einer bereits auf dem Gerät vorhandenen
älteren/andersartig installierten Version derselben App (z.B. eine ganz
frühe Testinstallation von einem anderen Rechner mit einem anderen
Debug-Schlüssel) - Android akzeptiert dann kein Update, meldet das aber
auf vielen Geräten nur als diese unspezifische Datei-Fehlermeldung statt
eines klaren Signatur-Hinweises. Live so aufgetreten und bestätigt (siehe
`SESSION.md`, 2026-08-08): **App komplett deinstallieren, danach neu
installieren** (nicht als Update) behebt es zuverlässig. Voraussetzung
für künftige nahtlose Updates ist danach derselbe Debug-Signierschlüssel
über die Zeit (`~/.android/debug.keystore` auf dem Build-Host, siehe
"Update-Mechanismus" unten).

## Bauen und Testen

```bash
cd android-app
./gradlew assembleDebug
cp app/build/outputs/apk/debug/app-debug.apk radiosabbelnich.apk   # lokal, adb install
STAMP=$(date '+%Y%m%d-%H%M%S')
APK_NAME="radiosabbelnich-${STAMP}.apk"
cp radiosabbelnich.apk "$APK_NAME"
echo "{\"buildTime\": \"$(date '+%Y-%m-%d %H:%M')\", \"apkFile\": \"$APK_NAME\"}" > version.json
scp "$APK_NAME" version.json strato:/srv/www/blarks.de/update_radiosabbelnich/
rm "$APK_NAME"
```

Der Upload versorgt den Update-Mechanismus (siehe eigener Abschnitt
unten) mit einem aktuellen Versions-Stempel und der passenden Datei -
ohne ihn denkt die App weiterhin, der alte Stand sei der neueste, bzw.
findet gar keine neue Datei zum Herunterladen.

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
adb install -r radiosabbelnich.apk
adb logcat -s PlaybackService:*   # zeigt jeden Sprache/Musik-Wechsel
```

## Architektur in Kuerze

- `RadioSabbelNichApplication.kt` - einziger Zweck: `StationRepository.init()`
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
  (Wiedergabe, mit `C.WAKE_MODE_NETWORK` - nutzt die laengst deklarierte
  `WAKE_LOCK`-Berechtigung, damit bei ausgeschaltetem Display nicht die CPU
  schlafen geht) UND `StreamAnalyzer` (Analyse); reagiert auf den
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
- **NICHT mehr am Watchdog**: `StreamAnalyzer`s eigenes
  `PlaybackStatus.ERROR` haengte bis zum Phase-8-Review ebenfalls an diesem
  Mechanismus ("praktisch kostenlose zweite Bestaetigung"). Das war falsch -
  ein Analyse-Fehler heisst "die Analyse konnte nicht laufen" (nicht ladbares
  Vosk-Modell, Container, den `MediaExtractor` nicht versteht, kein
  Audio-Track), nicht "der Sender ist tot". Bei einem kaputten Modell traf das
  in Sekunden jeden Sender der Kategorie, bis alle gesperrt waren und die
  Eskalation in eine Dauer-Schnellrotation lief. Analyse-Fehler laufen
  jetzt ueber `handleAnalyzerError()`, siehe eigener Abschnitt unten.

Eigene Sperr-Map `deadUntil` (`STATION_DEAD_LOCK_SECONDS=300`, 5 Min. wie
im Docker-Projekt), bewusst getrennt von `stationCooldownUntil` (Sprache-
Cooldown) - `StationLockReason` haelt beide Gruende unterscheidbar,
sowohl fuer die Sperr-Logik als auch fuer die UI. Eine gemeinsame Auswahl-
Funktion (`nextAvailableStation()`/`findNextOrEscalate()`) behandelt
beide Sperrgruende einheitlich, inkl. der "alle gesperrt"-Eskalation
(beide Maps leeren statt haengenzubleiben, wie `dead_until.clear()`).
Manuelle Sender-Wahl (`manualPlay()`, von `MainActivity` statt `play()`
aufgerufen) hebt beide Sperren fuer den gewaehlten Sender auf.

### Analyse-Fehler getrennt von Sender-Fehlern

Seit dem Phase-8-Review (siehe `SESSION.md`, Review-Befund 2): der
`StreamAnalyzer` meldet eine Klartext-Ursache ueber `analyzerError`, statt
ueber `PlaybackStatus.ERROR` den Watchdog auszuloesen. `PlaybackService`
startet die Analyse daraufhin bis zu `ANALYZER_MAX_RETRIES=3`-mal im Abstand
von `ANALYZER_RETRY_SECONDS=15` neu (ein abgerissener Analyse-Stream faengt
sich damit von selbst wieder). Danach laeuft der Sender bewusst OHNE
Sprach-/Musik-Erkennung weiter, und die Startseite zeigt "⚠ Analyse gestoppt:
… (Wiedergabe laeuft weiter)". Wiedergabe und Sperr-Logik bleiben davon
unberuehrt - ueber "Sender tot" entscheidet ausschliesslich der ExoPlayer
(Fehler-Callback bzw. Buffering-Timeout, siehe Watchdog-Abschnitt oben).

Zusaetzlich hat der Analyzer eine laufende Nummer (`generation`): ein durch
`stop()`/`start()` abgeloester Lauf kann seine blockierenden MediaCodec-/
MediaExtractor-Aufrufe nicht sofort abbrechen und laeuft noch kurz aus -
Status, Fehler und Fingerprint-Treffer eines solchen Nachzueglers werden
verworfen, damit sie nicht dem laengst laufenden naechsten Sender
zugeschrieben werden.

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

### Audio-Fingerprinting

Vorbild: `fingerprint.py` im Docker-Projekt (siehe dessen Moduldoc fuer
die volle Herleitung). **Pflicht-Ausgangspunkt war der dortige 2D-Peak-Fix**,
nicht der urspruengliche naive Ansatz ("lautester Bin pro Frame"), der im
Praxistest 351 von 351 verschiedenen Sprache-Clips fälschlich als
identisch erkannte - menschliche Sprache hat generisch aehnliche
Formant-Energie, das macht "lautester Bin" zu einem content-UNABHAENGIGEN
Merkmal. Erst echte lokale Maxima in einer Zeit-Frequenz-Nachbarschaft
("Landmarken": Onsets, markante Toene) trennen zuverlaessig.

**Kein zweiter Decode-Pfad**: laeuft auf dem bereits vorhandenen
16kHz-Mono-Analysestrom aus `StreamAnalyzer.kt` (demselben, der an Vosk
geht), nicht auf einer zusaetzlichen 44.1kHz-Dekodierung wie im
Docker-Projekt - die diskriminative Energie liegt weit unter der
8kHz-Nyquist-Grenze bei 16kHz. Alle Frame-/Nachbarschafts-Konstanten
wurden proportional neu hergeleitet, nicht 1:1 aus Python kopiert:

| Python (44.1kHz) | Bedeutung | Android (16kHz) |
|---|---|---|
| `FRAME_SIZE=1024` (23ms) | FFT-Fenster | `FRAME_SIZE=512` (32ms) |
| `HOP_SIZE=512` (11.6ms) | Frame-Vorschub | `HOP_SIZE=256` (16ms) |
| `MIN_FREQ_HZ=200` | Netzbrumm-Ausschluss | `200` (absoluter Hz-Wert) |
| `PEAK_NEIGHBORHOOD_TIME=5` (58ms) | Zeit-Nachbarschaft | `5` (80ms) |
| `PEAK_NEIGHBORHOOD_FREQ=15` (645Hz) | Frequenz-Nachbarschaft | `21` (656Hz) |
| `PEAK_AMP_MIN_FACTOR=3.0` | Schwelle (dimensionslos) | `3.0` |
| `FAN_VALUE=5` | Hashes/Anker (dimensionslos) | `5` |
| `TARGET_ZONE_FRAMES=40` (465ms) | Max. Peak-Paar-Abstand | `30` (480ms) |
| `MIN_HASH_MATCHES=25` | Treffer-Schwelle | `25` (Startwert, siehe "Bekannte Grenzen") |

`fingerprint/Fingerprint.kt` ist reine Algorithmus-Logik (eigene
Radix-2-Cooley-Tukey-FFT, 2D-lokale-Maxima-Peak-Erkennung, Hash-Bildung
`"$f1-$f2-$dt"`) - bewusst KEINE externe FFT-Bibliothek wie JTransforms,
passt zum durchgehenden Minimal-Dependency-Stil dieses Projekts (das
Docker-Vorbild vermeidet aus demselben Grund `scipy`). Kennt weder
Android-SQLite noch `PlaybackService`. `fingerprint/FingerprintDb.kt`
nutzt `SQLiteOpenHelper` (Android-Bordmittel) statt Room - dieselbe
Begruendung wie die JSON-Datei-statt-Room-Entscheidung bei der
Senderliste, nur in die andere Richtung (hier rechtfertigt der indizierte
Hash-Lookup echtes SQL, aber keine Room-Boilerplate fuer zwei simple
Tabellen). `matchOrLearn()`/`deleteClip()`/`clearAll()` folgen 1:1
`FingerprintDB`s Vorbild, inkl. desselben Voting-Mechanismus
((clip_id, delta)-Zaehlung fuer konsistenten Zeitversatz). **Anders als das
Vorbild begrenzt sich die DB selbst** (seit dem Phase-8-Review, siehe
`SESSION.md`): `matchOrLearn()` lernt jeden ungematchten 2s-Sprachclip mit
mehreren hundert Hash-Zeilen, auf dem Handy waechst das sonst unbegrenzt.
Ab `MAX_CLIPS=500` werden die nach `last_seen` aeltesten Clips in Batches
verdraengt - ein Clip, der lange nicht mehr wiedererkannt wurde, war
offensichtlich kein wiederkehrender Jingle. Zusaetzlich der Knopf
"🗑 Fingerprint-DB leeren" auf der Startseite (mit Rueckfrage) fuer einen
bewussten Neuanfang.

`StreamAnalyzer` zaehlt fuers Fingerprint-Timing einen zusaetzlichen
ROHEN, ungeglaetteten Speech-Streak mit (`FINGERPRINT_TRIGGER_CHUNKS=4` =
2s, identisch zu Pythons `FINGERPRINT_TRIGGER_SECONDS=2`) - unabhaengig
von der bestehenden Glaettung/Hysterese (4s-Anlaufzeit), sonst waere der
erste Check unnoetig spaet dran. Ergebnis kommt als einmaliges
`FingerprintOutcome`-Ereignis ueber ein `SharedFlow` zurueck (bewusst
nicht `StateFlow` - ein Treffer ist kein Dauerzustand). `PlaybackService`
reagiert auf `Match` mit demselben `attemptAutoSwitch()` wie ein
Sprache-Treffer (identische Semantik zu `do_switch("Bekannte Werbung/
Jingle erkannt")`, erbt den bestehenden Cooldown automatisch). Pausiert
implizit waehrend einer Nachrichten-Pause, weil `analyzer.stop()` dort
bereits die komplette Analyse-Coroutine abbricht.

### Mehrsprachiges STT (Schritt 1: Grundgerüst)

(Schritt 2, der Kalibrierungs-Wizard, ist ein eigener Abschnitt weiter
unten.)

Vorbild: `stt_filter.py`/`settings_store.py` im Docker-Projekt (dessen
"Mehrsprachige STT-Erkennung"-Abschnitt in `../CLAUDE.md`) - wie dort
liegt die Zuordnung an der **Kategorie**, nicht am einzelnen Sender
(`stt/SttSettings.kt`s `categoryLanguages: Map<String, String>`,
`model/Categories.kt` bleibt unangetastet). Sprachliste als Freitext
(Code + Vosk-Modell-Download-URL), keine feste Auswahl - Android laedt
das Modell selbst herunter, anders als der Docker-Container, der auf
einen Host-Mount angewiesen ist, aber dieselbe Freitext-Philosophie.

`vosk/VoskModelManager.kt` ist seit dieser Phase ein Singleton (`object`)
statt einer Instanz-Klasse - mehrere Activities (Startseite, die neue
`stt/SttSettingsActivity.kt`) teilen sich denselben Download-Fortschritt
pro Sprache (`ConcurrentHashMap<String, MutableStateFlow<ModelState>>`).
Der Modell-Ordnername wird aus dem Dateinamen der Download-URL abgeleitet
statt sprachcode-basiert vergeben - fuer Deutsch mit der unveraenderten
Default-URL ergibt sich dadurch exakt derselbe Pfad wie vor dieser Phase
(`vosk-model-small-de-0.15`), bestehende Installationen mit bereits
heruntergeladenem Modell brauchen keinen Migrationsschritt.

`vosk/VoskModelCache.kt` (neu) ist ein LRU-Cache geladener
`org.vosk.Model`-Objekte pro Sprachcode (`MAX_LOADED_VOSK_LANGUAGES=2`,
identischer Default wie `MAX_LOADED_VOSK_LANGUAGES` im Docker-Projekt) -
direktes Pendant zu `SttFilter._get_vosk_engine()`. Instanzgebunden
(gehoert `PlaybackService`, analog `FingerprintDb`), cached sowohl Erfolg
als auch Fehlschlag, damit ein kaputter Modellpfad nicht bei jedem Sample
erneut das Dateisystem anfasst. `StreamAnalyzer.start()`/`runAnalysis()`
bekommen `language`/`voskModelCache`/`ratioToConfirmSpeech`/
`ratioToConfirmMusic` als Parameter statt der frueheren globalen
Konstanten `RATIO_TO_CONFIRM_SPEECH`/`RATIO_TO_CONFIRM_MUSIC` (deren
bisherige Werte 0.65/0.30 sind jetzt die Defaults in `LanguageConfig`,
pro Sprache ueberschreibbar). Das Modell kommt ueber
`voskModelCache.get(modelPath, language)` - Ownership liegt beim Cache,
`StreamAnalyzer` darf es deshalb nicht mehr selbst schliessen.

**Kein Parameter-Threading mehr fuer Sprache/Modellpfad**: bis zu dieser
Phase wurde der Vosk-Modellpfad einmal in `MainActivity` aufgeloest und
als Parameter durch `manualPlay()`/`play()` und vier interne
Aufrufstellen in `PlaybackService.kt` durchgereicht (`activeModelPath`-
Feld). Genau dieses Muster war im Docker-Projekt die Ursache eines
Absturz-Bugs beim Hinzufuegen der dortigen Mehrsprachigkeit (eine
Aufrufstelle bei der Signaturaenderung vergessen, siehe dessen
`SESSION.md`, "Fortsetzung 7"). Der Parameter wurde deshalb komplett
entfernt statt nur sorgfaeltig durchgereicht: `play(station)` loest
Sprache und Modellpfad jetzt bei JEDEM Aufruf intern ueber
`SttSettings.resolveLanguage(station.category)` auf - jeder Aufrufer
bekommt dadurch automatisch den aktuellen Stand, eine vergessene Stelle
kann strukturell nicht mehr veraltete Daten durchreichen.

`LanguageConfig` haelt `ratioToConfirmSpeech`/`ratioToConfirmMusic` bereits
pro Sprache - Speicherstruktur fuer den in Schritt 2 (naechster Abschnitt)
umgesetzten Kalibrierungs-Wizard.

### Mehrsprachiges STT, Schritt 2: Kalibrierungs-Wizard

Vorbild: der STT-Kalibrierungs-Wizard im Docker-Projekt (dessen
`CLAUDE.md`, Abschnitt STT-Sprachfilter, "Kalibrierungs-Wizard"). Dort
kalibriert der Wizard einen `confidence_threshold` gegen ein VAD+STT-Duo -
Android hat dieses Duo nicht, Vosk-Texterkennung ist hier bereits der
einzige Detektor. Der Wizard kalibriert deshalb stattdessen direkt die
vorhandene Hysterese-Bandbreite (`ratioToConfirmSpeech`/
`ratioToConfirmMusic`) um `StreamAnalyzer.speechRatio` - dasselbe Rohsignal,
das auch das "Bullshitometer" zeigt.

`stt/Calibration.kt` ist reine Domänenlogik (kennt weder `PlaybackService`
noch `StreamAnalyzer`, analog `NewsBreak.kt`/`Fingerprint.kt`):
`suggestRatios(speechSamples, musicSamples)` trennt die beiden
Verteilungen ueber `musicHigh`/`speechLow` und teilt die Luecke dazwischen
im Verhaeltnis `MARGIN_RATIO=0.7` auf (identischer Wert wie
`_THRESHOLD_MARGIN_RATIO` im Docker-Projekt, Richtung Sprache-Seite
gewichtet) - `ratioToConfirmMusic` liegt naeher an `musicHigh`,
`ratioToConfirmSpeech` naeher an `speechLow`. Ueberlappen sich die
Verteilungen (`musicHigh >= speechLow`), liefert die Funktion
`overlapping=true` statt eines potenziell falschen Vorschlags.

`musicHigh`/`speechLow` sind seit dem Phase-8-Review (siehe `SESSION.md`,
Befund 4) das 90.- bzw. 10.-Perzentil, NICHT mehr `max()`/`min()`, und es
braucht `MIN_SAMPLES_PER_LEVEL=20` Samples pro Seite (ca. 10 Sekunden).
Vorher genuegte ein einziger Uebergangswert - der Moderator holt Luft, ein
Jingle laeuft an - um jeden Vorschlag als "ueberlappend" zu verwerfen.

`PlaybackService` haelt die Session direkt als Felder (kein eigenes
State-Objekt) - dieselbe Entscheidung wie bei Fingerprint/News-Break,
die Session haengt am gerade laufenden `analyzer`. `refreshAnalyzer(station)`
wurde dafuer aus `play()` herausgezogen (reiner Refactor) und wird jetzt
auch von `startCalibration()`/`stopCalibration()`/
`applyCalibrationSuggestion()` genutzt, um die STT-Analyse fuer den GERADE
laufenden Sender neu aufzusetzen, ohne den ExoPlayer anzufassen. Ist eine
Session aktiv, ERZWINGT `refreshAnalyzer()` deren Sprache statt der ueber
`SttSettings.resolveLanguage()` aufgeloesten Kategorie-Sprache - Sender
koennen waehrenddessen ganz normal ueber die Startseite gewechselt werden,
jeder trifft automatisch dieselbe erzwungene Sprache. Automatisches
Umschalten (Sprache-Treffer, Fingerprint-Match) bleibt waehrend einer
aktiven Session bewusst AUS - ein durch die erzwungene Sprache
verfaelschtes Ergebnis darf keinen automatischen Wechsel ausloesen. Der
Wizard schaltet selbst NIEMALS einen Sender um.

`stt/CalibrationActivity.kt` bindet sich wie `MainActivity` an
`PlaybackService`, zeigt den laufenden Sender und den Live-Rohwert
(dieselbe Balken-Darstellung wie das Bullshitometer), zwei Toggle-Buttons
"🗣 Das ist Sprache"/"🎵 Das ist Musik" (erneutes Antippen pausiert das
Sammeln), Sample-Zaehler und einen live neu berechneten Vorschlag - kein
Zwischenspeichern, wird bei jeder Aenderung der Sample-Zaehler frisch aus
`calibrationSuggestion()` gezogen (analog zum Docker-Wizard, der den
Vorschlag bei jedem Status-Poll neu berechnet statt eine zweite
JS-Implementierung zu pflegen). "Übernehmen" speichert die Werte in
`SttSettings` und wendet sie sofort an, OHNE die Session zu beenden -
weiter sammeln/erneut uebernehmen bleibt moeglich.

`android:screenOrientation="portrait"` fuer `CalibrationActivity` ist
bewusst gesetzt: eine Rotation wuerde die Activity sonst zerstoeren und
neu erstellen, `onDestroy()` beendet aber die Session - ohne die Sperre
wuerde eine simple Bildschirmdrehung mitten in der Kalibrierung bereits
gesammelte Samples verwerfen. Zusaetzlich schuetzt
`activeCalibrationLanguage != language` in `onServiceConnected()` davor,
dass ein erneutes Binden (z.B. nach kurzem Backgrounding) `startCalibration()`
ein zweites Mal fuer dieselbe Sprache aufruft, was die Sample-Listen sonst
explizit leeren wuerde.

## Update-Mechanismus (blarks.de, kein Play Store)

Damit eine neue APK nicht jedes Mal per USB/Datei-Transfer aufs Handy
muss, liefert `UpdateManager.kt` sie per HTTP(S) direkt aus dem Netz nach.
Seit 2026-08-08 (vorher: eigener Python-Server nur uebers Tailscale-Netz,
siehe SESSION.md) ist die Gegenstelle keine eigene Software mehr, sondern
ein ganz normales, statisches Verzeichnis auf dem oeffentlich erreichbaren
Webserver von `blarks.de`: `/srv/www/blarks.de/update_radiosabbelnich/`
(per SSH-Host-Alias `strato` erreichbar). Apache liefert Dateien von dort
direkt aus - Verzeichnis-Listing ist per vhost-Konfiguration gesperrt
(`Options -Indexes`), der `.apk`-MIME-Type (`application/vnd.android.
package-archive`) kommt automatisch aus `/etc/mime.types`, kein
Zusatzcode noetig.

**Jede hochgeladene APK bekommt einen eigenen, zeitgestempelten Namen**
(`radiosabbelnich-YYYYMMDD-HHMMSS.apk`) statt eine bestehende Datei zu
ueberschreiben - `version.json` (`{"buildTime": "...", "apkFile":
"radiosabbelnich-...apk"}`) sagt der App, welche Datei gerade aktuell
ist. Kompletter Bauen-und-Verteilen-Schritt (siehe auch `../CLAUDE.md`):

```bash
./gradlew assembleDebug
cp app/build/outputs/apk/debug/app-debug.apk radiosabbelnich.apk   # lokal, adb install
STAMP=$(date '+%Y%m%d-%H%M%S')
APK_NAME="radiosabbelnich-${STAMP}.apk"
cp radiosabbelnich.apk "$APK_NAME"
echo "{\"buildTime\": \"$(date '+%Y-%m-%d %H:%M')\", \"apkFile\": \"$APK_NAME\"}" > version.json
scp "$APK_NAME" version.json strato:/srv/www/blarks.de/update_radiosabbelnich/
rm "$APK_NAME"
```

Aeltere Staende bleiben auf `blarks.de` liegen (kein automatisches
Aufraeumen) - nur `version.json` bestimmt, welcher als "aktuell" gilt.

**Kein Auth** - bewusst, siehe `../CLAUDE.md`, Abschnitt "Kein Auth": eine
App-Binary ohne Nutzerdaten oeffentlich zum Download bereitzustellen ist
ein anderes Risiko als der Rest des Projekts (Live-Radiodienst,
urheberrechtlich sensibler Restream), deshalb hier bewusst NICHT mehr
hinter einem VPN.

**Die Server-Adresse ist weiterhin NICHT hartcodiert** - `UpdateManager.kt`
haelt sie in `SharedPreferences` (`getBaseUrl()`/`setBaseUrl()`), mit
einem Textfeld direkt auf der Startseite ("Update-Server:") jederzeit
ohne Rebuild aenderbar. `DEFAULT_UPDATE_BASE_URL` im Code ist nur der
Startwert, falls noch nichts gespeichert ist - jetzt
`https://blarks.de/update_radiosabbelnich`. Wer die APK von woanders
bekommt oder einen eigenen Server betreiben will, traegt einfach eine
andere Adresse ins Textfeld ein.

Button "Nach Update suchen": laedt `version.json` von der aktuell
eingestellten Adresse, vergleicht den `buildTime`-String 1:1 gegen
`BuildConfig.BUILD_TIME` der laufenden App (reiner String-Vergleich, keine
Datums-Ordnung - ein Rollback auf einen aelteren Server-Stand wuerde also
ebenfalls als "Update verfuegbar" gemeldet) und merkt sich das `apkFile`
aus der Antwort fuer den naechsten Schritt. Bei Unterschied: Button laedt
GENAU diese Datei (`${baseUrl}/${apkFile}`) in den Cache und stoesst
danach ueber `FileProvider` + `Intent.ACTION_VIEW`
(`android.permission.REQUEST_INSTALL_PACKAGES`) den normalen
System-Installer an - keine stille Auto-Installation (dafuer waere die
App Device-Owner/MDM, weit ausserhalb des Scopes). Vor dem allerersten
Update fragt Android einmalig, ob diese App unbekannte Apps installieren
darf (`Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES`) - das faengt
`installUpdate()` in `MainActivity.kt` ab und leitet dorthin weiter, statt
dass der Install-Intent kommentarlos ins Leere laeuft.

**`downloadUpdate()` prueft zusaetzlich den `Content-Type` der Antwort**
(muss mit `application/vnd.android.package-archive` beginnen, sonst
Fehler statt stillem Weiterlaufen) - der reine HTTP-Statuscode reicht
NICHT: ein Webserver mit Catch-All-Route fuer unbekannte Pfade (wie
`blarks.de`s SPA-Startseite) antwortet auf einen falschen/veralteten
Dateinamen ebenfalls mit `200`, nur eben mit HTML statt der APK - live so
aufgetreten, als eine bereits installierte AELTERE App-Version (vor der
`apkFile`-Umstellung, siehe oben) noch den alten fest verdrahteten
Dateinamen abfragte. Ohne diese Pruefung landet die HTML-Seite
unbemerkt im APK-Cache und scheitert erst kommentarlos im
System-Installer ("Es liegt ein Problem mit der App-Datei vor").

**Live im Emulator verifiziert** (siehe SESSION.md, 2026-08-08): kompletter
Ablauf Check ("Kein Update verfuegbar" gegen den echten aktuellen Stand)
→ `version.json` auf `blarks.de` kurzzeitig auf einen fiktiven Stand
gesetzt → "Update verfuegbar: 2099-01-01 00:00" → Download der exakten
zeitgestempelten Datei (~46 MB ueber echtes Internet statt Emulator-NAT)
→ System-Installer-Dialog "Do you want to update this app?" (korrekt als
Update erkannt, Signatur passt) → abgebrochen, `version.json` auf den
echten Stand zurueckgesetzt.

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
- **`MIN_HASH_MATCHES=25` fuers Fingerprinting ist ein Startwert, keine
  Messung** (siehe "Audio-Fingerprinting" oben) - identisch zum
  Python-Wert uebernommen, aber NICHT automatisch gueltig fuer die andere
  Samplerate/Frame-Groesse hier. Live gegen echte Sender getestet wurde
  bisher nur die Kein-Falsch-Treffer-Seite (drei unterschiedliche
  Sprache-Clips, alle mit Match-Staerke 1 klar unter der Schwelle - siehe
  `SESSION.md`) - ein echter Wiederholungs-Fall (Match-Staerke deutlich
  UEBER der Schwelle) wurde mangels eines garantiert wiederkehrenden
  Test-Clips noch nicht live beobachtet, nur der gleiche, im Docker-Projekt
  bereits bewaehrte Algorithmus neu in Kotlin geschrieben.
- **Ein Vosk-Modellwechsel kostet weiterhin Ladezeit** (ca. 1-2s), wenn
  die Zielsprache nicht mehr im `VoskModelCache` liegt - innerhalb der
  `MAX_LOADED_VOSK_LANGUAGES=2` zuletzt genutzten Sprachen wird das
  geladene Modell dagegen ueber Sender-Wechsel hinweg wiederverwendet
  (seit Phase 7; die frueher hier stehende Aussage "wird bei JEDEM
  Play-Klick neu geladen" galt nur bis dahin).
- **Resampler ist reine lineare Interpolation ohne Anti-Aliasing-Filter**
  (`MonoResampler.kt`) - fuer die grobe Sprache/Musik-Unterscheidung
  ausreichend (siehe Live-Test), aber keine hochwertige Audiobearbeitung.
- **Glaettungs-/Hysterese-Schwellen sind standardmaessig nicht an echten
  Sendern kalibriert** - `ratioToConfirmSpeech`/`ratioToConfirmMusic`
  (pro Sprache in `LanguageConfig`) starten als plausible Startwerte,
  keine Messwerte. Seit Schritt 2 gibt es dafuer den Kalibrierungs-Wizard
  (siehe "Mehrsprachiges STT, Schritt 2" oben) - bis er tatsaechlich pro
  Sprache durchlaufen wurde, bleibt der ungemessene Startwert aktiv, bei
  Gesang koennte das haeufiger falsch-positiv auf "Sprache" kippen. Der
  Wizard selbst wurde bisher nur mit demselben (gemischten) Test-Sender
  fuer beide Level gegengetestet (bestaetigt den Ueberlappungs-Warnpfad,
  siehe `SESSION.md`) - der Erfolgspfad mit zwei echt unterschiedlich
  klassifizierten Quellen und tatsaechlich uebernommenem Vorschlag steht
  noch aus.
- **`CalibrationActivity`s Portrait-Sperre** (schuetzt eine laufende
  Kalibrierungs-Session vor Datenverlust durch Rotation, siehe
  "Mehrsprachiges STT, Schritt 2" oben) wurde nur im durchgehend im
  Hochformat laufenden Emulator verifiziert, nicht auf einem echten Geraet
  mit tatsaechlicher Drehung gegengeprueft.
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
- **Kein HLS/DASH**: eingebunden sind nur `media3-exoplayer`/
  `media3-common`, nicht `media3-exoplayer-hls`/`-dash`. Sender mit
  `.m3u8`-URL (in der Kodinerds-Importliste durchaus vorhanden) koennen
  deshalb prinzipiell nicht laufen - sie erscheinen als "⚠ Antwortet nicht"
  bzw. "nicht erreichbar", ohne dass die Ursache erkennbar waere. Auch der
  Analysepfad kann sie nicht lesen (`MediaExtractor` parst keine m3u8). Beim
  Phase-8-Review bewusst nur dokumentiert, nicht behoben - das Nachziehen der
  Dependency ist eine Funktionserweiterung, keine Review-Korrektur.
- **Verwaltungs-Activity baut bei jeder Aenderung alle Zeilen neu auf** -
  bei den real importierten 361 Sendern hunderte `inflate()` im Main-Thread
  pro Checkbox-Klick (bekannt seit dem Phase-8-Review, nicht behoben: ein
  RecyclerView-Umbau ist Feature-Arbeit, kein Bugfix).
- **Blockierende Decoder-Aufrufe lassen sich nicht abbrechen**
  (`MediaExtractor.setDataSource()` haengt bis zum OS-Timeout): ein
  abgeloester Analyse-Lauf und ein abgelaufener Erreichbarkeits-Check laufen
  im Hintergrund noch aus. Die daraus folgende Verwechslungsgefahr ist
  entschaerft (Nachzuegler koennen nichts mehr veroeffentlichen, siehe
  "Analyse-Fehler getrennt von Sender-Fehlern"), der doppelte
  Ressourcenverbrauch fuer die Restlaufzeit bleibt.
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
- **`VoskModelCache`s LRU-Verdraengung noch nicht mit einer dritten
  gleichzeitig genutzten Sprache live beobachtet** (nur mit zwei, siehe
  `SESSION.md`) - die Verdraengungslogik selbst ist Standard-
  `LinkedHashMap`-Verhalten, aber noch nicht am eigenen Cache unter
  echter Last gegengeprueft.
