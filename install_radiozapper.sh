#!/bin/bash
# Erkennt das OS, installiert Docker falls nötig, fertig.
set -e

if command -v docker &> /dev/null; then
    echo "Docker ist bereits installiert."
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
                echo "Nicht unterstützte Linux-Distribution: $ID" >&2
                exit 1
                ;;
        esac
    else
        echo "Unbekanntes Betriebssystem: $OS" >&2
        exit 1
    fi
fi

echo "Fertig. RadioZapper starten mit: ./run_radiozapper.sh"
