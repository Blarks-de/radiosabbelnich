<p align="center">
  <img src="radiozapper.webp" alt="RadioZapper" width="700">
</p>

# RadioZapper

*[🇬🇧 English version further below](#radiozapper-english-version)*

RadioZapper hört mehrere Internetradio-Sender gleichzeitig für dich mit
und schaltet automatisch weiter, sobald irgendwo geredet wird —
Moderation, Nachrichten, Werbung, Jingles. Übrig bleibt (möglichst) nur
Musik. Der ausgewählte Sender wird per Icecast neu ausgestrahlt, sodass
man ihn im ganzen (Tail-)Netz mit VLC, im Browser oder sonst einem
Streaming-Client hören kann.

## ⚠️ Nur privat, nur hinter VPN — kein öffentlicher Betrieb

**RadioZapper ist ausdrücklich nicht für den öffentlichen Betrieb
gedacht.** Icecast-Port (8000) und Web-Interface-Port (5000) gehören
niemals direkt ins offene Internet (kein Port-Forwarding, kein
öffentlicher Reverse-Proxy) — RadioZapper läuft immer hinter einem VPN
(Tailscale o.ä.), erreichbar nur für Geräte im eigenen vertrauten Netz.
Zwei konkrete Gründe:

- **Ressourcen**: Ein offen erreichbarer Icecast-Mountpoint wird früher
  oder später gefunden (Scanner, Streaming-Aggregatoren, Hotlinking) —
  und dann zieht potenziell das halbe Internet unkontrolliert Bandbreite
  und Rechenzeit, ohne dass man das je wieder eingefangen bekommt.
- **Urheberrecht**: RadioZapper streamt fremde, lizenzierte
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
   RadioZapper reihum zum nächsten aktivierten Sender, bis wieder Musik
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
  fliegt er für 5 Minuten aus der Rotation und RadioZapper schaltet
  automatisch weiter (`STREAM_FAILURE_LIMIT`/`STATION_DEAD_COOLDOWN` in
  `radiozapper.py`).
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

## Vorausschauendes Puffern

Damit ein Wechsel nicht erst neu verbinden muss, hält RadioZapper die
nächsten Sender in Rotationsreihenfolge im Hintergrund bereits am Laufen
und puffert von jedem die letzten paar Sekunden vor. Ein Wechsel dorthin
(automatisch oder manuell) übernimmt die schon laufende Quelle sofort,
statt neu zu verbinden — spürbar flüssiger, kostet aber zusätzliche
Bandbreite/CPU (ein zusätzlicher ffmpeg-Prozess pro gepuffertem Sender,
parallel zum aktuellen; Default 5 Sender × 10s ist auf haushaltsüblicher
Hardware unkritisch).

Aus dem Puffer ausgestrahlt wird dabei nur so viel, wie der Wechsel
tatsächlich gedauert hat (typisch Bruchteile einer Sekunde) — der Rest
wird verworfen. Die gepufferten Sekunden sind also ein *Vorrat für die
Dauer der Übergabe*, kein Vorlauf, der mitgesendet wird: die
Audio-Zeitachse bleibt deckungsgleich mit der echten Zeit, egal wie oft
gezappt wird. `prebuffer_seconds` legt damit fest, wie lange ein Wechsel
maximal dauern darf, ohne dass eine Lücke entsteht.

Beide Werte (Sekunden pro Sender, Anzahl vorausgepufferter Sender) sind
unter `/config` einstellbar und wirken sofort, ohne Neustart.

## Nachrichten-Pause

Zur vollen und halben Stunde verlesen praktisch alle Radiosender
Nachrichten. Statt dessen kann RadioZapper für ein kurzes Zeitfenster
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
- **`mp3_folder`** — Container-interner Pfad (nicht der Host-Pfad!), im
  Formular normalerweise unverändert auf `/app/news_mp3` lassen. Der
  eigentliche Host-Ordner wird über `NEWS_MP3_FOLDER` in `.env` von außen
  reingemountet (siehe `docker-compose.yml`), typischerweise ein
  SMB-Mount — dafür braucht es einen Container-Neustart, kein Feld auf der
  Config-Seite. Ordner fehlt/ist leer/nicht lesbar → Feature wird für
  dieses Zeitfenster einfach übersprungen, mit Logeintrag, kein Fehler.
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
eine Datei enthält), bis `window_minutes` abgelaufen ist. Erst dann geht's
automatisch zurück zum pausierten Sender. Ein manueller Sender-Wechsel
während der Pause bricht sie sofort ab (eigene Entscheidung schlägt
Automatik, wie überall sonst in RadioZapper auch). Während der Pause
pausiert auch die automatische Sprache-Erkennung (VAD/Heuristik/
Fingerprint) — die MP3 selbst enthält u.U. Sprache, das soll nicht als
"Moderation" auf dem eigentlichen Sender fehlgedeutet werden.

## STT-Sprachfilter

Silero VAD/die Signal-Heuristik erkennen "ist hier eine menschliche
Stimme" — auch deutsch gesungene Musik zählt da oft fälschlich mit. Der
STT-Sprachfilter (`stt_filter.py`) hört stattdessen per Speech-to-Text
mit, ob gerade *zusammenhängender deutscher Text* zu erkennen ist, und
liefert das als zusätzliches Signal für die Switch-Entscheidung.

Zwei austauschbare Engines, nie gleichzeitig geladen:

- **Vosk** — kleines deutsches Kaldi-Modell, leichtgewichtig und auch auf
  einem Raspberry Pi gut nutzbar.
- **Whisper** (über `faster-whisper`) — genauer, aber deutlich
  ressourcenhungriger, selbst als "tiny"-Modell.

Konfiguriert wird das über den `stt_filter`-Block in `settings.json`,
einstellbar über die Formular-Sektion "🗣 STT-Sprachfilter" auf der
Config-Seite (`/config`) oder direkt per API:

```json
"stt_filter": {
  "enabled": false,
  "engine": "vosk",
  "vosk_model_path": "/app/vosk-model-de",
  "whisper_model_size": "tiny",
  "sample_interval_seconds": 8.0,
  "confidence_threshold": 0.75,
  "combine_mode": "and"
}
```

- **`enabled`** — Feature an/aus.
- **`engine`** — `"vosk"` oder `"whisper"`.
- **`vosk_model_path`** — Container-interner Pfad (nicht der Host-Pfad!)
  zu einem entpackten deutschen Vosk-Modell. Der eigentliche Host-Ordner
  wird über `VOSK_MODEL_FOLDER` in `.env` reingemountet (siehe
  `docker-compose.yml`). Ein deutsches Modell gibt es unter
  [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models) —
  `vosk-model-small-de-0.15` (~45 MB) für schwache Hardware/Pi,
  `vosk-model-de-0.21` (~1 GB) für mehr Genauigkeit. Entpackt in den
  Ordner legen, auf den `VOSK_MODEL_FOLDER` zeigt. Die Config-Seite zeigt
  unter dem Feld read-only den echten Host-Pfad an (analog zum
  MP3-Ordner der Nachrichten-Pause, siehe oben).
- **`whisper_model_size`** — z.B. `"tiny"`, `"base"` (siehe
  faster-whisper-Dokumentation für weitere Größen). Modelle werden beim
  ersten Gebrauch automatisch von HuggingFace geladen und in einem
  dauerhaften Volume zwischengespeichert (kein manueller Download nötig,
  braucht aber beim ersten Aktivieren Internetzugriff und etwas Zeit).
- **`sample_interval_seconds`** — wie oft ein kurzer Clip (ca. 3s) zur
  Analyse genommen wird. Läuft kontinuierlich im Hintergrund, unabhängig
  vom aktuellen VAD-Ergebnis (blockiert den Hauptloop nie).
- **`confidence_threshold`** — ab welcher (Best-Effort-)Konfidenz ein
  Sample als "zusammenhängender deutscher Text" gilt. Der Default (0.75)
  ist **empirisch gemessen**, nicht geraten: 10 Live-Clips von
  Deutschlandfunk (Sprache) lagen nie unter 0.83 Konfidenz, 30 Live-Clips
  von drei Schlager-Sendern (gesungene deutsche Musik) im Schnitt bei
  0.38 — 0.75 liegt mit Sicherheitsabstand unter dem Sprache-Minimum.
  Erkannte Texte/Konfidenzwerte landen zum Nachjustieren in
  `logs/radiozapper.log`.
- **`combine_mode`** — wie das STT-Ergebnis mit VAD/Heuristik verknüpft
  wird: `"and"` (Default) verlangt, dass beide "Sprache" sagen — das
  lässt einen Großteil deutsch gesungener Musik (VAD ja, STT erkennt
  meist keinen zusammenhängenden Text) korrekt als Musik durchgehen.
  **Kein Allheilmittel**: bei klar/langsam gesungenem Schlager erkennt
  Vosk gelegentlich kurze, grammatisch plausible Wortfetzen mit hoher
  Konfidenz (bei obigem Test ~20% der Schlager-Clips trotz Schwelle 0.75)
  — UND reduziert Fehl-Switches auf gesungene Musik deutlich, verhindert
  sie aber nicht zu 100%. `"or"` reicht, wenn eines der beiden Signale
  "Sprache" sagt — fängt mehr echte Moderation, aber wieder anfälliger
  für denselben Gesangs-Fall.

Modell nicht gefunden oder Ladefehler → das Feature deaktiviert sich
selbst (Log-Meldung, Status auch auf der Config-Seite sichtbar),
RadioZapper läuft normal ohne STT-Filter weiter. Ein Absturz der Engine
bei einem einzelnen Sample überspringt nur diesen einen Sample, nicht den
Hauptprozess.

## Sprache des Web-Interfaces

Player- und Config-Seite gibt es auf Deutsch und Englisch. Umschaltbar
unter `/config` → "🌐 Sprache" (wirkt spätestens eine Sekunde später,
kein Neustart nötig — die Seite lädt nach dem Speichern automatisch
neu). Startwert für eine frische Installation kommt aus `UI_LANGUAGE`
in `.env` (`de` oder `en`, Default `de`) — sobald einmal über die
Config-Seite gespeichert, gewinnt danach immer diese Einstellung,
auch nach einem Neustart des Containers.

Übersetzt sind alle Texte, die im Browser sichtbar sind (Labels,
Buttons, Meldungen). Log-Datei und Server-seitige Fehlermeldungen
(z.B. bei einer ungültigen Einstellung) bleiben unabhängig von dieser
Einstellung deutsch.

## Web-Interface

Erreichbar unter `http://<host>:5000/`:

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
  Feature auf einem sonst Musik-Sender), ohne dass RadioZapper
  dazwischenfunkt. Aktueller Zustand direkt am Button erkennbar.
- **🤥 Bullshitometer** — grüner-zu-roter Balken, zeigt den aktuell
  gemessenen Sprache-Wert (VAD-Wahrscheinlichkeit bzw. Heuristik-Votum)
  live in Prozent, aktualisiert alle 3s. Rein informativ (nicht klickbar)
  — friert grau ein, während Nachrichten-Pause läuft oder der
  Sabbelfilter aus ist, weil dann gar nicht klassifiziert wird.

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
- **📰 Nachrichten-Pause** (auf der Config-Seite, oberhalb der Senderliste)
  — siehe eigener Abschnitt oben.
- **🗣 STT-Sprachfilter** (auf der Config-Seite) — siehe eigener Abschnitt
  oben.

Der rohe Icecast-Stream bleibt parallel unter `http://<host>:8000/radiozapper.mp3`
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

## Architektur

| Datei | Zweck |
|---|---|
| `radiozapper.py` | Hauptprozess: Stream holen, klassifizieren, umschalten, Icecast-Output |
| `speech_detector.py` | Silero-VAD-Wrapper mit Signal-Heuristik-Fallback |
| `fingerprint.py` | Audio-Fingerprinting (Constellation-Map-Hashing) in SQLite |
| `stations_store.py` | Laden/Speichern/CRUD der Senderliste (`stations.json`) |
| `settings_store.py` | Laufzeit-Einstellungen (Puffer-Parameter, Import-URL, `settings.json`) |
| `station_import.py` | M3U-Import: laden, parsen, parallel auf dauerhaften Audiofluss prüfen |
| `webui.py` | Eingebettetes Web-Interface (Player-Seite + Config-Seite) |
| `logging_setup.py` | Zentrale Logging-Konfiguration (Konsole + rotierende Logdatei) |
| `news_break.py` | Nachrichten-Pause: Zeitfenster-Logik + zufällige MP3-Auswahl |
| `stt_filter.py` | STT-Sprachfilter: Vosk/Whisper-Engines, austauschbar, Zusatzsignal für die Switch-Entscheidung |
| `i18n.py` | Übersetzungstabelle fürs Web-Interface (Deutsch/Englisch, siehe "Sprache des Web-Interfaces") |
| `qrcode.js` | Vendorte QR-Code-Bibliothek (MIT, kazuhikoarase/qrcode-generator) fürs "📱 QR-Code"-Popup |
| `manifest.json` | PWA-Manifest (Name, Icons, `display: standalone`) fürs "Zum Startbildschirm hinzufügen" |
| `sw.js` | Service Worker: cached die statische Oberflächen-Hülle fürs Offline-Öffnen, kein Audio/API-Caching |
| `icon-192.png`, `icon-512.png` | PWA-Icons fürs Installieren als App (aktuell Platzhalter) |
| `favicon.ico` | Browser-Tab-Icon, quadratische Miniatur von `radiozapper.webp` |
| `stations.json` | Senderliste (Name, URL, Kategorie, aktiv/inaktiv) |
| `docker-compose.yml` | Icecast + RadioZapper als zwei Services |
| `check-radiozapper.sh` | Preflight-Check vor dem (ersten) Start: Docker, RAM/HD/Internet, `.env`, MP3-Ordner, Ports |
| `run_radiozapper.sh` | Start-Skript: RAM/HD/Internet-Check + `docker compose up -d --build` |

RadioZapper und das Web-Interface laufen im selben Prozess (Web-Server
als Hintergrund-Thread) — kein separater Service, keine IPC nötig, nur
geteilter In-Memory-Zustand.

Audio läuft intern als Stereo-PCM durch (Icecast-Ausgabe), die
Analyse-Pipeline (VAD/Heuristik/Fingerprint) rechnet bewusst nur auf
einem Mono-Downmix, um Rechenzeit zu sparen.

## Setup

```bash
git clone <repo-url> RadioZapper
cd RadioZapper
cp env.example .env      # Passwörter/Hostname eintragen
touch fingerprints.db    # muss als Datei existieren, siehe unten
./check-radiozapper.sh   # optional: prüft Docker/.env/MP3-Ordner/Ports vorab
docker compose up -d --build
```

`./check-radiozapper.sh` installiert bei Bedarf Docker, zeigt RAM/HD/
Internet-Status, prüft ob `.env` vollständig ausgefüllt ist (inkl.
Warnung vor unveränderten `env.example`-Platzhaltern), ob der in
`NEWS_MP3_FOLDER` eingetragene Ordner existiert/lesbar ist/MP3s enthält,
und ob `WEBUI_PORT`/`ICECAST_PORT`/`ICECAST_SSL_PORT` frei sind — läuft
bereits RadioZapper selbst auf diesen Ports, gilt das als ok; blockiert
stattdessen ein anderer Docker-Container den Port, schlägt das Skript
eine freie Alternative zum Eintragen in `.env` vor. Reine Diagnose (Exit-
Code 1 bei Problemen), startet selbst nichts. Danach `./run_radiozapper.sh`
zum eigentlichen Start (macht denselben RAM/HD/Internet-Check nochmal,
dann `docker compose up -d --build`).

Das `touch` ist Pflicht, nicht Kosmetik: `fingerprints.db` hängt in
`docker-compose.yml` als einzelne Datei im Container. Fehlt sie auf dem
Host, legt Docker an der Stelle ein *Verzeichnis* an — SQLite kann sie
dann nicht öffnen und der Container landet in einer Neustartschleife.
(Die DB selbst ist gitignored, ein frischer Clone hat sie also nie.)

Danach `stations.json` nach Belieben anpassen — entweder direkt in der
Datei oder bequemer über `http://<host>:5000/config`.

### Wichtige `.env`-Variablen

| Variable | Bedeutung |
|---|---|
| `ICECAST_ADMIN_USER`/`_PASSWORD` | Icecast-Admin-Login (auch für die Hörer-Abfrage im Web-Interface) |
| `ICECAST_SOURCE_PASSWORD` | Passwort, mit dem RadioZapper selbst auf Icecast pusht |
| `ICECAST_HOSTNAME` | Öffentlicher Hostname für den Icecast-Stream |
| `ICECAST_PORT` | Host-Port für den rohen Icecast-Stream (Default 8000) |
| `ICECAST_LOCATION`/`ICECAST_ADMIN_EMAIL` | Server-Info-Felder in Icecasts `icecast.xml` |
| `WEBUI_PORT` | Host-Port für das Web-Interface (Default 5000) |
| `TLS_CERT_FILE`/`TLS_KEY_FILE` | Host-Pfade zu PEM-Dateien für HTTPS (optional, siehe unten) |
| `ICECAST_SSL_PORT` | Host-Port für den Icecast-Stream per HTTPS (Default 8443) |
| `VOSK_MODEL_FOLDER` | Host-Ordner mit einem entpackten deutschen Vosk-Modell für den STT-Sprachfilter (optional, siehe eigener Abschnitt) |
| `UI_LANGUAGE` | Startsprache des Web-Interfaces, `de` oder `en` (optional, Default `de` — siehe "Sprache des Web-Interfaces") |

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
docker compose up -d --build radiozapper

# Konsole mitlesen (nur die wichtigen Ereignisse)
docker compose logs -f radiozapper

# Vollständiges Debug-Log (VAD-Werte, Fingerprint-Details, HTTP-Requests)
tail -f logs/radiozapper.log

# Fingerprint-Mitschnitte anhören (nach einem "Zapping-Fehler"-Verdacht)
ls fingerprint_clips/
```

### Logging

Zwei Ziele mit unterschiedlichem Detailgrad:

- **Konsole** (`docker compose logs`): nur Ereignisse, die man im Alltag
  sehen will — Senderwechsel, Fingerprint-Treffer, Warnungen, Fehler.
- **`logs/radiozapper.log`**: *immer* auf DEBUG, unabhängig von der
  Konsole. Pro Analysefenster die VAD-Wahrscheinlichkeit bzw. die
  Heuristik-Features, jeder Fingerprint-Vergleich mit Match-Stärke und
  Abstand zur Schwelle, jeder HTTP-Request des Web-Interfaces, jeder
  gestartete/gestorbene Hintergrund-Puffer. Rotierend (5 × 10 MB), auf
  dem Host unter `logs/` gemountet — überlebt also Container-Neustarts.

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

---

<a id="radiozapper-english-version"></a>

*[🇩🇪 Deutsche Version weiter oben](#radiozapper)*

# RadioZapper (English version)

RadioZapper listens to several internet radio stations at once and
automatically switches away the moment someone starts talking —
presenting, news, ads, jingles. What's left (ideally) is just music.
The currently selected station is re-streamed via Icecast, so you can
listen to it anywhere on your (Tail)net with VLC, in the browser, or
any other streaming client.

## ⚠️ Private use only, behind a VPN — no public deployment

**RadioZapper is explicitly not meant for public deployment.** The
Icecast port (8000) and the web interface port (5000) must never be
exposed directly to the open internet (no port forwarding, no public
reverse proxy) — RadioZapper always runs behind a VPN (Tailscale or
similar), reachable only from devices on your own trusted network. Two
concrete reasons:

- **Resources**: an openly reachable Icecast mount point will sooner
  or later be found (scanners, streaming aggregators, hotlinking) —
  and then potentially half the internet starts pulling bandwidth and
  CPU time uncontrolled, in a way you can never fully rein back in.
- **Copyright**: RadioZapper re-streams other people's licensed radio
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
   RadioZapper cycles to the next enabled station until music is
   playing again.
3. In parallel, **audio fingerprinting** runs (a Shazam-style
   constellation-map approach): detected speech clips are hashed and
   compared against a SQLite database of clips already heard. If a
   clip is already known (e.g. a recurring ad spot or station jingle),
   RadioZapper switches immediately instead of waiting out the full
   speech-detection time.

Neither mechanism is perfect — that's what the correction buttons in
the web interface are for (see below).

## Handling dead stations (watchdog)

Not every station URL stays playable forever — imported lists contain
stale entries, and even a working station can go silent for minutes at
a time. So this doesn't stall playback entirely:

- If the **current** station delivers nothing for three analysis
  windows in a row, it's pulled from rotation for 5 minutes and
  RadioZapper automatically switches on (`STREAM_FAILURE_LIMIT`/
  `STATION_DEAD_COOLDOWN` in `radiozapper.py`).
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

## Look-ahead buffering

So a switch doesn't have to reconnect from scratch, RadioZapper keeps
the next stations in rotation order running in the background and
buffers the last few seconds of each. Switching to one of them
(automatically or manually) takes over the already-running source
immediately instead of reconnecting — noticeably smoother, but costs
extra bandwidth/CPU (one extra ffmpeg process per buffered station,
running alongside the current one; the default of 5 stations × 10s is
uncritical on typical home hardware).

Only as much as the switch actually took (typically a fraction of a
second) is broadcast from the buffer — the rest is discarded. The
buffered seconds are thus a *reserve for the duration of the handover*,
not a lead that gets sent along: the audio timeline stays in lockstep
with real time, no matter how often you zap. `prebuffer_seconds`
therefore defines the maximum time a switch may take without creating
a gap.

Both values (seconds per station, number of pre-buffered stations) are
configurable under `/config` and take effect immediately, no restart
needed.

## News break

Practically every radio station reads the news on the hour and half
hour. Instead, RadioZapper can play a random MP3 from a local folder
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
- **`mp3_folder`** — a container-internal path (not the host path!),
  normally leave it unchanged at `/app/news_mp3` in the form. The
  actual host folder is mounted in from outside via `NEWS_MP3_FOLDER`
  in `.env` (see `docker-compose.yml`), typically an SMB mount — that
  needs a container restart, not a field on the config page. Folder
  missing/empty/unreadable → the feature is simply skipped for that
  time window, with a log entry, no error. Below the field, the config
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
until `window_minutes` has elapsed. Only then does it automatically
return to the paused station. A manual station switch during the break
cancels it immediately (a manual decision beats automation, as
everywhere else in RadioZapper). During the break, automatic speech
detection (VAD/heuristic/fingerprint) is also paused — the MP3 itself
may well contain speech, and that shouldn't be misread as "presenting"
on the actual station.

## STT speech filter

Silero VAD/the signal heuristic detect "is there a human voice here" —
music sung in German often counts as a false positive there too. The
STT speech filter (`stt_filter.py`) instead listens via speech-to-text
for whether *coherent German text* is currently audible, and feeds
that in as an additional signal for the switch decision.

Two interchangeable engines, never loaded at the same time:

- **Vosk** — a small German Kaldi model, lightweight and usable on a
  Raspberry Pi.
- **Whisper** (via `faster-whisper`) — more accurate, but noticeably
  more resource-hungry, even as the "tiny" model.

Configured via the `stt_filter` block in `settings.json`, adjustable
through the "🗣 STT-Sprachfilter" form section on the config page
(`/config`), or directly via the API:

```json
"stt_filter": {
  "enabled": false,
  "engine": "vosk",
  "vosk_model_path": "/app/vosk-model-de",
  "whisper_model_size": "tiny",
  "sample_interval_seconds": 8.0,
  "confidence_threshold": 0.75,
  "combine_mode": "and"
}
```

- **`enabled`** — feature on/off.
- **`engine`** — `"vosk"` or `"whisper"`.
- **`vosk_model_path`** — a container-internal path (not the host
  path!) to an unpacked German Vosk model. The actual host folder is
  mounted in via `VOSK_MODEL_FOLDER` in `.env` (see
  `docker-compose.yml`). A German model is available at
  [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models) —
  `vosk-model-small-de-0.15` (~45 MB) for weaker hardware/Pi,
  `vosk-model-de-0.21` (~1 GB) for more accuracy. Unpack it into the
  folder that `VOSK_MODEL_FOLDER` points to. The config page shows the
  real host path read-only below the field (analogous to the news
  break MP3 folder above).
- **`whisper_model_size`** — e.g. `"tiny"`, `"base"` (see the
  faster-whisper docs for further sizes). Models are automatically
  downloaded from HuggingFace on first use and cached in a persistent
  volume (no manual download needed, but first activation needs
  internet access and some time).
- **`sample_interval_seconds`** — how often a short clip (~3s) is
  taken for analysis. Runs continuously in the background, independent
  of the current VAD result (never blocks the main loop).
- **`confidence_threshold`** — the (best-effort) confidence above
  which a sample counts as "coherent German text". The default (0.75)
  is **empirically measured**, not guessed: 10 live clips from
  Deutschlandfunk (speech) never dropped below 0.83 confidence, 30
  live clips from three Schlager stations (sung German music) averaged
  0.38 — 0.75 sits safely below the speech minimum. Detected
  text/confidence values are logged to `logs/radiozapper.log` for
  fine-tuning.
- **`combine_mode`** — how the STT result is combined with VAD/
  heuristic: `"and"` (default) requires both to say "speech" — this
  lets a large share of sung German music (VAD says yes, STT usually
  detects no coherent text) correctly pass through as music. **Not a
  silver bullet**: with clearly/slowly sung Schlager, Vosk occasionally
  detects short, grammatically plausible word fragments with high
  confidence (~20% of Schlager clips in the test above despite the
  0.75 threshold) — `"and"` noticeably reduces false switches on sung
  music, but doesn't eliminate them 100%. `"or"` is enough if either
  signal says "speech" — catches more actual presenting, but is again
  more prone to that same singing case.

Model not found or load error → the feature disables itself (log
entry, status also visible on the config page), RadioZapper keeps
running normally without the STT filter. A crash of the engine on a
single sample only skips that one sample, not the main process.

## Web interface language

The player and config pages are available in German and English.
Switch it under `/config` → "🌐 Sprache" (takes effect within about a
second, no restart needed — the page reloads automatically after
saving). The starting value for a fresh install comes from
`UI_LANGUAGE` in `.env` (`de` or `en`, default `de`) — once saved via
the config page, that setting always wins afterwards, even after
restarting the container.

Everything visible in the browser is translated (labels, buttons,
messages). The log file and server-side error messages (e.g. for an
invalid setting) stay German regardless of this setting.

## Web interface

Reachable at `http://<host>:5000/`:

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
  otherwise music station) without RadioZapper interfering. Current
  state is visible directly on the button.
- **🤥 Bullshit-o-meter** — a green-to-red bar showing the currently
  measured speech value (VAD probability or heuristic vote) live in
  percent, updated every 3s. Purely informational (not clickable) —
  freezes gray while a news break is running or the chatter filter is
  off, because nothing is being classified then.

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
- **📰 News break** (on the config page, above the station list) — see
  its own section above.
- **🗣 STT speech filter** (on the config page) — see its own section
  above.

The raw Icecast stream also remains reachable in parallel at
`http://<host>:8000/radiozapper.mp3` (e.g. for VLC).

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

## Architecture

| File | Purpose |
|---|---|
| `radiozapper.py` | Main process: fetch stream, classify, switch, Icecast output |
| `speech_detector.py` | Silero VAD wrapper with signal-heuristic fallback |
| `fingerprint.py` | Audio fingerprinting (constellation-map hashing) in SQLite |
| `stations_store.py` | Load/save/CRUD for the station list (`stations.json`) |
| `settings_store.py` | Runtime settings (buffer parameters, import URL, `settings.json`) |
| `station_import.py` | M3U import: download, parse, check for continuous audio in parallel |
| `webui.py` | Embedded web interface (player page + config page) |
| `logging_setup.py` | Central logging config (console + rotating log file) |
| `news_break.py` | News break: time-window logic + random MP3 selection |
| `stt_filter.py` | STT speech filter: interchangeable Vosk/Whisper engines, additional signal for the switch decision |
| `i18n.py` | Translation table for the web interface (German/English, see "Web interface language") |
| `qrcode.js` | Vendored QR code library (MIT, kazuhikoarase/qrcode-generator) for the "📱 QR code" popup |
| `manifest.json` | PWA manifest (name, icons, `display: standalone`) for "Add to home screen" |
| `sw.js` | Service worker: caches the static UI shell for offline opening, no audio/API caching |
| `icon-192.png`, `icon-512.png` | PWA icons for installing as an app (currently placeholders) |
| `favicon.ico` | Browser tab icon, a square thumbnail of `radiozapper.webp` |
| `stations.json` | Station list (name, URL, category, active/inactive) |
| `docker-compose.yml` | Icecast + RadioZapper as two services |
| `check-radiozapper.sh` | Preflight check before the (first) start: Docker, RAM/disk/internet, `.env`, MP3 folder, ports |
| `run_radiozapper.sh` | Start script: RAM/disk/internet check + `docker compose up -d --build` |

RadioZapper and the web interface run in the same process (web server
as a background thread) — no separate service, no IPC needed, just
shared in-memory state.

Audio flows internally as stereo PCM (Icecast output); the analysis
pipeline (VAD/heuristic/fingerprint) deliberately only computes on a
mono downmix to save CPU time.

## Setup

```bash
git clone <repo-url> RadioZapper
cd RadioZapper
cp env.example .env      # enter passwords/hostname
touch fingerprints.db    # must exist as a file, see below
./check-radiozapper.sh   # optional: pre-checks Docker/.env/MP3 folder/ports
docker compose up -d --build
```

`./check-radiozapper.sh` installs Docker if needed, shows RAM/disk/
internet status, checks whether `.env` is fully filled in (including a
warning about unchanged `env.example` placeholders), whether the
folder set in `NEWS_MP3_FOLDER` exists/is readable/contains MP3s, and
whether `WEBUI_PORT`/`ICECAST_PORT`/`ICECAST_SSL_PORT` are free — if
RadioZapper itself is already running on those ports, that counts as
fine; if a different Docker container is blocking the port instead,
the script suggests a free alternative to enter in `.env`. Pure
diagnostics (exit code 1 on problems), starts nothing itself.
Afterwards, `./run_radiozapper.sh` does the actual start (runs the
same RAM/disk/internet check again, then `docker compose up -d
--build`).

The `touch` is mandatory, not cosmetic: `fingerprints.db` is mounted in
`docker-compose.yml` as a single file inside the container. If it's
missing on the host, Docker creates a *directory* there instead —
SQLite then can't open it and the container ends up in a restart loop.
(The DB itself is gitignored, so a fresh clone never has it.)

Afterwards, adjust `stations.json` as you like — either directly in
the file or more conveniently via `http://<host>:5000/config`.

### Important `.env` variables

| Variable | Meaning |
|---|---|
| `ICECAST_ADMIN_USER`/`_PASSWORD` | Icecast admin login (also used for the listener query in the web interface) |
| `ICECAST_SOURCE_PASSWORD` | Password RadioZapper itself uses to push to Icecast |
| `ICECAST_HOSTNAME` | Public hostname for the Icecast stream |
| `ICECAST_PORT` | Host port for the raw Icecast stream (default 8000) |
| `ICECAST_LOCATION`/`ICECAST_ADMIN_EMAIL` | Server info fields in Icecast's `icecast.xml` |
| `WEBUI_PORT` | Host port for the web interface (default 5000) |
| `TLS_CERT_FILE`/`TLS_KEY_FILE` | Host paths to PEM files for HTTPS (optional, see below) |
| `ICECAST_SSL_PORT` | Host port for the Icecast stream over HTTPS (default 8443) |
| `VOSK_MODEL_FOLDER` | Host folder with an unpacked German Vosk model for the STT speech filter (optional, see its own section) |
| `UI_LANGUAGE` | Starting language of the web interface, `de` or `en` (optional, default `de` — see "Web interface language") |

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
docker compose up -d --build radiozapper

# Follow the console (only the important events)
docker compose logs -f radiozapper

# Full debug log (VAD values, fingerprint details, HTTP requests)
tail -f logs/radiozapper.log

# Listen to fingerprint recordings (after a suspected "zap error")
ls fingerprint_clips/
```

### Logging

Two destinations with different levels of detail:

- **Console** (`docker compose logs`): only the events you want to see
  day-to-day — station switches, fingerprint matches, warnings,
  errors.
- **`logs/radiozapper.log`**: *always* at DEBUG, independent of the
  console. Per analysis window, the VAD probability or heuristic
  features, every fingerprint comparison with match strength and
  distance to the threshold, every HTTP request to the web interface,
  every background buffer started/died. Rotating (5 × 10 MB), mounted
  on the host under `logs/` — so it survives container restarts.

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
