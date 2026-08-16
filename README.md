<p align="center">
  <img src="pics/radiosabbelnich.webp" alt="RadioSabbelNich" width="700">
</p>

# RadioSabbelNich

*[🇬🇧 English version further below](#radiosabbelnich-english-version)*

# Namensänderung: von RadioZapper über KeinSabbelRadio zu --> RadioSabbelNich
Zweiter Anlauf: KeinSabbelRadio war als Name auch nicht der große Wurf,
deswegen heisst das Ding jetzt RadioSabbelNich. Diesmal soll es dabei
bleiben.

RadioSabbelNich hört mehrere Internetradio-Sender gleichzeitig für dich mit
und schaltet automatisch weiter, sobald irgendwo geredet wird.
Moderation, Nachrichten, Werbung, Jingles. Übrig bleibt (möglichst) nur
Musik. Der ausgewählte Sender wird per Icecast neu ausgestrahlt, sodass
man ihn im ganzen (Tail-)Netz mit VLC, im Browser oder sonst einem
Streaming-Client hören kann.

## ⚠️ Nur privat, nur hinter VPN — kein öffentlicher Betrieb

**RadioSabbelNich ist ausdrücklich nicht für den öffentlichen Betrieb
gedacht.** Icecast-Port (8000) und Web-Interface-Port (5000) gehören
niemals direkt ins offene Internet (kein Port-Forwarding, kein
öffentlicher Reverse-Proxy) — RadioSabbelNich läuft immer hinter einem VPN
(Tailscale o.ä.), erreichbar nur für Geräte im eigenen vertrauten Netz.
Zwei konkrete Gründe:

- **Ressourcen**: Ein offen erreichbarer Icecast-Mountpoint wird früher
  oder später gefunden (Scanner, Streaming-Aggregatoren, Hotlinking) —
  und dann zieht potenziell das halbe Internet unkontrolliert Bandbreite
  und Rechenzeit, ohne dass man das je wieder eingefangen bekommt.
- **Urheberrecht**: RadioSabbelNich streamt fremde, lizenzierte
  Radioprogramme neu aus. Für den privaten Eigenbedarf im eigenen
  (Tail-)Netz ist das eine Sache — öffentlich zugänglich gemacht, ist es
  eine unlizenzierte öffentliche Wiedergabe urheberrechtlich geschützter
  Inhalte. Es gibt reichlich Kanzleien, für die genau das ein
  Geschäftsmodell ist.

Web-Interface und Config-Seite haben zudem keinerlei Authentifizierung
(siehe unten) — ein weiterer Grund, warum "kurz mal öffentlich
erreichbar machen" keine gute Idee ist.

## Wie die Erkennung funktioniert

1. **Silero VAD** (neuronales Netz, spezialisiert auf Sprache-Erkennung)
   klassifiziert laufend ~1-Sekunden-Fenster des aktuellen Senders als
   Sprache oder Musik. Fällt VAD mal nicht (z.B. aus Umgebungsgründen),
   springt automatisch eine einfachere Signal-Heuristik ein
   (Zero-Crossing-Rate/Spektrale Flachheit/Energie-Modulation).
2. Hält die Sprache-Erkennung einige Sekunden am Stück durch, schaltet
   RadioSabbelNich reihum zum nächsten aktivierten Sender, bis wieder Musik
   läuft.
3. Parallel dazu läuft ein **Audio-Fingerprinting** (Shazam-artiges
   Constellation-Map-Verfahren): erkannte Sprache-Clips werden
   gehasht und mit einer SQLite-Datenbank bereits gehörter Clips
   verglichen. Ist ein Clip schon bekannt (z.B. ein wiederkehrender
   Werbespot oder Sender-Jingle), wird sofort umgeschaltet, ohne erst
   die volle Sprache-Erkennungszeit abzuwarten.

Beide Mechanismen sind nicht perfekt — dafür gibt's im Web-Interface
Korrektur-Knöpfe (siehe unten).

## Umgang mit toten Sendern (Watchdog)

Nicht jede Sender-URL bleibt dauerhaft abspielbar — importierte Listen
enthalten Karteileichen, und auch ein funktionierender Sender kann mal
minutenlang nichts liefern. Damit das nicht die ganze Wiedergabe anhält:

- Liefert der **aktuelle** Sender drei Analysefenster in Folge gar nichts,
  fliegt er für 5 Minuten aus der Rotation und RadioSabbelNich schaltet
  automatisch weiter (`STREAM_FAILURE_LIMIT`/`STATION_DEAD_COOLDOWN` in
  `radiosabbelnich.py`).
- Stirbt ein **Hintergrund-Puffer**, wandert der Sender sofort auf dieselbe
  Sperrliste, statt im Sekundentakt neu verbunden zu werden.
- Gesperrte Sender werden beim automatischen Weiterschalten übersprungen
  und nicht gepuffert. Nach Ablauf der 5 Minuten bekommen sie automatisch
  wieder eine Chance — ein manueller Klick im Web-Interface hebt die
  Sperre sofort auf.

Ohne diesen Watchdog konnte ein einziger toter Sender den kompletten
Player anhalten: real passiert mit einer importierten DASH-URL, die
ffprobe beim Import korrekt als "hat Audio" durchwinkte, die ffmpeg aber
nicht dauerhaft abspielen kann — 3569 Reconnect-Versuche über 8,5
Stunden, Icecast-Mount die ganze Zeit weg.

## Vorausschauendes Puffern & Playout-Delay

RadioSabbelNich hält die nächsten Sender in Rotationsreihenfolge im
Hintergrund bereits am Laufen und puffert von jedem die letzten
`prebuffer_seconds` Sekunden vor (Default 10s, unter `/config`
einstellbar, wirkt sofort ohne Neustart). Das dient zwei Zwecken
gleichzeitig:

1. **Nahtloser Wechsel**: ein Wechsel zu einem vorgepufferten Sender
   (automatisch oder manuell) übernimmt die schon laufende Quelle sofort,
   statt neu zu verbinden — kein Reconnect-Ruckler.
2. **Hörer-Delay für die Sprache-Erkennung**: derselbe Puffer verzögert
   auch die Ausstrahlung des GERADE laufenden Senders um exakt
   `prebuffer_seconds`. Die Sprache-Erkennung (VAD/Heuristik/STT/
   Fingerprint) läuft dabei auf frisch eingetroffenem Audio, das der
   Hörer erst nach dieser Verzögerung bekommt — Moderation/Werbung kann
   dadurch VOR der Hörer-Ausgabe erkannt und weggeschaltet werden, statt
   erst danach. Kostet zusätzliche Bandbreite/CPU (ein zusätzlicher
   ffmpeg-Prozess pro gepuffertem Sender, parallel zum aktuellen; Default
   5 Sender × 10s ist auf haushaltsüblicher Hardware unkritisch).

Ein Wechsel übernimmt dabei die komplette Fenster-Reihe des Ziel-Puffers
auf einen Schlag — die Ausgabe läuft danach im selben Sekundentakt weiter
wie vorher, keine Lücke, kein doppelt gesendetes Audio, keine kumulative
Drift gegenüber der echten Zeit (verifiziert: das Delay ist konstant, es
wächst nicht mit jedem Zap).

**Einschränkung**: Trifft ein Wechsel einen Sender, der gerade NICHT
vorgepuffert ist (z.B. manueller Klick außerhalb der nächsten
`prebuffer_count` Sender in der Rotation, oder ein Notfall-Wechsel, weil
alle Puffer-Kandidaten selbst tot sind), läuft dieser Sender ohne Delay
weiter — sofortige Reaktion auf den Klick, aber ohne den
Vorausschau-Vorteil, bis der nächste Wechsel wieder einen vorgepufferten
Sender trifft. Ein lückenloser Übergang von 0 auf volle Verzögerung ist
ohne Zeitdehnung/Pitch-Manipulation nicht möglich, deshalb bewusst nicht
versucht.

**Nachrichten-Pause verschiebt sich entsprechend**: läuft der aktuelle
Sender gerade mit vollem Delay, kommt die Nachrichten-MP3 bis zu
`prebuffer_seconds` später beim Hörer an als die tatsächliche :00/:30 —
die Fensterlänge selbst (`window_minutes`) bleibt davon unberührt.

## Nachrichten-Pause

Zur vollen und halben Stunde verlesen praktisch alle Radiosender
Nachrichten. Statt dessen kann RadioSabbelNich für ein kurzes Zeitfenster
eine zufällige MP3 aus einem lokalen Ordner abspielen (z.B. eigene
Jingles/Musikstücke von einem SMB-Mount) — danach geht's automatisch
mit dem pausierten Sender weiter, ganz normal.

Konfiguriert wird das über den `news_break`-Block in `settings.json`,
einstellbar über die Formular-Sektion "📰 Nachrichten-Pause" oberhalb der
Senderliste auf der Config-Seite (`/config`) oder direkt per API:

```json
"news_break": {
  "enabled": false,
  "mp3_folder": "/app/news_mp3",
  "window_minutes": 2.0,
  "enabled_hours": null
}
```

- **`enabled`** — Feature an/aus.
- **`mp3_folder`** — Container-interner Pfad (nicht der Host-Pfad!), auf
  der Config-Seite über eine Breadcrumb-Ordnerauswahl gesetzt (durch die
  Unterordner von `/app/news_mp3` klicken statt den Pfad einzutippen).
  Der eigentliche Host-Ordner wird über `NEWS_MP3_FOLDER` in `.env` von
  außen reingemountet (siehe `docker-compose.yml`), typischerweise ein
  SMB-Mount — dafür braucht es einen Container-Neustart, kein Feld auf der
  Config-Seite. Die Auswahl durchsucht seit 2026-08-14 auch Unterordner,
  bis zu 5 Ebenen tief. Ordner fehlt/ist leer/enthält (auch in den
  Unterordnern) keine MP3s/nicht lesbar → Feature wird für dieses
  Zeitfenster einfach übersprungen, mit Logeintrag, kein Fehler.
  Unter dem Feld zeigt die Config-Seite zur Orientierung read-only den
  echten Host-Pfad an (aus `NEWS_MP3_FOLDER` durchgereicht) — der
  Container kennt ihn sonst grundsätzlich nicht, Docker übersetzt
  Host→Container-Pfad nur einmalig beim Start.
- **`window_minutes`** — wie viele Minuten vor/nach :00 und :30 aktiv.
- **`enabled_hours`** — optional `[start, end]`, z.B. `[6, 22]` für "nur
  6–22 Uhr"; `null` = rund um die Uhr. Kein Übernacht-Wraparound (22–6
  wird nicht unterstützt).

Alternativ direkt per API setzen (z.B. für Skripte):
```bash
curl -X POST http://<host>:5000/api/config/settings \
     -H 'Content-Type: application/json' \
     -d '{"news_break_enabled": true, "news_break_window_minutes": 2}'
```

Ein Zeitfenster wird höchstens einmal betreten — läuft eine MP3 kürzer als
das restliche Fenster, wird automatisch eine weitere zufällige MP3
nachgeladen (kein Repeat direkt hintereinander, sofern der Ordner mehr als
eine Datei enthält), bis `window_minutes` abgelaufen ist. Die gerade
laufende MP3 wird dabei **immer bis zu ihrem Ende gespielt**, auch wenn
`window_minutes` währenddessen abläuft — die Pause dauert dadurch im
Zweifel etwas länger als eingestellt, statt eine MP3 mittendrin
abzuwürgen. Erst danach geht's automatisch zurück zum pausierten Sender.
Ein manueller Sender-Wechsel
während der Pause bricht sie sofort ab (eigene Entscheidung schlägt
Automatik, wie überall sonst in RadioSabbelNich auch). Während der Pause
pausiert auch die automatische Sprache-Erkennung (VAD/Heuristik/
Fingerprint) — die MP3 selbst enthält u.U. Sprache, das soll nicht als
"Moderation" auf dem eigentlichen Sender fehlgedeutet werden. Auf der
Radio-Startseite zeigt eine Tag-Anzeige (seit 2026-08-15, per mutagen,
format-übergreifend) währenddessen Titel/Interpret/Album/Jahr der
laufenden MP3 statt nur des Dateinamens — Details siehe "Player-Modus"
unten (dieselbe Anzeige, gleiches Fallback-Verhalten).

## Player-Modus (Grundgerüst)

Erster Umsetzungsschritt der unten unter "Zukünftige Features"
beschriebenen Musik-Library-Idee: ein **eigenständiger, persistierter
Modus** neben dem normalen Radio-Betrieb — oben auf der Radio- UND der
Player-Seite per gut sichtbarem Umschalter ("📻 Radio" / "🎵 Player")
wechselbar (die Funktion hieß bis 2026-08-13 "Musiksammlung" — intern,
in `settings.json`/Code, heißt der Modus weiterhin `music`, nur die
Bezeichnung im Web-Interface wurde vereinfacht). Im Player-Modus ist die
komplette automatische Erkennung (VAD/Heuristik/STT/Fingerprint) aus,
nicht nur pausiert — es läuft ausschließlich lokale Musik, nichts wird
analysiert. Der Modus übersteht einen Container-Neustart (in
`settings.json` gespeichert) — seit 2026-08-15 startet dabei außerdem
**automatisch die Wiedergabe** (erster Track des konfigurierten Ordners),
sowohl nach einem Neustart mit bereits gespeichertem Player-Modus als
auch bei einem manuellen Wechsel Radio→Player. Vorher blieb die
Wiedergabe in beiden Fällen inaktiv, bis manuell auf ▶ getippt wurde —
der Modus selbst war zwar korrekt gemerkt, aber es kam kein Ton, bis
jemand aktiv Play drückte.

Auf der eigenständigen Seite `/musik` ("🎵 Player"):

- Der ausgewählte Musik-Ordner wird angezeigt — seit 2026-08-13 der
  **echte Host-Pfad** (serverseitig aus dem Container-Pfad
  zurückübersetzt, per `MUSIC_LIBRARY_FOLDER` aus `.env`), vorher stand
  dort der technisch korrekte, aber für den Nutzer bedeutungslose
  Container-Pfad (`/app/music_library/...`). Ein Knopf "Pfad ändern"
  führt zur eigentlichen Ordnerauswahl auf der Config-Seite (siehe
  unten).
- Zwei Gruppen von Buttons, "Kategorien" (schnell/langsam/rock/klassik)
  und "Favoriten" (Queen/Pavarotti) — aktuell reine Platzhalter ohne
  Funktion, echte Filterung (Kategorien auf Metadaten/Tags wie BPM/Genre,
  Favoriten auf den Künstler-Tag) kommt erst mit dem Musik-Scan (Phase 1
  der Roadmap unten).
- Ein großer Play/Stop-Button und Zurück/Nächster — spielt die Musikdateien
  im konfigurierten Ordner samt Unterordnern (seit 2026-08-14, bis zu
  5 Ebenen tief), alphabetisch, endlos im Kreis, bis Stop gedrückt wird.
  Seit 2026-08-13 ist dieser eine Button
  auch der einzige sichtbare Knopf fürs tatsächliche Zuhören: ein
  unsichtbares `<audio>`-Element (ohne eigene Bedienleiste) folgt
  automatisch dem Wiedergabestatus. Vorher gab es zusätzlich einen
  nativen Browser-Player mit eigenem Play-Knopf, der unabhängig vom
  großen Button reagierte — zwei "Play"-Knöpfe, die sich gegenseitig
  nicht kannten und sich dadurch in die Quere kamen.
- Kein Banner-Bild mehr auf dieser Seite (seit 2026-08-13, aufgeräumtere
  eigenständige Optik statt der Radio-Seiten-Elemente).
- **Tag-Anzeige** (seit 2026-08-15): unter dem Dateinamen/Fortschritt
  ("Track (i/total)") zeigt eine zweite/dritte Zeile die per mutagen
  ausgelesenen Metadaten — "Interpret – Titel" und "Album (Jahr)",
  format-übergreifend (MP3, FLAC, OGG, M4A/AAC, WAV, APE). Kein Titel-Tag
  vorhanden → Dateiname als Fallback; fehlt Album/Jahr, entfällt die
  zweite Zeile komplett statt Platzhaltern wie "Album: –". Dieselbe
  Anzeige läuft auf der Radio-Startseite mit, sobald eine
  Nachrichten-Pause-MP3 läuft (siehe "Nachrichten-Pause" oben).

Der Musik-Ordner wird — wie der News-Break-MP3-Ordner — auf der
Config-Seite gesetzt, per **Breadcrumb-Ordnerauswahl**: durch die
Unterordner des über `MUSIC_LIBRARY_FOLDER` (`.env`, gleiches Muster wie
`NEWS_MP3_FOLDER`) gemounteten Verzeichnisses klicken, statt einen Pfad
einzutippen. Beide Felder (News-Break-Ordner, Player-Root) nutzen
dieselbe Komponente, speichern aber unabhängig voneinander.

## STT-Sprachfilter

Silero VAD/die Signal-Heuristik erkennen "ist hier eine menschliche
Stimme" — auch gesungene Musik zählt da oft fälschlich mit. Der
STT-Sprachfilter (`stt_filter.py`) hört stattdessen per Speech-to-Text
mit, ob gerade *zusammenhängender Text in der erwarteten Sprache* zu
erkennen ist, und liefert das als zusätzliches Signal für die
Switch-Entscheidung.

Zwei austauschbare Engines, nie gleichzeitig geladen:

- **Vosk** — kleines Kaldi-Modell, leichtgewichtig und auch auf einem
  Raspberry Pi gut nutzbar. Braucht ein eigenes Modell **pro Sprache**.
- **Whisper** (über `faster-whisper`) — genauer, aber deutlich
  ressourcenhungriger, selbst als "tiny"-Modell. Ein einziges geladenes
  Modell deckt beliebig viele Sprachen ab (der Sprachcode wird nur pro
  Analyse mitgegeben) — bei Whisper kostet eine zusätzliche Sprache also
  kein zusätzliches RAM.

### Mehrsprachigkeit: Sprache pro Sender-Kategorie

Welche Sprache für einen Sender geprüft wird, richtet sich nach seiner
**Kategorie** (Lokal/Regional/National/International/…, siehe
"Web-Interface" oben) — nicht nach dem einzelnen Sender. Auf der
Config-Seite gibt es dafür zwei neue Abschnitte unterhalb von
"🗣 STT-Sprachfilter":

- **🌐 STT-Sprachen** — legt an, welche Sprachen überhaupt zur Verfügung
  stehen: Sprachcode (Freitext, z.B. `en`, `fr` — keine feste Liste, da
  Vosk-Modelle ohnehin selbst besorgt werden müssen), bei Engine "Vosk"
  ein Modellpfad, plus eine (empirisch zu ermittelnde, siehe unten)
  Konfidenz-Schwelle. Ein bereits vorhandener Sprachcode wird beim
  erneuten Eintragen aktualisiert statt doppelt angelegt. Jede Zeile
  zeigt zusätzlich den Ladezustand (✅ geladen / ⚠ Fehlermeldung / noch
  nicht geladen) — bei Vosk wird jedes Sprachmodell erst **lazy** beim
  ersten tatsächlichen Sample geladen, nicht schon beim Speichern.
- **🏷 Kategorie-Sprachen** — ordnet jeder der festen Kategorien eine der
  oben angelegten Sprachen zu. Kategorien ohne Auswahl gelten als
  Deutsch (`de`).

Bei Vosk sind aus RAM-Gründen (siehe schwache Hardware/Pi) nie mehr als
**2 Sprachmodelle gleichzeitig** geladen — bei mehr konfigurierten
Sprachen wird das am längsten ungenutzte automatisch verdrängt (LRU) und
beim nächsten Bedarf neu geladen. Wechselt ein Sender die erwartete
Sprache (z.B. durch einen Kategoriewechsel), wird ein noch nicht
abgelaufener STT-Befund der VORHERIGEN Sprache verworfen statt
fälschlich weiterverwendet.

**Zusätzliche Vosk-Modelle mounten**: der mitgelieferte
`VOSK_MODEL_FOLDER`-Mount in `docker-compose.yml` deckt genau EIN Modell
ab (Default: Deutsch, `/app/vosk-model-de`). Für eine weitere Sprache
selbst eine zusätzliche Zeile in `docker-compose.yml` ergänzen, z.B.:

```yaml
      - ${VOSK_MODEL_FOLDER_EN:-./data/vosk-model-en}:/app/vosk-model-en:ro
```

und den resultierenden Container-Pfad (`/app/vosk-model-en`) als
Modellpfad bei "🌐 STT-Sprachen" eintragen — danach `docker compose up -d
--build radiosabbelnich`, damit der neue Mount aktiv wird.

### Kalibrierungs-Wizard

Statt `confidence_threshold` blind zu raten, gibt es auf der Config-Seite
unterhalb von "🏷 Kategorie-Sprachen" den Abschnitt "🧪
Schwellwert-Kalibrierung" — er reproduziert dieselbe Methode, mit der
ursprünglich der Deutsch-Default (0.75) hergeleitet wurde (siehe oben),
nur geführt statt manuell aus den Logs abgelesen:

1. Sprachcode eintragen (bei Vosk muss die Sprache vorher mit Modellpfad
   unter "🌐 STT-Sprachen" angelegt sein, bei Whisper nicht nötig) und
   "🧪 Kalibrierung starten" klicken. Voraussetzung: STT-Filter und
   Sabbelfilter sind aktiv (sonst sampelt STT gar nicht, siehe oben).
2. Manuell auf der Player-Seite einen Sender mit garantiert echtem
   Sprachtext dieser Sprache anschalten (z.B. eine Nachrichtenwelle) und
   ein paar Minuten laufen lassen — die Wizard-Seite zeigt die Sample-Zahl
   sowie Konfidenz-Minimum/Maximum/Mittelwert live (Poll alle 2s).
3. Auf "🎵 Musik-Stufe" umschalten und manuell auf einen Musiksender
   derselben Sprache wechseln, erneut ein paar Minuten sammeln lassen.
4. Sobald beide Stufen Samples haben, erscheint ein Vorschlag (Grenze
   zwischen dem höchsten gemessenen Musik-Wert und dem niedrigsten
   gemessenen Sprache-Wert, mit Sicherheitsmarge Richtung Sprache-Seite)
   — "Übernehmen" speichert ihn direkt als `confidence_threshold` der
   Sprache. Trennen sich Sprache und Musik im gemessenen Sample NICHT
   sauber (Überlappung), zeigt der Vorschlag eine Warnung statt ihn
   unkommentiert zu übernehmen — dann helfen meist mehr Samples oder ein
   anderer Test-Sender.

Samples, bei denen STT gar keinen Text erkannt hat (Pause/Jingle/
Werbeblock während der Sprache-Stufe, reine Instrumentalpassage während
der Musik-Stufe), zählen NICHT in die Statistik — leerer Text bedeutet
"kein Urteil gebildet", nicht "mit niedriger Konfidenz erkannt". Wichtig
bei der Senderwahl für die Musik-Stufe: viele kommerzielle Radiosender
haben erheblichen gesprochenen Anteil (Werbung, Moderation zwischen
Songs) — das kann trotzdem zu einer unsauberen Trennung führen, auch
ganz ohne Erkennungsfehler. Ein Sender mit möglichst wenig Wortanteil
liefert bessere Ergebnisse.

**Wichtig**: Die Kalibrierung schaltet selbst NICHTS um — welcher Sender
gerade läuft, entscheidet ausschließlich die Player-Seite. Während einer
laufenden Kalibrierung ist außerdem die automatische Sender-Umschaltung
komplett pausiert (nicht nur für die Kalibrierungs-Sprache), damit ein
durch die erzwungene Test-Sprache verfälschtes STT-Ergebnis nicht mitten
in der Kalibrierung einen Wechsel auslöst — der laufende Sender bleibt
also stehen, bis die Kalibrierung beendet wird.

### Konfiguration im Detail

Konfiguriert wird das über den `stt_filter`-Block in `settings.json`
(Sprachen selbst über `set_stt_language()`/`delete_stt_language()`
verwaltet, nicht direkt im Block editieren):

```json
"stt_filter": {
  "enabled": false,
  "engine": "vosk",
  "whisper_model_size": "tiny",
  "sample_interval_seconds": 8.0,
  "combine_mode": "and",
  "languages": {
    "de": {"vosk_model_path": "/app/vosk-model-de", "confidence_threshold": 0.75}
  },
  "category_languages": {}
}
```

- **`enabled`** — Feature an/aus.
- **`engine`** — `"vosk"` oder `"whisper"`, gilt GLOBAL für alle
  konfigurierten Sprachen gleichzeitig (siehe oben, warum nie beide
  gemischt werden).
- **`whisper_model_size`** — z.B. `"tiny"`, `"base"` (siehe
  faster-whisper-Dokumentation für weitere Größen), ebenfalls global.
  Modelle werden beim ersten Gebrauch automatisch von HuggingFace geladen
  und in einem dauerhaften Volume zwischengespeichert (kein manueller
  Download nötig, braucht aber beim ersten Aktivieren Internetzugriff und
  etwas Zeit).
- **`sample_interval_seconds`** — wie oft ein kurzer Clip (ca. 3s) zur
  Analyse genommen wird. Läuft kontinuierlich im Hintergrund, unabhängig
  vom aktuellen VAD-Ergebnis (blockiert den Hauptloop nie).
- **`languages.<code>.vosk_model_path`** — Container-interner Pfad (nicht
  der Host-Pfad!) zu einem entpackten Vosk-Modell dieser Sprache, siehe
  oben. Für Deutsch gibt es passende Modelle unter
  [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models) —
  `vosk-model-small-de-0.15` (~45 MB) für schwache Hardware/Pi,
  `vosk-model-de-0.21` (~1 GB) für mehr Genauigkeit; für andere Sprachen
  auf derselben Seite nach dem passenden Modell suchen.
- **`languages.<code>.confidence_threshold`** — ab welcher
  (Best-Effort-)Konfidenz ein Sample als "zusammenhängender Text in
  dieser Sprache" gilt. Der de-Default (0.75) ist **empirisch gemessen**,
  nicht geraten: 10 Live-Clips von Deutschlandfunk (Sprache) lagen nie
  unter 0.83 Konfidenz, 30 Live-Clips von drei Schlager-Sendern (gesungene
  deutsche Musik) im Schnitt bei 0.38 — 0.75 liegt mit Sicherheitsabstand
  unter dem Sprache-Minimum. Für jede weitere Sprache gilt dieselbe
  Methode: ein paar Minuten gegen einen bekannten Sprache- UND einen
  bekannten Musik-Sender dieser Sprache mithören, erkannte
  Texte/Konfidenzwerte landen dafür in `logs/radiosabbelnich.log`.
- **`category_languages`** — Kategorie → Sprachcode (siehe oben), über
  die Tabelle "🏷 Kategorie-Sprachen" gepflegt.
- **`combine_mode`** — wie das STT-Ergebnis mit VAD/Heuristik verknüpft
  wird: `"and"` (Default) verlangt, dass beide "Sprache" sagen — das
  lässt einen Großteil in dieser Sprache gesungener Musik (VAD ja, STT
  erkennt meist keinen zusammenhängenden Text) korrekt als Musik
  durchgehen. **Kein Allheilmittel**: bei klar/langsam gesungenem
  deutschem Schlager erkennt Vosk gelegentlich kurze, grammatisch
  plausible Wortfetzen mit hoher Konfidenz (bei obigem Test ~20% der
  Schlager-Clips trotz Schwelle 0.75) — UND reduziert Fehl-Switches auf
  gesungene Musik deutlich, verhindert sie aber nicht zu 100%. `"or"`
  reicht, wenn eines der beiden Signale "Sprache" sagt — fängt mehr echte
  Moderation, aber wieder anfälliger für denselben Gesangs-Fall.

Modell nicht gefunden oder Ladefehler → nur die betroffene Sprache bleibt
wirkungslos (Log-Meldung, Ladezustand auch auf der Config-Seite pro
Sprache sichtbar), RadioSabbelNich läuft mit den übrigen Sprachen/Sendern
normal weiter. Ein Absturz der Engine bei einem einzelnen Sample
überspringt nur diesen einen Sample, nicht den Hauptprozess.

## Sprache des Web-Interfaces

Player- und Config-Seite gibt es auf Englisch (im Code eingebaute
Basissprache) und Deutsch (externes "Sprachpaket", siehe unten).
Umschaltbar unter `/config` → "🌐 Sprache" (wirkt spätestens eine
Sekunde später, kein Neustart nötig — die Seite lädt nach dem
Speichern automatisch neu). Startwert für eine frische Installation
kommt aus `UI_LANGUAGE` in `.env` (Default `en`, leer lassen reicht
ebenfalls) — sobald einmal über die Config-Seite gespeichert, gewinnt
danach immer diese Einstellung, auch nach einem Neustart des
Containers.

Übersetzt sind alle Texte, die im Browser sichtbar sind (Labels,
Buttons, Meldungen). Log-Datei und Server-seitige Fehlermeldungen
(z.B. bei einer ungültigen Einstellung) bleiben unabhängig von dieser
Einstellung deutsch.

**Weitere Sprachen nachrüsten**: eine Sprache außer Englisch kommt aus
einer eigenen Datei im Ordner `language/` (z.B. `language/Deutsch.lng`
für Deutsch) — analog zu einem Windows-Sprachpaket. Format: einfaches
`Key=Value`, eine Zeile pro Text, `#` leitet einen Kommentar ein. Zwei
Zeilen am Dateianfang sind Pflicht:

```
#!code=de
#!name=Deutsch
```

`code` ist der Maschinencode (taucht in `UI_LANGUAGE`/der gespeicherten
Einstellung auf), `name` der Anzeigename im Sprachauswahl-Dropdown. Die
Datei muss nicht vollständig sein — ein fehlender Text fällt automatisch
auf die englische Basis zurück, kein Absturz. Eine neue `.lng`-Datei
wirkt nach `docker compose up -d --build radiosabbelnich` (Sprachdateien
werden wie der übrige Code beim Bauen ins Image übernommen, kein
Bind-Mount).

## Web-Interface

Erreichbar unter `http://<host>:5000/`:

- Unter dem Banner-Bild steht klein die aktuell laufende Version
  (`VERSION` im Repo-Root, siehe Versionspflege in `CLAUDE.md`) — auf der
  Player- und der Config-Seite.
- **⚙ oben rechts** (fest positioniert, bleibt beim Scrollen sichtbar) —
  führt zur Config-Seite (`/config`).
- **📻 Radio / 🎵 Player** — Modus-Umschalter oben auf der Radio- und der
  Player-Seite (siehe eigener Abschnitt weiter oben). Ein Klick auf den
  jeweils anderen Modus schaltet um UND springt auf die passende Seite
  (dort liegen die zugehörigen Bedienelemente).
- **Aktueller Sender + "Jetzt läuft"** — Titel/Interpret, falls der
  Sender ICY-Metadaten oder eine bekannte Alternativ-Quelle liefert
- **Eingebetteter Player** — direkt im Browser mithören, ohne extra
  App/Client
- **▶️ VLC / 📱 Handy** — zwei Icons unter der "Läuft gerade"-Box öffnen
  jeweils ein QR-Code-Popup: **▶️ VLC** für die Stream-URL zum Eintragen
  in einen externen Player (Standardmäßig automatisch aus der Adresse
  gebildet, über die die Seite gerade aufgerufen wird; auf der
  Config-Seite unter "🔗 Streaming-Adresse" fest hinterlegbar, falls die
  tatsächliche öffentliche Adresse davon abweicht), **📱 Handy** für die
  Adresse dieses Web-Interfaces selbst (praktisch, um die Seite auf einem
  zweiten Gerät zu öffnen oder als PWA zu installieren, siehe unten).
  Jedes Popup zeigt zusätzlich die Adresse als Klartext samt
  "📋 Adresse kopieren"-Knopf. QR-Codes werden rein clientseitig erzeugt
  (kein zusätzlicher Request, keine externe Bibliothek — läuft komplett
  offline im Browser).
- **Sender-Liste** zum manuellen Umschalten
- **⚡ ZAPPEN!** — hast du selbst erkannt, dass gerade geredet wird
  (die Automatik aber noch nicht reagiert hat)? Schaltet sofort weiter.
- **🛑 Zapping-Fehler** — hat die Fingerprint-Erkennung fälschlich
  umgeschaltet (z.B. ein kurzer Sender-übergreifender Sting über einem
  Musikbett)? Wirft den zugrundeliegenden Clip aus der Datenbank, damit
  er nicht weiter fälschlich erkannt wird, UND schaltet zurück zu dem
  Sender, der vor dem Fehl-Switch lief.
- **Sabbelfilter deaktivieren/aktivieren** — schaltet die komplette
  automatische Erkennung für eine Weile aus (z.B. für ein Hörspiel/
  Feature auf einem sonst Musik-Sender), ohne dass RadioSabbelNich
  dazwischenfunkt. Aktueller Zustand direkt am Button erkennbar.
- **🤥 Bullshitometer** — grüner-zu-roter Balken, zeigt den aktuell
  gemessenen Sprache-Wert (VAD-Wahrscheinlichkeit bzw. Heuristik-Votum)
  live in Prozent, aktualisiert alle 3s. Rein informativ (nicht klickbar)
  — friert grau ein, während Nachrichten-Pause läuft oder der
  Sabbelfilter aus ist, weil dann gar nicht klassifiziert wird.
- **🗣 STT-Balken** — gleiche Optik wie das Bullshitometer, zeigt aber
  die rohe Konfidenz des STT-Sprachfilters (siehe eigener Abschnitt
  oben), nicht die von VAD/Heuristik. Friert zusätzlich grau ein
  ("STT aus"), wenn der STT-Filter selbst deaktiviert ist oder noch kein
  frischer Befund vorliegt — unabhängig vom Sabbelfilter-Zustand, da der
  STT-Filter eine eigene An/Aus-Einstellung hat.
- **🔎 Fingerprint-Anzeige** — anders als die beiden Balken oben kein
  Dauerwert, sondern ein kurz aufblitzendes Ereignis: 🔴 "Treffer:
  &lt;Name&gt;" bei einer erkannten Werbung/Jingle (löst den automatischen
  Wechsel aus), 🟢 "Gelernt" bei einem neuen, noch unbekannten Clip.
  Fällt 5s nach dem letzten Ereignis von selbst auf ⚪ "Idle" zurück.

Aktueller Sender, News-Break-Status und Sabbelfilter-Zustand kommen nicht
nur per Intervall-Polling (alle 3s), sondern zusätzlich über einen
Long-Poll (`GET /api/status/wait`) an — ein Senderwechsel oder News-Break-
Übergang erscheint dadurch binnen Millisekunden statt erst beim nächsten
Poll-Tick.
- **Hörer-Übersicht** — wer gerade zuhört (IP/Client/Verbindungsdauer)
- **⚙ Sender verwalten** (`/config`) — Sender hinzufügen, bearbeiten,
  löschen, per Haken (de)aktivieren, gruppiert nach Kategorie
  (Lokal/Regional/National/International/Global/Interstellar/Unsortiert).
  "Unsortiert" ist standardmäßig eingeklappt (zum Ausklappen anklicken) —
  füllt sich nach einem Import mit hunderten Sendern und würde die Seite
  sonst sprengen. Jede Kategorie hat einen "Alle deaktivieren"-Knopf
  (praktisch nach einem Import mit hunderten neuen Sendern). Änderungen
  wirken sofort,
  ohne Neustart.
- **📻 Sender-Import** (auf der Config-Seite) — lädt eine M3U-Playlist
  (Default: die Kodinerds-Kodi-Radioliste) und hört bei jedem Sender ein
  paar Sekunden mit (parallel, mit Fortschrittsanzeige). Übernommen wird
  nur, wer dabei *durchgehend* Audio liefert — inklusive der letzten
  Sekunden des Prüffensters. Das ist bewusst strenger als ein
  ffprobe-Blick beim Verbinden: DASH-/HLS-Quellen schütten gerne einen
  Fragment-Vorrat auf einen Schlag aus und verstummen danach für immer
  (siehe "Umgang mit toten Sendern"). Neue Sender landen
  **deaktiviert** in der Kategorie "Unsortiert" — was tatsächlich in die
  Rotation kommt, entscheidet der Haken auf der Config-Seite. Manueller
  Trigger, kein Auto-Import.
- **🗑 Clip-DB leeren** (auf der Config-Seite) — löscht alle gelernten
  Fingerprint-Clips (nicht die Senderliste), mit Sicherheitsabfrage.
- **💾 Ressourcen-Verbrauch** (auf der Config-Seite) — RAM (Python-Prozess
  + alle ffmpeg-Kindprozesse zusammen sowie einzeln aufgeschlüsselt), CPU,
  Anzahl laufender ffmpeg-Prozesse sowie Festplattenverbrauch von
  Fingerprint-DB, Logdatei (inkl. rotierter Backups) und Whisper-Modell-
  Cache — jeweils nur RadioSabbelNich selbst, nicht der ganze Host. Alle 5s
  aktualisiert.
- **📰 Nachrichten-Pause** (auf der Config-Seite, oberhalb der Senderliste)
  — siehe eigener Abschnitt oben.
- **🗣 STT-Sprachfilter** (auf der Config-Seite) — siehe eigener Abschnitt
  oben.

Der rohe Icecast-Stream bleibt parallel unter `http://<host>:8000/radiosabbelnich.mp3`
erreichbar (z.B. für VLC).

### Als App installieren (PWA)

Die Player-Seite ist als Progressive Web App installierbar — praktisch für
unterwegs, damit "Zappen" nicht erst einen Browser-Tab braucht. Unter
Chrome/Android: Seite öffnen → Menü (⋮) → "Zum Startbildschirm hinzufügen"
(bzw. Chrome zeigt das oft von selbst als Vorschlag an). Die installierte
App läuft dann im eigenen Fenster ohne Adressleiste (`display: standalone`).

Auf der installierten/mobilen Ansicht gibt es zwei große Buttons
"⏮ Zurück"/"Weiter ⏭" für den vorherigen/nächsten Sender in der
konfigurierten Rotationsreihenfolge (alphabetisch, wie die normale
Sender-Liste) — ohne erst durch die ganze Liste scrollen zu müssen. Ein
Klick zeigt den Ziel-Sender **sofort** an (optimistisches UI-Update), die
Bestätigung vom Server kommt normalerweise binnen Millisekunden über
denselben Long-Poll nach, der auch die normale Sender-Liste aktuell hält.

Ein Service Worker (`sw.js`) cached die statische Oberflächen-Hülle
(HTML-Shell, Icons, QR-Bibliothek) fürs Offline-Öffnen — reine Live-Daten
(`/api/*`, der Audio-Stream selbst) sind davon ausdrücklich ausgenommen,
ohne Netzwerkverbindung zeigt die App also weiterhin ehrlich "Verbindung
zum Server verloren" statt einen eingefrorenen alten Zustand. Icons unter
`icon-192.png`/`icon-512.png` sind aktuell schlichte Platzhalter-Grafiken.

## Android-App (eigenständige Zweitumsetzung)

Im Unterverzeichnis [`android-app/`](android-app/) liegt eine **native
Android-App**, die dasselbe Grundprinzip komplett lokal auf dem Handy
umsetzt — Kotlin/ExoPlayer/Vosk statt Python/ffmpeg/Silero, ohne
Web-Wrapper und **ohne jede Abhängigkeit von dieser Docker-Instanz**. Sie
ist seit dem 2026-08-08 im Sinne ihres Fahrplans fertig: Senderverwaltung
mit Kategorien, Watchdog gegen tote Sender, Vorwärmung des nächsten
Senders, M3U-/Kodi-Import, Nachrichten-Pause, Audio-Fingerprinting und
mehrsprachiges STT samt Kalibrierungs-Wizard sind umgesetzt und im
Emulator getestet.

Eigene Doku dort: [`android-app/README.md`](android-app/README.md)
(Funktionsumfang, Installation, bekannte Grenzen — u.a. doppelter
Netzwerkverbrauch durch zwei Dekodierungen, kein HLS/DASH, Verteilung per
eigenem Update-Server statt Play Store).

<img src="pics/android-apk-qr.svg" width="160" alt="QR-Code zum APK-Download">

**Direkt-Download per QR-Code**: mit dem Handy scannen, um die aktuelle
Debug-APK (`radiosabbelnich-latest.apk`) direkt herunterzuladen — der Link
wird bei jedem Android-Build automatisch aktualisiert (siehe
`android-app/README.md`, Abschnitt "Bauen und Testen"). Vor der
Installation muss Android **"Installation aus unbekannten Quellen" für den
verwendeten Browser/Dateimanager erlauben** — kein Play Store, keine
Signaturprüfung über die Debug-Signierung hinaus (siehe oben).

## Architektur

Grafische Gesamtübersicht mit Diagrammen pro Subsystem: `ARCHITECTURE.md`.

| Datei | Zweck |
|---|---|
| `python/radiosabbelnich.py` | Hauptprozess: Stream holen, klassifizieren, umschalten, Icecast-Output |
| `python/speech_detector.py` | Silero-VAD-Wrapper mit Signal-Heuristik-Fallback |
| `python/fingerprint.py` | Audio-Fingerprinting (Constellation-Map-Hashing) in SQLite |
| `python/stations_store.py` | Laden/Speichern/CRUD der Senderliste (`stations.json`) |
| `python/settings_store.py` | Laufzeit-Einstellungen (Puffer-Parameter, Import-URL, `settings.json`) |
| `python/station_import.py` | M3U-Import: laden, parsen, parallel auf dauerhaften Audiofluss prüfen |
| `python/webui.py` | Eingebettetes Web-Interface (Player-Seite + Config-Seite) |
| `python/logging_setup.py` | Zentrale Logging-Konfiguration (Konsole + rotierende Logdatei) |
| `python/news_break.py` | Nachrichten-Pause: Zeitfenster-Logik + zufällige MP3-Auswahl |
| `python/audio_tags.py` | Format-übergreifende Tag-Anzeige (Titel/Interpret/Album/Jahr) via mutagen, geteilt zwischen News-Break/Musik-Player-Live-Anzeige und dem Musik-Scan |
| `python/music_library.py` | Musiksammlung-Modus: Dateien eines Ordners auflisten (rekursiv, bis zu 5 Ebenen) |
| `python/music_scan.py` | Musik-Library-Scan (Phase 1): rekursiver ID3-Scan in eigene SQLite-DB |
| `python/folder_browse.py` | Gemeinsame Breadcrumb-Ordnerauswahl (News-Break-Pfad + Musiksammlung-Root) |
| `python/stt_filter.py` | STT-Sprachfilter: Vosk/Whisper-Engines, austauschbar, Zusatzsignal für die Switch-Entscheidung |
| `python/i18n.py` | Basissprache Englisch fürs Web-Interface + Lader für `language/*.lng`-Sprachpakete (siehe "Sprache des Web-Interfaces") |
| `language/*.lng` | Externe Sprachpakete (z.B. `Deutsch.lng`), Key=Value-Format |
| `python/resource_monitor.py` | Ressourcen-Verbrauch (RAM/CPU/DB-Größe) fürs "💾 Ressourcen-Verbrauch" auf der Config-Seite |
| `web/qrcode.js` | Vendorte QR-Code-Bibliothek (MIT, kazuhikoarase/qrcode-generator) fürs "📱 QR-Code"-Popup |
| `web/manifest.json` | PWA-Manifest (Name, Icons, `display: standalone`) fürs "Zum Startbildschirm hinzufügen" |
| `web/sw.js` | Service Worker: cached die statische Oberflächen-Hülle fürs Offline-Öffnen, kein Audio/API-Caching |
| `pics/icon-192.png`, `pics/icon-512.png` | PWA-Icons fürs Installieren als App (aktuell Platzhalter) |
| `pics/favicon.ico` | Browser-Tab-Icon, quadratische Miniatur von `radiosabbelnich.webp` |
| `pics/radiosabbelnich.webp` | Banner-Grafik auf Player-/Config-Seite und in diesem README |
| `data/stations.json` | Senderliste (Name, URL, Kategorie, aktiv/inaktiv) |
| `data/settings.json` | Laufzeit-Einstellungen, siehe `settings_store.py` |
| `data/fingerprints.db`, `data/fingerprint_clips/` | Fingerprint-Datenbank + gelernte Clip-Mitschnitte |
| `data/logs/` | Rotierende Logdatei (siehe "Logging" unten) |
| `data/news_mp3/`, `data/vosk-model-de/`, `data/whisper_cache/` | Standard-Mountziele für `NEWS_MP3_FOLDER`/`VOSK_MODEL_FOLDER`/faster-whisper-Cache (überschreibbar in `.env`) |
| `data/music_library/` | Standard-Mountziel für `MUSIC_LIBRARY_FOLDER` (überschreibbar in `.env`) |
| `docker-compose.yml` | Icecast + RadioSabbelNich als zwei Services |
| `radiosabbelnich.sh` | Alles-in-einem-Wrapper: `check`/`start`/`stop`/`restart`/`status` (Default) |
| `CHANGELOG.md` | Verdichtete Versionshistorie, neueste zuerst (Details in `SESSION.md`) |

RadioSabbelNich und das Web-Interface laufen im selben Prozess (Web-Server
als Hintergrund-Thread) — kein separater Service, keine IPC nötig, nur
geteilter In-Memory-Zustand.

Audio läuft intern als Stereo-PCM durch (Icecast-Ausgabe), die
Analyse-Pipeline (VAD/Heuristik/Fingerprint) rechnet bewusst nur auf
einem Mono-Downmix, um Rechenzeit zu sparen.

## Setup

```bash
git clone <repo-url> RadioSabbelNich
cd RadioSabbelNich
cp env.example .env      # Passwörter/Hostname eintragen
touch data/fingerprints.db    # muss als Datei existieren, siehe unten
touch data/music_library.db   # dito, für den Musik-Library-Scan (siehe unten)
./radiosabbelnich.sh check   # optional: prüft Docker/.env/MP3-Ordner/Ports vorab
./radiosabbelnich.sh start
```

`./radiosabbelnich.sh check` installiert bei Bedarf Docker, zeigt RAM/HD/
Internet-Status, prüft ob `.env` vollständig ausgefüllt ist (inkl.
Warnung vor unveränderten `env.example`-Platzhaltern), ob der in
`NEWS_MP3_FOLDER` eingetragene Ordner existiert/lesbar ist/MP3s enthält,
und ob `WEBUI_PORT`/`ICECAST_PORT`/`ICECAST_SSL_PORT` frei sind — läuft
bereits RadioSabbelNich selbst auf diesen Ports, gilt das als ok; blockiert
stattdessen ein anderer Docker-Container den Port, schlägt das Skript
eine freie Alternative zum Eintragen in `.env` vor. Reine Diagnose (Exit-
Code 1 bei Problemen), startet selbst nichts. `./radiosabbelnich.sh start`
für den eigentlichen Start prüft schlanker (RAM/HD/Internet, `NEWS_MP3_FOLDER`)
und bricht bei einem kaputten/fehlenden Pfad **vor** `docker compose up`
mit einer klaren Diagnose ab, statt Docker den rohen, oft kryptischen
Mount-Fehler werfen zu lassen — danach `docker compose up -d --build`.

Für den `NEWS_MP3_FOLDER`-Check fragt `radiosabbelnich.sh` bewusst
`docker compose config` statt `.env` selbst zu parsen: eine Shell und
Docker Compose interpretieren z.B. Backslashes in `.env`-Werten
unterschiedlich (siehe `NEWS_MP3_FOLDER` in `env.example`) — ein per Shell
"korrekt" gelesener Pfad kann also genau der kaputte Pfad sein, den Docker
gleich als Mount-Quelle verwendet. `docker compose config` liefert
garantiert den Wert, den Docker tatsächlich benutzt.

Das `touch` ist Pflicht, nicht Kosmetik: `fingerprints.db` hängt in
`docker-compose.yml` als einzelne Datei im Container. Fehlt sie auf dem
Host, legt Docker an der Stelle ein *Verzeichnis* an — SQLite kann sie
dann nicht öffnen und der Container landet in einer Neustartschleife.
(Die DB selbst ist gitignored, ein frischer Clone hat sie also nie.)

Danach `stations.json` nach Belieben anpassen — entweder direkt in der
Datei oder bequemer über `http://<host>:5000/config`.

Für den laufenden Betrieb danach reicht `./radiosabbelnich.sh` (ohne
Argument = `status`, sonst `check`/`start`/`stop`/`restart`) statt sich
`docker compose`-Befehle zu merken — `status` zeigt Container-Zustand,
lokale Port-Erreichbarkeit, RAM/HD sowie den aktuell laufenden Sender/
Track und die Hörerzahl, sofern das Web-Interface erreichbar ist.
Zusätzlich zeigt `status` den konfigurierten `ICECAST_HOSTNAME` (die
Adresse für Hörer von außen, nicht nur `localhost`) und warnt rot, falls
Tailscale ausgeloggt/gestoppt ist (nur bei einem `*.ts.net`-Hostnamen
relevant) oder gar kein Internet/DNS erreichbar ist (per Ping gegen
`hamburg.de` geprüft) — beides Fälle, in denen der Stream lokal noch
normal läuft, aber niemand von außen mehr rankommt. Ein weiterer
Abschnitt zeigt den `NEWS_MP3_FOLDER`-Pfad der Nachrichten-Pause samt
Trefferzahl (schlankere Variante desselben Checks aus `check`).

### Wichtige `.env`-Variablen

| Variable | Bedeutung |
|---|---|
| `ICECAST_ADMIN_USER`/`_PASSWORD` | Icecast-Admin-Login (auch für die Hörer-Abfrage im Web-Interface) |
| `ICECAST_SOURCE_PASSWORD` | Passwort, mit dem RadioSabbelNich selbst auf Icecast pusht |
| `ICECAST_HOSTNAME` | Öffentlicher Hostname für den Icecast-Stream |
| `ICECAST_PORT` | Host-Port für den rohen Icecast-Stream (Default 8000) |
| `ICECAST_LOCATION`/`ICECAST_ADMIN_EMAIL` | Server-Info-Felder in Icecasts `icecast.xml` |
| `WEBUI_PORT` | Host-Port für das Web-Interface (Default 5000) |
| `TLS_CERT_FILE`/`TLS_KEY_FILE` | Host-Pfade zu PEM-Dateien für HTTPS (optional, siehe unten) |
| `ICECAST_SSL_PORT` | Host-Port für den Icecast-Stream per HTTPS (Default 8443) |
| `VOSK_MODEL_FOLDER` | Host-Ordner mit einem entpackten deutschen Vosk-Modell für den STT-Sprachfilter (optional, siehe eigener Abschnitt) |
| `UI_LANGUAGE` | Startsprache des Web-Interfaces: `en` (Basissprache) oder der Code eines Sprachpakets unter `language/` wie `de` (optional, Default `en` — siehe "Sprache des Web-Interfaces") |

### HTTPS/TLS (optional)

Ohne `TLS_CERT_FILE`/`TLS_KEY_FILE` laufen Web-Interface und Icecast-Stream
wie bisher nur über HTTP — kein Pflichtschritt.

Mit einem Zertifikat (z.B. per `tailscale cert <hostname>` erzeugt, ein
`.crt`+`.key`-Paar):

1. Beide Host-Pfade in `.env` eintragen (`TLS_CERT_FILE`/`TLS_KEY_FILE`).
2. `docker compose up -d --build` — der **Icecast-Stream** bekommt dann
   automatisch einen zusätzlichen HTTPS-Port (`ICECAST_SSL_PORT`, Default
   8443) *neben* dem bisherigen HTTP-Port 8000, der unverändert
   weiterläuft — bestehende Hörerverbindungen sind also nie betroffen.
3. Fürs **Web-Interface** zusätzlich unter `/config` → "🔒 HTTPS" den Haken
   setzen (oder `tls_enabled` in `settings.json`) und den Container einmal
   neu starten. **Wichtig:** anders als beim Stream gibt es hier keinen
   Parallelbetrieb — sobald aktiv, ist das Web-Interface nur noch über
   `https://` erreichbar, alte `http://`-Lesezeichen auf Port 5000 laufen
   dann ins Leere.

Icecast selbst muss dafür kurz mit Root-Rechten starten (um die
0600-Zertifikatsdatei lesen zu können) und gibt sie danach intern wieder
ab — Details dazu in `CLAUDE.md`.

## Deploy-Befehle

```bash
# Neu bauen + starten
docker compose up -d --build radiosabbelnich

# Konsole mitlesen (nur die wichtigen Ereignisse)
docker compose logs -f radiosabbelnich

# Vollständiges Debug-Log (VAD-Werte, Fingerprint-Details, HTTP-Requests)
tail -f data/logs/radiosabbelnich.log

# Fingerprint-Mitschnitte anhören (nach einem "Zapping-Fehler"-Verdacht)
ls data/fingerprint_clips/
```

### Logging

Zwei Ziele mit unterschiedlichem Detailgrad:

- **Konsole** (`docker compose logs`): nur Ereignisse, die man im Alltag
  sehen will — Senderwechsel, Fingerprint-Treffer, Warnungen, Fehler.
- **`data/logs/radiosabbelnich.log`**: *immer* auf DEBUG, unabhängig von der
  Konsole. Pro Analysefenster die VAD-Wahrscheinlichkeit bzw. die
  Heuristik-Features, jeder Fingerprint-Vergleich mit Match-Stärke und
  Abstand zur Schwelle, jeder HTTP-Request des Web-Interfaces, jeder
  gestartete/gestorbene Hintergrund-Puffer. Rotierend (5 × 10 MB), auf
  dem Host unter `data/logs/` gemountet — überlebt also Container-Neustarts.

Der Sinn der Trennung: wenn nachts etwas schiefgeht, will man die Details
hinterher lesen können, ohne den Container vorher zufällig im richtigen
Modus gestartet zu haben. `--verbose` schiebt die DEBUG-Zeilen zusätzlich
auf die Konsole, `--log-file ""` schaltet die Datei ab.

## Bekannte Einschränkungen

- Kein Auth auf dem Web-Interface/Config-Seite — siehe Warnung oben,
  unbedingt hinter VPN/Tailscale betreiben.
- Nicht jeder Sender liefert brauchbare "Jetzt läuft"-Metadaten; das
  entscheidet der jeweilige Sender-Betreiber.
- Fingerprint-Erkennung ist ein Best-Effort-Mechanismus (Constellation-
  Map-Hashing mit 2D-Landmarken-Peaks, siehe `fingerprint.py`) — an 26
  echten Mitschnitten aus dem Live-Betrieb verifiziert (0 Fehltreffer
  bei klarer Trennung zu echten Wiederholungen), gelegentliche
  Fehlalarme sind trotzdem nie ganz ausgeschlossen. Dafür gibt's den
  "Zapping-Fehler"-Knopf.
- "⏮ Zurück"/"Weiter ⏭" während einer laufenden Nachrichten-Pause: die
  Pause kennt (bewusst, siehe `CLAUDE.md`) nur den pausierten Sender als
  virtuelle ID, nicht dessen Position in der Rotation — ein Klick während
  der Pause schaltet deshalb zum ersten Sender der Liste statt zum
  eigentlichen Nachbarn des pausierten Senders.

## Zukünftige Features

### Eigene Musik-Library & Kategorisierung (geplant)

Optionaler Modus als Ergänzung zum Stream-Switching: lokale Musiksammlung
scannen, taggen und nach Kategorien abspielbar machen.

- ✅ **Umschaltbar per Toggle** (Radio-Modus vs. Player-Modus, STT/VAD im
  Musik-Modus komplett aus) **und ein minimaler Player** (Play/Stop/
  Zurück/Nächster über einen konfigurierbaren Ordner, seit 2026-08-14
  rekursiv bis zu 5 Unterordner-Ebenen tief, keine Kategorisierung) sind
  umgesetzt — siehe "Player-Modus (Grundgerüst)" weiter oben.
- ✅ **Format-Unterstützung erweitert** (seit 2026-08-12): Scan UND
  Playback laufen jetzt über MP3 hinaus auch für FLAC, OGG (Vorbis),
  M4A (MP4-Container), rohes ADTS-AAC, WAV und APE (Monkey's Audio,
  nur Text-Tags — siehe unten). Playback brauchte keine Änderung
  (ffmpeg ist bereits format-agnostisch), Metadaten/Cover-Extraktion
  in `music_scan.py` dagegen schon: FLAC/OGG/MP4 legen Cover-Bilder an
  komplett unterschiedlichen Stellen ab (kein gemeinsames mutagen-API
  wie bei den Text-Tags), WAV wird von mutagen nicht "easy"-gewrappt
  (Tags mussten über die rohen ID3-Frames gelesen werden), und
  getaggtes rohes AAC wurde von mutagens Auto-Erkennung fälschlich als
  MP3 erkannt und crashte beim Frame-Sync — an echten, per ffmpeg
  erzeugten und per mutagen getaggten Testdateien gefunden und behoben,
  nicht nur aus der Doku übernommen (siehe SESSION.md). **APE-Cover
  werden bewusst nicht extrahiert** (kein standardisiertes Feld dafür,
  kein mutagen-API) und die APE-Unterstützung selbst ist mangels
  Encoder im Image **nicht gegen eine echte `.ape`-Datei verifiziert**
  — Text-Tags sollten laut mutagen-Doku funktionieren, das steht aber
  noch aus.
- ✅ **Phase 1 umgesetzt**: rekursiver Scan der Musiksammlung (`music_scan.py`,
  getrennt vom Player-Modul `music_library.py`) über ID3-Metadaten
  (mutagen) → eigene SQLite-DB `music_library.db` (Artist, Album, Titel,
  Genre, Jahr, Dateipfad, eingebettetes Cover als gecachte Datei falls
  vorhanden). Manueller Trigger per `POST /api/library/scan`
  (`GET /api/library/scan/status` fürs Polling, kein Cronjob) —
  **bewusst noch ohne UI-Anschluss** in dieser Phase, siehe SESSION.md.
  Unveränderte Dateien (mtime+Größe wie beim letzten Scan) werden beim
  erneuten Scan übersprungen, damit ein Re-Scan einer großen Sammlung
  nicht jedes Mal wieder alle Dateien komplett neu einliest.
  - Quelle: Fileserver 192.168.1.10, per SMB auf SERVER gemountet
    unter `/mnt/server/data`
- ✅ **Phase 2 umgesetzt**: schlanker Query-Layer (`music_query.py`, an
  Beets' Query-Syntax angelehnt, aber ohne echten Parser — nur feste
  Artist-/Genre-Teilstring-Filter) direkt an den Musik-Player angebunden.
  Die Kategorie-/Favoriten-Buttons auf `/musik` sind damit größtenteils
  funktionsfähig: **Queen/Pavarotti** filtern per Artist-Teilstring,
  **rock/klassik** per Genre-Teilstring (`LIKE '%rock%'` — reine
  Freitext-Ähnlichkeit, kein exaktes Genre-Mapping, deckt sich nicht mit
  jeder Schreibweise). **schnell/langsam** seit Phase 3 ebenfalls aktiv
  (BPM-Teilstring bzw. -Bereich, siehe unten). Ein Klick löst dieselbe
  `POST /api/music/play`-Route wie der normale Play-Knopf aus
  (optionaler `query`-Body statt eines zweiten Endpoints), ersetzt eine
  laufende Wiedergabe sofort durch die Query-Ergebnisliste und zeigt bei
  0 Treffern eine klare Meldung statt nichts zu tun. Läuft Artist/Titel
  bekannt (aus der DB), zeigt "Jetzt läuft" **Artist – Titel** statt nur
  des Dateinamens — auf `/musik` UND auf der Player-Seite.
- ✅ **Phase 3 (BPM-Teil) umgesetzt**: BPM-Schätzung (`music_bpm.py`,
  aubio statt librosa — deutlich leichtgewichtiger zur Laufzeit, siehe
  CLAUDE.md für den Grund und einen nötigen Build-Patch) läuft im selben
  Scan-Durchlauf wie das ID3-Parsing (gleiche mtime/Größe-Skip-Logik,
  nur ein 60s-Schnipsel statt des kompletten Tracks wird dekodiert:
  ~0,25s/Track gemessen). `schnell` (≥120 BPM) / `langsam` (≤90 BPM)
  sind feste Schwellwerte, dazwischen fällt bei beiden raus — bekannte
  Grenze: Oktavfehler (halbe/doppelte Geschwindigkeit) sind ein
  generisches Problem jeder Beat-Tracking-Methode, an einer echten
  402-Track-Sammlung gemessen fielen dadurch spürbar mehr Tracks unter
  "schnell" als musikalisch stimmen dürfte. Energy-Erkennung/Browse-UI
  aus der ursprünglichen Phase-3-Idee bleiben offen.
- ✅ **Duplikat-Erkennung umgesetzt** (seit 2026-08-12): `music_query.
  find_duplicates()` gruppiert Tracks mit demselben normalisierten
  Artist+Titel-Paar (klein geschrieben, Whitespace getrimmt) — bewusst
  reiner Metadaten-Abgleich, kein Audio-Fingerprint-Vergleich (der
  bräuchte eigenen Analyse-Code wie `music_bpm.py`, auf Nutzerwunsch
  nicht Teil dieser Runde). Erkennt z.B. denselben Song als MP3 UND
  FLAC, aber nicht inhaltlich identisches Audio mit abweichenden Tags.
  Tracks ohne Artist/Titel werden ausgeschlossen (sonst würden untaggte
  Dateien fälschlich als eine riesige Duplikat-Gruppe erscheinen). Über
  `GET /api/library/duplicates` abrufbar (JSON, inkl. Dateigröße pro
  Treffer als Entscheidungshilfe) — **bewusst noch ohne UI-Anschluss**
  und ohne Lösch-Aktion in dieser Phase (Nutzerentscheidung: erst nur
  anzeigen/melden), siehe SESSION.md. An der echten 402-Track-Sammlung
  des Nutzers verifiziert: genau eine echte Duplikat-Gruppe gefunden.
- Enrichment (späterer Baustein, getrennt vom Scan): fehlende
  Cover/Lyrics nachträglich über externe Quellen (z.B.
  MusicBrainz/Cover Art Archive, lrclib.net) ergänzen, langfristiges
  Ziel: alle Tracks mit Cover + Lyrics + Kategorie-Markierung
- Ideenliste (ganz langfristig, unklar ob umgesetzt): eigener
  KI-"Moderator" für Zwischenansagen zu externen Ereignissen (z.B.
  Termine, eingetroffene Mails, Klingel-Events)

Tech-Stack: Python, mutagen, SQLite, ggf. FastAPI für Query-API.
Referenz: Beets (Library-Manager) als Inspiration für
Datenmodell/Query-Sprache, kein 1:1-Einsatz.

### iOS-App (Idee, noch nicht terminiert)

Native iOS-App als Pendant zur bestehenden Android-App (siehe
"Android-App" weiter oben): würde dieselbe Sender-Steuerung und ggf.
Musiksammlung-Bedienung bieten wie die Android-Version, aber mit
Swift/SwiftUI gebaut und über Xcode auf einem Mac kompiliert — ein
eigenständiges Projekt mit eigenem Tech-Stack, analog zu
`android-app/`. Bislang nur Idee, kein Zeitplan.

---

<a id="radiosabbelnich-english-version"></a>

*[🇩🇪 Deutsche Version weiter oben](#radiosabbelnich)*

# RadioSabbelNich (English version)

RadioSabbelNich listens to several internet radio stations at once and
automatically switches away the moment someone starts talking —
presenting, news, ads, jingles. What's left (ideally) is just music.
The currently selected station is re-streamed via Icecast, so you can
listen to it anywhere on your (Tail)net with VLC, in the browser, or
any other streaming client.

## ⚠️ Private use only, behind a VPN — no public deployment

**RadioSabbelNich is explicitly not meant for public deployment.** The
Icecast port (8000) and the web interface port (5000) must never be
exposed directly to the open internet (no port forwarding, no public
reverse proxy) — RadioSabbelNich always runs behind a VPN (Tailscale or
similar), reachable only from devices on your own trusted network. Two
concrete reasons:

- **Resources**: an openly reachable Icecast mount point will sooner
  or later be found (scanners, streaming aggregators, hotlinking) —
  and then potentially half the internet starts pulling bandwidth and
  CPU time uncontrolled, in a way you can never fully rein back in.
- **Copyright**: RadioSabbelNich re-streams other people's licensed radio
  programs. For private personal use inside your own (Tail)net that's
  one thing — made publicly accessible, it's an unlicensed public
  performance of copyrighted content. There is no shortage of law
  firms for whom that's exactly a business model.

The web interface and config page also have no authentication
whatsoever (see below) — another reason "briefly making it publicly
reachable" is a bad idea.

## How detection works

1. **Silero VAD** (a neural network specialized in speech detection)
   continuously classifies ~1-second windows of the current station as
   speech or music. If VAD isn't available (e.g. for environment
   reasons), a simpler signal heuristic automatically takes over
   (zero-crossing rate/spectral flatness/energy modulation).
2. Once speech detection holds up for a few seconds in a row,
   RadioSabbelNich cycles to the next enabled station until music is
   playing again.
3. In parallel, **audio fingerprinting** runs (a Shazam-style
   constellation-map approach): detected speech clips are hashed and
   compared against a SQLite database of clips already heard. If a
   clip is already known (e.g. a recurring ad spot or station jingle),
   RadioSabbelNich switches immediately instead of waiting out the full
   speech-detection time.

Neither mechanism is perfect — that's what the correction buttons in
the web interface are for (see below).

## Handling dead stations (watchdog)

Not every station URL stays playable forever — imported lists contain
stale entries, and even a working station can go silent for minutes at
a time. So this doesn't stall playback entirely:

- If the **current** station delivers nothing for three analysis
  windows in a row, it's pulled from rotation for 5 minutes and
  RadioSabbelNich automatically switches on (`STREAM_FAILURE_LIMIT`/
  `STATION_DEAD_COOLDOWN` in `radiosabbelnich.py`).
- If a **background buffer** dies, its station is immediately put on
  the same block list instead of being reconnected every second.
- Blocked stations are skipped during automatic switching and aren't
  buffered. After the 5 minutes are up they automatically get another
  chance — a manual click in the web interface lifts the block right
  away.

Without this watchdog, a single dead station could stall the entire
player: this actually happened with an imported DASH URL that ffprobe
correctly flagged as "has audio" during import, but that ffmpeg can't
play continuously — 3569 reconnect attempts over 8.5 hours, Icecast
mount silent the whole time.

## Look-ahead buffering & playout delay

RadioSabbelNich keeps the next stations in rotation order running in the
background and buffers the last `prebuffer_seconds` seconds of each
(default 10s, configurable under `/config`, takes effect immediately, no
restart needed). That buffer serves two purposes at once:

1. **Seamless switching**: switching to a buffered station (automatically
   or manually) takes over the already-running source immediately instead
   of reconnecting — no reconnect stutter.
2. **Listener delay for speech detection**: the same buffer also delays
   the broadcast of the CURRENTLY playing station by exactly
   `prebuffer_seconds`. Speech detection (VAD/heuristic/STT/fingerprint)
   runs on freshly arrived audio that the listener only gets after this
   delay — talk/ads can therefore be detected and switched away from
   BEFORE it reaches the listener, not just after.  Costs extra
   bandwidth/CPU (one extra ffmpeg process per buffered station, running
   alongside the current one; the default of 5 stations × 10s is
   uncritical on typical home hardware).

A switch takes over the target buffer's entire window sequence in one
go — output continues afterwards in the same one-second cadence as
before: no gap, no duplicated audio, no cumulative drift from real time
(verified: the delay stays constant, it doesn't grow with every zap).

**Limitation**: if a switch lands on a station that is NOT currently
buffered (e.g. a manual click outside the next `prebuffer_count` stations
in rotation, or an emergency switch because all buffered candidates are
themselves dead), that station plays without delay — instant reaction to
the click, but without the look-ahead benefit until the next switch hits
a buffered station again. A gapless transition from 0 to full delay isn't
possible without time-stretching/pitch manipulation, so it's deliberately
not attempted.

**News break timing shifts accordingly**: if the current station is
running with full delay, the news-break MP3 reaches the listener up to
`prebuffer_seconds` later than the actual top/bottom of the hour — the
window length itself (`window_minutes`) is unaffected.

## News break

Practically every radio station reads the news on the hour and half
hour. Instead, RadioSabbelNich can play a random MP3 from a local folder
for a short time window (e.g. your own jingles/music from an SMB
mount) — afterwards it automatically resumes the paused station, as
normal.

Configured via the `news_break` block in `settings.json`, adjustable
through the "📰 Nachrichten-Pause" form section above the station list
on the config page (`/config`), or directly via the API:

```json
"news_break": {
  "enabled": false,
  "mp3_folder": "/app/news_mp3",
  "window_minutes": 2.0,
  "enabled_hours": null
}
```

- **`enabled`** — feature on/off.
- **`mp3_folder`** — a container-internal path (not the host path!), set
  on the config page via a breadcrumb folder picker (click through the
  subfolders of `/app/news_mp3` instead of typing the path). The
  actual host folder is mounted in from outside via `NEWS_MP3_FOLDER`
  in `.env` (see `docker-compose.yml`), typically an SMB mount — that
  needs a container restart, not a field on the config page. Since
  2026-08-14 the picker also searches subfolders, up to 5 levels deep.
  Folder missing/empty/no MP3s (including in subfolders)/unreadable →
  the feature is simply skipped for that time window, with a log entry,
  no error. Below the field, the config
  page shows the real host path read-only for reference (passed
  through from `NEWS_MP3_FOLDER`) — the container otherwise has no way
  to know it, Docker translates host→container path only once at
  startup.
- **`window_minutes`** — how many minutes before/after :00 and :30 the
  feature is active.
- **`enabled_hours`** — optional `[start, end]`, e.g. `[6, 22]` for
  "only 6am–10pm"; `null` = around the clock. No overnight wraparound
  (22–6 is not supported).

Alternatively, set it directly via the API (e.g. for scripts):
```bash
curl -X POST http://<host>:5000/api/config/settings \
     -H 'Content-Type: application/json' \
     -d '{"news_break_enabled": true, "news_break_window_minutes": 2}'
```

A time window is served at most once — if an MP3 finishes before the
remaining window is over, another random MP3 is automatically loaded
(no immediate repeat, as long as the folder has more than one file)
until `window_minutes` has elapsed. The MP3 currently playing is
**always played to the end**, even if `window_minutes` runs out while
it's playing — the break may end up running a bit longer than
configured rather than cutting a track off mid-playback. Only after
that does it automatically return to the paused station. A manual
station switch during the break
cancels it immediately (a manual decision beats automation, as
everywhere else in RadioSabbelNich). During the break, automatic speech
detection (VAD/heuristic/fingerprint) is also paused — the MP3 itself
may well contain speech, and that shouldn't be misread as "presenting"
on the actual station. On the radio home page, a tag display (since
2026-08-15, via mutagen, format-agnostic) shows the playing MP3's
title/artist/album/year instead of just the filename — see "Player
mode" below for details (same display, same fallback behavior).

## Player mode (foundation)

First implementation step of the music library idea described further
below under "Future features": a **standalone, persisted mode**
alongside normal radio operation — switchable on both the radio and the
player page via a clearly visible toggle ("📻 Radio" / "🎵 Player") (the
feature was called "Music library" until 2026-08-13 — internally, in
`settings.json`/code, the mode is still named `music`, only the
web interface label was simplified). In player mode, all automatic
detection (VAD/heuristic/STT/fingerprint) is off, not just paused —
only local music plays, nothing gets analyzed. The mode survives a
container restart (stored in `settings.json`) — since 2026-08-15 it also
**automatically starts playback** (first track of the configured
folder), both after a restart with the player mode already saved and on
a manual switch from radio to player. Before, playback stayed inactive
in both cases until ▶ was tapped manually — the mode itself was
correctly remembered, but no sound played until someone actively hit
play.

On the standalone `/musik` page ("🎵 Player"):

- The selected music folder is displayed — since 2026-08-13 the **real
  host path** (translated server-side from the container path, via
  `MUSIC_LIBRARY_FOLDER` from `.env`), previously it showed the
  technically correct but meaningless-to-the-user container path
  (`/app/music_library/...`). A "Change path" button leads to the
  actual folder picker on the config page (see below).
- Two button groups, "Categories" (schnell/langsam/rock/klassik) and
  "Favorites" (Queen/Pavarotti) — currently pure placeholders with no
  function; real filtering (categories on metadata/tags like BPM/genre,
  favorites on the artist tag) arrives with the music scan (roadmap
  phase 1 below).
- A big play/stop button plus back/next — plays the music files in the
  configured folder including subfolders (since 2026-08-14, up to
  5 levels deep), alphabetically, looping forever until stop is pressed.
  Since 2026-08-13 this single button is
  also the only visible control for actually listening: a hidden
  `<audio>` element (no control bar of its own) automatically follows
  the playback state. Before, there was also a native browser player
  with its own play button reacting independently from the big button —
  two "play" buttons that didn't know about each other and got in each
  other's way.
- No more banner image on this page (since 2026-08-13, a tidier,
  standalone look instead of the radio page's elements).
- **Tag display** (since 2026-08-15): below the filename/progress line
  ("Track (i/total)"), a second/third line shows the metadata read via
  mutagen — "Artist – Title" and "Album (Year)", format-agnostic (MP3,
  FLAC, OGG, M4A/AAC, WAV, APE). No title tag → falls back to the
  filename; missing album/year → that second line is omitted entirely
  instead of a placeholder like "Album: –". The same display runs on the
  radio home page whenever a news-break MP3 is playing (see "News break"
  above).

The music folder is set on the config page, just like the news break MP3
folder, via a **breadcrumb folder picker**: click through the
subfolders of the directory mounted via `MUSIC_LIBRARY_FOLDER` (`.env`,
same pattern as `NEWS_MP3_FOLDER`) instead of typing a path. Both
fields (news break folder, music library root) use the same component
but save independently of each other.

## STT speech filter

Silero VAD/the signal heuristic detect "is there a human voice here" —
sung music often counts as a false positive there too. The STT speech
filter (`stt_filter.py`) instead listens via speech-to-text for whether
*coherent text in the expected language* is currently audible, and
feeds that in as an additional signal for the switch decision.

Two interchangeable engines, never loaded at the same time:

- **Vosk** — a small Kaldi model, lightweight and usable on a Raspberry
  Pi. Needs its own model **per language**.
- **Whisper** (via `faster-whisper`) — more accurate, but noticeably
  more resource-hungry, even as the "tiny" model. A single loaded model
  covers any number of languages (the language code is just passed per
  analysis) — with Whisper, an extra language costs no extra RAM.

### Multi-language: language per station category

Which language is checked for a station depends on its **category**
(Local/Regional/National/International/…, see "Web interface" above) —
not the individual station. The config page has two new sections for
this below "🗣 STT-Sprachfilter":

- **🌐 STT-Sprachen** — sets up which languages are available at all:
  language code (free text, e.g. `en`, `fr` — no fixed list, since Vosk
  models have to be sourced manually anyway), a model path for engine
  "Vosk", plus an (empirically determined, see below) confidence
  threshold. Entering an existing language code again updates it instead
  of duplicating it. Each row also shows the load state (✅ loaded / ⚠
  error message / not loaded yet) — with Vosk, each language model is
  loaded **lazily** on its first actual sample, not already when saved.
- **🏷 Kategorie-Sprachen** — assigns one of the languages configured
  above to each of the fixed categories. Categories without a selection
  default to German (`de`).

With Vosk, never more than **2 language models are loaded at once** (for
RAM reasons, see weak hardware/Pi) — with more configured languages, the
least recently used one is evicted automatically (LRU) and reloaded on
next demand. If a station's expected language changes (e.g. through a
category change), a not-yet-expired STT reading from the PREVIOUS
language is discarded instead of being reused incorrectly.

**Mounting additional Vosk models**: the bundled `VOSK_MODEL_FOLDER`
mount in `docker-compose.yml` covers exactly ONE model (default:
German, `/app/vosk-model-de`). For another language, add your own extra
line to `docker-compose.yml`, e.g.:

```yaml
      - ${VOSK_MODEL_FOLDER_EN:-./data/vosk-model-en}:/app/vosk-model-en:ro
```

and enter the resulting container path (`/app/vosk-model-en`) as the
model path under "🌐 STT-Sprachen" — then `docker compose up -d --build
radiosabbelnich` so the new mount takes effect.

### Calibration wizard

Instead of guessing `confidence_threshold`, the config page has a "🧪
Schwellwert-Kalibrierung" section below "🏷 Kategorie-Sprachen" — it
reproduces the same method originally used to derive the German default
(0.75, see above), just guided instead of reading it off the logs by
hand:

1. Enter a language code (for Vosk, the language must already be set up
   with a model path under "🌐 STT-Sprachen" first; not needed for
   Whisper) and click "🧪 Start calibration". Requirement: the STT filter
   and chatter filter must be active (otherwise STT doesn't sample at
   all, see above).
2. Manually switch to a station with guaranteed real speech in that
   language on the player page (e.g. a news channel) and let it run for
   a few minutes — the wizard page shows the sample count as well as
   confidence min/max/average live (polled every 2s).
3. Switch to the "🎵 Musik-Stufe" and manually switch to a music station
   in the same language, again let it collect for a few minutes.
4. Once both stages have samples, a suggestion appears (the boundary
   between the highest measured music value and the lowest measured
   speech value, with a safety margin toward the speech side) — "Apply"
   saves it directly as that language's `confidence_threshold`. If speech
   and music don't separate cleanly in the measured sample (overlap), the
   suggestion shows a warning instead of being applied silently — usually
   more samples or a different test station help.

Samples where STT recognized no text at all (pause/jingle/ad break
during the speech stage, a purely instrumental passage during the music
stage) do NOT count toward the statistics — empty text means "no
judgment formed", not "recognized with low confidence". Important when
picking the music-stage station: many commercial radio stations have a
substantial spoken share (ads, DJ links between songs) — that alone can
cause an unclean separation, with no recognition error involved. A
station with as little talk as possible gives better results.

**Important**: calibration itself never switches anything — which
station is playing is decided exclusively on the player page. While a
calibration is running, automatic station switching is also completely
paused (not just for the calibration language), so that an STT result
distorted by the forced test language can't trigger a switch mid-
calibration — the running station stays put until calibration ends.

### Configuration in detail

Configured via the `stt_filter` block in `settings.json` (languages
themselves managed via `set_stt_language()`/`delete_stt_language()`,
don't edit the block directly):

```json
"stt_filter": {
  "enabled": false,
  "engine": "vosk",
  "whisper_model_size": "tiny",
  "sample_interval_seconds": 8.0,
  "combine_mode": "and",
  "languages": {
    "de": {"vosk_model_path": "/app/vosk-model-de", "confidence_threshold": 0.75}
  },
  "category_languages": {}
}
```

- **`enabled`** — feature on/off.
- **`engine`** — `"vosk"` or `"whisper"`, GLOBAL for all configured
  languages at once (see above for why the two are never mixed).
- **`whisper_model_size`** — e.g. `"tiny"`, `"base"` (see the
  faster-whisper docs for further sizes), also global. Models are
  automatically downloaded from HuggingFace on first use and cached in a
  persistent volume (no manual download needed, but first activation
  needs internet access and some time).
- **`sample_interval_seconds`** — how often a short clip (~3s) is
  taken for analysis. Runs continuously in the background, independent
  of the current VAD result (never blocks the main loop).
- **`languages.<code>.vosk_model_path`** — a container-internal path
  (not the host path!) to an unpacked Vosk model for that language, see
  above. German models are available at
  [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models) —
  `vosk-model-small-de-0.15` (~45 MB) for weaker hardware/Pi,
  `vosk-model-de-0.21` (~1 GB) for more accuracy; for other languages,
  look for the matching model on the same site.
- **`languages.<code>.confidence_threshold`** — the (best-effort)
  confidence above which a sample counts as "coherent text in that
  language". The de default (0.75) is **empirically measured**, not
  guessed: 10 live clips from Deutschlandfunk (speech) never dropped
  below 0.83 confidence, 30 live clips from three Schlager stations
  (sung German music) averaged 0.38 — 0.75 sits safely below the speech
  minimum. The same method applies to any further language: listen in
  for a few minutes against a known speech AND a known music station in
  that language; detected text/confidence values are logged to
  `logs/radiosabbelnich.log` for this.
- **`category_languages`** — category → language code (see above),
  managed via the "🏷 Kategorie-Sprachen" table.
- **`combine_mode`** — how the STT result is combined with VAD/
  heuristic: `"and"` (default) requires both to say "speech" — this
  lets a large share of music sung in that language (VAD says yes, STT
  usually detects no coherent text) correctly pass through as music.
  **Not a silver bullet**: with clearly/slowly sung German Schlager,
  Vosk occasionally detects short, grammatically plausible word
  fragments with high confidence (~20% of Schlager clips in the test
  above despite the 0.75 threshold) — `"and"` noticeably reduces false
  switches on sung music, but doesn't eliminate them 100%. `"or"` is
  enough if either signal says "speech" — catches more actual
  presenting, but is again more prone to that same singing case.

Model not found or load error → only that language stays ineffective
(log entry, load state also visible per language on the config page),
RadioSabbelNich keeps running normally with the remaining languages/
stations. A crash of the engine on a single sample only skips that one
sample, not the main process.

## Web interface language

The player and config pages are available in English (the base
language, built into the code) and German (an external "language
pack", see below). Switch it under `/config` → "🌐 Sprache" (takes
effect within about a second, no restart needed — the page reloads
automatically after saving). The starting value for a fresh install
comes from `UI_LANGUAGE` in `.env` (default `en`, leaving it empty
works too) — once saved via the config page, that setting always wins
afterwards, even after restarting the container.

Everything visible in the browser is translated (labels, buttons,
messages). The log file and server-side error messages (e.g. for an
invalid setting) stay German regardless of this setting.

**Adding more languages**: any language besides English lives in its
own file under the `language/` folder (e.g. `language/Deutsch.lng` for
German) — similar to a Windows language pack. Format: simple
`Key=Value`, one line per text, `#` starts a comment. Two lines are
required at the top of the file:

```
#!code=de
#!name=Deutsch
```

`code` is the machine code (shows up in `UI_LANGUAGE`/the saved
setting), `name` is the display name in the language dropdown. The
file doesn't need to be complete — a missing text automatically falls
back to the English base instead of crashing. A new `.lng` file takes
effect after `docker compose up -d --build radiosabbelnich` (language
files are baked into the image at build time like the rest of the
code, no bind mount).

## Web interface

Reachable at `http://<host>:5000/`:

- The currently deployed version is shown in small text below the
  banner image (`VERSION` at the repo root, see version tracking in
  `CLAUDE.md`) — on both the player and config page.
- **⚙ top right** (fixed position, stays visible while scrolling) —
  leads to the config page (`/config`).
- **📻 Radio / 🎵 Player** — mode toggle at the top of the radio and the
  player page (see the dedicated section further up). Clicking the
  other mode switches to it AND jumps to the matching page (that's
  where the corresponding controls live).
- **Current station + "now playing"** — title/artist, if the station
  provides ICY metadata or a known alternative source
- **Embedded player** — listen right in the browser, no extra
  app/client needed
- **▶️ VLC / 📱 Phone** — two icons below the "now playing" box each
  open a QR code popup: **▶️ VLC** for the stream URL to enter into an
  external player (by default derived automatically from the address
  the page is currently being accessed with; can be pinned on the
  config page under "🔗 Streaming-Adresse" if the actual public address
  differs), **📱 Phone** for the address of this web interface itself
  (handy for opening the page on a second device or installing it as a
  PWA, see below). Each popup also shows the address as plain text
  with a "📋 Copy address" button. QR codes are generated entirely
  client-side (no extra request, no external library — works fully
  offline in the browser).
- **Station list** for manual switching
- **⚡ ZAP!** — noticed someone talking yourself (before automation
  reacted)? Switches immediately.
- **🛑 Zap error** — did fingerprint detection switch away incorrectly
  (e.g. a short cross-station sting over a music bed)? Throws the
  underlying clip out of the database so it won't be misdetected
  again, AND switches back to the station that was playing before the
  false switch.
- **Disable/enable chatter filter** — turns off all automatic
  detection for a while (e.g. for a radio drama/feature on an
  otherwise music station) without RadioSabbelNich interfering. Current
  state is visible directly on the button.
- **🤥 Bullshit-o-meter** — a green-to-red bar showing the currently
  measured speech value (VAD probability or heuristic vote) live in
  percent, updated every 3s. Purely informational (not clickable) —
  freezes gray while a news break is running or the chatter filter is
  off, because nothing is being classified then.
- **🗣 STT bar** — same look as the bullshit-o-meter, but shows the raw
  confidence of the STT speech filter (see its own section above)
  instead of VAD/heuristic. Also freezes gray ("STT off") when the STT
  filter itself is disabled or no fresh reading is available yet —
  independent of the chatter filter state, since the STT filter has its
  own on/off setting.
- **🔎 Fingerprint indicator** — unlike the two bars above, not a
  continuous value but a briefly flashing event: 🔴 "Match: &lt;name&gt;"
  on a recognized ad/jingle (triggers the automatic switch), 🟢 "Learned"
  on a new, previously unknown clip. Falls back to ⚪ "Idle" on its own
  5s after the last event.

Current station, news-break status and chatter-filter state arrive not
just via interval polling (every 3s) but additionally via long polling
(`GET /api/status/wait`) — a station switch or news-break transition
appears within milliseconds instead of waiting for the next poll tick.
- **Listener overview** — who's currently listening (IP/client/
  connection duration)
- **⚙ Manage stations** (`/config`) — add, edit, delete stations,
  (de)activate via checkbox, grouped by category (Local/Regional/
  National/International/Global/Interstellar/Unsorted). "Unsorted" is
  collapsed by default (click to expand) — it fills up with hundreds
  of stations after an import and would otherwise blow up the page.
  Each category has a "disable all" button (handy after importing
  hundreds of new stations). Changes take effect immediately, no
  restart needed.
- **📻 Station import** (on the config page) — downloads an M3U
  playlist (default: the Kodinerds Kodi radio list) and listens to
  each station for a few seconds (in parallel, with a progress
  indicator). Only stations that deliver audio *continuously* —
  including the last seconds of the check window — are kept. This is
  deliberately stricter than an ffprobe glance on connect: DASH/HLS
  sources like to dump a fragment supply all at once and then go
  silent forever (see "Handling dead stations"). New stations land
  **disabled** in the "Unsorted" category — what actually joins the
  rotation is decided via the checkbox on the config page. Manual
  trigger, no auto-import.
- **🗑 Clear clip DB** (on the config page) — deletes all learned
  fingerprint clips (not the station list), with a confirmation
  prompt.
- **💾 Resource usage** (on the config page) — RAM (Python process +
  all ffmpeg child processes combined, plus a breakdown), CPU, number
  of running ffmpeg processes, and disk usage of the fingerprint DB,
  log file (including rotated backups), and Whisper model cache — all
  for RadioSabbelNich itself, not the whole host. Refreshed every 5s.
- **📰 News break** (on the config page, above the station list) — see
  its own section above.
- **🗣 STT speech filter** (on the config page) — see its own section
  above.

The raw Icecast stream also remains reachable in parallel at
`http://<host>:8000/radiosabbelnich.mp3` (e.g. for VLC).

### Installing as an app (PWA)

The player page can be installed as a Progressive Web App — handy on
the go, so "zapping" doesn't need a browser tab first. On Chrome/
Android: open the page → menu (⋮) → "Add to home screen" (Chrome often
suggests this on its own). The installed app then runs in its own
window without an address bar (`display: standalone`).

On the installed/mobile view there are two large buttons "⏮ Back"/
"Next ⏭" for the previous/next station in the configured rotation
order (alphabetical, like the normal station list) — without having to
scroll through the whole list. A tap shows the target station
**immediately** (optimistic UI update); server confirmation normally
follows within milliseconds via the same long poll that also keeps the
regular station list current.

A service worker (`sw.js`) caches the static UI shell (HTML shell,
icons, QR library) for offline opening — actual live data (`/api/*`,
the audio stream itself) is explicitly excluded, so without a network
connection the app still honestly shows "connection to server lost"
instead of a frozen stale state. The icons at `icon-192.png`/
`icon-512.png` are currently plain placeholder graphics.

## Android app (separate second implementation)

The [`android-app/`](android-app/) subdirectory contains a **native Android
app** that implements the same idea entirely on the phone — Kotlin/ExoPlayer/
Vosk instead of Python/ffmpeg/Silero, no web wrapper and **no dependency on
this Docker instance whatsoever**. As of 2026-08-08 it is complete with
respect to its own roadmap: station management with categories, a watchdog
for dead stations, pre-warming of the next station, M3U/Kodi import, the
news break, audio fingerprinting and multilingual STT including a
calibration wizard are all implemented and tested in the emulator.

It has its own documentation (in German):
[`android-app/README.md`](android-app/README.md) — feature list,
installation and known limitations (among them doubled network usage from
two independent decodes, no HLS/DASH, and distribution via a self-hosted
update server rather than the Play Store).

<img src="pics/android-apk-qr.svg" width="160" alt="QR code for the APK download">

**Direct download via QR code**: scan with your phone to download the
current debug APK (`radiosabbelnich-latest.apk`) directly — the link is
updated automatically with every Android build (see
`android-app/README.md`, "Bauen und Testen" section). Android must be
allowed to **"install from unknown sources" for whichever browser/file
manager you use** before installing — no Play Store, no signature check
beyond the debug signing (see above).

## Architecture

| File | Purpose |
|---|---|
| `python/radiosabbelnich.py` | Main process: fetch stream, classify, switch, Icecast output |
| `python/speech_detector.py` | Silero VAD wrapper with signal-heuristic fallback |
| `python/fingerprint.py` | Audio fingerprinting (constellation-map hashing) in SQLite |
| `python/stations_store.py` | Load/save/CRUD for the station list (`stations.json`) |
| `python/settings_store.py` | Runtime settings (buffer parameters, import URL, `settings.json`) |
| `python/station_import.py` | M3U import: download, parse, check for continuous audio in parallel |
| `python/webui.py` | Embedded web interface (player page + config page) |
| `python/logging_setup.py` | Central logging config (console + rotating log file) |
| `python/news_break.py` | News break: time-window logic + random MP3 selection |
| `python/audio_tags.py` | Format-agnostic tag display (title/artist/album/year) via mutagen, shared between the news-break/music-player live display and the music scan |
| `python/music_library.py` | Music library mode: list a folder's files (recursive, up to 5 levels) |
| `python/music_scan.py` | Music library scan (phase 1): recursive ID3 scan into its own SQLite DB |
| `python/folder_browse.py` | Shared breadcrumb folder picker (news break path + music library root) |
| `python/stt_filter.py` | STT speech filter: interchangeable Vosk/Whisper engines, additional signal for the switch decision |
| `python/i18n.py` | English base language for the web interface + loader for `language/*.lng` language packs (see "Web interface language") |
| `language/*.lng` | External language packs (e.g. `Deutsch.lng`), Key=Value format |
| `python/resource_monitor.py` | Resource usage (RAM/CPU/DB size) for the "💾 Resource usage" section on the config page |
| `web/qrcode.js` | Vendored QR code library (MIT, kazuhikoarase/qrcode-generator) for the "📱 QR code" popup |
| `web/manifest.json` | PWA manifest (name, icons, `display: standalone`) for "Add to home screen" |
| `web/sw.js` | Service worker: caches the static UI shell for offline opening, no audio/API caching |
| `pics/icon-192.png`, `pics/icon-512.png` | PWA icons for installing as an app (currently placeholders) |
| `pics/favicon.ico` | Browser tab icon, a square thumbnail of `radiosabbelnich.webp` |
| `pics/radiosabbelnich.webp` | Banner graphic on the player/config page and in this README |
| `data/stations.json` | Station list (name, URL, category, active/inactive) |
| `data/settings.json` | Runtime settings, see `settings_store.py` |
| `data/fingerprints.db`, `data/fingerprint_clips/` | Fingerprint database + learned clip recordings |
| `data/logs/` | Rotating log file (see "Logging" below) |
| `data/news_mp3/`, `data/vosk-model-de/`, `data/whisper_cache/` | Default mount targets for `NEWS_MP3_FOLDER`/`VOSK_MODEL_FOLDER`/the faster-whisper cache (overridable in `.env`) |
| `data/music_library/` | Default mount target for `MUSIC_LIBRARY_FOLDER` (overridable in `.env`) |
| `docker-compose.yml` | Icecast + RadioSabbelNich as two services |
| `radiosabbelnich.sh` | All-in-one wrapper: `check`/`start`/`stop`/`restart`/`status` (default) |
| `CHANGELOG.md` | Condensed version history, newest first (details in `SESSION.md`) |

RadioSabbelNich and the web interface run in the same process (web server
as a background thread) — no separate service, no IPC needed, just
shared in-memory state.

Audio flows internally as stereo PCM (Icecast output); the analysis
pipeline (VAD/heuristic/fingerprint) deliberately only computes on a
mono downmix to save CPU time.

## Setup

```bash
git clone <repo-url> RadioSabbelNich
cd RadioSabbelNich
cp env.example .env      # enter passwords/hostname
touch data/fingerprints.db    # must exist as a file, see below
touch data/music_library.db   # same, for the music library scan (see below)
./radiosabbelnich.sh check   # optional: pre-checks Docker/.env/MP3 folder/ports
./radiosabbelnich.sh start
```

`./radiosabbelnich.sh check` installs Docker if needed, shows RAM/disk/
internet status, checks whether `.env` is fully filled in (including a
warning about unchanged `env.example` placeholders), whether the
folder set in `NEWS_MP3_FOLDER` exists/is readable/contains MP3s, and
whether `WEBUI_PORT`/`ICECAST_PORT`/`ICECAST_SSL_PORT` are free — if
RadioSabbelNich itself is already running on those ports, that counts as
fine; if a different Docker container is blocking the port instead,
the script suggests a free alternative to enter in `.env`. Pure
diagnostics (exit code 1 on problems), starts nothing itself.
`./radiosabbelnich.sh start` does the actual start with a leaner check
(RAM/disk/internet, `NEWS_MP3_FOLDER`), then `docker compose up -d
--build`.

The `touch` is mandatory, not cosmetic: `fingerprints.db` is mounted in
`docker-compose.yml` as a single file inside the container. If it's
missing on the host, Docker creates a *directory* there instead —
SQLite then can't open it and the container ends up in a restart loop.
(The DB itself is gitignored, so a fresh clone never has it.)

Afterwards, adjust `stations.json` as you like — either directly in
the file or more conveniently via `http://<host>:5000/config`.

For day-to-day operation afterwards, `./radiosabbelnich.sh` (no
argument = `status`, otherwise `check`/`start`/`stop`/`restart`) saves
you from remembering `docker compose` commands — `status` shows
container state, local port reachability, RAM/disk, plus the currently
playing station/track and listener count, if the web interface is
reachable. It also shows the configured `ICECAST_HOSTNAME` (the address
listeners use from outside, not just `localhost`) and prints a red
warning if Tailscale is logged out/stopped (only relevant for a
`*.ts.net` hostname) or if there's no internet/DNS at all (checked via
a ping to `hamburg.de`) — both cases where the stream still runs fine
locally but nobody outside can reach it anymore. Another section shows
the news break's `NEWS_MP3_FOLDER` path along with a file count (a
leaner version of the same check from `check`).

### Important `.env` variables

| Variable | Meaning |
|---|---|
| `ICECAST_ADMIN_USER`/`_PASSWORD` | Icecast admin login (also used for the listener query in the web interface) |
| `ICECAST_SOURCE_PASSWORD` | Password RadioSabbelNich itself uses to push to Icecast |
| `ICECAST_HOSTNAME` | Public hostname for the Icecast stream |
| `ICECAST_PORT` | Host port for the raw Icecast stream (default 8000) |
| `ICECAST_LOCATION`/`ICECAST_ADMIN_EMAIL` | Server info fields in Icecast's `icecast.xml` |
| `WEBUI_PORT` | Host port for the web interface (default 5000) |
| `TLS_CERT_FILE`/`TLS_KEY_FILE` | Host paths to PEM files for HTTPS (optional, see below) |
| `ICECAST_SSL_PORT` | Host port for the Icecast stream over HTTPS (default 8443) |
| `VOSK_MODEL_FOLDER` | Host folder with an unpacked German Vosk model for the STT speech filter (optional, see its own section) |
| `UI_LANGUAGE` | Starting language of the web interface: `en` (base language) or the code of a language pack under `language/` such as `de` (optional, default `en` — see "Web interface language") |

### HTTPS/TLS (optional)

Without `TLS_CERT_FILE`/`TLS_KEY_FILE`, the web interface and Icecast
stream keep running over plain HTTP as before — not a required step.

With a certificate (e.g. generated via `tailscale cert <hostname>`, a
`.crt`+`.key` pair):

1. Enter both host paths in `.env` (`TLS_CERT_FILE`/`TLS_KEY_FILE`).
2. `docker compose up -d --build` — the **Icecast stream** then
   automatically gets an additional HTTPS port (`ICECAST_SSL_PORT`,
   default 8443) *alongside* the existing HTTP port 8000, which keeps
   running unchanged — existing listener connections are never
   affected.
3. For the **web interface**, additionally check the box under
   `/config` → "🔒 HTTPS" (or set `tls_enabled` in `settings.json`) and
   restart the container once. **Important:** unlike the stream, there
   is no parallel operation here — once enabled, the web interface is
   only reachable via `https://`, old `http://` bookmarks on port 5000
   then lead nowhere.

Icecast itself has to briefly start with root privileges for this (to
be able to read the 0600 certificate file) and drops them again
internally afterwards — details on that are in `CLAUDE.md`.

## Deploy commands

```bash
# Rebuild + restart
docker compose up -d --build radiosabbelnich

# Follow the console (only the important events)
docker compose logs -f radiosabbelnich

# Full debug log (VAD values, fingerprint details, HTTP requests)
tail -f data/logs/radiosabbelnich.log

# Listen to fingerprint recordings (after a suspected "zap error")
ls data/fingerprint_clips/
```

### Logging

Two destinations with different levels of detail:

- **Console** (`docker compose logs`): only the events you want to see
  day-to-day — station switches, fingerprint matches, warnings,
  errors.
- **`data/logs/radiosabbelnich.log`**: *always* at DEBUG, independent of the
  console. Per analysis window, the VAD probability or heuristic
  features, every fingerprint comparison with match strength and
  distance to the threshold, every HTTP request to the web interface,
  every background buffer started/died. Rotating (5 × 10 MB), mounted
  on the host under `data/logs/` — so it survives container restarts.

The point of the split: if something goes wrong overnight, you want to
be able to read the details afterwards, without having had to
accidentally start the container in the right mode beforehand.
`--verbose` additionally pushes the DEBUG lines to the console,
`--log-file ""` turns the file off.

## Known limitations

- No auth on the web interface/config page — see the warning above,
  make sure to run it behind a VPN/Tailscale.
- Not every station delivers usable "now playing" metadata; that's up
  to the respective station operator.
- Fingerprint detection is a best-effort mechanism (constellation-map
  hashing with 2D landmark peaks, see `fingerprint.py`) — verified
  against 26 real recordings from live operation (0 false positives
  with clear separation from actual repeats), but occasional false
  positives are still never fully ruled out. That's what the "zap
  error" button is for.
- "⏮ Back"/"Next ⏭" during an ongoing news break: the break
  (deliberately, see `CLAUDE.md`) only knows the paused station as a
  virtual ID, not its position in the rotation — a click during the
  break therefore switches to the first station in the list instead of
  the actual neighbor of the paused station.

## Future features

### Own music library & categorization (planned)

Optional mode alongside stream switching: scan a local music collection,
tag it, and make it playable by category.

- ✅ **Switchable via toggle** (radio mode vs. player mode, STT/VAD fully
  off in music mode) **and a minimal player** (play/stop/back/next over
  a configurable folder, recursive up to 5 subfolder levels since
  2026-08-14, no categorization) are implemented — see "Player mode
  (foundation)" further up.
- ✅ **Expanded format support** (since 2026-08-12): scan AND playback
  now go beyond MP3 to FLAC, OGG (Vorbis), M4A (MP4 container), raw
  ADTS AAC, WAV, and APE (Monkey's Audio, text tags only — see below).
  Playback needed no changes (ffmpeg was already format-agnostic), but
  metadata/cover extraction in `music_scan.py` did: FLAC/OGG/MP4 store
  cover art in completely different places (no shared mutagen API like
  for the text tags), WAV isn't "easy"-wrapped by mutagen (tags had to
  be read via the raw ID3 frames instead), and tagged raw AAC was
  misdetected as MP3 by mutagen's auto-detection and crashed on frame
  sync — found and fixed against real ffmpeg-generated, mutagen-tagged
  test files, not just assumed from the docs (see SESSION.md). **APE
  cover art is deliberately not extracted** (no standardized field, no
  mutagen API for it), and APE support itself is **not verified
  against a real `.ape` file** since the image's ffmpeg has no encoder
  to create one — text tags should work per the mutagen docs, but
  that's still unconfirmed.
- ✅ **Phase 1 implemented**: recursive scan of the music collection
  (`music_scan.py`, separate from the player module `music_library.py`)
  via ID3 metadata (mutagen) → its own SQLite DB `music_library.db`
  (artist, album, title, genre, year, file path, embedded cover cached
  as a file if present). Manually triggered via `POST /api/library/scan`
  (`GET /api/library/scan/status` for polling, no cron job) —
  **deliberately without UI hookup yet** in this phase, see SESSION.md.
  Unchanged files (same mtime+size as the last scan) are skipped on a
  re-scan, so re-scanning a large collection doesn't re-read every file
  from scratch each time.
  - Source: file server 192.168.5.101, SMB-mounted on Dockfish under
    `/mnt/eimer/data`
- ✅ **Phase 2 implemented**: a lean query layer (`music_query.py`,
  modeled on Beets' query syntax but without a real parser — just a
  handful of fixed artist/genre substring filters) hooked directly into
  the music player. The category/favorite buttons on `/musik` are now
  mostly functional: **Queen/Pavarotti** filter by artist substring,
  **rock/klassik** by genre substring (`LIKE '%rock%'` — plain text
  similarity, not an exact genre mapping, so it won't catch every
  spelling). **schnell/langsam** ("fast"/"slow") are active too since
  phase 3 (BPM substring/range, see below). A click reuses the same
  `POST /api/music/play` route as the regular play button (an optional
  `query` body instead of a second endpoint), replaces any running
  playback immediately with the query results, and shows a clear
  message on 0 hits instead of doing nothing. Once artist/title are
  known (from the DB), "now playing" shows **artist – title** instead
  of just the filename — on `/musik` AND on the player page.
- ✅ **Phase 3 (BPM part) implemented**: BPM estimation (`music_bpm.py`,
  aubio instead of librosa — much lighter at runtime, see CLAUDE.md for
  why and for a required build patch) runs in the same scan pass as the
  ID3 parsing (same mtime/size skip logic, only a 60s snippet gets
  decoded instead of the whole track: ~0.25s/track measured). `schnell`
  (≥120 BPM) / `langsam` (≤90 BPM) are fixed thresholds, anything in
  between falls under neither — known limitation: octave errors (half/
  double tempo) are a generic problem of any beat-tracking method;
  measured against a real 402-track collection, noticeably more tracks
  ended up under "fast" than would musically make sense. Energy
  detection/browse UI from the original phase 3 idea remain open.
- ✅ **Duplicate detection implemented** (since 2026-08-12):
  `music_query.find_duplicates()` groups tracks with the same
  normalized artist+title pair (lowercased, whitespace trimmed) —
  deliberately metadata-only, no audio fingerprint comparison (that
  would need its own analysis code like `music_bpm.py`, out of scope
  for this round by user request). Catches e.g. the same song as MP3
  AND FLAC, but not audio content that's identical despite differing
  tags. Tracks without artist/title are excluded (otherwise untagged
  files would falsely show up as one giant duplicate group). Available
  via `GET /api/library/duplicates` (JSON, includes file size per hit
  as a decision aid) — **deliberately without a UI hookup** and without
  a delete action in this phase (user's choice: report only for now),
  see SESSION.md. Verified against the user's real 402-track
  collection: found exactly one genuine duplicate group.
- Enrichment (later building block, separate from the scan): fill in
  missing covers/lyrics afterwards from external sources (e.g.
  MusicBrainz/Cover Art Archive, lrclib.net), long-term goal: every
  track with cover + lyrics + category tag
- Idea list (very long-term, unclear if it'll ever be built): a custom
  AI "moderator" for announcements about external events (e.g.
  appointments, incoming mail, doorbell events)

Tech stack: Python, mutagen, SQLite, possibly FastAPI for the query API.
Reference: Beets (library manager) as inspiration for the data
model/query language, not a 1:1 adoption.

### iOS app (idea, not yet scheduled)

Native iOS app as a counterpart to the existing Android app (see
"Android app" further up): would offer the same station control and
possibly music library operation as the Android version, but built
with Swift/SwiftUI and compiled via Xcode on a Mac — a standalone
project with its own tech stack, analogous to `android-app/`. Idea
only so far, no timeline.
