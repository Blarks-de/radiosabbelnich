#!/bin/bash
# Alles-in-einem-Wrapper für RadioSabbelNich: check/start/stop/restart/status
# in einem Kommando, statt sich docker-compose-Aufrufe zu merken oder
# zwischen mehreren Skripten zu wechseln.
#
# Bis 2026-08-12 gab es dafür drei Dateien (check-radiosabbelnich.sh für die
# Preflight-Diagnose, run_radiosabbelnich.sh fürs eigentliche Starten, dieses
# Skript für den laufenden Betrieb) -- auf Nutzerwunsch zu einer
# zusammengeführt, die beiden anderen sind ersatzlos entfernt (siehe
# SESSION.md). Grund für die ursprüngliche Trennung war rein "drei kleine
# Skripte, keine gemeinsame Bibliothek nötig" (siehe ältere SESSION.md-
# Einträge) -- innerhalb EINER Datei ergibt Codeverdopplung dagegen keinen
# Sinn mehr, deshalb sind die vormals dreifach kopierten Blöcke (RAM/HD-
# Anzeige, MP3-Ordner-Check) jetzt gemeinsame Funktionen.
#
#   check   - Preflight-Diagnose (Docker-Installation, .env, MP3-Ordner,
#             Ports) -- reine Diagnose, startet selbst nichts.
#   start   - Schlanker System-Check (RAM/HD/Internet, MP3-Ordner-Check
#             MIT Abbruch bei fehlendem Ordner) + docker compose up -d --build.
#             Bewusst NICHT dieselbe volle Tiefe wie "check" (Docker-
#             Installation, .env-Vollständigkeit, Portkonflikte) -- wer das
#             will, ruft vorher "check" separat auf.
#   stop    - docker compose stop.
#   restart - stop, dann start.
#   status  - Container-Zustand, Erreichbarkeit (inkl. Tailscale/Internet),
#             MP3-Ordner, RAM/HD, Live-Stand übers Web-Interface (Default
#             ohne Argument -- häufigster Aufruf: "läuft der Stack gerade?").
set -e

cd "$(dirname "$0")"

BAR_WIDTH=28
RED='\033[0;31m'; YELLOW='\033[0;33m'; GREEN='\033[0;32m'; NC='\033[0m'
FAILED=0  # nur von "check" genutzt -- zählt harte Probleme für den Exit-Code

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

usage() {
    echo "Verwendung: $0 [check|start|stop|restart|status]"
    echo "  check   - Preflight-Diagnose (Docker/.env/MP3-Ordner/Ports), startet nichts"
    echo "  start   - System-Check + docker compose up -d --build"
    echo "  stop    - Stack anhalten (docker compose stop)"
    echo "  restart - stop, dann start"
    echo "  status  - aktueller Zustand (Default ohne Argument)"
}

# Versionsinfo aus VERSION am Repo-Root -- bei JEDEM Aufruf (egal welches
# Unterkommando) als Erstes ausgegeben, nicht nur bei "status": der Wrapper
# ist der einzige Berührungspunkt vieler Nutzer mit dem Stack, und welcher
# Build gerade läuft ist unabhängig vom gewählten Kommando relevant.
print_version() {
    if [ -f VERSION ]; then
        echo "📌 $(cat VERSION)"
        echo
    fi
}

# --- RAM/HD-Anzeige, von check/start/status gemeinsam genutzt ---
print_ram_hd() {
    local mem_total_kb mem_avail_kb mem_used_kb mem_total_gb mem_used_gb mem_percent
    mem_total_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
    mem_avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
    mem_used_kb=$(( mem_total_kb - mem_avail_kb ))
    mem_total_gb=$(awk "BEGIN{printf \"%.1f\", $mem_total_kb/1024/1024}")
    mem_used_gb=$(awk "BEGIN{printf \"%.1f\", $mem_used_kb/1024/1024}")
    mem_percent=$(( mem_used_kb * 100 / mem_total_kb ))
    printf "🧠 RAM:  %6s GB / %6s GB (%d%%) " "$mem_used_gb" "$mem_total_gb" "$mem_percent"
    draw_bar "$mem_percent"
    echo

    local disk_total disk_used disk_percent
    read -r disk_total disk_used disk_percent <<< "$(df -BG / | awk 'NR==2 {gsub("G","",$2); gsub("G","",$3); gsub("%","",$5); print $2, $3, $5}')"
    printf "💾 HD:   %6s GB / %6s GB (%d%%) " "$disk_used" "$disk_total" "$disk_percent"
    draw_bar "$disk_percent"
    echo
}

# --- Internet-Erreichbarkeit für check/start (Docker-Image-Pull/apt) --
# ANDERE Frage als der Tailscale/Ping-Check in cmd_status() weiter unten
# (der prüft, ob HÖRER von außen reinkommen) -- deshalb bewusst getrennt
# gehalten, kein Versuch, beide zusammenzulegen.
print_internet_check() {
    if curl -s --max-time 3 https://1.1.1.1 -o /dev/null; then
        echo -e "🌐 Internet: ${GREEN}✅ verfügbar${NC}"
    else
        echo -e "🌐 Internet: ${RED}❌ nicht verfügbar${NC}"
    fi
}

# --- MP3-Ordner-Check (Nachrichten-Pause), von check/start gemeinsam
# genutzt. $1="abort": bricht bei fehlendem/nicht lesbarem Ordner sofort
# mit exit 1 ab (für "start" -- Docker würde sonst versuchen, den Pfad
# als leeres Verzeichnis anzulegen). Ohne Argument zählt ein Problem nur
# über fail() mit (für "check", das am Ende über alle Probleme hinweg
# einmal exit 1 liefert, aber auch bei einem kaputten MP3-Ordner noch die
# restlichen Checks -- z.B. Ports -- durchlaufen lassen soll).
#
# Bewusst NICHT ".env" selbst parsen (per "source" o.ä.): eine Shell und
# Docker Compose interpretieren z.B. Backslashes in Werten UNTERSCHIEDLICH
# (Shell entfernt "\'" beim Sourcen zu "'", Compose lässt den Backslash
# wörtlich stehen) -- ein per Shell "korrekt" gelesener Pfad kann also
# genau der kaputte Pfad sein, den Docker gleich als Mount-Quelle verwendet
# und an dem "docker compose up" dann mit einem kryptischen "invalid
# argument" scheitert (live erlebt: NEWS_MP3_FOLDER=.../80\'s/ in .env).
# Deshalb fragen wir Compose selbst nach dem aufgelösten Wert.
check_mp3_folder() {
    local abort_mode="${1:-}"
    local compose_config_json news_folder_host

    echo "📻 MP3-Ordner (Nachrichten-Pause)"

    if ! compose_config_json=$(docker compose config --format json 2>/dev/null); then
        fail "'docker compose config' schlägt fehl -- Fehler in docker-compose.yml oder .env."
        if [ "$abort_mode" = "abort" ]; then
            docker compose config --format json
            exit 1
        fi
        echo
        return
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
        warn "NEWS_MP3_FOLDER konnte nicht ermittelt werden -- Check übersprungen."
    elif [ "$news_folder_host" = "./data/news_mp3" ]; then
        warn "NEWS_MP3_FOLDER nicht gesetzt (Default './data/news_mp3') -- Nachrichten-Pause bleibt inaktiv, bis dort MP3s liegen oder ein eigener Pfad in .env eingetragen ist. Kein Fehler, das Feature ist optional."
    elif [ ! -d "$news_folder_host" ]; then
        fail "NEWS_MP3_FOLDER='$news_folder_host' existiert nicht (Tippfehler? SMB-Mount nicht eingehängt?)."
        local stripped="${news_folder_host//\\/}"
        if [ "$stripped" != "$news_folder_host" ] && [ -d "$stripped" ]; then
            echo -e "   ${YELLOW}→ Ohne Backslash existiert der Ordner ('$stripped') -- .env enthält vermutlich ein wörtliches '\\', das Docker Compose (anders als eine Shell) NICHT als Escapezeichen entfernt. In .env korrigieren zu: NEWS_MP3_FOLDER=$stripped${NC}"
        fi
        if [ "$abort_mode" = "abort" ]; then
            echo -e "${RED}Abgebrochen -- Docker würde sonst versuchen, diesen Pfad als leeres Verzeichnis anzulegen.${NC}"
            exit 1
        fi
    elif [ ! -r "$news_folder_host" ]; then
        fail "NEWS_MP3_FOLDER='$news_folder_host' ist nicht lesbar -- Dateirechte prüfen."
        [ "$abort_mode" = "abort" ] && exit 1
    else
        local mp3_count
        mp3_count=$(find "$news_folder_host" -maxdepth 1 -iname '*.mp3' 2>/dev/null | wc -l)
        if [ "$mp3_count" -eq 0 ]; then
            warn "NEWS_MP3_FOLDER='$news_folder_host' existiert, enthält aber keine MP3-Dateien."
        else
            ok "NEWS_MP3_FOLDER='$news_folder_host' ($mp3_count MP3-Datei(en) gefunden)."
        fi
    fi
    echo
}

# Liest den tatsächlich AUFGELÖSTEN Host-Port aus "docker compose config"
# statt .env selbst zu parsen -- gleicher Grund wie beim NEWS_MP3_FOLDER-
# Check oben: Shell und Compose interpretieren .env-Werte nicht immer
# identisch, "docker compose config" ist die Quelle der Wahrheit für das,
# was tatsächlich läuft.
resolved_port() {
    local service=$1 index=$2
    printf '%s' "$compose_config_json" | python3 -c "
import json, sys
try:
    cfg = json.load(sys.stdin)
    ports = cfg['services']['$service']['ports']
    print(ports[$index]['published'])
except Exception:
    print('')
" 2>/dev/null
}

# --- Port-Helfer, nur von "check" genutzt ---
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
    if [ "$owner" = "radiosabbelnich" ] || [ "$owner" = "icecast-radiosabbelnich" ]; then
        ok "$label (Port $port) läuft bereits -- eigener Container ('$owner')."
        return
    fi
    if [ -n "$owner" ]; then
        fail "$label (Port $port) ist durch einen anderen Docker-Container belegt: '$owner'."
    else
        fail "$label (Port $port) ist belegt (kein RadioSabbelNich-Container -- evtl. ein anderer Prozess direkt auf dem Host)."
    fi
    local alt; alt=$(find_free_port "$port")
    if [ -n "$alt" ]; then
        echo -e "   ${YELLOW}→ Alternative: $var_name=$alt in .env eintragen.${NC}"
    else
        echo -e "   ${YELLOW}→ Keine freie Alternative im Bereich $((port + 1))-$((port + 20)) gefunden.${NC}"
    fi
}

cmd_check() {
    # -------------------------------------------------------------
    # 1. Docker installieren, falls nötig
    # -------------------------------------------------------------
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

    # -------------------------------------------------------------
    # 2. System-Ressourcen
    # -------------------------------------------------------------
    echo "🔍 System-Check"
    echo
    print_ram_hd
    echo
    print_internet_check
    echo

    # -------------------------------------------------------------
    # 3. .env vorhanden und ausgefüllt?
    # -------------------------------------------------------------
    echo "📄 .env"

    if [ ! -f .env ]; then
        fail ".env nicht gefunden -- mit 'cp env.example .env' anlegen und Passwörter/Hostname eintragen."
    else
        # Bewusst OHNE "set -a": ge-source-te (und damit von der Shell,
        # abweichend von Docker Compose, ggf. anders interpretierte -- siehe
        # MP3-Ordner-Check unten) Werte dürfen NICHT als echte Umgebungsvariablen
        # exportiert werden. Sonst würde der spätere "docker compose config"-
        # Aufruf im selben Skript-Prozess exakt diese (falsch entschärften) Werte
        # sehen statt der rohen .env-Datei -- Vorrang von Shell-Env vor .env ist
        # Docker Compose selbst so definiert. Live erlebt: verdeckte genau den
        # NEWS_MP3_FOLDER-Backslash-Bug, den dieser Check eigentlich finden soll.
        # shellcheck disable=SC1091
        source .env

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

    # -------------------------------------------------------------
    # 4. MP3-Ordner für die Nachrichten-Pause
    # -------------------------------------------------------------
    check_mp3_folder

    # -------------------------------------------------------------
    # 5. Ports: frei, durch RadioSabbelNich selbst belegt (ok), oder durch
    #    einen ANDEREN Docker-Container/Prozess blockiert?
    # -------------------------------------------------------------
    echo "🔌 Ports"
    check_port "Web-Interface" "WEBUI_PORT" "$WEBUI_PORT"
    check_port "Icecast (HTTP)" "ICECAST_PORT" "$ICECAST_PORT"
    if [ -n "$TLS_CERT_FILE" ] && [ -n "$TLS_KEY_FILE" ]; then
        check_port "Icecast (HTTPS)" "ICECAST_SSL_PORT" "$ICECAST_SSL_PORT"
    fi
    echo

    echo "======================================"
    if [ "$FAILED" -gt 0 ]; then
        echo -e "${RED}❌ $FAILED Problem(e) gefunden -- siehe oben.${NC}"
        exit 1
    else
        echo -e "${GREEN}✅ Alle Prüfungen bestanden. Start mit: $0 start${NC}"
    fi
}

cmd_status() {
    echo "🎛  RadioSabbelNich — Status"
    echo "======================================"
    echo
    echo "📦 Container"
    local ps_json
    if ! ps_json=$(docker compose ps --format json 2>/dev/null) || [ -z "$ps_json" ]; then
        echo -e "${YELLOW}⚠️  Keine Container gefunden -- noch nie gestartet (siehe '$0 start')?${NC}"
    else
        # JSON Lines (ein Objekt pro Zeile), kein JSON-Array -- so liefert
        # "docker compose ps --format json" es auf diesem Host.
        printf '%s\n' "$ps_json" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        c = json.loads(line)
    except json.JSONDecodeError:
        continue
    state = c.get("State", "")
    name = c.get("Name") or c.get("Service", "?")
    status = c.get("Status", "")
    icon = "✅" if state == "running" else ("🔴" if state in ("exited", "dead") else "⚠️ ")
    print(f"{icon} {name}: {status}")
'
    fi
    echo

    local compose_config_json webui_port icecast_port
    compose_config_json=$(docker compose config --format json 2>/dev/null) || compose_config_json=""
    if [ -n "$compose_config_json" ]; then
        webui_port=$(resolved_port radiosabbelnich 0)
        icecast_port=$(resolved_port icecast 0)
    fi
    webui_port="${webui_port:-5000}"
    icecast_port="${icecast_port:-8000}"

    # tls_enabled entscheidet http vs. https fürs Web-Interface (siehe
    # CLAUDE.md, Abschnitt TLS/HTTPS) -- bei aktivem TLS antwortet der Port
    # NUR noch https, ein reiner http-Versuch scheitert dann mit "Empty
    # reply". data/settings.json direkt lesen statt zu raten/beides zu
    # probieren, das ist ohnehin die Quelle der Wahrheit dafür.
    local webui_scheme="http"
    if [ -f data/settings.json ]; then
        tls_enabled=$(python3 -c "
import json
try:
    print(json.load(open('data/settings.json')).get('tls_enabled', False))
except Exception:
    print(False)
" 2>/dev/null)
        [ "$tls_enabled" = "True" ] && webui_scheme="https"
    fi

    echo "🔌 Erreichbarkeit"

    # ICECAST_HOSTNAME ist die Adresse, unter der HÖRER sich tatsächlich
    # verbinden (siehe docker-compose.yml, IC_HOSTNAME) -- bewusst als
    # ERSTES in diesem Block (vor den localhost-Checks unten), weil das die
    # Adresse ist, die man als Mensch beim Vorlesen des Status zuerst sehen
    # will. Aus "docker compose config" gelesen statt .env selbst geparst,
    # gleicher Grund wie bei resolved_port() oben (Shell/Compose
    # interpretieren .env nicht immer identisch).
    local icecast_hostname
    icecast_hostname=$(printf '%s' "$compose_config_json" | python3 -c "
import json, sys
try:
    cfg = json.load(sys.stdin)
    print(cfg['services']['icecast']['environment'].get('IC_HOSTNAME', ''))
except Exception:
    print('')
" 2>/dev/null)

    if [ -n "$icecast_hostname" ]; then
        echo "🔗 Hostname für Hörer: $icecast_hostname"
    else
        echo -e "${YELLOW}⚠️  ICECAST_HOSTNAME nicht gesetzt (.env prüfen).${NC}"
    fi

    local status_json=""
    # -k: bei https evtl. selbstsigniertes/auf einen anderen Hostnamen
    # ausgestelltes Zertifikat (z.B. per "tailscale cert") -- für einen
    # reinen Erreichbarkeits-Check hier egal, kein Sicherheitskontext.
    if status_json=$(curl -sk --max-time 2 "$webui_scheme://localhost:$webui_port/api/status" 2>/dev/null) && [ -n "$status_json" ]; then
        echo -e "${GREEN}✅ Web-Interface ($webui_scheme, Port $webui_port) antwortet.${NC}"
    else
        echo -e "${RED}❌ Web-Interface ($webui_scheme, Port $webui_port) antwortet nicht.${NC}"
        status_json=""
    fi
    if curl -s --max-time 2 "http://localhost:$icecast_port/status.xsl" -o /dev/null 2>/dev/null; then
        echo -e "${GREEN}✅ Icecast (Port $icecast_port) antwortet.${NC}"
    else
        echo -e "${RED}❌ Icecast (Port $icecast_port) antwortet nicht.${NC}"
    fi

    # Tailscale-Check nur, wenn der Hostname tatsächlich ein
    # Tailscale-MagicDNS-Name ist (*.ts.net) -- bei jedem anderen Hostnamen
    # (eigene Domain, Reverse-Proxy) wäre ein Tailscale-Status hier
    # irreführend, nicht aussagekräftig.
    if [[ "$icecast_hostname" == *.ts.net ]]; then
        if ! command -v tailscale >/dev/null 2>&1; then
            echo -e "${YELLOW}⚠️  Tailscale-CLI nicht gefunden -- kann Tailscale-Status nicht prüfen.${NC}"
        else
            local ts_state
            ts_state=$(tailscale status --json 2>/dev/null | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('BackendState', ''))
except Exception:
    print('')
" 2>/dev/null)
            if [ "$ts_state" = "Running" ]; then
                echo -e "${GREEN}✅ Tailscale läuft -- $icecast_hostname sollte erreichbar sein.${NC}"
            elif [ -n "$ts_state" ]; then
                echo -e "${RED}❌ Tailscale nicht aktiv (Status: $ts_state) -- $icecast_hostname ist darüber NICHT erreichbar!${NC}"
            else
                echo -e "${RED}❌ Tailscale-Status nicht abrufbar (Daemon down/ausgeloggt?) -- $icecast_hostname vermutlich NICHT erreichbar!${NC}"
            fi
        fi
    fi

    # Internet/DNS-Grundcheck: ein einzelner Ping deckt beides ab -- schlägt
    # die DNS-Auflösung fehl, gibt es gar keine Ziel-IP zum Anpingen; ist DNS
    # ok, aber kein Uplink da, läuft der Ping stattdessen in den Timeout.
    # hamburg.de statt z.B. 8.8.8.8, weil das zusätzlich einen echten
    # DNS-Lookup erzwingt (eine reine IP würde DNS-Ausfälle nicht zeigen).
    if ping -c 1 -W 2 hamburg.de >/dev/null 2>&1; then
        echo -e "${GREEN}✅ Internet/DNS erreichbar (Ping hamburg.de).${NC}"
    else
        echo -e "${RED}❌ Kein Internet oder DNS kaputt (Ping hamburg.de fehlgeschlagen)!${NC}"
    fi
    echo

    # MP3-Ordner der Nachrichten-Pause: Pfad + Trefferzahl, gleicher
    # NEWS_MP3_FOLDER_HOST-Weg über "docker compose config" wie oben beim
    # Hostname (Quelle der Wahrheit statt .env selbst zu parsen) -- bewusst
    # eine schlankere Variante des ausführlichen check_mp3_folder() oben
    # (kein Backslash-Hinweis etc.): das hier ist eine laufende Status-
    # Anzeige, keine Preflight-Diagnose.
    echo "📻 MP3-Ordner (Nachrichten-Pause)"
    local news_folder_host mp3_count
    news_folder_host=$(printf '%s' "$compose_config_json" | python3 -c "
import json, sys
try:
    cfg = json.load(sys.stdin)
    print(cfg['services']['radiosabbelnich']['environment'].get('NEWS_MP3_FOLDER_HOST', ''))
except Exception:
    print('')
" 2>/dev/null)

    if [ -z "$news_folder_host" ]; then
        echo -e "${YELLOW}⚠️  NEWS_MP3_FOLDER nicht ermittelbar.${NC}"
    elif [ ! -d "$news_folder_host" ]; then
        echo -e "${RED}❌ $news_folder_host existiert nicht (Tippfehler? SMB-Mount nicht eingehängt?).${NC}"
    elif [ ! -r "$news_folder_host" ]; then
        echo -e "${RED}❌ $news_folder_host ist nicht lesbar.${NC}"
    else
        mp3_count=$(find "$news_folder_host" -maxdepth 1 -iname '*.mp3' 2>/dev/null | wc -l)
        if [ "$mp3_count" -eq 0 ]; then
            echo -e "${YELLOW}⚠️  $news_folder_host enthält keine MP3-Dateien.${NC}"
        else
            echo -e "${GREEN}✅ $news_folder_host ($mp3_count MP3-Datei(en) gefunden)${NC}"
        fi
    fi
    echo

    # --- RAM/HD des Hosts (nicht container-spezifisch -- für Details zu
    # den Container-eigenen Ressourcen siehe die "🖥 Ressourcen"-Sektion auf
    # der Config-Seite, die läuft über resource_monitor.py/psutil und
    # braucht dafür das erreichbare Web-Interface, s.o.) ---
    print_ram_hd

    # --- Live-Stand übers Web-Interface: Radio/Musiksammlung-Modus (siehe
    # CLAUDE.md), aktueller Sender bzw. Track, Hörerzahl -- nur wenn oben
    # erreichbar, sonst übersprungen statt einen leeren Abschnitt zu zeigen. ---
    if [ -n "$status_json" ]; then
        echo
        echo "🎶 Live"
        printf '%s' "$status_json" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if d.get("mode") == "music":
    m = d.get("music") or {}
    if m.get("active"):
        print(f"🎵 Musiksammlung: {m.get('file')} ({m.get('index', -1) + 1}/{m.get('total', 0)})")
    else:
        print("🎵 Musiksammlung-Modus (Wiedergabe gestoppt)")
else:
    name = d.get("current_name") or "–"
    now = d.get("now_playing")
    print(f"📻 {name}" + (f" — {now}" if now else ""))
listeners = d.get("listeners")
if listeners is not None:
    print(f"👂 {len(listeners)} Hörer")
'
    fi
}

cmd_start() {
    echo "🔍 System-Check vor dem Start"
    echo
    print_ram_hd
    echo
    print_internet_check
    echo
    check_mp3_folder abort

    echo "🚀 Starte RadioSabbelNich ..."
    echo
    docker compose up -d --build
}

cmd_stop() {
    echo "🛑 Stoppe RadioSabbelNich ..."
    # "stop" statt "down": hält Container/Netzwerk/Volumes bestehen, ein
    # nachfolgendes "start" kommt darüber ohne Neuanlegen wieder hoch --
    # "down" wäre für einen reinen Stop-Befehl unnötig destruktiv (Daten
    # in stations.json/settings.json etc. sind zwar Bind-Mounts und damit
    # ohnehin sicher, aber Netzwerk/Container-Metadaten müssten neu
    # angelegt werden, ohne Vorteil gegenüber "stop").
    docker compose stop
    echo -e "${GREEN}✅ Gestoppt.${NC}"
}

cmd_restart() {
    cmd_stop
    echo
    cmd_start
}

print_version

case "${1:-status}" in
    check)   cmd_check ;;
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    -h|--help) usage ;;
    *)
        echo "Unbekannter Befehl: '$1'"
        usage
        exit 1
        ;;
esac
