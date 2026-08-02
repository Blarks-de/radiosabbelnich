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
