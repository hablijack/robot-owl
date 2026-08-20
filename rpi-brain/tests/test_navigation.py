"""
Navigation controller tests (real brain.Navigation, faked serial + supervisor).

The controller is the RPi-side brain of the "guide me home" feature: given the
live GPS fix + IMU heading (from a telemetry frame) and a destination from the
locations store, it computes the head aim and streams it to the ESP32. Here the
serial port is a FakeSerial (records commands) and the supervisor is a tiny
stub (provides .last / .audio), so the logic under test is the real controller.

GPS geometry used throughout (see test_navigation_geo.py for the math):
  * destination 1 deg north of the owl (same lon)  -> bearing 0  (due north)
  * destination 1 deg east  of the owl (same lat)  -> bearing ~90 (due east)
  * destination 1 deg south of the owl (same lon)  -> bearing 180 (due south)
  * ~1 deg of latitude  ~ 111 km (far outside the 15 m arrival radius)
"""

import sys
import types
import unittest

from stubs import install_stub_modules, FakeAudio, FakeSerial, make_config

install_stub_modules()

from brain.locations import LocationsStore  # noqa: E402
from brain.navigation import Navigation  # noqa: E402
from brain.serial_handler import (Telemetry, FaceDetection, VibrationData,  # noqa: E402
                                  IMUData, GPSData, UpdateMode)


def make_tel(state, face, vibration, t, gps_valid, lat, lon, yaw, calibrated):
    """Build a real Telemetry frame (the controller reads gps/imu/timestamp)."""
    return Telemetry(
        timestamp=t,
        state=state,
        firmware="test",
        face=FaceDetection(detected=face),
        vibration=VibrationData(detected=vibration),
        imu=IMUData(yaw=yaw, calibrated=calibrated),
        gps=GPSData(valid=gps_valid, latitude=lat, longitude=lon,
                     satellites=8 if gps_valid else 0),
        update=UpdateMode(active=False),
        servos=[0.0] * 5,
    )


class StubSupervisor:
    """Just enough of the Supervisor for the Navigation controller."""
    def __init__(self, serial, audio=None):
        self.serial = serial
        self.audio = audio if audio is not None else FakeAudio()
        self.last = None
        self.last_state = "interacting"


def make_nav(serial, sup, cfg_overrides=None, locations_file=None):
    cfg = make_config()
    if cfg_overrides:
        cfg["navigation"].update(cfg_overrides)
    if locations_file is not None:
        cfg["navigation"]["locations_file"] = locations_file
    store = LocationsStore(cfg["navigation"]["locations_file"] or None)
    return Navigation(serial, sup, store, cfg), store


class TestNavigationStartStop(unittest.TestCase):
    """start()/stop() bookkeeping and the commands they send."""

    def test_start_unknown_place_is_noop(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, _ = make_nav(serial, sup)
        self.assertFalse(nav.start("nowhere"))
        self.assertFalse(nav.is_active())
        self.assertEqual(serial.commands, [])

    def test_start_then_active(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup)
        store.add("home", 49.0, 11.0)
        self.assertTrue(nav.start("home"))
        self.assertTrue(nav.is_active())
        self.assertEqual(nav.target_name, "home")

    def test_start_sends_nav_active_true(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup)
        store.add("home", 49.0, 11.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("home")
        # start() aims immediately from the fresh fix -> an active nav command.
        self.assertTrue(any(k == "nav" and c[1] is True for k, c in serial.commands))

    def test_stop_sends_nav_active_false(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup)
        store.add("home", 49.0, 11.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("home")
        serial.commands.clear()
        self.assertTrue(nav.stop("test"))
        self.assertFalse(nav.is_active())
        self.assertEqual(serial.commands, [("nav", (0.0, False))])

    def test_stop_when_not_active_is_noop(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, _ = make_nav(serial, sup)
        self.assertFalse(nav.stop("test"))
        self.assertEqual(serial.commands, [])

    def test_start_replaces_previous(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup)
        store.add("home", 49.0, 11.0)
        store.add("zoo", 48.0, 12.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("home")
        serial.commands.clear()
        nav.start("zoo")   # starting a new place stops the old one first
        self.assertEqual(nav.target_name, "zoo")
        # First command after the switch is the stop (active False), then the aim.
        self.assertEqual(serial.commands[0], ("nav", (0.0, False)))
        self.assertTrue(nav.is_active())

    def test_disabled_start_is_noop(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup, cfg_overrides={"enabled": False})
        store.add("home", 49.0, 11.0)
        self.assertFalse(nav.start("home"))
        self.assertFalse(nav.is_active())
        self.assertEqual(serial.commands, [])

    def test_start_cues_start_sound(self):
        serial = FakeSerial()
        audio = FakeAudio()
        sup = StubSupervisor(serial, audio=audio)
        nav, store = make_nav(serial, sup)
        store.add("home", 49.0, 11.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("home")
        self.assertIn("detecting", audio.played)   # config's sound_start


class TestNavigationAiming(unittest.TestCase):
    """on_telemetry() computes the right head aim from GPS + heading."""

    def test_points_north_when_facing_north(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup, cfg_overrides={"refresh_min_s": 0.0})
        store.add("north", 49.0, 11.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("north")
        # Facing north (yaw 0), destination due north -> aim 0.
        self.assertAlmostEqual(nav.last_aim, 0.0, places=1)

    def test_points_east_when_facing_north(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup, cfg_overrides={"refresh_min_s": 0.0})
        store.add("east", 48.0, 12.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("east")
        # Facing north, destination due east -> aim +90, clamped to head_max 45.
        self.assertEqual(nav.last_aim, 45.0)

    def test_points_south_when_facing_north(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup, cfg_overrides={"refresh_min_s": 0.0})
        store.add("south", 47.0, 11.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("south")
        # Destination straight behind (180) -> pinned to -head_max.
        self.assertEqual(nav.last_aim, -45.0)

    def test_aim_follows_heading(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup, cfg_overrides={"refresh_min_s": 0.0})
        store.add("east", 48.0, 12.0)
        # Facing east (yaw 90), destination ~due east (bearing ~89.6) -> aim ~0
        # (straight ahead). Not exactly 0 because the "east" bearing is 89.6.
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 90.0, True)
        nav.start("east")
        self.assertAlmostEqual(nav.last_aim, 0.0, delta=1.0)

    def test_aim_sign_config_flips(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup, cfg_overrides={
            "refresh_min_s": 0.0, "aim_sign": -1,
            "head_min": -180, "head_max": 180})
        store.add("east", 48.0, 12.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("east")
        # sign -1 flips the aim: +89.6 becomes -89.6 (i.e. ~-90).
        self.assertAlmostEqual(nav.last_aim, -90.0, delta=1.0)

    def test_no_fix_keeps_last_aim(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup, cfg_overrides={"refresh_min_s": 0.0})
        store.add("north", 49.0, 11.0)
        # First a good fix (establishes an aim), then a frame with no fix.
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("north")
        first = nav.last_aim
        serial.commands.clear()
        sup.last = make_tel("interacting", True, False, 100.5, False, 48.0, 11.0, 0.0, True)
        nav.on_telemetry(sup.last)
        self.assertEqual(nav.last_aim, first)          # unchanged
        self.assertEqual(serial.commands, [])           # no new aim sent

    def test_uncalibrated_imu_keeps_last_aim(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup, cfg_overrides={"refresh_min_s": 0.0})
        store.add("north", 49.0, 11.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("north")
        first = nav.last_aim
        serial.commands.clear()
        sup.last = make_tel("interacting", True, False, 100.5, True, 48.0, 11.0, 30.0, False)
        nav.on_telemetry(sup.last)
        self.assertEqual(nav.last_aim, first)
        self.assertEqual(serial.commands, [])

    def test_on_telemetry_noop_when_not_active(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup)
        store.add("north", 49.0, 11.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.on_telemetry(sup.last)   # not active -> does nothing
        self.assertEqual(serial.commands, [])
        self.assertFalse(nav.is_active())

    def test_rate_limit(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup, cfg_overrides={"refresh_min_s": 1.0})
        store.add("north", 49.0, 11.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("north")
        n_after_start = len(serial.commands)
        # Two more frames 0.3 s apart: within the 1.0 s refresh window -> no new send.
        sup.last = make_tel("interacting", True, False, 100.3, True, 48.0, 11.0, 1.0, True)
        nav.on_telemetry(sup.last)
        sup.last = make_tel("interacting", True, False, 100.6, True, 48.0, 11.0, 2.0, True)
        nav.on_telemetry(sup.last)
        self.assertEqual(len(serial.commands), n_after_start)
        # A frame 1.2 s later (past the window) sends again.
        sup.last = make_tel("interacting", True, False, 101.2, True, 48.0, 11.0, 3.0, True)
        nav.on_telemetry(sup.last)
        self.assertEqual(len(serial.commands), n_after_start + 1)


class TestNavigationExit(unittest.TestCase):
    """The exit paths: arrival, self-timeout, and the status snapshot."""

    def test_arrival_stops_navigation(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup,
                              cfg_overrides={"refresh_min_s": 0.0, "arrive_m": 15.0})
        store.add("home", 48.0001, 11.0)   # ~11 m north -> inside 15 m
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("home")
        # Already close: the first aim detects arrival and stops.
        self.assertFalse(nav.is_active())
        self.assertTrue(any(k == "nav" and c[1] is False for k, c in serial.commands))

    def test_not_arrived_when_far(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup,
                              cfg_overrides={"refresh_min_s": 0.0, "arrive_m": 15.0})
        store.add("home", 49.0, 11.0)   # ~111 km away
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("home")
        self.assertTrue(nav.is_active())   # still navigating (not arrived)

    def test_self_timeout_stops_navigation(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup,
                              cfg_overrides={"refresh_min_s": 0.0, "timeout_s": 5.0})
        store.add("north", 49.0, 11.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("north")
        self.assertTrue(nav.is_active())
        # A fresh 101.0 s frame re-establishes the "last seen" baseline.
        sup.last = make_tel("interacting", True, False, 101.0, True, 48.0, 11.0, 0.0, True)
        nav.on_telemetry(sup.last)
        self.assertTrue(nav.is_active())
        # Then a frame 6 s later (107.0) is more than timeout_s (5 s) after the
        # last-seen (101.0): the controller drops the navigation.
        sup.last = make_tel("interacting", True, False, 107.0, True, 48.0, 11.0, 0.0, True)
        nav.on_telemetry(sup.last)
        self.assertFalse(nav.is_active())
        self.assertTrue(any(k == "nav" and c[1] is False for k, c in serial.commands))

    def test_status_reports_state(self):
        serial = FakeSerial()
        sup = StubSupervisor(serial)
        nav, store = make_nav(serial, sup, cfg_overrides={"refresh_min_s": 0.0})
        store.add("north", 49.0, 11.0)
        sup.last = make_tel("interacting", True, False, 100.0, True, 48.0, 11.0, 0.0, True)
        nav.start("north")
        st = nav.status()
        self.assertTrue(st["active"])
        self.assertEqual(st["target"], "north")
        self.assertIsNotNone(st["bearing"])
        self.assertIsNotNone(st["distance_m"])
        self.assertIn("aim", st)


if __name__ == "__main__":
    unittest.main(verbosity=2)
