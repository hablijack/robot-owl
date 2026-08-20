#!/usr/bin/env python3
"""
Run the Robot Owl RPi brain test suite.

    python3 run_tests.py            # run everything
    python3 run_tests.py -v         # verbose

The tests are designed to run on a plain dev machine (e.g. a Mac) with NO
Raspberry Pi, NO microphone, and NO faster-whisper/PortAudio installed -- the
third-party modules are stubbed out (see tests/stubs.py). The only hard
dependency is numpy (a normal project dependency).

On the Raspberry Pi itself the same suite runs against the real installed
packages (pyserial/flask/yaml are present there), so it doubles as a
smoke test of the real imports.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    # Make the brain package importable (tests import `brain.speech`).
    sys.path.insert(0, os.path.dirname(HERE))
    # Make the test helpers importable (tests import `stubs`).
    sys.path.insert(0, HERE)

    verbosity = 2 if "-v" in sys.argv or "--verbose" in sys.argv else 1

    # Discover every test_*.py in the tests/ directory (the Phase-3 web-UI
    # tests live in test_speech_asr.py alongside the Phase-2 ASR tests).
    loader = unittest.TestLoader()
    suite = loader.discover(HERE, pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)

    # Non-zero exit code on failure so this can gate a CI / pre-flash check.
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
