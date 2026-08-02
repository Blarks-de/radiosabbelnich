#!/usr/bin/env python3
"""
webui.py — Eingebettetes Webinterface für RadioZapper.

Läuft als ThreadingHTTPServer in einem Hintergrund-Thread des
Hauptprozesses (radiozapper.py). Zeigt den aktuell laufenden Sender und
verbundene Hörer (IP/User-Agent/Verbindungsdauer, abgefragt über Icecasts
Admin-API) und erlaubt manuelles Umschalten über eine Sender-Liste aus
stations.json.

Kommunikation mit dem Hauptloop läuft über SwitcherState: geteilter,
lock-geschützter In-Memory-Zustand statt Datei-Polling oder IPC — läuft
im selben Prozess, also reicht das.
"""

import base64
import json
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import stations_store


class SwitcherState:
    """Thread-sicherer geteilter Zustand zwischen Hauptloop und Webserver.

    Hält den Live-Rotationszustand (welcher Sender läuft gerade, anstehende
    manuelle Switch-/Reload-Requests) als In-Memory-Cache. stations.json
    (über stations_store) bleibt die eigentliche Quelle der Wahrheit für
    Senderdaten — reload() liest sie neu ein, z.B. nachdem die Config-Seite
    etwas geändert hat."""

    def __init__(self):
        self._lock = threading.Lock()
        self._all_stations = []
        self._active_stations = []
        self._current_id = None
        self._manual_request = None
        self._reload_requested = False
        self.reload()

    def reload(self):
        all_stations = stations_store.load_all()
        active = sorted(
            (s for s in all_stations if s.get("enabled", True)),
            key=lambda s: s["name"].lower(),
        )
        with self._lock:
            self._all_stations = all_stations
            self._active_stations = active
            if self._current_id is None and active:
                self._current_id = active[0]["id"]

    @property
    def active_stations(self) -> list:
        """Nur aktivierte Sender, alphabetisch — die Rotationsreihenfolge."""
        with self._lock:
            return list(self._active_stations)

    @property
    def all_stations(self) -> list:
        """Alle Sender (aktiv + deaktiviert), für die Config-Seite."""
        with self._lock:
            return list(self._all_stations)

    @property
    def current_id(self):
        with self._lock:
            return self._current_id

    def set_current(self, station_id):
        with self._lock:
            self._current_id = station_id

    def current_station(self):
        """Aktuell laufender Sender als dict, oder None."""
        with self._lock:
            cid = self._current_id
            for s in self._active_stations:
                if s["id"] == cid:
                    return s
            for s in self._all_stations:
                if s["id"] == cid:
                    return s
            return None

    def request_switch(self, station_id):
        with self._lock:
            self._manual_request = station_id

    def pop_manual_request(self):
        """Liefert den anstehenden manuellen Switch-Request (oder None) und
        leert ihn dabei — wird vom Hauptloop einmal pro Fenster abgefragt."""
        with self._lock:
            req = self._manual_request
            self._manual_request = None
            return req

    def request_reload(self):
        """Von der Config-Seite nach jeder Änderung aufgerufen — der
        Hauptloop liest stations.json beim nächsten Durchlauf neu ein."""
        with self._lock:
            self._reload_requested = True

    def pop_reload_request(self) -> bool:
        with self._lock:
            flag = self._reload_requested
            self._reload_requested = False
            return flag


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


# Cache für "Jetzt läuft"-Metadaten: eine eigene ICY-Verbindung pro
# Sender-URL kostet eine kurze zusätzliche Verbindung zum jeweiligen
# Radiosender-Server (unabhängig von der laufenden ffmpeg-Wiedergabe) —
# bei mehreren offenen Browser-Tabs, die alle /api/status pollen, soll
# das nicht bei jedem einzelnen Poll erneut passieren.
_NOW_PLAYING_TTL = 15.0
_now_playing_cache = {}  # url -> (timestamp, titel_oder_None)
_now_playing_lock = threading.Lock()


def _read_exact(resp, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = resp.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def _fetch_icy_title(url: str, timeout: float = 3) -> str | None:
    """Öffnet kurz eine eigene Verbindung zum Stream mit `Icy-MetaData: 1`
    und liest das erste eingebettete Metadaten-Paket (StreamTitle=...) aus.
    Nicht jeder Sender füllt das mit echten Song/Interpret-Daten (manche
    zeigen nur den Sendernamen oder gar nichts) — das ist serverseitig
    entschieden, nicht etwas, das wir beeinflussen können."""
    req = urllib.request.Request(url, headers={"Icy-MetaData": "1", "User-Agent": "RadioZapper/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            metaint = resp.headers.get("icy-metaint")
            if not metaint:
                return None
            metaint = int(metaint)
            _read_exact(resp, metaint)  # Audio-Bytes bis zum Metadaten-Block verwerfen
            length_byte = _read_exact(resp, 1)
            if not length_byte:
                return None
            meta_len = length_byte[0] * 16
            if meta_len == 0:
                return None
            meta = _read_exact(resp, meta_len).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    match = re.search(r"StreamTitle='([^']*)'", meta)
    title = match.group(1).strip() if match else None
    return title or None


# Für die meisten Sender ist ICY-StreamTitle (siehe oben) die einzige
# realistische Quelle. Für einzelne Sender, deren eigene Website eine
# stabile, öffentlich erreichbare JSON-API für "Jetzt läuft" hat, lohnt
# sich ein gezielter Fallback statt ICY-Branding-Text anzuzeigen — kein
# genereller Website-Scraper (die meisten Sender-Homepages rendern das
# clientseitig per JS, die jeweilige API pro Sender zu reverse-engineeren
# wäre pro Sender eigener, fragiler Wartungsaufwand). Bisher recherchiert
# und bestätigt: R.SH läuft über die "loverad.io"-Plattform (Regiocast),
# stream-service.loverad.io/v4/<slug> liefert artist_name/song_title als
# sauberes JSON. Weitere Sender können hier ergänzt werden, sobald jemand
# deren API-Muster gefunden hat.
_LOVERAD_STREAM_SERVICE_SLUGS = {
    "r-sh": "rsh",
}


def _fetch_loverad_now_playing(slug: str, timeout: float = 3) -> str | None:
    url = f"https://stream-service.loverad.io/v4/{slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "RadioZapper/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    channel = data.get("1") if isinstance(data, dict) else None
    if not channel:
        return None
    artist = (channel.get("artist_name") or "").strip()
    title = (channel.get("song_title") or "").strip()
    if artist and title:
        return f"{artist} - {title}"
    return title or artist or None


def _fetch_now_playing(station: dict, timeout: float = 3):
    if not station:
        return None
    cache_key = station["id"]
    now = time.time()
    with _now_playing_lock:
        cached = _now_playing_cache.get(cache_key)
        if cached and now - cached[0] < _NOW_PLAYING_TTL:
            return cached[1]

    slug = _LOVERAD_STREAM_SERVICE_SLUGS.get(station["id"])
    if slug:
        title = _fetch_loverad_now_playing(slug, timeout=timeout)
    else:
        title = _fetch_icy_title(station["url"], timeout=timeout)

    with _now_playing_lock:
        _now_playing_cache[cache_key] = (now, title)
    return title


def _build_status(state: SwitcherState, icecast_cfg: dict) -> dict:
    current = state.current_station()
    active = state.active_stations
    listeners = _fetch_listeners(
        icecast_cfg.get("admin_url"), icecast_cfg.get("user"),
        icecast_cfg.get("password"), icecast_cfg.get("mount"),
    )
    now_playing = _fetch_now_playing(current) if current else None
    return {
        "current_id": current["id"] if current else None,
        "current_name": current["name"] if current else None,
        "now_playing": now_playing,
        "stations": [{"id": s["id"], "name": s["name"]} for s in active],
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
    font-size: 1.1rem; padding: .75rem 1rem; border-radius: .5rem .5rem 0 0;
    background: #eee;
  }
  #now-playing {
    font-size: .9rem; padding: 0 1rem .6rem 1rem; border-radius: 0 0 .5rem .5rem;
    background: #eee; color: #555; min-height: 1.1em;
  }
  #now-playing:empty { display: none; }
  @media (prefers-color-scheme: dark) {
    #current, #now-playing { background: #2a2a2a; }
    #now-playing { color: #aaa; }
  }
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
  a.config-link { display: inline-block; margin-top: 1rem; font-size: .9rem; }
</style>
</head>
<body>
<h1>📻 RadioZapper</h1>
<div id="current">Lade …</div>
<div id="now-playing"></div>
<audio id="player" controls preload="none"></audio>

<h2>Sender</h2>
<ul id="stations"></ul>

<h2>Hörer</h2>
<div id="listeners">Lade …</div>

<div id="meta"></div>
<a class="config-link" href="/config">⚙ Sender verwalten</a>

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
  document.getElementById('now-playing').textContent = data.now_playing ? '🎵 ' + data.now_playing : '';

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
    if (s.id === data.current_id) btn.classList.add('active');
    btn.disabled = switching;
    btn.addEventListener('click', () => switchStation(s.id));
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

async function switchStation(id) {
  switching = true;
  try {
    await fetch('/api/switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id}),
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


_CONFIG_PAGE_HTML = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RadioZapper — Sender verwalten</title>
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    max-width: 720px; margin: 2rem auto; padding: 0 1rem;
  }
  h1 { font-size: 1.4rem; }
  a.back { display: inline-block; margin-bottom: 1rem; }
  h2 {
    font-size: 1.05rem; margin-top: 2rem; border-bottom: 1px solid #8884;
    padding-bottom: .25rem;
  }
  ul.stations { list-style: none; padding: 0; margin: .5rem 0; }
  ul.stations li {
    display: flex; align-items: center; gap: .6rem; padding: .5rem 0;
    border-bottom: 1px solid #8882;
  }
  ul.stations li .name { flex: 1; min-width: 0; }
  ul.stations li .name .url {
    display: block; font-size: .75rem; color: #888; word-break: break-all;
  }
  ul.stations li.disabled .name { opacity: .5; }
  ul.stations li button {
    font-size: .85rem; padding: .3rem .6rem; cursor: pointer; flex-shrink: 0;
  }
  .empty { color: #888; font-size: .9rem; font-style: italic; margin: .3rem 0; }
  .edit-row { flex-wrap: wrap; }
  .edit-row .fields { flex: 1 1 100%; display: grid; gap: .3rem; margin-bottom: .4rem; }
  .edit-row input, .edit-row select {
    font-size: .9rem; padding: .3rem; width: 100%; box-sizing: border-box;
  }
  form#add-form {
    margin-top: 1.5rem; padding: 1rem; border: 1px solid #8884; border-radius: .5rem;
    display: grid; gap: .6rem;
  }
  form#add-form input, form#add-form select {
    padding: .5rem; font-size: 1rem; width: 100%; box-sizing: border-box;
  }
  form#add-form button { padding: .6rem; font-size: 1rem; cursor: pointer; }
  #msg { margin-top: 1rem; font-size: .9rem; min-height: 1.2em; }
  #msg.error { color: #d33; }
  #msg.ok { color: #2a7a4a; }
</style>
</head>
<body>
<a class="back" href="/">← zurück zum Player</a>
<h1>⚙ Sender verwalten</h1>
<div id="categories">Lade …</div>

<h2>Neuer Sender</h2>
<form id="add-form">
  <input type="text" id="add-name" placeholder="Name" required>
  <input type="url" id="add-url" placeholder="Stream-URL (https://...)" required>
  <select id="add-category"></select>
  <label><input type="checkbox" id="add-enabled" checked> aktiviert</label>
  <button type="submit">Hinzufügen</button>
</form>

<div id="msg"></div>

<script>
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

let categories = [];
let editingId = null;
let msgTimer = null;

function showMsg(text, isError) {
  const el = document.getElementById('msg');
  el.textContent = text;
  el.className = isError ? 'error' : 'ok';
  if (msgTimer) clearTimeout(msgTimer);
  msgTimer = setTimeout(() => { el.textContent = ''; el.className = ''; }, 4000);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw new Error('Ungültige Antwort vom Server');
  }
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || ('HTTP ' + res.status));
  }
  return data;
}

async function loadStations() {
  let data;
  try {
    data = await api('/api/config/stations');
  } catch (e) {
    showMsg('Konnte Senderliste nicht laden: ' + e.message, true);
    return;
  }
  categories = data.categories;

  const addCategorySelect = document.getElementById('add-category');
  const prevAddCategory = addCategorySelect.value;
  addCategorySelect.innerHTML = categories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  if (categories.includes(prevAddCategory)) addCategorySelect.value = prevAddCategory;

  const container = document.getElementById('categories');
  container.innerHTML = '';
  for (const cat of categories) {
    const stations = data.stations
      .filter(s => s.category === cat)
      .sort((a, b) => a.name.localeCompare(b.name, 'de'));

    const h2 = document.createElement('h2');
    h2.textContent = cat;
    container.appendChild(h2);

    if (stations.length === 0) {
      const p = document.createElement('div');
      p.className = 'empty';
      p.textContent = 'Keine Sender in dieser Kategorie.';
      container.appendChild(p);
      continue;
    }

    const ul = document.createElement('ul');
    ul.className = 'stations';
    for (const s of stations) {
      ul.appendChild(renderStationRow(s));
    }
    container.appendChild(ul);
  }
}

function renderStationRow(s) {
  const li = document.createElement('li');
  if (!s.enabled) li.classList.add('disabled');

  if (editingId === s.id) {
    li.classList.add('edit-row');

    const fields = document.createElement('div');
    fields.className = 'fields';

    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.value = s.name;
    nameInput.placeholder = 'Name';

    const urlInput = document.createElement('input');
    urlInput.type = 'url';
    urlInput.value = s.url;
    urlInput.placeholder = 'Stream-URL';

    const catSelect = document.createElement('select');
    catSelect.innerHTML = categories.map(c =>
      `<option value="${esc(c)}"${c === s.category ? ' selected' : ''}>${esc(c)}</option>`).join('');

    fields.appendChild(nameInput);
    fields.appendChild(urlInput);
    fields.appendChild(catSelect);
    li.appendChild(fields);

    const saveBtn = document.createElement('button');
    saveBtn.textContent = 'Speichern';
    saveBtn.onclick = async () => {
      try {
        await api('/api/config/stations/' + encodeURIComponent(s.id), {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            name: nameInput.value, url: urlInput.value,
            category: catSelect.value, enabled: s.enabled,
          }),
        });
        editingId = null;
        showMsg('Gespeichert.', false);
        loadStations();
      } catch (e) {
        showMsg('Fehler: ' + e.message, true);
      }
    };
    li.appendChild(saveBtn);

    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Abbrechen';
    cancelBtn.onclick = () => { editingId = null; loadStations(); };
    li.appendChild(cancelBtn);

    return li;
  }

  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = s.enabled;
  checkbox.title = 'aktiviert';
  checkbox.onchange = async () => {
    const wanted = checkbox.checked;
    try {
      await api('/api/config/stations/' + encodeURIComponent(s.id) + '/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled: wanted}),
      });
      loadStations();
    } catch (e) {
      showMsg('Fehler: ' + e.message, true);
      checkbox.checked = !wanted;
    }
  };
  li.appendChild(checkbox);

  const nameDiv = document.createElement('div');
  nameDiv.className = 'name';
  nameDiv.innerHTML = `${esc(s.name)}<span class="url">${esc(s.url)}</span>`;
  li.appendChild(nameDiv);

  const editBtn = document.createElement('button');
  editBtn.textContent = 'Bearbeiten';
  editBtn.onclick = () => { editingId = s.id; loadStations(); };
  li.appendChild(editBtn);

  const delBtn = document.createElement('button');
  delBtn.textContent = 'Löschen';
  delBtn.onclick = async () => {
    if (!confirm(`"${s.name}" wirklich löschen?`)) return;
    try {
      await api('/api/config/stations/' + encodeURIComponent(s.id) + '/delete', {method: 'POST'});
      showMsg('Gelöscht.', false);
      loadStations();
    } catch (e) {
      showMsg('Fehler: ' + e.message, true);
    }
  };
  li.appendChild(delBtn);

  return li;
}

document.getElementById('add-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const name = document.getElementById('add-name').value;
  const url = document.getElementById('add-url').value;
  const category = document.getElementById('add-category').value;
  const enabled = document.getElementById('add-enabled').checked;
  try {
    await api('/api/config/stations', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, url, category, enabled}),
    });
    document.getElementById('add-form').reset();
    document.getElementById('add-enabled').checked = true;
    showMsg('Hinzugefügt.', false);
    loadStations();
  } catch (e) {
    showMsg('Fehler: ' + e.message, true);
  }
});

loadStations();
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

        def _send_json(self, obj, status: int = 200):
            self._send(json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", status=status)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                return json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            if self.path in ("/", ""):
                self._send(_PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/config":
                self._send(_CONFIG_PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/status":
                self._send_json(_build_status(state, icecast_cfg))
            elif self.path == "/api/config/stations":
                # bewusst frisch von der Platte, nicht state.all_stations:
                # das ist nur ein Cache für die Rotation im Hauptloop und
                # wird erst beim nächsten Reload-Poll dort aktualisiert —
                # die Config-Seite muss aber eigene Änderungen sofort sehen
                self._send_json({
                    "stations": stations_store.load_all(),
                    "categories": stations_store.CATEGORIES,
                })
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path == "/api/switch":
                self._handle_switch()
            elif self.path == "/api/config/stations":
                self._handle_add_station()
            elif self.path.startswith("/api/config/stations/"):
                self._handle_station_action()
            else:
                self.send_error(404)

        def _handle_switch(self):
            payload = self._read_json_body()
            station_id = payload.get("id")
            active_ids = {s["id"] for s in state.active_stations}
            if isinstance(station_id, str) and station_id in active_ids:
                state.request_switch(station_id)
                self._send_json({"ok": True})
            else:
                self._send_json({"ok": False, "error": "invalid id"}, status=400)

        def _handle_add_station(self):
            payload = self._read_json_body()
            try:
                station = stations_store.add(
                    payload.get("name", ""), payload.get("url", ""),
                    payload.get("category", ""), payload.get("enabled", True),
                )
                state.request_reload()
                self._send_json({"ok": True, "station": station})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)

        def _handle_station_action(self):
            # Pfadschema: /api/config/stations/<id>[/delete|/toggle]
            rest = self.path[len("/api/config/stations/"):]
            parts = [p for p in rest.split("/") if p]
            if not parts:
                self.send_error(404)
                return
            station_id = parts[0]
            action = parts[1] if len(parts) > 1 else None
            payload = self._read_json_body()

            try:
                if action is None:
                    station = stations_store.update(
                        station_id, payload.get("name", ""), payload.get("url", ""),
                        payload.get("category", ""), payload.get("enabled", True),
                    )
                    state.request_reload()
                    self._send_json({"ok": True, "station": station})
                elif action == "toggle":
                    station = stations_store.set_enabled(station_id, bool(payload.get("enabled", True)))
                    state.request_reload()
                    self._send_json({"ok": True, "station": station})
                elif action == "delete":
                    stations_store.delete(station_id)
                    state.request_reload()
                    self._send_json({"ok": True})
                else:
                    self.send_error(404)
            except KeyError:
                self._send_json({"ok": False, "error": "Sender nicht gefunden"}, status=404)
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)

    return Handler


def start_server(port: int, state: SwitcherState, icecast_cfg: dict) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(state, icecast_cfg))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd
