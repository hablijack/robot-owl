#pragma once

#include <Arduino.h>
#include "GC9D01.h"
#include "common.h"

class Eyes {
public:
    Eyes(GC9D01& left, GC9D01& right);

    void setExpression(EyeExpression expr);
    void setGaze(float x, float y); // -1.0 to 1.0
    void blink(uint8_t speed = 3); // 1=fast, 5=slow
    void render();

    EyeExpression getCurrentExpression() const { return _expr; }

private:
    void drawSclera(GC9D01& lcd);
    void drawIris(GC9D01& lcd, int cx, int cy);
    void drawPupil(GC9D01& lcd, int cx, int cy);
    void drawHighlight(GC9D01& lcd, int cx, int cy);
    void drawEyelids(GC9D01& lcd, float openness);
    void drawExpressionOverlay(GC9D01& lcd);

    void renderEye(GC9D01& lcd, int irisCX, int irisCY);

    GC9D01& _left;
    GC9D01& _right;

    EyeExpression _expr;
    bool _sleeping;
    float _gazeX;
    float _gazeY;
    uint8_t _blinkProgress;
    uint8_t _blinkSpeed;
    bool _blinking;
    uint32_t _lastBlink;
    uint32_t _nextBlinkTime;

    // Dirty-flag rendering: skip the full redraw when the visible
    // frame is identical to the last one sent to the LCDs.
    bool _dirty;
    EyeExpression _lastExpr;
    bool _lastSleeping;
    int _lastIrisCX;
    int _lastIrisCY;
    uint8_t _lastBlinkProgress;

    // UPDATE spinner animation timing
    uint32_t _lastAnimFrame;
    uint16_t _animPhase;
};
