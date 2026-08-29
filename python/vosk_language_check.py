#!/usr/bin/env python3
# Copyright (C) 2026 RadioSabbelNich
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 (or
# later), as published by the Free Software Foundation. See LICENSE.

"""
vosk_language_check.py — eigenständiges Werkzeug (kein Teil des Hauptloops
oder der WebUI): hört bei jedem Sender kurz mit und erkennt per Vosk-STT,
welche der konfigurierten STT-Sprachen dort tatsächlich gesprochen wird —
für das `language`-Feld pro Sender (siehe stations_store.py-Moduldocstring,
SESSION.md 2026-08-29 "STT-Sprache pro Sender"), das bislang von Hand über
die Config-Seite gepflegt werden muss.

Aufruf (im laufenden Container, NICHT auf dem Host -- braucht ffmpeg,
Netzwerkzugriff auf die Sender-URLs und die gemounteten Vosk-Modelle):

    docker exec radiosabbelnich python3 vosk_language_check.py [Optionen]

Standardmäßig NUR ein Report (JSON unter /app/logs/, siehe
DEFAULT_REPORT_PATH -- landet über den logs/-Bind-Mount unter
data/logs/ auf dem Host), OHNE stations.json anzufassen -- "checken UND
notieren", nicht "checken und automatisch umschreiben". Erst --apply
schreibt erkannte Sprachen tatsächlich per stations_store.set_language()
zurück, und auch dann nur für Sender, bei denen die Erkennung eindeutig
war (siehe _pick_winner()) UND die noch KEIN eigenes language-Feld haben
(--force nötig, um das zu überschreiben) -- ein Nutzer, der eine Sprache
schon manuell gesetzt hat, kennt seinen Sender besser als eine
automatische Kurzanalyse.

Technik, Wiederverwendung statt Neubau:
- Audio-Capture per ffmpeg exakt wie station_import.check_reachable()
  (gleicher Grund fürs Zeitfenster-Prinzip, siehe dessen Docstring) --
  hier zusätzlich das PCM tatsächlich behalten statt nur Byte-Zähler.
- Sprach-Erkennung per stt_filter._VoskEngine.transcribe() -- dieselbe
  Engine-Klasse wie im Live-Betrieb, hier aber für ALLE konfigurierten
  Sprachen GLEICHZEITIG geladen (nicht der Lazy-Load+LRU-Cache aus
  stt_filter.SttFilter, der ist fürs Live-Sampling EINER erwarteten
  Sprache gebaut, siehe dortiger Moduldocstring -- hier soll pro Clip
  jede Sprache gegeneinander antreten).

Eine einzelne 3-Sekunden-Stichprobe (CLIP_SECONDS, wie im Live-Betrieb)
reicht nicht: viele Sender senden über weite Strecken Musik, ein Clip
mittendrin trifft oft keine Sprache. Deshalb pro Sender CAPTURE_SECONDS
(Default 30s) am Stück aufnehmen, in CLIP_SECONDS-Häppchen zerlegen und
JEDEN Häppchen gegen JEDE konfigurierte Sprache prüfen -- Sieger ist die
Sprache mit den meisten Clips über ihrer eigenen confidence_threshold,
aber nur bei mindestens MIN_CONFIDENT_CLIPS Treffern UND klarem Vorsprung
vor der zweitplatzierten Sprache (siehe _pick_winner()) -- sonst "unklar"
statt einer geratenen Zuordnung, die stillschweigend falsch bliebe.

CPU-Vorsicht: läuft im SELBEN Container wie der Hauptloop (der ohnehin
laufend Silero-VAD/STT/Fingerprinting rechnet) -- Concurrency deshalb
niedriger als beim rein netzwerkgebundenen station_import.py
(CHECK_CONCURRENCY=10), siehe DEFAULT_CONCURRENCY unten.
"""

import argparse
import json
import logging
import os
import select
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

import settings_store
import station_import
import stations_store
import stt_filter

log = logging.getLogger("vosk_language_check")

CAPTURE_SECONDS = 30.0
CONCURRENCY = 4  # niedriger als station_import.CHECK_CONCURRENCY, siehe Moduldocstring
MIN_CONFIDENT_CLIPS = 2  # weniger als das gilt als Zufallstreffer, nicht als Befund
# Vorsprung, den die Sieger-Sprache vor der zweitplatzierten Sprache
# (Trefferzahl) haben muss, um als eindeutig zu gelten -- ein 3:2-Ergebnis
# ist kein verlässlicher Befund, ein 5:1 schon.
MIN_LEAD = 2

DEFAULT_REPORT_PATH = "/app/logs/vosk_language_check_report.json"

# Im Dockerfile-ENTRYPOINT fest verdrahtet (--webui-port 5000), NICHT über
# .env konfigurierbar -- siehe ARCHITECTURE.md. Für den Reload-Trigger
# unten reicht das, weil dieses Skript ohnehin nur INNERHALB desselben
# Containers per `docker exec` läuft (localhost).
WEBUI_INTERNAL_PORT = 5000


def capture_pcm(url: str, seconds: float, sample_rate: int = stt_filter.TARGET_SR):
    """Nimmt bis zu `seconds` Sekunden Mono-PCM (int16) direkt bei
    `sample_rate` auf -- ffmpeg resampled dabei selbst, spart den
    manuellen Resample-Schritt aus stt_filter._resample() für den
    Normalfall (source_sr == TARGET_SR). Gleicher select()-Lese-Loop wie
    station_import.check_reachable(), hier aber mit tatsächlich
    aufgehobenen Bytes statt nur Zählung -- siehe dessen Docstring für
    die Begründung des Zeitfenster-Prinzips (nicht "kam überhaupt was",
    sondern "kommt gerade noch was"). Gibt None zurück, wenn zu wenig
    Audio ankam (Sender tot/nicht erreichbar).

    ZWEI unabhängige Abbruchbedingungen, nicht nur die Wall-Clock-Deadline:
    eine Quelle, die schneller als Echtzeit liefert (z.B. ein CDN-Vorrat,
    siehe station_import.py-Moduldocstring für den bekannten BBC-Radio-
    Scotland-Fall), würde sonst unbegrenzt viel Audio in `chunks` sammeln,
    bevor die Wall-Clock-Deadline überhaupt greift -- deshalb zusätzlich
    hart bei `seconds` Sekunden AUFGENOMMENEM Audio abbrechen, nicht erst
    bei `seconds` Sekunden VERSTRICHENER Zeit."""
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-user_agent", station_import.USER_AGENT,
             "-i", url,
             "-map", "0:a:0", "-f", "s16le", "-acodec", "pcm_s16le",
             "-ar", str(sample_rate), "-ac", "1", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        log.debug("[capture] %s -> ffmpeg nicht startbar (%s)", url, e)
        return None

    chunks = []
    total_bytes = 0
    target_bytes = int(seconds * sample_rate * 2)  # s16le = 2 Bytes/Sample
    deadline = time.monotonic() + seconds
    try:
        fd = proc.stdout.fileno()
        while total_bytes < target_bytes:
            time_left = deadline - time.monotonic()
            if time_left <= 0:
                break
            ready, _, _ = select.select([fd], [], [], time_left)
            if not ready:
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            total_bytes += len(chunk)
    finally:
        proc.kill()
        proc.wait()

    if not chunks:
        return None
    raw = b"".join(chunks)[:target_bytes]
    got_seconds = len(raw) / (sample_rate * 2)
    if got_seconds < stt_filter.CLIP_SECONDS:
        log.debug("[capture] %s -> nur %.1fs Audio, zu wenig für einen Clip", url, got_seconds)
        return None
    return np.frombuffer(raw, dtype=np.int16)


def _split_clips(pcm: np.ndarray, sample_rate: int) -> list:
    clip_len = int(stt_filter.CLIP_SECONDS * sample_rate)
    n_clips = len(pcm) // clip_len
    return [pcm[i * clip_len:(i + 1) * clip_len] for i in range(n_clips)]


def _pick_winner(hits: dict):
    """hits: {code: Trefferzahl}. Sieger nur bei ausreichend UND klar
    führenden Treffern (siehe Moduldocstring) -- sonst None (unklar)."""
    if not hits:
        return None
    ranked = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)
    top_code, top_n = ranked[0]
    runner_up_n = ranked[1][1] if len(ranked) > 1 else 0
    if top_n >= MIN_CONFIDENT_CLIPS and (top_n - runner_up_n) >= MIN_LEAD:
        return top_code
    return None


def detect_language(pcm: np.ndarray, engines: dict, languages_cfg: dict,
                     sample_rate: int = stt_filter.TARGET_SR) -> dict:
    """Prüft jeden Clip aus `pcm` gegen jede Sprache in `engines`
    (code -> stt_filter._VoskEngine). Gibt {"winner": code|None,
    "hits": {code: n}, "clips_analyzed": n} zurück."""
    clips = _split_clips(pcm, sample_rate)
    hits = {code: 0 for code in engines}
    for clip in clips:
        for code, engine in engines.items():
            threshold = languages_cfg.get(code, {}).get("confidence_threshold", 0.6)
            try:
                _text, confidence = engine.transcribe(clip, sample_rate)
            except Exception as e:
                log.debug("STT-Fehler bei Sprache '%s': %s", code, e)
                continue
            if confidence >= threshold:
                hits[code] += 1
    return {"winner": _pick_winner(hits), "hits": hits, "clips_analyzed": len(clips)}


def _load_engines(languages_cfg: dict, only_codes=None) -> dict:
    """Lädt EIN _VoskEngine-Objekt PRO konfigurierter Sprache, alle
    gleichzeitig im Speicher (bewusst kein Lazy-Load/LRU wie
    stt_filter.SttFilter -- siehe Moduldocstring, hier müssen alle
    Kandidaten gleichzeitig verfügbar sein). Bei vielen/großen Modellen
    entsprechend RAM-hungrig -- liegt in der Verantwortung des Aufrufers
    (--languages schränkt bei Bedarf ein)."""
    engines = {}
    for code, cfg in languages_cfg.items():
        if only_codes and code not in only_codes:
            continue
        model_path = cfg.get("vosk_model_path")
        if not model_path:
            log.warning("⚠ Sprache '%s' hat keinen Modellpfad, wird übersprungen.", code)
            continue
        try:
            engines[code] = stt_filter._VoskEngine(model_path)
            log.info("🗣 Vosk-Modell '%s' geladen (%s).", code, model_path)
        except Exception as e:
            log.warning("⚠ Sprache '%s' nicht ladbar (%s), wird übersprungen.", code, e)
    return engines


def scan_stations(stations: list, engines: dict, languages_cfg: dict,
                   capture_seconds: float = CAPTURE_SECONDS,
                   concurrency: int = CONCURRENCY, progress=None) -> list:
    results = []

    def _check_one(station):
        pcm = capture_pcm(station["url"], capture_seconds)
        if pcm is None:
            return {"id": station["id"], "name": station["name"],
                     "current_language": station.get("language", ""),
                     "winner": None, "hits": {}, "clips_analyzed": 0,
                     "error": "nicht erreichbar / zu wenig Audio"}
        detection = detect_language(pcm, engines, languages_cfg)
        return {"id": station["id"], "name": station["name"],
                 "current_language": station.get("language", ""),
                 "error": None, **detection}

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_check_one, s): s for s in stations}
        for future in as_completed(futures):
            station = futures[future]
            try:
                result = future.result()
            except Exception as e:
                log.warning("⚠ Sender '%s' übersprungen (%s).", station["name"], e)
                result = {"id": station["id"], "name": station["name"],
                          "current_language": station.get("language", ""),
                          "winner": None, "hits": {}, "clips_analyzed": 0, "error": str(e)}
            results.append(result)
            if progress:
                progress(result)
    return results


def trigger_reload(any_station_id: str) -> bool:
    """Stößt einen Reload im laufenden Hauptloop an (siehe ARCHITECTURE.md,
    Abschnitt "Automatische Sender-Sprach-Erkennung", für die volle
    Herleitung): set_language() schreibt direkt in stations.json, aber der
    Hauptloop hält `active_stations` nur als In-Memory-Cache, der
    ausschließlich in `SwitcherState.reload()` aktualisiert wird -- das
    wiederum nur läuft, wenn `state.request_reload()` (exklusiv in den
    WebUI-Handlern) das Flag gesetzt hat. Dieser separate `docker exec`-
    Prozess kann das In-Memory-Flag nicht direkt setzen.

    Workaround: denselben Sender einmal UNVERÄNDERT über die lokale
    Config-API erneut speichern -- `reload()` liest dabei ohnehin die
    GESAMTE stations.json neu ein, nicht nur den einen angefassten
    Sender, jede beliebige Sender-ID reicht also. Best-Effort: die WebUI
    könnte deaktiviert sein (`--webui-port 0`) oder gerade neu starten --
    dann bleibt die Änderung bis zum nächsten ohnehin fälligen Reload
    liegen, das ist kein harter Fehler (die Datei ist ja bereits korrekt
    geschrieben), nur eine verzögerte Wirkung. Schema/TLS ist von außen
    nicht bekannt (`tls_enabled` ist Laufzeit-Konfiguration) -- deshalb
    beide Varianten probieren, HTTPS zuerst (der Default in den meisten
    Deployments, siehe ARCHITECTURE.md TLS-Abschnitt)."""
    try:
        stations = {s["id"]: s for s in stations_store.load_all()}
    except Exception as e:
        log.warning("⚠ Reload-Trigger: stations.json nicht lesbar (%s).", e)
        return False
    s = stations.get(any_station_id)
    if s is None:
        return False

    payload = json.dumps({
        "name": s["name"], "url": s["url"], "category": s["category"],
        "enabled": s["enabled"], "language": s["language"],
    }).encode("utf-8")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # selbstsigniertes Zertifikat, siehe ARCHITECTURE.md TLS-Abschnitt

    path = "/api/config/stations/" + urllib.parse.quote(s["id"], safe="")
    for scheme in ("https", "http"):
        url = f"{scheme}://localhost:{WEBUI_INTERNAL_PORT}{path}"
        req = urllib.request.Request(url, data=payload, method="POST",
                                      headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                resp.read()
            log.info("🔄 Reload ausgelöst (via %s, Sender '%s').", scheme, s["name"])
            return True
        except Exception as e:
            log.debug("Reload-Trigger über %s fehlgeschlagen: %s", scheme, e)
            continue

    log.warning("⚠ Reload-Trigger fehlgeschlagen (WebUI nicht erreichbar) -- Änderungen "
                "wirken erst beim nächsten ohnehin fälligen Reload oder Neustart.")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--languages", default=None,
                         help="Kommagetrennte Sprachcodes, gegen die geprüft wird "
                              "(Default: alle in settings.json konfigurierten).")
    parser.add_argument("--category", default=None,
                         help="Nur Sender dieser Kategorie prüfen (Default: alle aktivierten).")
    parser.add_argument("--include-disabled", action="store_true",
                         help="Auch DEAKTIVIERTE Sender prüfen (Default: nur aktivierte, wie in "
                              "der Rotation). Bei vielen importierten, noch nie freigeschalteten "
                              "Sendern (siehe stations_store.IMPORT_CATEGORY) kann das die "
                              "Laufzeit deutlich verlängern -- ggf. mit --category/--limit "
                              "eingrenzen.")
    parser.add_argument("--all", action="store_true",
                         help="Auch Sender mit bereits gesetztem language-Feld erneut prüfen "
                              "(Default: nur Sender ohne eigene Sprache).")
    parser.add_argument("--limit", type=int, default=None,
                         help="Höchstens so viele Sender prüfen (für einen schnellen Testlauf).")
    parser.add_argument("--capture-seconds", type=float, default=CAPTURE_SECONDS,
                         help=f"Aufnahmedauer pro Sender in Sekunden (Default {CAPTURE_SECONDS}).")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY,
                         help=f"Max. gleichzeitige Sender-Checks (Default {CONCURRENCY}, "
                              "siehe Moduldocstring zur CPU-Vorsicht).")
    parser.add_argument("--apply", action="store_true",
                         help="Erkannte Sprachen tatsächlich in stations.json schreiben "
                              "(sonst nur Report, siehe Moduldocstring).")
    parser.add_argument("--force", action="store_true",
                         help="Zusammen mit --apply UND --all: auch bereits gesetzte "
                              "language-Felder überschreiben.")
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH,
                         help=f"Pfad für den JSON-Report (Default {DEFAULT_REPORT_PATH}).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(message)s",
                         datefmt="%H:%M:%S")

    settings = settings_store.load()
    languages_cfg = settings["stt_filter"]["languages"]
    only_codes = set(c.strip().lower() for c in args.languages.split(",")) if args.languages else None

    engines = _load_engines(languages_cfg, only_codes)
    if len(engines) < 2:
        print(f"Mindestens 2 geladene Sprachen nötig, um sie gegeneinander zu prüfen "
              f"(geladen: {sorted(engines)}). Sprachen unter '🌐 STT-Sprachen' anlegen "
              f"oder --languages einschränken.")
        return 1

    if args.include_disabled:
        stations = sorted(stations_store.load_all(), key=lambda s: s["name"].lower())
    else:
        stations = stations_store.load_active()  # bereits alphabetisch sortiert
    if args.category:
        stations = [s for s in stations if s["category"] == args.category]
    if not args.all:
        stations = [s for s in stations if not s.get("language")]
    if args.limit:
        stations = stations[:args.limit]

    if not stations:
        print("Keine passenden Sender gefunden (siehe --category/--all/--limit).")
        return 0

    print(f"Prüfe {len(stations)} Sender gegen {sorted(engines)} "
          f"({args.capture_seconds:.0f}s Aufnahme, max. {args.concurrency} gleichzeitig) ...")

    done = 0

    def _on_result(result):
        nonlocal done
        done += 1
        tag = result["winner"] or ("?" if result["error"] else "unklar")
        print(f"  [{done}/{len(stations)}] {result['name']}: {tag} "
              f"(Treffer: {result['hits']}, Clips: {result['clips_analyzed']})"
              + (f"  ⚠ {result['error']}" if result["error"] else ""))

    results = scan_stations(stations, engines, languages_cfg,
                             capture_seconds=args.capture_seconds,
                             concurrency=args.concurrency, progress=_on_result)

    winners = [r for r in results if r["winner"]]
    print(f"\n{len(winners)} von {len(results)} Sendern eindeutig erkannt.")

    report_dir = os.path.dirname(args.report)
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "languages_checked": sorted(engines), "results": results}, f,
                  ensure_ascii=False, indent=2)
    print(f"Report geschrieben: {args.report}")

    if args.apply:
        applied_id = None
        applied = 0
        for r in winners:
            if r["current_language"] and not args.force:
                continue  # eigene Wahl des Nutzers, nicht ungefragt überschreiben
            stations_store.set_language(r["id"], r["winner"])
            applied_id = r["id"]
            applied += 1
        print(f"{applied} Sender-Sprachen in stations.json übernommen.")
        if applied_id and trigger_reload(applied_id):
            print("Laufender Hauptloop neu geladen -- Änderungen wirken sofort.")
        elif applied_id:
            print("⚠ Hauptloop konnte nicht automatisch neu geladen werden -- Änderungen "
                  "wirken erst beim nächsten ohnehin fälligen Reload/Neustart (siehe README).")
    else:
        print("Kein --apply angegeben -- stations.json wurde NICHT verändert, "
              "nur der Report oben geschrieben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
