# Speech Recognition — Implementation Plan

Goal: the owl hears the user's voice and reacts (expression + owl-call), per the
spec's `handle_behavior_pipeline`. The spec is a sketch; this plan maps it onto
the **actual** architecture (see README "Software Decisions") so nothing existing
breaks.

---

## 1. Architecture decision (the important one)

**Where does the behavior pipeline live?**

The spec sketch puts the keyword pipeline, cooldown, and the ESP32 calls
(`EMOTION_*`, `QUERY_GAZE_DIRECTION`, `BLICKE_ZUM_NUTZER`) in one file. Those
commands **do not exist** in our firmware, and running this pipeline on the
ESP32 would violate two documented decisions:

- **Decision #7** — the ESP32 owns the behavior state machine; the RPi is a
  *supervisor* (logging, health, policy). The RPi "never drives the owl's
  behavior on its own."
- **Decision #8** — face detection is on the ESP32 to avoid a high-latency
  serial round trip.

Speech recognition **cannot** run on the ESP32 (no mic, no CPU headroom for
ASR), so it *must* run on the RPi. That means the spec's pipeline, as written,
requires moving a behavior decision to the RPi — a tension we must resolve
deliberately, not by accident.

**Chosen design: RPi runs ASR + a *temporary-override* pipeline.**

- ASR (mic → text) runs on the RPi in a thread.
- The RPi pipeline (cooldown → keyword cluster → action) reacts by sending the
  commands the firmware **already supports** as *temporary overrides*:
  - `{"type":"expression","value":"happy"}` → 3 s expression override
  - `{"type":"gaze","x":0,"y":0}` → "look at me" gaze override (3 s)
  - Audio plays locally on the RPi (MAX98357A amp) — no serial needed.
- The ESP32 **state machine is untouched**. The spec's `EMOTION_*` states map
  onto existing `expression` overrides; `QUERY_GAZE_DIRECTION` /
  `BLICKE_ZUM_NUTZER` map onto the existing `gaze` command. No firmware change
  is required for the core feature.

This keeps the documented separation (ESP32 = persistent behavior; RPi =
supervisor + *temporary* overrides) and is the lowest-risk path. The one
concession: the RPi now initiates *transient* reactions (like the web UI already
does), not persistent state. That is consistent with the existing override
mechanism and does not create a second competing state machine.

> Alternatives considered:
> - **Full pipeline on ESP32** — rejected: needs ASR (impossible there) and new
>   `EMOTION_*` states + a new protocol, inverting Decisions #7/#8.
> - **New `emotion` firmware command to force sleep from a spoken word** —
>   **rejected** (2026-08-19): a command that forces the owl to sleep is the
>   wrong model. The owl should fall asleep *autonomously* on inactivity and
>   wake on a real interaction. Phase 4 was re-scoped accordingly to be
>   RPi-side only (see §10 Phase 4) — it reuses the firmware's existing
>   `sleep`/`wake` commands and its existing self-wake on face/vibration, so
>   the firmware's state machine stays untouched.

---

## 2. Spec → existing-feature mapping

| Spec sketch | Real implementation |
|---|---|
| `send_to_esp32("EMOTION_GLUECKLICH")` | `serial.set_expression("happy")` (3 s override) |
| `send_to_esp32("EMOTION_TRAURIG")` | `serial.set_expression("surprised")` or `"angry"` — **no `sad`/`traurig` expression exists** (see §5) |
| `send_to_esp32("EMOTION_VERWIRRT")` | `serial.set_expression("surprised")` (no `confused` expression exists) |
| `send_to_esp32("QUERY_GAZE_DIRECTION")` + `BLICKE_ZUM_NUTZER` | `serial.set_gaze(0, 0)` (look center / at user) |
| `play_sound("zirp_*.wav")` | `audio.play("happy" / "alert" / ...)` — RPi-side amp |
| `COOLDOWN_TIME = 4.5` | Keep, but make it a config value (see §5) |
| `last_interaction_time` global | Move into a `Speech` class instance (no module global) |

Note the spec's keyword lists are **German** ("fein", "brav", "toll", "wer",
"wie", "was", "warum", "lass das", "aufhören"). ASR language + keyword matching
must therefore be German. This is a deliberate product choice — keep it
configurable.

---

## 3. ASR engine choice

The RPi 5 has enough headroom for offline ASR. Three options were considered:

| Option | Pros | Cons |
|---|---|---|
| **`faster-whisper` (tiny/base, int8 on CPU)** ✅ | Same Whisper models, **no torch**, CTranslate2 runtime, **up to 4× faster** + less RAM than openai-whisper, German-capable, offline | First-run model download (pre-cached by `setup.sh`) |
| `openai-whisper` (tiny/base, fp32) | Familiar API | Heavy torch + nvidia deps, slower + more RAM on the Pi's ARM CPU |
| Vosk (small de model) | Fast, lightweight, streams | German small-model accuracy is mediocre; more setup |

**Recommendation:** **`faster-whisper`** with the `tiny` (or `base`) model,
German, transcribe on a **short rolling window** (not continuous streaming).
Rationale:
- The owl reacts to *short utterances* ("toll!", "wer bist du?"), not long
  conversations. A 2–3 s window is plenty.
- `faster-whisper` runs the *same* Whisper models as `openai-whisper` but on the
  CTranslate2 engine: on a CPU with int8 it is up to 4× faster and uses far less
  RAM, and it installs **without torch** (no nvidia/cuda wheels) — a big win on
  the RPi's ARM CPU.
- The German `tiny` model is good enough for a handful of high-frequency words
  and is far more robust to the amp/room noise than a Vosk small model.
- Stays fully offline (no cloud, no key) — matches the project's local-first
  ethos.

API note: `faster_whisper.WhisperModel(model, device="cpu", compute_type="int8")`
and `segments, info = model.transcribe(audio, language="de")` where `segments`
is an iterator of `Segment` objects (each with a `.text`). We join the segment
text to form the transcript.

**Wake / VAD gating (critical for CPU + false triggers):** do not transcribe
24/7. Gate ASR on:
1. The owl is awake (`state != sleeping`, `state != update`), **and**
2. A face is detected (`telemetry.face.detected`), **and/or**
3. A simple energy/VAD threshold on the mic (so silence never triggers ASR).

This keeps the Pi 5 cool and quiet and prevents the owl "hearing" itself or
ambient TV.

---

## 4. Microphone acquisition

The XIAO ESP32-S3 Sense has an **I2S MEMS mic** (INMP431-style) on its I2S pins,
but the ESP32 has **no audio pipeline** in our firmware (audio is RPi-side by
Decision in README). Two options:

- **A (recommended): a separate USB/I2S mic on the RPi.** e.g. a USB headset mic
  or an I2S mic (INM431/SPH0645) wired to the **Pi's** I2S pins (shared with the
  amp is fine if time-division, but a dedicated USB mic is simplest). Capture via
  `sounddevice`/`pyaudio` into a ring buffer.
- **B: use the XIAO's on-board mic** by adding an I2S *capture* path in the
  firmware and streaming PCM over serial to the RPi. **Rejected for now** — it
  means firmware audio work + serial bandwidth, and contradicts "audio is
  entirely on the RPi." Keep as a future option if a standalone mic is
  undesirable.

Use **A** for this feature. Mic device is a config value (`audio.mic_device`).

---

## 5. Files to change (RPi side; firmware untouched for core)

### New
- `brain/speech.py` — `Speech` class:
  - Opens the mic (device from config), runs a capture loop in a daemon thread.
  - Maintains a short rolling buffer + a simple energy VAD.
  - When gated (awake + face + voice), slices a ~2–3 s window and runs Whisper
    (`tiny`, `language="de"`) → transcript.
  - Runs the **pipeline** (below) and emits the resulting override/sound.
  - Owns `last_interaction_time` + cooldown (instance state, not a global).
- `config/speech.yaml` (or a `speech:` block in `config.yaml`) — see §6.

### Modified
- `main.py` — after `Supervisor` is created, if `speech.enabled`:
  construct `Speech(serial, supervisor, config)` and `speech.start()`.
  Stop it in the `finally` block. (Mirrors the existing `web_server` pattern.)
- `brain/serial_handler.py` — no change needed (`set_expression`/`set_gaze`
  already exist). Add a `Telemetry` field only if we later expose ASR status.
- `brain/supervisor.py` — no change. (Optional: a `speak_reaction()` helper if
  we want the supervisor to own the keyword table; I'd keep the table in
  `speech.py` to avoid coupling.)
- `requirements.txt` — add `faster-whisper` + `ctranslate2` (no torch) +
  `sounddevice` (or `pyaudio`) + `soundfile` for mic capture.
- `setup.sh` — add `portaudio19-dev` (for `sounddevice`/`pyaudio`) to the apt
  list; `faster-whisper`/`ctranslate2` ship aarch64 wheels on PyPI (no apt, no
  torch). `setup.sh` also runs an **interactive config wizard** (serial port,
  web UI, speech, auto-sleep — useful defaults, Enter to accept) and
  **pre-downloads the Whisper model** so the first live transcription is instant.
- `config.yaml` — add a `speech:` section (see §6).

### Firmware
- **None.** Phase 4 (autonomous sleep on inactivity + wake on speech) is entirely
  RPi-side: it reuses the firmware's existing `sleep`/`wake` commands and its
  existing self-wake on face/vibration. The firmware's state machine is untouched.
  (An earlier draft proposed a new `emotion` firmware command to force sleep from
  a spoken word — that was rejected; see §10 Phase 4.)

---

## 6. Configuration (`config.yaml` → new `speech:` block)

```yaml
speech:
  enabled: false            # off by default; flip on once a mic is wired
  language: de
  model: tiny               # whisper model size (tiny/base/small)
  mic_device: ""            # ALSA/USB device index or name (from `arecord -l`)
  window_s: 2.5             # ASR clip length
  vad_threshold: 0.02       # RMS energy gate (tune per mic)
  cooldown_s: 4.5           # min gap between reactions (from the spec)
  require_face: true        # only react when a face is in frame
  clusters:
    happy:    ["fein", "brav", "toll", "super", "gute eule", "mag dich", "schön"]
    negative: ["nein", "lass das", "aufhören", "böse", "ach", "huch"]
    question: ["wer", "wie", "was", "warum"]
  # cluster -> (expression override, sound). Maps spec EMOTION_* to real names.
  reactions:
    happy:    { expression: happy,     sound: happy }
    negative: { expression: surprised, sound: alert }   # no 'sad' expr exists
    question: { expression: surprised, sound: alert }
  # Fallback when no keyword matches (spec's stochastic branch).
  fallback:
    gaze_center: true        # send gaze(0,0) = "look at me"
    idle_chance: 0.2         # spec: 0.8 act / 0.2 idle
```

---

## 7. The pipeline (faithful to the spec, adapted)

In `speech.py`, after Whisper returns a transcript:

```
segments, info = whisper_model.transcribe(window, language="de")   # faster-whisper
transcript = " ".join(seg.text for seg in segments).strip()
now = time.time()
if now - self.last_reaction < cooldown_s:      # spec: COOLDOWN_TIME
    return
text = transcript.lower()
if matches(text, "happy"):        reaction("happy");    return
if matches(text, "negative"):     reaction("negative"); return
if matches(text, "question"):     reaction("question"); return
# spec's else-branch: stochastic ambient fallback
if random.random() < (1 - idle_chance):
    serial.set_gaze(0, 0)          # BLICKE_ZUM_NUTZER (look at user)
# else: stay idle (organic rest)
```

`reaction(cluster)`:
- `serial.set_expression(cfg.expression)`  → ESP32 3 s override (eyes)
- `audio.play(cfg.sound)`                   → RPi amp (owl call)
- `last_reaction = now`

`matches(text, cluster)`: `any(kw in text for kw in keywords)` — same logic as
the spec. (Substring over lowercased text; good enough for high-frequency words.)

**Why this doesn't break existing behavior:**
- All actions are the *existing* 3-second overrides + local audio. The ESP32
  state machine, face-follow gaze, vibration wake, and OTA are untouched.
- Overrides already coexist with the state machine (`applyExpression`/
  `applyGaze` in `main.cpp` check `overrideExprUntil`/`overrideGazeActive`).
- Cooldown + face-gate prevent the owl from reacting to every ambient noise.
- `speech.enabled: false` by default → a no-op until a mic is wired and the
  flag is flipped, so nothing changes on existing deployments.

---

## 8. Gaps in the spec to flag (and how we handle them)

1. **`EMOTION_TRAURIG` / `EMOTION_VERWIRRT` don't exist.** The firmware has
   `neutral, happy, sleepy, surprised, angry, sleeping, searching, detecting,
   update, error`. We map negative→`surprised`/`angry` and question→`surprised`
   (configurable in `reactions`). If you want a distinct "sad" face, that's a
   small firmware addition (new `EyeExpression::SAD` + drawing) — Phase 4.
2. **`QUERY_GAZE_DIRECTION` is a no-op here.** The ESP32 already reports
   `face.gaze_x/gaze_y` in telemetry every 500 ms; the RPi can read
   `supervisor.last.face` instead of a round-trip query. We just send
   `gaze(0,0)` to aim at the user.
3. **The spec's 0.8/0.2 stochastic branch** is preserved as
   `fallback.idle_chance` (0.2 idle / 0.8 act).
4. **Audio file names** (`zirp_freude.wav` etc.) don't exist in
   `assets/sounds/`. We reuse the existing owl-call samples
   (`owl_happy.wav`, `owl_alert.wav`, ...) via `audio.play(name)`.
5. **Language is German** — Whisper must run with `language="de"` and the
   keyword lists are German. If English is ever wanted, it's a config change.

---

## 9. Threading / performance (Pi 5)

- Mic capture: daemon thread, blocking read into a ring buffer (no GIL
  contention with the serial loop — `pyaudio`/`sounddevice` release the GIL).
- ASR: run **only** when the VAD+face gate opens, on the same daemon thread
  (or a dedicated worker). Whisper `tiny` on a 2.5 s clip ≈ 200–400 ms on a
  Pi 5 — acceptable, and it's gated so it's rare. Never run in the foreground
  serial read loop.
- Never block `serial.read_loop()`: all speech work is in threads; the serial
  loop only sees the resulting `set_expression`/`set_gaze`/`audio.play` calls.
- If `tiny` is too slow on the target Pi, drop to `tiny` int8 or shorten
  `window_s`; if too inaccurate, bump to `base`.

---

## 10. Phased delivery

**Phase 1 — Skeleton (no hardware needed, safe to merge) — DONE**
- Added `speech:` config block to `config.yaml` (disabled by default).
- Added `brain/speech.py`: the `Speech` class with the full pipeline
  (cooldown → face-gate → keyword cluster → reaction → stochastic fallback),
  config-driven clusters/reactions, and `start()`/`stop()`/`feed()` lifecycle.
  `feed(transcript)` is the seam Phase 2's ASR thread will call.
- Wired `main.py` to construct/start/stop `Speech` when `speech.enabled`.
- Added `sounddevice`/`soundfile`/`faster-whisper`/`ctranslate2` to
  `requirements.txt` (imported lazily) and `portaudio19-dev` to `setup.sh`
  (for Phase 2's mic).
- *Verified:* all changed files compile; pipeline unit-tested with stubbed
  serial/supervisor (happy/negative/question clusters, cooldown, face-gate,
  fallback, and disabled-no-op all pass).
- **Improvement over the spec:** the spec's keyword test was a plain substring
  (`word in text`), which over-matches in German ("wie" inside "völlig", "was"
  inside "irgendwas"). The implementation uses word-boundary matching
  (`_keyword_hit`) — single words match whole words, multi-word phrases
  ("gute eule") still match as substrings. Verified the false positives are gone.

**Phase 2 — Mic capture + VAD + Whisper (code DONE; hardware verify pending)**
- `start()` loads the **faster-whisper** `tiny` model (CPU/int8) on the worker
  thread and opens a `sounddevice.InputStream` (16 kHz mono, 0.3 s block) whose
  callback pushes chunks to a bounded queue; a dedicated worker thread runs the
  VAD loop. `transcribe()` returns a segment iterator, joined into the
  transcript. `stop()` sets a stop flag and waits for the worker to drain the
  queue.
- **Energy VAD** (`_process_chunk`): an utterance opens on the first voiced
  chunk (RMS ≥ `vad_threshold`) and closes on sustained trailing silence or
  when `window_s` is reached. Gated by `_gate_open()` (owl awake/interactive
  + face in frame when `require_face`).
- **Timing gotcha (fixed):** silence is measured in *audio duration*
  (`_silence_frames / sample_rate`), not wall-clock. The worker can process
  chunks faster or slower than real time (burst, slow Pi, or the test's
  25 ms feed), so a `time.time()`-based silence timer under-counted the gap
  and never closed the utterance. Measuring in audio frames keeps the VAD
  correct and feed-rate-independent.
- *Verified on the Mac (no hardware):* `tests/test_speech_asr.py` drives the
  real worker loop end-to-end with a synthetic mic (silence → voice burst →
  trailing silence) and a stubbed Whisper. All 21 tests pass: voice →
  transcribed → correct reaction (happy/surprised); pure silence, asleep
  state, and no-face all correctly yield **no** reaction (gate holds).
- *Still to verify on hardware:* speak "toll" / "wer bist du" / "lass das"
  with a face in frame → correct eyes + owl call; 4.5 s cooldown respected;
  10 min of TV/room noise with no face → zero reactions; `journalctl` shows no
  serial stall. Tune `vad_threshold` against the real mic's noise floor.

**Phase 3 — "last heard" in the web UI (code DONE; hardware verify pending)**
- `Speech` now records `last_heard_at` (epoch seconds) alongside `last_heard`
  whenever a transcript is fed, so the web UI can show *how recently* the owl
  heard something.
- `WebUI.__init__` takes an optional `speech=` (main.py passes the Speech
  instance, or None when speech is disabled/failed). `/api/telemetry` adds
  `last_heard: {text, at}` **iff speech is wired in** — the payload is byte-for-
  byte unchanged for deployments that don't use speech.
- The control page renders a "heard *…*" line (with a "Ns ago" that refreshes
  each 1 s poll) in the Live status card; it stays hidden until the owl hears
  something / when speech is off.
- *Verified on the Mac:* 4 new unit tests (in `test_speech_asr.py`) exercise the
  `/api/telemetry` view function directly (Flask is stubbed): present-and-set,
  present-but-empty, omitted-when-speech-off, and the `feed()` timestamp stamp.
  All 25 tests pass.
- *Still to verify on hardware:* with `speech.enabled: true` + `web.enabled:
  true`, speak to the owl and confirm the Live status card shows the transcript
  and a sensible "Ns ago".

**Phase 4 — Autonomous sleep on inactivity + wake on speech (code DONE; hardware verify pending)**

*RPi-side, no firmware change.*

*Revised 2026-08-19.* The earlier sketch ("a command that sends the owl to sleep
on a spoken word") was rejected: **there should be no command that forces the owl
to sleep.** Instead the owl falls asleep *by itself* when the environment goes
quiet, and wakes *immediately* when something interacts with it.

### What "active" and "asleep" mean

- **Active (awake):** the owl is in one of the reactive states
  (IDLE / DETECTING / INTERACTING) — i.e. **not** SLEEPING and **not** UPDATE.
- **Asleep:** SLEEPING.
- **An "interaction trigger"** (wakes / keeps awake) is any of:
  - a **face** detected (ESP32 camera),
  - **vibration** (the SW420 tap sensor), or
  - **speech** — the RPi hears the user. (This is the one trigger the firmware
    does *not* currently wire to a wake — see below.)

### Key insight: this is ~all on the RPi, and needs **zero firmware changes**

Two facts make that possible:

1. **The owl already self-wakes from SLEEPING on a face or a vibration.**
   `updateState()` in `main.cpp` transitions `SLEEPING --(wake)--> IDLE`, and
   the IDLE case immediately re-enters DETECTING when `vib.detected` or
   `faceResult.detected` is true. So the "react immediately to an interaction
   trigger" behavior for face + vibration **already exists** — nothing to build.
2. **Telemetry (every 500 ms) already reports everything the RPi needs** to judge
   "is anything happening?": `state`, `vibration.detected`/`count`, and
   `face.detected`. The RPi additionally knows about **speech** via its own
   `Speech` worker (`last_heard_at`). So the RPi can run the inactivity timer
   entirely from data it already receives.

The **only** real gap is: the firmware does not wake on *speech*. The RPi closes
that gap by sending the existing `wake` command when it hears the user while the
owl is asleep. No new firmware command, no new ack, no re-flash behavior change.

### The design (all RPi-brain)

Add a small autonomous "inactivity → sleep" policy to the RPi (a method on
`Supervisor`, driven from the existing `on_telemetry` callback — no new thread):

- **Config** (`config.yaml` → `supervisor:` block):
  - `auto_sleep.enabled` (default **false**, so existing deploys are unchanged)
  - `auto_sleep.after_s` (default e.g. **60** — how long with *no* interaction
    triggers before the owl is put to sleep)
- **Inactivity timer.** Track `last_activity = max(last face seen, last vibration,
  last speech heard)`. On each telemetry frame:
  - if a face is detected **or** vibration is detected → `last_activity = now`.
  - if `supervisor.last_state` is a reactive state and
    `now - last_activity > after_s` **and** the owl is not in UPDATE mode →
    `serial.send_command({"type":"sleep"})` (reuse the existing `sleep` command;
    the firmware already handles `any --(sleep)--> SLEEPING`).
  - While SLEEPING, do **not** send `sleep` again (idempotent no-op guard).
- **Wake on speech.** In the `Speech` worker, when an utterance is transcribed
  while `supervisor.last_state == "sleeping"`, send `{"type":"wake"}` (the
  existing command) *before* running the normal reaction pipeline. This is the
  new capability: a spoken word now wakes the owl, which the firmware alone
  could not do. (Face/vibration already wake it on their own.)

### The one deliberate design decision: the speech gate while asleep

Phases 1–2 deliberately **exclude SLEEPING** from `REACTIVE_STATES` so a sleeping
owl is not startled by ambient words. Phase 4 keeps that for *reactions* but adds
a narrow **wake-exception**: a transcript heard while asleep triggers a `wake`
(and then a normal reaction, since the owl is now awake). To avoid the owl
"hearing" the TV while asleep and waking itself, the wake-exception should be
**stricter than the normal gate** — e.g. require a high-confidence, clearly
addressed keyword (a name / "Eule" / "wacht auf") rather than any voiced chunk.
This is the single riskiest knob; keep it conservative and config-driven.

### Implementation pieces (RPi-brain only)

| Piece | File | Size | Notes |
|---|---|---|---|
| `auto_sleep.*` config | `config.yaml` | few lines | disabled by default |
| inactivity timer + `send sleep` | `supervisor.py` (`on_telemetry`) | ~20 lines | reuses `last_state`, `last.face`, `last.vibration` |
| expose "last activity" to speech | `supervisor.py` | ~3 lines | a `last_activity` property/attr |
| wake-on-speech + wake-exception gate | `speech.py` (`_process_chunk`/`feed`) | ~15 lines | sends existing `wake` cmd |
| config plumbing in `main.py` | `main.py` | ~2 lines | pass supervisor cfg to the policy |

### Testing (Mac, no hardware) — **done**, 13 tests in `tests/test_speech_autosleep.py`

Drive the **real** `Supervisor` with a `FakeSerial` + a scripted telemetry stream
(`on_telemetry` + `check_auto_sleep`), so the policy logic under test is the real
code — only the serial port and the clock are faked. Wake-on-speech is exercised
by calling `Speech.feed()` directly with a canned transcript and a supervisor
forced into the "sleeping" state.

| Scenario | Test | Result |
|---|---|---|
| No triggers for `after_s` → `sleep` sent | `test_no_activity_sends_sleep` | ✅ |
| Face / vibration within window → **no** sleep (timer resets) | `test_face_resets_timer_no_sleep`, `test_vibration_resets_timer_no_sleep` | ✅ |
| Already asleep → no re-send | `test_no_sleep_while_already_sleeping` | ✅ |
| Not spammed on every idle call (one per window) | `test_sleep_not_spammed_within_a_window` | ✅ |
| UPDATE mode → never sleeps | `test_no_sleep_in_update_mode` | ✅ |
| Disabled → no-op | `test_disabled_is_noop` | ✅ |
| Asleep + wake keyword → `wake` sent, owl no longer asleep | `test_wake_keyword_wakes_asleep_owl`, `test_single_wake_keyword_wakes` | ✅ |
| Asleep + ambient (non-addressing) speech → **no** wake | `test_ambient_speech_does_not_wake` | ✅ |
| Asleep + bare wake keyword (no face) → only `wake`, no reaction | `test_wake_keyword_alone_sends_only_wake` | ✅ |
| Asleep + wake keyword + happy phrase + face → `wake` **then** `happy` | `test_wake_then_reaction_when_face_present` | ✅ |
| Already awake → a wake keyword is a no-op (no redundant `wake`) | `test_wake_noop_when_already_awake` | ✅ |

Firmware is untouched, so there is **no firmware test surface** — the only
on-hardware check is behavioral (see below).

### Hardware verification (when the Pi arrives)

- Leave the owl alone with no face / no taps / no speech for `after_s` → it
  plays the sleeping cue and its eyes close (SLEEPING) on its own.
- Tap it (vibration) **or** show a face → it wakes immediately (existing path).
- Say the wake keyword → it wakes (new Phase 4 path) and reacts.
- `journalctl -u robot-owl-brain` shows the inactivity log + the `sleep` send,
  and telemetry keeps flowing at 500 ms (no serial stall).
- Tune `after_s` per room; confirm it does **not** sleep while you're clearly
  interacting (face present).

### Why this is safer than the old Phase 4

The old plan added a firmware command that could force a state from the RPi —
crossing the "ESP32 owns the state machine" boundary and requiring a re-flash.
This revision only (a) reuses the firmware's *existing* `sleep`/`wake` commands
and (b) reuses the firmware's *existing* self-wake on face/vibration. The RPi
merely *chooses when* to send `sleep` (on inactivity) and *adds speech* as a
wake source. The firmware's state machine, transitions, and autonomy are
completely unchanged.

---

## 11. Testing & verification

- **Regression (Phase 1):** `speech.enabled: false` → brain runs exactly as
  today (telemetry, state sounds, web UI, OTA all unchanged).
- **Pipeline unit test (Phase 1):** mock `SerialHandler` + `Audio`; feed
  canned transcripts; assert correct expression/gaze/sound + cooldown.
- **ASR accuracy (Phase 2):** a small German phrase set; measure WER on the
  target Pi; confirm the high-frequency keywords in the clusters are recognized.
- **No false triggers (Phase 2):** 10 min of ambient audio (TV/quiet room) with
  no face → zero reactions (VAD + face gate hold).
- **No serial stall (Phase 2):** `journalctl -u robot-owl-brain` shows telemetry
  still arriving at 500 ms cadence during an ASR run.

## 12. Risks

| Risk | Mitigation |
|---|---|
| ASR too slow / hot on the Pi | **faster-whisper** int8 (4× faster, less RAM than openai-whisper, no torch), short window, gate on face+VAD; `enabled:false` default |
| Mic picks up the owl's own amp (feedback) | amp + mic are separate paths; gate ASR on *face* not on our own output; lower mic gain |
| False triggers from ambient speech | face-gate + VAD threshold + 4.5 s cooldown; tune per room |
| ASR dep bloats install | **faster-whisper** (no torch) is much lighter than openai-whisper; pinned in requirements; `enabled:false` so it's optional at runtime |
| German keywords too narrow | clusters are config-driven → extend without code changes |
