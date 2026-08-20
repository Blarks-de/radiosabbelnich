#!/usr/bin/env python3
"""
i18n.py — Übersetzungstabelle für die Benutzerdialoge des Web-Interfaces
(_PAGE_HTML/_CONFIG_PAGE_HTML/_MUSIC_PAGE_HTML in webui.py). Reine
Domänenlogik ohne Bezug zu StreamSource/SwitcherState, analog zu
news_break.py/stt_filter.py.

Übersetzt wird NUR, was der Nutzer im Browser sieht (Labels, Buttons,
Überschriften, alert()/confirm()-Dialoge, dynamische Statuszeilen). Log-
Meldungen, Code-Kommentare und die vom Backend geworfenen ValueError-Texte
(settings_store.py/station_import.py/stations_store.py) bleiben bewusst
deutsch -- siehe CLAUDE.md ("Alles auf Deutsch" für Code/Logs) und SESSION.md
(Scope-Entscheidung beim Einbau dieses Features).

Seit 2026-08-12 (Umstellung Basissprache): Englisch ist die im Code
eingebettete Basis-/Fallback-Sprache (_BASE_STRINGS unten, IMMER vollständig
-- jeder in den Templates verwendete Key muss hier existieren, siehe
_check_i18n_coverage() in webui.py). Weitere Sprachen (z.B. Deutsch) kommen
als externe "Sprachpakete" aus language/*.lng (analog zu Windows-
Sprachpaketen: eine Datei pro Sprache, Klartext-Format, ohne Neubau des
Basis-Codes nachrüstbar -- Rebuild des Docker-Images reicht). Eine .lng-Datei
muss NICHT vollständig sein: fehlt ein Key, fällt genau dieser Key für diese
Sprache automatisch auf die englische Basis zurück (siehe _discover_languages()
unten) -- kein hartes Scheitern wie früher, als "de" UND "en" für jeden Key
zwingend vorhanden sein mussten.

STRINGS bleibt nach außen (webui.py) unverändert nach Key gruppiert
(STRINGS[key][lang_code]), damit _render_i18n_variants()/_check_i18n_coverage()
in webui.py ohne Änderung weiterlaufen -- nur wie STRINGS/LANGUAGES intern
zustande kommen, hat sich geändert (Code+Datei-Merge statt reinem
Code-Dict).
"""

import glob
import logging
import os

log = logging.getLogger("i18n")

# Wie bei web/qrcode.js & Co.: __file__-relativ, weil im Container alles
# unterhalb von /app/ landet (siehe CLAUDE.md, "Host-Layout und
# Container-Layout") -- language/ ist hier bewusst die EINE Ausnahme von
# "jede Datei einzeln per COPY", weil _discover_languages() unten den
# ganzen Ordner zur Laufzeit durchsucht (glob): eine neue .lng-Datei soll
# durch bloßes Ablegen + Rebuild wirken, ohne dass zusätzlich noch eine
# COPY-Zeile im Dockerfile ergänzt werden muss (dort deshalb ein einziges
# "COPY language/ language/" statt einer Zeile pro Datei, siehe Dockerfile).
LANGUAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "language")

# ---------------------------------------------------------------------------
# Englische Basis -- IMMER vollständig, das garantiert _check_i18n_coverage()
# in webui.py beim Modul-Import (prüft nur noch gegen diese eine Sprache,
# nicht mehr gegen alle). Reihenfolge/Gruppierung folgt den Seiten, auf denen
# die Keys verwendet werden (siehe Abschnittskommentare) -- rein zur
# Lesbarkeit, ohne Bedeutung fürs Programm.
# ---------------------------------------------------------------------------
_BASE_STRINGS = {
    # ---- Gemeinsame Texte (Player- und Config-Seite) ----
    'common_save': 'Save',
    'common_cancel': 'Cancel',
    'common_edit': 'Edit',
    'common_delete': 'Delete',
    'common_error': 'Error: {msg}',
    'common_loading': 'Loading …',
    'common_unknown': 'unknown',

    # ---- Player-Seite (Startseite) ----
    'idx_prev_title': 'Previous station',
    'idx_prev_btn': '⏮ Back',
    'idx_next_title': 'Next station',
    'idx_next_btn': 'Next ⏭',
    'idx_qr_vlc_title': 'QR code for the stream address (VLC & co.)',
    'idx_qr_vlc_label': 'VLC Stream',
    'idx_qr_phone_title': 'QR code for this web interface (to open on your phone)',
    'idx_qr_phone_label': 'Phone Remote',
    'idx_qr_modal_close_aria': 'Close',
    'idx_qr_modal_title': 'Address to scan',
    'idx_qr_modal_copy_btn': '📋 Copy address',
    'idx_zapping_error_title': 'Delete the last incorrectly detected ad clip from the database',
    'idx_zapping_error_btn': '🛑 Zap error',
    'idx_gesabbel_title': "Switch immediately because someone's talking right now",
    'idx_gesabbel_btn': '⚡ ZAP!',
    'idx_filter_toggle_title': 'Pause/resume automatic speech detection entirely',
    'idx_filter_disable_btn': 'Disable chatter filter',
    'idx_filter_enable_btn': 'Enable chatter filter',
    'idx_bs_meter_label': '🤥 Bullshit-o-meter',
    'idx_vu_meter_label': '🔊 Level',
    'idx_stt_meter_label': '🗣 STT (speech-to-text) filter',
    'idx_stt_meter_off': 'STT off',
    'idx_fp_indicator_label': '🔎 Fingerprint',
    'idx_fp_state_idle': '⚪ Idle',
    'idx_fp_state_match': '🔴 Match: {label}',
    'idx_fp_state_learned': '🟢 Learned',
    'idx_fp_last_learned': 'Last learned: {time}',
    'idx_fp_last_match': 'Last recognized: {time}',
    'idx_fp_never': 'never',
    'idx_stations_heading': 'Stations',
    'idx_listeners_heading': 'Listeners',
    'idx_config_link': '⚙ Manage stations',
    'idx_connection_lost': 'Connection to server lost …',
    'idx_current_playing': '▶ Now playing: {name}',
    'idx_no_station_active': 'No station active',
    'idx_news_break': '📰 Break',
    'idx_filter_off': 'Filter off',
    'idx_listeners_unavailable': 'Listener info unavailable.',
    'idx_listeners_none': 'No listeners currently connected.',
    'idx_listeners_col_ip': 'IP',
    'idx_listeners_col_since': 'Connected since',
    'idx_listeners_col_client': 'Client',
    'idx_meta_updated': 'Updated: {time}',
    'idx_qr_vlc_modal_title': '▶️ Stream address to scan (VLC & co.)',
    'idx_qr_phone_modal_title': '📱 Web interface address to scan',
    'idx_address_copied': '📋 Address copied.',
    'idx_copy_failed': 'Copy failed: {msg}',
    'idx_clip_deleted': '✓ Clip deleted',
    'idx_switched_back_to': ' — back to {name}',
    'idx_zap_switching': '🗣️ Switching …',
    'idx_filter_switching': 'Toggling chatter filter …',
    'idx_music_mode_active': '🎵 Player active — radio paused',
    'idx_music_mode_short': '🎵 Music',

    # ---- Radio/Musiksammlung-Modus-Umschalter ----
    'mode_radio_btn': '📻 Radio',
    'mode_music_btn': '🎵 Player',
    'idx_mode_switching': 'Switching mode …',

    # ---- Musiksammlung-Seite (/musik) ----
    'music_heading': '🎵 Player',
    'music_root_label': 'Music folder:',
    'music_root_change_link': 'Change path',
    'music_categories_heading': 'Categories',
    'music_favorites_heading': 'Favorites',
    'music_query_failed': 'Query failed.',
    'music_play_title': 'Play',
    'music_stop_title': 'Stop',
    'music_prev_title': 'Previous track',
    'music_next_title': 'Next track',
    'music_now_playing': '🎵 {file} ({index}/{total})',
    'music_idle': 'Ready — tap ▶ to play.',
    'music_switch_hint': 'Switch to 🎵 Player above first.',
    'music_back_link': 'back to the radio player',

    # ---- Config-Seite (/config) ----
    'cfg_back_link': '← back to player',
    'cfg_heading': '⚙ Manage stations',
    'cfg_news_break_heading': '📰 News break',
    'cfg_news_break_hint': 'Plays a random local MP3 instead of a radio station on the hour/half hour. The folder below lives under a <strong>container-internal path</strong> — the actual host folder (e.g. an SMB mount) is mounted via <code>NEWS_MP3_FOLDER</code> in <code>.env</code> to <code>/app/news_mp3</code> and needs a restart of the container for that. Click below to pick the subfolder you want.',
    'cfg_active_label': 'enabled',
    'cfg_nb_folder_label': 'MP3 folder (container path)',
    'cfg_nb_window_label': 'Time window (minutes)',
    'cfg_nb_hours_enabled_label': 'only active during certain hours',
    'cfg_nb_hour_start_label': 'from hour',
    'cfg_nb_hour_end_label': 'to hour',
    'cfg_music_library_heading': '🎵 Player',
    'cfg_music_library_hint': 'Root folder for the player mode (play/stop on the /musik page) — container-internal path, mounted via MUSIC_LIBRARY_FOLDER in .env.',
    'cfg_music_library_folder_label': 'Music folder (container path)',
    'cfg_music_library_saved': '🎵 Player settings saved.',
    'cfg_new_station_heading': 'New station',
    'cfg_add_name_placeholder': 'Name',
    'cfg_add_url_placeholder': 'Stream URL (https://...)',
    'cfg_enabled_label': 'enabled',
    'cfg_add_btn': 'Add',
    'cfg_import_heading': '📻 Station import',
    'cfg_import_hint': 'Downloads an M3U playlist and listens to each station for a few seconds: only stations that deliver audio continuously (not just when connecting) are kept. New stations land <strong>disabled</strong> in the "Unsorted" category — you decide via checkbox who joins the rotation. Can take a few minutes for a long list.',
    'cfg_import_url_label': 'Playlist URL',
    'cfg_import_btn': 'Import stations',
    'cfg_stream_heading': '🔗 Streaming address',
    'cfg_stream_hint': 'Address shown on the home page under "Streaming via VLC" (to enter into an external player). Leave empty to derive it automatically from the address the home page is currently being accessed with in the browser.',
    'cfg_stream_url_label': 'Stream URL',
    'cfg_tls_heading': '🔒 HTTPS',
    'cfg_tls_hint': 'Encrypts access to the web interface (player page and this config page) via TLS. Needs a certificate at <code>TLS_CERT_FILE</code>/<code>TLS_KEY_FILE</code> in <code>.env</code> (host paths to PEM files, e.g. generated via <code>tailscale cert</code>) — without those, this checkbox has no effect and the web interface keeps running over HTTP. <strong>Only takes effect after restarting the container</strong> (<code>docker compose up -d --build radiosabbelnich</code>), not immediately like most other settings here. The Icecast stream itself independently gets an additional HTTPS port automatically as soon as the same certificates are set in <code>.env</code> — there is no separate switch for that.',
    'cfg_tls_checkbox_label': 'HTTPS for the web interface enabled',
    'cfg_buffer_heading': '⏱ Buffer settings',
    'cfg_buffer_hint': 'The next stations in rotation order run in the background and buffer audio so switches happen smoothly. More seconds/stations = smoother, but more bandwidth/CPU.',
    'cfg_buffer_seconds_label': 'Seconds per buffered station',
    'cfg_buffer_count_label': 'Number of pre-buffered stations',
    'cfg_stt_heading': '🗣 STT speech filter',
    'cfg_stt_hint': 'Additional signal via speech-to-text: detects whether coherent speech in the respectively expected language is currently audible (actual presenting) or not (music sung in that language also counts as "no speech" then) — complements VAD/heuristic, which often misjudges pure singing as speech. <strong>Vosk</strong> is lightweight and Pi-friendly, <strong>Whisper</strong> more accurate but noticeably more resource-hungry. Which language applies to which station is set below via the station category.',
    'cfg_stt_status_loading': 'Loading status …',
    'cfg_stt_engine_label': 'Engine',
    'cfg_stt_engine_vosk_option': 'Vosk (lightweight, Pi-friendly)',
    'cfg_stt_engine_whisper_option': 'Whisper (more accurate, more resource-hungry)',
    'cfg_stt_whisper_size_label': 'Whisper model size',
    'cfg_stt_interval_label': 'Sample interval (seconds)',
    'cfg_stt_combine_label': 'Combination with VAD/heuristic',
    'cfg_stt_combine_and_option': 'AND — both must say "speech" (recommended)',
    'cfg_stt_combine_or_option': 'OR — either is enough',
    'cfg_stt_lang_heading': '🌐 STT languages',
    'cfg_stt_lang_hint': 'One Vosk model path per language (only relevant with engine "Vosk" — each language needs its own model) and an empirically determined confidence threshold (see README). Entering an existing language code again updates it instead of duplicating it.',
    'cfg_stt_lang_col_code': 'Language',
    'cfg_stt_lang_col_vosk_path': 'Vosk model path',
    'cfg_stt_lang_col_threshold': 'Threshold',
    'cfg_stt_lang_col_status': 'Status',
    'cfg_stt_lang_code_placeholder': 'e.g. en',
    'cfg_stt_lang_add_btn': '+ Add/update language',
    'cfg_stt_lang_status_unknown': 'not loaded yet',
    'cfg_stt_lang_status_ok': '✅ loaded',
    'cfg_stt_lang_status_error': '⚠ {error}',
    'cfg_stt_lang_saved': 'Language saved.',
    'cfg_stt_lang_deleted': 'Language deleted.',
    'cfg_stt_lang_delete_confirm': "Really delete language '{code}'? Categories assigned to it will fall back to German afterwards.",
    'cfg_stt_cat_lang_heading': '🏷 Category languages',
    'cfg_stt_cat_lang_hint': 'Sets which of the languages configured above is checked for stations of which category. Categories without a selection default to German.',
    'cfg_stt_cat_lang_default': '(default: German)',
    'cfg_stt_cat_lang_saved': 'Category language saved.',
    'cfg_stt_calib_heading': '🧪 Threshold calibration',
    'cfg_stt_calib_hint': 'Determines a suggestion for a language\'s <code>confidence_threshold</code>, using the same method as the original German calibration (see README): first listen in on a station with guaranteed real speech in that language for a few minutes, then a music station in the same language. Select stations for this manually on the <a href="/">player page</a> — calibration itself never switches anything. Requirement: the STT filter and chatter filter above must be active. For Vosk, the language must already be set up with a model path under "🌐 STT-Sprachen" (not needed for Whisper).',
    'cfg_stt_calib_start_btn': '🧪 Start calibration',
    'cfg_stt_calib_active_label': 'Calibrating:',
    'cfg_stt_calib_stage_speech_btn': '🗣 Speech stage',
    'cfg_stt_calib_stage_music_btn': '🎵 Music stage',
    'cfg_stt_calib_stop_btn': 'Cancel',
    'cfg_stt_calib_col_speech': '🗣 Speech samples',
    'cfg_stt_calib_col_music': '🎵 Music samples',
    'cfg_stt_calib_no_samples': 'no samples yet',
    'cfg_stt_calib_summary': '{count} sample(s), confidence min {min} / max {max} / avg {mean}',
    'cfg_stt_calib_suggestion_clean': '✅ Suggestion: {threshold} (speech and music separate cleanly in the measured sample)',
    'cfg_stt_calib_suggestion_warn': '⚠ Suggestion: {threshold} — speech and music overlap in the measured sample, no clean separating value found. Collect more samples or check the stations.',
    'cfg_stt_calib_apply_btn': 'Apply',
    'cfg_stt_calib_applied': 'Threshold applied.',
    'cfg_stt_calib_samples_summary': 'Show recent samples',
    'cfg_stt_calib_no_text': '(no text detected)',
    'cfg_stt_calib_lang_required': 'Language code must not be empty.',
    'cfg_fingerprint_heading': '🗑 Fingerprint database',
    'cfg_fingerprint_hint': 'Deletes all learned jingle/ad clips (not the station list). Detection then starts learning from scratch again.',
    'cfg_fingerprint_clear_btn': 'Clear clip DB',
    'cfg_resources_heading': '💾 Resource usage',
    'cfg_resources_hint': 'Current usage of RadioSabbelNich itself (not the host), refreshed every 5 seconds.',
    'cfg_resources_ram_total': 'Total RAM',
    'cfg_resources_ram_breakdown': 'of which Python / ffmpeg',
    'cfg_resources_cpu_total': 'Total CPU',
    'cfg_resources_ffmpeg_count': 'Running ffmpeg processes',
    'cfg_resources_fingerprint_db': 'Fingerprint DB',
    'cfg_resources_log': 'Log file (incl. rotation)',
    'cfg_resources_whisper_cache': 'Whisper model cache',
    'cfg_language_heading': '🌐 Language',
    'cfg_language_hint': 'Language of the web interface (player and config page). Takes effect immediately for new page loads; this page reloads automatically after saving. The initial value comes from <code>UI_LANGUAGE</code> in <code>.env</code>, after that the setting saved here always wins.',
    'cfg_language_label': 'Interface language',

    # ---- Breadcrumb-Ordner-Browser (News-Break und Musiksammlung) ----
    'cfg_folder_selected': 'Selected: {path}',
    'cfg_folder_error': '⚠ Not readable: {msg}',
    'cfg_folder_empty': '(no subfolders)',

    # ---- Config-Seite: dynamische Meldungen (JavaScript) ----
    'cfg_load_stations_failed': 'Could not load station list: {msg}',
    'cfg_no_stations_in_category': 'No stations in this category.',
    'cfg_disable_all_btn': 'Disable all',
    'cfg_disable_all_title': 'Disable all {count} enabled stations in "{cat}"',
    'cfg_disable_all_confirm': 'Really disable all {count} enabled stations in "{cat}"?',
    'cfg_disable_all_done': '{count} stations in "{cat}" disabled.',
    'cfg_saved': 'Saved.',
    'cfg_field_url_placeholder': 'Stream URL',
    'cfg_delete_confirm': 'Really delete "{name}"?',
    'cfg_deleted': 'Deleted.',
    'cfg_added': 'Added.',
    'cfg_load_settings_failed': 'Could not load settings: {msg}',
    'cfg_stt_status_disabled': 'Status: disabled.',
    'cfg_stt_status_active': 'Status: ✅ {engine} active.',
    'cfg_stt_status_error': 'Status: ⚠ disabled ({error}).',
    'cfg_stt_status_model_not_loadable': 'model not loadable',
    'cfg_buffer_saved': 'Buffer settings saved.',
    'cfg_stream_saved': 'Streaming address saved.',
    'cfg_tls_saved': 'HTTPS setting saved — takes effect only after restarting the container.',
    'cfg_news_break_saved': 'News break saved.',
    'cfg_stt_saved': 'STT speech filter saved.',
    'cfg_import_progress_error': 'Error querying progress: {msg}',
    'cfg_import_loading_playlist': 'Loading playlist …',
    'cfg_import_checking': 'Checking stations … {checked} of {total}',
    'cfg_import_failed': 'Import failed: {error}',
    'cfg_import_result': '{checked} stations checked, {working} deliver audio continuously, {added} new (disabled) in "Unsorted" — check the box to enable.',
    'cfg_import_starting': 'Starting import …',
    'cfg_fingerprint_clear_confirm': 'Really delete ALL learned fingerprint clips? This cannot be undone.',
    'cfg_fingerprint_cleared': '{cleared} clip(s) deleted from the fingerprint database.',
    'cfg_invalid_response': 'Invalid response from server',
    'cfg_host_path_mounted': '📁 Currently mounted from host path: {path} (change via {envVar} in .env + restart the container)',
    'cfg_host_path_unknown': "Host path unknown (container hasn't run with this display yet -- docker compose up -d --build radiosabbelnich will show it afterwards).",
    'cfg_language_saved': 'Language saved — reloading page …',
}


def _parse_lng_file(path: str):
    """Liest eine .lng-Datei: Key=Value, eine Zeile pro Eintrag, '#'-Zeilen
    sind Kommentare, '#!'-Zeilen sind reservierte Metadaten (code=.../
    name=...). Wert wird NUR am Zeilenende (\\r\\n) getrimmt, nicht an den
    Rändern -- mindestens ein Wert (idx_switched_back_to) hat ein
    bedeutungstragendes führendes Leerzeichen, weil er im Frontend direkt an
    einen anderen String angehängt wird. Split am ERSTEN '=', Werte dürfen
    also selbst '=' enthalten."""
    meta = {}
    translations = {}
    with open(path, encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            if line.startswith("#!"):
                key, sep, value = line[2:].partition("=")
                if sep:
                    meta[key.strip()] = value.strip()
                continue
            if line.lstrip().startswith("#"):
                continue
            if "=" not in line:
                log.warning("%s:%d: Zeile ohne '=' ignoriert: %r", path, lineno, line)
                continue
            key, _, value = line.partition("=")
            translations[key.strip()] = value
    return meta, translations


def _discover_languages():
    """Baut aus _BASE_STRINGS (immer vollständig) + allen language/*.lng-
    Dateien (dürfen unvollständig sein -- fehlende Keys fallen einzeln auf
    Englisch zurück) das komplette Sprachen-Set. 'en' ist IMMER vorhanden,
    auch ohne jede .lng-Datei (Basissprache direkt im Code, siehe
    Moduldoc) -- language/ fehlt z.B. in einem minimalen Testaufbau ohne
    Probleme."""
    lang_strings = {"en": dict(_BASE_STRINGS)}
    lang_names = {"en": "English"}
    if not os.path.isdir(LANGUAGE_DIR):
        return lang_strings, lang_names
    for path in sorted(glob.glob(os.path.join(LANGUAGE_DIR, "*.lng"))):
        try:
            meta, translations = _parse_lng_file(path)
        except OSError as exc:
            log.warning("⚠ Sprachdatei %s konnte nicht gelesen werden: %s", path, exc)
            continue
        code = meta.get("code", "").strip().lower()
        if not code:
            log.warning("⚠ Sprachdatei %s hat keine '#!code=...'-Zeile -- übersprungen.", path)
            continue
        unknown = sorted(set(translations) - set(_BASE_STRINGS))
        if unknown:
            log.warning(
                "⚠ Sprachdatei %s (%s): %d Key(s) ohne Entsprechung in der englischen Basis "
                "(vermutlich veraltet): %s", path, code, len(unknown), unknown,
            )
        missing = sorted(set(_BASE_STRINGS) - set(translations))
        if missing:
            log.debug(
                "Sprachdatei %s (%s): %d Key(s) fehlen, fallen auf Englisch zurück: %s",
                path, code, len(missing), missing,
            )
        merged = dict(_BASE_STRINGS)
        merged.update({k: v for k, v in translations.items() if k in _BASE_STRINGS})
        lang_strings[code] = merged
        lang_names[code] = meta.get("name", code)
    return lang_strings, lang_names


# _LANG_STRINGS: {code: {key: value}}, jede Sprache garantiert vollständig
# (fehlende Keys wurden beim Merge oben schon aufgefüllt) -- STRINGS/
# LANGUAGES/LANGUAGE_NAMES sind die einzigen drei Namen, die webui.py von
# hier importiert, alle drei bleiben in Form/Bedeutung identisch zum
# Vor-.lng-Stand.
_LANG_STRINGS, LANGUAGE_NAMES = _discover_languages()
LANGUAGES = set(_LANG_STRINGS)

# Wieder nach Key gruppiert (wie vor der .lng-Umstellung) -- _render_i18n_variants()/
# _check_i18n_coverage() in webui.py lesen STRINGS unverändert weiter.
STRINGS = {key: {lang: _LANG_STRINGS[lang][key] for lang in LANGUAGES} for key in _BASE_STRINGS}

_env_lang = os.environ.get("UI_LANGUAGE", "en").strip().lower()
if _env_lang not in LANGUAGES:
    if os.environ.get("UI_LANGUAGE"):
        log.warning(
            "⚠ UI_LANGUAGE=%r ungültig (verfügbar: %s) -- Fallback auf 'en'.",
            _env_lang, sorted(LANGUAGES),
        )
    _env_lang = "en"
DEFAULT_LANGUAGE = _env_lang


def validate():
    """Wirft AssertionError, wenn ein Key nicht in allen geladenen Sprachen
    vorkommt -- durch den Fallback-Merge in _discover_languages() eigentlich
    immer erfüllt, bleibt aber als billiges Sicherheitsnetz gegen
    Programmierfehler in dieser Datei selbst stehen. webui.py gleicht beim
    Modul-Import zusätzlich die tatsächlich in den Templates verwendeten
    Keys gegen STRINGS ab (siehe dortiger _check_i18n_coverage())."""
    for key, variants in STRINGS.items():
        missing = LANGUAGES - set(variants)
        assert not missing, f"i18n.STRINGS[{key!r}] fehlt Sprache(n) {missing}"


validate()
