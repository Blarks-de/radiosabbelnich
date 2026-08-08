# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Dieses Verzeichnis ist ein **eigenständiges** Projekt innerhalb des
> RadioZapper-Repos — siehe `../CLAUDE.md`, Abschnitt "Android-Prototyp
> (separates Projekt)", für die Abgrenzung zum Docker-Dienst. Zwei feste
> Regeln von dort gelten weiterhin unbedingt: nach jedem Build die APK
> nach `radiozapper.apk` kopieren + `version.json` neu schreiben (siehe
> unten), und `README.md` bei jeder inhaltlichen Änderung nachziehen.

Aktiver Prototyp (kein fertiges Produkt) — natives Kotlin/Android, das
dasselbe Grundprinzip wie das RadioZapper-Docker-Projekt lokal auf dem
Handy nachbildet: mehrere Internetradio-Sender abspielen, per Vosk
(Speech-to-Text) grob Sprache/Musik unterscheiden, bei Sprache automatisch
zum nächsten Sender weiterschalten. Kein Web-Wrapper, keine Abhängigkeit
von der Docker-Instanz. Vollständige Feature-Liste, Live-Testergebnisse
und "Bekannte Grenzen" stehen in `README.md` — hier nur, was zum
produktiven Arbeiten im Code nötig ist. Verlauf/Begründungen einzelner
Entscheidungen: `SESSION.md`.

## Sprache und Konventionen

Wie im übergeordneten Projekt (`../CLAUDE.md`): **alles auf Deutsch**
(Kommentare, Log-Meldungen, UI-Texte, README, SESSION.md). Kommentare
erklären warum, nicht was. `README.md` beschreibt den aktuellen Stand für
Nutzer, `SESSION.md` ist append-only (ein neuer Eintrag pro Arbeitseinheit,
ältere nicht rückwirkend korrigieren) — beide vor jedem Commit nachziehen,
wenn sich Verhalten/Architektur ändert.

## Bauen, Installieren, Testen

Kein Test-Framework, keine CI (analog zum Docker-Projekt). Verifikation
läuft über Live-Tests im Emulator, protokolliert in `SESSION.md`.

```bash
cd android-app
./gradlew assembleDebug

# PFLICHT nach jedem Build (siehe ../CLAUDE.md):
cp app/build/outputs/apk/debug/app-debug.apk radiozapper.apk
echo "{\"buildTime\": \"$(date '+%Y-%m-%d %H:%M')\"}" > version.json
```

Ohne den zweiten Schritt hält die App den alten Stand weiterhin für
aktuell (`UpdateManager` vergleicht `version.json` gegen
`BuildConfig.BUILD_TIME` als reinen String, siehe unten) — der erste
Schritt allein reicht nicht.

Benötigt Android SDK (`local.properties` mit `sdk.dir=...` oder
`ANDROID_HOME`/`ANDROID_SDK_ROOT`) sowie Internetzugriff beim ersten Build
(Gradle-Wrapper-Distribution + Abhängigkeiten aus `google()`/
`mavenCentral()`).

Laufzeit-Tests (nicht nur Kompilieren) über den auf diesem Host
eingerichteten Emulator (`~/Android/Sdk`, AVD `test_device`, API 34
google_apis x86_64):

```bash
emulator -avd test_device -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect &
adb wait-for-device
adb install -r radiozapper.apk
adb logcat -s PlaybackService:*   # jeder Sprache/Musik-Wechsel, Watchdog-Events
```

Installation auf einem echten Gerät und Update-Server-Details (Tailscale,
`update_server.py`, systemd-Service): siehe README-Abschnitte
"Installation" und "Update-Mechanismus".

## Architektur

`RadioZapperApplication.onCreate()` ruft `StationRepository.init()` VOR
jeder anderen Komponente auf (Application läuft garantiert zuerst) — kein
"wer zuerst dran ist, ruft init() auf"-Muster in einzelnen Komponenten.

### Zwei unabhängige Dekodierungen desselben Streams

Bewusste Vereinfachung gegenüber dem Docker-Projekt (das einen ffmpeg mit
zwei Ausgabe-Pipes aus EINER Dekodierung nutzt, siehe dessen `CLAUDE.md`):
hier öffnen ExoPlayer (Wiedergabe, `PlaybackService.kt`) und
`analysis/StreamAnalyzer.kt` (Analyse, MediaExtractor/MediaCodec) je eine
eigene Verbindung zur selben Stream-URL. Vorteil: kein Eingriff in
ExoPlayers internen Audio-Pfad (kein custom `AudioProcessor`), wenig
Fehlerfläche. Preis: der Stream läuft effektiv doppelt über das Netz —
für Mobilfunk ungeeignet, nur WLAN/Prototyp. `StreamAnalyzer` resampelt
per `analysis/MonoResampler.kt` (reine lineare Interpolation, kein
Anti-Aliasing) auf 16kHz-Mono und füttert Vosk; ein gleitendes
Mehrheitsvotum über `SMOOTHING_WINDOW_SECONDS=4.0` mit Hysterese
(`RATIO_TO_CONFIRM_SPEECH=0.65`/`RATIO_TO_CONFIRM_MUSIC=0.30`, **nicht**
an echten Sendern kalibriert, anders als die Docker-Werte) liefert den
geglätteten Sprache/Musik-Status statt Einzel-Chunk-Flackern.

### `PlaybackService.kt`: Wiedergabe, Auto-Switch, Watchdog in einem

Foreground Service, hält ExoPlayer UND `StreamAnalyzer`. Drei Dinge lösen
hier Reaktionen aus:

- **Geglätteter Sprache-Status** vom `StreamAnalyzer` → Sprung zum
  nächsten aktivierten Sender im Ring (`StationRepository.activeStations()`
  wird bei jedem Versuch frisch gelesen, kein Request/Pop-Mechanismus wie
  im Docker-Projekt nötig — es gibt hier keinen zweiten Thread, der um
  denselben Zustand konkurriert). Verlassener Sender bekommt
  `STATION_COOLDOWN_SECONDS=60` Cooldown. Sind entweder alle Sender einmal
  ohne Treffer probiert oder alle im Cooldown, folgt
  `AUTO_SWITCH_PAUSE_SECONDS=20` Pause statt Endlos-Springen.
- **Änderungen aus `StationRepository.stations`** (z.B. der gerade
  laufende Sender wird in der Verwaltungs-Activity deaktiviert/gelöscht)
  → automatischer Wechsel bzw. sauberer Stopp, falls keiner mehr aktiv ist.
- **Watchdog gegen tote Sender** (Vorbild `dead_until`/`alive_stations()`
  im Docker-Projekt, siehe dessen `CLAUDE.md`) — zwei Erkennungswege im
  `Player.Listener`: `onPlayerError()` (hartes Signal, sofortige Sperre,
  bewusst ohne eigenen Reconnect-Zähler wie `STREAM_FAILURE_LIMIT` dort,
  weil ExoPlayer transiente Fehler schon intern retried) und
  ununterbrochenes `STATE_BUFFERING` länger als
  `BUFFERING_TIMEOUT_SECONDS=15` (deckt "verbindet, liefert aber nie
  Daten" ab, löst nie einen Error aus). Eigene Sperr-Map `deadUntil`
  (`STATION_DEAD_LOCK_SECONDS=300`), getrennt vom Sprache-Cooldown über
  `playback/StationLockReason.kt` (`SPEECH_COOLDOWN` vs. `DEAD`) — beide
  in der Play-Liste unterscheidbar angezeigt. Sind ALLE aktiven Sender
  gesperrt (egal welcher Grund), werden beide Maps geleert und einmal neu
  versucht (`findNextOrEscalate()`) statt hängenzubleiben.
  `manualPlay()` (nicht `play()` direkt — von `MainActivity` für jede
  Nutzer-Auswahl aufgerufen) hebt beide Sperren für den gewählten Sender
  auf: expliziter Nutzerwunsch schlägt Automatik.

Bekannte Grenze dabei: eine schmale Race, bei der ein spät eintreffender
`onPlayerError()` der ALTEN Quelle nach bereits erfolgtem Wechsel die NEUE
Quelle fälschlich sperren könnte — bewusst nicht per Generation-Counter
abgesichert (Zeitfenster sehr schmal). Beide Sperren sind reines
In-Memory-Timing, überleben also keinen App-Neustart.

### `model/StationRepository.kt`: Persistenz als flache JSON-Datei

Singleton, hält die Senderliste als `StateFlow<List<Station>>`,
persistiert nach `filesDir/stations.json` via `Files.move(...,
ATOMIC_MOVE, REPLACE_EXISTING)` (echtes atomares Schreiben — anders als
`stations_store.py` im Docker-Projekt, das wegen eines gebindmounteten
Einzeldatei-Mounts direkt schreiben muss, siehe dessen `CLAUDE.md`;
`filesDir` hier ist normaler lokaler Speicher ohne diese Einschränkung).
Bewusst kein Room: `stations_store.py` als Vorbild ist selbst ein flacher
JSON-Store, die Datenmenge (Dutzende Sender, keine Relationen) rechtfertigt
keinen Compiler/KSP-Dependency samt Entity/DAO/Migrations-Boilerplate.

`Station(id, name, url, category, enabled)` — `id` ist stabil (Slugify +
Kollisionssuffix, analog `stations_store.py`s `_slugify`/`_unique_id`),
Rotation/Cooldown/Watchdog referenzieren ausschließlich darüber, nie über
Listenposition. Rotation = aktivierte Sender alphabetisch nach Name.
Kategorien (`model/Categories.kt`: `Lokal`/`National`/`International`/
`Unsortiert`) sind nur im Code änderbar, keine Nutzereinstellung. Beim
allerersten Start (keine `stations.json` vorhanden) wird ein fester
3-Sender-Startbestand geschrieben, damit sich für Bestandsnutzer beim
Update auf diese Version nichts sichtbar ändert.

### Update-Mechanismus (`update/UpdateManager.kt`)

Kein Play Store, keine Signaturprüfung über die Debug-Signierung hinaus.
Lädt `version.json` vom in `SharedPreferences` gespeicherten Server
(Textfeld auf der Startseite, Default `DEFAULT_UPDATE_BASE_URL` = die
Tailscale-Adresse des Entwicklungs-Hosts), vergleicht `buildTime` als
reinen String gegen `BuildConfig.BUILD_TIME` — deshalb ist der
`version.json`-Schritt beim Bauen (siehe oben) nicht optional. Bei
Unterschied: APK-Download in den Cache, dann System-Installer über
`FileProvider` + `ACTION_VIEW` (`REQUEST_INSTALL_PACKAGES`) — keine stille
Auto-Installation. Server-seitige Gegenstelle ist `update_server.py` +
der systemd-User-Service, beide außerhalb dieses Gradle-Projekts (siehe
`../CLAUDE.md`, Abschnitt "Android-Prototyp") — liefert nur
`radiozapper.apk`/`version.json` aus diesem Verzeichnis aus, kein Auth,
nur übers Tailscale-Netz erreichbar.

Update-Installationen brauchen über die Zeit denselben Debug-Signierschlüssel
(`~/.android/debug.keystore` auf diesem Host) — sonst verweigert Android
das Update, weil sich der APK-Signer geändert hat.

## Kein Auth, nur privater Rahmen

Wie das Docker-Projekt (siehe dessen `CLAUDE.md`, "Kein Auth, nur hinter
VPN"): `update_server.py` hat keinerlei Authentifizierung. Keine Änderungen
vorschlagen oder umsetzen, die auf öffentliche Erreichbarkeit hinauslaufen.
