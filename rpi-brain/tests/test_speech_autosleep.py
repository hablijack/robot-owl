"""
Phase-4 tests: autonomous sleep on inactivity + wake on speech.

The owl falls asleep BY ITSELF when the environment goes quiet (no face, no
vibration, no speech) and wakes immediately on a real interaction. There is no
command / spoken word that FORCES it to sleep.

How it is exercised WITHOUT hardware:
  * The auto-sleep policy lives on the RPi supervisor. We drive the REAL
    brain.Supervisor with a FakeSerial + a scripted telemetry stream (via
    on_telemetry + check_auto_sleep), so the policy logic under test is the real
    code -- only the serial port and the clock are faked.
  * Wake-on-speech lives in brain.Speech.feed(). We call feed() directly with a
    canned transcript and a supervisor forced into the "sleeping" state, then
    assert on the serial command log (a "wake" command is sent iff the
    transcript is a wake keyword).

The firmware is unchanged: it already self-wakes on a face or a vibration. The
RPi's only new job is (a) deciding when the environment is quiet enough to send
the existing "sleep" command, and (b) sending the existing "wake" command when
it hears a wake keyword while the owl is asleep.
"""

import sys
import unittest

from stubs import install_stub_modules, FakeSerial, FakeAudio, make_config

install_stub_modules()

import brain.supervisor as supervisor_mod  # noqa: E402
from brain.supervisor import Supervisor  # noqa: E402
from brain.speech import Speech  # noqa: E402


def make_telemetry(state, face, vibration, t, gps_valid=False, lat=48.0, lon=11.0,
                   yaw=0.0, calibrated=False):
    """Build a minimal real Telemetry frame for the scripted stream.

    GPS/IMU are invalid/uncalibrated by default (matching the real sensor
    defaults), so navigation -- which needs a valid fix + calibrated heading --
    stays inert in these auto-sleep tests (it does not interfere with the
    sleep/wake assertions).
    """
    from brain.serial_handler import Telemetry, FaceDetection, VibrationData
    from brain.serial_handler import IMUData, GPSData, UpdateMode
    return Telemetry(
        timestamp=t,
        state=state,
        firmware="test",
        face=FaceDetection(detected=face),
        vibration=VibrationData(detected=vibration),
        imu=IMUData(yaw=yaw, calibrated=calibrated),
        gps=GPSData(valid=gps_valid, latitude=lat, longitude=lon),
        update=UpdateMode(active=False),
        servos=[0.0] * 5,
    )


class TestAutoSleepPolicy(unittest.TestCase):
    """The RPi supervisor puts the owl to sleep after sustained inactivity."""

    def _sup(self, serial, after_s=10.0, enabled=True):
        cfg = make_config()
        cfg["supervisor"]["auto_sleep"] = {"enabled": enabled, "after_s": after_s}
        return Supervisor(serial, cfg, audio=FakeAudio())

    def test_no_activity_sends_sleep(self):
        serial = FakeSerial()
        sup = self._sup(serial, after_s=10.0)
        # A quiet stream: awake, no face, no vibration.
        sup.on_telemetry(make_telemetry("idle", False, False, 100.0))
        sup.check_auto_sleep(now=100.0)            # baseline: clock starts now
        sup.check_auto_sleep(now=109.0)            # 9 s: not yet
        self.assertEqual([k for k, c in serial.commands if k == "sleep"], [])
        sup.check_auto_sleep(now=111.0)            # 11 s: past the 10 s threshold
        self.assertEqual([k for k, c in serial.commands if k == "sleep"], ["sleep"])

    def test_face_resets_timer_no_sleep(self):
        serial = FakeSerial()
        sup = self._sup(serial, after_s=10.0)
        sup.on_telemetry(make_telemetry("idle", False, False, 100.0))
        sup.check_auto_sleep(now=109.0)            # 9 s quiet
        sup.on_telemetry(make_telemetry("idle", True, False, 110.0))   # face seen
        sup.check_auto_sleep(now=119.0)            # 10 s since the face, but a
                                                    # face at 110 reset the clock
        self.assertEqual([c for k, c in serial.commands if k == "sleep"], [])

    def test_vibration_resets_timer_no_sleep(self):
        serial = FakeSerial()
        sup = self._sup(serial, after_s=10.0)
        sup.on_telemetry(make_telemetry("idle", False, False, 100.0))
        sup.check_auto_sleep(now=109.0)
        sup.on_telemetry(make_telemetry("idle", False, True, 110.0))   # a tap
        sup.check_auto_sleep(now=119.0)
        self.assertEqual([c for k, c in serial.commands if k == "sleep"], [])

    def test_sleep_not_spammed_within_a_window(self):
        # Once the owl is asleep, last_activity is never reset (the owl is
        # asleep, not "active"), so the owl STAYS asleep. The policy is
        # rate-limited to one "sleep" send per window -- it must not spam a
        # command on every idle call. (A re-send in a later window is a
        # harmless no-op the firmware ignores; what matters is no per-call spam.)
        serial = FakeSerial()
        sup = self._sup(serial, after_s=10.0)
        sup.on_telemetry(make_telemetry("idle", False, False, 100.0))
        sup.check_auto_sleep(now=100.0)             # baseline: clock starts now
        sup.check_auto_sleep(now=111.0)             # 11 s quiet: first send
        sup.check_auto_sleep(now=115.0)             # same window: no repeat
        sup.check_auto_sleep(now=119.0)             # same window: no repeat
        self.assertEqual([k for k, c in serial.commands if k == "sleep"],
                         ["sleep"])

    def test_no_sleep_while_already_sleeping(self):
        serial = FakeSerial()
        sup = self._sup(serial, after_s=10.0)
        sup.on_telemetry(make_telemetry("sleeping", False, False, 100.0))
        sup.check_auto_sleep(now=200.0)            # long after: still no re-send
        self.assertEqual([c for k, c in serial.commands if k == "sleep"], [])

    def test_no_sleep_in_update_mode(self):
        serial = FakeSerial()
        sup = self._sup(serial, after_s=10.0)
        sup.on_telemetry(make_telemetry("update", False, False, 100.0))
        sup.check_auto_sleep(now=200.0)             # owl is on an isolated SoftAP
        self.assertEqual([c for k, c in serial.commands if k == "sleep"], [])

    def test_disabled_is_noop(self):
        serial = FakeSerial()
        sup = self._sup(serial, after_s=10.0, enabled=False)
        sup.on_telemetry(make_telemetry("idle", False, False, 100.0))
        sup.check_auto_sleep(now=500.0)
        self.assertEqual(serial.commands, [])


class TestWakeOnSpeech(unittest.TestCase):
    """A spoken wake keyword wakes a sleeping owl; other speech does not."""

    def _setup(self, state, face=True, audio=None):
        from stubs import FakeSupervisor
        serial = FakeSerial()
        audio = audio if audio is not None else FakeAudio()
        sup = FakeSupervisor(state=state, face_detected=face, audio=audio)
        cfg = make_config()  # wake_keywords = ["eule", "wacht auf"]
        speech = Speech(serial, sup, cfg)
        return speech, serial, sup

    def test_wake_keyword_wakes_asleep_owl(self):
        speech, serial, sup = self._setup("sleeping")
        speech.feed("Eule, wach auf!")
        # A "wake" command was sent ...
        self.assertEqual([c for k, c in serial.commands if k == "wake"], [{"type": "wake"}])
        # ... and the owl is no longer asleep (optimistically; telemetry confirms).
        self.assertEqual(sup.last_state, "idle")

    def test_single_wake_keyword_wakes(self):
        speech, serial, sup = self._setup("sleeping")
        speech.feed("Eule!")
        self.assertEqual([c for k, c in serial.commands if k == "wake"], [{"type": "wake"}])

    def test_ambient_speech_does_not_wake(self):
        # "Das ist toll" is a happy-cluster phrase but NOT a wake keyword: a
        # sleeping owl must not be startled by it.
        speech, serial, sup = self._setup("sleeping")
        speech.feed("Das ist toll!")
        self.assertEqual([k for k, c in serial.commands if k == "wake"], [])
        self.assertEqual(sup.last_state, "sleeping")   # still asleep

    def test_wake_keyword_alone_sends_only_wake(self):
        # A bare wake keyword (no other cluster keyword) wakes the owl but, with
        # no face in frame, does not fire a normal reaction -- the wake command
        # is the only thing sent.
        speech, serial, sup = self._setup("sleeping", face=False)
        speech.feed("Eule!")
        self.assertEqual([k for k, c in serial.commands if k == "wake"], ["wake"])
        self.assertEqual([k for k, c in serial.commands if k == "expression"], [])

    def test_wake_then_reaction_when_face_present(self):
        # A wake keyword heard while asleep wakes the owl. The wake path returns
        # after waking (it does not also run the reaction on the same utterance --
        # the wake is the exception that lets a sleeping owl hear you at all). A
        # subsequent utterance, now that the owl is awake with a face in frame,
        # reacts normally.
        speech, serial, sup = self._setup("sleeping", face=True)
        speech.feed("Eule, das ist toll!")
        self.assertEqual([k for k, c in serial.commands if k == "wake"], ["wake"])
        self.assertEqual([c for k, c in serial.commands if k == "expression"], [])
        # Now awake (the feed set last_state to idle): the next utterance reacts.
        speech.feed("Das ist toll!")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])

    def test_wake_noop_when_already_awake(self):
        # The wake-exception only applies while asleep. Awake, a wake keyword is
        # just ordinary (ignored) speech -- no redundant "wake" command.
        speech, serial, sup = self._setup("interacting")
        speech.feed("Eule!")
        self.assertEqual([k for k, c in serial.commands if k == "wake"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
