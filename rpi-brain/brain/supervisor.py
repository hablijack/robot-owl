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
from brain.locations import LocationsStore, DEFAULT_LOCATIONS_FILE
from brain.navigation import Navigation

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

        # Phase 4: autonomous "fall asleep on inactivity" policy. The owl is
        # put to sleep (via the existing firmware "sleep" command) after
        # `after_s` with NO interaction trigger (face / vibration / speech).
        # The mirror image -- waking -- is mostly the firmware's job (it already
        # self-wakes on a face or a vibration); the RPi adds "wake on speech".
        # Disabled by default so existing deployments are unaffected.
        auto_sleep_cfg = (config or {}).get("supervisor", {}).get("auto_sleep", {})
        self.auto_sleep_enabled = bool(auto_sleep_cfg.get("enabled", False))
        self.auto_sleep_after_s = float(auto_sleep_cfg.get("after_s", 60))
        # Epoch seconds of the most recent interaction trigger (face, vibration,
        # or speech). Exposed to the Speech worker so it can reset the timer
        # when it hears the user. 0.0 until the first trigger.
        self.last_activity: float = 0.0
        self._last_auto_sleep_sent = 0.0

        # Navigation ("guide me home"): the RPi computes a compass bearing to a
        # named destination and tells the ESP32 which way to point the head.
        # The store is loaded from disk here (so the web UI and speech share it);
        # the controller is attached by main.py once the Speech/web refs exist.
        nav_cfg = (config or {}).get("navigation", {})
        nav_file = nav_cfg.get("locations_file") or DEFAULT_LOCATIONS_FILE
        self.locations = LocationsStore(nav_file)
        self.navigation = Navigation(serial, self, self.locations, config)

    def on_telemetry(self, telemetry: Telemetry) -> None:
        """Handle a telemetry frame from the ESP32 (called by the read loop)."""
        now = time.time()
        self.last = telemetry

        # Phase 4: an interaction trigger (a face in frame or a vibration/tap)
        # counts as "the environment is active" and resets the auto-sleep timer.
        # A face is only a trigger while the owl is awake: once it is SLEEPING
        # the firmware self-wakes on the face (and re-enters DETECTING), so the
        # wake is already in flight -- we must not, in the same frame, treat that
        # same face as "activity" and cancel a pending sleep. The Speech worker
        # separately resets this timer when it hears the user (see
        # Speech._register_activity).
        #
        # Navigation is ALSO a trigger: while the owl is guiding you home you
        # may be walking with no face in frame, and it must not fall asleep
        # mid-navigation. (check_auto_sleep likewise never sleeps while
        # navigating.)
        if self.auto_sleep_enabled and self.last_state not in ("sleeping", "update"):
            if (telemetry.face and telemetry.face.detected) or \
               (telemetry.vibration and telemetry.vibration.detected) or \
               (self.navigation and self.navigation.is_active()):
                self.last_activity = now

        # Navigation: forward every frame so the controller can re-aim the head
        # from the freshest GPS + heading, and handle arrival / self-timeout.
        if self.navigation is not None:
            self.navigation.on_telemetry(telemetry)

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
    # Phase 4: autonomous sleep on inactivity
    # ------------------------------------------------------------------
    def register_activity(self, now: float = None) -> None:
        """Record that the user just interacted (the RPi heard them speak).

        Called by the Speech worker when it transcribes an utterance. Resets the
        inactivity timer so the owl is not put to sleep while the user is
        talking to it. (A face or a vibration resets the timer on their own via
        on_telemetry.)
        """
        if self.auto_sleep_enabled:
            self.last_activity = now if now is not None else time.time()

    def check_auto_sleep(self, now: float = None) -> None:
        """Put the owl to sleep if the environment has been quiet too long.

        Called from the idle loop (serial_handler invokes it on quiet
        iterations). It is a pure policy decision on the RPi side: when the owl
        is awake and no interaction trigger (face / vibration / speech) has been
        seen for `auto_sleep_after_s`, send the firmware's existing "sleep"
        command. The firmware then runs its own SLEEPING state, which it already
        knows how to wake on a face or a vibration.

        Guards:
          * no-op unless auto_sleep is enabled;
          * never sends while the owl is already SLEEPING (idempotent);
          * never sends while in UPDATE mode (the owl is on an isolated SoftAP
            and unreachable over the normal serial link);
          * rate-limited to one "sleep" send per `after_s` window, so a stale
            link (no new frames) does not spam the command.
        """
        if not self.auto_sleep_enabled:
            return
        if self.last is None:
            return
        now = now if now is not None else time.time()
        state = self.last_state or (self.last.state if self.last else None)
        if state in ("sleeping", "update"):
            return
        # Never auto-sleep while guiding: the owl is actively pointing the user
        # somewhere and may have no face in frame while they walk.
        if self.navigation is not None and self.navigation.is_active():
            return
        # Baseline: with no trigger yet, start the clock from the first frame.
        if self.last_activity == 0.0:
            self.last_activity = now
        if (now - self.last_activity) >= self.auto_sleep_after_s:
            if (now - self._last_auto_sleep_sent) >= self.auto_sleep_after_s:
                logger.info(
                    "Auto-sleep: no interaction for %.0fs (no face/tap/speech) -> putting owl to sleep",
                    now - self.last_activity,
                )
                self._last_auto_sleep_sent = now
                self.sleep()

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

    # Navigation ("guide me home"): thin wrappers over the Navigation controller
    # so the web UI and speech can start/stop it through the supervisor.
    def nav_start(self, name: str) -> bool:
        """Start guiding toward a named location (no-op / False if unknown)."""
        if self.navigation is None:
            return False
        return self.navigation.start(name)

    def nav_stop(self, reason: str = "command") -> bool:
        """Stop guiding (head recenters)."""
        if self.navigation is None:
            return False
        return self.navigation.stop(reason)

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
    # Each behavior state maps to a distinct recorded owl-call (see
    # assets/sounds/). If a recording is missing, Audio falls back to a
    # synthesized tone of the same name, so cues still work.
    _STATE_SFX = {
        "detecting": "detecting",
        "interacting": "interacting",
        "sleeping": "sleeping",
        "waking": "waking",
        # also covers the wake-up / attention moments
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
