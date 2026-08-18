# Robot Owl — Embedded Firmware & Brain

A robotic owl companion with expressive LCD eyes, face detection, IMU, GPS, and servo-controlled ears/head/wings. The ESP32-S3 Sense owns the behavior state machine and runs on-device sensor fusion and eye rendering, communicating NDJSON telemetry to a Raspberry Pi "supervisor" for logging, health monitoring, and policy.

---

## Hardware

### Main Board
- **Seeed Studio XIAO ESP32-S3 Sense** — ESP32-S3 dual-core @ 240 MHz, 8 MB Flash, 8 MB PSRAM (OPI), USB-CDC on boot, built-in OV2640 camera connector

### Peripherals
| Component | Model | Interface | Address / Pins | Function |
|---|---|---|---|---|
| **LCD Eyes** | Waveshare 0.71" round (×2) | SPI (shared bus) | SCK=5, MOSI=7, DC_L=6, CS_L=8, DC_R=9, CS_R=44, RST=43 | Expressive eyes with GC9D01 driver |
| **Camera** | OV2640 (on-board) | SCCB/I2C + parallel | XCLK=10, SIOD=40, SIOC=39, D0-D7 on GPIO 15-18,38-48, VSYNC=38, HREF=47, PCLK=13 | Face detection input |
| **IMU** | Adafruit BNO055 | I2C | SDA=1, SCL=2, addr 0x28 | Orientation (pitch/roll/yaw) |
| **GPS** | Adafruit PA1010D | I2C | SDA=1, SCL=2, addr 0x10 | Position/satellites |
| **Servo Driver** | PCA9685 | I2C | SDA=1, SCL=2, addr 0x40 | 5-channel PWM for servos |
| **Vibration** | SW420 | Digital GPIO | Pin 4 (input) | Wake-on-vibration trigger |

### Servo Channels (PCA9685)
| Channel | Function | Range |
|---|---|---|
| CH0 | Left ear | -45° to +45° |
| CH1 | Right ear | -45° to +45° |
| CH2 | Head tilt | -45° to +45° |
| CH3 | Left wing | -45° to +45° |
| CH4 | Right wing | -45° to +45° |

### I2C Bus (GPIO1/2)
All three I2C devices share the same bus: BNO055 @ 0x28, PA1010D @ 0x10, PCA9685 @ 0x40. Clock: 400 kHz.

### Power
- LCD backlights tied directly to 3.3V (always on)
- Servos powered separately (not from ESP32 3.3V rail)

---

## Software Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    RPi Brain (Python)                    │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Serial Handler│  │  Supervisor  │  │  Policy / OTA │  │
│  │ (NDJSON parse)│  │(log + health)│  │(sleep/wake)   │  │
│  └──────┬───────┘  └──────┬───────┘  └───────┬───────┘  │
│         └──────────────────┼──────────────────┘          │
│                    USB Serial (115200)                   │
└──────────────────────────┬──────────────────────────────┘
                           │ NDJSON
┌──────────────────────────▼──────────────────────────────┐
│              ESP32-S3 Firmware (Arduino)                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
 │  │ Serial Parser│  │State Machine │  │ Face Detector │  │
 │  │(NDJSON recv) │  │(7 states)    │  │ (esp-dl MSR01)│  │
│  └──────┬───────┘  └──────┬───────┘  └───────┬───────┘  │
│         └──────────────────┼──────────────────┘          │
│                    Telemetry Sender                      │
│              (500ms interval, NDJSON)                    │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────────────┐  │
│  │ GC9D0│ │ Eyes │ │Sensors│ │Servo │ │ FaceDetector │  │
│  │ LCD  │ │Render│ │IMU/GPS│ │Ctrl │ │   (optional)  │  │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### State Machine (ESP32)
Seven states. The ESP32 owns this machine and runs it autonomously from local
inputs (vibration sensor + on-device face detection):

| State | Eye Expression | Behavior |
|---|---|---|
| **BOOT** | SEARCHING | 3-second init, then → IDLE |
| **IDLE** | NEUTRAL | Monitors vibration + face; on either → DETECTING |
| **DETECTING** | DETECTING | Face confirmed → INTERACTING; no face for 10s → IDLE |
| **INTERACTING** | HAPPY | Gaze follows face; face lost for 5s → IDLE |
| **SLEEPING** | SLEEPING (closed) | Eyes closed, servos centered; wake command → IDLE |
| **UPDATE** | UPDATE (spinner) | SoftAP + `/update` HTTP server; 4-tap vibration enters, one tap exits |
| **ERROR** | — | Hardware init failure |

### Eye Renderer
- Two 160×160 LCDs with PSRAM framebuffers (2×51.2 KB each)
- Procedural eye drawing: sclera → iris → pupil → eyelids
- 8 expressions controlling iris position, pupil size, eyelid openness
- Auto-blink every 2–5 seconds
- Gaze tracking: iris/pupil offset based on face position or remote command

### Servo Controller
- PCA9685 at 50 Hz PWM (1000–2000 µs pulse width)
- Smooth interpolation: 2° per loop iteration (~60 Hz = ~120°/s max speed)
- Center position on idle/sleep transitions

### Sensors Module
- **BNO055**: Reads Euler angles (pitch/roll/yaw) from registers 0x1A–0x21, calibration status from 0x35
- **PA1010D GPS**: Native I2C mode via `Adafruit_GPS GPS(&Wire)` — full NMEA parsing (RMC+GGA), no UART pins
- **SW420**: Digital input with 100 ms debounce, event counting

### NDJSON Protocol

**ESP32 → RPi (telemetry, every 500ms):**
```json
{
  "type": "telemetry",
  "state": "idle",
  "uptime": 12345,
  "imu": { "pitch": -2.5, "roll": 0.8, "yaw": 180.3, "calibrated": true },
  "gps": { "valid": true, "latitude": 52.52, "longitude": 13.405, "altitude": 34.2, "satellites": 8 },
  "vibration": { "detected": false, "count": 3 },
  "servos": [0.0, 0.0, 5.2, -1.0, 1.5],
  "face": { "detected": true, "x": 45, "y": 38, "w": 62, "h": 74, "confidence": 0.87, "gaze_x": -0.19, "gaze_y": -0.05 },
  "eye": "detecting"
}
```

**RPi → ESP32 (commands):**
| Command | Payload | Description |
|---|---|---|
| `sleep` | `{"type":"sleep"}` | Policy: put the owl to sleep |
| `wake` | `{"type":"wake"}` | Policy: wake from sleep |
| `expression` | `{"type":"expression","value":"happy"}` | Temporary expression override (3s) |
| `servo` | `{"type":"servo","channel":0,"angle":15.0}` | Set servo angle (-45 to 45) |
| `gaze` | `{"type":"gaze","x":-0.5,"y":0.2}` | Temporary gaze override (3s) |
| `blink` | `{"type":"blink","speed":3}` | Trigger blink animation |
| `heartbeat` | `{"type":"heartbeat"}` | Request state ack |

While in **UPDATE** state the owl is on an isolated SoftAP (`RobotOwl-Update`); all commands except `heartbeat` are ignored, and telemetry carries the AP credentials under `update` (ssid / password / ip / url).

---

## Software Decisions

### 1. Arduino Framework over ESP-IDF
**Decision:** Use Arduino core 2.0.17 (ESP-IDF 4.4.x) via PlatformIO (espressif32 platform 7.0.1).

**Rationale:**
- All peripheral drivers (GC9D01, Eyes, BNO055, PCA9685) already implemented in Arduino style
- `ArduinoJson` v7.4.3 for clean JSON serialization
- USB CDC (`ARDUINO_USB_CDC_ON_BOOT=1`) works out of the box
- ESP-DL (face detection) is an ESP-IDF component — switching frameworks would require rewriting all drivers

**Trade-off:** On-device face detection uses esp-dl, whose prebuilt libraries (`libhuman_face_detect.a`, `libdl.a`) and headers ship inside the Arduino SDK and are linked by the framework build scripts by default (see `platformio-build-esp32s3.py`). Enabled with `FACE_DETECTION_ENABLED=1`.

### 2. Custom GC9D01 SPI Driver
**Decision:** Write custom driver instead of using TFT_eSPI or Adafruit_ST7789.

**Rationale:**
- GC9D01 is a round LCD with unique init sequence (not ST7789-compatible)
- TFT_eSPI PR #3783 has the correct init but adds significant bloat
- Need PSRAM framebuffers for smooth rendering at 60 Hz
- Full control over SPI transfer size (25.6 KB per framebuffer)

**Implementation:** Direct SPI register writes, PSRAM allocation via `heap_caps_calloc()`, DMA-enabled transfers.

### 3. Shared SPI Bus for Both LCDs
**Decision:** Both eyes share SCK/MOSI/RST lines, each with independent CS and DC pins.

**Rationale:**
- ESP32-S3 has limited GPIO (only ~20 usable pins available)
- Sharing bus saves 2 pins (SCK, MOSI, RST)
- Independent CS allows sequential rendering without pin conflicts

### 4. USB CDC for Serial Communication
**Decision:** Use native USB CDC (`Serial`) instead of UART pins (GPIO43/44).

**Rationale:**
- GPIO43 (D6) is repurposed as the shared LCD reset pin
- UART pins stay free for other use; native USB CDC works via `ARDUINO_USB_CDC_ON_BOOT=1` on the XIAO ESP32-S3
- No external USB-to-serial adapter needed
- For a compact build the ESP32's native USB D+/D− pads can be soldered straight to the Raspberry Pi 4's USB pads (see `WIRING.md`)

### 5. PSRAM Framebuffers
**Decision:** Allocate LCD framebuffers in PSRAM (2 × 51.2 KB = 102.4 KB total).

**Rationale:**
- 160×160×2 bytes = 51.2 KB per eye — too large for internal RAM
- Internal RAM: 320 KB, only 7.2% used by firmware (23.5 KB)
- PSRAM: 8 MB available, minimal impact on other allocations

### 6. NDJSON over Binary Protocol
**Decision:** Use newline-delimited JSON instead of binary protocol.

**Rationale:**
- Human-readable for debugging (`screen`/`minicom`)
- `ArduinoJson` handles serialization natively
- Easy to integrate with Python on RPi side
- ~200 bytes per telemetry frame at 115200 baud = ~2 ms transmission time

### 7. Behavior State Machine on ESP32 (not RPi)
**Decision:** High-level behavior (idle/detecting/interacting transitions) runs on the ESP32. The RPi is a supervisor that logs telemetry, monitors health, and sends policy commands (sleep/wake) plus temporary overrides (expression/gaze). It does not run its own state machine.

**Rationale:**
- Face detection already runs on the ESP32 (Decision #8), so the state transitions that depend on it must live there to avoid a high-latency serial round trip
- Vibration wake is a local ESP32 sensor, so the full behavior loop (vibration + face + timeouts) runs autonomously on-device
- Clean separation: ESP32 = "brain + eyes + sensors" (owns behavior), RPi = "supervisor" (logging, health, policy, OTA)
- Eliminates the previous duplicate state machine that existed on both sides and fought over expressions/gaze

### 8. Face Detection Placement
**Decision:** Face detection runs on the ESP32-S3 using esp-dl's `HumanFaceDetectMSR01` model.

**Rationale for ESP32:**
- ESP32-S3 has AI instructions and 8 MB PSRAM — sufficient for esp-dl face detection (~25 FPS with MSR_S8_V1 model)
- Keeps latency low (no frame transfer to RPi needed)
- ESP32 already sends gaze coordinates in telemetry
- The Arduino SDK ships esp-dl headers and prebuilt `libhuman_face_detect.a`/`libdl.a`/`libesp32-camera.a`, all linked by the framework build script — no wrapper or framework switch needed

**Implementation:** `FaceDetector.cpp` init the OV2640 (QVGA RGB565, PSRAM frame buffer), runs `HumanFaceDetectMSR01(0.25, 0.3, 5, 0.3)` per frame, publishes the best box + gaze offset into telemetry, and drives the DETECTING→INTERACTING state transitions on-device.

---

## Project Structure

```
esp32-s3-sense/                    # ESP32 firmware
├── platformio.ini                 # PlatformIO config (Arduino, PSRAM, USB CDC)
├── include/
│   ├── config.h                   # Pin assignments, constants, addresses
│   ├── common.h                   # Shared enums (EyeExpression), colors, geometry
│   ├── Sensors.h                  # Sensor data structures
│   └── ServoController.h          # Servo controller interface
├── lib/
│   ├── GC9D01/                    # Custom SPI driver for round LCDs
│   │   ├── GC9D01.h/.cpp          # Init, framebuffer, flush, pixel drawing
│   │   └── common.h               # Display constants
│   ├── Eyes/                      # Eye renderer class
│   │   ├── Eyes.h/.cpp            # Sclera, iris, pupil, eyelids, 8 expressions
│   │   └── common.h               # Eye geometry, colors
│   └── FaceDetector/              # Face detection module (esp-dl MSR01)
│       ├── FaceDetector.h/.cpp    # OV2640 + HumanFaceDetectMSR01 integration
│                                    # Enabled with FACE_DETECTION_ENABLED=1
├── src/
│   ├── main.cpp                   # State machine, protocol, telemetry, main loop
│   ├── Sensors.cpp                # BNO055 IMU, PA1010D GPS, SW420 vibration
│   └── ServoController.cpp        # PCA9685 smooth servo interpolation
└── .pio/build/xiao_esp32s3/       # Build output (firmware.bin)

rpi-brain/                         # Raspberry Pi brain (Python)
├── main.py                        # Entry point, config loading, serial loop
├── config.yaml                    # Serial port, expression names
├── requirements.txt               # pyserial, opencv-python-headless, numpy, pyyaml
└── brain/
    ├── __init__.py
    ├── serial_handler.py          # NDJSON parser, ESP32 communication API
    └── supervisor.py              # Supervisor: logs telemetry, sends policy (no state machine)
```

---

## Build & Flash

### ESP32 Firmware
```bash
cd esp32-s3-sense
pio run                    # Build firmware.bin
pio run --target upload    # Flash to device
pio run --target monitor   # Serial monitor (115200 baud)
```

**Build size:** RAM 11.2% (36,644 / 327,680 bytes), Flash 25.5% (851,585 / 3,342,336 bytes)

---

## First Run Checklist

> ⚠️ **Re-wire required before first run with the Sense camera:** GPIO 10 is now reserved for the camera's XCLK. The right eye's CS line must be moved from **GPIO 10 → D7 (GPIO 44)** (`LCD_CS_R` in `config.h`). See `WIRING.md`.

### 0. Flash firmware — use USB-C first, not the Pi

The XIAO's native USB D+/D− pads share the same lines as the USB-C port, so **never keep the direct solder link to the Pi connected while flashing**. Flash over USB-C, then attach the native USB link afterwards.

```bash
cd esp32-s3-sense
pio run --target upload    # flash over USB-C
pio run --target monitor   # 115200 baud; opens the CDC link (firmware waits for it)
```

> ℹ️ `setup()` blocks on `while (!Serial)`, so the firmware starts only once a USB host opens the CDC link (the serial monitor counts). Powered alone, the board waits forever — always start it with a host attached.

### 1. Boot sequence on the serial monitor

After upload the monitor should show, in order:

```
Robot Owl ESP32-S3 starting...
Face detection enabled            ← only if FACE_DETECTION_ENABLED=1 (default)
System ready
{"type":"boot","msg":"ready"}
{"type":"telemetry","state":"idle","uptime":..., ...}   ← repeats every 500 ms
```

If it prints `ERROR: Left LCD failed` / `ERROR: Right LCD failed` / `ERROR: Sensors failed` or `Face detection failed - running without it`, stop here — see the checks below.

### 2. Verify healthy telemetry

| Field | Expected on first run |
|---|---|
| `state` | `boot` → `idle` after ~3 s |
| `imu.calibrated` | eventually `true` (rotate/yaw the owl to calibrate the magnetometer) |
| `gps.valid` | `true` only with a sky-view fix; `gps.satellites` > 0 |
| `vibration.count` | `0` — increments when the SW420 is tapped |
| `face.detected` | `false` (true when a face is in frame) |
| `eye` | `searching` → `neutral` |

When wired, all three I2C devices must be present on D0/D1 (addresses `0x28` BNO055, `0x10` PA1010D, `0x40` PCA9685). A missing device is almost always a solder/daisy-chain issue, not a code one.

### 3. Test face detection (camera)

1. Confirm the Sense camera is seated on the B2B connector and GPIO 10 is *not* wired to any LCD (it is the camera XCLK).
2. Point the camera at a face ~0.5–1.5 m away with decent lighting.
3. The telemetry should flip to `"face":{"detected":true,...}` and the state should advance `idle → detecting → interacting` while the eyes follow the face.
4. If `face.detected` stays `false`: check `pio monitor` boot messages, ensure `CAMERA_FB_IN_PSRAM` has PSRAM available (it does on the Sense), and try raising the model sensitivity note in `config.h` / `FaceDetector.cpp` (score threshold / resize scale).

### 4. Send a test command

Paste one of these into the monitor:

```json
{"type":"blink","speed":3}
{"type":"expression","value":"happy"}
{"type":"servo","channel":0,"angle":15}
{"type":"heartbeat"}
```

Expect an `*_ack` reply and a visible eye/servo reaction.

### 5. Connect the native USB link to the Pi 4

1. Power down, remove the USB-C cable.
2. Solder XIAO D+/D− (backside pads) + 5V + GND to a **USB 2.0** port's underside pads on the Pi 4 (see `WIRING.md`).
3. Power the Pi — the XIAO powers from the Pi's 5V rail.
4. On the Pi check the device appeared: `ls /dev/ttyACM0` (or `ttyUSB0`).
5. `dmesg | tail` should show a USB CDC device enumerating; `lsusb` shows the ESP32-S3.

**If it does not enumerate:** re-check DP↔D+ vs DN↔D− (most common swap), then GND continuity and wire length (< 10 cm).

### 6. Run the RPi brain

```bash
cd rpi-brain
pip install -r requirements.txt
python main.py        # connects to /dev/ttyACM0 @ 115200 by default (see config.yaml)
```

Expect log lines like `Robot Owl Brain started (supervisor mode)` and `ESP32 owns behavior; waiting for telemetry...`, then state-change and face-detection log lines. The ESP32 drives expressions/servos; the RPi supervisor only observes and can send policy (sleep/wake).

### 7. Test the OTA update mode end-to-end

This is the first thing to validate once the mechanical build is wired, since the 4-tap vibration trigger is the one part not yet hardware-tested.

1. **Enter update mode:** tap the owl's body (the SW420 vibration sensor) **4 times** within ~1.5 s. The eyes switch to the green spinner and the RPi supervisor logs the SoftAP credentials.
2. **Join the SoftAP:** on a phone or laptop, connect to WiFi **`RobotOwl-Update`** (password **`robotowl123`**). The owl is on an isolated AP — you lose normal internet while connected, which is expected.
3. **Open the update page:** go to **`http://192.168.4.1/update`** in a browser. (The exact IP is also printed in the supervisor log / telemetry `update` object.)
4. **Flash:** select the new `firmware.bin` (from `esp32-s3-sense/.pio/build/xiao_esp32s3/firmware.bin`) and upload. Watch the progress bar.
5. **Confirm the new version:** after flashing, the owl reboots into the new firmware. The RPi supervisor logs `Owl firmware changed: <old> -> <new>` — this is the confirmation the OTA took.
6. **Exit update mode:** tap the owl **once** (after the ~1 s grace period). The eyes return to normal and the owl rejoins normal operation.

> ⚠️ If the 4-tap trigger doesn't fire, the SW420 debounce / tap-counting in `Sensors.cpp` is the thing to debug first (see `BACKLOG.md`). The owl boots standalone (5 s USB wait), so you can test update mode without the RPi attached — you just won't get the credential log lines.

---

### Enable Face Detection
Face detection is enabled by default (`platformio.ini`: `-DFACE_DETECTION_ENABLED=1`). To disable, set it to `0` in `platformio.ini` (and `config.h`).
The esp-dl model libraries ship with the Arduino SDK — no extra component installation needed.

### RPi Brain
```bash
cd rpi-brain
pip install -r requirements.txt
python main.py [config.yaml]   # Default config path
```

---

## Current State

| Component | Status | Notes |
|---|---|---|
| **GC9D01 LCD Driver** | ✅ Complete | Custom SPI driver, PSRAM framebuffers, 27 MHz |
| **Eye Renderer** | ✅ Complete | 8 expressions, auto-blink, gaze tracking, eyelids |
| **BNO055 IMU** | ✅ Complete | Euler angles + calibration status via I2C |
| **PA1010D GPS** | ✅ Complete | Native I2C (Adafruit_GPS), RMC+GGA NMEA parsing |
| **SW420 Vibration** | ✅ Complete | Digital input with debounce, event counting |
| **PCA9685 Servo Ctrl** | ✅ Complete | 5 channels, smooth interpolation (2°/iteration) |
| **State Machine** | ✅ Complete | 7 states on ESP32 (owns behavior): BOOT/IDLE/DETECTING/INTERACTING/SLEEPING/UPDATE/ERROR |
| **NDJSON Protocol** | ✅ Complete | Telemetry (500ms) + commands (expression/servo/gaze/wake/blink/heartbeat) |
| **Face Detection (ESP32)** | ✅ Complete | esp-dl `HumanFaceDetectMSR01`, OV2640 QVGA RGB565, gaze offsets + state transitions on-device |
| **OTA Update Mode** | ✅ Complete | 4-tap vibration → SoftAP `RobotOwl-Update` + `/update` HTTP page (HTTPUpdateServer); one tap exits; dual-bank ota_0/ota_1; standalone boot (5s USB wait) |
| **Face Detection (RPi)** | ❌ Not implemented | OpenCV/MediaPipe fallback not needed (ESP32 does it); optional future enhancement |
| **Hardware Assembly** | 🚧 Wiring done/ongoing | Solder links documented in `WIRING.md`; mechanical build (ears/head/wings, enclosure) pending |

---

## Known Issues & TODO

- [ ] **Face detection on ESP32:** Use esp-dl `HumanFaceDetectMSR01` (implemented). Tune score threshold / resize scale for speed vs accuracy; test indoors with good lighting
- [x] **RPi face detection fallback:** Resolved — detection is fully on-device; an OpenCV/MediaPipe fallback on the RPi is only needed if ESP32 detection is later disabled
- [x] **OTA updates:** Implemented — 4-tap vibration enters update mode (SoftAP `RobotOwl-Update` + `/update` HTTP page), one tap exits. Dual-bank (ota_0/ota_1) partitions; owl boots standalone without the RPi. RPi supervisor surfaces the AP credentials. Remaining: RPi-side push tooling, firmware version reporting, hardware validation of the 4-tap trigger
- [ ] **Mechanical assembly:** 3D printing/enclosure, servo attachment for ears/head/wings, LCD bezels

---

## References

- XIAO ESP32-S3 Sense pinout: https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/
- GC9D01 init sequence (TFT_eSPI PR #3783): https://github.com/Bodmer/TFT_eSPI/pull/3783
- ESP-DL (formerly esp-face): https://github.com/espressif/esp-dl
- OV2640 camera pinout (XIAO Sense): PWDN=-1, RESET=-1, XCLK=10, SIOD=40, SIOC=39, D7-D0=48,11,12,14,16,18,17,15, VSYNC=38, HREF=47, PCLK=13
- PlatformIO ESP-IDF framework: https://docs.platformio.org/en/latest/platforms/espressif32.html
