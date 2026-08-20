"""
Robot Owl RPi Brain - Speech Recognition

The owl hears the user's voice and reacts: a short utterance is transcribed
(offline Whisper on the RPi) and mapped to a reaction (eye expression + an
owl-call on the MAX98357A amp). This is the RPi-side "behavior pipeline" from
the speech spec: cooldown -> keyword cluster -> action.

Design (see SPEECH_RECOGNITION_PLAN.md):
  * ASR runs on the RPi (a USB mic) -- the ESP32 has no mic / no ASR headroom.
  * Reactions drive the owl through the EXISTING temporary overrides
    (expression / gaze) + local amp audio. The ESP32 firmware and its own
    behavior state machine are NOT changed.
  * ASR is gated (awake + face + energy VAD) so the Pi is not transcribing
    24/7 and the owl does not "hear" the TV or itself.

Threading model
  A single daemon worker thread owns the whole audio path. It pulls mic chunks
  from a queue (filled by sounddevice's input callback), runs the energy VAD,
  and -- only when the gate is open -- transcribes the accumulated window with
  Whisper and hands the transcript to feed(). All reactions (serial commands,
  amp audio) happen in this worker, never in the foreground serial read loop.

  faster-whisper (ASR) + PortAudio (mic) are imported lazily (inside start())
  so that:
  * the brain still runs on a machine with no mic / no ASR engine installed,
  * the heavy ASR import stays out of the normal (speech-disabled) startup.
  With speech.enabled: false nothing here is imported or started.
"""

import logging
import math
import queue
import random
import re
import threading
import time

from brain.serial_handler import SerialHandler
from brain.supervisor import Supervisor

logger = logging.getLogger(__name__)

# States the owl must be in for speech to be allowed to react. (SLEEPING is
# deliberately excluded: a sleeping owl should not be woken by ambient words.)
# UPDATE is excluded because the owl is then on an isolated SoftAP and the RPi
# can no longer reach it over the normal USB serial link.
REACTIVE_STATES = {"idle", "detecting", "interacting"}


class Speech:
    """Hears the user (USB mic) and reacts through the owl's overrides.

    All reaction + ASR state lives on the instance (no module-level globals).
    The class is safe to construct even with no audio/mic: it simply logs and
    no-ops when audio is unavailable or when speech is disabled.
    """

    def __init__(self, serial: SerialHandler, supervisor: Supervisor, config: dict):
        self.serial = serial
        self.supervisor = supervisor
        # Audio is optional: reactions that include a sound no-op cleanly if
        # the amp/I2S is absent (supervisor.play_sound already handles that).
        self.audio = getattr(supervisor, "audio", None)

        cfg = (config or {}).get("speech", {})
        self.enabled = bool(cfg.get("enabled", False))
        self.language = cfg.get("language", "de")
        self.model = cfg.get("model", "tiny")
        self.mic_device = cfg.get("mic_device", "") or ""
        self.window_s = float(cfg.get("window_s", 2.5))
        self.chunk_s = float(cfg.get("chunk_s", 0.3))
        self.sample_rate = int(cfg.get("sample_rate", 16000))
        self.channels = int(cfg.get("channels", 1))
        self.vad_threshold = float(cfg.get("vad_threshold", 0.02))
        self.energy_floor_ms = float(cfg.get("energy_floor_ms", 700))
        self.cooldown_s = float(cfg.get("cooldown_s", 4.5))
        self.require_face = bool(cfg.get("require_face", True))

        # cluster name -> list of keywords (lowercased German, per the spec)
        self.clusters = cfg.get("clusters", {}) or {}
        # cluster name -> {"expression": ..., "sound": ...}
        self.reactions = cfg.get("reactions", {}) or {}
        # Phase 4: words that wake the owl from SLEEPING (the firmware does not
        # wake on speech, so the RPi sends the existing "wake" command when one
        # of these is heard while the owl is asleep). Kept strict/short so the
        # owl isn't woken by the TV. Empty = speech never wakes it.
        self.wake_keywords = [str(k).lower() for k in (cfg.get("wake_keywords") or [])]

        # Navigation ("guide me home"): phrases that START guiding toward a
        # place (the words after the trigger are the place name, fuzzy-matched
        # against the saved locations) and phrases that STOP it. See
        # NAVIGATION_PLAN.md §5.5 / §13. Longest trigger first so a short
        # trigger can't shadow a longer one.
        self.nav_triggers = [str(t).lower() for t in (cfg.get("nav_triggers") or [])]
        self.nav_stop_keywords = [str(k).lower() for k in (cfg.get("nav_stop_keywords") or [])]

        # Cooldown state (instance-level, per the spec's last_interaction_time)
        self._last_reaction = 0.0
        self.last_heard: str = ""
        # When the most recent transcript was captured (epoch seconds). Exposed
        # to the web UI so it can show "heard 4s ago"; 0.0 until the first
        # utterance is transcribed.
        self.last_heard_at: float = 0.0

        # Worker-thread plumbing (populated in start()).
        self._queue: "queue.Queue" = queue.Queue(maxsize=64)
        self._worker = None
        self._stop = threading.Event()
        # VAD / utterance-buffer state (owned by the worker thread).
        self._utterance = []
        self._voice_started = 0.0
        self._silence_frames = 0
        # Lazy-loaded Whisper (loaded on the worker thread in start()).
        self._whisper = None

    # ==================================================================
    # Lifecycle (called from main.py, mirroring the web UI pattern)
    # ==================================================================
    def start(self) -> None:
        """Start the speech pipeline (mic capture + VAD + Whisper worker).

        Raises if speech is enabled but the mic/Whisper cannot be brought up;
        main.py catches that and continues without speech. Calling start() while
        already running is a no-op with a warning (a double-start would open a
        second mic and a second worker); main.py starts it exactly once.
        """
        if not self.enabled:
            logger.info("Speech recognition disabled (speech.enabled: false)")
            return
        if self._worker is not None:
            logger.warning("Speech.start() called while already running; ignoring")
            return

        logger.info(
            "Speech recognition starting (lang=%s, model=%s, mic=%s, "
            "window=%.1fs, cooldown=%.1fs, require_face=%s)",
            self.language, self.model, self.mic_device or "default",
            self.window_s, self.cooldown_s, self.require_face,
        )

        # Lazy heavy imports: only pay for faster-whisper (CTranslate2) +
        # portaudio when the feature is actually on. faster-whisper (not the
        # torch openai-whisper) is used because it runs on the RPi's ARM CPU
        # without a heavy torch dependency and is several times faster -- see
        # SPEECH_RECOGNITION_PLAN.md "ASR engine".
        import numpy as np  # noqa: F401  (ensures numpy is present early)
        import sounddevice as sd
        from faster_whisper import WhisperModel

        # Load the model once on the worker thread (first run downloads it).
        # int8 on CPU: the RPi has no GPU, int8 is the right precision, and it
        # keeps the model small + fast. Auto-detects the device (cpu on the Pi).
        try:
            self._whisper = WhisperModel(self.model, device="cpu", compute_type="int8")
        except Exception as e:
            raise RuntimeError(f"Could not load faster-whisper model {self.model!r}: {e}") from e

        # Open the mic. The input callback runs in PortAudio's own thread and
        # only enqueues chunks (non-blocking); the worker thread does the heavy
        # lifting. blocksize = chunk_s * sample_rate frames.
        blocksize = max(1, int(self.chunk_s * self.sample_rate))
        try:
            self._stream = sd.InputStream(
                device=self.mic_device or None,
                channels=self.channels,
                samplerate=self.sample_rate,
                dtype="float32",
                blocksize=blocksize,
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as e:
            self._whisper = None
            raise RuntimeError(f"Could not open microphone {self.mic_device or 'default'}: {e}") from e

        self._stop.clear()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="robot-owl-speech")
        self._worker.start()
        logger.info("Speech recognition started (mic=%s, %d Hz)", self.mic_device or "default", self.sample_rate)

    def stop(self) -> None:
        """Stop the worker, then the mic.

        We set the stop flag and then wait for the worker thread to actually
        exit. The worker may be blocked in queue.get(timeout=0.5), so it wakes
        at most ~0.5 s after the flag is set; waiting until it is no longer
        alive (or a generous timeout) guarantees the worker has stopped
        consuming the queue before a new instance is started. The thread is a
        daemon, so any straggler is reaped at process exit.
        """
        if not self.enabled:
            return
        if self._worker is not None:
            self._stop.set()
            deadline = time.time() + 5.0
            while self._worker.is_alive() and time.time() < deadline:
                time.sleep(0.02)
            if self._worker.is_alive():
                logger.warning("Speech worker did not exit cleanly; dropping (daemon thread)")
            self._worker = None
        stream = getattr(self, "_stream", None)
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self._stream = None
        logger.info("Speech recognition stopped")

    # ==================================================================
    # Worker: mic callback -> VAD gate -> Whisper -> feed()
    # ==================================================================
    def _audio_callback(self, indata, frames, time_info, status) -> None:
        """PortAudio input callback. Non-blocking: just enqueue the chunk."""
        if status:
            logger.debug("Mic status: %s", status)
        try:
            self._queue.put_nowait(indata.copy())
        except queue.Full:
            # Drop the oldest chunk if we're falling behind (ASR is slow).
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(indata.copy())
            except queue.Empty:
                pass

    def _worker_loop(self) -> None:
        """Runs on the worker thread: pull chunks, run the VAD, transcribe."""
        import numpy as np  # local import; worker-thread only

        while not self._stop.is_set():
            try:
                chunk = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            except Exception:
                continue

            self._process_chunk(np.asarray(chunk).flatten())

    def _process_chunk(self, chunk) -> None:
        """VAD state machine over one chunk; transcribe a closed utterance.

        Gate: the owl must be awake and (if require_face) a face must be in
        frame. While open, voiced chunks accumulate into an utterance window;
        sustained silence (or the window filling) closes it and we transcribe.
        Everything runs on the worker thread.

        NOTE on timing: silence is measured in *audio duration* (frames /
        sample_rate), not wall-clock time. The worker may process chunks faster
        or slower than real time (e.g. a burst, or a slow CPU), so wall-clock
        timing would mis-measure the silence gap. Audio duration is independent
        of processing speed, which keeps the VAD correct and testable.
        """
        # --- Gate: is the owl in a state where it may react? ---
        if not self._gate_open():
            # Owl is asleep / in update mode / face-gate closed: drop audio.
            if self._utterance:
                self._utterance = []
                self._voice_started = 0.0
                self._silence_frames = 0
            return

        energy = self._rms(chunk)
        voiced = energy >= self.vad_threshold
        chunk_frames = len(chunk)

        if voiced:
            # A voiced chunk resets the trailing-silence counter.
            self._silence_frames = 0
            if not self._utterance:
                self._voice_started = time.time()
            self._utterance.append(chunk)

            total = sum(len(c) for c in self._utterance)
            max_frames = int(self.window_s * self.sample_rate)
            if total >= max_frames:
                self._transcribe_and_feed()
            # else: keep accumulating until silence or the window fills
        else:
            if self._utterance:
                self._silence_frames += chunk_frames
                if self._silence_frames >= self.energy_floor_ms * self.sample_rate / 1000.0:
                    # Sustained silence (in audio time): the utterance is over.
                    self._transcribe_and_feed()
            # else: still in silence, nothing to do

    def _gate_open(self) -> bool:
        """True if the owl is in a reactive state and (optionally) a face is
        in frame. Cheap: reads the supervisor's cached last telemetry."""
        last = self.supervisor.last
        if last is None or last.state not in REACTIVE_STATES:
            return False
        if self.require_face and not last.face.detected:
            return False
        return True

    @staticmethod
    def _rms(chunk) -> float:
        """RMS energy of a float32 chunk (PortAudio float32 is in [-1, 1])."""
        if chunk.size == 0:
            return 0.0
        return float(math.sqrt((chunk ** 2).mean()))

    def _transcribe_and_feed(self) -> None:
        """Transcribe the accumulated window (if worth it) and run the pipeline."""
        import numpy as np

        if not self._utterance:
            return
        utterance = np.concatenate(self._utterance)
        self._utterance = []
        self._voice_started = 0.0
        self._silence_frames = 0

        # Ignore fragments shorter than a tenth of a second (noise clicks).
        if utterance.size < int(0.1 * self.sample_rate):
            return
        if self._whisper is None:
            return

        # faster-whisper.transcribe() returns (segments_iterator, info). Consume
        # the segments and join their text. Pinned to the configured language so
        # it never auto-detects (faster + no mis-detection on short clips).
        try:
            segments, _info = self._whisper.transcribe(
                utterance, language=self.language,
            )
            transcript = " ".join(seg.text for seg in segments).strip()
        except Exception as e:
            logger.warning("Speech: transcription failed: %s", e)
            return

        if not transcript:
            return
        logger.debug("Speech: heard %r", transcript)
        self.feed(transcript)

    # ==================================================================
    # Behavior pipeline (Phase 1). Called by the worker with a transcript.
    # ==================================================================
    def feed(self, transcript: str) -> None:
        """Run the behavior pipeline on a transcript.

        Mirrors the spec's handle_behavior_pipeline(), plus the Phase-4
        wake-on-speech exception and the navigation override:
          0. (Phase 4) the user is an interaction trigger -> reset the
             auto-sleep inactivity timer; if the owl is asleep, a wake keyword
             wakes it, a navigation STOP keyword ends an in-progress navigation
             (so "stop" always works, even from the sofa while the owl sleeps),
             and any other speech is ignored
          1. face-gate (if require_face). A navigation STOP keyword is EXEMPT:
             "stop it" must end navigation even if the face momentarily left
             frame. (The STOP is acted on after the face-gate, before the
             cooldown, so it is never suppressed by either.)
          2. navigation STOP keyword -> end navigation (EXEMPT from the
             cooldown: it must work even right after the owl reacted to the
             phrase that started it)
          3. enforce the cooldown
          4. navigation START trigger -> start guiding toward the named place
             (checked BEFORE the keyword clusters, so a nav sentence is not
             stolen by a single question word, e.g. "wie komm ich zu hotel")
          5. keyword clusters (first match wins, in cluster definition order)
          6) else stochastic ambient fallback (gaze at user / idle)
        """
        if not self.enabled:
            return
        transcript = (transcript or "").strip()
        if not transcript:
            return

        self.last_heard = transcript
        now = time.time()
        self.last_heard_at = now
        normalized = transcript.lower()

        # 0) Phase 4: hearing the user is an interaction trigger.
        self.supervisor.register_activity(now)
        state = self.supervisor.last_state or (
            self.supervisor.last.state if self.supervisor.last else None)
        if state == "sleeping":
            # The normal pipeline below is gated off while asleep. The
            # exceptions are a clearly addressed wake keyword (wake the owl)
            # and a navigation STOP keyword (end an in-progress navigation so
            # "stop" always works, even from the sofa while the owl sleeps).
            # Any other speech is ignored so the sleeping owl is not startled
            # by the TV / ambient words.
            if any(self._keyword_hit(normalized, kw) for kw in self.wake_keywords):
                logger.info("Speech: wake keyword %r heard while asleep -> waking", transcript)
                self.serial.wake()
                self.supervisor.last_state = "idle"  # optimistic; telemetry confirms
                return
            if self._nav_stop_hit(normalized):
                logger.info("Speech: nav stop %r while asleep -> ending navigation", transcript)
                self.nav_stop(transcript)
                return
            logger.debug("Speech: %r heard while asleep (not wake/stop) -> ignored", transcript)
            return

        # 1) Face-gate: only react while a face is in frame (prevents the owl
        #    reacting to ambient TV / the user's own amp). A navigation STOP
        #    keyword is exempt (handled in step 2, before the gate applies).
        is_stop = self._nav_stop_hit(normalized)
        if self.require_face and not is_stop:
            last = self.supervisor.last
            if last is None or not last.face.detected:
                logger.debug("Speech: no face in frame, ignoring %r", transcript)
                return

        # 2) Navigation STOP keyword (exempt from the cooldown and face-gate:
        #    "stop it" must always work).
        if is_stop:
            self.nav_stop(transcript)
            return

        # 3) Cooldown: ignore reactions closer than cooldown_s apart.
        if now - self._last_reaction < self.cooldown_s:
            logger.debug("Speech: in cooldown, ignoring %r", transcript)
            return

        # 4) Navigation START trigger (checked before the keyword clusters so a
        #    nav sentence isn't claimed by a single question word). If no trigger
        #    matches we fall through to the normal reaction clusters.
        if self.nav_triggers and self._nav_trigger_hit(normalized):
            self._react_navigate(transcript)
            return

        # 5) Keyword clusters (first match wins, in cluster definition order).
        for cluster, keywords in self.clusters.items():
            if any(self._keyword_hit(normalized, kw) for kw in keywords):
                self._react(cluster, transcript)
                return

        # 6) No keyword matched: stochastic ambient fallback (spec's else-branch).
        #    Default 0.8 act / 0.2 idle -> we act (gaze at user) most of the time.
        if random.random() < 0.8:
            logger.info("Speech: ambient %r -> gaze at user", transcript)
            self.serial.set_gaze(0.0, 0.0)
            self._last_reaction = now
        # else: remain idle (organic resting behavior)

    # ------------------------------------------------------------------
    # Keyword matching
    # ------------------------------------------------------------------
    @staticmethod
    def _keyword_hit(text: str, keyword: str) -> bool:
        """True if `keyword` occurs in `text` as a whole word / phrase.

        The spec sketch used a plain substring test (`keyword in text`), which
        over-matches on German: the question word "wie" would fire inside
        "völlig", and "was" inside "irgendwas". We instead anchor on word
        boundaries (letters/digits on either side), so single words match whole
        words while multi-word phrases ("gute eule") still match as substrings.
        """
        if " " in keyword:
            return keyword in text
        pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------
    def _react(self, cluster: str, transcript: str) -> None:
        """Apply a cluster's reaction: expression override + (optional) sound."""
        reaction = self.reactions.get(cluster)
        if not reaction:
            logger.warning("Speech: cluster %r matched %r but has no reaction",
                           cluster, transcript)
            return

        expression = reaction.get("expression")
        sound = reaction.get("sound")

        if expression:
            ok = self.serial.set_expression(expression)
            if not ok:
                logger.warning("Speech: failed to send expression %r", expression)
        if sound and self.audio is not None:
            self.audio.play(sound)

        self._last_reaction = time.time()
        logger.info(
            "Heard %r -> %s (expression=%s, sound=%s)",
            transcript, cluster, expression or "-", sound or "-",
        )

    # ------------------------------------------------------------------
    # Navigation ("guide me home")
    # ------------------------------------------------------------------
    def _nav_stop_hit(self, normalized: str) -> bool:
        """True if the transcript is a navigation STOP keyword (see §13)."""
        return any(self._keyword_hit(normalized, kw) for kw in self.nav_stop_keywords)

    def _nav_trigger_hit(self, normalized: str) -> bool:
        """True if the transcript contains a navigation START trigger.

        A trigger is a multi-word phrase, so this is a plain substring test
        (see `_keyword_hit`). Checked before the keyword clusters in feed().
        """
        return any(t in normalized for t in self.nav_triggers)

    def nav_stop(self, transcript: str = "") -> None:
        """End the current navigation (idempotent; no-op if not navigating)."""
        if not self.supervisor.navigation or not self.supervisor.navigation.is_active():
            return
        self.supervisor.nav_stop("speech")
        self._last_reaction = time.time()
        if transcript:
            logger.info("Heard %r -> stop navigation", transcript)

    def _react_navigate(self, transcript: str) -> None:
        """Match a nav trigger, extract the place name, fuzzy-match it, start.

        The trigger phrases are lowercased (config), so we search the normalized
        (lowercased) transcript and slice the raw transcript at the same index
        (the index is case-independent). The extracted name is normalized again
        in _match_place, so the raw capitalization of the spoken name is fine.
        """
        normalized = transcript.lower()
        for trigger in sorted(self.nav_triggers, key=len, reverse=True):
            idx = normalized.find(trigger)
            if idx == -1:
                continue
            # The words after the trigger are the (fuzzy) place name.
            raw_name = transcript[idx + len(trigger):].strip(" .,!?")
            name = self._match_place(raw_name)
            if not name:
                logger.info("Speech: nav trigger %r but no place matches %r", trigger, raw_name)
                if self.audio is not None:
                    self.audio.play("alert")
                self._last_reaction = time.time()
                return
            self.supervisor.nav_start(name)
            self._last_reaction = time.time()
            return

    def _match_place(self, raw_name: str):
        """Fuzzy-match a (possibly garbled) place name against the saved places.

        Returns the normalized key if something matches, else None. Order:
          1. exact / normalized match,
          2. one is a prefix of the other (ASR often truncates: "hote" -> "hotel"),
          3. Levenshtein distance <= 2 (a couple of mis-heard letters).
        """
        stores = self.supervisor.locations
        if stores is None:
            return None
        target = (raw_name or "").strip().lower()
        if not target:
            return None
        names = stores.names()
        # 1) exact / normalized
        for n in names:
            if n == target:
                return n
        # 2) prefix (shorter is a prefix of the other)
        for n in names:
            if n.startswith(target) or target.startswith(n):
                return n
        # 3) Levenshtein <= 2
        for n in names:
            if self._levenshtein(target, n) <= 2:
                return n
        return None

    @staticmethod
    def _levenshtein(a: str, b: str) -> int:
        """Classic Levenshtein edit distance (small strings; cheap enough)."""
        if len(a) < len(b):
            a, b = b, a
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cost = 0 if ca == cb else 1
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
            prev = cur
        return prev[-1]
