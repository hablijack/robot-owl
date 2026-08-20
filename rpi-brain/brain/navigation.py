"""
Robot Owl RPi Brain - Navigation controller.

The owl acts as a compass that points at a named destination. The RPi owns all
the logic here: given the live GPS fix + IMU heading (from telemetry) and a
destination from the LocationsStore, it computes the bearing to the destination
and the head-servo angle that points the beak at it, then streams that angle to
the ESP32 (`nav` command). The ESP32 just holds the head at the given angle
(its NAVIGATING state); it does no math.

Driving model
  on_telemetry() is called by the supervisor for every telemetry frame (~2 Hz).
  While a navigation is active it re-computes the aim from the freshest GPS +
  heading and (rate-limited) sends a `nav angle:` update. It is a no-op when
  there is no active navigation, or no GPS fix / uncalibrated heading, so a
  stale or missing sensor never makes the head flail.

Exit (see NAVIGATION_PLAN.md "Exiting navigation mode") -- any of these ends it:
  * a spoken stop keyword (Speech calls stop()),
  * the web UI Stop button (supervisor calls stop()),
  * arrival (distance < arrive_m),
  * a self-timeout (no telemetry for timeout_s -> the RPi drops it; the
    firmware independently times out too, so a dead link can't stick the head).
"""

import logging
import time

from brain import geo
from brain.locations import LocationsStore

logger = logging.getLogger(__name__)


class Navigation:
    """Computes and streams the head aim toward a named destination."""

    def __init__(self, serial, supervisor, locations: LocationsStore, config: dict):
        self.serial = serial
        self.supervisor = supervisor
        self.locations = locations
        self.audio = getattr(supervisor, "audio", None)

        cfg = (config or {}).get("navigation", {})
        self.enabled = bool(cfg.get("enabled", False))
        self.head_min = float(cfg.get("head_min", -45))
        self.head_max = float(cfg.get("head_max", 45))
        self.refresh_min_s = float(cfg.get("refresh_min_s", 0.5))
        self.arrive_m = float(cfg.get("arrive_m", 15.0))
        self.timeout_s = float(cfg.get("timeout_s", 120.0))
        self.aim_sign = int(cfg.get("aim_sign", 1))
        self.sound_start = cfg.get("sound_start", "detecting")
        self.sound_stop = cfg.get("sound_stop", "waking")

        # Active-navigation state.
        self.active = False
        self.target_name = None      # display name of the destination
        self.target = None           # {"lat":..,"lon":..}
        self.last_aim = 0.0          # last head angle sent (for the web UI)
        self.distance_m = None       # latest computed distance (web UI)
        self.bearing = None          # latest computed bearing (web UI)
        self._last_aim_sent = 0.0    # rate-limit clock
        # For the self-timeout. 0.0 (not wall-clock) so that the FIRST
        # on_telemetry after a start() establishes the baseline instead of
        # immediately comparing against a stale "now" (which would wrongly
        # look like a huge gap). 0.0 also means "not yet seen".
        self._last_telemetry = 0.0

    # ------------------------------------------------------------------
    # Query helpers (used by the web UI)
    # ------------------------------------------------------------------
    def status(self) -> dict:
        """A JSON-able snapshot of the current navigation state."""
        return {
            "active": self.active,
            "target": self.target_name,
            "bearing": self.bearing,
            "distance_m": self.distance_m,
            "aim": self.last_aim,
        }

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def start(self, name: str) -> bool:
        """Begin navigating toward a named location.

        Returns True if navigation started, False if the place is unknown (the
        owl can then say it doesn't know it). Starting a new navigation first
        stops any current one.
        """
        if not self.enabled:
            logger.info("Navigation: disabled, ignoring start(%r)", name)
            return False
        loc = self.locations.get(name)
        if not loc:
            logger.info("Navigation: no saved place named %r", name)
            return False
        # Replace any in-progress navigation.
        if self.active:
            self._send_stop()
        self.target_name = loc["name"]
        self.target = {"lat": loc["lat"], "lon": loc["lon"]}
        self.active = True
        self._last_aim_sent = 0.0  # force an immediate first aim
        self._last_telemetry = 0.0  # first on_telemetry establishes the clock
        self._cue(self.sound_start)
        logger.info("Navigation: started -> %s (%.5f, %.5f)",
                     self.target_name, loc["lat"], loc["lon"])
        # If we already have a fresh fix, aim right away (don't wait for the
        # next telemetry frame).
        self._maybe_aim_from(self.supervisor.last)
        return True

    def stop(self, reason: str = "command") -> bool:
        """Stop navigating (head recenters, owl returns to normal)."""
        if not self.active:
            return False
        was = self.target_name
        self.active = False
        self.target_name = None
        self.target = None
        self._send_stop()
        self._cue(self.sound_stop)
        logger.info("Navigation: stopped (%s) [was heading to %s]", reason, was)
        return True

    def is_active(self) -> bool:
        return self.active

    # ------------------------------------------------------------------
    # Telemetry-driven aiming
    # ------------------------------------------------------------------
    def on_telemetry(self, t) -> None:
        """Called by the supervisor for every telemetry frame.

        While navigating, re-aim from the freshest GPS + heading (rate-limited)
        and handle arrival / self-timeout. Does nothing when not navigating.
        """
        if not self.active or t is None:
            return
        # Self-timeout: measure the gap since the PREVIOUS frame (before we
        # refresh _last_telemetry) so a stale frame is detected correctly. No
        # frames for too long (link dropped) -> stop so the head is not left
        # stuck. (The firmware times out independently.)
        if self._last_telemetry > 0.0 and t.timestamp - self._last_telemetry > self.timeout_s:
            self.stop("stale telemetry")
            return
        self._last_telemetry = t.timestamp

        self._maybe_aim_from(t)

    def _maybe_aim_from(self, t) -> None:
        """Compute the aim from one telemetry frame and (if due) send it."""
        if t is None or self.target is None:
            return
        gps = getattr(t, "gps", None)
        imu = getattr(t, "imu", None)
        if gps is None or not gps.valid:
            # No fix: keep the last aim, don't flail. (Web UI can show "no fix".)
            return
        if imu is None or not getattr(imu, "calibrated", False):
            # Uncalibrated heading: the aim would be wrong; hold last aim.
            return

        bearing = geo.bearing_deg(gps.latitude, gps.longitude,
                                   self.target["lat"], self.target["lon"])
        distance = geo.distance_m(gps.latitude, gps.longitude,
                                  self.target["lat"], self.target["lon"])
        self.bearing = round(bearing, 1)
        self.distance_m = round(distance, 1)

        # Arrival: close enough -> done.
        if distance < self.arrive_m:
            logger.info("Navigation: arrived at %s (%.0f m)", self.target_name, distance)
            self.stop("arrived")
            return

        aim = geo.aim_angle(bearing, imu.yaw, self.aim_sign,
                            self.head_min, self.head_max)
        self.last_aim = round(aim, 1)

        now = t.timestamp
        if now - self._last_aim_sent >= self.refresh_min_s:
            self._last_aim_sent = now
            self.serial.nav(angle=aim, active=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _send_stop(self) -> None:
        if self.serial is not None:
            self.serial.nav(angle=0.0, active=False)

    def _cue(self, sound) -> None:
        if sound and self.audio is not None:
            self.audio.play(sound)
