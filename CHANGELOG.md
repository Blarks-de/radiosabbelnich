# Changelog

Alle nennenswerten Änderungen an RadioSabbelNich, neueste zuerst. Deckt
sowohl den Docker-Dienst als auch die native Android-App (`android-app/`,
eigenständiges Projekt, siehe `CLAUDE.md`) ab — Android-Einträge sind mit
**Android:** markiert. Ausführliche Begründungen, Design-Entscheidungen
und Messwerte hinter den einzelnen Punkten stehen in `SESSION.md`
(Docker) bzw. `android-app/SESSION.md` (Android); dies hier ist die
verdichtete Übersicht.

**Namenshistorie**: das Projekt hieß ursprünglich *RadioZapper*, wurde am
2026-08-08 zu *KeinSabbelRadio* und am 2026-08-09 zu *RadioSabbelNich*
umbenannt. Ältere Commit-Nachrichten/Dateinamen (z.B. `radio_switch`,
`check-radiozapper.sh`) spiegeln den jeweils zur Zeit gültigen Namen
wider — hier einheitlich mit dem aktuellen Namen beschrieben, sofern
nicht der Umbenennungsvorgang selbst der Inhalt eines Eintrags ist.

## 2026-08-13

- `/musik` aufgeräumt: nur noch ein Play-Knopf (der native Browser-
  Play-Knopf kam vorher dem großen Play/Stop-Button in die Quere), die
  Funktion heißt jetzt schlicht "Player" statt "Musiksammlung", kein
  Banner-Bild mehr auf der Seite, angezeigter Musik-Ordner ist jetzt der
  echte Host-Pfad aus `.env` statt des Container-Pfads, neuer
  "Pfad ändern"-Knopf statt Text-Link.
- Musiksammlung-Seite (`/musik`) bekommt einen eigenen eingebetteten
  Audio-Player — man muss zum Zuhören nicht mehr zur Player-Seite
  zurückspringen.

## 2026-08-12

- Musik-Library: Duplikat-Erkennung (`music_query.find_duplicates()`,
  normalisiertes Artist+Titel-Metadaten-Match) über neuen
  `GET /api/library/duplicates`-Endpoint, bewusst ohne UI-Anschluss.
- Musik-Library: Format-Unterstützung über MP3 hinaus auf FLAC, OGG,
  M4A, AAC, WAV und APE erweitert (Scan + Playback).
- Web-Interface: Basissprache auf Englisch umgestellt, Deutsch als
  externes Sprachpaket (`language/*.lng`, Windows-Sprachpaket-Analogie)
  ausgelagert.
- **Android:** UI-Basissprache ebenfalls auf Englisch umgestellt
  (`values/` = Englisch, `values-de/` = Deutsch, nativer Android-
  Ressourcenmechanismus); Update-Server-Pfad auf
  `blarks.de/radio/update` verschoben.
- Die drei Betriebs-Skripte auf eines reduziert: `check-radiosabbelnich.sh`
  und `run_radiosabbelnich.sh` als neue Subcommands `check`/`start` in
  `radiosabbelnich.sh` integriert, beide alten Dateien gelöscht.
- Musik-Library Phase 3: BPM-Schätzung (`music_bpm.py`, aubio) im
  Scan-Pass ergänzt, `schnell`/`langsam`-Buttons auf `/musik` aktiviert.
- Bugfix Nachrichten-Pause: eine laufende MP3 wird nicht mehr hart
  abgebrochen, wenn `window_minutes` mittendrin abläuft — sie spielt
  jetzt immer bis zum Ende, der Rückweg zum Sender passiert danach.
- Musik-Library Phase 2: Query-Layer (`music_query.py`, Artist-/
  Genre-Teilstring-Filter) an die `rock`/`klassik`/Queen/Pavarotti-
  Buttons angebunden; "Jetzt läuft" zeigt bei Query-Wiedergabe
  Artist – Titel statt nur den Dateinamen.
- `radiosabbelnich.sh status`: warnt jetzt rot bei ausgeloggtem/
  gestopptem Tailscale (für `*.ts.net`-Hostnamen) oder fehlendem
  Internet/DNS (Ping gegen `hamburg.de`), zeigt den konfigurierten
  `ICECAST_HOSTNAME` und den MP3-Ordner-Status der Nachrichten-Pause.
- Alle `.py`-Module von Repo-Root nach `python/` verschoben (reines
  Aufräumen); Web-Interface-Button-Labels präzisiert ("VLC" → "VLC
  Stream", "Handy" → "Handy Fernsteuerung"); QR-Code zum Download der
  Android-APK im README ergänzt (fester Alias `radiosabbelnich-latest.apk`
  auf blarks.de, überlebt künftige Android-Builds).
- Musik-Library Phase 1: rekursiver ID3-Scan (`music_scan.py`, mutagen)
  der Musiksammlung in eine eigene SQLite-DB (`music_library.db`,
  inkrementell per mtime/Größe), Kategorie-/Favoriten-Buttons auf
  `/musik` von einer Reihe in zwei benannte Gruppen aufgeteilt.

## 2026-08-11

- Musiksammlung-Modus, Grundgerüst: Umschalter Radio ⇄ Musiksammlung
  (STT/VAD im Musik-Modus komplett aus), minimaler Player (Play/Stop/
  Zurück/Nächster über einen konfigurierbaren, nicht-rekursiven Ordner),
  neue Seite `/musik`, gemeinsame Breadcrumb-Ordnerauswahl
  (`folder_browse.py`) für Nachrichten-Pause- und Musiksammlung-Pfad.
  Dazu `radiosabbelnich.sh` als erster Betriebs-Wrapper (`start`/`stop`/
  `restart`/`status`).
- README-Serverpfade aktualisiert.
- Musik-Library-Feature als Idee in die Roadmap aufgenommen (reine
  Doku, noch ohne Code).

## 2026-08-09

- Preflight-Checks lesen `NEWS_MP3_FOLDER` jetzt über `docker compose
  config` statt `.env`/Shell selbst zu parsen — behebt einen Bug, bei
  dem ein wörtlicher Backslash im Pfad (z.B. `80\'s`) von der Shell
  "korrigiert" wurde, aber nicht von Docker Compose, wodurch der Check
  fälschlich grün war und der eigentliche Mount trotzdem scheiterte.
- **Android:** Brücken-Build- und Landingpage-Update dokumentiert; neuer
  Server-Update-Pfad (blarks.de) dokumentiert.
- Projekt umbenannt: *KeinSabbelRadio* → *RadioSabbelNich*.

## 2026-08-08

- Namensänderung dokumentiert (README-Hinweis, VERSION-Bump).
- **Android:** Update-Server von einem separaten, nur per Tailscale
  erreichbaren Dienst auf den ganz normalen, öffentlich erreichbaren
  Webserver von blarks.de umgezogen (bewusste Ausnahme von "kein
  öffentlicher Betrieb" — verteilt nur eine App-Binary ohne
  Nutzerdaten, siehe `android-app/CLAUDE.md`) + Bugfixes.
- Projekt umbenannt: *RadioZapper* → *KeinSabbelRadio* (neues Banner-
  Bild passend zum neuen Namen).
- **Android:** Feature-Parität mit dem Docker-Dienst als fertig
  dokumentiert (README-Abschnitt ergänzt).
- **Android:** Review-Befunde aus Phase 8 (Code-Review) umgesetzt.
- **Android:** Mehrsprachiges STT samt geführtem Kalibrierungs-Wizard
  (Phase 7, in zwei Schritten: Grundgerüst, dann Wizard).
- **Android:** Audio-Fingerprinting gegen Jingles/Werbung (Phase 4).
- **Android:** Nachrichten-Pause / News-Break (Phase 6).
- **Android:** Prebuffering für lückenlosere Senderwechsel (Phase 3).
- **Android:** Optik-Angleichung (Phase 5) + Kodi-/M3U-Sender-Import.

## 2026-08-07

- **Android:** Watchdog für tote/nicht erreichbare Sender ergänzt.
- **Android:** 8-Phasen-Fahrplan für Feature-Parität mit dem
  Docker-Dienst aufgestellt.
- **Android:** feste Senderliste durch persistente Senderverwaltung
  ersetzt.
- **Android:** Cooldown pro Sender nach einem Wechsel + konfigurierbarer
  OTA-Update-Mechanismus.
- **Android:** Build-Zeitstempel in der App-UI angezeigt.
- **Android:** komplett neues, natives Kotlin/Android-Prototyp-Projekt
  gestartet (`android-app/`, media3 + Vosk-Android) — bildet dasselbe
  Grundprinzip (mehrere Sender, Sprache/Musik-Erkennung, Auto-Switch)
  lokal auf dem Handy nach; MVP mit geglätteter Sprache/Musik-Erkennung
  und Auto-Switch, danach als eigenständiges Projekt neben dem
  Docker-Dienst weitergepflegt (siehe `CLAUDE.md`).

## 2026-08-06

- Nachrichten-Pause: wiederholt zuletzt gespielte MP3s vermeiden statt
  rein zufällig neu zu wählen.
- Zwei Bugs aus der Englisch-Kalibrierung behoben, zusätzlicher
  englischer Vosk-Modell-Mount.
- Mehrsprachiges STT: Grundgerüst (Kategorie → Sprache statt einer
  einzigen globalen Sprache) und geführter Kalibrierungs-Wizard zur
  Ermittlung der Konfidenz-Schwelle pro Sprache.
- Live-Statusanzeigen (Bullshitometer, STT-Balken, Fingerprint-Chip)
  und Versionsanzeige im Web-Interface.
- Ressourcen-Verbrauch (RAM/CPU/DB-Größe) auf der Config-Seite.
- Playout-Delay: echte Vorausschau für die Sprache-Erkennung (Audio
  wird verzögert ausgegeben, damit die Klassifikation VOR der Ausgabe
  passiert, nicht danach) — vorgewärmte Sender wechseln dadurch ohne
  hörbaren Ruck.

## 2026-08-05

- Repo aufgeräumt: Nicht-`.py`-Dateien nach `pics/`, `web/`, `data/`
  sortiert statt alles am Root.
- Zweisprachiges Web-Interface (Deutsch/Englisch), "Unsortiert"-
  Kategorie auf der Config-Seite einklappbar.
- Nachrichten-Pause: lädt innerhalb eines Zeitfensters so lange weitere
  MP3s nach, bis `window_minutes` abgelaufen ist, statt nach der ersten
  Datei sofort zurückzuschalten.

## 2026-08-04

- `install_radiozapper.sh` zu `check-radiozapper.sh` umbenannt, volle
  Preflight-Diagnose ergänzt (Vorläufer der heutigen
  `radiosabbelnich.sh check`).
- PWA-Unterstützung: Web-Interface als installierbare App
  (Zurück/Weiter-Steuerung ergänzt).

## 2026-08-03

- QR-Code für die Stream-Adresse, STT-Sprachfilter (erste Version),
  TLS-Unterstützung fürs Web-Interface, diverse UI-Verbesserungen.
- Nachrichten-Pause-Feature: zur vollen/halben Stunde eine lokale MP3
  statt eines Senders.
- `CLAUDE.md` mit Architektur-Notizen und Konventionen angelegt.
- Sender-Import: neue Sender werden deaktiviert übernommen, Prüfung auf
  dauerhaften (nicht nur anfänglichen) Audiofluss.
- Watchdog gegen tote Sender, Logging überarbeitet.

## 2026-08-02

- "Alle deaktivieren"-Knopf pro Sender-Kategorie.
- Sender-Import aus M3U-Playlist (Kodinerds-Kodi-Radioliste), Knopf zum
  Leeren der Fingerprint-Clip-DB.
- Fingerprint-Algorithmus überarbeitet: echte 2D-Landmarken
  (Constellation-Map) statt des schlicht lautesten Frequenz-Bins pro
  Frame — deutlich weniger Fehltreffer auf echtem Broadcast-Radio.
- Puffer-Einstellungen konfigurierbar, "Zapping-Fehler" schaltet direkt
  zurück, README-Warnung (privater Betrieb, Urheberrecht).
- Vorausschauendes Puffern der nächsten 5 Sender für flüssigere
  Wechsel.
- "Sabbelfilter deaktivieren"-Knopf: automatische Erkennung komplett
  pausierbar.
- "Zapping-Fehler"/"Gesabbel"-Knöpfe, Hero-Bild, README ersetzt die
  bisherige HANDOVER-Datei.
- Fingerprint-Fehlalarm behoben: "Zapping-Fehler"-Knopf zum Löschen
  eines falsch gelernten Clips, Audio-Mitschnitt für künftige Treffer.
- Now-Playing-Fallback über Sender-eigene Webseiten (z.B. R.SH), wenn
  keine ICY-Metadaten kommen.
- Umschalt-Latenz reduziert, Now-Playing-Anzeige über ICY-Metadaten.
- Config-Seite für Sender-Verwaltung (aktivieren/deaktivieren/CRUD,
  Kategorien).
- Player ins Web-Interface eingebettet (alles auf einer Seite).
- Silero-VAD-Ladefehler behoben, Umschalt-Zuverlässigkeit verbessert.
- Icecast-Konfiguration gefixt (Location/Admin-Kontakt, Neustart-
  Absturzschleife im Basis-Image behoben).
- Initial Commit: Umstellung auf Stereo-Ausgabe, Web-Interface, erste
  Session-Doku.
