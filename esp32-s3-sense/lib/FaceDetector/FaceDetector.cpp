#include "FaceDetector.h"
#include "config.h"
#include <Arduino.h>
#include <esp_log.h>

static const char *TAG = "FaceDetector";

#if FACE_DETECTION_ENABLED

#include "esp_camera.h"
#include "human_face_detect_msr01.hpp"

static camera_config_t camera_config = {
    .pin_pwdn = -1,
    .pin_reset = -1,
    .pin_xclk = 10,
    .pin_sccb_sda = 40,
    .pin_sccb_scl = 39,
    .pin_d7 = 48,
    .pin_d6 = 11,
    .pin_d5 = 12,
    .pin_d4 = 14,
    .pin_d3 = 16,
    .pin_d2 = 18,
    .pin_d1 = 17,
    .pin_d0 = 15,
    .pin_vsync = 38,
    .pin_href = 47,
    .pin_pclk = 13,
    .xclk_freq_hz = 20000000,
    .ledc_timer = LEDC_TIMER_0,
    .ledc_channel = LEDC_CHANNEL_0,
    .pixel_format = PIXFORMAT_RGB565,
    .frame_size = FRAMESIZE_QVGA,
    .fb_count = 1,
    .fb_location = CAMERA_FB_IN_PSRAM,
    .grab_mode = CAMERA_GRAB_WHEN_EMPTY,
};

static HumanFaceDetectMSR01 *detector = NULL;
static bool initialized = false;

bool FaceDetector_Init(void) {
    if (initialized) return true;

    // Initialize camera
    esp_err_t err = esp_camera_init(&camera_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Camera init failed: %d", err);
        return false;
    }

    // Initialize face detection model (MSR01, weights embedded in prebuilt lib).
    // Tunables live in config.h (FACE_*); first-pass values, validate on hardware.
    detector = new HumanFaceDetectMSR01(FACE_SCORE_THRESHOLD, FACE_NMS_THRESHOLD, FACE_TOP_K, FACE_RESIZE_SCALE);

    initialized = true;
    ESP_LOGI(TAG, "Face detection initialized");
    return true;
}

void FaceDetector_Detect(FaceResult_t *result) {
    if (!initialized || !detector) {
        result->detected = false;
        return;
    }

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        result->detected = false;
        return;
    }

    result->detected = false;

    if (fb->format == PIXFORMAT_RGB565 && fb->width > 0 && fb->height > 0) {
        std::vector<int> input_shape = {1, (int)fb->height, (int)fb->width, 3};
        std::list<dl::detect::result_t> &results = detector->infer<uint16_t>((uint16_t *)fb->buf, input_shape);

        if (!results.empty()) {
            // Take the highest-confidence face
            const dl::detect::result_t *best = NULL;
            for (auto &r : results) {
                if (!best || r.score > best->score) best = &r;
            }

            if (best) {
                // Post-filter: require the best face to clear FACE_MIN_CONFIDENCE
                // before we report a detection. The model's own score_threshold
                // already drops the weakest boxes, but this second gate stops
                // borderline (0.25-0.5) boxes from flickering the state machine.
                if (best->score >= FACE_MIN_CONFIDENCE) {
                    result->detected = true;
                    result->x = best->box[0];
                    result->y = best->box[1];
                    result->w = best->box[2] - best->box[0];
                    result->h = best->box[3] - best->box[1];
                    result->confidence = best->score;

                    // Gaze offset from face center relative to frame center (-1..1)
                    int cx = (best->box[0] + best->box[2]) / 2;
                    int cy = (best->box[1] + best->box[3]) / 2;
                    result->gaze_x = (float)(cx - (int)fb->width / 2) / ((int)fb->width / 2);
                    result->gaze_y = (float)(cy - (int)fb->height / 2) / ((int)fb->height / 2);

                    ESP_LOGD(TAG, "Face detected: (%d,%d) %dx%d conf=%.2f",
                             result->x, result->y, result->w, result->h, result->confidence);
                } else {
                    ESP_LOGD(TAG, "Face below confidence gate (conf=%.2f < %.2f)",
                             best->score, FACE_MIN_CONFIDENCE);
                }
            }
        }
    }

    esp_camera_fb_return(fb);
}

void FaceDetector_Deinit(void) {
    if (detector) {
        delete detector;
        detector = NULL;
    }
    esp_camera_deinit();
    initialized = false;
    ESP_LOGI(TAG, "Face detection deinitialized");
}

#else
// Face detection disabled - stub implementation
// Returns no faces detected

bool FaceDetector_Init(void) {
    ESP_LOGW(TAG, "Face detection not enabled (compile with FACE_DETECTION_ENABLED=1)");
    return false;
}

void FaceDetector_Detect(FaceResult_t *result) {
    result->detected = false;
}

void FaceDetector_Deinit(void) {
    // Nothing to clean up in stub mode
}
#endif