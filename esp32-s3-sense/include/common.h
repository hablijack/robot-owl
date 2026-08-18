#pragma once

// Eye expressions
enum class EyeExpression {
    NEUTRAL,
    HAPPY,
    SLEEPY,
    SURPRISED,
    ANGRY,
    SLEEPING,
    SEARCHING,
    DETECTING,
    UPDATE,
    ERROR
};

// LCD dimensions
#ifndef LCD_WIDTH
#define LCD_WIDTH 160
#endif

#ifndef LCD_HEIGHT
#define LCD_HEIGHT 160
#endif

// Eye geometry
#define EYE_CX (LCD_WIDTH / 2)
#define EYE_CY (LCD_HEIGHT / 2)
#define SCLERA_R 65
#define IRIS_R 28
#define PUPIL_R 14
#define HIGHLIGHT_R 5

// Colors (RGB565)
#define COLOR_SCLERA 0xFFFF
#define COLOR_IRIS 0x080F
#define COLOR_PUPIL 0x0000
#define COLOR_HIGHLIGHT 0xFFFF
#define COLOR_EYELID 0x7BEF
#define COLOR_EYEBROW 0x7BEF
#define COLOR_UPDATE 0x07E0
#define COLOR_ERROR 0xF800
