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
