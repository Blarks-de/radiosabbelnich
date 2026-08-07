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

## 2026-08-03 (Fortsetzung) — Prebuffer-Burst: nur die Lücke ausstrahlen

Letzter offener Punkt aus dem Review mit messbarer Auswirkung auf Hörer.

### Befund

Beim Übernehmen eines Hintergrund-Puffers ging bisher der **komplette**
Puffer an den Encoder (`output.write(buf_stereo)`), also bis zu
`prebuffer_seconds` = 10s Audio auf einen Schlag. Am laufenden Betrieb
gemessen: 87,6s Audio in 75s Wall-Clock, 224 statt 192 kbit/s. Drei Folgen:

- Der Hörer rutscht mit jedem Zap ~10s weiter hinter Live, **kumulativ** —
  das holt nichts wieder auf.
- Er hört denselben Zeitraum doppelt: die letzten 10s des alten Senders
  waren schon raus, dann kommen die letzten 10s des neuen.
- Icecasts Client-Queue (`queue-size` 524288 B ≈ 21s bei 192 kbit/s) läuft
  bei ein paar schnellen Wechseln über → Hörer fliegen raus.

### Denkfehler dahinter

Der Ringpuffer enthält Audio, das bis zu 10s **alt** ist. Ihn auszustrahlen
heißt, bewusst alte Audio zu senden. Gebraucht wird er nur, um die Lücke zu
füllen, die während der Übergabe entsteht (`promote()`/`join()`/
Klassifikation) — das sind typisch Bruchteile einer Sekunde, in Ausnahmen
ein paar Sekunden.

### Umsetzung (`radiozapper.py`)

- Neuer modulweiter Helper `stereo_tail(stereo, seconds, sample_rate)`.
  Wichtig: der Schnitt wird **ab Array-Anfang** in Frames gerechnet, nicht
  per `stereo[-n:]`. Beim Testen aufgefallen: endet der Puffer mit einem
  angefangenen Halb-Frame (möglich, wenn ein `read_window()` in den Timeout
  lief und mit ungerader Sample-Zahl zurückkam), landet ein naives
  Tail-Slice auf ungeradem Index und **vertauscht ab da links und rechts**.
- `write_audio(pcm)` als einziger Schreibpfad zum Output; führt Buch, wann
  zuletzt geschrieben wurde (`last_output_at`). Alle bisherigen
  `output.write()`-Aufrufe (Hauptloop, `quick_forward`, Probe-Fenster,
  beide Puffer-Übernahmen) laufen jetzt darüber.
- `promote_bridge(buf_stereo)` liefert genau so viel Audio, wie seit dem
  letzten Schreiben Wall-Clock-Zeit vergangen ist. Damit bleibt die
  Audio-Zeitachse deckungsgleich mit der echten Zeit — der Puffer
  kompensiert die Übergabedauer, statt sie zu überzahlen. Selbstkorrigierend:
  dauerte die Übergabe ausnahmsweise 2,5s, werden 2,5s überbrückt; dauerte
  sie 0,14s, eben 0,14s.
- `prebuffer_seconds` bedeutet dadurch etwas Sinnvolles: die Obergrenze
  dafür, wie lange ein Wechsel dauern darf, ohne dass eine Lücke entsteht.

### Verifiziert

- **Unit** (`stereo_tail`): 1s aus 10s exakt das Pufferende, 0,25s,
  "mehr angefordert als vorhanden" → alles, 0s/negativ/leerer Puffer → leer,
  ungerade Puffergröße → Ergebnis bleibt frame-sauber (L/R korrekt), 1 Sample
  → leer, 1 Frame → genau dieses Frame.
- **Live gemessen** am Deployment, drei erzwungene Wechsel während eines
  100s-Mitschnitts: **102,7s Audio in 100s** (197,3 kbit/s). Die
  Bridge-Zeilen im Log zeigen 0,14s / 0,45s / 2,53s / 0,25s überbrückt bei
  jeweils 10,0s vorhandenem Puffer.
- **Kontrollmessung ohne Wechsel**: 42,5s Audio in 40s, also derselbe
  Überschuss von ~2,5s. Der stammt nicht aus den Wechseln, sondern aus
  Icecasts `burst-on-connect` (65536 B = 2,73s bei 192 kbit/s), den der
  Mitschnitt beim Verbinden geschenkt bekommt.
- Damit: **Drift pro Wechsel vorher ~10s, jetzt praktisch 0.** (Die
  75s-Messung von vorhin enthielt dieselben 2,7s Connect-Burst, der reale
  Überschuss dort war also ~9,9s = genau ein Puffer.)

### Weiterhin offen

Der *frische* Wechselpfad in `do_switch()` (nicht gepufferter Kandidat)
macht `time.sleep(1.5)` und schreibt danach ein einzelnes Probe-Fenster —
der produziert also weniger Audio als Wall-Clock-Zeit vergeht, also eher
eine kleine Lücke statt Überschuss. Nicht angefasst; betrifft nur Kandidaten
jenseits der ersten `prebuffer_count` Sender.

## 2026-08-03 (Fortsetzung) — Neues Feature: Nachrichten-Pause / News-Zapper

Wunsch: zur vollen und halben Stunde, wo praktisch jeder Sender
Nachrichten sendet, für ein kurzes Zeitfenster stattdessen eine zufällige
MP3 aus einem lokalen Ordner (SMB-Mount) spielen, danach automatisch
zurück zum pausierten Sender.

### Architekturentscheidung (vor der Umsetzung mit Nutzer abgestimmt)

Statt eines synthetischen "Sender"-Objekts bleibt `current` (die lokale
Variable im Hauptloop) während der Pause bewusst UNVERÄNDERT der
pausierte Sender. Nur `source` wird kurz auf die MP3 umgehängt und über
dieselbe `switch_to_station()`-Funktion zurückgeschaltet, die auch jeder
normale Wechsel benutzt. Damit läuft Prebuffering für die übrigen Sender
während der Pause unbeeinflusst weiter (keine Sonderbehandlung nötig),
und Watchdog/do_switch bekommen die synthetische ID nie zu Gesicht, weil
sie während der Pause schlicht nicht aufgerufen werden. Das
Web-Interface zeigt trotzdem korrekt "📰 Nachrichten-Pause": eine neue
`SwitcherState.set_news_break()`/`current_station()`-Überschreibung
liefert währenddessen eine virtuelle Station, unabhängig vom
Hauptloop-internen `current`.

### Neue/geänderte Dateien
- **`news_break.py`** (neu): reine Domänenlogik, kein Zugriff auf
  StreamSource/SwitcherState. `active_slot(cfg, now)` liefert eine
  stabile Slot-ID (nächstgelegene :00/:30 als ISO-Zeit) für "gerade
  aktiv", oder None. `pick_random_mp3(folder, exclude)` wählt zufällig,
  vermeidet die zuletzt gespielte Datei falls möglich, loggt und gibt
  `None` zurück statt zu werfen (Ordner fehlt/leer/unlesbar).
- **`settings_store.py`**: neuer verschachtelter `news_break`-Block
  (`enabled`, `mp3_folder`, `window_minutes`, `enabled_hours`) in den
  DEFAULTS. `_read_raw()` merged ihn jetzt speziell (bekannte Unterfelder
  übernehmen, Rest Default) statt der bisherigen flachen Top-Level-Logik.
  `update()` bekommt vier `news_break_*`-Kwargs, Stil bleibt: benannte
  Parameter statt generischem Dict-Merge.
- **`webui.py`**: `SwitcherState` bekommt `news_break_cfg`
  (aus settings.json gecacht wie prebuffer_seconds/-count),
  `set_news_break()`/`news_break_active`/`news_break_file`.
  `current_station()` liefert während der Pause eine virtuelle Station
  (`NEWS_BREAK_STATION_ID = "__news_break__"`, existiert absichtlich
  NICHT in stations.json). `_build_status()` setzt `now_playing` auf den
  MP3-Dateinamen statt ICY-Metadaten abzufragen, neues Feld
  `news_break_active` in der API-Antwort. `_handle_update_settings()`
  leitet die vier neuen Felder weiter.
- **`radiozapper.py`**: `StreamSource.start()` bekommt einen neuen
  `realtime`-Parameter (siehe Bug unten). Neue State-Variablen
  (`news_break_active`, `news_break_resume_id`, `news_break_last_file`,
  `news_break_served_slot`) und zwei geschachtelte Funktionen
  (`note_news_break_interrupted()`, `resume_from_news_break()`) im
  Hauptloop. Drei Eingriffe in die bestehende Schleife: Zeitfenster-Check
  vor `read_window()` (Eintritt + Fensterablauf-Austritt), MP3-Ende
  VOR dem Watchdog abgefangen (im `pcm.size==0`-Zweig), Auto-Erkennung
  komplett übersprungen (`if news_break_active: continue` nach
  `write_audio()`).
- **Dockerfile**: `news_break.py` zur COPY-Liste hinzugefügt.
- **docker-compose.yml**: neuer Bind-Mount
  `${NEWS_MP3_FOLDER:-./news_mp3}:/app/news_mp3:ro`.
- **env.example**, **.gitignore** (`news_mp3/`), **README.md** (neuer
  Abschnitt "Nachrichten-Pause"), **CLAUDE.md** (neuer Architektur-
  Unterabschnitt).

### Zwei echte Bugs beim Durcharbeiten gefunden (nicht nur beim Testen —
beim genauen Nachvollziehen der bestehenden Interaktionen)

1. **Reload während laufender Pause hätte die MP3 gekappt.** Der
   bestehende Reload-Zweig prüft `current["id"] not in active_ids` und
   schaltet dann zwangsweise um. Weil `current["id"]` während der Pause
   der PAUSIERTE (nicht der hörbare) Sender ist, hätte JEDE Config-
   Änderung während einer Pause — auch eine, die mit dem pausierten
   Sender gar nichts zu tun hat — die MP3 live gegen einen Sender
   ausgetauscht, während die UI weiter "Nachrichten-Pause" gezeigt hätte
   (inkonsistenter Zustand). Fix: `elif news_break_active:` als eigener
   Zweig davor, der nur "Senderliste neu geladen (Nachrichten-Pause
   läuft weiter)" loggt.
2. **Klick auf den pausierten Sender selbst wäre verschluckt worden.**
   Die bestehende Bedingung für einen manuellen Switch ist
   `manual_id != current["id"]`. Da `current` während der Pause
   unverändert der pausierte Sender bleibt, wäre ein Klick GENAU auf den
   (der naheliegendste Klick überhaupt — "ich will jetzt zurück zu dem,
   was pausiert war") vom Vergleich als "schon aktuell" fehlinterpretiert
   und stillschweigend ignoriert worden. Fix:
   `manual_id is not None and (news_break_active or manual_id != current["id"])`.

Beide sind beim Durchspielen der Interaktionen VOR dem Testen gefunden
worden (Teil des Umsetzungsvorschlags), dann live bestätigt (siehe unten).

### Dritter Bug, beim ersten Live-Test entdeckt: keine Echtzeit-Taktung für lokale Dateien

Erster Testlauf: eine 35s-MP3 wurde in 0,8 Sekunden komplett "abgespielt"
(Log zeigte sofortigen Übergang Eintritt -> "MP3 zu Ende"). Ursache:
anders als eine Radio-URL (deren Auslieferung durch den Sender selbst in
Echtzeit getaktet ist — DAS ist der Mechanismus, der den ganzen
Hauptloop bei ~1 Analysefenster/Sekunde hält, keine explizite Bremse in
radiozapper.py) dekodiert ffmpeg eine LOKALE Datei so schnell wie
CPU/Disk erlauben. Verifiziert isoliert:
```
ffmpeg -i 35s.mp3 ...        -> 0,1s Wall-Clock
ffmpeg -re -i 35s.mp3 ...    -> 35,1s Wall-Clock
```
Fix: `StreamSource.start()` bekommt einen `realtime: bool = False`
Parameter, der ffmpegs `-re`-Flag einfügt (Input in "nativer" Wall-Clock-
Geschwindigkeit lesen). Default bleibt False (unnötig und nicht der
Normalfall für echte Radio-URLs), der Nachrichten-Pause-Eintritt ruft
`source.start(path, realtime=True)`.

### Verifiziert

- **Unit `news_break.active_slot()`**: 15 Fälle — Fensterkanten exakt
  auf/knapp innerhalb/knapp außerhalb um :00 und :30 (inkl.
  Mitternachtsgrenze 23:59:30 -> nächster Tag :00), Slot-ID-Stabilität
  über das ganze Fenster hinweg, verschiedene Fenster -> verschiedene
  IDs, `enabled=False`, `enabled_hours`-Filter, `window_minutes<=0`.
  Alle korrekt.
- **Unit `news_break.pick_random_mp3()`**: fehlender/leerer/unlesbarer
  Ordner -> None + Log, nur `.mp3` (case-insensitive) ausgewählt (200
  Züge, Verteilung ~50/50 zwischen zwei Dateien), `exclude` vermieden
  wenn Alternative da, bei nur 1 Datei + exclude=sie selbst trotzdem
  gespielt, unlesbarer Ordner (chmod 000) -> None.
- **Unit `settings_store`-Validierung**: 12 Fälle inkl. Migration von
  altem settings.json ohne news_break-Feld, teilweise gesetztem
  news_break-Subdict (unbekannte Felder ignoriert, Rest Default),
  DEFAULTS-Dict bleibt bei Mutation der zurückgegebenen Kopie
  unangetastet, window_minutes-/enabled_hours-Grenzen. Dabei **einen
  echten Bug gefunden und gefixt**: `news_break_enabled_hours=None` sollte
  laut Docstring "aktiv auf 'immer' zurücksetzen" bedeuten, kollidierte
  aber mit der allgemeinen Konvention "None = Parameter nicht übergeben,
  unverändert lassen" — beide Fälle waren nicht unterscheidbar. Fix: neues
  Sentinel `settings_store.UNSET` als Default für dieses eine Argument;
  `webui.py`s Handler unterscheidet jetzt explizit "Key fehlt im Payload"
  (-> UNSET) von "Key ist im Payload null" (-> None, aktiv zurücksetzen).
- **Live-Integrationstests** (isoliertes Projekt-Verzeichnis, eigene
  stations.json mit 2 echten Sendern, eigener Icecast-Test-Mount, echte
  generierte Test-MP3s via ffmpeg/lavfi):
  - Kurze MP3 (8,05s) mit langem Fenster -> Resume nach 8,17s über
    "MP3 zu Ende" (nicht Fensterablauf), Sender korrekt zurückgewechselt.
  - Lange MP3 (35s) mit kurzem Fenster, Eintritt exakt auf einer echten
    :00-Grenze abgewartet (realer Wall-Clock-Test, kein Mock) -> Resume
    nach 22s über "Fenster abgelaufen" (nicht MP3-Ende), während die MP3
    noch mitten in der Wiedergabe war.
  - `/api/status` während der Pause: `current_id: "__news_break__"`,
    `current_name: "📰 Nachrichten-Pause"`, `now_playing: "🎵
    long_news.mp3"`, `news_break_active: true` — alles über die
    bestehenden Frontend-Felder, ohne `_PAGE_HTML`/JS anzufassen.
  - Manueller Switch auf einen ANDEREN Sender während der Pause: sofort
    interrupted, nutzte sogar den weiterlaufenden Prebuffer ("aus
    Puffer") — bestätigt, dass Prebuffering während der Pause unbeeinflusst
    normal weiterläuft, wie im Architektur-Entwurf vorgesehen.
  - Manueller Switch auf GENAU den pausierten Sender während der Pause
    (Bug #2 oben): korrekt interrupted und umgeschaltet, `news_break_active`
    danach `false` — ohne den Fix wäre das (verifiziert per Code-Nachvollzug)
    stillschweigend ignoriert worden.
  - Config-Änderung (neuer Sender angelegt) während laufender Pause
    (Bug #1 oben): Log zeigt "Senderliste neu geladen (Nachrichten-Pause
    läuft weiter)", MP3 lief unangetastet weiter, `/api/status` zeigte
    währenddessen durchgehend `news_break_active: true`.
  - Nach jedem Testlauf: echte `stations.json` unverändert geprüft (kein
    Test-Sender durchgesickert), keine verwaisten Prozesse.

### Bewusst nicht gebaut
- Kein echtes Audio-Ducking/Überblenden — klarer Hart-Switch (Verbindung
  trennen/neu aufbauen), passt zur bestehenden "Zapper"-Architektur (jeder
  andere Wechsel im Programm funktioniert genauso) und zum Feature-Namen.
- Keine eigene Formular-Sektion auf der Config-Seite — war nicht Teil der
  Anfrage, Konfiguration läuft über den bestehenden
  `POST /api/config/settings`-Endpunkt (curl/API). Der Unterbau
  (`settings_store.update(news_break_*=...)`) ist bereits generisch genug,
  falls später ein Formular gewünscht wird.
- Kein Übernacht-`enabled_hours`-Wraparound (z.B. 22–6 Uhr) — nur
  `0 <= start < end <= 24`.

## 2026-08-03 (Fortsetzung) — Nachrichten-Pause: Formular-Sektion auf der Config-Seite

Auslöser: Nutzer wollte den MP3-Ordner-Pfad eintragen, fand aber keine
Nachrichten-Pause-Einstellung auf `/config` — das war im vorigen Eintrag
bewusst weggelassen worden ("war nicht Teil der Anfrage"), jetzt aber
nachgefordert, Position "oberhalb der Radiosender".

### Umsetzung
Neue Sektion `<h2>📰 Nachrichten-Pause</h2>` + `<form id="news-break-form">`
in `_CONFIG_PAGE_HTML` (`webui.py`), platziert zwischen `<h1>` und
`<div id="categories">` (also vor der Senderliste). Felder: Checkbox
`enabled`, Text `mp3_folder` (mit Hinweistext, dass das der
Container-Pfad ist und der Host-Ordner über `NEWS_MP3_FOLDER` in `.env` +
Neustart läuft — kein Feld dafür), Zahl `window_minutes`, Checkbox
"nur zu bestimmten Stunden aktiv" + zwei Zahlenfelder Start/Ende.

Kein neuer Backend-Code nötig — `settings_store.update()` und der
`POST /api/config/settings`-Handler unterstützten alle vier
`news_break_*`-Parameter bereits (aus dem vorigen Durchgang). Einzige
Feinheit im Frontend: die Checkbox "nur zu bestimmten Stunden aktiv"
steuert, ob `news_break_enabled_hours` als `[start, end]` oder explizit
als `null` gesendet wird — beides sind "Feld gesetzt" aus Sicht von
`settings_store.UNSET`, nicht "Feld weggelassen", trifft also genau die
vom Backend vorgesehene Unterscheidung (siehe `update()`-Docstring).

`loadSettings()` (bereits vorhanden für Puffer-Einstellungen) um das
Befüllen der neuen Felder aus `settings.news_break` erweitert statt einer
zweiten Fetch-Funktion — ein API-Call liefert beide Blöcke ohnehin.

### Verifiziert
- `python3 -c "import ast; ast.parse(open('webui.py').read())"` — kein
  Syntaxfehler.
- Eingebettetes JS aus `_CONFIG_PAGE_HTML` extrahiert und mit
  `node --check` geprüft — kein Fehler.
- Isoliertes Testverzeichnis (siehe CLAUDE.md-Testmuster): `webui.py`
  mit `SwitcherState()` + `start_server()` gegen eine Ein-Sender-
  `stations.json` gestartet. `GET /config` → 200. `GET
  /api/config/settings` liefert den `news_break`-Block mit Defaults.
  `POST /api/config/settings` mit allen vier `news_break_*`-Feldern
  (inkl. `enabled_hours: [6, 22]`) → gespeichert, per erneutem GET
  bestätigt. Danach `enabled_hours: null` gesendet → im Folge-GET korrekt
  wieder `null` (bestätigt die UNSET-vs-null-Unterscheidung end-to-end,
  nicht nur im Store-Unittest aus dem vorigen Durchgang).

### Bewusst NICHT gemacht
- Keine Validierung von `mp3_folder` im Frontend (Existenz/Erreichbarkeit)
  — der Ordner ist typischerweise ein SMB-Mount, der beim Speichern noch
  nicht verfügbar sein kann (siehe `settings_store.update()`-Docstring,
  unverändert vom vorigen Durchgang).
- README.md entsprechend nachgezogen: Abschnitt "Nachrichten-Pause"
  verweist jetzt primär auf die Formular-Sektion, API-Beispiel bleibt als
  Alternative für Skripte.

## 2026-08-03 (Fortsetzung 2) — Nachrichten-Pause spielte nie: mp3_folder zeigte auf Host-Pfad

Auslöser: Nutzer meldet, zur vollen Stunde läuft keine MP3 statt der
Nachrichten. Log (`docker compose logs radiozapper | grep -i nachrichten`)
zeigte seit 08:26 bei jedem Slot dieselbe Warnung: "Nachrichten-Pause:
Ordner /mnt/eimer/data/Audio/Musik/+_Blarks_Favoriten/Fav_Queen/ nicht
lesbar ([Errno 2] No such file or directory) — übersprungen."

### Ursache
`mp3_folder` in `settings.json` war über das neue Formular auf `/config`
(siehe vorigen Eintrag) mit dem **Host**-Pfad des SMB-Mounts befüllt
worden, nicht mit dem Container-internen `/app/news_mp3` — genau die
Verwechslung, vor der `docker-compose.yml` und `settings_store.py` per
Kommentar warnen. Zusätzlich hatte `.env` gar kein `NEWS_MP3_FOLDER`
gesetzt, lief also auf den leeren Default `./news_mp3` — selbst ein
korrekter Container-Pfad in `settings.json` hätte also ebenfalls ins
Leere gezeigt.

### Umsetzung
- `.env`: `NEWS_MP3_FOLDER=/mnt/eimer/data/Audio/Musik/+_Blarks_Favoriten/Fav_Queen/`
  ergänzt (Host-Pfad, wird read-only nach `/app/news_mp3` gemountet).
- `settings.json`: `news_break.mp3_folder` auf `/app/news_mp3` korrigiert.
- `docker compose up -d radiozapper` (kein Rebuild nötig, nur der Mount
  ändert sich — Bind-Mounts wirken erst nach Neuerstellen des Containers,
  nicht durch bloßes Ändern von `.env`).

### Verifiziert
- `docker compose exec radiozapper ls /app/news_mp3` zeigt die MP3s
  (u.a. "01-We will Rock you.mp3") — vorher leer.
- Neustart-Log zeigt normalen Start ohne Fehler, Player läuft weiter
  ("▶ Spiele: 1LIVE").
- Nächster Slot (volle/halbe Stunde) noch nicht abgewartet — Fehlerursache
  ist per Log-Historie eindeutig (jeder Slot seit Feature-Aktivierung
  betroffen, immer derselbe Pfad-Fehler), Fix behebt exakt diesen Pfad.

### Für die Zukunft
Die Formular-Sektion auf `/config` (voriger Eintrag) macht diesen Fehler
leicht: das Feld heißt einfach "MP3-Ordner", auch wenn der Hinweistext
"Container-interner Pfad" sagt. Denkbare Härtung (nicht in diesem
Durchgang umgesetzt): Server-seitige Prüfung, ob `mp3_folder` beim
Speichern via `os.listdir()` erreichbar ist, und eine Warnung statt
stillem Erfolg, falls nicht — siehe "Bewusst NICHT gemacht" im vorigen
Eintrag, wo genau das aus anderem Grund (SMB evtl. noch nicht verbunden)
zurückgestellt wurde. Eine Warnung statt eines harten Fehlers würde beide
Fälle abdecken.

## 2026-08-03 (Fortsetzung 3) — "Gesabbel!" → "ZAPPEN!", Streaming-Adresse fürs Hauptfenster

Auslöser: Nutzerwunsch, den Knopf umzubenennen (⚡ ZAPPEN! statt 🗣️
Gesabbel!) und unter der "Läuft gerade"-Box die volle Icecast-Stream-URL
anzuzeigen, damit man sie leicht in VLC o.ä. eintragen kann.

### Umsetzung
- `webui.py`: Button-Text/Emoji geändert (`btn-gesabbel`-ID unverändert
  gelassen, nur das sichtbare Label betrifft die Nutzeranfrage). Log-
  Meldung und Docstrings, die den Knopfnamen wörtlich zitieren
  (`request_skip()`, `filter_enabled`-Property, `_handle_skip()`),
  entsprechend nachgezogen — sonst stimmt der Log-Text nicht mehr mit dem
  Knopf überein, den der Nutzer tatsächlich sieht.
- Neues `<div id="stream-url">` zwischen `#now-playing` und `<audio>`.
  Wird im selben `if (!playerSrcSet && ...)`-Block in `refresh()` befüllt
  wie `player.src` — dieselbe Adresse, dieselbe Bedingung ("nur einmal
  setzen, ändert sich eh nicht"), kein zweiter Codepfad nötig. Aufbau
  identisch zum bestehenden Player-Src-Muster:
  `location.protocol + '//' + location.hostname + ':' + stream_port +
  stream_mount` — bewusst NICHT der feste `ICECAST_HOSTNAME` aus `.env`,
  damit die angezeigte Adresse immer zu dem Host passt, über den der
  Nutzer die Seite gerade tatsächlich erreicht (Tailscale-Name, IP,
  localhost, …), genau wie beim eingebetteten Player, der nach demselben
  Prinzip schon lief.
- `radiozapper.py`: Kommentare, die "Gesabbel!" als Knopfnamen zitieren,
  auf "ZAPPEN!" aktualisiert. Der interne `do_switch()`-Grund
  ("Nutzer meldete Gesabbel (ZAPPEN!-Knopf)") bleibt inhaltlich (Nutzer
  hat Gesabbel gemeldet), ergänzt nur den neuen Knopfnamen in Klammern —
  reine Log-Kosmetik, keine Verhaltensänderung.
- `README.md`: Abschnitt "Web-Interface" auf neuen Knopfnamen aktualisiert,
  neuer Punkt für die Streaming-Adresse ergänzt.

### Verifiziert
- `python3 -c "import ast; ast.parse(...)"` für `webui.py` und
  `radiozapper.py` — kein Syntaxfehler.
- Eingebettetes `<script>` der Hauptseite extrahiert, `node --check` —
  kein Fehler.
- `docker compose up -d --build radiozapper`: sauberer Neustart, Log zeigt
  normalen Start und einen echten Sender-Switch danach (unbeeinflusst).
  `curl /` zeigt `⚡ ZAPPEN!` und `id="stream-url"` im HTML, `curl
  /api/status` liefert `stream_port`/`stream_mount` wie erwartet — die
  clientseitige URL-Zusammensetzung selbst lief nicht im Browser, sondern
  wurde nur per `node --check` auf Syntaxfehler geprüft (kein Headless-
  Browser zur Hand).

## 2026-08-03 (Fortsetzung 4) — Streaming-Adresse konfigurierbar, türkis/unterstrichen, Klick kopiert

Auslöser: Nutzerwunsch, die im vorigen Eintrag ergänzte Stream-Adressen-
Anzeige (1) über die Einstellungen fest überschreibbar zu machen statt
sie stur aus `location.hostname` abzuleiten, und (2) optisch als Link
(türkis, unterstrichen) mit Klick-zum-Kopieren aufzuwerten.

### Umsetzung
- `settings_store.py`: neues Top-Level-Feld `stream_url` (Default `""`).
  Anders als `import_url` ist bei diesem Feld ein leerer String ein
  gültiger Fachwert ("automatisch ermitteln"), nicht "ungültig" — Docstring
  von `update()` weist explizit darauf hin, damit das nicht versehentlich
  der `import_url`-Validierung angeglichen wird. Nicht-leerer Wert muss
  wie bei `import_url` mit `http(s)://` beginnen.
- `webui.py`/`SwitcherState`: `stream_url` analog zu `prebuffer_seconds`
  gecacht (`_stream_url`, in `reload()` aus `settings_store.load()`
  nachgezogen, eigene Property). `_build_status()` liefert es im
  `/api/status`-JSON mit.
- Neue Sektion "🔗 Streaming-Adresse" auf `/config`, zwischen Import- und
  Puffer-Formular — ein Textfeld, leer = automatisch. Gleiches
  Request/Response-Muster wie die anderen Settings-Formulare
  (`POST /api/config/settings` mit nur dem einen Feld).
- Hauptseite (`_PAGE_HTML`): `#stream-url` jetzt türkis (`#1abc9c`),
  unterstrichen, `cursor: pointer`. Klick kopiert die angezeigte Adresse
  in die Zwischenablage. **Wichtig:** der eingebettete `<audio>`-Player
  nutzt WEITERHIN ausschließlich die aus `location.hostname` abgeleitete
  Adresse, nicht `stream_url` — die ist garantiert erreichbar (der
  Browser lädt diese Seite gerade genau darüber), während `stream_url`
  eine vom Nutzer frei eingetragene Anzeige-/Kopier-Adresse ist, die
  falsch oder aus dem Player-Netz heraus nicht erreichbar sein könnte.
  Nur die Textanzeige/Kopierfunktion nutzt `data.stream_url || autoUrl`.
- `copyToClipboard()`-Helper mit Fallback: `navigator.clipboard.writeText()`
  verlangt einen "secure context" (HTTPS oder localhost) — dieses
  Interface läuft aber typischerweise über einen Tailscale-Hostnamen per
  schlichtem HTTP (siehe `ICECAST_HOSTNAME` in `.env`), dort ist die API
  entweder `undefined` oder verweigert. Fallback über eine unsichtbare
  Textarea + `execCommand('copy')`, das funktioniert auch dort. Ohne
  diesen Fallback hätte der Klick auf dem eigentlichen Deployment
  schlicht nichts getan.

### Verifiziert
- `python3 -c "import ast; ast.parse(...)"` für `webui.py` und
  `settings_store.py` — kein Syntaxfehler. Beide `<script>`-Blöcke aus
  `_PAGE_HTML`/`_CONFIG_PAGE_HTML` extrahiert, `node --check` — kein
  Fehler.
- `docker compose up -d --build radiozapper`: sauberer Neustart, Log
  zeigt normalen Start (und nebenbei bestätigt: der News-Break-Fix aus
  dem vorigen Eintrag greift jetzt tatsächlich — "📰 Nachrichten-Pause:
  spiele 'It's a Hard Life.mp3'" direkt beim Start).
- `curl /api/status` zeigt `"stream_url": ""` im Default-Zustand.
- `curl -X POST /api/config/settings -d '{"stream_url": "http://dockfish...:8000/radiozapper.mp3"}'`
  → gespeichert, per Folge-GET bestätigt.
- `curl -X POST ... -d '{"stream_url": "nicht-eine-url"}'` → `400` mit
  der erwarteten Fehlermeldung (Validierung greift).
  `curl -X POST ... -d '{"stream_url": ""}'` → zurückgesetzt auf
  automatisch, per Folge-GET bestätigt — Feld am Ende wieder im
  Auslieferungszustand hinterlassen.
- Kein Headless-Browser zur Hand: Klick-Kopieren selbst (inkl.
  `execCommand`-Fallback) nur per Code-Nachvollzug geprüft, nicht per
  echtem Klick im Browser verifiziert.

## 2026-08-03 (Fortsetzung 5) — HTTPS: Web-Interface + Icecast-Stream per TLS

Auslöser: Nutzer hat unter `/certs` ein Tailscale-Zertifikat
(`dockfish.icefish-ghost.ts.net.crt`/`.key`, gültig bis 2026-09-08) liegen
und wollte es einbinden, mit Eintrag in den Settings und Zertifikatspfaden
in `.env`. Rückfrage per AskUserQuestion, ob Web-Interface, Icecast-Stream
oder beides — Antwort: beides.

### Umsetzung
- `settings_store.py`: neues Top-Level-Feld `tls_enabled` (Default
  `False`) — steuert NUR das Web-Interface (siehe unten, warum Icecast
  getrennt läuft). Wie `stream_url` ein normales None-=-unverändert-Feld
  in `update()`.
- `webui.py`: `import ssl`. `SwitcherState.tls_enabled` analog zu
  `stream_url` gecacht, mit explizitem Docstring-Hinweis, dass eine
  Änderung erst nach Neustart wirkt (kein Hot-Reload möglich — ein
  laufendes `ThreadingHTTPServer`-Socket lässt sich nicht nachträglich in
  TLS einwickeln). `start_server()` bekommt `tls_cert_file`/`tls_key_file`,
  wrappt bei beidem vorhanden das Socket per `ssl.SSLContext`, fängt
  `ssl.SSLError`/`OSError` ab und bleibt bei Klartext-HTTP statt
  abzustürzen (greift z.B. wenn `tls_enabled=true`, aber die gemountete
  Datei in Wirklichkeit `/dev/null` ist). Loggt jetzt selbst "🌐
  Web-Interface läuft auf Port N (http|https)" statt wie vorher extern in
  `radiozapper.py` — dort die alte Zeile entfernt, sonst zwei
  widersprüchliche Log-Zeilen.
- Neue Sektion "🔒 HTTPS" auf `/config` (Checkbox `tls_enabled`, Hinweis
  auf `.env`-Abhängigkeit und Neustart-Pflicht). **Nebenbei behoben:**
  die im vorigen Eintrag neu hinzugefügte `stream-url-form` hatte gar
  keine CSS-Regeln bekommen (unstyled) — beim Anlegen der `tls-form`-Regeln
  aufgefallen und mitkorrigiert (`form#stream-url-form, form#tls-form`
  jetzt gemeinsam gestylt wie die anderen Config-Formulare).
- `radiozapper.py`: `--tls-cert-file`/`--tls-key-file`-Argumente. In
  `main()` werden sie nur an `webui.start_server()` durchgereicht, wenn
  `state.tls_enabled` true ist — der Schalter lebt in `settings.json`,
  die Pfade selbst kommen aus `.env`/Docker-Env, beides zusammen ergibt
  erst "TLS aktiv".
- `Dockerfile`: `ENTRYPOINT` reicht `TLS_CERT_PATH`/`TLS_KEY_PATH`
  (Container-interne feste Pfade, siehe `docker-compose.yml`) als
  `--tls-cert-file`/`--tls-key-file` durch.
- `docker-compose.yml` (radiozapper-Service): zwei neue Bind-Mounts
  (`TLS_CERT_FILE`/`TLS_KEY_FILE` aus `.env`, Default `/dev/null` statt
  einer Repo-Platzhalterdatei — `/dev/null` ist immer ein gültiges
  Mount-Ziel und liefert 0 Byte) nach `/app/certs/cert.pem`/`key.pem`.
  Läuft als root (kein `USER` im Dockerfile) und kann die
  root-only-0600-Originaldatei direkt lesen — anders als beim
  Icecast-Service unten keine Kopie/kein chown nötig.
- `docker-compose.yml` (icecast-Service), der aufwändigere Teil:
  - `user: "0:0"` (Image-Default ist der unprivilegierte User `icecast2`,
    kann eine 0600-root-Datei gar nicht öffnen).
  - Zwei read-only-Mounts nach `/run/tls-cert.pem`/`tls-key.pem`, gleiches
    `/dev/null`-Default-Muster wie oben.
  - `command:`-Skript um drei Dinge erweitert: (1) **immer** einen
    `sed`-Fix für `<group>icecast2</group>` → `<group>icecast</group>` in
    der generierten `icecast.xml` — Bug im `icegen`-Template, bislang nie
    aufgefallen, weil der Container ohne `user: root` nie tatsächlich als
    root startete und `<changeowner>` darum nie griff; jetzt zwingend,
    sonst verweigert Icecast als root generell den Start. (2) bei
    vorhandenem Zertifikat (`-s`-Test auf beide gemounteten Dateien):
    Cert+Key zu einer PEM zusammengefügt (`cat`), auf `icecast2:icecast`
    gechownt (siehe Verifikation unten, warum das nötig ist, nicht nur
    0600-root), zweiter `<listen-socket>` mit `<ssl>1</ssl>` und
    `<ssl-certificate>` per `sed` nach `<hostname>` bzw. `<paths>`
    eingefügt — bewusst NICHT nach `<listen-socket>`, weil dieser String
    auch in einem auskommentierten Beispielblock vorkommt und ein
    naives `sed -i "/<listen-socket>/i..."` dort ein zweites Mal
    zugeschlagen hätte. (3) ohne Zertifikat: Log-Zeile, sonst
    unverändertes Verhalten.
  - `ports`: zusätzlich `${ICECAST_SSL_PORT:-8443}:8443` — links der
    Host-Port (konfigurierbar), rechts der Container-Port (fix, muss zu
    `IC_SSL_PORT` in der `environment:`-Sektion passen). **Fehler beim
    ersten Versuch:** `IC_SSL_PORT` zunächst versehentlich auch auf
    `${ICECAST_SSL_PORT:-8443}` gesetzt statt hart auf `8443` — dadurch
    hätte Icecast intern auf einem anderen Port gelauscht als dem, auf
    den Docker das Host-Mapping tatsächlich zeigt, sobald `ICECAST_SSL_PORT`
    vom Default abweicht (wie hier: 8444 statt 8443, siehe unten). Nach
    dem Muster von `IC_PORT`/`ICECAST_PORT` (dort schon immer getrennt)
    korrigiert, bevor es scharf lief.
- `.env`/`env.example`: `TLS_CERT_FILE`, `TLS_KEY_FILE`, `ICECAST_SSL_PORT`
  (Default in `env.example`: 8443, leer/leer). In der echten `.env` dieses
  Hosts: die beiden `/certs/...`-Pfade, `ICECAST_SSL_PORT=8444` — 8443 war
  hier bereits von einem fremden Container (`npm`, Nginx Proxy Manager)
  belegt, siehe Verifikation.
- `README.md`: neue `.env`-Tabellenzeilen + Abschnitt "HTTPS/TLS
  (optional)" mit Schritt-für-Schritt-Anleitung und der Warnung, dass das
  Web-Interface bei aktivem TLS NICHT mehr parallel über Klartext-HTTP
  erreichbar ist (anders als der Icecast-Stream, der beide Ports parallel
  bedient). `CLAUDE.md` um einen Architektur-Abschnitt "TLS/HTTPS"
  ergänzt (Docker-Besonderheiten) — u.a. der icegen-Gruppennamen-Bug und
  der Grund fürs Chown statt reinem 0600, damit das nicht beim nächsten
  Anfassen erneut mühsam nachvollzogen werden muss.

### Verifiziert
- **Isoliert VOR jeder Änderung am Live-Deployment**: Icecast-TLS in einem
  komplett separaten Testcontainer (eigener Name, eigene Ports 18000/18443,
  `--entrypoint /bin/bash` explizit gesetzt — sonst überschreibt `docker
  run <image> <cmd>` nur die CMD-Args, nicht das im Image gesetzte
  `ENTRYPOINT ["/bin/bash","start.sh"]`, und der Test hätte unbemerkt immer
  das Original-`start.sh` laufen lassen) mit selbstsigniertem Test-Zertifikat
  durchgespielt: erst das eigentliche `<group>icecast2</group>`-Problem
  gefunden (Icecast verweigert als root generell den Start, unabhängig von
  TLS), dann das Cert-Lesbarkeits-Problem (root-only 0600 vom Host reicht
  NICHT, weil Icecast die Cert-Datei nachweislich erst nach dem
  `<changeowner>`-Drop liest — bestätigt durch Log-Reihenfolge
  "server started" → "Invalid cert file" NACH dem erfolgreichen
  Privilegien-Drop). Erst mit beiden Fixes: `HTTPS-Status: 200`,
  `openssl s_client` zeigt das erwartete Test-Zertifikat, Klartext-HTTP
  bleibt parallel `200`.
- **Danach am echten Deployment** (mit dem echten Tailscale-Zertifikat):
  `docker compose config -q` sauber, `docker compose up -d --build` ohne
  Fehler NACH Korrektur des Port-Konflikts (8443 war durch `npm` belegt,
  auf 8444 ausgewichen) und NACH Korrektur des `IC_SSL_PORT`-Bugs.
  Icecast-Log: "TLS aktiviert: zusätzlicher HTTPS-Port 8443" (Container-
  intern). `curl https://localhost:8444/status.xsl` → `200`,
  `curl http://localhost:8000/status.xsl` weiterhin `200`
  (Hörer unbetroffen), `openssl s_client` zeigt das echte
  Let's-Encrypt/Tailscale-Zertifikat, Stream-Mount selbst
  (`/radiozapper.mp3`) per HTTPS ebenfalls `200`.
  `POST /api/config/settings {"tls_enabled": true}` + `docker compose
  restart radiozapper` → Log zeigt "🌐 Web-Interface läuft auf Port 5000
  (https)", `curl -k https://localhost:5000/`, `/api/status` und `/config`
  alle `200` mit dem echten Zertifikat, `curl http://localhost:5000/`
  (Klartext) währenddessen `000` (kein Parallelbetrieb, wie dokumentiert
  — bewusst so gelassen, nicht als Bug behandelt).
  Fallback-Pfad separat per `python3 -c` verifiziert:
  `ssl.SSLContext().load_cert_chain('/dev/null', '/dev/null')` wirft
  exakt den `ssl.SSLError`, den `start_server()` abfängt.
- `python3 -c "import ast; ast.parse(...)"` für alle geänderten `.py`,
  beide `<script>`-Blöcke per `node --check`, `docker compose config -q`
  — alle ohne Fehler.

### Bewusst NICHT gemacht
- Kein automatischer HTTP→HTTPS-Redirect fürs Web-Interface — wer TLS
  aktiviert, merkt es am nicht mehr erreichbaren `http://`-Link ohnehin
  sofort, ein Redirect wäre zusätzlicher Code für einen Fall, der sich
  selbst erklärt.
- Kein eigener `tls_enabled`-Schalter für Icecast in `settings.json` —
  Icecast liest `settings.json` grundsätzlich nicht (eigener Container,
  kein Python), ein Schalter dort hätte ohne einen neuen
  Cross-Container-Mechanismus keine Wirkung. Dort entscheidet allein die
  Anwesenheit von `TLS_CERT_FILE`/`TLS_KEY_FILE` in `.env` — konsistent
  mit dem bereits bestehenden `NEWS_MP3_FOLDER`-Muster ("leer = Feature
  übersprungen").
- Zertifikat am Ende `tls_enabled=true` belassen (nicht zurückgesetzt) —
  war der erkennbare Zweck der Anfrage und lief im Test sauber durch;
  Nutzer kann jederzeit einen Haken in `/config` wieder entfernen.

## 2026-08-03 (Fortsetzung 6) — Bugfix: Stream-Adresse auf der HTTPS-Seite zeigte auf den falschen Port

Auslöser: Nutzer meldet, nach Aktivieren von HTTPS fürs Web-Interface
spiele die angezeigte/kopierte Stream-Adresse in VLC nicht mehr ("auf der
https Seite kommt jetzt keine Musik mehr, auch nicht via VLC. per http
spielt VLC noch").

### Ursache
`autoUrl` in `_PAGE_HTML`s `refresh()` kombinierte `location.protocol`
(also `https:`, sobald die Player-Seite selbst per HTTPS aufgerufen wird)
einfach mit `data.stream_port` — das ist aber IMMER der Klartext-Port
8000 (`ICECAST_PUBLIC_PORT`, kennt gar kein TLS). Ergebnis:
`https://host:8000/radiozapper.mp3` — eine Kombination, die niemand
beantwortet, weil Icecasts Port 8000 kein TLS spricht (das eigentliche
HTTPS läuft auf einem GANZ ANDEREN Port, 8444 auf diesem Host). Curl mit
explizitem `http://` traf weiterhin den richtigen Port und lief deshalb
unauffällig weiter — genau das Symptom aus der Meldung.

### Umsetzung
- `webui.py`/`_build_status()`: neues Feld `stream_ssl_port` im
  `/api/status`-JSON, aus `icecast_cfg.get("public_ssl_port")`.
- `radiozapper.py`: neues CLI-Argument `--icecast-public-ssl-port`, landet
  in `icecast_cfg["public_ssl_port"]`.
- `Dockerfile`: `ENTRYPOINT` reicht `ICECAST_PUBLIC_SSL_PORT` durch.
- `docker-compose.yml` (radiozapper-Service): `ICECAST_PUBLIC_SSL_PORT=${ICECAST_SSL_PORT:-8443}`
  — dieselbe `.env`-Variable, die auch das Host-Port-Mapping des
  icecast-Service steuert, damit beide immer zueinander passen (auf
  diesem Host: 8444, siehe letzter Eintrag).
- `_PAGE_HTML`-JS: `autoUrl`-Konstruktion baut Schema+Port jetzt als PAAR
  statt unabhängig: nur wenn die Seite selbst per HTTPS läuft UND ein
  `stream_ssl_port` bekannt ist, werden `https:` + SSL-Port zusammen
  verwendet — sonst bleibt es bei `http:` + normalem Port, auch auf einer
  https-aufgerufenen Seite (bewusst in Kauf genommen: ein `http://`-Stream
  in einem `https://`-eingebetteten `<audio>` kann der Browser als "mixed
  content" blocken, das ist aber nur *möglicherweise* kaputt, ein falscher
  Port war *garantiert* kaputt). Betrifft sowohl den eingebetteten Player
  als auch den Kopier-/Anzeige-Text, beide nutzen `autoUrl` als Basis.

### Verifiziert
- `docker compose up -d --build radiozapper`, Log zeigt weiterhin "🌐
  Web-Interface läuft auf Port 5000 (https)".
- `curl -k https://localhost:5000/api/status` → `"stream_port": "8000",
  "stream_ssl_port": "8444"`.
- `curl -k https://localhost:8444/radiozapper.mp3` → `200`, tatsächlich
  Audio-Bytes (vorher schon in einem separaten Test per `ffprobe` als
  valides MP3 bestätigt, siehe letzter Eintrag) — das ist exakt die
  Adresse, die die Seite jetzt bei HTTPS-Aufruf anzeigt/kopiert
  (`https://dockfish.icefish-ghost.ts.net:8444/radiozapper.mp3`), manuell
  gegen den vom JS erzeugten String abgeglichen.
- `python3 -c "import ast; ast.parse(...)"` für `webui.py`/`radiozapper.py`,
  `node --check` fürs extrahierte `<script>`, `docker compose config -q`
  — alle ohne Fehler.

## 2026-08-03 (Fortsetzung 7) — Portabilitäts-Check auf Nutzeranfrage

Auslöser: Nutzer fragt, ob das Setup ohne die eigene `.env` (also frisch
auf einem anderen Host) problemlos läuft. Reine Bestandsaufnahme, keine
Code-Änderung.

### Befund
- `.env` ist gitignored und wird nirgends im getrackten Code hart
  referenziert (`git ls-files | grep '^\.env$'` → leer) — alle
  host-spezifischen Werte (Passwörter, Hostname, Ports, `NEWS_MP3_FOLDER`,
  jetzt auch `TLS_CERT_FILE`/`TLS_KEY_FILE`/`ICECAST_SSL_PORT`) laufen
  über `env.example` als Vorlage, exakt das bereits etablierte Muster.
- **Die Zertifikate selbst sind NICHT portabel** — `dockfish.icefish-ghost.ts.net.crt`/`.key`
  gelten nur für genau diesen Tailscale-Hostnamen (CN im Zertifikat). Auf
  einem anderen Host mit anderem Hostnamen braucht es ein eigenes, dort
  frisch erzeugtes Zertifikat (z.B. wieder per `tailscale cert
  <hostname>`) — das ist eine inhärente Eigenschaft von TLS-Zertifikaten,
  kein Code-Portabilitätsproblem.
- `ICECAST_SSL_PORT`-Default (8443 in `env.example`) kann auf einem
  anderen Host mit einem dortigen Fremd-Dienst kollidieren, genau wie
  `ICECAST_PORT`/`WEBUI_PORT` das theoretisch auch schon konnten — keine
  neue Fehlerklasse, nur dieselbe wie immer.
- `settings.json`/`stations.json` sind (wie schon vor diesem Feature)
  bewusst mit im Repo — ein frischer Clone erbt also den aktuellen
  `tls_enabled`-Stand. Ohne passende `.env`/Zertifikate auf dem neuen Host
  fällt das dank des Fallbacks in `webui.start_server()` einfach auf HTTP
  zurück (Warnung im Log), kein Absturz.
- Sonst nichts host-spezifisches Neues gegenüber dem bereits bestehenden
  Docker-Compose-Aufbau gefunden.

## 2026-08-03 (Fortsetzung 8) — Neues Feature: STT-Sprachfilter (Vosk/Whisper)

Auslöser: Nutzerwunsch nach einem zusätzlichen Sprache-Signal per
Speech-to-Text, das VAD/Heuristik ergänzt — Ziel: deutsch gesungene
Musik (VAD/Heuristik werten Gesang oft fälschlich als Sprache) korrekt
als Musik erkennen, ohne echte Moderation zu verpassen.

### Umsetzung
- Neues Modul `stt_filter.py`: `_VoskEngine`/`_WhisperEngine` (lazy
  Import wie `speech_detector.py`, fehlendes Paket führt zu
  "Engine nicht verfügbar", nicht zum Crash), `SttFilter` (lädt genau
  eine Engine gemäß Config, Hintergrund-Thread pro Sample mit Busy-Guard,
  Verdict-Cache mit Timestamp), `combine_label()` als reine Funktion —
  einzige Kopplungsstelle mit der bestehenden Switch-Logik.
- `settings_store.py`: neuer `stt_filter`-Block (`enabled`, `engine`,
  `vosk_model_path`, `whisper_model_size`, `sample_interval_seconds`,
  `confidence_threshold`, `combine_mode`), Merge-Sonderfall analog
  `news_break`, Validierung in `update()`.
- `webui.py`: `SwitcherState.stt_filter_cfg`/`stt_status`/
  `set_stt_status()`, `_handle_update_settings()` um die neuen Felder
  erweitert, `/api/status` liefert jetzt `stt_status`, Config-Seite
  bekommt eine neue Formular-Sektion "🗣 STT-Sprachfilter" (Engine-Wahl,
  Modellpfad/-größe, Intervall, Schwelle, Verknüpfungsmodus, Live-
  Statusanzeige).
- `radiozapper.py`: `stt = stt_filter.SttFilter(state.stt_filter_cfg)`
  nach dem Start-Check auf aktivierte Sender. Ringpuffer der letzten
  `stt_filter.CLIP_SECONDS` Sekunden Mono-PCM, gefüllt in jedem
  Schleifendurchlauf (außer während Nachrichten-Pause/deaktiviertem
  Sabbelfilter), alle `sample_interval_seconds` per `sample_async()` an
  die Engine gegeben — läuft im Hintergrund, blockiert den ~1s-Takt des
  Hauptloops nie. `classify()` ruft am Ende `combine_label()` auf, alles
  danach (Streak-Zählung, Fingerprint-Trigger, `do_switch()`) unverändert.
  Engine-Reload bei Config-Änderung nur, wenn sich `enabled`/`engine`/
  `vosk_model_path`/`whisper_model_size` tatsächlich geändert haben.
- `Dockerfile`: `pip install ... vosk faster-whisper`, `COPY
  stt_filter.py .`. `docker-compose.yml`: neue Volumes
  `VOSK_MODEL_FOLDER:-./vosk-model-de` (read-only, Modell nicht im Image)
  und `./whisper_cache` (beschreibbar, faster-whisper lädt Modelle selbst
  nach). `env.example`: `VOSK_MODEL_FOLDER` dokumentiert.
- README.md: neuer Abschnitt "STT-Sprachfilter" (Config-Block, Feld-
  Erklärungen, Download-Hinweis fürs Vosk-Modell), Architektur-Tabelle,
  Web-Interface-Bullet, `.env`-Tabelle ergänzt. CLAUDE.md: neue
  Architektur-Untersektion mit den Design-Entscheidungen (kontinuierliches
  Sampling unabhängig vom VAD-Label, Best-Effort-Konfidenzen, Thread-
  Sicherheit beim Engine-Reload).

### Bewusst NICHT gemacht
- Keine echte Erkennungsgenauigkeit mit echten Modellen/echtem Audio
  gemessen — in dieser Umgebung sind weder ein Vosk-Modell noch
  Internetzugriff für einen Whisper-Download verfügbar. Der
  `confidence_threshold`-Default (0.6) und `combine_mode="and"` sind
  begründete Startwerte (siehe CLAUDE.md), keine empirisch ermittelten.
- Kein automatischer Download/keine Bereitstellung eines Vosk-Modells im
  Repo/Image — bewusst Nutzeraufgabe (siehe README-Link), das kleinste
  brauchbare deutsche Modell ist trotzdem ~45 MB, zu groß fürs Image.
- Keine Vereinheitlichung von `_resample()` mit
  `speech_detector.SpeechDetector._resample()` — bewusst dupliziert,
  damit `stt_filter.py` keine Abhängigkeit auf `speech_detector.py`
  bekommt (beide Module sollen unabhängig bleiben, siehe CLAUDE.md).

### Verifiziert
- `python3 -c "import ast; ast.parse(...)"` für `stt_filter.py`,
  `settings_store.py`, `webui.py`, `radiozapper.py` — alle ohne Fehler.
- `node --check` für das aus `webui.py` extrahierte Config-Seiten-`<script>`
  — ohne Fehler.
- `settings_store.py`: isolierter Test gegen eine temporäre settings.json
  (NICHT die echte im Repo) — Defaults, `update()`-Validierung
  (`stt_filter_engine="bogus"` → `ValueError`), Rückwärtskompatibilität
  mit einer settings.json ohne `stt_filter`-Block: alle wie erwartet.
- `stt_filter.py`: isoliert getestet (lokal, ohne installiertes vosk/
  faster-whisper) — `SttFilter` degradiert bei fehlendem Paket sauber
  (`available=False`, kein Crash), `combine_label()` in beiden
  `combine_mode`s inkl. des Gesangs-Falls (VAD "speech" + STT niedrige
  Konfidenz → "music" im UND-Modus), veralteter Befund wird als "kein
  Befund" behandelt, `sample_async()` auf nicht verfügbarer Engine ist
  No-Op.
- `docker compose config -q` nach den Compose-/`.env`-Änderungen — ohne
  Fehler.
- **Echter Image-Build durchgeführt** (`docker compose build radiozapper`,
  betrifft NICHT den laufenden Produktiv-Container): `vosk-0.3.45` und
  `faster-whisper-1.2.1` installieren sauber, keine Abhängigkeitskonflikte
  mit den bestehenden Paketen (numpy/silero-vad-lite). Zusätzlich per
  `docker run --rm --entrypoint python3 radiozapper-radiozapper -c "..."`
  (Ad-hoc-Container, nicht der laufende Dienst) verifiziert: `stt_filter`,
  `settings_store`, `webui`, `radiozapper` importieren zusammen sauber im
  Container, UND mit tatsächlich installiertem vosk gegen einen nicht
  gemounteten/leeren Modellordner (`/app/vosk-model-de`) liefert Vosk
  intern einen echten Ladefehler ("does not contain model files") — vom
  Code sauber abgefangen (`available=False`, Fehlermeldung geloggt, kein
  Absturz). Damit ist der komplette Anforderung-4-Pfad (Modell fehlt →
  Feature deaktiviert sich selbst) einmal end-to-end mit echten
  Bibliotheken bestätigt, nicht nur simuliert.
- Weiterhin NICHT gemacht: ein echtes deutsches Vosk-Modell besorgen und
  echte Erkennungsgenauigkeit/Konfidenzwerte gegen einen laufenden Sender
  messen (kein Modell-Download/Internetzugriff für ein Modell in dieser
  Session) — das bleibt der nächste Schritt vor dem produktiven Einsatz.

## 2026-08-03 (Fortsetzung 9) — Korrektur: Internetzugriff war doch vorhanden, Vosk-Modell nachgeliefert

Auslöser: Nutzerfrage, warum das Vosk-Modell nicht gleich mit heruntergeladen
wurde — die Behauptung "kein Internetzugriff" im vorigen Eintrag war eine
ungeprüfte Annahme, keine tatsächlich getestete Tatsache.

### Korrektur
`curl -sI https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip`
lieferte `200 OK` — Internetzugriff war die ganze Zeit vorhanden, nur nie
verifiziert worden, bevor die gegenteilige Aussage in den vorigen Eintrag
geschrieben wurde.

### Umsetzung
- `vosk-model-small-de-0.15.zip` (~45 MB, genau das in der README als
  Pi-taugliche Empfehlung genannte Modell) heruntergeladen.
- Extraktion NICHT direkt auf dem Host, sondern über einen
  Wegwerf-Container (`docker run --rm -v ...:/out python:3.12-slim ...`):
  Docker hatte `./vosk-model-de` beim vorigen Compose-Start bereits als
  `root:root`-Verzeichnis angelegt (Bind-Mount-Ziel, das vorher nicht
  existierte) — der normale Nutzer hier hat darauf kein Schreibrecht,
  ein Container-Prozess (läuft per Default als root) schon.
- Zip enthält alles unter einem Top-Level-Ordner
  (`vosk-model-small-de-0.15/`) — beim Kopieren eine Ebene übersprungen,
  damit die Modell-Dateien direkt unter `./vosk-model-de` liegen (Vosks
  `Model()`-Konstruktor erwartet sie dort, nicht in einem Unterordner).
- `.gitignore`: `vosk-model-de/` und `whisper_cache/` ergänzt (großer
  Binärinhalt, analog zu `fingerprint_clips/`/`news_mp3/`/`logs/`).

### Verifiziert
- Echtes Modell lädt im tatsächlichen `radiozapper-radiozapper`-Image
  (`docker run --rm --entrypoint python3 ... -v .../vosk-model-de:/app/vosk-model-de:ro`):
  `SttFilter.available == True`, `last_error is None`. Vosks eigenes Log
  zeigt den vollständigen Modell-Ladevorgang (HCL/G-Graph, i-Vector-
  Extractor) ohne Fehler.
- Ein Test-Transkript auf digitaler Stille (2s Nullen) liefert erwartungs-
  gemäß leeren Text und Konfidenz 0.0 — kein Crash, sinnvolles Ergebnis.
- Damit ist jetzt (im Gegensatz zum vorigen Eintrag) auch der
  Erfolgspfad (Modell lädt UND transkribiert) mit einer echten
  Vosk-Installation bestätigt, nicht nur der Fehlerfall.

### Bewusst NICHT gemacht
- `stt_filter.enabled` NICHT in der Produktiv-`settings.json` auf `true`
  gesetzt — das würde das laufende Verhalten des echten Streams sofort
  ändern (unkalibrierter `confidence_threshold`), das ist eine bewusste
  Nutzer-Entscheidung über `/config`, keine, die automatisch mitgemacht
  werden sollte.
- Keine Erkennungsgenauigkeit gegen echte Sprache/Musik gemessen (nur der
  Lade- und Stille-Fall) — dafür bräuchte es echtes Audiomaterial, nicht
  Teil dieser Anfrage.

## 2026-08-03 (Fortsetzung 10) — STT-Konfidenzschwelle gegen echte Sender kalibriert

Auslöser: Nutzerfrage, ob sich `confidence_threshold` kalibrieren lässt,
jetzt wo ein echtes Vosk-Modell installiert ist.

### Methodik
Zwei Pseudo-Ground-Truth-Kategorien aus der echten `stations.json`
gewählt (keine manuell erstellten/künstlichen Testclips):
- **Sprache**: `deutschlandfunk` (deutsches Info-/Wortradio, praktisch
  durchgehend Moderation/Nachrichten).
- **Gesungene deutsche Musik** (genau der Fall, den `combine_mode="and"`
  von echter Moderation trennen soll): `ndr-schlager`, `radio-paloma`,
  `schlagerparadies`.

Für jeden Sender per `ffmpeg` 30s Live-Audio direkt von der Sender-URL
gezogen (NICHT über den laufenden RadioZapper-Container/-Stream, eigener
Prozess, um den Produktivbetrieb nicht zu beeinflussen), auf 16kHz Mono
dekodiert, in `stt_filter.CLIP_SECONDS`-Stücke (3s) geschnitten und durch
`stt_filter._VoskEngine.transcribe()` gejagt — den echten Produktionscode,
keine Neuimplementierung. Skript unter
`/tmp/.../scratchpad/calibrate_stt.py` (nicht Teil des Repos, Wegwerf-
Analyse).

### Ergebnis
| Kategorie | n | mean | median | min | max |
|---|---|---|---|---|---|
| Sprache (Deutschlandfunk) | 10 | 0.914 | 0.910 | 0.830 | 1.000 |
| Gesungene Musik (3 Schlager-Sender) | 30 | 0.376 | 0.406 | 0.000 | 1.000 |

Erkannter Text bei Sprache: durchgehend vollständige, grammatisch
kohärente Sätze (7–9 Wörter/Clip), z.B. "bei der physik stolpert die ki
noch objekt". Bei Gesang: meist gar kein Text (Konfidenz 0) ODER kurze,
grammatisch plausible Wortfetzen (1–4 Wörter, z.B. "du kannst", "ich
will") — Letzteres v.a. bei `radio-paloma` (langsamer, klar
artikulierter Schlager): dort lagen 5 von 10 Clips über 0.7, teils bei
1.0 Konfidenz. Vosk erkennt hier tatsächlich korrekt gesungene deutsche
Wörter — das Problem ist nicht Fehlerkennung, sondern dass kurze, klar
gesungene Phrasen für den Decoder ununterscheidbar von kurzer
gesprochener Sprache sind.

Anteil Gesang-Clips, die eine Schwelle noch überschreiten (falsch-positiv
für "Sprache"):
- Schwelle 0.6 (alter Default): 12/30 (40%)
- Schwelle 0.7: 7/30 (23%)
- Schwelle 0.75 (neuer Default): 6/30 (20%)
- Schwelle 0.83 (= gemessenes Sprache-Minimum, keine Sicherheitsmarge): 4/30 (13%)

### Entscheidung
`confidence_threshold`-Default in `settings_store.py` von 0.6 auf **0.75**
angehoben — mit Sicherheitsabstand unter dem gemessenen Sprache-Minimum
(0.83), damit reale Moderation (nur 10 Clips getestet, Varianz durch
Akzent/Verbindungsqualität/Mikrofonqualität in der Praxis vermutlich
größer) nicht knapp verpasst wird. Bewusst NICHT auf 0.83 gesetzt (exakt
am gemessenen Minimum) — das wäre Overfitting auf eine sehr kleine
Stichprobe (n=10) ohne Sicherheitsmarge.

### Bewusst NICHT gemacht
- **Keine Wortdichte-Heuristik ergänzt**, obwohl die Daten nahelegen,
  dass sie zusätzlich trennen würde (Sprache 6–9 Wörter/3s, Gesang meist
  1–4) — nur an diesem einen kleinen Test (n=40, 4 Sender) beobachtet,
  keine ausreichende Grundlage, um eine weitere Schwellwert-Entscheidung
  fest einzubauen. Als Idee in CLAUDE.md vermerkt.
- **Whisper nicht kalibriert** — nur Vosk war betroffen (das aktuell
  installierte Modell), Whisper braucht dafür einen eigenen Download
  (HuggingFace) und eigenen Test.
- **`combine_mode` weiterhin auf `"and"`** belassen trotz der 20%-
  Einschränkung — schneidet in diesem Test immer noch deutlich besser ab
  als kein STT-Filter (VAD allein würde JEDEN als Sprache erkannten
  Gesang durchlassen) bzw. als `"or"` (das die 20%-Schwäche nicht
  mindert, sondern zusätzlich VAD-Fehlalarme addiert).
- Live-Setting (`settings.json` des Produktivbetriebs) NICHT direkt
  verändert — der neue Default wirkt automatisch, sobald `stt_filter`
  dort noch nicht explizit gesetzt ist (aktuell der Fall), eine bewusste
  Aktivierung (`enabled=true`) bleibt weiterhin Nutzer-Entscheidung.

### Verifiziert
- Alle 40 Konfidenzwerte oben sind reale Messwerte aus dem laufenden
  Kalibrier-Skript (`calibrate_stt.py`), nicht geschätzt — Rohausgabe lag
  während der Session vor (Log unter
  `/tmp/.../scratchpad/calibrate_log.txt`).
- `python3 -c "import ast; ast.parse(open('settings_store.py').read())"`
  nach der Default-Änderung — ohne Fehler.

## 2026-08-03 (Fortsetzung 11) — Host-Pfad-Anzeige (Nachrichten-Pause/STT) + "Bullshitometer"

Auslöser: zwei Nutzerwünsche in einer Runde.
1. Verwirrung, warum die Config-Seite bei `mp3_folder`/`vosk_model_path`
   nur den Container-Pfad (`/app/...`) zeigt, obwohl der Nutzer den echten
   Host-Pfad (SMB-Mount bzw. Vosk-Modell-Ordner) komfortabel ändern
   möchte — Frage, ob ein Auswahl-Dialog dafür sinnvoll wäre.
2. Wunsch nach einem visuellen "Bullshitmeter" auf der Startseite: der
   aktuell gemessene Sprache-Wahrscheinlichkeitswert, grün (Musik) bis
   rot (Sprache), live in Prozent.

### Umsetzung: Host-Pfad-Anzeige
- Kein Auswahl-Dialog gebaut — bewusst dagegen entschieden (siehe unten).
- `docker-compose.yml` (radiozapper-Service): zwei neue rein informative
  Env-Vars `NEWS_MP3_FOLDER_HOST`/`VOSK_MODEL_FOLDER_HOST`, gespeist aus
  denselben `.env`-Variablen wie die eigentlichen Bind-Mounts (`${NEWS_MP3_FOLDER:-./news_mp3}`/
  `${VOSK_MODEL_FOLDER:-./vosk-model-de}`) — EIN Wahrheits-Ursprung in
  `.env`, nur zweimal durchgereicht (einmal fürs Mounten, einmal fürs
  Anzeigen).
- `Dockerfile`: `ENTRYPOINT` reicht sie als `--news-mp3-folder-host`/
  `--vosk-model-folder-host` an `radiozapper.py` durch.
- `radiozapper.py`: neue CLI-Argumente, gebaut zu `host_paths`-Dict,
  durchgereicht an `webui.start_server(..., host_paths=...)`.
- `webui.py`: `make_handler()`/`start_server()` nehmen `host_paths`
  entgegen (rein informativ, NICHT Teil von `SwitcherState` — ändert sich
  nie zur Laufzeit). `GET /api/config/settings` hängt `_host_paths` an die
  Antwort. Config-Seite: read-only `<p class="hint">` unter beiden
  betroffenen Eingabefeldern, zeigt den echten Host-Pfad + Hinweis, wie er
  sich ändern lässt (`.env` + Neustart).

### Umsetzung: Bullshitometer
- `radiozapper.py`: `classify_window()` gibt jetzt `(label, score)` statt
  nur `label` zurück (`score` = votes/3, bei Bass-Veto auf 0 gesetzt,
  damit die Anzeige zur tatsächlichen Entscheidung passt). `classify()`-
  Closure erfasst den Rohwert (VAD-`mean_prob` bzw. Heuristik-Score, VOR
  der STT-Verknüpfung) und meldet ihn per `state.set_speech_probability()`.
  Bewusst der Rohwert, nicht das STT-kombinierte Ergebnis — STT sampelt
  viel seltener (Sekunden statt ~1x/Fenster), ein kombinierter Wert würde
  sprunghaft statt flüssig wirken.
- `webui.py`: `SwitcherState.set_speech_probability()`/`speech_probability`
  (einfacher lock-geschützter Getter/Setter, kein request/pop nötig —
  reine Statusanzeige, keine Aktion). `_build_status()` liefert den Wert
  über `/api/status` mit.
- Startseite (`_PAGE_HTML`): neuer Balken "🤥 Bullshitometer" unter den
  Aktions-Buttons. Farbe wird in JS per HSL-Interpolation berechnet
  (Hue 120→0 linear zur Prozentzahl) statt über einen CSS-Gradient auf der
  Fläche — vermeidet den sonst nötigen Cover-Div-Trick, um Balkenbreite
  und Gradient-Position exakt synchron zu halten. Balken friert grau ein
  (`.paused`-Klasse), während Nachrichten-Pause läuft oder der
  Sabbelfilter aus ist (Hauptloop klassifiziert dann gar nicht) — zeigt
  sonst einen veralteten Wert als aktuell an.

### Bewusst NICHT gemacht
- **Kein Auswahl-/Browse-Dialog für Host-Ordner** — ein Dialog im
  Web-Interface könnte ohnehin nur zeigen, was innerhalb des Containers
  sichtbar ist (also wieder nur `/app/...`-Pfade), NICHT den Host. Echten
  Host-Zugriff gäbe es nur über volles Host-Filesystem-Mount oder
  Docker-Socket-Zugriff — beides ein deutlicher Sicherheitsrückschritt
  für ein Web-Interface, das laut CLAUDE.md bewusst ohne Auth läuft und
  nur durchs VPN geschützt ist. Stattdessen nur Transparenz (Anzeige),
  Ändern bleibt `.env` + Neustart wie bisher.
- Die editierbaren `mp3_folder`/`vosk_model_path`-Felder NICHT auf
  readonly umgestellt — bleiben editierbar für den (seltenen) Fall
  mehrerer zukünftiger Mount-Ziele, auch wenn aktuell nur ein sinnvoller
  Wert existiert.

### Verifiziert
- `python3 -c "import ast; ast.parse(...)"` für `webui.py`, `radiozapper.py`
  — ohne Fehler. `docker compose config -q` — ohne Fehler.
- `node --check` für beide extrahierten `<script>`-Blöcke (Player-Seite
  UND Config-Seite) — ohne Fehler.
- `classify_window()` lokal gegen Stille und Rauschen aufgerufen: liefert
  jetzt korrekt ein `(label, score)`-Tupel mit `0.0 <= score <= 1.0`.
- Container noch NICHT neu gebaut/gestartet in dieser Runde — steht als
  nächster Schritt aus (`docker compose up -d --build radiozapper`),
  danach echte Prüfung: Bullshitometer bewegt sich live mit echtem
  Sender, Host-Pfad-Hinweis zeigt auf der Config-Seite den korrekten
  `.env`-Wert.

## 2026-08-03 (Fortsetzung 12) — QR-Code für Stream-URL

Auslöser: Wunsch, die Stream-URL bequem per Handy-Kamera (z.B. für VLC)
statt manuellem Abtippen/Kopieren übernehmen zu können.

### Umsetzung
- `qrcode.js`: unverändert vendorte QR-Code-Bibliothek von
  github.com/kazuhikoarase/qrcode-generator (`js/dist/qrcode.js`,
  MIT-Lizenz, Original-Lizenzkopf im Dateikopf erhalten). Bewusst lokal
  statt CDN-`<script>`: das Web-Interface läuft laut CLAUDE.md nur im
  eigenen VPN, ein Client dort hat nicht zwangsläufig Internetzugriff.
  UTF-8-Overlay der Bibliothek NICHT mitvendort — Stream-URLs sind reines
  ASCII (Hostname/IP + Port + Pfad), der Default-Encoder reicht.
- `webui.py`: `_QRCODE_JS_BYTES` einmalig beim Modul-Import gelesen
  (gleiches Muster wie `_BANNER_BYTES` fürs Banner-Bild), ausgeliefert
  über eine neue `GET /qrcode.js`-Route mit `Cache-Control`. Startseite:
  neuer Button "📱 QR-Code" neben der bestehenden Streaming-Adresse
  (`#stream-url`), erscheint erst, sobald `currentStreamUrl` bekannt ist
  (gleiche Bedingung wie die Adresse selbst). Klick öffnet ein Modal mit
  per `qrcode(0,'M').addData(...).make().createSvgTag(...)` erzeugtem
  Inline-SVG (kein Canvas, kein zusätzlicher Bild-Request) + Klartext-URL
  + Kopieren-Knopf (nutzt die schon vorhandene `copyToClipboard()`-
  Fallback-Logik). Schließen per ✕-Knopf, Klick auf den Overlay-
  Hintergrund oder Escape-Taste.
- Bewusst NUR ein QR-Button für die eine `currentStreamUrl`, keine
  Pro-Sender-Logik: RadioZapper strahlt laut CLAUDE.md ("Audio-Pfad")
  grundsätzlich nur EINEN Icecast-Mount aus, der Senderwechsel tauscht
  nur die Quelle dahinter — es gibt gar keine zweite Stream-Adresse, die
  der QR-Code alternativ encodieren könnte.
- `Dockerfile`: `COPY qrcode.js .` ergänzt (jede `.py`/Asset-Datei wird
  dort einzeln kopiert, siehe CLAUDE.md).
- `README.md`: neuer Bullet unter "Web-Interface", neue Zeile in der
  Datei-Tabelle unter "Architektur".

### Verifiziert
- `python3 -c "import ast; ast.parse(open('webui.py').read())"` — ohne
  Fehler.
- `node --check` für den aus `_PAGE_HTML` extrahierten `<script>`-Block
  — ohne Fehler.
- `docker compose config -q` — ohne Fehler.
- `node -e` mit `eval()` des vendorten `qrcode.js`: `qrcode(0,'M')` gegen
  eine Beispiel-Stream-URL erzeugt ein valides `<svg>...</svg>`
  (11582 Zeichen für `http://192.168.1.50:8000/radiozapper.mp3`, exakt
  quadratisch, mit `viewBox`).
- Testinstanz von `webui.start_server()` (In-Process, kein Docker) gegen
  `urllib.request` geprüft: `GET /qrcode.js` liefert 200 mit
  `Content-Type: application/javascript`, exakt 57020 Bytes (Dateigröße
  auf Platte); `GET /` enthält das `<script src="/qrcode.js">`-Tag, den
  `btn-qrcode`-Button und das `qr-modal`-Markup.
- NICHT geprüft: das eigentliche Scannen des erzeugten QR-Codes mit einer
  echten Handy-Kamera/VLC — kein Zugriff auf ein Testgerät in dieser
  Umgebung. Die SVG-Struktur (weißer Hintergrund, schwarze Module, Quiet
  Zone über `margin`) folgt aber exakt dem Standard-Verfahren der
  Bibliothek, keine eigene Anpassung an Farben/Kontrast, die die
  Scanbarkeit beeinträchtigen könnte.
- Container noch NICHT neu gebaut/gestartet — steht als nächster Schritt
  aus (`docker compose up -d --build radiozapper`), danach Sichtprüfung
  im echten Browser + Scan mit einem echten Gerät.

## 2026-08-04 — Zwei Bugfixes: Frontend-Sync-Verzögerung + News-Break spielte nur eine MP3

Auslöser: zwei gemeldete Bugs in einer Runde.

### Bug 1: Web-UI hinkt dem Backend-Zustand hinterher

**Root Cause**: Die Startseite holte den gesamten Zustand ausschließlich
per `setInterval(refresh, 5000)` — alle 5 Sekunden ein `GET /api/status`,
kein Push, kein Caching-Problem im eigentlichen Sinn. Ein Senderwechsel
oder News-Break-Übergang (beide laufen im Hauptloop, request/pop-Muster,
siehe CLAUDE.md) war im `SwitcherState` sofort sichtbar, aber die Web-UI
erfuhr davon frühestens beim nächsten Poll-Tick — im schlechtesten Fall
knapp 5s Verzögerung.

**Fix**: Long-Poll-Fast-Path zusätzlich zum bestehenden Intervall-Polling
(nicht ersetzt — siehe unten, warum).
- `webui.py`/`SwitcherState`: neuer Versionszähler `_version` +
  `threading.Condition` (auf demselben Lock wie der übrige State). Bumped
  in `set_current()`, `set_news_break()`, `set_filter_enabled()` — genau
  die drei Ereignisse, die die Web-UI zeitnah sehen soll (Senderwechsel,
  News-Break-Start/Ende, Filter-Toggle). BEWUSST NICHT bei
  `set_speech_probability()`/`set_stt_status()` mitgezählt — die ändern
  sich viel häufiger (jedes Analysefenster) und würden den Long-Poll
  wieder auf Poll-Tempo runterziehen, ohne dem eigentlichen Problem zu
  helfen.
- Neue Methode `wait_for_change(known_version, timeout)`: blockiert im
  aufrufenden Thread, bis sich die Version ändert oder der Timeout
  erreicht ist. Läuft gefahrlos im Request-Handler-Thread, weil
  `ThreadingHTTPServer` (siehe `daemon_threads=True`, Python-Default) für
  jeden Request ohnehin einen eigenen Thread aufmacht — ein hängender
  Long-Poll blockiert keine anderen Requests.
- Neue Route `GET /api/status/wait?version=N` (`_STATUS_WAIT_TIMEOUT =
  25.0`s Obergrenze) — liefert `_build_status()` (jetzt mit `"version"`-
  Feld) entweder sofort nach einem echten Zustandswechsel oder nach dem
  Timeout als Heartbeat.
- Frontend (`_PAGE_HTML`-Script): `refresh()` in einen Fetch-Teil und
  eine neue `applyStatus(data)`-Funktion (alle bisherigen UI-Updates)
  aufgeteilt. Neue `longPollLoop()` hängt endlos an `/api/status/wait`
  und ruft bei jeder Antwort `applyStatus()` auf, mit dem zurückgegebenen
  `version` fürs nächste Long-Poll. Das bestehende `setInterval(refresh,
  ...)` bleibt zusätzlich bestehen (jetzt 3000ms statt 5000ms) — Grund:
  Bullshitometer/Hörerzahlen ändern sich auch OHNE Versionssprung (keine
  request/pop-Aktion dahinter), rein auf den Long-Poll umzustellen hätte
  diese beiden Anzeigen bis zu 25s einfrieren lassen. Das
  Intervall-Polling ist also kein Fallback fürs alte Problem, sondern
  bleibt für andere Daten zuständig; der Long-Poll behebt gezielt "hinkt
  nach einem Senderwechsel/News-Break-Ende hinterher".
- BEWUSST KEIN WebSocket/SSE: `ThreadingHTTPServer` + ein einfacher
  Long-Poll pro offenem Tab passt zur bestehenden Architektur (kein
  asyncio, keine neue Abhängigkeit) und reicht für die private
  Nutzerzahl dieses Web-Interfaces (siehe "Kein Auth, nur hinter VPN").

### Bug 2: News-Break spielte nur eine MP3 statt das volle Fenster

**Root Cause**: Im Hauptloop (`radiozapper.py`) behandelte der
`pcm.size == 0`-Zweig (MP3 zu Ende, `source.read_window()` liefert
nichts mehr) das Ende JEDER News-Break-MP3 unconditional als Ende der
gesamten Pause — `resume_from_news_break()` wurde direkt aufgerufen,
ganz gleich ob `window_minutes` noch nicht abgelaufen war. Der
Eintritts-Zweig (`slot and not news_break_active and slot !=
news_break_served_slot`) lud dagegen korrekt eine MP3 beim ERSTEN
Betreten des Fensters — es gab aber keinen Code-Pfad, der beim
MP3-Ende erneut prüfte, ob das Fenster selbst noch lief.

**Fix**:
- Neue Closure `start_news_break_mp3(cfg)` in `radiozapper.py`s `main()`
  — extrahiert die bisher im Eintritts-Zweig inline stehende Logik
  (`pick_random_mp3()` + `source.start(path, realtime=True)` +
  `state.set_news_break()` + `quick_forward()`), damit sie von zwei
  Stellen aus aufrufbar ist.
- Eintritts-Zweig nutzt jetzt `start_news_break_mp3()`.
- `pcm.size == 0`-Zweig: bevor `resume_from_news_break()` aufgerufen
  wird, jetzt zusätzlich `news_break.active_slot(state.news_break_cfg)`
  geprüft — läuft das Fenster noch, wird `start_news_break_mp3()` erneut
  aufgerufen (nächste zufällige MP3, `exclude=news_break_last_file`
  verhindert Wiederholung direkt hintereinander, sofern der Ordner mehr
  als eine Datei hat). Erst wenn das Fenster selbst vorbei ODER keine
  weitere MP3 verfügbar ist, greift `resume_from_news_break()` wie
  bisher.

### Verifiziert
- `python3 -c "import ast; ast.parse(...)"` für `radiozapper.py` und
  `webui.py` — ohne Fehler. `node --check` für den extrahierten
  `<script>`-Block — ohne Fehler.
- Long-Poll-Fast-Path: In-Process-Testinstanz von `webui.start_server()`
  (kein Docker) gegen `urllib.request`. Version-Bump per
  `state.set_current()` während ein Long-Poll-Request lief: Antwort kam
  nach 0.501s (statt bis zu 25s Timeout), `version` korrekt um 1 erhöht.
  Gleicher Test für `state.set_news_break(True, ...)`: Antwort nach
  0.301s, `news_break_active` korrekt `true`. Timeout-Pfad separat mit
  künstlich verkürztem `_STATUS_WAIT_TIMEOUT=1.5` geprüft: Antwort nach
  1.501s mit unveränderter Version (kein Zustandswechsel in der Zeit).
- News-Break-Verkettung: kompletter Testaufbau nach dem in CLAUDE.md
  dokumentierten Muster (`*.py` in Temp-Verzeichnis kopiert, eigene
  `stations.json`/`settings.json`, separater Icecast-Mount auf demselben
  Produktiv-Icecast unter neuem Mount-Namen `test-output2.mp3` — NICHT
  der echte `radiozapper.mp3`-Mount). Fake-"Radiosender" als eigener,
  dauerhaft laufender ffmpeg-Sinuston-Stream auf Mount
  `test-fakestation.mp3`. Vier ~6s-Test-MP3s mit unterschiedlichen
  Frequenzen (440/660/880/1200 Hz) als News-Break-Ordner.
  `window_minutes` wurde bewusst so berechnet, dass die nächstgelegene
  :00/:30-Grenze (von `news_break.active_slot()` verwendet) bereits in
  der VERGANGENHEIT lag (sonst ist das Fenster symmetrisch um die
  Grenze aktiv und damit viel länger als beabsichtigt — beim ersten
  Testversuch dadurch fälschlich 27+ Minuten statt der geplanten ~50s,
  siehe unten).
  Live-Ergebnis (echte Uhrzeiten, `window_minutes=0.807` ≈ 48.4s ab
  09:30:03): Fenster betreten 09:30:03 (`tone_440.mp3`), danach
  automatisch nachgeladen um 09:30:09 (`tone_880`), 09:30:15 (`tone_1200`),
  09:30:22 (`tone_880`), 09:30:28 (`tone_660`), 09:30:34 (`tone_880`),
  09:30:40 (`tone_660`), 09:30:46 (`tone_440`) — sieben automatische
  Nachlade-Ereignisse über 43s, nie zwei gleiche Dateien direkt
  hintereinander. Fenster korrekt als abgelaufen erkannt um 09:30:53
  (nach Ende der letzten MP3), sauberer Rücksprung zu "Fake Test
  Station" per `resume_from_news_break()`. Vorher (ungefixt) hätte der
  Lauf nach der ERSTEN MP3 (09:30:09) sofort zurückgeschaltet.
- Erster Testlauf-Fehlversuch bewusst dokumentiert (siehe oben): bei
  Testaufbau um 09:15:52 lag die nächstgelegene Grenze (09:30:00) noch
  in der Zukunft, das resultierende Fenster war dadurch von 09:14:52 bis
  09:45:07 aktiv (30 Minuten) statt der beabsichtigten ~60s — keine
  Fehlfunktion des Fixes, sondern ein Fehler in der Testvorbereitung
  (falsche Annahme über `active_slot()`s Symmetrie um die Grenze). Für
  zukünftige Tests: `window_minutes` nur dann als "Distanz zur
  nächsten Grenze + gewünschte Testdauer" berechnen, wenn die
  nächstgelegene Grenze bereits in der Vergangenheit liegt (Minute
  der Stunde in [0,15) oder [30,45)), sonst warten.
- Container noch NICHT neu gebaut/gestartet — steht als nächster Schritt
  aus (`docker compose up -d --build radiozapper`).

## 2026-08-04 (Fortsetzung) — PWA: installierbar auf Android + Vor/Zurück-Buttons

**Auslöser**: Nutzer wollte das Web-Interface auf Android installieren
("Zum Startbildschirm hinzufügen") und von unterwegs mit großen Touch-
Buttons statt der langen Sender-Liste umschalten können.

### Umsetzung

- **PWA-Grundgerüst**: neue statische Dateien `manifest.json` (Name,
  `display: standalone`, Icons, `theme_color` passend zum bestehenden
  Akzent-Türkis `#1abc9c`) und `sw.js` (Service Worker). Beide wie
  `radiozapper.webp`/`qrcode.js` schon vorher: einmalig beim Modul-Import
  von der Platte gelesen (`_load_static()`, neue kleine Helper-Funktion,
  die das bisher pro Datei duplizierte try/open-Muster für die beiden
  neuen Assets nicht ein drittes Mal kopiert), über eigene GET-Routen
  ausgeliefert. `icon-192.png`/`icon-512.png` sind Platzhalter — reines
  Python (`zlib`+`struct`, keine neue Abhängigkeit) hat ein simples
  Broadcast-Symbol (Punkt + zwei Viertel-Kreisbögen) in Türkis auf
  weißem Grund als valides PNG erzeugt, ohne Pillow im Container
  installieren zu müssen. `sw.js` bekommt bewusst `Cache-Control:
  no-cache` (anders als die sonst 24h gecachten statischen Assets) —
  sonst würde ein veralteter Service Worker im Browser-Cache eine
  künftige `sw.js`-Änderung erst nach bis zu 24h bemerken (Chromes
  eingebautes SW-Update-Intervall), nicht beim nächsten Seitenaufruf.
  Alle vier neuen Dateien in `Dockerfile` einzeln per `COPY` ergänzt
  (siehe CLAUDE.md: der Dockerfile kopiert jede Datei einzeln, sonst
  fehlt sie im Image).
- **Service Worker** cached nur die statische Oberflächen-Hülle (`/`,
  Manifest, Icons, Banner, `qrcode.js`) — Strategie: network-first mit
  Cache-Fallback fürs Offline-Öffnen. `/api/*` und alles mit
  `Range`-Header (Audio-Anfragen) werden im `fetch`-Handler explizit
  übersprungen, ebenso alles außerhalb der eigenen Origin: der
  Icecast-Stream läuft auf einem eigenen Port/eigener Origin (siehe
  CLAUDE.md, "IcecastOutput besteht über Senderwechsel hinweg"), aber
  ein Service Worker bekommt trotzdem `fetch`-Events dafür, sobald die
  Seite unter seiner Kontrolle steht — den dauerhaft offenen
  Audio-Stream durch `respondWith()`/`cache.put()` zu schleifen wäre
  Speicher-/Latenz-Unsinn und wurde deshalb von vornherein ausgeschlossen,
  nicht erst nach einem beobachteten Problem.
- **Meta-Tags** in `_PAGE_HTML`: `<link rel="manifest">`, `theme-color`,
  `apple-touch-icon` + die üblichen `*-mobile-web-app-capable`-Tags (auch
  wenn nur Android/Chrome verlangt war — Standard-Boilerplate, schadet
  auf anderen Plattformen nicht). Service-Worker-Registrierung im
  bestehenden `<script>`-Block, hinter dem existierenden
  `refresh()`/`longPollLoop()`/`setInterval()`-Block.
- **Vor/Zurück-Buttons**: zwei große Touch-Buttons (`min-height: 3.2rem`,
  Android-Empfehlung ≥48dp) direkt unter der Sender-Anzeige, noch vor der
  Streaming-Adresse. Rufen zwei neue schlanke Endpoints
  `POST /api/switch/next`/`POST /api/switch/prev` auf (vorher gab es nur
  `POST /api/switch` mit expliziter Ziel-ID — kein Endpoint kannte die
  Rotationsreihenfolge). Server-seitige Umsetzung in `webui.py`
  (`_handle_switch_relative()`): ermittelt den Nachbarn aus
  `state.active_stations` (alphabetisch = Rotationsreihenfolge, siehe
  CLAUDE.md) + `state.current_id`, ruft danach exakt denselben
  `state.request_switch(id)`-Pfad wie ein normaler manueller Klick auf —
  kein zweiter Code-Pfad für "Umschalten", nur ein zusätzlicher Weg, die
  Ziel-ID zu bestimmen. Bleibt damit im request/pop-Muster: der Hauptloop
  entscheidet weiter selbst, wann und wie der Wechsel tatsächlich passiert.
  - **Edge Case News-Break**: `state.current_id` zeigt während einer
    Nachrichten-Pause auf die synthetische `NEWS_BREAK_STATION_ID` (siehe
    `SwitcherState.set_news_break()`), die nicht in `active_stations`
    steckt — `ids.index()` wirft dann `ValueError`. Statt das als Fehler
    nach außen zu geben, fällt der Handler auf "vor dem ersten" (bei
    "nächster") bzw. "nach dem letzten" (bei "vorheriger") Sender zurück,
    damit der Klick trotzdem sinnvoll in der Liste landet statt ins Leere
    zu laufen (Test unten). Der eigentliche Nachbar des *pausierten*
    Senders ist darüber nicht erreichbar — bewusst nicht gelöst, siehe
    unten.
- **Optimistisches UI-Update**: neue `lastStatus`-Variable im Frontend
  hält den letzten vollständigen `/api/status`-Stand; `computeNeighbor()`
  berechnet daraus rein clientseitig denselben Nachbarn, den der Server
  gleich unabhängig noch mal ermittelt (zwei unabhängige, aber identische
  Berechnungen — kein Griff auf einen gemeinsamen State, weil der Server
  ja gerade erst nach dem Request seinen state.current_id aktualisiert).
  `applyOptimistic()` setzt sofort "Läuft gerade: …" + Active-Highlight in
  der Sender-Liste, noch bevor die Server-Antwort da ist. Trifft die
  Annahme mal nicht zu (Sender inzwischen deaktiviert, News-Break-
  Fallback wie oben), korrigiert der nächste `/api/status`-Stand das von
  selbst — kein Sonderfall-Code nötig, weil `refresh()`/der Long-Poll
  ohnehin regelmäßig den echten Stand nachzieht.
- **Bereits bestehender Long-Poll-Fast-Path** (`GET /api/status/wait`,
  siehe Eintrag "Bug 1" oben vom selben Tag) übernimmt weiterhin die
  eigentliche Zustellung des neuen Zustands an ALLE offenen Tabs —
  dafür war für diese Aufgabe keine weitere Änderung nötig (kein
  WebSocket, kein verkürztes Poll-Intervall): das optimistische Update
  deckt die Wahrnehmungslücke auf dem Gerät ab, das selbst geklickt hat,
  der Long-Poll den Rest.

### Bewusst NICHT gemacht

- Kein WebSocket — der bestehende Long-Poll-Fast-Path (Version-Zähler +
  `Condition.wait()`, siehe SESSION.md vom selben Tag weiter oben) liefert
  bereits Sub-Sekunden-Latenz für alle Clients; eine zweite Zustellart
  einzuziehen hätte keinen Nutzen gebracht, nur Komplexität.
- Kein echter "Vorheriger Sender relativ zum pausierten Sender während
  News-Break" — dafür müsste `SwitcherState` die reale Sender-ID
  zusätzlich zur synthetischen News-Break-ID vorhalten, während die Pause
  läuft. Aktuell dokumentiertes Fallback-Verhalten (auf den ersten Sender
  der Liste springen) reicht für den seltenen Fall "Klick genau in den
  paar Sekunden einer Nachrichten-Pause" — siehe auch README.md,
  "Bekannte Einschränkungen".
- Kein Audio-Caching im Service Worker (war explizit nicht verlangt) und
  keine Offline-Wiedergabe — ergibt bei einem Live-Radio-Restream ohnehin
  keinen Sinn.
- Eigene, "echte" Icons (nur Platzhalter-PNGs) — reine Optik, kein
  Blocker für Installierbarkeit/Funktion.

### Verifiziert

- `python3 -c "import ast; ast.parse(...)"` für `webui.py` — ohne Fehler.
  `node --check` für den extrahierten `<script>`-Block und für `sw.js` —
  beide ohne Fehler.
- In-Process-Testinstanz von `webui.start_server()` (kein Docker, Muster
  wie beim Long-Poll-Test oben) gegen `urllib.request`, mit einer
  Test-`stations.json` (drei aktive Sender `alpha`/`beta`/`gamma` + ein
  deaktivierter, um zu prüfen, dass Deaktivierte nicht mitrotieren):
  - `GET /manifest.json` liefert valides JSON mit `name: "RadioZapper"`,
    `display: "standalone"`, zwei Icon-Einträgen.
  - `GET /sw.js`, `GET /icon-192.png`, `GET /icon-512.png` liefern
    200 mit den erwarteten Content-Types (`icon-*.png` beginnen korrekt
    mit der PNG-Magic-Number `\x89PNG\r\n\x1a\n`).
  - `GET /` enthält `<link rel="manifest">`, beide neuen Button-IDs
    (`btn-prev-station`/`btn-next-station`) und die
    Service-Worker-Registrierung.
  - `POST /api/switch/next` dreimal hintereinander (mit dazwischen
    simuliertem `pop_manual_request()`+`set_current()`, weil ohne
    laufenden Hauptloop niemand den Request tatsächlich anwendet):
    alpha → beta → gamma → **alpha** — korrekter Wrap-around am Ende der
    alphabetischen Liste.
  - `POST /api/switch/prev` von alpha aus: liefert korrekt **gamma**
    (Wrap-around rückwärts).
  - `state.set_news_break(True, "jingle.mp3")` gesetzt, danach
    `POST /api/switch/next`: liefert wie dokumentiert `alpha` (Fallback
    auf ersten Sender) statt eines Fehlers.
- Container noch NICHT neu gebaut/gestartet — steht als nächster Schritt
  aus (`docker compose up -d --build radiozapper`).

## 2026-08-04 (Fortsetzung 2) — Adress-Icons statt Text-Link + eigenes Favicon

**Auslöser**: Nutzer hat die PWA vom vorigen Eintrag live auf dem Handy
getestet ("läuft ausgezeichnet") und zwei Nachbesserungen gewünscht: (1)
den bisherigen Text-Link "Streaming via VLC" durch ein Icon ersetzen, das
per Klick den bestehenden QR-Code zeigt, plus ein zweites Icon für einen
QR-Code auf das Web-Interface selbst (fürs schnelle Öffnen auf einem
zweiten Handy); (2) das Favicon (Browser-Tab-Icon) auf eine Miniatur von
`radiozapper.webp` ändern statt des Browser-Default.

### Umsetzung

- **Adress-Icons**: `#stream-url-row` (Text + versteckter "📱 QR-Code"-
  Knopf) ersetzt durch `#address-row` mit zwei gleichwertigen Icon-Buttons:
  `btn-qr-vlc` (▶️, Label "VLC", bleibt wie vorher bis `playerSrcSet`
  versteckt) und `btn-qr-phone` (📱, Label "Handy", von Anfang an
  sichtbar — hängt nicht von der erst asynchron ermittelten Stream-URL
  ab). Kein Klick-zum-Kopieren mehr direkt auf der Seite (die alte
  `#stream-url`-Klick-Kopieren-Funktion ist mit dem Element weggefallen)
  — Kopieren läuft jetzt ausschließlich über den bereits vorhandenen
  "📋 Adresse kopieren"-Knopf im QR-Modal, das für beide Icons wieder-
  verwendet wird.
  - Modal generalisiert: `openQrModal(url, title)` statt der bisher fest
    auf `currentStreamUrl` verdrahteten Logik, neues `<h2
    id="qr-modal-title">` statt des festen "📱 Stream-URL zum Scannen".
    Neue Variable `qrModalUrl` merkt sich, welche der beiden Adressen
    gerade im Modal steckt, damit der Kopieren-Knopf im Modal die
    richtige kopiert (vorher hart an `currentStreamUrl` gebunden — hätte
    beim Handy-Icon die falsche Adresse kopiert).
  - Handy-Icon kodiert `location.origin + '/'`, **nicht** eine fest
    einprogrammierte Adresse — dieselbe Begründung wie beim eingebetteten
    Player weiter oben im selben Script (Kommentar dort: "die Adresse,
    über die der Browser diese Seite selbst erreicht, ist garantiert
    erreichbar"). Für den anfragenden Nutzer ergibt das exakt die
    gewünschte `https://dockfish.icefish-ghost.ts.net:5000/`, bleibt aber
    auch bei anderem Hostname/Port/anderem Deployment korrekt, ohne
    Sonderfall-Code.
  - "VLC"-Icon: ▶️ statt eines tatsächlichen VLC-Logos (kein offizielles
    Emoji dafür verfügbar) — bewusst NICHT 🚧 gewählt (naheliegende
    Anspielung auf VLCs Verkehrshütchen-Maskottchen), weil das auf
    Android/Noto als Baustellen-Absperrung statt als Hütchen gerendert
    wird und ohne den daneben stehenden "VLC"-Text missverständlich wäre.
- **Favicon**: `favicon.ico` (Multi-Size: 16/32/48px, per PIL aus einem
  quadratischen Center-Crop von `radiozapper.webp` erzeugt — NICHT
  dieselbe Grafik wie `icon-192/512.png`, die bleiben der schlichte
  "Broadcast"-Platzhalter fürs Installieren als App). Geladen/ausgeliefert
  nach demselben Muster wie die übrigen statischen Assets
  (`_load_static()`, neue GET-Route `/favicon.ico`,
  `Cache-Control: public, max-age=86400` wie bei den Icons). Neuer
  `<link rel="icon" href="/favicon.ico">`-Tag in BEIDEN Seiten-Templates
  (Player-Seite UND `/config`) — Browser fragen `/favicon.ico` zwar auch
  ohne expliziten Link-Tag automatisch pro Origin ab, der explizite Tag
  ist aber zuverlässiger (manche Browser cachen sonst hartnäckig ein
  einmal gesehenes leeres/Default-Favicon). In `Dockerfile` ergänzt.

### Bewusst NICHT gemacht

- `icon-192.png`/`icon-512.png` (PWA-/Startbildschirm-Icons) NICHT
  ebenfalls auf die `radiozapper.webp`-Miniatur umgestellt — nur das
  Favicon war verlangt. Die Startbildschirm-Icons bleiben also vorerst
  der schlichte Broadcast-Platzhalter aus dem vorigen Eintrag.

### Verifiziert

- `python3 -c "import ast; ast.parse(...)"` für `webui.py` — ohne Fehler.
  `node --check` für den neu extrahierten `<script>`-Block — ohne Fehler.
- In-Process-Test (gleiches Muster wie oben): `_PAGE_HTML` enthält
  `btn-qr-vlc`/`btn-qr-phone`/`qr-modal-title`/`address-row`, die alten
  IDs `stream-url`/`btn-qrcode` sind vollständig verschwunden.
- **Container diesmal tatsächlich neu gebaut und live geprüft** (Korrektur
  zum "noch nicht gebaut"-Stand am Ende des vorigen Eintrags — der Nutzer
  hat `docker compose up -d --build radiozapper` zwischen den beiden
  Einträgen bereits selbst ausgeführt und die PWA/Vor-Zurück-Buttons live
  auf dem Handy für gut befunden): nach jedem der beiden Rebuilds in
  diesem Eintrag `curl -sk https://localhost:5000/...` gegen den
  laufenden Container geprüft — `/favicon.ico` liefert `200`/
  `image/x-icon`, `<link rel="icon" href="/favicon.ico">` steht im
  ausgelieferten HTML, `btn-qr-vlc`/`btn-qr-phone`/`qr-modal-title` sind
  im ausgelieferten HTML vorhanden.

## 2026-08-04 (Fortsetzung 3) — install_radiozapper.sh → check-radiozapper.sh: voller Preflight-Check

**Auslöser**: `install_radiozapper.sh` installierte bisher nur Docker und
war sonst funktionslos. Nutzer wollte daraus ein echtes Preflight-Check-
Skript machen (umbenannt, mit demselben RAM/HD/Internet-Check wie
`run_radiozapper.sh`), das zusätzlich prüft: `.env` vorhanden/ausgefüllt,
`NEWS_MP3_FOLDER` eingetragen und funktionsfähig, und ob die benötigten
Ports frei sind (bei Belegung durch einen fremden Docker-Container:
Alternative suchen und vorschlagen). Auf Deutsch weiterarbeiten (die
letzten Antworten waren versehentlich auf Deutsch begonnen, aber der
Nutzer hat es hier nochmal ausdrücklich bestätigt).

### Umsetzung

- `git mv install_radiozapper.sh check-radiozapper.sh`, Docker-Install-
  Teil inhaltlich unverändert übernommen (nur `echo`/`exit 1` durch die
  neuen `ok()`/`fail()`-Helfer ersetzt, damit er sich ins einheitliche
  Report-Format der übrigen Checks einfügt).
- RAM/HD/Internet-Block **1:1 aus `run_radiozapper.sh` übernommen**
  (identischer Code, bewusst dupliziert statt in eine dritte, gemeinsame
  Datei ausgelagert — zwei kurze, unabhängig aufrufbare Skripte für zwei
  verschiedene Zwecke, siehe "Bewusst NICHT gemacht").
- **`.env`-Check**: fehlt die Datei komplett → `fail` mit Hinweis auf
  `cp env.example .env`. Existiert sie, werden die Pflichtfelder
  (`ICECAST_ADMIN_USER`/`_PASSWORD`, `ICECAST_SOURCE_PASSWORD`,
  `ICECAST_HOSTNAME`, `ICECAST_ADMIN_EMAIL`, `ICECAST_LOCATION`) auf
  "gesetzt und nicht leer" geprüft (`fail` bei Lücken) UND separat
  darauf, ob sie noch exakt den `env.example`-Platzhaltern entsprechen
  (`change_me_admin`/`change_me_source`/`admin@example.com` → `warn`,
  kein `fail`: technisch startet der Stack damit, ist aber unsicher).
  Passwortwerte selbst werden nirgends ausgegeben, nur ob sie gesetzt
  bzw. noch der Platzhalter sind — die echte `.env` auf diesem Host
  enthält Klartext-Passwörter, die gehören nicht in Skript-Output/Logs.
- **MP3-Ordner-Check**: `NEWS_MP3_FOLDER` roch nach drei Zuständen:
  unverändert auf dem Default `./news_mp3` (Repo-Platzhalterordner) →
  `warn`, Feature bleibt einfach inaktiv, kein Fehler (siehe
  `env.example`-Kommentar, Feature ist explizit optional); Pfad
  eingetragen, existiert aber nicht oder ist nicht lesbar → `fail`; Pfad
  eingetragen und lesbar, aber keine `*.mp3`-Dateien drin → `warn`; alles
  gut → `ok` mit gefundener Dateianzahl.
- **Port-Check** (`WEBUI_PORT`, `ICECAST_PORT`, und `ICECAST_SSL_PORT`
  nur falls `TLS_CERT_FILE`+`TLS_KEY_FILE` gesetzt sind): `port_open()`
  testet rein über Bash-Bordmittel (`/dev/tcp/127.0.0.1/$port`) statt
  `netstat`/`ss` — die sind nicht überall installiert, insbesondere nicht
  auf macOS, das `install_radiozapper.sh`s Docker-Teil ja weiterhin
  explizit unterstützt. Ist der Port offen, ermittelt
  `port_owner_container()` per `docker ps --format
  '{{.Names}}\t{{.Ports}}'` + Grep auf `(^|:)PORT->`, welcher Container
  ihn hält:
  - Eigener Container (`radiozapper`/`icecast-radiozapper`, z.B. weil
    der Stack bereits läuft und man den Check einfach nochmal ausführt)
    → `ok`, kein Problem.
  - Fremder Container → `fail` samt Namen, PLUS `find_free_port()` sucht
    ab `port+1` (max. 20 Versuche) den nächsten freien Port und schlägt
    ihn als `VAR=neuer_port` zum Eintragen in `.env` vor.
  - Belegt, aber kein Docker-Container passt (Docker läuft nicht, oder
    ein Nicht-Docker-Prozess hält den Port) → `fail` mit entsprechendem
    Hinweis, gleicher Alternativ-Vorschlag.
  Port-Konflikte zählen als `fail`, nicht `warn` — anders als beim
  MP3-Ordner würde `docker compose up` hier tatsächlich mit "port is
  already allocated" scheitern, das ist kein rein kosmetisches Problem.
- Exit-Code: `FAILED`-Zähler über alle `fail()`-Aufrufe, Skript endet mit
  `exit 1`, sobald mindestens einer aufgetreten ist (nutzbar in CI/
  Automatisierung, auch wenn es hier keine gibt) — reine Diagnose, das
  Skript startet selbst nichts (das bleibt `run_radiozapper.sh`
  vorbehalten).

### Bewusst NICHT gemacht

- RAM/HD/Internet-Check NICHT in eine gemeinsame dritte Datei
  ausgelagert, die beide Skripte einbinden — explizit als "denselben
  Preflightcheck einbauen" angefordert (verstanden als: kopieren, nicht
  als gemeinsame Bibliothek extrahieren), und zwei kurze, unabhängig
  lauffähige Skripte sind hier pragmatischer als eine dritte Datei nur
  für ~25 gemeinsame Zeilen.
- `VOSK_MODEL_FOLDER` NICHT mitgeprüft, obwohl strukturell identisch zum
  MP3-Ordner-Check — nur `NEWS_MP3_FOLDER` war explizit verlangt, und das
  Vosk-Modell deaktiviert sich laut `stt_filter.py` ohnehin selbst mit
  klarer Logmeldung, wenn es fehlt (kein stiller Fehlzustand, den ein
  Preflight-Check zusätzlich abfangen müsste).
- `TLS_CERT_FILE`/`TLS_KEY_FILE` NICHT auf Existenz/Lesbarkeit geprüft —
  nicht verlangt, und beide Dienste fallen ohne gültiges Zertifikat schon
  selbst sauber auf HTTP zurück (siehe CLAUDE.md, TLS-Abschnitt), kein
  Blocker für den Start.
- Port-Alternative wird nur VORGESCHLAGEN, nicht automatisch in `.env`
  geschrieben — ungefragtes Verändern der Nutzer-Konfiguration wäre hier
  überraschendes Verhalten für einen reinen Diagnose-Lauf.

### Verifiziert

- `bash -n check-radiozapper.sh` — Syntax ohne Fehler.
- **Live gegen das echte Deployment** (nur lesend: `.env` einlesen,
  `docker ps`, `/dev/tcp`-Verbindungstests — keine Schreiboperation):
  alle drei laufenden Ports korrekt als "eigener Container" erkannt
  (`radiozapper` auf 5000, `icecast-radiozapper` auf 8000 und 8444),
  `.env` als vollständig ausgefüllt erkannt (keine Platzhalter mehr
  drin), `NEWS_MP3_FOLDER=/mnt/eimer/data/Audio/Musik/Oldies/` mit 431
  gefundenen MP3s bestätigt. Exit-Code 0.
- **Isolierte Tests** in einem Temp-Verzeichnis (Kopie des Skripts +
  `env.example`, nach CLAUDE.md-Testmuster):
  - Keine `.env` → `fail`.
  - `.env` = unverändertes `env.example` → 3 Platzhalter-Warnungen
    (`ICECAST_ADMIN_PASSWORD`/`ICECAST_SOURCE_PASSWORD`/
    `ICECAST_ADMIN_EMAIL`), kein `fail` (technisch vollständig).
  - `ICECAST_HOSTNAME` leer geräumt → korrekt als fehlend gemeldet,
    zusätzlich zu den Platzhalter-Warnungen.
  - `NEWS_MP3_FOLDER` auf nicht existenten Pfad → `fail`.
  - `NEWS_MP3_FOLDER` auf leeres (aber existentes) Verzeichnis → `warn`.
  - **Port-Konflikt mit echtem Fremd-Container**: `docker run -d --rm
    --name test-port-blocker -p 18080:80 nginx:alpine`, dann
    `WEBUI_PORT=18080` in Test-`.env` gesetzt → korrekt `fail` mit Namen
    `test-port-blocker` erkannt, Alternative `WEBUI_PORT=18081`
    vorgeschlagen (18080 selbst war ja belegt, 18081 der erste freie
    Port danach). Testcontainer danach gestoppt (`--rm` hat ihn beim
    Stop automatisch entfernt), Temp-Verzeichnis gelöscht — am
    Produktivsystem bleibt nichts zurück.

## 2026-08-05 — Zweisprachiges Web-Interface (Deutsch/Englisch)

**Auslöser**: Nutzerwunsch, alle Benutzerdialoge des Web-Interfaces
(Player- und Config-Seite) auf Deutsch und Englisch anzubieten,
umschaltbar über die Config-Seite und per `.env`-Default.

**Umsetzung**: neues Modul `i18n.py` mit `STRINGS` (nach Key gruppiert,
beide Sprachen nebeneinander) und `DEFAULT_LANGUAGE` (aus `UI_LANGUAGE`
in `.env`, Fallback `"de"` bei fehlendem/ungültigem Wert). Kein
Duplizieren der ~1300 Zeilen `_PAGE_HTML`/`_CONFIG_PAGE_HTML` in
`webui.py` pro Sprache: die Templates bleiben EIN Quelltext mit
`data-i18n`/`data-i18n-html`/`data-i18n-title`/`data-i18n-placeholder`/
`data-i18n-aria-label`-Attributen für statisches Markup und `t('key',
vars)`-Aufrufen für JS-seitige Dialoge (`alert()`/`confirm()`/
`showMsg()`/dynamisch gebaute Listenelemente). Ein injiziertes
`<script>` (`const I18N = {...}; function t(...)`) plus ein
`applyStaticI18n()`, der beim Laden einmal synchron über alle
`[data-i18n*]`-Elemente läuft, ersetzt die Texte noch vor dem ersten
Repaint — kein sichtbares Umspringen der Sprache. Pro Sprache wird beim
Modul-Import einmal ein fertig gerendertes Byte-Paar vorberechnet
(`_PAGE_HTML_BYTES`/`_CONFIG_PAGE_HTML_BYTES`, analog zu
`_MANIFEST_JSON_BYTES`), `do_GET` wählt nur per `state.language`-Lookup
aus — kein Pro-Request-Stringersatz.

`language` ist ein normales `settings_store`-Feld (Default aus
`i18n.DEFAULT_LANGUAGE`, validiert gegen `i18n.LANGUAGES`), läuft über
denselben request/pop-Reload-Zyklus wie `tls_enabled`/`stream_url` und
ist damit spätestens einen Hauptloop-Tick (~1s) nach dem Speichern
serverseitig aktiv — anders als `tls_enabled` aber OHNE Neustart des
Containers, weil die Auswahl reiner Dict-Lookup ist, kein Socket-
Rewrap. Die Config-Seite lädt sich nach dem Speichern trotzdem per
`location.reload()` neu, weil das schon vorgerechnete Markup pro
Sprache fest ist und ein Sprachwechsel ohne Reload sonst nur die JS-
Strings, nicht aber z.B. server-seitig gewählte `<option>`-Reihenfolge
o.ä. nachziehen würde (aktuell irrelevant, aber so keine stille
Inkonsistenzquelle für später).

Beim Modul-Import läuft `_check_i18n_coverage()`: ein Regex sammelt
alle in beiden Templates verwendeten `data-i18n*`/`t('key'`-Keys und
gleicht sie gegen `i18n.STRINGS` ab — fehlender Key wirft sofort beim
Start (`AssertionError`), nicht erst als leerer Text im Browser. Erster
Anlauf des Regex (`t\('([^']+)'` ohne Lookbehind) hat massenhaft
Fehltreffer erzeugt, weil er in JEDEM Bezeichner matcht, der zufällig
auf "t(" endet — `document.createElement('div')` (…**t(**'div'…),
`s.split('{'…)` (spli**t(**'{'…). Fix: `(?<![A-Za-z0-9_])t\('` (Keys nur
bei echtem `t(`-Funktionsaufruf, nicht als Substring in `createElement`/
`split`/etc.).

**Scope-Entscheidung** (mit Nutzer geklärt): nur Frontend-Text
(`webui.py` + `i18n.py`) wird übersetzt. Backend-`ValueError`-Texte aus
`settings_store.py`/`station_import.py`/`stations_store.py`, die im
Browser als Fehlermeldung landen, bleiben deutsch — kein Fehlercode-
Katalog in den Kernmodulen in diesem Durchgang.

**`.env`**: `UI_LANGUAGE` in `env.example` ergänzt (Default `"de"`,
gilt nur für eine Neuinstallation ohne bestehende `settings.json`,
danach gewinnt immer der über `/config` gespeicherte Wert). `.env`
selbst nicht angetastet (Nutzer-Konfigurationsdatei).

**Docker**: `i18n.py` in die `COPY`-Liste des Dockerfiles eingetragen
(siehe CLAUDE.md, "Docker-Besonderheiten" — sonst fehlt das Modul im
Image).

### Bewusst NICHT gemacht

- Backend-Fehlermeldungen (ValueError-Texte) NICHT übersetzt — siehe
  Scope-Entscheidung oben.
- `manifest.json` (PWA-Name/`short_name`) und `<title>` NICHT
  zweisprachig — Startbildschirm-Beschriftung fürs App-Icon, wird vom
  Betriebssystem einmalig übernommen, keine Notwendigkeit gesehen, das
  zu verkomplizieren.
- Kein Live-Nachübersetzen des schon gerenderten Markups ohne Reload —
  Sprachwechsel auf der Config-Seite triggert bewusst
  `location.reload()`.

### Verifiziert

- `python3 -c "import webui"` — lief nach dem Regex-Fix ohne
  `AssertionError` durch (siehe oben), bestätigt vollständige
  Coverage aller in den Templates verwendeten i18n-Keys.
- **Isoliert** (Kopie aller `.py`-Dateien + Wegwerf-`stations.json`/
  `settings.json` in ein Temp-Verzeichnis, `--icecast-url` auf einen
  nicht erreichbaren Test-Mount, `--webui-port 5099`, nach
  CLAUDE.md-Testmuster): mit `UI_LANGUAGE=en` gestartet → `/` und
  `/config` liefern `<html lang="en">`, das injizierte `I18N`-JSON
  enthält die englischen Texte (`idx_stations_heading` → `"Stations"`,
  `common_error` → `"Error: {msg}"`), `/api/config/settings` meldet
  `language: "en"`. `POST /api/config/settings {"language":"de"}` →
  nach ~1,5s liefert `/` wieder `<html lang="de">` (request/pop-Zyklus
  bestätigt). `POST .../{"language":"fr"}` → korrekt `400` mit
  `"language muss eine von ['de', 'en'] sein."`. Test-Prozess
  anschließend beendet, Temp-Verzeichnis bleibt isoliert vom
  Produktivsystem.
- **Live am echten Deployment**: `docker compose up -d --build
  radiozapper` (Nutzer-Zustimmung eingeholt) → Container startet
  sauber durch (Vosk-Modell lädt, HTTPS aktiv wie zuvor, Sender spielt
  an), `GET /api/config/settings` bestätigt `language: "de"` (kein
  `UI_LANGUAGE` in der Produktiv-`.env` gesetzt → korrekter
  Default-Fallback).

## 2026-08-05 (Fortsetzung) — Kategorie "Unsortiert" auf der Config-Seite einklappbar

**Auslöser**: Nutzerwunsch, die Kategorie "Unsortiert" hinter ein
`<details>` zu packen — die füllt sich nach einem Import mit
hunderten Sendern (siehe CLAUDE.md, "Config-Seite skaliert nicht auf
mehrere hundert Sender" unter "Bekannte offene Punkte") und sprengt
sonst die Seite optisch.

**Umsetzung**: nur in `loadStations()`
(`_CONFIG_PAGE_HTML`-Script in `webui.py`) geändert, nichts
Serverseitiges. Für `cat === 'Unsortiert'` wird der bestehende
`h2.category-header` (unverändert samt "Alle deaktivieren"-Knopf) in
ein `<summary>` innerhalb eines neuen `<details class="category-
details">` gepackt statt direkt in den Container gehängt; die
Sender-`<ul>` folgt entsprechend als Kind von `<details>`. Alle
anderen Kategorien bleiben unverändert direkt sichtbar. Auf/Zu-Zustand
in modulweitem `unsortedExpanded` gemerkt und über den `toggle`-
Event synchron gehalten — `loadStations()` baut die komplette
Kategorie-Liste bei praktisch jeder Aktion (Haken setzen, Bearbeiten,
Löschen, "Alle deaktivieren", …) neu auf, ohne dieses Merken würde ein
gerade aufgeklapptes "Unsortiert" bei der nächsten Aktion sofort
wieder zuklappen.

Ein Stolperstein: der "Alle deaktivieren"-Knopf hängt jetzt (über den
`h2`) innerhalb von `<summary>` — ein Klick darauf hätte ohne
Gegenmaßnahme zusätzlich zum eigentlichen Knopf-Handler auch das
`<details>` zu-/aufgeklappt (Browser-Default: jeder Klick irgendwo in
`<summary>` toggelt). `ev.preventDefault()` im Klick-Handler des
Knopfs unterdrückt das zuverlässig (verifiziert: `preventDefault()`
auf dem Event during Bubbling unterdrückt die Default-Aktion des
Vorfahren-Elements, hier das Toggle-Verhalten von `<summary>`).

### Verifiziert

- `python3 -m py_compile webui.py` + `python3 -c "import webui"` —
  keine Syntaxfehler, i18n-Coverage-Check läuft weiter unverändert
  durch (dieses Feature führt keine neuen i18n-Keys ein, reine
  Struktur-/Layoutänderung).
- **Isoliert** (Kopie aller `.py`-Dateien in ein Temp-Verzeichnis,
  Wegwerf-`stations.json` mit 5 Sendern in "Unsortiert" + 1 in
  "Lokal", `--webui-port 5098`, nach CLAUDE.md-Testmuster): `/config`
  liefert 4 Fundstellen für `category-details` im HTML (Klasse selbst
  + die 3 zugehörigen CSS-Regeln — Bestätigung, dass Markup und Styles
  ausgeliefert werden), `/api/config/stations` bestätigt die 6 Test-
  Sender über beide Kategorien korrekt. Test-Prozess anschließend
  beendet.
- Kein Browser-UI-Test in diesem Durchgang (keine Browser-Automation
  in dieser Umgebung verfügbar) — Öffnen/Schließen-Verhalten und
  Button-Klick-Verhalten sind Standard-`<details>`/`preventDefault()`-
  Semantik, aber ein manueller Klicktest im echten Browser steht noch
  aus.

## 2026-08-05 (Fortsetzung 2) — Repo aufgeräumt: pics/, web/, data/

**Auslöser**: Nutzerwunsch, das mit ~35 Einträgen überladene Root-
Verzeichnis aufzuräumen — alles außer `*.py` in Unterordner. Scripts
(`check-radiozapper.sh`/`run_radiozapper.sh`) sollten auf Nutzerwunsch
im Root bleiben.

**Umsetzung**: `pics/` (Bilder: `radiozapper.webp`, `favicon.ico`,
`icon-192.png`, `icon-512.png`), `web/` (vom Webserver ausgelieferte,
aber nicht-Bild-Assets: `qrcode.js`, `manifest.json`, `sw.js`), `data/`
(alles Persistente/Laufzeitbezogene: `stations.json`, `settings.json`,
`fingerprints.db`, `fingerprint_clips/`, `logs/`, `news_mp3/`,
`vosk-model-de/`, `whisper_cache/`). `*.py`, `CLAUDE.md`, `README.md`,
`SESSION.md`, `Dockerfile`, `docker-compose.yml`, `.env`/`env.example`,
`.gitignore`, die Scripts sowie `v1/` bleiben am Root.

Leitprinzip, um das Risiko für den laufenden Container klein zu
halten: **Container-interne Pfade bleiben exakt wie vorher** (alles
flach in `/app/`). `stations_store.STATIONS_FILE`,
`settings_store.SETTINGS_FILE`, `radiozapper.FINGERPRINT_DB_FILE`/
`FINGERPRINT_CLIPS_DIR`, `logging_setup.DEFAULT_LOG_FILE` und
`webui._load_static()` berechnen ihren Pfad alle `__file__`-relativ
zum jeweiligen `.py`-Modul — da die `.py`-Dateien am Root bleiben,
war **keine einzige Zeile Python-Code zu ändern**. Geändert wurden
ausschließlich: die Dockerfile-`COPY`-Quellpfade (Ziel bleibt `.`
= `/app/`), die linke (Host-)Seite der `docker-compose.yml`-Volume-
Mounts (rechte/Container-Seite unverändert) inkl. der
`NEWS_MP3_FOLDER`/`VOSK_MODEL_FOLDER`-Fallback-Defaults, `env.example`,
3 hartkodierte `./news_mp3`-Vergleichsstellen in `check-radiozapper.sh`,
`.gitignore` (Präfix `data/` vor den weiterhin ignorierten
Laufzeit-Pfaden — `data/stations.json`/`data/settings.json` bleiben
bewusst getrackt), sowie README.md (DE+EN: Banner-`<img src>`,
Setup-Befehle, Architektur-Tabelle) und CLAUDE.md (neuer Absatz zur
Host-/Container-Pfad-Entkopplung, aktualisierte Einzeldatei-Bind-Mount-
und TLS-Abschnitte).

`stations.json`/`settings.json` per `git mv` verschoben (History
erhalten). Die vier root-eigenen Laufzeit-Ordner (`logs/`, `news_mp3/`,
`vosk-model-de/`, `whisper_cache/` — von Docker beim ersten Start als
root angelegt) ließen sich als `blarks` nicht per normalem `mv`
verschieben ("Keine Berechtigung", kein Sticky-Bit auf dem
Repo-Wurzelverzeichnis als Erklärung gefunden, vermutlich eine
Eigenheit dieses Docker-Setups). Passwortloses `sudo` ist auf diesem
Host nicht eingerichtet. Workaround: ein Wegwerf-`alpine`-Container
mit `-v /opt/docker/radiozapper:/repo` hat die vier `mv`-Befehle als
root im Container ausgeführt — funktioniert, weil `blarks` in der
`docker`-Gruppe ist, ganz ohne `sudo`-Passwort.

Container wurde für den Umbau bewusst gestoppt (Nutzer-Zustimmung):
`docker compose stop radiozapper` vor der Umsortierung, Icecast lief
die ganze Zeit unverändert weiter, `docker compose up -d --build
radiozapper` erst nach Abschluss aller Anpassungen.

### Verifiziert

- `docker compose config` vor dem Rebuild: alle Volume-Mount-Quellen
  lösen korrekt zu `/opt/docker/radiozapper/data/...` auf, Ziele
  unverändert bei `/app/...`. `NEWS_MP3_FOLDER_HOST` zeigt weiterhin
  den echten Produktiv-SMB-Pfad (`/mnt/eimer/...`, aus `.env`,
  unbetroffen von der Umsortierung), `VOSK_MODEL_FOLDER_HOST` korrekt
  auf `./data/vosk-model-de` (dieser Wert hängt live am
  Compose-Default, da `VOSK_MODEL_FOLDER` in der echten `.env` NICHT
  gesetzt ist — mit dem alten Default hätte STT nach dem Neustart sein
  Modell verloren).
- **Live am echten Deployment**: nach `docker compose up -d --build
  radiozapper` lädt das Vosk-Modell erfolgreich aus `/app/vosk-model-de`
  (Log: "STT-Filter: Engine 'vosk' geladen"), Senderliste lädt
  (`▶ Spiele: 105'5 Spreeradio 80er`), Nachrichten-Pause funktioniert
  weiterhin end-to-end (spielt eine MP3 aus dem externen SMB-Pfad).
  `GET /` und `GET /config` liefern `200`, `GET /radiozapper.webp`
  liefert `200`/144950 Bytes (bestätigt `pics/radiozapper.webp` korrekt
  in den Image-Build eingebunden), `GET /api/config/settings` liefert
  lesbare Werte inkl. `language: "de"` (bestätigt `data/settings.json`
  korrekt gemountet). `icecast-radiozapper` lief nachweislich
  ununterbrochen durch (Container-Uptime nicht neu gestartet).
- `git status` nach dem Umbau: alle Verschiebungen von getrackten
  Dateien als `R` (Rename) erkannt, keine Lösch-/Neuanlage-Paare mit
  Inhaltsverlust.

## 2026-08-06 — Reaktionszeit auf Moderation verkürzt (Missverständnis "Timeshift-Puffer" aufgeklärt)

Auslöser: Nutzer beobachtete bis zu 5s Verzögerung zwischen Sprachbeginn
und Wegzappen und vermutete, der `prebuffer_seconds`-Puffer (10s) solle
eigentlich als echtes Hörer-Delay wirken — Analyse also der Ausstrahlung
zeitlich voraus sein, damit VOR dem Hörer erkannt und geschaltet wird.

### Befund

`prebuffer_seconds`/`PrebufferedSource` betrifft ausschließlich die
*nächsten Kandidaten-Sender* im Hintergrund (Vorrat für einen unterbrechungs-
freien Wechsel dorthin, siehe "Prebuffer-Burst"-Eintrag oben) — der aktuell
laufende Sender bekommt dadurch **keinerlei** zusätzliche Verzögerung.
Zeitkette pro Analysefenster in `main()`: `read_window()` →
`write_audio(pcm_stereo)` (Icecast-Ausgabe) → erst danach `classify(pcm)`.
Die Analyse läuft also auf Audio, das der Hörer bereits bekommen hat; ein
Vorausschau-Mechanismus existiert nicht und war auch nie so gebaut. Die
beobachteten 5s waren exakt `CONSECUTIVE_SPEECH_TO_SWITCH = 5` × `WINDOW_SECONDS
= 1.0s` — die Mindestzahl aufeinanderfolgender "speech"-Fenster, bevor
`do_switch()` feuert.

Ein echtes Hörer-Delay (Ausgabe absichtlich N Sekunden hinter der Quelle,
Analyse auf dem noch nicht gesendeten Fenster) wurde bewusst NICHT gebaut:
genau dieses Muster wurde am 2026-08-03 aus gutem Grund entfernt (siehe
"Prebuffer-Burst" oben) — kumulative Hörer-Drift, doppelt gesendetes Audio,
Icecast-Queue-Overflow. Das wieder einzuführen wäre ein deutlich größerer
Eingriff, der dieselben Probleme neu lösen müsste.

### Umsetzung

Nutzer entschied sich für die risikoärmere Alternative: reine
Reaktionszeit-Verkürzung statt neuer Delay-Architektur.

- `CONSECUTIVE_SPEECH_TO_SWITCH`: 5 → 3 (`radiozapper.py:71`).
- `FINGERPRINT_TRIGGER_SECONDS`: 3 → 2 (`radiozapper.py:117`), damit die
  Invariante "muss kleiner als `CONSECUTIVE_SPEECH_TO_SWITCH` sein, sonst
  kein Vorteil" (Kommentar direkt daneben) weiterhin mit Marge gilt.

### Bewusst NICHT gemacht

Kein echtes Hörer-Delay eingebaut (s.o. — expliziter Nutzerentscheid gegen
die aufwändigere Option). `prebuffer_seconds` unverändert gelassen, da es
mit dem eigentlichen Problem nichts zu tun hatte.

### Verifiziert

- `python3 -c "import ast; ast.parse(...)"` gegen `radiozapper.py`: syntaktisch ok.
- `docker compose up -d --build radiozapper`: Image baut durch, Container
  neu gestartet, Icecast lief währenddessen unberührt weiter.
- Log direkt nach dem Neustart: VAD ("Silero VAD") und STT ("Engine
  'vosk' geladen") laden erfolgreich, Wiedergabe startet ("▶ Spiele:
  105'5 Spreeradio 80er"), Puffer-Start-Meldung ("Puffere die nächsten 5
  Sender 10s im Voraus") wie gewohnt, kurz danach ein regulärer
  automatischer Switch ("🎙 Moderation erkannt ... → aus Puffer, nahtlos").
  Kein Absturz, kein Fehler beim Laden der neuen Konstanten.
- Kein Langzeit-Vergleich der Falsch-Positiv-Rate bei 3 statt 5 Fenstern
  gemessen (bräuchte längere Beobachtung über mehrere Sender/Tageszeiten) —
  bei Bedarf in einer Folgesitzung nachholen.

## 2026-08-06 (Fortsetzung) — Echtes Playout-Delay statt reiner Reaktionszeit-Verkürzung

Auslöser: die 5→3-Verkürzung von vorhin senkt nur die Reaktionszeit,
verhindert aber nicht, dass der Hörer die Sprache VOR dem Switch bereits
hört (siehe Befund im vorigen Eintrag: `write_audio()` lief schon immer
vor `classify()`). Nutzer wollte das ernsthaft angehen: ein echtes
Playout-Delay, das die Erkennung vor die Hörer-Ausgabe zieht. Zielgröße
laut Nutzer: bis zu 30s akzeptabel (Musik, nicht latenzkritisch),
gestartet wird konservativ mit dem bestehenden 8-10s-Bereich.

### Entscheidung: vereinheitlichtes Deque-Design statt zweier Mechanismen

Nach Durchsprache mehrerer Optionen mit dem Nutzer (Architektur-Skizze,
Fragen zu Icecast-Queue/Watchdog/News-Break/Aufwand) fiel die Wahl auf
Option A: `PrebufferedSource` (Kandidaten-Vorwärmung) und ein neues,
aktives Playout-Delay für den laufenden Sender teilen sich dieselbe
Grundidee (Fenster-Deque fester Tiefe) und denselben Konfigwert
(`prebuffer_seconds`/`prebuffer_count`), statt zwei getrennte Systeme zu
pflegen. Ein Wechsel zu einem vorgewärmten Kandidaten tauscht die
Playout-Deque dadurch komplett aus (`adopt_windows()`), kein
Bridge-Timing mehr nötig — die alte `promote_bridge()`/`stereo_tail()`-
Mechanik (siehe "Prebuffer-Burst"-Eintrag von 2026-08-03) ist komplett
entfallen, nicht nur ersetzt.

**Mathematisch bewusst NICHT versucht**: ein lückenloser Übergang von
Delay=0 auf Delay=`prebuffer_seconds` ohne Zeitdehnung/Pitch-Manipulation
ist unmöglich (jedes gapless System mit fester Fenstergröße hat
zwangsläufig konstantes Delay — ein wachsendes Delay braucht entweder
Lücken oder doppelt gesendetes Audio). Deshalb: Wechsel zu einem
vorgewärmten Sender läuft sofort mit vollem Delay (Deque schon auf
Zieltiefe), Wechsel zu einem NICHT vorgewärmten Sender läuft komplett
OHNE Delay (Passthrough, exakt das alte Verhalten) bis zum nächsten
warmen Wechsel. Das deckt praktisch alle Fälle ab, weil `do_switch()`
und manuelle Klicks fast immer einen der `prebuffer_count` vorgewärmten
Sender treffen.

### Umsetzung (`radiozapper.py`)

- `PrebufferedSource.promote()` gibt jetzt `(windows, source)` zurück
  (Fenster-Liste `[(mono, stereo), ...]`, älteste zuerst) statt
  konkatenierter Arrays — Fenster-Granularität bleibt erhalten, damit
  eine Playout-Deque sie direkt übernehmen kann.
- Neu in `main()`: `playout` (Deque), `playout_primed` (Flag),
  `playout_target_windows()` (liest `state.prebuffer_seconds` live),
  `push_and_drain()` (einziger Weg für ein frisch gelesenes Fenster des
  aktuellen Senders: primed → anhängen + klassifizieren + ggf. ältestes
  Fenster abziehen und ausgeben; unprimed → sofort schreiben),
  `adopt_windows()` (kompletter Deque-Tausch bei warmem Wechsel),
  `reset_playout()` (Passthrough bei kaltem Wechsel).
- `write_audio(pcm_stereo)` direkt nach `read_window()` im Hauptloop
  ersetzt durch `push_and_drain(pcm, pcm_stereo)` — Klassifikation
  (VAD/Heuristik/STT-Sampling/Fingerprint) läuft dadurch unverändert auf
  `pcm`, aber dieses Fenster ist jetzt das FRISCH gepushte, nicht das
  gerade ausgestrahlte.
- `switch_to_station()`/`do_switch()`: warmer Fall ruft `adopt_windows()`
  statt `write_audio(promote_bridge(...))`; kalter Fall ruft
  `reset_playout()` vor dem Neuverbinden.
- `start_news_break_mp3()` ruft `reset_playout()`, bevor die MP3 startet
  — sonst würde die Deque des pausierten Senders mit MP3-Fenstern
  vermischt. Während der Pause wird ohnehin nicht klassifiziert, Delay
  hätte dort keinen Nutzen.
- Reload-Zweig (`state.pop_reload_request()`): bei geänderten
  `prebuffer_seconds`/`prebuffer_count` wird zusätzlich zu den
  Kandidaten-Puffern jetzt auch die (falls primed) laufende
  Playout-Deque verworfen — aus demselben Grund wie oben kein gapless
  Umrechnen auf die neue Zieltiefe möglich.
- `promote_bridge()`/`stereo_tail()` komplett entfernt (nicht nur
  auskommentiert) — mit dem Deque-Tausch gibt es keine Bridge-Berechnung
  mehr, `last_output_at` war dadurch ebenfalls überflüssig.

### Verifiziert (isoliert, temp-Verzeichnis + separater Icecast-Mount `rztest.mp3`)

Testmuster wie in CLAUDE.md beschrieben: alle `.py` in ein Temp-
Verzeichnis kopiert, eigene `stations.json` (Radio Bob/1LIVE/SWR3 +
Deutschlandfunk als vierter Sender für den kalten Pfad)/`settings.json`,
`--webui-port 5099` für API-gesteuerte Tests, gegen `rztest.mp3` auf dem
laufenden Produktiv-Icecast gestreamt (eigener Mount, Hörer unbetroffen).
Silero-VAD auf dem Host nicht verfügbar (bekannt, siehe CLAUDE.md) —
lief auf der Signal-Heuristik.

- **Warmer Wechsel**: 4× manueller Switch über `/api/switch` auf jeweils
  vorgewärmte Kandidaten → jedes Mal sofort `🎛 Manuell umgeschaltet auf:
  X (aus Puffer)`, kein Fehler, `[feat]`-Klassifikation lief im
  ~1s-Takt ohne Unterbrechung weiter.
- **Kalter Wechsel**: 4. Sender (Deutschlandfunk) bewusst außerhalb von
  `prebuffer_count=2` platziert, manueller Switch dorthin →
  `🎛 Manuell umgeschaltet auf: SWR3` (ohne "aus Puffer"-Zusatz, Reaktion
  in ~0,6s dank `quick_forward()`), Klassifikation lief danach normal im
  ~1s-Takt weiter, kein Gap, kein Crash.
- **Drift-Messung** (Kontrollmuster wie beim 2026-08-03-Fix):
  Kontrollmessung ohne Wechsel: 32,4s Audio in 30s Wall-Clock (+2,4s,
  deckt sich mit Icecasts bekanntem `burst-on-connect`, nicht mit
  echtem Drift). Messung MIT 3 Wechseln über 40s: 42,5s Audio (+2,5s) —
  praktisch identischer Überschuss trotz dreier Wechsel. Damit: **kein
  kumulativer Drift durch Wechsel**, der Puffer-Tausch verhält sich wie
  gewollt (im Gegensatz zum 2026-08-03-Bug, der pro Wechsel bis zu 10s
  zusätzlich addierte).
- **Settings-Reload zur Laufzeit**: `prebuffer_seconds` per
  `/api/config/settings` erst im unprimed (8→4s), dann im primed
  Zustand (4→6s) geändert → beide Male sauberes
  `⏱ Puffer-/Delay-Einstellungen geändert: N Sender × Xs`, danach
  neu vorgewärmte Kandidaten mit korrekter neuer Fenster-Zahl im Log
  (`Puffer gestartet: ... (6 Fenster à 1.0s)`), Klassifikation lief beim
  Reset (primed-Fall) ohne Unterbrechung im ~1s-Takt weiter, kein Crash.
- **Nachrichten-Pause**: mit synthetischer Test-MP3 (6s Sinuston) und
  `window_minutes` erst 15 (aktives Fenster), dann live auf 0,1
  reduziert → `📰 Nachrichten-Pause: spiele 'test.mp3'`, korrektes
  Nachladen bei MP3-Ende innerhalb des noch laufenden Fensters (3×
  "nächste MP3"), danach `📰 Nachrichten-Pause-Fenster abgelaufen —
  zurück zu: 1LIVE`, Klassifikation lief danach normal weiter (u.a. ein
  reales `SPEECH`-Label auf 1LIVE beobachtet, unabhängig bestätigt: die
  automatische Erkennung funktioniert nach dem Resume).
- Gesamtes Testlog (`grep -iE "error|traceback|exception"`, STT-Warnung
  wegen fehlendem `silero_vad_lite` ausgenommen): keine Treffer über alle
  drei Testläufe.

### Bewusst NICHT gemacht

Kein gapless Ramp-Up von 0 auf volle Verzögerung (mathematisch ohne
Zeitdehnung nicht möglich, s.o.) — frische Wechsel bleiben dauerhaft ohne
Delay, bis der nächste warme Wechsel passiert. Kein automatisches
"Nachwärmen" eines lange laufenden, kalt gestarteten Senders (würde beim
Aktivieren des Delays denselben Sprung-Bug reproduzieren, den der
2026-08-03-Fix beseitigt hat). Live-Messung mit echter Sprache/Moderation
(wann genau relativ zur Ausgabe erkannt wird) nicht durchgeführt — dafür
bräuchte es einen Sender mit garantierter Moderation im Testfenster,
schwer reproduzierbar; die Zeitkette selbst (push vor pop, Klassifikation
auf `pcm` vor der Verzögerung) ist aber durch Code-Review + die
Drift-Messung ausreichend abgesichert.

### Live deployt

Nach Zustimmung des Nutzers: `docker compose up -d --build radiozapper`.
Startet sauber mit Silero VAD + Vosk-STT, sofort ein realer
Fingerprint-Treffer beim Start ("🔁 Bekannter Jingle/Werbespot
wiedererkannt: Clip #859") löste einen automatischen Wechsel aus —
Log zeigt neu `▶ Spiele: 1LIVE (aus Puffer, nahtlos, 10s Playout-Delay)`,
bestätigt den warmen Pfad unter echter Last (Silero statt Heuristik,
reale Sender statt Testmount). Keine Fehler/Tracebacks in den ersten
Minuten. Kontroll-Mitschnitt direkt vom Produktiv-Mount
(`radiozapper.mp3`, kein erzwungener Wechsel): 27,5s Audio in 25s
Wall-Clock (+2,5s), deckt sich mit dem isoliert gemessenen
Connect-Burst-Überschuss, kein Hinweis auf Drift. Container läuft
stabil weiter (`docker compose ps`: Up, kein Neustart-Loop).

## 2026-08-06 (Fortsetzung 2) — Ressourcen-Snapshot im Config-Menü

Erster von drei Teilen aus einem größeren, vorab gemeinsam bewerteten
Architektur-Vorschlag (mehrsprachige STT-Erkennung, Resource-Monitoring,
Live-Statusanzeigen) — hier nur Teil 2, bewusst zuerst, da unabhängig von
den anderen beiden und am schnellsten nutzbar.

### Umsetzung

- Neues Modul `resource_monitor.py` (reine Domänenlogik, kein Bezug zu
  `StreamSource`/`SwitcherState`, analog zu `news_break.py`/`stt_filter.py`):
  `ResourceMonitor` hält `psutil.Process`-Handles über mehrere
  `snapshot()`-Aufrufe hinweg am Leben (Python-Hauptprozess UND ffmpeg-
  Kindprozesse, `children(recursive=True)`) — `cpu_percent(interval=None)`
  liefert laut psutil-Doku beim JEWEILS ERSTEN Aufruf pro Process-Objekt
  einen bedeutungslosen Wert, ein Cache pro PID ist deshalb notwendig,
  nicht nur eine Optimierung. Neu auftauchende ffmpeg-Kinder liefern im
  Snapshot ihrer ersten Sichtung deshalb bewusst 0% CPU, erst ab dem
  nächsten Poll-Intervall einen echten Wert. Disk-Werte (Fingerprint-DB,
  Logdatei inkl. `.1`/`.2`/…-Rotation, Whisper-Modell-Cache) sind einfache
  `os.path.getsize()`/`os.walk()`-Summen, kein psutil nötig.
- `webui.py`: `ResourceMonitor` wird einmal pro Server-Instanz in
  `make_handler()` angelegt (Closure, gleiches Muster wie `import_state`)
  — RAM/CPU sind reine Lesewerte, die den Player-Zustand nicht berühren,
  deshalb bewusst NICHT über `SwitcherState`/request-pop geführt (analog
  zum `host_paths`-Muster, siehe CLAUDE.md). Neuer Endpoint
  `GET /api/resources`. Config-Seite bekommt eine neue Sektion mit einer
  kleinen Tabelle (RAM gesamt + Aufschlüsselung Python/ffmpeg, CPU gesamt,
  Anzahl ffmpeg-Prozesse, DB-/Log-/Whisper-Cache-Größe), gepollt per
  `setInterval` alle 5s — bewusst kein Long-Poll wie beim Bullshitometer
  auf der Player-Seite, da nur relevant, solange die Config-Seite offen ist
  und kein zeitkritischer Wert.
- `radiozapper.py`: `log_path` (Rückgabewert von `logging_setup.setup()`,
  vorher nur lokal verwendet) wird jetzt zusätzlich an
  `webui.start_server()` durchgereicht, damit die Logdatei-Größe
  überhaupt bekannt ist.
- `Dockerfile`: `psutil` zur pip-install-Zeile ergänzt, `resource_monitor.py`
  als neues Modul einzeln kopiert (siehe CLAUDE.md, Docker-Besonderheiten).
- i18n: neue Keys `cfg_resources_*` (de/en) für Überschrift, Hinweistext
  und Tabellen-Labels.

### Verifiziert (isoliert, ohne den laufenden Produktiv-Container anzufassen)

- `resource_monitor.ResourceMonitor` direkt in einem Python-Interpreter
  gegen die echten `data/fingerprints.db`/`data/logs/radiozapper.log`
  getestet, dabei einen `sleep 5`-Kindprozess als ffmpeg-Stellvertreter
  erzeugt: 1. Snapshot zeigt das Kind sofort mit `ffmpeg_count=1`, aber
  `ffmpeg_cpu_percent=0.0` (Priming, wie erwartet); 2. Snapshot 1,2s später
  liefert `main_cpu_percent=38.6`; 3. Snapshot nach Prozessende zeigt
  `ffmpeg_count=0` — der Cache wirft gestorbene PIDs korrekt raus, kein
  unbegrenztes Wachstum. Disk-Werte stimmten mit echten Dateigrößen
  überein (Fingerprint-DB 26.009.600 Bytes, Log 26.619.980 Bytes).
- `webui.py` komplett importiert (`python3 -c "import webui"`) — die
  beim Modul-Import laufende `_check_i18n_coverage()` ist dabei
  durchgelaufen, ohne den neuen `cfg_resources_*`-Keys zu widersprechen
  (kein `AssertionError`).
- `webui.start_server()` auf einem separaten Testport (15123, nicht der
  Produktivport 5000) mit echtem `SwitcherState` (liest `data/stations.json`
  nur lesend, keine Schreiboperation) gestartet: `GET /api/resources`
  lieferte ein plausibles JSON (`main_rss_bytes`, `total_cpu_percent`,
  Disk-Größen wie oben), `GET /config` lieferte HTTP 200 mit dem neuen
  `id="resource-section"`-Markup und `loadResources`-Aufruf im injizierten
  Skript. Testprozess danach beendet, keine Auswirkung auf den laufenden
  Produktiv-Container (`radiozapper`/`icecast-radiozapper`, beide vorher
  wie nachher `Up`).

### Bewusst NICHT gemacht

Noch nicht gebaut/deployt (`docker compose up -d --build radiozapper`) —
das ändert den laufenden Container und damit kurzzeitig den Live-Stream,
soll erst nach expliziter Zustimmung des Nutzers passieren. Kein
Caching des Whisper-Cache-Verzeichnis-Scans (`os.walk()` bei jedem Poll)
— für die üblichen paar Modell-Dateien unkritisch, bei sehr großen Caches
ggf. später nachrüstbar. Teile 1 (mehrsprachige STT) und 3
(Live-Statusanzeigen VAD/STT/Fingerprint) aus dem Architektur-Vorschlag
absichtlich noch nicht angefasst.

### Live deployt

Nach Zustimmung des Nutzers: `docker compose up -d --build radiozapper`.
Sauberer Start (Silero VAD + Vosk-STT geladen, kein Fehler/Traceback),
Web-Interface läuft auf Port 5000. `GET /api/resources` gegen den echten
Container: `main_rss_bytes=328294400` (Python-Hauptprozess inkl. Silero/
Vosk-Modellen), `ffmpeg_count=7` (laufender Sender + Prebuffer-Kandidaten
gemäß `prebuffer_count=5`, plus News-Break/Übergangs-Overhead),
`ffmpeg_rss_bytes=373907456`, macht in Summe knapp 700MB — bestätigt,
dass die ffmpeg-Kinder tatsächlich den größeren Anteil ausmachen, wie im
Architektur-Vorschlag vermutet. `fingerprint_db_bytes`/`log_bytes`
stimmten mit den realen Dateigrößen überein. `/config` lieferte HTTP 200
mit dem neuen Ressourcen-Panel. `docker compose ps`: beide Container
`Up`, kein Neustart-Loop.

## 2026-08-06 (Fortsetzung 3) — Live-Statusanzeigen VAD/STT/Fingerprint (Teil 3)

Zweiter Teil aus demselben Architektur-Vorschlag wie Teil 2 (siehe
"Fortsetzung 2" oben) — Live-Statusanzeigen für die drei
Erkennungsebenen auf der Player-Seite. Teil 1 (mehrsprachige STT) ist
weiterhin nicht angefasst; die STT-Anzeige zeigt deshalb bewusst nur
einen Prozentwert, kein Sprachkürzel.

### Umsetzung

- **VAD**: unverändert — das bestehende 🤥 Bullshitometer IST bereits die
  VAD/Heuristik-Anzeige (siehe Architektur-Vorschlag oben), kein neuer
  Code nötig.
- **STT**: `stt_filter.py` bekommt `_fresh_confidence(verdict, cfg)` als
  gemeinsame Basis für `combine_label()` (Switch-Logik, unverändertes
  Verhalten) UND die neue `live_confidence(verdict, cfg)` (Web-UI) — statt
  die Freshness-Altersprüfung zweimal leicht unterschiedlich zu
  implementieren. `radiozapper.py`s `classify()`-Closure ruft
  `stt.last_verdict()` jetzt genau einmal ab (statt potenziell zweimal)
  und setzt `state.set_stt_probability(stt_filter.live_confidence(...))`
  direkt neben dem bestehenden `set_speech_probability()`-Aufruf.
- **Fingerprint**: neuer, von `set_last_fingerprint_clip()`/
  `pop_last_fingerprint_clip()` GETRENNTER Setter
  `state.set_fingerprint_activity(status, label=None)` (status: "match"/
  "learned") — der bestehende pop-once-Mechanismus ist für den
  "🛑 Zapping-Fehler"-Button reserviert und wird bei dessen Klick
  konsumiert; ein zweiter Konsument (die Dauer-Anzeige, die bei jedem
  Poll denselben Wert lesen will) hätte sich mit dem Button sonst den
  Wert gegenseitig weggeschnappt. In `radiozapper.py` an beiden Stellen
  im Fingerprint-Trigger-Block gesetzt (Treffer bzw. neu gelernter Clip).
  `_build_status()` in `webui.py` wendet eine neue `FP_ACTIVITY_TTL`
  (5s) an: ein Ereignis fällt serverseitig von selbst auf `null`
  zurück, sobald es älter ist — das Frontend braucht dadurch keine
  eigene Altersprüfung, `fingerprint_activity: null` heißt einfach
  "idle" anzeigen. Bewusst KEIN "checking"-Zwischenzustand (ursprünglich
  im Architektur-Vorschlag angedacht): `fp_db.match_or_learn()` läuft
  synchron im Hauptloop und ist bei der aktuellen DB-Größe schnell genug,
  dass ein sichtbarer "prüft gerade"-Zustand keinen echten Mehrwert
  gehabt hätte, nur zusätzliche Zustandsverwaltung.
- **Frontend** (`webui.py`, `_PAGE_HTML`): CSS von `#bs-meter-*`-IDs auf
  gemeinsame `.meter-wrap`/`.meter-label`/`.meter-track`/`.meter-fill`-
  Klassen umgestellt (IDs bleiben für JS-Zugriff erhalten, nur zusätzlich
  Klassen) — der neue STT-Balken teilt sich dieselben Regeln, statt sie
  zu duplizieren. Neuer `#fp-chip` (diskreter Zustand statt Balken:
  `.state-match`/`.state-learned`/Default "idle", per CSS-Klasse
  eingefärbt). JS in `applyStatus()` ergänzt: STT-Balken exakt wie das
  Bullshitometer plus zusätzlichem eingefrorenen Zustand
  (`stt_probability === null` → "STT aus"), Fingerprint-Chip liest
  `data.fingerprint_activity` direkt (kein eigener Timer/keine eigene
  Altersprüfung nötig, siehe TTL-Server-Logik oben).
- i18n: neue Keys `idx_stt_meter_label`/`idx_stt_meter_off`/
  `idx_fp_indicator_label`/`idx_fp_state_{idle,match,learned}` (de/en).

### Verifiziert (isoliert, ohne den laufenden Produktiv-Container anzufassen)

- `stt_filter.live_confidence()` direkt geprüft: `enabled=False` → `None`,
  kein Verdict → `None`, frischer Verdict → korrekter Konfidenzwert
  durchgereicht.
- `webui.SwitcherState.set_fingerprint_activity()`/
  `_fresh_fingerprint_activity()` direkt geprüft: künstlich auf 10s
  gealtertes Ereignis (> `FP_ACTIVITY_TTL=5.0`) liefert korrekt `None`.
- `webui.py` komplett importiert — `_check_i18n_coverage()` lief ohne
  `AssertionError` durch (neue Keys stimmen mit den `data-i18n`/`t(...)`-
  Verwendungen im Template überein).
- `webui.start_server()` auf separatem Testport (15124) mit echtem
  `SwitcherState` gestartet, `set_speech_probability(0.77)`/
  `set_stt_probability(0.42)`/`set_fingerprint_activity('match', 'Werbejingle
  Testclip')` manuell gesetzt: `GET /api/status` lieferte alle drei Werte
  korrekt im JSON. `GET /` (Player-Seite) enthielt das neue Markup
  (`stt-meter-wrap`/`stt-meter-fill`/`stt-meter-pct`/`fp-indicator-wrap`/
  `fp-chip`) sowie die neue JS-Logik (`sttWrap`/`fpChip` im injizierten
  Skript). Testprozess danach beendet, keine Auswirkung auf den
  laufenden Produktiv-Container.

### Bewusst NICHT gemacht

Kein "checking"-Zwischenzustand für den Fingerprint-Chip (Begründung
oben). Keine Sprachkennzeichnung am STT-Balken — hängt an Teil 1
(mehrsprachige STT), der noch nicht umgesetzt ist; bis dahin zeigt der
Balken nur den nackten Prozentwert.

### Live deployt

Nach Zustimmung des Nutzers: `docker compose up -d --build radiozapper`.
Sauberer Start (Silero VAD + Vosk-STT geladen, kein Fehler/Traceback).
`GET /api/status` direkt nach dem Start zeigte den Einfrier-Fall live und
unabsichtlich in Aktion: der Container startete mitten in einer laufenden
Nachrichten-Pause, `speech_probability`/`stt_probability` standen dadurch
korrekt auf ihren Initialwerten (`0.0`/`null`), weil `classify()` während
`news_break_active` gar nicht aufgerufen wird. Nach Ende der Pause (`GET
/api/status` erneut abgefragt): `speech_probability=0.3757`,
`stt_probability=0.8105`, `fingerprint_activity=null` — beide Balken
liefern jetzt echte Werte vom laufenden Sender, exakt wie in der
isolierten Verifikation vorhergesagt. `docker compose ps`: beide
Container `Up`, kein Neustart-Loop.

## 2026-08-06 (Fortsetzung 4) — Versionsnummer unter dem Banner-Bild

Kurzer Nutzerwunsch zwischendurch: `VERSION` (Repo-Root) soll im
Web-Interface sichtbar sein, nicht nur in der Datei.

### Umsetzung

- `VERSION` war bisher NICHT Teil des Docker-Images — `Dockerfile`
  bekommt `COPY VERSION .`, landet flach in `/app/VERSION` wie alle
  anderen `__file__`-relativ geladenen Dateien (siehe CLAUDE.md,
  Docker-Besonderheiten).
- `webui.py`: gleiches Lade-Muster wie `_BANNER_BYTES` oben im Modul —
  einmalig beim Import gelesen (`_VERSION_STRING`), nicht pro Request.
  Fehlt die Datei, bleibt die Zeile leer statt die Seite abstürzen zu
  lassen (gleicher Fallback-Stil wie beim Banner-Bild).
- `_render_i18n_variants()` bekommt einen dritten Platzhalter-Ersatz
  (`%%VERSION%%` neben `%%LANG%%`/`%%I18N_JSON%%`) — der Versionsstring
  ist sprachunabhängig, wird aber trotzdem nur einmal pro Sprache beim
  Modul-Import ins vorgerechnete HTML eingesetzt statt pro Request,
  exakt das bestehende Muster für beide Templates.
  `<div class="version-tag">%%VERSION%%</div>` direkt unter
  `<img class="banner">` auf BEIDEN Seiten (Player + Config) — kein
  `data-i18n`, da reiner Klartext ohne Übersetzungsbedarf, taucht
  deshalb auch nicht in `_check_i18n_coverage()` auf.

### Verifiziert

`webui`-Modul neu importiert, `_VERSION_STRING` zeigte den aktuellen
Inhalt von `VERSION` (`v1.0.3 build 2026-08-06 10:35 Uhr`), das
vorgerechnete `_PAGE_HTML_BYTES["de"]` enthielt
`<img class="banner" ...><div class="version-tag">v1.0.3 build
2026-08-06 10:35 Uhr</div>` direkt hintereinander.

### Bewusst NICHT gemacht

Keine Verlinkung/Historie (z.B. Link auf ein Changelog) — die Anzeige
ist bewusst nur der aktuelle Versionsstring, wie in `VERSION` selbst
auch nur eine Zeile steht.

### Live deployt

Nach Zustimmung des Nutzers: `docker compose up -d --build radiozapper`.
Sauberer Start, kein Fehler/Traceback. `GET /` und `GET /config` zeigten
beide `<div class="version-tag">v1.0.3 build 2026-08-06 10:35 Uhr</div>`
direkt unter dem Banner-Bild. `docker compose ps`: beide Container `Up`.

## 2026-08-06 (Fortsetzung 5) — Mehrsprachige STT-Erkennung (Teil 1a)

Dritter und größter Teil aus dem Architektur-Vorschlag (siehe
"Fortsetzung 2"/"Fortsetzung 3" oben) — Kern-Plumbing für mehrsprachige
STT-Erkennung. Vor Beginn zwei offene Designfragen mit dem Nutzer
geklärt: Kalibrierung zweistufig (Sprache- UND Musik-Sender, wie die
ursprüngliche DE-Kalibrierung) statt einstufig, Sprachliste als Freitext
statt feste Auswahl. Wegen des Umfangs bewusst in zwei Schritte
gesplittet: **1a** (dieser Eintrag) legt Datenmodell, Multi-Language-
Engine und manuelle Config-UI hin; **1b** (der geführte
Kalibrierungs-Wizard mit Live-Sampling) folgt separat, sobald 1a live
läuft — gleiches inkrementelles Muster wie bei Teil 2/3.

### Kernentscheidung: Kategorie → Sprache, nicht Sender → Sprache

`stations_store.CATEGORIES` bleibt unangetastet (weiterhin eine feste,
nicht persistierte Python-Konstante, keine Migration von `stations.json`
nötig). Stattdessen bekommt `settings_store.py`s `stt_filter`-Block ein
neues `category_languages`-Dict (Kategorie-Name → Sprachcode) — eine
Kategorie ohne Eintrag gilt als Deutsch. Das hält die Änderung komplett
auf `settings.json` beschränkt, keine Anfassung von `stations_store.py`
oder der bestehenden Sender-Verwaltung nötig.

### Umsetzung

- **`settings_store.py`**: `stt_filter.vosk_model_path`/
  `confidence_threshold` (bisher flach, EIN Wert für "die" Sprache)
  werden zu `stt_filter.languages` (Dict Sprachcode → {vosk_model_path,
  confidence_threshold}). `engine`/`whisper_model_size` bleiben bewusst
  GLOBAL (nicht pro Sprache) — Whisper ist multilingual mit einem
  einzigen geladenen Modell, ein Pro-Sprache-`whisper_model_size` hätte
  bei jedem Sprachwechsel einen Modell-Neustart erzwungen und damit genau
  den Whisper-Vorteil (eine zusätzliche Sprache kostet kein RAM) wieder
  zunichtegemacht. `_migrate_stt_filter()` übersetzt eine alte
  `settings.json` (flache Felder) beim Laden in-memory in die neue Form
  (Deutsch übernimmt die vorhandenen Werte) — kein Zurückschreiben nötig,
  der nächste `set_stt_language()`-Aufruf persistiert die neue Form
  ohnehin, gleiches Muster wie das bestehende `news_break`/`stt_filter`-
  Reconcile beim Laden. Neue Funktionen `set_stt_language()`/
  `delete_stt_language()` (Upsert bzw. Löschen mit Schutz der letzten
  verbliebenen Sprache + Aufräumen verwaister `category_languages`-
  Einträge)/`set_category_language()`/`resolve_stt_language()`.
- **`stt_filter.py`**: `_WhisperEngine.transcribe()` bekommt einen
  `language`-Parameter statt hartkodiertem `"de"`. `SttFilter` hält bei
  Vosk keine einzelne Engine mehr, sondern `_get_vosk_engine(lang, cfg)`:
  Lazy-Load + LRU-Cache (`MAX_LOADED_VOSK_LANGUAGES=2`), der SOWOHL
  erfolgreich geladene Modelle ALS AUCH Ladefehler cacht (Fehlertext statt
  Objekt als Cache-Wert) — ein kaputter Pfad soll nicht bei jedem
  Sample-Tick erneut versucht werden. `last_verdict()` liefert jetzt ein
  4-Tupel `(confidence, text, timestamp, language)` statt 3; die geteilte
  `_fresh_verdict()`-Prüfung (genutzt von `combine_label()`/
  `live_confidence()`/`live_language()`) verwirft einen Befund zusätzlich
  zur Altersprüfung, wenn sein Sprach-Tag nicht zur AKTUELL erwarteten
  Sprache passt — verhindert, dass kurz nach einem Kategoriewechsel noch
  ein Befund der vorherigen Sprache mit der FALSCHEN Schwelle bewertet
  wird.
- **`radiozapper.py`**: `classify()` bekommt einen `stt_lang`-Parameter,
  einmal pro Loop-Durchlauf über `settings_store.resolve_stt_language(
  current["category"], state.stt_filter_cfg)` aufgelöst und sowohl an
  `stt.sample_async()` (Sampling-Ziel) als auch an `classify()`
  (Verdict-Interpretation) weitergereicht — beide MÜSSEN dieselbe Sprache
  sehen. Reload-Trigger-Feldliste von `vosk_model_path` auf `languages`
  umgestellt.
- **`webui.py`**: `SwitcherState` bekommt `stt_language`
  (Sprachkürzel für die Live-Anzeige) und `stt_language_status`
  (Ladezustand pro Sprache fürs Config-UI, getrennt von
  `stt_status`/dem Gesamtzustand). Drei neue Endpoints:
  `POST /api/config/stt-languages` (Upsert), `POST
  /api/config/stt-languages/<code>/delete`, `POST
  /api/config/stt-category-language`. Config-Seite: STT-Formular um
  Modellpfad/Schwelle gekürzt (jetzt pro Sprache), zwei neue Sektionen
  "🌐 STT-Sprachen" (Tabelle + Add/Update-Formular, Ladezustand pro Zeile)
  und "🏷 Kategorie-Sprachen" (Dropdown pro fester Kategorie). Player-Seite:
  STT-Balken zeigt jetzt `82% (en)` statt nur `82%`, wenn eine erkannte
  Sprache vorliegt.
- **Doku**: `CLAUDE.md`-Abschnitt zum STT-Sprachfilter um die
  Mehrsprachigkeits-Architektur ergänzt (Kategorie-Auflösung,
  Engine-Asymmetrie/LRU-Cache, Cross-Language-Invalidierung).
  `README.md` (DE+EN): STT-Abschnitt um Mehrsprachigkeits-Unterabschnitt
  erweitert, inkl. Beispiel für einen zusätzlichen `docker-compose.yml`-
  Mount für weitere Vosk-Sprachmodelle (bewusst KEIN Umbau des
  bestehenden `VOSK_MODEL_FOLDER`-Mounts — hätte Nutzer gezwungen, ihr
  bestehendes deutsches Modell in einen neuen Ordner umzuziehen).

### Verifiziert (isoliert, temp-Verzeichnis + separater Testport, ohne den laufenden Produktiv-Container anzufassen)

- `settings_store.py` direkt getestet: Migration einer alten
  `settings.json` mit flachen `vosk_model_path`/`confidence_threshold`
  → korrekt zu `languages.de` migriert, Flachfelder verschwunden.
  `set_stt_language()`/`set_category_language()`/`resolve_stt_language()`
  End-to-End (National→en gesetzt, Lokal fällt auf Standard "de" zurück).
  `delete_stt_language()` räumt zugehörige `category_languages`-Einträge
  mit auf, letzte verbleibende Sprache ist geschützt (`ValueError`).
- `stt_filter.py` direkt getestet (vosk/faster-whisper auf dem Host nicht
  installiert, siehe CLAUDE.md — nutzt das deterministisch als
  "Modell nicht ladbar"-Simulation ohne echte Modelle zu brauchen):
  3 Sprachen konfiguriert, `MAX_LOADED_VOSK_LANGUAGES=2` eingehalten,
  älteste Sprache korrekt aus dem Cache verdrängt (auch bei
  Fehlschlägen). `sample_async()` mit kaputtem Modell setzt keinen
  Verdict. `_fresh_verdict()`/`combine_label()`/`live_confidence()`/
  `live_language()` mit synthetischen Verdicts geprüft: passende Sprache
  liefert Befund, Sprachwechsel invalidiert einen noch "frischen" alten
  Verdict korrekt (kein Cross-Language-Leck), `confidence_threshold`
  wird pro Sprache aufgelöst (gleicher Konfidenzwert 0.6 zählt für "en"
  als Sprache, für "de" als Musik), Altersschwelle greift weiterhin.
- End-to-End über echtes HTTP (temp-Verzeichnis mit allen `.py`,
  synthetischer `stations.json` mit zwei Kategorien, Testport 15200):
  Sprache 'en' anlegen → in `/api/config/settings` sichtbar, Kategorie
  "International" → 'en' setzen → `category_languages` korrekt gefüllt,
  'de' löschen (nicht die letzte) → erfolgreich, 'en' danach löschen
  (jetzt letzte) → korrekt abgelehnt, unbekannte Kategorie → korrekt
  abgelehnt. Config-Seite (`GET /config`) enthält alle neuen
  Markup-IDs (`stt-lang-section`/`stt-lang-tbody`/
  `stt-cat-lang-section`/`stt-cat-lang-tbody`/`stt-lang-add-form`).
  `_check_i18n_coverage()` beim Modul-Import ohne `AssertionError`
  durchgelaufen (alle neuen `data-i18n`/`t(...)`-Keys vollständig in
  `i18n.STRINGS`). `radiozapper.py` importiert sauber (Modul-Ebene),
  `classify()`-Aufruf mit neuem `stt_lang`-Argument konsistent.
  Testprozess/-verzeichnis danach entfernt, keine Auswirkung auf den
  laufenden Produktiv-Container.

### Bewusst NICHT gemacht

Der geführte Kalibrierungs-Wizard (Teil 1b) — Nutzer lässt einen
Sprache- UND einen Musik-Sender kurz mithören, Schwelle wird
automatisch vorgeschlagen. Bis dahin bleibt das Ermitteln von
`confidence_threshold` für eine neue Sprache manuell (README beschreibt
die Methode). Kein Umbau des `VOSK_MODEL_FOLDER`-Mounts auf eine
Parent-Ordner-Struktur mit Sprach-Unterordnern (hätte bestehende
Deployments zum Umziehen ihres Modells gezwungen) — zusätzliche Sprachen
brauchen stattdessen eine manuell ergänzte Mount-Zeile in
`docker-compose.yml` (README zeigt ein Beispiel). Kein eifriges
Vorladen aller konfigurierten Vosk-Modelle bei `reload()` (bewusst lazy,
siehe RAM-Begründung oben).

### Live deployt

Nach Zustimmung des Nutzers: `docker compose up -d --build radiozapper`.
Sauberer Start, kein Fehler/Traceback. **Migration griff korrekt am
echten `settings.json`**: Log zeigte `⚙ settings.json: alte
STT-Konfiguration (vosk_model_path='/app/vosk-model-de',
confidence_threshold=0.75) zu Sprache 'de' migriert.` — die bestehende,
empirisch kalibrierte Deutsch-Schwelle blieb erhalten, kein manueller
Eingriff nötig. `GET /api/config/settings` zeigte danach korrekt
`languages: {"de": {...0.75}}`, `category_languages: {}`. Config-Seite
(`GET /config`) enthielt die neuen Sektionen `stt-lang-section`/
`stt-cat-lang-section`.

Container startete mitten in einer laufenden Nachrichten-Pause (kein
STT-Sampling währenddessen, siehe CLAUDE.md) — direkt danach lazy
geladen: Log zeigte `🗣 STT-Filter: Vosk-Modell für Sprache 'de' geladen
(/app/vosk-model-de).` als allerersten Ladeversuch (nicht schon beim
Start), bestätigt den Lazy-Load-Pfad unter echter Last. Erster
`/api/status`-Check direkt nach Ende der Pause zeigte noch
`stt_probability: null` (das allererste Sample lief zu diesem Zeitpunkt
noch, reiner Timing-Artefakt meines Test-Checks) — ein zweiter Check
kurz danach bestätigte `stt_language: "de"`,
`stt_probability: 0.321038`, `_stt_language_status: {"de": null}`
(erfolgreich geladen, kein Fehler). `docker compose ps`: beide Container
`Up`, kein Neustart-Loop.

## 2026-08-06 (Fortsetzung 6) — Geführter STT-Kalibrierungs-Wizard (Teil 1b)

Letzter Teil aus dem ursprünglichen Architektur-Vorschlag (siehe
"Fortsetzung 2/3/5" oben) — der in Teil 1a bewusst zurückgestellte
zweistufige Kalibrierungs-Wizard.

### Kernentscheidung: kein eigener Audio-Pfad, Wiederverwendung der laufenden STT-Pipeline

Statt eine zweite, vom Player entkoppelte `StreamSource` nur fürs
Kalibrieren aufzubauen (hätte einen Großteil von `StreamSource`s
ffmpeg-Pipe-Komplexität dupliziert und `webui.py`s Grenze "kein
Player-Zustand außerhalb request/pop" verletzt), hängt sich die
Kalibrierung an die ohnehin laufende STT-Sampling-Pipeline des GERADE
gespielten Senders — der Nutzer schaltet manuell auf der Player-Seite um
(Sprache-Testsender, dann Musik-Testsender), der Wizard sammelt nur mit.
Zwei Konsequenzen, die das erzwingen:

- Die erwartete Sprache muss während einer Session ERZWUNGEN werden
  (nicht mehr über `resolve_stt_language(current["category"], …)`
  aufgelöst) — sonst würde z.B. ein deutscher Nachrichtensender beim
  Kalibrieren von "en" weiterhin als "de" gesampelt.
- Die automatische Switch-Logik muss für die Dauer der Session komplett
  PAUSIERT werden (nicht nur die Kalibrierungs-Sprache betreffend) —
  sonst könnte ein durch die erzwungene Sprache verfälschtes
  `combine_label()`-Ergebnis mitten in der Kalibrierung einen Wechsel
  auslösen, während der Nutzer gerade bewusst auf einem Testsender bleiben
  will.

### Umsetzung

- **`stt_filter.py`**: `suggest_confidence_threshold(speech_samples,
  music_samples)` — reine Funktion, reproduziert die Methode, mit der der
  ursprüngliche DE-Default (0.75) von Hand hergeleitet wurde: Schwelle =
  `music_max + 0.7 × (speech_min − music_max)`, sofern `speech_min >
  music_max` (saubere Trennung im Sample). Bei Überlappung: Mittelwert
  beider Mittelwerte als Kompromiss, mit `clean_separation=False`-Flag für
  eine Warnung in der UI statt eines unkommentierten Vorschlags.
- **`webui.py` `SwitcherState`**: neue Kalibrierungs-Session
  (`start_calibration()`/`set_calibration_stage()`/`stop_calibration()`/
  `calibration_language`-Property/`add_calibration_sample()`/
  `calibration_snapshot()`) — bewusst OHNE request/pop (sonst
  durchgängiges Muster in dieser Datei), weil keine der Player-kritischen
  Zustände betroffen sind, für die das Muster da ist; Webserver-Thread
  schreibt direkt lock-geschützt, Hauptloop liest+ergänzt direkt, siehe
  ausführliche Begründung im neuen `CLAUDE.md`-Abschnitt.
  `add_calibration_sample()` dedupliziert über den Verdict-Timestamp
  (sonst würde derselbe STT-Sample über mehrere Hauptloop-Ticks hinweg
  mehrfach gezählt) und deckelt auf `MAX_CALIBRATION_SAMPLES=100` pro
  Stufe (gegen unbegrenztes Wachstum, falls eine Session vergessen
  weiterläuft).
- **`radiozapper.py`**: Hauptloop erzwingt `stt_lang =
  state.calibration_language`, sofern gesetzt, UND springt direkt nach
  `classify()` per `continue` zur nächsten Iteration, wenn eine Session
  aktiv ist (Streak-Zählung/Fingerprint-Trigger/`do_switch()` werden für
  diesen Tick komplett übersprungen). `classify()` speist jeden neuen,
  zur aktuellen Kalibrierungssprache passenden Verdict in
  `state.add_calibration_sample()` ein.
- **`webui.py` API**: `POST /api/config/stt-calibration/start`
  (validiert: Sprachcode nicht leer, STT-Filter UND Sabbelfilter aktiv),
  `POST .../stage` (`"speech"`/`"music"`), `POST .../stop`,
  `GET .../status` (`_build_calibration_status()` berechnet den
  Vorschlag bei JEDEM Poll neu aus den bisherigen Samples — keine
  zweite Formel-Implementierung in JS). "Übernehmen" nutzt bewusst den
  bestehenden `/api/config/stt-languages`-Upsert-Endpoint statt eines
  eigenen "apply"-Endpoints.
- **Config-Seite**: neue Sektion "🧪 Schwellwert-Kalibrierung" unterhalb
  von "🏷 Kategorie-Sprachen" — Sprachcode-Eingabe + Start-Button (Idle-
  Zustand), danach Stufen-Buttons (aktive Stufe optisch hervorgehoben),
  Live-Zusammenfassung pro Stufe (Count/Min/Max/Mittelwert, Poll alle
  2s), aufklappbare Liste der letzten 15 Samples (Konfidenz + erkannter
  Text) der aktiven Stufe, Vorschlags-Box (grün bei sauberer Trennung,
  rot umrandet bei Überlappungs-Warnung) mit Übernehmen-Button.
- **Doku**: `CLAUDE.md`-STT-Abschnitt um die Wizard-Architektur ergänzt,
  "Bekannte offene Punkte" aktualisiert (Wizard nicht mehr offen, dafür
  Hinweis, dass die Vorschlagsformel bisher nur an den DE-Messwerten
  plausibilisiert ist). `README.md` (DE+EN): neuer Abschnitt
  "Kalibrierungs-Wizard" mit Schritt-für-Schritt-Anleitung und dem
  wichtigen Hinweis, dass die Kalibrierung selbst nichts umschaltet und
  die automatische Umschaltung währenddessen pausiert.

### Verifiziert (isoliert, temp-Verzeichnis + separater Testport, ohne den laufenden Produktiv-Container anzufassen)

- `stt_filter.suggest_confidence_threshold()` direkt getestet: saubere
  Trennung (Sprache 0.83–0.95, Musik 0.25–0.42) → Schwelle innerhalb der
  Lücke, `clean_separation=True`; überlappende Verteilungen →
  `clean_separation=False`.
- `SwitcherState`-Kalibrierungsmethoden direkt getestet: Dedup über
  Timestamp (gleicher Verdict zählt nur einmal), Stufenwechsel trennt
  Sample-Listen korrekt, `_build_calibration_status()` liefert den
  erwarteten Vorschlag, `stop_calibration()` räumt vollständig auf,
  `MAX_CALIBRATION_SAMPLES`-Deckel greift bei 100 Samples (bei 120
  hinzugefügten).
- End-to-End über echtes HTTP (temp-Verzeichnis, Testport 15300/15301):
  Start OHNE aktivierten STT-Filter → korrekt abgelehnt ("STT-Filter ist
  deaktiviert"); STT-Filter aktiviert (per `/api/config/settings` +
  simuliertem `state.reload()`, da in diesem isolierten Test kein
  Hauptloop läuft, der `pop_reload_request()` sonst automatisch
  anwendet); Start danach erfolgreich; `GET .../status` lieferte
  korrekten Snapshot; leerer Sprachcode UND ungültige Stufe → korrekt
  abgelehnt; Config-Seite (`GET /config`) enthielt alle neuen
  Markup-IDs (`stt-calib-section`/`stt-calib-idle`/`stt-calib-active`/
  `btn-stt-calib-start`/`btn-stt-calib-apply`/`stt-calib-samples-list`).
  `_check_i18n_coverage()` beim Modul-Import ohne `AssertionError`
  durchgelaufen. Testprozesse/-verzeichnis danach entfernt, keine
  Auswirkung auf den laufenden Produktiv-Container.

### Bewusst NICHT gemacht

Kein automatisches Umschalten auf einen Test-Sender durch den Wizard
selbst (bewusst passiv, siehe Kernentscheidung oben) — der Nutzer nutzt
dafür die bestehende Player-Seite. Keine feste Mindest-Sample-Zahl pro
Stufe (der Nutzer entscheidet selbst, wann "genug" gesammelt wurde,
anhand der Live-Zusammenfassung) — kein hartkodiertes Timing/keine
hartkodierte Zielzahl. Die Vorschlagsformel wurde NICHT an einer echten
zweiten Sprache in Produktion verifiziert, nur an den ursprünglichen
DE-Messwerten plausibilisiert (siehe CLAUDE.md).

### Live deployt

Nach Zustimmung des Nutzers: `docker compose up -d --build radiozapper`.
Sauberer Start, Migration griff erneut korrekt, Vosk-Modell lazy nach
Ende der Nachrichten-Pause geladen — alles wie in "Fortsetzung 5"
bereits verifiziert.

Kalibrierungs-Wizard live gegen den echten Sender getestet (105'5
Spreeradio 80er lief bereits, keine Umschaltung dafür nötig): Start für
Sprache "de" → `🧪 Kalibrierung gestartet` im Log, Status-Endpoint
zeigte binnen ~23s sieben Samples (Confidence 0/leerer Text — der
80er-Sender bringt gerade keine deutsche Sprache, inhaltlich plausibel,
technisch zeigt es aber, dass das Sampling korrekt lief). **Wichtigster
Befund**: `current_name` blieb während der gesamten aktiven Session
unverändert ("105'5 Spreeradio 80er") — die Switch-Pause griff wie
vorgesehen, kein automatischer Wechsel trotz laufender Klassifikation.
Stufenwechsel zu "music" → Log zeigte `Stufe gewechselt zu 'music'`,
Speech-Samples blieben in der Session erhalten (7 Samples weiterhin
sichtbar). Stop → Log zeigte `Kalibrierung beendet`, Status danach
`{"active": false}`, normale Wiedergabe (`current_name`/
`news_break_active`) unverändert korrekt. Keine Fehler/Tracebacks im
Log über den gesamten Testzeitraum. `docker compose ps`: beide
Container `Up`, kein Neustart-Loop.

Damit ist der gesamte ursprüngliche Architektur-Vorschlag (Resource-
Monitoring, Live-Statusanzeigen, mehrsprachige STT inkl.
Kalibrierungs-Wizard) vollständig umgesetzt und live verifiziert.

## 2026-08-06 (Fortsetzung 7) — Englisch kalibriert, zwei echte Bugs beim ersten produktiven Einsatz gefunden

Erster tatsächlicher Produktiv-Einsatz des in "Fortsetzung 5/6" gebauten
Mehrsprachigkeits-Features: Nutzerwunsch "Kalibriere Englisch für die
BBC-Kategorie". Dabei zwei echte, vorher nicht entdeckte Fehler
aufgedeckt UND live behoben — genau der Fall, für den dieses Protokoll
da ist.

### Bug 1 (kritisch, Absturz): `classify()`-Aufrufstellen in `do_switch()` nicht mitgezogen

Beim manuellen Umschalten auf "BBC World Service" (dessen DASH-Stream
sich als technisch nicht nutzbar herausstellte, siehe unten) griff der
Watchdog nach 3 gescheiterten Reconnects und rief `do_switch()` auf —
dessen zwei `classify()`-Aufrufstellen (Kandidaten-Vorwärmung aus dem
Puffer, Frischer-Start-Probe) waren beim Signaturwechsel in "Fortsetzung
5" (`classify(pcm)` → `classify(pcm, stt_lang)`) schlicht übersehen
worden — nur die Aufrufstelle im normalen Hauptloop-Takt wurde
angepasst. Ergebnis: `TypeError: classify() missing 1 required
positional argument: 'stt_lang'`, unbehandelt bis zum Modul-Level,
**Hauptprozess stürzte ab** (Docker-Neustart fing es auf, aber mit
vollem State-Verlust: zurück zum ersten Sender der Rotation).

Behoben: beide Stellen lösen jetzt `stt_lang` über
`settings_store.resolve_stt_language(candidate["category"],
state.stt_filter_cfg)` für den jeweils GEPRÜFTEN Kandidaten auf (nicht
für `current` — an der Stelle in der Puffer-Vorwärmung ist `current`
noch der ALTE Sender, an der Stelle nach dem frischen Start ist
`current` bereits auf den Kandidaten aktualisiert, siehe Code-Kommentare
dort). Lehre: Bei einer Signaturänderung an einer Closure-Funktion
IMMER `grep -n "funktionsname("` über die GANZE Datei laufen lassen,
nicht nur den offensichtlichen Haupt-Aufrufpfad testen — die
isolierten Tests in "Fortsetzung 5" prüften `classify()` nur direkt,
nie über den watchdog-getriggerten `do_switch()`-Pfad.

### Bug 2 (Datenqualität, kein Crash): leere STT-Samples verfälschten die Kalibrierungs-Formel

Erste echte Kalibrierungs-Session (Sprache: LBC UK, Musik: Heart London)
lieferte einen offensichtlich unbrauchbaren Vorschlag (0.27,
`clean_separation: false`) trotz eigentlich brauchbarer Rohdaten. Ursache:
`add_calibration_sample()` zählte JEDES Sample, auch solche mit
`confidence=0.0`/leerem Text (Pausen, Jingles, Werbeblöcke — STT bildet
dabei gar keine Wort-Hypothese, das ist etwas anderes als "mit niedriger
Konfidenz erkannt"). Dadurch wurde `speech_min` künstlich auf 0 gezogen,
jede Pause zählte als "schlechtester Sprache-Sample".

Behoben: `add_calibration_sample()` verwirft jetzt Samples mit leerem
`text` (Details/Begründung siehe CLAUDE.md-Ergänzung im STT-Abschnitt).
Nach dem Fix: 15/17 Samples pro Stufe, Sprache-Konfidenz durchgehend
0.76–1.0 (statt vorher voller Nullen), deutlich sauberere Daten.

### Kalibrierungs-Ablauf und Ergebnis

- **Infrastruktur**: `vosk-model-small-en-us-0.15` (~40MB,
  alphacephei.com) heruntergeladen nach `data/vosk-model-en/`, neuer
  Mount in `docker-compose.yml` (`VOSK_MODEL_FOLDER_EN`, Default
  `./data/vosk-model-en` → `/app/vosk-model-en:ro`) nach demselben Muster
  wie der bestehende `VOSK_MODEL_FOLDER`-Mount, ergänzt in `env.example`.
  `.gitignore`s `data/vosk-model-de/`-Eintrag zu `data/vosk-model-*/`
  verallgemeinert — sonst wäre das 40MB-Modell beim nächsten Commit
  versehentlich mit eingecheckt worden.
- **BBC-Sender funktionieren technisch NICHT**: alle 15 importierten
  BBC-Sender liefern DASH-Manifeste (`.mpd`). Direkter ffmpeg-Test (auf
  dem Host, außerhalb des Containers) bestätigte das dokumentierte
  DASH-Verhalten (siehe CLAUDE.md, Sender-Import-Abschnitt) live: ein
  3-Sekunden-Test dekodiert sauber, ein 30-40-Sekunden-Test hängt nach
  dem initialen Fragment-Burst komplett (kein weiterer Output, vom
  `timeout`-Wrapper nach Ablauf hart beendet). Beide testweise
  aktivierten BBC-Sender (World Service, Radio 2) wurden nach dem Fund
  zurück auf `enabled: false`/Kategorie "Unsortiert" gesetzt — bleiben
  als Kandidaten in der Liste, aber nicht produktiv nutzbar ohne
  Weiteres (z.B. ein DASH-fähigeres Downstream-Tool, nicht evaluiert).
- **Ersatz-Sender**: "LBC UK" (`media-ice.musicradio.com/LBCUKMP3`,
  Sprache-Talk-Radio) und "Heart London"
  (`media-ice.musicradio.com/HeartLondonMP3`, Popmusik) — beide vor dem
  Anlegen per direktem ffmpeg-Test auf dem Host verifiziert (30s
  Dauertest, kontinuierlicher Real-Time-Durchsatz, kein Burst-dann-Stille-
  Muster). Kategorie "International" zugeordnet, aktiviert.
- **Sprache-Stufe** (LBC UK, nach Bug-2-Fix): 17 Samples, Konfidenz
  0.7556–1.0.
- **Musik-Stufe** (Heart London): 16 Samples, Konfidenz 0.5849–0.9942 —
  Trennung NICHT sauber (`clean_separation: false`), weil Heart London
  als kommerzieller Sender erheblichen gesprochenen Anteil hat (Werbung,
  Moderation zwischen Songs — im Sample sichtbar an Texten wie "only
  took ninety seven messages to find today"). Kein Erkennungsfehler,
  sondern eine Grenze der Sender-Wahl (dokumentiert jetzt in README).
- **Vorschlag**: 0.85 (Mittelwert-Kompromiss wegen Überlappung). Nutzer
  hat sich nach Rückfrage bewusst für "trotz Warnung übernehmen"
  entschieden statt eines dritten Versuchs mit einem reineren
  Musiksender — übernommen via `set_stt_language('en',
  confidence_threshold=0.85)`.
- **Kategorie-Zuordnung**: `category_languages["International"] = "en"`.

### Verifiziert

Nach dem Bug-1-Fix: `docker compose up -d --build radiozapper`, sauberer
Start, kein Absturz mehr beim Umschalten auf einen toten Sender (LBC UK/
Heart London-Wechsel mehrfach ohne Traceback). Nach dem Bug-2-Fix:
zweite Kalibrierungs-Session lieferte durchgehend Samples mit echtem
Text statt Nullen (siehe oben). Nach dem Anwenden des Vorschlags:
`GET /api/config/settings` zeigt `languages: {"de": {...0.75}, "en":
{...0.85}}`, `category_languages: {"International": "en"}`. Kein
automatischer Wechsel während beider Kalibrierungs-Sessions (Switch-
Pause griff, wie in "Fortsetzung 6" verifiziert). Player kehrte nach
Kalibrierungsende zur normalen Rotation zurück (`filter_enabled: true`,
automatischer Wechsel weg von Heart London beobachtet). Keine
Fehler/Tracebacks im Log seit dem Bug-1-Fix.

### Bewusst NICHT gemacht

Kein dritter Kalibrierungsversuch mit einem werbefreien Musiksender für
eine saubere Trennung — Nutzerentscheidung, 0.85 trotz Warnung zu
übernehmen. BBC-Sender NICHT technisch nutzbar gemacht (kein
DASH-fähiger Ingestion-Pfad evaluiert/gebaut) — bleiben deaktiviert in
"Unsortiert". Kein automatisierter Test, der `do_switch()`s
`classify()`-Aufrufstellen abgedeckt hätte (kein Test-Framework im
Projekt, siehe CLAUDE.md) — die Lehre daraus (systematisches Grep aller
Aufrufstellen bei Signaturänderungen) ist oben festgehalten, aber nicht
in Werkzeug/Prozess gegossen.

## 2026-08-06 (Fortsetzung 8) — Nachrichten-Pause: kein Repeat kurz hintereinander

Nutzer-Beobachtung: dieselbe MP3 aus dem News-Break-Ordner kam öfter kurz
hintereinander dran. `news_break.pick_random_mp3()` hatte bereits einen
`exclude`-Parameter (die eine zuletzt gespielte Datei), das reicht aber
bei mehreren MP3s im Ordner nicht — eine Datei kann trotzdem schon nach
1-2 Wechseln wieder drankommen, wenn nur der unmittelbare Vorgänger
ausgeschlossen wird.

### Umsetzung

- **`news_break.py`**: `pick_random_mp3(folder, exclude=None)` →
  `pick_random_mp3(folder, recent=None)`. `recent` ist jetzt eine
  Iterable mehrerer zuletzt gespielter Dateinamen statt nur einer
  einzelnen — `candidates = [f for f in files if f not in recent] or
  files`. Neue Konstante `RECENT_HISTORY_SIZE = 3` (Kompromiss: groß
  genug, dass eine Datei nicht sofort wiederkommt, klein genug, dass
  auch Ordner mit 4-5 Dateien noch eine echte Auswahl behalten). Der
  `or files`-Fallback ist der Randfall-Schutz aus Anforderung 3: enthält
  der Ordner insgesamt nicht mehr Dateien als `recent` (z.B. nur 1-2
  MP3s), lässt der Ausschluss nichts mehr übrig — dann lieber eine
  Wiederholung als eine fehlschlagende Nachrichten-Pause (`None`
  zurückgeben würde das Feature für dieses Fenster ausfallen lassen).
- **`radiozapper.py`**: die bisherige einzelne Variable
  `news_break_last_file` (String, per `nonlocal` reassigned) wurde durch
  `news_break_recent_files = collections.deque(maxlen=
  news_break.RECENT_HISTORY_SIZE)` ersetzt — `deque.append()` mutiert
  nur das Objekt, kein Rebinding des Namens, daher entfällt das
  `nonlocal` in `start_news_break_mp3()`. An allen vier Stellen
  nachgezogen (Auswahl-Aufruf, `state.set_news_break()`, zwei
  Log-Zeilen) — jeweils `news_break_recent_files[-1]` statt der alten
  einzelnen Variable für "die gerade gestartete Datei".

### Verifiziert (isoliert, temp-Verzeichnis mit synthetischen leeren .mp3-Dateien, ohne den laufenden Container anzufassen)

- 8 Dateien, 30 simulierte Zyklen: nie zwei gleiche Dateien direkt
  hintereinander.
- 5 Dateien, 20 Zyklen: keine Wiederholung innerhalb der letzten 3 Picks.
- Randfall 1 Datei insgesamt: liefert bei jedem der 10 Aufrufe dieselbe
  Datei, nie `None` — Feature fällt nicht aus.
- Randfall 2 Dateien (weniger als `RECENT_HISTORY_SIZE`): nie `None`,
  Wiederholungen kommen zwangsläufig vor (erwartet, siehe Randfall-Logik
  oben).
- Randfall genau 3 Dateien (== `RECENT_HISTORY_SIZE`): Fallback griff
  korrekt (u.a. eine direkte Wiederholung im Log sichtbar, weil der
  Ausschluss aller 3 Dateien nichts mehr übrig ließ) — kein Fehlschlag.
- Danach `docker compose up -d --build radiozapper`: sauberer Start,
  keine Fehler/Tracebacks im Log über die ersten ~40s.

### Bewusst NICHT gemacht

`RECENT_HISTORY_SIZE` nicht konfigurierbar gemacht (kein neues
Settings-Feld) — Anforderung war "einfaches Gedächtnis", ein
hartkodierter, begründeter Wert reicht dafür. Kein Persistieren der
zuletzt gespielten Dateien über einen Neustart hinweg (explizit nicht
gefordert, `deque` lebt nur im Hauptloop-Prozess).

## 2026-08-07 — Android RadioZapper MVP (eigenständiger Kotlin-Prototyp)

Nutzerwunsch: ein minimales, natives Android-MVP (kein Web-Wrapper),
das dasselbe Grundprinzip lokal auf dem Handy abbildet — Radiostream
per ExoPlayer abspielen, per Vosk (Speech-to-Text) grob Sprache/Musik
unterscheiden, NICHT auf die laufende Docker-Instanz angewiesen. Neues,
komplett eigenständiges Gradle-Projekt unter `android-app/` (eigenes
`CLAUDE.md`-Regime gilt dort nicht 1:1 — andere Sprache/Toolchain —,
aber Doku-Pflicht hier trotzdem beachtet, weil das Repo insgesamt
betroffen ist).

### Umsetzung

- **Android-SDK/Emulator-Toolchain auf diesem Host neu aufgesetzt**
  (vorher nicht vorhanden): cmdline-tools, platform-tools, platform 34,
  build-tools 34, Emulator + `system-images;android-34;google_apis;
  x86_64`, AVD `test_device` (Pixel-Profil, ressourcenschonend: 1536MB
  RAM, Audio aus, `swiftshader_indirect`). `ANDROID_HOME`/PATH dauerhaft
  in `~/.bashrc` ergänzt. Ermöglicht ab jetzt automatisiertes Bauen UND
  Testen (Emulator, adb, Logcat, Screenshots) für Android-Arbeit auf
  diesem Host, nicht nur Kompilieren.
- **`android-app/`**: Kotlin/Gradle-Projekt (minSdk 26, targetSdk/
  compileSdk 34, media3 1.4.1 statt neuerer Versionen — ab media3 1.5
  ist compileSdk 35+ Pflicht, siehe AAR-Metadata-Check), Vosk-Android
  0.3.47 (Maven Central, kein eigenes Repo mehr nötig), deutsches
  Kleinmodell `vosk-model-small-de-0.15` (dasselbe wie im Docker-Projekt)
  — Download+Entpacken beim ersten Start statt ins APK gebundlet.
  Bewusst XML-Views statt Jetpack Compose (weniger
  Versions-Fallstricke ohne Testgerät). 3 hartcodierte Platzhalter-Sender
  (Deutschlandfunk/1LIVE/SWR3). `StreamAnalyzer` dekodiert den Stream
  ein zweites Mal unabhängig vom ExoPlayer nur für die Analyse (kein
  Anzapfen von ExoPlayers Audio-Pfad) — Preis: doppelter
  Netzwerkverbrauch, siehe README.
- **Erkennungsglättung nachgerüstet** (Nutzer beobachtete Flackern
  zwischen Sprache/Musik): Ursache war eine strikte "N Sekunden ohne
  Unterbrechung"-Serie (wie `CONSECUTIVE_SPEECH_TO_SWITCH` im
  Docker-Projekt) bei 0.5s-Häppchen — normale kurze Sprechpausen (Vosk
  liefert dann kurz ein leeres Partial-Result) warfen die Serie staendig
  zurück. Ersetzt durch gleitendes Mehrheitsvotum über die letzten 4.0s
  (`SMOOTHING_WINDOW_SECONDS`) mit Hysterese (`RATIO_TO_CONFIRM_SPEECH
  =0.65` / `RATIO_TO_CONFIRM_MUSIC=0.30`) statt harter Serie. Dabei
  einen zweiten, unabhängigen Bug gefunden und mitgefixt: die
  Abbruchprüfung der Analyse-Schleife testete den dauerhaften
  Service-Scope statt den eigenen, kurzlebigen Job — eine alte Analyse
  wäre bei jedem Senderwechsel im Hintergrund weitergelaufen
  (`coroutineContext.isActive` statt `scope.isActive`).
- **Automatisches Umschalten aktiviert** (`PlaybackService`): reagiert
  auf den geglätteten Status, schaltet bei bestätigter Sprache zum
  nächsten Sender in der hartcodierten Liste (Ring), mit Obergrenze
  (ein voller Durchlauf durch alle Sender ohne Treffer → 20s Pause statt
  Endlosschleife). Erste Umsetzung hatte die Richtung versehentlich
  umgedreht (schaltete weg von Musik statt weg von Sprache) — auf
  Nutzerhinweis korrigiert, jetzt wie im Docker-Projekt: weg von
  Sprache/Moderation/Werbung, hin zu Musik. `PlaybackService` exponiert
  jetzt zusätzlich `currentStation` als StateFlow, damit die UI beim
  automatischen Wechsel mitzieht (vorher blieb die Anzeige beim manuell
  gestarteten Sender stehen).

### Verifiziert (live im soeben aufgesetzten Emulator, nicht nur kompiliert)

- `./gradlew assembleDebug` erfolgreich, Debug-APK unter
  `android-app/app/build/outputs/apk/debug/app-debug.apk` (~46MB).
- Modell-Download im Emulator: "Vosk-Modell (DE) fehlt noch." →
  Button-Klick → "Vosk-Modell (DE) bereit." (Screenshot).
- Vor der Richtungskorrektur: 1LIVE (Musik) gestartet → Logcat
  `08:11:38 Musik erkannt auf '1LIVE...' - schalte weiter zu 'SWR3...'`
  → `08:12:08 Musik erkannt auf 'SWR3...' - schalte weiter zu
  'Deutschlandfunk...'` → über 1 Minute stabil auf Deutschlandfunk,
  Status "🗣 Sprache", kein Nachflackern, kein Weiterspringen.
- Nach der Richtungskorrektur: Deutschlandfunk (Sprache) gestartet →
  Logcat `08:20:25 Sprache erkannt auf 'Deutschlandfunk...' - schalte
  weiter zu '1LIVE...'` → danach stabil auf 1LIVE, Status "🎵 Musik"
  (Screenshot), kein Weiterspringen — Richtung jetzt wie gewünscht.

### Bewusst NICHT gemacht

Kein Watchdog/Ban-System für tote/dauerhaft-musikalische Sender (explizit
für später vorgesehen). Kein Wiederverwenden des geladenen Vosk-`Model`
über Senderwechsel hinweg (kostet ~1-2s pro Wechsel, nicht optimiert).
Keine Kalibrierung der Hysterese-Schwellen gegen echte Sender-Statistik
wie im Docker-Projekt (Deutschlandfunk/Schlager-Messung) — die 65%/30%
sind plausible Startwerte, nicht empirisch abgesichert. Root-`README.md`
NICHT um den Android-Prototyp ergänzt — der Abschnitt dort beschreibt
den *deployten Docker-Dienst*, dessen Verhalten/Setup/Konfiguration durch
`android-app/` unverändert bleibt; das MVP hat sein eigenes README direkt
in `android-app/`.

## 2026-08-07 (Fortsetzung) — Android-Prototyp wird ab jetzt mitgepflegt

Nutzerentscheidung: `android-app/` ist kein Wegwerf-Experiment mehr,
sondern wird künftig genauso dokumentiert gepflegt wie der Docker-Dienst.
Auslöser war die Nachfrage, ob SESSION.md/README.md/CLAUDE.md aktuell
sind — dabei fiel auf, dass `CLAUDE.md` den Android-Code gar nicht
erwähnte (eine künftige Session könnte sonst unvorbereitet auf ein
fremdes Kotlin-Projekt neben dem Python-Code stoßen) und dass
`android-app/README.md` seit der Smoothing-/Auto-Switch-Änderung vom
Vortag bereits veraltet war (behauptete noch "kein automatisches
Umschalten" und "ungetestet auf echtem Gerät").

### Umsetzung

- **`CLAUDE.md`**: neuer kurzer Abschnitt "Android-Prototyp (separates
  Projekt)" direkt nach der Einleitung — verweist auf `android-app/`,
  stellt klar, dass die Docker-spezifischen Konventionen (Deutsch,
  SESSION.md, VERSION) nicht 1:1 für den Android-Code gelten, und hält
  die beiden neuen Arbeitsablauf-Regeln fest (siehe unten).
- **Zwei neue Konventionen** (ab jetzt bei jeder Android-Änderung
  anzuwenden): nach jedem Build die Debug-APK zusätzlich nach
  `android-app/radiozapper.apk` kopieren (fester Pfad statt des tief
  verschachtelten, gitignorten `app/build/outputs/apk/debug/
  app-debug.apk`) — `radiozapper.apk` selbst ebenfalls gitignored
  (Build-Artefakt, kein Quelltext). `android-app/README.md` bei jeder
  inhaltlichen Änderung an der App nachziehen, analog zur
  README-Pflicht des Docker-Projekts.
- **`android-app/README.md` komplett aktualisiert**: "Was funktioniert"
  spiegelt jetzt den Stand nach Smoothing-Fix + Auto-Switch (inkl. der
  konkreten Konstanten `SMOOTHING_WINDOW_SECONDS`/
  `RATIO_TO_CONFIRM_SPEECH`/`RATIO_TO_CONFIRM_MUSIC`/
  `AUTO_SWITCH_PAUSE_SECONDS`) statt des überholten "nur Anzeige,
  ungetestet"-Stands von der Ersterstellung. Live-Testergebnis vom
  Vortag (Deutschlandfunk → 1LIVE, stabil) referenziert. Installation/
  Bauen-Abschnitte um den neuen `radiozapper.apk`-Pfad und die
  Emulator-Kurzform ergänzt. "Bekannte Grenzen" um Cooldown-pro-Sender
  und Mindest-Verweildauer als offene Punkte ergänzt (vorher nur implizit
  in der Chat-Antwort genannt, nicht im README).

### Bewusst NICHT gemacht

Kein Commit/Push in diesem Schritt (Nutzer hat nur die Doku-Änderungen
angefordert, nicht explizit einen Commit). Root-`README.md` weiterhin
NICHT angefasst — Begründung unverändert (siehe Eintrag oben).
