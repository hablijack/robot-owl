"""
Robot Owl RPi Brain - Web UI

A small LAN-only web page for manually poking the owl: blink, change
expression, and move the head with arrow buttons. Every action forwards the
same NDJSON command the supervisor uses, so no firmware change is needed.

The page polls /api/telemetry for live state (so you can see the result of
each action). There is NO authentication: only expose this on a trusted
local network.
"""

import logging
import threading

from flask import Flask, jsonify, render_template_string, request

from brain.serial_handler import SerialHandler, Telemetry
from brain.supervisor import Supervisor

logger = logging.getLogger(__name__)


def _ear_wing_angle(direction: str, up: float, down: float) -> float:
    """Map a UI direction to an absolute servo angle for a single-axis part
    (ear or wing). 'center' returns 0; unknown directions default to center."""
    if direction == "up":
        return up
    if direction == "down":
        return down
    return CENTER


# Servo channel indices (must match esp32-s3-sense/include/config.h).
CH_LEFT_EAR = 0
CH_RIGHT_EAR = 1
CH_HEAD = 2
CH_LEFT_WING = 3
CH_RIGHT_WING = 4

# Absolute head servo angles for the left/right buttons (degrees, -45..45).
# The head only pans left/right (it does not tilt up/down), so there are no
# up/down angles or buttons for it.
HEAD_LEFT = -40
HEAD_RIGHT = 40

# Ears and wings pivot on a single axis, so each has an "up" and "down" button
# (and center). Angles are absolute and clamped to +-45 by the firmware.
EAR_UP = -35
EAR_DOWN = 35
WING_UP = -40
WING_DOWN = 40
CENTER = 0

# Expressions offered in the UI (subset of the firmware's EyeExpression names).
EXPRESSIONS = ["neutral", "happy", "sleepy", "surprised", "angry", "searching"]

# Sound effects the RPi can play through the MAX98357A amp (see brain/audio.py).
# The owl-call voices (detecting/interacting/happy/sleeping/waking/alert) play a
# real recording when present, else a synthesized tone of the same name.
SOUNDS = ["detecting", "interacting", "happy", "sleeping", "waking", "alert", "beep"]

# Friendly labels for the sound buttons (value -> display text).
SOUND_LABELS = {
    "detecting": "Hoot (spotted)",
    "interacting": "Hoot (talking)",
    "happy": "Hoot (happy)",
    "sleeping": "Hoot (sleepy)",
    "waking": "Hoot (waking)",
    "alert": "Hoot (alert)",
    "beep": "Beep",
}

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robot Owl - Control</title>
<style>
  :root { --bg:#111418; --card:#1c2128; --fg:#e6edf3; --accent:#57ab5a; --muted:#8b949e; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, sans-serif; background:var(--bg); color:var(--fg); }
  header { padding:16px 20px; background:var(--card); display:flex; justify-content:space-between; align-items:center; }
  header h1 { font-size:18px; margin:0; }
  .status { font-size:13px; color:var(--muted); }
  .status b { color:var(--fg); }
  main { padding:20px; max-width:720px; margin:0 auto; display:grid; gap:16px; }
  .card { background:var(--card); border-radius:12px; padding:16px; }
  .card h2 { font-size:14px; margin:0 0 12px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(120px,1fr)); gap:10px; }
  button { cursor:pointer; border:none; border-radius:10px; padding:12px; font-size:15px; background:#2a313b; color:var(--fg); }
  button:hover { background:#333c0164; }
  button:active { transform:translateY(1px); }
  button.primary { background:var(--accent); color:#0b0d0f; font-weight:600; }
  button.danger { background:#7a2e2e; color:#fff; }
  button.danger:hover { background:#933939; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .row select, .row input { padding:10px; border-radius:10px; background:#2a313b; color:var(--fg); border:none; }
  .duo { display:grid; grid-template-columns:repeat(2, 1fr); gap:16px; }
  .duo-col { display:grid; grid-template-columns:repeat(3, 64px); gap:8px; justify-content:center; }
  .duo-col button { width:64px; height:48px; font-size:20px; }
  .duo-label { text-align:center; font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; margin-bottom:4px; }
  #msg { min-height:18px; font-size:13px; color:var(--muted); }
  .telemetry { font-size:13px; color:var(--muted); line-height:1.7; }
  .telemetry b { color:var(--fg); }
  .heard { margin-top:6px; padding-top:6px; border-top:1px solid rgba(128,128,128,.25); }
  .heard .q { color:var(--fg); font-style:italic; }
  .heard .ago { color:var(--muted); font-size:12px; }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f6f8fa; --card:#ffffff; --fg:#1f2328; --muted:#57606a; }
    button { background:#e6edf3; color:var(--fg); }
    button:hover { background:#d0d7de; }
    button.primary { background:var(--accent); color:#fff; }
  }
</style>
<!-- Leaflet (OpenStreetMap) for the "Pick on map" place picker. Loaded by the
     BROWSER (not the RPi): the RPi needs no internet / no API key. If this
     fails (airgapped viewer), the map is simply unavailable and you type the
     lat/lon instead. -->
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCJo9tDMHisX+PMd0x6BXM9M=" crossorigin="">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQC2B90hWBXoPa43Lkn+Jj4eV4YVbHsRQs2cVJtYk=" crossorigin=""></script>
</head>
<body>
<header>
  <h1>🦉 Robot Owl</h1>
  <div class="status">state <b id="state">-</b> &middot; fw <b id="fw">-</b></div>
</header>
<main>
  <div class="card">
    <h2>Live status</h2>
    <div class="telemetry" id="telemetry">waiting for telemetry&hellip;</div>
    <div class="heard" id="heard" style="display:none;"></div>
    <div id="msg"></div>
  </div>

  <div class="card">
    <h2>Eyes</h2>
    <div class="row">
      <button class="primary" data-act="blink">Blink once</button>
      <select id="blink-speed">
        <option value="1">fast</option>
        <option value="2">medium</option>
        <option value="3" selected>normal</option>
        <option value="4">slow</option>
        <option value="5">very slow</option>
      </select>
    </div>
  </div>

  <div class="card">
    <h2>Expression</h2>
    <div class="grid" id="expressions"></div>
  </div>

  <div class="card">
    <h2>Sounds</h2>
    <div class="grid" id="sounds"></div>
  </div>

  <div class="card">
    <h2>Head</h2>
    <div class="row" style="justify-content:center; gap:12px;">
      <button data-act="head" data-v="left">&#9664; Left</button>
      <button data-act="head" data-v="center">Center</button>
      <button data-act="head" data-v="right">Right &#9654;</button>
    </div>
  </div>

  <div class="card">
    <h2>Ears</h2>
    <div class="duo">
      <div class="duo-col">
        <div class="duo-label">Left</div>
        <button data-act="ear" data-side="left" data-v="up">&#9650;</button>
        <button data-act="ear" data-side="left" data-v="down">&#9660;</button>
        <button data-act="ear" data-side="left" data-v="center">&#9673;</button>
      </div>
      <div class="duo-col">
        <div class="duo-label">Right</div>
        <button data-act="ear" data-side="right" data-v="up">&#9650;</button>
        <button data-act="ear" data-side="right" data-v="down">&#9660;</button>
        <button data-act="ear" data-side="right" data-v="center">&#9673;</button>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Wings</h2>
    <div class="duo">
      <div class="duo-col">
        <div class="duo-label">Left</div>
        <button data-act="wing" data-side="left" data-v="up">&#9650;</button>
        <button data-act="wing" data-side="left" data-v="down">&#9660;</button>
        <button data-act="wing" data-side="left" data-v="center">&#9673;</button>
      </div>
      <div class="duo-col">
        <div class="duo-label">Right</div>
        <button data-act="wing" data-side="right" data-v="up">&#9650;</button>
        <button data-act="wing" data-side="right" data-v="down">&#9660;</button>
        <button data-act="wing" data-side="right" data-v="center">&#9673;</button>
      </div>
    </div>
  </div>

  <div class="card" id="nav-card">
    <h2>Places &amp; Navigate</h2>
    <p class="hint">Teach the owl a place (name + coordinates), then press Start &mdash;
    its head turns to point at that place, and keeps re-aiming as you walk. Say
    <i>&quot;bring mich nach &lt;name&gt;&quot;</i> (or press Stop) to end.</p>
    <div class="row" style="gap:8px; align-items:flex-end;">
      <label>Name <input id="place-name" placeholder="e.g. hotel"></label>
      <label>Lat <input id="place-lat" type="number" step="any" style="width:110px"></label>
      <label>Lon <input id="place-lon" type="number" step="any" style="width:110px"></label>
      <button data-act="pick-map">Pick on map</button>
      <button data-act="add-place">Add place</button>
    </div>
    <div id="places"></div>
    <div class="row" style="gap:8px; margin-top:10px;">
      <select id="nav-target" style="flex:1; min-width:160px;"></select>
      <button data-act="nav-start" class="primary">Start</button>
      <button data-act="nav-stop" class="danger">Stop</button>
    </div>
    <div id="nav-status" class="hint" style="margin-top:8px;"></div>
  </div>

  <!-- OpenStreetMap map (Leaflet) for picking a place's coordinates. Tiles are
       loaded by the BROWSER viewing this page (not the RPi), so the RPi needs
       no internet / no API key. If the viewer is offline the map is blank and
       you fall back to typing lat/lon. -->
  <div id="map-overlay" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:50; align-items:center; justify-content:center;">
    <div style="background:#fff; border-radius:12px; padding:12px; width:min(720px,95vw); height:min(520px,90vh); display:flex; flex-direction:column; gap:8px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <b>Pick a location</b>
        <button id="map-close" style="cursor:pointer;">&times;</button>
      </div>
      <div id="map" style="flex:1; border-radius:8px; background:#dfe6ee;"></div>
      <div class="row" style="gap:8px; justify-content:flex-end;">
        <span id="map-coords" class="hint">Click the map (or drag the marker) to set the coordinate.</span>
        <button id="map-cancel">Cancel</button>
        <button id="map-apply" class="primary">Use this location</button>
      </div>
    </div>
  </div>
</main>

<script>
  // Navigation UI: manage saved places, pick one on an OpenStreetMap map, and
  // start/stop the owl pointing at a place. The Leaflet map (if loaded) is used
  // for "Pick on map"; the lat/lon inputs always work as a fallback.
  (function() {
    var lat = 48.1351, lon = 11.5820;  // default: Munich
    var map = null, marker = null;
    function post(path, body) {
      return fetch(path, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) }).then(function(r){ return r.json(); });
    }
    function escapeHtml(s){ return String(s).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
    function openMap() {
      var ov = document.getElementById('map-overlay');
      ov.style.display = 'flex';
      if (typeof L === 'undefined') {
        document.getElementById('map-coords').textContent =
          "Map unavailable (offline?) &mdash; type the lat/lon and press 'Use this location'.";
        return;
      }
      if (!map) {
        map = L.map('map').setView([lat, lon], 15);
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
          { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
        marker = L.marker([lat, lon], { draggable: true }).addTo(map);
        function update() {
          var p = marker.getLatLng();
          lat = p.lat; lon = p.lng;
          document.getElementById('place-lat').value = lat.toFixed(5);
          document.getElementById('place-lon').value = lon.toFixed(5);
          document.getElementById('map-coords').textContent = lat.toFixed(5) + ', ' + lon.toFixed(5);
        }
        map.on('click', function(e){ marker.setLatLng(e.latlng); update(); });
        marker.on('drag', update);
        update();
      } else {
        map.setView([lat, lon], 15);
      }
    }
    function closeMap() { document.getElementById('map-overlay').style.display = 'none'; }
    function refreshPlaces() {
      fetch('/api/locations').then(function(r){ return r.json(); }).then(function(d) {
        if (!d.ok) { document.getElementById('places').innerHTML =
          '<span class="hint">Navigation is disabled in config.</span>'; return; }
        var list = document.getElementById('places');
        var sel = document.getElementById('nav-target');
        list.innerHTML = ''; sel.innerHTML = '';
        if (!d.locations.length) { list.innerHTML = '<span class="hint">No places yet &mdash; add one above.</span>'; return; }
        d.locations.forEach(function(loc) {
          var row = document.createElement('div');
          row.className = 'row'; row.style.gap = '8px'; row.style.alignItems = 'center';
          row.innerHTML = '<span style="flex:1">' + escapeHtml(loc.name) +
            ' <span class="hint">' + loc.lat.toFixed(5) + ', ' + loc.lon.toFixed(5) + '</span></span>';
          var del = document.createElement('button');
          del.textContent = 'Remove'; del.className = 'danger';
          del.onclick = function(){ post('/api/locations/delete', {name: loc.name}).then(refreshPlaces); };
          row.appendChild(del);
          list.appendChild(row);
          var opt = document.createElement('option');
          opt.value = loc.name; opt.textContent = loc.name;
          sel.appendChild(opt);
        });
      }).catch(function(){});
    }
    function renderNavStatus(t) {
      var el = document.getElementById('nav-status');
      var n = t.navigation;
      if (!n) { el.textContent = ''; return; }
      if (n.active) {
        el.innerHTML = 'Pointing at <b>' + (n.target||'?') + '</b> &middot; bearing ' +
          (n.bearing!=null? n.bearing.toFixed(0)+'&deg;':'&ndash;') + ' &middot; ' +
          (n.distance_m!=null? n.distance_m.toFixed(0)+' m':'&ndash;') +
          ' &middot; head ' + (n.aim!=null? n.aim.toFixed(0)+'&deg;':'&ndash;');
        el.style.color = '#c9a227';
      } else {
        el.textContent = 'Not navigating.';
        el.style.color = '';
      }
    }
    // Wire the buttons (id- and data-act based).
    function bind() {
      var addBtn = document.querySelector('[data-act="add-place"]');
      if (addBtn) addBtn.onclick = function() {
        var name = document.getElementById('place-name').value.trim();
        var la = parseFloat(document.getElementById('place-lat').value);
        var lo = parseFloat(document.getElementById('place-lon').value);
        if (!name || isNaN(la) || isNaN(lo)) { alert('Enter a name and valid lat/lon.'); return; }
        post('/api/locations', {name: name, lat: la, lon: lo}).then(function(d) {
          if (d.ok) { document.getElementById('place-name').value = ''; }
          refreshPlaces();
        });
      };
      var pickBtn = document.querySelector('[data-act="pick-map"]');
      if (pickBtn) pickBtn.onclick = openMap;
      if (document.getElementById('map-close')) document.getElementById('map-close').onclick = closeMap;
      if (document.getElementById('map-cancel')) document.getElementById('map-cancel').onclick = closeMap;
      if (document.getElementById('map-apply')) document.getElementById('map-apply').onclick = closeMap;
      var startBtn = document.querySelector('[data-act="nav-start"]');
      if (startBtn) startBtn.onclick = function() {
        var name = document.getElementById('nav-target').value;
        if (!name) { alert('Add a place first (or say "bring mich nach <name>").'); return; }
        post('/api/nav/start', {name: name}).then(function(d){ if (!d.ok) alert('Could not start: ' + (d.error||'unknown place')); });
      };
      var stopBtn = document.querySelector('[data-act="nav-stop"]');
      if (stopBtn) stopBtn.onclick = function(){ post('/api/nav/stop', {}); };
    }
    // Expose for the main script (which polls telemetry and calls renderNavStatus).
    window.owlNav = { refreshPlaces: refreshPlaces, renderNavStatus: renderNavStatus, bind: bind };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function(){ bind(); });
    } else {
      bind();
    }
  })();
const $ = (s) => document.querySelector(s);
const exprs = {{ expressions | tojson }};
const sounds = {{ sounds | tojson }};
const soundLabels = {{ sound_labels | tojson }};

// Build expression buttons.
const expWrap = $('#expressions');
for (const e of exprs) {
  const b = document.createElement('button');
  b.textContent = e[0].toUpperCase() + e.slice(1);
  b.dataset.act = 'expression';
  b.dataset.value = e;
  expWrap.appendChild(b);
}

// Build sound buttons.
const sndWrap = $('#sounds');
for (const s of sounds) {
  const b = document.createElement('button');
  b.textContent = soundLabels[s] || (s[0].toUpperCase() + s.slice(1));
  b.dataset.act = 'sound';
  b.dataset.value = s;
  sndWrap.appendChild(b);
}

function post(path, body) {
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json());
}

function flash(text) { $('#msg').textContent = text; }

document.querySelectorAll('button[data-act]').forEach(btn => {
  btn.addEventListener('click', async () => {
    const act = btn.dataset.act;
    if (act === 'blink') {
      await post('/api/blink', { speed: parseInt($('#blink-speed').value, 10) });
      flash('Blink sent.');
    } else if (act === 'expression') {
      await post('/api/expression', { value: btn.dataset.value });
      flash('Expression: ' + btn.dataset.value);
    } else if (act === 'sound') {
      const r = await post('/api/sound', { value: btn.dataset.value });
      flash('Sound "' + btn.dataset.value + '"' + (r.ok ? ' played.' : ' (audio unavailable)'));
    } else if (act === 'head') {
      await post('/api/head', { direction: btn.dataset.v });
      flash('Head ' + btn.dataset.v + '.');
    } else if (act === 'ear') {
      await post('/api/ear', { side: btn.dataset.side, direction: btn.dataset.v });
      flash(btn.dataset.side + ' ear ' + btn.dataset.v + '.');
    } else if (act === 'wing') {
      await post('/api/wing', { side: btn.dataset.side, direction: btn.dataset.v });
      flash(btn.dataset.side + ' wing ' + btn.dataset.v + '.');
    }
  });
});

// Poll live telemetry.
const servoNames = ['L-ear','R-ear','head','L-wing','R-wing'];
// Render the "last heard" line. t.last_heard is present only when speech
// recognition is enabled; otherwise the element stays hidden.
function renderHeard(t) {
  const el = $('#heard');
  const lh = t.last_heard;
  if (!lh || !lh.text) { el.style.display = 'none'; return; }
  const secs = lh.at ? Math.max(0, Math.round((Date.now() / 1000) - lh.at)) : null;
  const ago = secs === null ? '' : ` &middot; <span class="ago">${secs}s ago</span>`;
  el.innerHTML = `heard <span class="q">&ldquo;${lh.text}&rdquo;</span>${ago}`;
  el.style.display = 'block';
}
async function poll() {
  try {
    const t = await (await fetch('/api/telemetry')).json();
    if (!t || !t.state) return;
    $('#state').textContent = t.state;
    $('#fw').textContent = t.firmware || '?';
    const s = t.servos || [];
    const servoStr = servoNames.map((n,i) => `${n}:${(s[i]||0).toFixed(0)}\u00b0`).join('  ');
    const face = t.face ? `face:${t.face.detected?'yes':'no'}` : '';
    $('#telemetry').innerHTML =
      `state <b>${t.state}</b> &middot; eye <b>${t.eye || '-'}</b> &middot; ${face}<br>` +
      `servos: ${servoStr}`;
    renderHeard(t);
    if (window.owlNav) window.owlNav.renderNavStatus(t);
  } catch (e) { /* ignore transient poll errors */ }
}
poll();
setInterval(poll, 1000);
</script>
</body>
</html>
"""


class WebUI:
    """Flask app for manually testing owl features over the LAN."""

    def __init__(self, serial: SerialHandler, supervisor: Supervisor,
                 host: str = "0.0.0.0", port: int = 8080, speech=None):
        self.serial = serial
        self.supervisor = supervisor
        # Optional Speech recognizer: when present, the page shows the last
        # transcript the owl heard (and how recently). None when speech is
        # disabled / failed to start, in which case the field is omitted.
        self.speech = speech
        self.host = host
        self.port = port
        self._thread = None
        self.app = self._build_app()

    # ------------------------------------------------------------------
    # Flask app
    # ------------------------------------------------------------------
    def _build_app(self) -> Flask:
        app = Flask("robot-owl-web")

        @app.route("/")
        def index():
            return render_template_string(TEMPLATE, expressions=EXPRESSIONS, sounds=SOUNDS,
                                           sound_labels=SOUND_LABELS)

        @app.route("/api/telemetry")
        def api_telemetry():
            t: Telemetry = self.supervisor.last
            if t is None:
                return jsonify({"state": None})
            payload = {
                "state": t.state,
                "firmware": t.firmware,
                "eye": t.eye_expression,
                "servos": t.servos,
                "face": {
                    "detected": t.face.detected,
                    "confidence": t.face.confidence,
                    "gaze_x": t.face.gaze_x,
                    "gaze_y": t.face.gaze_y,
                },
            }
            # Phase 3: surface what the owl last heard (and how recently) so the
            # page can display it. Omitted entirely when speech is disabled, so
            # the payload is unchanged for deployments that don't use speech.
            if self.speech is not None:
                payload["last_heard"] = {
                    "text": self.speech.last_heard,
                    "at": self.speech.last_heard_at,
                }
            # Navigation: surface the live compass state (active? target? bearing?
            # distance?) so the Navigate card can show it without a separate poll.
            nav = getattr(self.supervisor, "navigation", None)
            if nav is not None:
                payload["navigation"] = nav.status()
            return jsonify(payload)

        @app.route("/api/blink", methods=["POST"])
        def api_blink():
            speed = request.get_json(silent=True) or {}
            speed = int(speed.get("speed", 3))
            ok = self.serial.blink(speed)
            return jsonify({"ok": ok})

        @app.route("/api/expression", methods=["POST"])
        def api_expression():
            value = (request.get_json(silent=True) or {}).get("value", "neutral")
            ok = self.serial.set_expression(value)
            return jsonify({"ok": ok})

        @app.route("/api/head", methods=["POST"])
        def api_head():
            # The head only pans left/right (no up/down tilt), so those are the
            # only directions offered. Any other value falls back to center.
            direction = (request.get_json(silent=True) or {}).get("direction", "center")
            angle = HEAD_LEFT if direction == "left" else HEAD_RIGHT if direction == "right" else 0
            ok = self.serial.set_servo(CH_HEAD, angle)
            return jsonify({"ok": ok, "channel": CH_HEAD, "angle": angle})

        @app.route("/api/ear", methods=["POST"])
        def api_ear():
            body = request.get_json(silent=True) or {}
            side = body.get("side", "left")
            direction = body.get("direction", "center")
            channel = CH_LEFT_EAR if side == "left" else CH_RIGHT_EAR
            angle = _ear_wing_angle(direction, EAR_UP, EAR_DOWN)
            ok = self.serial.set_servo(channel, angle)
            return jsonify({"ok": ok, "channel": channel, "angle": angle})

        @app.route("/api/wing", methods=["POST"])
        def api_wing():
            body = request.get_json(silent=True) or {}
            side = body.get("side", "left")
            direction = body.get("direction", "center")
            channel = CH_LEFT_WING if side == "left" else CH_RIGHT_WING
            angle = _ear_wing_angle(direction, WING_UP, WING_DOWN)
            ok = self.serial.set_servo(channel, angle)
            return jsonify({"ok": ok, "channel": channel, "angle": angle})

        @app.route("/api/sound", methods=["POST"])
        def api_sound():
            value = (request.get_json(silent=True) or {}).get("value", "beep")
            # Play locally on the RPi (amp) and tell the ESP32 as well.
            ok = self.supervisor.play_sound(value)
            return jsonify({"ok": ok, "sound": value})

        @app.route("/api/sleep", methods=["POST"])
        def api_sleep():
            return jsonify({"ok": self.supervisor.sleep()})

        @app.route("/api/wake", methods=["POST"])
        def api_wake():
            return jsonify({"ok": self.supervisor.wake()})

        # ------------------------------------------------------------------
        # Navigation ("guide me home"). The store + controller live on the
        # supervisor (shared with speech); the web UI just reads/writes them.
        # ------------------------------------------------------------------
        def _nav_available():
            nav = getattr(self.supervisor, "navigation", None)
            # The controller must exist AND be enabled (a disabled navigation
            # still has a controller object, so "exists" alone is not enough).
            return nav is not None and getattr(nav, "enabled", False)

        @app.route("/api/locations")
        def api_locations():
            if not _nav_available():
                return jsonify({"ok": False, "error": "navigation disabled", "locations": []})
            return jsonify({"ok": True, "locations": self.supervisor.locations.all()})

        @app.route("/api/locations", methods=["POST"])
        def api_locations_add():
            if not _nav_available():
                return jsonify({"ok": False, "error": "navigation disabled"})
            body = request.get_json(silent=True) or {}
            name = (body.get("name") or "").strip()
            lat, lon = body.get("lat"), body.get("lon")
            if not name or lat is None or lon is None:
                return jsonify({"ok": False, "error": "name, lat and lon are required"})
            ok = self.supervisor.locations.add(name, float(lat), float(lon))
            return jsonify({"ok": ok})

        @app.route("/api/locations/delete", methods=["POST"])
        def api_locations_delete():
            if not _nav_available():
                return jsonify({"ok": False, "error": "navigation disabled"})
            name = (request.get_json(silent=True) or {}).get("name", "")
            return jsonify({"ok": self.supervisor.locations.remove(name)})

        @app.route("/api/nav/start", methods=["POST"])
        def api_nav_start():
            if not _nav_available():
                return jsonify({"ok": False, "error": "navigation disabled"})
            name = (request.get_json(silent=True) or {}).get("name", "")
            ok = self.supervisor.nav_start(name)
            return jsonify({"ok": ok, "status": self.supervisor.navigation.status()})

        @app.route("/api/nav/stop", methods=["POST"])
        def api_nav_stop():
            if not _nav_available():
                return jsonify({"ok": False, "error": "navigation disabled"})
            ok = self.supervisor.nav_stop("web")
            return jsonify({"ok": ok, "status": self.supervisor.navigation.status()})

        return app

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the Flask server in a daemon thread (non-blocking)."""
        if self._thread is not None:
            return
        logger.info("Starting web UI on %s:%s", self.host, self.port)
        self._thread = threading.Thread(
            target=self.app.run,
            kwargs={"host": self.host, "port": self.port,
                    "debug": False, "use_reloader": False},
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Best-effort shutdown. The thread is a daemon, so process exit
        cleans it up; this just logs intent."""
        if self._thread is not None:
            logger.info("Web UI thread will exit with the process")
            self._thread = None
