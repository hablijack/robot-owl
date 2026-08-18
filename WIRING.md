# ESP32-S3 Wiring Guide — XIAO ESP32-S3

Complete **XIAO ESP32-S3** wiring guide, matching the firmware pin assignments in `esp32-s3-sense/include/config.h`.

> ⚠️ **Silkscreen caution**: On the XIAO ESP32-S3, D4/D5 are printed with their *default* I2C function (`SDA`/`SCL`). That is only the default — the firmware remaps I2C onto D0/D1 and uses D4/D5 as plain GPIO/SPI via the ESP32-S3 GPIO matrix. Wire them to the LCDs as listed below, not to I2C devices.

---

## 🔌 All Pins You Need to Solder

| Board PAD | GPIO | Purpose | Connect To |
|:---------:|:----:|---------|------------|
| **D0** | GPIO 1 | I2C SDA | BNO055 IMU + PA1010D GPS + PCA9685 Servo Driver |
| **D1** | GPIO 2 | I2C SCL | BNO055 IMU + PA1010D GPS + PCA9685 Servo Driver |
| **D3** | GPIO 4 | Vibration sensor (SW420) | SW420 signal pin |
| **D4** | GPIO 5 | LCD SPI SCK (shared) | Both LCDs: SCK |
| **D5** | GPIO 6 | LCD DC — Left eye | Left LCD: DC |
| **D6** | GPIO 43 | LCD RST (shared) | Both LCDs: RST |
| **D7** | GPIO 44 | LCD CS — Right eye | Right LCD: CS |
| **D8** | GPIO 7 | LCD SPI MOSI (shared) | Both LCDs: MOSI |
| **D9** | GPIO 8 | LCD CS — Left eye | Left LCD: CS |
| **D10** | GPIO 9 | LCD DC — Right eye | Right LCD: DC |
| **GP10** | GPIO 10 | Camera XCLK | Sense expansion camera (back B2B pad) |

> **GPIO 10** is the camera's XCLK on the XIAO ESP32-S3 **Sense** — it must **not** be used for the LCDs. The right eye's CS was moved to **D7 (GPIO 44)** to free GPIO 10 for the camera.

---

## 🔋 Power & Ground — Combine These Lines

| Rail | Devices to combine |
|------|-------------------|
| **3.3V** | BNO055 IMU, PCA9685 Servo Driver, both LCDs (logic + backlight) |
| **5V (optional)** | Servo motor supply (recommended: external 5V for servos, not the Pi rail) |
| **GND** | Everything — all I2C devices, both LCDs, vibration sensor, servo driver, GPS |

> 💡 **Tip**: Use a small breadboard or perfboard as a power rail to daisy-chain 3.3V and GND instead of running individual wires from the ESP32 to every component.

---

## 📡 Raspberry Pi Connection — Direct Native USB (no cable)

The firmware runs **USB CDC** (`ARDUINO_USB_CDC_ON_BOOT=1`, `ARDUINO_USB_MODE=1`), so `Serial` maps to the chip's **native USB**. Instead of a USB-C cable, solder the XIAO's **backside D+/D− pads** directly to the Raspberry Pi 4's USB pads for a compact, cable-free link (perfect for embedding both boards in the owl body).

`Serial` appears on the Pi as **`/dev/ttyACM0`** (USB 2.0 Full-Speed, 12 Mbps CDC).

### Wire mapping

| XIAO ESP32-S3 (backside pad) | Raspberry Pi 4 (USB pads) | Wire colour | Notes |
|:-----------------------------:|:------------------------|:-----------:|-------|
| **DP** (USB D+) | USB **D+** | Green | twist together |
| **DN** (USB D−) | USB **D−** | White | twist together |
| **5V / VBUS** | USB **5V** | Red | powers the XIAO, any USB port works |
| **GND** | USB **GND** | Black | mandatory, common ground |

### Locating the pads

- **XIAO side:** confirmed — the **D+ / D−** pads are on the **backside** of the XIAO, next to the USB-C connector. Also on that edge: the **5V** and **GND** pads. Quick sanity check before soldering: with the board unpowered, measure continuity between each pad and the matching USB-C pin (D+ ↔ D+, D− ↔ D−).
- **Pi 4 side:** the Pi 4 has **4 USB-A ports** on the right edge (2× USB 3.0 using the blue ports, 2× USB 2.0 using the black ports). All four are behind the same VL805 hub, so any of them works. The solder pads are the **through-hole pads on the underside of the PCB**, directly below each USB connector.
  - **Pick a USB 2.0 (black) port.** The USB 3.0 (blue) ports still carry the D+/D− lines, but the connector has 9 pins and the super-speed pairs sit right beside the signal pads — much easier to bridge accidentally. A black port's 4 pads are: **VBUS, D−, D+, GND** (verify order with a multimeter).
  - Mark the chosen port and **never plug anything into it** afterwards.
  - Use the bottom pads on the board → no connector removal needed; tin them gently and keep wires under ~6 cm.

### Soldering recipe

1. **Tin** each XIAO pad and each Pi pad with a fresh, small solder bead (leaded solder, flux core).
2. Use short **30–32 AWG** silicone or enameled (magnet) wire. Cut four lengths of ~3–6 cm.
3. **Twist the DP + DN pair** tightly together (~3–6 twists/cm) and keep them far from the 5V and GND wires — this preserves the USB differential signal.
4. **Common ground is critical**: GND must be joined between the two boards (do not rely on a shared supply only).
5. Solder **one wire at a time**, then re-check with a multimeter: no shorts between DP/DN, DP↔5V, or DN↔GND.
6. Add a blob of hot glue over the joints to relieve strain.

### Powering the XIAO

- With this method the XIAO is powered from the **Pi's 5V rail** via the USB VBUS wire; its onboard regulator produces 3.3V.
- Do **not** also plug a USB-C cable into the XIAO while the direct solder link is live.
- Servos draw high current — power the PCA9685 motor rail from a **separate 5V supply with a common GND**, not from the Pi's rail.

> ⚠️ If the Pi doesn't enumerate the XIAO (`/dev/ttyACM0` missing), first check **DP/DN swap** (most common), then GND continuity, then wire length. Re-check contrast with a normal USB-C cable to isolate firmware vs. solder issues.

---

## 🔊 Audio — MAX98357A (Adafruit) on the Raspberry Pi

The owl's voice/output is a **MAX98357A** mono class-D amplifier (Adafruit 2980 / 3322) driven over the Pi's **I2S** bus. It takes a small speaker (4Ω or 8Ω) directly. It is **entirely on the Raspberry Pi side** — no ESP32 pins are used, so it does not touch the pin map above.

The RPi brain generates short procedural sound effects (beeps/chirps) in-process and plays them through the amp with `aplay` (ALSA). All audio lives on the Pi; the ESP32 is not involved in sound.

### Wire mapping (Pi 40-pin header → MAX98357A)

| MAX98357A pin | Raspberry Pi pin | Function |
|:-------------:|:----------------:|----------|----------|
| **GND** | GND (pin 6/9/14/20/25) | Common ground |
| **BCLK** | GPIO 18 (pin 12) | Bit clock |
| **LRCLK** | GPIO 19 (pin 21) | Word-select (L/R) |
| **DIN** | GPIO 21 (pin 40) | Serial data in |
| **SD MODE** | 3.3V (pin 1) | **Tie to 3.3V** for I2S (leave floating = PWM) |
| **GAIN** | 3.3V (pin 1) | High-gain mode (0 dB); tie to GND for −6 dB if too loud |
| **VSUP** | 5V (pin 2/4) | Amp supply (5–35 V). 5 V is fine for a small speaker |
| **Speaker +** | speaker + | 4Ω or 8Ω speaker |
| **Speaker −** | speaker − | Speaker ground |

> ⚠️ The **SD MODE pin must be tied to 3.3V** for I2S operation. Left floating, the amp defaults to PWM mode and the Pi's I2S output will be silent. This is the #1 "no sound" mistake.

### Enable I2S on the Pi
Add to `/boot/config.txt` (or `/boot/firmware/config.txt` on Bookworm) and reboot:
```
dtoverlay=hifiberry-i2s-lite
```
This maps the standard I2S pins (BCLK=18, LRCLK=19, DIN=21) to the `snd-soc-bcm2835` driver, so `aplay -l` shows a `bcm2835` playback device. (The MAX98357A needs no codec I2C address — it's a dumb amp — so no `dtparameter` is required.)

### Verify
```
aplay -l                      # should list a bcm2835-I2S-hw-0 playback device
sudo apt install espeak-ng    # optional: test with a real voice
espeak-ng "hello owl"         # should speak through the speaker
```

### Software
`brain/audio.py` generates WAV bytes in-process (no external assets) and plays them via `aplay` in a daemon thread, so the serial read loop is never blocked. See the **NDJSON Protocol** table in `README.md` for the `sound` command the RPi forwards.

---

## 📊 I2C Bus Summary (Physically on D0 + D1)

| Device | Address | SDA → D0 | SCL → D1 |
|--------|:-------:|:--------:|:--------:|
| BNO055 IMU | 0x28 | ✅ | ✅ |
| PA1010D GPS | 0x10 | ✅ | ✅ |
| PCA9685 Servo Driver | 0x40 | ✅ | ✅ |

All three share the same bus — no conflict since each has a unique address.

---

## ✅ GPS (PA1010D) — Now on I2C (firmware fixed)

The GPS is wired on the **I2C bus (D0/D1)** and `Sensors.cpp` now reads it natively:

- `Adafruit_GPS GPS(&Wire)` — I2C driver, no `HardwareSerial`/UART pins involved
- `GPS.begin(ADDR_GPS)` (0x10) probes the module on the bus
- `PMTK_SET_NMEA_OUTPUT_RMCGGA` + `PMTK_SET_NMEA_UPDATE_1HZ` configure the output sentences

The old `gpsSerial.begin(..., 1, 2)` on the I2C pins has been removed, so GPIO 1/2 are exclusively I2C now.

---

## 🔢 Complete Pin Reference (XIAO ESP32-S3 Board Pads)

| Board Pad | GPIO | Used? | Function |
|:---------:|:----:|:-----:|----------|
| D0 | 1 | ✅ | I2C SDA |
| D1 | 2 | ✅ | I2C SCL |
| D2 | 3 | ⬜ | Free |
| **D3** | **4** | ✅ | Vibration sensor (SW420) |
| **D4** | **5** | ✅ | LCD SPI SCK (shared) |
| **D5** | **6** | ✅ | LCD DC — Left |
| **D6** | **43** | ✅ | LCD RST (shared) — silkscreen `TX`, repurposed as GPIO |
| **D7** | **44** | ✅ | LCD CS — Right — silkscreen `RX`, repurposed as GPIO |
| **D8** | **7** | ✅ | LCD SPI MOSI (shared) |
| **D9** | **8** | ✅ | LCD CS — Left |
| **D10** | **9** | ✅ | LCD DC — Right |
| — | 10 | ✅ | Camera XCLK (Sense B2B, not a D pad) |
| D11 | 42 | ⬜ | Free |
| D12 | 41 | ⬜ | Free |

> D6 (GPIO 43) and D7 (GPIO 44) print `TX`/`RX`, but are used here as plain GPIOs (or left free). This works because native USB CDC does not occupy the UART pins.

---

## 🔌 Connection Count Summary

1. **SDA** → D0 (branches to BNO055 + GPS + PCA9685)
2. **SCL** → D1 (branches to BNO055 + GPS + PCA9685)
3. **3.3V** → all 3.3V devices (common rail)
4. **GND** → all devices (common rail)
5. **Vibration signal** → D3
6. **LCD SCK** → D4 (shared by both LCDs)
7. **LCD MOSI** → D8 (shared by both LCDs)
8. **LCD DC Left** → D5
9. **LCD CS Left** → D9
10. **LCD DC Right** → D10
11. **LCD CS Right** → D7
12. **LCD RST** → D6 (shared by both LCDs)
13. **USB data pair** → XIAO D+/D− (backside) to Pi 4 USB 2.0 port pads
14. **Power to Pi link** → 5V (VBUS) + GND to Pi 4 USB port pads

With common power rails this collapses to ~12 signal wires plus the 4-wire native-USB link to the Raspberry Pi.