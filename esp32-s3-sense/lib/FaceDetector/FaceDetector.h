#pragma once

#include <stdint.h>
#include <stdbool.h>

// Face detection result structure
typedef struct {
    bool detected;
    int16_t x;        // Top-left X position (0-319 for QVGA)
    int16_t y;        // Top-left Y position (0-239 for QVGA)
    uint16_t w;       // Width of face bounding box
    uint16_t h;       // Height of face bounding box
    float confidence; // Detection confidence (0.0-1.0)
    float gaze_x;     // Gaze offset X (-1.0 to 1.0)
    float gaze_y;     // Gaze offset Y (-1.0 to 1.0)
} FaceResult_t;

// Initialize face detection module
// Returns true if initialization successful
bool FaceDetector_Init(void);

// Run face detection on current camera frame
// Populates result with detection data
void FaceDetector_Detect(FaceResult_t *result);

// Deinitialize face detection module
void FaceDetector_Deinit(void);
