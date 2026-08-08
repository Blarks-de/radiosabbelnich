# KeinSabbelRadio Android MVP — Session-Log

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

## 2026-08-08 (Fortsetzung 3) — Audio-Fingerprinting (Phase 4 aus dem Fahrplan, Plan-Mode)

Auslöser: `RadioZapper_Android_Fahrplan.md`, Phase 4 - der größte
verbleibende Brocken. Vorbild: `fingerprint.py` im Docker-Projekt, MIT der
ausdrücklichen Vorgabe, den dortigen 2D-Peak-Fix als Pflicht-Ausgangspunkt
zu übernehmen (der naive "lauteste Bins pro Frame"-Ansatz hatte dort 351
von 351 verschiedenen Sprache-Clips fälschlich als identisch erkannt, siehe
`../SESSION.md` "Fingerprint-Algorithmus überarbeitet" vom 2026-08-02).
Plan-Mode mit einer Rückfrage (den "🛑 Zapping-Fehler"-Korrekturknopf, im
Fahrplan als zweite Priorität markiert, gleich mitnehmen statt separat
nachzuziehen - Nutzerentscheidung: ja), danach Umsetzung, danach Live-Test
im Emulator gegen echte Sender.

### Architektur

- **Kein zweiter Decode-Pfad**: läuft auf dem bereits vorhandenen
  16kHz-Mono-Analysestrom aus `StreamAnalyzer.kt` (demselben, der an Vosk
  geht), nicht auf einer zusätzlichen 44.1kHz-Dekodierung wie im
  Docker-Projekt. Alle Frame-/Nachbarschafts-Konstanten wurden proportional
  für 16kHz neu hergeleitet (`FRAME_SIZE=512`/`HOP_SIZE=256` statt
  `1024`/`512`, `PEAK_NEIGHBORHOOD_FREQ=21` statt `15` usw. - volle
  Umrechnungstabelle in `android-app/README.md`), nicht 1:1 aus Python
  kopiert.
- **`fingerprint/Fingerprint.kt`**: reine Algorithmus-Logik (eigene
  Radix-2-Cooley-Tukey-FFT, 2D-lokale-Maxima-Peak-Erkennung, Hash-Bildung) -
  bewusst KEINE externe FFT-Bibliothek wie JTransforms, passt zum
  durchgehenden Minimal-Dependency-Stil (Docker-Projekt vermeidet aus
  demselben Grund `scipy`). Kennt weder Android-SQLite noch
  `PlaybackService` - direktes Pendant zu den freien Funktionen in
  `fingerprint.py`.
- **`fingerprint/FingerprintDb.kt`**: `SQLiteOpenHelper` (Android-Bordmittel)
  statt Room - direktes Pendant zu Pythons rohem `sqlite3`-Modul, dieselbe
  Begründung wie die JSON-Datei-statt-Room-Entscheidung bei der
  Senderliste, nur in die andere Richtung (hier rechtfertigt der indizierte
  Hash-Lookup echtes SQL, aber keine Room-Boilerplate für zwei simple
  Tabellen). `matchOrLearn()`/`deleteClip()`/`clearAll()` 1:1 nach
  `FingerprintDB`s Vorbild, inkl. desselben Voting-Mechanismus
  (`(clip_id, delta)`-Zählung für konsistenten Zeitversatz) und
  Chunk-Batching (500er-Chunks) gegen SQLites Parameterlimit.
- **Zwei parallele Trigger-Signale in `StreamAnalyzer`**: die bestehende
  Glättung/Hysterese (4s-Anlaufzeit) steuert weiterhin `status`
  unverändert. Für Fingerprinting zählt ein zusätzlicher ROHER,
  ungeglätteter Speech-Streak mit (`FINGERPRINT_TRIGGER_CHUNKS=4` = 2s,
  identisch zu Pythons `FINGERPRINT_TRIGGER_SECONDS=2`) - sonst wäre der
  erste Check erst nach der vollen Hysterese-Anlaufzeit dran gewesen statt
  nach den in Python gemessenen ~2s. Ergebnis kommt über ein
  `MutableSharedFlow<FingerprintOutcome>` zurück (bewusst SharedFlow statt
  StateFlow - ein Treffer ist ein einmaliges Ereignis, `StateFlow`s
  "letzter-Wert-bleibt-hängen"-Semantik hätte bei erneutem Sammeln
  denselben Treffer nochmal ausgelöst bzw. einen zweiten identischen
  Treffer maskiert).
- **`PlaybackService`** reagiert auf `Match` mit demselben
  `attemptAutoSwitch()` wie ein Sprache-Treffer (identische Semantik zu
  `do_switch("Bekannte Werbung/Jingle erkannt")`, erbt automatisch den
  bestehenden Cooldown). `Learned` aktualisiert nur die UI. Pausiert
  implizit während einer Nachrichten-Pause (Phase 6), weil `analyzer.stop()`
  dort bereits die komplette `runAnalysis()`-Coroutine (inkl. des neuen
  Fingerprint-Codes) abbricht - kein weiterer Sonderfall nötig.
- **"🛑 Zapping-Fehler"-Knopf**: `undoLastFingerprintMatch()` löscht den
  zuletzt gematchten Clip aus der DB (`Dispatchers.IO`), Knopf nur
  sichtbar/aktiv, solange ein rückgängig machbarer Match ansteht (eigene
  `lastFingerprintMatch`-StateFlow).

### Verifiziert (Emulator, echte Sender: Deutschlandfunk/SWR3)

- **Kern-Pipeline funktioniert**: erstes Deutschlandfunk-Segment fingerprinted
  nach ~2s Sprache, korrekt als neuer Clip gelernt (214 Hashes, Clip #1),
  Logcat bestätigt - deutlich VOR dem regulären, hysterese-basierten
  Auto-Switch (der ~2.5s später griff), wie geplant unabhängig/parallel.
- **Kein-Falsch-Treffer-Test (der historisch kritische Punkt dieser Phase)**:
  drei echte, unterschiedliche Sprache-Clips (Deutschlandfunk, SWR3,
  erneut Deutschlandfunk) paarweise gegeneinander geprüft - alle drei
  Vergleiche ergaben eine Match-Stärke von genau 1 (Schwelle 25), jeweils
  korrekt als neuer, eigener Clip gelernt statt fälschlich gematcht. Passt
  zur Verteilung aus dem Python-Referenztest (0-14 für unterschiedlichen
  Inhalt, 100+ für echte Wiederholungen).
- Fingerprint-Chip in der UI zeigte korrekt "Neuer Clip gelernt" nach dem
  Learned-Ereignis, "🛑 Zapping-Fehler"-Button blieb dabei korrekt
  versteckt (kein Match, also nichts rückgängig zu machen).
- `fingerprints.db` wurde korrekt unter dem App-Standardpfad
  (`databases/fingerprints.db`) angelegt und persistiert (`adb shell run-as
  ... ls databases/` bestätigt).
- Kein Absturz über die gesamte Testreihe (`adb logcat -d | grep -iE
  "FATAL|AndroidRuntime"` durchgehend leer).

### Bewusst NICHT verifiziert (ehrliche Lücke, kein übersprungener Test)

**Kein echter "Match"-Fall (Wiederholung erkannt + sofortiger Wechsel) live
beobachtet.** Dafür wäre ein Clip nötig, der GARANTIERT zweimal exakt
wiederkehrt - auf einem echten, fortlaufenden Live-Radiostream lässt sich
das nicht erzwingen (der Inhalt bewegt sich weiter), und auf diesem Host
stand weder ein TTS-Tool (`espeak-ng`/`flite` wären via `apt` verfügbar
gewesen, aber nur mit Root-Passwort installierbar, das hier nicht vorliegt)
noch ein vorhandener Sprach-Testkorpus zur Verfügung, um einen kontrolliert
wiederholten Clip zu erzeugen. Der Matching-/Voting-Mechanismus selbst ist
aber derselbe wie im Python-Original (dort mit echten Wiederholungen
gegengetestet: 651-702 von 722 Treffern) und wurde hier nur auf der
Kotlin-Seite (Hash-Generierung, DB-Lookup) neu geschrieben - das
Vertrauen in die Korrektheit stützt sich auf den bestandenen
Negativ-Test (keine Falsch-Treffer) plus Code-Review gegen das Vorbild,
nicht auf einen bestätigten Positiv-Fall. Nachholbedarf, sobald ein
geeigneter wiederkehrender Clip (echter Jingle im Live-Betrieb oder ein
per TTS erzeugter Testclip) verfügbar ist.

Ebenfalls nicht getestet: der "🛑 Zapping-Fehler"-Knopf selbst (setzt einen
Match voraus, der in dieser Sitzung nicht auftrat) und das Zusammenspiel
mit einer laufenden Nachrichten-Pause (strukturell durch `analyzer.stop()`
abgedeckt, siehe oben, aber nicht eigens mit einem echten Fingerprint-Treffer
während einer Pause gegengeprüft).

## 2026-08-08 (Fortsetzung 4) — Mehrsprachiges STT, Schritt 1: Grundgerüst (Phase 7 aus dem Fahrplan, Plan-Mode)

Auslöser: "Weiter mit Phase 7" - laut Fahrplan der größte verbleibende
Brocken, deshalb wie im Docker-Projekt (siehe dessen SESSION.md,
"Mehrsprachige STT-Erkennung (Teil 1a)"/"Geführter STT-Kalibrierungs-Wizard
(Teil 1b)") explizit in zwei Schritte gesplittet: dieser Durchgang deckt
nur das Grundgerüst ab (Mehrsprachigkeit, Modell-Verwaltung, Kategorie-
Zuordnung) - der Kalibrierungs-Wizard (Schritt 2) folgt erst nach
Rückmeldung, dass Schritt 1 live läuft. Plan vorab per Plan-Mode
festgelegt und vom Nutzer bestätigt.

Zwei echte Bugs aus dem ersten Produktiveinsatz der Docker-Mehrsprachigkeit
(dessen SESSION.md, "Fortsetzung 7") wurden hier gezielt vermieden - siehe
Architektur-Abschnitt unten für die konkrete Gegenmaßnahme.

### Architektur

- **Kategorie → Sprache, nicht Sender → Sprache** (identische
  Kernentscheidung wie im Docker-Projekt): neues `stt/SttSettings.kt` hält
  `categoryLanguages: Map<String, String>`, `Categories.kt` bleibt
  unangetastet. Sprachliste als Freitext (Code + Vosk-Modell-Download-URL),
  keine feste Auswahl - dieselbe Design-Entscheidung wie dort, nur mit
  Download-URL statt Host-Modellpfad (Android lädt selbst herunter, der
  Docker-Container ist auf einen Host-Mount angewiesen).
- **`VoskModelManager.kt`: Instanz-Klasse → Singleton (`object`)**, ein
  `StateFlow<ModelState>` pro Sprachcode statt einer einzelnen Variable -
  mehrere Activities (MainActivity, die neue SttSettingsActivity) müssen
  sich denselben Download-Fortschritt teilen. Modell-Ordnername wird aus
  dem Download-URL-Dateinamen abgeleitet statt sprachcode-basiert vergeben
  - für Deutsch mit der unveränderten Default-URL ergibt sich dadurch
  exakt derselbe Pfad wie vor dieser Änderung
  (`vosk-model-small-de-0.15`), bestehende Installationen mit bereits
  heruntergeladenem Modell brauchen **keinen Migrationsschritt** (live
  bestätigt, siehe Verifikation unten).
- **`vosk/VoskModelCache.kt` (neu)**: LRU-Cache geladener `org.vosk.Model`-
  Objekte pro Sprachcode (`LinkedHashMap` mit `accessOrder=true` +
  `removeEldestEntry()`), `MAX_LOADED_VOSK_LANGUAGES=2` - direktes Pendant
  zu `SttFilter._get_vosk_engine()` im Docker-Projekt. Instanzgebunden
  (gehört `PlaybackService`, analog `FingerprintDb`), cached sowohl Erfolg
  als auch Fehlschlag, damit ein kaputter Modellpfad nicht bei jedem
  Sample erneut das Dateisystem anfasst.
- **Gegenmaßnahme gegen Docker-Bug 1 (Signatur-Änderungs-Absturz durch
  vergessene Aufrufstelle)**: `modelPath`/`activeModelPath`-Parameter-
  Threading durch `PlaybackService.manualPlay()`/`play()` und vier interne
  Aufrufstellen komplett ENTFERNT statt nur sorgfältig durchgereicht.
  `play(station)` löst Sprache/Modellpfad jetzt INTERN bei JEDEM Aufruf
  frisch über `SttSettings.resolveLanguage(station.category)` auf -
  strukturell unmöglich, dass eine veraltete Kopie an einer vergessenen
  Stelle landet. Zusätzlich abgesichert durch `grep -n "\.play(\|
  manualPlay("` über `PlaybackService.kt`/`MainActivity.kt` nach der
  Umsetzung (alle Fundstellen einzeln gegen die neue, parameterlose
  Signatur geprüft - vollständig, keine vergessene Stelle).
- **`analysis/StreamAnalyzer.kt`**: `start()`/`runAnalysis()` bekommen
  `language`/`voskModelCache`/`ratioToConfirmSpeech`/`ratioToConfirmMusic`
  als Parameter statt der bisherigen globalen Top-Level-Konstanten
  `RATIO_TO_CONFIRM_SPEECH`/`RATIO_TO_CONFIRM_MUSIC` (deren bisherige Werte
  0.65/0.30 wandern als Defaults nach `LanguageConfig`). Modell kommt jetzt
  über `voskModelCache.get(modelPath, language)` statt direkter
  `Model(modelPath)`-Konstruktion - Ownership liegt beim Cache,
  `StreamAnalyzer` darf das Modell deshalb NICHT mehr selbst `close()`n
  (würde sonst ein von einem anderen Sender/einer anderen Sprache noch
  gecachtes Modell zerstören).
- **`stt/SttSettingsActivity.kt` (neu)**: eigener Bildschirm (analog
  `StationManagementActivity`), zwei Abschnitte - "🌐 STT-Sprachen"
  (Liste, Download-Status pro Sprache, Hinzufügen/Löschen, Löschsperre für
  die letzte verbleibende Sprache) und "🏷 Kategorie-Sprachen" (ein
  Spinner pro fester Kategorie). `MainActivity` verliert die alte
  Einzelmodell-UI (fest "DE") komplett, bekommt stattdessen einen Button
  zur neuen Activity plus eine nur bei Bedarf sichtbare Statuszeile
  (`sttModelMissing`-StateFlow: Sprache, für die kein Modell vorliegt -
  Analyse pausiert dann, kein automatischer Wechsel).
- `LanguageConfig` hält `ratioToConfirmSpeech`/`ratioToConfirmMusic` bereits
  PRO Sprache (Speicherstruktur für Schritt 2), auch wenn deren Bearbeitung
  in diesem Schritt noch nicht über die UI möglich ist - Nutzerentscheidung
  aus der Plan-Rückfrage: der künftige Kalibrierungs-Wizard soll genau
  diese beiden, bereits vorhandenen Verhältnis-Schwellen kalibrieren
  (Android hat kein Docker-artiges "VAD + STT-Konfidenz-Schwelle"-Duo,
  Vosk-Texterkennung ist hier bereits der einzige Detektor).

### Verifiziert (Emulator, API 34 x86_64)

- **Rückwärtskompatibilität**: bestehende Installation mit bereits
  heruntergeladenem deutschen Modell (aus früheren Phasen) - `de` wurde
  ohne erneuten Download über den identischen, URL-abgeleiteten Pfad
  gefunden (`VoskModelCache: Vosk-Modell fuer Sprache 'de' geladen
  (.../vosk-model-small-de-0.15)`), Deutschlandfunk → SWR3-Auto-Switch
  funktionierte wie vor dieser Änderung.
- **Neue Sprache**: Englisch (`vosk-model-small-en-us-0.15`, dieselbe
  Quelle wie im Docker-Projekt) über die neue "+ Neue Sprache"-UI
  hinzugefügt, Download über die App abgeschlossen ("Modell bereit."),
  Kategorie "International" auf "en" gesetzt (persistiert in
  `stt_settings_prefs.xml`, überlebt Activity-Neustart), Testsender in
  dieser Kategorie angelegt und abgespielt - Logcat bestätigt
  `VoskModelCache: Vosk-Modell fuer Sprache 'en' geladen`, kein Absturz.
- **Löschsperre**: Lösch-Button für die letzte verbleibende Sprache korrekt
  `enabled="false"` (UI-Dump geprüft), sobald eine zweite Sprache existiert
  wieder aktiv.
- **Fehlender-Modell-Fall**: Kategorie auf eine Sprache ohne heruntergeladenes
  Modell gesetzt, Sender dieser Kategorie abgespielt - kein Absturz,
  `⚠ Kein Modell für „en" – Analyse pausiert.`-Hinweis erschien korrekt auf
  der Startseite, kein automatischer Wechsel ausgelöst (Passthrough wie bei
  jedem fehlenden Modell schon bisher).
- `adb logcat -d --pid=<App-PID> | grep -iE "FATAL|Exception|Error"` über
  die gesamte Testreihe ohne app-eigene Fatals (die beiden aufgetretenen
  `NuCachedSource2`-Zeilen sind normale, transiente ExoPlayer-Netzwerk-
  Warnungen, kein Absturz - Prozess blieb durchgehend am Leben).
- `./gradlew assembleDebug` sauber, keine Compiler-Warnings mehr (der
  einzige verbliebene, `Parameter 'code' is never used" in
  `VoskModelManager.modelPathOrNull()`, per `@Suppress` explizit als
  bewusste API-Symmetrie markiert statt stillschweigend ignoriert).

### Bewusst NICHT gemacht (Nachholbedarf, kein übersprungener Test)

- **Kein Live-Test der LRU-Verdrängung mit einer dritten Sprache** - der
  Cache wurde nur mit zwei gleichzeitig geladenen Sprachen (de/en, exakt
  `MAX_LOADED_VOSK_LANGUAGES`) geprüft, nicht mit einer dritten, die das
  älteste Modell tatsächlich verdrängt. Die Verdrängungslogik selbst ist
  Standard-`LinkedHashMap`-Verhalten (`accessOrder=true` +
  `removeEldestEntry()`), aber noch nicht am eigenen Cache live beobachtet.
- **Kalibrierungs-Wizard (Schritt 2) nicht Teil dieses Durchgangs** - wie
  im Fahrplan gefordert und im Docker-Projekt vorgemacht: erst nach
  Rückmeldung, dass dieses Grundgerüst im echten Betrieb läuft.
- Kein Test mit tatsächlich englischsprachigem Audio (der Test-Sender
  spielte aus Zeitgründen einen deutschsprachigen Stream ab, nur um den
  Lade-/Cache-Pfad für eine zweite Sprache ohne Absturz zu bestätigen) -
  ob die STT-Erkennungsqualität für Englisch in der Praxis stimmt, ist
  damit noch nicht geprüft.

## 2026-08-08 (Fortsetzung 5) — Mehrsprachiges STT, Schritt 2: Kalibrierungs-Wizard (Phase 7 aus dem Fahrplan)

Auslöser: "Läuft, weiter zu Schritt 2" - Rückmeldung des Nutzers, dass das
in Fortsetzung 4 gebaute Grundgerüst produktiv lief, damit war die im
Fahrplan geforderte Voraussetzung für Schritt 2 erfüllt. Direkt umgesetzt
(kein erneuter Plan-Mode-Durchlauf, da die Architektur bereits in
Fortsetzung 4 als Nutzerentscheidung festgelegt wurde: der Wizard
kalibriert die bestehenden `ratioToConfirmSpeech`/`ratioToConfirmMusic`-
Schwellen anhand von `StreamAnalyzer.speechRatio`, NICHT einen neu
erfundenen `confidence_threshold` wie im Docker-Projekt - Android hat kein
VAD+STT-Konfidenz-Duo, Vosk-Texterkennung ist hier bereits der einzige
Detektor).

### Architektur

- **`stt/Calibration.kt` (neu)**: reine Domänenlogik (kennt weder
  `PlaybackService` noch `StreamAnalyzer`, analog `NewsBreak.kt`/
  `Fingerprint.kt`) - `CalibrationLevel`-Enum, `CalibrationSuggestion`-
  Datenklasse, `suggestRatios(speechSamples, musicSamples)`. Pendant zu
  `stt_filter.suggest_confidence_threshold()` im Docker-Projekt
  (`_THRESHOLD_MARGIN_RATIO=0.7`, hier `MARGIN_RATIO`, identischer Wert),
  aber auf ZWEI Schwellen statt einer angewandt: `musicMax`/`speechMin`
  trennen die Verteilungen, die Lücke dazwischen wird im Verhältnis
  `MARGIN_RATIO` aufgeteilt - `ratioToConfirmMusic` näher an `musicMax`,
  `ratioToConfirmSpeech` näher an `speechMin`. Überlappen sich die
  Verteilungen (`musicMax >= speechMin`), liefert die Funktion
  `overlapping=true` statt eines potenziell falschen Vorschlags - live
  bestätigt (siehe Verifikation unten).
- **`PlaybackService` haelt die Session direkt als Felder** (kein eigenes
  State-Objekt/Singleton) - dieselbe Entscheidung wie bei Fingerprint/
  News-Break: die Session ist service-lebensdauer-gebunden, hängt am
  gerade laufenden `analyzer`. `refreshAnalyzer(station)` wurde aus
  `play()` herausgezogen (reiner Refactor, identisches Verhalten) - genau
  dieser Baustein wird jetzt auch von `startCalibration()`/
  `stopCalibration()`/`applyCalibrationSuggestion()` wiederverwendet, um
  die STT-Analyse fuer den GERADE laufenden Sender neu aufzusetzen, OHNE
  den ExoPlayer anzufassen (kein Kaltstart/keine Hoerbarkeitsluecke nur
  wegen eines Sprachwechsels bei der Analyse).
- **`calibrationLanguage` schlägt `SttSettings.resolveLanguage()`**: ist
  eine Session aktiv, erzwingt `refreshAnalyzer()` diese Sprache fuer
  jeden Sender, zu dem gewechselt wird (auch waehrend der Kalibrierung
  bleibt Sender-Wechsel ueber die Startseite normal moeglich - der Wizard
  schaltet selbst NIEMALS um, exakt wie im Docker-Projekt). Automatisches
  Umschalten (Sprache-Treffer in `handleStatusForAutoSwitch()`,
  Fingerprint-Match in `handleFingerprintOutcome()`) ist waehrend einer
  aktiven Session bewusst ausgeschaltet - ein durch die erzwungene Sprache
  verfaelschtes Ergebnis darf keinen automatischen Wechsel ausloesen.
- **Sampling laeuft ueber denselben `analyzer.speechRatio`-Kollektor, der
  auch das "Bullshitometer" speist** (`handleCalibrationSample()`, neu in
  `onCreate()` angehaengt) - kein zweiter Analyse-Pfad. No-Op, solange
  keine Session aktiv ODER kein Level markiert ist, dieselbe
  Absicherung wie bei `combine_label()`s "kein Befund"-Fall im Docker-
  Projekt.
- **`stt/CalibrationActivity.kt` (neu)**: bindet sich wie `MainActivity`
  an `PlaybackService`, zeigt den laufenden Sender, den Live-Rohwert
  (dieselbe Balken-Darstellung wie das Bullshitometer), zwei Toggle-
  Buttons "🗣 Das ist Sprache"/"🎵 Das ist Musik" (erneutes Antippen pausiert
  das Sammeln), Sample-Zaehler und einen live neu berechneten Vorschlag
  (kein Zwischenspeichern - wird bei jeder Aenderung der Sample-Zaehler
  frisch aus `calibrationSuggestion()` gezogen, analog zum Docker-Wizard,
  der den Vorschlag bei jedem Status-Poll neu berechnet statt eine zweite
  JS-Implementierung zu pflegen). "Übernehmen" speichert die aktuellen
  Werte in `SttSettings` und wendet sie sofort an (`refreshAnalyzer()`
  erneut), ohne die Session zu beenden - weiter sammeln/erneut uebernehmen
  bleibt möglich. Deaktiviert, solange `overlapping=true` oder eine der
  beiden Listen leer ist.
- **`android:screenOrientation="portrait"` fuer `CalibrationActivity`
  bewusst gesetzt** (siehe deren Klassen-Doc): eine Rotation wuerde die
  Activity sonst zerstoeren und neu erstellen, `onDestroy()` beendet aber
  die Session (`stopCalibration()`) - ohne die Sperre wuerde eine simple
  Bildschirmdrehung mitten in der Kalibrierung bereits gesammelte Samples
  verwerfen. Zusaetzlich schuetzt `activeCalibrationLanguage != language`
  in `onServiceConnected()` davor, dass ein erneutes Binden (z.B. nach
  `onStop()`/`onStart()` bei kurzem Backgrounding) `startCalibration()`
  ein zweites Mal fuer dieselbe Sprache aufruft - das wuerde die
  Sample-Listen sonst explizit leeren.
- **`SttSettingsActivity`**: neuer "Kalibrieren"-Button pro Sprachzeile,
  nur sichtbar, wenn das Modell dieser Sprache `ModelState.Ready` ist
  (ohne geladenes Modell gaebe es ohnehin nichts zu sampeln - `play()`
  wuerde nur `sttModelMissing` setzen).

### Verifiziert (Emulator, API 34 x86_64)

- Kalibrieren-Button erscheint korrekt nur bei bereits heruntergeladenem
  Modell (fuer "de"/"en" sichtbar, fuer eine Sprache ohne Modell waere er
  `GONE` geblieben - aus dem Code ersichtlich, mangels dritter
  undownloaded Sprache in dieser Sitzung nicht separat nachgestellt).
- **Session-Start**: Kalibrieren fuer "de" geoeffnet, waehrend "Test-EN"
  lief (Kategorie "International" -> normalerweise "en") - `refreshAnalyzer()`
  erzwang korrekt "de" fuer den laufenden Sender, kein Kaltstart/keine
  Wiedergabeluecke.
- **Sampling**: "🗣 Das ist Sprache" markiert, nach einigen Sekunden
  Anlaufzeit (siehe unten) stieg der Sample-Zaehler zuverlaessig
  (`Sprache-Samples: 29`), Live-Rohwert-Balken aktualisierte sich
  synchron. Erneutes Antippen desselben Levels pausiert das Sammeln
  korrekt ("Markiert als: – (kein Sammeln)"), bestaetigt per UI-Dump.
- **Ueberlappungs-Warnung**: mit "🎵 Das ist Musik" auf demselben (real
  gemischten) Sender zusaetzlich Samples gesammelt - `suggestRatios()`
  erkannte die ueberlappenden Verteilungen korrekt
  (`Verteilungen überlappen sich noch...`), "Übernehmen"-Button dabei
  `enabled="false"` (UI-Dump bestaetigt) statt einen fragwuerdigen
  Vorschlag anwendbar zu machen.
- **Automatisches Umschalten blieb waehrend der gesamten Session aus** -
  kein einziger "Sprache erkannt... schalte weiter"-Log-Eintrag zwischen
  Session-Start und "Fertig", obwohl der Rohwert zeitweise deutlich ueber
  0.5 lag (waere ohne den Guard ein Auto-Switch-Kandidat gewesen).
- **Session-Ende**: "Fertig" beendete die Activity sauber, kein Absturz,
  `stopCalibration()` lief durch (Log-Analyse bestaetigt Rueckkehr in den
  Vordergrund-Bildschirm ohne Fehlermeldung).
- `adb logcat -d --pid=<App-PID> | grep -iE "FATAL|Exception"` (gefiltert
  um bekannte, harmlose ExoPlayer-Netzwerk-Fehler eines separat toten
  Test-Senders) ueber die gesamte Testreihe leer, Prozess durchgehend am
  Leben.
- `./gradlew assembleDebug` sauber, keine neuen Compiler-Warnings.

### Bewusst NICHT gemacht / offene Punkte

- **Kein Test mit zwei ECHT unterschiedlich klassifizierten Quellen**
  (Sprache-Sender vs. Instrumental-Sender) - aus Zeitgruenden wurde
  derselbe (gemischte) Test-Sender fuer beide Level markiert, weshalb die
  Ueberlappungs-Warnung erwartungsgemaess griff. Das bestaetigt den
  Warnpfad, aber NICHT den Erfolgspfad (einen tatsaechlich sauberen,
  anwendbaren Vorschlag mit `overlapping=false`) - dafuer waere ein Sender
  mit reinem Wortbeitrag UND ein zweiter mit reiner Instrumentalmusik
  noetig, beide in der zu kalibrierenden Sprache passend.
- **"Übernehmen" wurde nicht tatsaechlich angewendet** (deaktiviert wegen
  der Ueberlappung oben) - dass der gespeicherte Wert danach wirklich in
  `SttSettings` landet und `refreshAnalyzer()` ihn sofort uebernimmt, ist
  nur durch Code-Review abgesichert, nicht durch einen Live-Durchlauf.
- **Portrait-Sperre verhindert das Rotationsproblem nur fuer diesen einen
  Bildschirm** - nicht eigens gegengetestet (Emulator lief durchgehend im
  Hochformat), bleibt bei einer echten Drehung auf einem physischen Geraet
  zu bestaetigen.

## 2026-08-08 (Fortsetzung 6) — Review-Befunde: kompletter Durchgang durch android-app/ (Phase 8 aus dem Fahrplan)

Auslöser: `RadioZapper_Android_Fahrplan.md`, Phase 8 - nach den sieben
Feature-Phasen einmal komplett durch den Code, analog zum Review des
Docker-Projekts vom 2026-08-03, das dort den Watchdog-Bug aufdeckte.
Auftrag laut Fahrplan explizit: **nichts sofort ändern**, erst einen
Befund-Bericht liefern und gemeinsam priorisieren. Gelesen wurden alle 24
Kotlin-Dateien, Manifest, Gradle-Config und Ressourcen, plus Quervergleich
mit den Python-Vorbildern (`news_break.py`, `station_import.py`,
`fingerprint.py`) an allen Stellen, an denen README/CLAUDE.md Parität
behaupten. Dieser Eintrag hält den Bericht fest; die Umsetzung steht im
nächsten Eintrag.

### P1 - echte Fehlfunktionen

1. **`fingerprintBuffer` wächst unbegrenzt während eines Sprache-Streaks**
   (`analysis/StreamAnalyzer.kt`): nach dem einmaligen Fingerprint-Check
   (`fingerprintCheckedThisRun = true`) werden weiter 8.000 Samples pro
   Sprache-Chunk angehängt, ohne je wieder gelesen zu werden - geleert wird
   nur bei einem Nicht-Sprache-Chunk. Dazu `mutableListOf<Short>`, also ein
   geboxtes Objekt pro Sample (~16.000/s). Bei durchgehender Sprache
   (Nachrichtenblock, Wortbeitrag - besonders während einer Kalibrierung,
   wo Auto-Switch bewusst aus ist) nach wenigen Minuten dreistellige MB,
   OOM-Kandidat.
2. **Analyse-Fehler werden als "Sender ist tot" behandelt**
   (`StreamAnalyzer`s `catch` -> `PlaybackStatus.ERROR` ->
   `PlaybackService.handleStatusForAutoSwitch()` -> `handlePlaybackFailure()`):
   auch "Vosk-Modell nicht ladbar", "kein Audio-Track" und "Codec nicht
   unterstützt" landen so in der 5-Minuten-Tot-Sperre. Bei einem kaputten
   Modell trifft das JEDEN Sender der betroffenen Kategorie in Sekunden ->
   alle gesperrt -> `findNextOrEscalate()` leert beide Maps -> Endlos-
   Schnellrotation ohne je stabile Wiedergabe. Die "praktisch kostenlose
   zweite Bestätigung" aus Phase 2 ist nur korrekt, wenn der Fehler
   tatsächlich von der Quelle kommt.
3. **`VoskModelCache` schließt Modelle, die ein laufender `Recognizer` noch
   benutzt**: `Model.close()` gibt nativen Speicher frei, der `Recognizer`
   im Analyzer hält einen Zeiger darauf. Zwei Fenster - LRU-Verdrängung (ab
   der 3. genutzten Sprache, genau der Pfad, der laut Fortsetzung 4 noch nie
   live lief) und `clear()` in `PlaybackService.onDestroy()`, wo
   `analyzer.stop()` nur kooperativ abbricht und die Schleife noch in
   `acceptWaveForm()` stehen kann. Ergebnis wäre ein nativer Absturz ohne
   Java-Stacktrace.
4. **Die Kalibrierung verliert systematisch Samples, ein einzelner Ausreißer
   kippt den Vorschlag**: gesammelt wird über `analyzer.speechRatio`, einen
   `StateFlow` - identische Folgewerte werden verschluckt. Der Rohwert kennt
   nur 9 diskrete Stufen (0/8...8/8); bei stabiler Sprache steht er auf 1.0
   und es kommt KEIN weiteres Sample mehr an, gezählt werden vor allem
   Übergangswerte. `suggestRatios()` nutzt dann `min(speech)`/`max(music)`,
   also genau die Extreme - ein einziges Übergangssample reicht für
   `overlapping=true`. Sehr plausible Erklärung dafür, dass im Live-Test aus
   Fortsetzung 5 nur der Warnpfad und nie der Erfolgspfad zu sehen war.

### P2 - Robustheit und Ressourcen

5. **Blockierende Decoder-Aufrufe sind nicht abbrechbar**: `job.cancel()`
   bzw. `withTimeoutOrNull` beenden nur die Coroutine,
   `MediaExtractor.setDataSource()` läuft bis zum OS-Timeout weiter. Folgen:
   alte und neue Analyse dekodieren kurzzeitig parallel; im
   Erreichbarkeits-Check gibt `withPermit` das Semaphor frei, obwohl der
   Thread noch hängt (faktisch mehr als `CHECK_CONCURRENCY=3` offene
   Verbindungen); und wirft der ALTE Job im Abwind noch eine Exception,
   sperrt er über Befund 2 den bereits laufenden NEUEN Sender - dieselbe
   Klasse wie die dokumentierte `onPlayerError()`-Race, aber bisher nirgends
   erwähnt.
6. **Main-Thread-I/O**: `StationRepository.init()` parst `stations.json`
   synchron in `Application.onCreate()` (mit den real importierten 361
   Sendern spürbar), `NewsBreakSettings.listMp3s()` macht eine
   SAF-ContentResolver-Abfrage aus dem Main-Dispatcher-Ticker heraus
   (ANR-Risiko bei großem/langsamem Ordner).
7. **Kein Wakelock, obwohl deklariert**: `WAKE_LOCK` steht im Manifest, wird
   aber nirgends genutzt, ExoPlayer bekommt kein `setWakeMode()`. Bei
   ausgeschaltetem Display kann die CPU schlafen - im dauerwachen Emulator
   nie beobachtbar gewesen.
8. **Verwaltungs-Activity skaliert nicht mit dem eigenen Import**: jede
   Änderung baut alle Zeilen synchron neu auf, bei 361 Sendern hunderte
   `inflate()` im Main-Thread pro Checkbox-Klick.

### P3 - Zustands- und Konsistenzfehler

9. Eine **Kalibrierungs-Session läuft weiter, wenn man den Bildschirm nur
   verlässt** (Home/Task-Switch statt "Fertig") - erst `onDestroy()` ruft
   `stopCalibration()`. Auto-Switch bleibt aus, die Sprache bleibt
   erzwungen, ohne jeden Hinweis auf der Startseite.
10. **Sprachen sind nur anlegbar/löschbar, nicht editierbar**: erneutes
    Anlegen desselben Codes (die einzige Möglichkeit, die Modell-URL zu
    ändern) überschreibt stillschweigend die kalibrierten Schwellen mit den
    Defaults. Zusätzlich cached `VoskModelManager.state()` den StateFlow pro
    Code inkl. des aus der ALTEN URL abgeleiteten Pfads.
11. **Kein Cache-Invalidieren bei Konfigurationsänderung**: Modell gelöscht/
    neu heruntergeladen lässt das alte `Model` im `VoskModelCache` des
    laufenden Service (das Docker-Vorbild leert den Cache genau dafür beim
    Reload); ein einmal gecachter Fehlschlag wird nie erneut versucht.
12. **`resolveLanguage()` fällt hart auf "de" zurück** - wird "de" gelöscht,
    steht für jede nicht explizit gesetzte Kategorie dauerhaft "kein
    Modell", obwohl Sprachen konfiguriert sind.
13. **`StationRepository.readFromDisk()` überschreibt eine kaputte Datei
    endgültig**: Fallback auf den 3-Sender-Startbestand, der nächste
    Schreibvorgang macht den Verlust permanent. Kein Backup, keine
    Unterscheidung "Datei kaputt" vs. "Datei fehlt".
14. **`UpdateManager.downloadUpdate()` prüft keinen Response-Code** (eine
    404-HTML-Seite landet als `radiozapper.apk` im Cache);
    **`VoskModelManager.unzip()` hat keinen Zip-Slip-Schutz** bei frei
    eintragbarer Download-URL.
15. **`StationReachabilityChecker.checkCategory()` ohne Reentrancy-Schutz**
    (zwei parallele Läufe teilen sich `_unreachableIds`);
    `StationImporter._state` bleibt als Singleton auf `Done` stehen und
    zeigt beim nächsten Öffnen ein altes Ergebnis.
16. **Toter Code + fehlende Grenze**: `FingerprintDb.clearAll()` wird
    nirgends aufgerufen - es gibt keine Möglichkeit, die DB zu leeren,
    während `matchOrLearn()` jeden ungematchten 2s-Sprachclip mit hunderten
    Hash-Zeilen lernt (unbegrenztes Wachstum ohne Pruning; im Docker-Projekt
    als offener Punkt dokumentiert, hier gar nicht).
17. **Kein HLS/DASH**: nur `media3-exoplayer`/`media3-common`, kein
    `media3-exoplayer-hls`/`-dash`. `.m3u8`-Sender aus der Kodinerds-Liste
    können prinzipiell nicht laufen und erscheinen als "⚠ Antwortet nicht"
    bzw. "nicht erreichbar", ohne erkennbare Ursache (der Analysepfad kann
    es ebenso wenig, `MediaExtractor` parst keine m3u8).

### P4 - Doku vs. tatsächlicher Stand

18. README "Bekannte Grenzen": "Vosk-`Model` wird bei jedem Play-/
    Auto-Switch-Klick neu geladen ... ca. 1-2 Sekunden" - seit Phase 7
    (`VoskModelCache`) überholt.
19. README-Bullet "Geglätteter Status" nennt `RATIO_TO_CONFIRM_*` weiter als
    Konstanten in `StreamAnalyzer.kt`; seit Phase 7 sind es Pro-Sprache-
    Werte aus `LanguageConfig`. Der spätere Phase-7-Abschnitt widerspricht
    dem direkt.
20. README-Kopf verweist für den Verlauf auf `../SESSION.md`, obwohl es seit
    2026-08-07 diese Datei hier gibt.
21. **`android-app/CLAUDE.md` beschreibt architektonisch nur den Stand bis
    Phase 2**: Vorwärmung, M3U-Import/Reachability-Check, Fingerprinting,
    News-Break, Mehrsprachigkeit inkl. `VoskModelCache` und der
    Kalibrierungs-Wizard fehlen komplett, die dort genannten Hysterese-
    Konstanten sind ebenfalls überholt. Größter Doku-Rückstand.
22. Nicht dokumentierte Grenzen: unbegrenzt wachsende Fingerprint-DB
    (Befund 16), fehlendes HLS/DASH (Befund 17).

### Geprüft und in Ordnung

Watchdog-Eskalation inkl. Doppelsperr-Priorisierung; Übernahme des
vorgewärmten Players inkl. `armBufferingWatchdog()`-Randfall; News-Break-
Slot-Logik und `pickRandom()` semantisch identisch zu `news_break.py`
(inkl. ±Fenster um :00/:30); M3U-Parsing 1:1 wie
`station_import.parse_m3u`; atomarer Schreibvorgang und ID-Stabilität im
Repository; FFT/2D-Peak-Erkennung und das (clip_id, delta)-Voting
entsprechen `fingerprint.py`; alle Format-Strings passen zu ihren
Argumenten; außer `clearAll()` kein toter öffentlicher Code.

### Vereinbarte Reihenfolge

Nutzerentscheidung nach dem Bericht: **2 → 1 → 4 → 3**, danach die
Doku-Gruppe P4 in einem Rutsch, dann P2/P3 nach Bedarf. Umsetzung im
nächsten Eintrag.

## 2026-08-08 (Fortsetzung 7) — Umsetzung der Review-Befunde aus Phase 8

Auslöser: Freigabe des Berichts aus dem vorigen Eintrag mit der dort
vorgeschlagenen Reihenfolge (**2 → 1 → 4 → 3**, danach P4-Doku, dann P2/P3
nach Bedarf). Umgesetzt wurden alle vier P1-Befunde, die komplette
P4-Doku-Gruppe und die P2/P3-Punkte, die klein und eindeutig richtig waren -
was bewusst offen blieb, steht unten.

### P1: Analyse-Fehler sind keine Sender-Fehler mehr (Befund 2)

`StreamAnalyzer` hat jetzt ein eigenes `analyzerError: StateFlow<String?>`
(Klartext-Ursache). `PlaybackStatus.ERROR` löst in
`handleStatusForAutoSwitch()` NICHTS mehr aus - die Tot-Sperre gehört
ausschließlich dem ExoPlayer (Fehler-Callback/Buffering-Timeout).
Stattdessen `handleAnalyzerError()`: bis zu `ANALYZER_MAX_RETRIES=3`
Neustarts der Analyse im Abstand von `ANALYZER_RETRY_SECONDS=15` (ein
abgerissener Analyse-Stream fängt sich damit von selbst wieder), danach
läuft der Sender bewusst ohne Erkennung weiter und die Startseite zeigt
"⚠ Analyse gestoppt: … (Wiedergabe läuft weiter)". Zähler wird in `play()`
pro Sender zurückgesetzt.

Zusätzlich meldet der Analyzer jetzt auch ein reguläres Stream-Ende als
Fehler: vorher lief die Analyse nach einem Verbindungsabbruch
stillschweigend nie wieder an (die Schleife endete einfach), jetzt greift
derselbe begrenzte Neustart.

### P1: Fingerprint-Puffer und Boxing im Audio-Pfad (Befund 1)

`fingerprintBuffer` ist ein `ShortArray` fester Größe
(`CHUNK_SAMPLES * FINGERPRINT_TRIGGER_CHUNKS`, also genau die 2s, die der
Check braucht) und wird nach dem einmaligen Check pro Streak NICHT mehr
weitergefüllt. Gleiche Gelegenheit, gleiche Ursache: der Rest-Puffer
zwischen zwei Häppchen war eine `ArrayDeque<Short>` - beides zusammen
boxte dauerhaft ~16.000 `Short`-Objekte pro Sekunde. Jetzt `ShortArray` +
`copyOfRange`, unverändertes Verhalten, ohne Dauer-Allokation.

### P1: Kalibrierung sammelt wieder vollständig (Befund 4)

Neuer `speechRatioSamples: SharedFlow<Double>` (ein Ereignis pro
0.5s-Häppchen) neben dem bestehenden `speechRatio`-StateFlow: die UI
(Bullshitometer) nutzt weiter den StateFlow, die Sample-Sammlung den neuen
Flow. `suggestRatios()` arbeitet außerdem mit dem 90.-/10.-Perzentil statt
`max()`/`min()` und verlangt `MIN_SAMPLES_PER_LEVEL=20` Samples pro Seite -
vorher genügte ein einziger Übergangswert, um jeden Vorschlag als
"überlappend" zu verwerfen.

### P1: `VoskModelCache` schließt keine benutzten Modelle mehr (Befund 3)

`acquire()`/`release()` mit Belegzähler statt `get()`: `StreamAnalyzer`
hält das Modell für die Dauer seines Laufs und gibt es im `finally` NACH
`recognizer.close()` frei. Verdrängt/geschlossen wird nur, was niemand
benutzt; noch belegte Einträge werden vorgemerkt (`pendingClose`) und beim
`release()` geschlossen - das gilt auch für `clear()` aus
`PlaybackService.onDestroy()`, wo ein Analyse-Lauf noch auslaufen kann.
Cache-Schlüssel ist jetzt Sprachcode UND Modellpfad, und gecachte
Fehlschläge laufen nach `FAILURE_RETRY_MS=60_000` ab (vorher blieb eine
Sprache für die gesamte Service-Lebensdauer kaputt, auch nach erneutem
Download - Befund 11).

### P2/P3 mit erledigt

- **Befund 5 (Teil)**: `StreamAnalyzer` hat eine laufende Nummer
  (`generation`); ein abgelöster Lauf kann keine Status-/Fehler-/
  Fingerprint-Werte mehr veröffentlichen. Die blockierenden Aufrufe selbst
  bleiben unabbrechbar (siehe "bewusst NICHT").
- **Befund 6 (Teil)**: `NewsBreakSettings.listMp3s()` läuft über
  `Dispatchers.IO` statt im Main-Dispatcher-Ticker.
- **Befund 7**: `setWakeMode(C.WAKE_MODE_NETWORK)` für beide ExoPlayer -
  nutzt die längst deklarierte, bis dahin ungenutzte `WAKE_LOCK`-Berechtigung.
- **Befund 9**: `calibrationLanguage` ist ein StateFlow, die Startseite
  zeigt "🎚 Kalibrierung für „…" läuft – automatisches Zappen ist solange
  aus."
- **Befund 10**: erneutes Anlegen eines vorhandenen Sprachcodes (die einzige
  Möglichkeit, die Modell-URL zu ändern) behält die kalibrierten Schwellen;
  `VoskModelManager`-States sind nach Code+URL geschlüsselt.
- **Befund 12**: `resolveLanguage()` fällt auf die erste konfigurierte
  Sprache zurück, wenn "de" gelöscht wurde.
- **Befund 13**: eine unlesbare `stations.json` wird als
  `stations.json.corrupt` gesichert, bevor der Startbestand sie überschreibt.
- **Befund 14**: Response-Code-Prüfung beim APK-Download, Zip-Slip-Schutz
  beim Modell-Entpacken.
- **Befund 15**: Reentrancy-Schutz im Erreichbarkeits-Check.
- **Befund 16**: `FingerprintDb` begrenzt sich selbst (`MAX_CLIPS=500`,
  Verdrängung der nach `last_seen` ältesten in Batches) und `clearAll()` ist
  kein toter Code mehr - Knopf "🗑 Fingerprint-DB leeren" auf der Startseite
  (mit Rückfrage).
- **Neu beim Aufräumen des Testaufbaus gefunden**: zwei Sprachen mit
  derselben Modell-URL teilen sich den Ordner - das Löschen der einen nahm
  der anderen das Modell weg. `SttSettingsActivity` löscht die Dateien jetzt
  nur, wenn keine andere konfigurierte Sprache dieselbe URL nutzt.

### Verifiziert (Emulator, API 34 x86_64)

- `./gradlew clean assembleDebug` sauber, **keine** Compiler-Warnings.
- **Befund 2, der eigentliche Regressionstest**: deutsches Modell gezielt
  zerstört (`am/final.mdl` auf 7 Byte gekürzt, Datei existiert also weiter,
  `Model()` scheitert), Deutschlandfunk abgespielt. Logcat zeigt exakt
  "Neuversuch 1/3", "2/3", "3/3" im 15s-Takt, danach "Analyse für
  'Deutschlandfunk' bleibt aus (…) - Wiedergabe läuft weiter". **Null**
  Treffer für "antwortet nicht"/"aus der Rotation"/"schalte weiter" im
  gesamten Lauf (vorher hätte hier die Kaskade durch alle Sender begonnen),
  UI-Dump zeigt den Hinweistext, Wiedergabe lief durchgehend weiter.
- **Befund 4**: Kalibrierung für "de" gestartet, "🗣 Das ist Sprache"
  markiert - Zähler stieg auf 59 Samples nach 30s und 108 nach 50s (exakt
  2/s, ein Sample pro Häppchen), und zwar bei konstantem Rohwert 0%. Genau
  dieser Fall (Wert ändert sich nicht) hätte vorher nach EINEM Sample
  aufgehört zu zählen.
- **Befund 3, der bis dahin nie live gelaufene Pfad**: dritte Sprache "de2"
  angelegt (dieselbe Modell-URL wie "de", dadurch ohne zweiten 45MB-Download
  ein drittes gleichzeitig geladenes `Model`), über den Wizard nacheinander
  "en" und "de2" erzwungen - Logcat zeigt "Vosk-Modell fuer Sprache 'de2'
  geladen" und danach "… aus dem Cache verdraengt (LRU, max 2 gleichzeitig)",
  kein Absturz, Wiedergabe/Auto-Switch liefen normal weiter. Crash-Buffer
  (`adb logcat -b crash`) über die gesamte Testreihe leer.
- **Befund 16**: Knopf leerte eine real gewachsene DB mit **321** gelernten
  Clips ("🗑 Fingerprint-DB geleert: 321 Clip(s) gelöscht") - schöner Beleg
  dafür, dass das unbegrenzte Wachstum kein theoretisches Problem war.
- **Nebenbei bestätigt**: der neue Zip-Slip-Schutz bricht keinen normalen
  Modell-Download (deutsches Modell nach dem Test regulär über die App neu
  geladen, "Modell bereit."), und das Löschen einer Sprache mit geteilter
  Modell-URL lässt die andere intakt (Dateisystem nach dem Löschen von "de2"
  geprüft).
- Emulator danach wieder im Ausgangszustand (dieselben Sender, "de"/"en"
  bereit, "de2" entfernt).

### Bewusst NICHT gemacht

- **Befund 17 (kein HLS/DASH)**: nur dokumentiert. Das Nachziehen von
  `media3-exoplayer-hls`/`-dash` ist eine Funktionserweiterung (mehr
  spielbare Sender), keine Review-Korrektur - Entscheidung dafür gehört in
  eine eigene Runde, zumal der Analysepfad HLS weiterhin nicht lesen könnte
  (`MediaExtractor` parst keine m3u8) und solche Sender dann dauerhaft ohne
  Erkennung liefen.
- **Befund 8 (Verwaltungs-Activity mit hunderten Sendern)**: nur
  dokumentiert. Ein RecyclerView-Umbau samt Suche/Filter ist Feature-Arbeit.
- **Befund 5 (Rest)**: blockierende MediaExtractor-/MediaCodec-Aufrufe
  bleiben unabbrechbar - ein sauberer Fix bräuchte einen eigenen,
  interruptierbaren Thread pro Lauf. Entschärft ist nur die Folge
  (Nachzügler können nichts mehr veröffentlichen), nicht die Ursache.
- **Befund 6 (Rest)**: `StationRepository.init()` liest `stations.json`
  weiterhin synchron in `Application.onCreate()` - asynchron zu machen
  würde die dort bewusst hergestellte Invariante "Repository ist fertig,
  bevor irgendeine Komponente läuft" aufweichen; das ist die teurere
  Änderung von beiden.
- Kein Test des Wake-Mode bei ausgeschaltetem Display (Befund 7) - im
  headless-Emulator nicht sinnvoll nachstellbar, bleibt auf einem echten
  Gerät zu bestätigen.
- Kein Test des Erfolgspfads der Kalibrierung mit zwei echt
  unterschiedlichen Quellen (offen seit Fortsetzung 5) - die Sample-Sammlung
  ist jetzt verifiziert, ein sauberer `overlapping=false`-Vorschlag samt
  "Übernehmen" weiterhin nicht.

## 2026-08-08 (Fortsetzung 8) — Status "fertig" in der Doku nachgezogen

Auslöser: mit Phase 8 (siehe die beiden vorigen Einträge) ist der komplette
Fahrplan `RadioZapper_Android_Fahrplan.md` abgearbeitet - die Doku führte die
App aber weiter als "Aktiver Prototyp im Bau, kein fertiges Produkt".

- `README.md`: Warnbanner ersetzt durch einen Fertig-Hinweis (alle acht
  Phasen umgesetzt und live getestet, keine geplanten Ausbaustufen offen).
  Bewusst MIT dem Zusatz, dass das an den dokumentierten Grenzen nichts
  ändert (doppelter Netzwerkverbrauch, kein HLS/DASH, keine Play-Store-
  Reife) - "fertig laut Fahrplan" ist nicht "kann alles". Titel jetzt
  "RadioZapper (Android)" statt "RadioZapper MVP (Android)", und der
  Einleitungsabsatz zählt den heutigen Stand auf statt des Zwischenstands
  von Phase 3.
- `CLAUDE.md`: dieselbe Statuszeile ("Fertig im Sinne des Fahrplans …
  weiterhin Eigenbedarfs-Software mit dokumentierten Grenzen").
- `../README.md` (Docker-Projekt, zugleich die GitHub-Startseite): neuer
  kurzer Abschnitt zur App in BEIDEN Sprachfassungen - Details siehe
  `../SESSION.md`, Eintrag vom selben Tag.

Reine Doku-Änderung, kein Code angefasst, deshalb kein neuer Build und
keine neue `version.json` (die installierte APK bleibt der Stand von
15:57).

## 2026-08-08 (Fortsetzung 9) — Umbenennung RadioZapper → KeinSabbelRadio

Auslöser: Nutzerwunsch, das Gesamtprojekt umzubenennen (siehe `../SESSION.md`,
Eintrag vom selben Tag, für die Docker-Seite und die Gesamt-Abwägung).

- `strings.xml`/`app_name`: „RadioZapper MVP“ → „KeinSabbelRadio MVP“.
- `RadioZapperApplication.kt` → `KeinSabbelRadioApplication.kt` (Klasse +
  Datei), `AndroidManifest.xml`-Referenz mitgezogen.
- `themes.xml`: `Theme.RadioZapperMvp` → `Theme.KeinSabbelRadioMvp`
  (5 Referenzstellen im Manifest).
- `settings.gradle.kts`: `rootProject.name` → `"KeinSabbelRadioMvp"`.
- `RadioZapper_Android_Fahrplan.md` → `KeinSabbelRadio_Android_Fahrplan.md`
  (git mv), alle Verweise darauf in `CLAUDE.md`/Kommentaren nachgezogen.
- `UpdateManager.kt` + `update_server.py` (`ALLOWED_PATHS`) + `.gitignore`:
  Dateiname `radiozapper.apk` → `keinsabbelradio.apk`.
- `CLAUDE.md`/`README.md`: durchgängiges Ersetzen des Produktnamens in
  Prosa und Code-Beispielen.
- **Bewusst NICHT angefasst**: `applicationId`/`namespace`
  (`com.radiozapper.mvp`) im ganzen Java-Package-Baum — Abwägung und
  Begründung (In-Place-Update via `UpdateManager` würde sonst brechen,
  bestehende Installation bliebe als zweites Icon liegen) steht in
  `../SESSION.md`. `SESSION.md` (dieses Dokument) bewusst NICHT
  rückwirkend durchsucht — nur die Titelzeile angepasst, ältere Einträge
  bleiben wie sie waren (append-only-Konvention).

Da Klassen-/Theme-/Ressourcennamen sich geändert haben (nicht nur Doku),
war ein echter Rebuild PFLICHT, nicht optional:

```bash
./gradlew assembleDebug   # BUILD SUCCESSFUL, 9s
cp app/build/outputs/apk/debug/app-debug.apk keinsabbelradio.apk
echo "{\"buildTime\": \"2026-08-08 17:19\"}" > version.json
```

Zusätzlich (außerhalb dieses Gradle-Projekts, Host-Infrastruktur): der
systemd-User-Service zeigte schon VOR diesem Rename auf einen toten Pfad
(`/opt/docker/radiozapper/...` — das Repo-Verzeichnis war bereits manuell
umbenannt, der laufende Prozess hielt nur noch ein offenes Datei-Handle).
Unit-Datei umbenannt auf `keinsabbelradio-android-update.service`,
`ExecStart`-Pfad und `Description=` korrigiert, `daemon-reload` +
Neustart durchgeführt.

### Verifiziert

- `./gradlew assembleDebug` erfolgreich, keine Kompilierfehler durch die
  Umbenennung (Package-Pfad ja unverändert, nur Klassen-/Ressourcennamen).
- `applicationId`/`namespace` sowie alle `package`/`import`-Zeilen im
  Java-Baum stichprobenartig gegengeprüft: weiterhin durchgängig
  `com.radiozapper.mvp`.
- Update-Server nach dem systemd-Fix live getestet:
  `curl http://localhost:8098/version.json` → `200`, Inhalt
  `{"buildTime": "2026-08-08 17:19"}`; `curl .../keinsabbelradio.apk` →
  `200`, Größe passend zur frisch gebauten APK (~46 MB); `curl
  .../radiozapper.apk` (alter Name) → `404` wie erwartet, da nicht mehr
  in `ALLOWED_PATHS`.

### Bewusst NICHT gemacht

Kein `adb install`/Emulator-Test dieser spezifischen APK — reine
Umbenennung ohne Verhaltensänderung, das Laufzeitverhalten wurde in
früheren Einträgen bereits ausführlich verifiziert. Kein neues
App-Icon/Logo (Banner-Bild `pics/keinsabbelradio.webp` zeigt weiterhin
den alten „RADIOZAPPER“-Schriftzug) — kein Bildgenerierungs-Tool zur
Hand, offener Folgepunkt.
