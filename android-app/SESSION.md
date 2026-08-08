# RadioZapper Android MVP — Session-Log

Laufendes Protokoll der Arbeit an `android-app/` (chronologisch, neueste
Einträge unten) — hier steht das *Wie und Warum* der einzelnen Schritte,
analog zum `../SESSION.md` des Docker-Projekts. Für den aktuellen
Funktionsstand siehe `README.md` (keine Historie, keine Doppelung dieser
Datei). Frühere Arbeit an dieser App (Erstellung, Smoothing/Auto-Switch,
Cooldown, Update-Mechanismus) ist bislang nur in `../SESSION.md`
protokolliert (diese Datei existierte noch nicht) — ab jetzt läuft alles
Android-Spezifische hier weiter.

## 2026-08-07 — Persistente Senderverwaltung (Plan-Mode, Vorbild stations_store.py)

Auslöser: die 3 Sender waren als `object Stations { val ALL = ... }`
compile-time-Konstanten in `model/Station.kt` - keine Möglichkeit, Sender
hinzuzufügen/zu bearbeiten/zu löschen ohne Rebuild. Nutzerwunsch: echte,
persistente Senderverwaltung, ausdrücklich nach dem Vorbild von
`stations_store.py`/der Config-Seite des Docker-Projekts (stabile IDs,
Kategorien, Enabled-Flag, alphabetische Rotation). Nutzer bat explizit um
Plan-Mode (kein Code vor Freigabe des Plans) - Ablauf: zwei Explore-Agents
(aktueller Android-Stand + Docker-Vorbild), ein Plan-Agent zur Validierung
einzelner Architekturentscheidungen, eine Nutzerfrage zur Kategorienliste,
dann Plan-Datei + `ExitPlanMode`, vom Nutzer freigegeben.

### Entscheidungen aus der Planungsphase

- **Kategorien reduziert** (Nutzerentscheidung statt 1:1-Uebernahme):
  `Lokal, National, International, Unsortiert` statt der 7 Kategorien des
  Docker-Projekts (inkl. "Interstellar") - auf dem Handy vermutlich nur
  eine Handvoll Sender, nicht Hunderte nach einem M3U-Import.
- **JSON-Datei statt Room** (Nutzer bat explizit um eine begruendete
  Empfehlung) - Vorbild `stations_store.py` ist selbst ein flacher
  JSON-Store, Datenmenge/Komplexitaet rechtfertigt keine Room-Dependency
  (Compiler/KSP, Entity/DAO, Migrations-Versionierung). Details siehe
  README, Abschnitt "Persistenz: JSON-Datei statt Room".
- **Singleton-Init ueber eine neue `Application`-Subklasse statt
  "wer zuerst dran ist, ruft init() auf"** - ein vom Plan-Agent
  vorgeschlagener Schutz gegen genau die Art Bug, die erst auffaellt,
  wenn ein neuer Einstiegspunkt (hier: die neue Verwaltungs-Activity) die
  Initialisierung vergisst.
- **Atomarer Schreibvorgang echt umgesetzt** (`Files.move(...,
  ATOMIC_MOVE, REPLACE_EXISTING)`) statt `File.renameTo()` - letzteres ist
  laut eigener Javadoc plattformabhaengig und liefert bei Fehlern nur
  `false` statt einer Exception. Anders als `stations_store.py` (dort
  wegen Docker-Bind-Mount unmoeglich) kann Android das tatsaechlich.
  Bewusste Verbesserung gegenueber dem Vorbild, keine blinde Kopie.
  Zweite Gradle-KTS-Falle in dieser App-Historie: kein direkter Bezug
  hier, aber `import java.nio.file.Files`/`StandardCopyOption` normal aus
  `java.nio.file` - keine Ueberraschung, im Gegensatz zu den fruehreren
  `java.text.SimpleDateFormat`-Vollqualifizierungs-Problemen im
  Gradle-KTS-Scope (siehe `../SESSION.md`, Fortsetzung 2/3 vom selben Tag).
- **Loeschsperre exakt wie im Vorbild**: mindestens 1 Sender TOTAL, nicht
  "mindestens 1 aktivierter" - `stations_store.delete()` prueft ebenfalls
  nur gegen die ungefilterte Liste.

### Umsetzung

- `model/Station.kt`: `category`/`enabled`-Felder ergaenzt, das alte
  `object Stations` entfernt.
- `model/Categories.kt` (neu): feste Kategorienliste + Default.
- `model/StationRepository.kt` (neu, Kern der Aenderung): Singleton-
  `object`, `StateFlow<List<Station>>`, CRUD (`addStation`/
  `updateStation`/`setEnabled`/`deleteStation`), `activeStations()` fuer
  die Rotation, Slugify+Kollisionssuffix fuer IDs (analog `_slugify`/
  `_unique_id`), Seed-Startbestand (dieselben 3 Sender/IDs/URLs wie vorher)
  nur wenn `stations.json` noch nicht existiert.
- `RadioZapperApplication.kt` (neu) + Manifest-Eintrag
  (`android:name=".RadioZapperApplication"`).
- `playback/PlaybackService.kt`: `attemptAutoSwitch()` liest jetzt
  `StationRepository.activeStations()` statt `Stations.ALL`. Neuer
  dritter Collector (`handleStationListChanged()`) neben dem bestehenden
  `analyzer.status`-Collector: prueft bei jeder Aenderung der Senderliste,
  ob der GERADE LAUFENDE Sender noch aktiv ist, sonst automatischer
  Wechsel zum ersten aktiven Sender oder sauberer Stopp. Fruehes
  `?: return` bei `_currentStation.value == null` (vom Plan-Agent als
  konkreter Bug im ersten Entwurf identifiziert - sonst haette jede
  Bearbeitung eines unbeteiligten Senders die Wiedergabe faelschlich neu
  gestartet).
- `MainActivity.kt`: `setupStationRows()` (einmalig) → `observeStations()`
  (reaktiver Collector auf `StationRepository.stations`, volles
  `removeAllViews()`+Neuaufbau je Aenderung - kein RecyclerView, Datenmenge
  klein). Neuer Button "Sender verwalten" startet die neue Activity.
- `station/StationManagementActivity.kt` (neu) + `activity_station_
  management.xml`/`item_station_manage.xml`/`dialog_edit_station.xml`:
  kategorie-gruppierte Liste (auch leere Kategorien sichtbar), Enabled-
  Checkbox, Bearbeiten/Loeschen pro Zeile, ein wiederverwendeter Add/Edit-
  `AlertDialog` mit `Spinner` fuer die Kategorie. Fehlermeldungen aus dem
  Repository (Validierung, Loeschsperre) werden 1:1 als Toast angezeigt.

### Verifiziert (Emulator, frischer Build)

- `./gradlew assembleDebug` erfolgreich beim ERSTEN Versuch (kein
  Kompilierfehler in dieser Runde, anders als bei fruaheren
  Gradle-KTS-Ueberraschungen).
- Frischer Install: 3 geseedete Sender identisch zu vorher, korrekt unter
  "National" in Hauptschirm UND Verwaltungs-Activity, alle 4 Kategorien
  (auch leere) sichtbar in fester Reihenfolge.
- Neuer Sender ("SWR3 Test", Kategorie "International") per Dialog
  angelegt → sofort in beiden Bildschirmen sichtbar. `adb shell run-as
  com.radiozapper.mvp cat files/stations.json` zeigt korrektes,
  menschenlesbares JSON mit generierter id `swr3-test` (Kollisionsfreiheit
  gegenueber dem bestehenden `swr3` bestaetigt).
- 1LIVE gestartet, waehrend laufender Wiedergabe in der Verwaltungs-
  Activity deaktiviert → Logcat: "Senderliste geaendert, '1LIVE (WDR, viel
  Musik)' nicht mehr aktiv - schalte auf 'Deutschlandfunk (viel
  Sprache)'" - der neue Collector funktioniert wie geplant, kein Absturz.
- Sender nacheinander geloescht bis auf einen einzigen → Loeschversuch auf
  dem letzten liefert Toast "Mindestens ein Sender muss konfiguriert
  bleiben." statt die Liste zu leeren.
- App per `am force-stop` beendet und neu gestartet → kompletter Stand
  (inkl. des angelegten Testsenders, inkl. der 1LIVE-Deaktivierung)
  persistiert korrekt.
- Crash-Log-Buffer (`adb logcat -d -b crash`) nach jedem einzelnen Schritt
  der Sequenz leer geprueft - keine Ausnahme in der gesamten Testreihe.

### Bewusst NICHT gemacht

Kein Live-Test von "Cooldown ueberlebt eine Umbenennung waehrend er noch
laeuft" (Plan-Punkt 8) - durch Code-Review abgesichert (ID aendert sich
bei `updateStation()` nachweislich nie), aber nicht durch einen echten
zeitgesteuerten Emulator-Durchlauf verifiziert (haette wegen der
Vosk-Erkennungszeiten mehrere Minuten zusaetzlich gebraucht, Risiko wurde
als gering genug fuer einen reinen Code-Beleg eingeschaetzt). Kein
Bulk-Import, keine Sender-Suche, keine Drag-Sortierung, keine
Reachability-Pruefung beim Anlegen - siehe README, "Bekannte Grenzen".
Root-`CLAUDE.md`/`SESSION.md`/`VERSION` nicht angefasst - betrifft
ausschliesslich `android-app/`, keine Aenderung am Docker-Dienst.

## 2026-08-07 (Fortsetzung) — Watchdog gegen tote/nicht antwortende Sender (Plan-Mode, Phase 2 aus dem Fahrplan)

Auslöser: `RadioZapper_Android_Fahrplan.md`, Phase 2 - seit der
Senderverwaltung (siehe Eintrag oben) kann die Liste beliebige, auch
kaputte URLs enthalten. Ohne Watchdog kann ein toter Stream die Rotation
lahmlegen, genau wie im Docker-Projekt vor dessen `dead_until`/
`alive_stations()` (siehe dessen `SESSION.md`, "Review-Befunde: Watchdog
gegen tote Sender", 8,5h Stillstand durch BBC Radio Scotland). Wieder per
Plan-Mode: zwei Explore-Agents (aktueller Android-Stand + Docker-Vorbild),
Plan geschrieben, vom Nutzer freigegeben, danach umgesetzt.

### Umsetzung

- **Erkennung**: `Player.Listener` an ExoPlayer (vorher komplett
  unbeobachtet) - `onPlayerError()` sperrt sofort (ExoPlayer versucht bei
  transienten Fehlern bereits intern zu reconnecten, bevor der Callback
  ueberhaupt feuert, ein weiterer eigener Retry waere doppelte Arbeit).
  Zusaetzlich ein Timeout auf ununterbrochenes `Player.STATE_BUFFERING`
  (`BUFFERING_TIMEOUT_SECONDS=15`) - deckt "verbindet, liefert aber nie
  Daten" ab, das nie einen Error ausloest (der eigentliche BBC-Radio-
  Scotland-Fall). Timer startet bei Eintritt in `STATE_BUFFERING`, wird
  bei jedem anderen Zustand abgebrochen - deckt automatisch auch die
  anfaengliche "Verbinde…"-Phase mit ab, kein Sonderfall noetig. Bonus:
  `StreamAnalyzer`s bisher ignoriertes `PlaybackStatus.ERROR` (eigene,
  unabhaengige Dekodierung, siehe deren Klassen-Doc) haengt jetzt ebenfalls
  am selben Mechanismus - zweite, praktisch kostenlose Bestaetigung.
- **Sperr-Mechanismus**: neue Map `deadUntil` (`STATION_DEAD_LOCK_SECONDS
  =300`, 5 Min. wie im Docker-Projekt), bewusst getrennt von der
  bestehenden `stationCooldownUntil` (Sprache-Cooldown) - neues Enum
  `StationLockReason { SPEECH_COOLDOWN, DEAD }` haelt beide Gruende
  unterscheidbar. `nextStationOffCooldown()` verallgemeinert zu
  `nextAvailableStation()` (prueft BEIDE Maps) + `findNextOrEscalate()`
  (neu: findet sich kein Kandidat, weil ALLE aktiven Sender durch
  irgendeinen der beiden Gruende gesperrt sind, werden beide Maps geleert
  und einmal neu versucht - wie `dead_until.clear()` im Docker-Projekt,
  statt haengenzubleiben). Neue `manualPlay()` (statt `play()` direkt) fuer
  jede Nutzer-Auswahl: hebt beide Sperren fuer den gewaehlten Sender auf -
  expliziter Nutzerwunsch schlaegt Automatik. `MainActivity.startPlayback()`
  nutzt jetzt `manualPlay()`.
- **UI**: neuer `PlaybackService`-StateFlow `lockedStations: Map<String,
  StationLockReason>`, in `MainActivity`s Play-Liste als kleiner
  Zusatztext ("⏸ Pause wegen Sprache" / "⚠ Antwortet nicht") unter dem
  Sendernamen. Bewusst kein Live-Countdown (keine zusaetzliche UI-Tick-
  Schleife nur fuer Kosmetik) - Text verschwindet einfach beim naechsten
  ohnehin eintretenden Re-Render. `PlaybackStatus`-Enum bewusst
  unangetastet - beschreibt weiterhin nur den Vosk-Inhalts-Befund, nicht
  die Sender-Verfuegbarkeit.

### Verifiziert (Emulator, ueber die Verwaltungs-Activity aus Phase 1 einen echten toten Sender angelegt)

- Sender mit nicht routbarer IP (`http://10.255.255.1:9999/...`) angelegt,
  abgespielt → nach exakt 15s Logcat: "Kein Fortschritt seit 15s -
  'TOT-Test' antwortet nicht" → "fuer 5 Min. aus der Rotation" →
  automatischer Wechsel zu SWR3. Play-Liste zeigt "⚠ Antwortet nicht"
  unter TOT-Test.
- TOT-Test manuell erneut angetippt → Badge verschwindet sofort, "Verbinde…"
  startet neu (Sperre korrekt aufgehoben) → nach weiteren 15s erneut
  korrekt gesperrt (Wiederholbarkeit bestaetigt).
- **Eskalationstest**: zusaetzlich SWR3 per Bearbeiten-Dialog auf eine
  zweite tote URL umgestellt (beide aktiven Sender jetzt tot) →
  abgespielt → Logcat: "Sender 'SWR3...' antwortet nicht..." → "Alle 2
  aktiven Sender gesperrt - hebe beide Sperrlisten auf und probiere
  erneut" → "Schalte weiter zu 'TOT-Test'". Weiterbeobachtet: stabiler
  ~15s-Zyklus zwischen beiden (weiterhin toten) Sendern, kein Haengen-
  bleiben, keine Exception - erwartetes Verhalten bei einem Zustand, in
  dem tatsaechlich ALLES tot ist (gleiche Grenze wie im Docker-Projekt:
  "alle Sperren aufheben" bedeutet nicht "alles ist jetzt reparierbar").
- **Regressionscheck**: SWR3-URL zurueck auf die echte Adresse gesetzt,
  abgespielt → 25s lang stabil "🎵 Musik", kein Watchdog-Fehlalarm auf
  einem gesunden Sender, kein Log-Eintrag.
- Crash-Log-Buffer nach jedem Schritt leer geprueft - keine Ausnahme in
  der gesamten, recht langen Testreihe (inkl. mehrfacher Dialog-
  Bedienfehler meinerseits beim Testen selbst, z.B. vergessenes Tippen
  auf "Speichern" - jeweils ohne App-Absturz, nur ohne Wirkung).

### Bewusst NICHT gemacht

Kein Reconnect-Zaehler vor dem Sperren bei `onPlayerError` (anders als das
Docker-Projekt mit seinem `STREAM_FAILURE_LIMIT`) - ExoPlayer haelt
bereits eigene interne Retries fuer transiente Fehler, ein zusaetzlicher
manueller Zaehler waere vermutlich redundant; falls sich das in der Praxis
als zu aggressiv herausstellt, waere das ein guter naechster Schritt.
Kein Sperr-Anzeige in der Verwaltungs-Activity (nur in der Play-Liste auf
dem Hauptschirm) - dort ist ein gesperrter Sender tatsaechlich relevant
(man will ihn abspielen), in der Verwaltung eher nicht. Kein Schutz gegen
die theoretische Race (spaet eintreffender `onPlayerError`-Callback der
ALTEN Quelle nach bereits erfolgtem `play()`-Wechsel auf eine neue) -
schmales Zeitfenster, dokumentiert als bekannte Grenze statt Generation-
Counter o.ae. einzubauen.

## 2026-08-08 — Optik-Angleichung (Phase 5 aus dem Fahrplan) + Kodi-/M3U-Sender-Import (Plan-Mode)

Auslöser: `RadioZapper_Android_Fahrplan.md`, Phase 5 ("Android und Web
fühlen sich wie ein Projekt an") plus expliziter Nutzerwunsch nach einem
Android-Pendant zu `station_import.py` ("Radioliste von Kodi importieren").
Plan-Mode mit zwei Rückfragen an den Nutzer zur Tiefe des
Erreichbarkeits-Checks (siehe unten), dann Umsetzung, dann Live-Test im
Emulator.

### Phase 5: Branding, Bullshitometer, ZAPPEN!

- **Banner + Türkis** (`#1ABC9C`, Web-Vorbild `webui.py`): `pics/radiozapper.webp`
  nach `drawable-nodpi/banner.webp` kopiert (dieselbe Datei wie im Web-
  Interface), `ShapeableImageView` (Material-Dependency war schon vorhanden)
  oben in `activity_main.xml`. `colors.xml` `primary`/`accent` auf Türkis -
  färbt über das bestehende `Theme.MaterialComponents`-Theme automatisch alle
  Buttons/EditTexts mit.
- **Bullshitometer**: `StreamAnalyzer` berechnete den rohen `speechRatio`
  (Anteil erkannter Text-Chunks im Glättungsfenster) bereits intern für die
  Hysterese, reichte ihn aber nicht nach außen. Neuer `_speechRatio:
  MutableStateFlow<Double?>` (null = idle/noch kein volles Fenster),
  durchgereicht über `PlaybackService.speechRatio` (gleiches Muster wie das
  bestehende `status`), gebunden in `MainActivity.renderBullshitometer()` -
  horizontale `ProgressBar` + Prozenttext, Farbverlauf grün→rot exakt wie im
  Web (`hue = max(0, 120 - pct*1.2)`, HSL 70%/45%). Android kennt nur
  HSV-Konvertierung, keine HSL - eigene `hslToColor()`-Hilfsfunktion, sonst
  hätte der Farbverlauf sichtbar anders ausgesehen als im Web-Vorbild.
  Bewusst KEIN separater "STT-Meter" (der Docker-Doppel-Detektor VAD+STT hat
  in Android keine Entsprechung - hier gibt es nur Vosk direkt) und KEIN
  "Fingerprint-Chip" (Phase 4/Fingerprinting ist noch nicht umgesetzt) - ein
  zweiter, inhaltlich identischer Balken wäre reine Doppelung gewesen.
- **"⚡ ZAPPEN!"-Button**: `PlaybackService.manualSkip()` ruft schlicht das
  bereits bestehende `attemptAutoSwitch()` auf - inhaltlich exakt dieselbe
  Aktion wie ein automatisch erkannter Sprache-Treffer (Cooldown setzen,
  Ring weiterschalten, Pause-Eskalation bei "alle gesperrt"), nur manuell
  statt automatisch ausgelöst. Kein Duplikat der Ring-Logik nötig.

### Kodi-/M3U-Sender-Import (`importer/StationImporter.kt`, `importer/StationReachabilityChecker.kt`)

Vorbild: `station_import.py` im Docker-Projekt. Zwei Rückfragen an den
Nutzer vor der Umsetzung, weil der 8s-Audiofluss-Check des Vorbilds
(ffmpeg, parallelisiert) auf dem Handy für eine mehrere-hundert-Sender-Liste
(Kodinerds-Liste) zu langsam/akkuintensiv wäre:

1. **Import selbst ohne Reachability-Check** (Nutzerentscheidung) - nur
   laden/parsen/deduplizieren, alle neuen Sender deaktiviert in "Unsortiert"
   übernehmen. Begründung: deaktivierte Sender sind ohnehin harmlos, bis der
   Nutzer sie bewusst aktiviert.
2. **Separater "🔍 Unsortierte Sender prüfen"-Knopf** (Nutzerentscheidung:
   nur Kategorie "Unsortiert", Ergebnis rein informativ, kein
   Auto-Löschen) trägt den echten Check nach, wer will.

Umsetzung:

- `StationImporter.parseM3u()` - 1:1 dieselbe `#EXTINF:...,Name`-Logik wie
  `station_import.parse_m3u`. Default-URL identisch zum Docker-Projekt
  (`http://bit.ly/kn-kodi-radio`, editierbar per Textfeld, SharedPreferences
  wie `UpdateManager.getBaseUrl()`/`setBaseUrl()`).
- **Live-Bug beim ersten echten Test entdeckt und gefixt**: `bit.ly`
  redirected `http://` → `https://` (301). `HttpURLConnection` folgt
  Redirects standardmäßig NUR innerhalb desselben Protokolls - bei einem
  Protokollwechsel bekommt man kommentarlos die Redirect-Antwortseite selbst
  statt der Playlist (erster Testlauf: "0 geprüft, 0 neu hinzugefügt" trotz
  Erfolg ohne Exception). Fix: `fetchM3u()` folgt `Location`-Headern jetzt
  von Hand, protokollübergreifend inklusive. Nach dem Fix: echter Import der
  Kodinerds-Liste im Emulator, 362 Einträge geparst, 361 neu (1 Duplikat -
  SWR3 war schon vorhanden - korrekt übersprungen).
- `StationRepository.bulkAdd()` neu: wie `addStation()`, aber EIN
  Schreibvorgang für die ganze Batch statt einem pro Sender (analog
  `stations_store.bulk_add`).
- `StationReachabilityChecker.checkReachable()` - eigenständige
  MediaExtractor/MediaCodec-Dekodierschleife (kein Vosk nötig), Wall-Clock-
  begrenzt auf `CHECK_WINDOW_MS=8000`/`CHECK_TAIL_MS=3000`/
  `CHECK_MIN_SECONDS=3.0`, konzeptionell identisch zu
  `station_import.check_reachable()` (siehe dessen Docstring/`../CLAUDE.md`
  Abschnitt "Sender-Import"). `withTimeoutOrNull()` zusätzlich als
  Sicherheitsnetz: `MediaExtractor.setDataSource()` kann bei einer nicht
  routbaren Adresse länger blockieren als das Prüffenster selbst (OS-
  Verbindungstimeout statt der eigenen 8s) - ohne die Grenze würde ein
  einziger toter Kandidat den ganzen Check-Lauf verzögern.
  `CHECK_CONCURRENCY=3` (Kotlin-`Semaphore`, deutlich weniger als die 10 im
  Python-Vorbild) - MediaCodec-Dekodierung ist auf dem Handy teurer als ein
  ffmpeg-Prozess auf dem Server, und nebenbei läuft im selben Prozess meist
  noch Wiedergabe + Vosk-Analyse des aktuellen Senders.
- UI: neue Sektion "📻 Sender-Import" + "🔍 Unsortierte Sender prüfen" in
  `StationManagementActivity`, Statuszeilen nach dem `renderUpdateState()`-
  Muster aus `MainActivity`. Neues `unreachableBadge` in
  `item_station_manage.xml` ("⚠ nicht erreichbar"), sichtbar wenn
  `station.id in StationReachabilityChecker.unreachableIds.value`.

### Verifiziert (Emulator, `test_device`)

- Optik: Banner + Türkis sichtbar auf beiden Bildschirmen, Bullshitometer
  bewegt sich live (SWR3 abgespielt, Balken bei 0% während "🎵 Musik").
  ZAPPEN! löst sofortigen Reconnect aus (Logcat: "Sprache erkannt auf
  'SWR3...' - schalte weiter zu 'SWR3...'" - bei nur 1 aktivem Sender via
  Eskalationspfad korrekt auf sich selbst zurück, kein Hängenbleiben).
- Import: `362 geprüft, 361 neu hinzugefügt` mit der echten Kodinerds-Liste
  über den Emulator (nach dem Redirect-Fix), `adb shell run-as
  com.radiozapper.mvp cat files/stations.json` bestätigt alle 361 neuen
  Einträge korrekt deaktiviert in Kategorie "Unsortiert", bestehender SWR3
  unverändert aktiviert in "National".
- Check-Knopf: Live-Lauf über alle 361 "Unsortiert"-Sender gestartet,
  Fortschrittsanzeige zählt korrekt hoch ("Prüfe 8 / 361 …" nach ~45s bei
  Concurrency 3), erster echter Treffer "⚠ nicht erreichbar" bei "100'5
  Alemannia" korrekt als Badge angezeigt, Sender blieb unverändert in der
  Liste (kein Auto-Löschen). Kompletten 361er-Lauf nicht bis zum Ende
  abgewartet (~15-25 Min. bei dieser Sender-Zahl) - Mechanismus
  (Concurrency, Fortschritt, Timeout-Sicherheitsnetz) damit aber am echten
  Datensatz verifiziert, nicht nur gegen eine kleine Testliste.
- Crash-Log-Buffer nach jedem Schritt leer geprüft (`adb logcat -d | grep
  -iE "FATAL|AndroidRuntime"`) - keine Exception über die gesamte
  Testreihe.

### Bewusst NICHT gemacht

Kein automatisches Löschen nicht erreichbarer Sender (Nutzerentscheidung -
ein falsch-negativer Check, z.B. durch eine kurze Netzwerkstörung während
des Checks, würde sonst einen echten Sender unwiderruflich entfernen).
Keine Persistenz der Check-Ergebnisse über einen App-Neustart hinweg (rein
informativ für die aktuelle Sitzung, `unreachableIds` lebt nur im
In-Memory-`StateFlow` von `StationReachabilityChecker`). Kein Reachability-
Check beim Import selbst (siehe oben) - bewusste Abweichung vom
Docker-Vorbild, dort verhindert der Check das Eindringen toter Sender in
die LAUFENDE Rotation, hier landen Importe ohnehin deaktiviert.

## 2026-08-08 (Fortsetzung) — Prebuffering für flüssigeres Umschalten (Phase 3 aus dem Fahrplan, Plan-Mode)

Auslöser: `RadioZapper_Android_Fahrplan.md`, Phase 3 - jeder Senderwechsel
machte bisher einen kompletten Kaltstart in `PlaybackService.play()`
(`player.stop()` → neue `MediaItem` → `prepare()` → Buffering), spürbare
Lücke von 1-3s. Plan-Mode mit 3 vorgestellten Optionen
(Aufwand/Nutzen: nur UI-Feedback / eine einzelne vorgewärmte Instanz für
den nächsten Sender / ein kleiner Pool analog `PrebufferedSource` im
Docker-Projekt) - Nutzer wählte die mittlere Option.

### Umsetzung (alles in `playback/PlaybackService.kt`)

- **`refreshPreload()`**: bereitet über die bereits bestehende
  `nextAvailableStation()`-Funktion den laut Ringlogik nächsten Sender im
  Hintergrund vor (`ExoPlayer.prepare()`, `playWhenReady=false`). Läuft rein
  reaktiv (kein Polling-Timer wie `sync_prebuffer()` im Docker-Projekt) -
  aufgerufen an den drei Stellen, an denen sich einer der Einflussfaktoren
  (aktueller Sender/Sperren/Senderliste) tatsächlich ändert: Ende von
  `play()`, Ende von `refreshLockedStationsSnapshot()` (deckt dadurch ALLE
  deren Aufrufer ab), Ende von `handleStationListChanged()`.
- **`play()`**: übernimmt den vorgewärmten Player statt eines Kaltstarts,
  wenn das Wechselziel mit dem vorbereiteten Kandidaten übereinstimmt - der
  Normalfall, weil `attemptAutoSwitch()` (automatisch UND der
  ZAPPEN!-Button) sowie `handlePlaybackFailure()` (Watchdog) über dieselbe
  `nextAvailableStation()`-Logik gehen wie `refreshPreload()`.
- **Wichtiger, beim Testen tatsächlich getroffener Randfall**: ein
  `Player.Listener` feuert `onPlaybackStateChanged` nur bei KÜNFTIGEN
  Zustandswechseln, nicht rückwirkend für den Zustand zum Zeitpunkt von
  `addListener()`. War der übernommene Vorwärm-Kandidat beim Wechsel noch
  nicht `STATE_READY`, hätte der `BUFFERING_TIMEOUT_SECONDS`-Watchdog nie
  angefangen zu laufen. Fix: die Timer-Start-Logik aus dem Listener in eine
  eigene `armBufferingWatchdog()`-Funktion extrahiert, die `play()` nach
  einer Übernahme explizit erneut aufruft, falls der übernommene Player noch
  buffert.
- Ein eigener, minimaler `preloadFailureListener` verwirft einen
  fehlgeschlagenen Kandidaten NUR aus der Vorwärmung (`clearPreload()`) -
  löst bewusst NICHT den `deadUntil`/Watchdog-Mechanismus aus, solange der
  Sender noch nicht `current` ist (ein Kandidat, der nie promoted wird, ist
  kein Fall für die 5-Minuten-Sperre).
- `stopPlayback()`/`onDestroy()` geben den Vorwärm-Player zusätzlich frei.

### Verifiziert (Emulator, 3 aktive Sender: Deutschlandfunk/SWR3/eine
absichtlich tote Test-URL "TOT-Test")

- Logcat zeigt den kompletten Normalfall zweimal hintereinander:
  `Waerme 'X' als naechsten Kandidaten vor` direkt nach jedem Wechsel, dann
  bei der nächsten Sprache-Erkennung `Uebernehme vorgewaermten Kandidaten
  'X' - kein Kaltstart` statt der bisherigen Kaltstart-Logs - der
  Kern-Mechanismus funktioniert wie geplant.
- **Der oben beschriebene Randfall trat live tatsächlich auf** und wurde
  korrekt behandelt: TOT-Test wurde als noch-buffernder Kandidat übernommen,
  exakt `BUFFERING_TIMEOUT_SECONDS` (15s) später feuerte
  `Kein Fortschritt seit 15s - 'TOT-Test' antwortet nicht` trotz des späten
  `addListener()` - ohne den `armBufferingWatchdog()`-Fix wäre das nicht
  passiert. Sender wurde korrekt für 5 Minuten gesperrt und der Service
  schaltete sauber (wieder per Warmstart) auf den nächsten Kandidaten
  weiter.
- Ein zweiter, unabhängiger Pfad ebenfalls beobachtet: ein noch gar nicht
  übernommener Vorwärm-Kandidat (TOT-Test, nur im Hintergrund am Puffern)
  schlug für sich fehl - Logcat: `Vorgewaermter Kandidat 'tot-test'
  fehlgeschlagen - verwerfe`, ohne die `deadUntil`-Sperre auszulösen (wie
  geplant - der Sender wurde ja nie `current`).
- Kein Absturz über mehrere komplette Wechsel-Zyklen (`adb logcat -d | grep
  FATAL` durchgehend leer).
- "Stopp" beendet die Wiedergabe weiterhin sauber.

### Bewusst NICHT gemacht

Kein Pool mehrerer vorgewärmter Kandidaten (Nutzerentscheidung, siehe oben)
- `manualPlay()` (Sender in der Liste antippen) profitiert deshalb nur
  zufällig vom Warmstart. Keine Persistenz/Debug-Anzeige des
  Vorwärm-Zustands in der UI - rein internes Implementierungsdetail, nur in
  Logcat sichtbar (`Log.d`).

### Nebenbeobachtung (kein Bug, bestehendes Verhalten aus Phase 2)

Bei nur 2-3 Sendern, die im Test alle sehr sprachlastig sind, greift die
"alle gesperrt"-Eskalation (`findNextOrEscalate()`) sehr häufig und leert
dabei BEIDE Sperrlisten - das hebt inzwischen auch eine erst Sekunden zuvor
gesetzte Watchdog-Sperre (`deadUntil`) wieder auf, ein toter Testsender kam
dadurch im Test mehrfach kurz hintereinander erneut dran. Exakt das
dokumentierte Verhalten von `dead_until.clear()`/der Eskalation aus Phase 2,
keine Regression durch die Vorwärmung - bei einer realistischeren
Sendermenge (mehr als 2-3 aktive Sender) tritt der Fall seltener auf.

## 2026-08-08 (Fortsetzung 2) — Nachrichten-Pause / News-Break (Phase 6 aus dem Fahrplan, Plan-Mode)

Auslöser: `RadioZapper_Android_Fahrplan.md`, Phase 6. Vorbild: `news_break.py`
+ dessen Einbindung in `radiozapper.py`s `main()` (Docker-Projekt). Plan-Mode
mit einer Rückfrage (`enabled_hours`-Ruhezeiten des Vorbilds weglassen -
Nutzerentscheidung: ja, kleinere UI, passt zum "bewusst minimal"-Ansatz),
danach Umsetzung, danach Live-Test im Emulator mit selbst erzeugten
Test-MP3s und manuell vorgestellter Systemzeit.

### Architektur

- **`newsbreak/NewsBreak.kt`**: reine Domänenlogik, direktes Pendant zu
  `news_break.py` - `activeSlot()` (nächstgelegene :00/:30-Grenze,
  Fensterprüfung über `LocalDateTime`/`ChronoUnit`) und `pickRandom()`
  (Zufallsauswahl mit `RECENT_HISTORY_SIZE=3`-Ausschluss, generisch über
  `keyOf: (T) -> String` statt konkret auf Dateipfade festgelegt). Kennt
  weder Android/SAF noch `PlaybackService` - exakt dieselbe Trennung wie im
  Docker-Projekt, aus demselben Grund (die Switch-Infrastruktur existiert
  bereits in `PlaybackService`, nicht duplizieren).
- **`newsbreak/NewsBreakSettings.kt`**: SAF-Ordnerzugriff
  (`ACTION_OPEN_DOCUMENT_TREE`, gestartet von `MainActivity`,
  `takePersistableUriPermission()` für Persistenz über App-Neustarts hinweg)
  + SharedPreferences (`enabled`/`windowMinutes`, Default identisch zum
  Docker-Projekt: `false`/`2.0`). Neue Dependency
  `androidx.documentfile:documentfile` fürs Auflisten der `.mp3`-Kind-Dateien
  einer Tree-Uri ohne rohes `DocumentsContract`-Cursor-Handling.
- **`PlaybackService.kt`** (Hauptarbeit): `_currentStation` bleibt während
  einer Pause bewusst der pausierte Sender (wie `current` im Docker-Projekt)
  - Ring-Berechnung, Cooldown/Dead-Maps und die Phase-3-Vorwärmung laufen
  dadurch unverändert weiter, ohne einen einzigen Sonderfall dafür zu
  brauchen. Neue `newsBreakActive`/`newsBreakFileName`-StateFlows fürs UI.
  Ein periodischer Tick (`startNewsBreakTicker()`, alle 15s während der
  Wiedergabe, gestartet in `play()`) ersetzt die "jeder Hauptloop-
  Durchlauf"-Prüfung des Docker-Projekts - Android hat keinen vergleichbaren
  Dauer-Takt (`StreamAnalyzer` liefert nur, solange ein echter Sender läuft).
  `playerListener` unterscheidet jetzt zwischen "MP3 hat ein Problem"
  (`advanceNewsBreak()`) und "Sender hat ein Problem" (regulärer Watchdog) -
  sonst hätte ein MP3-Fehler fälschlich den pausierten, aber gesunden
  Sender als tot markiert. Neuer `STATE_ENDED`-Zweig im Listener (Radiostreams
  enden nie, das war vorher kein behandelter Fall).
- **Kein `-re`/realtime-Sonderfall nötig** (anders als im Docker-Projekt,
  dessen ffmpeg-Pipe eine lokale Datei sonst in Sekundenbruchteilen
  durchreicht) - ExoPlayer spielt eine lokale Datei ohnehin in ihrem eigenen
  Tempo ab, unabhängig von der Quelle. Eine der Stellen, an denen die
  Android-Umsetzung einfacher ist als das Vorbild.
- `manualPlay()`/`manualSkip()` rufen `interruptNewsBreak()` zuerst auf
  (Pendant zu `note_news_break_interrupted()`, an genau den beiden
  Aufrufstellen wie im Docker-Projekt: manueller Switch UND ZAPPEN!).
  Android brauchte dafür KEINEN Sonderfall wie Pythons
  `manual_id != current["id"]`-Guard-Erweiterung - `manualPlay()` ruft
  ohnehin immer `play()` auf, unabhängig davon ob schon dieselbe Station
  läuft.

### UI (`MainActivity.kt`)

Neue Sektion "📰 Nachrichten-Pause" auf dem Hauptbildschirm (analog zur
bestehenden Update-Server-Sektion): Aktiviert-Checkbox (speichert sofort),
"📁 Ordner wählen"-Button (SAF-Picker, zeigt danach den gewählten
Ordnernamen), Fensterlänge-Feld + Speichern-Button. Die "Läuft: ..."-Anzeige
kombiniert jetzt drei Service-Flows (`currentStation`/`newsBreakActive`/
`newsBreakFileName`) über eine gemeinsame `renderCurrentDisplay()` -
während einer Pause "📰 Nachrichten-Pause: <Dateiname>" statt des
Sendernamens, Statuszeile wird geleert (sonst stünde irreführend "Gestoppt"
da, weil `analyzer.stop()` beim Eintritt in die Pause aufgerufen wird).

### Verifiziert (Emulator, `test_device`)

Drei selbst erzeugte 6s-Test-MP3s (`ffmpeg`, reine Sinustöne 440/660/880Hz)
in einen Ordner gepusht, per SAF-Picker ausgewählt. Da die Fensterlogik an
echte Uhrzeiten gebunden ist: Emulator-Systemzeit per `adb root` + `adb
shell date` mehrfach auf 30s vor eine :00/:30-Grenze gestellt (funktioniert
auf diesem AVD-Image ohne Weiteres).

- **Kompletter Normalfall**: Fenster erreicht → "📰 Nachrichten-Pause:
  spiele 'tone_c.mp3' (zurück zu 'Deutschlandfunk' danach)" → automatisch
  `tone_a.mp3` → `tone_b.mp3` → `tone_c.mp3` (je nach Ablauf der 6s-Datei,
  **der historische Kern-Bug "spielte nur eine MP3" tritt nicht auf** -
  mehrere Dateien liefen bis das Fenster um war) → nach Fensterende exakt
  "📰 Nachrichten-Pause-MP3 zu Ende - zurück zu 'Deutschlandfunk'", Sender
  lief normal weiter (inkl. sofort wieder korrekt greifender Auto-Switch-
  Logik, sobald Deutschlandfunk erneut Sprache lieferte).
- **Recent-Ausschluss verifiziert**: bei nur 3 Dateien im Ordner (=
  `RECENT_HISTORY_SIZE`) wich die Auswahl c→a→b→c - kein direktes
  Wiederholen, Fallback auf Wiederholung erst nachdem alle 3 einmal dran
  waren (genau das dokumentierte Verhalten bei kleinen Ordnern).
- **Manueller Interrupt (ZAPPEN!)**: während einer laufenden Pause
  angetippt → Pause endete sofort, normaler Ringwechsel lief korrekt weiter
  (sogar per Vorwärm-Übernahme, kein Kaltstart - Phase-3-Integration
  funktioniert nahtlos mit).
- Kein Absturz über die gesamte Testreihe (`adb logcat -d | grep -iE
  "FATAL|AndroidRuntime"` durchgehend leer).

### Bewusst NICHT gemacht

`enabled_hours`-Ruhezeiten des Docker-Vorbilds (Nutzerentscheidung, siehe
oben). Kein manueller Interrupt-Test über einen Sender-Tap in der Liste
(nur ZAPPEN! getestet) - beide Pfade rufen aber dieselbe
`interruptNewsBreak()`-Funktion auf, keine getrennte Logik, die getrennt
hätte verifiziert werden müssen. Keine Persistenz-Prüfung des SAF-Zugriffs
über einen echten Geräte-Neustart hinweg (nur App-intern via
`takePersistableUriPermission()` - laut Android-Doku über Neustarts
hinweg gültig, im Emulator nicht eigens neu gebootet, um das zu
verifizieren).
