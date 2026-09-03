"""Pure-numpy DSP engine: procedural ambience beds, speech-bus assembly,
sidechain ducking, and loudness normalization.

All functions operate on mono float32 arrays at SR=24000. Filtering uses FFT
masks rather than IIR designs — plenty for ambience beds, and keeps the module
scipy-free. ffmpeg is deliberately not required anywhere in this module.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable

import numpy as np
import soundfile as sf

SR = 24000

_EPS = 1e-12


def _ms_to_samples(ms: int | float) -> int:
    return round(ms * SR / 1000)


# --------------------------------------------------------------------------
# Noise generators (unit-peak output)
# --------------------------------------------------------------------------


def white_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    x = rng.standard_normal(n).astype(np.float32)
    return _unit_peak(x)


def pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """1/f noise via spectral shaping: scale white spectrum by 1/sqrt(f)."""
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, d=1.0 / SR)
    # Flatten the shaping below 10 Hz so DC/subsonic bins don't dominate.
    scale = 1.0 / np.sqrt(np.maximum(freqs, 10.0))
    scale[0] = 0.0
    x = np.fft.irfft(spectrum * scale, n).astype(np.float32)
    return _unit_peak(x)


def brown_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """1/f^2 noise: integrated white noise, de-meaned."""
    x = np.cumsum(rng.standard_normal(n))
    x -= np.mean(x)
    return _unit_peak(x.astype(np.float32))


def _unit_peak(x: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(x)))
    if peak > 0.0:
        x = x / peak
    return x.astype(np.float32)


# --------------------------------------------------------------------------
# FFT-mask filters
# --------------------------------------------------------------------------


def band_pass(x: np.ndarray, low_hz: float, high_hz: float) -> np.ndarray:
    """Brick-wall band-pass via FFT bin masking (fine for noise beds)."""
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), d=1.0 / SR)
    spectrum[(freqs < low_hz) | (freqs > high_hz)] = 0.0
    return np.fft.irfft(spectrum, len(x)).astype(np.float32)


def low_pass(x: np.ndarray, cutoff_hz: float) -> np.ndarray:
    return band_pass(x, 0.0, cutoff_hz)


def high_pass(x: np.ndarray, cutoff_hz: float) -> np.ndarray:
    return band_pass(x, cutoff_hz, SR / 2.0)


# --------------------------------------------------------------------------
# Ambience layer synthesis
# --------------------------------------------------------------------------


def _lfo(n: int, freq_hz: float, phase: float) -> np.ndarray:
    """Unipolar sine LFO in [0, 1]."""
    t = np.arange(n, dtype=np.float64) / SR
    return (0.5 + 0.5 * np.sin(2.0 * np.pi * freq_hz * t + phase)).astype(np.float32)


def _add_events(
    out: np.ndarray,
    rng: np.random.Generator,
    mean_interval_s: float,
    make_event: Callable[[np.random.Generator], np.ndarray],
) -> None:
    """Scatter short one-shot events across the buffer (Poisson-ish count)."""
    duration_s = len(out) / SR
    count = int(rng.poisson(max(duration_s / mean_interval_s, 0.0)))
    for _ in range(count):
        event = make_event(rng)
        start = int(rng.integers(0, max(len(out) - len(event), 1)))
        end = min(start + len(event), len(out))
        out[start:end] += event[: end - start]


def _layer_room_tone(n: int, rng: np.random.Generator) -> np.ndarray:
    return 0.7 * low_pass(brown_noise(n, rng), 250.0)


def _layer_outdoor_air(n: int, rng: np.random.Generator) -> np.ndarray:
    bed = band_pass(pink_noise(n, rng), 150.0, 2500.0)
    am = 0.75 + 0.25 * _lfo(n, 0.07, rng.uniform(0, 2 * np.pi))
    return 0.5 * _unit_peak(bed) * am


def _layer_wind(n: int, rng: np.random.Generator) -> np.ndarray:
    bed = band_pass(white_noise(n, rng), 80.0, 700.0)
    # Two incommensurate slow LFOs squared -> gusty, non-repeating swells.
    gust = _lfo(n, 0.11, rng.uniform(0, 2 * np.pi)) * _lfo(n, 0.031, rng.uniform(0, 2 * np.pi))
    return 0.85 * _unit_peak(bed) * (0.35 + 0.65 * gust**2)


def _layer_waves(n: int, rng: np.random.Generator) -> np.ndarray:
    bed = low_pass(pink_noise(n, rng), 500.0)
    swell = _lfo(n, 0.1, rng.uniform(0, 2 * np.pi)) ** 2
    return 0.75 * _unit_peak(bed) * (0.2 + 0.8 * swell)


def _layer_water_lapping(n: int, rng: np.random.Generator) -> np.ndarray:
    bed = band_pass(pink_noise(n, rng), 200.0, 1500.0)
    am = _lfo(n, 0.35, rng.uniform(0, 2 * np.pi)) * _lfo(n, 0.53, rng.uniform(0, 2 * np.pi))
    return 0.6 * _unit_peak(bed) * (0.25 + 0.75 * am)


def _layer_rain(n: int, rng: np.random.Generator) -> np.ndarray:
    out = 0.5 * high_pass(white_noise(n, rng), 1800.0)

    def droplet(r: np.random.Generator) -> np.ndarray:
        length = int(r.uniform(0.003, 0.008) * SR)
        env = np.exp(-np.linspace(0.0, 6.0, length, dtype=np.float32))
        return (r.uniform(0.3, 0.9) * env * r.standard_normal(length)).astype(np.float32)

    _add_events(out, rng, mean_interval_s=0.05, make_event=droplet)
    return 0.7 * _unit_peak(out)


def _layer_thunder(n: int, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(n, dtype=np.float32)

    def rumble(r: np.random.Generator) -> np.ndarray:
        length = int(r.uniform(2.0, 4.0) * SR)
        t = np.linspace(0.0, 1.0, length, dtype=np.float32)
        # Fast attack, long tail.
        env = np.minimum(t / 0.08, 1.0) * np.exp(-3.5 * t)
        body = low_pass(r.standard_normal(length).astype(np.float32), 90.0)
        return (r.uniform(0.6, 1.0) * env * _unit_peak(body)).astype(np.float32)

    _add_events(out, rng, mean_interval_s=15.0, make_event=rumble)
    return 0.9 * out


def _layer_crickets(n: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n, dtype=np.float64) / SR
    carrier = np.sin(2.0 * np.pi * rng.uniform(4200.0, 4800.0) * t)
    # ~22 Hz pulse train gated by a slow on/off cycle = chirp bursts.
    pulses = (np.sin(2.0 * np.pi * 22.0 * t) > 0.0).astype(np.float32)
    gate = (np.sin(2.0 * np.pi * 0.8 * t + rng.uniform(0, 2 * np.pi)) > -0.3).astype(np.float32)
    return (0.3 * carrier * pulses * gate).astype(np.float32)


def _layer_crowd(n: int, rng: np.random.Generator) -> np.ndarray:
    total = np.zeros(n, dtype=np.float32)
    for _ in range(10):
        voice = band_pass(rng.standard_normal(n).astype(np.float32), 250.0, 2800.0)
        # Syllabic-rate AM plus a slow talk/pause cycle per fake voice.
        syllabic = _lfo(n, rng.uniform(3.0, 6.0), rng.uniform(0, 2 * np.pi))
        talking = _lfo(n, rng.uniform(0.05, 0.2), rng.uniform(0, 2 * np.pi))
        total += _unit_peak(voice) * syllabic * (0.3 + 0.7 * talking)
    return 0.6 * _unit_peak(low_pass(total, 2000.0))


def _swept_cry(
    r: np.random.Generator,
    length_s: tuple[float, float],
    freq_start: tuple[float, float],
    freq_end: tuple[float, float],
) -> np.ndarray:
    length = int(r.uniform(*length_s) * SR)
    t = np.linspace(0.0, 1.0, length, dtype=np.float32)
    f0, f1 = r.uniform(*freq_start), r.uniform(*freq_end)
    freq = f0 + (f1 - f0) * t
    phase = 2.0 * np.pi * np.cumsum(freq) / SR
    env = np.minimum(t / 0.1, 1.0) * np.exp(-4.0 * t)
    return (r.uniform(0.4, 0.9) * env * np.sin(phase)).astype(np.float32)


def _layer_gulls(n: int, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(n, dtype=np.float32)
    _add_events(
        out,
        rng,
        mean_interval_s=5.0,
        make_event=lambda r: _swept_cry(r, (0.3, 0.6), (1200.0, 1500.0), (800.0, 1000.0)),
    )
    return 0.5 * out


def _layer_birds(n: int, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(n, dtype=np.float32)
    _add_events(
        out,
        rng,
        mean_interval_s=2.5,
        make_event=lambda r: _swept_cry(r, (0.08, 0.2), (2200.0, 3200.0), (2800.0, 4200.0)),
    )
    return 0.4 * out


def _layer_city_hum(n: int, rng: np.random.Generator) -> np.ndarray:
    hum = low_pass(brown_noise(n, rng), 400.0)
    swish = band_pass(pink_noise(n, rng), 100.0, 1200.0)
    am = 0.7 + 0.3 * _lfo(n, 0.09, rng.uniform(0, 2 * np.pi))
    return 0.6 * _unit_peak(hum + 0.4 * swish) * am


def _layer_rope_creak(n: int, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(n, dtype=np.float32)

    def creak(r: np.random.Generator) -> np.ndarray:
        length = int(r.uniform(0.2, 0.5) * SR)
        body = band_pass(r.standard_normal(length).astype(np.float32), 150.0, 600.0)
        env = np.sin(np.linspace(0.0, np.pi, length, dtype=np.float32))
        return (r.uniform(0.4, 0.8) * env * _unit_peak(body)).astype(np.float32)

    _add_events(out, rng, mean_interval_s=4.0, make_event=creak)
    return 0.4 * out


def _layer_glassware(n: int, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(n, dtype=np.float32)

    def clink(r: np.random.Generator) -> np.ndarray:
        length = int(r.uniform(0.05, 0.12) * SR)
        t = np.linspace(0.0, 1.0, length, dtype=np.float32)
        tone = np.sin(2.0 * np.pi * r.uniform(2500.0, 4000.0) * t * length / SR)
        return (r.uniform(0.3, 0.6) * np.exp(-8.0 * t) * tone).astype(np.float32)

    _add_events(out, rng, mean_interval_s=3.0, make_event=clink)
    return 0.3 * out


_LAYER_GENERATORS: dict[str, Callable[[int, np.random.Generator], np.ndarray]] = {
    "room_tone": _layer_room_tone,
    "outdoor_air": _layer_outdoor_air,
    "daytime_ambience": _layer_outdoor_air,
    "wind": _layer_wind,
    "wind_trees": _layer_wind,
    "waves_distant": _layer_waves,
    "water_lapping": _layer_water_lapping,
    "rain": _layer_rain,
    "thunder_distant": _layer_thunder,
    "night_crickets": _layer_crickets,
    "crowd_murmur": _layer_crowd,
    "gulls": _layer_gulls,
    "birds": _layer_birds,
    "traffic": _layer_city_hum,
    "city_hum": _layer_city_hum,
    "rope_creak": _layer_rope_creak,
    "glassware": _layer_glassware,
}


def _tag_rng(seed: int, tag: str) -> np.random.Generator:
    # Per-tag substream so the layer mix is independent of tag list order.
    tag_hash = int.from_bytes(hashlib.sha256(tag.encode("utf-8")).digest()[:8], "little")
    return np.random.default_rng([seed, tag_hash])


def synth_ambience(tags: list[str], duration_s: float, seed: int) -> np.ndarray:
    """Layered procedural ambience bed, deterministic per (tags, seed).

    Unknown tags fall back to room tone so a scene never renders dead silent.
    Output is peak-normalized to 0.5 to leave headroom for the speech bus.
    """
    n = round(duration_s * SR)
    mix = np.zeros(n, dtype=np.float32)
    for tag in sorted(set(tags)):
        generator = _LAYER_GENERATORS.get(tag, _layer_room_tone)
        mix += generator(n, _tag_rng(seed, tag))
    peak = float(np.max(np.abs(mix)))
    if peak > 0.0:
        mix *= 0.5 / peak
    return mix.astype(np.float32)


# --------------------------------------------------------------------------
# Looping and clip placement
# --------------------------------------------------------------------------


def loop_to_length(bed: np.ndarray, target_len: int, crossfade_s: float = 2.0) -> np.ndarray:
    """Extend a bed to target_len samples with equal-power crossfade loops."""
    if len(bed) >= target_len:
        return bed[:target_len].copy()
    crossfade = min(int(crossfade_s * SR), len(bed) // 2)
    if crossfade == 0:
        reps = -(-target_len // len(bed))
        return np.tile(bed, reps)[:target_len].copy()

    # cos/sin fades keep constant power through the seam.
    theta = np.linspace(0.0, np.pi / 2.0, crossfade, dtype=np.float32)
    fade_out = np.cos(theta)
    fade_in = np.sin(theta)

    out = bed.copy()
    while len(out) < target_len:
        seam = out[-crossfade:] * fade_out + bed[:crossfade] * fade_in
        out = np.concatenate([out[:-crossfade], seam, bed[crossfade:]])
    return out[:target_len].astype(np.float32)


def place_clips(clips: list[tuple[np.ndarray, int]], total_ms: int) -> np.ndarray:
    """Assemble the speech bus from (samples, start_ms) pairs."""
    out = np.zeros(_ms_to_samples(total_ms), dtype=np.float32)
    for samples, start_ms in clips:
        start = _ms_to_samples(start_ms)
        if start >= len(out):
            continue
        end = min(start + len(samples), len(out))
        out[start:end] += samples[: end - start]
    return out


# --------------------------------------------------------------------------
# Sidechain ducking
# --------------------------------------------------------------------------

_ENV_BLOCK = SR // 1000  # 1 ms control blocks keep the smoothing loop cheap

# PRD compressor ratio. Named because the timeline editor's ambience-duck knob
# is calibrated against it (app.render.audio.model.SceneRenderSettings).
DUCK_RATIO_DEFAULT = 6.0


def duck(
    ambience: np.ndarray,
    speech: np.ndarray,
    threshold: float = 0.02,
    ratio: float = DUCK_RATIO_DEFAULT,
    attack_ms: int = 12,
    release_ms: int = 380,
) -> np.ndarray:
    """Sidechain-duck ambience under speech (PRD compressor parameters).

    Envelope follower: per-1ms block peak of |speech|, smoothed by a one-pole
    with separate attack/release time constants; gain reduction follows a
    standard downward compressor curve above the threshold.
    """
    n = min(len(ambience), len(speech))
    blocks = -(-n // _ENV_BLOCK)
    padded = np.zeros(blocks * _ENV_BLOCK, dtype=np.float32)
    padded[:n] = np.abs(speech[:n])
    block_peaks = padded.reshape(blocks, _ENV_BLOCK).max(axis=1)

    attack_coef = float(np.exp(-1.0 / max(attack_ms, 1)))
    release_coef = float(np.exp(-1.0 / max(release_ms, 1)))
    envelope = np.empty(blocks, dtype=np.float32)
    state = 0.0
    for i in range(blocks):
        target = float(block_peaks[i])
        coef = attack_coef if target > state else release_coef
        state = coef * state + (1.0 - coef) * target
        envelope[i] = state

    env_db = 20.0 * np.log10(envelope + _EPS)
    threshold_db = 20.0 * np.log10(threshold)
    over_db = np.maximum(env_db - threshold_db, 0.0)
    gain = np.power(10.0, -over_db * (1.0 - 1.0 / ratio) / 20.0).astype(np.float32)

    gain_full = np.repeat(gain, _ENV_BLOCK)[: len(ambience)]
    if len(gain_full) < len(ambience):
        gain_full = np.pad(gain_full, (0, len(ambience) - len(gain_full)), constant_values=1.0)
    return (ambience * gain_full).astype(np.float32)


# --------------------------------------------------------------------------
# Loudness / limiting / final mix
# --------------------------------------------------------------------------


def normalize_loudness(x: np.ndarray, target_rms_db: float = -20.0) -> np.ndarray:
    """Scale to a target RMS level.

    This is a crude stand-in for LUFS: no K-weighting or gating, just full-band
    RMS. Good enough to land scenes at a consistent level without ffmpeg.
    """
    rms = float(np.sqrt(np.mean(np.square(x))))
    if rms < _EPS:
        return x.copy()
    target = 10.0 ** (target_rms_db / 20.0)
    return (x * (target / rms)).astype(np.float32)


def peak_limit(x: np.ndarray, ceiling: float = 0.98) -> np.ndarray:
    """Bring peaks under the ceiling by uniform gain (transparent, no clipping)."""
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak <= ceiling:
        return x.copy()
    return (x * (ceiling / peak)).astype(np.float32)


def mix_scene(
    speech_bus: np.ndarray,
    ambience_bed: np.ndarray,
    sfx_bus: np.ndarray | None = None,
    duck_ratio: float = DUCK_RATIO_DEFAULT,
) -> np.ndarray:
    """Duck ambience under the foreground (speech + SFX), sum, normalize, limit.

    Sound effects are foreground events: they sit alongside speech and the
    ambience bed ducks under them too, so a thunder crack or a door slam
    pushes the bed down the same way a spoken line does.

    ``duck_ratio`` is the sidechain compressor ratio; 1.0 disables ducking
    entirely (unity gain) and higher values push the bed further down.
    """
    lengths = [len(speech_bus), len(ambience_bed)]
    if sfx_bus is not None:
        lengths.append(len(sfx_bus))
    n = max(lengths)

    speech = np.zeros(n, dtype=np.float32)
    speech[: len(speech_bus)] = speech_bus
    ambience = np.zeros(n, dtype=np.float32)
    ambience[: len(ambience_bed)] = ambience_bed
    foreground = speech
    if sfx_bus is not None:
        foreground = speech.copy()
        foreground[: len(sfx_bus)] += sfx_bus

    mixed = foreground + duck(ambience, foreground, ratio=duck_ratio)
    return peak_limit(normalize_loudness(mixed))


# --------------------------------------------------------------------------
# WAV serialization and resampling
# --------------------------------------------------------------------------


def wav_bytes(x: np.ndarray, sr: int) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, x, sr, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def read_wav_bytes(data: bytes) -> tuple[np.ndarray, int]:
    samples, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return samples.mean(axis=1).astype(np.float32), int(sr)


def resample_linear(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """Linear-interpolation resampler (fine for speech clips feeding a mix)."""
    if sr_from == sr_to:
        return x.astype(np.float32)
    n_out = round(len(x) * sr_to / sr_from)
    positions = np.linspace(0.0, len(x) - 1.0, n_out)
    return np.interp(positions, np.arange(len(x)), x).astype(np.float32)
