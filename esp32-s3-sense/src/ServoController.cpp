#include "ServoController.h"

ServoController::ServoController() {
    for (int i = 0; i < NUM_SERVO_CHANNELS; i++) {
        _currentAngles[i] = 0;
        _targetAngles[i] = 0;
        _dirty[i] = false;
    }
}

bool ServoController::begin() {
    _pca.begin();
    _pca.setPWMFreq(50); // 50Hz for servos



    // Set all to center position
    setCenter();
    return true;
}

void ServoController::setAngle(uint8_t channel, float angle) {
    if (channel >= NUM_SERVO_CHANNELS) return;
    angle = constrain(angle, SERVO_MIN_ANGLE, SERVO_MAX_ANGLE);
    _targetAngles[channel] = angle;
    _dirty[channel] = true;
}

void ServoController::setAngles(const float* angles, uint8_t count) {
    for (uint8_t i = 0; i < count && i < NUM_SERVO_CHANNELS; i++) {
        setAngle(i, angles[i]);
    }
}

void ServoController::setCenter() {
    for (int i = 0; i < NUM_SERVO_CHANNELS; i++) {
        _targetAngles[i] = 0;
        _currentAngles[i] = 0;
        _dirty[i] = true;
    }
}

float ServoController::getAngle(uint8_t channel) const {
    if (channel >= NUM_SERVO_CHANNELS) return 0;
    return _currentAngles[channel];
}

void ServoController::update() {
    for (int i = 0; i < NUM_SERVO_CHANNELS; i++) {
        if (!_dirty[i]) continue;

        float diff = _targetAngles[i] - _currentAngles[i];

        if (abs(diff) <= SERVO_SMOOTH_SPEED) {
            _currentAngles[i] = _targetAngles[i];
            _dirty[i] = false;
        } else {
            _currentAngles[i] += (diff > 0 ? SERVO_SMOOTH_SPEED : -SERVO_SMOOTH_SPEED);
        }

        uint16_t us = (uint16_t)angleToUs(_currentAngles[i]);
        writeMicroseconds(i, us);
    }
}

void ServoController::writeMicroseconds(uint8_t channel, uint16_t us) {
    _pca.setPWM(channel, 0, us);
}

float ServoController::angleToUs(float angle) {
    // Map angle (-45 to 45) to microseconds (1000 to 2000)
    return SERVO_CENTER_US + (angle / SERVO_MAX_ANGLE) * (SERVO_CENTER_US - SERVO_MIN_US);
}
