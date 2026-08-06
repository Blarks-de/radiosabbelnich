#!/usr/bin/env python3
"""
i18n.py — Übersetzungstabelle für die Benutzerdialoge des Web-Interfaces
(_PAGE_HTML/_CONFIG_PAGE_HTML in webui.py). Reine Domänenlogik ohne Bezug zu
StreamSource/SwitcherState, analog zu news_break.py/stt_filter.py.

Übersetzt wird NUR, was der Nutzer im Browser sieht (Labels, Buttons,
Überschriften, alert()/confirm()-Dialoge, dynamische Statuszeilen). Log-
Meldungen, Code-Kommentare und die vom Backend geworfenen ValueError-Texte
(settings_store.py/station_import.py/stations_store.py) bleiben bewusst
deutsch -- siehe CLAUDE.md ("Alles auf Deutsch" für Code/Logs) und SESSION.md
(Scope-Entscheidung beim Einbau dieses Features).

STRINGS ist nach Key gruppiert (beide Sprachen nebeneinander), nicht nach
Sprache -- eine fehlende Übersetzung fällt beim Nebeneinanderstehen leichter
auf als in zwei komplett getrennten Dicts. webui.py prüft beim Modul-Import
zusätzlich per Regex, dass jeder in den Templates verwendete Key hier mit
beiden Sprachen existiert (siehe dortiger _check_i18n_coverage()).
"""

import logging
import os

log = logging.getLogger("i18n")

LANGUAGES = {"de", "en"}

_env_lang = os.environ.get("UI_LANGUAGE", "de").strip().lower()
if _env_lang not in LANGUAGES:
    if os.environ.get("UI_LANGUAGE"):
        log.warning("⚠ UI_LANGUAGE=%r ungültig (erlaubt: de, en) -- Fallback auf 'de'.", _env_lang)
    _env_lang = "de"
DEFAULT_LANGUAGE = _env_lang

STRINGS = {
    # ---- gemeinsam (Player- und Config-Seite) ----
    "common_save": {"de": "Speichern", "en": "Save"},
    "common_cancel": {"de": "Abbrechen", "en": "Cancel"},
    "common_edit": {"de": "Bearbeiten", "en": "Edit"},
    "common_delete": {"de": "Löschen", "en": "Delete"},
    "common_error": {"de": "Fehler: {msg}", "en": "Error: {msg}"},
    "common_loading": {"de": "Lade …", "en": "Loading …"},
    "common_unknown": {"de": "unbekannt", "en": "unknown"},

    # ---- Player-Seite (_PAGE_HTML) ----
    "idx_prev_title": {"de": "Vorheriger Sender", "en": "Previous station"},
    "idx_prev_btn": {"de": "⏮ Zurück", "en": "⏮ Back"},
    "idx_next_title": {"de": "Nächster Sender", "en": "Next station"},
    "idx_next_btn": {"de": "Weiter ⏭", "en": "Next ⏭"},
    "idx_qr_vlc_title": {"de": "QR-Code für die Stream-Adresse (VLC & Co.)",
                          "en": "QR code for the stream address (VLC & co.)"},
    "idx_qr_vlc_label": {"de": "VLC", "en": "VLC"},
    "idx_qr_phone_title": {"de": "QR-Code für dieses Web-Interface (zum Öffnen auf dem Handy)",
                            "en": "QR code for this web interface (to open on your phone)"},
    "idx_qr_phone_label": {"de": "Handy", "en": "Phone"},
    "idx_qr_modal_close_aria": {"de": "Schließen", "en": "Close"},
    "idx_qr_modal_title": {"de": "Adresse zum Scannen", "en": "Address to scan"},
    "idx_qr_modal_copy_btn": {"de": "📋 Adresse kopieren", "en": "📋 Copy address"},
    "idx_zapping_error_title": {"de": "Letzten fälschlich erkannten Werbe-Clip aus der Datenbank löschen",
                                 "en": "Delete the last incorrectly detected ad clip from the database"},
    "idx_zapping_error_btn": {"de": "🛑 Zapping-Fehler", "en": "🛑 Zap error"},
    "idx_gesabbel_title": {"de": "Sofort weiterschalten, weil hier gerade geredet wird",
                            "en": "Switch immediately because someone's talking right now"},
    "idx_gesabbel_btn": {"de": "⚡ ZAPPEN!", "en": "⚡ ZAP!"},
    "idx_filter_toggle_title": {"de": "Automatische Sprache-Erkennung komplett pausieren/wieder anschalten",
                                 "en": "Pause/resume automatic speech detection entirely"},
    "idx_filter_disable_btn": {"de": "Sabbelfilter deaktivieren", "en": "Disable chatter filter"},
    "idx_filter_enable_btn": {"de": "Sabbelfilter aktivieren", "en": "Enable chatter filter"},
    "idx_bs_meter_label": {"de": "🤥 Bullshitometer", "en": "🤥 Bullshit-o-meter"},
    "idx_stations_heading": {"de": "Sender", "en": "Stations"},
    "idx_listeners_heading": {"de": "Hörer", "en": "Listeners"},
    "idx_config_link": {"de": "⚙ Sender verwalten", "en": "⚙ Manage stations"},
    "idx_connection_lost": {"de": "Verbindung zum Server verloren …", "en": "Connection to server lost …"},
    "idx_current_playing": {"de": "▶ Läuft gerade: {name}", "en": "▶ Now playing: {name}"},
    "idx_no_station_active": {"de": "Kein Sender aktiv", "en": "No station active"},
    "idx_news_break": {"de": "📰 Pause", "en": "📰 Break"},
    "idx_filter_off": {"de": "Filter aus", "en": "Filter off"},
    "idx_listeners_unavailable": {"de": "Hörer-Info nicht verfügbar.", "en": "Listener info unavailable."},
    "idx_listeners_none": {"de": "Aktuell keine Hörer verbunden.", "en": "No listeners currently connected."},
    "idx_listeners_col_ip": {"de": "IP", "en": "IP"},
    "idx_listeners_col_since": {"de": "Verbunden seit", "en": "Connected since"},
    "idx_listeners_col_client": {"de": "Client", "en": "Client"},
    "idx_meta_updated": {"de": "Aktualisiert: {time}", "en": "Updated: {time}"},
    "idx_qr_vlc_modal_title": {"de": "▶️ Stream-Adresse zum Scannen (VLC & Co.)",
                                "en": "▶️ Stream address to scan (VLC & co.)"},
    "idx_qr_phone_modal_title": {"de": "📱 Web-Interface-Adresse zum Scannen",
                                  "en": "📱 Web interface address to scan"},
    "idx_address_copied": {"de": "📋 Adresse kopiert.", "en": "📋 Address copied."},
    "idx_copy_failed": {"de": "Kopieren fehlgeschlagen: {msg}", "en": "Copy failed: {msg}"},
    "idx_clip_deleted": {"de": "✓ Clip gelöscht", "en": "✓ Clip deleted"},
    "idx_switched_back_to": {"de": " — zurück zu {name}", "en": " — back to {name}"},
    "idx_zap_switching": {"de": "🗣️ Wird umgeschaltet …", "en": "🗣️ Switching …"},
    "idx_filter_switching": {"de": "Sabbelfilter wird umgeschaltet …", "en": "Toggling chatter filter …"},

    # ---- Config-Seite (_CONFIG_PAGE_HTML) ----
    "cfg_back_link": {"de": "← zurück zum Player", "en": "← back to player"},
    "cfg_heading": {"de": "⚙ Sender verwalten", "en": "⚙ Manage stations"},

    "cfg_news_break_heading": {"de": "📰 Nachrichten-Pause", "en": "📰 News break"},
    "cfg_news_break_hint": {
        "de": ('Spielt zur vollen/halben Stunde statt eines Radiosenders '
               'eine zufällige lokale MP3 ab. Der MP3-Ordner unten ist ein '
               '<strong>Container-interner Pfad</strong> — der eigentliche Host-Ordner '
               '(z.B. ein SMB-Mount) wird über <code>NEWS_MP3_FOLDER</code> in '
               '<code>.env</code> nach <code>/app/news_mp3</code> gemountet und braucht '
               'dafür einen Neustart des Containers, kein Feld hier.'),
        "en": ('Plays a random local MP3 instead of a radio station on the hour/'
               'half hour. The MP3 folder below is a '
               '<strong>container-internal path</strong> — the actual host folder '
               '(e.g. an SMB mount) is mounted via <code>NEWS_MP3_FOLDER</code> in '
               '<code>.env</code> to <code>/app/news_mp3</code> and needs a restart '
               'of the container for that, not a field here.'),
    },
    "cfg_active_label": {"de": "aktiv", "en": "enabled"},
    "cfg_nb_folder_label": {"de": "MP3-Ordner (Container-Pfad)", "en": "MP3 folder (container path)"},
    "cfg_nb_window_label": {"de": "Zeitfenster (Minuten)", "en": "Time window (minutes)"},
    "cfg_nb_hours_enabled_label": {"de": "nur zu bestimmten Stunden aktiv",
                                    "en": "only active during certain hours"},
    "cfg_nb_hour_start_label": {"de": "von Stunde", "en": "from hour"},
    "cfg_nb_hour_end_label": {"de": "bis Stunde", "en": "to hour"},

    "cfg_new_station_heading": {"de": "Neuer Sender", "en": "New station"},
    "cfg_add_name_placeholder": {"de": "Name", "en": "Name"},
    "cfg_add_url_placeholder": {"de": "Stream-URL (https://...)", "en": "Stream URL (https://...)"},
    "cfg_enabled_label": {"de": "aktiviert", "en": "enabled"},
    "cfg_add_btn": {"de": "Hinzufügen", "en": "Add"},

    "cfg_import_heading": {"de": "📻 Sender-Import", "en": "📻 Station import"},
    "cfg_import_hint": {
        "de": ('Lädt eine M3U-Playlist und hört bei jedem Sender ein '
               'paar Sekunden mit: übernommen wird nur, wer dabei durchgehend Audio '
               'liefert (nicht bloß beim Verbinden). Neue Sender landen '
               '<strong>deaktiviert</strong> in der Kategorie "Unsortiert" — du '
               'entscheidest per Haken, wer in die Rotation darf. Kann bei einer '
               'langen Liste einige Minuten dauern.'),
        "en": ('Downloads an M3U playlist and listens to each station for a '
               'few seconds: only stations that deliver audio continuously '
               '(not just when connecting) are kept. New stations land '
               '<strong>disabled</strong> in the "Unsorted" category — you '
               'decide via checkbox who joins the rotation. Can take a few '
               'minutes for a long list.'),
    },
    "cfg_import_url_label": {"de": "Playlist-URL", "en": "Playlist URL"},
    "cfg_import_btn": {"de": "Sender importieren", "en": "Import stations"},

    "cfg_stream_heading": {"de": "🔗 Streaming-Adresse", "en": "🔗 Streaming address"},
    "cfg_stream_hint": {
        "de": ('Adresse, die auf der Startseite unter "Streaming via VLC" '
               'angezeigt wird (zum Eintragen in einen externen Player). Leer lassen, '
               'um sie automatisch aus der Adresse zu bilden, über die die Startseite '
               'gerade im Browser aufgerufen wird.'),
        "en": ('Address shown on the home page under "Streaming via VLC" '
               '(to enter into an external player). Leave empty to derive it '
               'automatically from the address the home page is currently being '
               'accessed with in the browser.'),
    },
    "cfg_stream_url_label": {"de": "Stream-URL", "en": "Stream URL"},

    "cfg_tls_heading": {"de": "🔒 HTTPS", "en": "🔒 HTTPS"},
    "cfg_tls_hint": {
        "de": ('Verschlüsselt den Zugriff aufs Web-Interface (Player-'
               'Seite und diese Config-Seite) per TLS. Braucht ein Zertifikat unter '
               '<code>TLS_CERT_FILE</code>/<code>TLS_KEY_FILE</code> in <code>.env</code> '
               '(Host-Pfade zu PEM-Dateien, z.B. per <code>tailscale cert</code> '
               'erzeugt) — ohne die bleibt der Haken hier wirkungslos, das '
               'Web-Interface läuft dann weiter über HTTP. <strong>Wirkt erst nach '
               'einem Neustart des Containers</strong> (<code>docker compose up -d '
               '--build radiozapper</code>), nicht sofort wie die meisten anderen '
               'Einstellungen hier. Der Icecast-Stream selbst bekommt unabhängig davon '
               'automatisch einen zusätzlichen HTTPS-Port, sobald dieselben '
               'Zertifikate in <code>.env</code> eingetragen sind — dafür gibt es '
               'keinen eigenen Schalter.'),
        "en": ('Encrypts access to the web interface (player '
               'page and this config page) via TLS. Needs a certificate at '
               '<code>TLS_CERT_FILE</code>/<code>TLS_KEY_FILE</code> in <code>.env</code> '
               '(host paths to PEM files, e.g. generated via <code>tailscale cert</code>) '
               '— without those, this checkbox has no effect and the '
               'web interface keeps running over HTTP. <strong>Only takes effect after '
               'restarting the container</strong> (<code>docker compose up -d '
               '--build radiozapper</code>), not immediately like most other '
               'settings here. The Icecast stream itself independently gets '
               'an additional HTTPS port automatically as soon as the same '
               'certificates are set in <code>.env</code> — there is no separate '
               'switch for that.'),
    },
    "cfg_tls_checkbox_label": {"de": "HTTPS fürs Web-Interface aktiv",
                                "en": "HTTPS for the web interface enabled"},

    "cfg_buffer_heading": {"de": "⏱ Puffer-Einstellungen", "en": "⏱ Buffer settings"},
    "cfg_buffer_hint": {
        "de": ('Die nächsten Sender in Rotationsreihenfolge laufen im '
               'Hintergrund mit und halten Audio vor, damit Wechsel flüssig ablaufen. '
               'Mehr Sekunden/Sender = flüssiger, aber mehr Bandbreite/CPU.'),
        "en": ('The next stations in rotation order run in the background and '
               'buffer audio so switches happen smoothly. More seconds/stations '
               '= smoother, but more bandwidth/CPU.'),
    },
    "cfg_buffer_seconds_label": {"de": "Sekunden pro gepuffertem Sender", "en": "Seconds per buffered station"},
    "cfg_buffer_count_label": {"de": "Anzahl vorausgepufferter Sender", "en": "Number of pre-buffered stations"},

    "cfg_stt_heading": {"de": "🗣 STT-Sprachfilter", "en": "🗣 STT speech filter"},
    "cfg_stt_hint": {
        "de": ('Zusätzliches Signal per Speech-to-Text: erkennt, ob '
               'gerade zusammenhängender deutscher Text zu hören ist (echte '
               'Moderation) oder nicht (auch deutsch gesungene Musik zählt dann als '
               '"keine Sprache") — ergänzt VAD/Heuristik, die reinen Gesang oft '
               'fälschlich als Sprache werten. <strong>Vosk</strong> ist leichtgewichtig '
               'und Pi-tauglich, <strong>Whisper</strong> genauer, aber deutlich '
               'ressourcenhungriger. Modellpfad/-größe sind Container-interne Werte '
               '(siehe README) — braucht ggf. einen Neustart des Containers, falls '
               'das Modell erstmals gemountet wird.'),
        "en": ('Additional signal via speech-to-text: detects whether '
               'coherent German speech is currently audible (actual '
               'presenting) or not (music sung in German also counts as '
               '"no speech" then) — complements VAD/heuristic, which often '
               'misjudges pure singing as speech. <strong>Vosk</strong> is lightweight '
               'and Pi-friendly, <strong>Whisper</strong> more accurate but noticeably '
               'more resource-hungry. Model path/size are container-internal values '
               '(see README) — may need a restart of the container if '
               'the model is mounted for the first time.'),
    },
    "cfg_stt_status_loading": {"de": "Lade Status …", "en": "Loading status …"},
    "cfg_stt_engine_label": {"de": "Engine", "en": "Engine"},
    "cfg_stt_engine_vosk_option": {"de": "Vosk (leichtgewicht, Pi-tauglich)",
                                    "en": "Vosk (lightweight, Pi-friendly)"},
    "cfg_stt_engine_whisper_option": {"de": "Whisper (genauer, ressourcenhungriger)",
                                       "en": "Whisper (more accurate, more resource-hungry)"},
    "cfg_stt_vosk_path_label": {"de": "Vosk-Modellpfad (Container-Pfad)", "en": "Vosk model path (container path)"},
    "cfg_stt_whisper_size_label": {"de": "Whisper-Modellgröße", "en": "Whisper model size"},
    "cfg_stt_interval_label": {"de": "Sample-Intervall (Sekunden)", "en": "Sample interval (seconds)"},
    "cfg_stt_threshold_label": {"de": "Konfidenz-Schwelle (0–1)", "en": "Confidence threshold (0–1)"},
    "cfg_stt_combine_label": {"de": "Verknüpfung mit VAD/Heuristik", "en": "Combination with VAD/heuristic"},
    "cfg_stt_combine_and_option": {"de": 'UND — beide müssen "Sprache" sagen (empfohlen)',
                                    "en": 'AND — both must say "speech" (recommended)'},
    "cfg_stt_combine_or_option": {"de": "ODER — eines reicht", "en": "OR — either is enough"},

    "cfg_fingerprint_heading": {"de": "🗑 Fingerprint-Datenbank", "en": "🗑 Fingerprint database"},
    "cfg_fingerprint_hint": {
        "de": ('Löscht alle gelernten Jingle-/Werbespot-Clips (nicht '
               'die Senderliste). Danach lernt die Erkennung wieder bei Null.'),
        "en": ('Deletes all learned jingle/ad clips (not the station list). '
               'Detection then starts learning from scratch again.'),
    },
    "cfg_fingerprint_clear_btn": {"de": "Clip-DB leeren", "en": "Clear clip DB"},

    "cfg_resources_heading": {"de": "💾 Ressourcen-Verbrauch", "en": "💾 Resource usage"},
    "cfg_resources_hint": {
        "de": "Aktueller Verbrauch von RadioZapper selbst (nicht des Hosts), alle 5 Sekunden aktualisiert.",
        "en": "Current usage of RadioZapper itself (not the host), refreshed every 5 seconds.",
    },
    "cfg_resources_ram_total": {"de": "RAM gesamt", "en": "Total RAM"},
    "cfg_resources_ram_breakdown": {"de": "davon Python / ffmpeg", "en": "of which Python / ffmpeg"},
    "cfg_resources_cpu_total": {"de": "CPU gesamt", "en": "Total CPU"},
    "cfg_resources_ffmpeg_count": {"de": "Laufende ffmpeg-Prozesse", "en": "Running ffmpeg processes"},
    "cfg_resources_fingerprint_db": {"de": "Fingerprint-DB", "en": "Fingerprint DB"},
    "cfg_resources_log": {"de": "Logdatei (inkl. Rotation)", "en": "Log file (incl. rotation)"},
    "cfg_resources_whisper_cache": {"de": "Whisper-Modell-Cache", "en": "Whisper model cache"},

    "cfg_language_heading": {"de": "🌐 Sprache", "en": "🌐 Language"},
    "cfg_language_hint": {
        "de": ('Sprache der Web-Oberfläche (Player- und Config-Seite). Wirkt '
               'sofort für neue Seitenaufrufe; diese Seite lädt nach dem Speichern '
               'automatisch neu. Startwert kommt aus <code>UI_LANGUAGE</code> in '
               '<code>.env</code>, danach gewinnt immer die hier gespeicherte '
               'Einstellung.'),
        "en": ('Language of the web interface (player and config page). Takes '
               'effect immediately for new page loads; this page reloads '
               'automatically after saving. The initial value comes from '
               '<code>UI_LANGUAGE</code> in <code>.env</code>, after that the '
               'setting saved here always wins.'),
    },
    "cfg_language_label": {"de": "Sprache der Oberfläche", "en": "Interface language"},

    # ---- Config-Seite: dynamische JS-Meldungen ----
    "cfg_load_stations_failed": {"de": "Konnte Senderliste nicht laden: {msg}",
                                  "en": "Could not load station list: {msg}"},
    "cfg_no_stations_in_category": {"de": "Keine Sender in dieser Kategorie.",
                                     "en": "No stations in this category."},
    "cfg_disable_all_btn": {"de": "Alle deaktivieren", "en": "Disable all"},
    "cfg_disable_all_title": {"de": 'Alle {count} aktivierten Sender in "{cat}" deaktivieren',
                               "en": 'Disable all {count} enabled stations in "{cat}"'},
    "cfg_disable_all_confirm": {"de": 'Wirklich alle {count} aktivierten Sender in "{cat}" deaktivieren?',
                                 "en": 'Really disable all {count} enabled stations in "{cat}"?'},
    "cfg_disable_all_done": {"de": '{count} Sender in "{cat}" deaktiviert.',
                              "en": '{count} stations in "{cat}" disabled.'},
    "cfg_saved": {"de": "Gespeichert.", "en": "Saved."},
    "cfg_field_url_placeholder": {"de": "Stream-URL", "en": "Stream URL"},
    "cfg_delete_confirm": {"de": '"{name}" wirklich löschen?', "en": 'Really delete "{name}"?'},
    "cfg_deleted": {"de": "Gelöscht.", "en": "Deleted."},
    "cfg_added": {"de": "Hinzugefügt.", "en": "Added."},
    "cfg_load_settings_failed": {"de": "Konnte Einstellungen nicht laden: {msg}",
                                  "en": "Could not load settings: {msg}"},
    "cfg_stt_status_disabled": {"de": "Status: deaktiviert.", "en": "Status: disabled."},
    "cfg_stt_status_active": {"de": "Status: ✅ {engine} aktiv.", "en": "Status: ✅ {engine} active."},
    "cfg_stt_status_error": {"de": "Status: ⚠ deaktiviert ({error}).", "en": "Status: ⚠ disabled ({error})."},
    "cfg_stt_status_model_not_loadable": {"de": "Modell nicht ladbar", "en": "model not loadable"},
    "cfg_buffer_saved": {"de": "Puffer-Einstellungen gespeichert.", "en": "Buffer settings saved."},
    "cfg_stream_saved": {"de": "Streaming-Adresse gespeichert.", "en": "Streaming address saved."},
    "cfg_tls_saved": {"de": "HTTPS-Einstellung gespeichert — wirkt erst nach Neustart des Containers.",
                       "en": "HTTPS setting saved — takes effect only after restarting the container."},
    "cfg_news_break_saved": {"de": "Nachrichten-Pause gespeichert.", "en": "News break saved."},
    "cfg_stt_saved": {"de": "STT-Sprachfilter gespeichert.", "en": "STT speech filter saved."},
    "cfg_import_progress_error": {"de": "Fehler beim Abfragen des Fortschritts: {msg}",
                                   "en": "Error querying progress: {msg}"},
    "cfg_import_loading_playlist": {"de": "Lade Playlist …", "en": "Loading playlist …"},
    "cfg_import_checking": {"de": "Prüfe Sender … {checked} von {total}",
                             "en": "Checking stations … {checked} of {total}"},
    "cfg_import_failed": {"de": "Import fehlgeschlagen: {error}", "en": "Import failed: {error}"},
    "cfg_import_result": {
        "de": ('{checked} Sender geprüft, {working} liefern dauerhaft Audio, '
               '{added} neu (deaktiviert) in "Unsortiert" — zum Aktivieren Haken setzen.'),
        "en": ('{checked} stations checked, {working} deliver audio continuously, '
               '{added} new (disabled) in "Unsorted" — check the box to enable.'),
    },
    "cfg_import_starting": {"de": "Starte Import …", "en": "Starting import …"},
    "cfg_fingerprint_clear_confirm": {
        "de": "Wirklich ALLE gelernten Fingerprint-Clips löschen? Das kann nicht rückgängig gemacht werden.",
        "en": "Really delete ALL learned fingerprint clips? This cannot be undone.",
    },
    "cfg_fingerprint_cleared": {"de": "{cleared} Clip(s) aus der Fingerprint-Datenbank gelöscht.",
                                 "en": "{cleared} clip(s) deleted from the fingerprint database."},
    "cfg_invalid_response": {"de": "Ungültige Antwort vom Server", "en": "Invalid response from server"},
    "cfg_host_path_mounted": {
        "de": "📁 Aktuell gemountet von Host-Pfad: {path} (ändern über {envVar} in .env + Neustart des Containers)",
        "en": "📁 Currently mounted from host path: {path} (change via {envVar} in .env + restart the container)",
    },
    "cfg_host_path_unknown": {
        "de": ('Host-Pfad unbekannt (Container lief noch nicht mit dieser Anzeige -- '
               'docker compose up -d --build radiozapper zeigt ihn danach an).'),
        "en": ("Host path unknown (container hasn't run with this display yet -- "
               "docker compose up -d --build radiozapper will show it afterwards)."),
    },
    "cfg_language_saved": {"de": "Sprache gespeichert — Seite wird neu geladen …",
                            "en": "Language saved — reloading page …"},
}


def validate():
    """Wirft AssertionError, wenn ein Key nicht beide Sprachen hat -- wird
    beim Modul-Import von webui.py zusätzlich mit den tatsächlich in den
    Templates verwendeten Keys abgeglichen (siehe dortiger
    _check_i18n_coverage())."""
    for key, variants in STRINGS.items():
        missing = LANGUAGES - set(variants)
        assert not missing, f"i18n.STRINGS[{key!r}] fehlt Sprache(n) {missing}"


validate()
