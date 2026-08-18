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

    def __init__(self, serial: SerialHandler, config: dict, audio=None):
        self.serial = serial
        self.config = config
        self.audio = audio
        self.last_state: Optional[str] = None
        self.last: Optional[Telemetry] = None
        self._last_face_log = 0.0
        self._face_log_interval_s = 1.0
        self._update_mode_active = False
        self._last_fw: Optional[str] = None
        # Liveness: how often to emit a one-line status, and how long without
        # a telemetry frame before we warn that the link may be down.
        self._heartbeat_interval_s = 30.0
        self._stale_after_s = 10.0
        self._last_heartbeat = 0.0

    def on_telemetry(self, telemetry: Telemetry) -> None:
        """Handle a telemetry frame from the ESP32 (called by the read loop)."""
        now = time.time()
        self.last = telemetry

        # Log the firmware version once (and again if it changes, e.g. after
        # an OTA update).
        if telemetry.firmware and telemetry.firmware != self._last_fw:
            if self._last_fw is None:
                logger.info("Owl firmware version: %s", telemetry.firmware)
            else:
                logger.info("Owl firmware changed: %s -> %s", self._last_fw, telemetry.firmware)
            self._last_fw = telemetry.firmware

        # Log state transitions (and cue a matching sound effect).
        if telemetry.state != self.last_state:
            logger.info("Owl state: %s -> %s", self.last_state, telemetry.state)
            self.last_state = telemetry.state
            self._cue_state_sound(telemetry.state)

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
            if now - self._last_face_log >= self._face_log_interval_s:
                logger.info(
                    "Face detected (conf=%.2f, gaze=%.2f, %.2f)",
                    telemetry.face.confidence,
                    telemetry.face.gaze_x,
                    telemetry.face.gaze_y,
                )
                self._last_face_log = now

        # Periodic one-line liveness status so a quiet system still shows it
        # is alive (and so telemetry staleness is obvious in the log).
        if now - self._last_heartbeat >= self._heartbeat_interval_s:
            self._last_heartbeat = now
            logger.info(
                "Owl alive: state=%s, up=%ds, face=%s, fw=%s, servos=%s",
                telemetry.state,
                telemetry.uptime_ms // 1000,
                "yes" if telemetry.face.detected else "no",
                telemetry.firmware or "?",
                ",".join(f"{a:g}" for a in telemetry.servos),
            )

    def check_stale(self) -> None:
        """Warn (throttled) if no telemetry frame arrived for a while.

        Call this from an idle loop. While the owl is in UPDATE mode it is on
        an isolated SoftAP and legitimately stops sending, so we skip the
        warning there.
        """
        if self.last is None:
            return
        now = time.time()
        if (now - self.last.timestamp) > self._stale_after_s:
            if not self.last.update.active:
                logger.warning(
                    "No telemetry for %.0fs (last state=%s) - serial link problem?",
                    now - self.last.timestamp,
                    self.last.state,
                )
            self.last = None  # avoid re-warning every loop until a frame lands

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

    def play_sound(self, sound: str) -> bool:
        """Play a sound effect on the RPi (MAX98357A I2S amp).

        Audio is entirely on the RPi side — the amp is wired to the Pi's I2S
        bus, not the ESP32. Returns True if it was actually played.
        """
        return self.audio.play(sound) if self.audio else False

    # ------------------------------------------------------------------
    # Sound cues tied to behavior state transitions
    # ------------------------------------------------------------------
    _STATE_SFX = {
        "detecting": "chirp",
        "interacting": "happy",
        "sleeping": "sad",
        # also covers the wake-up moment
        "update": "alert",
        "error": "alert",
    }

    def _cue_state_sound(self, state: str) -> None:
        """Play a short cue when the owl changes behavior state."""
        if self.audio is None:
            return
        sfx = self._STATE_SFX.get(state)
        if sfx:
            self.audio.play(sfx)
