# Robot Owl — Backlog

Cross-session working backlog. Update the status as items get done so any
session can pick up where the last one left off.

Status legend: `[ ]` open · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Eyes rendering

- [x] **UPDATE spinner eyelid clipping** — the green spinner ring (radius
  `SCLERA_R+5`) was drawn *after* the eyelids, so the full-width eyelid
  `fillRect` overdraw the top/bottom of the ring. Fixed: `renderEye()` now
  draws the UPDATE overlay *before* the eyelids (other overlays still after).
  `lib/Eyes/Eyes.cpp`
- [x] **ERROR red-eyes state** — `State::ERROR` was an empty stub ("Flash red
  eyes (not implemented yet)"). Added `EyeExpression::ERROR` + `COLOR_ERROR`
  (red) and a red-X face in `drawExpressionOverlay()`; `main.cpp` ERROR case
  now calls `applyExpression(ERROR)` + `servos.setCenter()`.
- [x] **Dead iris gradient** — `drawIris()` drew concentric outline rings that
  were immediately covered by the flat `fillCircle`, so the "gradient" never
  showed. Removed the dead loop.
- [x] **Asymmetric gaze range** — RESOLVED: intentional. Vertical travel is
  deliberately smaller than horizontal (6 vs 8 px) for a "natural" look (real
  eyes move less up/down). Documented in `Eyes.cpp`; do NOT make it symmetric.
- [x] **Blink speed param unused** — wired up. `blink(speed)` (1=fast…5=slow)
  now sets `_blinkSpeed`, which paces both the animation duration and the
  eyelid openness curve in `renderEye()`. The RPi `blink` command's `speed`
  field is now honored.

## Module integration

- [x] **Firmware version in telemetry** — added `FW_VERSION` to `config.h` and
  `doc["fw"]` to `sendTelemetry()`. RPi `Telemetry.firmware` parses it and the
  supervisor logs it once (and on change, to confirm an OTA took).
- [x] **RPi-side update tooling** — DROPPED (not needed). Updates are done by
  joining the owl's SoftAP from a phone/laptop hotspot and flashing via the
  browser `/update` page. No RPi push tooling required.
- [ ] **`webServer.stop()` doesn't unregister `/update`** — handled with the
  `updateServerReady` one-shot guard (`main.cpp`), so it works, but the handler
  stays registered forever. Low priority; revisit if a second `WebServer` or a
  path change is ever added.
- [ ] **4-tap trigger not hardware-validated** — SW420 debounce / tap-counting
  in `Sensors.cpp` is written but untested on the real sensor. First thing to
  verify once the mechanical build is wired.

## Face detection

- [~] **Tune esp-dl MSR01** — first-pass tuning done: model knobs moved to
  `config.h` (`FACE_SCORE_THRESHOLD 0.5`, `FACE_NMS_THRESHOLD 0.3`,
  `FACE_TOP_K 5`, `FACE_RESIZE_SCALE 0.3`) plus a post-filter
  `FACE_MIN_CONFIDENCE 0.5` gate that stops low-confidence flicker from
  flipping the state machine. A `config.h` shim was added in `lib/FaceDetector/`
  so the lib sees the project `config.h`. **Still needs on-hardware
  validation**: if the owl under-detects (ignores you), lower the thresholds
  toward 0.3; if it false-triggers on hands/pictures, raise them.
- [ ] **RPi face-detection fallback** — explicitly NOT needed (ESP32 does it).
  Only revisit if on-device detection is later disabled.

## Hardware

- [ ] **Mechanical assembly** — 3D print / enclosure, servo mounting for
  ears/head/wings, LCD bezels. (Wiring is documented in `WIRING.md`.)
- [ ] **First-run checklist** — no step yet for testing update mode end-to-end
  (4-tap → join AP → flash → tap to exit). Add one once hardware is assembled.

## Code review (2026-08-18)

- [x] **`fillScreen` dead + wrong `memset`** — `GC9D01.cpp` did a
  `memset(_fb, color>>8, ...)` then immediately overwrote every pixel with a
  full `for` loop. The `memset` was both wasted work (ran on every eye
  redraw) and the wrong value (only the high byte). Removed the `memset`;
  the loop alone is correct.
- [x] **`ServoController` bounds mismatch** — `setAngle` guarded with
  `channel >= 5` but `getAngle` used `channel >= 6`, and the arrays were
  `[6]` while only 5 channels are real. Added `NUM_SERVO_CHANNELS 5` to
  `config.h` and used it for the array sizes and all bounds checks.
- [x] **Duplicated `#ifndef` blocks in `Eyes.cpp`** — the whole
  `EYE_*`/`SCLERA_R`/`COLOR_*` guard block already existed in `common.h`
  (included via `Eyes.h`). Removed the redundant block from the `.cpp`.
- [x] **Hand-rolled JSON acks in `main.cpp`** — `handleCommand` built
  `expression_ack`/`servo_ack`/`sleep_ack`/`wake_ack`/`blink_ack`/
  `heartbeat_ack` with `String` concatenation while telemetry used
  ArduinoJson. Switched all acks (and the `invalid_json` error) to
  `JsonDocument` + `serializeJson` for consistency.
- [x] **Dead `Eyes::sleep()`** — declared in `Eyes.h` but never called; the
  `SLEEPING` state drives `_sleeping` via `setExpression`. Removed it.
- [x] **RPi drops non-telemetry messages** — `serial_handler.py::read_loop`
  only parsed `type == "telemetry"` and silently discarded everything else.
  Added a `_handle_message()` dispatcher: telemetry still goes to the
  callback, while `boot`, `update_mode`/`update_mode_end`, `error`, and any
  `*_ack` are now logged (acks at debug level).
- [x] **`parseSerialCommands` silent buffer drop** — `main.cpp` dropped any
  inbound line longer than 255 chars with no log. Now emits
  `{"type":"error","msg":"line_too_long"}` before clearing, so a
  truncated/malformed frame is diagnosable instead of silent.

## RPi-brain UX (2026-08-18)

Ideas to make the RPi supervisor nicer to run and debug.

### Service & startup

- [x] **One-command setup script** — `rpi-brain/setup.sh` is the single entry
  point for a fresh Raspberry Pi OS install: `sudo ./setup.sh`. It runs apt
  (python3-venv/alsa-utils/rsync), adds the I2S `dtoverlay` to the correct
  `config.txt` (`/boot` or `/boot/firmware`), installs the tree to
  `/opt/robot-owl` + venv + requirements, creates the `robotowl` user in the
  `dial` group, installs the udev rule, enables the systemd unit, then reboots
  to apply I2S — with a one-shot boot hook that starts the robot and clears
  itself. Idempotent. `deploy/install.sh` remains as the no-reboot/no-apt
  variant for manual installs.
- [x] **systemd service** — `deploy/robot-owl-brain.service` + `deploy/install.sh`
  (copies the tree to `/opt/robot-owl/rpi-brain`, builds a `.venv`, creates a
  `robotowl` system user in the `dial` group, installs a udev rule for the
  ESP32 USB CDC port, then `enable`s the unit). Re-runnable. **Not yet run on
  real hardware** — verify `install.sh` + `systemctl start` on the RPi.
- [x] **Startup banner** — `brain/banner.py` prints an ASCII owl + firmware
  version / serial port / config path / web-UI status once at launch (stdout,
  so it shows in `journalctl`).
- [x] **Nicer debug loglines** — `Supervisor` now keeps the latest frame in
  `.last`, emits a 30s one-line liveness status (state, uptime, face, fw,
  servo angles), and `check_stale()` (run from the read-loop idle path) warns
  when no telemetry arrives for >10s (suppressed while in UPDATE mode, where
  silence is expected).

### Web UI (manual feature tester)

A LAN-only Flask page (`brain/web_ui.py`, started from `main.py` when
`web.enabled: true` in `config.yaml`) to poke the owl manually. Every action
forwards the same NDJSON command the supervisor uses, so no firmware change is
needed. The page polls `/api/telemetry` (1s) to show live state/face/servos.

- [x] **Web UI scaffold** — Flask app in a daemon thread; `/` (control page)
  + `/api/telemetry`, `/api/blink`, `/api/expression`, `/api/head`,
  `/api/sleep`, `/api/wake`. Routes verified with a stub serial (all 200,
  correct NDJSON forwarded).
- [x] **"Blink once"** — button + speed selector (fast…very slow) →
  `{"type":"blink","speed":N}`.
- [x] **"Look <expression>"** — button grid (neutral/happy/sleepy/surprised/
  angry/searching) → `{"type":"expression","value":...}` (3s overrides).
- [x] **"Make a sound"** — audio is **RPi-side only**: the MAX98357A I2S amp
  is wired to the Pi (see the new **Audio** section in `WIRING.md`), so no
  firmware change is needed. `brain/audio.py` synthesizes short effects
  (beep/chirp/happy/sad/alert) in-process as 16-bit mono WAV and plays them via
  `aplay` in a daemon thread (never blocks the serial read loop); it degrades
  to a logged no-op if `aplay`/I2S is unavailable. `Supervisor.play_sound()`
  wraps it, `/api/sound` exposes it to the web UI (button grid), and the
  supervisor auto-cues a matching effect on state transitions
  (`detecting`→chirp, `interacting`→happy, `sleeping`→sad, `update`/`error`
  →alert). Config: `audio.enabled` / `audio.device` / `audio.volume` in
  `config.yaml`. **On-hardware TODO:** enable I2S
  (`dtoverlay=hifiberry-i2s-lite` in `config.txt`), wire the amp (SD MODE →
  3.3V!), then confirm `aplay -l` lists a bcm2835 device and the buttons are
  audible.
- [x] **"Move the head (arrow buttons)"** — 3×3 pad (up/left/center/right/
  down) driving the head servo (`CH_HEAD`) with absolute angles
  (±30° L/R, ±20° up/down, 0 center) → `{"type":"servo","channel":2,"angle":...}`.
- [x] **Web UI safety/scope** — policy buttons (sleep/wake) exposed as
  `/api/sleep` + `/api/wake` (endpoints ready; page buttons optional). No
  auth (LAN only) — do not expose the port beyond the local network.

**On-hardware TODO for the web UI:** enable `web.enabled: true`, then confirm
the page loads and each button visibly drives the owl (blink/expression/head).

---

## Recently completed (context for future sessions)

- [x] **OTA update mode** — 4-tap vibration → SoftAP `RobotOwl-Update` +
  `/update` HTTP page (HTTPUpdateServer); one tap exits; dual-bank
  `ota_0`/`ota_1` partitions; standalone boot (5s USB wait, no RPi needed).
- [x] **RPi supervisor update-mode handling** — `Telemetry.update` dataclass
  parses the `update` object; `Supervisor` logs the AP ssid/password/url on
  entry and a confirmation on exit.
- [x] **README corrected** — state machine documented as 7 states (was 6),
  OTA line flipped from "not implemented" to implemented, protocol + status
  tables updated.
