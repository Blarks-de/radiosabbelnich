#!/bin/bash
# Preflight-Check vor dem ersten (oder erneuten) Start: Docker-Installation,
# System-Ressourcen (wie run_keinsabbelradio.sh), .env vollständig ausgefüllt,
# MP3-Ordner für die Nachrichten-Pause nutzbar, und ob die benötigten Ports
# frei sind -- inkl. Alternativvorschlag, falls ein Port durch einen
# ANDEREN Docker-Container belegt ist. Reine Diagnose, startet selbst
# nichts -- das macht weiterhin ./run_keinsabbelradio.sh.
set -e

cd "$(dirname "$0")"

BAR_WIDTH=28
RED='\033[0;31m'; YELLOW='\033[0;33m'; GREEN='\033[0;32m'; NC='\033[0m'
FAILED=0  # zählt harte Probleme, für den Exit-Code am Ende

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; FAILED=$((FAILED + 1)); }

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

# ---------------------------------------------------------------------
# 1. Docker installieren, falls nötig (bisheriger Inhalt von
#    install_keinsabbelradio.sh, unverändert -- nur um ok()/fail() ergänzt)
# ---------------------------------------------------------------------
echo "🐳 Docker"

if command -v docker &> /dev/null; then
    ok "Docker ist installiert."
else
    echo "Docker nicht gefunden, installiere..."

    OS="$(uname -s)"
    if [ "$OS" = "Darwin" ]; then
        # macOS: offizielles Docker Desktop kommt nur als GUI-Installer,
        # per Paketmanager geht das brew-Cask am einfachsten ohne Interaktion.
        brew install --cask docker
    elif [ -f /etc/os-release ]; then
        # Debian/Ubuntu (und Derivate): offizielles Docker-Install-Script
        # deckt beide ab, ohne die Paketquellen manuell einrichten zu müssen.
        . /etc/os-release
        case "$ID" in
            debian|ubuntu)
                curl -fsSL https://get.docker.com | sh
                ;;
            *)
                fail "Nicht unterstützte Linux-Distribution: $ID"
                ;;
        esac
    else
        fail "Unbekanntes Betriebssystem: $OS"
    fi

    if command -v docker &> /dev/null; then
        ok "Docker installiert."
    else
        fail "Docker-Installation fehlgeschlagen -- siehe Ausgabe oben."
    fi
fi
echo

# ---------------------------------------------------------------------
# 2. System-Ressourcen (identisch zu run_keinsabbelradio.sh -- absichtlich
#    dupliziert statt in eine gemeinsame Datei ausgelagert, siehe SESSION.md)
# ---------------------------------------------------------------------
echo "🔍 System-Check"
echo

mem_total_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
mem_avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
mem_used_kb=$(( mem_total_kb - mem_avail_kb ))
mem_total_gb=$(awk "BEGIN{printf \"%.1f\", $mem_total_kb/1024/1024}")
mem_used_gb=$(awk "BEGIN{printf \"%.1f\", $mem_used_kb/1024/1024}")
mem_percent=$(( mem_used_kb * 100 / mem_total_kb ))

printf "🧠 RAM:  %6s GB / %6s GB (%d%%) " "$mem_used_gb" "$mem_total_gb" "$mem_percent"
draw_bar "$mem_percent"
echo

read -r disk_total disk_used disk_percent <<< "$(df -BG / | awk 'NR==2 {gsub("G","",$2); gsub("G","",$3); gsub("%","",$5); print $2, $3, $5}')"
printf "💾 HD:   %6s GB / %6s GB (%d%%) " "$disk_used" "$disk_total" "$disk_percent"
draw_bar "$disk_percent"
echo

if curl -s --max-time 3 https://1.1.1.1 -o /dev/null; then
  echo -e "🌐 Internet: ${GREEN}✅ verfügbar${NC}"
else
  echo -e "🌐 Internet: ${RED}❌ nicht verfügbar${NC}"
fi
echo

# ---------------------------------------------------------------------
# 3. .env vorhanden und ausgefüllt?
# ---------------------------------------------------------------------
echo "📄 .env"

if [ ! -f .env ]; then
    fail ".env nicht gefunden -- mit 'cp env.example .env' anlegen und Passwörter/Hostname eintragen."
else
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a

    missing=()
    for var in ICECAST_ADMIN_USER ICECAST_ADMIN_PASSWORD ICECAST_SOURCE_PASSWORD \
               ICECAST_HOSTNAME ICECAST_ADMIN_EMAIL ICECAST_LOCATION; do
        [ -z "${!var}" ] && missing+=("$var")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        fail ".env unvollständig -- es fehlt: ${missing[*]}"
    fi

    # Werte, die 1:1 aus env.example übernommen wurden, sind mit hoher
    # Wahrscheinlichkeit vergessene Platzhalter, kein Scheitern wert (der
    # Stack startet damit technisch), aber eine klare Warnung wert.
    placeholders=()
    [ "$ICECAST_ADMIN_PASSWORD" = "change_me_admin" ] && placeholders+=("ICECAST_ADMIN_PASSWORD")
    [ "$ICECAST_SOURCE_PASSWORD" = "change_me_source" ] && placeholders+=("ICECAST_SOURCE_PASSWORD")
    [ "$ICECAST_ADMIN_EMAIL" = "admin@example.com" ] && placeholders+=("ICECAST_ADMIN_EMAIL")
    if [ ${#placeholders[@]} -gt 0 ]; then
        warn ".env enthält noch unveränderte Platzhalterwerte aus env.example: ${placeholders[*]}"
    fi

    if [ ${#missing[@]} -eq 0 ] && [ ${#placeholders[@]} -eq 0 ]; then
        ok ".env vorhanden und ausgefüllt."
    fi
fi
echo

# Defaults wie in docker-compose.yml (${VAR:-default}), damit die
# folgenden Checks auch ohne .env (oder mit Lücken darin) sinnvoll gegen
# das laufen, was tatsächlich beim Start verwendet würde.
: "${WEBUI_PORT:=5000}"
: "${ICECAST_PORT:=8000}"
: "${ICECAST_SSL_PORT:=8443}"
: "${NEWS_MP3_FOLDER:=./data/news_mp3}"

# ---------------------------------------------------------------------
# 4. MP3-Ordner für die Nachrichten-Pause: eingetragen und nutzbar?
# ---------------------------------------------------------------------
echo "📻 MP3-Ordner (Nachrichten-Pause)"

if [ "$NEWS_MP3_FOLDER" = "./data/news_mp3" ]; then
    warn "NEWS_MP3_FOLDER nicht gesetzt (Default './data/news_mp3') -- Feature bleibt inaktiv, bis dort MP3s liegen oder ein eigener Pfad in .env eingetragen ist. Kein Fehler, das Feature ist optional."
elif [ ! -d "$NEWS_MP3_FOLDER" ]; then
    fail "NEWS_MP3_FOLDER='$NEWS_MP3_FOLDER' existiert nicht (Tippfehler? SMB-Mount nicht eingehängt?)."
elif [ ! -r "$NEWS_MP3_FOLDER" ]; then
    fail "NEWS_MP3_FOLDER='$NEWS_MP3_FOLDER' ist nicht lesbar -- Dateirechte prüfen."
else
    mp3_count=$(find "$NEWS_MP3_FOLDER" -maxdepth 1 -iname '*.mp3' 2>/dev/null | wc -l)
    if [ "$mp3_count" -eq 0 ]; then
        warn "NEWS_MP3_FOLDER='$NEWS_MP3_FOLDER' existiert, enthält aber keine MP3-Dateien."
    else
        ok "NEWS_MP3_FOLDER='$NEWS_MP3_FOLDER' ($mp3_count MP3-Datei(en) gefunden)."
    fi
fi
echo

# ---------------------------------------------------------------------
# 5. Ports: frei, durch KeinSabbelRadio selbst belegt (ok), oder durch einen
#    ANDEREN Docker-Container/Prozess blockiert (dann Alternative suchen)?
# ---------------------------------------------------------------------

port_open() {
    # Bash-Bordmittel (/dev/tcp) statt netstat/ss -- die sind nicht
    # überall installiert (insbesondere macOS), /dev/tcp funktioniert
    # sowohl dort als auch auf jedem halbwegs aktuellen Linux.
    local port=$1
    (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null
    local result=$?
    exec 3<&- 2>/dev/null
    exec 3>&- 2>/dev/null
    return $result
}

port_owner_container() {
    # Name des laufenden Docker-Containers, dessen Port-Mapping den
    # angegebenen Host-Port enthält, oder leer, wenn keiner passt (z.B.
    # weil ein Nicht-Docker-Prozess den Port hält, oder Docker gar nicht
    # läuft).
    local port=$1
    docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | while IFS=$'\t' read -r name ports; do
        if echo "$ports" | grep -qE "(^|:)${port}->"; then
            echo "$name"
            break
        fi
    done
}

find_free_port() {
    # Sucht ab port+1 aufwärts (max. 20 Versuche) den nächsten freien Port.
    local start=$1 p
    for (( p = start + 1; p <= start + 20; p++ )); do
        if ! port_open "$p"; then
            echo "$p"
            return 0
        fi
    done
    return 1
}

check_port() {
    local label=$1 var_name=$2 port=$3
    if ! port_open "$port"; then
        ok "$label (Port $port) ist frei."
        return
    fi
    local owner; owner=$(port_owner_container "$port")
    if [ "$owner" = "keinsabbelradio" ] || [ "$owner" = "icecast-keinsabbelradio" ]; then
        ok "$label (Port $port) läuft bereits -- eigener Container ('$owner')."
        return
    fi
    if [ -n "$owner" ]; then
        fail "$label (Port $port) ist durch einen anderen Docker-Container belegt: '$owner'."
    else
        fail "$label (Port $port) ist belegt (kein KeinSabbelRadio-Container -- evtl. ein anderer Prozess direkt auf dem Host)."
    fi
    local alt; alt=$(find_free_port "$port")
    if [ -n "$alt" ]; then
        echo -e "   ${YELLOW}→ Alternative: $var_name=$alt in .env eintragen.${NC}"
    else
        echo -e "   ${YELLOW}→ Keine freie Alternative im Bereich $((port + 1))-$((port + 20)) gefunden.${NC}"
    fi
}

echo "🔌 Ports"
check_port "Web-Interface" "WEBUI_PORT" "$WEBUI_PORT"
check_port "Icecast (HTTP)" "ICECAST_PORT" "$ICECAST_PORT"
if [ -n "$TLS_CERT_FILE" ] && [ -n "$TLS_KEY_FILE" ]; then
    check_port "Icecast (HTTPS)" "ICECAST_SSL_PORT" "$ICECAST_SSL_PORT"
fi
echo

# ---------------------------------------------------------------------
echo "======================================"
if [ "$FAILED" -gt 0 ]; then
    echo -e "${RED}❌ $FAILED Problem(e) gefunden -- siehe oben.${NC}"
    exit 1
else
    echo -e "${GREEN}✅ Alle Prüfungen bestanden. Start mit: ./run_keinsabbelradio.sh${NC}"
fi
