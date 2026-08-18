"""
Robot Owl RPi Brain - Audio

Plays the owl's "voice" through the MAX98357A I2S amplifier on the Raspberry
Pi (see WIRING.md). Each emotion has its own sound:

  * If a recorded owl-call sample exists in assets/sounds/<name>.wav, that
    real recording is played (resampled to the output rate and level-matched).
    This is what makes the owl sound like an *owl*.
  * If no sample is present (or the amp/I2S is unavailable), the module falls
    back to a procedurally-synthesized tone so the owl still makes a sound.

Playback is done with the system `aplay` in a daemon thread, so the serial
read loop is never blocked. If the amp is not wired or I2S is not enabled,
the module degrades gracefully: it logs once and play() becomes a no-op.
"""

import array
import io
import logging
import math
import os
import shutil
import struct
import subprocess
import threading
import wave

logger = logging.getLogger(__name__)

# Sample rate the synthesized fallback tones are generated at. Recorded samples
# are resampled to this rate before playback so the whole voice is uniform.
SAMPLE_RATE = 22050

# Emotion -> recorded owl-call sample filename (in assets/sounds/). If the file
# is missing, we fall back to the synthesized tone of the same name (see _synth).
# Each emotion has its own distinct real recording.
EMOTION_SOUNDS = {
    "detecting":   "owl_detecting.wav",    # short, crisp "I see you"
    "interacting": "owl_interacting.wav",  # friendly hoot
    "happy":       "owl_happy.wav",        # bright little hoot
    "sleeping":    "owl_sleeping.wav",     # low, soft, drowsy
    "waking":      "owl_waking.wav",       # loud rising "I'm up"
    "alert":       "owl_alert.wav",        # sharp, urgent
    "beep":        "beep.wav",             # neutral UI blip (synthesized if absent)
}


class Audio:
    """Plays the owl's voice (recorded samples, or synthesized fallback)."""

    def __init__(self, config: dict):
        cfg = (config or {}).get("audio", {})
        self.enabled = bool(cfg.get("enabled", False))
        self.device = cfg.get("device", "") or ""
        self.volume = float(cfg.get("volume", 0.8))
        self._ready = False
        self._warned = False
        self._lock = threading.Lock()

        # Resolve the sounds directory and pre-load any samples that exist.
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.sounds_dir = cfg.get("sounds_dir") or os.path.join(base, "assets", "sounds")
        self._samples = self._load_samples()

        if self.enabled:
            if shutil.which("aplay") is None:
                self._note("aplay not found - audio disabled (install alsa-utils)")
            else:
                self._ready = True
                n = len(self._samples)
                if n:
                    logger.info("Audio: %d recorded owl-call sample(s) loaded from %s", n, self.sounds_dir)
                else:
                    logger.info("Audio: no recorded samples found in %s - using synthesized tones", self.sounds_dir)
                logger.info("Audio enabled (I2S amp), device=%s, volume=%.2f",
                             self.device or "default", self.volume)

    # ------------------------------------------------------------------
    # Sample loading: read each recorded .wav, resample to SAMPLE_RATE, and
    # keep the raw 16-bit mono PCM bytes ready to play (no disk I/O at play time).
    # ------------------------------------------------------------------
    def _load_samples(self):
        samples = {}
        if not os.path.isdir(self.sounds_dir):
            return samples
        for emo, fname in EMOTION_SOUNDS.items():
            path = os.path.join(self.sounds_dir, fname)
            if not os.path.isfile(path):
                continue
            try:
                pcm, src_rate = self._read_wav(path)
            except Exception as e:
                logger.warning("Audio: could not load %s (%s) - will synthesize", fname, e)
                continue
            if src_rate != SAMPLE_RATE:
                pcm = self._resample(pcm, src_rate, SAMPLE_RATE)
            samples[emo] = pcm
        return samples

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def play(self, sfx: str) -> bool:
        """Play a named sound. Prefers a recorded owl-call sample; falls back
        to a synthesized tone if none is loaded. Returns False if unavailable."""
        if not self._ready:
            self._note("audio unavailable - ignoring play(%r)" % sfx)
            return False
        if sfx == "stop":
            # (No persistent player to stop; each play() is a short one-shot.)
            return True
        # 1) Real owl-call recording, if we have one for this emotion.
        data = self._samples.get(sfx)
        if data is None:
            # 2) Otherwise synthesize a tone (also covers 'beep'/'chirp'/'sad').
            data = self._synth(sfx)
        if data is None:
            self._note("no sound for %r (no sample + no synth)" % sfx)
            return False
        with self.lock:
            return self._aplay(data)

    # ------------------------------------------------------------------
    # Sample I/O helpers
    # ------------------------------------------------------------------
    def _read_wav(self, path):
        """Read a 16-bit mono WAV. Returns (pcm_bytes, sample_rate)."""
        with wave.open(path, "rb") as w:
            rate = w.getframerate()
            nch = w.getnchannels()
            sw = w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if sw != 2:
            raise ValueError("only 16-bit samples supported (got %d-bit)" % (sw * 8))
        if nch != 1:
            # Mix down stereo -> mono by averaging channels.
            a = array.array('h')
            a.frombytes(raw)
            mono = array.array('h', ((a[i] + a[i + 1]) // 2 for i in range(0, len(a), 2)))
            raw = mono.tobytes()
        return bytes(raw), rate

    def _resample(self, pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
        """Linear-interpolation resample of 16-bit mono PCM."""
        if src_rate == dst_rate:
            return pcm
        a = array.array('h')
        a.frombytes(pcm)
        ratio = src_rate / dst_rate
        out_len = int(len(a) / ratio)
        out = array.array('h')
        out.extend(0 for _ in range(out_len))
        for i in range(out_len):
            pos = i * ratio
            i0 = int(pos)
            frac = pos - i0
            i1 = i0 + 1
            if i1 >= len(a):
                out[i] = a[-1]
            else:
                out[i] = int(a[i0] * (1 - frac) + a[i1] * frac)
        return out.tobytes()

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
        if sfx == "sleeping":
            # Slow, low, fading - a drowsy murmur.
            return self._tone(freqs=[330, 262, 196], ms=220, gap_ms=80)
        if sfx == "waking":
            # Two rising notes - "I'm up".
            return self._tone(freqs=[440, 660, 880], ms=110, gap_ms=40)
        if sfx == "detecting":
            # Crisp short notice.
            return self._tone(freqs=[880, 1100], ms=90, gap_ms=30)
        if sfx == "interacting":
            # Friendly double note.
            return self._tone(freqs=[660, 990], ms=110, gap_ms=40)
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
