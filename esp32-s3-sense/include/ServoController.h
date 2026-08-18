#pragma once

#include <Arduino.h>
#include <Adafruit_PWMServoDriver.h>
#include "config.h"

class ServoController {
public:
    ServoController();
    bool begin();

    void setAngle(uint8_t channel, float angle);
    void setAngles(const float* angles, uint8_t count);
    void setCenter();
    void update();

    float getAngle(uint8_t channel) const;

private:
    void writeMicroseconds(uint8_t channel, uint16_t us);
    float angleToUs(float angle);

    Adafruit_PWMServoDriver _pca;
    float _currentAngles[NUM_SERVO_CHANNELS];
    float _targetAngles[NUM_SERVO_CHANNELS];
    bool _dirty[NUM_SERVO_CHANNELS];
};
