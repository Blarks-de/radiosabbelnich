#!/bin/bash
# Wrapper für den laufenden Betrieb: start/stop/restart/status in einem
# Kommando, statt sich docker-compose-Aufrufe zu merken. "start" ruft dafür
# bewusst das bestehende run_radiosabbelnich.sh auf (System-/MP3-Ordner-
# Checks + docker compose up -d --build) statt die Logik hier zu duplizieren
# -- "status" dagegen bringt eine eigene, auf laufenden Betrieb zugeschnittene
# Anzeige mit (Container-Zustand, Ports, Live-Stand übers Web-Interface),
# das deckt check-radiosabbelnich.sh (Preflight VOR dem ersten Start) nicht ab.
# Ohne Argument: status (häufigster Aufruf -- "läuft der Stack gerade?").
set -e

cd "$(dirname "$0")"

# Farben/Balken 1:1 aus check-radiosabbelnich.sh/run_radiosabbelnich.sh
# übernommen (bewusst dupliziert statt in eine gemeinsame Datei ausgelagert,
# siehe SESSION.md-Begründung dort: drei kleine Skripte, keine gemeinsame
# Bibliothek nötig).
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

usage() {
    echo "Verwendung: $0 [start|stop|restart|status]"
    echo "  Ohne Argument bzw. 'status': zeigt den aktuellen Zustand (Default)."
}

# Liest den tatsächlich AUFGELÖSTEN Host-Port aus "docker compose config"
# statt .env selbst zu parsen -- gleicher Grund wie beim NEWS_MP3_FOLDER-
# Check in check-radiosabbelnich.sh: Shell und Compose interpretieren .env-
# Werte nicht immer identisch, "docker compose config" ist die Quelle der
# Wahrheit für das, was tatsächlich läuft.
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

    # ICECAST_HOSTNAME ist die Adresse, unter der HÖRER sich tatsächlich
    # verbinden (siehe docker-compose.yml, IC_HOSTNAME) -- die obigen beiden
    # Checks laufen bewusst gegen localhost und sagen deshalb nichts darüber
    # aus, ob diese Adresse von AUSSEN erreichbar ist. Aus "docker compose
    # config" gelesen statt .env selbst geparst, gleicher Grund wie bei
    # resolved_port() oben (Shell/Compose interpretieren .env nicht immer
    # identisch).
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

    # --- RAM/HD des Hosts (nicht container-spezifisch -- für Details zu
    # den Container-eigenen Ressourcen siehe die "🖥 Ressourcen"-Sektion auf
    # der Config-Seite, die läuft über resource_monitor.py/psutil und
    # braucht dafür das erreichbare Web-Interface, s.o.) ---
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
    echo "🚀 Starte RadioSabbelNich ..."
    echo
    exec ./run_radiosabbelnich.sh
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

case "${1:-status}" in
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
