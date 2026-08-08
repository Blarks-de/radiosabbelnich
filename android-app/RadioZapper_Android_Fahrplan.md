# RadioZapper Android — Fahrplan zur Feature-/Optik-Parität mit dem Python-Projekt

Stand: 2026-08-07. Basis: `android-app/` (Kotlin/ExoPlayer/Vosk, 3 hartcodierte
Sender, Glättung + Auto-Switch + Cooldown, Update-Mechanismus) vs. das
Docker/Python-Projekt (voller Funktionsumfang, siehe SESSION.md).

## Wie dieses Dokument gemeint ist

Acht Phasen, grob nach Nutzwert/Aufwand sortiert. Jede Phase hat einen
fertigen **Plan-Prompt** zum Copy-Paste in Claude Code — der Prompt bittet
explizit erst um einen Plan (dein bekanntes Muster "Plan first vs.
implement"), nicht um sofortige Umsetzung. Erst wenn der Plan steht und dir
gefällt, sagst du "leg los" o.ä. Jeder Prompt endet mit der Bitte, Änderungen
für `android-app/SESSION.md` (neu anzulegen, analog zum Docker-Projekt) und
`android-app/README.md` zusammenzufassen.

Reihenfolge ist ein Vorschlag, keine Pflicht — Phase 1 (Sender-Verwaltung)
ist aber die sinnvollste erste Baustelle, weil praktisch alles Weitere
(Kategorien fürs STT, Kalibrierung, News-Break-Sender-Bezug) darauf aufbaut.

---

## Phase 1 — Sender-Verwaltung (Persistenz + UI)

**Warum zuerst:** 3 hartcodierte Sender sind der größte funktionale
Rückstand. Ohne echte Senderliste mit Kategorien hängt alles Weitere
(Kategorie-Sprachen fürs STT, Watchdog-Ban pro Sender-ID) in der Luft.

**Ziel:** Sender-Liste analog `stations.json`/Config-Seite — hinzufügen,
bearbeiten, löschen, aktivieren/deaktivieren, Kategorien (Lokal/Regional/
National/International/Global/Interstellar/Unsortiert), alphabetisch
sortiert. Persistenz lokal auf dem Gerät (Room oder einfaches JSON-File im
App-internen Speicher — Claude Code soll das bewerten).

```
Ich möchte die Android-App (android-app/) von den 3 hartcodierten
Sendern auf eine echte, persistente Senderverwaltung umstellen — als
Vorbild dient stations_store.py / die Config-Seite des Docker-Projekts
(siehe SESSION.md, Abschnitte zur Sender-Verwaltung, Kategorien,
"Unsortiert"). Lies dir android-app/README.md und den aktuellen Stand
von PlaybackService.kt/MainActivity.kt durch, bevor du planst.

Anforderungen:
- Sender als {id, name, url, category, enabled}, persistent auf dem
  Gerät gespeichert (deine Empfehlung: Room vs. einfaches JSON — bitte
  kurz begründen, was hier angemessen ist, das ist kein Server-Backend).
- Eigener Bildschirm/Screen zur Sender-Verwaltung: Liste gruppiert nach
  Kategorie, Haken zum Aktivieren/Deaktivieren, Bearbeiten/Löschen,
  Formular für neue Sender.
- Rotation folgt weiterhin alphabetisch innerhalb der aktiven Sender
  (wie im Docker-Projekt).
- Die 3 bisherigen Sender (Deutschlandfunk/1LIVE/SWR3) werden beim
  ersten Start als Startbestand migriert, nicht einfach gelöscht.
- Cooldown-Logik (stationCooldownUntil) muss weiterhin über Sender-ID
  funktionieren, auch wenn sich die Liste jetzt live ändert.

Bitte KEINEN Code schreiben. Erstelle zuerst einen Plan: welche neuen
Dateien/Klassen, wie die Persistenz konkret aussieht, wie der
Migrationsschritt für Bestandsnutzer (die App läuft ja schon auf
meinem Handy) sauber und ohne Datenverlust abläuft. Danach frage ich
gezielt nach, bevor wir umsetzen.

Am Ende (nach der eigentlichen Umsetzung, nicht des Plans) bitte
Zusammenfassung für android-app/SESSION.md (analog zum bestehenden
Docker-Session-Log — neu anzulegen, falls noch nicht vorhanden) sowie
Aktualisierung von android-app/README.md.
```

---

## Phase 2 — Watchdog: tote/dauerhaft sprechende Sender sperren

**Warum:** Direkt anschließend an Phase 1 sinnvoll, weil jetzt beliebig
viele echte Sender-URLs reinkommen können (inkl. kaputter). Ohne Watchdog
kann ein toter Stream die Rotation lahmlegen — exakt der Bug, den ihr im
Docker-Projekt am 2026-08-03 gefunden habt (8,5h Stillstand durch BBC
Radio Scotland).

```
Basierend auf der neuen Senderverwaltung aus Phase 1: ich möchte einen
Watchdog analog zum Docker-Projekt (siehe SESSION.md, Eintrag
"Review-Befunde: Watchdog gegen tote Sender" vom 2026-08-03).

Anforderungen:
- Erkennt Sender, die dauerhaft keinen Ton/keine Verbindung liefern
  (ExoPlayer-Fehler/Timeout), und nimmt sie für eine Cooldown-Zeit
  (z.B. 5 Minuten, wie im Docker-Projekt) aus der Rotation.
- Getrennt vom bereits vorhandenen STATION_COOLDOWN_SECONDS-Mechanismus
  (der ist für "gerade als Sprache erkannt", hier geht's um "technisch
  kaputt") — beide Sperren sollen im UI unterscheidbar sein (z.B. "Pause
  wegen Sprache" vs. "Sender antwortet nicht").
- Sind alle aktiven Sender gesperrt, werden alle Sperren aufgehoben statt
  in einer Warteschleife hängen zu bleiben (wie im Docker-Projekt gelöst).
- Ein manueller Sender-Wechsel auf einen gesperrten Sender hebt dessen
  Sperre auf (expliziter Nutzerwunsch schlägt Automatik).

Bitte zuerst einen Plan (kein Code), inkl. wie ExoPlayer-Fehler/-Events
am zuverlässigsten für "liefert dauerhaft nichts" ausgewertet werden
(Player.Listener/onPlayerError vs. Buffering-State-Timeout). Danach
Rückfrage an mich, dann Umsetzung.

Am Ende: Zusammenfassung für android-app/SESSION.md + README-Update.
```

---

## Phase 3 — Prebuffering für flüssiges Umschalten

**Warum:** Sobald es viele echte Sender gibt, nervt eine spürbare
Verbindungslücke bei jedem Wechsel. Im Docker-Projekt löst das
`PrebufferedSource` (siehe SESSION.md, 2026-08-02 "Vorausschauendes
Puffern"). Auf Android reicht vermutlich eine einfachere Variante
(ExoPlayer kann mehrere `MediaItem`s vorbereiten).

```
Ich möchte Sender-Wechsel in der Android-App spürbar flüssiger machen,
ohne die volle Prebuffer-Architektur des Docker-Projekts 1:1 zu
kopieren (das ist Python/ffmpeg-spezifisch, siehe SESSION.md
"Vorausschauendes Puffern der nächsten 5 Sender" vom 2026-08-02) —
bitte bewerte, was mit ExoPlayer/media3 auf Android idiomatisch und
sinnvoll ist (z.B. mehrere ExoPlayer-Instanzen vorwärmen vs. einfach
nur die Verbindungs-/Pufferzeit über UI-Feedback (Spinner) überbrücken).

Kontext: Sender-Wechsel passiert entweder manuell (Button/Sender-Liste)
oder automatisch bei erkannter Sprache (attemptAutoSwitch in
PlaybackService.kt).

Bitte zuerst einen Plan mit 2-3 Optionen (Aufwand/Nutzen), keine
Umsetzung. Ich entscheide dann, wie weit wir hier gehen — das muss
nicht 1:1 wie im Docker-Projekt werden, Hauptsache spürbar besser als
jetzt.

Am Ende: Zusammenfassung für android-app/SESSION.md + README-Update.
```

---

## Phase 4 — Audio-Fingerprinting (Jingle-/Werbe-Erkennung)

**Warum:** Größerer Brocken, aber macht die Erkennung deutlich
zuverlässiger (wiederkehrende Jingles/Ads werden nach 2x sofort erkannt,
nicht erst durch VAD/STT). Vorlage: `fingerprint.py` im Docker-Projekt,
inkl. der wichtigen Lehre aus SESSION.md (2D-Peaks statt Top-N global,
sonst matcht alles gegen alles — siehe Eintrag "Fingerprint-Algorithmus
überarbeitet" vom 2026-08-02).

```
Ich möchte die Android-App um Audio-Fingerprinting erweitern, analog zu
fingerprint.py im Docker-Projekt. WICHTIG: lies dir in SESSION.md den
Eintrag "Fingerprint-Algorithmus überarbeitet (Frequenzbänder/2D-Peaks)"
vom 2026-08-02 genau durch, bevor du planst — der ALTE, naive Ansatz
(Top-N-Peaks pro Frame ohne Frequenzband-Trennung) hat im Praxistest
351 von 351 unterschiedlichen Clips fälschlich als identisch erkannt.
Der Fix (2D-lokale-Maxima in Zeit UND Frequenz, MIN_FREQ_HZ=200 gegen
Netzbrumm, Schwelle MIN_HASH_MATCHES) ist Pflicht-Ausgangspunkt, keine
Kür — bitte das Verfahren 1:1 konzeptionell übernehmen (Kotlin-Neubau,
kein Python-Wrapper), nicht den naiven Ansatz neu erfinden.

Anforderungen:
- Läuft auf dem bereits vorhandenen Analyse-Audiostrom (StreamAnalyzer),
  kein zweiter Decode-Pfad nötig, falls vermeidbar.
- Lokale SQLite/Room-DB für Hashes (analog fingerprints.db).
- Bei Treffer: sofortiger Wechsel, analog zur bestehenden
  Sprache-erkannt-Logik.
- Ein einfaches "Fehlalarm zurücknehmen" (analog zum Docker-Projekt-
  Knopf "Zapping-Fehler") ist wünschenswert, aber zweite Priorität.

Bitte zuerst einen Plan (kein Code): wie die FFT/Peak-Erkennung auf
Android performant läuft (Bibliothek vs. eigene Implementierung),
Datenmodell, Einbindung in den bestehenden Analyse-Loop. Ich gebe erst
danach grünes Licht.

Am Ende: Zusammenfassung für android-app/SESSION.md + README-Update.
```

---

## Phase 5 — Optik-Angleichung (Branding, Bullshitometer, Zustandsanzeigen)

**Warum:** Rein kosmetisch, aber genau das, was "Android und Web fühlen
sich wie ein Projekt an" ausmacht — und im Vergleich zu Phase 3/4 schnell
erledigt.

```
Ich möchte die Optik der Android-App an das Web-Interface angleichen
(siehe SESSION.md, u.a. Einträge zu Banner-Bild, Türkis-Akzentfarbe
#1abc9c, Bullshitometer, STT-Meter, Fingerprint-Chip, Buttonnamen
"⚡ ZAPPEN!"/"🛑 Zapping-Fehler"/"🔇 Sabbelfilter").

Anforderungen (nur UI, keine neue Logik):
- Akzentfarbe/Branding wie im Web-Interface übernehmen (Türkis, dunkles
  Banner-Motiv falls sinnvoll für Mobile-Format).
- Live-Balken "Bullshitometer" (aktuelle Sprache-Wahrscheinlichkeit,
  grün→rot) sichtbar auf dem Hauptbildschirm, gespeist aus dem bereits
  vorhandenen Smoothing-Status.
- Buttons/Beschriftungen analog zum Web-Interface benennen, wo es
  sinnvoll ist (z.B. manueller Sofort-Wechsel = "⚡ ZAPPEN!").
- Build-Zeitstempel-Anzeige (bereits vorhanden) beibehalten.

Bitte zuerst kurz planen, welche Compose/View-Änderungen nötig sind
(reine Optik, überschaubarer Umfang — kann knapper geplant werden als
die vorherigen Phasen). Danach umsetzen.

Am Ende: Zusammenfassung für android-app/SESSION.md + README-Update.
```

---

## Phase 6 — Nachrichten-Pause (News-Break)

**Warum:** Eigenständiges, gut abgegrenztes Feature — baut auf Phase 1
(Sender-Verwaltung) und einer lokalen MP3-Quelle auf dem Gerät auf.
Vorlage: `news_break.py` (siehe SESSION.md, 2026-08-03).

```
Ich möchte das Nachrichten-Pause-Feature aus dem Docker-Projekt
(news_break.py, siehe SESSION.md-Einträge vom 2026-08-03) sinngemäß
für Android nachbauen: zur vollen/halben Stunde für ein Zeitfenster
statt des Radiosenders eine zufällige MP3 aus einem lokalen Android-
Ordner abspielen (z.B. per Storage Access Framework ausgewählt), danach
automatisch zurück zum vorher laufenden Sender.

Wichtig aus der SESSION.md-Historie, bitte beachten:
- Denselben Titel nicht direkt zweimal hintereinander spielen (siehe
  "recent"-Mechanismus, RECENT_HISTORY_SIZE-Muster vom 2026-08-06).
- Bei mehreren Titeln im Fenster: nachladen, bis das Zeitfenster vorbei
  ist (nicht nach der ersten MP3 sofort zurückspringen — das war ein
  echter Bug im Docker-Projekt, siehe Eintrag "News-Break spielte nur
  eine MP3" vom 2026-08-04).
- Der pausierte Sender bleibt "gemerkt", damit ein Klick währenddessen
  auf genau diesen Sender korrekt interpretiert wird.

Bitte zuerst planen (kein Code): wie der Android-Ordnerzugriff (SAF/
MediaStore) am robustesten funktioniert, wie sich das mit dem
bestehenden PlaybackService verträgt (Pause der Auto-Switch-Logik
während der Nachrichten-Pause, analog zum Docker-Projekt). Danach
Rückfrage, dann Umsetzung.

Am Ende: Zusammenfassung für android-app/SESSION.md + README-Update.
```

---

## Phase 7 — Mehrsprachiges STT + Kalibrierung

**Warum:** Größter/aufwändigster Brocken, deshalb spät. Nur sinnvoll,
wenn du wirklich international hörst — sonst reicht das bestehende
deutsche Vosk-Modell dauerhaft. Vorlage: `stt_filter.py` +
Kalibrierungs-Wizard (SESSION.md, 2026-08-06, "Fortsetzung 5" + "6").

```
Ich möchte die Android-App um mehrsprachige STT-Erkennung erweitern,
analog zum Docker-Projekt (siehe SESSION.md, Einträge "Mehrsprachige
STT-Erkennung (Teil 1a)" und "Geführter STT-Kalibrierungs-Wizard
(Teil 1b)" vom 2026-08-06, plus die zwei dort dokumentierten echten
Bugs aus dem ersten Praxiseinsatz — bitte die Lehren daraus
berücksichtigen (leere STT-Samples aus der Kalibrierung ausschließen,
alle Aufrufstellen einer geänderten Funktionssignatur konsequent
durchsuchen, nicht nur den Hauptpfad).

Anforderungen:
- Sprache pro Kategorie zuordenbar (Kategorie → Vosk-Modell/Sprachcode),
  Kategorien existieren bereits seit Phase 1.
- Mehrere Vosk-Sprachmodelle können auf dem Gerät vorliegen (Download
  bei Bedarf, wie das deutsche Modell aktuell schon geladen wird).
- Ein einfacher Kalibrierungs-Modus (Sprache-Sender kurz hören lassen,
  dann Musik-Sender, Schwelle vorschlagen) ist wünschenswert, kann aber
  ein zweiter Umsetzungsschritt nach dem Grundgerüst sein.

Bitte zuerst einen Plan (kein Code), inkl. RAM-Abwägung auf dem Handy
(mehrere gleichzeitig geladene Vosk-Modelle vs. Lazy-Load mit kleinem
Cache, analog zum Docker-Projekt-Muster MAX_LOADED_VOSK_LANGUAGES).
Danach Rückfrage, dann Umsetzung in zwei Schritten (Grundgerüst, dann
Kalibrierung) wie im Docker-Projekt.

Am Ende (nach jedem Schritt): Zusammenfassung für android-app/
SESSION.md + README-Update.
```

---

## Phase 8 — Aufräumen & Feinschliff

Am Ende, wenn der Rest steht:

```
Bitte einmal komplett durch android-app/ reviewen (analog zum
Docker-Projekt-Review vom 2026-08-03, das den Watchdog-Bug aufgedeckt
hat): auf tote Code-Pfade, unbehandelte Fehlerfälle (z.B.
Netzwerkabbruch mitten in Wiedergabe), Speicherlecks bei mehrfachem
Sender-/Modell-Wechsel, und Konsistenz zwischen android-app/README.md
und dem tatsächlichen Funktionsstand prüfen.

Bitte NICHTS sofort ändern, sondern einen Befund-Bericht liefern
(analog zum Docker-Projekt-Eintrag "Review-Befunde" vom 2026-08-03),
den wir gemeinsam priorisieren.
```

---

## Kurzübersicht

| Phase | Thema | Aufwand | Abhängig von |
|---|---|---|---|
| 1 | Sender-Verwaltung | mittel | — |
| 2 | Watchdog (tote Sender) | klein-mittel | Phase 1 |
| 3 | Prebuffering | mittel | Phase 1 |
| 4 | Fingerprinting | groß | — (unabhängig, aber sinnvoll nach 1) |
| 5 | Optik-Angleichung | klein | — |
| 6 | Nachrichten-Pause | mittel | Phase 1 |
| 7 | Mehrsprachiges STT | groß | Phase 1 |
| 8 | Review/Feinschliff | klein | alle vorherigen |

Empfehlung für die Reihenfolge, falls du nicht strikt der Liste folgen
willst: **1 → 5 → 2 → 3 → 6 → 4 → 7 → 8** (schneller sichtbarer Fortschritt
zuerst, die zwei großen Brocken 4/7 zuletzt).
