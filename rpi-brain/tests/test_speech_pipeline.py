"""
Phase-1 tests: the behavior pipeline (cooldown -> face-gate -> keyword cluster
-> reaction -> stochastic fallback), driven directly through Speech.feed().

These need no microphone, no faster-whisper -- just the stubs in stubs.py.
Run with:  python3 run_tests.py
"""

import random
import sys
import time
import unittest

from stubs import install_stub_modules, FakeSerial, FakeSupervisor, FakeAudio, make_config

install_stub_modules()

sys.path.insert(0, "..")
from brain.speech import Speech  # noqa: E402


def make_speech(state="interacting", face=True, **cfg_over):
    serial = FakeSerial()
    audio = FakeAudio()
    sup = FakeSupervisor(state=state, face_detected=face, audio=audio)
    speech = Speech(serial, sup, make_config(**cfg_over))
    return speech, serial, sup, audio


class TestKeywordClusters(unittest.TestCase):
    def test_happy_keyword(self):
        speech, serial, sup, audio = make_speech()
        speech.feed("Das ist toll!")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])
        self.assertEqual(audio.played, ["happy"])

    def test_happy_phrase_keyword(self):
        speech, serial, sup, audio = make_speech()
        speech.feed("Du bist eine Gute Eule!")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])

    def test_negative_keyword(self):
        speech, serial, sup, audio = make_speech()
        speech.feed("Nein, lass das!")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["surprised"])
        self.assertEqual(audio.played, ["alert"])

    def test_question_keyword(self):
        speech, serial, sup, audio = make_speech()
        speech.feed("Wer bist du?")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["surprised"])
        self.assertEqual(audio.played, ["alert"])

    def test_question_wie_standalone(self):
        speech, serial, sup, audio = make_speech()
        speech.feed("Wie geht's?")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["surprised"])


class TestWordBoundaryMatching(unittest.TestCase):
    """The spec's plain substring test over-matches in German. We must not."""
    def test_no_false_wie_in_vollig(self):
        speech, serial, sup, audio = make_speech()
        speech.feed("Das ist völlig klar")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], [])
        self.assertEqual(audio.played, [])

    def test_no_false_was_in_irgendwas(self):
        speech, serial, sup, audio = make_speech()
        speech.feed("Ich habe irgendwas gehört")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], [])

    def test_real_wie_still_matches(self):
        speech, serial, sup, audio = make_speech()
        speech.feed("wie heißt du")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["surprised"])


class TestCooldown(unittest.TestCase):
    def test_cooldown_blocks_repeat(self):
        speech, serial, sup, audio = make_speech()
        speech._last_reaction = time.time() - 1.0  # reacted 1s ago (< 4.5s cooldown)
        speech.feed("Das ist toll!")
        self.assertEqual(serial.commands, [])
        self.assertEqual(audio.played, [])

    def test_cooldown_expired_allows(self):
        speech, serial, sup, audio = make_speech()
        speech._last_reaction = time.time() - 10.0  # well past the 4.5s cooldown
        speech.feed("Das ist toll!")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])


class TestFaceGate(unittest.TestCase):
    def test_no_face_no_reaction(self):
        speech, serial, sup, audio = make_speech(face=False)
        speech.feed("Das ist toll!")
        self.assertEqual(serial.commands, [])
        self.assertEqual(audio.played, [])

    def test_require_face_false_reacts_without_face(self):
        speech, serial, sup, audio = make_speech(face=False, require_face=False)
        speech.feed("Das ist toll!")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])


class TestFallback(unittest.TestCase):
    def test_fallback_never_sets_expression(self):
        for seed in range(30):
            random.seed(seed)
            speech, serial, sup, audio = make_speech()
            speech.feed("hallo mein Freund")  # contains no cluster keyword
            self.assertEqual([c for k, c in serial.commands if k == "expression"], [],
                              f"seed {seed}: fallback set an expression")

    def test_fallback_gaze_ratio(self):
        # 0.8 act / 0.2 idle -> over 40 trials expect roughly 32 gaze commands.
        random.seed(7)
        gaze = 0
        for _ in range(40):
            speech, serial, sup, audio = make_speech()
            speech.feed("hallo mein Freund")
            if any(k == "gaze" for k, _ in serial.commands):
                gaze += 1
        self.assertGreater(gaze, 15)
        self.assertLess(gaze, 40)


class TestDisabled(unittest.TestCase):
    def test_disabled_is_noop(self):
        speech, serial, sup, audio = make_speech(enabled=False)
        speech.feed("Das isst toll!")
        self.assertEqual(serial.commands, [])
        self.assertEqual(audio.played, [])

    def test_asleep_state_no_reaction(self):
        # Phase 4: feed() is state-aware. A NON-wake utterance heard while the
        # owl is asleep is dropped (a sleeping owl is not startled by ambient
        # words like "Das ist toll!"). Only a wake keyword (see
        # test_speech_autosleep.TestWakeOnSpeech) gets through while asleep.
        speech, serial, sup, audio = make_speech(state="sleeping")
        speech.feed("Das ist toll!")
        self.assertEqual([k for k, c in serial.commands if k == "expression"], [])
        self.assertEqual(audio.played, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
