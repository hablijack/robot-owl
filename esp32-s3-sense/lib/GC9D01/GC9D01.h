#pragma once

#include <Arduino.h>
#include <SPI.h>

#ifndef LCD_WIDTH
#define LCD_WIDTH 160
#endif

#ifndef LCD_HEIGHT
#define LCD_HEIGHT 160
#endif

#ifndef LCD_SPI_FREQ
#define LCD_SPI_FREQ 27000000
#endif

class GC9D01 {
public:
    GC9D01(int8_t sck, int8_t mosi, int8_t dc, int8_t cs, int8_t rst);
    ~GC9D01();

    bool begin();
    void fillScreen(uint16_t color);
    void drawPixel(int16_t x, int16_t y, uint16_t color);
    void drawFastVLine(int16_t x, int16_t y, int16_t h, uint16_t color);
    void drawFastHLine(int16_t x, int16_t y, int16_t w, uint16_t color);
    void drawCircle(int16_t cx, int16_t cy, int16_t r, uint16_t color);
    void fillCircle(int16_t cx, int16_t cy, int16_t r, uint16_t color);
    void drawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint16_t color);
    void drawRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color);
    void fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color);

    void setWindow(int16_t x0, int16_t y0, int16_t x1, int16_t y1);
    void pushPixels(const uint16_t* data, size_t len);
    void flush();

    uint16_t* getFramebuffer() { return _fb; }
    int width() const { return LCD_WIDTH; }
    int height() const { return LCD_HEIGHT; }

private:
    void writeCommand(uint8_t cmd);
    void writeData(const uint8_t* data, size_t len);
    void writeData8(uint8_t data);
    void writeCmdData(const uint8_t* params, size_t len);
    void reset();

    int _sck;
    int _mosi;
    int _dc;
    int _cs;
    int _rst;
    SPIClass _spi;
    uint16_t* _fb;
};
