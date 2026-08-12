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
    "idx_stt_meter_label": {"de": "🗣 STT", "en": "🗣 STT"},
    "idx_stt_meter_off": {"de": "STT aus", "en": "STT off"},
    "idx_fp_indicator_label": {"de": "🔎 Fingerprint", "en": "🔎 Fingerprint"},
    "idx_fp_state_idle": {"de": "⚪ Idle", "en": "⚪ Idle"},
    "idx_fp_state_match": {"de": "🔴 Treffer: {label}", "en": "🔴 Match: {label}"},
    "idx_fp_state_learned": {"de": "🟢 Gelernt", "en": "🟢 Learned"},
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
    "idx_music_mode_active": {"de": "🎵 Musiksammlung aktiv — Radio pausiert",
                               "en": "🎵 Music library active — radio paused"},
    "idx_music_mode_short": {"de": "🎵 Musik", "en": "🎵 Music"},

    # ---- gemeinsam: Radio/Musiksammlung-Modus-Umschalter (Player- UND
    # Musiksammlung-Seite, siehe CLAUDE.md Modus-Fork) ----
    "mode_radio_btn": {"de": "📻 Radio", "en": "📻 Radio"},
    "mode_music_btn": {"de": "🎵 Musiksammlung", "en": "🎵 Music library"},
    "idx_mode_switching": {"de": "Modus wird gewechselt …", "en": "Switching mode …"},

    # ---- Musiksammlung-Seite (_MUSIC_PAGE_HTML) ----
    "music_heading": {"de": "🎵 Musik Sammlung", "en": "🎵 Music Library"},
    "music_root_label": {"de": "MP3-Root-Ordner:", "en": "MP3 root folder:"},
    "music_root_change_link": {"de": "Ordner unter /config ändern →", "en": "Change folder under /config →"},
    "music_categories_heading": {"de": "Kategorien", "en": "Categories"},
    "music_favorites_heading": {"de": "Favoriten", "en": "Favorites"},
    "music_play_title": {"de": "Play", "en": "Play"},
    "music_stop_title": {"de": "Stop", "en": "Stop"},
    "music_prev_title": {"de": "Vorheriger Track", "en": "Previous track"},
    "music_next_title": {"de": "Nächster Track", "en": "Next track"},
    "music_now_playing": {"de": "🎵 {file} ({index}/{total})", "en": "🎵 {file} ({index}/{total})"},
    "music_idle": {"de": "Bereit — auf ▶ tippen zum Abspielen.", "en": "Ready — tap ▶ to play."},
    "music_switch_hint": {"de": "Erst oben auf 🎵 Musiksammlung wechseln.",
                           "en": "Switch to 🎵 Music library above first."},
    "music_back_link": {"de": "zurück zum Radio-Player", "en": "back to the radio player"},

    # ---- Config-Seite (_CONFIG_PAGE_HTML) ----
    "cfg_back_link": {"de": "← zurück zum Player", "en": "← back to player"},
    "cfg_heading": {"de": "⚙ Sender verwalten", "en": "⚙ Manage stations"},

    "cfg_news_break_heading": {"de": "📰 Nachrichten-Pause", "en": "📰 News break"},
    "cfg_news_break_hint": {
        "de": ('Spielt zur vollen/halben Stunde statt eines Radiosenders '
               'eine zufällige lokale MP3 ab. Der Ordner unten liegt unter einem '
               '<strong>Container-internen Pfad</strong> — der eigentliche Host-Ordner '
               '(z.B. ein SMB-Mount) wird über <code>NEWS_MP3_FOLDER</code> in '
               '<code>.env</code> nach <code>/app/news_mp3</code> gemountet und braucht '
               'dafür einen Neustart des Containers. Darunter kannst du per Klick den '
               'gewünschten Unterordner auswählen.'),
        "en": ('Plays a random local MP3 instead of a radio station on the hour/'
               'half hour. The folder below lives under a '
               '<strong>container-internal path</strong> — the actual host folder '
               '(e.g. an SMB mount) is mounted via <code>NEWS_MP3_FOLDER</code> in '
               '<code>.env</code> to <code>/app/news_mp3</code> and needs a restart '
               'of the container for that. Click below to pick the subfolder you want.'),
    },
    "cfg_active_label": {"de": "aktiv", "en": "enabled"},
    "cfg_nb_folder_label": {"de": "MP3-Ordner (Container-Pfad)", "en": "MP3 folder (container path)"},
    "cfg_nb_window_label": {"de": "Zeitfenster (Minuten)", "en": "Time window (minutes)"},
    "cfg_nb_hours_enabled_label": {"de": "nur zu bestimmten Stunden aktiv",
                                    "en": "only active during certain hours"},
    "cfg_nb_hour_start_label": {"de": "von Stunde", "en": "from hour"},
    "cfg_nb_hour_end_label": {"de": "bis Stunde", "en": "to hour"},

    "cfg_music_library_heading": {"de": "🎵 Musiksammlung", "en": "🎵 Music library"},
    "cfg_music_library_hint": {
        "de": ("Root-Ordner für den Musiksammlung-Modus (Play/Stop auf der /musik-Seite) "
               "— Container-interner Pfad, gemountet über MUSIC_LIBRARY_FOLDER in .env."),
        "en": ("Root folder for the music library mode (play/stop on the /musik page) "
               "— container-internal path, mounted via MUSIC_LIBRARY_FOLDER in .env."),
    },
    "cfg_music_library_folder_label": {"de": "Musik-Ordner (Container-Pfad)",
                                        "en": "Music folder (container path)"},
    "cfg_music_library_saved": {"de": "🎵 Musiksammlung-Einstellungen gespeichert.",
                                 "en": "🎵 Music library settings saved."},

    # ---- gemeinsam: Breadcrumb-Ordner-Browser (News-Break UND
    # Musiksammlung, siehe folder_browse.py/CLAUDE.md) ----
    "cfg_folder_selected": {"de": "Ausgewählt: {path}", "en": "Selected: {path}"},
    "cfg_folder_error": {"de": "⚠ Nicht lesbar: {msg}", "en": "⚠ Not readable: {msg}"},
    "cfg_folder_empty": {"de": "(keine Unterordner)", "en": "(no subfolders)"},

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
               '--build radiosabbelnich</code>), nicht sofort wie die meisten anderen '
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
               '--build radiosabbelnich</code>), not immediately like most other '
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
               'gerade zusammenhängender Text in der jeweils erwarteten Sprache '
               'zu hören ist (echte Moderation) oder nicht (auch in dieser Sprache '
               'gesungene Musik zählt dann als "keine Sprache") — ergänzt '
               'VAD/Heuristik, die reinen Gesang oft fälschlich als Sprache werten. '
               '<strong>Vosk</strong> ist leichtgewichtig und Pi-tauglich, '
               '<strong>Whisper</strong> genauer, aber deutlich ressourcenhungriger. '
               'Welche Sprache für welchen Sender gilt, wird unten über die '
               'Sender-Kategorie festgelegt.'),
        "en": ('Additional signal via speech-to-text: detects whether '
               'coherent speech in the respectively expected language is '
               'currently audible (actual presenting) or not (music sung in '
               'that language also counts as "no speech" then) — complements '
               'VAD/heuristic, which often misjudges pure singing as speech. '
               '<strong>Vosk</strong> is lightweight and Pi-friendly, '
               '<strong>Whisper</strong> more accurate but noticeably more '
               'resource-hungry. Which language applies to which station is set '
               'below via the station category.'),
    },
    "cfg_stt_status_loading": {"de": "Lade Status …", "en": "Loading status …"},
    "cfg_stt_engine_label": {"de": "Engine", "en": "Engine"},
    "cfg_stt_engine_vosk_option": {"de": "Vosk (leichtgewicht, Pi-tauglich)",
                                    "en": "Vosk (lightweight, Pi-friendly)"},
    "cfg_stt_engine_whisper_option": {"de": "Whisper (genauer, ressourcenhungriger)",
                                       "en": "Whisper (more accurate, more resource-hungry)"},
    "cfg_stt_whisper_size_label": {"de": "Whisper-Modellgröße", "en": "Whisper model size"},
    "cfg_stt_interval_label": {"de": "Sample-Intervall (Sekunden)", "en": "Sample interval (seconds)"},
    "cfg_stt_combine_label": {"de": "Verknüpfung mit VAD/Heuristik", "en": "Combination with VAD/heuristic"},
    "cfg_stt_combine_and_option": {"de": 'UND — beide müssen "Sprache" sagen (empfohlen)',
                                    "en": 'AND — both must say "speech" (recommended)'},
    "cfg_stt_combine_or_option": {"de": "ODER — eines reicht", "en": "OR — either is enough"},

    "cfg_stt_lang_heading": {"de": "🌐 STT-Sprachen", "en": "🌐 STT languages"},
    "cfg_stt_lang_hint": {
        "de": ('Pro Sprache ein Vosk-Modellpfad (nur bei Engine "Vosk" relevant, '
               'jede Sprache braucht ein eigenes Modell) und eine empirisch '
               'ermittelte Konfidenz-Schwelle (siehe README). Ein bereits '
               'vorhandener Sprachcode wird beim erneuten Eintragen aktualisiert '
               'statt doppelt angelegt.'),
        "en": ('One Vosk model path per language (only relevant with engine '
               '"Vosk" — each language needs its own model) and an empirically '
               'determined confidence threshold (see README). Entering an '
               'existing language code again updates it instead of duplicating it.'),
    },
    "cfg_stt_lang_col_code": {"de": "Sprache", "en": "Language"},
    "cfg_stt_lang_col_vosk_path": {"de": "Vosk-Modellpfad", "en": "Vosk model path"},
    "cfg_stt_lang_col_threshold": {"de": "Schwelle", "en": "Threshold"},
    "cfg_stt_lang_col_status": {"de": "Status", "en": "Status"},
    "cfg_stt_lang_code_placeholder": {"de": "z.B. en", "en": "e.g. en"},
    "cfg_stt_lang_add_btn": {"de": "+ Sprache hinzufügen/aktualisieren", "en": "+ Add/update language"},
    "cfg_stt_lang_status_unknown": {"de": "noch nicht geladen", "en": "not loaded yet"},
    "cfg_stt_lang_status_ok": {"de": "✅ geladen", "en": "✅ loaded"},
    "cfg_stt_lang_status_error": {"de": "⚠ {error}", "en": "⚠ {error}"},
    "cfg_stt_lang_saved": {"de": "Sprache gespeichert.", "en": "Language saved."},
    "cfg_stt_lang_deleted": {"de": "Sprache gelöscht.", "en": "Language deleted."},
    "cfg_stt_lang_delete_confirm": {
        "de": "Sprache '{code}' wirklich löschen? Kategorien, die ihr zugeordnet sind, "
              "fallen danach auf Deutsch zurück.",
        "en": "Really delete language '{code}'? Categories assigned to it will fall "
              "back to German afterwards.",
    },

    "cfg_stt_cat_lang_heading": {"de": "🏷 Kategorie-Sprachen", "en": "🏷 Category languages"},
    "cfg_stt_cat_lang_hint": {
        "de": ('Legt fest, welche der oben konfigurierten Sprachen für Sender welcher '
               'Kategorie geprüft wird. Kategorien ohne Auswahl gelten als Deutsch.'),
        "en": ('Sets which of the languages configured above is checked for stations '
               'of which category. Categories without a selection default to German.'),
    },
    "cfg_stt_cat_lang_default": {"de": "(Standard: Deutsch)", "en": "(default: German)"},
    "cfg_stt_cat_lang_saved": {"de": "Kategorie-Sprache gespeichert.", "en": "Category language saved."},

    "cfg_stt_calib_heading": {"de": "🧪 Schwellwert-Kalibrierung", "en": "🧪 Threshold calibration"},
    "cfg_stt_calib_hint": {
        "de": ('Ermittelt einen Vorschlag für <code>confidence_threshold</code> einer Sprache, '
               'nach derselben Methode wie die ursprüngliche Deutsch-Kalibrierung (siehe README): '
               'erst ein paar Minuten einen Sender mit garantiert echtem Sprachtext dieser Sprache '
               'mithören lassen, dann einen Musiksender derselben Sprache. Sender dafür manuell auf '
               'der <a href="/">Player-Seite</a> auswählen — die Kalibrierung selbst schaltet nichts '
               'um. Voraussetzung: STT-Filter und Sabbelfilter oben sind aktiv. Für Vosk muss die '
               'Sprache mit Modellpfad bereits in "🌐 STT-Sprachen" angelegt sein (bei Whisper nicht '
               'nötig).'),
        "en": ('Determines a suggestion for a language\'s <code>confidence_threshold</code>, using '
               'the same method as the original German calibration (see README): first listen in on '
               'a station with guaranteed real speech in that language for a few minutes, then a '
               'music station in the same language. Select stations for this manually on the '
               '<a href="/">player page</a> — calibration itself never switches anything. '
               'Requirement: the STT filter and chatter filter above must be active. For Vosk, the '
               'language must already be set up with a model path under "🌐 STT-Sprachen" (not '
               'needed for Whisper).'),
    },
    "cfg_stt_calib_start_btn": {"de": "🧪 Kalibrierung starten", "en": "🧪 Start calibration"},
    "cfg_stt_calib_active_label": {"de": "Kalibriere:", "en": "Calibrating:"},
    "cfg_stt_calib_stage_speech_btn": {"de": "🗣 Sprache-Stufe", "en": "🗣 Speech stage"},
    "cfg_stt_calib_stage_music_btn": {"de": "🎵 Musik-Stufe", "en": "🎵 Music stage"},
    "cfg_stt_calib_stop_btn": {"de": "Abbrechen", "en": "Cancel"},
    "cfg_stt_calib_col_speech": {"de": "🗣 Sprache-Samples", "en": "🗣 Speech samples"},
    "cfg_stt_calib_col_music": {"de": "🎵 Musik-Samples", "en": "🎵 Music samples"},
    "cfg_stt_calib_no_samples": {"de": "noch keine Samples", "en": "no samples yet"},
    "cfg_stt_calib_summary": {
        "de": "{count} Sample(s), Konfidenz min {min} / max {max} / Ø {mean}",
        "en": "{count} sample(s), confidence min {min} / max {max} / avg {mean}",
    },
    "cfg_stt_calib_suggestion_clean": {
        "de": "✅ Vorschlag: {threshold} (Sprache und Musik trennen sauber im gemessenen Sample)",
        "en": "✅ Suggestion: {threshold} (speech and music separate cleanly in the measured sample)",
    },
    "cfg_stt_calib_suggestion_warn": {
        "de": "⚠ Vorschlag: {threshold} — Sprache und Musik überlappen sich im gemessenen Sample, "
              "kein sauberer Trennwert gefunden. Mehr Samples sammeln oder Sender prüfen.",
        "en": "⚠ Suggestion: {threshold} — speech and music overlap in the measured sample, no clean "
              "separating value found. Collect more samples or check the stations.",
    },
    "cfg_stt_calib_apply_btn": {"de": "Übernehmen", "en": "Apply"},
    "cfg_stt_calib_applied": {"de": "Schwellwert übernommen.", "en": "Threshold applied."},
    "cfg_stt_calib_samples_summary": {"de": "Letzte Samples anzeigen", "en": "Show recent samples"},
    "cfg_stt_calib_no_text": {"de": "(kein Text erkannt)", "en": "(no text detected)"},
    "cfg_stt_calib_lang_required": {"de": "Sprachcode darf nicht leer sein.", "en": "Language code must not be empty."},

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
        "de": "Aktueller Verbrauch von RadioSabbelNich selbst (nicht des Hosts), alle 5 Sekunden aktualisiert.",
        "en": "Current usage of RadioSabbelNich itself (not the host), refreshed every 5 seconds.",
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
               'docker compose up -d --build radiosabbelnich zeigt ihn danach an).'),
        "en": ("Host path unknown (container hasn't run with this display yet -- "
               "docker compose up -d --build radiosabbelnich will show it afterwards)."),
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
