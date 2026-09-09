#!/bin/bash
# Erneuert das Tailscale-TLS-Zertifikat für dockfish.icefish-ghost.ts.net
# und startet bei tatsächlicher Erneuerung die beiden betroffenen Container
# neu (webui.py liest das Zertifikat nur beim Start, Icecast baut sein
# kombiniertes cert+key-PEM ebenfalls nur beim eigenen Containerstart --
# siehe ARCHITECTURE.md, Abschnitt "TLS/HTTPS"). Läuft als root (Cert-
# Verzeichnis ist root-only), siehe systemd-Unit im selben Verzeichnis.
#
# "tailscale cert" ist bei --min-validity idempotent: schreibt die Dateien
# nur neu, wenn das bestehende Zertifikat die geforderte Mindestgültigkeit
# unterschreitet -- der Hash-Vergleich unten ist trotzdem nötig, um genau
# das (seltene, alle ~90 Tage) zu erkennen und NICHT bei jedem täglichen
# Lauf die laufende Wiedergabe per Container-Neustart zu unterbrechen.
set -euo pipefail

HOSTNAME="dockfish.icefish-ghost.ts.net"
CERT_FILE="/certs/${HOSTNAME}.crt"
KEY_FILE="/certs/${HOSTNAME}.key"
COMPOSE_DIR="/opt/docker/radiosabbelnich"

before=$(sha256sum "$CERT_FILE" 2>/dev/null || echo "missing")
tailscale cert --min-validity 720h --cert-file "$CERT_FILE" --key-file "$KEY_FILE" "$HOSTNAME"
after=$(sha256sum "$CERT_FILE" 2>/dev/null || echo "missing")

if [ "$before" != "$after" ]; then
    echo "Zertifikat erneuert, starte radiosabbelnich + icecast neu ..."
    cd "$COMPOSE_DIR"
    docker compose restart radiosabbelnich icecast
    echo "Neustart abgeschlossen."
else
    echo "Zertifikat weiterhin gültig, kein Neustart nötig."
fi
