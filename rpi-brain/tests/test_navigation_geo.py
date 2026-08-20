"""
Navigation tests (pure RPi side): geodesy + the named-locations store.

Both modules are dependency-free, so these run on a plain dev machine:
  * brain.geo      - bearing / haversine / angle-wrap / aim math (pure functions)
  * brain.locations - the name -> {lat, lon} store + JSON persistence

These are the building blocks the Navigation controller (test_navigation.py)
and the web UI (test_navigation_webui.py) build on.
"""

import os
import sys
import tempfile
import unittest

from stubs import install_stub_modules

install_stub_modules()

import brain.geo as geo  # noqa: E402
from brain.locations import LocationsStore  # noqa: E402


class TestWrap(unittest.TestCase):
    """Angle normalization into (-180, 180] and [0, 360)."""

    def test_wrap_180_range(self):
        self.assertEqual(geo.wrap_180(0), 0)
        self.assertEqual(geo.wrap_180(90), 90)
        self.assertEqual(geo.wrap_180(-90), -90)
        self.assertEqual(geo.wrap_180(180), -180)   # 180 -> -180 (half-open)
        self.assertEqual(geo.wrap_180(190), -170)
        self.assertEqual(geo.wrap_180(-190), 170)
        self.assertEqual(geo.wrap_180(360), 0)

    def test_wrap_360_range(self):
        self.assertEqual(geo.wrap_360(0), 0)
        self.assertEqual(geo.wrap_360(360), 0)
        self.assertEqual(geo.wrap_360(-10), 350)
        self.assertEqual(geo.wrap_360(370), 10)


class TestBearing(unittest.TestCase):
    """Compass bearing from one point to another (initial bearing)."""

    def test_cardinal_directions(self):
        # Due north (same longitude, higher latitude): exactly 0.
        self.assertAlmostEqual(geo.bearing_deg(48.0, 11.0, 49.0, 11.0), 0.0, places=1)
        # Due south: exactly 180.
        self.assertAlmostEqual(geo.bearing_deg(49.0, 11.0, 48.0, 11.0), 180.0, places=1)
        # "Due east" (same latitude, higher longitude) is NOT exactly 90 on a
        # sphere: a point of the same latitude at a higher longitude sits slightly
        # south of the great-circle initial bearing, so the bearing is ~89.6.
        # It is still within 1 degree of due east.
        self.assertAlmostEqual(geo.bearing_deg(48.0, 11.0, 48.0, 12.0), 90.0, delta=1.0)
        # Likewise "due west" is within 1 degree of 270.
        self.assertAlmostEqual(geo.bearing_deg(48.0, 11.0, 48.0, 10.0), 270.0, delta=1.0)

    def test_bearing_is_wrapped_0_360(self):
        for a in range(-720, 721, 45):
            self.assertTrue(0.0 <= geo.wrap_360(a) < 360.0)
        # A bearing is always in [0, 360) even for arbitrary inputs.
        self.assertTrue(0.0 <= geo.bearing_deg(0, 0, -10, -10) < 360.0)

    def test_bearing_symmetry_180(self):
        # The bearing A->B and B->A differ by ~180 degrees (opposite directions).
        # (Not exactly 180 on a sphere -- the return bearing is a few tenths of a
        # degree off -- so allow a 1 degree tolerance.)
        ab = geo.bearing_deg(48.0, 11.0, 48.5, 11.5)
        ba = geo.bearing_deg(48.5, 11.5, 48.0, 11.0)
        self.assertAlmostEqual((ab + 180.0) % 360.0, ba, delta=1.0)


class TestDistance(unittest.TestCase):
    """Haversine distance in meters."""

    def test_zero_distance(self):
        self.assertAlmostEqual(geo.distance_m(48.0, 11.0, 48.0, 11.0), 0.0, places=6)

    def test_one_degree_latitude_is_about_111km(self):
        d = geo.distance_m(48.0, 11.0, 49.0, 11.0)
        self.assertAlmostEqual(d, 111195.0, delta=2000.0)   # ~111 km

    def test_distance_is_monotonic(self):
        d_close = geo.distance_m(48.0, 11.0, 48.01, 11.0)
        d_far = geo.distance_m(48.0, 11.0, 49.0, 11.0)
        self.assertLess(d_close, d_far)

    def test_distance_symmetric(self):
        self.assertAlmostEqual(geo.distance_m(48.0, 11.0, 48.5, 11.5),
                               geo.distance_m(48.5, 11.5, 48.0, 11.0), places=6)


class TestAimAngle(unittest.TestCase):
    """Map (bearing, heading) -> a head-servo angle, clamped to the head range."""

    def test_straight_ahead_is_zero(self):
        self.assertEqual(geo.aim_angle(0.0, 0.0), 0.0)

    def test_offset_equals_bearing_minus_heading(self):
        # aim_angle's default head range is +-45 (the real head), which would
        # clamp a +-90 offset. Use the full +-180 range here to assert the raw
        # offset: facing north (0), destination east (90) -> +90 (right);
        # facing east (90), destination north (0) -> -90 (left).
        self.assertAlmostEqual(geo.aim_angle(90.0, 0.0, head_min=-180.0, head_max=180.0), 90.0, places=1)
        self.assertAlmostEqual(geo.aim_angle(0.0, 90.0, head_min=-180.0, head_max=180.0), -90.0, places=1)

    def test_wraps_around_180(self):
        # Facing north (0), destination just past south (190): that's -170 (left).
        self.assertAlmostEqual(geo.aim_angle(190.0, 0.0, head_min=-180.0, head_max=180.0), -170.0, places=1)

    def test_clamps_to_head_range(self):
        # A destination 90 deg to the side can't be aimed at by a +-45 head:
        # it pins to the limit.
        self.assertEqual(geo.aim_angle(90.0, 0.0, head_max=45.0, head_min=-45.0), 45.0)
        self.assertEqual(geo.aim_angle(-90.0, 0.0, head_max=45.0, head_min=-45.0), -45.0)

    def test_behind_pins_to_limit(self):
        # Destination straight behind (180 vs heading 0): pins to -45 (a limit).
        self.assertEqual(geo.aim_angle(180.0, 0.0, head_max=45.0, head_min=-45.0), -45.0)

    def test_aim_sign_flips_direction(self):
        # The one hardware-unknown: if the convention is reversed, flip the sign.
        a = geo.aim_angle(90.0, 0.0, sign=1, head_max=180.0, head_min=-180.0)
        b = geo.aim_angle(90.0, 0.0, sign=-1, head_max=180.0, head_min=-180.0)
        self.assertAlmostEqual(a, 90.0, places=1)
        self.assertAlmostEqual(b, -90.0, places=1)


class TestLocationsStore(unittest.TestCase):
    """The named-locations store + its JSON persistence."""

    def setUp(self):
        # Each test gets its own temp file so tests can't see each other's data.
        self._dir = tempfile.mkdtemp(prefix="owl-locations-")
        self.path = os.path.join(self._dir, "locations.json")

    def test_add_and_get(self):
        store = LocationsStore(self.path)
        self.assertTrue(store.add("home", 48.0, 11.0))
        loc = store.get("home")
        self.assertIsNotNone(loc)
        self.assertEqual(loc["name"], "home")
        self.assertAlmostEqual(loc["lat"], 48.0)
        self.assertAlmostEqual(loc["lon"], 11.0)

    def test_get_is_case_insensitive(self):
        store = LocationsStore(self.path)
        store.add("Hotel", 48.0, 11.0)
        self.assertIsNotNone(store.get("hotel"))
        self.assertIsNotNone(store.get(" HOTEL "))

    def test_get_unknown_is_none(self):
        store = LocationsStore(self.path)
        self.assertIsNone(store.get("nowhere"))

    def test_reject_bad_coordinates(self):
        store = LocationsStore(self.path)
        self.assertFalse(store.add("home", 200.0, 11.0))   # lat out of range
        self.assertFalse(store.add("home", 48.0, 200.0))   # lon out of range
        self.assertFalse(store.add("   ", 48.0, 11.0))     # empty name
        self.assertEqual(len(store), 0)

    def test_add_updates_existing(self):
        store = LocationsStore(self.path)
        store.add("home", 48.0, 11.0)
        store.add("home", 49.0, 12.0)
        self.assertEqual(len(store), 1)
        self.assertAlmostEqual(store.get("home")["lat"], 49.0)

    def test_remove(self):
        store = LocationsStore(self.path)
        store.add("home", 48.0, 11.0)
        self.assertTrue(store.remove("HOME"))
        self.assertIsNone(store.get("home"))
        self.assertFalse(store.remove("home"))   # already gone

    def test_names_sorted_and_contains(self):
        store = LocationsStore(self.path)
        store.add("zoo", 48.0, 11.0)
        store.add("alpha", 48.1, 11.1)
        self.assertEqual(store.names(), ["alpha", "zoo"])
        self.assertIn("alpha", store)
        self.assertIn("ZOO", store)   # __contains__ normalizes
        self.assertNotIn("beta", store)

    def test_all_returns_display_names(self):
        store = LocationsStore(self.path)
        store.add("  Hotel  ", 48.0, 11.0)
        items = store.all()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "Hotel")   # display name, trimmed

    def test_persistence_roundtrip(self):
        store = LocationsStore(self.path)
        store.add("home", 48.0, 11.0)
        store.add("zoo", 49.0, 12.0)
        # A fresh store pointed at the same file sees the same data.
        again = LocationsStore(self.path)
        self.assertEqual(again.names(), ["home", "zoo"])
        self.assertAlmostEqual(again.get("zoo")["lon"], 12.0)

    def test_load_tolerates_corrupt_file(self):
        with open(self.path, "w") as f:
            f.write("{ this is not valid json ]")
        store = LocationsStore(self.path)   # must not raise
        self.assertEqual(len(store), 0)

    def test_load_ignores_malformed_entries(self):
        with open(self.path, "w") as f:
            f.write('{"home": {"lat": 48.0}, "zoo": {"lat": 49.0, "lon": 12.0}}')
        store = LocationsStore(self.path)
        # "home" has no lon -> skipped; "zoo" is valid -> kept.
        self.assertEqual(store.names(), ["zoo"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
