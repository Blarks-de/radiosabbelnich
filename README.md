<p align="center">
  <img src="radiozapper.webp" alt="RadioZapper" width="700">
</p>

# RadioZapper

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
  Jede Kategorie hat einen "Alle deaktivieren"-Knopf (praktisch nach
  einem Import mit hunderten neuen Sendern). Änderungen wirken sofort,
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
| `qrcode.js` | Vendorte QR-Code-Bibliothek (MIT, kazuhikoarase/qrcode-generator) fürs "📱 QR-Code"-Popup |
| `manifest.json` | PWA-Manifest (Name, Icons, `display: standalone`) fürs "Zum Startbildschirm hinzufügen" |
| `sw.js` | Service Worker: cached die statische Oberflächen-Hülle fürs Offline-Öffnen, kein Audio/API-Caching |
| `icon-192.png`, `icon-512.png` | PWA-Icons fürs Installieren als App (aktuell Platzhalter) |
| `favicon.ico` | Browser-Tab-Icon, quadratische Miniatur von `radiozapper.webp` |
| `stations.json` | Senderliste (Name, URL, Kategorie, aktiv/inaktiv) |
| `docker-compose.yml` | Icecast + RadioZapper als zwei Services |

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
docker compose up -d --build
```

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
