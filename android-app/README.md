# RadioZapper MVP (Android)

Eigenstaendiger Android-Prototyp, der dasselbe Grundprinzip wie das
RadioZapper-Docker-Projekt (`../`) auf dem Handy abbildet: Radiostream
abspielen und per Vosk (Speech-to-Text) grob erkennen, ob gerade Sprache
oder Musik laeuft, und bei Sprache automatisch weiterschalten. **Kein
Web-Wrapper, keine Abhaengigkeit von der Docker-Instanz** - reines
natives Kotlin/Android.

Bewusst minimal (siehe urspruengliche Anforderung): kein Watchdog/
Ban-System, kein News-Break, keine Settings-UI.

## Was funktioniert (live im Emulator getestet, siehe unten)

- 3 hartcodierte Sender (Deutschlandfunk, 1LIVE, SWR3 - alle drei
  oeffentliche Streams, in `model/Station.kt` direkt austauschbar)
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
  Sprache springt die App zum naechsten Sender in der Liste (Ring) -
  wie das Docker-Projekt schaltet sie WEG von Sprache/Moderation, HIN zu
  Musik. Obergrenze gegen Endlosschleife: nach einem vollen Durchlauf
  durch alle Sender ohne Treffer (`AUTO_SWITCH_PAUSE_SECONDS=20`) Sekunden
  Pause statt weiter im Kreis zu springen.
- Foreground Service mit Notification, damit Wiedergabe+Analyse
  weiterlaufen, wenn die App im Hintergrund ist; UI zeigt den aktuellen
  Sender live mit, auch wenn der automatische Wechsel ihn geaendert hat
- Einfache UI: Senderliste mit Play-Buttons, Stop-Button, Statusanzeige,
  Modell-Download-Fortschritt

### Live-Testergebnis (Android-Emulator auf diesem Host, API 34 x86_64)

Deutschlandfunk (Sprache) gestartet → nach ~15s bestaetigt "Sprache"
erkannt → automatisch zu 1LIVE gewechselt → dort ueber 1 Minute stabil
"🎵 Musik", kein Nachflackern, kein weiteres Springen. Keine Abstuerze/
Exceptions im Logcat. Details und weitere Durchlaeufe siehe
`../SESSION.md`, Eintrag "2026-08-07 — Android RadioZapper MVP".

## Was NICHT funktioniert / nicht im Scope

- Kein Watchdog/Ban-System fuer tote oder dauerhaft-sprachige Sender
  (Obergrenze oben ist nur ein einfacher Endlosschleifen-Schutz, keine
  Sperrliste)
- Kein News-Break, keine Settings-UI
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
```

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

- `model/Station.kt` - hartcodierte Senderliste
- `vosk/VoskModelManager.kt` - Download+Entpacken des Vosk-Modells nach
  `filesDir`, Fortschritt als `StateFlow<ModelState>`
- `playback/PlaybackService.kt` - Foreground Service, haelt ExoPlayer
  (Wiedergabe) UND `StreamAnalyzer` (Analyse); reagiert auf den
  geglaetteten Status mit automatischem Umschalten (siehe oben);
  exponiert `currentStation`/`status` als StateFlows fuer die UI;
  Notification via `NotificationCompat`/`ServiceCompat.startForeground`
- `analysis/StreamAnalyzer.kt` - **eigene** MediaExtractor/MediaCodec-
  Dekodierung desselben Stream-URLs nur fuer die Analyse (unabhaengig
  vom ExoPlayer der Wiedergabe), Downmix+Resample auf 16kHz-Mono
  (`MonoResampler.kt`), Fuetterung von Vosk, gleitendes Mehrheitsvotum
  mit Hysterese fuer den geglaetteten Sprache/Musik-Status
- `MainActivity.kt` - bindet an den Service, zeigt Senderliste, Status
  und Modell-Fortschritt live an (inkl. automatischer Senderwechsel)

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

## Bekannte Grenzen / offene Punkte

- **Doppelter Netzwerkverbrauch** durch die zwei unabhaengigen
  Dekodierungen (siehe oben) - fuer echten Mobilfunk-Betrieb ungeeignet,
  nur fuer WLAN/Prototyp gedacht. Durch automatisches Umschalten jetzt
  noch relevanter (haeufigere neue Verbindungen).
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
- **Kein Cooldown pro einzelnem Sender** - die Pause nach einem vollen
  Durchlauf gilt global, nicht "dieser eine Sender ist erstmal tabu".
  Kommt frueher oder spaeter mit dem Watchdog/Ban-System.
- **Keine Mindest-Verweildauer** vor dem naechsten Auto-Switch-Versuch.
- Kein Wiederverbindungsversuch bei Stream-Abbruch (ExoPlayer-Listener
  fuer Fehler/Retry ist nicht angebunden).
