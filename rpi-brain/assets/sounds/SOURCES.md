# Owl sound sources

Each `.wav` here is a short clip extracted from a longer recording on
[Freesound.org](https://freesound.org), trimmed to the interesting part,
level-matched (peak 0.85), and resampled to **22050 Hz, 16-bit mono** so the
whole voice plays uniformly through the MAX98357A amp. Five of the six are
isolated owl hoots; the "sleeping" clip is a soft, continuous sleeping-breath
(the owl "snoring" softly rather than hooting).

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

The samples were produced with a throwaway script (not committed): for each
source it (1) decoded the Freesound preview `.ogg`, (2) read the energy
envelope (50 ms windows) to locate the hoot — for multi-hoot phrases like the
interacting clip it keeps the whole phrase, tolerating short dips between
hoots, (3) trimmed to the active region with a head pad and a generous tail
pad (so the last hoot is never cut), (4) applied a **noise gate** — the field
recordings carry a white-noise floor (wind/handling/ambient) in the gaps
around each hoot, so samples below ~2 dB above the measured noise floor are
gated to silence with a short attack / longer release to avoid clicks — and
(5) peak-normalized to 0.85 and wrote a 22050 Hz mono 16-bit WAV. `ffmpeg` +
`numpy` were used for decoding/analysis. If you want different calls, swap the
source IDs above and re-run the same steps.

If a file is missing at runtime, `brain/audio.py` falls back to a
procedurally-synthesized tone of the same name, so the owl still makes a sound.
