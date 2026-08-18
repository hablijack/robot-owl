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

# Servo channel indices (must match esp32-s3-sense/include/config.h).
CH_LEFT_EAR = 0
CH_RIGHT_EAR = 1
CH_HEAD = 2
CH_LEFT_WING = 3
CH_RIGHT_WING = 4

# Absolute head servo angles for the arrow buttons (degrees, -45..45).
HEAD_LEFT = -30
HEAD_RIGHT = 30
HEAD_UP = -20
HEAD_DOWN = 20
HEAD_CENTER = 0

# Expressions offered in the UI (subset of the firmware's EyeExpression names).
EXPRESSIONS = ["neutral", "happy", "sleepy", "surprised", "angry", "searching"]

# Sound effects the RPi can play through the MAX98357A amp (see brain/audio.py).
SOUNDS = ["beep", "chirp", "happy", "sad", "alert"]

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
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .row select { padding:10px; border-radius:10px; background:#2a313b; color:var(--fg); border:none; }
  .pad { display:grid; grid-template-columns:repeat(3, 64px); grid-template-rows:repeat(3, 56px); gap:8px; justify-content:center; }
  .pad button { width:64px; height:56px; font-size:20px; }
  .pad .corner { visibility:hidden; }
  #msg { min-height:18px; font-size:13px; color:var(--muted); }
  .telemetry { font-size:13px; color:var(--muted); line-height:1.7; }
  .telemetry b { color:var(--fg); }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f6f8fa; --card:#ffffff; --fg:#1f2328; --muted:#57606a; }
    button { background:#e6edf3; color:var(--fg); }
    button:hover { background:#d0d7de; }
    button.primary { background:var(--accent); color:#fff; }
  }
</style>
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
    <div class="pad">
      <span class="corner"></span>
      <button data-act="head" data-v="up">&#9650;</button>
      <span class="corner"></span>
      <button data-act="head" data-v="left">&#9664;</button>
      <button data-act="head" data-v="center">&#9673;</button>
      <button data-act="head" data-v="right">&#9654;</button>
      <span class="corner"></span>
      <button data-act="head" data-v="down">&#9660;</button>
      <span class="corner"></span>
    </div>
  </div>
</main>

<script>
const $ = (s) => document.querySelector(s);
const exprs = {{ expressions | tojson }};
const sounds = {{ sounds | tojson }};

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
  b.textContent = s[0].toUpperCase() + s.slice(1);
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
    }
  });
});

// Poll live telemetry.
const servoNames = ['L-ear','R-ear','head','L-wing','R-wing'];
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
                 host: str = "0.0.0.0", port: int = 8080):
        self.serial = serial
        self.supervisor = supervisor
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
            return render_template_string(TEMPLATE, expressions=EXPRESSIONS, sounds=SOUNDS)

        @app.route("/api/telemetry")
        def api_telemetry():
            t: Telemetry = self.supervisor.last
            if t is None:
                return jsonify({"state": None})
            return jsonify({
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
            })

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
            direction = (request.get_json(silent=True) or {}).get("direction", "center")
            angles = {
                "left": HEAD_LEFT,
                "right": HEAD_RIGHT,
                "up": HEAD_UP,
                "down": HEAD_DOWN,
                "center": HEAD_CENTER,
            }
            angle = angles.get(direction, HEAD_CENTER)
            ok = self.serial.set_servo(CH_HEAD, angle)
            return jsonify({"ok": ok, "channel": CH_HEAD, "angle": angle})

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
