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
(automatisch oder manuell) übernimmt den fertigen Puffer sofort, statt
neu zu verbinden — spürbar flüssiger, kostet aber zusätzliche
Bandbreite/CPU (ein zusätzlicher ffmpeg-Prozess pro gepuffertem Sender,
parallel zum aktuellen; Default 5 Sender × 10s ist auf haushaltsüblicher
Hardware unkritisch).

Beide Werte (Sekunden pro Sender, Anzahl vorausgepufferter Sender) sind
unter `/config` einstellbar und wirken sofort, ohne Neustart.

## Web-Interface

Erreichbar unter `http://<host>:5000/`:

- **Aktueller Sender + "Jetzt läuft"** — Titel/Interpret, falls der
  Sender ICY-Metadaten oder eine bekannte Alternativ-Quelle liefert
- **Eingebetteter Player** — direkt im Browser mithören, ohne extra
  App/Client
- **Sender-Liste** zum manuellen Umschalten
- **🗣️ Gesabbel!** — hast du selbst erkannt, dass gerade geredet wird
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

Der rohe Icecast-Stream bleibt parallel unter `http://<host>:8000/radiozapper.mp3`
erreichbar (z.B. für VLC).

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
