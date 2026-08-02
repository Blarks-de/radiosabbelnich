#!/usr/bin/env python3
"""
webui.py — Eingebettetes Webinterface für RadioZapper.

Läuft als ThreadingHTTPServer in einem Hintergrund-Thread des
Hauptprozesses (radio_switch.py). Zeigt den aktuell laufenden Sender und
verbundene Hörer (IP/User-Agent/Verbindungsdauer, abgefragt über Icecasts
Admin-API) und erlaubt manuelles Umschalten über eine Sender-Liste aus
stations.json.

Kommunikation mit dem Hauptloop läuft über SwitcherState: geteilter,
lock-geschützter In-Memory-Zustand statt Datei-Polling oder IPC — läuft
im selben Prozess, also reicht das.
"""

import base64
import json
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class SwitcherState:
    """Thread-sicherer geteilter Zustand zwischen Hauptloop und Webserver."""

    def __init__(self, stations: list):
        self.stations = stations
        self._lock = threading.Lock()
        self._current_index = 0
        self._manual_request = None

    @property
    def current_index(self) -> int:
        with self._lock:
            return self._current_index

    def set_current(self, index: int):
        with self._lock:
            self._current_index = index

    def request_switch(self, index: int):
        with self._lock:
            self._manual_request = index

    def pop_manual_request(self):
        """Liefert den anstehenden manuellen Switch-Request (oder None) und
        leert ihn dabei — wird vom Hauptloop einmal pro Fenster abgefragt."""
        with self._lock:
            req = self._manual_request
            self._manual_request = None
            return req


def _fetch_listeners(admin_url, user, password, mount, timeout=3):
    """Fragt Icecasts Admin-API nach verbundenen Hörern eines Mountpoints ab.
    Gibt None zurück, wenn nicht konfiguriert oder die Abfrage fehlschlägt
    (Icecast down, falsche Credentials, Netzwerkproblem etc.) — der Aufrufer
    zeigt das dann als "nicht verfügbar" statt einer leeren Liste an."""
    if not (admin_url and user and password and mount):
        return None
    url = f"{admin_url.rstrip('/')}/admin/listclients?mount={mount}"
    req = urllib.request.Request(url)
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError):
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    listeners = []
    for listener in root.iter("listener"):
        listeners.append({
            "ip": listener.findtext("IP") or "?",
            "user_agent": listener.findtext("UserAgent") or "",
            "connected_seconds": int(listener.findtext("Connected") or 0),
        })
    return listeners


def _build_status(state: SwitcherState, icecast_cfg: dict) -> dict:
    idx = state.current_index
    stations = state.stations
    current_name = stations[idx]["name"] if 0 <= idx < len(stations) else None
    listeners = _fetch_listeners(
        icecast_cfg.get("admin_url"), icecast_cfg.get("user"),
        icecast_cfg.get("password"), icecast_cfg.get("mount"),
    )
    return {
        "current_index": idx,
        "current_name": current_name,
        "stations": [{"index": i, "name": s["name"]} for i, s in enumerate(stations)],
        "listeners": listeners,
        "stream_port": icecast_cfg.get("public_port"),
        "stream_mount": icecast_cfg.get("mount"),
    }


_PAGE_HTML = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RadioZapper</title>
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    max-width: 640px; margin: 2rem auto; padding: 0 1rem;
  }
  h1 { font-size: 1.4rem; margin-bottom: 1rem; }
  h2 { font-size: 1.05rem; margin-top: 2rem; }
  #current {
    font-size: 1.1rem; padding: .75rem 1rem; border-radius: .5rem;
    background: #eee;
  }
  @media (prefers-color-scheme: dark) { #current { background: #2a2a2a; } }
  #player { width: 100%; margin-top: 1rem; }
  ul#stations { list-style: none; padding: 0; display: grid; gap: .5rem; }
  ul#stations li button {
    width: 100%; text-align: left; padding: .6rem .8rem; font-size: 1rem;
    border-radius: .4rem; border: 1px solid #999; background: none;
    color: inherit; cursor: pointer;
  }
  ul#stations li button.active { border-color: #2a7a4a; background: #2a7a4a33; font-weight: 600; }
  ul#stations li button:disabled { opacity: .5; cursor: default; }
  table { width: 100%; border-collapse: collapse; margin-top: .5rem; font-size: .9rem; }
  td, th { text-align: left; padding: .3rem .5rem; border-bottom: 1px solid #8884; }
  #meta { color: #888; font-size: .8rem; margin-top: 2rem; }
</style>
</head>
<body>
<h1>📻 RadioZapper</h1>
<div id="current">Lade …</div>
<audio id="player" controls preload="none"></audio>

<h2>Sender</h2>
<ul id="stations"></ul>

<h2>Hörer</h2>
<div id="listeners">Lade …</div>

<div id="meta"></div>

<script>
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

let switching = false;
let playerSrcSet = false;

async function refresh() {
  let data;
  try {
    const res = await fetch('/api/status');
    data = await res.json();
  } catch (e) {
    document.getElementById('current').textContent = 'Verbindung zum Server verloren …';
    return;
  }

  document.getElementById('current').textContent =
    data.current_name ? ('▶ Läuft gerade: ' + data.current_name) : 'Kein Sender aktiv';

  // Player-Quelle nur einmal setzen, nicht bei jedem Poll -> sonst würde
  // die Wiedergabe alle 5s neu starten/stottern
  if (!playerSrcSet && data.stream_port && data.stream_mount) {
    const player = document.getElementById('player');
    player.src = location.protocol + '//' + location.hostname + ':' + data.stream_port + data.stream_mount;
    playerSrcSet = true;
  }

  const list = document.getElementById('stations');
  list.innerHTML = '';
  for (const s of data.stations) {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.textContent = s.name;
    if (s.index === data.current_index) btn.classList.add('active');
    btn.disabled = switching;
    btn.addEventListener('click', () => switchStation(s.index));
    li.appendChild(btn);
    list.appendChild(li);
  }

  const listenersEl = document.getElementById('listeners');
  if (data.listeners === null) {
    listenersEl.textContent = 'Hörer-Info nicht verfügbar.';
  } else if (data.listeners.length === 0) {
    listenersEl.textContent = 'Aktuell keine Hörer verbunden.';
  } else {
    let out = '<table><tr><th>IP</th><th>Verbunden seit</th><th>Client</th></tr>';
    for (const l of data.listeners) {
      const mins = Math.floor(l.connected_seconds / 60);
      const secs = l.connected_seconds % 60;
      out += `<tr><td>${esc(l.ip)}</td><td>${mins}m ${secs}s</td>` +
             `<td>${esc((l.user_agent || '').slice(0, 40))}</td></tr>`;
    }
    out += '</table>';
    listenersEl.innerHTML = out;
  }

  document.getElementById('meta').textContent = 'Aktualisiert: ' + new Date().toLocaleTimeString('de-DE');
}

async function switchStation(index) {
  switching = true;
  try {
    await fetch('/api/switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({index}),
    });
  } finally {
    switching = false;
  }
  refresh();
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def make_handler(state: SwitcherState, icecast_cfg: dict):
    """Baut eine BaseHTTPRequestHandler-Subklasse mit state/icecast_cfg im
    Closure — so bleibt der Handler selbst zustandslos und threadsicher."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # kein Logspam für jeden Poll-Request

        def _send(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", ""):
                self._send(_PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/status":
                body = json.dumps(_build_status(state, icecast_cfg)).encode("utf-8")
                self._send(body, "application/json; charset=utf-8")
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/api/switch":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            index = payload.get("index")
            if isinstance(index, int) and 0 <= index < len(state.stations):
                state.request_switch(index)
                self._send(b'{"ok": true}', "application/json; charset=utf-8")
            else:
                self._send(
                    json.dumps({"ok": False, "error": "invalid index"}).encode("utf-8"),
                    "application/json; charset=utf-8", status=400,
                )

    return Handler


def start_server(port: int, state: SwitcherState, icecast_cfg: dict) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(state, icecast_cfg))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd
