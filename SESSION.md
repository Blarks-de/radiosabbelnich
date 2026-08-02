# RadioZapper — Session-Log

Laufendes Protokoll der Arbeit an diesem Projekt (chronologisch, neueste
Einträge unten). Für den aktuellen Architektur-/Status-Überblick siehe
weiterhin `HANDOVER.md` — hier steht das *Wie und Warum* der einzelnen
Schritte.

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
