# Navigation / "Guide me home" — Implementation Plan

The owl becomes a **compass that points at a named destination**. You teach it
places (name + lat/lon) through the web UI, then say *"Bring mich nach Hause"*
or *"Zeig mir den Weg zum Hotel"* and the owl enters a **NAVIGATING** mode in
which its head continuously turns to point at the destination — exactly like a
compass needle always pointing at north.

> **Status (2026-08-19): implemented.** Everything that can be built and tested
> without hardware is done and passing: the RPi core (geo math, locations store,
> navigation controller, speech start/stop, web UI map picker + endpoints) and
> the firmware `NAVIGATING` state + `nav` command (compiles clean under
> PlatformIO). The only remaining work is **on-hardware**: flash the firmware,
> verify the `aim_sign` convention (§10.1), and confirm the head actually tracks
> a live bearing. The build checklist in §9 is marked accordingly.

---

## 1. How it works (concept)

Two pieces, split across the two boards:

- **RPi (the "brain")** owns all the logic: the named-locations store, the
  *bearing* / *distance* math, and the decision "aim the head here". The RPi
  already parses every telemetry frame (GPS fix + IMU yaw), so it has everything
  it needs to compute a bearing.
- **ESP32 (the "body")** gets one new behavior: a **NAVIGATING** state in which
  it holds the head at whatever angle the RPi tells it to (a *persistent*
  override, not the 3-second expression/gaze override). The ESP32 does **not**
  do the math — it just points where told, and keeps pointing as the RPi updates
  the target.

The RPi sends a small, rate-limited stream of `{"type":"nav","angle":A}`
commands (A within the head's ±45° range). The owl's head servo follows A, so
the beak points at the destination. As you walk, your GPS position and the IMU
yaw change, the bearing changes, and the head re-points — a live compass.

> Why the RPi and not the ESP32? The ESP32 is already busy with face detection
> (esp-dl) + LCD rendering + I2C sensor polling at ~60 Hz. The RPi has spare
> CPU, already owns the serial command path, and is the natural home for
> "user data" (the locations). Keeping the math on the RPi means **no firmware
> math to get wrong** and the ESP32 change stays tiny (one new state + one new
> command).

---

## 2. Architecture

```
                        RPi (brain)
   ┌──────────────────────────────────────────────────────────────┐
   │  LocationsStore   (name -> {lat, lon})  — persisted to disk  │
   │        ▲ add/remove via Web UI        │ look up by name       │
   │        │                              ▼                       │
   │  Speech ──"bring mich nach <name>"──►  Navigation controller │
   │  (new cluster + <name> extraction)         │  bearing/dist    │
   │                                             ▼  (math, RPi)    │
   │  on_telemetry(gps fix, imu yaw) ──────────►  aim = bearing-yaw │
   │                                             ▼  clamp ±45, rate-limit
   │  serial.send_command({"type":"nav","angle":aim})  ──► ESP32
   └──────────────────────────────────────────────────────────────┘
        (one-way, ~1 Hz)
```

The controller runs on the RPi. It is driven by **telemetry arrivals** (a new
GPS/yaw sample every ~500 ms) — not by a separate timer — so it naturally
re-computes the aim whenever fresh sensor data lands, and is a no-op when there
is no fix.

---

## 3. The math (all on the RPi)

Given current position `(lat1, lon1)` (GPS) and destination `(lat2, lon2)`
(location store):

1. **Bearing** (initial great-circle bearing, degrees 0–360, 0 = north):
   ```
   φ1, φ2 = radians(lat1), radians(lat2)
   Δλ      = radians(lon2 - lon1)
   y = sin Δλ · cos φ2
   x = cos φ1 · sin φ2 − sin φ1 · cos φ2 · cos Δλ
   bearing = (atan2(y, x) + 2π) mod 2π        # radians → degrees
   ```
2. **Distance** (haversine, meters) — for "you're 120 m from home" and for the
   "arrived" detection:
   ```
   a = sin²(Δφ/2) + cos φ1 · cos φ2 · sin²(Δλ/2)
   d = R · 2 · asin(√a)                         # R = 6371000 m
   ```
3. **Aim the head**: the head servo angle that points the beak at the destination
   is the bearing **relative to where the owl is currently facing**:
   ```
   aim = wrap180(bearing − imu_yaw)      # −180..180, 0 = straight ahead
   aim = clamp(aim, head_min, head_max)  # default head_min=−45, head_max=+45
   ```
   `imu_yaw` is the BNO055 orientation yaw (already in telemetry, degrees).

**Sign convention is the one real risk** (see §10). If the head points the
*opposite* way to the destination, the fix is a one-line sign flip
(`aim = wrap180(imu_yaw − bearing)` or negate). This is settled on hardware by
pointing the owl at a known destination and watching. The plan treats the
convention as a single config flag (`aim_sign: +1/-1`) so it's a config change,
not a code change, once observed wrong.

**Yaw reference.** The aim uses the **raw BNO055 yaw** (0–360). We do **not**
try to make the owl "know" absolute north — the user just holds/stands the owl
facing some way, and the head points *bearing − current facing*, which is
correct regardless of what the body happens to face. (An optional refinement,
see §10: let the user say "calibrate" while facing true north to offset the yaw.)

---

## 4. Firmware changes (ESP32) — small, additive

**Done (implemented in `esp32-s3-sense/src/main.cpp`, compiles clean).** The
ESP32 had 7 states (BOOT, IDLE, DETECTING, INTERACTING, SLEEPING, UPDATE, ERROR)
and 7 command types; it now has 8 states and 8 command types. As planned:

1. **New state `NAVIGATING`** in `main.cpp`:
   - `enum class State { ..., NAVIGATING, ... }`; `stateToString` → `"navigating"`.
   - In `updateState()`: when `NAVIGATING`, apply a dedicated eye expression
     (e.g. `DETECTING`/"focussed", or a new `NAVIGATING` eye glyph — see §10)
     and **hold the head at `navHeadAngle`** (do NOT `servos.setCenter()` —
     that would snap the head back to center and defeat the compass).
   - **Entry** is via a new command (below); **exit** is via a new command and
     via a self-timeout (e.g. 120 s with no nav refresh → back to IDLE, so a
     dropped RPi can't leave the head stuck).
   - `transitionTo(NAVIGATING)` should **not** clear the nav target (it must
     survive the transition), unlike the expression/gaze overrides.
2. **New command `nav`** in `handleCommand()`:
   ```
   {"type":"nav", "angle": 30.0, "active": true}   // aim head at 30°, stay in NAVIGATING
   {"type":"nav", "active": false}                  // stop navigating → IDLE, head centers
   ```
   - `active:true` → if not already NAVIGATING, `transitionTo(NAVIGATING)`;
     set `navHeadAngle = clamp(angle, ±45)`; refresh `lastNavRefresh = millis()`.
   - `active:false` → if NAVIGATING, `transitionTo(IDLE)` (which re-centers).
   - Reply `nav_ack {active, angle}`.
   - While in UPDATE mode it is ignored (consistent with the other overrides).
3. **New telemetry field** (optional but useful for the web UI + tests):
   report `doc["nav"]["active"]` and `doc["nav"]["angle"]` so the RPi/web UI can
   see the owl's current nav state and aim.
4. **Bump `FW_VERSION`** (minor) in `config.h`.

Nothing else in the firmware changes. The face-detection / state machine /
expression / gaze paths are untouched; NAVIGATING is just one more state the
RPi can ask for.

> Sizing: this is ~40–60 lines of firmware. It must be re-flashed (OTA via the
> existing 4-tap update mode, or USB) before the RPi can use it.

---

## 5. RPi changes

### 5.1 `brain/locations.py` (new) — the named-locations store
- Loads/saves a JSON (or YAML) file, e.g. `~/.config/robot-owl/locations.json`:
  ```json
  { "home":  {"lat": 48.1351, "lon": 11.5820},
    "hotel": {"lat": 48.1400, "lon": 11.5810} }
  ```
- API: `add(name, lat, lon)`, `remove(name)`, `get(name)`, `names()`, `all()`.
- Names are normalized (lowercased, trimmed) for matching; stored as given.
- Persisted on every mutation so the map survives reboots.

### 5.2 `brain/navigation.py` (new) — the controller
- `Navigation(serial, supervisor, locations, config)`.
- Holds: `active`, `target_name`, `target (lat,lon)`, `last_aim_sent`, and the
  config knobs (`head_min`, `head_max`, `refresh_min_s`, `arrive_m`,
  `timeout_s`, `aim_sign`).
- `start(name)`: look up the location; if missing → no-op + (optionally) tell
  the owl "I don't know that place". If present: set active, send
  `nav active:true` + first aim, cue a sound, return.
- `stop()`: send `nav active:false`, clear state, cue a sound.
- `on_telemetry(t)`: **only while active** — if `t.gps.valid` and
  `t.imu.calibrated`, compute bearing + distance from `t.gps`/`t.imu.yaw` to the
  target, derive `aim`, and (rate-limited) send `nav angle:aim`. Also:
  - if `distance < arrive_m` → announce arrival + `stop()`.
  - if no `nav` refresh has been sent for `timeout_s` (link dropped) → `stop()`.
- The bearing/haversine math lives here (pure functions, unit-testable without
  hardware).

### 5.3 `brain/serial_handler.py` (extend)
- Add `nav(angle, active)` → `send_command({"type":"nav","angle":..,"active":..})`.
- (Optional) parse the new `nav` telemetry field into `Telemetry` if we add it.

### 5.4 `brain/supervisor.py` (extend)
- Own (or hold a ref to) the `Navigation` controller.
- In `on_telemetry()`, forward each frame to `navigation.on_telemetry(t)`.
- Expose `nav_start(name)` / `nav_stop()` for the web UI + speech.
- Keep auto-sleep from fighting navigation: while NAVIGATING, the owl should
  **not** be put to sleep by the inactivity policy (a face may not be in frame
  while you're walking). So `check_auto_sleep` must treat the `navigating` state
  as "active" (don't sleep), and the speech gate must allow reacting while
  navigating (see 5.5).

### 5.5 `brain/speech.py` (extend)
- Add a new cluster, e.g. `navigate`, with the trigger phrases:
  `"bring mich nach"`, `"bringe mich nach"`, `"zeig mir den weg nach"`,
  `"wie komme ich nach"`, `"wie komme ich zum"`, … (German, configurable).
- **Name extraction**: strip the trigger phrase from the transcript and treat the
  remainder as the location name, e.g. `"bring mich nach hotel"` → `hotel`.
  This is the fiddly part (Whisper may render "Hotel" as "hotel", "dem hotel",
  "zu dem hotel"). Strategy: match the remainder against the **known location
  names** (fuzzy: substring / token overlap / small edit distance) rather than
  trusting the raw remainder. If exactly one known name matches → navigate there.
  If 0 or >1 match → the owl says it doesn't know / is unsure.
- Reaction for the cluster = `navigation.start(name)`.
- Allow the reactive gate to include `navigating` (so "stop" / "danke" still
  works mid-walk).

### 5.6 `brain/web_ui.py` (extend) + `main.py` (wire up)
- New card **"Places"**: a table of name + lat + lon, an **Add** form, and a
  **Remove** per row. The Add form has **two ways to set the position**:
  - **Manual** — type lat + lon directly (the simple fallback), or
  - **"Pick on map"** — an embedded **OpenStreetMap** widget (Leaflet + the
    free OSM tile layer, no API key needed). You drag a marker (or click the
    map) and the lat/lon fill into the form automatically. See §12 for the
    embedding approach (works offline-free, no Google account).
  Endpoints: `GET /api/locations`, `POST /api/locations` (add),
  `POST /api/locations/remove`.
- New card **"Navigate"**: a **Start** dropdown (pick a place) + **Start** /
  **Stop** buttons, plus a live line: `to <name> · <dist> m · bearing <deg>° ·
  head at <aim>°`. Endpoints: `POST /api/nav/start {name}`, `POST /api/nav/stop`.
  `/api/telemetry` can include the current nav state for the live line.
- `main.py`: instantiate `LocationsStore` + `Navigation`, pass into
  `Supervisor` and `WebUI`, add `navigation.on_telemetry` to the read-loop
  callback chain, and stop navigation on shutdown.

### 5.7 `config.yaml` (new `navigation:` block)
```yaml
navigation:
  enabled: true
  locations_file: ""        # "" = default path under the brain's data dir
  head_min: -45
  head_max: 45
  refresh_min_s: 0.5        # min seconds between nav angle sends
  arrive_m: 15.0            # "you've arrived" distance
  timeout_s: 120            # stop navigating if no refresh for this long
  aim_sign: 1               # +1 or -1; flip if the head points the wrong way
  sound_start: "detecting"  # owl-call on entering navigation
  sound_stop:  "waking"
```
The `setup.sh` config wizard can grow a "Navigation" question (enable? add
default "home"?), matching the existing wizard pattern.

---

## 6. The full user flow

1. **Teach** (web UI): open the Places card, add `home` = 48.1351, 11.5820 and
   `hotel` = 48.1400, 11.5810. Saved to disk.
2. **Ask** (voice): *"Bring mich nach Hause."* → Speech matches the `navigate`
   cluster, extracts `hause`→fuzzy-matches `home`, calls `navigation.start("home")`.
3. **Point**: RPi sends `nav active:true` + `nav angle:<aim>`. ESP32 enters
   NAVIGATING, eyes go "focussed", head turns to aim. Every ~500 ms a new GPS+
   yaw sample re-computes the aim and the head tracks it — a live compass to
   home.
4. **Walk**: as your position and facing change, the head keeps pointing at home.
5. **Arrive**: distance < `arrive_m` → owl cued "arrived", `nav active:false`,
   head recenters, back to IDLE.
6. **Or cancel** — see §13 "Exiting navigation": a spoken keyword *and* the web
   UI *Stop* button *and* a self-timeout, whichever comes first.

---

## 12. Picking a place on a map (web UI)

The Places "Add" form gets a **Pick on map** button that opens an interactive
map; you drop a marker (or click) and the lat/lon fill in.

**Recommended: OpenStreetMap via Leaflet** (not Google Maps):
- **No API key, no account, no billing, no ToS risk.** Google Maps embeds need
  a JS API key and are restricted from being used in many non-browser/
  self-hosted contexts; OSM/Leaflet is free for exactly this use.
- **Works over the LAN** the owl is on: Leaflet's JS + the OSM tile server are
  fetched from the public internet by *your phone/laptop browser* (the device
  viewing the web UI), not by the RPi. So the RPi itself needs **no internet
  and no new dependencies** — it just serves a static map div. (The viewing
  device needs internet to load tiles, which it has.)
- **Implementation**: the web UI already serves one self-contained HTML page
  (`TEMPLATE`). We add a small `<div id="map">` to the Places card and load
  Leaflet from a CDN (`unpkg`/`cdn.jsdelivr`) with the OSM tile layer
  (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`). A click/marker-drop writes
  `lat`/`lon` into the Add form's inputs. ~30 lines of JS, no build step.
- **Fallback**: if the viewing device has no internet (pure-airgapped LAN), the
  map won't load tiles — the manual lat/lon entry still works, so the feature
  degrades gracefully. (Optional later: bundle a small offline tile option.)

**Endpoints** are unchanged by the map — it only pre-fills the same
`POST /api/locations {name, lat, lon}` the manual form uses.

> If you'd specifically prefer Google Maps (familiar UI / better street
> labels), that's doable too but requires a Google Cloud project + JS API key +
> accepting the embed ToS; OSM/Leaflet is the zero-friction default. Tell me
> which you'd like and I'll wire that one.

---

## 13. Exiting navigation mode

There is no single magic word — there are **four** ways out, and any one of them
ends the mode (they're redundant on purpose, so you're never stuck):

1. **Spoken keyword** (primary). A dedicated `stop_nav` cluster, e.g.
   `"stopp"`, `"stopp die navigation"`, `"reicht"`, `"danke"`, `"aus"`. The
   keyword must be **specific to navigation** (not the generic "stop" used by
   other reactions, so saying "stop" to make it drop a toy doesn't kill the
   compass). It calls `navigation.stop()`.
2. **Web UI** — the Navigate card's **Stop** button (also useful if you can't
   speak, e.g. in a loud place).
3. **Arrival** — automatically, when you get within `arrive_m` of the target
   (the owl announces it and recenters).
4. **Self-timeout** — the firmware leaves NAVIGATING after `timeout_s` with no
   `nav` refresh, so a dropped RPi link can't leave the head stuck pointing.

All four funnel to the same `nav active:false` command, so the firmware never
has to guess *why* it's exiting — it just recenters and returns to IDLE.

> Design note: the spoken keyword is the one you'll actually use 90% of the
> time; the other three are safety nets. The keyword list is config, so you can
> add your own ("Genug." "Lass uns gehen." …).

---

## 7. Edge cases & failure modes (must be handled)

- **No GPS fix** while navigating → don't send a stale aim; show "no GPS fix"
  in the web UI; keep the last head position (don't flail).
- **IMU not calibrated** (yaw unreliable) → the aim will be wrong; warn and
  optionally refuse to start (or start with a "calibrate your heading" cue).
- **Link drops** (no telemetry for `timeout_s`) → self-`stop()` on the RPi and
  the firmware's own NAVIGATING timeout re-centers the head (double safety).
- **Unknown location name** (speech extraction fails or name not in store) →
  owl says "I don't know that place" (a reaction), no mode change.
- **Ambiguous name** (two matches) → owl asks which one.
- **Destination behind the owl** (|aim| > 45) → head clamps to ±45 (points as
  close as its range allows). The web UI should say "turn around — it's behind
  you" rather than pretending to point there.
- **Navigation + auto-sleep** → navigating suppresses auto-sleep (you may be
  walking with no face in frame).
- **Navigation + face detected** → the owl is pointing at a destination, not at
  you; that's the intended behavior. (Optional: a brief "I'm navigating" blink.)
- **Rapid re-issue** (you say "go to home" then "go to hotel") → `start` first
  stops any active nav, then starts the new one.

---

## 8. Testing (no hardware, on the Mac)

Pure functions are trivially testable:
- **Bearing**: known pairs (north/south/east/west/diagonals) → expected degrees.
  e.g. same lat, lon+1 → bearing ≈ 90° (east).
- **Haversine distance**: known pairs (e.g. 1° of latitude ≈ 111,195 m).
- **Aim math**: `aim = wrap180(bearing − yaw)` with `aim_sign`, clamped to
  ±45; check the sign convention and clamping.

Integration (using the existing `stubs.py` pattern — `FakeSerial`,
`FakeTelemetry`, `FakeSupervisor`):
- `FakeSerial` gains a `nav(angle, active)` that records the command.
- `FakeTelemetry` gains `gps` (valid/lat/lon) and `imu` (yaw/calibrated).
- Tests: `start("home")` sends `nav active:true` then an aim; feeding a
  `FakeTelemetry` with a changed GPS/yaw updates the aim (rate-limited);
  distance < arrive_m → sends `nav active:false`; unknown name → no nav command;
  speech `"bring mich nach hotel"` → `start("hotel")` (name extraction).

Firmware (NAVIGATING state) can't be *unit*-tested on the Mac, but it **is**
compiled there via PlatformIO (`pio run` cross-compiles to the ESP32-S3), so
syntax/type errors are caught without hardware. The behavioral check (head
actually tracks a live bearing, `aim_sign` correct) is the on-hardware step.

---

## 9. What's needed (build checklist)

**Firmware (ESP32)** — implemented + compiles; **flash before the RPi can use it**:
- [x] `State::NAVIGATING` + `stateToString` → `"navigating"`
- [x] `nav` command handler (active true/false) + `nav_ack`
- [x] NAVIGATING branch in `updateState()` (hold head at `navTargetAngle`,
      `SEARCHING` eyes, self-timeout `NAV_TIMEOUT_MS` → IDLE)
- [x] `navigation` telemetry field (`active` + `angle`)
- [x] `FW_VERSION` bump (1.1.0 → 1.2.0)
- [ ] **On hardware:** flash (OTA 4-tap or USB) + verify `aim_sign` (§10.1)

**RPi (brain)** — all implemented + unit-tested (104 tests pass):
- [x] `brain/geo.py` — bearing / haversine / wrap / aim math (pure, tested)
- [x] `brain/locations.py` — store + JSON persistence
- [x] `brain/navigation.py` — controller (start/stop/on_telemetry, 4 exit paths)
- [x] `serial_handler.nav()` + `NavigationState` telemetry parse
- [x] `supervisor.py` — owns Navigation, forwards telemetry, nav_start/stop,
      auto-sleep suppressed while navigating
- [x] `speech.py` — nav triggers + stop keywords, checked before the reaction
      clusters; stop is exempt from cooldown + face-gate (works while asleep)
- [x] `web_ui.py` — Places card (add/remove/list + **OSM/Leaflet map picker**,
      §12) + Navigate card (start/stop + live status) + endpoints
- [x] `main.py` — wires store + controller, feeds telemetry
- [x] `config.yaml` — `navigation:` block + `speech.nav_*` keywords
- [x] tests — geo, controller (incl. the four exit paths), speech start/stop,
      web UI endpoints

**Docs**:
- [x] this plan → phases marked done (above)
- [ ] `README.md` — navigation section (teach places, ask, what you'll see)
- [ ] `BACKLOG.md` — add a navigation entry

---

## 10. What's missing / open questions (answer before or during build)

1. **Head sign convention** (the big one): does `+angle` turn the head left or
   right, and does BNO055 yaw increase clockwise or counter-clockwise? The
   `aim = wrap180(bearing − yaw) · aim_sign` formula has exactly one unknown
   sign. **Needs a 2-minute on-hardware check** (point the owl at a known
   direction, read the yaw, aim at a known bearing, see if it points right).
   Mitigated by the `aim_sign` config flag so it's a config fix, not a re-code.
2. **Yaw = absolute north?** BNO055 in IMU+ mode fuses the magnetometer, so yaw
   *should* be ~compass heading — but it can be offset by local magnetic
   distortion. **Question:** do you want a "calibrate while facing north" step
   (one-time offset), or is "head points bearing relative to however the owl is
   currently facing" good enough? (The latter is simpler and still useful as a
   compass; the former makes the head point at true bearing.)
3. **GPS accuracy / update rate.** The PA1010D is a basic GPS; expect ~3–10 m
   horizontal accuracy and 1 Hz updates. **Question:** is a 15 m "arrived"
   threshold acceptable, or do you want coarser/finer? (Indoor/under-bridge the
   fix may drop — handled by the no-fix edge case above.)
4. **Head range vs. "behind you".** The head only turns ±45°. A destination
   behind the owl can't be pointed at. **Question:** (a) just clamp + say
   "it's behind you", or (b) also rotate the **body** (there's no body yaw servo
   in the current 5-channel setup — head is the only rotation), so this is
   likely (a) unless you add a body-turn servo later.
 5. **Eye expression for NAVIGATING.** **Decided: reuse `SEARCHING`** (the
    "focussed" glyph) — no new `EyeExpression` needed, so the firmware change
    stays minimal. If a dedicated compass glyph is wanted later, adding a new
    `EyeExpression` + a `drawExpressionOverlay()` case is a small change.
6. **Language/trigger phrases.** The example phrases are German. Confirm the
   exact trigger set (and whether English "take me to…" / "show me the way to…"
   should also work) — these are just config strings in the `navigate` cluster.
7. **Name extraction robustness.** Whisper on the Pi can mangle "Hause" →
   "hauses", "dem Hotel" → "de hotel". The plan assumes fuzzy-matching the
   remainder against known names. **Question:** is that acceptable, or do you
   want the owl to *confirm* ("You mean **Hotel**? Say 'yes'") before pointing?
   Confirmation is safer but adds a second turn.
8. **Persistence location.** `~/.config/robot-owl/locations.json` (or under the
   brain's install dir)? Confirm the path the `setup.sh` install uses.
9. **Scope of "guide".** This plan is a **live compass** (head points at the
   destination and you walk toward it). It does **not** do turn-by-turn routing
   (no map, no "turn left in 20 m") — that would need a routing backend + maps
   and is a much bigger project. Confirm the compass behavior is what you want
   for v1.
10. **Map provider.** Plan defaults to **OpenStreetMap + Leaflet (free, no key, §12).
    Do you want that, or specifically **Google Maps** (needs a JS API key +
    Google Cloud project)? (Answering "OSM" keeps the RPi dependency-free.)
11. **Exit keyword.** Plan uses a dedicated `stop_nav` cluster (default
    `"stopp"`, `"stopp die navigation"`, `"danke"`, `"aus"`) *plus* the web-UI
    Stop button *plus* arrival *plus* a self-timeout (§13). Confirm the spoken
    keyword(s) — or add your own preferred phrase.

---

## 11. Suggested build order

1. **RPi math + store + controller** (pure logic, fully unit-testable on the
   Mac) — `locations.py`, `navigation.py`, tests for bearing/haversine/aim.
2. **Web UI Places + Navigate cards** (teach via manual **or map picker** +
   start/stop + live readout) — so the feature is usable/testable *without*
   voice, over HTTP. (The OSM/Leaflet picker is a self-contained JS add to the
   template, §12.)
3. **Firmware NAVIGATING state + `nav` command** — flash, then the RPi's
   `nav` commands actually move the head. Verify the **aim sign** here.
4. **Speech `navigate` + `stop_nav` clusters + name extraction** — the full
   hands-free flow, including the spoken exit keyword (§13).
5. **Polish**: auto-sleep interplay, arrival/cancel cues, config wizard, docs.

Steps 1–2 are testable on the Mac with no hardware; 3 needs the Pi/ESP32; 4
needs mic + (ideally) the Pi.
