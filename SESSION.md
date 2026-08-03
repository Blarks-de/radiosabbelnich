# RadioZapper — Session-Log

Laufendes Protokoll der Arbeit an diesem Projekt (chronologisch, neueste
Einträge unten) — hier steht das *Wie und Warum* der einzelnen Schritte.
Für den allgemeinen Projekt-Überblick siehe `README.md` (bis 2026-08-02
gab's dafür `HANDOVER.md`, das war aber als Übergabe-Doku an Claude Code
gedacht und ist inzwischen durch die README ersetzt — Verweise darauf in
älteren Einträgen unten sind bewusst nicht rückwirkend korrigiert, siehe
Eintrag "Umbenennung radio_switch.py → radiozapper.py" für die Begründung).

## 2026-08-02

### Mono → Stereo
- `StreamSource` (radio_switch.py) startet jetzt einen ffmpeg-Prozess mit
  zwei `-map`-Outputs: Mono (s16le) auf `pipe:1` für die Analyse-Pipeline
  (VAD/Heuristik/Fingerprint), Stereo (s16le) auf einer zweiten Pipe (via
  `os.pipe()` + `pass_fds`) fürs Playback/Icecast-Encoding.
- `read_window()` liest beide Pipes parallel über `select.select()`, um
  Deadlocks zu vermeiden, falls eine Pipe vollläuft während auf die andere
  gewartet wird (Fenstergröße bei 44.1kHz/1s überschreitet den Standard-
  Pipe-Puffer von 64KB).
- `IcecastOutput` und `LocalOutput` liefern jetzt `-ac 2` / `channels=2`.
- Getestet: synthetische ffmpeg-Quelle (kein Deadlock, korrekte
  Sample-Anzahl) + Live-Test gegen SWR3. Nach Rebuild/Deploy per `ffprobe`
  gegen den laufenden Icecast-Mountpoint verifiziert: `channels=2`,
  `sample_rate=44100`.
- Nebenbefund (nicht behoben, nur notiert): Silero VAD scheitert beim
  Container-Start (`cannot enable executable stack as shared object
  requires`), fällt auf die Signal-Heuristik zurück. Vermutlich Kernel-
  Hardening auf dem Docker-Host oder neue `silero-vad-lite`-Wheel beim
  Rebuild. Noch nicht untersucht.

### Webinterface: aktueller Sender, Hörer-IPs, manueller Senderwechsel
- Ziel: Browser-UI zeigt (a) aktuell laufenden Sender, (b) verbundene
  Hörer inkl. IP, (c) Liste aller Sender aus `stations.json` mit
  Möglichkeit, von Hand umzuschalten.
- Bislang existiert **kein** Webinterface im Projekt — wird neu gebaut.
- Hörer-IPs kommen von Icecasts Admin-API:
  `GET /admin/listclients?mount=/radiozapper.mp3` (Basic-Auth mit den
  ICECAST_ADMIN_*-Credentials aus `.env`) liefert pro Hörer IP,
  User-Agent und Verbindungsdauer als XML.
- Architekturentscheidung: kein separater Service/Container, kein
  Flask/FastAPI — stattdessen ein `ThreadingHTTPServer` (Python-Stdlib)
  in einem Hintergrund-Thread *im selben Prozess* wie `radio_switch.py`.
  Grund: Sender-Wechsel muss den laufenden Haupt-Loop beeinflussen können
  (Umschalt-Request), das ist mit geteiltem In-Memory-State (Lock-
  geschützt) am einfachsten und ohne IPC/Datei-Polling zu lösen. Passt
  auch zum bisherigen Minimal-Dependency-Stil des Projekts.
- Umsetzung:
  - `webui.py` (neu): `SwitcherState` (lock-geschützter Zustand: aktueller
    Sender-Index, ausstehender manueller Switch-Request), `ThreadingHTTPServer`
    mit `GET /` (HTML+JS-Seite), `GET /api/status` (aktueller Sender,
    Sender-Liste, Hörer-Liste als JSON), `POST /api/switch` (`{"index": N}`).
    Hörer-Abfrage via `_fetch_listeners()` gegen
    `GET /admin/listclients?mount=...` mit Basic-Auth; liefert `None` bei
    Fehler/Nichtverfügbarkeit statt leerer Liste, damit UI "nicht verfügbar"
    von "0 Hörer" unterscheiden kann. Frontend pollt `/api/status` alle 5s
    per `fetch()`, keine Server-Side-Templates nötig (Seite ist statisch,
    Daten kommen clientseitig rein). IP/User-Agent werden im JS über
    `textContent`→`innerHTML`-Trick escaped, bevor sie in die Hörer-Tabelle
    gerendert werden — User-Agent kommt von den Hörern selbst und ist damit
    potenziell attacker-kontrollierter Input (XSS-Risiko sonst).
  - `radio_switch.py`: `state = webui.SwitcherState(STREAMS)`,
    `webui.start_server(...)` bei gesetztem `--webui-port` (0 = aus).
    Hauptloop prüft am Anfang jeder Iteration `state.pop_manual_request()`
    und springt bei Treffer direkt zum gewünschten Sender (kein
    "erst auf Musik warten" wie beim automatischen Switch — manueller
    Wechsel ist ein bewusster Nutzer-Wunsch, kein Ausweich-Mechanismus).
    `state.set_current(current)` überall dort ergänzt, wo `current` sich
    ändert, damit die UI den echten Stand sieht.
  - Neue CLI-Args: `--webui-port`, `--icecast-admin-url`,
    `--icecast-admin-user`, `--icecast-admin-password`, `--icecast-mount`.
  - `Dockerfile`: `webui.py` mit ins Image kopiert, `ENTRYPOINT` um die
    neuen Args erweitert (Webserver lauscht containerintern fest auf Port
    5000, der Host-Port ist über `WEBUI_PORT` in `.env`/docker-compose
    konfigurierbar — bewusst getrennt, damit die interne Bindung nicht an
    einer falsch gesetzten Host-Portvariable hängt).
  - `docker-compose.yml`: `radio-switch`-Service bekommt
    `ICECAST_ADMIN_URL/-USER/-PASSWORD/-MOUNT` (fürs webui.py) und
    `ports: ${WEBUI_PORT:-5000}:5000`.
  - `.env` / `env.example`: `WEBUI_PORT=5000` ergänzt.
- Getestet nach Rebuild/Deploy: `GET /`, `GET /api/status` (zeigt korrekten
  Sender + alle 7 Stationen aus stations.json), `POST /api/switch` (Log
  bestätigt `🎛 Manuell umgeschaltet auf: SWR3`, danach zurück auf Radio
  Bob geschaltet, da während des Tests ein echter Hörer im Tailnet
  verbunden war), Hörer-Liste zeigt reale IP + User-Agent + Verbindungs-
  dauer eines tatsächlich verbundenen Clients korrekt an.
- Web-Interface erreichbar unter `http://<host>:5000/` (Port über
  `WEBUI_PORT` in `.env` konfigurierbar, Default 5000).

### Icecast-Warnungen fixen: Location + Admin-Kontakt
- Log zeigte:
  `WARN CONFIG/_parse_root <location> not configured, using default value "Earth"`
  und dasselbe für `<admin>` (`icemaster@localhost`). Gewünscht:
  Location "Hamburg", Admin-Kontakt `blarks@gmail.com`.
- Erste Analyse ging in die Irre: `docker exec icecast-radioswitch cat
  /etc/icecast2/icecast.xml` zeigte `<location>Earth</location>` und
  `<admin>icemaster@localhost</admin>` als literale Werte — das ist aber
  die vom Debian-Paket mitgelieferte Default-Config unter `/etc/icecast2/`,
  **nicht** die tatsächlich benutzte Datei. Das Base-Image
  (`perl19/icecast2`) generiert bei jedem Start per `/app/start.sh` eine
  eigene `/app/icecast.xml` über ein Tool `icegen` (Go-Binary,
  `./icegen new --admin ... --host ... --port ...`) und startet
  `icecast2 -c icecast.xml` mit relativem Pfad aus `/app`.
- `icegen new --help` zeigt: keine Flags für `<location>`/`<admin>`
  (Server-Info-Felder). Der `--admin`-Flag von icegen setzt nur den
  Admin-**Login-Benutzernamen**, nicht die Kontakt-E-Mail. Das
  generierte `/app/icecast.xml` enthält `<location>`/`<admin>` also
  **gar nicht** als Tags — deshalb greift Icecasts interner Default.
- Fix: `docker-compose.yml` überschreibt für den `icecast`-Service jetzt
  `entrypoint`/`command` komplett (statt sich auf `start.sh` zu
  verlassen): ruft `icegen new` mit denselben Flags wie `start.sh` auf,
  fügt danach per `sed -i "/<icecast>/a\\...` `<location>`/`<admin>`
  direkt nach dem öffnenden `<icecast>`-Tag ein (kein `s///`-Replace
  möglich, da nichts zum Ersetzen da war — erster Versuch mit
  `s#<location>.*</location>#...#` lief ins Leere, Warnungen blieben),
  und startet dann `exec icecast2 -c icecast.xml`. Werte kommen aus
  neuen Env-Vars `IC_LOCATION`/`IC_ADMIN_EMAIL`, gespeist aus
  `ICECAST_LOCATION`/`ICECAST_ADMIN_EMAIL` in `.env` (Hamburg /
  blarks@gmail.com) und `env.example` (Platzhalter).
- Stolperfalle dabei gefunden und gefixt: `icegen new` überschreibt eine
  bereits vorhandene `icecast.xml` **nicht**, sondern hängt eine zweite
  Kopie an (146 → 292 Zeilen bei zweitem Lauf gegen dieselbe Datei) —
  ergibt ungültiges XML mit zwei Root-Elementen
  (`parser error: Extra content at the end of the document`). Bei jedem
  Container-Neustart (z.B. durch `restart: unless-stopped` nach einem
  Crash) hätte das eine Absturzschleife ausgelöst, die sich mit jedem
  Neustart verschlimmert. Fix: `rm -f icecast.xml` direkt vor
  `./icegen new` im command-Script — nicht kosmetisch, sondern Pflicht
  für Restart-Robustheit.
- Nebenwirkung beim Testen: Neuerstellen des `icecast`-Containers hat die
  bestehende Source-Verbindung von `radio-switch` gekappt (kein
  Auto-Reconnect für den Icecast-Push in `IcecastOutput`) — Stream stand
  kurz still, bis `radio-switch` neugestartet wurde. Für künftige
  Icecast-Änderungen einplanen: danach immer auch `radio-switch`
  neustarten.
- Verifiziert: `/admin/stats` zeigt `<location>Hamburg</location>` und
  `<admin>blarks@gmail.com</admin>`, Container läuft stabil (kein
  Crash-Loop mehr), Stream danach wieder erreichbar (44.1kHz/Stereo via
  `ffprobe` bestätigt).
- Bleibt unangetastet (nicht Teil der Anfrage, nur notiert): Icecast
  loggt beim Start weiterhin `Couldn't find group "icecast2" in groups
  file` / `Can't change user id unless you are root` sowie `Cannot open
  mime types file /etc/mime.types` — harmlose Altlasten aus dem
  icegen-Template (Container läuft eh schon als User `icecast2`, der
  Privilege-Drop-Versuch ist ein No-Op).

## 2026-08-02 (Fortsetzung) — "Nachrichten werden nicht erkannt" + "manuelles Zappen dauert ewig"

Nutzer meldet zwei Probleme im laufenden Betrieb. Beide auf konkrete,
reproduzierte Bugs zurückgeführt (nicht nur Tuning).

### Bug 1: Silero VAD lädt nicht -> Heuristik-Fallback erkennt kaum Sprache
- Bereits am 2026-08-02 (erster Eintrag oben) als offener Punkt notiert,
  jetzt tatsächlich untersucht und gefixt.
- Log zeigte durchgehend `Silero VAD konnte nicht initialisiert werden
  (... cannot enable executable stack as shared object requires: Invalid
  argument) — falle auf die Signal-Heuristik zurück`. Seit dem allerersten
  Rebuild dieser Session lief die Erkennung nur auf der Heuristik
  (`classify_window()`), deren Votes in den Logs fast durchgehend 0/3
  oder 1/3 blieben (Schwelle ist 2/3) — d.h. so gut wie nie "speech".
  Das erklärt direkt, warum Nachrichten/Moderation nicht erkannt wurden.
- Root-Cause-Suche ging erst in die falsche Richtung: `import
  silero_vad_lite` allein funktionierte überall problemlos (auch in
  einem frischen `python:3.12-slim`-Testcontainer) — das ist aber nur
  der reine Python-Modul-Import, der lädt die `.so` noch nicht. Der
  eigentliche Fehler passiert erst bei `SileroVAD(16000)` (Konstruktor
  in `speech_detector.py`), der intern `ctypes.CDLL(...)` aufruft und
  damit `dlopen()` auf `silero_vad_lite.so` — DAS reproduziert den Fehler
  zuverlässig, und zwar in **jedem** Container auf diesem Host (auch
  einem komplett unmodifizierten `python:3.12-slim`), nicht nur in
  radio-switch. Also ein Host-/Kernel-Verhalten, kein Compose-/User-
  /Capabilities-Problem unseres Containers.
- Ursache: `silero_vad_lite.so` ist mit einem `PT_GNU_STACK`-Programm-
  header gebaut, der `PF_X` (ausführbar) für den Stack verlangt (alter
  Toolchain-Default). Der Kernel auf Dockfish verweigert das `mprotect`
  beim Laden. Klassischer Fix wäre `execstack -c datei.so`, aber das
  Paket `execstack` existiert in den aktuellen Debian-Repos (trixie)
  nicht mehr.
- Fix: `fix_silero_execstack.py` (neu, Repo-Root) patcht das `PF_X`-Bit
  im `PT_GNU_STACK`-Segment direkt in der ELF-Datei per `struct`-Modul
  (reines Python, keine externe Abhängigkeit). Wird im Dockerfile direkt
  nach `pip install ... silero-vad-lite` ausgeführt und danach wieder
  gelöscht (`RUN python3 fix_silero_execstack.py && rm
  fix_silero_execstack.py`).
- Verifiziert nach Rebuild: Log zeigt jetzt `🗣 Sprache-Erkennung: Silero
  VAD` statt Fallback, `--verbose`-Output zeigt `[vad] mean_prob=...
  speech_ratio=...`-Zeilen statt `[feat]`-Heuristik-Zeilen. End-to-End
  live beobachtet: VAD erkannte einen Sprache-Lauf auf SWR3
  (`speech_ratio=1.00 -> SPEECH`), Fingerprint-DB matchte ihn gegen einen
  bereits 8x gehörten Radio-Bob-Jingle/Werbespot, automatischer Switch
  auf R.SH lief korrekt durch (`🔁 Bekannter Jingle/Werbespot
  wiedererkannt` -> `🎙 ... schalte um` -> `▶ Spiele: R.SH`).

### Bug 2: Manuelles Umschalten kann quasi unbegrenzt hängen
Drei zusammenhängende strukturelle Lücken gefunden, alle in
`radio_switch.py`:

1. **`StreamSource.read_window()` hatte keinen Timeout.** `select.select()`
   wurde ohne Timeout aufgerufen -> falls eine frisch gestartete Quelle
   (z.B. nach manuellem Wechsel) nie Daten liefert (Verbindungsproblem,
   hängender Redirect, totes Encoder-Feed), blockiert der komplette
   Hauptloop unbegrenzt — inklusive **jedes weiteren** manuellen
   Switch-Requests, da deren Abfrage am Loop-Anfang nie wieder erreicht
   wird. Genau das erklärt "dauert ewig": ein einziger kaputter Sender
   kann den ganzen Switcher einfrieren, bis der Container manuell
   neugestartet wird.
   Fix: neue Konstante `STREAM_READ_TIMEOUT = 8.0` (Sekunden), `select`
   bekommt jetzt ein Timeout-Budget über eine Deadline, `read_window()`
   gibt spätestens nach 8s zurück (ggf. mit leeren/unvollständigen
   Arrays). Ein leeres Ergebnis lässt den bereits vorhandenen
   Reconnect-Pfad im Hauptloop greifen (`liefert nichts mehr, versuche
   neu zu verbinden`).
2. **`do_switch()` (automatisches Rundlauf-Probieren) konnte einen
   manuellen Request bis zu `MAX_SKIPS_PER_ROUND * (1.5s +
   STREAM_READ_TIMEOUT)` blockieren** (7 Sender × ~9,5s ≈ 66s im
   Worst Case vor dem Timeout-Fix, war vorher sogar unbegrenzt). Fix:
   `do_switch()` prüft jetzt bei jedem Skip zuerst
   `state.pop_manual_request()` — kommt ein manueller Wunsch rein, legt
   `do_switch()` ihn per `state.request_switch(pending)` sofort wieder
   zurück (nicht verwerfen!) und bricht die Auto-Suche ab. Der
   Hauptloop übernimmt den eigentlichen Wechsel beim nächsten Durchlauf
   (dort ist die komplette Switch-Logik inkl. Streak-Reset schon
   vorhanden, keine Duplizierung nötig).
3. **`IcecastOutput` hatte keine Reconnect-Logik.** Bei einem Broken
   Pipe (z.B. weil der Icecast-Container neugestartet wird — wie es
   heute beim Location/Admin-Fix mehrfach live passiert ist, siehe
   oben) blieb `self.proc` dauerhaft der tote ffmpeg-Prozess. Jeder
   `write()` scheiterte danach für immer, nur mit einer Log-Zeile pro
   Sekunde (`⚠ Icecast-Verbindung unterbrochen.`) ohne jeden
   Wiederherstellungsversuch — der Hauptloop lief zwar weiter und
   schaltete brav Sender, aber der tatsächliche Broadcast blieb für
   den Rest der Prozess-Laufzeit stumm. Aus Hörer-Sicht sieht das
   identisch aus wie "Umschalten tut nichts" bzw. "dauert ewig", nur
   dass es in Wahrheit nie wieder funktioniert hätte ohne manuellen
   `docker compose restart radio-switch`.
   Fix: `IcecastOutput` merkt sich jetzt seine eigenen Verbindungsdaten,
   `write()` versucht bei `BrokenPipeError`/`OSError` automatisch einen
   Reconnect (kill + neuer ffmpeg-Prozess), mit 5s Cooldown zwischen
   Versuchen (kein Popen-Spam bei Dauerausfall).
- Syntaxgeprüft (`py_compile`), Rebuild + Deploy durchgeführt, manueller
  Switch getestet (Response < 20ms, Wechsel im Log/Status sofort
  sichtbar).

## 2026-08-02 (Fortsetzung) — Player ins Web-Interface einbetten

Nutzer wollte den Stream (`:8000/radiozapper.mp3`) und das Web-Interface
(`:5000/`) auf einer Seite vereint haben ("umtopfen"). Interpretiert als:
Web-Interface bekommt einen eingebetteten `<audio>`-Player, damit man auf
einer einzigen Seite hört UND umschaltet — der rohe Icecast-Port 8000
bleibt daneben bestehen (wird u.a. von VLC-Hörern im Tailnet direkt
genutzt, siehe HANDOVER.md, "Was das Projekt macht").

- `webui.py`: `<audio id="player" controls preload="none">` unter der
  "aktueller Sender"-Anzeige. `_build_status()` liefert jetzt zusätzlich
  `stream_port`/`stream_mount`. JS setzt `player.src` **einmalig** beim
  ersten `refresh()` (`playerSrcSet`-Flag) — bewusst NICHT bei jedem
  5s-Poll neu, sonst würde die Wiedergabe alle 5 Sekunden neu
  anlaufen/stottern.
- Stream-URL wird clientseitig aus `location.hostname` (Browser weiß,
  über welchen Hostnamen er die Seite gerade aufgerufen hat — Tailscale-
  Name, LAN-IP, `localhost`, was auch immer) + serverseitig gelieferten
  `stream_port`/`stream_mount` zusammengebaut. Kein Hardcoding eines
  bestimmten Hostnamens nötig, funktioniert unabhängig davon, wie man
  die Seite erreicht.
- Neue Konfig-Kette für den Port: `ICECAST_PUBLIC_PORT` (docker-
  compose.yml, `radio-switch`-Service, aus `${ICECAST_PORT:-8000}` in
  `.env`) -> `--icecast-public-port`-CLI-Arg (Dockerfile-Entrypoint,
  radio_switch.py) -> `icecast_cfg["public_port"]` -> `/api/status`.
  Getrennt vom containerinternen `ICECAST_ADMIN_URL` (der zeigt auf
  `icecast-radioswitch:8000`, ist für Container-zu-Container-Zugriff
  auf die Admin-API, nicht für den Browser erreichbar).
- Verifiziert: `/api/status` liefert `stream_port: "8000"`,
  `stream_mount: "/radiozapper.mp3"`; `<audio>`-Tag im ausgelieferten
  HTML bestätigt.

## 2026-08-02 (Fortsetzung) — Config-Seite für Sender-Verwaltung

Neue Anforderung: Sender auswählen/aktivieren/deaktivieren/hinzufügen/
löschen/editieren, gegliedert in Lokal/Regional/National/International/
Global/Interstellar, alphabetisch sortiert, Aktivierung per Haken.

### Architekturentscheidung
Größter Umbau bisher: `radio_switch.py` referenzierte Sender bisher über
eine feste Listen-Position (`current` = int-Index in einem einmal beim
Start geladenen `STREAMS`). Damit Sender live (ohne Neustart) hinzugefügt/
gelöscht/deaktiviert werden können, ohne die laufende Wiedergabe
durcheinanderzubringen, war ein Wechsel auf ID-basierte Referenzierung
nötig — Details:

- **Neues Modul `stations_store.py`**: alleinige Quelle der Wahrheit für
  `stations.json`. Schema jetzt `{id, name, url, category, enabled}`
  statt nur `{name, url}`. Migration alter Dateien passiert transparent
  beim ersten Laden (`_ensure_ids_and_defaults`): fehlende `id` wird aus
  dem Namen geslugged (`_slugify` + Kollisionsauflösung
  `radio-bob`, `radio-bob-2`, ...), fehlende `category` bekommt
  `DEFAULT_CATEGORY = "National"`, fehlendes `enabled` wird `true`.
  CRUD-Funktionen (`add`/`update`/`set_enabled`/`delete`) mit
  Datei-Lock (`threading.Lock`) gegen Races zwischen mehreren
  Config-Seiten-Requests. `delete()` verweigert das Löschen des letzten
  verbleibenden Senders (sonst kein Rotationsziel mehr möglich).
  Gemeinsam genutzt von `radio_switch.py` (Playback) und `webui.py`
  (Config-Seite) — beide importieren dasselbe Modul, kein Duplikat.
- **`webui.SwitcherState`** umgebaut: hält jetzt `_current_id` (statt
  Index) sowie einen In-Memory-Cache `_all_stations`/`_active_stations`
  (aktive = `enabled=true`, alphabetisch nach Name — das ist die
  Rotationsreihenfolge). Neuer `_reload_requested`-Flag +
  `request_reload()`/`pop_reload_request()`, analog zum bereits
  vorhandenen `_manual_request`-Mechanismus für manuelle Switches: die
  Config-Seite ruft nach jeder Änderung `state.request_reload()` auf,
  der Hauptloop pollt das Flag einmal pro Analysefenster (dieselbe
  Stelle wie der manuelle-Switch-Check) und lädt bei Bedarf per
  `state.reload()` neu aus `stations_store`.
- **`radio_switch.py`**: `current` ist jetzt ein dict (Sender-Snapshot:
  id/name/url/category/enabled) statt ein int-Index. `do_switch()`
  snapshotet `state.active_stations` einmal zu Beginn eines Durchlaufs
  und rotiert innerhalb dieses Snapshots (`len(active)` statt der alten
  festen `MAX_SKIPS_PER_ROUND`-Konstante). Neuer Reload-Zweig ganz oben
  im Hauptloop (vor dem manuellen-Switch-Check): wenn die Config-Seite
  etwas geändert hat, `state.reload()`, dann prüfen ob der *aktuell
  laufende* Sender noch in der aktiven Liste ist — falls nicht (gerade
  deaktiviert/gelöscht), automatisch auf den ersten aktiven Sender
  wechseln (`⚙ Senderliste geändert, aktueller Sender nicht mehr aktiv
  — schalte auf: ...`). Falls *gar keine* Sender mehr aktiv sind:
  Wiedergabe pausiert (bleibt auf der letzten Quelle stehen), bis wieder
  einer aktiviert wird — kein Crash.
- **Stolperfalle beim Deploy gefunden**: `stations_store._write()` nutzte
  zuerst write-temp-then-`os.replace()` für atomare Schreibvorgänge —
  schlug im Container mit `OSError: [Errno 16] Device or resource busy`
  fehl. Ursache: `stations.json` ist in `docker-compose.yml` als
  *einzelne Datei* gebindmountet (`./stations.json:/app/stations.json`),
  und über eine solche Mount-Grenze lässt sich keine neue Inode per
  `rename()`/`os.replace()` drüberschieben. Fix: direktes Schreiben in
  die Datei (kein temp+rename) — weniger "atomar" im Crash-Fall, aber
  bei dieser Nutzungsfrequenz (Admin-Klicks, kein Hot-Loop) irrelevant.
- **Zweiter Bug beim ersten Test gefunden**: `/api/config/stations`
  (GET) las zunächst aus `state.all_stations` (dem Rotations-Cache) statt
  frisch von der Platte — eigene Änderungen der Config-Seite waren dadurch
  erst sichtbar, nachdem der Hauptloop irgendwann seinen Reload-Poll
  gemacht hatte (spürbare Verzögerung, UI wirkte kaputt: "füge Sender
  hinzu, Liste zeigt ihn nicht"). Fix: die Config-Seite liest jetzt immer
  direkt über `stations_store.load_all()` (Platte), der Cache in
  `SwitcherState` ist bewusst nur für die Rotationslogik im Hauptloop da.

### Neue Endpunkte (webui.py)
- `GET /config` — Config-Seite (HTML, gruppiert nach Kategorie, pro
  Sender: Haken zum Aktivieren, Bearbeiten/Löschen-Buttons, Formular für
  neue Sender unten)
- `GET /api/config/stations` — alle Sender + Kategorienliste (JSON)
- `POST /api/config/stations` — neuer Sender (`name`/`url`/`category`/
  `enabled`)
- `POST /api/config/stations/<id>` — Sender bearbeiten (volle Felder)
- `POST /api/config/stations/<id>/toggle` — aktivieren/deaktivieren
  (`{"enabled": bool}`)
- `POST /api/config/stations/<id>/delete` — löschen
- `/api/switch` auf der Haupt-Player-Seite nutzt jetzt `{"id": "..."}`
  statt `{"index": N}` (Breaking Change der bisherigen Session, aber
  beide Seiten — Server und Frontend — wurden zusammen angepasst)

### Bestehende Sender kategorisiert
Migration hat allen 7 bestehenden Sendern erstmal pauschal
`category: "National"` gegeben (der Default). Danach von Hand über die
neue API sinnvoll sortiert:
- Lokal: Rock Antenne Hamburg, Hamburg Zwei
- Regional: SWR3, R.SH, ffn
- National: Radio Bob, 1LIVE
- International/Global/Interstellar: noch leer (Kategorien erscheinen
  auf der Config-Seite trotzdem mit "Keine Sender in dieser Kategorie.",
  damit man sieht, dass es sie gibt)

### Tests (isoliert, ohne Docker, gegen temporäre stations.json)
- Add → erscheint sofort in `/api/config/stations`
- Toggle aus → verschwindet NICHT sofort aus `/api/status` (Cache), aber
  nach `state.reload()` (simuliert Hauptloop-Poll) korrekt weg
- Update (Rename+Recategorize) → `/api/switch` auf die neue ID schlägt
  vor Reload fehl (400 invalid id, da Cache noch alte ids/aktiv-Status
  kennt), nach Reload erfolgreich
- Delete → funktioniert; Delete auf nicht-existente ID → 404; Delete bis
  auf 0 Sender → letzter Löschversuch korrekt mit 400 abgelehnt

### Tests (live im Container nach Rebuild)
- Migration bestätigt: `stations.json` hat jetzt `id`/`category`/
  `enabled` für alle 7 Sender
- Sender live deaktiviert (nicht der aktuell laufende) → verschwindet aus
  `/api/status`-Senderliste, aktueller Sender bleibt unbeeinflusst, Log:
  `⚙ Senderliste neu geladen.`
- **Aktuell laufenden Sender live deaktiviert** (R.SH) → automatischer
  Wechsel auf ersten verbleibenden aktiven Sender (1LIVE) bestätigt,
  Log: `⚙ Senderliste geändert, aktueller Sender nicht mehr aktiv —
  schalte auf: 1LIVE`
- Danach Stream-Gesundheit erneut per `ffprobe` bestätigt (44.1kHz,
  Stereo, weiterhin sauber).

## 2026-08-02 (Fortsetzung) — Umschalt-Latenz + Now-Playing-Anzeige

Nutzer meldet: manueller Wechsel wird sofort geloggt, aber der Sender
wechselt hörbar erst mehrere Sekunden später. Zusätzlich gewünscht:
Song/Interpret der aktuellen Sendung anzeigen.

### Umschalt-Latenz
- Erste Hypothese (ffmpeg braucht lange zum Verbinden/Probing der neuen
  Quelle) per Benchmark widerlegt: Time-to-first-byte lag für alle 7
  Sender bei 0.2–1.6s, `-fflags nobuffer -analyzeduration 0 -probesize
  32k` brachte nur marginale Verbesserung (0.02–0.16s) — nicht die
  Ursache.
- Tatsächliche Ursache per Live-Test mit timestamped Logs gefunden: nach
  `source.start()` beim manuellen Switch geht der Hauptloop direkt in die
  nächste Iteration, die per `read_window(WINDOW_SECONDS=1.0)` ein
  **volles 1-Sekunden-Fenster** von der neuen Quelle abwartet, bevor
  `output.write()` überhaupt das erste Mal aufgerufen wird. Da ein Live-
  Stream nicht schneller als Echtzeit dekodiert werden kann, dauert das
  Füllen eines vollen Fensters für eine frisch verbundene Quelle je nach
  Server-Antwortzeit 1–3.4s (live gemessen: SWR3 3.4s, Radio Bob 0.6s —
  Varianz durch Netzwerk/Server-Jitter des jeweiligen Senders, nicht
  durch unseren Code).
- Fix: neue Funktion `quick_forward(seconds=0.3)` in `radio_switch.py`
  (Closure in `main()`, Zugriff auf `source`/`output`) — liest direkt
  nach einem direkten Sender-Wechsel (manuell oder durch Config-Reload
  erzwungen) einen kurzen 0.3s-Schnipsel und schreibt ihn sofort an
  `output`, statt auf das nächste volle Analysefenster zu warten. Nur in
  die beiden *direkten* Wechsel-Pfade eingebaut (manueller Switch,
  Reload-erzwungener Switch) — `do_switch()`s automatisches Rundlauf-
  Probieren bleibt unangetastet (hat mit dem 1.5s-Sleep vor der Probe
  ohnehin eine andere, absichtliche Charakteristik: Zeit geben, damit
  sich der Stream vor der Musik/Sprache-Prüfung stabilisiert).
- Verifiziert per Live-Test (timestamped Logs, mehrere Sender
  nacheinander durchgeschaltet): Lücke zwischen `🎛 Manuell
  umgeschaltet auf: ...`-Logzeile und erster tatsächlich verarbeiteter
  Audio-Sekunde der neuen Quelle jetzt durchgehend ~0.4–0.5s (vorher bis
  zu 3.4s).
- Eingeordnet, nicht behoben (weil serverseitig nicht beeinflussbar):
  zusätzlich zur jetzt minimierten Server-Latenz kommt noch Icecast-
  Queue/Burst- und vor allem der Player-Puffer beim Hörer (Browser/VLC
  puffern selbst typischerweise mehrere Sekunden voraus) — das bleibt
  eine inhärente Eigenschaft von Icecast-Streaming und lässt sich vom
  Server aus nicht wegoptimieren, ohne die Hörer-Verbindung aktiv zu
  kappen (was selbst störender wäre).

### Now-Playing-Anzeige (Song/Interpret)
- Viele Icecast/Shoutcast-Quellen betten periodisch ICY-Metadaten direkt
  in den Audio-Stream ein (`StreamTitle='Interpret - Titel';`), Intervall
  über den `icy-metaint`-Response-Header angegeben. Getestet gegen alle
  7 konfigurierten Sender: 1LIVE und ffn liefern echte Interpret-Titel-
  Daten, andere (Radio Bob, R.SH, Rock Antenne, Hamburg Zwei) zeigen nur
  Sender-Branding, SWR3 zeigt den Moderationsnamen — das entscheidet
  jeweils der Sender-Betreiber serverseitig, nicht von uns beeinflussbar.
- Implementiert in `webui.py`: `_fetch_icy_title(url)` öffnet eine kurze
  eigene HTTP-Verbindung zum Stream mit Header `Icy-MetaData: 1`
  (unabhängig von der laufenden ffmpeg-Wiedergabe-Pipeline), liest Bytes
  bis zum ersten Metadaten-Block (`icy-metaint` Bytes Audio verwerfen,
  dann 1 Längen-Byte × 16 + Metadaten-Text lesen), extrahiert
  `StreamTitle` per Regex, schließt die Verbindung sofort wieder.
  `_fetch_now_playing()` cached das Ergebnis 15s pro URL
  (`_now_playing_cache`, Lock-geschützt) — verhindert, dass mehrere
  offene Browser-Tabs (jeder pollt `/api/status` alle 5s) den
  Radiosender-Server unnötig oft extra anfragen.
- Frontend: neues `#now-playing`-Div unter der "aktueller Sender"-Zeile,
  zeigt `🎵 <StreamTitle>` wenn vorhanden, bleibt leer/versteckt sonst
  (`#now-playing:empty { display: none; }`). Über `textContent`
  gesetzt (nicht `innerHTML`) — der Titel kommt vom Sender-Server, also
  potenziell nicht vertrauenswürdiger externer Text, `textContent`
  escaped automatisch.
- Verifiziert live: Wechsel auf 1LIVE zeigt korrekt
  `now_playing: "Liam Payne & Rita Ora - For You"`, aktualisiert sich
  nach Sender-Wechsel.

## 2026-08-02 (Fortsetzung) — Now-Playing-Fallback über Senderseiten

Frage: kann man Titel/Interpret alternativ von der Sender-Homepage
ziehen, für die Sender, deren ICY-StreamTitle nur Branding statt echter
Songdaten zeigt (Radio Bob, R.SH, Rock Antenne Hamburg, Hamburg Zwei;
SWR3 zeigt den Moderationsnamen)?

### Recherche
- Homepages der 5 Kandidaten per `curl -A "Mozilla/5.0"` geladen und nach
  Now-Playing-Widget-Hinweisen durchsucht (CSS-Klassen wie
  `player__track__marquee__text`, `c-player__currentartist` gefunden —
  bestätigt, dass es Widgets gibt, aber alle clientseitig per JS befüllt,
  nicht im initial ausgelieferten HTML).
- radiobob.de und rsh.de laden beide von `upload.<domain>/production/
  static/<hash>/*.js` und referenzieren `auth-cdn.loverad.io` — beide auf
  derselben "loverad.io"-Plattform (Regiocast-Familie). JS-Bundles
  heruntergeladen und nach API-Base-URLs gegrept
  (`grep -ohE '"https?://...' *.js`).
- **radiobob**: JS referenziert `iris-bob.loverad.io/flow.json?station=
  <stationId>&offset=1&count=1` — aber `stationId` wird zur Laufzeit aus
  einer großen, webpack-minifizierten Konstanten-Tabelle aufgelöst
  (Variablen wie `_`, `aa`, `ab` im Code), aus dem statischen HTML/JS
  nicht ohne echte JS-Ausführung rekonstruierbar. Geraten (`bob`, aus dem
  ICY-Header `X-Audalaxy-Channelkey: bob`) — Ergebnis `{"result":
  {"found": "0"}}`, falscher Parameter. Aufgegeben (bräuchte einen
  Headless-Browser, um den echten Netzwerk-Request zu beobachten — nicht
  in dieser Umgebung verfügbar).
- **R.SH**: `rsh.de`-HTML enthält (im Gegensatz zu radiobob) das
  Konfigurationsobjekt server-seitig eingebettet mit der vollen URI:
  `uri:"https://stream-service.loverad.io/v4/rsh"`.
  Direkt abgerufen: liefert sauberes JSON, Struktur
  `{"1": {"song_title": "...", "artist_name": "...", "url_low": "//
  streams.rsh.de/rsh-live/mp3-128/homepage/", ...}}` — der Schlüssel "1"
  ist der Hauptkanal, `url_low`/`url_high` bestätigen, dass das exakt
  unser Stream ist. **Funktioniert und liefert echte Song/Interpret-
  Daten** (verifiziert: "Miley Cyrus - Flowers", später "Kygo, Khalid &
  Gryffin - Save My Love").
- Gleiches Muster (`stream-service.loverad.io/v4/<slug>`) für Radio Bob,
  Hamburg Zwei geraten (`bob`, `hamburg2`, `hh2`, `hamburgzwei`, `h2`) —
  alle `[]` (nicht gefunden). Hamburg Zwei referenziert `loverad.io`
  überhaupt nicht in seinem HTML (nutzt stattdessen `rmsi-player.de`,
  eine andere Plattform) — vermutlich kein Regiocast/loverad-Sender.
- SWR3 (ARD/öffentlich-rechtlich) hat eine Playlisten-Seite
  (`/playlisten/index.html`), aber auch dort keine im HTML sichtbare
  API-URL gefunden — vermutlich eigenes, nicht-triviales Backend.
- **Entscheidung**: kein genereller Website-Scraper gebaut (zu fragil —
  jede Sender-Homepage hat ihre eigene, sich ändernde clientseitige
  Render-Logik, würde dauerhaften Wartungsaufwand pro Sender bedeuten).
  Stattdessen: nur der eine konkret verifizierte, stabile Fallback (R.SH)
  eingebaut, mit klarer Struktur, um bei Bedarf weitere Sender zu
  ergänzen, sobald jemand deren Muster gefunden hat.

### Implementierung (webui.py)
- `_LOVERAD_STREAM_SERVICE_SLUGS = {"r-sh": "rsh"}` — Mapping Sender-ID
  (aus stations.json) -> Slug für die loverad.io-API.
- `_fetch_loverad_now_playing(slug)` ruft `stream-service.loverad.io/
  v4/<slug>` ab, liest Kanal `"1"`, baut `"<Interpret> - <Titel>"`.
- `_fetch_now_playing(station)` (Signatur geändert: nimmt jetzt das
  komplette Sender-dict statt nur die URL, gecacht per Sender-`id` statt
  URL) schaut zuerst im Mapping nach — falls vorhanden, loverad-Fallback,
  sonst ICY wie bisher.
- Isoliert getestet: R.SH liefert echten Song/Interpret über den
  Fallback, Radio Bob fällt korrekt auf ICY-Branding zurück (kein
  Mapping-Eintrag).

## 2026-08-02 (Fortsetzung) — Umbenennung radio_switch.py → radiozapper.py

Nutzerwunsch: das Hauptscript und alle Referenzen darauf durchgängig auf
den Projektnamen "RadioZapper" umbenennen.

- `radio_switch.py` -> `radiozapper.py` (per `git mv`, Docstring/Nutzungs-
  hinweis/argparse-Beschreibung im Dateiinhalt mit umbenannt)
- `Dockerfile`: `COPY`/`ENTRYPOINT` auf `radiozapper.py`, Kommentar-Beispiel
  `icecast-radioswitch` -> `icecast-radiozapper`
- `docker-compose.yml`: Service-Key `radio-switch:` -> `radiozapper:`,
  `container_name: radio-switch` -> `radiozapper`, Icecast-Container
  `icecast-radioswitch` -> `icecast-radiozapper` (inkl. aller internen
  `ICECAST_URL`/`ICECAST_ADMIN_URL`-Referenzen darauf)
- `webui.py`/`stations_store.py`: Docstring-Verweise auf `radio_switch.py`
  aktualisiert
- `HANDOVER.md`: alle Verweise durchgehend aktualisiert (lebendes
  Dokument, im Gegensatz zu diesem Session-Log bewusst nicht historisch
  gehalten)
- `v1/radio_switch.py` **bewusst nicht umbenannt** — das ist die
  archivierte allererste Version des Scripts, soll den historischen Stand
  repräsentieren, nicht den aktuellen Namen
- Alte Einträge weiter oben in dieser Datei referenzieren weiterhin
  `radio_switch.py`/`radio-switch` (Container) — bewusst so belassen,
  das ist ein chronologisches Protokoll und beschreibt akkurat, wie die
  Dinge zum jeweiligen Zeitpunkt hießen
- `__pycache__/` mit dem alten kompilierten Modulnamen gelöscht (war eh
  gitignored, nur lokale Aufräumarbeit)
- Rebuild + Redeploy: da sich die Container-Namen ändern, übernimmt
  `docker compose up -d --build` das nicht automatisch für die
  ALT-benannten Container (die werden zu "Orphans", laufen unter altem
  Namen weiter) — alte Container explizit gestoppt/entfernt, dann neu
  hochgefahren. Details zum genauen Vorgehen: siehe Deploy-Log unten,
  falls noch nicht bestätigt ergänzt.

## 2026-08-02 (Fortsetzung) — Fehlalarm beim Fingerprint-Switching

Nutzer meldet: "gerade hat er weggezappt, obwohl Musik lief und keine
Sprache — eben wieder" (zweimal innerhalb kurzer Zeit).

### Diagnose
- `docker compose logs -t radiozapper | grep "🎙|🎛|▶|🔁|SPEECH"` zeigt:
  beide Switches kamen NICHT vom regulären VAD-Speech-Streak-Pfad
  (`CONSECUTIVE_SPEECH_TO_SWITCH`), sondern über einen Fingerprint-
  Treffer (`🔁 Bekannter Jingle/Werbespot wiedererkannt`).
- `fingerprints.db` direkt inspiziert (`sqlite3`/Python): zum Zeitpunkt
  der Meldung gab es in der GESAMTEN DB nur **einen einzigen** gelernten
  Clip (`id=1`, Label "Radio Bob", erstmals gelernt 2026-08-02 14:55:07)
  — der aber bereits **59x** wiedererkannt wurde, zuletzt kurz
  hintereinander auf 90s90s, ffn und Hamburg Zwei (`times_seen` 56→59
  innerhalb weniger Minuten, log-bestätigt).
- Log zeigt außerdem `[fingerprint] ... N konsistente Hash-Matches` mit
  N zwischen 30 und 275 (Schwelle `MIN_HASH_MATCHES=12`) — das sind keine
  Zufallstreffer (die Shazam-artige Delta-Konsistenzprüfung filtert
  Rauschen zuverlässig raus), sondern eine echte, strukturell identische
  Audio-Wiederholung. VAD hatte vor jedem Switch tatsächlich hohe
  `speech_ratio`-Werte (bis 0.94) — die Erkennung selbst hat also
  technisch korrekt Sprache gefunden.
- Einordnung: sehr wahrscheinlich ein kurzer (~3s, das ist die gesamte
  Länge des per `FINGERPRINT_TRIGGER_SECONDS` gepufferten Clips), über
  ein gemeinsames Ad-/Sweeper-Netzwerk mehrerer Sender eingespielter
  Sting/Liner über einem Musikbett — technisch echte Sprache, aber genau
  die Art kurzer, in Musik eingebetteter Einspieler, die sich für einen
  Hörer nicht wie "jetzt kommt Werbung" anfühlt und für die ein kompletter
  Senderwechsel überzogen wirkt. Nicht abschließend verifizierbar ohne
  das Audio selbst zu hören — die Fingerprint-DB speichert nur Hashes,
  keine Audiodaten, das ursprüngliche Clip-Audio war nicht mehr
  rekonstruierbar.

### Mit dem Nutzer abgestimmtes Vorgehen (AskUserQuestion)
Optionen waren: (a) Clip löschen + Mitschnitt-Feature für künftige
Treffer einbauen, (b) nur Mitschnitt einbauen und abwarten, (c) global
striktere Fingerprint-Schwellwerte. Nutzer wählte (a).

### Umsetzung
- `radiozapper.py`: neue Funktion `save_fingerprint_debug_clip()` —
  schreibt jeden Fingerprint-Kandidaten (Treffer UND neu gelernte Clips,
  nicht nur Treffer) als WAV nach `fingerprint_clips/`
  (Dateiname enthält Clip-ID/Sender-ID/Timestamp), damit sich künftige
  Treffer tatsächlich anhören lassen statt nur an den Hash-Match-Zahlen
  zu raten. Räumt automatisch alte Mitschnitte weg
  (`FINGERPRINT_CLIPS_KEEP = 100`), damit unbeaufsichtigter Dauerbetrieb
  nicht unbegrenzt Speicher frisst.
- `docker-compose.yml`: neues Volume
  `./fingerprint_clips:/app/fingerprint_clips`, damit die Mitschnitte
  Container-Neustarts überleben und vom Host aus anhörbar sind.
  `.gitignore` um `fingerprint_clips/` ergänzt (Audiodaten, nicht Code).
- Fingerprint-DB bereinigt: Container gestoppt, Clip `id=1` (Label
  "Radio Bob", 59x gesehen) inkl. aller zugehörigen Hashes aus
  `fingerprints.db` gelöscht (`DELETE FROM hashes/clips WHERE ...`).
  DB ist jetzt leer, lernt bei nächster Gelegenheit neu — falls es sich
  wirklich um einen echten, störenden Werbespot handelt, wird der nach
  zwei erneuten Vorkommen wieder erkannt UND diesmal als WAV mitgehört
  werden können.
- WAV-Schreibmechanismus isoliert getestet (`save_fingerprint_debug_clip`
  direkt mit synthetischem PCM aufgerufen, resultierende Datei per
  `wave`-Modul verifiziert: korrekt Mono/44100Hz/1s). Realer Trigger im
  Live-Betrieb noch nicht abgewartet (bräuchte eine neue Sprache-Situation
  nach dem Reset) — Mechanismus aber unabhängig davon bestätigt korrekt.
- **Für später, falls das Muster wiederkehrt:** die WAVs unter
  `fingerprint_clips/*.wav` anhören (z.B. `newclip_*` = neu gelernt,
  `match_clip<id>_*` = Treffer) und dann entscheiden, ob es sich wirklich
  um störende Werbung handelt oder ob die Fingerprint-Schwellwerte
  (`MIN_HASH_MATCHES`, `FINGERPRINT_TRIGGER_SECONDS`) generell
  nachjustiert werden sollten.

## 2026-08-02 (Fortsetzung) — Korrektur-Knöpfe, Hintergrundbild, README statt HANDOVER

Vier Wünsche in einem Rutsch: zwei neue Buttons im Web-Interface, ein
neues Logo/Hero-Bild als Branding, und README.md statt HANDOVER.md.

### "Zapping-Fehler"-Knopf (Fingerprint-Treffer zurücknehmen)
- `fingerprint.py`: neue freistehende Funktion `delete_clip(db_path,
  clip_id)` — öffnet eine eigene kurze SQLite-Connection zur selben DB-
  Datei statt sich die laufende `FingerprintDB`-Instanz des Hauptprozesses
  zu teilen (sqlite3-Connection-Objekte sind nicht thread-übergreifend
  sicher, und `webui.py` läuft in einem anderen Thread). Gibt das Label
  des gelöschten Clips zurück, oder `None` falls die ID nicht mehr
  existiert.
- `webui.SwitcherState`: neue Felder `_last_fingerprint_clip` +
  `set_last_fingerprint_clip()`/`pop_last_fingerprint_clip()` (pop leert
  dabei, damit ein zweiter Klick ohne neuen Treffer dazwischen nicht ins
  Leere/denselben-schon-gelöschten-Clip läuft).
- `radiozapper.py`: ruft `state.set_last_fingerprint_clip(...)` direkt
  neben dem bereits vorhandenen `save_fingerprint_debug_clip()`-Aufruf im
  Match-Zweig auf.
- Neuer Endpunkt `POST /api/fingerprint/undo` in `webui.py` — braucht den
  Pfad zur Fingerprint-DB, dafür `make_handler()`/`start_server()` um
  einen `fingerprint_db_path`-Parameter erweitert, `radiozapper.py`
  übergibt `args.fingerprint_db` beim Aufruf.

### "Gesabbel!"-Knopf (manueller Sofort-Switch)
- `webui.SwitcherState`: neues Flag `_skip_requested` +
  `request_skip()`/`pop_skip_request()`, analog zum bestehenden
  Reload-Mechanismus.
- `radiozapper.py`: neue Prüfung im Hauptloop direkt nach dem manuellen-
  Switch-Check (gleiche Prioritätsebene, vor dem blockierenden
  `read_window()`) — bei gesetztem Flag: Streak/Buffer zurücksetzen,
  `do_switch("Nutzer meldete Gesabbel")` aufrufen. Läuft über denselben
  Code-Pfad wie die automatische Umschaltung (reihum zum nächsten aktiven
  Sender, bis Musik läuft) — keine Duplizierung der Umschalt-Logik.
- Neuer Endpunkt `POST /api/skip`.
- Beide Live getestet: Gesabbel-Klick löste sofort einen echten Wechsel
  aus (Log: `🎙 Nutzer meldete Gesabbel auf '1LIVE' — schalte um ...` ->
  `▶ Spiele: 80s80s`, innerhalb von ~1s nach dem Klick).

### Hintergrundbild (radiozapper.webp)
- Nutzer hat ein transparentes Hero-/Logo-Bild erstellt (1408×768,
  dunkler Hintergrund, "RADIOZAPPER"-Wortmarke mit Blitz-Icon, Tagline
  "ZAPPING AWAY! MODERATION · ADS · JINGLES · NEWS").
  Als `<img>`-Banner (nicht als CSS-`background-image` über die ganze
  Seite) oben auf Player- UND Config-Seite eingebaut — bei einem so
  bildlastigen Hero-Grafik-Stil hätte ein Vollflächen-Hintergrund die
  Lesbarkeit der eigentlichen UI-Elemente (Sender-Liste, Buttons)
  beeinträchtigt. Die bisherige `<h1>RadioZapper</h1>`-Textüberschrift auf
  der Player-Seite wurde `sr-only` (visuell versteckt, für Screenreader/
  Barrierefreiheit weiter vorhanden) — das Bild trägt die Wortmarke schon
  visuell.
- `webui.py`: Bild wird einmalig beim Modul-Import von der Platte gelesen
  (`_BANNER_BYTES`, kein Re-Read pro Request) und über eine neue Route
  `GET /radiozapper.webp` mit `Cache-Control: public, max-age=86400`
  ausgeliefert (Browser cached es dann über Player-/Config-Seite hinweg).
- `Dockerfile`: `COPY radiozapper.webp .` ergänzt, damit es im Image
  landet (statisches Asset, kein Volume-Mount nötig wie bei
  `stations.json`).
- Das vom Nutzer ebenfalls abgelegte `radiozapper.png` (2.3MB, unkomprimiert)
  bewusst nicht angefasst/committed — nicht Teil der Anfrage, könnte z.B.
  als Favicon-Rohmaterial gedacht sein, aber das war nicht spezifiziert.

### README.md statt HANDOVER.md
- `HANDOVER.md` war ursprünglich als Übergabe-Dokument AN Claude Code
  gedacht (Rahmen: "hier ist der Stand, mach weiter") — passend für die
  Zusammenarbeit hier, aber keine sinnvolle Projekt-Doku für jemand
  anderen, der auf das Repo stößt. Per `git rm` entfernt.
- `README.md` neu geschrieben: was RadioZapper macht, wie die Erkennung
  funktioniert (VAD + Heuristik-Fallback + Fingerprinting), Web-Interface-
  Überblick (inkl. der beiden neuen Knöpfe), Architektur-Tabelle,
  Setup-/Deploy-Befehle, `.env`-Variablen-Tabelle, bekannte
  Einschränkungen. Bewusst knapper und nach außen gerichtet als
  `HANDOVER.md` war — der detaillierte Debugging-Verlauf bleibt weiter
  hier im Session-Log, nicht in der README verlinkt (README ist für
  Nutzer/Betrachter des Repos, nicht für die Weiterarbeit mit Claude Code).

### Nebenbefund beim Testen: Fingerprinting scheint systematisch zu über-matchen
- Direkt nach dem DB-Reset von vorhin (siehe voriger Eintrag) bereits
  wieder ein neuer Clip gelernt (`id=2`, Label "1LIVE") und binnen
  weniger Minuten **8x** quer über fast alle konfigurierten Sender
  (80s80s, 90s90s, Hamburg Zwei, Radio Bob, Rock Antenne Hamburg, R.SH)
  getroffen — bestätigt per `fingerprint_clips/*.wav`-Mitschnitten.
  Audio-Analyse der Clips (RMS/Peak/Silence-Ratio) zeigt: keine Stille,
  substantielle Energie (RMS 5600–7200) — also kein Stille-Puffer-Bug.
- Einordnung (noch nicht behoben, nur notiert): `_spectrogram_peaks()` in
  `fingerprint.py` wählt pro Zeitframe schlicht die N stärksten FFT-Bins
  als "Peaks", ohne Frequenzband-Aufteilung (der Original-Shazam-
  Algorithmus teilt das Spektrum in mehrere Bänder auf und wählt Peaks
  pro Band, gerade um genau diese Art Über-Matching zu vermeiden). Bei
  stark komprimiertem/gemastertem Broadcast-Radio-Audio (loudness-war-
  typisch bass-lastig, wenig dynamische Bandbreite) konzentrieren sich
  die stärksten Peaks vermutlich systematisch auf ähnliche
  Frequenzbereiche über völlig unterschiedliche Songs/Sprecher hinweg,
  was zu zufällig aber konsistent wirkenden Hash-Treffern führt, auch bei
  komplett unterschiedlichem Audio-Inhalt. Nicht angefasst — wäre ein
  größerer Umbau von `fingerprint.py` (Band-Aufteilung einführen), nicht
  Teil dieser Anfrage. Der neue "Zapping-Fehler"-Knopf ist das direkte
  Werkzeug, um damit im Alltag umzugehen, löst aber nicht die
  Grundursache. Für später: falls das Muster sich häuft, lohnt sich ein
  Umbau der Peak-Auswahl auf Frequenzbänder.

## 2026-08-02 (Fortsetzung) — "Sabbelfilter deaktivieren"-Knopf

Dritter Korrektur-Knopf, unter "Zapping-Fehler"/"Gesabbel!": komplettes
Ein-/Ausschalten der automatischen Sprache-Erkennung (VAD/Heuristik/
Fingerprint), für Situationen wo man die Automatik eine Weile ignorieren
will (z.B. ein Hörspiel/Feature auf einem sonst Musik-Sender laufen
lassen, ohne dass RadioZapper ständig wegschaltet).

### Umsetzung
- `webui.SwitcherState`: `_filter_enabled` (Default `True`) +
  `filter_enabled`-Property (Read) / `set_filter_enabled()` (Write) —
  plus ein separates Request-Flag-Pärchen `request_filter_toggle()`/
  `pop_filter_toggle_request()` — analog zu Reload/Skip. Bewusst NICHT
  direkt aus dem Webserver-Thread umgedreht: der Hauptloop muss beim
  tatsächlichen Umschalten auch `speech_streak`/`speech_buffer`/
  `fp_checked_this_run` zurücksetzen (sonst könnte ein alter, vor dem
  Deaktivieren aufgelaufener Sprache-Streak beim Wieder-Aktivieren
  sofort einen Switch auslösen, obwohl die Ursache längst vorbei ist).
- `radiozapper.py`: neue Prüfung im Hauptloop (gleiche Ebene wie Reload/
  Manual/Skip, vor dem blockierenden `read_window()`) — pollt das
  Toggle-Request-Flag, dreht `filter_enabled` um, setzt die Streak-
  Variablen zurück, loggt `🔇 Sabbelfilter (de)aktiviert`. Die eigentliche
  Filterwirkung sitzt weiter unten: direkt nach `output.write(pcm_stereo)`
  (Audio läuft also immer weiter) und vor `classify(pcm)` — bei
  deaktiviertem Filter wird die komplette Erkennung (VAD-Klassifikation,
  Fingerprint-Check, Streak-Zählung) übersprungen, `continue` zur
  nächsten Iteration. Manueller Switch, "Gesabbel!" und "Zapping-Fehler"
  bleiben davon unberührt (laufen als eigene Zweige VOR dieser Prüfung).
- `webui.py`: neuer Endpunkt `POST /api/filter/toggle`, `filter_enabled`
  zusätzlich in `/api/status` exponiert.
- Frontend: dritter Button unter der bestehenden Zwei-Button-Reihe,
  zentriert (`.filter-toggle-row`, eigene Zeile statt `flex:1` in der
  `.action-buttons`-Reihe). Label wechselt dynamisch je nach Zustand
  ("Sabbelfilter deaktivieren" / "Sabbelfilter aktivieren"), zusätzlich
  roter Rand/Text (`.disabled-state`) wenn der Filter gerade aus ist —
  klar sichtbar auch ohne den Button-Text genau zu lesen.
- Live getestet: Toggle aus -> `[vad]`-Zeilen verschwinden komplett aus
  dem Log, Stream bleibt aber gesund (weiterhin 44.1kHz/Stereo via
  `ffprobe` bestätigt) und spielt normal weiter. Toggle wieder an ->
  `[vad]`-Zeilen kommen sofort zurück. Beide Richtungen im Log bestätigt
  (`🔇 Sabbelfilter deaktiviert (automatisches Umschalten pausiert).` /
  `🔇 Sabbelfilter wieder aktiviert (automatisches Umschalten läuft
  weiter).`).

## 2026-08-02 (Fortsetzung) — Vorausschauendes Puffern der nächsten 5 Sender

Wunsch: die nächsten 5 Sender in Rotationsreihenfolge sollen 10 Sekunden
vorgepuffert werden, damit Wechsel flüssig ablaufen — bisher musste jeder
Wechsel (auch der "fresh path" nach `quick_forward()`) trotz aller
bisherigen Latenz-Fixes immer noch neu verbinden.

### Architektur
- Neue Klasse `PrebufferedSource` (nach `StreamSource`): hält im
  Hintergrund eine eigene `StreamSource` am Laufen, ein Thread liest in
  denselben `WINDOW_SECONDS`-Schnipseln wie der Hauptloop und sammelt sie
  in zwei `collections.deque(maxlen=10)` (Mono+Stereo, je 10 Fenster =
  10s bei `WINDOW_SECONDS=1.0`). `promote()` stoppt den Thread (Event +
  `join()`, wartet höchstens auf das gerade laufende `read_window()` —
  Pipes dürfen nie von zwei Seiten gleichzeitig gelesen werden) und gibt
  `(mono, stereo, source)` zurück: die gepufferten Sekunden als fertige
  Arrays plus die weiterlaufende `StreamSource` zur Übernahme durch den
  Hauptloop. `stop()` verwirft die Quelle komplett.
- Modulweite Helper `prebuffer_target_ids(current_id, active)` (liefert
  die nächsten `PREBUFFER_COUNT=5` IDs in derselben Rotationsreihenfolge,
  die `do_switch()` auch automatisch durchprobieren würde) und
  `sync_prebuffer(prebuffer, current_id, active, sample_rate)` (startet/
  stoppt Hintergrund-Puffer, bis das `prebuffer`-dict genau die
  gewünschten IDs enthält; ersetzt außerdem Puffer, deren Quelle
  unterwegs gestorben ist, durch einen frischen Versuch).
- `main()`: `prebuffer = {}`-dict, initial befüllt direkt nach dem
  ersten Senderstart. Neuer Helper `switch_to_station(station)` — nutzt
  einen laufenden Puffer falls vorhanden (übernimmt die `StreamSource`,
  schreibt den kompletten gepufferten Stereo-Batch sofort an den
  Output), sonst der bisherige frische Connect + `quick_forward()`.
  Ersetzt die alten `source.start()+quick_forward()`-Aufrufe im
  manuellen und im Reload-erzwungenen Switch-Zweig.
- `do_switch()` (automatisches Durchprobieren, auch vom "Gesabbel!"-
  Knopf genutzt) geht jetzt beim Kandidaten-Check zuerst im
  `prebuffer`-dict nach: ist der Kandidat gepuffert, wird direkt anhand
  des LETZTEN gepufferten Fensters klassifiziert (kein `time.sleep(1.5)`
  + frisches Fenster nötig — die gepufferten 10s liefern sogar mehr
  Kontext als der bisherige Einzel-Snapshot). Bei "music": sofort
  übernehmen, kompletten Puffer an den Output schreiben, fertig. Bei
  "speech": Kandidat verwerfen (`.stop()`), nächster in der Reihe. Nicht
  gepufferte Kandidaten (z.B. jenseits der ersten 5) laufen weiter über
  den alten Pfad.
- `sync_prebuffer()` wird einmal pro Hauptloop-Durchlauf aufgerufen
  (ganz oben in `while True:`, vor allen anderen Request-Checks) — billig
  genug (Soll/Ist-Vergleich zweier ID-Mengen) für die ~1s-Taktung, hält
  den Puffer nach JEDEM Wechsel automatisch auf dem aktuellen Stand,
  ohne dass jede einzelne Switch-Stelle sich selbst darum kümmern muss.
- `finally`-Block räumt jetzt auch alle noch offenen Puffer-Quellen auf
  (`for pb in prebuffer.values(): pb.stop()`), nicht nur die aktuelle.

### Ressourcen-Tradeoff
Bis zu `PREBUFFER_COUNT=5` zusätzliche ffmpeg-Prozesse laufen jetzt
parallel zum aktuellen Sender (jeweils Mono+Stereo-Dual-Pipe wie die
Haupt-`StreamSource`) — live gemessen: 7 ffmpeg-Prozesse insgesamt
(1 aktuell + 5 gepuffert + 1 Icecast-Encoder), ~5% CPU, ~190MB RAM auf
Dockfish (62GB RAM verfügbar) — für den vorhandenen Host unkritisch,
aber explizit erwähnenswert, falls RadioZapper mal auf schwächerer
Hardware läuft oder die Senderzahl/PREBUFFER_COUNT deutlich wächst.

### Getestet
- Isoliert: `PrebufferedSource` gegen echten SWR3-Stream, 6s laufen
  lassen, `promote()` liefert exakt die erwarteten ~5s Mono/Stereo-
  Samples (Puffer-Größe für den Test testweise auf 5s statt 10s gesetzt).
- `prebuffer_target_ids()` gegen mehrere Randfälle (2/3/6+ Sender,
  aktueller Sender nicht in der Liste, nur 1 Sender aktiv) — alle
  korrekt.
- Live im Container: Start zeigt `⏱ Puffere die nächsten 5 Sender 10s im
  Voraus.`, `/proc`-Prozesszählung bestätigt 7 ffmpeg-Prozesse.
  Manueller Switch auf einen gepufferten Sender (80s80s) zeigt
  `🎛 Manuell umgeschaltet auf: 80s80s (aus Puffer)` im Log, danach
  weiterhin exakt 7 ffmpeg-Prozesse (kein Leck) und Stream weiterhin
  44.1kHz/Stereo via `ffprobe`. "Gesabbel!"-Klick (nutzt `do_switch()`)
  auf den nächsten gepufferten Kandidaten zeigt
  `▶ Spiele: 90s90s (aus Puffer, nahtlos)` — der automatische Pfad nutzt
  den Puffer also ebenfalls korrekt.

## 2026-08-02 (Fortsetzung) — Puffer-Einstellungen, Zapping-Fehler-Fix, README-Warnung

Drei Dinge in einem Rutsch.

### Puffer-Parameter über /config einstellbar
- Neues Modul `settings_store.py` (analog zu `stations_store.py`):
  persistiert `{"prebuffer_seconds": float, "prebuffer_count": int}` in
  `settings.json`, eigener Lock, direktes Schreiben (kein temp+rename —
  gleiche Bind-Mount-Einschränkung wie bei `stations.json`), Validierung
  gegen grobe Leitplanken (`LIMITS`: 0–60s, 0–20 Sender).
- `webui.SwitcherState`: `_prebuffer_seconds`/`_prebuffer_count` als
  weitere Felder, die `reload()` (derselbe Mechanismus wie für
  Sender-Änderungen) mit befüllt — Properties `prebuffer_seconds`/
  `prebuffer_count` zum Auslesen.
- Neue Endpunkte `GET/POST /api/config/settings`. POST validiert über
  `settings_store.update()` und ruft `state.request_reload()` — nutzt
  denselben Reload-Mechanismus wie Sender-Änderungen, kein separater
  Pfad nötig.
- `radiozapper.py`: `prebuffer_target_ids()`/`sync_prebuffer()` nehmen
  jetzt `count`/`buffer_seconds` als Parameter (vorher globale
  Konstanten) — Aufrufer übergeben `state.prebuffer_seconds`/
  `state.prebuffer_count`. Beim Reload wird geprüft, ob sich die
  Puffer-Einstellungen tatsächlich geändert haben (Vergleich vor/nach
  `state.reload()`); falls ja, werden ALLE bestehenden Puffer verworfen
  (`PrebufferedSource`-Instanzen haben ihre Puffergröße als
  `deque(maxlen=...)` fest einkompiliert bei der Konstruktion — ändert
  sich die gewünschte Sekundenzahl, taugen bestehende Puffer nicht mehr)
  und beim nächsten Schleifendurchlauf mit den neuen Werten frisch
  aufgebaut.
- Config-Seite: neue Sektion "⏱ Puffer-Einstellungen" mit zwei
  Zahlenfeldern + Speichern-Button, unterhalb von "Neuer Sender".
- Live getestet: Settings auf 3×6s geändert -> Log bestätigt
  `⏱ Puffer-Einstellungen geändert: 3 Sender × 6s.`, `/proc`-
  Prozesszählung fällt korrekt von 7 auf 5 ffmpeg-Prozesse
  (1 aktuell + 3 gepuffert + 1 Icecast). Zurück auf 5×10s gesetzt ->
  wieder 7 Prozesse. `settings.json` auf dem Host korrekt aktualisiert.

### "Zapping-Fehler" schaltet jetzt auch zum vorherigen Sender zurück
Nutzer meldete: der Knopf löscht zwar den Clip, springt aber nicht zum
Sender zurück, der vor dem fälschlichen Switch lief — und "löscht immer
nur den Clip 1LIVE". Zweiteres bei Live-Prüfung der DB relativiert: zum
Meldezeitpunkt war es tatsächlich durchgehend derselbe Clip (jeweils
aktuelle ID, aber ja korrekt der EINE Clip, der gerade der Wiederholungs-
täter war) — deckt sich mit dem bereits dokumentierten systemischen
Fingerprint-Problem (Peaks ohne Frequenzband-Trennung, siehe Eintrag
weiter oben "Fehlalarm beim Fingerprint-Switching"), keine zusätzliche
Bug gefunden. Der fehlende Rücksprung war aber eine echte Lücke:

- `state.set_last_fingerprint_clip()` bekommt jetzt zusätzlich
  `previous_station_id` (der Sender, der lief, BEVOR der Fingerprint-
  Treffer `do_switch()` auslöste — in `radiozapper.py` einfach
  `current["id"]`, ausgelesen bevor `do_switch()` den Wert überschreibt).
- `_handle_fingerprint_undo()` in `webui.py`: löscht den Clip wie bisher,
  ruft zusätzlich `state.request_switch(prev_id)` auf (derselbe
  Mechanismus wie ein normaler manueller Switch — kein neuer Code im
  Hauptloop nötig, `switch_to_station()` inkl. Prebuffer-Erkennung greift
  automatisch). Antwort enthält jetzt `switched_back_to` (Sendername oder
  `null`, falls der vorherige Sender inzwischen deaktiviert wurde).
- Frontend zeigt jetzt "✓ Clip gelöscht: X — zurück zu Y" und ruft
  `refresh()` auf, damit der Sender-Wechsel sofort sichtbar wird.
- Live getestet: Undo-Klick liefert
  `{"ok": true, "label": "ffn", "switched_back_to": "ffn"}`, Log
  bestätigt `🎛 Manuell umgeschaltet auf: ffn`. Clip danach nachweislich
  aus der DB verschwunden (`SELECT * FROM clips` liefert `[]`).
  Wiederholter Klick ohne neuen Treffer dazwischen liefert sauber 404
  ("Kein kürzlicher Fingerprint-Treffer zum Zurücknehmen"), kein Fehler.

### README: Warnung vor öffentlichem Betrieb
Neuer, prominent platzierter Abschnitt direkt nach der Einleitung (vor
"Wie die Erkennung funktioniert"): RadioZapper ist ausdrücklich nicht für
öffentlichen Betrieb gedacht, muss immer hinter VPN/Tailscale laufen.
Zwei Gründe explizit benannt (Nutzer-Formulierung sinngemäß übernommen,
in Fließtext gegossen): unkontrollierter Bandbreiten-/Ressourcenverbrauch
bei offener Erreichbarkeit, und urheberrechtliches Risiko durch
öffentliche Weiterverbreitung fremder lizenzierter Radioprogramme (Zitat
sinngemäß: "genug Kanzleien, für die das ein Geschäftsmodell ist").
Bisherige schwächere Erwähnung unter "Bekannte Einschränkungen" gekürzt,
verweist jetzt auf die neue Sektion statt zu duplizieren.

## 2026-08-02 (Fortsetzung) — Fingerprint-Algorithmus überarbeitet (Frequenzbänder/2D-Peaks)

Auftrag: das schon mehrfach dokumentierte systemische Fingerprint-
Problem (ein Clip matcht quer über viele Sender) tatsächlich an der
Wurzel angehen, nicht nur mit dem "Zapping-Fehler"-Knopf drumherum
kurieren.

### Diagnose mit echten Daten
- `fingerprint_clips/` enthielt zu diesem Zeitpunkt 26+ echte, vom
  laufenden System aufgezeichnete 3s-Sprache-Clips aus verschiedenen
  Sendern/Zeitpunkten — ein realistischer Testdatensatz, kein
  synthetisches Beispiel.
- Cross-Match-Test (alle 351 möglichen Paare) mit dem ALTEN Algorithmus
  (Top-5-Peaks pro Frame global, ohne Frequenzband-Trennung): **351 von
  351 Paaren "matchten"** mit bis zu 6455 Hashes/Clip, bei einer Schwelle
  von nur 12 nötigen Treffern. Bestätigt: nicht ein unglücklicher
  Einzel-Clip, sondern ein strukturelles Problem des Algorithmus selbst.
- Erster Fix-Versuch (nur Frequenzband-Aufteilung, 1 Peak pro Band statt
  Top-5 global, wie ursprünglich vom Nutzer vermutet): brachte die
  Match-Stärke deutlich runter (6000er auf 100-600er Bereich), aber
  **immer noch 351 von 351 Paaren matchten**. Frequenzband-Trennung
  allein reichte nicht.
- Tiefer gegraben: FFT-Analyse der Clips zeigte dominante Energie bei
  ~48-52Hz (und Oberwellen ~95-105Hz) in mehreren Clips — sehr nah an
  50Hz-Netzbrumm. Test mit Ausschluss der Bänder unter 200/300/500Hz:
  **immer noch 351/351 Paare matchten** (Match-Stärke sank weiter,
  Brumm-Ausschluss half, war aber nicht die Hauptursache).
- Eigentliche Ursache identifiziert: die Peak-Auswahl nahm pro
  Zeitframe/Band einfach den LAUTESTEN Bin — bei Sprache ist "lautester
  Bin" aber kein content-spezifisches Merkmal (menschliche Formanten
  liegen bei praktisch jedem Sprecher in ähnlichen Frequenzbereichen),
  das erzeugt über verschiedene Sprecher/Sender hinweg systematisch
  ähnliche, damit fälschlich matchende Hash-Muster. Der originale
  Shazam-Algorithmus verlangt deshalb ECHTE lokale Maxima in Zeit UND
  Frequenz (2D), nicht einfach "laut" — nur echte Landmarken-Ereignisse
  (Onsets, markante Töne), keine dauerhaft anwesende generische Energie.
- Mit echter 2D-Peak-Erkennung (lokales Maximum in einer
  Zeit-Frequenz-Nachbarschaft, log-Magnitude, Schwelle relativ zu
  Mittelwert+Standardabweichung) im selben 351-Paar-Test: **nur noch 1
  von 351 Paaren matchte** (Match-Stärke 14, knapp über der alten
  Schwelle von 12) — Rest lag bei 0-8 Treffern, Median 2.
- Gegentest (Robustheit auf ECHTE Wiederholungen darf nicht leiden):
  identischer Clip -> 702/702 Treffer; mit Rauschen -> 651/722; mit
  halber Lautstärke -> 684/722; mit 0.1s Zeitversatz -> 104/626. Klare
  Trennung zwischen echten Wiederholungen (100-700+) und verschiedenem
  Inhalt (0-14) — vorher gab's diese Trennung schlicht nicht.

### Implementierung (fingerprint.py)
- `_spectrogram()`: STFT wie bisher, jetzt aber als eigene Funktion (das
  volle Spektrogramm wird für die 2D-Nachbarschaftsanalyse gebraucht,
  nicht mehr Frame für Frame isoliert verarbeitet).
- `_local_max_mask()`: reines-NumPy-Ersatz für
  `scipy.ndimage.maximum_filter` (kein `scipy` als neue Abhängigkeit für
  eine einzelne 2D-Sliding-Window-Operation — Projekt-Stil ist bewusst
  minimal an Dependencies, siehe Modul-Docstring).
- `_spectrogram_peaks()`: schließt zuerst alles unter `MIN_FREQ_HZ=200`
  aus (Brumm/Rumpeln), rechnet dann Log-Magnitude, findet 2D-lokale
  Maxima (`PEAK_NEIGHBORHOOD_TIME=5` Frames ≈ 58ms,
  `PEAK_NEIGHBORHOOD_FREQ=15` Bins ≈ 630Hz) über
  `PEAK_AMP_MIN_FACTOR=3.0` Standardabweichungen über dem Mittelwert.
- `MIN_HASH_MATCHES` von 12 auf 25 angehoben — Sicherheitsabstand zum
  stärksten beobachteten Fehltreffer (14) im Testdatensatz, weit unter
  dem, was echte Wiederholungen erreichen (100+).
- `PEAKS_PER_FRAME`-Konstante entfernt (kein "Top-N pro Frame" mehr),
  Modul-Docstring um einen ausführlichen "Warum 2D-Peaks"-Abschnitt
  ergänzt, der die ganze Diagnose oben zusammenfasst (für den Fall, dass
  hier nochmal jemand ansetzen muss).
- Hash-Format (`f"{f1}-{f2}-{dt}"`) und Voting-Mechanismus (konsistenter
  Zeitversatz zählt) unverändert — nur WELCHE Peaks überhaupt in die
  Hash-Bildung eingehen, hat sich geändert.
- Performance: ~7ms pro 3s-Clip (gemessen) — komplett vernachlässigbar,
  läuft eh nur einmal pro Fingerprint-Check (alle paar Sekunden), nicht
  im Audio-Hot-Path.

### Verifiziert über das echte Modul (nicht nur das Test-Script)
- Alle 28 echten Clips aus `fingerprint_clips/` nacheinander durch das
  echte `FingerprintDB.match_or_learn()` gejagt (frische Test-DB): 0
  Fehltreffer, alle 28 korrekt als neu/verschieden erkannt.
- Echte Wiederholung (derselbe Clip zweimal, dann mit Rauschen) über das
  echte Modul: korrekt erkannt (675 bzw. 627 konsistente Treffer, `[fingerprint]
  Treffer: ...`-Log bestätigt).
- `fingerprints.db` vor dem Deploy komplett geleert (alte Hashes stammen
  vom fehlerhaften Algorithmus, nicht sinnvoll mit neuen vergleichbar,
  lernt jetzt sauber neu). Rebuild + Deploy, Container startet sauber,
  Stream weiterhin 44.1kHz/Stereo, erster neuer Clip (#6 — SQLite-
  AUTOINCREMENT zählt über die geleerten alten IDs hinaus weiter, kein
  Bug) korrekt gelernt. Längerfristige Beobachtung (bleibt die
  Fehlerquote im Live-Betrieb niedrig?) noch ausstehend — dafür sind die
  WAV-Mitschnitte (`fingerprint_clips/`) weiterhin aktiv, falls doch mal
  wieder was Verdächtiges auftaucht.

## 2026-08-02 (Fortsetzung) — Sender-Import aus Kodinerds-Radioliste

Neue Funktion nach genauer Spezifikation: Sender aus einer M3U-Playlist
(Default: Kodinerds-Kodi-Radioliste) importieren, mit Erreichbarkeits-
Check, Duplikat-Vermeidung, Kategorie "Unsortiert", Fortschrittsanzeige.
Zusätzlich ein "Clip-DB leeren"-Knopf für die Fingerprint-DB.

### Vorarbeit (wie angefordert): bestehende Struktur zuerst angeschaut
- `stations_store.py`: Schema `{id, name, url, category, enabled}`,
  `CATEGORIES`-Liste bestimmt Anzeigereihenfolge auf der Config-Seite
  (einfache Iteration `for cat of categories`).
- `settings_store.py`: bereits etabliertes Muster für Config-Seiten-
  Einstellungen (`DEFAULTS`/`update()`/eigener Lock) — für die Import-URL
  wiederverwendet statt was Neues zu erfinden.
- Reale M3U live geladen (`http://bit.ly/kn-kodi-radio` -> redirectet zu
  einem GitHub-Raw-Link, 362 Einträge): Extended-M3U-Format,
  `#EXTINF:-1 tvg-name="..." group-title="..." ...,Anzeigename` gefolgt
  von der Stream-URL-Zeile — Name ist der Teil NACH dem letzten Komma.

### Erreichbarkeits-Check: ffprobe statt HTTP HEAD/GET (Begründung)
Live an drei Beispielen verifiziert, bevor die Entscheidung fiel:
- Normale Stream-URL: ffprobe liefert in ~1s `audio`.
- Eine verschachtelte `.m3u`-Playlist-URL (kommt in der Kodinerds-Liste
  vor, z.B. AntenneSaar): ffprobe lehnt mit "Invalid data found when
  processing input" ab — und das ist KORREKT, denn unser eigener Player
  (`StreamSource` in radiozapper.py) ruft genau denselben
  `ffmpeg -i url`-Demuxer-Stack auf und könnte diese URL genauso wenig
  direkt abspielen. Ein simples HTTP-GET hätte 200 OK gemeldet und die
  URL fälschlich als "erreichbar" durchgewunken.
- Nicht existente Domain: DNS-Fehler in ~0.06s, kein Hängen.
Begründung: ffprobe prüft exakt das, was zählt ("kann unser Player das
wirklich abspielen"), nicht nur "antwortet der Server auf HTTP".

### Implementierung
- `stations_store.py`: `CATEGORIES` um `"Unsortiert"` erweitert (bewusst
  als LETZTES Element -> erscheint auf der Config-Seite immer nach allen
  "richtigen" Kategorien, ohne Sonderlogik nötig — die Seite iteriert
  einfach in `CATEGORIES`-Reihenfolge). Neue Konstante `IMPORT_CATEGORY
  = "Unsortiert"`. Neue Funktion `bulk_add(entries, category)`: ein
  Read+Write für die ganze Charge statt einem Lock-Zyklus pro Sender
  (bei 300+ Sendern spürbar effizienter als N Einzel-`add()`-Aufrufe).
- `settings_store.py`: `import_url` zu `DEFAULTS` hinzugefügt (Default
  `http://bit.ly/kn-kodi-radio`), `update()` validiert auf nicht-leer +
  `http(s)://`-Präfix (kein `LIMITS`-Zahlenbereich wie bei den
  Puffer-Werten, andere Art von Eingabe).
- `fingerprint.py`: neue Funktion `clear_all(db_path)` — löscht ALLE
  Clips+Hashes (nicht die Datei, nicht stations.json), eigene kurze
  SQLite-Connection aus demselben Grund wie `delete_clip()` (Thread-
  Sicherheit gegenüber der laufenden `FingerprintDB`-Instanz).
- Neues Modul `station_import.py`: `fetch_m3u()` (urllib, User-Agent
  gesetzt), `parse_m3u()` (Regex auf `#EXTINF:[^,]*,(.*)$` für den
  Namen, nächste Nicht-Kommentar-Zeile als URL), `check_reachable()`
  (ffprobe-Aufruf mit Timeout, siehe oben), `run_import()`
  (Orchestrierung: laden -> `ThreadPoolExecutor(max_workers=10)` für die
  parallelen Checks mit `as_completed()` -> Duplikat-Filter gegen
  bestehende Sender UND innerhalb der eigenen Charge -> `bulk_add()`).
  `run_import()` nimmt ein optionales `progress`-Objekt
  (`set_phase()`/`increment_checked()`) für die Fortschrittsanzeige,
  ohne dass das Modul selbst irgendwas über HTTP/Threading auf
  Webserver-Seite wissen muss (sauberer Schnitt).
- `webui.py`:
  - Neue Klasse `ImportState` (analog zu `SwitcherState`, aber komplett
    unabhängig davon — der Import ist reine Webserver-Sache, der
    Hauptloop bekommt davon nichts mit außer dem normalen
    `state.request_reload()` danach, genau wie bei jeder anderen
    Sender-Änderung über die Config-Seite). Eine Instanz pro
    `make_handler()`-Aufruf (= einmal pro Prozesslaufzeit).
  - Import läuft in einem `threading.Thread(daemon=True)` — bei 362
    Sendern und 10-facher Parallelität dauert der komplette Durchlauf
    ca. 40-45s (live gemessen), das würde einen synchronen HTTP-Request
    blockieren; `POST /api/config/import/start` gibt deshalb sofort
    zurück, `GET /api/config/import/status` liefert den Fortschritt zum
    Pollen. Schutz gegen doppeltes Starten: `ImportState.start()` gibt
    `False` zurück, wenn schon einer läuft -> Endpunkt antwortet mit 409.
  - Neuer Endpunkt `POST /api/fingerprint/clear` für den "Clip-DB
    leeren"-Knopf (Sicherheitsabfrage sitzt im Frontend als
    `confirm()`, nicht serverseitig — reicht für eine Config-Seite ohne
    Auth im eigenen Netz).
  - `_handle_update_settings` um `import_url` erweitert (nutzt einfach
    `settings_store.update()`s neuen Parameter durch).
  - Config-Seite: neue Sektion "📻 Sender-Import" (URL-Feld,
    "Sender importieren"-Button, Fortschritts-/Ergebnisanzeige mit
    1s-Polling) und "🗑 Fingerprint-Datenbank" (Button + `confirm()`).
    Beim Laden der Config-Seite wird außerdem geprüft, ob gerade schon
    ein Import läuft (z.B. nach einem Seiten-Reload mittendrin) und das
    Polling ggf. sofort fortgesetzt.
- `Dockerfile`: `COPY station_import.py .` ergänzt. Kein neues Volume in
  `docker-compose.yml` nötig — `station_import.py` ist Code (landet im
  Image), schreibt nur in bereits gemountete `stations.json`/
  `settings.json`.

### Verifiziert (echter Import gegen die echte Kodinerds-Liste, kein Mock)
- Isolierter Test zuerst: `parse_m3u()` gegen die echte Liste -> 362
  Sender korrekt geparst. `run_import()` mit einer kleinen Test-Playlist
  (2x derselbe erreichbare Sender, 1 nicht-existent, 1 verschachtelte
  M3U, gegen eine vorbelegte Test-`stations.json` mit einem Namens- UND
  einem URL-Duplikat) -> alle Fälle korrekt behandelt: `checked=5,
  working=3, added=1`, Namens- und URL-Duplikate beide korrekt
  übersprungen, verschachtelte M3U und nicht-existente Domain korrekt
  als nicht erreichbar erkannt.
- Danach live über die echten API-Endpunkte gegen die echte, gerade
  laufende stations.json (12 Sender): Import gestartet, Fortschritt per
  Polling verfolgt (`19 von 362` -> `333 von 362` -> `362 von 362`),
  Ergebnis `{"checked": 362, "working": 349, "added": 344}`. Finale
  `stations.json`-Prüfung: 356 Sender gesamt (12+344), keine
  Namensduplikate, "Unsortiert" korrekt mit 344 Einträgen, erscheint auf
  der Config-Seite nach allen anderen Kategorien.
  **Diese 344 Sender sind jetzt live im System** (nicht zurückgesetzt —
  legitimer Test des Features mit echten Daten, Kategorisierung/Aufräumen
  bleibt dem Nutzer über die Config-Seite überlassen, genau wie das
  Feature es vorsieht).
- 409-Schutz gegen doppelten Start verifiziert (zweiter Start-Request
  während des laufenden ersten Imports -> `409 Es läuft bereits ein
  Import.`).
- Re-Import (gleiche URL, gegen die jetzt schon 356 Sender umfassende
  Liste) korrekt mit `added: 0` (alle 347 erreichbaren waren schon
  vorhanden) — Dedupe funktioniert auch über mehrere Import-Läufe hinweg.
- Player währenddessen durchgehend stabil: trotz Sprung von 12 auf 356
  aktive Sender blieb die Prozesszahl bei 7 ffmpeg-Prozessen (Prebuffer-
  Anzahl ist unabhängig von der Gesamt-Senderzahl), Stream weiterhin
  44.1kHz/Stereo.
- `fingerprint.clear_all()` über `POST /api/fingerprint/clear` getestet:
  13 Clips gelöscht, DB danach leer, `stations.json` unangetastet.

## 2026-08-02 (Fortsetzung) — "Alle deaktivieren"-Knopf pro Sender-Kategorie

Direkter Auslöser: nach dem Import (344 neue Sender in "Unsortiert")
wollte der Nutzer nicht jeden einzeln per Haken deaktivieren müssen.

### Implementierung
- `stations_store.py`: neue Funktion `set_category_enabled(category,
  enabled) -> int` — ein Read+Write für die ganze Kategorie statt einem
  Request pro Sender (gleiches Muster wie `bulk_add()` vom Import).
  Zählt nur tatsächlich geänderte Sender (die schon den Zielzustand
  hatten, zählen nicht mit).
- `webui.py`: neuer Endpunkt `POST /api/config/categories/<category>/disable-all`
  (Pfad-Segment per `urllib.parse.unquote` dekodiert, falls eine
  Kategorie mal Sonderzeichen/Leerzeichen bekommt), validiert gegen
  `stations_store.CATEGORIES`, ruft danach `state.request_reload()` wie
  jede andere Sender-Änderung auch.
- Config-Seite: jede Kategorie-Überschrift (`<h2>`) ist jetzt eine
  Flex-Zeile mit Name + Button "Alle deaktivieren" rechts. Button
  erscheint nur, wenn die Kategorie mindestens einen AKTIVIERTEN Sender
  hat (bei leerer oder schon komplett deaktivierter Kategorie gibt's
  nichts zu tun). Klick fragt erst per `confirm()` nach ("Wirklich alle
  N aktivierten Sender in 'X' deaktivieren?"), erst danach der Request.
- Bewusst NUR "Alle deaktivieren" gebaut, kein symmetrisches "Alle
  aktivieren" — war nicht Teil der Anfrage, kann bei Bedarf ergänzt
  werden (der Unterbau `set_category_enabled(category, enabled)` ist
  schon generisch genug dafür, nur der Endpunkt/Button fehlt).

### Verifiziert (echter Test gegen die echten 344 "Unsortiert"-Sender)
- Config-Seite zeigt den Button korrekt (`category-header`/
  `disable-all-btn`-Klassen im ausgelieferten HTML bestätigt).
- `POST .../categories/Unsortiert/disable-all` -> `{"ok": true,
  "changed": 344}`. `stations.json` direkt geprüft: alle 344 auf
  `enabled: false`, andere Kategorien unberührt.
- Laufender Player hat den Reload korrekt übernommen: aktive Sender in
  der Rotation fielen von 356 auf 12 (nur noch die ursprünglichen,
  nicht-importierten Sender), und da der bis dahin aktuelle Sender
  ("100'5 Alemannia", einer der importierten) jetzt deaktiviert war,
  schaltete der Player automatisch auf einen verbleibenden aktiven
  Sender um (derselbe Reload-erzwungene-Switch-Mechanismus wie bei
  jeder anderen Deaktivierung des laufenden Senders).
- Danach die 344 Sender wieder aktiviert (`set_category_enabled(...,
  True)` — nur mein Test, war nicht als dauerhafte Nutzer-Entscheidung
  gedacht), Player wieder bei 356 aktiven Sendern, Prozesszahl weiterhin
  bei 7 ffmpeg-Prozessen, Stream durchgehend 44.1kHz/Stereo.

## 2026-08-03 — Review-Befunde: Watchdog gegen tote Sender + zentrales Logging

Auslöser: kompletter Review-Durchgang durch das Projekt (Stereo-Umbau,
Web-Interface, Fingerprint-Fix, Sender-Import, Prebuffering). Dabei kam
aus den Live-Daten des laufenden Containers ein Totalausfall zum
Vorschein, der vorher niemandem aufgefallen war.

### Der Befund: 8,5 Stunden Stillstand in der Nacht

`docker logs` des seit 21:23 laufenden Containers, nach Stunden gruppiert:

```
19:45–04:19 UTC   3569 × "⚠ Stream 'BBC Radio Scotland' liefert nichts mehr"
                  0 Wiedergabe-Zeilen, 0 Senderwechsel
```

8,9 h Stillstand bei 9,2 h Logspanne. Icecasts `error.log` zeigt passend
dazu um 19:45:04 `Disconnecting source due to socket timeout` — der Mount
war also die ganze Nacht komplett weg, Hörer konnten sich nicht mal neu
verbinden. Ursachenkette, drei Glieder:

1. **`radiozapper.py`, Hauptloop**: leerer Read -> Meldung ->
   `source.start()` -> `sleep(1)` -> `continue`, endlos. Kein Limit, kein
   Weiterschalten, und vor allem nie wieder ein `output.write()`. Der
   Kommentar an `STREAM_READ_TIMEOUT` behauptete, der Timeout verhindere
   ein Blockieren des Loops — er schützt aber nur den einzelnen Read, nicht
   die Endlosschleife drumherum.
2. **`do_switch()`**: `verdict = classify(tail) if tail.size else "music"`
   — ein Puffer *ohne jedes Audio* galt als Musik und wurde übernommen.
   `pb.dead` wurde gar nicht abgefragt. Genau so wurde der tote Sender um
   04:55 "aus Puffer, nahtlos" zum aktuellen Sender.
3. **Import**: `bulk_add()` legt importierte Sender mit `enabled: True`
   an, die 344 Sender aus der Kodinerds-Liste standen also ungeprüft
   sofort in der Rotation. BBC Radio Scotland ist eine DASH-`.mpd`-URL:
   ffprobe akzeptiert sie (Audio-Stream vorhanden), ffmpeg kann sie nicht
   dauerhaft streamen. Der Erreichbarkeits-Check hat hier eine echte
   Lücke — unverändert offen, siehe unten.

Der Nutzer hatte BBC Radio Scotland morgens von Hand in `stations.json`
deaktiviert; das war der Workaround, nicht der Fix.

### Umgesetzt: Watchdog (`radiozapper.py`)

- Neue Konstanten `STREAM_FAILURE_LIMIT = 3` und
  `STATION_DEAD_COOLDOWN = 300.0`, neues dict `dead_until` (id ->
  Ablaufzeitpunkt der Sperre) im Hauptloop.
- Leerer Read zählt `stream_failures` hoch. Unter dem Limit wie bisher
  reconnecten (ein kurzer Hänger soll keinen Senderwechsel auslösen), am
  Limit: Sender auf die Sperrliste, `do_switch("Sender liefert kein
  Audio")`. Jeder erfolgreiche Read setzt den Zähler zurück, ebenso jeder
  Wechsel (`switch_to_station()`/`do_switch()` per `nonlocal`).
- Neuer Helper `alive_stations(active, dead_until, keep_id=None)` filtert
  gesperrte Sender raus. `keep_id` (der laufende Sender) bleibt immer
  drin, sonst würden bei jedem Wechsel sämtliche Puffer-Positionen
  verrutschen und alle Puffer unnötig neu aufgebaut.
- `do_switch()` überspringt gesperrte Kandidaten, ohne sie anzufassen.
- Ein manueller Switch löscht die Sperre für diesen Sender (ausdrücklicher
  Nutzerwunsch schlägt Automatik — der Sender kann längst wieder da sein).
- Sind *alle* aktiven Sender gesperrt (z.B. Netz komplett weg), werden die
  Sperren aufgehoben und alle bekommen nochmal eine Chance, statt in einer
  Runde aus lauter übersprungenen Kandidaten hängenzubleiben.

### Umgesetzt: tote Puffer nicht mehr übernehmen

- `do_switch()`: bei `pb.dead` wird der Kandidat verworfen und gesperrt,
  statt ihn als "music" durchzuwinken. Ein noch *leerer* (gerade erst
  gestarteter) Puffer wird nicht als Verdachtsfall behandelt, sondern
  ganz normal frisch verbunden.
- `switch_to_station()` (manueller/Reload-Pfad): tote Puffer-Quelle wird
  nicht adoptiert, stattdessen frischer Connect.
- `PrebufferedSource._join()`: läuft der Reader-Thread nach dem
  `join(timeout=...)` noch, wird die Quelle jetzt als tot markiert statt
  stillschweigend übernommen — vorher hätten Hauptloop und Reader-Thread
  gleichzeitig aus derselben Pipe gelesen und sich die Bytes geteilt.
- `sync_prebuffer()` gibt jetzt die gestorbenen IDs zurück, statt sie
  direkt neu zu starten. **Im Live-Test aufgefallen**: bei einer dauerhaft
  toten URL bedeutete "einfach neu versuchen" einen frischen
  ffmpeg-Prozess pro Schleifendurchlauf, also im Sekundentakt. Der
  Hauptloop sperrt sie stattdessen.

### Umgesetzt: Logging (`logging_setup.py`, alle Module)

Bisher: `print()` nach stdout, Warnungen nach stderr, Detailausgaben nur
bei `--verbose`, HTTP-Requests komplett verworfen (`log_message` = `pass`)
— und nichts davon persistent. Die Nacht-Diagnose oben ging nur, weil der
Container zufällig nicht neugestartet worden war.

- Neues Modul `logging_setup.py`: `setup(log_file, verbose)` konfiguriert
  den Root-Logger mit zwei Handlern — Konsole (INFO, mit `--verbose`
  DEBUG) und `RotatingFileHandler` auf `logs/radiozapper.log`, **immer**
  DEBUG, 5 × 10 MB. Nicht beschreibbares Logverzeichnis degradiert zu
  "nur Konsole" statt den Start zu verhindern.
- `threading.excepthook` wird umgebogen: Exceptions in Hintergrund-Threads
  (Puffer-Reader, Import-Worker, Webserver) landen im Log statt still auf
  stderr. Der Hauptloop hat zusätzlich ein `except Exception:` mit
  `log.exception()`.
- Alle Module haben jetzt einen eigenen Logger (`radiozapper`, `webui`,
  `fingerprint`, `speech`, `import`, `stations`, `settings`); sämtliche
  `print()`-Aufrufe sind ersetzt. Die `verbose`-Parameter von
  `SpeechDetector.classify()` und `FingerprintDB.match_or_learn()` sind
  weggefallen — das entscheidet jetzt der Log-Level.
- Threads haben sprechende Namen (`pb-<sender-id>`, `import`, `webui`),
  das Dateiformat zeigt sie an.
- Neu im Log, weil beim Debuggen genau das gefehlt hat: der **beste
  Nicht-Treffer** jedes Fingerprint-Vergleichs mit Abstand zur Schwelle,
  die HTTP-Requests des Web-Interfaces (DEBUG), jede Config-Änderung mit
  Sender-Namen, jeder gestartete/gestorbene Puffer.
- `--log-file` (Default `logs/radiozapper.log`, leer = aus) als neues
  CLI-Argument; `--verbose` heißt jetzt "DEBUG auch auf der Konsole".
- Dockerfile: `--verbose` aus dem ENTRYPOINT entfernt (die Datei hat die
  Details ohnehin), `logging_setup.py` wird mitkopiert.
  docker-compose.yml: `./logs:/app/logs` gemountet, `logs/` in
  `.gitignore`.

### Verifiziert

- **Isoliert** (Kopie des Projekts in einem Temp-Verzeichnis, eigene
  stations.json mit einer toten URL `http://127.0.0.1:1/dead.mp3` plus
  zwei echten Sendern, Restream auf einen separaten Icecast-Mount):
  3 Fehlversuche in ~2 s, dann `⛔ ... nehme ihn für 5 Min. aus der
  Rotation`, Wechsel auf Radio Bob aus dem Puffer, danach 58 s stabil
  weitergespielt. Der tote Sender wurde danach kein einziges Mal mehr
  gepuffert.
- **Unit**: `alive_stations()` gegen alle Fälle (keine Sperre, aktive
  Sperre, abgelaufene Sperre, `keep_id`, alles gesperrt),
  `prebuffer_target_ids()` auf der gefilterten Liste,
  `PrebufferedSource` gegen tote URL (`dead=True`, Puffer leer) und
  gegen SWR3 live (`dead=False`, 5,0 s Mono + 5,0 s Stereo, Verhältnis
  exakt 1:2).
- **Live am echten Deployment**: Test-Sender mit toter URL über die
  Config-API angelegt. Beim Start sofort
  `⚠ Hintergrund-Puffer von 'AAA Watchdog-Test' liefert nichts — für
  5 Min. aus der Rotation` (kein Sekundentakt-Respawn mehr). Danach
  manuell draufgeschaltet: 06:39:36 Wechsel hin, 06:39:39 Sperre,
  06:39:40 `▶ Spiele: ANTENNE BAYERN (aus Puffer, nahtlos)` — **~4
  Sekunden Stille statt 8,5 Stunden**. Test-Sender danach wieder
  gelöscht, `stations.json` unverändert bei 356 Sendern / 14 aktiven.
- Logdatei liegt auf dem Host unter `logs/radiozapper.log`, überlebt
  Container-Neustarts, Konsole zeigt nur noch die 8 Startzeilen +
  Ereignisse.

### Bewusst NICHT in diesem Durchgang (aus dem Review, weiterhin offen)

- **Import aktiviert alles sofort** (`bulk_add(enabled=True)`) und der
  ffprobe-Check erkennt DASH/HLS-Manifeste fälschlich als dauerhaft
  abspielbar. Der Watchdog fängt die Folgen jetzt ab, die Ursache bleibt.
  Vorschlag: `enabled=False` beim Import + `ffmpeg -t 3 -f null -` statt
  eines reinen Manifest-Parse.
- **Prebuffer-Burst**: gemessen 87,6 s Audio in 75 s Wall-Clock — jeder
  Wechsel schiebt bis zu `prebuffer_seconds` auf einen Schlag in den
  Encoder. Hörer rutschen pro Zap ~10 s hinter Live; Icecasts
  `queue-size` (512 KB ≈ 21 s bei 192 kbit/s) kann bei mehreren schnellen
  Wechseln überlaufen.
- **`sync_prebuffer()`/`pb.stop()` blockieren den Hauptloop** bis zu 9 s
  pro Quelle (bei 5 Puffern also bis 45 s) — passiert bei jeder
  Config-Änderung.
- **Kein Health-Status im Web-Interface**: während der 8,5 h zeigte die UI
  unverändert "Läuft gerade: BBC Radio Scotland".
- **Fingerprint**: Algorithmus ist verifiziert gut (Selbst-Match 727/729,
  stärkster Fremd-Treffer 7 von 20 Clips bei Schwelle 25), hat aber real
  noch nie eine Wiederholung erkannt — 83 Clips, alle `times_seen = 1`,
  einziger Treffer seit dem Fix war ein Fehlalarm. Verdacht: Prüfung nur
  einmal pro Sprach-Run bei exakt 3 s, Wiederholungen liegen anders
  ausgerichtet. Außerdem: DB wächst unbegrenzt, kein Pruning/`VACUUM`.
- **`SpeechDetector.leftover`** wird beim Senderwechsel nicht
  zurückgesetzt (bis zu 511 Samples des alten Senders im ersten Fenster
  des neuen).
- Config-Seite skaliert nicht auf 356 Sender (keine Suche, kein
  Bulk-Delete, kein "Alle aktivieren"), CSRF auf `/api/skip` und
  `/api/filter/toggle`, keine automatisierten Tests im Repo.

## 2026-08-03 (Fortsetzung) — Import-Fix: deaktiviert importieren + echter Audiofluss-Check

Die beiden Punkte, die der Watchdog-Durchgang oben als Ursache benannt,
aber bewusst offen gelassen hatte.

### Teil 1: importierte Sender kommen deaktiviert an

`stations_store.bulk_add()` hat jetzt einen `enabled`-Parameter (Default
weiter True, damit die Funktion generisch bleibt); `station_import.py`
übergibt explizit `enabled=False`. Hunderte fremde Sender ungefragt in
die laufende Rotation zu kippen ist keine Entscheidung, die ein
Import-Knopf treffen sollte — und genau so kam BBC Radio Scotland dorthin.

Web-Interface/README entsprechend umformuliert; die Ergebnismeldung sagt
jetzt "X neu (deaktiviert) in 'Unsortiert' — zum Aktivieren Haken setzen".

### Teil 2: der Check — erste Idee war falsch, Messung hat sie widerlegt

Geplant war "statt ffprobe einfach 3 Sekunden mit ffmpeg dekodieren".
Vor dem Einbau gegen die echte BBC-URL gemessen — und die besteht diesen
Check mühelos:

```
ffmpeg -v error -t 3  -i <bbc.mpd> ... -> 3,00s Audio in 0,2s Wall-Clock, rc=0
ffmpeg -v error -t 12 -i <bbc.mpd> ... -> 12,00s Audio in 0,4s Wall-Clock, rc=0
ffmpeg -v error -t 12 -i <swr3.mp3> .. -> 12,00s Audio in 7,4s Wall-Clock, rc=0
```

Der Dauerlauf zeigt, warum: die DASH-Quelle schüttet den vorhandenen
Fragment-Pool auf einen Schlag aus (insgesamt ~38s Audio in Sekunden-
bruchteilen) und bekommt danach auf jedes weitere Fragment ein HTTP 404
(`Failed to open fragment of playlist`, 143 KB stderr in 35s). Der
Prozess lebt weiter, liefert aber nie wieder ein Sample. "Kommt Audio?"
ist also die falsche Frage — jede Menge Audio kommt.

Die richtige Frage ist "kommt am ENDE eines Zeitfensters noch Audio?".
Genau daran hängt auch der Hauptloop (`StreamSource.read_window`).

`check_reachable()` läuft deshalb nicht mehr über `subprocess.run()`,
sondern über einen eigenen select-Lese-Loop: ffmpeg dekodiert nach
`pipe:1`, CHECK_WINDOW=8s lang wird mitgelesen und der Zeitpunkt des
letzten Bytes festgehalten. Bestanden nur, wenn insgesamt ≥3s Audio kamen
UND das letzte Byte höchstens CHECK_TAIL=3s vor Fensterende ankam.
Gebraucht wird nicht "wie viel kam", sondern "wann kam das letzte".

### Verifiziert

- **Einzelfälle**: BBC-DASH False (19,2s Audio, aber die letzten 7,5s
  nichts), SWR3/1LIVE/Radio Bob True (zuletzt vor 0,0–0,2s), tote URL
  und nicht existente Domain False in 0,1s. 6/6 wie erwartet.
- **Falsch-Negativ-Rate an echten Daten**: 40 zufällige der 341
  importierten "Unsortiert"-Sender mit altem ffprobe-Check UND neuem
  Check geprüft. 37× beide OK, 0× nur neu OK, **3× nur alt OK** — und
  diese drei sind BBC Radio 4, BBC Radio 3 und BBC Radio nan Gàidheal,
  also exakt dieselbe DASH-Klasse wie der Übeltäter. Kein einziger
  gesunder Sender fällt durch das strengere Raster.
- **Ende zu Ende isoliert**: eigene Test-Playlist (gesunder Sender,
  BBC-DASH, tote URL, Duplikat) über einen lokalen HTTP-Server gegen eine
  eigene stations.json: `{'checked': 4, 'working': 2, 'added': 1}`,
  Duplikat übersprungen, hinzugefügter Sender `enabled=False`. 8 Sekunden
  für alles (parallel).
- **Live am echten Deployment** über die echten API-Endpunkte
  (`/api/config/settings` -> Test-Playlist, `/api/config/import/start`,
  Status gepollt): identisches Ergebnis, neuer Sender in der echten
  stations.json mit `enabled: false` in "Unsortiert", BBC im Log als
  "nur ein Vorrat, kein laufender Stream" abgelehnt. Test-Sender danach
  gelöscht, `import_url` auf die Kodinerds-URL zurückgesetzt, Liste
  wieder bei 356 Sendern / 14 aktiven.

### Kostenpunkt

Der Check dauert jetzt fixe 8s pro Sender statt bis zu 6s — bei 362
Einträgen und 10 parallelen Checks also grob 5 Minuten statt vorher ~2.
Das steht so auch in der Startmeldung im Log ("grob X Min."). Für einen
manuell ausgelösten Import, der einmal im Monat läuft, ist das der
richtige Tausch: die Alternative war ein Sender, der den Player nachts
8,5 Stunden lahmlegt.
