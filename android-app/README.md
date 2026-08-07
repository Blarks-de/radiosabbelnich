# RadioZapper MVP (Android)

Eigenstaendiger Android-Prototyp, der dasselbe Grundprinzip wie das
RadioZapper-Docker-Projekt (`../`) auf dem Handy abbildet: Radiostream
abspielen und per Vosk (Speech-to-Text) grob erkennen, ob gerade Sprache
oder Musik laeuft. **Kein Web-Wrapper, keine Abhaengigkeit von der
Docker-Instanz** - reines natives Kotlin/Android.

Bewusst minimal (siehe Anforderung): kein automatisches Umschalten, kein
Watchdog, kein News-Break, keine Settings-UI. Nur Anzeige des erkannten
Zustands.

## Was funktioniert (kompiliert, aber NICHT auf echtem Geraet getestet)

In dieser Umgebung stand kein Android-Emulator und kein physisches Geraet
zur Verfuegung (kein KVM/Display fuer einen Emulator, kein per USB
verbundenes Handy) - verifiziert ist ausschliesslich, dass das Projekt
sich mit `./gradlew assembleDebug` fehlerfrei uebersetzen und paketieren
laesst. Alles Folgende ist nach Code-Lesart korrekt umgesetzt, aber die
tatsaechliche Laufzeit auf einem Geraet ist ungetestet:

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
  Text (nicht-leer) = Sprache-Signal
- Streak-Logik: 3 Sekunden am Stueck Text erkannt -> Status "🗣 Sprache";
  3 Sekunden am Stueck kein Text -> Status "🎵 Musik" (Konstante
  `SPEECH_STREAK_SECONDS` in `StreamAnalyzer.kt`)
- Foreground Service mit Notification, damit Wiedergabe+Analyse
  weiterlaufen, wenn die App im Hintergrund ist
- Einfache UI: Senderliste mit Play-Buttons, Stop-Button, Statusanzeige,
  Modell-Download-Fortschritt

## Was NICHT funktioniert / nicht im Scope

- Kein automatisches Sender-Umschalten bei erkannter Musik (nur Anzeige)
- Kein Watchdog fuer tote Streams, kein News-Break, keine Settings-UI
- Kein Play-Store-taugliches Icon (einfaches Vektor-Icon)
- Keine Fehlerbehandlung fuer jeden Edge Case (z.B. Stream ohne
  erkennbaren Audio-Track, Wechsel des Mobilfunknetzes mitten im Stream)

## Installation

Debug-APK liegt nach dem Build unter:

```
app/build/outputs/apk/debug/app-debug.apk
```

Per USB (ADB, falls Entwickleroptionen aktiv sind):

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
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

## Bauen

```bash
cd android-app
./gradlew assembleDebug
```

Benoetigt Android SDK (`local.properties` mit `sdk.dir=...` oder
`ANDROID_HOME`/`ANDROID_SDK_ROOT` gesetzt) sowie Internetzugriff beim
ersten Build (Gradle-Wrapper-Distribution + Abhaengigkeiten aus
`google()`/`mavenCentral()`).

## Architektur in Kuerze

- `model/Station.kt` - hartcodierte Senderliste
- `vosk/VoskModelManager.kt` - Download+Entpacken des Vosk-Modells nach
  `filesDir`, Fortschritt als `StateFlow<ModelState>`
- `playback/PlaybackService.kt` - Foreground Service, haelt ExoPlayer
  (Wiedergabe) UND `StreamAnalyzer` (Analyse); Notification via
  `NotificationCompat`/`ServiceCompat.startForeground`
- `analysis/StreamAnalyzer.kt` - **eigene** MediaExtractor/MediaCodec-
  Dekodierung desselben Stream-URLs nur fuer die Analyse (unabhaengig
  vom ExoPlayer der Wiedergabe), Downmix+Resample auf 16kHz-Mono
  (`MonoResampler.kt`), Fuetterung von Vosk, Streak-basierte
  Sprache/Musik-Erkennung
- `MainActivity.kt` - bindet an den Service, zeigt Senderliste, Status
  und Modell-Fortschritt an

### Bewusste Vereinfachung ggue. dem Docker-Projekt: zwei Dekodierungen

Das Docker-Projekt (`../CLAUDE.md`, Abschnitt "Audio-Pfad") nutzt einen
einzigen ffmpeg-Prozess mit zwei Ausgabe-Pipes (Wiedergabe + Analyse aus
derselben Dekodierung). Dieses MVP macht das bewusst NICHT nach: ExoPlayer
und `StreamAnalyzer` oeffnen unabhaengig voneinander je eine eigene
Verbindung zum selben Stream-URL und dekodieren beide komplett selbst.
Vorteil: kein Eingriff in ExoPlayers internen Audio-Pfad noetig (kein
custom `AudioProcessor`/`RenderersFactory`), deutlich weniger
Fehlerflaeche fuer ein MVP ohne Testgeraet. Preis: der Stream laeuft
effektiv doppelt ueber das Netz (Mobilfunk-Datenvolumen!). Falls das MVP
weitergefuehrt wird, waere das Anzapfen des ExoPlayer-Audio-Pfads via
custom `AudioProcessor` der naheliegende naechste Schritt.

## Bekannte Grenzen / offene Punkte

- **Ungetestet auf echtem Geraet** (siehe oben) - insbesondere ob
  `MediaExtractor.setDataSource(url)` mit den drei Beispiel-Streams ohne
  zusaetzliche Header (User-Agent, Icy-MetaData) tatsaechlich anspringt,
  ist unverifiziert. Falls ein Sender nicht laedt: zuerst pruefen, ob der
  Stream ueberhaupt ohne spezielle Header erreichbar ist.
- **Doppelter Netzwerkverbrauch** durch die zwei unabhaengigen
  Dekodierungen (siehe oben) - fuer echten Mobilfunk-Betrieb ungeeignet,
  nur fuer WLAN/Prototyp gedacht.
- **Vosk-`Model` wird bei jedem Play-Klick neu geladen** (kein
  Wiederverwenden ueber Sender-Wechsel hinweg) - kostet jedes Mal ca.
  1-2 Sekunden zusaetzliche Verzoegerung, nicht optimiert.
- **Resampler ist reine lineare Interpolation ohne Anti-Aliasing-Filter**
  (`MonoResampler.kt`) - fuer die grobe Sprache/Musik-Unterscheidung
  vermutlich ausreichend, aber keine hochwertige Audiobearbeitung.
- **Konfidenz/Schwellwerte sind nicht kalibriert** - anders als im
  Docker-Projekt (dort gegen echte Sender gemessen, siehe dessen
  `CLAUDE.md`) nutzt dieses MVP nur "Text erkannt oder nicht" ohne
  Konfidenzschwelle. Bei Gesang koennte das haeufiger falsch-positiv auf
  "Sprache" kippen als die kalibrierte Docker-Loesung.
- Kein Wiederverbindungsversuch bei Stream-Abbruch (ExoPlayer-Listener
  fuer Fehler/Retry ist nicht angebunden).
