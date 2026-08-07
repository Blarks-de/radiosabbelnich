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

Bewusst minimal (siehe urspruengliche Anforderung): kein Watchdog/
Ban-System, kein News-Break, keine Settings-UI.

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
- Foreground Service mit Notification, damit Wiedergabe+Analyse
  weiterlaufen, wenn die App im Hintergrund ist; UI zeigt den aktuellen
  Sender live mit, auch wenn der automatische Wechsel ihn geaendert hat
- Einfache UI: Senderliste mit Play-Buttons (nur aktivierte Sender, flach,
  kein Kategorie-Gruppieren - das ist Aufgabe der Verwaltungs-Activity),
  Stop-Button, Statusanzeige, Modell-Download-Fortschritt, Button
  "Sender verwalten"
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
  kein RecyclerView - Datenmenge klein), Add/Edit-`AlertDialog`.
- `vosk/VoskModelManager.kt` - Download+Entpacken des Vosk-Modells nach
  `filesDir`, Fortschritt als `StateFlow<ModelState>`
- `playback/PlaybackService.kt` - Foreground Service, haelt ExoPlayer
  (Wiedergabe) UND `StreamAnalyzer` (Analyse); reagiert auf den
  geglaetteten Status mit automatischem Umschalten UND auf Aenderungen aus
  `StationRepository.stations` (siehe oben); exponiert `currentStation`/
  `status` als StateFlows fuer die UI; Notification via
  `NotificationCompat`/`ServiceCompat.startForeground`
- `analysis/StreamAnalyzer.kt` - **eigene** MediaExtractor/MediaCodec-
  Dekodierung desselben Stream-URLs nur fuer die Analyse (unabhaengig
  vom ExoPlayer der Wiedergabe), Downmix+Resample auf 16kHz-Mono
  (`MonoResampler.kt`), Fuetterung von Vosk, gleitendes Mehrheitsvotum
  mit Hysterese fuer den geglaetteten Sprache/Musik-Status
- `MainActivity.kt` - bindet an den Service, zeigt Senderliste (reaktiv
  aus `StationRepository`), Status und Modell-Fortschritt live an, Button
  zur Verwaltungs-Activity

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
- **Cooldown ist reines In-Memory-Timing, kein Ban-System** - er verfaellt
  einfach nach `STATION_COOLDOWN_SECONDS`, es gibt keine Eskalation (z.B.
  laenger werdende Cooldowns bei wiederholten Treffern) und nichts
  ueberlebt einen Neustart der App. Das eigentliche Watchdog/Ban-System
  ist weiterhin nicht umgesetzt.
- **Keine Mindest-Verweildauer** vor dem naechsten Auto-Switch-Versuch -
  der Cooldown wirkt nur auf den VERLASSENEN Sender, nicht auf den NEUEN
  (der koennte theoretisch sofort wieder als Sprache gelten).
- Kein Wiederverbindungsversuch bei Stream-Abbruch (ExoPlayer-Listener
  fuer Fehler/Retry ist nicht angebunden).
- **Kategorienliste selbst ist weiterhin nur im Code aenderbar**
  (`model/Categories.kt`) - anders als die Sender ist sie keine
  Nutzerdateneinstellung. Kein Bulk-Import (M3U o.ae., anders als im
  Docker-Projekt), keine Drag-Sortierung innerhalb einer Kategorie,
  keine Sender-Suche - bei der auf dem Handy erwarteten Senderzahl
  (Dutzende, nicht Hunderte) bisher nicht als notwendig eingeschaetzt.
- **Kein Import-Reachability-Check** (anders als `station_import.py`s
  `check_reachable()` im Docker-Projekt) - ein neu angelegter Sender mit
  kaputter URL faellt erst beim tatsaechlichen Abspielversuch auf.
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
