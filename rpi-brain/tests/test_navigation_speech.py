"""
Speech navigation tests: the owl starts / stops "guide me home" from a spoken
phrase, and a nav sentence is not stolen by a single question word.

Exercised without hardware: FakeSupervisor + FakeSerial + a temp locations store. The
real Speech.feed() pipeline runs; we assert on the supervisor's navigation
state and the serial command log.
"""

import os
import sys
import tempfile
import unittest

from stubs import install_stub_modules, FakeSerial, FakeAudio, FakeSupervisor, make_config

install_stub_modules()

from brain.speech import Speech  # noqa: E402
from brain.locations import LocationsStore  # noqa: E402
from brain.navigation import Navigation  # noqa: E402
from brain.serial_handler import (Telemetry, FaceDetection, VibrationData,  # noqa: E402
                                  IMUData, GPSData, UpdateMode)


def make_tel(state, face, t, gps_valid, lat, lon, yaw, calibrated):
    """A real telemetry frame (the navigation controller reads gps/imu/timestamp).

    A distinct, non-zero timestamp is required: Navigation.start() aims
    immediately, but the rate-limit gate (now - last_aim_sent >= refresh) only
    lets the first aim through when the frame's timestamp is > 0 (a 0.0
    timestamp reads as "no fix yet" and skips the immediate aim).
    """
    return Telemetry(
        timestamp=t, state=state, firmware="test",
        face=FaceDetection(detected=face),
        vibration=VibrationData(detected=False),
        imu=IMUData(yaw=yaw, calibrated=calibrated),
        gps=GPSData(valid=gps_valid, latitude=lat, longitude=lon,
                     satellites=8 if gps_valid else 0),
        update=UpdateMode(active=False), servos=[0.0] * 5,
    )


class TestSpeechNavigate(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="owl-speech-nav-speech-")

    def _speech(self, state="interacting", face=True, places=(), cfg_over=None):
        serial = FakeSerial()
        audio = FakeAudio()
        sup = FakeSupervisor(state=state, face_detected=face, audio=audio)
        cfg = make_config()
        if cfg_over:
            cfg["speech"].update(cfg_over)
        path = os.path.join(self._dir, "locations.json")
        cfg["navigation"]["locations_file"] = path
        # A real frame (fresh fix, calibrated heading) so start() aims at once.
        sup.last = make_tel(state, face, 100.0, True, 48.0, 11.0, 0.0, True)
        sup.locations = LocationsStore(path)
        for (name, lat, lon) in places:
            sup.locations.add(name, lat, lon)
        sup.navigation = Navigation(serial, sup, sup.locations, cfg)
        sup.nav_start = lambda name: sup.navigation.start(name)
        sup.nav_stop = lambda reason="command": sup.navigation.stop(reason)
        return Speech(serial, sup, cfg), serial, sup

    # ------------------------------------------------------------------
    # START navigation from a spoken trigger
    # ------------------------------------------------------------------
    def test_nav_trigger_starts_navigation(self):
        speech, serial, sup = self._speech(places=[("home", 49.0, 11.0)])
        speech.feed("Bring mich nach home")
        self.assertTrue(sup.navigation.is_active())
        self.assertEqual(sup.navigation.target_name, "home")
        # An active nav command was sent to the (fake) serial.
        self.assertTrue(any(k == "nav" and c[1] is True for k, c in serial.commands))

    def test_nav_trigger_fuzzy_name(self):
        # "hote" is a prefix of the saved "hotel" -> fuzzy match starts it.
        speech, serial, sup = self._speech(places=[("hotel", 48.2, 11.3)])
        speech.feed("Bring mich nach hote")
        self.assertTrue(sup.navigation.is_active())
        self.assertEqual(sup.navigation.target_name, "hotel")

    def test_nav_trigger_unknown_place_no_start(self):
        speech, serial, sup = self._speech(places=[("home", 49.0, 11.0)])
        speech.feed("Bring mich nach nirgendwo")
        self.assertFalse(sup.navigation.is_active())
        self.assertTrue(any(k == "nav" for k, c in serial.commands) is False)

    def test_nav_trigger_beats_question_cluster(self):
        # "wie" is a question keyword, but "wie komme ich zu ..." is a nav
        # trigger and must win (nav is checked before the clusters).
        speech, serial, sup = self._speech(places=[("zoo", 48.4, 11.4)])
        speech.feed("Wie komme ich zu zoo?")
        self.assertTrue(sup.navigation.is_active())
        self.assertEqual(sup.navigation.target_name, "zoo")
        # And no question reaction (surprised) was fired.
        self.assertEqual([c for k, c in serial.commands if k == "expression"], [])

    def test_plain_question_still_reacts(self):
        # A plain question (no nav trigger) still fires the question reaction.
        speech, serial, sup = self._speech(places=[("home", 49.0, 11.0)])
        speech.feed("Wie geht's?")
        self.assertFalse(sup.navigation.is_active())
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["surprised"])

    # ------------------------------------------------------------------
    # STOP navigation from a spoken keyword
    # ------------------------------------------------------------------
    def test_stop_keyword_stops_navigation(self):
        speech, serial, sup = self._speech(places=[("home", 49.0, 11.0)])
        # Start it (bypass cooldown by seeding last_reaction in the past).
        speech._last_reaction = 0.0
        speech.feed("Bring mich nach home")
        self.assertTrue(sup.navigation.is_active())
        # Now stop it (cooldown would block, but the stop path bypasses it).
        speech.feed("Stopp die navigation!")
        self.assertFalse(sup.navigation.is_active())
        self.assertTrue(any(k == "nav" and c[1] is False for k, c in serial.commands))

    def test_stop_keyword_overrides_face_gate(self):
        # Start with a face in frame, then the face leaves; the stop keyword
        # must still end navigation (it is exempt from the face-gate).
        speech, serial, sup = self._speech(face=True, places=[("home", 49.0, 11.0)])
        speech._last_reaction = 0.0
        speech.feed("Bring mich nach home")
        self.assertTrue(sup.navigation.is_active())
        # The face leaves frame before the user says stop.
        sup.last.face.detected = False
        speech.feed("Danke")   # a stop keyword; no face required
        self.assertFalse(sup.navigation.is_active())

    def test_stop_keyword_while_asleep(self):
        # A stop keyword heard while the owl is asleep still ends navigation
        # (and does not wake the owl).
        speech, serial, sup = self._speech(state="sleeping", places=[("home", 49.0, 11.0)])
        # Start it while "awake" first, then put the owl to sleep.
        sup.last_state = "interacting"
        speech._last_reaction = 0.0
        speech.feed("Bring mich nach home")
        sup.last_state = "sleeping"
        # An unrelated ambient word (not a wake keyword, not a stop keyword)
        # while asleep -> ignored, the owl is not woken.
        speech.feed("Hallo")
        self.assertTrue(sup.navigation.is_active())  # still navigating
        speech.feed("Stopp die navigation")
        self.assertFalse(sup.navigation.is_active())
        # And no wake command was sent (stop is not a wake keyword).
        self.assertEqual([k for k, c in serial.commands if k == "wake"], [])

    # ------------------------------------------------------------------
    # A normal reaction does not disturb an active navigation
    # ------------------------------------------------------------------
    def test_happy_reaction_does_not_cancel_navigation(self):
        speech, serial, sup = self._speech(places=[("home", 49.0, 11.0)])
        speech._last_reaction = 0.0
        speech.feed("Bring mich nach home")
        self.assertTrue(sup.navigation.is_active())
        # A happy phrase (not a stop keyword) reacts but leaves navigation on.
        speech._last_reaction = 0.0
        speech.feed("Das ist toll!")
        self.assertTrue(sup.navigation.is_active())
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
