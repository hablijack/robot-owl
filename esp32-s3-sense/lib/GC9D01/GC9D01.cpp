#include "GC9D01.h"

// GC9D01 commands
#define GC9D01_SWRESET 0x01
#define GC9D01_SLPOUT 0x11
#define GC9D01_DISPON 0x29
#define GC9D01_CASET 0x2A
#define GC9D01_RASET 0x2B
#define GC9D01_RAMWR 0x2C
#define GC9D01_COLMOD 0x3A
#define GC9D01_MADCTL 0x36
#define GC9D01_PTLAR 0x30
#define GC9D01_VSCRSDEF 0x33
#define GC9D01_POFC 0xB1
#define GC9D01_COMM 0xB4
#define GC9D01_COLCTRL 0xB6
#define GC9D01_PCMD 0xB7
#define GC9D01_PWCTRL1 0xC1
#define GC9D01_VCOMCTRL1 0xC5
#define GC9D01_VCOMCTRL2 0xC7
#define GC9D01_RDID1 0xA1
#define GC9D01_RDID2 0xA2
#define GC9D01_RDID3 0xA3
#define GC9D01_RAMCTRL 0xB0
#define GC9D01_RAMCFG 0xB4
#define GC9D01_TCMD 0xB6
#define GC9D01_PGC 0xE0
#define GC9D01_NGC 0xE1

GC9D01::GC9D01(int8_t sck, int8_t mosi, int8_t dc, int8_t cs, int8_t rst)
    : _sck(sck), _mosi(mosi), _dc(dc), _cs(cs), _rst(rst) {
}

GC9D01::~GC9D01() {
    if (_fb) {
        free(_fb);
    }
}

bool GC9D01::begin() {
    // Allocate framebuffer in PSRAM
    _fb = (uint16_t*)ps_calloc(LCD_WIDTH * LCD_HEIGHT, sizeof(uint16_t));
    if (!_fb) {
        return false;
    }

    // Setup SPI pins if using hardware SPI with arbitrary pins
    _spi.begin(_sck, -1, _mosi, _cs);
    _spi.setFrequency(LCD_SPI_FREQ);

    pinMode(_dc, OUTPUT);
    pinMode(_cs, OUTPUT);
    digitalWrite(_cs, HIGH);
    digitalWrite(_dc, LOW);

    if (_rst >= 0) {
        pinMode(_rst, OUTPUT);
        digitalWrite(_rst, HIGH);
        delay(20);
        digitalWrite(_rst, LOW);
        delay(20);
        digitalWrite(_rst, HIGH);
        delay(120);
    }

    // GC9D01 initialization sequence (based on TFT_eSPI PR #3783)
    writeCommand(GC9D01_SWRESET);
    delay(120);

    writeCommand(GC9D01_SLPOUT);
    delay(120);

    // Power control A
    writeCmdData((const uint8_t[]){0x00, 0x00, 0x00, 0x00}, 4); // Register needs to be written

    // Power control B
    writeCommand(GC9D01_PWCTRL1);
    writeData8(0x00);

    // Panel driving settings
    writeCommand(GC9D01_COMM);
    writeData8(0x00); // ENCOM_NONE

    // Display timing control
    writeCommand(GC9D01_POFC);
    writeData8(0x01); // Frame rate 75Hz
    writeData8(0x3B); // Dummy sequences related to RGB interface
    writeData8(0x03);

    writeCommand(GC9D01_VSCRSDEF); // Vertical scroll definitions
    writeData8(0x00); // TFA
    writeData8(0x00); // VSA
    writeData8(0xA0); // BFA

    writeCommand(GC9D01_PTLAR);
    writeData8(0x00);
    writeData8(0x00);
    writeData8(0x00);
    writeData8(0xA0);

    writeCommand(GC9D01_VCOMCTRL2);
    writeData8(0x00);
    writeData8(0x00);

    writeCommand(GC9D01_PWCTRL1);
    writeData8(0x80); // VRH=5.0V

    writeCommand(GC9D01_COMM);
    writeData8(0x00); // COM_SPLIT_NONE, COM_INTLEAVE_NONE

    writeCommand(GC9D01_COLCTRL);
    writeData8(0x00); // NR=011b (6 lines), SC=0, NC=0

    // Pixel format - RGB565
    writeCommand(GC9D01_COLMOD);
    writeData8(0x05); // 16-bit/pixel

    // Memory access control - BGR order, row/col increment
    uint8_t madctl = GC9D01_MADCTL;
    writeCommand(madctl);
    writeData8(0xC8); // MY=0, MX=0, MV=0, ML=1(BGR), MH=1, SS=0, RR=0

    // Gamma curve - positive gamma
    writeCommand(GC9D01_PGC);
    const uint8_t pgc[] = {
        0x00, 0x03, 0x09, 0x08, 0x08, 0x1A, 0x25, 0x2F,
        0x3D, 0x46, 0x4B, 0x55, 0x5C, 0x66, 0x6E, 0x74
    };
    writeData(pgc, sizeof(pgc));

    // Gamma curve - negative gamma
    writeCommand(GC9D01_NGC);
    const uint8_t ngc[] = {
        0x00, 0x03, 0x09, 0x08, 0x07, 0x1A, 0x25, 0x2F,
        0x3C, 0x46, 0x4B, 0x54, 0x5B, 0x65, 0x6D, 0x73
    };
    writeData(ngc, sizeof(ngc));

    // Display on
    writeCommand(GC9D01_DISPON);
    delay(20);

    fillScreen(0x0000); // Black
    flush();

    return true;
}

void GC9D01::writeCommand(uint8_t cmd) {
    digitalWrite(_dc, LOW);
    _spi.transfer(cmd);
    digitalWrite(_dc, HIGH);
}

void GC9D01::writeData(const uint8_t* data, size_t len) {
    _spi.writeBytes(data, len);
}

void GC9D01::writeData8(uint8_t data) {
    _spi.transfer(data);
}

void GC9D01::writeCmdData(const uint8_t* params, size_t len) {
    writeCommand(0xFF); // Register write command
    writeData(params, len);
}

void GC9D01::reset() {
    writeCommand(GC9D01_SWRESET);
    delay(120);
}

void GC9D01::setWindow(int16_t x0, int16_t y0, int16_t x1, int16_t y1) {
    // Column address set
    writeCommand(GC9D01_CASET);
    uint8_t data[] = {
        (uint8_t)(x0 >> 8), (uint8_t)x0,
        (uint8_t)(x1 >> 8), (uint8_t)x1
    };
    writeData(data, 4);

    // Row address set
    writeCommand(GC9D01_RASET);
    data[0] = (uint8_t)(y0 >> 8);
    data[1] = (uint8_t)y0;
    data[2] = (uint8_t)(y1 >> 8);
    data[3] = (uint8_t)y1;
    writeData(data, 4);

    // Memory write
    writeCommand(GC9D01_RAMWR);
}

void GC9D01::pushPixels(const uint16_t* data, size_t len) {
    // Bulk 16-bit pixel write: the SPI HAL batches 64 bytes per
    // hardware-FIFO transaction instead of one 16-bit transfer per pixel.
    _spi.writePixels(data, len * sizeof(uint16_t));
}

void GC9D01::flush() {
    setWindow(0, 0, LCD_WIDTH - 1, LCD_HEIGHT - 1);
    digitalWrite(_dc, HIGH);
    // Push the whole framebuffer in one bulk pixel write (800 FIFO
    // transactions) rather than 25,600 individual 16-bit transfers.
    _spi.writePixels(_fb, LCD_WIDTH * LCD_HEIGHT * sizeof(uint16_t));
}

void GC9D01::fillScreen(uint16_t color) {
    for (size_t i = 0; i < LCD_WIDTH * LCD_HEIGHT; i++) {
        _fb[i] = color;
    }
}

void GC9D01::drawPixel(int16_t x, int16_t y, uint16_t color) {
    if (x < 0 || x >= LCD_WIDTH || y < 0 || y >= LCD_HEIGHT) return;
    _fb[y * LCD_WIDTH + x] = color;
}

void GC9D01::drawFastVLine(int16_t x, int16_t y, int16_t h, uint16_t color) {
    for (int16_t i = 0; i < h; i++) {
        drawPixel(x, y + i, color);
    }
}

void GC9D01::drawFastHLine(int16_t x, int16_t y, int16_t w, uint16_t color) {
    for (int16_t i = 0; i < w; i++) {
        drawPixel(x + i, y, color);
    }
}

void GC9D01::drawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint16_t color) {
    int16_t dx = abs(x1 - x0);
    int16_t dy = abs(y1 - y0);
    int16_t sx = x0 < x1 ? 1 : -1;
    int16_t sy = y0 < y1 ? 1 : -1;
    int16_t err = dx - dy;

    while (true) {
        drawPixel(x0, y0, color);
        if (x0 == x1 && y0 == y1) break;
        int16_t e2 = 2 * err;
        if (e2 > -dy) { err -= dy; x0 += sx; }
        if (e2 < dx) { err += dx; y0 += sy; }
    }
}

void GC9D01::drawRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
    drawFastHLine(x, y, w, color);
    drawFastHLine(x, y + h - 1, w, color);
    drawFastVLine(x, y, h, color);
    drawFastVLine(x + w - 1, y, h, color);
}

void GC9D01::fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
    for (int16_t i = 0; i < h; i++) {
        drawFastHLine(x, y + i, w, color);
    }
}

void GC9D01::drawCircle(int16_t cx, int16_t cy, int16_t r, uint16_t color) {
    int16_t x = r;
    int16_t y = 0;
    int16_t err = 0;

    auto plot = [this, cx, cy, color](int16_t dx, int16_t dy) {
        drawPixel(cx + dx, cy + dy, color);
        drawPixel(cx + dy, cy + dx, color);
        drawPixel(cx - dy, cy + dx, color);
        drawPixel(cx - dx, cy + dy, color);
        drawPixel(cx - dx, cy - dy, color);
        drawPixel(cx - dy, cy - dx, color);
        drawPixel(cx + dy, cy - dx, color);
        drawPixel(cx + dx, cy - dy, color);
    };

    plot(x, y);

    while (x > y) {
        y++;
        err += 1 + 2 * y;
        if (err + 2 * (-x - 1) + 1 > 0) {
            x--;
            err += 1 + 2 * (-x);
        }
        plot(x, y);
    }
}

void GC9D01::fillCircle(int16_t cx, int16_t cy, int16_t r, uint16_t color) {
    drawFastVLine(cx, cy - r, 2 * r + 1, color);
    int16_t x = r;
    int16_t y = 0;
    int16_t err = 0;

    auto plot = [this, cx, cy, color](int16_t dx, int16_t dy) {
        if (dy <= dx) {
            drawFastVLine(cx + dx, cy - dy, 2 * dy + 1, color);
            drawFastVLine(cx - dx, cy - dy, 2 * dy + 1, color);
        }
    };

    plot(x, y);

    while (x > y) {
        y++;
        err += 1 + 2 * y;
        if (err + 2 * (-x - 1) + 1 > 0) {
            x--;
            err += 1 + 2 * (-x);
        }
        plot(x, y);
    }
}
