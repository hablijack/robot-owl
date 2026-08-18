"""
Robot Owl RPi Brain - Audio

Plays short, procedurally-generated sound effects through the MAX98357A I2S
amplifier on the Raspberry Pi (see WIRING.md). No external audio assets are
needed: each effect is synthesized in-process as a 16-bit mono WAV and played
with the system `aplay` in a daemon thread, so the serial read loop is never
blocked.

If the amp is not wired or I2S is not enabled, the module degrades gracefully:
it logs once that audio is unavailable and play() becomes a no-op.
"""

import io
import logging
import math
import shutil
import struct
import subprocess
import threading
import wave

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100


class Audio:
    """Generates and plays sound effects over the Pi's I2S output."""

    def __init__(self, config: dict):
        cfg = (config or {}).get("audio", {})
        self.enabled = bool(cfg.get("enabled", False))
        self.device = cfg.get("device", "") or ""
        self.volume = float(cfg.get("volume", 0.8))
        self._ready = False
        self._warned = False
        self._lock = threading.Lock()

        if self.enabled:
            if shutil.which("aplay") is None:
                self._note("aplay not found - audio disabled (install alsa-utils)")
            else:
                self._ready = True
                logger.info("Audio enabled (I2S amp), device=%s, volume=%.2f",
                             self.device or "default", self.volume)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def play(self, sfx: str) -> bool:
        """Play a named sound effect. Returns False if audio is unavailable."""
        if not self._ready:
            self._note("audio unavailable - ignoring play(%r)" % sfx)
            return False
        if sfx == "stop":
            # (No persistent player to stop; each play() is a short one-shot.)
            return True
        data = self._synth(sfx)
        if data is None:
            self._note("unknown sound effect %r" % sfx)
            return False
        with self.lock:
            return self._aplay(data)

    # ------------------------------------------------------------------
    # Synthesis: returns raw 16-bit mono PCM bytes at SAMPLE_RATE
    # ------------------------------------------------------------------
    def _synth(self, sfx: str):
        if sfx == "beep":
            return self._tone(freqs=[880], ms=120, gap_ms=0)
        if sfx == "chirp":
            # Two rising notes - a friendly "notice" sound.
            return self._tone(freqs=[660, 990], ms=110, gap_ms=40)
        if sfx == "happy":
            # Little ascending arpeggio.
            return self._tone(freqs=[523, 659, 784], ms=90, gap_ms=30)
        if sfx == "sad":
            # Descending - "going to sleep".
            return self._tone(freqs=[660, 440, 330], ms=160, gap_ms=50)
        if sfx == "alert":
            # Two sharp equal blips - "waking / attention".
            return self._tone(freqs=[1046, 1046], ms=90, gap_ms=60)
        return None

    def _tone(self, freqs, ms, gap_ms=0):
        """Concatenate square-ish tones. Returns 16-bit mono PCM bytes."""
        frames = bytearray()
        amp = 0.5 * self.volume
        for i, f in enumerate(freqs):
            if i > 0 and gap_ms > 0:
                n = int(SAMPLE_RATE * gap_ms / 1000)
                frames += b"\x00\x00" * n
            n = int(SAMPLE_RATE * ms / 1000)
            for k in range(n):
                # Smooth-ish tone: sine with a touch of square for "beep" character.
                phase = 2 * math.pi * f * (k / SAMPLE_RATE)
                s = math.sin(phase) + 0.3 * math.copysign(1.0, math.sin(phase))
                val = int(max(-1.0, min(1.0, s * amp)) * 32767)
                frames += struct.pack("<h", val)
        return bytes(frames)

    # ------------------------------------------------------------------
    # Playback via aplay (daemon thread, non-blocking)
    # ------------------------------------------------------------------
    def _aplay(self, pcm: bytes) -> bool:
        wav = self._wrap_wav(pcm)
        args = ["aplay", "-q"]
        if self.device:
            args += ["-c", self.device]
        try:
            proc = subprocess.Popen(
                args, stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self._note("aplay failed to start: %s" % e)
            return False
        # Feed the WAV over stdin in a daemon thread so play() returns at once.
        threading.Thread(target=self._feed, args=(proc, wav), daemon=True).start()
        return True

    def _feed(self, proc, wav):
        try:
            proc.stdin.write(wav)
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=15)
        except Exception:
            pass

    def _wrap_wav(self, pcm: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        return buf.getvalue()

    def _note(self, msg):
        # Log once per distinct condition to avoid spamming on every play().
        if not self._warned:
            logger.warning("Audio: %s", msg)
            self._warned = True
