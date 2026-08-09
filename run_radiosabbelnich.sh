#!/bin/bash
# Startet den RadioSabbelNich-Stack (Icecast + RadioSabbelNich) via docker compose.
#!/usr/bin/env bash
set -e

BAR_WIDTH=28
RED='\033[0;31m'; YELLOW='\033[0;33m'; GREEN='\033[0;32m'; NC='\033[0m'

bar_color() {
  local p=$1
  if   (( p < 60 )); then echo -n "$GREEN"
  elif (( p < 85 )); then echo -n "$YELLOW"
  else                    echo -n "$RED"
  fi
}

draw_bar() {
  local percent=$1
  local filled=$(( percent * BAR_WIDTH / 100 ))
  local empty=$(( BAR_WIDTH - filled ))
  local color; color=$(bar_color "$percent")
  printf "%b[" "$color"
  printf '%0.s█' $(seq 1 $filled) 2>/dev/null
  printf '%0.s░' $(seq 1 $empty) 2>/dev/null
  printf "]%b" "$NC"
}

cd "$(dirname "$0")"

echo "🔍 System-Check vor dem Start"
echo

# --- RAM ---
mem_total_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
mem_avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
mem_used_kb=$(( mem_total_kb - mem_avail_kb ))
mem_total_gb=$(awk "BEGIN{printf \"%.1f\", $mem_total_kb/1024/1024}")
mem_used_gb=$(awk "BEGIN{printf \"%.1f\", $mem_used_kb/1024/1024}")
mem_percent=$(( mem_used_kb * 100 / mem_total_kb ))

printf "🧠 RAM:  %6s GB / %6s GB (%d%%) " "$mem_used_gb" "$mem_total_gb" "$mem_percent"
draw_bar "$mem_percent"
echo

# --- HD ---
read -r disk_total disk_used disk_percent <<< "$(df -BG / | awk 'NR==2 {gsub("G","",$2); gsub("G","",$3); gsub("%","",$5); print $2, $3, $5}')"
printf "💾 HD:   %6s GB / %6s GB (%d%%) " "$disk_used" "$disk_total" "$disk_percent"
draw_bar "$disk_percent"
echo

# --- Internet ---
if curl -s --max-time 3 https://1.1.1.1 -o /dev/null; then
  echo -e "🌐 Internet: ${GREEN}✅ verfügbar${NC}"
else
  echo -e "🌐 Internet: ${RED}❌ nicht verfügbar${NC}"
fi

echo

# --- MP3-Ordner (Nachrichten-Pause) ---
# Bewusst NICHT ".env" selbst parsen (per "source" o.ä.): eine Shell und
# Docker Compose interpretieren z.B. Backslashes in Werten UNTERSCHIEDLICH
# (Shell entfernt "\'" beim Sourcen zu "'", Compose lässt den Backslash
# wörtlich stehen) -- ein per Shell "korrekt" gelesener Pfad kann also genau
# der kaputte Pfad sein, den Docker gleich als Mount-Quelle verwendet und an
# dem "docker compose up" dann mit einem kryptischen "invalid argument"
# scheitert (live erlebt: NEWS_MP3_FOLDER=.../80\'s/ in .env). Deshalb fragen
# wir Compose selbst nach dem aufgelösten Wert -- das ist garantiert exakt
# das, was gleich als Bind-Mount-Quelle verwendet wird.
echo "📻 MP3-Ordner (Nachrichten-Pause)"

if ! compose_config_json=$(docker compose config --format json 2>/dev/null); then
    echo -e "${RED}❌ 'docker compose config' schlägt fehl -- Fehler in docker-compose.yml oder .env:${NC}"
    docker compose config --format json
    exit 1
fi

news_folder_host=$(printf '%s' "$compose_config_json" | python3 -c '
import json, sys
try:
    cfg = json.load(sys.stdin)
    print(cfg["services"]["radiosabbelnich"]["environment"].get("NEWS_MP3_FOLDER_HOST", ""))
except Exception:
    print("")
' 2>/dev/null)

if [ -z "$news_folder_host" ]; then
    echo -e "${YELLOW}⚠️  NEWS_MP3_FOLDER konnte nicht ermittelt werden -- Check übersprungen.${NC}"
elif [ "$news_folder_host" = "./data/news_mp3" ]; then
    echo -e "${YELLOW}⚠️  NEWS_MP3_FOLDER nicht gesetzt (Default './data/news_mp3') -- Nachrichten-Pause bleibt inaktiv, bis dort MP3s liegen oder ein eigener Pfad in .env eingetragen ist.${NC}"
elif [ ! -d "$news_folder_host" ]; then
    echo -e "${RED}❌ NEWS_MP3_FOLDER='$news_folder_host' existiert nicht.${NC}"
    stripped="${news_folder_host//\\/}"
    if [ "$stripped" != "$news_folder_host" ] && [ -d "$stripped" ]; then
        echo -e "   ${YELLOW}→ Ohne Backslash existiert der Ordner ('$stripped') -- .env enthält vermutlich ein wörtliches '\\', das Docker Compose (anders als eine Shell) NICHT als Escapezeichen entfernt. In .env korrigieren zu: NEWS_MP3_FOLDER=$stripped${NC}"
    else
        echo -e "   ${YELLOW}→ NEWS_MP3_FOLDER in .env prüfen (Tippfehler? SMB-Mount nicht eingehängt?).${NC}"
    fi
    echo -e "${RED}Abgebrochen -- Docker würde sonst versuchen, diesen Pfad als leeres Verzeichnis anzulegen.${NC}"
    exit 1
elif [ ! -r "$news_folder_host" ]; then
    echo -e "${RED}❌ NEWS_MP3_FOLDER='$news_folder_host' ist nicht lesbar -- Dateirechte prüfen.${NC}"
    exit 1
else
    mp3_count=$(find "$news_folder_host" -maxdepth 1 -iname '*.mp3' 2>/dev/null | wc -l)
    if [ "$mp3_count" -eq 0 ]; then
        echo -e "${YELLOW}⚠️  NEWS_MP3_FOLDER='$news_folder_host' existiert, enthält aber keine MP3-Dateien.${NC}"
    else
        echo -e "${GREEN}✅ NEWS_MP3_FOLDER='$news_folder_host' ($mp3_count MP3-Datei(en) gefunden).${NC}"
    fi
fi
echo

echo "🚀 Starte RadioSabbelNich..."
echo

docker compose up -d --build

# Platzhalter für später: weitere Schritte (z.B. Healthcheck, Log-Ausgabe,
# Update-Logik) können hier ergänzt werden.
