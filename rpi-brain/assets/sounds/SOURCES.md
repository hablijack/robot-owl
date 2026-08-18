# Owl sound sources

Each `.wav` here is a short clip extracted from a longer recording on
[Freesound.org](https://freesound.org), trimmed to the interesting part, and
resampled to **22050 Hz, 16-bit mono** so the whole voice plays uniformly
through the MAX98357A amp. Five of the six are isolated owl hoots; the
"sleeping" clip is a soft, continuous sleeping-breath (the owl "snoring"
softly rather than hooting).

The owl is a **robot**, so the hoots are pushed through a light "robot voice"
effect chain (see below) — a bitcrusher + ring-modulation warble + soft
distortion, with a low-end lift so the calls still carry over the small
speaker. The sleep-breath gets only a gentle crush (no hard ring-mod) so it
stays soft. All six are **RMS-matched** (equal perceived loudness, peak-capped
at 0.9) so no single emotion blasts past the others.

All recordings are licensed **Creative Commons 0 (CC0)** — public domain, no
attribution required. Credits below are given out of respect for the recordists.

| File | Emotion | Source | Recordist | Freesound ID |
|------|---------|--------|-----------|--------------|
| owl_detecting.wav   | detecting   | owl_hooting ... 007-015.wav (tawny) | Gerent | [558398](https://freesound.org/s/558398/) |
| owl_interacting.wav | interacting | Tawny Owl, multi-hoot phrase (Molkom, Sweden) | faxfaxfax | [655380](https://freesound.org/s/655380/) |
| owl_happy.wav       | happy       | owl_hooting ... 068-074.wav (tawny) | Gerent | [558396](https://freesound.org/s/558396/) |
| owl_sleeping.wav    | sleeping    | Sleep Breathing (continuous, soft) | heartsprout | [735296](https://freesound.org/s/735296/) |
| owl_waking.wav      | waking      | owl hooting (short, loud) | mokasza | [810336](https://freesound.org/s/810336/) |
| owl_alert.wav       | alert       | Great Horned Owl, big booming hoot | TheKingOfGeeks360 | [863463](https://freesound.org/s/863463/) |

## Regenerating the samples

The samples were produced with a throwaway script (not committed). For each
source it:

1. **Decoded** the Freesound preview `.ogg` to 22050 Hz mono (`ffmpeg`).
2. **Located the hoot** from the energy envelope (50 ms windows) — for
   multi-hoot phrases like the interacting clip it keeps the whole phrase,
   tolerating short dips between hoots.
3. **Trimmed** to the active region with a head pad and a generous tail pad so
   the last hoot is never cut.
4. **Noise-gated** — the field recordings carry a white-noise floor
   (wind/handling/ambient) in the gaps around each hoot, so samples below ~2
   dB above the measured noise floor are gated to silence with a short attack /
   longer release to avoid clicks.
5. **Robot effect chain** (the "robot owl" voice):
   - *Hoots* (detecting / interacting / happy / waking / alert): **8-bit
     bitcrush** → **ring-modulation** at 7 Hz (a slow, smooth warble — a faster
     rate stutters) → **hard-clip distortion** (drive 4) → **EQ** with a +3 dB
     low-shelf (120–400 Hz) so the hoot body survives the small speaker, plus a
     gentle high-frequency roll-off.
   - *Sleeping*: **11-bit bitcrush** → a very light 5 Hz ring-mod hum (12 %
     depth, no hard clip) → +2 dB low-shelf. Kept soft, not buzzy.
6. **RMS-matched** all six to a shared loudness (target RMS 0.12) with a peak
   cap at 0.9, then wrote 22050 Hz mono 16-bit WAVs.

`ffmpeg` + `numpy` were used for decoding/analysis/effects. If you want
different calls, swap the source IDs above and re-run the same steps.

If a file is missing at runtime, `brain/audio.py` falls back to a
procedurally-synthesized tone of the same name, so the owl still makes a sound.
