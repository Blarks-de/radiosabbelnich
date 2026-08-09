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
echo "🚀 Starte RadioSabbelNich..."
echo

cd "$(dirname "$0")"

docker compose up -d --build

# Platzhalter für später: weitere Schritte (z.B. Healthcheck, Log-Ausgabe,
# Update-Logik) können hier ergänzt werden.
