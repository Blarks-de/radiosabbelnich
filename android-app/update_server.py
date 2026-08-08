#!/usr/bin/env python3
"""Minimaler Update-Server fuer die KeinSabbelRadio-Android-APK.

Liefert ausschliesslich die zwei Dateien in diesem Verzeichnis
(keinsabbelradio.apk + version.json) aus - kein Verzeichnis-Listing, kein
Zugriff auf Quellcode/Keystore im selben Baum, obwohl der zugrunde
liegende Handler technisch das gesamte Verzeichnis ausliefern koennte.

Kein Auth - genau wie das Docker-Projekt selbst (siehe dessen CLAUDE.md,
Abschnitt "Kein Auth, nur hinter VPN"). Nur fuers Tailscale-Netz gedacht;
dieser Prozess bindet trotzdem auf 0.0.0.0 (nicht auf die Tailscale-IP
allein), weil auch der Rest des Projekts sich auf "erreichbar nur ueber
das VPN" statt auf eine Interface-Bindung verlaesst - keine oeffentliche
Portfreigabe fuer diesen Port einrichten.
"""
import http.server
import os
import socketserver

PORT = 8098
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
ALLOWED_PATHS = ("/keinsabbelradio.apk", "/version.json")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        if self.path not in ALLOWED_PATHS:
            self.send_error(404, "Not Found")
            return
        super().do_GET()

    def do_HEAD(self):
        if self.path not in ALLOWED_PATHS:
            self.send_error(404, "Not Found")
            return
        super().do_HEAD()

    def log_message(self, format, *args):
        # Debug-Log statt stderr-Spam, siehe restliches Projekt (Datei-Log
        # ist fuer Nachvollziehbarkeit da, Konsole nur fuer Ereignisse).
        pass


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    with ReusableTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"Update-Server laeuft auf Port {PORT} (nur keinsabbelradio.apk/version.json)")
        httpd.serve_forever()
