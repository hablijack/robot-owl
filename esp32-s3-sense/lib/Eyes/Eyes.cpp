#include "Eyes.h"
#include <math.h>

// Eye geometry and colors come from common.h (included via Eyes.h).

Eyes::Eyes(GC9D01& left, GC9D01& right)
    : _left(left), _right(right),
      _expr(EyeExpression::NEUTRAL),
      _sleeping(false),
      _gazeX(0.0f),
      _gazeY(0.0f),
      _blinkProgress(0),
      _blinkSpeed(3),
      _blinking(false),
      _lastBlink(0),
      _nextBlinkTime(millis() + 3000),
      _dirty(true),
      _lastExpr(EyeExpression::NEUTRAL),
      _lastSleeping(false),
      _lastIrisCX(EYE_CX),
      _lastIrisCY(EYE_CY),
      _lastBlinkProgress(0),
      _lastAnimFrame(0),
      _animPhase(0) {
}

void Eyes::setExpression(EyeExpression expr) {
    _expr = expr;
    if (expr == EyeExpression::SLEEPING) {
        _sleeping = true;
    } else {
        _sleeping = false;
    }
}

void Eyes::setGaze(float x, float y) {
    _gazeX = constrain(x, -1.0f, 1.0f);
    _gazeY = constrain(y, -1.0f, 1.0f);
}

void Eyes::blink(uint8_t speed) {
    // speed: 1=fast ... 5=slow. Maps to the number of render ticks the eye
    // stays closed (the "dip" of the blink). Lower = snappier blink.
    _blinkSpeed = constrain(speed, 1, 5);
    _blinking = true;
    _blinkProgress = 0;
}

void Eyes::render() {
    // Auto-blink every 2-5 seconds (not while showing the update spinner)
    uint32_t now = millis();
    if (!_blinking && !_sleeping && _expr != EyeExpression::UPDATE && now > _nextBlinkTime) {
        blink();
        _lastBlink = now;
        _nextBlinkTime = now + 2000 + random(3000);
    }

    // Update blink animation. Total duration = 2 * _blinkSpeed ticks
    // (close over _blinkSpeed ticks, then reopen over _blinkSpeed ticks).
    if (_blinking) {
        _blinkProgress++;
        if (_blinkProgress > 2 * _blinkSpeed) {
            _blinking = false;
            _blinkProgress = 0;
        }
    }

    // Calculate iris position based on gaze.
    // Vertical travel is intentionally smaller than horizontal (6 vs 8 px)
    // so the eyes track in a "natural" way — real eyes move less up/down
    // than side to side. Don't "fix" this into a symmetric range.
    int irisCX = EYE_CX + (int)(_gazeX * 8);
    int irisCY = EYE_CY + (int)(_gazeY * 6);

    // The UPDATE spinner animates continuously: force a new frame
    // every 50ms (~20fps) while the update expression is active.
    if (_expr == EyeExpression::UPDATE && now - _lastAnimFrame >= 50) {
        _dirty = true;
        _animPhase = (_animPhase + 30) % 360;
    }

    // Skip the redraw if the visible frame is unchanged
    if (!_dirty &&
        _expr == _lastExpr &&
        _sleeping == _lastSleeping &&
        irisCX == _lastIrisCX &&
        irisCY == _lastIrisCY &&
        _blinkProgress == _lastBlinkProgress) {
        return;
    }

    renderEye(_left, irisCX, irisCY);
    renderEye(_right, irisCX, irisCY);

    _dirty = false;
    _lastExpr = _expr;
    _lastSleeping = _sleeping;
    _lastIrisCX = irisCX;
    _lastIrisCY = irisCY;
    _lastBlinkProgress = _blinkProgress;
    _lastAnimFrame = now;
}

void Eyes::renderEye(GC9D01& lcd, int irisCX, int irisCY) {
    // Clear framebuffer
    lcd.fillScreen(COLOR_SCLERA);

    if (_sleeping) {
        // Closed eyes - just eyelids.
        drawEyelids(lcd, 0.0f);
        return;
    }

    // Draw sclera (white circle)
    drawSclera(lcd);

    // Draw iris
    drawIris(lcd, irisCX, irisCY);

    // Draw pupil
    drawPupil(lcd, irisCX, irisCY);

    // Draw highlight (specular reflection)
    drawHighlight(lcd, irisCX - 6, irisCY - 8);

    // The UPDATE spinner is a ring *around* the eye (radius SCLERA_R+5). It
    // must be drawn BEFORE the eyelids, otherwise the full-width eyelid fill
    // would overdraw the top/bottom of the ring and the spinner would look
    // clipped. Other overlays (eyebrows) sit above the eye and are drawn
    // after the eyelids.
    if (_expr == EyeExpression::UPDATE) {
        drawExpressionOverlay(lcd);
    }

    // Draw eyelids based on expression and blink state
    float openness = 1.0f;
    if (_blinking) {
        // Close then open, paced by _blinkSpeed (see render()).
        float half = (float)_blinkSpeed;
        if (_blinkProgress <= _blinkSpeed) {
            openness = 1.0f - (float)_blinkProgress / half;
        } else {
            openness = (float)(_blinkProgress - _blinkSpeed) / half;
        }
    }

    switch (_expr) {
        case EyeExpression::SLEEPY:
            openness = 0.5f;
            break;
        case EyeExpression::SURPRISED:
            openness = 1.2f; // Slightly wider
            break;
        case EyeExpression::ANGRY:
            openness = 0.7f;
            break;
        default:
            break;
    }

    drawEyelids(lcd, openness);

    if (_expr != EyeExpression::UPDATE) {
        drawExpressionOverlay(lcd);
    }
}

void Eyes::drawSclera(GC9D01& lcd) {
    lcd.fillCircle(EYE_CX, EYE_CY, SCLERA_R, COLOR_SCLERA);
    // Subtle outline
    lcd.drawCircle(EYE_CX, EYE_CY, SCLERA_R, 0xA51F); // Dark gray
}

void Eyes::drawIris(GC9D01& lcd, int cx, int cy) {
    // Flat iris fill. (A concentric-ring "gradient" was attempted here but the
    // fillCircle below covers it, so the rings never showed — removed.)
    lcd.fillCircle(cx, cy, IRIS_R, COLOR_IRIS);
}

void Eyes::drawPupil(GC9D01& lcd, int cx, int cy) {
    lcd.fillCircle(cx, cy, PUPIL_R, COLOR_PUPIL);
}

void Eyes::drawHighlight(GC9D01& lcd, int cx, int cy) {
    lcd.fillCircle(cx, cy, HIGHLIGHT_R, COLOR_HIGHLIGHT);
    // Smaller bright highlight
    lcd.fillCircle(cx + 1, cy + 1, 2, 0xD620); // Slightly dimmer white
}

void Eyes::drawEyelids(GC9D01& lcd, float openness) {
    openness = constrain(openness, 0.0f, 1.2f);

    // Calculate eyelid positions
    int eyeTop = EYE_CY - SCLERA_R;
    int eyeBottom = EYE_CY + SCLERA_R;
    int eyeHeight = 2 * SCLERA_R;

    // Openness determines how much of the eye is visible
    // 1.0 = fully open, 0.0 = fully closed
    float visibleHeight = eyeHeight * openness;
    int topLidY = EYE_CY - (int)(visibleHeight / 2);
    int bottomLidY = EYE_CY + (int)(visibleHeight / 2);

    // Draw top eyelid (skin tone, covering everything above)
    lcd.fillRect(0, 0, LCD_WIDTH, topLidY, COLOR_EYELID);

    // Draw bottom eyelid (skin tone, covering everything below)
    lcd.fillRect(0, bottomLidY, LCD_WIDTH, LCD_HEIGHT - bottomLidY, COLOR_EYELID);

    // Eyelid edge line
    if (topLidY > 0 && topLidY < LCD_HEIGHT) {
        lcd.drawFastHLine(0, topLidY, LCD_WIDTH, 0x5A88); // Darker eyelid line
    }
    if (bottomLidY > 0 && bottomLidY < LCD_HEIGHT) {
        lcd.drawFastHLine(0, bottomLidY - 1, LCD_WIDTH, 0x5A88);
    }
}

void Eyes::drawExpressionOverlay(GC9D01& lcd) {
    switch (_expr) {
        case EyeExpression::HAPPY:
            // Curved happy eyebrows (above the eyes)
            // Simple arc using multiple pixels
            for (int x = -50; x <= 50; x++) {
                int y = -SCLERA_R - 8 + (x * x) / 200;
                lcd.drawPixel(EYE_CX + x, EYE_CY + y, COLOR_EYEBROW);
            }
            break;

        case EyeExpression::ANGRY:
            // Angry eyebrows - angled downward toward center
            for (int x = -45; x <= -10; x++) {
                int y = -SCLERA_R - 5 + (x + 45) * 1;
                lcd.drawPixel(EYE_CX + x, EYE_CY + y, COLOR_EYEBROW);
            }
            for (int x = 10; x <= 45; x++) {
                int y = -SCLERA_R - 5 - (x - 45) * 1;
                lcd.drawPixel(EYE_CX + x, EYE_CY + y, COLOR_EYEBROW);
            }
            break;

        case EyeExpression::SURPRISED:
            // Raised eyebrows
            for (int x = -40; x <= 40; x++) {
                int y = -SCLERA_R - 12 + (x * x) / 300;
                lcd.drawPixel(EYE_CX + x, EYE_CY + y, COLOR_EYEBROW);
            }
            break;

        case EyeExpression::SEARCHING:
        case EyeExpression::DETECTING:
            // Pulsing ring around the eye (drawn as dashed circle)
            for (int angle = 0; angle < 360; angle += 15) {
                int rad = angle * 3.14159 / 180;
                int px = EYE_CX + (int)((SCLERA_R + 5) * cos(rad));
                int py = EYE_CY + (int)((SCLERA_R + 5) * sin(rad));
                lcd.drawPixel(px, py, 0x080F); // Dark marker
            }
            break;

        case EyeExpression::UPDATE:
            // Two counter-rotating green arcs (spinner) around the eye.
            // Drawn before the eyelids (see renderEye) so the ring is not
            // clipped by the eyelid fill.
            for (int a = 0; a < 120; a += 6) {
                int rad = (_animPhase + a) * 3.14159f / 180.0f;
                lcd.drawPixel(EYE_CX + (int)((SCLERA_R + 5) * cos(rad)),
                               EYE_CY + (int)((SCLERA_R + 5) * sin(rad)),
                               COLOR_UPDATE);
            }
            for (int a = 0; a < 120; a += 6) {
                int rad = (180 - _animPhase + a) * 3.14159f / 180.0f;
                lcd.drawPixel(EYE_CX + (int)((SCLERA_R + 5) * cos(rad)),
                               EYE_CY + (int)((SCLERA_R + 5) * sin(rad)),
                               COLOR_UPDATE);
            }
            break;

        case EyeExpression::ERROR:
            // Hardware fault: a red X across the eye (drawn after the eyelids,
            // so it stays visible on top of the lid fill).
            {
                int r = SCLERA_R - 8;
                for (int d = -r; d <= r; d += 2) {
                    lcd.drawPixel(EYE_CX + d, EYE_CY + d, COLOR_ERROR);
                    lcd.drawPixel(EYE_CX + d, EYE_CY - d, COLOR_ERROR);
                }
            }
            break;

        default:
            break;
    }
}
