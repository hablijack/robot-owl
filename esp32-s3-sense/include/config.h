#pragma once

// ============================================================================
// Firmware version (reported in telemetry so the running build is identifiable
// and OTA updates can be verified). Bump the minor on behavior changes, the
// patch on fixes.
// ============================================================================
#define FW_VERSION_MAJOR 1
#define FW_VERSION_MINOR 1
#define FW_VERSION_PATCH 0
#define FW_VERSION "1.1.0"

// ============================================================================
// Board & System
// ============================================================================
#define SERIAL_BAUD 115200
#define I2C_SDA 1
#define I2C_SCL 2
#define I2C_FREQ 400000

// ============================================================================
// LCD Eyes (GC9D01, SPI)
// Shared: SCK=5, MOSI=7, RST=43
// Left eye:  DC=6,  CS=8
// Right eye: DC=9,  CS=44
// Backlights tied to 3.3V (always on)
// ============================================================================
#define LCD_SPI_HOST SPI2_HOST
#define LCD_SCK 5
#define LCD_MOSI 7
#define LCD_DC_L 6
#define LCD_CS_L 8
#define LCD_RST 43
#define LCD_DC_R 9
#define LCD_CS_R 44

#define LCD_WIDTH 160
#define LCD_HEIGHT 160
#define LCD_SPI_FREQ 27000000

// ============================================================================
// Camera (OV2640, XIAO ESP32-S3 Sense expansion)
// PWDN=-1, RESET=-1, XCLK=10, SIOD=40, SIOC=39
// Y9=48, Y8=11, Y7=12, Y6=14, Y5=16, Y4=18
// Y3=17, Y2=15, VSYNC=38, HREF=47, PCLK=13
// ============================================================================
#define CAMERA_ENABLED 1  // Set to 1 when camera is wired and tested

// ============================================================================
// Vibration Sensor (SW420)
// ============================================================================
#define VIBRATION_PIN 4

// ============================================================================
// Firmware update mode (4-tap vibration trigger -> WiFi SoftAP + OTA)
// ============================================================================
#define UPDATE_TAP_REQUIRED 4       // consecutive taps to enter update mode
#define UPDATE_TAP_GAP_MS 1500      // max gap between taps in a sequence
#define UPDATE_EXIT_GRACE_MS 1000   // ignore exit taps right after entering
#define UPDATE_AP_SSID "RobotOwl-Update"
#define UPDATE_AP_PASSWORD "robotowl123"
#define USB_WAIT_TIMEOUT_MS 5000    // max wait for USB CDC before continuing boot

// ============================================================================
// I2C Devices
// BNO055 IMU @ 0x28
// PA1010D GPS @ 0x10
// PCA9685 Servo Driver @ 0x40
// ============================================================================
#define ADDR_BNO055 0x28
#define ADDR_GPS 0x10
#define ADDR_PCA9685 0x40

// ============================================================================
// Servo Channels (PCA9685)
// ============================================================================
#define CH_LEFT_EAR 0
#define CH_RIGHT_EAR 1
#define CH_HEAD 2
#define CH_LEFT_WING 3
#define CH_RIGHT_WING 4
#define NUM_SERVO_CHANNELS 5

// Servo center positions (pulse width in microseconds, 50Hz)
#define SERVO_CENTER_US 1500
#define SERVO_MIN_US 1000
#define SERVO_MAX_US 2000
#define SERVO_MIN_ANGLE -45
#define SERVO_MAX_ANGLE 45

// ============================================================================
// Face Detection (optional, disabled by default)
// ============================================================================
#ifndef FACE_DETECTION_ENABLED
#define FACE_DETECTION_ENABLED 1
#endif
#define FACE_DETECT_INTERVAL_MS 100   // min ms between detection runs

// esp-dl MSR01 model knobs (first-pass tuning; validate on real hardware).
// score_threshold: min confidence for a box to be kept. 0.25 was too loose
// (false positives from hands/pictures); 0.5 is a solid first-pass default.
// nms_threshold: IoU above which duplicate boxes are merged.
// top_k: max boxes to keep.
// resize_scale: input downscale fed to the model. 0.3 (48x48) is the fastest
// option and the best speed/accuracy trade for a 320x240 QVGA feed.
#define FACE_SCORE_THRESHOLD 0.5f
#define FACE_NMS_THRESHOLD 0.3f
#define FACE_TOP_K 5
#define FACE_RESIZE_SCALE 0.3f
// Post-filter: even above the model threshold, only treat a face as "detected"
// (and drive gaze) if confidence clears this. Prevents low-confidence flicker
// from flipping the state machine.
#define FACE_MIN_CONFIDENCE 0.5f

// ============================================================================
// Telemetry
// ============================================================================
#define TELEMETRY_INTERVAL_MS 500
#define VIBRATION_DEBOUNCE_MS 100
#define SERVO_SMOOTH_SPEED 2  // degrees per loop iteration

// ============================================================================
// Behavior state machine (owned by ESP32)
// ============================================================================
#define BOOT_TIMEOUT_MS 3000        // BOOT -> IDLE
#define DETECT_TIMEOUT_MS 10000     // DETECTING -> IDLE when no face seen
#define INTERACT_TIMEOUT_MS 5000    // INTERACTING -> IDLE when face lost

// Temporary overrides sent by the RPi supervisor (do not change state)
#define EXPRESSION_OVERRIDE_MS 3000 // how long an RPi expression override lasts
#define GAZE_OVERRIDE_MS 3000       // how long an RPi gaze override lasts
