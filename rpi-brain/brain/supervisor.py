"""
Robot Owl RPi Brain - Supervisor

The ESP32 owns the behavior state machine and drives the eyes, gaze, and
servos autonomously from its local sensors (vibration + on-device face
detection). The RPi no longer runs its own behavior state machine.

The RPi is a supervisor: it logs telemetry and can send high-level policy
commands (sleep/wake) plus temporary overrides (expression/gaze). It never
drives the owl's behavior on its own.
"""

import time
import logging
from typing import Optional

from brain.serial_handler import SerialHandler, Telemetry

logger = logging.getLogger(__name__)


class Supervisor:
    """Supervises the ESP32: logs telemetry and sends policy commands.

    This is intentionally NOT a state machine. The ESP32 is the single owner
    of the owl's behavior; the supervisor only observes and issues policy.
    """

    def __init__(self, serial: SerialHandler, config: dict):
        self.serial = serial
        self.config = config
        self.last_state: Optional[str] = None
        self._last_face_log = 0.0
        self._face_log_interval_s = 1.0
        self._update_mode_active = False
        self._last_fw: Optional[str] = None

    def on_telemetry(self, telemetry: Telemetry) -> None:
        """Handle a telemetry frame from the ESP32 (called by the read loop)."""
        # Log the firmware version once (and again if it changes, e.g. after
        # an OTA update).
        if telemetry.firmware and telemetry.firmware != self._last_fw:
            if self._last_fw is None:
                logger.info("Owl firmware version: %s", telemetry.firmware)
            else:
                logger.info("Owl firmware changed: %s -> %s", self._last_fw, telemetry.firmware)
            self._last_fw = telemetry.firmware

        # Log state transitions.
        if telemetry.state != self.last_state:
            logger.info("Owl state: %s -> %s", self.last_state, telemetry.state)
            self.last_state = telemetry.state

        # Update mode: the owl is on an isolated SoftAP and unreachable
        # over the normal USB serial link. Surface the AP credentials once on
        # entry and once on exit so the operator can join the network and
        # flash firmware via the /update page.
        if telemetry.update.active and not self._update_mode_active:
            self._update_mode_active = True
            logger.info(
                "Owl entered UPDATE mode: join WiFi '%s' (password '%s') "
                "and open %s to flash firmware. Tap the owl once to exit.",
                telemetry.update.ssid,
                telemetry.update.password,
                telemetry.update.url,
            )
        elif not telemetry.update.active and self._update_mode_active:
            self._update_mode_active = False
            logger.info("Owl exited UPDATE mode; normal operation resumed.")

        # Log face detections (throttled to avoid log spam).
        if telemetry.face.detected:
            now = time.time()
            if now - self._last_face_log >= self._face_log_interval_s:
                logger.info(
                    "Face detected (conf=%.2f, gaze=%.2f, %.2f)",
                    telemetry.face.confidence,
                    telemetry.face.gaze_x,
                    telemetry.face.gaze_y,
                )
                self._last_face_log = now

    # ------------------------------------------------------------------
    # Policy commands (change the owl's state)
    # ------------------------------------------------------------------
    def sleep(self) -> bool:
        """Put the owl to sleep."""
        logger.info("Supervisor: sleep")
        return self.serial.send_command({"type": "sleep"})

    def wake(self) -> bool:
        """Wake the owl from sleep."""
        logger.info("Supervisor: wake")
        return self.serial.send_command({"type": "wake"})

    # ------------------------------------------------------------------
    # Temporary overrides (do NOT change the owl's state)
    # ------------------------------------------------------------------
    def set_expression(self, expression: str) -> bool:
        """Temporarily override the eye expression."""
        return self.serial.set_expression(expression)

    def set_gaze(self, x: float, y: float) -> bool:
        """Temporarily override the gaze direction."""
        return self.serial.set_gaze(x, y)

    def blink(self, speed: int = 3) -> bool:
        """Trigger a blink animation."""
        return self.serial.blink(speed)
