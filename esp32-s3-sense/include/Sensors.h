#pragma once

#include <Arduino.h>
#include <Wire.h>
#include "config.h"

// IMU data from BNO055
struct ImuData {
    float pitch;    // degrees, -90 to 90
    float roll;     // degrees, -180 to 180
    float yaw;      // degrees, 0 to 360
    bool isCalibrated;
};

// GPS data from PA1010D
struct GpsData {
    float latitude;
    float longitude;
    float altitude; // meters
    uint8_t satellites;
    bool valid;
};

// Vibration state
struct VibrationData {
    bool detected;
    uint32_t lastDetected;
    uint16_t count;
    bool updateSequence; // one-shot: set for one read after a 4-tap sequence
};

class Sensors {
public:
    bool begin();

    ImuData getImu();
    GpsData getGps();
    VibrationData getVibration();

    // Consume the one-shot 4-tap update flag.
    void clearUpdateSequence() { _updateSeqPending = false; }

    bool isImuReady() const { return _imuReady; }
    bool isGpsReady() const { return _gpsReady; }

private:
    bool _imuReady;
    bool _gpsReady;

    // Vibration debounce state
    bool _vibState;        // debounced: true while the sensor is stably triggered
    bool _vibRaw;          // last raw reading (true = triggered)
    uint32_t _vibRawSince; // millis() when the raw reading last changed
    uint32_t _vibLastEvent; // millis() of the last confirmed tap
    uint16_t _vibCount;    // confirmed tap count
    uint8_t _rapidTapCount; // taps in the current rapid-tap sequence
    bool _updateSeqPending; // one-shot flag consumed by the state machine
};
