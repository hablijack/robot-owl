#include "Sensors.h"
#include <Adafruit_BNO055.h>
#include <Adafruit_GPS.h>

static Adafruit_BNO055 bno = Adafruit_BNO055(28, ADDR_BNO055);
static Adafruit_GPS GPS(&Wire);

bool Sensors::begin() {
    _imuReady = false;
    _gpsReady = false;

    // Initialize I2C bus (BNO055 + PA1010D GPS + PCA9685 share D0/D1)
    Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);

    // Probe the GPS on the I2C bus (no UART pins used anymore)
    Wire.beginTransmission(ADDR_GPS);
    _gpsReady = (Wire.endTransmission() == 0);

    // PA1010D GPS in native I2C mode
    if (_gpsReady) {
        GPS.begin(ADDR_GPS);
        GPS.sendCommand(PMTK_SET_NMEA_OUTPUT_RMCGGA);
        GPS.sendCommand(PMTK_SET_NMEA_UPDATE_1HZ);
    }

    // BNO055 IMU
    if (!bno.begin()) {
        return false;
    }
    bno.setMode(OPERATION_MODE_IMUPLUS);

    // Give BNO055 time to initialize
    delay(100);

    sensor_t sensor;
    bno.getSensor(&sensor);
    _imuReady = true;

    // SW420 pulls its signal to GND while vibrating, so use an internal
    // pull-up and treat LOW as the active state.
    pinMode(VIBRATION_PIN, INPUT_PULLUP);
    _vibState = false;
    _vibRaw = false;
    _vibRawSince = 0;
    _vibLastEvent = 0;
    _vibCount = 0;
    _rapidTapCount = 0;
    _updateSeqPending = false;

    return true;
}

ImuData Sensors::getImu() {
    ImuData data = {0, 0, 0, false};

    if (!_imuReady) return data;

    sensors_event_t event;
    bno.getEvent(&event);

    data.pitch = event.orientation.y;
    data.roll = event.orientation.x;
    data.yaw = event.orientation.z;

    // Check calibration status
    uint8_t sys, gyro, accel, mag;
    bno.getCalibration(&sys, &gyro, &accel, &mag);
    data.isCalibrated = (sys >= 3 && gyro >= 3 && accel >= 3 && mag >= 3);

    return data;
}

GpsData Sensors::getGps() {
    GpsData data = {0, 0, 0, 0, false};

    if (!_gpsReady) return data;

    // Pump the I2C GPS reader, then parse any complete NMEA sentence
    while (GPS.available()) {
        GPS.read();
    }

    if (GPS.newNMEAreceived()) {
        GPS.parse(GPS.lastNMEA());
    }

    if (GPS.fix) {
        data.latitude = GPS.latitudeDegrees;
        data.longitude = GPS.longitudeDegrees;
        data.altitude = GPS.altitude;
        data.satellites = GPS.satellites;
        data.valid = true;
    }

    return data;
}

VibrationData Sensors::getVibration() {
    VibrationData data = {false, 0, _vibCount, _updateSeqPending};

    // Active-low: the SW420 connects the signal to GND while vibrating.
    bool raw = (digitalRead(VIBRATION_PIN) == LOW);

    if (raw != _vibRaw) {
        _vibRaw = raw;
        _vibRawSince = millis();
    }

    // Accept a change only once the signal has been stable for the debounce
    // window; each confirmed trigger counts as a single event.
    if (raw != _vibState && (millis() - _vibRawSince) >= VIBRATION_DEBOUNCE_MS) {
        _vibState = raw;
        if (_vibState) {
            _vibCount++;
            // Taps within UPDATE_TAP_GAP_MS of each other build a rapid
            // sequence; UPDATE_TAP_REQUIRED in a row arms the one-shot
            // update flag.
            if (millis() - _vibLastEvent <= UPDATE_TAP_GAP_MS) {
                _rapidTapCount++;
            } else {
                _rapidTapCount = 1;
            }
            _vibLastEvent = millis();
            if (_rapidTapCount >= UPDATE_TAP_REQUIRED) {
                _rapidTapCount = 0;
                _updateSeqPending = true;
            }
        }
    }

    data.detected = _vibState;
    data.lastDetected = _vibLastEvent;

    return data;
}