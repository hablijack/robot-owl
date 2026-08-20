"""
Web UI navigation-endpoint tests (real brain.WebUI, faked Flask via stubs).

The fake Flask (tests/stubs.py) records each @app.route view in
app.view_functions keyed by the view's function name, and request.get_json
returns {} -- so these tests call the view functions directly with a stubbed
request body (supervisor.request patched) and assert on the returned dict.

The serial + supervisor are fakes (FakeSerial / a stub supervisor), so no
hardware is needed. The locations store is pointed at a temp file.
"""

import os
import sys
import tempfile
import types
import unittest

from stubs import install_stub_modules, FakeSerial, FakeAudio, make_config

install_stub_modules()

import brain.web_ui as webui_mod  # noqa: E402
from brain.web_ui import WebUI  # noqa: E402

# The Flask `request` the views call is the module-level object imported into
# brain.web_ui (NOT an attribute of the supervisor). The fake's get_json returns
# {}, so to exercise a POST endpoint we patch webui_mod.request.get_json to
# return the test's body for the duration of the call.
_orig_get_json = webui_mod.request.get_json


class StubSupervisor:
    """Just enough of the Supervisor for the WebUI (last, audio, nav)."""
    def __init__(self, serial, audio=None):
        self.serial = serial
        self.audio = audio if audio is not None else FakeAudio()
        self.last = None
        self.last_state = "interacting"

    def play_sound(self, s):
        if self.audio:
            self.audio.play(s)
        return True

    def sleep(self):
        self.serial.commands.append(("sleep", "sleep"))
        return True

    def wake(self):
        self.serial.commands.append(("wake", "wake"))
        return True

    def nav_start(self, name):
        if self.navigation is None:
            return False
        return self.navigation.start(name)

    def nav_stop(self, reason="command"):
        if self.navigation is None:
            return False
        return self.navigation.stop(reason)


def make_webui(nav_enabled=True, locations_file=None):
    serial = FakeSerial()
    sup = StubSupervisor(serial)
    cfg = make_config()
    cfg["navigation"]["enabled"] = nav_enabled
    if locations_file is not None:
        cfg["navigation"]["locations_file"] = locations_file
    # Attach the real locations store + navigation controller to the supervisor,
    # exactly as brain.supervisor.Supervisor does.
    from brain.locations import LocationsStore
    from brain.navigation import Navigation
    sup.locations = LocationsStore(cfg["navigation"]["locations_file"] or None)
    sup.navigation = Navigation(serial, sup, sup.locations, cfg)
    ui = WebUI(serial, sup, host="127.0.0.1", port=0)
    return ui, serial, sup


class TestWebUINavEndpoints(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="owl-webui-nav-")
        self.path = os.path.join(self._dir, "locations.json")

    def _call(self, ui, sup, view_name, body):
        # Point the module-level Flask request at this test's body for the call.
        webui_mod.request.get_json = lambda **k: body
        try:
            return ui.app.view_functions[view_name]()
        finally:
            webui_mod.request.get_json = _orig_get_json

    # ------------------------------------------------------------------
    # /api/locations
    # ------------------------------------------------------------------
    def test_locations_list_empty_then_add(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        # Empty at first.
        d = self._call(ui, sup, "api_locations", {})
        self.assertTrue(d["ok"])
        self.assertEqual(d["locations"], [])
        # Add a place.
        d = self._call(ui, sup, "api_locations_add", {"name": "home", "lat": 48.0, "lon": 11.0})
        self.assertTrue(d["ok"])
        d = self._call(ui, sup, "api_locations", {})
        self.assertEqual(len(d["locations"]), 1)
        self.assertEqual(d["locations"][0]["name"], "home")

    def test_locations_add_requires_fields(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        self.assertFalse(self._call(ui, sup, "api_locations_add", {"name": "x"})["ok"])
        self.assertFalse(self._call(ui, sup, "api_locations_add", {"lat": 48.0, "lon": 11.0})["ok"])
        self.assertFalse(self._call(ui, sup, "api_locations_add", {})["ok"])

    def test_locations_add_rejects_bad_coords(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        self.assertFalse(self._call(ui, sup, "api_locations_add",
                                     {"name": "x", "lat": 999.0, "lon": 11.0})["ok"])

    def test_locations_delete(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        self._call(ui, sup, "api_locations_add", {"name": "home", "lat": 48.0, "lon": 11.0})
        self.assertTrue(self._call(ui, sup, "api_locations_delete", {"name": "home"})["ok"])
        self.assertEqual(self._call(ui, sup, "api_locations", {})["locations"], [])

    def test_locations_disabled(self):
        ui, serial, sup = make_webui(nav_enabled=False, locations_file=self.path)
        d = self._call(ui, sup, "api_locations", {})
        self.assertFalse(d["ok"])
        self.assertIn("error", d)

    # ------------------------------------------------------------------
    # /api/nav/start + /api/nav/stop
    # ------------------------------------------------------------------
    def test_nav_start_unknown_place(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        d = self._call(ui, sup, "api_nav_start", {"name": "nowhere"})
        self.assertFalse(d["ok"])
        self.assertFalse(d["status"]["active"])

    def test_nav_start_known_place_activates(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        # A place to the north of the (48, 11) owl, and a fresh fix, so the
        # controller aims immediately and goes active.
        sup.locations.add("home", 49.0, 11.0)
        from brain.serial_handler import (Telemetry, FaceDetection, VibrationData,
                                          IMUData, GPSData, UpdateMode)
        sup.last = Telemetry(timestamp=100.0, state="interacting", firmware="test",
                             face=FaceDetection(detected=True),
                             vibration=VibrationData(detected=False),
                             imu=IMUData(yaw=0.0, calibrated=True),
                             gps=GPSData(valid=True, latitude=48.0, longitude=11.0, satellites=8),
                             update=UpdateMode(active=False), servos=[0.0] * 5)
        d = self._call(ui, sup, "api_nav_start", {"name": "home"})
        self.assertTrue(d["ok"])
        self.assertTrue(d["status"]["active"])
        self.assertEqual(d["status"]["target"], "home")

    def test_nav_stop(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        sup.locations.add("home", 49.0, 11.0)
        from brain.serial_handler import (Telemetry, FaceDetection, VibrationData,
                                          IMUData, GPSData, UpdateMode)
        sup.last = Telemetry(timestamp=100.0, state="interacting", firmware="test",
                             face=FaceDetection(detected=True),
                             vibration=VibrationData(detected=False),
                             imu=IMUData(yaw=0.0, calibrated=True),
                             gps=GPSData(valid=True, latitude=48.0, longitude=11.0, satellites=8),
                             update=UpdateMode(active=False), servos=[0.0] * 5)
        self._call(ui, sup, "api_nav_start", {"name": "home"})
        self.assertTrue(sup.navigation.is_active())
        d = self._call(ui, sup, "api_nav_stop", {})
        self.assertTrue(d["ok"])
        self.assertFalse(d["status"]["active"])

    def test_nav_disabled(self):
        ui, serial, sup = make_webui(nav_enabled=False, locations_file=self.path)
        self.assertFalse(self._call(ui, sup, "api_nav_start", {"name": "x"})["ok"])
        self.assertFalse(self._call(ui, sup, "api_nav_stop", {})["ok"])

    # ------------------------------------------------------------------
    # telemetry payload exposes the nav status
    # ------------------------------------------------------------------
    def test_telemetry_includes_navigation(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        from brain.serial_handler import (Telemetry, FaceDetection, VibrationData,
                                          IMUData, GPSData, UpdateMode)
        sup.last = Telemetry(timestamp=100.0, state="interacting", firmware="test",
                             face=FaceDetection(detected=True),
                             vibration=VibrationData(detected=False),
                             imu=IMUData(yaw=0.0, calibrated=True),
                             gps=GPSData(valid=True, latitude=48.0, longitude=11.0, satellites=8),
                             update=UpdateMode(active=False), servos=[0.0] * 5)
        d = ui.app.view_functions["api_telemetry"]()
        self.assertIn("navigation", d)
        self.assertFalse(d["navigation"]["active"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
