#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPUpdateServer.h>
#include "config.h"
#include "GC9D01.h"
#include "Eyes.h"
#include "Sensors.h"
#include "ServoController.h"
#include "FaceDetector.h"
#include <ArduinoJson.h>

// ============================================================================
// Global instances
// ============================================================================
static GC9D01 lcdLeft(LCD_SCK, LCD_MOSI, LCD_DC_L, LCD_CS_L, LCD_RST);
static GC9D01 lcdRight(LCD_SCK, LCD_MOSI, LCD_DC_R, LCD_CS_R, LCD_RST);
static Eyes eyes(lcdLeft, lcdRight);
static Sensors sensors;
static ServoController servos;
static WebServer webServer(80);
static HTTPUpdateServer httpUpdateServer;
static bool updateServerReady = false;

// ============================================================================
// Face detection state
// ============================================================================
static FaceResult_t faceResult = {0};

// ============================================================================
// State machine
// ============================================================================
enum class State {
    BOOT,
    IDLE,
    DETECTING,
    INTERACTING,
    SLEEPING,
    NAVIGATING,
    UPDATE,
    ERROR
};

static State currentState = State::BOOT;
static uint32_t lastTelemetry = 0;
static uint32_t lastFaceSeen = 0;
static uint32_t lastFaceDetect = 0;
static uint32_t updateModeSince = 0;
static uint32_t navSince = 0;
static float navTargetAngle = 0.0f;

// Temporary overrides from the RPi supervisor. These take precedence over the
// state-driven expression/gaze until they expire or the state changes. They
// never change the state itself.
static EyeExpression overrideExpr = EyeExpression::NEUTRAL;
static uint32_t overrideExprUntil = 0;
static bool overrideGazeActive = false;
static float overrideGazeX = 0.0f;
static float overrideGazeY = 0.0f;
static uint32_t overrideGazeUntil = 0;

// ============================================================================
// Forward declarations
// ============================================================================
const char* stateToString(State state);
void transitionTo(State newState);
void applyExpression(EyeExpression stateExpr);
void applyGaze(float stateGx, float stateGy);

// ============================================================================
// Protocol handlers
// ============================================================================
// Map an expression name (from the RPi) to an EyeExpression.
EyeExpression parseExpression(const char* name) {
    if (strcmp(name, "happy") == 0) return EyeExpression::HAPPY;
    if (strcmp(name, "sleepy") == 0) return EyeExpression::SLEEPY;
    if (strcmp(name, "surprised") == 0) return EyeExpression::SURPRISED;
    if (strcmp(name, "angry") == 0) return EyeExpression::ANGRY;
    if (strcmp(name, "sleeping") == 0) return EyeExpression::SLEEPING;
    if (strcmp(name, "searching") == 0) return EyeExpression::SEARCHING;
    if (strcmp(name, "detecting") == 0) return EyeExpression::DETECTING;
    if (strcmp(name, "update") == 0) return EyeExpression::UPDATE;
    if (strcmp(name, "error") == 0) return EyeExpression::ERROR;
    return EyeExpression::NEUTRAL;
}

void handleCommand(const char* json) {
    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, json);

    if (error) {
        JsonDocument err;
        err["type"] = "error";
        err["msg"] = "invalid_json";
        String out;
        serializeJson(err, out);
        Serial.println(out);
        return;
    }

    const char* type = doc["type"];
    if (!type) return;

    // Reusable document for building acknowledgment replies.
    JsonDocument resp;
    String out;

    // Update mode is local-only: while the SoftAP is up the supervisor
    // can't drive the owl (heartbeat still answers for liveness).
    if (currentState == State::UPDATE && strcmp(type, "heartbeat") != 0) {
        return;
    }

    if (strcmp(type, "expression") == 0) {
        // Temporary expression override from the supervisor. It takes
        // precedence over the state-driven expression until it expires, but
        // it never changes the state machine itself.
        const char* expr = doc["value"];
        EyeExpression eyeExpr = parseExpression(expr);
        overrideExpr = eyeExpr;
        overrideExprUntil = millis() + EXPRESSION_OVERRIDE_MS;
        eyes.setExpression(eyeExpr);

        resp["type"] = "expression_ack";
        resp["value"] = expr;
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "servo") == 0) {
        uint8_t ch = doc["channel"];
        float angle = doc["angle"];
        servos.setAngle(ch, angle);
        resp["type"] = "servo_ack";
        resp["channel"] = ch;
        resp["angle"] = angle;
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "gaze") == 0) {
        // Temporary gaze override from the supervisor (manual aim / testing).
        overrideGazeX = doc["x"];
        overrideGazeY = doc["y"];
        overrideGazeActive = true;
        overrideGazeUntil = millis() + GAZE_OVERRIDE_MS;
        eyes.setGaze(overrideGazeX, overrideGazeY);

    } else if (strcmp(type, "nav") == 0) {
        // Persistent navigation command from the supervisor. Unlike the 3s
        // expression/gaze overrides, this HOLDS: active=true enters/keeps the
        // NAVIGATING state and points the head at `angle` until an active=false
        // arrives (or the firmware's own timeout fires). The RPi recomputes the
        // bearing from live GPS + heading and re-sends the angle each refresh,
        // so the head tracks the destination.
        float angle = doc["angle"];
        bool active = doc["active"] | false;
        if (active) {
            navTargetAngle = angle;
            if (currentState != State::NAVIGATING) {
                transitionTo(State::NAVIGATING);
            }
            navSince = millis();  // (re)arm the self-timeout on every update
            eyes.setGaze(0, 0);
            servos.setAngle(CH_HEAD, angle);
        } else {
            navTargetAngle = 0.0f;
            if (currentState == State::NAVIGATING) {
                transitionTo(State::IDLE);
            }
            eyes.setGaze(0, 0);
            servos.setAngle(CH_HEAD, 0);
        }
        resp["type"] = "nav_ack";
        resp["active"] = active;
        resp["angle"] = angle;
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "sleep") == 0) {
        // Policy command: put the owl to sleep.
        transitionTo(State::SLEEPING);
        resp["type"] = "sleep_ack";
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "wake") == 0) {
        // Policy command: wake the owl (only meaningful while sleeping).
        if (currentState == State::SLEEPING) {
            transitionTo(State::IDLE);
        }
        resp["type"] = "wake_ack";
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "blink") == 0) {
        uint8_t speed = doc["speed"] | 3;
        eyes.blink(speed);
        resp["type"] = "blink_ack";
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "heartbeat") == 0) {
        resp["type"] = "heartbeat_ack";
        resp["state"] = stateToString(currentState);
        serializeJson(resp, out);
        Serial.println(out);
    }
}

const char* stateToString(State state) {
    switch (state) {
        case State::BOOT: return "boot";
        case State::IDLE: return "idle";
        case State::DETECTING: return "detecting";
        case State::INTERACTING: return "interacting";
        case State::SLEEPING: return "sleeping";
        case State::NAVIGATING: return "navigating";
        case State::UPDATE: return "update";
        case State::ERROR: return "error";
        default: return "unknown";
    }
}

// ============================================================================
// Telemetry sender
// ============================================================================
void sendTelemetry() {
    JsonDocument doc;

    doc["type"] = "telemetry";
    doc["state"] = stateToString(currentState);
    doc["uptime"] = millis();
    doc["fw"] = FW_VERSION;

    // IMU data
    ImuData imu = sensors.getImu();
    if (sensors.isImuReady()) {
        doc["imu"]["pitch"] = imu.pitch;
        doc["imu"]["roll"] = imu.roll;
        doc["imu"]["yaw"] = imu.yaw;
        doc["imu"]["calibrated"] = imu.isCalibrated;
    }

    // GPS data
    GpsData gps = sensors.getGps();
    if (sensors.isGpsReady()) {
        doc["gps"]["valid"] = gps.valid;
        doc["gps"]["latitude"] = gps.latitude;
        doc["gps"]["longitude"] = gps.longitude;
        doc["gps"]["altitude"] = gps.altitude;
        doc["gps"]["satellites"] = gps.satellites;
    }

    // Vibration
    VibrationData vib = sensors.getVibration();
    doc["vibration"]["detected"] = vib.detected;
    doc["vibration"]["count"] = vib.count;

    // Navigation status (present only while the owl is NAVIGATING). Lets the
    // RPi confirm the head is actually being held at the requested angle.
    if (currentState == State::NAVIGATING) {
        doc["navigation"]["active"] = true;
        doc["navigation"]["angle"] = navTargetAngle;
    }

    // Update mode (SoftAP + OTA)
    if (currentState == State::UPDATE) {
        doc["update"]["ssid"] = UPDATE_AP_SSID;
        doc["update"]["password"] = UPDATE_AP_PASSWORD;
        doc["update"]["ip"] = WiFi.softAPIP().toString();
        doc["update"]["url"] = String("http://") + WiFi.softAPIP().toString() + "/update";
    }

    // Servo positions (array: left_ear, right_ear, head, left_wing, right_wing)
    doc["servos"][0] = servos.getAngle(CH_LEFT_EAR);
    doc["servos"][1] = servos.getAngle(CH_RIGHT_EAR);
    doc["servos"][2] = servos.getAngle(CH_HEAD);
    doc["servos"][3] = servos.getAngle(CH_LEFT_WING);
    doc["servos"][4] = servos.getAngle(CH_RIGHT_WING);

    // Face detection state
    doc["face"]["detected"] = faceResult.detected;
    doc["face"]["x"] = faceResult.x;
    doc["face"]["y"] = faceResult.y;
    doc["face"]["w"] = faceResult.w;
    doc["face"]["h"] = faceResult.h;
    doc["face"]["confidence"] = faceResult.confidence;
    doc["face"]["gaze_x"] = faceResult.gaze_x;
    doc["face"]["gaze_y"] = faceResult.gaze_y;

    // Eye expression
    const char* exprNames[] = {
        "neutral", "happy", "sleepy", "surprised", "angry", "sleeping", "searching", "detecting", "update", "error"
    };
    doc["eye"] = exprNames[(int)eyes.getCurrentExpression()];

    String output;
    serializeJson(doc, output);
    Serial.println(output);
}

// ============================================================================
// State machine helpers
// ============================================================================
// Change state and clear any active supervisor overrides so the new state's
// expression/gaze take effect immediately.
void transitionTo(State newState) {
    if (newState == currentState) return;
    currentState = newState;
    overrideExprUntil = 0;
    overrideGazeActive = false;
}

// Apply the expression for the current state, honoring an active supervisor
// expression override.
void applyExpression(EyeExpression stateExpr) {
    if (millis() < overrideExprUntil) {
        eyes.setExpression(overrideExpr);
    } else {
        eyes.setExpression(stateExpr);
    }
}

// Apply the gaze for the current state, honoring an active supervisor gaze
// override.
void applyGaze(float stateGx, float stateGy) {
    if (overrideGazeActive && millis() < overrideGazeUntil) {
        eyes.setGaze(overrideGazeX, overrideGazeY);
    } else {
        overrideGazeActive = false;
        eyes.setGaze(stateGx, stateGy);
    }
}

// Bring up the SoftAP + /update HTTP server for a firmware update.
void enterUpdateMode() {
    transitionTo(State::UPDATE);
    updateModeSince = millis();
    servos.setCenter();
    eyes.setGaze(0, 0);
    eyes.setExpression(EyeExpression::UPDATE);

    WiFi.mode(WIFI_AP);
    WiFi.softAP(UPDATE_AP_SSID, UPDATE_AP_PASSWORD);
    delay(100); // let the AP interface come up

    // The SoftAP password is the only authentication; the /update page is
    // only reachable on the isolated update network. Configure the handlers
    // only once: webServer.stop() does not remove them.
    if (!updateServerReady) {
        httpUpdateServer.setup(&webServer, "/update");
        updateServerReady = true;
    }
    webServer.begin();

    String out = "{\"type\":\"update_mode\",\"ssid\":\"";
    out += UPDATE_AP_SSID;
    out += "\",\"password\":\"";
    out += UPDATE_AP_PASSWORD;
    out += "\",\"url\":\"http://";
    out += WiFi.softAPIP().toString();
    out += "/update\"}";
    Serial.println(out);
}

// Tear down the SoftAP and resume normal behavior.
void exitUpdateMode() {
    webServer.stop();
    WiFi.mode(WIFI_OFF);
    transitionTo(State::IDLE);
    eyes.setGaze(0, 0);
    faceResult.detected = false;
    Serial.println("{\"type\":\"update_mode_end\"}");
}

// ============================================================================
// State machine update
// ============================================================================
// The ESP32 owns the behavior state machine. It runs autonomously from local
// inputs (vibration sensor + on-device face detection) and drives the eyes,
// gaze, and servos. The RPi is a supervisor that can only send policy
// commands (sleep/wake) and temporary overrides (expression/gaze).
//
//   BOOT --(3s)--> IDLE
//   IDLE --(vibration OR face)--> DETECTING
//   DETECTING --(face confirmed)--> INTERACTING
//   DETECTING --(no face for 10s)--> IDLE
//   INTERACTING --(face lost for 5s)--> IDLE
//   SLEEPING --(wake command)--> IDLE
//   any --(sleep command)--> SLEEPING
//   any --(nav active=true)--> NAVIGATING
//   NAVIGATING --(nav active=false)--> IDLE
//   NAVIGATING --(no nav update for NAV_TIMEOUT_MS)--> IDLE  (self-timeout)
//   any --(4 rapid taps)--> UPDATE
//   UPDATE --(1 tap)--> IDLE
void updateState() {
    // 4-tap vibration sequence enters update mode from any state.
    VibrationData vib = sensors.getVibration();
    if (vib.updateSequence) {
        sensors.clearUpdateSequence();
        if (currentState != State::UPDATE) {
            enterUpdateMode();
            return;
        }
    }

    if (currentState == State::UPDATE) {
        // A new tap after the grace period exits update mode. The 4th tap
        // that entered the mode (or a still-pressed contact) does not.
        if (vib.detected && vib.lastDetected > updateModeSince + UPDATE_EXIT_GRACE_MS) {
            exitUpdateMode();
            return;
        }
        applyExpression(EyeExpression::UPDATE);
        servos.setCenter();
        return;
    }

    switch (currentState) {
        case State::BOOT: {
            applyExpression(EyeExpression::SEARCHING);
            if (millis() > BOOT_TIMEOUT_MS) {
                transitionTo(State::IDLE);
            }
            break;
        }

        case State::IDLE: {
            applyExpression(EyeExpression::NEUTRAL);
            if (vib.detected) {
                // Vibration wakes us up to look for a face.
                lastFaceSeen = millis();
                transitionTo(State::DETECTING);
                servos.setAngle(CH_HEAD, 0); // Look forward
            } else if (faceResult.detected) {
                // A face nearby wakes us up.
                lastFaceSeen = millis();
                transitionTo(State::DETECTING);
                servos.setAngle(CH_HEAD, 0);
            }
            break;
        }

        case State::DETECTING: {
            applyExpression(EyeExpression::DETECTING);
            if (faceResult.detected) {
                // Face confirmed: start interacting and follow it.
                lastFaceSeen = millis();
                applyGaze(faceResult.gaze_x, faceResult.gaze_y);
                transitionTo(State::INTERACTING);
            } else if (millis() - lastFaceSeen > DETECT_TIMEOUT_MS) {
                // Nothing found: give up and go back to idle.
                transitionTo(State::IDLE);
                eyes.setGaze(0, 0);
                servos.setCenter();
            }
            break;
        }

        case State::INTERACTING: {
            applyExpression(EyeExpression::HAPPY);
            if (faceResult.detected) {
                lastFaceSeen = millis();
                applyGaze(faceResult.gaze_x, faceResult.gaze_y);
            } else if (millis() - lastFaceSeen > INTERACT_TIMEOUT_MS) {
                // Face lost: return to idle.
                transitionTo(State::IDLE);
                eyes.setGaze(0, 0);
                servos.setCenter();
            }
            break;
        }

        case State::SLEEPING: {
            applyExpression(EyeExpression::SLEEPING);
            servos.setCenter();
            break;
        }

        case State::NAVIGATING: {
            // "Guide me home": the head is pinned to the RPi-computed compass
            // bearing (navTargetAngle) and ignores face-following. The RPi
            // re-sends the angle on each refresh, so the head tracks the
            // destination. If the RPi stops sending (link dropped / it crashed),
            // the self-timeout recenters the head and returns to idle so it is
            // never left stuck pointing somewhere.
            applyExpression(EyeExpression::SEARCHING);
            servos.setAngle(CH_HEAD, navTargetAngle);
            if (millis() - navSince > NAV_TIMEOUT_MS) {
                transitionTo(State::IDLE);
                servos.setCenter();
                eyes.setGaze(0, 0);
            }
            break;
        }

        case State::UPDATE:
            // Handled before the switch (SoftAP + OTA mode).
            break;

        case State::ERROR: {
            // Hardware fault: show the red X error face and hold servos
            // centered. The owl cannot recover from a boot-time init failure,
            // so it stays here until power-cycled.
            applyExpression(EyeExpression::ERROR);
            servos.setCenter();
            break;
        }
    }
}

// ============================================================================
// Serial command parser
// ============================================================================
void parseSerialCommands() {
    static String inputBuffer;

    while (Serial.available()) {
        char c = Serial.read();

        if (c == '\n' || c == '\r') {
            if (inputBuffer.length() > 0) {
                handleCommand(inputBuffer.c_str());
                inputBuffer.clear();
            }
        } else {
            inputBuffer += c;
            // Safety: prevent buffer overflow. Log the drop so a truncated
            // or malformed frame is diagnosable instead of failing silently.
            if (inputBuffer.length() > 255) {
                Serial.println(F("{\"type\":\"error\",\"msg\":\"line_too_long\"}"));
                inputBuffer.clear();
            }
        }
    }
}

// ============================================================================
// Setup
// ============================================================================
void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(100);

    // Wait for USB CDC to connect, but only briefly: the owl must boot
    // standalone (for the 4-tap update mode) even without the RPi.
    uint32_t usbWaitStart = millis();
    while (!Serial && millis() - usbWaitStart < USB_WAIT_TIMEOUT_MS) {
        delay(10);
    }

    Serial.println(F("Robot Owl ESP32-S3 starting..."));

    // Initialize displays
    if (!lcdLeft.begin()) {
        Serial.println(F("ERROR: Left LCD failed"));
        currentState = State::ERROR;
    }
    if (!lcdRight.begin()) {
        Serial.println(F("ERROR: Right LCD failed"));
        currentState = State::ERROR;
    }

    // Initialize sensors
    if (!sensors.begin()) {
        Serial.println(F("ERROR: Sensors failed"));
        currentState = State::ERROR;
    }

    // Initialize servos
    servos.begin();

    // Initialize face detection (optional)
#if FACE_DETECTION_ENABLED
    if (FaceDetector_Init()) {
        Serial.println(F("Face detection enabled"));
    } else {
        Serial.println(F("Face detection failed - running without it"));
    }
#else
    Serial.println(F("Face detection disabled (compile with FACE_DETECTION_ENABLED=1 to enable)"));
#endif

    // Initial expression
    eyes.setExpression(EyeExpression::SEARCHING);

    Serial.println(F("System ready"));
    Serial.println(F("{\"type\":\"boot\",\"msg\":\"ready\"}"));
}

// ============================================================================
// Main loop
// ============================================================================
void loop() {
    // Parse incoming commands
    parseSerialCommands();

    // Update state machine
    updateState();

    // Serve the /update page while in update mode.
    if (currentState == State::UPDATE) {
        webServer.handleClient();
    }

    // Run face detection (if enabled), rate-limited so the expensive
    // inference doesn't starve the rest of the loop. Between runs the
    // state machine keeps using the last result in faceResult.
#if FACE_DETECTION_ENABLED
    if (currentState != State::UPDATE && millis() - lastFaceDetect >= FACE_DETECT_INTERVAL_MS) {
        FaceDetector_Detect(&faceResult);
        lastFaceDetect = millis();
    }
#else
    faceResult.detected = false;
#endif

    // Render eyes
    eyes.render();

    // Update servos (smooth motion)
    servos.update();

    // Send telemetry at interval
    if (millis() - lastTelemetry > TELEMETRY_INTERVAL_MS) {
        sendTelemetry();
        lastTelemetry = millis();
    }

    // Small delay to prevent watchdog
    delay(16); // ~60Hz loop
}
