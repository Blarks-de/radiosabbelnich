#!/usr/bin/env python3
# Copyright (C) 2026 RadioSabbelNich
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 (or
# later), as published by the Free Software Foundation. See LICENSE.

"""
vosk_catalog.py — statische, kuratierte Liste bekannter Vosk-Modelle für
den Download-Knopf in der "🌐 STT-Sprachen"-Tabelle (siehe SESSION.md).

Warum statisch statt live von alphacephei.com/vosk/models abgefragt: die
Seite liefert nur eine HTML-Tabelle, keinen JSON-Index (per WebFetch
geprüft) -- Scraping wäre brüchig (bricht lautlos bei einer
Layout-Änderung) und würde bei jedem Seitenaufruf einen externen Request
erzwingen. Eine statische Liste veraltet zwar mit der Zeit (Vosk
aktualisiert Modell-Versionen gelegentlich), das ist aber derselbe
akzeptierte Trade-off wie bei anderen kuratierten Listen im Projekt
(z.B. stations_store.CATEGORIES) -- Pflege heißt: diese Liste hier von
Hand nachziehen, siehe README für den Verweis auf die Originalquelle.

Pro Sprache maximal zwei Einträge (klein/groß, "variant"), nur offizielle
Kaldi-Team-Modelle (keine Community-Forks wie *-daanzu-/-zamia-/-tuda-),
damit die Liste überschaubar UND die URLs stabil bleiben. `key` ist die
eindeutige ID für einen Katalog-Eintrag (Sprachcode+Variante, z.B.
"en-small") -- NICHT identisch mit dem Sprachcode, weil ein Sprachcode in
stt_filter.languages nur EINEN Modellpfad gleichzeitig halten kann,
small/big derselben Sprache aber unterschiedliche Katalog-Einträge sind.
`size_mb` ist eine grobe Schätzung (Angaben der Modellseite, G≈1000MB
gerundet) für den Speicherplatz-Check in vosk_download.py -- keine exakte
Content-Length-Ersatz, nur ausreichend fürs Vorab-Abschätzen.
"""

MODEL_BASE_URL = "https://alphacephei.com/vosk/models"

# (code, variant, model_filename ohne .zip, size_mb, Anzeigename)
_ENTRIES = [
    ("en", "small", "vosk-model-small-en-us-0.15", 40, "Englisch (klein)"),
    ("en", "big", "vosk-model-en-us-0.22", 1800, "Englisch (groß, genauer)"),
    ("de", "small", "vosk-model-small-de-0.15", 45, "Deutsch (klein)"),
    ("de", "big", "vosk-model-de-0.21", 1900, "Deutsch (groß, genauer)"),
    ("fr", "small", "vosk-model-small-fr-0.22", 41, "Französisch (klein)"),
    ("fr", "big", "vosk-model-fr-0.22", 1400, "Französisch (groß, genauer)"),
    ("es", "small", "vosk-model-small-es-0.42", 39, "Spanisch (klein)"),
    ("es", "big", "vosk-model-es-0.42", 1400, "Spanisch (groß, genauer)"),
    ("it", "small", "vosk-model-small-it-0.22", 48, "Italienisch (klein)"),
    ("it", "big", "vosk-model-it-0.22", 1200, "Italienisch (groß, genauer)"),
    ("pt", "small", "vosk-model-small-pt-0.3", 31, "Portugiesisch (klein)"),
    ("nl", "small", "vosk-model-small-nl-0.22", 39, "Niederländisch (klein)"),
    ("nl", "big", "vosk-model-nl-spraakherkenning-0.6", 860, "Niederländisch (groß, genauer)"),
    ("ru", "small", "vosk-model-small-ru-0.22", 45, "Russisch (klein)"),
    ("ru", "big", "vosk-model-ru-0.22", 1500, "Russisch (groß, genauer)"),
    ("zh", "small", "vosk-model-small-cn-0.22", 42, "Chinesisch (klein)"),
    ("zh", "big", "vosk-model-cn-0.22", 1300, "Chinesisch (groß, genauer)"),
    ("tr", "small", "vosk-model-small-tr-0.3", 35, "Türkisch (klein)"),
    ("vi", "small", "vosk-model-small-vn-0.4", 32, "Vietnamesisch (klein)"),
    ("vi", "big", "vosk-model-vn-0.4", 78, "Vietnamesisch (groß, genauer)"),
    ("ca", "small", "vosk-model-small-ca-0.4", 42, "Katalanisch (klein)"),
    ("ar", "big", "vosk-model-ar-mgb2-0.4", 318, "Arabisch"),
    ("fa", "small", "vosk-model-small-fa-0.42", 53, "Farsi (klein)"),
    ("fa", "big", "vosk-model-fa-0.42", 1600, "Farsi (groß, genauer)"),
    ("uk", "small", "vosk-model-small-uk-v3-small", 133, "Ukrainisch (klein)"),
    ("uk", "big", "vosk-model-uk-v3", 343, "Ukrainisch (groß, genauer)"),
    ("sv", "small", "vosk-model-small-sv-rhasspy-0.15", 289, "Schwedisch"),
    ("ja", "small", "vosk-model-small-ja-0.22", 48, "Japanisch (klein)"),
    ("ja", "big", "vosk-model-ja-0.22", 1000, "Japanisch (groß, genauer)"),
    ("eo", "small", "vosk-model-small-eo-0.42", 42, "Esperanto"),
    ("hi", "small", "vosk-model-small-hi-0.22", 42, "Hindi (klein)"),
    ("hi", "big", "vosk-model-hi-0.22", 1500, "Hindi (groß, genauer)"),
    ("cs", "small", "vosk-model-small-cs-0.4-rhasspy", 44, "Tschechisch"),
    ("pl", "small", "vosk-model-small-pl-0.22", 50, "Polnisch"),
    ("uz", "small", "vosk-model-small-uz-0.22", 49, "Usbekisch"),
    ("ko", "small", "vosk-model-small-ko-0.22", 82, "Koreanisch"),
    ("el", "big", "vosk-model-el-gr-0.7", 1100, "Griechisch"),
    ("kk", "small", "vosk-model-small-kz-0.42", 58, "Kasachisch (klein)"),
    ("kk", "big", "vosk-model-kz-0.42", 1300, "Kasachisch (groß, genauer)"),
    ("ky", "small", "vosk-model-small-ky-0.42", 49, "Kirgisisch (klein)"),
    ("ky", "big", "vosk-model-ky-0.42", 1100, "Kirgisisch (groß, genauer)"),
    ("gu", "small", "vosk-model-small-gu-0.42", 100, "Gujarati (klein)"),
    ("gu", "big", "vosk-model-gu-0.42", 700, "Gujarati (groß, genauer)"),
    ("tg", "small", "vosk-model-small-tg-0.22", 50, "Tadschikisch (klein)"),
    ("tg", "big", "vosk-model-tg-0.22", 327, "Tadschikisch (groß, genauer)"),
    ("te", "small", "vosk-model-small-te-0.42", 58, "Telugu"),
    ("br", "small", "vosk-model-br-0.8", 70, "Bretonisch"),
    ("tl", "big", "vosk-model-tl-ph-generic-0.6", 320, "Filipino"),
]

CATALOG = [
    {
        "key": f"{code}-{variant}",
        "code": code,
        "variant": variant,
        "display_name": display_name,
        "url": f"{MODEL_BASE_URL}/{filename}.zip",
        "size_mb": size_mb,
    }
    for code, variant, filename, size_mb, display_name in _ENTRIES
]

CATALOG_BY_KEY = {entry["key"]: entry for entry in CATALOG}


def available_entries(installed_codes) -> list:
    """Katalog-Einträge, deren Sprachcode NOCH NICHT unter
    stt_filter.languages konfiguriert ist (siehe settings_store.py) --
    genau die Auswahl fürs Download-Dropdown. `installed_codes` ist
    typischerweise settings_store.load()["stt_filter"]["languages"].keys()."""
    installed = set(installed_codes)
    return [entry for entry in CATALOG if entry["code"] not in installed]
